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
# a window's core ports alone driven (mom25d's excite; the rest of its rows
# by reciprocity): its neighbours' ports had been driven and thrown away
EXCITE_CORE = os.environ.get('K2E_SEG_EXCITE_CORE', 'on') != 'off'
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
    # each net's own circles in the group: what its copper is cut by (a track
    # passing another net's via in the group stays whole: cut by the group's
    # circle, a Thor DisplayPort lane running past the SoM connector's vias
    # was cut in and out of it five times, each stretch a stub to the via
    # model's barrel: -39 dB)
    return [dict(vias=g, circle=_solid(unary_union(
        [Point(v['x'], v['y']).buffer(v['r'], 64) for v in g])),
        own={n: _solid(unary_union(
            [Point(v['x'], v['y']).buffer(v['r'], 64) for v in g
             if v['net'] == n])) for n in {v['net'] for v in g}})
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
                c = dict(layer=t['layer'], net=t['net'],
                         at=(float(q[0]), float(q[1])),
                         out=(float(d[0]), float(d[1])), nudge=nudge,
                         width=float(t.get('width') or 0.0))
                # the same conductor crossing again (a track drawn twice, as a
                # KiCad 4 file has them, or again a few um off): one cut, where
                # the two cross within half the narrower's width, alike. Two
                # ports there are two via chains at one place (their ports
                # centred on the copper): the matrix near singular in either
                # basis (four LimeRFE pieces at 0.1 GHz, 2026-11-22; then two
                # via models 3 um apart, every factor failing to precondition,
                # 11 and 28 min in double precision)
                if any(o['layer'] == c['layer'] and o['net'] == c['net']
                       and math.hypot(o['at'][0] - q[0], o['at'][1] - q[1])
                       < max(1e-3, 0.5 * min(o['width'], c['width']))
                       and o['out'][0] * d[0] + o['out'][1] * d[1] > 0.98
                       for o in out):
                    continue
                out.append(c)
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


# a piece's reference planes: those whose ground covers half the area within
# this of its copper (mm)
REF_REACH = 1.0
# with the pour kept (POUR), a layer's nets grouped this far apart (mm): on a
# LimeRFE's GCPW receive piece the nets 1.5 mm and more apart coupled at -68.6
# dB median, -48.5 at most (its stitched ground between them); the microstrip
# rule's 20 heights (10 mm on its 20 mil RO4350B) had chained 322 nets
POUR_APART = float(os.environ.get('K2E_SEG_POUR_APART', 1.5))
# a stripline piece's ground vias: those within this of its copper (mm)
STITCH_REACH = float(os.environ.get('K2E_SEG_STITCH', 1.0))
# a layer piece's own layer's ground (an RF board's coplanar ground beside its
# traces): 'keep' within POUR_REACH mm of its copper, voids that do not border
# it filled, its ground vias kept, solved with mom25d's signal zone (the pour
# beyond it in coarse cells); 'off' the planes alone. On rf_gcpw widened to
# 19 mm pours, the whole pour in the zone: 3,929 unknowns and |dS| 0.004 of
# the fine mesh; a 3 mm band 0.005; a 0.8 mm one (the fence on its edge)
# 9,017 unknowns. On a LimeRFE's 11 GCPW nets (0.79 mm, 0.25 mm gaps on 20
# mil RO4350B; 0.1-6 GHz) against the pour to 6 mm on a fine mesh (33,659
# unknowns, 32 min): kept, 14,378 unknowns, 57 s, |dS| 0.006 (S11 within
# 0.05 dB at the median); off, 5,937, 17 s, |dS| 0.137 (S11 up to 10 dB
# off: the line 57 ohm without its coplanar ground). Coarse edges with it:
# 6,464, |dS| 0.080. At 1.5 mm (the default since) with the nets grouped
# POUR_APART: 6 pieces, 13.6k unknowns, 27 s, |dS| 0.007 (through paths
# within 0.03 dB at 6 GHz, S11 0.2 dB at the median)
POUR = os.environ.get('K2E_SEG_POUR', 'off')
POUR_REACH = float(os.environ.get('K2E_SEG_POUR_REACH', 1.5))


def _pour(p, near, reach):
    """A layer's ground polygon p as a piece keeps it: within reach of the
    piece's copper (near: its union), its holes filled but those bordering
    that copper (a clearance round another net's pad or trace, gone from the
    piece, would be edges to mesh)."""
    from shapely.geometry import Polygon
    # the band's own edge straight-joined and simplified (it is outside the
    # signal zone; round, its arcs' vertices had made a LimeRFE piece's band
    # edge a line of small cells)
    band = near.buffer(reach, quad_segs=1, join_style='mitre',
                       mitre_limit=1.5).simplify(0.2 * reach)
    g = p.intersection(band)
    out = []
    for q in getattr(g, 'geoms', [g]):
        if q.geom_type != 'Polygon' or q.is_empty:
            continue
        keep = [r for r in q.interiors
                if Polygon(r).distance(near) < 0.02]
        out.append(Polygon(q.exterior, keep))
    from shapely.ops import unary_union
    return unary_union(out) if out else None


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
    pour = POUR == 'keep'
    if len(refs) != 2 and not (pour and refs):
        return lambda n, x, y: False
    near = [p for p, n in zip(m.copper.get(lay, []),
                              m.copper_nets.get(lay, [])) if n in gnets]
    if not near:
        return lambda n, x, y: False
    # (with the pour kept, its vias: those through it to the planes)
    zone = prep(unary_union(near).buffer(POUR_REACH if pour
                                         else STITCH_REACH))
    zs = [m.cu_z[r] for r in refs] + [m.cu_z[lay]]
    lo, hi = min(zs), max(zs)
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
            clip.setdefault(n, []).append(g['own'][n])
    clip = {n: unary_union(cs) for n, cs in clip.items()}
    allcuts = []
    for gi, g in enumerate(groups):
        for c in (c for n, own in sorted(g['own'].items())
                  for c in cuts(kb, dict(circle=own), {n})):
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
                        and g['own'][pd['net']].contains(
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
            # a pad where another part's pad is (footprints on top of each
            # other, on one net): one port there, both parts joined to it
            # (run: a junction); two ports at one place had made the piece's
            # matrix singular (a LimeRFE piece, 2026-11-22)
            twin = next((o for o in allcuts if o.get('part')
                         and o['net'] == c['net'] and o['layer'] == lay
                         and math.hypot(o['at'][0] - me[0],
                                        o['at'][1] - me[1]) < 1e-3), None)
            if twin is not None:
                c['port'] = twin['port']
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

    gnd = {l: unary_union([q for q, n in zip(m.copper.get(l, []),
                                             m.copper_nets.get(l, []))
                           if n in m.ground_nets]) for l in m.cu_order}

    def refs_of(lay, near=None):
        """The planes next to a signal layer: the nearest below, above. With
        near (a piece's copper on it): the nearest whose ground covers half of
        the area within REF_REACH of it, where the board-wide test (half the
        outline) would take one further off (a LimeRFE's bottom RF lines over
        its PWR layer's ground, 35 % of the board: their reference had been
        the layer beyond)."""
        z = m.cu_z[lay]
        if near is not None:
            zone = near.buffer(REF_REACH)
            ok = [l for l in m.cu_order if l != lay and not gnd[l].is_empty
                  and gnd[l].intersection(zone).area >= 0.5 * zone.area]
        else:
            ok = plane_layers
        below = [l for l in ok if m.cu_z[l] < z - 1e-9]
        above = [l for l in ok if m.cu_z[l] > z + 1e-9]
        return ({max(below, key=lambda l: m.cu_z[l])} if below else set()) | (
            {min(above, key=lambda l: m.cu_z[l])} if above else set())
    net_of_cut = {c['k']: c['net'] for c in allcuts if not c.get('part')}

    def layer_piece(lay, gnets, name, refs):
        """The piece of layer lay's nets gnets (over its planes), or None
        where it has no ports (slivers of a via's ring)."""
        mine = [c for c in allcuts if c['layer'] == lay
                and c['net'] in gnets and not c.get('via_only')]
        ports = _unique([(c.get('port', f"P{1000 + c['k']}"),
                          _fp(f"cutL{c['k']}", c['at'], c['out'], lay,
                              c.get('nudge', NUDGE)))
                         for c in mine])
        fps = [(p.name, _port_fp(p)) for p in ext.values()
               if p.layer == lay and getattr(p, 'net', None) in gnets
               and (p.net not in clip
                    or not clip[p.net].contains(_pt(*p.c[:2])))]
        mine_cu = unary_union([q for q, n in zip(m.copper.get(lay, []),
                                                 m.copper_nets.get(lay, []))
                               if n in gnets])
        # (the planes under this piece's own copper)
        planes = refs_of(lay, mine_cu if not mine_cu.is_empty else None)
        refs = sorted(planes, key=lambda l: m.cu_z[l])
        near = (mine_cu if POUR == 'keep' and not mine_cu.is_empty
                else None)

        def keep(l, n, p, done=set()):
            if l == lay:
                if n in m.ground_nets and near is not None:
                    return _pour(p, near, POUR_REACH)
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
        elif POUR == 'keep':
            apart = POUR_APART
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
        ports = _unique([(c.get('port', f"P{1000 + c['k']}"),
                          _fp(f"cutV{c['k']}", c['at'], c['out'], c['layer'],
                              0.0)
                          if c.get('via_only') else
                          _fp(f"cutV{c['k']}", c['at'],
                              (-c['out'][0], -c['out'][1]), c['layer']))
                         for c in mine])
        # the board's own ports inside its circle (a pad close to the via)
        own = [(p.name, _port_fp(p)) for p in ext.values()
               if p.net in gnets and g['own'][p.net].contains(
                   _pt(*p.c[:2]))]

        # the planes in the box: layers whose ground covers half of it; a
        # signal layer's pour left out as the layer pieces leave it (in via1
        # of a CM5 carrier it was 9,100 of 19,445 triangles), a ground via's
        # small pad kept
        planes = {l for l, ps in m.copper.items() if sum(
            p.intersection(bx).area for p, n in zip(ps, m.copper_nets.get(
                l, [])) if n in m.ground_nets) >= 0.5 * bx.area}

        def keep(l, n, p, gnets=gnets, bx=bx, planes=planes, mine=g['own']):
            if n in gnets:
                return p.intersection(mine[n])
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
                        external=[n for n, _ in own], vias=g['vias'],
                        unmeshable=_unmeshable(m, b, bx)))
    del scale
    return [_one_port_a_place(p) for p in out], allcuts


