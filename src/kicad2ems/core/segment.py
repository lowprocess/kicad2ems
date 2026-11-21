"""
kicad2ems.core.segment - a board solved in pieces (the user's plan,
2026-11-20): each signal layer's traces over ideal planes, each signal via (or
a group of them close together: a pair's) a small model of its own with its
pads, antipads and the ground vias round it, and the pieces joined as one
network at the cuts between them.

    python -m kicad2ems.core.segment board.toml --out out/seg
    python -m kicad2ems.core.segment board.kicad_pcb --nets RF,GND --out out/seg

A whole board in one full-wave solve meshes every plane and every trace at once
(a CM5 carrier's F.Cu alone: 165,000 unknowns, more than this machine's RAM).
In pieces, a layer's planes are not meshed at all (mom25d's Green's function
carries them: `--mom-stack ground`), and a via model is a few square
millimetres.

The cuts: a circle round each signal via, its radius clear of the via's pad and
of the largest antipad round it in the planes; every trace that crosses it is
cut there, a port on each side (the layer's piece outside, the via's inside),
the two joined. What the pieces leave out: a plane's apertures under a trace
(the layer's planes are ideal), the cavity between two planes where they are far
from any ground via (a via model's planes end at its box), broadside coupling
between layers.
"""
import copy
import json
import math
import os
import sys

import numpy as np

FORMAT = 'kicad2ems-segment'
RESULT = 'segment.json'
GAP = float(os.environ.get('K2E_SEG_GAP', 0.25))  # mm: the cut circle beyond
# the via's pad and antipads
GROUND_REACH = float(os.environ.get('K2E_SEG_REACH', 1.5))  # mm: a via
# model's box beyond its cut circles
NUDGE = 1e-3      # mm: a cut port's point inside its own piece
PAD_AREA = 0.5    # mm2: a via model's ground copper off its planes kept up to
                  # this (a ground via's pad), the rest (a pour) left out
APART_STRIP_B = 3.0  # a stripline layer's groups: this many plane spacings
# ... a group of more nets than GROUP_MAX grouped again closer, down to this
# many: a Thor carrier's In7 bus chained 64 nets (32.9k unknowns: hours); with
# the board's stitching vias in, its In2 lanes 0.6-0.9 mm (2.2-3.3 b) apart
# coupled at -45 dB at most, 0.45-0.6 mm at -38
GROUP_MAX = int(os.environ.get('K2E_SEG_GROUP_MAX', 24))
APART_FLOOR_B = 2.0
APART_FLOOR_H = 10.0  # ... and a microstrip layer's, in heights
# a group of more nets than this in overlapping windows of CORE_NETS nets and
# their neighbours (0: none). A Thor carrier's In7 bus, 42 nets in one piece:
# 23.8k unknowns, 66 ports, 73 min
WINDOW_NETS = int(os.environ.get('K2E_SEG_WINDOW', 32))
CORE_NETS = 6
END_MARGIN = 0.2  # mm: a via this near beyond its circle to a port: not cut
RUN = 2.0         # mm: two nets are one layer piece where they run that far
                  # within --apart of each other (coupling.groups' run)
PORT = os.environ.get('K2E_SEG_PORT', 'lumped')  # mom25d's ports in the pieces (wave where it can: its feed's
                  # own parasitics de-embedded, which two lumped ports back to
                  # back at a cut are not)


# ---- the network: pieces joined at their ports ----------------------------
def join(blocks, links):
    """Networks joined at pairs of their ports. blocks: [(S (nf, n, n), port
    names)], one reference impedance throughout, the names unique across the
    blocks; links: [(name, name)], each port at most once. Returns (S (nf, m,
    m), the m ports left, in the blocks' order).

    The blocks' S side by side (b = S a); a link makes the wave into each of
    its ports the wave out of the other (a_i = G b_i, G a symmetric
    permutation), so b_e = (S_ee + S_ei G (I - S_ii G)^-1 S_ie) a_e."""
    names, Ss = [], []
    for S, ns in blocks:
        S = np.asarray(S, complex)
        if S.ndim != 3 or S.shape[1] != S.shape[2] or S.shape[1] != len(ns):
            raise ValueError(f"a block of {len(ns)} ports has S {S.shape}")
        names += list(ns)
        Ss.append(S)
    if len(set(names)) != len(names):
        raise ValueError("port names repeat across the blocks")
    nf = Ss[0].shape[0]
    if any(S.shape[0] != nf for S in Ss):
        raise ValueError("the blocks have different frequencies")
    at = {n: i for i, n in enumerate(names)}
    used = [p for pair in links for p in pair]
    if len(set(used)) != len(used):
        raise ValueError("a port is in two links")
    miss = [p for p in used if p not in at]
    if miss:
        raise ValueError(f"no such ports: {', '.join(miss)}")
    # a frequency at a time, the blocks side by side as a sparse matrix: the
    # dense one of every frequency (nf n^2) was 39 GB for a Thor carrier's
    # ~3,500 ports (its pieces', their cuts' corrections), killed at 24 GB
    from scipy import sparse
    from scipy.sparse.linalg import splu
    n = len(names)
    inner = [at[p] for p in used]
    isin = np.zeros(n, bool)
    isin[inner] = True
    outer = np.flatnonzero(~isin)
    pos = {p: j for j, p in enumerate(inner)}
    r = [pos[at[a]] for a, b in links] + [pos[at[b]] for a, b in links]
    c = [pos[at[b]] for a, b in links] + [pos[at[a]] for a, b in links]
    G = sparse.csr_matrix((np.ones(len(r)), (r, c)),
                          shape=(len(inner), len(inner)))
    eye = sparse.identity(len(inner), format='csc')
    out = np.empty((nf, len(outer), len(outer)), complex)
    for f in range(nf):
        big = sparse.block_diag([sparse.csr_matrix(S[f]) for S in Ss],
                                format='csr')
        rows_i, rows_e = big[inner], big[outer]
        S_ii, S_ie = rows_i[:, inner], rows_i[:, outer].toarray()
        S_ei, S_ee = rows_e[:, inner], rows_e[:, outer].toarray()
        if len(inner):
            X = splu((eye - S_ii @ G).tocsc()).solve(S_ie)
            out[f] = S_ee + S_ei @ (G @ X)
        else:
            out[f] = S_ee
    return out, [names[i] for i in outer]


# ---- the cuts ---------------------------------------------------------------
def _pad_radius(m, x, y):
    """The pad radius of the via at model (x, y) (its size / 2), else 0."""
    for v in m.prims.vias:
        if abs(v['x'] - x) < 1e-6 and abs(v['y'] - y) < 1e-6:
            return float(v.get('size') or 0.0) / 2
    return 0.0


def _antipad(m, x, y, reach=3.0):
    """The largest hole round model point (x, y) in a ground plane: the
    distance from it to the ground copper of each layer whose ground copper
    is round it but not at it (an antipad); 0 where none."""
    from shapely.geometry import Point
    pt, r = Point(x, y), 0.0
    for lay, ps in m.copper.items():
        nets = m.copper_nets.get(lay, [])
        gnd = [p for p, n in zip(ps, nets) if n in m.ground_nets]
        if not gnd or any(p.covers(pt) for p in gnd):
            continue
        d = min(p.distance(pt) for p in gnd)
        # round it: a ring of ground within reach on most sides
        if d < reach and any(p.distance(pt) < reach and p.area > 4 * d * d
                             for p in gnd):
            r = max(r, d)
    return r


def _leaves(m, net, x, y, pad, layer):
    """Whether net's copper at (x, y) is more than a via's ring (pad mm) on a
    layer other than `layer`: a track leaves the via there."""
    from shapely.geometry import Point
    pt, ring = Point(x, y), math.pi * (pad + 0.05) ** 2 * 1.5
    for l, ps in m.copper.items():
        if l == layer:
            continue
        for g, n in zip(ps, m.copper_nets.get(l, [])):
            if n == net and g.area > ring and g.distance(pt) < 1e-6:
                return True
    return False


def via_groups(m, nets, gap=GAP, ends=(), keep_out=()):
    """The signal vias of `nets`, each with its cut radius (beyond its pad and
    the largest antipad round it, by gap mm), in groups: vias whose circles
    touch are one model. [dict(vias [dict(x, y, net, r, pad, antipad)],
    circle (shapely, model coordinates))]."""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    vs = []
    for (x, y, _r, _z0, _z1), net in zip(m.vias, m.via_nets):
        if net in nets and net not in m.ground_nets:
            pad, anti = _pad_radius(m, x, y), _antipad(m, x, y)
            r = max(pad, anti) + gap
            near = [math.hypot(x - e[0], y - e[1]) for e in ends]
            # a via where its net ends at a port (a through-hole pad's own
            # barrel): no transition, the port is there. Unless the net's
            # copper leaves it on another layer than the port's (a via in a
            # connector's pad, its track on the far side: skipped, the Thor
            # carrier's DisplayPort lanes were open end to end)
            at = [e for e, dd in zip(ends, near) if dd <= pad + 0.05]
            if at and not any(_leaves(m, net, x, y, pad, e[2] if len(e) > 2
                                      else None) for e in at):
                continue
            # a transition with a port near it (USB-C's A7, 0.65 mm from its
            # via, on the circle): the circle takes the port in, the via model
            # has it (left out, the via had cut its line: an open leg; on the
            # circle, its copper had been cut from under the port)
            close = [dd for dd in near if dd < r + END_MARGIN]
            if close:
                r = max(r, max(close) + END_MARGIN)
            # short of a series part's pads (keep_out: (x, y, half size)): a
            # via model is no place for an AC-coupling capacitor (a Thor
            # carrier's DP0 AUX pair: C322's pad inside its via's circle, the
            # capacitor split between two pieces, the lane open)
            for kx, ky, kh in keep_out:
                dk = math.hypot(x - kx, y - ky) - kh - 0.05
                if dk < r:
                    r = max(dk, pad + 0.05)
            vs.append(dict(x=float(x), y=float(y), net=net, pad=pad,
                           antipad=anti, r=r))
    up = list(range(len(vs)))

    def root(i):
        while up[i] != i:
            up[i] = up[up[i]]
            i = up[i]
        return i
    for i, a in enumerate(vs):
        for j in range(i + 1, len(vs)):
            b = vs[j]
            if math.hypot(a['x'] - b['x'], a['y'] - b['y']) < a['r'] + b['r']:
                up[root(i)] = root(j)
    out = {}
    for i, v in enumerate(vs):
        out.setdefault(root(i), []).append(v)
    return [dict(vias=g, circle=_solid(unary_union(
        [Point(v['x'], v['y']).buffer(v['r'], 64) for v in g])))
        for g in out.values()]


