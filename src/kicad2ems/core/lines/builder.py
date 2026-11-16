"""
The board to the line model: build(board) breaks every net a port sits on into uniform pieces (trace
runs, component pads, via barrels, lumped parts and copper shapes as strips), classifies each against its
reference copper (reference.py) and joins them at nodes; topology.py then finds the coupled groups, the
discontinuities and the ports' paths.
"""
import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import substring, unary_union
from shapely.strtree import STRtree
from . import reference
from .formulas import coax, via_inductance
from .elements import Element, LineModel, direction_at
from .reference import NetCopper, RefCopper, _dielectric, _split_by_reference, classify
from .topology import _couple, _junctions, _paths


def _chord(poly, c, d, reach=50.0):
    """Length of the line through c along d inside poly (the piece containing c)."""
    seg = LineString([c - d * reach, c + d * reach]).intersection(poly)
    parts = [seg] if seg.geom_type == 'LineString' else [g for g in getattr(seg, 'geoms', []) if g.geom_type == 'LineString']
    parts = [p for p in parts if p.distance(Point(*c)) < 1e-6] or parts
    return max((p.length for p in parts), default=0.0)


def _split_at_junctions(tracks, tol=1e-4):
    """Split tracks where another track of the same layer and net ends on their interior (a T-junction
    KiCad does not break), so that the branch gets a node of its own: each track cut at every such end at once,
    the ends found through a spatial index (it had rescanned every end of the board after each split: 3.1 s of the
    CM5's 11.6 s build). A cut is at the branch's end itself, so their nodes are one."""
    ends = {}
    for t in tracks:
        for q in (t['pts'][0], t['pts'][-1]):
            ends.setdefault((t['layer'], t['net']), []).append((float(q[0]), float(q[1])))
    index = {k: (STRtree([Point(q) for q in v]), v) for k, v in ends.items()}
    out = []
    for t in tracks:
        tree, pts = index[(t['layer'], t['net'])]
        line = LineString(t['pts'])
        cuts = []
        for i in tree.query(line, predicate='dwithin', distance=tol):
            q = pts[int(i)]
            s = line.project(Point(q))
            if tol < s < line.length - tol and all(abs(s - c) > tol for c, _ in cuts):
                cuts.append((s, q))
        if not cuts:
            out.append(t)
            continue
        coords = [(tuple(c), line.project(Point(c))) for c in line.coords]
        cuts.sort()
        edges = [(0.0, None)] + cuts + [(line.length, None)]
        for (s0, q0), (s1, q1) in zip(edges[:-1], edges[1:]):
            inner = [c for c, s in coords[1:-1] if s0 < s < s1]
            piece = [q0 or coords[0][0]] + inner + [q1 or coords[-1][0]]
            out.append(dict(t, pts=np.array(piece)))
    return out


def _key(layer, x, y):
    return f"{layer}:{x:.4f}:{y:.4f}"


def _footprint_pads(fp, nets, model, inside):
    """(node, pad, ref) for the footprint's pads on the line nets, in model coordinates. Pads sharing a
    number, net and layers that touch are one pad (a connector's signal pad drawn as several shapes):
    their union, centred on its centroid."""
    groups = {}
    for q in fp['pads']:
        if q['net'] in nets and any(l in model.cu_z for l in q['layers']) and q['geom'].intersects(inside):
            groups.setdefault((q['name'], q['net'], tuple(q['layers'])), []).append(dict(q))
    out = []
    for (name, _, _), qs in groups.items():
        if len(qs) == 1:
            out.append((f"pad:{fp['ref']}.{name}", qs[0], fp['ref']))
            continue
        u = unary_union([q['geom'] for q in qs])
        parts = list(getattr(u, 'geoms', [u]))
        for k, g in enumerate(parts):
            c = g.centroid
            q = dict(qs[0], geom=g, xy=np.array([c.x, c.y]))
            out.append((f"pad:{fp['ref']}.{name}" + (f"#{k + 1}" if len(parts) > 1 else ""), q, fp['ref']))
    return out