VIA_GROUND_MIN = 2  # ground vias a via model's box needs to be meshed
# a via model that is no meshed box (_unmeshable) the physics-based model's
# where no --via-solver is given (K2E_SEG_VIA_AUTO=off: meshed as asked)
VIA_AUTO = os.environ.get('K2E_SEG_VIA_AUTO', 'on') != 'off'


def _unmeshable(m, b, bx):
    """Why via model b (its box bx) is no meshed model, or '': a port whose
    reference is a signal net's copper (its own: a cut circle inside the
    antipad, a series part's pad near the via having shrunk it, or a through-
    hole pad's port over its own barrel; the port then shorted by the via, the
    model open: five of LimeRFE's via models, ThunderScope's four BNC inputs);
    or fewer than VIA_GROUND_MIN ground vias in the box (its planes, clipped
    to the box, tied by none: the return current at low frequency only their
    small capacitance, -12 to -18 dB at 0.1 GHz where the board's whole planes
    pass it; or by one, a shorted patch: LimeRFE's J18 via -16 dB at 3.9 GHz).
    The physics-based model (viamodel: the planes infinite) has neither."""
    bad = [p.name for p in b.ports
           if getattr(p, 'kind', 'z') == 'z' and p.ref_layer
           and b.net_at(p.ref_layer, *p.c[:2]) not in (None, *m.ground_nets)]
    if bad:
        return (f"port{'s' if len(bad) > 1 else ''} {', '.join(bad[:4])} "
                "over signal copper, no plane under")
    inside = bx.buffer(-1e-6)
    gv = sum(1 for (x, y, *_), n in zip(m.vias, m.via_nets)
             if n in m.ground_nets and inside.contains(_pt(x, y)))
    if gv < VIA_GROUND_MIN:
        return f"{gv} ground via{'s' if gv != 1 else ''} in its box"
    return ''


def _one_port_a_place(piece):
    """The piece with its cut and joint ports that came to one place (one net
    and layer, within 1 um: two series parts' pads 1.1 mm apart, each port
    centred on the same 4 mm of copper, a LimeRFE piece's) made one, the
    other's links that one's (run's nodes then joins them: a junction). Two
    ports at one place are two via chains there, the matrix near singular in
    either basis: every complex64 factor failed and the double LDL^T without
    pivoting gave |S| of 1.4 to 539, run to run."""
    b = piece['board']
    ext = set(piece.get('external') or ())
    keep, alias = [], {}
    for p in b.ports:
        q = None
        if p.name not in ext:
            q = next((k for k in keep if k.name not in ext
                      and k.layer == p.layer and k.net == p.net
                      and math.hypot(k.c[0] - p.c[0], k.c[1] - p.c[1]) < 1e-3),
                     None)
        if q is None:
            keep.append(p)
        else:
            alias[p.name] = q.name
    if alias:
        b.ports = keep
        piece['links'] = [(alias.get(n, n), k) for n, k in piece['links']]
        if piece.get('part_net'):
            piece['part_net'] = {n: v for n, v in piece['part_net'].items()
                                 if n not in alias}
        if piece.get('core_ports'):
            piece['core_ports'] = list(dict.fromkeys(
                alias.get(n, n) for n in piece['core_ports']))
        piece['merged'] = alias
    return piece


def _unique(ports):
    """A piece's (name, footprint) ports, each name once (a port two parts'
    pads share: _joints' twin)."""
    seen, out = set(), []
    for n, fp in ports:
        if n not in seen:
            seen.add(n)
            out.append((n, fp))
    return out