def _solid(g):
    """g without holes: a ring of circles (a connector's rows of vias) closes
    round a gap whose edge had cut tracks with the inside on both sides (a
    Thor camera lane's port on no copper); the gap is the via model's."""
    from shapely.geometry import MultiPolygon, Polygon
    parts = [Polygon(p.exterior) for p in getattr(g, 'geoms', [g])]
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def cuts(kb, group, nets):
    """Where the tracks of `nets` (KiCadBoard kb's, KiCad coordinates) cross a
    group's circle: [dict(layer, net, at (KiCad x, y), out (unit vector away
    from the via, KiCad))]."""
    from shapely.geometry import LineString, Point
    edge = group['circle'].boundary
    # the circle in KiCad coordinates (y down)
    from shapely.affinity import scale
    edge = scale(edge, 1.0, -1.0, origin=(0, 0))
    inside = scale(group['circle'], 1.0, -1.0, origin=(0, 0))
    out = []
    for t in kb.tracks:
        if t['net'] not in nets:
            continue
        pts = np.asarray(t['pts'], float)
        for a, b in zip(pts[:-1], pts[1:]):
            hit = LineString([a, b]).intersection(edge)
            for h in getattr(hit, 'geoms', [hit]):
                if h.is_empty or h.geom_type != 'Point':
                    continue
                q = np.array([h.x, h.y])
                d = (b - a) / (np.linalg.norm(b - a) or 1.0)
                flip = inside.contains(Point(*(q + d * 1e-3)))
                if flip:
                    d = -d  # away from the via
                # the segment's part outside: none where it only ends on the
                # circle (a vertex on it: the next segment's crossing is the
                # cut; counted twice, one port had pointed off the copper, a
                # Thor carrier's camera lanes a four-port star or open)
                if np.linalg.norm((a if flip else b) - q) < 1e-4:
                    continue
                # the layer piece's port clear of the circle: a track that
                # grazes it is barely out 1 um along (a Thor camera lane's
                # port fell inside after the clip: no copper under it)
                room = float(np.linalg.norm((a if flip else b) - q))
                nudge = next((s for s in (NUDGE, 5e-3, 0.02, 0.05)
                              if s < max(room, 0.02)
                              and inside.boundary.distance(
                                  Point(*(q + d * s))) > 1e-4
                              and not inside.contains(Point(*(q + d * s)))),
                             None)
                if nudge is None:
                    continue  # it touches the edge (a notch's tip): no cut
                out.append(dict(layer=t['layer'], net=t['net'],
                                at=(float(q[0]), float(q[1])),
                                out=(float(d[0]), float(d[1])), nudge=nudge))
    return out


def _fp(name, c, d, layer, nudge=NUDGE):
    """A cut port's footprint-like dict (as region.cut_port_fps's): at KiCad
    point c (moved nudge mm along d), pointing along d (KiCad) into its
    piece."""
    q = np.asarray(c, float) + np.asarray(d, float) * nudge
    dm = (d[0], -d[1])
    return dict(ref=name, x=float(q[0]), y=float(q[1]),
                rot=float(math.degrees(math.atan2(dm[1], dm[0])) % 360.0),
                side='B' if layer == 'B.Cu' else 'F',
                props={'port.layer': layer},
                source=f"cut ({c[0]:.3f}, {c[1]:.3f})")


def _piece(m, raw, keep, ports, outline=None, vias=None):
    """A copy of board m with only its copper `keep(layer, net, polygon)` ->
    polygon or None, its vias `vias(net, x, y)` -> bool, its outline, and the
    ports [(name, footprint-like dict)] made on that copper (Board._port)."""
    b = copy.copy(m)
    b.notes = list(m.notes)
    b.copper, b.copper_nets = {}, {}
    for lay, ps in m.copper.items():
        b.copper[lay], b.copper_nets[lay] = [], []
        for p, n in zip(ps, m.copper_nets.get(lay, [])):
            g = keep(lay, n, p)
            if g is None or g.is_empty:
                continue
            for piece in getattr(g, 'geoms', [g]):
                if piece.geom_type == 'Polygon' and piece.area > 1e-6:
                    b.copper[lay].append(piece)
                    b.copper_nets[lay].append(n)
    if outline is not None:
        b.outline = outline
    if vias is not None:
        keepv = [vias(n, v[0], v[1]) for v, n in zip(m.vias, m.via_nets)]
        b.vias = [v for v, k in zip(m.vias, keepv) if k]
        b.via_nets = [n for n, k in zip(m.via_nets, keepv) if k]
    b.ports = [_port_closing(b, raw, name, fp) for name, fp in ports]
    return b


# a stripline piece's ground vias: those within this of its copper (mm)
STITCH_REACH = float(os.environ.get('K2E_SEG_STITCH', 1.0))


def _windows(kb, lay, nets, reach):
    """A group's windows: [(core nets, window nets)]. The nets as units (a
    pair's legs one), ordered breadth first through who is within reach (mm)
    of whom (along a bus: across it), cut into cores of up to CORE_NETS nets;
    a window is its core and every net within reach of it."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    from .si import _legs
    geo = {}
    for t in kb.tracks:
        if t['layer'] == lay and t['net'] in nets and len(t['pts']) > 1:
            geo.setdefault(t['net'], []).append(
                LineString(t['pts']).buffer(t['width'] / 2))
    geo = {n: unary_union(g) for n, g in geo.items()}
    units, seen = [], set()
    for n in nets:
        if n in seen:
            continue
        u = [n] + [o for o in nets if o != n and o not in seen
                   and _legs(n, o)][:1]
        seen.update(u)
        units.append(u)
    near = lambda a, b: a in geo and b in geo and geo[a].distance(
        geo[b]) <= reach
    adj = {i: {j for j, v in enumerate(units) if j != i and any(
        near(a, b) for a in u for b in v)} for i, u in enumerate(units)}
    order, done = [], set()
    while len(done) < len(units):
        start = min((i for i in adj if i not in done),
                    key=lambda i: len(adj[i]))
        queue = [start]
        done.add(start)
        while queue:
            i = queue.pop(0)
            order.append(i)
            for j in sorted(adj[i], key=lambda j: len(adj[j])):
                if j not in done:
                    done.add(j)
                    queue.append(j)
    cores, cur = [], []
    for i in order:
        if cur and sum(len(units[j]) for j in cur) + len(units[i]) > CORE_NETS:
            cores.append(cur)
            cur = []
        cur.append(i)
    if cur:
        cores.append(cur)
    out = []
    for c in cores:
        win = set(c) | {j for i in c for j in adj[i]}
        out.append(({n for i in c for n in units[i]},
                    sorted(n for i in win for n in units[i])))
    return out


def assemble(wins):
    """A group's S from its windows' [(S (nf, n, n), port names, core
    ports)]: each core port's row from its own window; an entry two windows
    have, their mean; one neither has, 0. (S (nf, m, m), the m ports)."""
    names = list(dict.fromkeys(p for _, _, core in wins for p in core))
    at = {p: i for i, p in enumerate(names)}
    nf = wins[0][0].shape[0]
    A = np.zeros((nf, len(names), len(names)), complex)
    D = np.zeros((len(names), len(names)), bool)
    for S, ns, core in wins:
        wi = {p: i for i, p in enumerate(ns)}
        cols = [p for p in ns if p in at]
        J, Jw = [at[p] for p in cols], [wi[p] for p in cols]
        for p in core:
            A[:, at[p], J] = S[:, wi[p], Jw]
            D[at[p], J] = True
    both = D & D.T
    S = np.where(both, 0.5 * (A + A.transpose(0, 2, 1)),
                 np.where(D, A, A.transpose(0, 2, 1)))
    return S, names


def _together(grps, pairs):
    """Groups merged where a pair of nets (a part left in the mesh) would be
    split between two."""
    grps = [list(g) for g in grps]
    for a, b in pairs:
        ia = next((i for i, g in enumerate(grps) if a in g), None)
        ib = next((i for i, g in enumerate(grps) if b in g), None)
        if ia is not None and ib is not None and ia != ib:
            grps[min(ia, ib)] += grps[max(ia, ib)]
            del grps[max(ia, ib)]
    return grps


def _capped(kb, lay, grps, apart, floor, echo=print):
    """Groups of more than GROUP_MAX nets grouped again at 0.8 x the spacing,
    again, down to floor (mm): couplings between nets further apart than the
    spacing that did it are left out (said)."""
    from .coupling import groups as near_groups
    out = []
    for g in grps:
        a = apart
        sub = [g]
        while max(map(len, sub)) > GROUP_MAX and a * 0.8 >= floor - 1e-9:
            a *= 0.8
            sub = [x for y in sub for x in (
                near_groups(kb, lay, y, a, RUN) if len(y) > GROUP_MAX
                else [y])]
        if len(sub) > 1:
            echo(f"segment: {lay}: a group of {len(g)} nets in {len(sub)} "
                 f"(the largest {max(map(len, sub))}) at {a:.2f} mm: their "
                 f"coupling across it left out")
        out += [(x, a) for x in sub]
    return out


def _stitching(m, lay, gnets, refs):
    """A layer piece's vias (_piece's `vias`): none over one plane; between
    two, the ground vias through both within STITCH_REACH of the group's
    copper. The two whole-sheet planes with nothing between them carry a
    parallel-plate mode that every port excites and every port picks up: a
    Thor carrier's In2 camera lanes coupled at -40 to -46 dB at 1.5 mm and
    more, where the board's stitching vias short the planes together."""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    if len(refs) != 2:
        return lambda n, x, y: False
    near = [p for p, n in zip(m.copper.get(lay, []),
                              m.copper_nets.get(lay, [])) if n in gnets]
    if not near:
        return lambda n, x, y: False
    zone = prep(unary_union(near).buffer(STITCH_REACH))
    lo, hi = m.cu_z[refs[0]], m.cu_z[refs[1]]
    ok = {(round(v[0], 4), round(v[1], 4))
          for v, n in zip(m.vias, m.via_nets)
          if n in m.ground_nets and v[3] <= lo + 1e-6 and v[4] >= hi - 1e-6
          and zone.contains(Point(v[0], v[1]))}
    return lambda n, x, y: (round(x, 4), round(y, 4)) in ok


