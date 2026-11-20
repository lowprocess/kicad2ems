"""
kicad2ems.core.coupling - a board's coupling, one layer at a time: many nets
named by patterns (a DDR bus, its byte lanes), each signal layer solved with
its reference planes alone, and for each victim its worst aggressors

    python -m kicad2ems.core.coupling board.kicad_pcb --nets 'DQ*,DQS*'
        --out out/cpl [--layers F.Cu,In2.Cu] [--solvers mom25d|lines]
        [--rise 100] [--limit 3] [--mode both|next|fext] [--top 3]
        [--min-length 5] [--near U1] [--z0 50] [--fmax GHz] [--sigma S/m]

The nets are those whose names match a pattern (fnmatch: DQ*, DQS?_[PN]; a
name alone is a pattern too), the board's poured nets (planes) left out. On
each copper layer where they run, each net's stretch there (its tracks on that
layer, kicad2ems.core.si.trace_ends with the layer: ended where it leaves the
layer, at a via or a pad) gets a port at its two ends; a stretch shorter than
--min-length mm (a breakout) is left out, and said. The board is imported once
with every layer's ports, and each layer is then solved on its own board file:
that layer's stretches and the ground copper of every layer (the reference
planes; mom25d takes solid planes as PEC), the selected nets' vias and their
copper on the other layers left out. So a layer's figures are its own coupling:
what crosses between layers (a via's field, a plane's gap, broadside traces) is
not in them, and the result says so.

A differential pair (its legs by their names: D0_P / D0_N, TX+ / TX-) is one
channel (--legs: two nets): it drives, and its receiver sees, in its
differential mode; crosstalk between channels of other modes is the voltage
share (S scaled by the modes' reference impedances), and each pair's own
conversion to common mode is reported. With each channel's near end driven in
turn (the near end: the one at a part
--near names, else the end nearest the layer's first net's first end), a
victim's NEXT is S(victim near, aggressor near) and its FEXT S(victim far,
aggressor near), each ports' termination z0; in time, the largest share of the
aggressor's swing at that end for an edge of --rise ps (kicad2ems.core.si's
edge response). Each layer gets the matrices of NEXT and FEXT (victim by
aggressor), each victim its --top worst aggressors and their sum (the worst
case: every aggressor's peak at once), and the worst pairs; --limit judges
the worst victim's sum (--mode: of NEXT, FEXT or the larger).

mom25d is the default solver (lumped ports: a wave port's fit takes a
neighbour's coupling out); the line model (--solvers lines) answers in seconds
for a first look, coupling only within its coupled sections on a layer.

Results: <out>/coupling.json, <out>/coupling_<layer>.png (the matrices, and
the layer's stretches coloured by each victim's sum), and each layer's run in
<out>/<layer>/.
"""
import argparse
import copy
import fnmatch
import json
import math
import os
import re
import sys

import numpy as np

RESULT = 'coupling.json'
FORMAT = 'kicad2ems-coupling'
# mm: stretches shorter than this on a layer (breakouts) are left out
MIN_LENGTH = 5.0
# a victim's worst aggressors listed
TOP = 3
# the worst pairs listed for each layer
PAIRS = 20


# ---- which nets, and where each runs ----------------------------------------
def select_nets(kb, patterns):
    """The routed nets of board kb whose names match one of `patterns` (fnmatch,
    case-sensitive as KiCad's names are), in the board's order of their
    tracks; nets with zones (planes, pours) are not traces."""
    poured = set(getattr(kb, 'zone_nets', ()))
    pats = [p.strip() for p in patterns if p.strip()]
    nets = dict.fromkeys(
        t['net'] for t in kb.tracks
        if t['net'] and t['net'] not in poured
        and any(fnmatch.fnmatchcase(t['net'], p) for p in pats))
    return sorted(nets, key=_natural)


def _natural(name):
    """DQ2 before DQ10."""
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', name)]


def stretches(kb, nets, layers=None, min_length=MIN_LENGTH):
    """({layer: {net: (end, end)}}, left out [dict(net, layer, length_mm,
    why)]): each net's stretch on each copper layer it has tracks on (layers:
    those alone), its two ends (si.trace_ends on the layer), the copper order
    of the board's layers."""
    from .si import trace_ends
    order = list(getattr(kb, 'copper_layers', None) or [])
    out, left = {}, []
    for net in nets:
        on = dict.fromkeys(t['layer'] for t in kb.tracks if t['net'] == net)
        for layer in on:
            if layers and layer not in layers:
                continue
            try:
                ends = trace_ends(kb, net, layer=layer)
            except ValueError as e:
                left.append(dict(net=net, layer=layer, length_mm=0.0,
                                 why=str(e)))
                continue
            length = ends[0]['length']
            if length < min_length:
                left.append(dict(
                    net=net, layer=layer, length_mm=float(length),
                    why=f"{length:.1f} mm on {layer}: shorter than "
                    f"{min_length:g} mm"))
                continue
            out.setdefault(layer, {})[net] = tuple(ends)
    rank = {l: i for i, l in enumerate(order)}
    return (dict(sorted(out.items(), key=lambda kv: rank.get(kv[0], 99))),
            left)


def across(ends):
    """The nets of one layer's stretches ({net: (end, end)}) in their order
    across the way they run (the mean direction from end to end): neighbours
    next to each other, as the matrices are read."""
    d = np.zeros(2)
    for a, b in ends.values():
        v = np.array([b['x'] - a['x'], b['y'] - a['y']])
        d += v if v @ d >= 0 else -v
    if not d.any():
        return list(ends)
    n = np.array([-d[1], d[0]]) / np.hypot(*d)
    mid = lambda e: np.array([(e[0]['x'] + e[1]['x']) / 2,
                              (e[0]['y'] + e[1]['y']) / 2])
    return sorted(ends, key=lambda net: float(mid(ends[net]) @ n))


def tracks_on(kb, net, layer):
    """A net's tracks on a layer as polylines [[[x, y], ...]] (KiCad mm), for
    the figure."""
    return [np.round(np.asarray(t['pts'], float), 4).tolist()
            for t in kb.tracks if t['net'] == net and t['layer'] == layer]