def node_block(n, nf):
    """An ideal n-port junction (a node: equal voltages, the currents summing
    to zero) at one reference impedance: S = 2/n - I, (nf, n, n)."""
    S = np.full((n, n), 2.0 / n) - np.eye(n)
    return np.repeat(S[None].astype(complex), nf, axis=0)


def nodes(blocks, pairs, nf, echo=print):
    """pairs with each port in several of them (two parts' pads at one place,
    one port: a node) joined to their other sides through an ideal junction
    (node_block, appended to blocks): every port in one pair at most."""
    pairs = list(pairs)
    used = {}
    for i, pr in enumerate(pairs):
        for j, x in enumerate(pr):
            used.setdefault(x, []).append((i, j))
    for x, at in used.items():
        if len(at) < 2:
            continue
        jn = [f"node{x}:{q}" for q in range(len(at) + 1)]
        blocks.append((node_block(len(jn), nf), jn))
        for q, (i, j) in enumerate(at, 1):
            pr = list(pairs[i])
            pr[j] = jn[q]
            pairs[i] = tuple(pr)
        pairs.append((x, jn[0]))
        echo(f"segment: {x} joins {len(at)} parts' pads (a junction)")
    return pairs


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


def placed(m, raw, base, specs, ends, apart_h=None, echo=print):
    """pieces() of model m, a channel left out (said) wherever a cut lands on
    copper the model gives another net (two nets' copper overlapping in the
    board file: a short, or nets renamed out of step with the tracks), the
    model made again without it: (m, parts, cuts, specs, ends). Likewise a
    channel whose end port the model puts on another net's copper
    (_off_its_net)."""
    while True:
        off = _off_its_net(m, ends) if ends else []
        if off:
            echo(f"segment: WARNING: {len(off)} channel(s) left out, an end "
                 "port on another net's copper in the board file (two nets' "
                 "copper overlapping): " + ', '.join(
                     f"{leg} ({port.name}: {x['ref']}.{x['pad']}, "
                     f"{x['pad_net']}, on {port.net})"
                     for leg, port, x in off[:8])
                 + (' ...' if len(off) > 8 else ''))
            legs = {leg for leg, _, _ in off}
            gone = {y['port'] for y in ends if y['net'] in legs}
            specs = [q for q in specs if q.split('=')[0] not in gone]
            ends = [y for y in ends if y['net'] not in legs]
            m, specs, ends = model_dropping(base, specs, ends, echo)
            continue
        nets = set(m.sel or [n for ns in m.copper_nets.values() for n in ns])
        try:
            parts, allcuts = pieces(m, raw, raw, nets, apart_h, echo=echo)
            return m, parts, allcuts, specs, ends
        except ValueError as e:
            leg = _channel_at(str(e), raw, ends) if ends else None
            if leg is None:
                raise
            echo(f"segment: WARNING: {leg} left out: its copper overlaps "
                 f"another net's in the board file ({str(e)[:90]})")
            gone = {x['port'] for x in ends if x['net'] == leg}
            specs = [q for q in specs if q.split('=')[0] not in gone]
            ends = [x for x in ends if x['net'] != leg]
            m, specs, ends = model_dropping(base, specs, ends, echo)


def _off_its_net(m, ends):
    """[(channel, port, its ends entry)], one an end port that model m puts
    on another net's copper than its pad's (each channel once). A port's net
    is the copper under its point (Board.net_at, the first net there): where
    two nets' copper overlap in the file, the port joins the other net's
    piece and reads that net's signal (LimeRFE's U11 pad 4, Net-(C120-Pad2),
    on /30MHz_TX's track: 37.7 % far-end "coupling", and /30MHz_TX -3.5 dB at
    0.1 GHz, its power split into the port), or no piece's (seven LimeRFE
    pads under a stale ground pour's fill: their channels open)."""
    pad = {x['port']: x for x in ends}
    out, seen = [], set()
    for p in m.ports:
        x = pad.get(p.name)
        if (x is not None and p.net and x.get('pad_net')
                and p.net != x['pad_net'] and x['net'] not in seen):
            seen.add(x['net'])
            out.append((x['net'], p, x))
    return out


def _channel_at(msg, kb, ends):
    """The channel (ends' 'net') whose track a piece's port error names: 'Pn
    (cutLk) at (x, y) mm is not on <layer> copper of the selected nets'. None
    where it is not such an error."""
    import re
    from shapely.geometry import LineString, Point
    hit = re.search(r'at \(([-\d.]+), ([-\d.]+)\) mm is not on (\S+) copper',
                    msg)
    if not hit:
        return None
    pt, lay = Point(float(hit.group(1)), float(hit.group(2))), hit.group(3)
    of = {n: x['net'] for x in ends for n in (x.get('nets') or [x['net']])}
    best = min(((LineString(t['pts']).distance(pt), t['net'])
                for t in kb.tracks if t['layer'] == lay and t['net'] in of
                and len(t['pts']) > 1), default=(None, None))
    return of.get(best[1]) if best[0] is not None and best[0] < 0.5 else None