def _port_closing(b, raw, name, fp):
    """Board._port on a piece; where it finds no reference copper (a port on a
    through-hole pin, whose own ring had filled the plane's antipad on the full
    board), the hole under it is closed with the nearest plane's copper, as if
    the plane ran on under it, and the port made again."""
    from shapely.geometry import Point
    try:
        return b._port(raw, name, fp)
    except ValueError as e:
        if 'no reference copper' not in str(e):
            raise
    lay = fp['props'].get('port.layer', 'F.Cu')
    z = b.cu_z[lay]
    at = Point(fp['x'], -fp['y'])
    best = None
    for l in b.cu_order:
        if l == lay:
            continue
        for g, n in zip(b.copper.get(l, []), b.copper_nets.get(l, [])):
            if n in b.ground_nets and g.area > 1.0:
                key = (abs(b.cu_z[l] - z), g.distance(at))
                if best is None or key < best[0]:
                    best = (key, l, n, g)
    if best is None or best[0][1] > 3.0:
        raise ValueError(f"{name}: no reference plane near the port")
    _, l, n, g = best
    disc = at.buffer(g.distance(at) + 0.1)
    b.copper[l] = list(b.copper[l]) + [disc]
    b.copper_nets[l] = list(b.copper_nets[l]) + [n]
    b.notes.append(f"{name}: the hole under it on {l} closed (a through-hole "
                   "pin's)")
    return b._port(raw, name, fp)


def _to_dict(b, piece=None):
    """The piece's board file. A layer piece's prims (what the line model
    builds from) are its own: its nets' tracks and pads on its layer, cut
    outside the via circles as its copper is (uncut, a trace ran on past its
    cut port into the circle: an open stub to the line model), and the other
    layers' ground copper (its reference planes)."""
    from shapely.geometry import LineString, mapping, shape
    d = b.to_dict()
    pr = d.get('prims') or {}
    pr['vias'] = []
    if piece is None or piece.get('kind') != 'layer':
        lays = {l for l, ps in b.copper.items() if ps}
        pr['tracks'] = [t for t in pr.get('tracks', []) if t['layer'] in lays]
        d['prims'] = pr
        return d
    lay, nets, clip = piece['layer'], set(piece['nets']), piece.get('clip')
    ground = set(d.get('ground_nets') or ())
    tracks = []
    for t in pr.get('tracks', []):
        if t['layer'] != lay:
            if t['net'] in ground:
                tracks.append(t)
            continue
        if t['net'] not in nets or len(t['pts']) < 2:
            continue
        g = LineString(t['pts'])
        if clip and t['net'] in clip:
            g = g.difference(clip[t['net']])
        for q in getattr(g, 'geoms', [g]):
            if q.geom_type == 'LineString' and q.length > 1e-6:
                tracks.append(dict(t, pts=[list(c[:2]) for c in q.coords]))
    items = []
    for it in pr.get('items', []):
        if it['layer'] != lay:
            if it['net'] in ground:
                items.append(it)
            continue
        if it['net'] not in nets:
            continue
        g = shape(it['geom'])
        if clip and it['net'] in clip:
            g = g.difference(clip[it['net']])
        for q in getattr(g, 'geoms', [g]):
            if q.geom_type == 'Polygon' and q.area > 1e-9:
                items.append(dict(it, geom=mapping(q)))
    pr['tracks'], pr['items'] = tracks, items
    d['prims'] = pr
    return d


# ---- the pieces --------------------------------------------------------------
def _height(m, layer):
    """A layer's height over its nearest plane (a layer whose ground copper
    covers half the board or more), mm; 0 where there is none."""
    half = 0.5 * m.outline.area
    zs = [m.cu_z[l] for l, ps in m.copper.items() if l != layer and sum(
        p.area for p, n in zip(ps, m.copper_nets.get(l, []))
        if n in m.ground_nets) >= half]
    return min((abs(z - m.cu_z[layer]) for z in zs), default=0.0)