def build(model, nets=None, refcu=None, field=False):
    """The line model of the nets the ports sit on (and the nets reached through series parts); `nets`: these
    nets instead (the KiCad plugin's impedance map of a board before it has ports), refcu: their reference
    copper (RefCopper) when it is built once for several calls (every signal net excluded, not only these); field:
    which sections the field solver solves on the board's cut across them (--lines-field): 'auto' (those whose cut
    holds what the formulas leave out), 'all' or True (every one), 'off' (none: the formulas); False or None: as
    lines.reference.FIELD is, 'auto' unless it was changed."""
    keep = reference.FIELD
    if field or isinstance(field, str):
        reference.FIELD = reference.field_mode(field)
    try:
        return _build(model, nets, refcu)
    finally:
        reference.FIELD = keep


def _build(model, nets, refcu):
    pr = model.prims
    lm = LineModel()
    inside = model.outline.buffer(1e-6)
    tracks = [t for t in pr.tracks if t['layer'] in model.cu_z]
    track_nets = {t['net'] for t in tracks}
    fps = {fp['ref']: fp for fp in pr.footprints}
    # ---- nets: the ports' nets, extended through series parts ----
    nets = set(nets) if nets is not None else {p.net for p in model.ports if p.net}
    port_parts = dict(getattr(model, 'part_of', None) or {})     # parts made ports (R-E5): a floating port each
    for p in model.ports:
        if p.kind == 'gap' and not (p.internal and p.name in port_parts):
            lm.notes.append(f"{p.name}: gap port - no line model")
    for name, legs in model.user_ports:
        if len(legs) == 2:
            lm.notes.append(f"{name}: differential pair - each leg is a single-ended line; where the legs run "
                            f"parallel they are a coupled section (even / odd impedances in its table)")
    part_pads = {}
    all_parts = list(model.parts) + list(port_parts.values())
    for pt in all_parts:
        pads = [q for q in fps.get(pt.ref, {}).get('pads', []) if q['layers']]
        if len(pads) == 2:
            part_pads[pt.ref] = pads
    grounds = model.ground_nets
    grew = True
    while grew:
        grew = False
        for r_, pads in part_pads.items():
            pn = [q['net'] for q in pads]
            for i in (0, 1):
                if pn[i] in nets and pn[1 - i] not in nets and pn[1 - i] in track_nets and pn[1 - i] not in grounds:
                    nets.add(pn[1 - i])
                    grew = True
    if not nets:
        lm.notes.append('no port sits on a routed net')
        return lm
    refcu = refcu or RefCopper(model, nets)
    # ---- pads and vias the tracks attach to ----
    pads = []                                              # (node, pad dict with model geometry, footprint ref)
    for fp in pr.footprints:
        if fp['ref'] not in model.port_refs:
            pads += _footprint_pads(fp, nets, model, inside)
    # the lines' own vias, and the ground's: a trace that ends on a ground via is shorted there (a stub's short, as
    # dfsynth draws it); a ground via no trace ends on stays out (the barrel loop below takes attached ones only)
    vias = [dict(v, xy=np.array([v['x'], v['y']]), i=i) for i, v in enumerate(pr.vias)
            if (v['net'] in nets or v['net'] in grounds) and not v.get('pth') and inside.contains(Point(v['x'], v['y']))]
    pad_tree = STRtree([q['geom'] for _, q, _ in pads]) if pads else None
    via_xy = np.array([v['xy'] for v in vias], float).reshape(-1, 2)
    via_r = np.array([max(v['size'], v['drill']) / 2 for v in vias], float)
    ground_via = np.array([v['net'] not in nets for v in vias], bool)
    parallel = {}                                          # a ground via's node -> the vias in parallel there

    def attach(layer, xy, w=0.0, toward=None, net=None):
        """The pad (first in order) or via a trace end at xy lies on, through spatial indexes (a scan of every pad
        and via per trace end took a second on a board of 4000 ends). A ground via counts where its pad reaches the
        end: across the trace within its width w, along it (toward: the trace's next point) within the pad's
        radius, so a row of vias across a stub's end shorts it, the row in parallel, and a tap beside them does not."""
        pt = Point(*xy)
        if pad_tree is not None:
            for i in sorted(pad_tree.query(pt, predicate='dwithin', distance=1e-6)):
                node, q, _ = pads[i]
                if layer in q['layers'] and q['geom'].distance(pt) < 1e-6:
                    return node + ':in', q
        if len(vias):
            r = via_xy - np.asarray(xy, float)
            d = np.hypot(*r.T)
            near = d <= via_r + 1e-6
            u = None if toward is None else np.asarray(toward, float) - xy
            if u is not None and w > 0 and np.hypot(*u) > 1e-9:
                u = u / np.hypot(*u)
                along, across = r @ u, np.abs(r[:, 0] * u[1] - r[:, 1] * u[0])
                near |= ground_via & (np.abs(along) <= via_r + 1e-6) & (across <= w / 2 + via_r + 1e-6)
            hit = [i for i in np.nonzero(near)[0] if layer in vias[i]['layers'] and
                   (not ground_via[i] or net is None or covers(layer, net, Point(*via_xy[i])))]
            own = [i for i in hit if not ground_via[i]]
            if own:
                return f"via{vias[own[0]]['i']}:{layer}", None
            if hit:
                node = f"via{vias[hit[0]]['i']}:{layer}"
                parallel[node] = len(hit)
                return node, None
        return _key(layer, *xy), None

    # ---- traces (split at T-junctions, shortened by the pads they end in) ----
    clipped = []
    for t in tracks:                                       # clipped to the outline: a --region crop cuts them
        if t['net'] not in nets:
            continue
        g = LineString(t['pts']).intersection(inside)
        for piece in getattr(g, 'geoms', [g]):
            if piece.geom_type == 'LineString' and piece.length > 1e-6:
                clipped.append(dict(t, pts=np.asarray(piece.coords)))
    strip_tracks = _shape_strips(model, nets, inside, lm, clipped)   # resonators, feeds, stubs drawn as shapes
    clipped = _attach_to_strips(clipped, strip_tracks) + strip_tracks
    from shapely.prepared import prep
    unions = {}

    def covers(layer, net, pt):
        """Whether the net's own copper on that layer (tracks, pads, zones, shapes as imported) holds the point; each
        layer and net's union made once, when first asked."""
        if (layer, net) not in unions:
            g = unary_union([geom for l, n, geom in pr.items if l == layer and n == net])
            unions[(layer, net)] = prep(g) if not g.is_empty else None
        return unions[(layer, net)] is not None and unions[(layer, net)].covers(pt)
    clipped = _split_at_vias(clipped, via_xy[ground_via], via_r[ground_via], covers)
    for t in _split_at_junctions(clipped):
        line = LineString(t['pts'])
        a, qa = attach(t['layer'], t['pts'][0], t['width'], _toward(t['pts']), t['net'])
        b, qb = attach(t['layer'], t['pts'][-1], t['width'], _toward(t['pts'][::-1]), t['net'])
        geom = line
        for q in (qa, qb):
            if q is not None:
                geom = geom.difference(q['geom'])
        if geom.is_empty or geom.length < 1e-9:
            geom, L = line, 0.0
        else:
            L = geom.length
        pts = np.asarray(geom.coords) if geom.geom_type == 'LineString' else t['pts']
        lm.elements.append(Element('trace', a, b, L, layer=t['layer'], w=t['width'], pts=pts, net=t['net'],
                                   label=f"{'strip' if t.get('strip') else 'trace'} {t['width']:.3g} mm",
                                   region=t.get('region'), radial=t.get('radial')))
    # ---- ports: split the trace they sit on, or end the pad section they sit in ----
    port_on_pad = {}                                       # pad node -> port position
    for p in model.ports:
        if p.kind != 'z' or not p.net:
            continue
        best = None
        for i, e in enumerate(lm.elements):
            if e.kind == 'trace' and e.layer == p.layer and e.net == p.net:
                dist = LineString(e.pts).distance(Point(*p.c))
                if dist <= e.w / 2 + 1e-6 and (best is None or dist < best[0]):
                    best = (dist, i)
        node = f"port:{p.name}"
        if best is None:
            q = next((q for n_, q, _ in pads if p.layer in q['layers'] and q['geom'].distance(Point(*p.c)) < 1e-6), None)
            if q is None:
                lm.notes.append(f"{p.name}: no trace or pad under the port")
                continue
            pn = next(n_ for n_, qq, _ in pads if qq is q)
            port_on_pad[pn] = np.asarray(p.c, float)
            node = pn + ':core'                                # the pad section runs from the trace to the port
        else:
            e = lm.elements[best[1]]
            line = LineString(e.pts)
            s = line.project(Point(*p.c))
            pa = [tuple(c) for c in line.coords if line.project(Point(c)) < s] + [tuple(line.interpolate(s).coords[0])]
            pb = [tuple(line.interpolate(s).coords[0])] + [tuple(c) for c in line.coords if line.project(Point(c)) > s]
            lm.elements[best[1]] = Element('trace', e.a, node, s, layer=e.layer, w=e.w, pts=np.array(pa), net=e.net,
                                           label=e.label, region=e.region, radial=e.radial)
            lm.elements.append(Element('trace', node, e.b, line.length - s, layer=e.layer, w=e.w, pts=np.array(pb),
                                       net=e.net, label=e.label, region=e.region, radial=e.radial))
        lm.ports[p.name] = node
        lm.port_z0[p.name] = p.z0
    # ---- pads: a section from where the traces attach to the core (where a part connects) ----
    used = {e.a for e in lm.elements} | {e.b for e in lm.elements} | set(lm.ports.values())
    part_of = {}
    for ref, pp in part_pads.items():
        for q in pp:
            part_of[f"pad:{ref}.{q['name']}"] = (ref, pp)
    for node, q, ref in pads:
        if node + ':in' not in used and node not in part_of:
            continue
        lay = next((l for l in model.cu_order[::-1] if l in q['layers']), q['layers'][0])
        tr = next((e for e in lm.elements if e.kind == 'trace' and node + ':in' in (e.a, e.b)), None)
        if node in port_on_pad and node not in part_of and tr is not None:
            entry = np.asarray(tr.pts[-1] if tr.b == node + ':in' else tr.pts[0], float)   # where the trace meets it
            pc = port_on_pad[node]
            L = float(np.hypot(*(pc - entry)))
            d = (pc - entry) / L if L > 1e-9 else np.array([1.0, 0.0])
            w = _chord(q['geom'], 0.5 * (entry + pc), np.array([-d[1], d[0]]))
            lm.elements.append(Element('pad', node + ':in', node + ':core', L, layer=lay, w=w, geom=q['geom'],
                                       label=f"pad {node[4:]} (to the port)", net=q['net'], pts=np.array([entry, pc])))
            continue
        if node in port_on_pad:                            # a part's pad: the port sits where the traces attach
            lm.ports.update({k: node + ':in' for k, v in lm.ports.items() if v == node + ':core'})
        c = q['xy']
        if node in part_of:
            other = next(o for o in part_of[node][1] if o['name'] != q['name'])
            d = np.asarray(other['xy'], float) - c
        else:
            d = (np.asarray(tr.pts[0 if tr.a != node + ':in' else -1]) - c) if tr is not None else np.array([1.0, 0.0])
        d = d / (np.linalg.norm(d) or 1.0)
        L = _chord(q['geom'], c, d)
        w = _chord(q['geom'], c, np.array([-d[1], d[0]]))
        full = node in part_of
        lm.elements.append(Element('pad', node + ':in', node + ':core', L if full else L / 2, layer=lay, w=w,
                                   geom=q['geom'], label=f"pad {node[4:]}", net=q['net'],
                                   pts=np.array([c - d * L / 2, c + d * L / 2])))
    # ---- parts: series between two pad cores, or shunt to ground; a part port in the part's place ----
    for pt in all_parts:
        pp = part_pads.get(pt.ref)
        if not pp:
            continue
        ends = [f"pad:{pt.ref}.{q['name']}:core" if q['net'] in nets else 'gnd' for q in pp]
        if ends == ['gnd', 'gnd']:
            continue
        if ends[0] == 'gnd':
            ends = ends[::-1]
        a_, b_ = pt.axis, 1 - pt.axis
        c0, c1 = np.zeros(2), np.zeros(2)                       # the part's centre line, end to end
        c0[a_], c1[a_] = pt.s[a_], pt.e[a_]
        c0[b_] = c1[b_] = 0.5 * (pt.s[b_] + pt.e[b_])
        e = Element('part', ends[0], ends[1], pt.gap, layer=pt.layer, part=pt, label=pt.ref, pts=np.array([c0, c1]))
        e.xs = 'lumped'
        if pt.ref in port_parts and port_parts[pt.ref] is pt:      # its port across the gap: the two nodes
            e.port = pt.ref
            lm.ports[pt.ref] = (ends[0], ends[1])
            lm.port_z0[pt.ref] = next((p.z0 for p in model.ports if p.name == pt.ref), 50.0)
        lm.elements.append(e)
    # ---- via barrels: between the layers tracks attach to, open stubs beyond ----
    ends = {n for e in lm.elements for n in (e.a, e.b)}

    def to_ground(v, la, lg, what, n=1):
        """A via (n in parallel) from layer la to ground on layer lg: its inductance."""
        h = abs(model.cu_z[lg] - model.cu_z[la])
        e = Element('via', f"via{v['i']}:{la}", 'gnd', h, layer=f"{la}-{lg}", w=v['drill'],
                    label=f"via {la} - {lg} ({what})", net=v['net'], pts=np.array([v['xy'], v['xy']]))
        e.er, e.tand = _dielectric(model, *sorted((model.cu_z[la], model.cu_z[lg])), e)
        e.eeff, e.h = e.er, h
        e.xs, e.L = 'via to ground', via_inductance(h, v['drill'] / 2) / n
        lm.elements.append(e)
        return e
    for v in vias:
        zl = [model.cu_z[l] for l in v['layers'] if l in model.cu_z]
        if len(zl) < 2:
            continue
        lys = [l for l in model.cu_order if min(zl) <= model.cu_z[l] <= max(zl)]   # every layer it passes
        att = [l for l in lys if f"via{v['i']}:{l}" in ends]
        if not att:
            continue
        if v['net'] in grounds and v['net'] not in nets:            # a ground via a trace ends on: to its plane,
            planes = [l for l in lys if l not in att and refcu.covers(l, v['xy'])]   # else to its far end
            far = max(lys, key=lambda l: min(abs(model.cu_z[l] - model.cu_z[a]) for a in att))
            for la in att:
                lg = min(planes or [far], key=lambda l: abs(model.cu_z[l] - model.cu_z[la]))
                if model.cu_z[lg] == model.cu_z[la]:
                    continue
                n = parallel.get(f"via{v['i']}:{la}", 1)
                e = to_ground(v, la, lg, v['net'], n)
                e.notes.append(f"{v['net']} via{'s' if n > 1 else ''} to "
                               f"{'the ' + lg + ' plane' if planes else 'its ' + lg + ' end (no plane)'}"
                               f"{f', {n} in parallel (their mutual inductance left out)' if n > 1 else ''}: "
                               f"{e.L * 1e9:.3f} nH")
            continue
        z = [model.cu_z[l] for l in lys]
        stack = Element('via', '', '', 0.0)                  # (holds the layers for the chain's pieces below)
        er, tand = _dielectric(model, min(z), max(z), stack)
        antipad = [refcu.geom[l].distance(Point(*v['xy'])) for l in lys
                   if l not in att and l in refcu.geom and not refcu.geom[l].is_empty]
        grounded = [l for l in lys if l not in att and refcu.covers(l, v['xy'])]
        if grounded:                                       # it lands in a plane: a via to ground
            for la in att:
                lg = min(grounded, key=lambda l: abs(model.cu_z[l] - model.cu_z[la]))
                e = to_ground(v, la, lg, 'to ground')
                e.notes.append(f"to the {lg} plane: {e.L * 1e9:.3f} nH")
            continue
        chain = [l for l in lys if l in att or l == lys[0] or l == lys[-1]]
        D = 2 * float(np.median(antipad)) if antipad else 0.0         # the antipads': a coax barrel when wider
        for l0, l1 in zip(chain[:-1], chain[1:]):
            h = model.cu_z[l1] - model.cu_z[l0]
            e = Element('via', f"via{v['i']}:{l0}", f"via{v['i']}:{l1}", h, layer=f"{l0}-{l1}", w=v['drill'],
                        label=f"via {l0} - {l1}" + ('' if (l0 in att and l1 in att) else ' (stub)'), net=v['net'],
                        pts=np.array([v['xy'], v['xy']]))
            e.er, e.tand, e.eeff, e.diel = er, tand, er, stack.diel
            if D > v['drill']:
                e.xs, e.z0, e.h = 'coax', coax(v['drill'], D, er), D
                e.z0_sheet, e.eeff_sheet = e.z0, er
            if e.z0 is None:
                e.xs, e.L = 'lumped', via_inductance(h, v['drill'] / 2)
                e.notes.append(f"no plane around the barrel: {e.L * 1e9:.3f} nH")
            lm.elements.append(e)
    # ---- cross-sections: traces split where their reference planes change (antipads, plane edges) ----
    netcu = NetCopper(model, nets, clipped, strips_=strip_tracks)
    split = []
    for e in lm.elements:
        split += _split_by_reference(model, refcu, e, netcu) if e.kind == 'trace' and e.length > 0 else [e]
    lm.elements = split
    keep, reference.DEFER = reference.DEFER, []                 # the field solver's sections: together, below
    try:
        for e in lm.elements:                                   # each trace and pad section's cross-section, at its
            if e.kind == 'trace':                               # middle
                pts = np.asarray(e.pts)
                line = LineString(pts) if len(pts) > 1 else None
                if line is None or line.length < 1e-9:
                    mid, d = pts[0], np.array([1.0, 0.0])
                else:
                    mid = np.asarray(line.interpolate(0.5, normalized=True).coords[0])
                    d = direction_at(line, line.length / 2)
                at = getattr(e, 'classify_at', None)           # over its via's antipad: the line's cross-section
                classify(model, refcu, e, (mid if at is None else at) + np.array([-d[1], d[0]]) * e.offset, d)
            elif e.kind == 'pad':
                d = e.pts[1] - e.pts[0]
                classify(model, refcu, e, 0.5 * (e.pts[0] + e.pts[1]), d / (np.linalg.norm(d) or 1.0))
        jobs = reference.DEFER
    finally:
        reference.DEFER = keep
    reference.solve_deferred(jobs)
    _couple(lm, model, refcu)
    _junctions(lm, model)
    _paths(lm)
    return lm


