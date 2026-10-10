"""
Transmission-line sections: a ballpark line model of the ports' nets, in the spirit of Simbeor's
decomposition. Every net a port sits on is broken into uniform pieces - trace runs, component pads,
via barrels and lumped parts - and each piece gets its cross-section (microstrip, stripline, offset
stripline, grounded coplanar waveguide, embedded microstrip) and quasi-static Z0, effective
permittivity and delay from closed-form formulas:

  microstrip        Hammerstad & Jensen (1980), with their strip-thickness correction
  embedded          microstrip covered by dielectric: the cover correction of Bahl / Wadell
  stripline         Wheeler (1978), thickness included; offset stripline: the parallel combination
                    of the two symmetric striplines (IPC-2141 style)
  grounded CPW      conformal mapping (Ghione & Naldi), zero thickness
  via barrel        coaxial line from the drill to the antipad in the planes it passes

Pads count at their own width: a trace that ends in a pad is shortened by the part inside it, and the
pad is a section of its own (the discontinuity that KiCad's track lengths hide). Parts between two pads
continue the path (series) or end in a shunt to ground.

build(model) returns a LineModel: nodes and elements (the netlist that fastsolve.py turns into
S-parameters) and, per port, the path to the other ports as an ordered list of sections for the
report. Units: mm, Ohm; model coordinates (y up).
"""
import heapq, math
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from .constants import C0, EPS0
from .geometry import flip_y

ETA0 = 376.730313668
MU0 = 4e-7 * math.pi


# ---- closed-form line impedances -------------------------------------------------------------------
def ellipk(k):
    """Complete elliptic integral of the first kind K(k), modulus k in [0, 1) (arithmetic-geometric mean)."""
    a, b = 1.0, math.sqrt(max(1.0 - k * k, 0.0))
    for _ in range(60):
        if abs(a - b) <= 1e-15 * a:
            break
        a, b = 0.5 * (a + b), math.sqrt(a * b)
    return math.pi / (2 * a)


def _hj_z01(u):
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _hj_eeff(u, er):
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def microstrip(w, h, er, t=0.0):
    """(Z0, eeff) of a microstrip line, Hammerstad & Jensen, quasi-static, strip thickness t."""
    u = w / h
    du1 = dur = 0.0
    if t > 0:
        th = t / h
        du1 = th / math.pi * math.log(1 + 4 * math.e / (th / math.tanh(math.sqrt(6.517 * u)) ** 2))
        dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(max(er - 1, 0.0)))) * du1
    u1, ur = u + du1, u + dur
    ee = _hj_eeff(ur, er)
    return _hj_z01(ur) / math.sqrt(ee), ee * (_hj_z01(u1) / _hj_z01(ur)) ** 2


def embedded_microstrip(w, h, er, t, cover, er_cover):
    """Microstrip under a dielectric cover of thickness `cover` (mm): Bahl's correction of eeff."""
    z0, ee = microstrip(w, h, er, t)
    x = math.exp(-2 * cover / h)
    ee2 = ee * x + er_cover * (1 - x)
    return z0 * math.sqrt(ee / ee2), ee2


def stripline(w, b, er, t=0.0):
    """Z0 of a centred stripline (planes b apart, strip thickness t), Wheeler (1978); eeff = er."""
    x = t / b
    wb = w / (b - t)
    if x > 0:
        n = 2 / (1 + 2 * x / (3 * (1 - x)))
        dw = x / (math.pi * (1 - x)) * (1 - 0.5 * math.log((x / (2 - x)) ** 2 + (0.0796 * x / (w / b + 1.1 * x)) ** n))
        wb += dw
    m = 8 / (math.pi * wb)
    return 30 / math.sqrt(er) * math.log(1 + 4 / (math.pi * wb) * (m + math.sqrt(m * m + 6.27))), er