def pieces(m, raw, kb, nets, apart_h=None, echo=print):
    """The board m (its own ports the external ones) in pieces (a layer's
    nets in groups further apart than apart_h heights over its plane:
    coupling.APART_H by default, 0 one piece a layer): [dict(kind
    'layer' | 'via', name, board (a Board), links [(cut port, its partner's
    name)])], and the cuts. A layer piece: that layer's signal copper outside
    every cut circle, the ground copper of the other layers (its planes: ideal
    in the solve), no vias, its external ports and a port at each cut. A via
    piece: its group's signal copper inside the circle on every layer, the
    vias in its box (its own and the ground vias round it), the ground copper
    clipped to its box (the box its outline), a port at each cut."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    from shapely.affinity import scale
    signal = set(nets) - set(m.ground_nets)
    sref = {ref for ref, _, _ in series_links(kb, signal)}
    keep_out = [(float(pd['xy'][0]), -float(pd['xy'][1]),
                 0.5 * max(pd['geom'].bounds[2] - pd['geom'].bounds[0],
                           pd['geom'].bounds[3] - pd['geom'].bounds[1]))
                for fp in kb.footprints if fp['ref'] in sref
                for pd in fp['pads'] if pd.get('geom') is not None]
    groups = via_groups(m, signal,
                        ends=[(float(p.c[0]), float(p.c[1]), p.layer)
                              for p in m.ports], keep_out=keep_out)
    # each net's circles: those of the groups holding one of its vias (a trace
    # passing through another net's circle stays whole: cut by every circle,
    # a pair's leg through its twin's lost its copper there unjoined)
    clip = {}
    for g in groups:
        for n in {v['net'] for v in g['vias']}:
            clip.setdefault(n, []).append(g['circle'])
    clip = {n: unary_union(cs) for n, cs in clip.items()}
    allcuts = []
    for gi, g in enumerate(groups):
        for c in cuts(kb, g, {v['net'] for v in g['vias']}):
            c['group'] = gi
            c['k'] = len(allcuts) + 1
            allcuts.append(c)
    # each series part a joint: a port on each of its pads (in whichever
    # pieces their nets are), joined through the part's own series R L C;
    # the part out of the pieces' meshes. (Its nets had been put in one piece:
    # a Thor carrier's F.Cu, where the AC-coupling capacitors sit, became one
    # piece of 222 ports, 62 min)
    k = len(allcuts)
    valued = {q.ref for q in m.parts}
    stay = []  # (net, net) of the parts left in the mesh: one group each
    for fp in kb.footprints:
        if fp['ref'] not in sref or fp['ref'] not in valued:
            continue
        pads = [pd for pd in fp['pads'] if pd.get('net') in signal]
        lay = 'B.Cu' if fp.get('side') == 'B' else 'F.Cu'
        if len(pads) != 2:
            continue
        # a pad in a via's circle: its port the via model's (a resistor's pad
        # on a via had been left in the mesh with the via's circle round it:
        # the Thor's PCIe C2x1 clock and DP0 AUX lanes open end to end)
        inside = [next((gi for gi, g in enumerate(groups)
                        if pd['net'] in {v['net'] for v in g['vias']}
                        and g['circle'].contains(
                            _pt(pd['xy'][0], -pd['xy'][1]))), None)
                  for pd in pads]
        k += 1
        a, b = (np.asarray(pd['xy'], float) for pd in pads)
        for pd, me, other, tag, gi in ((pads[0], a, b, 1000, inside[0]),
                                       (pads[1], b, a, 20000, inside[1])):
            d = (me - other) / (np.linalg.norm(me - other) or 1.0)
            c = dict(k=k, layer=lay, net=pd['net'],
                     at=(float(me[0]), float(me[1])),
                     out=(float(d[0]), float(d[1])), nudge=0.0,
                     part=fp['ref'], port=f"P{tag + k}")
            if gi is not None:
                c.update(group=gi, via_only=True)
            allcuts.append(c)
    joined = {c['part'] for c in allcuts if c.get('part')}
    out = []
    # the layers: every layer with signal copper
    # (outside the circles: a via's ring on a plane layer is the via's)
    sig_layers = [
        l for l in m.cu_order
        if any(n in signal and (n not in clip
                                or p.difference(clip[n]).area > 1e-6)
               for p, n in zip(m.copper.get(l, []), m.copper_nets.get(l, [])))
        or any(p.layer == l for p in m.ports)]
    ext = {p.name: p for p in m.ports}
    from .coupling import APART_H, groups as near_groups
    half = 0.5 * m.outline.area
    plane_layers = [l for l in m.cu_order if sum(
        q.area for q, n in zip(m.copper.get(l, []), m.copper_nets.get(l, []))
        if n in m.ground_nets) >= half]

    def refs_of(lay):
        """The planes next to a signal layer: the nearest below, above."""
        z = m.cu_z[lay]
        below = [l for l in plane_layers if m.cu_z[l] < z - 1e-9]
        above = [l for l in plane_layers if m.cu_z[l] > z + 1e-9]
        return ({max(below, key=lambda l: m.cu_z[l])} if below else set()) | (
            {min(above, key=lambda l: m.cu_z[l])} if above else set())
    net_of_cut = {c['k']: c['net'] for c in allcuts if not c.get('part')}

    def layer_piece(lay, gnets, name, refs):
        """The piece of layer lay's nets gnets (over its planes), or None
        where it has no ports (slivers of a via's ring)."""
        mine = [c for c in allcuts if c['layer'] == lay
                and c['net'] in gnets and not c.get('via_only')]
        ports = [(c.get('port', f"P{1000 + c['k']}"),
                  _fp(f"cutL{c['k']}", c['at'], c['out'], lay,
                      c.get('nudge', NUDGE)))
                 for c in mine]
        fps = [(p.name, _port_fp(p)) for p in ext.values()
               if p.layer == lay and getattr(p, 'net', None) in gnets
               and (p.net not in clip
                    or not clip[p.net].contains(_pt(*p.c[:2])))]
        planes = refs_of(lay)

        def keep(l, n, p, done=set()):
            if l == lay:
                if n not in gnets:
                    return None
                return p.difference(clip[n]) if n in clip else p
            # the planes next to the layer, each a whole sheet (the ideal
            # planes the piece is solved over), the rest of the board left
            # out (beyond them). The planes as imported: a split or
            # perforated one is not 'solid' to mom25d, which then meshed
            # every plane above a stripline (a Thor carrier's In7 bus:
            # 625,819 unknowns)
            if l in planes and n in m.ground_nets and l not in done:
                done.add(l)
                return m.outline
            return None
        if not fps and not ports:
            return None
        b = _piece(m, raw, keep, fps + ports,
                   vias=_stitching(m, lay, gnets, refs))
        b.parts = [q for q in b.parts if q.ref not in joined]
        return dict(kind='layer', name=name, layer=lay, board=b,
                    links=[(c.get('port', f"P{1000 + c['k']}"), c['k'])
                           for c in mine],
                    part_net={c['port']: c['net'] for c in mine
                              if c.get('part')},
                    external=[n for n, _ in fps],
                    nets=sorted(gnets), clip=clip)
    for lay in sig_layers:
        on = sorted({n for p, n in zip(m.copper.get(lay, []),
                                       m.copper_nets.get(lay, []))
                     if n in signal})
        h = _height(m, lay)
        refs = sorted(refs_of(lay), key=lambda l: m.cu_z[l])
        if apart_h is not None:
            apart = apart_h * h
        elif len(refs) == 2:
            # stripline: its crosstalk falls as exp(-pi s / b), b the planes'
            # spacing (under 1e-4 at 3 b); microstrip's as 1 / (s/h)^2 (20
            # heights). At 20 heights a Thor carrier's In7 bus was one piece
            # of 54 nets, 28k unknowns; at 3 b its largest is 20
            apart = APART_STRIP_B * (m.cu_z[refs[1]] - m.cu_z[refs[0]])
        else:
            apart = APART_H * h
        grps = (near_groups(kb, lay, on, apart, RUN)
                if len(on) > 1 and apart > 0 else [on])
        if apart_h is None:
            # (microstrip as well: the Thor's F.Cu breakouts within 20
            # heights of each other chained 90 nets across the board, 237
            # ports, 62 min)
            sized = _capped(kb, lay, grps, apart, APART_FLOOR_B * (
                m.cu_z[refs[1]] - m.cu_z[refs[0]]) if len(refs) == 2
                else APART_FLOOR_H * h, echo)
        else:
            sized = [(g, apart) for g in grps]
        reach = {n: a_ for g, a_ in sized for n in g}
        grps = _together([g for g, _ in sized], stay)
        for gi, gnets in enumerate(grps):
            name = _safe(lay) + (f"_g{gi + 1}" if len(grps) > 1 else '')
            if WINDOW_NETS and len(gnets) > WINDOW_NETS:
                # a big group in overlapping windows: each a core of a few
                # pairs and its neighbours within the group's spacing,
                # assembled into the group's S (run: assemble)
                wins = _windows(kb, lay, sorted(gnets),
                                min(reach.get(n, apart) for n in gnets))
                echo(f"segment: {name}: {len(gnets)} nets in {len(wins)} "
                     f"windows (the largest {max(len(w) for _, w in wins)} "
                     "nets)")
                for wi, (core, wnets) in enumerate(wins):
                    pc = layer_piece(lay, set(wnets), f"{name}_w{wi + 1}",
                                     refs)
                    if pc is None:
                        continue
                    pc['window_of'] = name
                    pc['core_ports'] = [
                        n for n, k in pc['links']
                        if (pc['part_net'].get(n) or net_of_cut.get(k))
                        in core] + [n for n in pc['external']
                                    if ext[n].net in core]
                    out.append(pc)
                continue
            pc = layer_piece(lay, set(gnets), name, refs)
            if pc is not None:
                out.append(pc)
    for gi, g in enumerate(groups):
        mine = [c for c in allcuts if c.get('group') == gi]
        gnets = {v['net'] for v in g['vias']}
        circle = g['circle']
        x0, y0, x1, y1 = circle.buffer(GROUND_REACH).bounds
        bx = box(x0, y0, x1, y1)
        ports = [(c.get('port', f"P{1000 + c['k']}"),
                  _fp(f"cutV{c['k']}", c['at'], c['out'], c['layer'], 0.0)
                  if c.get('via_only') else
                  _fp(f"cutV{c['k']}", c['at'], (-c['out'][0], -c['out'][1]),
                      c['layer'])) for c in mine]
        # the board's own ports inside its circle (a pad close to the via)
        own = [(p.name, _port_fp(p)) for p in ext.values()
               if p.net in gnets and circle.contains(_pt(*p.c[:2]))]

        # the planes in the box: layers whose ground covers half of it; a
        # signal layer's pour left out as the layer pieces leave it (in via1
        # of a CM5 carrier it was 9,100 of 19,445 triangles), a ground via's
        # small pad kept
        planes = {l for l, ps in m.copper.items() if sum(
            p.intersection(bx).area for p, n in zip(ps, m.copper_nets.get(
                l, [])) if n in m.ground_nets) >= 0.5 * bx.area}

        def keep(l, n, p, circle=circle, gnets=gnets, bx=bx, planes=planes):
            if n in gnets:
                return p.intersection(circle)
            if n in m.ground_nets:
                g = p.intersection(bx)
                return g if l in planes or g.area <= PAD_AREA else None
            return None
        b = _piece(m, raw, keep, own + ports, outline=bx,
                   vias=lambda n, x, y, bx=bx, gnets=gnets:
                   (n in gnets or n in m.ground_nets)
                   and bx.buffer(-1e-6).contains(_pt(x, y)))
        out.append(dict(kind='via', name=f"via{gi + 1}", board=b,
                        links=[(c.get('port', f"P{1000 + c['k']}"), c['k'])
                               for c in mine],
                        external=[n for n, _ in own], vias=g['vias']))
    del scale
    return out, allcuts


def _pt(x, y):
    from shapely.geometry import Point
    return Point(x, y)


def _port_fp(p):
    """An external port as its footprint-like dict again, from the Port (its
    position and direction, model coordinates): made anew on the piece."""
    c, d = np.asarray(p.c, float), np.asarray(p.d, float)
    # (its point was moved just inside the trace end: the footprint's own
    # position was the end; within the trace either way)
    return dict(ref=p.name, x=float(c[0]), y=float(-c[1]),
                rot=float(math.degrees(math.atan2(d[1], d[0])) % 360.0),
                side='B' if p.layer == 'B.Cu' else 'F',
                props={'port.layer': p.layer}, source=f"port {p.name}")


def _safe(layer):
    import re
    return re.sub(r'[^A-Za-z0-9]+', '_', layer).strip('_')


CAL_HALF = 2.0  # mm: the calibration line's half (a cut's correction)


def calibration_pieces(m, raw, layer, w, net):
    """Two straight lines of width w on `layer` over the board's own planes
    (each plane layer's ground a box round them), a lumped port at each end:
    the whole (2 CAL_HALF mm) and its half. A cut joins two such ports back to
    back, which a whole trace is not: X = A^-1 W B^-1 (ABCD, B the half
    mirrored) between them makes the two halves the whole (junction)."""
    from shapely.geometry import box
    L = 2 * CAL_HALF
    big = box(-2.0, -2.0 - 10 * w, L + 2.0, 2.0 + 10 * w)
    planes = [l for l in m.cu_order if l != layer and any(
        n in m.ground_nets for n in m.copper_nets.get(l, []))]
    gnd = sorted(m.ground_nets)[0] if m.ground_nets else 'GND'
    out = []
    for length in (L, CAL_HALF):
        strip = box(0.0, -w / 2, length, w / 2)
        b = copy.copy(m)
        b.notes = []
        b.copper = {l: ([strip] if l == layer else [big] if l in planes else
                        []) for l in m.copper}
        b.copper_nets = {l: ([net] if l == layer else [gnd] if l in planes
                             else []) for l in m.copper}
        b.outline, b.vias, b.via_nets = big, [], []
        ports = [('P1', _fp('cal1', (0.0, 0.0), (1.0, 0.0), layer)),
                 ('P2', _fp('cal2', (length, 0.0), (-1.0, 0.0), layer))]
        b.ports = [b._port(raw, name, fp) for name, fp in ports]
        out.append(dict(kind='layer', name=f"cal_{_safe(layer)}_{w:.3f}_"
                        f"{'whole' if length == L else 'half'}",
                        layer=layer, board=b, links=[], external=[]))
    return out


def series_block(f, part, z0=50.0):
    """A series part's S (nf, 2, 2) between two z0 ports: its R, L and C in
    series (a 0 ohm link: a through)."""
    w = 2 * np.pi * np.asarray(f, float)
    Z = np.zeros(len(w), complex)
    if part.R:
        Z += part.R
    if part.L:
        Z += 1j * w * part.L
    if part.C:
        Z += 1 / (1j * w * part.C)
    S = np.empty((len(w), 2, 2), complex)
    S[:, 0, 0] = S[:, 1, 1] = Z / (Z + 2 * z0)
    S[:, 0, 1] = S[:, 1, 0] = 2 * z0 / (Z + 2 * z0)
    return S


def unrouted(parts, allcuts, port_nets):
    """The channels (port_nets' 'net') whose two end ports' pieces are not
    joined through cuts on the channel's own nets: open end to end before
    anything is solved (a via left out, a port on a layer its net's copper
    does not reach). [(channel, [its ports' pieces])]."""
    where = {}
    for p in parts:
        for n in p.get('external', []):
            where[n] = p['name']
    of_cut = {}
    for p in parts:
        for _, k in p.get('links', []):
            of_cut.setdefault(k, []).append(p['name'])
    net_of = {c['k']: c['net'] for c in allcuts}
    by = {}
    for x in port_nets:
        by.setdefault(x['net'], []).append(x)
    out = []
    for ch, xs in by.items():
        mem = set(xs[0].get('nets') or [ch])
        up = {}

        def root(a):
            up.setdefault(a, a)
            while up[a] != a:
                up[a] = up[up[a]]
                a = up[a]
            return a
        for k, ps in of_cut.items():
            if net_of.get(k) in mem:
                for q in ps[1:]:
                    up[root(ps[0])] = root(q)
        got = [where.get(x['port']) for x in xs]
        if len(got) == 2 and (None in got or root(got[0]) != root(got[1])):
            out.append((ch, got))
    return out


def junction(fW, SW, fA, SA):
    """A cut's correction (S, 2 x 2 at 50 ohm) from the calibration's whole
    line W and its half A (calibration_pieces): X = A^-1 W B^-1, B the half
    mirrored (ports swapped)."""
    from .deembed import abcd_to_s, s_to_abcd
    if len(fW) != len(fA) or np.abs(fW - fA).max() > 1e-3 * fW.max():
        raise ValueError("the calibration's lines have other frequencies")
    TA, TW = s_to_abcd(SA, 50.0), s_to_abcd(SW, 50.0)
    SB = SA[:, ::-1, ::-1]
    TB = s_to_abcd(SB, 50.0)
    X = np.linalg.solve(TA, TW) @ np.linalg.inv(TB)
    return abcd_to_s(X, 50.0)


def solve_piece(piece, folder, band, solver='mom25d', echo=print,
                thickness=None, dmax=None, layer_solver=None, via_solver=None,
                edges=None):
    """A piece's run (its board file, kicad2ems's pipeline): (S at 50 ohm,
    its ports' names, the run's summary)."""
    from .config import build_settings
    from .pipeline import Job
    from .coupling import _over_its_plane
    os.makedirs(folder, exist_ok=True)
    d = _to_dict(piece['board'], piece)
    if piece['kind'] == 'layer' and layer_solver == 'auto':
        solver = _layer_choice(piece, d, band, thickness, dmax, edges, echo)
    elif piece['kind'] == 'layer' and layer_solver:
        solver = layer_solver
    elif piece['kind'] == 'via' and via_solver:
        solver = via_solver
    if solver == 'pbm':
        # the physics-based via model (viamodel.py): no mesh, no run
        from . import viamodel
        from .touchstone import write_touchstone
        f = np.linspace(band['fmin'], band['fmax'], int(band['points'])) * 1e9
        model = viamodel.build(d, piece['vias'])
        S, names = viamodel.solve(model, f)
        ts = os.path.join(folder, f"{piece['name']}_pbm.s{len(names)}p")
        write_touchstone(ts, f, S, 50.0,
                         comment=f"kicad2ems viamodel: ports {' '.join(names)}")
        return f, S, names, dict(ports=names, solved={'pbm': dict(
            touchstone=os.path.basename(ts))}, model=dict(
                planes=model['planes'], vias=len(model['sig']),
                ground_vias=len(model['gv'])))
    cli = dict(out=folder, solvers=solver, no_report=True, **band)
    if solver == 'mom25d':
        cli['mom_port'] = PORT
        if thickness:
            cli['mom_thickness'] = thickness
        if piece['kind'] == 'layer':
            ref = _over_its_plane(d, piece['layer'])
            if ref:
                cli['mom_stack'] = 'ground'
                echo(f"{piece['name']}: over {ref}"
                     + (" (turned over)" if d.get('turned') else ''))
        else:
            # the via's whole stack in its box, every plane meshed (planes
            # 'holes' is not taken on a whole stack, and asking for it had
            # turned the copper's thickness off: every via model solved as
            # sheets)
            cli['mom_stack'] = 'whole'
    if solver == 'mom25d' and dmax:
        cli['mom_dmax'] = dmax
    if solver == 'mom25d' and edges:
        cli['mom_edges'] = edges
    bj = os.path.join(folder, f"{piece['name']}_board.json")
    # a piece solved before from the same board file and settings: its result
    # again (a run that stopped at a later piece is resumed where it stopped)
    import hashlib
    text = json.dumps(d, sort_keys=True, default=str)
    key = hashlib.sha256((text + json.dumps(cli, sort_keys=True, default=str))
                         .encode()).hexdigest()
    stamp = os.path.join(folder, 'piece.sha256')
    cli['board'] = bj
    name = piece['name']
    done = os.path.join(folder, f"{name}_run.json")
    if (os.path.isfile(stamp) and os.path.isfile(done)
            and open(stamp).read().strip() == key):
        echo(f"{name}: solved before, its result again")
        return _read_piece(folder, name)
    if os.path.isfile(stamp):
        os.remove(stamp)
    with open(bj, 'w', encoding='utf-8') as fh:
        fh.write(text)
    if os.environ.get('K2E_SEG_INPROCESS'):
        job = Job(build_settings(cli), echo=echo)
        job.run()
    else:
        # a process of its own: the memory a piece took is the system's again
        # before the next (in one process the next had 20 of 24 GB)
        import subprocess
        argv = [sys.executable, '-m', 'kicad2ems.core.cli', bj]
        for k, v in cli.items():
            if k == 'board':
                continue
            flag = '--' + k.replace('_', '-')
            if v is True:
                argv.append(flag)
            elif v not in (None, False):
                argv += [flag, str(v)]
        with open(os.path.join(folder, 'run.log'), 'w') as log:
            # a direct factor wherever it fits (the block low-rank one did not
            # converge on a CM5 layer piece at 1-1.5 GHz, 29k and 42k
            # unknowns; the direct solved it, 49 min): the user's own
            # thresholds kept
            env = dict(os.environ, PYTHONUNBUFFERED='1')
            env.setdefault('MOM25D_BLR_MIN_N', '1000000')
            env.setdefault('MOM25D_BLR_CPU_MIN_N', '1000000')
            p = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                               env=env)
        if p.returncode:
            with open(os.path.join(folder, 'run.log')) as fh:
                tail = fh.read()[-1500:]
            raise ValueError(f"{piece['name']}: its run ended with code "
                             f"{p.returncode}: {tail}")
        with open(os.path.join(folder, 'run.log')) as fh:
            for line in fh:
                if line.startswith(('mom25d: ', 'WARNING')):
                    echo(line.rstrip()[:300])
    out = _read_piece(folder, name)
    with open(stamp, 'w') as fh:
        fh.write(key)
    return out