def _toward(pts):
    """The first point of a polyline apart from its first (its direction there; a strip's ends repeat points)."""
    p0 = np.asarray(pts[0], float)
    return next((q for q in pts[1:] if np.hypot(*(np.asarray(q, float) - p0)) > 1e-9), None)


def _split_at_vias(tracks, xy, r, covers=None):
    """Tracks cut where a ground via inside their copper meets the centreline: the trace is shorted there (a row
    of vias across a stub, a pad's width in from its end, as dfsynth draws a short), and attach() finds the vias
    at the cut. A via the copper does not reach leaves a track whole; covers(layer, net, point), when given, is the
    net's copper as imported, so a strip taken from a shape does not reach a via in that shape's hole (the CM5's LX
    pour round a ground via had shorted it)."""
    if not len(xy):
        return tracks
    pts = [Point(*p) for p in xy]
    tree = STRtree(pts)
    out = []
    for t in tracks:
        line = LineString(t['pts'])
        cuts = []
        for i in tree.query(line.buffer(t['width'] / 2 + float(np.max(r)))):
            if line.distance(pts[i]) <= t['width'] / 2 + 1e-9 and (covers is None or covers(t['layer'], t['net'], pts[i])):
                s = line.project(pts[i])
                if 1e-6 < s < line.length - 1e-6 and all(abs(s - c) > 1e-6 for c in cuts):
                    cuts.append(s)
        if not cuts:
            out.append(t)
            continue
        edges = [0.0, *sorted(cuts), line.length]
        for s0, s1 in zip(edges[:-1], edges[1:]):
            out.append(dict(t, pts=np.asarray(substring(line, s0, s1).coords)))
    return out