# small pieces solved JOBS at a time on the CPU (each with its share of the
# cores, the GPU off) while the big ones take the GPU one at a time: a Thor
# carrier's ~185 small pieces at ~12 s each (9 s of it previews and figures,
# --lean now) had been one after another
JOBS = int(os.environ.get('K2E_SEG_JOBS', max(1, min(6, (os.cpu_count() or 4)
                                                     // 4))))
SMALL_PORTS, SMALL_AREA = 8, 30.0  # (mm2 of signal copper)
# the memory the pieces share (EMSDEV_MEM_BUDGET each): the run keeps
# PARENT_ROOM; a small piece may take SMALL_BUDGET (95 % of LimeRFE's, Thor's
# and ThunderScope's 461 pieces planned 2.1 GB or less, the plan's 0.9 GB
# margin in it); a big one the rest less SMALL_HOLD a worker (what a small one
# holds). A piece whose direct solver does not fit its share gives it back
# (MOM25D_NO_ROOM_EXIT: exit NO_ROOM) and runs again at the end alone, with
# all of it; below LOW free the newest small one is stopped for the same. A
# LimeRFE run's 27k-unknown piece had planned 17.5 GB of a 23 GB cap with
# six small ones beside it: OOM-killed twice (2026-11-22)
PARENT_ROOM, SMALL_BUDGET, SMALL_HOLD = 1.5e9, float(os.environ.get(
    'K2E_SEG_SMALL_GB', 3.0)) * 1e9, 1.0e9
NO_ROOM = 75


class NoRoom(Exception):
    """A piece gave its share back (its direct solver needs more)."""


class Killed(ValueError):
    """A piece's run ended by a signal (the kernel's OOM killer, its
    oom_score_adj raised: _victim)."""


def _victim():
    """(In a piece's process, before it runs:) the OOM killer's first choice
    over the run that started it: raising one's own score needs no
    privilege. A LimeRFE run's systemd scope had stopped the whole study when
    the kernel killed one piece (OOMPolicy=stop, its default)."""
    try:
        with open('/proc/self/oom_score_adj', 'w') as fh:
            fh.write('800')
    except OSError:
        pass


def _small(p):
    """Whether a piece goes to the CPU workers (_solve_all)."""
    if p['kind'] != 'layer':
        return False
    b = p['board']
    area = sum(q.area for l, ps in b.copper.items()
               for q, n in zip(ps, b.copper_nets[l]) if n not in b.ground_nets)
    return len(b.ports) <= SMALL_PORTS and area < SMALL_AREA


def _solve_all(parts, out, band, a, echo):
    """Every piece solved: {name: (f, S, names, summary, seconds) or the
    exception}; the small ones (_small) JOBS at a time on the CPU beside the
    big ones, in order, on the GPU, each in its share of the memory (see
    SMALL_BUDGET); those that gave theirs back last, one at a time."""
    import threading
    import time
    import types
    from concurrent.futures import ThreadPoolExecutor
    from kicad2ems.emsdev.memory import available
    kw = dict(thickness=a.thickness, dmax=a.dmax, layer_solver=a.layer_solver,
              via_solver=a.via_solver, edges=a.edges)
    res, later, lock = {}, [], threading.Lock()
    running, stopped = {}, set()     # small ones: name -> (start, process)
    whole = max(2e9, available() - PARENT_ROOM)
    low = max(0.75e9, 0.04 * whole)

    def one(p, env=None, small=False, last=False):
        t, name = time.time(), p['name']

        def started(proc):
            if small:
                with lock:
                    if proc is None:
                        running.pop(name, None)
                    else:
                        running[name] = (time.time(), proc)
        try:
            got = solve_piece(p, os.path.join(out, name), band, a.solver,
                              echo, env=env, started=started, **kw)
            res[name] = (*got, time.time() - t)
        except Exception as e:  # (raised again, in order)  # noqa: BLE001
            if not last and (isinstance(e, (NoRoom, Killed))
                             or name in stopped):
                echo(f"segment: {name}: " + (
                    "stopped, memory low" if name in stopped else
                    "killed (out of memory?)" if isinstance(e, Killed)
                    else str(e)) + ": again at the end, alone")
                with lock:
                    later.append(p)
                return
            res[name] = e
        # its geometry no longer needed (run takes its ports alone): every
        # piece's board held to the end was most of the 7.6 GB a LimeRFE
        # run's process took, beside a 27k-unknown piece's 15 (a 24 GB cap)
        p['board'] = types.SimpleNamespace(ports=list(p['board'].ports))

    def admit():
        """Wait for the room a small piece may take (or for nothing else to
        run)."""
        while True:
            with lock:
                alone = not running and not big_now[0]
            if alone or available() >= SMALL_BUDGET / 2 + low:
                return
            time.sleep(1.0)

    def small_one(p):
        admit()
        one(p, small_env, small=True)

    def watch():
        """The newest small piece stopped while memory runs low."""
        while not done.wait(1.0):
            if available() >= low:
                continue
            with lock:
                if not running:
                    continue
                name, (_, proc) = max(running.items(), key=lambda kv: kv[1][0])
                stopped.add(name)
            proc.kill()
            done.wait(3.0)

    small = [p for p in parts if _small(p)] if JOBS > 1 else []
    cores = max(1, (os.cpu_count() or 4) // max(JOBS, 1))
    small_env = dict(MOM25D_GPU='off', MOM25D_OMP_THREADS=str(cores),
                     OMP_NUM_THREADS=str(cores),
                     OPENBLAS_NUM_THREADS=str(cores),
                     MKL_NUM_THREADS=str(cores),
                     EMSDEV_MEM_BUDGET=str(int(SMALL_BUDGET)),
                     MOM25D_NO_ROOM_EXIT=str(NO_ROOM))
    hold = min(JOBS * SMALL_HOLD, 0.4 * whole) if small else 0.0
    if small:
        echo(f"segment: {len(small)} small pieces {JOBS} at a time on the "
             f"CPU ({cores} threads each, up to {SMALL_BUDGET / 1e9:.1f} GB), "
             f"{len(parts) - len(small)} on their own (up to "
             f"{(whole - hold) / 1e9:.1f} GB beside them)")
    big_now, done = [False], threading.Event()
    dog = threading.Thread(target=watch, daemon=True)
    dog.start()
    try:
        with ThreadPoolExecutor(max_workers=max(JOBS, 1)) as pool:
            futs = [pool.submit(small_one, p) for p in small]
            names = {p['name'] for p in small}
            for p in parts:
                if p['name'] in names:
                    continue
                busy = any(not f.done() for f in futs)
                big_now[0] = True
                one(p, dict(EMSDEV_MEM_BUDGET=str(int(whole - hold)),
                            MOM25D_NO_ROOM_EXIT=str(NO_ROOM)) if busy else None)
                big_now[0] = False
            for f in futs:
                f.result()
    finally:
        done.set()
    # those that gave their share back: alone, with all of it
    for p in later:
        one(p, last=True)
    return res


def chain_nets(ends):
    """Every net of the channels end_ports made (a channel through series
    parts: its middle nets have no port, and 'auto' alone left them and their
    parts out of the model)."""
    return sorted({n for x in ends for n in x.get('nets') or [x['net']]})


def model_dropping(base, specs, ends, echo=print):
    """The board model with ports at the channels' ends (end_ports's specs and
    ends), a channel left out where one of its ports finds no reference copper
    (a LimeRFE's bottom-side SAW duplexer over a void in its PWR layer): (model,
    specs, ends), said."""
    import re
    from .config import build_settings
    from .pipeline import board_from_settings
    dropped = []
    while True:
        try:
            m = board_from_settings(build_settings(dict(
                base, ports=specs, nets=['auto'] + chain_nets(ends))),
                log=lambda *x: None)
            break
        except ValueError as e:
            hit = re.match(r'(P\d+): no reference copper', str(e))
            leg = next((x['net'] for x in ends if hit and x['port'] ==
                        hit.group(1)), None)
            if leg is None:
                raise
            dropped.append((leg, hit.group(1)))
            gone = {x['port'] for x in ends if x['net'] == leg}
            specs = [q for q in specs if q.split('=')[0] not in gone]
            ends = [x for x in ends if x['net'] != leg]
    if dropped:
        echo(f"segment: WARNING: {len(dropped)} channel leg(s) left out, a "
             "port with no reference copper under it: "
             + ', '.join(f"{n} ({p})" for n, p in dropped[:10])
             + (' ...' if len(dropped) > 10 else ''))
    return m, specs, ends


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


def open_pieces(parts, bad, port_nets):
    """The pieces (names) whose every net is a channel's that unrouted (bad)
    found open end to end: solved, they would give only those channels' own
    open S and their coupling to one another (pieces apart do not couple).
    A piece with a net of a live channel, or of none, is kept; a kept piece's
    cut to a piece left out ends open, as a leg with no path on does.
    LimeRFE (2026-11-23): 74 of its 317 pieces, 18.5 of the 91 min of
    solving, five via models of 25-29k unknowns among them."""
    dead = {c for c, _ in bad}
    of = {}
    for x in port_nets:
        for n in x.get('nets') or [x['net']]:
            of.setdefault(n, set()).add(x['net'])
    out = []
    for p in parts:
        nets = (set(p.get('nets') or ())
                | {v['net'] for v in p.get('vias') or () if v.get('net')}
                | {q.net for q in p['board'].ports if getattr(q, 'net', None)})
        if nets and all(of.get(n) and of[n] <= dead for n in nets):
            out.append(p['name'])
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
                edges=None, env=None, started=None):
    """A piece's run (its board file, kicad2ems's pipeline): (S at 50 ohm,
    its ports' names, the run's summary). started: called with the run's
    process once it starts and None once it ends. NoRoom where the run gave
    its memory share back (env's MOM25D_NO_ROOM_EXIT)."""
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
    auto = (piece['kind'] == 'via' and solver == 'mom25d' and not via_solver
            and piece.get('unmeshable') and VIA_AUTO)
    if auto:
        # (no via solver asked for: the meshed box where it is one)
        from .viamodel import planes_of
        if planes_of(d):
            solver = 'pbm'
            echo(f"{piece['name']}: the physics-based via model, not a meshed "
                 f"box ({piece['unmeshable']}; --via-solver mom25d to mesh "
                 "it)")
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
    cli = dict(out=folder, solvers=solver, no_report=True, lean=True, **band)
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
            if POUR == 'keep' and os.environ.get('K2E_SEG_POUR_ZONE',
                                                 'on') != 'off':
                cli['mom_zone'] = 'on'  # (the pour's far part coarse)
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
    if solver == 'mom25d' and piece.get('core_ports') and EXCITE_CORE:
        # a window: its core ports' rows alone (assemble), driven alone
        cli['mom_excite'] = ','.join(piece['core_ports'])
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
            penv = dict(os.environ, PYTHONUNBUFFERED='1', **(env or {}))
            penv.setdefault('MOM25D_BLR_MIN_N', '1000000')
            penv.setdefault('MOM25D_BLR_CPU_MIN_N', '1000000')
            p = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                 env=penv, preexec_fn=(
                                     _victim if sys.platform.startswith(
                                         'linux') else None))
            if started:
                started(p)
            try:
                p.wait()
            finally:
                if started:
                    started(None)
        if p.returncode == NO_ROOM and penv.get('MOM25D_NO_ROOM_EXIT'):
            raise NoRoom("its direct solver needs more than its share")
        if p.returncode and p.returncode < 0:
            raise Killed(f"{piece['name']}: its run ended by signal "
                         f"{-p.returncode}")
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
    global POUR
    if getattr(a, 'pour', None):
        POUR = a.pour
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
    ends = specs = None
    if a.ends or not _has_ports(s):
        # no ports of its own: one on each pad where a net's tracks end
        specs, ends = end_ports(raw, a.nets or ','.join(s.nets or []))
        if not specs:
            raise ValueError("no net ends on pads to put ports at (--nets)")
        base = dict(base, ports=specs, nets=['auto'])
        s = build_settings(base)
        echo(f"segment: {len(specs)} ports at the nets' ends on pads")
    specs = specs if ends else None
    if ends:
        m, specs, ends = model_dropping(base, specs, ends, echo)
        s = build_settings(dict(base, ports=specs,
                                nets=['auto'] + chain_nets(ends)))
    else:
        m = board_from_settings(s, log=lambda *x: None)
    m, parts, allcuts, specs, ends = placed(m, raw, base, specs, ends,
                                            a.apart, echo)
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
            skip = ([] if getattr(a, 'solve_open', False)
                    else open_pieces(parts, bad, ends))
            if skip:
                echo(f"segment: {len(skip)} piece(s) not solved, their every "
                     "channel open end to end (--solve-open to solve them): "
                     + ", ".join(skip[:12]) + (" ..." if len(skip) > 12
                                               else ''))
                parts = [p for p in parts if p['name'] not in set(skip)]
    echo(f"segment: {sum(p['kind'] == 'layer' for p in parts)} layer(s), "
         f"{sum(p['kind'] == 'via' for p in parts)} via model(s), "
         f"{len(allcuts)} cut(s)")
    blocks, links, f0, info, cut_of = [], {}, None, [], {}
    wins, win_links = {}, {}
    done = _solve_all(parts, out, band, a, echo)
    for p in parts:
        try:
            got = done[p['name']]
            if isinstance(got, Exception):
                raise got
            f, S, names, sm, dt = got
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
                         seconds=round(dt, 1), solver=got[0],
                         unknowns=(got[1] or {}).get('unknowns')))
        echo(f"{p['name']}: {len(names)} ports, {dt:.0f} s")
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
    pairs, opened = [], []
    for k, sides in links.items():
        # (sides, not ends: ends is the external ports' list, written below)
        if len(sides) == 1:
            # a leg with no path on (unrouted's warning: its other side in no
            # piece, a series part to copper not modelled): open there, as the
            # warning says (the LimeRFE study's first join had stopped here)
            nm = f"open{k}"
            blocks.append((np.ones((len(f0), 1, 1), complex), [nm]))
            pairs.append((sides[0], nm))
            opened.append(k)
            continue
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
    if opened:
        echo(f"segment: {len(opened)} cut(s) with one side open (their legs' "
             "other side in no piece)")
    pairs = nodes(blocks, pairs, len(f0), echo)
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
            cp = plot_couplings(os.path.join(out, RESULT), os.path.join(
                out, f"{name}_couplings.png"), raw)
            echo(f"segment: the worst couplings -> {cp}")
            pp = plot_coupling_profiles(os.path.join(out, RESULT),
                                        os.path.join(out, f"{name}_coupling"
                                                     "_profiles.png"), raw)
            echo(f"segment: where along them -> {pp}")
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
    from .parts import not_fitted
    out = []
    for fp in kb.footprints:
        if not_fitted(fp):
            continue  # (open: an 'NF' 0 ohm link joins nothing)
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
                    # two parts first: a USB-C connector's two D+ pins
                    # (A6, B6: the plug either way up) had been a channel's
                    # two ends, the hub it runs to left out
                    key = (a[0] != b[0],
                           -(_end_rank(a[0]) + _end_rank(b[0])),
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
    # a net without its two ends here (a leg open: segment's one-sided cuts;
    # or its end port another net's of its chain too, named that net's) left
    # out with its pair's other leg, and the report made of the rest (a
    # KeyError had lost the LimeRFE study's whole report)
    while True:
        try:
            return _pairs_report(f, S, ports, names, rise_ps, z0, curves)
        except KeyError as e:
            key = e.args[0] if e.args else None
            if not (isinstance(key, tuple) and len(key) == 2):
                raise
            from .coupling import channels
            have = list(dict.fromkeys(v[0] for v in names.values()))
            drop = {key[0]} | {l for c in channels(have + [key[0]], True)
                               if key[0] in c.get('legs', ())
                               for l in c['legs']}
            if not any(v[0] in drop for v in names.values()):
                raise
            names = {p: v for p, v in names.items() if v[0] not in drop}


def _pairs_report(f, S, ports, names, rise_ps, z0, curves):
    """pairs_report's, for the ports' (net, end) names."""
    from .coupling import matrix, summary, channels, modal
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


def plot_couplings(result, path, kb=None, top=12):
    """The board's worst couplings on a whole-board view: every channel's
    tracks faint, the `top` strongest channel pairs (both directions merged,
    by the larger of NEXT and FEXT) in colour, the stretch where the two run
    closest drawn thick, each labelled with its NEXT and FEXT (% of the
    aggressor's swing, the report's edge). result: segment.json's path."""
    import json as _json
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    from .importers.kicad import KiCadBoard
    from .coupling import channels
    res = _json.load(open(result, encoding='utf-8'))
    rep = res['report']
    kb = kb or KiCadBoard(res['board'])
    mem = {}
    for x in res['port_nets']:
        mem.setdefault(x['net'], set()).update(x.get('nets') or [x['net']])
    legs = {c['name']: c['legs'] for c in channels(list(mem), True)}
    nets_of = lambda ch: {n for leg in legs.get(ch, [ch])
                          for n in mem.get(leg, [leg])}
    best = {}
    cand = list(rep.get('pairs', [])) + [
        dict(aggressor=w['aggressor'], victim=v['victim'],
             next_pct=w['next_pct'], fext_pct=w['fext_pct'])
        for v in rep.get('victims', []) for w in v.get('worst', [])]
    for q in cand:
        key = tuple(sorted((q['aggressor'], q['victim'])))
        v = max(q['next_pct'], q['fext_pct'])
        if key not in best or v > best[key][0]:
            best[key] = (v, q)
    ranked = sorted(best.values(), key=lambda x: -x[0])[:top]
    lines = {}
    for t in kb.tracks:
        if len(t['pts']) > 1:
            lines.setdefault(t['net'], []).append(t)
    xs = np.vstack([np.asarray(t['pts'], float) for ts in lines.values()
                    for t in ts])
    w_, h_ = np.ptp(xs[:, 0]) + 10, np.ptp(xs[:, 1]) + 10
    fig = Figure(figsize=(17, max(6.0, 12.0 * h_ / w_ + 1.2)))
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.03, 0.06, 0.70, 0.86])
    lx = fig.add_axes([0.75, 0.06, 0.23, 0.86])
    lx.axis('off')
    for e in getattr(kb, 'edges', []) or []:
        xy = np.asarray(getattr(e, 'coords', e), float)
        ax.plot(xy[:, 0], xy[:, 1], color='0.7', lw=0.7)
    every = set().union(*[nets_of(c) for c in legs]) if legs else set()
    for n in every:
        for t in lines.get(n, []):
            xy = np.asarray(t['pts'], float)
            ax.plot(xy[:, 0], xy[:, 1], color='0.85', lw=0.5)
    # twelve that stay apart (tab10 repeats at the eleventh)
    pal = ['#1f77b4', '#d62728', '#2ca02c', '#ff7f0e', '#9467bd', '#8c564b',
           '#e377c2', '#17becf', '#bcbd22', '#393b79', '#7f7f7f', '#e7298a']
    cmap = lambda i: pal[i % len(pal)]
    rows = []
    for k, (v, q) in enumerate(ranked):
        col = cmap(k)
        a, b = nets_of(q['aggressor']), nets_of(q['victim'])
        for n in a | b:
            for t in lines.get(n, []):
                xy = np.asarray(t['pts'], float)
                ax.plot(xy[:, 0], xy[:, 1], color=col, lw=1.0, alpha=0.8)
        # where they run closest, per layer: the victim's copper within
        # (closest gap + 0.3 mm) of the aggressor's
        spot, gap = None, None
        for lay in {t['layer'] for n in a for t in lines.get(n, [])}:
            ga = [LineString(t['pts']).buffer(t['width'] / 2)
                  for n in a for t in lines.get(n, []) if t['layer'] == lay]
            gb = [LineString(t['pts']).buffer(t['width'] / 2)
                  for n in b for t in lines.get(n, []) if t['layer'] == lay]
            if not ga or not gb:
                continue
            ua, ub = unary_union(ga), unary_union(gb)
            d = ua.distance(ub)
            if gap is None or d < gap:
                gap = d
                spot = (ub.intersection(ua.buffer(d + 0.3)), lay)
        if spot is not None and not spot[0].is_empty:
            for g in getattr(spot[0], 'geoms', [spot[0]]):
                if g.geom_type == 'Polygon':
                    ax.fill(*g.buffer(0.25).exterior.xy, fc=col, ec='k',
                            lw=0.8, alpha=0.95, zorder=5)
            c = spot[0].centroid
            # the label set off from the spot, alternately up and down
            off = (14, 14 if k % 2 else -14)
            ax.annotate(str(k + 1), (c.x, c.y), xytext=off,
                        textcoords='offset points', fontsize=9,
                        weight='bold', color='white', ha='center',
                        va='center', zorder=7,
                        bbox=dict(boxstyle='circle,pad=0.25', fc=col,
                                  ec='k', lw=0.6),
                        arrowprops=dict(arrowstyle='-', color='k', lw=0.6))
        short = lambda n: n.split('/')[-1]
        rows.append((col, f"{k + 1}. {short(q['aggressor'])} -> "
                          f"{short(q['victim'])}",
                     f"NEXT {q['next_pct']:.2f} %, FEXT {q['fext_pct']:.2f} %"
                     + (f"; closest {gap:.2f} mm on {spot[1]}"
                        if spot else '')))
    ax.set_aspect('equal')
    ax.invert_yaxis()
    ax.set_xlabel('x (mm)')
    ax.set_title(f"{os.path.basename(res['board'])}: the {len(ranked)} "
                 f"worst-coupled channel pairs ({rep['rise_ps']:g} ps edge, "
                 "% of the aggressor's swing); grey: the other high-speed "
                 "channels", fontsize=10)
    y = 0.98
    for col, a1, a2 in rows:
        lx.add_patch(matplotlib.patches.Rectangle((0.0, y - 0.022), 0.04,
                                                  0.02, color=col,
                                                  transform=lx.transAxes))
        lx.text(0.06, y - 0.004, a1, fontsize=8.5, va='top',
                transform=lx.transAxes)
        lx.text(0.06, y - 0.03, a2, fontsize=7.5, va='top', color='0.3',
                transform=lx.transAxes)
        y -= 0.075
    fig.savefig(path, dpi=110)
    return path