# --layer-solver auto: mom25d up to this many unknowns, the line model past
# them. A Thor carrier's In7 bus (64 striplines chained 3 plane spacings
# apart, 850 mm2 of copper) meshed to 32k with coarse edges: past the GPU
# hybrid, a matrix-free refinement of 130 ports, hours; a 15.6k piece of 158
# ports took 14 minutes
LAYER_MOM_MAX = int(os.environ.get('K2E_SEG_MOM_MAX', 20000))
# ... a piece's signal copper below this (mm2) is not meshed to see: the
# Thor's largest under it was 8k unknowns
LAYER_SMALL = 100.0


def _layer_choice(piece, d, band, thickness, dmax, edges, echo):
    """A layer piece's solver for --layer-solver auto: mom25d where its mesh
    (as it would be solved: over its plane, these edges) has at most
    LAYER_MOM_MAX unknowns, else the line model."""
    import copy
    from .coupling import _over_its_plane
    b = piece['board']
    area = sum(q.area for l, ps in b.copper.items()
               for q, n in zip(ps, b.copper_nets[l]) if n not in b.ground_nets)
    if area < LAYER_SMALL:
        return 'mom25d'
    from kicad2ems.mom25d.api import build_board
    dd = copy.deepcopy(d)
    _over_its_plane(dd, piece['layer'])
    _, sim = build_board(dd, band['fmin'] * 1e9, band['fmax'] * 1e9,
                         dmax=(dmax or 0.5) * 1e-3,
                         port='via' if PORT == 'lumped' else PORT,
                         thickness=thickness or 'sides',
                         edges=edges or 'fine', mesh_only=True,
                         log=lambda *a: None)
    n = sim.Ne + sim.Nv
    pick = 'mom25d' if n <= LAYER_MOM_MAX else 'lines'
    echo(f"{piece['name']}: {n} unknowns -> {pick}"
         + (f" (past {LAYER_MOM_MAX})" if pick == 'lines' else ''))
    return pick


def _read_piece(folder, name):
    """A piece's run read back: (f, S at 50 ohm, its ports, its summary)."""
    from .touchstone import read_touchstone
    from .si import renormalised
    with open(os.path.join(folder, f"{name}_run.json"),
              encoding='utf-8') as fh:
        sm = json.load(fh)
    solved = {k: r for k, r in sm.get('solved', {}).items()
              if r.get('touchstone')}
    if not solved:
        raise ValueError(f"{name}: no solver gave S-parameters "
                         f"({sm.get('warnings')})")
    key = next(iter(solved))
    f, S = read_touchstone(os.path.join(folder, solved[key]['touchstone']))
    S = renormalised(S, sm.get('zref') or 50.0, 50.0)
    return f, S, list(sm['ports']), sm


