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
    n = len(names)
    big = np.zeros((nf, n, n), complex)
    k = 0
    for S in Ss:
        w = S.shape[1]
        big[:, k:k + w, k:k + w] = S
        k += w
    inner = [at[p] for p in used]
    outer = [i for i in range(n) if i not in set(inner)]
    G = np.zeros((len(inner), len(inner)))
    pos = {p: j for j, p in enumerate(inner)}
    for a, b in links:
        G[pos[at[a]], pos[at[b]]] = G[pos[at[b]], pos[at[a]]] = 1.0
    ii, ee = np.ix_(inner, inner), np.ix_(outer, outer)
    S_ii, S_ee = big[:, inner][:, :, inner], big[:, outer][:, :, outer]
    S_ie, S_ei = big[:, inner][:, :, outer], big[:, outer][:, :, inner]
    del ii, ee
    eye = np.eye(len(inner))
    out = np.empty((nf, len(outer), len(outer)), complex)
    for f in range(nf):
        X = np.linalg.solve(eye - S_ii[f] @ G, S_ie[f])
        out[f] = S_ee[f] + S_ei[f] @ G @ X
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


def via_groups(m, nets, gap=GAP, ends=()):
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
            # a via where its net ends at a port (a through-hole pad's own
            # barrel): no transition, the port is there
            if any(math.hypot(x - ex, y - ey) < r for ex, ey in ends):
                continue
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
    return [dict(vias=g, circle=unary_union(
        [Point(v['x'], v['y']).buffer(v['r'], 64) for v in g]))
        for g in out.values()]


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
                if inside.contains(Point(*(q + d * 1e-3))):
                    d = -d  # away from the via
                out.append(dict(layer=t['layer'], net=t['net'],
                                at=(float(q[0]), float(q[1])),
                                out=(float(d[0]), float(d[1]))))
    return out


def _fp(name, c, d, layer):
    """A cut port's footprint-like dict (as region.cut_port_fps's): at KiCad
    point c, pointing along d (KiCad) into its piece."""
    q = np.asarray(c, float) + np.asarray(d, float) * NUDGE
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
    b.ports = [b._port(raw, name, fp) for name, fp in ports]
    return b


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
        if clip is not None:
            g = g.difference(clip)
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
        if clip is not None:
            g = g.difference(clip)
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