def offset_stripline(w, h1, h2, er, t=0.0):
    """Z0 of a stripline h1 above one plane and h2 below the other: 2 Z1 Z2 / (Z1 + Z2) with Zi the
    centred stripline of spacing 2 hi + t."""
    z1, _ = stripline(w, 2 * h1 + t, er, t)
    z2, _ = stripline(w, 2 * h2 + t, er, t)
    return 2 * z1 * z2 / (z1 + z2), er


def cpwg(w, s, h, er):
    """(Z0, eeff) of a grounded (conductor-backed) coplanar waveguide: strip w, gaps s, substrate h."""
    k = w / (w + 2 * s)
    k3 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * s) / (4 * h))
    kp, k3p = math.sqrt(1 - k * k), math.sqrt(1 - k3 * k3)
    q = ellipk(kp) * ellipk(k3) / (ellipk(k) * ellipk(k3p))
    ee = (1 + er * q) / (1 + q)
    return 60 * math.pi / math.sqrt(ee) / (ellipk(k) / ellipk(kp) + ellipk(k3) / ellipk(k3p)), ee


def coax(d, D, er):
    """Z0 of a coaxial line (inner diameter d, outer D)."""
    return ETA0 / (2 * math.pi * math.sqrt(er)) * math.log(D / d)


def via_inductance(h, r):
    """Partial self-inductance (H) of a via barrel of length h and radius r (mm), Goldfarb & Pucel."""
    h, r = h * 1e-3, r * 1e-3
    s = math.hypot(r, h)
    return MU0 / (2 * math.pi) * (h * math.log((h + s) / r) + 1.5 * (r - s))


# ---- the line model ---------------------------------------------------------------------------------
class Element:
    """One piece of the line model between nodes a and b ('gnd' for a shunt part).
    kind: 'trace' | 'pad' | 'via' | 'part'. xs: the cross-section ('microstrip', 'embedded microstrip',
    'stripline', 'offset stripline', 'cpwg', 'coax', 'lumped', 'no reference'). Lengths mm."""
    def __init__(self, kind, a, b, length, **kw):
        self.kind, self.a, self.b, self.length = kind, a, b, float(length)
        self.layer = kw.get('layer', '')
        self.w = kw.get('w')
        self.pts = kw.get('pts')                    # model coordinates, for drawing
        self.geom = kw.get('geom')                  # pad polygon (model coordinates)
        self.label = kw.get('label', '')
        self.net = kw.get('net', '')
        self.part = kw.get('part')                  # parts.Part for 'part'
        self.xs, self.z0, self.eeff, self.er, self.h, self.s, self.t = 'no reference', None, None, None, None, None, 0.0
        self.z0_sheet = self.eeff_sheet = None      # zero-thickness copper (openEMS sheets)
        self.tand, self.L = 0.0, None               # dielectric loss, via inductance (H) when no antipad
        self.notes = []

    @property
    def delay_ps(self):
        if self.kind == 'part' or self.eeff is None:
            return None
        return self.length * 1e-3 * math.sqrt(self.eeff) / C0 * 1e12

    def row(self):
        return dict(kind=self.kind, label=self.label, layer=self.layer, xs=self.xs, w=self.w, length=self.length,
                    z0=self.z0, eeff=self.eeff, z0_sheet=self.z0_sheet, eeff_sheet=self.eeff_sheet, er=self.er, h=self.h, s=self.s, delay_ps=self.delay_ps,
                    notes=list(self.notes), value=self.part.value_str() if self.part else None)


class LineModel:
    """nodes: set of node names; elements: [Element]; ports {port name: node}; port_z0 {name: ohm};
    paths [(port, other port or None, [(Element, start mm, reversed)], branches)]; notes."""
    def __init__(self):
        self.elements, self.ports, self.port_z0, self.paths, self.notes = [], {}, {}, [], []

    def adjacency(self):
        adj = {}
        for i, e in enumerate(self.elements):
            adj.setdefault(e.a, []).append((i, e.b))
            if e.b != 'gnd':
                adj.setdefault(e.b, []).append((i, e.a))
        return adj

    def summary(self):
        """JSON-friendly: every path as a list of section rows with their start positions."""
        out = []
        for p, q, secs, branches in self.paths:
            rows = []
            for e, s0, _ in secs:
                rows.append(dict(e.row(), start=s0))
            out.append(dict(port=p, to=q, z_port=self.port_z0.get(p), sections=rows,
                            branches=[dict(at=a, length=L, label=lab, z0=z) for a, L, lab, z in branches]))
        return dict(paths=out, notes=list(self.notes))