def run(a, echo=print):
    """The board in pieces, solved and joined: the external ports' S
    (Touchstone) and segment.json."""
    import time
    from .config import build_settings
    from .importers.kicad import KiCadBoard
    from .pipeline import board_from_settings
    from .touchstone import write_touchstone
    t0 = time.time()
    base = dict(board=a.board)
    if a.nets:
        base['nets'] = a.nets.split(',')
    if a.config:
        from .config import read_toml
        values = read_toml(a.config)[0]
        base = dict(values, **{k: v for k, v in base.items()
                               if k != 'board' or v})
    s = build_settings(base)
    raw = KiCadBoard(s.board)
    ends = None
    if a.ends or not _has_ports(s):
        # no ports of its own: one on each pad where a net's tracks end
        specs, ends = end_ports(raw, a.nets or ','.join(s.nets or []))
        if not specs:
            raise ValueError("no net ends on pads to put ports at (--nets)")
        base = dict(base, ports=specs, nets=['auto'])
        s = build_settings(base)
        echo(f"segment: {len(specs)} ports at the nets' ends on pads")
    m = board_from_settings(s, log=lambda *x: None)
    nets = set(m.sel or [n for ns in m.copper_nets.values() for n in ns])
    parts, allcuts = pieces(m, raw, raw, nets, a.apart, echo=echo)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    band = dict(fmin=a.fmin or s.fmin, fmax=a.fmax or s.fmax,
                points=a.points or s.points)
    if ends:
        bad = unrouted(parts, allcuts, ends)
        if bad:
            echo(f"segment: WARNING: {len(bad)} channel leg(s) with no path "
                 "end to end through the pieces (open in the result): "
                 + ", ".join(f"{c} ({' / '.join(map(str, g))})"
                             for c, g in bad[:8])
                 + (" ..." if len(bad) > 8 else ''))
    echo(f"segment: {sum(p['kind'] == 'layer' for p in parts)} layer(s), "
         f"{sum(p['kind'] == 'via' for p in parts)} via model(s), "
         f"{len(allcuts)} cut(s)")
    blocks, links, f0, info, cut_of = [], {}, None, [], {}
    wins, win_links = {}, {}
    for p in parts:
        t = time.time()
        try:
            f, S, names, sm = solve_piece(p, os.path.join(out, p['name']),
                                          band, a.solver, echo,
                                          thickness=a.thickness,
                                          dmax=a.dmax,
                                          layer_solver=a.layer_solver,
                                          via_solver=a.via_solver,
                                          edges=a.edges)
        except ValueError as e:
            raise ValueError(
                f"piece {p['name']} ({p['kind']}"
                + (f", {len(p.get('nets', []))} nets" if p.get('nets') else '')
                + f", {len(p['board'].ports)} ports) did not solve: {e}"
                + (" - smaller pieces: --apart" if 'MemoryError' in str(e)
                   else '')) from None
        if f0 is None:
            f0 = f
        elif len(f) != len(f0) or np.abs(f - f0).max() > 1e-3 * f0.max():
            raise ValueError(f"{p['name']}: its frequencies are not the "
                             "others'")
        if p.get('window_of'):
            # (a window: its group's S assembled after the pieces)
            g = p['window_of']
            wins.setdefault(g, []).append((S, list(names),
                                           set(p['core_ports'])))
            for port, k in p['links']:
                if port in p['core_ports']:
                    win_links.setdefault(g, []).append((port, k))
        else:
            gl = [f"{p['name']}:{n}" for n in names]
            blocks.append((S, gl))
            for port, k in p['links']:
                links.setdefault(k, []).append(f"{p['name']}:{port}")
                cut_of[(p['name'], port)] = k
        got = next(iter(sm.get('solved', {}).items()), (None, {}))
        info.append(dict(piece=p['name'], kind=p['kind'], ports=names,
                         seconds=round(time.time() - t, 1), solver=got[0],
                         unknowns=(got[1] or {}).get('unknowns')))
        echo(f"{p['name']}: {len(names)} ports, {time.time() - t:.0f} s")
    for g, ws in wins.items():
        S, names = assemble(ws)
        blocks.append((S, [f"{g}:{n}" for n in names]))
        for port, k in win_links.get(g, []):
            links.setdefault(k, []).append(f"{g}:{port}")
            cut_of[(g, port)] = k
        echo(f"{g}: {len(names)} ports assembled from {len(ws)} windows")
    # each cut's correction, by its cross-section (layer, width): two
    # lumped ports back to back are not the trace they cut
    widths = {}
    for p in parts:
        if p['kind'] != 'layer':
            continue
        for port in p['board'].ports:
            k = cut_of.get((p['name'], port.name))
            if k is not None:
                widths[k] = (p['layer'], round(float(port.w), 3))
    cal = {}
    for key in sorted({v for v in widths.values()}):
        lay, w = key
        if not a.calibrate:
            break
        net = next(n for n in m.copper_nets.get(lay, [])
                   if n not in m.ground_nets)
        got = []
        for cp in calibration_pieces(m, raw, lay, w, net):
            f, S, names, _ = solve_piece(
                cp, os.path.join(out, 'calibration', cp['name']), band,
                a.solver, lambda *x: None)
            got.append((f, S))
        cal[key] = junction(got[0][0], got[0][1], got[1][0], got[1][1])
        echo(f"calibration: {lay}, {w:.3f} mm wide: a cut's correction "
             f"|S11| up to {np.abs(cal[key][:, 0, 0]).max():.3f}")
    part_of = {c['k']: c['part'] for c in allcuts if c.get('part')}
    parts_by = {q.ref: q for q in m.parts}
    miss = sorted({r for r in part_of.values() if r not in parts_by})
    if miss:
        raise ValueError(f"series parts with no value in the model: {miss}")
    pairs = []
    for k, sides in links.items():
        # (sides, not ends: ends is the external ports' list, written below)
        if len(sides) != 2:
            raise ValueError(f"cut {k}: {len(sides)} sides "
                             f"({', '.join(sides)})")
        X = (series_block(f0, parts_by[part_of[k]]) if k in part_of
             else cal.get(widths.get(k)))
        if X is None:
            pairs.append(tuple(sides))
            continue
        xa, xb = f"cut{k}:a", f"cut{k}:b"
        blocks.append((X, [xa, xb]))
        pairs += [(sides[0], xa), (xb, sides[1])]
    S, names = join(blocks, pairs)
    ext = [n.split(':', 1)[1] for n in names]
    order = sorted(range(len(ext)), key=lambda i: _num(ext[i]))
    S, ext = S[:, order][:, :, order], [ext[i] for i in order]
    name = os.path.splitext(os.path.basename(s.board))[0]
    ts = os.path.join(out, f"{name}_segment.s{len(ext)}p")
    write_touchstone(ts, f0, S, 50.0,
                     comment=f"kicad2ems segment: ports {' '.join(ext)}")
    report = None
    if ends:
        try:
            report = pairs_report(f0, S, ext, ends)
        except Exception as e:  # a figure never fails the run  # noqa: BLE001
            echo(f"segment: no per-pair figures ({type(e).__name__}: {e})")
    # where the pieces are, for the overlay (KiCad mm, y down)
    via_models = [dict(name=p['name'], ports=len(p['board'].ports),
                       vias=[dict(x=v['x'], y=-v['y'], r=v['r'], net=v['net'])
                             for v in p.get('vias', [])])
                  for p in parts if p['kind'] == 'via']
    layer_pieces = [dict(name=p['name'], layer=p['layer'],
                         nets=p.get('nets', []), ports=len(p['board'].ports))
                    for p in parts if p['kind'] == 'layer']
    res = dict(format=FORMAT, version=1, board=os.path.abspath(s.board),
               ports=ext, port_nets=ends, report=report,
               via_models=via_models, layer_pieces=layer_pieces,
               touchstone=os.path.relpath(ts, out),
               cuts=[dict(k=c['k'], layer=c['layer'], net=c['net'],
                          at=c['at']) for c in allcuts],
               pieces=info, seconds=round(time.time() - t0, 1))
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    if report:
        _echo_report(report, echo)
        try:
            ov = overlay(os.path.join(out, RESULT), raw)
            echo(f"segment: overlay -> {ov['html']} (and .json, .png)")
        except Exception as e:  # a figure never fails the run  # noqa: BLE001
            echo(f"segment: no overlay ({type(e).__name__}: {e})")
    echo(f"segment: {len(ext)} ports ({', '.join(ext)}) -> {ts} "
         f"({time.time() - t0:.0f} s)")
    return res


def _has_ports(s):
    """The settings name ports, or the board has port footprints."""
    if getattr(s, 'ports', None):
        return True
    try:
        from .pipeline import board_from_settings
        board_from_settings(s, log=lambda *x: None)
        return True
    except ValueError:
        return False


# a series part joining two nets of one channel (an AC-coupling capacitor, a
# 0 ohm or damping resistor, a ferrite): its reference's letters
SERIES_REFS = ('C', 'R', 'L', 'FB')


def series_links(kb, nets):
    """The two-pad series parts whose pads are on two of `nets` (a set):
    [(ref, net, net)]. A Thor carrier's 158 high-speed nets had 100 (88 AC-
    coupling capacitors, 12 resistors): each lane two nets, its ends at the
    capacitor's pads."""
    import re
    out = []
    for fp in kb.footprints:
        pads = [p for p in fp.get('pads', []) if p.get('net')]
        ns = {p['net'] for p in pads}
        head = re.match(r'[A-Za-z]+', str(fp.get('ref', '')))
        if (len(fp.get('pads', [])) == 2 and len(ns) == 2 and ns <= nets
                and head and head.group(0).upper() in SERIES_REFS):
            a, b = sorted(ns)
            out.append((fp['ref'], a, b))
    return out