# a layer's nets further apart than this many heights over its plane are
# solved as separate groups (microstrip crosstalk falls as 1 / (1 + (s/h)^2):
# ~0.25 % of its close range at 20 heights)
APART_H = 20.0


def groups(kb, layer, nets, apart, run=0.0):
    """A layer's nets in groups: those whose tracks there come within `apart`
    mm of each other (through others) one group, in the nets' order; run > 0:
    only where one runs that near the other over `run` mm of its length or
    more (a close approach at a connector's pads does not chain two buses
    that couple nowhere else: a CM5 carrier's D-PHY, PCIe and USB-C). apart
    <= 0: one group."""
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union
    nets = list(nets)
    if apart is None or apart <= 0 or len(nets) < 2:
        return [nets]
    geom, axis = {}, {}
    for n in nets:
        parts, lines = [], []
        for t in kb.tracks:
            if t['net'] == n and t['layer'] == layer:
                pts = [tuple(p) for p in t['pts']]
                g = (LineString(pts) if len(pts) > 1 else Point(pts[0]))
                parts.append(g.buffer(float(t.get('width') or 0) / 2))
                if len(pts) > 1:
                    lines.append(g)
        geom[n] = unary_union(parts) if parts else Point(0, 0).buffer(0)
        axis[n] = unary_union(lines) if lines else None
    near = {}

    from .si import _legs

    def coupled(a, b):
        if _legs(a, b):
            return True  # a pair's two legs: one group, always
        if geom[a].distance(geom[b]) > apart:
            return False
        if run <= 0:
            return True
        for x, y in ((a, b), (b, a)):
            if axis[x] is None:
                continue
            zone = near.setdefault(y, geom[y].buffer(apart, quad_segs=4))
            if axis[x].intersection(zone).length >= run:
                return True
        return False
    up = {n: n for n in nets}

    def root(n):
        while up[n] != n:
            up[n] = up[up[n]]
            n = up[n]
        return n
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            if root(a) != root(b) and coupled(a, b):
                up[root(a)] = root(b)
    out = {}
    for n in nets:
        out.setdefault(root(n), []).append(n)
    return list(out.values())


def merged(mats):
    """Groups' matrices (matrix's dicts) as one: their channels in turn, the
    coupling between groups none (0 %, -300 dB)."""
    if len(mats) == 1:
        return mats[0]
    nets = [c for m in mats for c in m['nets']]
    n, out, k = len(nets), {}, 0
    for key in ('next_pct', 'fext_pct', 'next_sign', 'fext_sign', 'next_db',
                'fext_db'):
        out[key] = np.full((n, n), -300.0 if key.endswith('_db') else 0.0)
    for m in mats:
        w = len(m['nets'])
        for key in list(out):
            out[key][k:k + w, k:k + w] = np.asarray(m[key])
        k += w
    out = {key: v.tolist() for key, v in out.items()}  # (as matrix's: JSON)
    out['nets'] = nets
    for key in ('kinds', 'legs', 'conversion'):
        out[key] = {c: v for m in mats for c, v in m[key].items()}
    return out


def ports(by_layer, near=()):
    """(the --port specs, {layer: {port: (net, 'near' | 'far')}}, {layer: {net:
    (near end, far end)}}): every layer's stretches, numbered across the
    layers (P1, P2, ...), each layer's ends ordered (si.order_ends)."""
    from .si import order_ends
    specs, names, ordered, k = [], {}, {}, 0
    for layer, ends in by_layer.items():
        ordered[layer] = order_ends({n: ends[n] for n in across(ends)}, near)
        names[layer] = {}
        for net, pair in ordered[layer].items():
            for e, which in zip(pair, ('near', 'far')):
                k += 1
                name = f"P{k}"
                specs.append(
                    f"{name}={e['pad'][0]}.{e['pad'][1]}" if e['pad'] else
                    f"{name}@{e['at'][0]:.4f},{e['at'][1]:.4f},{e['layer']}")
                names[layer][name] = (net, which)
    return specs, names, ordered


def layer_board(full, layer, nets, port_names):
    """A board file's dict (Board.to_dict) cut to one layer's question: the
    `nets`' copper on `layer`, the ground nets' copper on every layer (the
    reference planes) and their vias, the ports `port_names`; the selected
    nets' vias and their copper elsewhere, and the parts, left out."""
    d = copy.deepcopy(full)
    ground = set(d.get('ground_nets') or ())
    nets = set(nets)
    keep = lambda lay, net: net in ground or (lay == layer and net in nets)
    d['copper'] = {l: [p for p in ps if keep(l, p['net'])]
                   for l, ps in d['copper'].items()}
    d['vias'] = [v for v in d['vias'] if v.get('net') in ground]
    d['ports'] = [p for p in d['ports'] if p['name'] in port_names]
    d['parts'] = []
    if d.get('nets') is not None:
        d['nets'] = sorted(ground | nets)
    pr = d.get('prims') or {}
    pr['items'] = [it for it in pr.get('items', [])
                   if keep(it['layer'], it['net'])]
    pr['tracks'] = [t for t in pr.get('tracks', [])
                    if keep(t['layer'], t['net'])]
    pr['vias'] = [v for v in pr.get('vias', []) if v.get('net') in ground]
    d['prims'] = pr
    d['notes'] = list(d.get('notes', [])) + [
        f"coupling: {layer} alone ({len(nets)} nets) over the ground copper"]
    _close_antipads(d)
    return d


POUR_REACH_H, POUR_REACH_MIN = 15.0, 1.0  # the signal layer's ground kept