def _shape_strips(model, nets, inside, lm, tracks=()):
    """Strips (strips.py) of the rectilinear copper shapes of the line nets, per layer and net, and of the
    other shapes that sit across a track as a stub (strips.fan_strip: a radial stub)."""
    from .strips import shape_strips, rectilinear, fan_strip
    by = {}
    for l, n, g in model.prims.shapes:
        if n in nets and l in model.cu_z:
            by.setdefault((l, n), []).append(g)
    out = []
    for (l, n), gs in by.items():
        u = unary_union(gs).intersection(inside)
        for poly in getattr(u, 'geoms', [u]):
            if poly.geom_type != 'Polygon' or poly.area < 1e-6:
                continue
            if not rectilinear(poly):
                out += fan_strip(poly, l, n, tracks)
                continue
            out += shape_strips(poly, l, n)
    if out:
        lm.notes.append(f"{len(out)} strip(s) from copper drawn as shapes (rectilinear shapes on the line nets)")
    return out


def _split_at_strips(tracks, strips_):
    """A track that runs through a strip (a stub drawn as a rectangle over the line) is cut where it crosses
    the strip's centreline, so each half ends inside the strip and joins it as a T-junction. Left whole, the
    shape only widened the track: the stub notch's rectangle stubs became an 8 ohm piece of the line, the
    notch at 3.5 GHz instead of 2.4 GHz."""
    out = []
    for t in tracks:
        pieces = [np.asarray(t['pts'], float)]
        for s in strips_:
            if s['layer'] != t['layer'] or s['net'] != t['net']:
                continue
            nxt = []
            for p in pieces:
                line, region = LineString(p), s['region'].buffer(1e-6)
                ends_in = region.contains(Point(*p[0])) or region.contains(Point(*p[-1]))
                x = line.intersection(LineString(s['pts']))
                if ends_in or x.is_empty or x.geom_type != 'Point':
                    nxt.append(p)
                    continue
                d = line.project(x)
                a = [tuple(c) for c in substring(line, 0, d).coords]
                b = [tuple(c) for c in substring(line, d, line.length).coords]
                nxt += [np.asarray(q, float) for q in (a, b) if len(q) > 1 and LineString(q).length > 1e-6]
            pieces = nxt
        out += [dict(t, pts=p) for p in pieces]
    return out