def chains(kb, nets):
    """The nets joined by series parts (series_links) as channels: {net: its
    chain's nets (sorted)}; a net with none is its own."""
    up = {n: n for n in nets}

    def root(n):
        while up[n] != n:
            up[n] = up[up[n]]
            n = up[n]
        return n
    for _, a, b in series_links(kb, set(nets)):
        up[root(a)] = root(b)
    groups = {}
    for n in nets:
        groups.setdefault(root(n), []).append(n)
    return {n: sorted(groups[root(n)]) for n in nets}


def end_ports(kb, nets):
    """Port specs at the two ends of each net (fnmatch patterns, comma-
    separated; ground nets left out): of the net's pads that its own tracks or
    vias reach, the two furthest apart, so a pad off the line (an ESD diode's,
    a connector's twin pin) is left open, not a 50 ohm load. ([spec],
    [dict(port, net, ref, pad, layer)]), a pair's legs side by side. (The
    stretches' ends had missed an end on a short piece of track apart from
    the net's longest on its layer, and had made every pad end a port.)"""
    from shapely.geometry import Point
    from .coupling import select_nets
    from .si import _legs
    pats = [p for p in str(nets or '').split(',') if p.strip()]
    sel = [n for n in select_nets(kb, pats)
           if n.upper() not in ('GND', 'GROUND') and not n.upper().endswith(
               'GND')]
    # a channel is its nets joined by series parts (an AC-coupling capacitor's
    # two sides: one lane), its ends the chain's: the parts' own pads are not
    # ends (a port each side of the capacitor had split the lane's power,
    # -5 to -7 dB, and read its through signal as 38 % FEXT)
    chain = chains(kb, sel)
    links = series_links(kb, set(sel))
    inner = {ref for ref, _, _ in links}
    reach = {}
    for net in sel:
        ends = [(tuple(t['pts'][0]), t['layer']) for t in kb.tracks
                if t['net'] == net] + [
            (tuple(t['pts'][-1]), t['layer']) for t in kb.tracks
            if t['net'] == net]
        vias = [(v['x'], v['y']) for v in kb.vias if v.get('net') == net]
        reach[net] = []
        for fp in kb.footprints:
            if fp['ref'] in inner:
                continue
            for pd in fp.get('pads', []):
                if pd.get('net') != net or pd.get('geom') is None:
                    continue
                g = pd['geom'].buffer(1e-3)
                hit = any(l in pd['layers'] and g.contains(Point(*xy))
                          for xy, l in ends) or any(
                    g.contains(Point(*xy)) for xy in vias)
                if hit:
                    from .region import _pad_layer
                    reach[net].append((fp['ref'], pd['name'],
                                       np.asarray(pd['xy'], float),
                                       _pad_layer(kb, pd), net))
    # each chain named by the net holding its best end (a connector's): a
    # pair's two legs then named alike (DP2.TX1+ / DP2.TX1-)
    label = {}
    for net in sel:
        mem = chain[net]
        cand = [r for n in mem for r in reach[n]]
        label[net] = (min(cand, key=lambda r: (_end_rank(r[0]), r[4]))[4]
                      if cand else min(mem))
    heads = sorted({label[n] for n in sel})
    order = sorted(heads, key=lambda n: (min(
        (m for m in heads if m == n or _legs(m, n)), key=str), n))
    specs, info = [], []
    for head in order:
        mem = [n for n in sel if label[n] == head]
        reached = [r for n in mem for r in reach[n]]
        if not reached:
            continue
        if len(reached) == 1:
            pick = reached
        else:
            # the ends an interface runs between: connectors and modules,
            # then ICs, then the rest (an ESD diode, a resistor), the furthest
            # apart among those (USB-C's D101 had been further than its own
            # connector J101)
            best, pick = None, None
            for i, a in enumerate(reached):
                for b in reached[i + 1:]:
                    key = (-(_end_rank(a[0]) + _end_rank(b[0])),
                           float(np.hypot(*(a[2] - b[2]))))
                    if best is None or key > best:
                        best, pick = key, (a, b)
        for ref, pad, _, layer, net in pick:
            k = len(specs) + 1
            specs.append(f"P{k}={ref}.{pad}")
            info.append(dict(port=f"P{k}", net=head, pad_net=net, ref=ref,
                             pad=pad, layer=layer, nets=sorted(mem)))
    return specs, info


CURVE_POINTS = 60  # the report's curves: about this many frequencies


def pairs_report(f, S, ports, port_nets, rise_ps=100.0, z0=50.0,
                 curves=CURVE_POINTS):
    """Each net's (a pair's legs: one channel, in its differential mode)
    figures from the joined S: its insertion and return loss at 1, 5 and 10
    GHz (Sdd21, Sdd11; a single net's S21, S11) and its far end's conversion to
    common mode, and every victim's NEXT and FEXT for an edge of rise_ps
    (coupling.matrix, summary: as Board coupling has them). The near (driven)
    end of each net: the footprint holding the most ports' ends (the SoC, the
    module), else its other end. curves: each channel's insertion and return
    loss and its conversion to common mode (dB) at about this many of f's
    frequencies besides (0: none). dict(channels [...], victims [...], pairs
    [...], curve_ghz)."""
    import collections
    from .coupling import matrix, summary, channels, modal
    refs = collections.Counter(x['ref'] for x in port_nets)
    by = collections.defaultdict(list)
    for x in port_nets:
        by[x['net']].append(x)
    names = {}
    for net, xs in by.items():
        if len(xs) != 2:
            continue
        xs = sorted(xs, key=lambda x: -refs[x['ref']])
        names[xs[0]['port']] = (net, 'near')
        names[xs[1]['port']] = (net, 'far')
    order = [p for p in ports if p in names]
    ix = [ports.index(p) for p in order]
    Ss = np.asarray(S)[:, ix][:, :, ix]
    lnames = {p: names[p] for p in order}
    mat = matrix(f, Ss, lnames, rise_ps * 1e-12, pairs=True, z0=z0)
    vs, ps = summary(mat)
    chans = channels(list(dict.fromkeys(n for n, _ in lnames.values())), True)
    Sm, rows, zref = modal(Ss, lnames, chans, z0)
    at = {r: i for i, r in enumerate(rows)}
    k = [int(np.argmin(abs(np.asarray(f) - g * 1e9))) for g in (1, 5, 10)]
    db = lambda x: float(20 * np.log10(max(abs(x), 1e-15)))
    cj = (np.unique(np.linspace(0, len(f) - 1, curves).round().astype(int))
          if curves else np.zeros(0, int))
    dbs = lambda x: np.round(20 * np.log10(np.maximum(abs(x), 1e-15)),
                             3).tolist()
    out = []
    for c in chans:
        m = 'se' if c['kind'] == 'single' else 'd'
        a, b = at[(c['name'], m, 'near')], at[(c['name'], m, 'far')]
        row = dict(channel=c['name'], kind=c['kind'],
                   il_db=[db(Sm[j, b, a]) for j in k],
                   rl_db=[db(Sm[j, a, a]) for j in k])
        if c['kind'] != 'single':
            cf = at[(c['name'], 'c', 'far')]
            row['to_common_far_db'] = max(db(Sm[j, cf, a])
                                          for j in range(len(f)))
        if len(cj):
            row['curve'] = dict(il=dbs(Sm[cj, b, a]), rl=dbs(Sm[cj, a, a]))
            if c['kind'] != 'single':
                row['curve']['to_common'] = dbs(Sm[cj, cf, a])
        out.append(row)
    return dict(ghz=[1, 5, 10], rise_ps=rise_ps, channels=out, victims=vs,
                pairs=ps, curve_ghz=np.round(np.asarray(f)[cj] / 1e9,
                                             4).tolist())


def _end_rank(ref):
    """How likely a footprint is an interface's end (0: a connector or module;
    1: an IC; 2: anything else: protection diodes, passives, test points)."""
    import re
    m = re.match(r'[A-Za-z]+', str(ref))
    head = (m.group(0) if m else '').upper()
    if head in ('J', 'P', 'CN', 'CON', 'X', 'USB', 'MODULE', 'M', 'SW'):
        return 0
    if head in ('U', 'IC'):
        return 1
    return 2


OVERLAY = 'kicad2ems-segment-overlay'