def _xs_of(kb, lay):
    """A layer's cross-section for coupling_profile: ('microstrip', h mm, er)
    over the next copper layer for an outer one, ('stripline', (h1, h2), er)
    between its two neighbours for an inner one (signal layers between
    planes); er the dielectrics' mean by thickness."""
    st = [x for x in kb.stack_items if x['kind'] in ('copper', 'diel')]
    cu = [i for i, x in enumerate(st) if x['kind'] == 'copper']
    i = next(k for k in cu if st[k]['name'] == lay)
    j = cu.index(i)

    def gap(a, b):
        ds = [x for x in st[min(a, b) + 1:max(a, b)] if x['kind'] == 'diel']
        t = sum(x['t'] for x in ds) * 1e3
        er = sum(x['t'] * x['er'] for x in ds) * 1e3 / t if t else 4.0
        return t, er
    if j == 0 or j == len(cu) - 1:
        h, er = gap(i, cu[1] if j == 0 else cu[-2])
        return 'microstrip', h, er
    (h1, e1), (h2, e2) = gap(i, cu[j - 1]), gap(i, cu[j + 1])
    return 'stripline', (h1, h2), (h1 * e1 + h2 * e2) / (h1 + h2)


def coupling_profile(kb, victim_nets, aggressor_nets, start, step=0.1,
                     reach=3.0):
    """Where along a victim its coupling to an aggressor is: samples every
    `step` mm of the victim's tracks with their distance along its route from
    `start` ((layer, x, y), KiCad mm; through its vias and series parts), the
    gap to the aggressor's nearest copper on that layer, and the local
    backward-coupling coefficient Kb (what NEXT builds up from: (Cm/C +
    Lm/L) / 4 of the two lines' cross-section at that gap, the line model's
    field solve). dict(dist, kb, gap, layer, xy) (numpy arrays)."""
    import heapq
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    from .lines.topology import _group_c
    vt = [t for t in kb.tracks if t['net'] in victim_nets
          and len(t['pts']) > 1]
    at = [t for t in kb.tracks if t['net'] in aggressor_nets
          and len(t['pts']) > 1]
    # the victim's route as a graph: track ends, joined through its vias and
    # series parts
    key = lambda l, x, y: (l, round(float(x), 3), round(float(y), 3))
    adj = {}

    def link(a, b, w):
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))
    segs = []
    for t in vt:
        P = np.asarray(t['pts'], float)
        for a, b in zip(P[:-1], P[1:]):
            ka, kb_ = key(t['layer'], *a), key(t['layer'], *b)
            L = float(np.hypot(*(b - a)))
            link(ka, kb_, L)
            segs.append((t, a, b, ka, kb_, L))
    nodes = list(adj)
    pts = np.array([[n[1], n[2]] for n in nodes]) if nodes else np.zeros((0, 2))
    near = lambda x, y, r: [nodes[i] for i in np.flatnonzero(
        np.hypot(pts[:, 0] - x, pts[:, 1] - y) <= r)]
    for v in kb.vias:
        if v.get('net') in victim_nets:
            ns = near(v['x'], v['y'], 0.3)
            for a in ns:
                for b in ns:
                    if a[0] != b[0]:
                        link(a, b, 0.0)
    for fp in kb.footprints:
        pads = [pd for pd in fp.get('pads', []) if pd.get('net') in
                victim_nets]
        if len(fp.get('pads', [])) == 2 and len(pads) == 2:
            ends = [min(near(*pd['xy'], 1.0) or [None], key=lambda n: (
                n is None, 0 if n is None else math.hypot(
                    n[1] - pd['xy'][0], n[2] - pd['xy'][1]))) for pd in pads]
            if None not in ends:
                link(ends[0], ends[1], float(np.hypot(*np.subtract(
                    pads[0]['xy'], pads[1]['xy']))))
    dist = {}
    if nodes:
        # the track end nearest the start on any layer (a lane leaving its
        # connector's pad through a via has none on the pad's layer there:
        # its far end had been taken)
        s0 = min(nodes, key=lambda n: (round(math.hypot(
            n[1] - start[1], n[2] - start[2]), 2), n[0] != start[0]))
        q = [(0.0, s0)]
        while q:
            d, n = heapq.heappop(q)
            if n in dist:
                continue
            dist[n] = d
            for m_, w in adj.get(n, []):
                if m_ not in dist:
                    heapq.heappush(q, (d + w, m_))
    by_lay = {}
    for t in at:
        by_lay.setdefault(t['layer'], []).append(t)
    trees = {l: (STRtree([LineString(t['pts']) for t in ts]), ts)
             for l, ts in by_lay.items()}
    cache, out = {}, dict(dist=[], kb=[], gap=[], layer=[], xy=[])
    for t, a, b, ka, kb_, L in segs:
        if ka not in dist and kb_ not in dist:
            continue
        da, db_ = dist.get(ka, np.inf), dist.get(kb_, np.inf)
        tree = trees.get(t['layer'])
        for u in np.arange(0.5 * step, max(L, 1e-9), step) / max(L, 1e-9):
            x = a + (b - a) * u
            d = min(da + u * L, db_ + (1 - u) * L)
            k, g = 0.0, np.inf
            if tree is not None:
                i = tree[0].nearest(Point(*x))
                ta = tree[1][i]
                g = (LineString(ta['pts']).distance(Point(*x))
                     - 0.5 * (ta['width'] + t['width']))
                if 0 < g < reach:
                    xs, h, er = _xs_of(kb, t['layer'])
                    ck = (t['layer'], round(t['width'], 3),
                          round(ta['width'], 3), round(g, 2))
                    if ck not in cache:
                        C, Ca = _group_c(xs, (round(t['width'], 3),
                                              round(ta['width'], 3)),
                                         (round(g, 2),), h, er)
                        Lm = np.linalg.inv(Ca)
                        cache[ck] = 0.25 * (abs(C[0, 1]) / C[0, 0]
                                            + abs(Lm[0, 1]) / Lm[0, 0])
                    k = cache[ck]
            out['dist'].append(d)
            out['kb'].append(k)
            out['gap'].append(g)
            out['layer'].append(t['layer'])
            out['xy'].append(x)
    o = np.argsort(out['dist'])
    return {k: np.asarray(v)[o] for k, v in out.items()}