def _dielectric(model, z_lo, z_hi):
    """Thickness-weighted er and the largest tan d of the dielectrics between two heights."""
    tot, acc, tand = 0.0, 0.0, 0.0
    for l in model.layers:
        a, b = max(l['z0'], z_lo), min(l['z1'], z_hi)
        if b > a:
            tot += b - a
            acc += (b - a) * l['er']
            tand = max(tand, l['tand'])
    return (acc / tot if tot > 0 else 1.0), tand


def _covers(model, layer, pt, n, reach):
    """'full' when copper on `layer` covers pt and the points `reach` mm either side along n, 'partial'
    when only pt, else None."""
    polys = model.copper.get(layer, [])
    hit = lambda q: any(p.distance(Point(*q)) < 1e-6 for p in polys)
    if not hit(pt):
        return None
    return 'full' if hit(pt + n * reach) and hit(pt - n * reach) else 'partial'


def _side_gap(model, layer, pt, n, w, own, reach):
    """Distance from the strip edge to the nearest other copper on the same layer along +n (None: beyond reach)."""
    best = None
    ray = LineString([pt + n * (w / 2 + 1e-4), pt + n * (w / 2 + reach)])
    for p in model.copper.get(layer, []):
        if p is own or not p.intersects(ray):
            continue
        d = Point(*(pt + n * (w / 2))).distance(ray.intersection(p))
        best = d if best is None else min(best, d)
    return best


def classify(model, e, pt, d):
    """Set e.xs / z0 / eeff / er / h / s / tand for a trace or pad section on e.layer of width e.w,
    sampled at pt with direction d (unit vectors, model coordinates)."""
    n = np.array([-d[1], d[0]])
    zs, w, t = model.cu_z[e.layer], e.w, model.cu_t.get(e.layer, 0.0)
    e.t = t
    own = model.copper_at(e.layer, Point(*pt))
    found = {}
    for side in ('below', 'above'):
        cand = sorted((l for l in model.cu_order if (model.cu_z[l] < zs if side == 'below' else model.cu_z[l] > zs)),
                      key=lambda l: abs(model.cu_z[l] - zs))
        for l in cand:
            h = abs(model.cu_z[l] - zs)
            cov = _covers(model, l, pt, n, w / 2 + 2 * h)
            if cov:
                found[side] = (l, h, cov)
                break
    if not found:
        e.notes.append('no reference plane above or below')
        return
    for side, (l, h, cov) in found.items():
        if cov == 'partial':
            e.notes.append(f"{l} under it does not extend {w / 2 + 2 * h:.2f} mm either side")
    if len(found) == 2:
        (lb, h1, _), (la, h2, _) = found['below'], found['above']
        er, e.tand = _dielectric(model, zs - h1, zs + h2)
        e.er, e.h = er, (h1, h2)
        if abs(h1 - h2) <= 0.05 * max(h1, h2):
            e.xs, calc = 'stripline', lambda tt: stripline(w, h1 + h2 + tt, er, tt)
        else:
            e.xs, calc = 'offset stripline', lambda tt: offset_stripline(w, h1, h2, er, tt)
    else:
        side, (lp, h, _) = next(iter(found.items()))
        er, e.tand = _dielectric(model, min(zs, model.cu_z[lp]), max(zs, model.cu_z[lp]))
        e.er, e.h = er, h
        # dielectric on the far side of the strip (inner layer, or solder mask when modelled)
        far = (zs, model.thickness) if side == 'below' else (0.0, zs)
        cover = sum(max(0.0, min(l['z1'], far[1]) - max(l['z0'], far[0])) for l in model.layers)
        reach = max(3 * w, 5 * h)
        g1 = _side_gap(model, e.layer, pt, n, w, own, reach)
        g2 = _side_gap(model, e.layer, pt, -n, w, own, reach)
        close = [g for g in (g1, g2) if g is not None and g < 2 * h]
        if len(close) == 2 and cover < 1e-9:
            e.s = 0.5 * (g1 + g2)
            e.xs, calc = 'cpwg', lambda tt: cpwg(w, e.s, h, er)
        else:
            if len(close) == 1:
                e.notes.append(f"ground {min(close):.2f} mm beside it on one side: Z0 somewhat lower")
            if cover > 1e-9:
                er_c, _ = _dielectric(model, *far)
                e.xs, calc = 'embedded microstrip', lambda tt: embedded_microstrip(w, h, er, tt, cover, er_c)
            else:
                e.xs, calc = 'microstrip', lambda tt: microstrip(w, h, er, tt)
    e.z0, e.eeff = calc(t)                                   # the real copper thickness
    e.z0_sheet, e.eeff_sheet = calc(0.0)                     # zero thickness, as openEMS models copper