def _ground_near(d, layer, nets):
    """mom25d's board for a group: the ground copper on the signal layer kept
    within POUR_REACH_H heights over its plane (POUR_REACH_MIN mm at least) of
    the group's nets, cut along a coarse outline (a cut that follows the traces
    had more edges to mesh than it saved), and the ground vias left out where
    none of it is (the plane's own: it is not meshed). What was done, or ''."""
    from shapely.geometry import Point, mapping, shape
    from shapely.ops import unary_union
    ground = set(d.get('ground_nets') or ())
    cu = d['copper'].get(layer) or []
    sig = unary_union([shape(c['polygon']) for c in cu if c['net'] in nets])
    if sig.is_empty:
        return ''
    zs = {c['name']: c['z'] for c in d['stack']['copper']}
    ref = d.get('ground')
    h = abs(zs.get(ref, zs[layer]) - zs[layer])
    reach = max(POUR_REACH_MIN, POUR_REACH_H * h)
    # a coarse outline: square-ended, simplified to a tenth of the reach
    near = sig.buffer(reach, quad_segs=1, join_style='mitre').simplify(
        reach / 10)
    before = after = 0.0
    keep = []
    for c in cu:
        if c['net'] not in ground:
            keep.append(c)
            continue
        g = shape(c['polygon'])
        before += g.area
        g = g.intersection(near)
        for piece in getattr(g, 'geoms', [g]):
            if piece.geom_type == 'Polygon' and piece.area > 1e-4:
                after += piece.area
                keep.append(dict(c, polygon=mapping(piece)))
    if before - after < 1e-3:
        return ''
    d['copper'][layer] = keep
    pr = d['prims']
    items = []
    for it in pr.get('items', []):
        if it['layer'] != layer or it['net'] not in ground:
            items.append(it)
            continue
        g = shape(it['geom']).intersection(near)
        for piece in getattr(g, 'geoms', [g]):
            if piece.geom_type == 'Polygon' and piece.area > 1e-4:
                items.append(dict(it, geom=mapping(piece)))
    pr['items'] = items
    pr['tracks'] = [t for t in pr.get('tracks', [])
                    if t['layer'] != layer or t['net'] not in ground
                    or near.intersects(Point(*t['pts'][0]))]
    kept = unary_union([shape(c['polygon']) for c in keep
                        if c['net'] in ground])
    nv = len(d.get('vias') or [])

    def touches(v):
        at = v.get('xy') or (v.get('x'), v.get('y'))
        return at[0] is not None and kept.intersects(Point(*at))
    d['vias'] = [v for v in d.get('vias') or [] if touches(v)]
    pr['vias'] = [v for v in pr.get('vias') or [] if touches(v)]
    said = (f"its ground on {layer} kept within {reach:.2f} mm of its nets "
            f"({after:.0f} of {before:.0f} mm2), {len(d['vias'])} of {nv} "
            "ground vias")
    d['notes'].append(f"coupling: {said}")
    return said


def _over_its_plane(d, layer):
    """A layer's board (layer_board's) set up as its signal copper over its
    reference plane for mom25d: the plane its ports reference named as the
    ground, and the board turned over (every height mirrored) when that plane is
    above the layer (B.Cu under In4.Cu), mom25d's ground being below its
    copper. The plane's name, or None (no port says one)."""
    import collections
    refs = collections.Counter(p.get('ref_layer') for p in d.get('ports') or ()
                               if p.get('ref_layer'))
    if not refs:
        return None
    ref = refs.most_common(1)[0][0]
    st = d['stack']
    cu_z = {c['name']: c['z'] for c in st['copper']}
    if cu_z.get(ref, 0.0) > cu_z.get(layer, 0.0):
        T = float(st['thickness'])
        for lay in st['layers']:
            lay['z0'], lay['z1'] = T - lay['z1'], T - lay['z0']
        st['layers'].sort(key=lambda lay: lay['z0'])
        for c in st['copper']:
            c['z'] = T - c['z']
        st['copper'].sort(key=lambda c: c['z'])
        for v in d.get('vias') or ():
            if 'z0' in v and 'z1' in v:
                v['z0'], v['z1'] = T - v['z1'], T - v['z0']
        for p in d.get('ports') or ():
            for k in ('z_sig', 'z_ref'):
                if p.get(k) is not None:
                    p[k] = T - p[k]
        d['turned'] = True
        d['notes'].append(f"coupling: turned over, {layer} on top of {ref}")
    d['ground'] = ref
    return ref


def _close_antipads(d):
    """A port at its net's own via (a trace that leaves the layer there) had
    that via's ring on the plane layer under it as its reference; with the via
    left out, its antipad is a hole there and mom25d's port ended on no metal
    (a real board's PCIe, Ethernet, USB: every such port). The hole under each
    port closed with the nearest ground copper of its reference layer, as if
    the plane ran on under the trace's end (the via is not in the question)."""
    from shapely.geometry import Point, mapping, shape
    for p in d.get('ports') or ():
        ref = p.get('ref_layer')
        cu = d['copper'].get(ref) or []
        if not cu or p.get('c') is None:
            continue
        at = Point(*p['c'])
        polys = [(shape(c['polygon']), c['net']) for c in cu]
        if any(g.covers(at) for g, _ in polys):
            continue
        g, net = min(polys, key=lambda t: t[0].distance(at))
        gap = g.distance(at)
        if gap > max(1.0, 4 * (p.get('w') or 0.0)):  # no plane near: not a hole
            continue
        disc = at.buffer(gap + max(0.05, 0.5 * (p.get('w') or 0.0)))
        cu.append(dict(net=net, polygon=mapping(disc)))
        d['prims'].setdefault('items', []).append(
            dict(layer=ref, net=net, shape=False, geom=mapping(disc)))
        d['notes'].append(
            f"{p['name']}: the antipad under it on {ref} closed (its via is "
            "not in the question)")


# ---- from S-parameters: the coupling matrix ---------------------------------
def channels(nets, pairs=True):
    """The channels of a layer's nets, in their order: [dict(name, legs, kind)],
    kind 'single' (one net) or 'pair' (the two legs of a differential pair by
    their names, si._legs: D0_P / D0_N, TX+ / TX-; the positive leg first), its
    name the legs' common stem. pairs False: every net single."""
    from .si import _legs
    out, used = [], set()
    for a in nets:
        if a in used:
            continue
        b = next((x for x in nets if pairs and x != a and x not in used
                  and _legs(a, x)), None)
        if b is None:
            out.append(dict(name=a, legs=[a], kind='single'))
            used.add(a)
            continue
        pos, neg = sorted((a, b), key=lambda n: n[-1].upper() not in 'P+')
        stem = re.sub(r'[_.]?[PN+-]$', '', pos, flags=re.I) or pos
        out.append(dict(name=stem, legs=[pos, neg], kind='pair'))
        used |= {a, b}
    return out


