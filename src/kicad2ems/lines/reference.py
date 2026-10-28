"""
The reference copper of a line: which planes lie above and below a trace and what copper runs beside it
(RefCopper, NetCopper: ray casts against the planes, cut into TILE mm squares), the cross-section that
gives (classify: microstrip, stripline, offset stripline, grounded CPW, embedded microstrip), and a trace
cut into sections where its reference, gap or width changes (_split_by_reference).
"""
import math
import numpy as np
from shapely.geometry import LineString, Point
from .formulas import cpwg, embedded_microstrip, microstrip, offset_stripline, stripline
from .elements import Element


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

    def __getstate__(self):                                 # pickled for worker processes: no prepared geometry
        return {'geom': self.geom}

    def __setstate__(self, st):
        from shapely.prepared import prep
        self.geom = st['geom']
        self._prep = {l: prep(g) for l, g in self.geom.items()}

    def _tiled(self, layer):
        """The layer's reference copper cut into TILE mm squares (clip_by_rect, built once per layer) and an
        index of them: a ray tests the few pieces it crosses, not a whole plane with all its holes."""
        cache = self.__dict__.setdefault('_tiles', {})
        if layer not in cache:
            import shapely
            g = self.geom[layer]
            x0, y0, x1, y1 = g.bounds
            pieces = []
            for x in np.arange(x0, x1, TILE):
                for y in np.arange(y0, y1, TILE):
                    p = shapely.clip_by_rect(g, x, y, x + TILE, y + TILE)
                    if not p.is_empty:
                        pieces.append(p)
            cache[layer] = (shapely.STRtree(pieces), pieces)
        return cache[layer]

    def ray_gap(self, layer, origin, ray):
        """Distance from `origin` to the nearest reference copper of `layer` on the segment `ray`, or None; the
        same as ray.intersection(whole layer) (the nearest hit lies in the piece that holds it)."""
        if layer not in self.geom:
            return None
        tree, pieces = self._tiled(layer)
        best = None
        for i in tree.query(ray):
            hit = ray.intersection(pieces[i])
            if not hit.is_empty:
                d = origin.distance(hit)
                best = d if best is None else min(best, d)
        return best

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
    if TILE and isinstance(ref, RefCopper):
        return ref.ray_gap(layer, Point(*(pt + n * (w / 2))), ray)
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
    if not w or w <= 0:                                      # a degenerate pad chord: no cross-section
        e.notes.append('no width here: no impedance')
        return
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
TILE = 4.0              # mm: reference copper cut into squares of this size for ray casts (None: whole planes)
ADAPTIVE = None         # mm between samples away from any change (None: every `step`, 0.25 mm). Tried at 1-4 mm:
                        # the same map, but no faster (1.0-1.1x on KiCad's CM5 and video demos, 0.9x on the Jetson
                        # one): finding where copper corners lie along a trace cost what the samples saved


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
    def __init__(self, model, nets, tracks, reach=5.0, strips_=()):
        from shapely.ops import unary_union
        by = {}
        made = {}                                   # copper that became strips: a section of its own, not a widening
        for s in strips_:
            made.setdefault(s['layer'], []).append(s['region'])
        made = {l: unary_union(gs).buffer(1e-6) for l, gs in made.items()}
        for l, n, g in model.prims.shapes:
            if n in nets and l in model.cu_z:
                if l in made:
                    g = g.difference(made[l])
                if not g.is_empty:
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
    local = ref if TILE else _Local(ref, e.layer, line.buffer(rmax + 2 * hmax + 0.1))

    def sample(s):
        pt, d, w, off = across(s)
        r = _references(model, ref, e.layer, pt)
        return r, _gap_class(model, ref, e.layer, r, pt + np.array([-d[1], d[0]]) * off, d, w, local), \
            int(round(math.log(w / e.w) / math.log(WIDTH_STEP)))

    if ADAPTIVE and TILE and isinstance(ref, RefCopper) and L > 2 * ADAPTIVE:
        pos, got = _adaptive(model, ref, e, line, L, step, wide, rmax, sample, max_samples)
        n = len(pos)
        refs, gaps, widths = (list(x) for x in zip(*got))
    else:
        refs, gaps, widths = [], [], []
        for s in pos:
            r, g, wc = sample(s)
            refs.append(r)
            gaps.append(g)
            widths.append(wc)
    for i in range(1, n - 1):
        near = pos[i + 1] - pos[i - 1] <= 2.02 * L / max(1, min(max_samples, math.ceil(L / step)))
        if near and gaps[i - 1] == gaps[i + 1] != gaps[i] and refs[i - 1] == refs[i] == refs[i + 1]:
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
                     region=e.region, radial=e.radial)
        el.offset = off
        out.append(el)
    return out