def _references(model, layer, pt):
    """(nearest copper layer below, nearest above) covering point pt, or None each."""
    zs, P = model.cu_z[layer], Point(*pt)
    out = []
    for below in (True, False):
        cand = sorted((l for l in model.cu_order if (model.cu_z[l] < zs if below else model.cu_z[l] > zs)),
                      key=lambda l: abs(model.cu_z[l] - zs))
        out.append(next((l for l in cand if any(p.distance(P) < 1e-6 for p in model.copper.get(l, []))), None))
    return tuple(out)


def _split_by_reference(model, e, step=0.25, max_samples=400):
    """A trace element as consecutive pieces with the same reference planes (sampled every `step` mm)."""
    from shapely.ops import substring
    line = LineString(e.pts)
    L = line.length
    n = int(min(max_samples, max(3, math.ceil(L / step))))
    pos = [(i + 0.5) * L / n for i in range(n)]
    sig = [_references(model, e.layer, line.interpolate(s).coords[0]) for s in pos]
    cuts = [0.0] + [0.5 * (pos[i] + pos[i + 1]) for i in range(n - 1) if sig[i] != sig[i + 1]] + [L]
    if len(cuts) == 2:
        return [e]
    out = []
    for k, (s0, s1) in enumerate(zip(cuts[:-1], cuts[1:])):
        a = e.a if k == 0 else f"{e.a}>{e.b}#{k}"
        b = e.b if k == len(cuts) - 2 else f"{e.a}>{e.b}#{k + 1}"
        piece = substring(line, s0, s1)
        out.append(Element('trace', a, b, s1 - s0, layer=e.layer, w=e.w, pts=np.asarray(piece.coords), net=e.net,
                           label=e.label))
    return out


def _chord(poly, c, d, reach=50.0):
    """Length of the line through c along d inside poly (the piece containing c)."""
    seg = LineString([c - d * reach, c + d * reach]).intersection(poly)
    parts = [seg] if seg.geom_type == 'LineString' else [g for g in getattr(seg, 'geoms', []) if g.geom_type == 'LineString']
    parts = [p for p in parts if p.distance(Point(*c)) < 1e-6] or parts
    return max((p.length for p in parts), default=0.0)


def _split_at_junctions(tracks, tol=1e-4):
    """Split tracks where another track of the same layer and net ends on their interior (a T-junction
    KiCad does not break), so that the branch gets a node of its own."""
    out = list(tracks)
    changed = True
    while changed:
        changed = False
        ends = [(t['layer'], t['net'], Point(*q)) for t in out for q in (t['pts'][0], t['pts'][-1])]
        for i, t in enumerate(out):
            line = LineString(t['pts'])
            for lay, net, q in ends:
                if lay != t['layer'] or net != t['net'] or line.distance(q) > tol:
                    continue
                s = line.project(q)
                if tol < s < line.length - tol:
                    a = [tuple(c) for c in line.coords if line.project(Point(c)) < s] + [(q.x, q.y)]
                    b = [(q.x, q.y)] + [tuple(c) for c in line.coords if line.project(Point(c)) > s]
                    out[i:i + 1] = [dict(t, pts=np.array(a)), dict(t, pts=np.array(b))]
                    changed = True
                    break
            if changed:
                break
    return out


