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

build(board) returns a LineModel: nodes and elements (the netlist that fastsolve.py turns into
S-parameters) and, per port, the path to the other ports as an ordered list of sections for the
report. It reads the board's primitives (board.Board.prims: tracks, pads, zones, vias). Units: mm, Ohm;
model coordinates (y up).
"""
import heapq, math
import numpy as np
from shapely.geometry import LineString, Point
from ...constants import C0

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
        self.region = kw.get('region')              # a strip's own copper (strips.py): its width along it
        self.label = kw.get('label', '')
        self.net = kw.get('net', '')
        self.part = kw.get('part')                  # parts.Part for 'part'
        self.xs, self.z0, self.eeff, self.er, self.h, self.s, self.t = 'no reference', None, None, None, None, None, 0.0
        self.z0_sheet = self.eeff_sheet = None      # zero-thickness copper (openEMS sheets)
        self.tand, self.L = 0.0, None               # dielectric loss, via inductance (H) when no antipad
        self.offset = 0.0                           # centre of a widened trace's copper off its centre line (mm)
        self.group = None                           # index into LineModel.groups for a coupled line
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
        self.groups = []            # coupled sections: dict(members=[(Element, near node, far node)], gaps, length, ...)
        self.junctions = []         # discontinuities at nodes: dict(node, kind, arms=[(Element, end)], widths, ...)

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
        groups = [dict(lines=len(g['members']), length=g['length'], layer=g['layer'],
                       widths=[e.w for e, _, _ in g['members']], gaps=list(g['gaps']), h=g['h'], er=g['er'],
                       labels=[e.label for e, _, _ in g['members']],
                       pairs=[dict(p) for p in g['pairs']]) for g in self.groups]
        junctions = [dict(kind=j['kind'], node=j['node'], xs=j['xs'], layer=j['layer'], widths=list(j['widths']),
                          at=[float(j['xy'][0]), float(-j['xy'][1])], angle=j.get('angle'), gap=j.get('gap'))
                     for j in self.junctions]
        return dict(paths=out, notes=list(self.notes), groups=groups, junctions=junctions)


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


class RefCopper:
    """The copper that can be a reference (planes, coplanar ground) for the line sections: every modelled
    net except the signal nets themselves (so a trace never references itself, a via pad of its own net
    or the other leg of its differential pair), per layer, in model coordinates; a `ground` layer is the
    whole outline."""
    def __init__(self, model, signal_nets):
        from shapely.ops import unary_union
        from shapely.prepared import prep
        sel = model.sel
        outline = model.outline
        by_layer = {}
        for l, n, g in model.prims.items:
            if l in model.cu_z and n not in signal_nets and (sel is None or n in sel):
                by_layer.setdefault(l, []).append(g)
        self.geom, self._prep = {}, {}
        for l, gs in by_layer.items():
            u = unary_union(gs).intersection(outline)
            if not u.is_empty:
                self.geom[l] = u
        if model.ground:
            self.geom[model.ground] = outline
        self._prep = {l: prep(g) for l, g in self.geom.items()}

    def covers(self, layer, pt):
        p = self._prep.get(layer)
        return bool(p is not None and p.covers(Point(*pt)))


def _covers(ref, layer, pt, n, reach):
    """'full' when reference copper on `layer` covers pt and the points `reach` mm either side along n,
    'partial' when only pt, else None."""
    if not ref.covers(layer, pt):
        return None
    return 'full' if ref.covers(layer, pt + n * reach) and ref.covers(layer, pt - n * reach) else 'partial'


def _side_gap(ref, layer, pt, n, w, reach):
    """Distance from the strip edge to the nearest reference copper on the same layer along +n (None:
    beyond reach)."""
    g = ref.geom.get(layer)
    if g is None:
        return None
    ray = LineString([pt + n * (w / 2 + 1e-4), pt + n * (w / 2 + reach)])
    hit = ray.intersection(g)
    if hit.is_empty:
        return None
    return Point(*(pt + n * (w / 2))).distance(hit)


def classify(model, ref, e, pt, d):
    """Set e.xs / z0 / eeff / er / h / s / tand for a trace or pad section on e.layer of width e.w,
    sampled at pt with direction d (unit vectors, model coordinates); ref: the RefCopper."""
    n = np.array([-d[1], d[0]])
    zs, w, t = model.cu_z[e.layer], e.w, model.cu_t.get(e.layer, 0.0)
    e.t = t
    found = {}
    for side in ('below', 'above'):
        cand = sorted((l for l in model.cu_order if (model.cu_z[l] < zs if side == 'below' else model.cu_z[l] > zs)),
                      key=lambda l: abs(model.cu_z[l] - zs))
        for l in cand:
            h = abs(model.cu_z[l] - zs)
            cov = _covers(ref, l, pt, n, w / 2 + 2 * h)
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
        (_, h1, _), (_, h2, _) = found['below'], found['above']
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
        g1 = _side_gap(ref, e.layer, pt, n, w, reach)
        g2 = _side_gap(ref, e.layer, pt, -n, w, reach)
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


def _references(model, ref, layer, pt):
    """(nearest reference layer below, nearest above) covering point pt, or None each."""
    zs = model.cu_z[layer]
    out = []
    for below in (True, False):
        cand = sorted((l for l in model.cu_order if (model.cu_z[l] < zs if below else model.cu_z[l] > zs)),
                      key=lambda l: abs(model.cu_z[l] - zs))
        out.append(next((l for l in cand if ref.covers(l, pt)), None))
    return tuple(out)


GAP_STEP = 1.5          # a coplanar gap changing by this factor starts a new section (a tapered gap)


def _gap_class(model, ref, layer, refs, pt, d, w, local):
    """Coplanar-gap class at pt: None when there is no ground within 2h on both sides (the reach classify
    uses), else the mean gap on a log scale (GAP_STEP per class)."""
    below, above = refs
    if (below is None) == (above is None):                 # stripline, or no plane: the gap is not used
        return None
    h = abs(model.cu_z[below or above] - model.cu_z[layer])
    n = np.array([-d[1], d[0]])
    g = [_side_gap(local, layer, pt, s * n, w, 2 * h) for s in (1, -1)]
    if any(x is None or x >= 2 * h for x in g):
        return None
    return int(round(math.log(max(0.5 * (g[0] + g[1]), 1e-3)) / math.log(GAP_STEP)))


class _Local:
    """The reference copper of one layer near one trace (fast ray casts)."""
    def __init__(self, ref, layer, region):
        g = ref.geom.get(layer)
        self.geom = {layer: g.intersection(region)} if g is not None else {}


WIDTH_STEP = 1.2        # a copper width changing by this factor starts a new section (a taper)


class NetCopper:
    """The line nets' tracks plus their zones and drawn copper (gr_* / fp_* shapes) per layer, in model
    coordinates: where a shape of the trace's own net overlaps it (a taper, a teardrop, copper drawn in
    a footprint) the conductor is wider than the track. Pads and vias are not included: they are
    sections of their own."""
    def __init__(self, model, nets, tracks, reach=5.0):
        from shapely.ops import unary_union
        by = {}
        for l, n, g in model.prims.shapes:
            if n in nets and l in model.cu_z:
                by.setdefault(l, []).append(g)
        for t in tracks:
            if t['layer'] in by and t['net'] in nets:
                by[t['layer']].append(LineString(t['pts']).buffer(t['width'] / 2, quad_segs=6))
        self.geom = {l: unary_union(gs).intersection(model.outline) for l, gs in by.items()}
        self.reach = reach

    def near(self, layer, region):
        g = self.geom.get(layer)
        return None if g is None else g.intersection(region)

    def width(self, g, pt, n, w):
        """(width, offset of its centre along n) of the copper g across pt; the track's own (w, 0) when
        nothing widens it."""
        if g is None or g.is_empty:
            return w, 0.0
        r = max(self.reach if self is not None else 5.0, 10 * w)
        seg = LineString([pt - n * r, pt + n * r]).intersection(g)
        parts = [seg] if seg.geom_type == 'LineString' else [p for p in getattr(seg, 'geoms', []) if p.geom_type == 'LineString']
        part = next((p for p in parts if p.distance(Point(*pt)) < 1e-6), None)
        if part is None:
            return w, 0.0
        s = [float(np.dot(np.asarray(c) - pt, n)) for c in part.coords]
        lo, hi = min(s), max(s)
        if hi - lo <= w * 1.01:
            return w, 0.0
        return hi - lo, 0.5 * (hi + lo)


def _split_by_reference(model, ref, e, netcu=None, step=0.25, max_samples=400):
    """A trace element as consecutive pieces with the same reference planes, coplanar gap class and
    copper width class (sampled every `step` mm; a one-sample change of the gap alone, such as a via
    pad beside the trace, is ignored). A piece widened by copper shapes of its net gets that width."""
    from shapely.ops import substring
    line = LineString(e.pts)
    L = line.length
    n = int(min(max_samples, max(3, math.ceil(L / step))))
    pos = [(i + 0.5) * L / n for i in range(n)]
    hmax = max(model.cu_z.values()) - min(model.cu_z.values())
    if e.region is not None:                               # a strip: the width of its own copper
        wide = e.region
    else:
        wide = netcu.near(e.layer, line.buffer(max(netcu.reach, 10 * e.w) + 0.1)) if netcu is not None else None
    if wide is not None and wide.is_empty:
        wide = None

    def across(s):
        """(point, direction, copper width, centre offset) at distance s along the trace."""
        pt = np.asarray(line.interpolate(s).coords[0])
        a = np.asarray(line.interpolate(max(s - 0.01, 0.0)).coords[0])
        b = np.asarray(line.interpolate(min(s + 0.01, L)).coords[0])
        d = (b - a) / (np.linalg.norm(b - a) or 1.0)
        w, off = NetCopper.width(netcu, wide, pt, np.array([-d[1], d[0]]), e.w) if wide is not None else (e.w, 0.0)
        return pt, d, w, off

    rmax = max(netcu.reach if netcu is not None else 0.0, 10 * e.w) if wide is not None else e.w
    local = _Local(ref, e.layer, line.buffer(rmax + 2 * hmax + 0.1))
    refs, gaps, widths = [], [], []
    for s in pos:
        pt, d, w, off = across(s)
        r = _references(model, ref, e.layer, pt)
        refs.append(r)
        gaps.append(_gap_class(model, ref, e.layer, r, pt + np.array([-d[1], d[0]]) * off, d, w, local))
        widths.append(int(round(math.log(w / e.w) / math.log(WIDTH_STEP))))
    for i in range(1, n - 1):
        if gaps[i - 1] == gaps[i + 1] != gaps[i] and refs[i - 1] == refs[i] == refs[i + 1]:
            gaps[i] = gaps[i - 1]
    sig = list(zip(refs, gaps, widths))
    cuts = [0.0] + [0.5 * (pos[i] + pos[i + 1]) for i in range(n - 1) if sig[i] != sig[i + 1]] + [L]
    if len(cuts) == 2 and not any(widths):
        return [e]
    out = []
    for k, (s0, s1) in enumerate(zip(cuts[:-1], cuts[1:])):
        a = e.a if k == 0 else f"{e.a}>{e.b}#{k}"
        b = e.b if k == len(cuts) - 2 else f"{e.a}>{e.b}#{k + 1}"
        piece = substring(line, s0, s1)
        _, _, w, off = across(0.5 * (s0 + s1))
        lab = e.label if abs(w - e.w) < 1e-6 else f"{e.label}, widened to {w:.3g} mm"
        el = Element('trace', a, b, s1 - s0, layer=e.layer, w=w, pts=np.asarray(piece.coords), net=e.net, label=lab,
                     region=e.region)
        el.offset = off
        out.append(el)
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


def _footprint_pads(fp, nets, model, inside):
    """(node, pad, ref) for the footprint's pads on the line nets, in model coordinates. Pads sharing a
    number, net and layers that touch are one pad (a connector's signal pad drawn as several shapes):
    their union, centred on its centroid."""
    from shapely.ops import unary_union
    groups = {}
    for q in fp['pads']:
        if q['net'] in nets and any(l in model.cu_z for l in q['layers']):
            g = q['geom']
            if g.intersects(inside):
                groups.setdefault((q['name'], q['net'], tuple(q['layers'])), []).append(dict(q, geom=g))
    out = []
    for (name, _, _), qs in groups.items():
        u = unary_union([q['geom'] for q in qs]) if len(qs) > 1 else qs[0]['geom']
        parts = list(getattr(u, 'geoms', [u])) if len(qs) > 1 else [u]
        for k, g in enumerate(parts):
            if len(qs) == 1:
                q = qs[0]
            else:
                c = g.centroid
                q = dict(qs[0], geom=g, xy=np.array([c.x, c.y]))
            out.append((f"pad:{fp['ref']}.{name}" + (f"#{k + 1}" if len(parts) > 1 else ""), q, fp['ref']))
    return out


def build(model):
    """The line model of the nets the ports sit on (and the nets reached through series parts)."""
    pr = model.prims
    lm = LineModel()
    inside = model.outline.buffer(1e-6)
    tracks = [t for t in pr.tracks if t['layer'] in model.cu_z]
    track_nets = {t['net'] for t in tracks}
    fps = {fp['ref']: fp for fp in pr.footprints}
    # ---- nets: the ports' nets, extended through series parts ----
    nets = {p.net for p in model.ports if p.net}
    for p in model.ports:
        if p.kind == 'gap':
            lm.notes.append(f"{p.name}: gap port - no line model")
    for name, legs in model.user_ports:
        if len(legs) == 2:
            lm.notes.append(f"{name}: differential pair - each leg is a single-ended line; where the legs run "
                            f"parallel they are a coupled section (even / odd impedances in its table)")
    part_pads = {}
    for pt in model.parts:
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
                    nets.add(pn[1 - i]); grew = True
    if not nets:
        lm.notes.append('no port sits on a routed net')
        return lm
    refcu = RefCopper(model, nets)
    # ---- pads and vias the tracks attach to ----
    pads = []                                              # (node, pad dict with model geometry, footprint ref)
    for fp in pr.footprints:
        if fp['ref'] not in model.port_refs:
            pads += _footprint_pads(fp, nets, model, inside)
    vias = [dict(v, xy=np.array([v['x'], v['y']]), i=i) for i, v in enumerate(pr.vias)
            if v['net'] in nets and not v.get('pth') and inside.contains(Point(v['x'], v['y']))]

    def attach(layer, xy):
        for node, q, _ in pads:
            if layer in q['layers'] and q['geom'].distance(Point(*xy)) < 1e-6:
                return node + ':in', q
        for v in vias:
            if layer in v['layers'] and np.hypot(*(xy - v['xy'])) <= max(v['size'], v['drill']) / 2 + 1e-6:
                return f"via{v['i']}:{layer}", None
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
    strip_tracks = _shape_strips(model, nets, inside, lm)  # resonators / feeds drawn as copper shapes
    clipped = _attach_to_strips(clipped, strip_tracks) + strip_tracks
    for t in _split_at_junctions(clipped):
        line = LineString(t['pts'])
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
                                   label=f"{'strip' if t.get('strip') else 'trace'} {t['width']:.3g} mm",
                                   region=t.get('region')))
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
                                           label=e.label, region=e.region)
            lm.elements.append(Element('trace', node, e.b, line.length - s, layer=e.layer, w=e.w, pts=np.array(pb),
                                       net=e.net, label=e.label, region=e.region))
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
            g = refcu.geom.get(l)
            if g is not None and not g.is_empty:
                antipad.append(g.distance(Point(*v['xy'])))
        grounded = [l for l in lys if l not in att and refcu.covers(l, v['xy'])]
        if grounded:                                       # it lands in a plane: a via to ground
            for la in att:
                lg = min(grounded, key=lambda l: abs(model.cu_z[l] - model.cu_z[la]))
                h = abs(model.cu_z[lg] - model.cu_z[la])
                e = Element('via', f"via{v['i']}:{la}", 'gnd', h, layer=f"{la}-{lg}", w=v['drill'],
                            label=f"via {la} - {lg} (to ground)", net=v['net'], pts=np.array([v['xy'], v['xy']]))
                zz = sorted((model.cu_z[la], model.cu_z[lg]))
                e.er, e.tand = _dielectric(model, *zz)
                e.eeff, e.h = e.er, h
                e.xs, e.L = 'via to ground', via_inductance(h, v['drill'] / 2)
                e.notes.append(f"to the {lg} plane: {e.L * 1e9:.3f} nH")
                lm.elements.append(e)
            continue
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
    netcu = NetCopper(model, nets, clipped)
    split = []
    for e in lm.elements:
        split += _split_by_reference(model, refcu, e, netcu) if e.kind == 'trace' and e.length > 0 else [e]
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
            classify(model, refcu, e, mid + np.array([-d[1], d[0]]) * e.offset, d)
        elif e.kind == 'pad':
            d = e.pts[1] - e.pts[0]
            classify(model, refcu, e, 0.5 * (e.pts[0] + e.pts[1]), d / (np.linalg.norm(d) or 1.0))
    _couple(lm, model)
    _junctions(lm, model)
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
                                f"{dist[far]:.2f} mm from it (copper drawn as shapes or zones is not followed: it only widens the tracks it overlaps)")
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
                for j, _ in adj.get(u, []):
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


def _shape_strips(model, nets, inside, lm):
    """Strips (strips.py) of the rectilinear copper shapes of the line nets, per layer and net."""
    from shapely.ops import unary_union
    from .strips import shape_strips, rectilinear
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
                continue
            out += shape_strips(poly, l, n)
    if out:
        lm.notes.append(f"{len(out)} strip(s) from copper drawn as shapes (rectilinear shapes on the line nets)")
    return out


def _attach_to_strips(tracks, strips_, tol=1e-6):
    """Tracks that end inside a strip's copper: one entering along the strip stops at the copper edge and
    joins the strip's end; one entering from the side runs on to the strip's centreline (a T-junction)."""
    if not strips_:
        return tracks
    from shapely.ops import unary_union
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
                seg_dir = _direction_at(c, c.project(q))
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


def _direction_at(line, s):
    a = np.asarray(line.interpolate(max(s - 1e-3, 0.0)).coords[0])
    b = np.asarray(line.interpolate(min(s + 1e-3, line.length)).coords[0])
    d = b - a
    return d / (np.linalg.norm(d) or 1.0)


# ---- coupled lines -------------------------------------------------------------------------------
COUPLE_H = 5.0          # parallel microstrip lines couple when their edge gap is at most this many h
COUPLED_XS = ('microstrip',)
_COPY = ('xs', 'z0', 'eeff', 'z0_sheet', 'eeff_sheet', 'er', 'h', 's', 't', 'tand', 'offset')


def _straight(e):
    p = np.asarray(e.pts)
    if p is None or len(p) < 2:
        return False
    d = p[-1] - p[0]
    L = np.hypot(*d)
    if L < 1e-9:
        return False
    n = np.array([-d[1], d[0]]) / L
    return bool(np.all(np.abs((p - p[0]) @ n) < 1e-4))


def _piece(e, a, b, p0, p1, tag):
    el = Element(e.kind, a, b, float(np.hypot(*(np.asarray(p1) - p0))), layer=e.layer, w=e.w,
                 pts=np.array([p0, p1], float), net=e.net, label=e.label, region=e.region)
    for k in _COPY:
        setattr(el, k, getattr(e, k))
    el.notes = list(e.notes)
    return el


def _split_at(e, ts, u):
    """Element e (straight) cut where its coordinate along u passes the values ts: pieces in order a -> b."""
    p0, p1 = np.asarray(e.pts[0], float), np.asarray(e.pts[-1], float)
    ta, tb = float(p0 @ u), float(p1 @ u)
    lo, hi = sorted((ta, tb))
    cuts = sorted(t for t in ts if lo + 1e-4 < t < hi - 1e-4)
    if not cuts:
        return [e]
    if ta > tb:
        cuts = cuts[::-1]
    pts = [p0] + [p0 + (p1 - p0) * (t - ta) / (tb - ta) for t in cuts] + [p1]
    nodes = [e.a] + [f"{e.a}>{e.b}~{t:.4f}" for t in cuts] + [e.b]
    return [_piece(e, nodes[k], nodes[k + 1], pts[k], pts[k + 1], k) for k in range(len(pts) - 1)]


def _couple(lm, model):
    """Parallel straight microstrip sections on one layer within COUPLE_H * h of each other: cut where
    their overlaps start and end, and group the pieces side by side into coupled sections."""
    from .coupled import coupled_microstrip
    cand = [e for e in lm.elements if e.kind == 'trace' and e.xs in COUPLED_XS and e.length > 1e-3 and _straight(e)]
    if len(cand) < 2:
        return
    info = {}
    for e in cand:
        d = np.asarray(e.pts[-1], float) - np.asarray(e.pts[0], float)
        ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
        info[id(e)] = ang
    # interacting pairs (union-find over the candidates)
    parent = {id(e): id(e) for e in cand}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def geom(e, u, n):
        p = np.asarray(e.pts, float)
        t = sorted((float(p[0] @ u), float(p[-1] @ u)))
        return t[0], t[1], float(p[0] @ n)
    pairs = []
    for i, e in enumerate(cand):
        for f in cand[i + 1:]:
            if e.layer != f.layer:
                continue
            da = abs(info[id(e)] - info[id(f)])
            if min(da, 180 - da) > 1.0:
                continue
            ang = math.radians(info[id(e)])
            u, n = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
            a0, a1, ca = geom(e, u, n)
            b0, b1, cb = geom(f, u, n)
            gap = abs(ca - cb) - 0.5 * (e.w + f.w)
            h = e.h if isinstance(e.h, float) else None
            if h is None or gap <= 1e-4 or gap > COUPLE_H * h:
                continue
            if min(a1, b1) - max(a0, b0) < max(0.2, gap):
                continue
            pairs.append((e, f))
            parent[find(id(e))] = find(id(f))
    if not pairs:
        return
    sets = {}
    for e in cand:
        if any(e is x or e is y for x, y in pairs):
            sets.setdefault(find(id(e)), []).append(e)
    for members in sets.values():
        ang = math.radians(info[id(members[0])])
        u, n = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
        cuts = sorted({round(t, 4) for e in members for t in geom(e, u, n)[:2]})
        pieces = {}
        for e in members:
            k = lm.elements.index(e)
            parts = _split_at(e, cuts, u)
            lm.elements[k:k + 1] = parts
            for pc in parts:
                t0, t1, c = geom(pc, u, n)
                pieces.setdefault((round(t0, 4), round(t1, 4)), []).append((c, pc))
        for (t0, t1), row in sorted(pieces.items()):
            if t1 - t0 < 1e-3 or len(row) < 2:
                continue
            row.sort(key=lambda r: r[0])
            chain = [row[0]]
            for c, pc in row[1:] + [(None, None)]:
                prev = chain[-1][1]
                gap = None if pc is None else (c - chain[-1][0]) - 0.5 * (prev.w + pc.w)
                if pc is not None and 1e-4 < gap <= COUPLE_H * prev.h:
                    chain.append((c, pc))
                    continue
                if len(chain) >= 2:
                    _add_group(lm, chain, u, t0, coupled_microstrip)
                chain = [(c, pc)] if pc is not None else []


def _add_group(lm, chain, u, t0, coupled_microstrip):
    gid = len(lm.groups)
    members, gaps = [], []
    for k, (c, e) in enumerate(chain):
        near_first = float(np.asarray(e.pts[0]) @ u) <= float(np.asarray(e.pts[-1]) @ u)
        near, far = (e.a, e.b) if near_first else (e.b, e.a)
        members.append((e, near, far))
        e.group = gid
        e.label = f"{e.label} (coupled {k + 1}/{len(chain)})"
        if k:
            gaps.append((c - chain[k - 1][0]) - 0.5 * (e.w + chain[k - 1][1].w))
    e0 = chain[0][1]
    g = dict(members=members, gaps=gaps, length=e0.length, h=e0.h, er=e0.er, tand=e0.tand, layer=e0.layer, pairs=[])
    for k, gap in enumerate(gaps):
        wa, wb = members[k][0].w, members[k + 1][0].w
        ze, zo, ee, eo = coupled_microstrip(0.5 * (wa + wb), gap, e0.h, e0.er)
        g['pairs'].append(dict(gap=gap, ze=ze, zo=zo, eeff_e=ee, eeff_o=eo))
        cp = Element('coupling', members[k][1], members[k + 1][1], 0.0, layer=e0.layer, w=gap, net=e0.net,
                     label=f"coupled to the next line, gap {gap:.3g} mm",
                     pts=np.array([np.asarray(members[k][0].pts).mean(0), np.asarray(members[k + 1][0].pts).mean(0)]))
        cp.xs, cp.group = 'coupled', gid
        cp.notes.append(f"Ze {ze:.1f} / Zo {zo:.1f} ohm over {e0.length:.2f} mm")
        lm.elements.append(cp)
    lm.groups.append(g)


# ---- discontinuities -----------------------------------------------------------------------------
def _out_dir(e, end):
    p = np.asarray(e.pts, float)
    d = (p[1] - p[0]) if end == 0 else (p[-2] - p[-1])
    return d / (np.linalg.norm(d) or 1.0)


def _junctions(lm, model):
    """Discontinuities where trace and pad sections meet: open end, corner (90 degrees), bend (other
    angles: no model), step (width change in line), tee, cross; facing open ends close together are a
    gap. Nodes with a port, a via or a part are left out."""
    arms, busy = {}, set(lm.ports.values())
    for e in lm.elements:
        if e.kind in ('trace', 'pad') and e.pts is not None and len(e.pts) >= 2 and e.length > 1e-6:
            arms.setdefault(e.a, []).append((e, 0))
            arms.setdefault(e.b, []).append((e, 1))
        elif e.kind in ('via', 'part'):
            busy |= {e.a, e.b}
    opens = []
    for node, ar in arms.items():
        if node in busy or node == 'gnd':
            continue
        ends = [np.asarray(e.pts[0] if k == 0 else e.pts[-1], float) for e, k in ar]
        xy = np.mean(ends, axis=0)
        dirs = [_out_dir(e, k) for e, k in ar]
        ws = [e.w for e, _ in ar]
        xs = {e.xs for e, _ in ar}
        rec = dict(node=node, arms=ar, widths=ws, xy=xy, xs=xs.pop() if len(xs) == 1 else 'mixed', layer=ar[0][0].layer)
        n = len(ar)
        if n == 1:
            rec['kind'] = 'open'
            opens.append(rec)
        elif n == 2:
            cosang = float(np.clip(np.dot(dirs[0], dirs[1]), -1, 1))
            bend = 180.0 - math.degrees(math.acos(cosang))
            rec['angle'] = bend
            if abs(bend - 90) <= 10:
                rec['kind'] = 'corner'
            elif bend <= 10:
                if max(ws) / min(ws) <= 1.05:
                    continue
                rec['kind'] = 'step'
            else:
                rec['kind'] = 'bend'
        elif n == 3:
            best = max(((i, j) for i in range(3) for j in range(i + 1, 3)), key=lambda ij: -np.dot(dirs[ij[0]], dirs[ij[1]]))
            k3 = ({0, 1, 2} - set(best)).pop()
            rec['arms'] = [ar[best[0]], ar[best[1]], ar[k3]]
            rec['widths'] = [ar[best[0]][0].w, ar[best[1]][0].w, ar[k3][0].w]
            rec['kind'] = 'tee'
        elif n == 4:
            order = sorted(range(4), key=lambda i: math.atan2(dirs[i][1], dirs[i][0]))
            rec['arms'] = [ar[i] for i in order]
            rec['widths'] = [ar[i][0].w for i in order]
            rec['kind'] = 'cross'
        else:
            rec['kind'] = 'junction'
        lm.junctions.append(rec)
    # facing open ends close together: an end gap
    used = set()
    for i, a in enumerate(opens):
        for b in opens[i + 1:]:
            if id(a) in used or id(b) in used or a['layer'] != b['layer']:
                continue
            ea, ka = a['arms'][0]
            eb, kb = b['arms'][0]
            da, db = -_out_dir(ea, ka), -_out_dir(eb, kb)       # pointing out of the line, past its end
            v = b['xy'] - a['xy']
            g = float(np.linalg.norm(v))
            h = ea.h if isinstance(ea.h, float) else 0.0
            if g < 1e-6 or g > 2 * h or np.dot(da, db) > -0.98 or np.dot(v / g, da) < 0.98:
                continue
            a.update(kind='gap', gap=g, other=b)
            used |= {id(a), id(b)}
    lm.junctions = [r for r in lm.junctions if id(r) not in used or r['kind'] == 'gap']