def _attach_to_strips(tracks, strips_):
    """Tracks that end inside a strip's copper: one entering along the strip stops at the copper edge and
    joins the strip's end; one entering from the side runs on to the strip's centreline (a T-junction).
    Tracks running through a strip are cut there first (_split_at_strips)."""
    if not strips_:
        return tracks
    tracks = _split_at_strips(tracks, strips_)
    out = []
    for t in tracks:
        line = LineString(t['pts'])
        mine = [s for s in strips_ if s['layer'] == t['layer'] and s['net'] == t['net']]
        hit = [s for s in mine if s['region'].buffer(1e-6).contains(Point(*t['pts'][0])) or
               s['region'].buffer(1e-6).contains(Point(*t['pts'][-1]))]
        if not hit:
            out.append(t)
            continue
        cu = unary_union([s['region'] for s in mine])
        rest = line.difference(cu.buffer(1e-6))
        pieces = [g for g in getattr(rest, 'geoms', [rest]) if g.geom_type == 'LineString' and g.length > 1e-6]
        for piece in pieces:
            pts = [tuple(c) for c in piece.coords]
            for k in (0, -1):
                q = Point(*pts[k])
                s = min(mine, key=lambda s_: s_['region'].distance(q))
                if s['region'].distance(q) > 1e-4:
                    continue                               # this end is not at a strip
                c = LineString(s['pts'])
                d_t = np.subtract(pts[-1 if k == 0 else 0], pts[k])
                d_t = d_t / (np.linalg.norm(d_t) or 1.0)
                proj = c.interpolate(c.project(q))
                seg_dir = direction_at(c, c.project(q))
                ends = [np.asarray(s['pts'][0]), np.asarray(s['pts'][-1])]
                near = min(ends, key=lambda e: np.hypot(*(e - (q.x, q.y))))
                if abs(np.dot(d_t, seg_dir)) > 0.9 and np.hypot(*(near - (q.x, q.y))) <= s['width'] + 1e-4:
                    new = tuple(near)                      # along the strip: onto its end
                else:
                    new = (proj.x, proj.y)                 # from the side: onto its centreline
                if k == 0:
                    pts = [new] + pts
                else:
                    pts = pts + [new]
            out.append(dict(t, pts=np.asarray(pts, float)))
    return out