def _key(layer, x, y):
    return f"{layer}:{x:.4f}:{y:.4f}"


def build(model):
    """The line model of the nets the ports sit on (and the nets reached through series parts)."""
    board = model.board
    lm = LineModel()
    inside = model.outline.buffer(1e-6)
    tracks = [dict(t, pts=np.column_stack([t['pts'][:, 0], -t['pts'][:, 1]])) for t in board.tracks
              if t['layer'] in model.cu_z]
    track_nets = {t['net'] for t in tracks}
    fps = {fp['ref']: fp for fp in board.footprints}
    # ---- nets: the ports' nets, extended through series parts ----
    nets = {p.net for p in model.ports if p.net}
    for p in model.ports:
        if p.kind == 'gap':
            lm.notes.append(f"{p.name}: gap port - no line model")
    part_pads = {}
    for pt in model.parts:
        pads = [q for q in fps.get(pt.ref, {}).get('pads', []) if q['layers']]
        if len(pads) == 2:
            part_pads[pt.ref] = pads
    grew = True
    while grew:
        grew = False
        for ref, pads in part_pads.items():
            pn = [q['net'] for q in pads]
            for i in (0, 1):
                if pn[i] in nets and pn[1 - i] not in nets and pn[1 - i] in track_nets:
                    nets.add(pn[1 - i]); grew = True
    if not nets:
        lm.notes.append('no port sits on a routed net')
        return lm
    # ---- pads and vias the tracks attach to ----
    pads = []                                              # (node, pad dict with model geometry, footprint ref)
    for fp in board.footprints:
        if fp['ref'] in model.port_refs:
            continue
        for q in fp['pads']:
            if q['net'] in nets and any(l in model.cu_z for l in q['layers']):
                g = flip_y(q['geom'])
                if g.intersects(inside):
                    pads.append((f"pad:{fp['ref']}.{q['name']}", dict(q, geom=g, xy=np.array([q['xy'][0], -q['xy'][1]])),
                                 fp['ref']))
    vias = [dict(v, xy=np.array([v['x'], -v['y']]), i=i) for i, v in enumerate(board.vias)
            if v['net'] in nets and not v.get('pth') and inside.contains(Point(v['x'], -v['y']))]
    attached = {}                                          # node -> list of (element index, direction away)

    def attach(layer, xy):
        for node, q, _ in pads:
            if layer in q['layers'] and q['geom'].distance(Point(*xy)) < 1e-6:
                return node + ':in', q
        for v in vias:
            if layer in v['layers'] and np.hypot(*(xy - v['xy'])) <= max(v['size'], v['drill']) / 2 + 1e-6:
                return f"via{v['i']}:{layer}", None
        return _key(layer, *xy), None

    # ---- traces (split at T-junctions, shortened by the pads they end in) ----
    for t in _split_at_junctions([t for t in tracks if t['net'] in nets]):
        line = LineString(t['pts'])
        if not line.intersects(inside):
            continue
        a, qa = attach(t['layer'], t['pts'][0])
        b, qb = attach(t['layer'], t['pts'][-1])
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
                                   label=f"trace {t['width']:.3g} mm"))
    # ---- ports: split the trace they sit on ----
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
            node = next(n_ for n_, qq, _ in pads if qq is q) + ':in'
        else:
            e = lm.elements[best[1]]
            line = LineString(e.pts)
            s = line.project(Point(*p.c))
            pa = [tuple(c) for c in line.coords if line.project(Point(c)) < s] + [tuple(line.interpolate(s).coords[0])]
            pb = [tuple(line.interpolate(s).coords[0])] + [tuple(c) for c in line.coords if line.project(Point(c)) > s]
            lm.elements[best[1]] = Element('trace', e.a, node, s, layer=e.layer, w=e.w, pts=np.array(pa), net=e.net,
                                           label=e.label)
            lm.elements.append(Element('trace', node, e.b, line.length - s, layer=e.layer, w=e.w, pts=np.array(pb),
                                       net=e.net, label=e.label))
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
        c = q['xy']
        if node in part_of:
            other = next(o for o in part_of[node][1] if o['name'] != q['name'])
            d = np.array([other['xy'][0], -other['xy'][1]]) - c
        else:
            tr = next((e for e in lm.elements if e.kind == 'trace' and node + ':in' in (e.a, e.b)), None)
            d = (np.asarray(tr.pts[0 if tr.a != node + ':in' else -1]) - c) if tr is not None else np.array([1.0, 0.0])
        d = d / (np.linalg.norm(d) or 1.0)
        L = _chord(q['geom'], c, d)
        w = _chord(q['geom'], c, np.array([-d[1], d[0]]))
        lay = next((l for l in model.cu_order[::-1] if l in q['layers']), q['layers'][0])
        full = node in part_of
        lm.elements.append(Element('pad', node + ':in', node + ':core', L if full else L / 2, layer=lay, w=w,
                                   geom=q['geom'], label=f"pad {node[4:]}", net=q['net'],
                                   pts=np.array([c - d * L / 2, c + d * L / 2])))
    # ---- parts: series between two pad cores, or shunt to ground ----
    for pt in model.parts:
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
        lm.elements.append(e)
    # ---- via barrels: between the layers tracks attach to, open stubs beyond ----
    for v in vias:
        zl = [model.cu_z[l] for l in v['layers'] if l in model.cu_z]
        if len(zl) < 2:
            continue
        lys = [l for l in model.cu_order if min(zl) <= model.cu_z[l] <= max(zl)]   # every layer it passes
        att = [l for l in lys if any(f"via{v['i']}:{l}" in (e.a, e.b) for e in lm.elements)]
        if not att:
            continue
        z = [model.cu_z[l] for l in lys]
        er, tand = _dielectric(model, min(z), max(z))
        antipad = []
        for l in lys:
            if l in att:
                continue
            others = [p for p in model.copper.get(l, []) if p.distance(Point(*v['xy'])) > 1e-6]
            if others:
                antipad.append(min(p.distance(Point(*v['xy'])) for p in others))
        chain = [l for l in lys if l in att or l == lys[0] or l == lys[-1]]
        for l0, l1 in zip(chain[:-1], chain[1:]):
            h = model.cu_z[l1] - model.cu_z[l0]
            e = Element('via', f"via{v['i']}:{l0}", f"via{v['i']}:{l1}", h, layer=f"{l0}-{l1}", w=v['drill'],
                        label=f"via {l0} - {l1}" + ('' if (l0 in att and l1 in att) else ' (stub)'), net=v['net'],
                        pts=np.array([v['xy'], v['xy']]))
            e.er, e.tand, e.eeff = er, tand, er
            if antipad:
                D = 2 * float(np.median(antipad))
                if D > v['drill']:
                    e.xs, e.z0, e.h = 'coax', coax(v['drill'], D, er), D
                    e.z0_sheet, e.eeff_sheet = e.z0, er
            if e.z0 is None:
                e.xs, e.L = 'lumped', via_inductance(h, v['drill'] / 2)
                e.notes.append(f"no plane around the barrel: {e.L * 1e9:.3f} nH")
            lm.elements.append(e)
    # ---- cross-sections: traces split where their reference planes change (antipads, plane edges) ----
    split = []
    for e in lm.elements:
        split += _split_by_reference(model, e) if e.kind == 'trace' and e.length > 0 else [e]
    lm.elements = split
    for e in lm.elements:
        if e.kind == 'trace':
            pts = np.asarray(e.pts)
            line = LineString(pts) if len(pts) > 1 else None
            if line is None or line.length < 1e-9:
                mid, d = pts[0], np.array([1.0, 0.0])
            else:
                mid = np.asarray(line.interpolate(0.5, normalized=True).coords[0])
                a = np.asarray(line.interpolate(max(0.0, line.length / 2 - 1e-3)).coords[0])
                b = np.asarray(line.interpolate(min(line.length, line.length / 2 + 1e-3)).coords[0])
                d = (b - a) / (np.linalg.norm(b - a) or 1.0)
            classify(model, e, mid, d)
        elif e.kind == 'pad':
            d = e.pts[1] - e.pts[0]
            classify(model, e, 0.5 * (e.pts[0] + e.pts[1]), d / (np.linalg.norm(d) or 1.0))
    _paths(lm)
    return lm