def plot_coupling_profiles(result, path, kb=None, top=4):
    """The `top` worst-coupled channel pairs (plot_couplings's ranking), each
    as a profile along its victim: the local backward-coupling coefficient Kb
    (%) against the distance from the victim's near end, the layer it is on
    shaded, with a map of where it peaks (the victim coloured by Kb, the
    aggressor grey) and the full-wave NEXT and FEXT for the pair."""
    import json as _json
    import collections
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import LineCollection
    from .importers.kicad import KiCadBoard
    from .coupling import channels
    res = _json.load(open(result, encoding='utf-8'))
    rep = res['report']
    kb = kb or KiCadBoard(res['board'])
    mem = {}
    for x in res['port_nets']:
        mem.setdefault(x['net'], set()).update(x.get('nets') or [x['net']])
    legs = {c['name']: c['legs'] for c in channels(list(mem), True)}
    refs = collections.Counter(x['ref'] for x in res['port_nets'])
    best = {}
    cand = list(rep.get('pairs', [])) + [
        dict(aggressor=w['aggressor'], victim=v['victim'],
             next_pct=w['next_pct'], fext_pct=w['fext_pct'])
        for v in rep.get('victims', []) for w in v.get('worst', [])]
    for q in cand:
        k_ = tuple(sorted((q['aggressor'], q['victim'])))
        v = max(q['next_pct'], q['fext_pct'])
        if k_ not in best or v > best[k_][0]:
            best[k_] = (v, q)
    ranked = [q for _, q in sorted(best.values(), key=lambda x: -x[0])][:top]
    fig = Figure(figsize=(16, 4.2 * len(ranked)))
    FigureCanvasAgg(fig)
    gs = fig.add_gridspec(len(ranked), 2, width_ratios=[1.0, 1.9],
                          hspace=0.45, wspace=0.12)
    lay_col = {}
    cm = matplotlib.colormaps['magma_r']
    for r, q in enumerate(ranked):
        vleg = legs.get(q['victim'], [q['victim']])[0]
        vnets = mem.get(vleg, {vleg})
        anets = {n for leg in legs.get(q['aggressor'], [q['aggressor']])
                 for n in mem.get(leg, {leg})}
        ends = [x for x in res['port_nets'] if x['net'] == vleg]
        nearx = max(ends, key=lambda x: refs[x['ref']]) if ends else None
        fp = next((f for f in kb.footprints if nearx and f['ref'] ==
                   nearx['ref']), None)
        pad = next((p for p in (fp or {}).get('pads', [])
                    if str(p['name']) == str(nearx['pad'])), None) \
            if nearx else None
        start = ((nearx['layer'], *pad['xy']) if pad is not None
                 else ('F.Cu', 0.0, 0.0))
        pr = coupling_profile(kb, vnets, anets, start)
        ax = fig.add_subplot(gs[r, 1])
        k = 100 * pr['kb']
        top_k = max(k.max() if len(k) else 0, 1e-6)
        # the layer under each stretch, shaded
        if len(pr['dist']):
            edges = np.r_[0, np.flatnonzero(pr['layer'][1:] !=
                                            pr['layer'][:-1]) + 1,
                          len(pr['layer'])]
            for a_, b_ in zip(edges[:-1], edges[1:]):
                lay = pr['layer'][a_]
                c = lay_col.setdefault(lay, matplotlib.colormaps['Pastel1'](
                    len(lay_col) % 9))
                ax.axvspan(pr['dist'][a_], pr['dist'][b_ - 1], color=c,
                           alpha=0.6, lw=0)
            ax.fill_between(pr['dist'], 0, k, color='#7a1f5c', alpha=0.85,
                            step='mid', lw=0)
        ax.set_xlim(0, pr['dist'].max() if len(pr['dist']) else 1)
        ax.set_ylim(0, 1.15 * top_k)
        ax.set_ylabel('local backward coupling Kb (%)')
        ax.set_xlabel(f"distance along {vleg.split('/')[-1]} from its near "
                      f"end ({nearx['ref'] if nearx else '?'}), mm")
        ax.set_title(f"{r + 1}. {q['aggressor'].split('/')[-1]} -> "
                     f"{q['victim'].split('/')[-1]}: full-wave NEXT "
                     f"{q['next_pct']:.2f} %, FEXT {q['fext_pct']:.2f} % "
                     f"({rep['rise_ps']:g} ps edge)", fontsize=10)
        hand = [matplotlib.patches.Patch(color=c, alpha=0.6, label=l)
                for l, c in lay_col.items()
                if l in set(pr['layer'].tolist())]
        ax.legend(handles=hand, fontsize=8, loc='upper right')
        # the map: the victim coloured by Kb, round where it peaks
        mx = fig.add_subplot(gs[r, 0])
        # round the peak: its stretch above a quarter of it, within 15 mm
        if len(k):
            c0 = pr['xy'][int(np.argmax(k))]
            hot = pr['xy'][(k >= 0.25 * top_k) & (np.hypot(
                *(pr['xy'] - c0).T) < 7.5)]
            span = float(np.clip(1.4 * np.ptp(hot, 0).max(), 4.0, 15.0))
        else:
            c0, span = np.zeros(2), 4.0
        for t in kb.tracks:
            if t['net'] in anets:
                xy = np.asarray(t['pts'], float)
                mx.plot(xy[:, 0], xy[:, 1], color='0.6', lw=1.5)
        if len(k) > 1:
            seg = np.stack([pr['xy'][:-1], pr['xy'][1:]], 1)
            ok = np.hypot(*(seg[:, 1] - seg[:, 0]).T) < 0.3
            lc = LineCollection(seg[ok], cmap=cm, norm=matplotlib.colors
                                .Normalize(0, top_k), lw=3)
            lc.set_array(k[:-1][ok])
            mx.add_collection(lc)
            fig.colorbar(lc, ax=mx, orientation='horizontal', fraction=0.05,
                         pad=0.12, label='local Kb (%)')
        mx.set_xlim(c0[0] - span / 2, c0[0] + span / 2)
        mx.set_ylim(c0[1] + span / 2, c0[1] - span / 2)
        mx.set_aspect('equal')
        mx.set_title(f"where it peaks (aggressor grey), {span:.0f} mm",
                     fontsize=9)
    fig.suptitle("Where along each victim its crosstalk comes from: the local "
                 "backward-coupling coefficient Kb at the gap to the aggressor "
                 "(NEXT builds to Kb over a coupled stretch longer than half "
                 "the edge, ~7 mm at 100 ps; shorter stretches give less)",
                 fontsize=10, y=1.0)
    fig.savefig(path, dpi=100, bbox_inches='tight')
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
    p.add_argument('--pour', default=None, choices=('off', 'keep'),
                   help="a layer piece's own layer's ground pour (an RF board's "
                   "coplanar ground): keep (within K2E_SEG_POUR_REACH mm, 3, "
                   "its voids filled, its vias kept, mom25d's signal zone on) "
                   "or off (the planes alone; the default)")
    p.add_argument('--edges', default=None, choices=('fine', 'coarse'),
                   help="mom25d's mesh across the copper's edges "
                   "(--mom-edges): coarse ~7x fewer unknowns on narrow traces")
    p.add_argument('--via-solver', default=None,
                   help="the via models' solver where not --solver's: gfdtd, "
                   "or pbm (a physics-based model: the cavities between the "
                   "planes, no mesh; viamodel.py); mom25d meshes every box. "
                   "Unset, a via model that is no meshed box (a port with no "
                   "plane under it, fewer than 2 ground vias in its box) is "
                   "pbm's, said")
    p.add_argument('--dmax', type=float, default=None,
                   help="mom25d's largest cell along the copper, mm "
                   "(--mom-dmax)")
    p.add_argument('--ends', action='store_true',
                   help="ports at the nets' ends on pads (the default where "
                   "the board has no ports of its own)")
    p.add_argument('--solve-open', action='store_true',
                   help="solve the pieces whose every channel is open end to "
                   "end before solving too (by default left out: their "
                   "result would be those channels' own open S)")
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