SAMPLES = [0]           # sample() calls of the adaptive sampling (for measuring it)


def _adaptive(model, ref, e, line, L, step, wide, rmax, sample, max_samples):
    """Sample positions along a trace (and what sample() gives at each), dense only where something can change:
    within `step` of every place where reference copper of another layer crosses the trace, and of every corner of
    the same-layer reference copper or of copper widening it within reach; every ADAPTIVE mm elsewhere; then
    halving the space between any two neighbours that differ, down to `step`, so that the cuts fall where they
    did with every sample.

    The reach of the coplanar ground is 2 h (h: the height to the reference plane, as _gap_class has it): the
    coarse samples give the largest h; where they find no plane, every sample is taken."""
    import shapely
    n_full = int(min(max_samples, max(3, math.ceil(L / step))))
    fine = [(i + 0.5) * L / n_full for i in range(n_full)]
    got = {}

    def at(i):
        if i not in got:
            got[i] = sample(fine[i])
            SAMPLES[0] += 1
        return got[i]
    coarse = max(1, int(round(ADAPTIVE / (L / n_full))))
    keep = set(range(0, n_full, coarse)) | {n_full - 1}
    zs, h = model.cu_z[e.layer], 0.0
    for i in sorted(keep):
        below, above = at(i)[0]
        if below is None and above is None:
            h = None                                       # no plane: nothing to bound the reach
            break
        if (below is None) != (above is None):
            h = max(h, abs(model.cu_z[below or above] - zs))
    if h is None:
        keep = set(range(n_full))
    else:
        marks = []

        def project(geom):
            """Where the copper's corners (and crossings) lie along the trace: a gap or a reference can change
            only there (between them a straight edge keeps it, or changes it steadily: the halving finds that)."""
            pts = shapely.get_coordinates(geom)
            if len(pts):
                marks.append(shapely.line_locate_point(line, shapely.points(pts)))
        near = line.buffer(e.w / 2 + 2 * h + 0.05)
        for l in ref.geom:
            tree, pieces = ref._tiled(l)
            if l == e.layer:                               # coplanar ground within reach of the strip
                if h > 0:
                    for k in tree.query(near):
                        g = pieces[k].intersection(near)
                        if not g.is_empty:
                            project(g.boundary if g.geom_type in ('Polygon', 'MultiPolygon') else g)
            else:                                          # planes above / below: where an edge crosses the trace
                for k in tree.query(line):
                    hit = line.intersection(pieces[k].boundary)
                    if not hit.is_empty:
                        project(hit)
        if wide is not None:                               # copper of its own net widening it
            w_near = wide.intersection(line.buffer(rmax + 0.1))
            if not w_near.is_empty:
                project(w_near.boundary)
        if marks:
            fp = np.asarray(fine)
            for sv in np.unique(np.round(np.concatenate(marks) / step)) * step:
                lo, hi = np.searchsorted(fp, [sv - step, sv + step])
                keep.update(range(int(lo), int(hi)))
    idx = sorted(keep)
    for i in idx:
        at(i)
    k = 0
    while k < len(idx) - 1:                                # halve where neighbours differ
        a, b = idx[k], idx[k + 1]
        if b - a > 1 and got[a] != got[b]:
            m = (a + b) // 2
            at(m)
            idx.insert(k + 1, m)
            continue
        k += 1
    return [fine[i] for i in idx], [got[i] for i in idx]