def modal(S, names, chans, z0=50.0):
    """S of the channels' modes at their two ends from single-ended S (ports
    `names`: {port: (net, end)} in S's order, each terminated in z0): (Sm (nf,
    m, m), rows [(channel, mode, end)], zref per row). A single net is its own
    mode 'se' (z0); a pair its differential 'd' (2 z0) and common 'c' (z0 / 2)
    modes, (p - n) / sqrt 2 and (p + n) / sqrt 2 (power waves: the core's
    sparams.mixed_mode convention)."""
    idx = {v: i for i, v in enumerate(names.values())}
    n, rows, M, zref = len(names), [], [], []
    r2 = 1 / math.sqrt(2)
    for c in chans:
        for end in ('near', 'far'):
            if c['kind'] == 'single':
                v = np.zeros(n)
                v[idx[(c['legs'][0], end)]] = 1
                rows.append((c['name'], 'se', end))
                M.append(v)
                zref.append(z0)
                continue
            ip, im = idx[(c['legs'][0], end)], idx[(c['legs'][1], end)]
            for mode, sgn, z in (('d', -1, 2 * z0), ('c', 1, z0 / 2)):
                v = np.zeros(n)
                v[ip], v[im] = r2, sgn * r2
                rows.append((c['name'], mode, end))
                M.append(v)
                zref.append(z)
    M = np.array(M)
    return np.einsum('ij,fjk,lk->fil', M, S, M), rows, zref


def matrix(f, S, names, rise_s, pairs=True, z0=50.0):
    """The coupling of one layer's S (nf x n x n, ports `names`: {port: (net,
    end)} in S's order, terminated in z0) between its channels (channels: a
    differential pair one, in its differential mode, as it drives and as a
    receiver sees it; pairs False: every net single-ended): dict(nets (the
    channels' names), kinds, legs, next_pct, fext_pct (victim x aggressor, % of
    the aggressor's swing in its mode, as voltage, for the edge; the diagonal
    0), next_db, fext_db (the modes' S peaks over f, dB), next_sign, fext_sign,
    conversion {pair: dict(near_db, far_db, near_pct, far_pct)}: its own
    differential drive at its near end seen in common mode at either end)."""
    from .si import edge_response
    nets = list(dict.fromkeys(n for n, _ in names.values()))
    chans = channels(nets, pairs)
    Sm, rows, zref = modal(S, names, chans, z0)
    at = {r: i for i, r in enumerate(rows)}
    main = {c['name']: ('se' if c['kind'] == 'single' else 'd') for c in chans}
    n = len(chans)
    out = {k: np.zeros((n, n)) for k in ('next_pct', 'fext_pct', 'next_sign',
                                         'fext_sign')}
    out.update(next_db=np.full((n, n), -300.0), fext_db=np.full((n, n), -300.0))

    def edge(h, zv, za):
        """The edge's largest share at the victim, as voltage: S scaled by
        sqrt(zv / za) between modes of other reference impedances."""
        _, y = edge_response(f, h, rise_s)
        k = int(np.argmax(np.abs(y)))
        return 100 * abs(y[k]) * math.sqrt(zv / za), float(np.sign(y[k]))
    for j, a in enumerate(chans):
        src = at[(a['name'], main[a['name']], 'near')]
        for i, v in enumerate(chans):
            if i == j:
                continue
            for what, end in (('next', 'near'), ('fext', 'far')):
                dst = at[(v['name'], main[v['name']], end)]
                h = Sm[:, dst, src]
                pct, sgn = edge(h, zref[dst], zref[src])
                out[f"{what}_pct"][i, j] = pct
                out[f"{what}_sign"][i, j] = sgn
                out[f"{what}_db"][i, j] = float(
                    20 * np.log10(max(np.abs(h).max(), 1e-15)))
    conv = {}
    for c in chans:
        if c['kind'] != 'pair':
            continue
        src = at[(c['name'], 'd', 'near')]
        row = {}
        for end in ('near', 'far'):
            dst = at[(c['name'], 'c', end)]
            h = Sm[:, dst, src]
            row[f"{end}_db"] = float(20 * np.log10(max(np.abs(h).max(), 1e-15)))
            row[f"{end}_pct"] = edge(h, zref[dst], zref[src])[0]
        conv[c['name']] = row
    return dict(nets=[c['name'] for c in chans],
                kinds={c['name']: c['kind'] for c in chans},
                legs={c['name']: c['legs'] for c in chans}, conversion=conv,
                **{k: v.tolist() for k, v in out.items()})


def summary(m, top=TOP, pairs=PAIRS):
    """Each victim's worst aggressors and their sums, and the worst pairs, of
    a matrix (matrix's dict): (victims [dict(victim, worst [dict(aggressor,
    next_pct, fext_pct)], next_sum_pct, fext_sum_pct[, pair_leg])], pairs
    [dict(aggressor, victim, next_pct, fext_pct)]). A differential pair's other
    leg (si._legs: D0_P / D0_N) is not an aggressor: pair_leg names it."""
    from .si import _legs
    nets = m['nets']
    N, F = np.asarray(m['next_pct']), np.asarray(m['fext_pct'])
    # the two legs of a differential pair (D0_P / D0_N) are coupled on
    # purpose: not each other's aggressors (the matrix keeps them)
    other = lambda i, j: i != j and not _legs(nets[i], nets[j])
    vs = []
    for i, v in enumerate(nets):
        js = [j for j in range(len(nets)) if other(i, j)]
        order = sorted(js, key=lambda j: -max(N[i, j], F[i, j]))
        partner = [nets[j] for j in range(len(nets))
                   if j != i and not other(i, j)]
        vs.append(dict(
            victim=v, worst=[dict(aggressor=nets[j], next_pct=float(N[i, j]),
                                  fext_pct=float(F[i, j]))
                             for j in order[:top]],
            next_sum_pct=float(N[i, js].sum()),
            fext_sum_pct=float(F[i, js].sum()),
            **({'pair_leg': partner[0]} if partner else {})))
    flat = [(i, j) for i in range(len(nets)) for j in range(len(nets))
            if other(i, j)]
    flat.sort(key=lambda ij: -max(N[ij], F[ij]))
    ps = [dict(aggressor=nets[j], victim=nets[i], next_pct=float(N[i, j]),
               fext_pct=float(F[i, j])) for i, j in flat[:pairs]]
    return vs, ps