def pieces(m, raw, kb, nets, apart_h=None):
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
    groups = via_groups(m, signal,
                        ends=[(float(p.c[0]), float(p.c[1])) for p in m.ports])
    every = unary_union([g['circle'] for g in groups]) if groups else None
    allcuts = []
    for gi, g in enumerate(groups):
        for c in cuts(kb, g, {v['net'] for v in g['vias']}):
            c['group'] = gi
            c['k'] = len(allcuts) + 1
            allcuts.append(c)
    out = []
    # the layers: every layer with signal copper
    # (outside the circles: a via's ring on a plane layer is the via's)
    sig_layers = [
        l for l in m.cu_order
        if any(n in signal and (every is None
                                or p.difference(every).area > 1e-6)
               for p, n in zip(m.copper.get(l, []), m.copper_nets.get(l, [])))
        or any(p.layer == l for p in m.ports)]
    ext = {p.name: p for p in m.ports}
    from .coupling import APART_H, groups as near_groups
    for lay in sig_layers:
        on = sorted({n for p, n in zip(m.copper.get(lay, []),
                                       m.copper_nets.get(lay, []))
                     if n in signal})
        h = _height(m, lay)
        apart = APART_H * h if apart_h is None else apart_h * h
        grps = (near_groups(kb, lay, on, apart, RUN)
                if len(on) > 1 and apart > 0 else [on])
        for gi, gnets in enumerate(grps):
            gnets = set(gnets)
            mine = [c for c in allcuts if c['layer'] == lay
                    and c['net'] in gnets]
            ports = [(f"P{1000 + c['k']}", _fp(f"cutL{c['k']}", c['at'],
                                                c['out'], lay))
                     for c in mine]
            fps = [(p.name, _port_fp(p)) for p in ext.values()
                   if p.layer == lay and getattr(p, 'net', None) in gnets]

            def keep(l, n, p, lay=lay, gnets=gnets):
                if l == lay:
                    if n not in gnets:
                        return None
                    return p.difference(every) if every is not None else p
                return p if n in m.ground_nets else None
            if not fps and not ports:
                continue  # slivers of a via's ring: nothing to join
            b = _piece(m, raw, keep, fps + ports, vias=lambda n, x, y: False)
            name = _safe(lay) + (f"_g{gi + 1}" if len(grps) > 1 else '')
            out.append(dict(kind='layer', name=name, layer=lay, board=b,
                            links=[(f"P{1000 + c['k']}", c['k'])
                                   for c in mine],
                            external=[n for n, _ in fps],
                            nets=sorted(gnets), clip=every))
    for gi, g in enumerate(groups):
        mine = [c for c in allcuts if c['group'] == gi]
        gnets = {v['net'] for v in g['vias']}
        circle = g['circle']
        x0, y0, x1, y1 = circle.buffer(GROUND_REACH).bounds
        bx = box(x0, y0, x1, y1)
        ports = [(f"P{1000 + c['k']}", _fp(f"cutV{c['k']}", c['at'],
                                            (-c['out'][0], -c['out'][1]),
                                            c['layer'])) for c in mine]

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
        b = _piece(m, raw, keep, ports, outline=bx,
                   vias=lambda n, x, y, bx=bx, gnets=gnets:
                   (n in gnets or n in m.ground_nets)
                   and bx.buffer(-1e-6).contains(_pt(x, y)))
        out.append(dict(kind='via', name=f"via{gi + 1}", board=b,
                        links=[(f"P{1000 + c['k']}", c['k']) for c in mine],
                        external=[], vias=g['vias']))
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
                thickness=None, dmax=None, layer_solver=None, via_solver=None):
    """A piece's run (its board file, kicad2ems's pipeline): (S at 50 ohm,
    its ports' names, the run's summary)."""
    from .config import build_settings
    from .pipeline import Job
    from .coupling import _over_its_plane
    os.makedirs(folder, exist_ok=True)
    if piece['kind'] == 'layer' and layer_solver:
        solver = layer_solver
    elif piece['kind'] == 'via' and via_solver:
        solver = via_solver
    d = _to_dict(piece['board'], piece)
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
            # the via's whole stack in its box, the planes perfect conductors
            # with their holes (mom25d's planes 'holes')
            cli['mom_stack'] = 'whole'
            cli['mom_planes'] = 'holes'
    if solver == 'mom25d' and dmax:
        cli['mom_dmax'] = dmax
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
    parts, allcuts = pieces(m, raw, raw, nets, a.apart)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    band = dict(fmin=a.fmin or s.fmin, fmax=a.fmax or s.fmax,
                points=a.points or s.points)
    echo(f"segment: {sum(p['kind'] == 'layer' for p in parts)} layer(s), "
         f"{sum(p['kind'] == 'via' for p in parts)} via model(s), "
         f"{len(allcuts)} cut(s)")
    blocks, links, f0, info, cut_of = [], {}, None, [], {}
    for p in parts:
        t = time.time()
        try:
            f, S, names, sm = solve_piece(p, os.path.join(out, p['name']),
                                          band, a.solver, echo,
                                          thickness=a.thickness,
                                          dmax=a.dmax,
                                          layer_solver=a.layer_solver,
                                          via_solver=a.via_solver)
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
        gl = [f"{p['name']}:{n}" for n in names]
        blocks.append((S, gl))
        for port, k in p['links']:
            links.setdefault(k, []).append(f"{p['name']}:{port}")
            cut_of[(p['name'], port)] = k
        info.append(dict(piece=p['name'], kind=p['kind'], ports=names,
                         seconds=round(time.time() - t, 1),
                         unknowns=(sm.get('solved', {}).get(a.solver) or {})
                         .get('unknowns')))
        echo(f"{p['name']}: {len(names)} ports, {time.time() - t:.0f} s")
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
    pairs = []
    for k, ends in links.items():
        if len(ends) != 2:
            raise ValueError(f"cut {k}: {len(ends)} sides ({', '.join(ends)})")
        X = cal.get(widths.get(k))
        if X is None:
            pairs.append(tuple(ends))
            continue
        xa, xb = f"cut{k}:a", f"cut{k}:b"
        blocks.append((X, [xa, xb]))
        pairs += [(ends[0], xa), (xb, ends[1])]
    S, names = join(blocks, pairs)
    ext = [n.split(':', 1)[1] for n in names]
    order = sorted(range(len(ext)), key=lambda i: _num(ext[i]))
    S, ext = S[:, order][:, :, order], [ext[i] for i in order]
    name = os.path.splitext(os.path.basename(s.board))[0]
    ts = os.path.join(out, f"{name}_segment.s{len(ext)}p")
    write_touchstone(ts, f0, S, 50.0,
                     comment=f"kicad2ems segment: ports {' '.join(ext)}")
    res = dict(format=FORMAT, version=1, board=os.path.abspath(s.board),
               ports=ext, port_nets=ends, touchstone=os.path.relpath(ts, out),
               cuts=[dict(k=c['k'], layer=c['layer'], net=c['net'],
                          at=c['at']) for c in allcuts],
               pieces=info, seconds=round(time.time() - t0, 1))
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
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


def end_ports(kb, nets):
    """Port specs at the ends of the nets (fnmatch patterns, comma-separated;
    ground nets left out) that are on pads: ([spec], [dict(port, net, ref,
    pad, layer)]), the nets' legs side by side."""
    from .coupling import select_nets, stretches
    from .si import _legs
    pats = [p for p in str(nets or '').split(',') if p.strip()]
    sel = [n for n in select_nets(kb, pats)
           if n.upper() not in ('GND', 'GROUND') and not n.upper().endswith(
               'GND')]
    by_layer, _ = stretches(kb, sel, None, 0.0)
    seen, specs, info = set(), [], []
    order = sorted(sel, key=lambda n: (min(
        (m for m in sel if m == n or _legs(m, n)), key=str), n))
    for net in order:
        for layer, es in by_layer.items():
            for e in es.get(net, ()):
                pad = e.get('pad')
                if not pad or (pad[0], pad[1]) in seen:
                    continue
                seen.add((pad[0], pad[1]))
                k = len(specs) + 1
                specs.append(f"P{k}={pad[0]}.{pad[1]}")
                info.append(dict(port=f"P{k}", net=net, ref=pad[0],
                                 pad=pad[1], layer=layer))
    return specs, info


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
                   "--solver's)")
    p.add_argument('--via-solver', default=None,
                   help="the via models' solver where not --solver's (gfdtd)")
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