def overlay(result, kb=None, top=8):
    """The joined board's figures laid over it: <name>_overlay.json (each
    channel's tracks per layer, KiCad mm, with its figures: insertion and
    return loss at the report's frequencies, its conversion to common mode, its
    NEXT and FEXT sums as a victim; the via models' circles; the cuts) and
    <name>_overlay.png (the board's channels coloured by the worst of NEXT and
    FEXT, and by the insertion loss at the top frequency, the via models
    circled, the `top` worst labelled). result: segment.json's path; kb: the
    board (KiCadBoard), read again when None. {json, png}."""
    import json as _json
    from .importers.kicad import KiCadBoard
    res = _json.load(open(result, encoding='utf-8'))
    rep = res.get('report')
    if not rep:
        raise ValueError("no per-channel report in the result")
    if 'curve_ghz' not in rep:  # (a result from before the curves)
        from .touchstone import read_touchstone
        fq, S = read_touchstone(os.path.join(os.path.dirname(result),
                                             res['touchstone']))
        rep = pairs_report(fq, S, res['ports'], res['port_nets'],
                           rep['rise_ps'])
    kb = kb or KiCadBoard(res['board'])
    from .coupling import channels
    legs = {c['name']: list(c['legs']) for c in channels(
        list(dict.fromkeys(x['net'] for x in res['port_nets'])), True)}
    # a leg's own nets (its chain through series parts: both sides of an
    # AC-coupling capacitor)
    mem = {}
    for x in res['port_nets']:
        mem.setdefault(x['net'], set()).update(x.get('nets') or [x['net']])
    vic = {v['victim']: v for v in rep['victims']}
    chans = []
    for c in rep['channels']:
        nets = sorted({n for leg in legs.get(c['channel'], [c['channel']])
                       for n in mem.get(leg, [leg])})
        v = vic.get(c['channel'], {})
        tracks = [dict(layer=t['layer'], net=t['net'], w=float(t['width']),
                       pts=np.round(np.asarray(t['pts'], float), 4).tolist())
                  for t in kb.tracks if t['net'] in nets]
        chans.append(dict(
            channel=c['channel'], kind=c['kind'], nets=nets,
            il_db=c['il_db'], rl_db=c['rl_db'],
            to_common_far_db=c.get('to_common_far_db'),
            next_sum_pct=v.get('next_sum_pct'),
            fext_sum_pct=v.get('fext_sum_pct'),
            worst=v.get('worst', []),
            curve=c.get('curve'), tracks=tracks))
    ov = dict(format=OVERLAY, version=1, board=res['board'],
              ghz=rep['ghz'], rise_ps=rep['rise_ps'], channels=chans,
              curve_ghz=rep.get('curve_ghz', []), pairs=rep.get('pairs', []),
              outline=[np.round(np.asarray(getattr(e, 'coords', e),
                                           float)[:, :2], 3).tolist()
                       for e in getattr(kb, 'edges', []) or []],
              via_models=res.get('via_models', []), cuts=res.get('cuts', []),
              seconds=res.get('seconds'),
              layer_pieces=sum(p.get('kind') == 'layer'
                               for p in res.get('pieces', [])))
    stem = os.path.splitext(result)[0].rsplit(os.sep, 1)[0]
    name = os.path.splitext(os.path.basename(res['board']))[0]
    jp = os.path.join(stem, f"{name}_overlay.json")
    with open(jp, 'w', encoding='utf-8') as fh:
        _json.dump(ov, fh)
    pp = os.path.join(stem, f"{name}_overlay.png")
    plot_overlay(ov, kb, pp, top)
    hp = overlay_html(ov, os.path.join(stem, f"{name}_overlay.html"))
    return dict(json=jp, png=pp, html=hp)


def overlay_html(ov, path=None):
    """The overlay as one self-contained page (segment_overlay.html with the
    data in it: opens from the disk, no server): the board's channels
    coloured by a figure (insertion or return loss at a frequency, crosstalk,
    mode conversion), each channel's curves and worst aggressors. The page
    without data (path None: its text) fetches overlay.json beside it."""
    import json as _json
    page = open(os.path.join(os.path.dirname(__file__),
                             'segment_overlay.html'), encoding='utf-8').read()
    if path is None:
        return page
    data = _json.dumps(ov, separators=(',', ':')).replace('</', '<\\/')
    page = page.replace('/*OVERLAY_JSON*/null', data, 1)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('<!doctype html>\n<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width, '
                 'initial-scale=1">\n' + page)
    return path


def plot_overlay(ov, kb, path, top=8):
    """The overlay as a figure: two panels, the channels coloured by the worst
    of NEXT and FEXT (%), and by the insertion loss at the top frequency
    (dB), over the board's outline; the via models circled."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import cm, colors
    fig = Figure(figsize=(18, 8.5))
    FigureCanvasAgg(fig)
    edges = [np.asarray(getattr(e, 'coords', e), float)[:, :2]
             for e in getattr(kb, 'edges', []) or []]
    panels = (
        ('crosstalk', lambda c: max(c['next_sum_pct'] or 0,
                                    c['fext_sum_pct'] or 0),
         f"worst of NEXT, FEXT from all aggressors (%, {ov['rise_ps']:g} ps "
         "edge)", 'magma_r'),
        ('loss', lambda c: -c['il_db'][-1],
         f"insertion loss at {ov['ghz'][-1]:g} GHz (dB)", 'viridis_r'))
    for k, (key, val, title, cmap) in enumerate(panels):
        ax = fig.add_subplot(1, 2, k + 1)
        for e in edges:
            if e.ndim == 2 and len(e) > 1:
                ax.plot(e[:, 0], -e[:, 1], color='0.75', lw=0.6)
        vals = [val(c) for c in ov['channels']]
        # (the 95th percentile: one channel far off, USB-C's stubs at 10 GHz,
        # had washed the others out)
        hi = max(float(np.percentile(vals, 95)) if vals else 0.0, 1e-9)
        norm = colors.Normalize(0, hi)
        import matplotlib
        cmo = matplotlib.colormaps[cmap]
        order = np.argsort(vals)
        for i in order:
            c = ov['channels'][i]
            col = cmo(norm(vals[i]))
            for t in c['tracks']:
                xy = np.asarray(t['pts'], float)
                ax.plot(xy[:, 0], -xy[:, 1], '-', color=col,
                        lw=0.6 + 1.6 * min(vals[i] / hi, 1.0),
                        solid_capstyle='round')
        for vm in ov['via_models']:
            for v in vm['vias']:
                ax.add_patch(__import__('matplotlib').patches.Circle(
                    (v['x'], -v['y']), max(v['r'], 0.3), fill=False,
                    color='tab:blue', lw=0.5))
        for i in order[::-1][:top]:
            c = ov['channels'][i]
            if not c['tracks']:
                continue
            xy = np.asarray(max(c['tracks'], key=lambda t: len(t['pts']))
                            ['pts'], float)
            m = xy[len(xy) // 2]
            ax.annotate(f"{c['channel'].split('/')[-1]} {vals[i]:.2g}",
                        (m[0], -m[1]), fontsize=6,
                        bbox=dict(boxstyle='round,pad=0.15', fc='white',
                                  ec='0.6', alpha=0.85))
        ax.set_aspect('equal')
        ax.set_title(title, fontsize=10)
        fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmo), ax=ax,
                     fraction=0.035)
    fig.suptitle(f"{os.path.basename(ov['board'])}: {len(ov['channels'])} "
                 f"channels, {sum(len(v['vias']) for v in ov['via_models'])} "
                 "signal vias in "
                 f"{len(ov['via_models'])} via models (blue)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    return path


def _echo_report(rep, echo):
    g = '/'.join(str(x) for x in rep['ghz'])
    echo(f"segment: each channel at {g} GHz: insertion loss, return loss (dB)"
         "; a pair's far-end conversion to common mode (dB, worst)")
    for c in rep['channels']:
        echo(f"  {c['channel']:24s} IL " + '/'.join(f"{v:6.2f}" for v in
                                                    c['il_db'])
             + "  RL " + '/'.join(f"{v:6.1f}" for v in c['rl_db'])
             + (f"  to common {c['to_common_far_db']:6.1f}"
                if 'to_common_far_db' in c else ''))
    w = sorted(rep['victims'], key=lambda v: -max(v['next_sum_pct'],
                                                  v['fext_sum_pct']))[:5]
    echo(f"segment: worst victims for a {rep['rise_ps']:g} ps edge (% of the "
         "aggressors' swing, all at once):")
    for v in w:
        echo(f"  {v['victim']:24s} NEXT {v['next_sum_pct']:5.2f} %, FEXT "
             f"{v['fext_sum_pct']:5.2f} %, worst from "
             + ', '.join(x['aggressor'] for x in v['worst']))


def _num(name):
    import re
    m = re.match(r'P(\d+)([+-]?)', name)
    return (int(m.group(1)), m.group(2)) if m else (10 ** 9, name)


def parser():
    import argparse
    p = argparse.ArgumentParser(
        prog='python -m kicad2ems.core.segment',
        description=__doc__.strip().split('\n\n')[0])
    p.add_argument('board', help=".kicad_pcb, or a kicad2ems .toml config")
    p.add_argument('--nets', default=None,
                   help="the nets, comma-separated (default: the config's)")
    p.add_argument('--solver', default='mom25d', help="the pieces' solver")
    p.add_argument('--fmin', type=float, default=None)
    p.add_argument('--fmax', type=float, default=None)
    p.add_argument('--points', type=int, default=None)
    p.add_argument('--out', default='segment')
    p.add_argument('--calibrate', action='store_true',
                   help="correct each cut for its two lumped ports back to "
                   "back (a calibration line per cross-section; measured: "
                   "rf_msvia 0.042 to 0.069 dB rms from mom25d whole, "
                   "rf_diffvia 0.160 to 0.142)")
    p.add_argument('--apart', type=float, default=None,
                   help="a layer's nets further apart than this many heights "
                   "over its plane are pieces of their own, their coupling "
                   "taken as none (default 20; 0: one piece a layer)")
    p.add_argument('--thickness', default=None,
                   help="mom25d's copper thickness model (--mom-thickness): "
                   "grow has no side unknowns (a large piece)")
    p.add_argument('--layer-solver', default=None,
                   help="the layer pieces' solver where not --solver's: lines "
                   "(the line model: seconds; the via models stay on "
                   "--solver's), auto (mom25d where a piece meshes to at most "
                   f"{LAYER_MOM_MAX} unknowns, K2E_SEG_MOM_MAX, the line "
                   "model past that)")
    p.add_argument('--edges', default=None, choices=('fine', 'coarse'),
                   help="mom25d's mesh across the copper's edges "
                   "(--mom-edges): coarse ~7x fewer unknowns on narrow traces")
    p.add_argument('--via-solver', default=None,
                   help="the via models' solver where not --solver's: gfdtd, "
                   "or pbm (a physics-based model: the cavities between the "
                   "planes, no mesh; viamodel.py)")
    p.add_argument('--dmax', type=float, default=None,
                   help="mom25d's largest cell along the copper, mm "
                   "(--mom-dmax)")
    p.add_argument('--ends', action='store_true',
                   help="ports at the nets' ends on pads (the default where "
                   "the board has no ports of its own)")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    a.config = None
    if a.board.endswith('.toml'):
        a.config, a.board = a.board, None
    run(a)
    return 0


if __name__ == '__main__':
    sys.exit(main())