def worst(layers, mode='both', limit=None):
    """Over every layer: the worst victim's sum and the worst pair, and the
    limit's verdict on the sum (None: no limit)."""
    pick = dict(next=lambda r: r['next_sum_pct'],
                fext=lambda r: r['fext_sum_pct'],
                both=lambda r: max(r['next_sum_pct'], r['fext_sum_pct']))[mode]
    pp = dict(next=lambda r: r['next_pct'], fext=lambda r: r['fext_pct'],
              both=lambda r: max(r['next_pct'], r['fext_pct']))[mode]
    vs = [dict(v, layer=L['layer']) for L in layers for v in L['victims']]
    ps = [dict(p, layer=L['layer']) for L in layers for p in L['pairs']]
    if not vs:
        return None
    v, p = max(vs, key=pick), max(ps, key=pp) if ps else None
    return dict(victim=v['victim'], layer=v['layer'], sum_pct=pick(v),
                next_sum_pct=v['next_sum_pct'], fext_sum_pct=v['fext_sum_pct'],
                aggressors=[w['aggressor'] for w in v['worst']],
                pair=p, pair_pct=pp(p) if p else None,
                ok=None if limit is None else bool(pick(v) <= limit))


# ---- the question ------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(prog='python -m kicad2ems.core.coupling',
                                description=__doc__.split('\n\n')[0].strip())
    p.add_argument('board', help=".kicad_pcb")
    p.add_argument('--nets', required=True,
                   help="the nets, by name patterns, comma-separated (DQ*,DQS*)")
    p.add_argument('--layers', default='',
                   help="the copper layers to solve, comma-separated (default: "
                   "every layer the nets run on)")
    p.add_argument('--near', default='',
                   help="the parts at the near (driven) ends, comma-separated")
    p.add_argument('--rise', type=float, default=100.0,
                   help="the aggressors' edge, ps (10 to 90 %%)")
    p.add_argument('--limit', type=float, default=None,
                   help="the most a victim may take from all its aggressors, "
                   "%% of the swing")
    p.add_argument('--mode', default='both', choices=('both', 'next', 'fext'),
                   help="what --limit and the headline are about")
    p.add_argument('--legs', action='store_true',
                   help="a differential pair's legs as two single-ended nets "
                   "(default: the pair one channel, in its differential mode)")
    p.add_argument('--top', type=int, default=TOP,
                   help="each victim's worst aggressors listed")
    p.add_argument('--min-length', type=float, default=MIN_LENGTH,
                   help="mm: shorter stretches on a layer are left out")
    p.add_argument('--apart', type=float, default=None,
                   help="mm: a layer's nets further apart than this are "
                   "solved as separate groups, their coupling taken as none "
                   f"(default: {APART_H:g} heights over the layer's plane; 0: "
                   "one group)")
    p.add_argument('--solvers', default='mom25d',
                   help="mom25d (default) or lines (the line model: a first "
                   "look)")
    p.add_argument('--z0', type=float, default=50.0,
                   help="the ports' impedance: each net's termination")
    p.add_argument('--fmax', type=float, default=None,
                   help="GHz (default: 1 / rise)")
    p.add_argument('--sigma', type=float, default=None,
                   help="copper conductivity, S/m (default: lossless)")
    p.add_argument('--out', default=None, help="output folder")
    return p