def _shortest(lm, adj, src):
    """Dijkstra by physical length: (distance, previous (element, node)) per node."""
    dist, prev, heap = {src: 0.0}, {}, [(0.0, src)]
    while heap:
        d0, u = heapq.heappop(heap)
        if d0 > dist.get(u, math.inf):
            continue
        for i, v in adj.get(u, []):
            nd = d0 + lm.elements[i].length + 1e-9
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, (i, u)
                heapq.heappush(heap, (nd, v))
    return dist, prev


def _paths(lm):
    adj = lm.adjacency()
    names = list(lm.ports)
    done = set()
    for p in names:
        src = lm.ports[p]
        dist, prev = _shortest(lm, adj, src)
        targets = [(q, lm.ports[q]) for q in names if q != p and lm.ports[q] in dist and (q, p) not in done]
        if not targets and not any(p in pair for pair in done):
            far = max((n for n in dist if n != 'gnd'), key=lambda n: dist[n], default=None)
            targets = [(None, far)] if far is not None and far != src else []
            if targets:
                lm.notes.append(f"{p} reaches no other port through traces, pads and parts: its path ends "
                                f"{dist[far]:.2f} mm from it (copper drawn as shapes or zones is not part of the line model)")
        for q, dst in targets:
            done.add((p, q))
            seq, node = [], dst
            while node != src:
                i, u = prev[node]
                seq.append((i, u, node))
                node = u
            seq.reverse()
            secs, s0, on_path = [], 0.0, {src} | {n for _, _, n in seq}
            for i, u, v in seq:
                e = lm.elements[i]
                secs.append((e, s0, e.a != u))
                s0 += e.length
            branches = []
            path_elems = {i for i, _, _ in seq}
            for k, (i, u, v) in enumerate(seq):
                for j, w_ in adj.get(u, []):
                    if j in path_elems or (k > 0 and j == seq[k - 1][0]):
                        continue
                    L, lab, z = _branch(lm, adj, j, u, on_path)
                    if L < 0.25 and lab.endswith('ends: open)'):
                        continue                            # trace overhang behind a port or past a pad
                    branches.append((secs[k][1], L, lab, z))
            lm.paths.append((p, q, secs, branches))


def _branch(lm, adj, j, start, on_path):
    """Total length, a description and the first Z0 of the branch that leaves the path through element j."""
    e0 = lm.elements[j]
    if e0.kind == 'part' and 'gnd' in (e0.a, e0.b):
        return e0.length, f"{e0.label} {e0.part.value_str()} to ground", None
    seen, stack, total, ends = {j}, [(j, e0.b if e0.a == start else e0.a)], 0.0, []
    while stack:
        i, node = stack.pop()
        e = lm.elements[i]
        total += e.length
        if e.kind == 'part':
            ends.append(f"{e.label} {e.part.value_str()}" + (' to ground' if 'gnd' in (e.a, e.b) else ''))
        nxt = [(k, o) for k, o in adj.get(node, []) if k not in seen and node not in on_path]
        if not nxt and node not in on_path and e.kind != 'part':
            ends.append('open')
        for k, o in nxt:
            seen.add(k)
            stack.append((k, o))
    lab = f"stub ({len(seen)} section{'s' if len(seen) > 1 else ''}, ends: {', '.join(sorted(set(ends))) or 'open'})"
    return total, lab, e0.z0