def run(a, echo=print):
    """The question for parsed arguments `a`; returns coupling.json's dict."""
    from .importers.kicad import KiCadBoard
    from .config import build_settings
    from .pipeline import board_from_settings
    from .si import time_grid, _end
    kb = KiCadBoard(a.board)
    nets = select_nets(kb, a.nets.split(','))
    if len(nets) < 2:
        raise ValueError(f"--nets {a.nets}: {len(nets)} routed net(s) match; "
                         "two or more are needed")
    layers = [l.strip() for l in a.layers.split(',') if l.strip()]
    by_layer, left = stretches(kb, nets, layers or None, a.min_length)
    by_layer = {l: e for l, e in by_layer.items() if len(e) >= 2}
    if not by_layer:
        raise ValueError("no layer has two or more of the nets' stretches of "
                         f"{a.min_length:g} mm or more")
    out = os.path.abspath(a.out or os.path.join(
        os.path.dirname(os.path.abspath(a.board)), 'out', 'coupling'))
    os.makedirs(out, exist_ok=True)
    specs, names, ordered = ports(by_layer,
                                  [r for r in a.near.split(',') if r.strip()])
    echo(f"coupling: {len(nets)} nets ({', '.join(nets)}); "
         + "; ".join(f"{l}: {len(e)}" for l, e in by_layer.items()))
    for row in left:
        echo(f"  left out: {row['net']} on {row['layer']} ({row['why']})")
    rise = a.rise * 1e-12
    fmax = a.fmax or min(max(1e-9 / rise, 1.0), 40.0)                # GHz
    longest = max(e[0]['length'] for es in by_layer.values()
                  for e in es.values())
    er = max([it.get('er', 1.0) for it in kb.stack_items
              if it.get('kind') == 'diel'] or [4.5])
    fmin, points = time_grid(fmax * 1e9, longest, er)
    fmin /= 1e9
    # the board once, every layer's ports on it
    base = dict(board=a.board, ports=specs, nets=['auto'], parts='none',
                **({'sigma': a.sigma} if a.sigma else {}))
    m = board_from_settings(build_settings(dict(base)), log=lambda *x: None)
    full = m.to_dict()
    got = {p['name'] for p in full['ports']}
    name = os.path.splitext(os.path.basename(a.board))[0]
    result_layers = []
    for layer, ends in by_layer.items():
        want = names[layer]
        miss = [p for p in want if p not in got]
        if miss:
            raise ValueError(f"{layer}: ports {', '.join(miss)} were not "
                             "placed on the board")
        sub = os.path.join(out, _safe(layer))
        hs = [abs(p['z_sig'] - p['z_ref']) for p in full['ports']
              if p['name'] in want and p.get('z_ref') is not None]
        apart = (a.apart if a.apart is not None
                 else APART_H * min(hs) if hs else 0.0)
        grps = groups(kb, layer, list(ends), apart)
        if len(grps) > 1:
            echo(f"{layer}: {len(grps)} groups of nets more than "
                 f"{apart:.2f} mm apart, solved on their own (their coupling "
                 f"taken as none): {', '.join(str(len(g)) for g in grps)} nets")
        mats, solved_by, runs = [], [], []
        for gi, gnets in enumerate(grps):
            gwant = {p: v for p, v in want.items() if v[0] in gnets}
            gsub = (sub if len(grps) == 1
                    else os.path.join(sub, f"group{gi + 1}"))
            os.makedirs(gsub, exist_ok=True)
            tag = _safe(layer) + (f"_group{gi + 1}" if len(grps) > 1 else '')
            what = layer + (f" group {gi + 1}" if len(grps) > 1 else '')
            mat, label, ts, run_sum = _solve_group(
                a, full, layer, gnets, gwant, gsub, f"{name}_{tag}", what,
                dict(fmin=fmin, fmax=fmax, points=points), rise, echo)
            mats.append(mat)
            solved_by.append(label)
            runs.append(dict(nets=list(gnets), touchstone=os.path.relpath(
                ts, out), warnings=run_sum.get('warnings', [])))
        mat = merged(mats)
        vs, ps = summary(mat, a.top)
        result_layers.append(dict(
            layer=layer, label=solved_by[0],
            touchstone=runs[0]['touchstone'], nets=mat['nets'],
            groups=runs if len(runs) > 1 else None,
            apart_mm=apart if len(runs) > 1 else None,
            ports={p: dict(net=n, end=e) for p, (n, e) in want.items()},
            ends={n: dict(near=_end(e0), far=_end(e1),
                          length_mm=float(e0['length']))
                  for n, (e0, e1) in ordered[layer].items()},
            matrix={k: v for k, v in mat.items()
                    if k not in ('nets', 'kinds', 'legs', 'conversion')},
            kinds=mat['kinds'], legs=mat['legs'],
            conversion=mat['conversion'],
            tracks={n: tracks_on(kb, n, layer)
                    for legs in mat['legs'].values() for n in legs},
            victims=vs, pairs=ps,
            warnings=[x for r in runs for x in r['warnings']]))
        w = vs and max(vs, key=lambda r: max(r['next_sum_pct'],
                                             r['fext_sum_pct']))
        if w:
            echo(f"{layer}: worst victim {w['victim']}: NEXT "
                 f"{w['next_sum_pct']:.2f} %, FEXT {w['fext_sum_pct']:.2f} % "
                 f"from all, the worst from "
                 f"{', '.join(x['aggressor'] for x in w['worst'])}")
        for k, c in mat['conversion'].items():
            echo(f"{layer}: pair {k}: differential to common {c['near_pct']:.2f} "
                 f"% at its near end, {c['far_pct']:.2f} % at its far end")
    res = dict(
        format=FORMAT, version=1, board=os.path.abspath(a.board), name=name,
        patterns=a.nets, nets=nets, rise_ps=a.rise, limit_pct=a.limit,
        mode=a.mode, z0=a.z0, solvers=a.solvers, min_length_mm=a.min_length,
        layers=result_layers, left_out=left,
        not_modelled=["what crosses between layers (vias, a reference "
                      "plane's gaps, broadside traces on adjacent layers): "
                      "each layer is solved on its own"])
    res['worst'] = w = worst(result_layers, a.mode, a.limit)
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    try:
        for L in result_layers:
            plot(res, L, os.path.join(out, f"coupling_{_safe(L['layer'])}.png"))
    except Exception as e:  # a figure never fails the question  # noqa: BLE001
        echo(f"note: no figure ({type(e).__name__}: {e})")
    if w:
        echo(f"coupling: worst victim {w['victim']} on {w['layer']}, "
             f"{w['sum_pct']:.2f} % from all its aggressors ({a.mode}); worst "
             f"pair {w['pair']['aggressor']} -> {w['pair']['victim']} "
             f"{w['pair_pct']:.2f} %"
             + (f"; limit {a.limit:g} %: "
                f"{'met' if w['ok'] else 'NOT met'}"
                if a.limit is not None else ''))
    echo(f"-> {os.path.join(out, RESULT)}")
    return res


def _solve_group(a, full, layer, nets, want, sub, stem, what, band, rise,
                 echo):
    """One group of a layer's nets solved on its own board (layer_board's):
    (its matrix, the solver's label, its Touchstone, the run's summary)."""
    from .config import build_settings
    from .pipeline import Job
    from .touchstone import read_touchstone
    from .si import renormalised
    bj = os.path.join(sub, f"{stem}_board.json")
    lb = layer_board(full, layer, list(nets), set(want))
    cli = dict(board=bj, out=sub, solvers=a.solvers, no_report=True, **band,
               **({'sigma': a.sigma} if a.sigma else {}))
    if 'mom25d' in a.solvers:
        cli['mom_port'] = 'lumped'
        ref = _over_its_plane(lb, layer)
        if ref:
            # the layer over its own plane (the copper beyond left out), not
            # the whole stack meshed: a real board's planes have apertures
            # under its traces, and mom25d's auto stack then meshed them all
            # (a CM5 carrier: 1.6 TiB)
            cli['mom_stack'] = 'ground'
            echo(f"{what}: over {ref}" + (" (the board turned over: mom25d's "
                 "ground is below its copper)" if lb.get('turned') else ''))
            # its own layer's ground near the group alone, meshed in detail
            # only near the traces: a pour over the whole layer and its
            # stitching vias had been most of the question (CM5's F.Cu:
            # 155,000 unknowns and 37,000 via unknowns for 16 nets)
            said = _ground_near(lb, layer, nets)
            if said:
                echo(f"{what}: {said}")
            cli['mom_zone'] = 'on'
    with open(bj, 'w', encoding='utf-8') as fh:
        json.dump(lb, fh)
    echo(f"{what}: {len(nets)} nets, {len(want)} ports, {band['fmin']:.3g} to "
         f"{band['fmax']:g} GHz in {band['points']} points: {a.solvers}")
    job = Job(build_settings(cli), echo=echo)
    job.run()
    with open(os.path.join(sub, f"{job.name}_run.json"),
              encoding='utf-8') as fh:
        run_sum = json.load(fh)
    order = run_sum['ports']
    lnames = {p: want[p] for p in order if p in want}
    if len(lnames) != len(want):
        raise ValueError(f"{what}: the run has ports {', '.join(order)}, "
                         f"not every stretch's two ends")
    solved = {k: r for k, r in run_sum.get('solved', {}).items()
              if r.get('touchstone')}
    if not solved:
        raise ValueError(f"{what}: no solver gave S-parameters "
                         f"({run_sum.get('warnings')})")
    key = next((k for k in solved if 'line model' not in
                solved[k].get('label', k)), next(iter(solved)))
    ts = os.path.join(sub, solved[key]['touchstone'])
    f, S = read_touchstone(ts)
    S = renormalised(S, run_sum.get('zref') or 50.0, a.z0)
    mat = matrix(f, S, lnames, rise, pairs=not a.legs, z0=a.z0)
    return mat, solved[key].get('label', key), ts, run_sum


def _safe(layer):
    return re.sub(r'[^A-Za-z0-9]+', '_', layer).strip('_')


def plot(res, L, path):
    """A layer's figure: its NEXT and FEXT matrices, and its stretches drawn
    from end to end, coloured by each victim's sum."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    nets = L['nets']
    fig = Figure(figsize=(16, 5.2))
    FigureCanvasAgg(fig)
    gs = fig.add_gridspec(1, 3, width_ratios=(1, 1, 1.5))
    for k, (what, title) in enumerate((('next_pct', 'NEXT'),
                                       ('fext_pct', 'FEXT'))):
        ax = fig.add_subplot(gs[0, k])
        M = np.asarray(L['matrix'][what])
        im = ax.imshow(M, cmap='viridis', vmin=0)
        ax.set_xticks(range(len(nets)), nets, rotation=90, fontsize=7)
        ax.set_yticks(range(len(nets)), nets, fontsize=7)
        ax.set_xlabel('aggressor')
        ax.set_ylabel('victim')
        ax.set_title(f"{L['layer']}: {title}, % of the swing "
                     f"({res['rise_ps']:g} ps edge)", fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046)
    ax = fig.add_subplot(gs[0, 2])
    sums = {v['victim']: max(v['next_sum_pct'], v['fext_sum_pct'])
            for v in L['victims']}
    top = max(sums.values()) or 1.0
    of = {n: ch for ch, legs in L.get('legs', {}).items() for n in legs}
    from matplotlib import cm
    for net, e in L['ends'].items():
        ch = of.get(net, net)
        c = cm.viridis(sums.get(ch, 0) / top)
        for line in L.get('tracks', {}).get(net) or [
                [[e['near']['x'], e['near']['y']],
                 [e['far']['x'], e['far']['y']]]]:
            xy = np.asarray(line, float)
            ax.plot(xy[:, 0], -xy[:, 1], '-', color=c, lw=2)
        ax.plot([e['near']['x']], [-e['near']['y']], 'o', color='k', ms=3)
        ax.annotate(f" {net} {sums.get(ch, 0):.1f} %",
                    (e['far']['x'], -e['far']['y']), fontsize=6,
                    va='center')
    ax.set_aspect('equal')
    ax.margins(x=0.15)
    ax.set_title(f"{L['layer']}: each victim's sum (the worst of NEXT, FEXT); "
                 "near ends dotted", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.core.coupling: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


# ---- the question, as the study has it (kicad2ems.core.analyses) ------------
from .analyses import Analysis                                      # noqa: E402


def _result(folder):
    try:
        with open(os.path.join(folder or '', RESULT), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if d.get('format') == FORMAT else None
    except (OSError, ValueError):
        return None


class Coupling(Analysis):
    """A board's coupling, a layer at a time (this module)."""
    name, label, tool = 'coupling', 'Board coupling', 'kicad2ems.core.coupling'
    about = ("many nets' crosstalk (a bus by name patterns), each signal layer "
             "solved with its planes: each victim's worst aggressors")
    order = 16
    result = RESULT
    provides = ('coupling',)
    engine = 'mom25d'
    # who answers: mom25d (the layer's whole field), or the line model (its
    # coupled sections; seconds): the setting `solvers`
    variants = (('', 'mom25d', dict(solvers='mom25d')),
                ('lines', 'line model', dict(solvers='lines')))

    def check(self):
        return self.check_with(dict(solvers='mom25d'))

    def check_with(self, sets):
        from .solvers import LINES, get, resolve_lines
        name = sets.get('solvers') or 'mom25d'
        try:
            # (the line model: whichever a run takes; get('lines') had raised,
            # and the line-model variant was never offered)
            return get(resolve_lines() if name in (LINES, 'line', 'fast')
                       else name).check()
        except ValueError as e:
            return False, str(e)

    def options(self):
        from .options import Option, split
        o = lambda key, kind, default, help, group='Nets', **kw: Option(
            key, default, help, dict(list=list, float=float,
                                     int=int).get(kind, str),
            group=group, conv=split if kind == 'list' else None, **kw)
        return [
            o('nets', 'list', [],
              "the nets, by name patterns (DQ*, DQS?_[PN]; one a line, or "
              "comma-separated)", level='basic', pick='nets'),
            o('layers', 'str', '',
              "the copper layers solved, comma-separated (empty: every layer "
              "the nets run on)", level='basic'),
            o('near', 'str', '',
              "the parts at the near (driven) ends, comma-separated (empty: "
              "the ends nearest each layer's first net's first end)",
              pick='parts'),
            Option('legs', False,
                   "a differential pair's legs as two single-ended nets "
                   "(default: one channel, in its differential mode)", bool,
                   group='Nets'),
            o('min_length', 'float', MIN_LENGTH,
              "a stretch on a layer shorter than this is left out (a "
              "breakout), mm"),
            o('mode', 'str', 'both',
              "what the headline and the limit are about: both (the larger), "
              "next or fext", 'Edge', choices=('both', 'next', 'fext'),
              level='basic'),
            o('rise', 'float', 100.0, "the aggressors' edge, ps (10 to 90 %)",
              'Edge', level='basic'),
            o('limit', 'float', '',
              "the most a victim may take from all its aggressors, % of the "
              "swing (empty: none)", 'Edge', level='basic'),
            o('top', 'int', TOP, "each victim's worst aggressors listed",
              'Edge'),
            o('z0', 'float', 50.0, "each net's termination at both ends, ohm",
              'Edge'),
            o('fmax', 'float', '',
              "the highest frequency solved, GHz (empty: 1 / rise)", 'Solve'),
            o('sigma', 'float', '',
              "copper conductivity, S/m (empty: lossless)", 'Solve'),
        ]

    @staticmethod
    def _nets(values):
        v = values.get('nets') or []
        v = (v.replace('\n', ',').split(',') if isinstance(v, str) else
             [x for item in v for x in str(item).split(',')])
        return [x.strip() for x in v if x.strip()]

    def tag(self, values):
        nets = self._nets(values)
        return ('_'.join(re.sub(r'[^A-Za-z0-9]+', '', n)[-10:]
                         for n in nets[:3]) or 'run')

    def command(self, values, board, out, preview=False, results=None):
        nets = self._nets(values)
        if not board:
            raise ValueError("choose the board")
        if not nets:
            raise ValueError("name the nets: patterns such as DQ*")
        cmd = [sys.executable, '-m', 'kicad2ems.core.coupling', str(board),
               '--nets', ','.join(nets), '--out', str(out)]
        for key in ('layers', 'near', 'mode', 'rise', 'limit', 'top', 'z0',
                    'fmax', 'sigma', 'min_length'):
            v = values.get(key)
            if v not in (None, '', []):
                cmd += ['--' + key.replace('_', '-'), str(v)]
        if values.get('legs'):
            cmd.append('--legs')
        cmd += ['--solvers', str(values.get('solvers') or 'mom25d')]
        return cmd

    def status(self, line):
        line = line.strip()
        return (line[:160] if re.match(r'(coupling:|\S+\.Cu: )', line)
                else None)

    def report(self, folder):
        d = _result(folder)
        if d is None:
            return None
        w = d.get('worst')
        head = (f"worst victim {w['victim']} on {w['layer']}: "
                f"{w['sum_pct']:.2f} % of the swing from all its aggressors "
                f"({d['mode']}), the worst from {', '.join(w['aggressors'])}; "
                f"worst pair {w['pair']['aggressor']} -> "
                f"{w['pair']['victim']} {w['pair_pct']:.2f} %, for a "
                f"{d['rise_ps']:g} ps edge" if w else "no coupling solved")
        if w and d.get('limit_pct') is not None:
            head += (f"; limit {d['limit_pct']:g} %: "
                     f"{'met' if w['ok'] else 'NOT met'}")
        sec = []
        for L in d['layers']:
            sec.append(dict(
                title=f"{L['layer']}: each victim ({L['label']})",
                text=f"Each net's stretch on {L['layer']} terminated in "
                f"{d['z0']:g} ohm at both ends, driven at its near end in "
                "turn; the sums are every aggressor's peak at once (the "
                "worst case). A differential pair is one channel, in its "
                "differential mode as it drives and as its receiver sees it "
                "(% of the aggressor's swing, as voltage).",
                table=dict(
                    columns=['victim', 'worst aggressors', 'NEXT, all (%)',
                             'FEXT, all (%)', 'length (mm)'],
                    rows=[[v['victim'],
                           ', '.join(f"{x['aggressor']} "
                                     f"{max(x['next_pct'], x['fext_pct']):.2f}"
                                     for x in v['worst']),
                           v['next_sum_pct'], v['fext_sum_pct'],
                           L['ends'][L.get('legs', {}).get(
                               v['victim'], [v['victim']])[0]]['length_mm']]
                          for v in L['victims']],
                    formats=[None, None, '.2f', '.2f', '.1f'])))
            if L.get('conversion'):
                sec.append(dict(
                    title=f"{L['layer']}: the pairs' mode conversion",
                    text="A pair driven differentially at its near end, seen "
                    "in common mode at its near and far ends: what its "
                    "asymmetry (a leg longer, a neighbour nearer one leg) "
                    "turns into common mode.",
                    table=dict(columns=['pair', 'near (%)', 'far (%)',
                                        'near, peak (dB)', 'far, peak (dB)'],
                               rows=[[k, c['near_pct'], c['far_pct'],
                                      c['near_db'], c['far_db']]
                                     for k, c in L['conversion'].items()],
                               formats=[None, '.2f', '.2f', '.1f', '.1f'])))
            sec.append(dict(
                title=f"{L['layer']}: the worst pairs",
                table=dict(columns=['aggressor', 'victim', 'NEXT (%)',
                                    'FEXT (%)'],
                           rows=[[p['aggressor'], p['victim'], p['next_pct'],
                                  p['fext_pct']] for p in L['pairs'][:10]],
                           formats=[None, None, '.2f', '.2f'])))
        if d.get('left_out'):
            sec.append(dict(
                title="Left out",
                table=dict(columns=['net', 'layer', 'length (mm)', 'why'],
                           rows=[[r['net'], r['layer'], r['length_mm'],
                                  r['why']] for r in d['left_out']],
                           formats=[None, None, '.1f', None])))
        images = [os.path.join(folder, f"coupling_{_safe(L['layer'])}.png")
                  for L in d['layers']]
        return dict(title=f"Board coupling: {d['patterns']}", summary=head,
                    sections=sec,
                    notes=[str(x) for x in d.get('not_modelled', [])]
                    + [str(x) for L in d['layers']
                       for x in L.get('warnings', [])],
                    images=[p for p in images if os.path.isfile(p)],
                    footer=f"kicad2ems.core.coupling, "
                    f"{os.path.basename(d['board'])}")

    def figures(self, folder):
        d = _result(folder)
        if d is None:
            return []
        many = len(d['layers']) > 1
        return [dict(name=f"{what.upper()} {v['victim']}"
                     + (f" ({L['layer']})" if many else ''),
                     value=v[f"{what}_sum_pct"], unit='%')
                for L in d['layers'] for v in L['victims']
                for what in ('next', 'fext')]
