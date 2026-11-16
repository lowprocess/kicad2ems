"""
The reference copper of a line: which planes lie above and below a trace and what copper runs beside it
(RefCopper, NetCopper: ray casts against the planes, cut into TILE mm squares), the cross-section that
gives (classify: microstrip, stripline, offset stripline, grounded CPW, embedded microstrip), and a trace
cut into sections where its reference, gap or width changes (_split_by_reference).
"""
import functools
import math
import numpy as np
from shapely.geometry import LineString, Point
from .formulas import cpwg, embedded_microstrip, microstrip, offset_stripline, stripline
from .elements import Element, direction_at


MIXED_ER = 1.03             # dielectrics between a stripline's planes differing by more: solved on its cut

def _dielectric(model, z_lo, z_hi, e=None):
    """Thickness-weighted er and the tan d of the dielectrics between two heights, as a field across them in series
    weighs them (each layer's thickness over its er: the largest had been taken, 15 % high on a plain microstrip and
    the worst layer's on a mixed stack). e: an Element given them, which keeps the layers (e.diel: thickness, er,
    tand, fref each) for the wideband dielectric (lines.dispersion: each layer's own curve, averaged the same way)."""
    diel = tuple((min(l['z1'], z_hi) - max(l['z0'], z_lo), l['er'], l['tand'], l.get('fref'))
                 for l in model.layers if min(l['z1'], z_hi) > max(l['z0'], z_lo))
    if e is not None:
        e.diel = diel
    return mix(diel)


def mix(diel):
    """(er, tan d) of layers (thickness, er, tand, ...) across a field in series: _dielectric's averages."""
    tot = sum(t for t, *_ in diel)
    wsum = sum(t / er for t, er, *_ in diel)
    acc = sum(t * er for t, er, *_ in diel)
    tsum = sum(t / er * td for t, er, td, *_ in diel)
    return (acc / tot if tot > 0 else 1.0), (tsum / wsum if wsum > 0 else 0.0)


class RefCopper:
    """The copper that can be a reference (planes, coplanar ground) for the line sections: every modelled
    net except the signal nets themselves (so a trace never references itself, a via pad of its own net
    or the other leg of its differential pair), per layer, in model coordinates; a `ground` layer is the
    whole outline."""
    def __init__(self, model, signal_nets):
        from shapely.ops import unary_union
        from shapely.prepared import prep
        sel = model.sel
        by_layer = {}
        for l, n, g in model.prims.items:
            if l in model.cu_z and n not in signal_nets and (sel is None or n in sel):
                by_layer.setdefault(l, []).append(g)
        self.geom = {}
        for l, gs in by_layer.items():
            u = unary_union(gs).intersection(model.outline)
            if not u.is_empty:
                self.geom[l] = u
        if model.ground:
            self.geom[model.ground] = model.outline
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

    def covers(self, layer, pt):
        """True when the reference copper of `layer` covers the point pt (x, y: mm)."""
        p = self._prep.get(layer)
        return bool(p is not None and p.covers(Point(*pt)))


def _side_gap(ref, layer, pt, n, w, reach):
    """Distance from the strip edge to the nearest reference copper on the same layer along +n (None:
    beyond reach); against the TILE mm pieces of a RefCopper, which give what the whole layer does (the nearest hit
    lies in the piece that holds it)."""
    g = ref.geom.get(layer)
    if g is None:
        return None
    edge = Point(*(pt + n * (w / 2)))
    ray = LineString([pt + n * (w / 2 + 1e-4), pt + n * (w / 2 + reach)])
    if TILE and isinstance(ref, RefCopper):
        tree, pieces = ref._tiled(layer)
        hits = [ray.intersection(pieces[i]) for i in tree.query(ray)]
        return min((edge.distance(hit) for hit in hits if not hit.is_empty), default=None)
    hit = ray.intersection(g)
    return None if hit.is_empty else edge.distance(hit)


def _nearest(model, zs, below):
    """The copper layers below the height zs (else above it), nearest first."""
    return sorted((l for l in model.cu_order if (model.cu_z[l] < zs if below else model.cu_z[l] > zs)),
                  key=lambda l: abs(model.cu_z[l] - zs))


def classify(model, ref, e, pt, d):
    """Set e.xs / z0 / eeff / er / h / s / tand for a trace or pad section on e.layer of width e.w,
    sampled at pt with direction d (unit vectors, model coordinates); ref: the RefCopper."""
    n = np.array([-d[1], d[0]])
    zs, w, t = model.cu_z[e.layer], e.w, model.cu_t.get(e.layer, 0.0)
    e.t = t
    if not w or w <= 0:                                      # a degenerate pad chord: no cross-section
        e.notes.append('no width here: no impedance')
        return
    found = {}                                               # the nearest plane each side covering pt: 'full' when
    for side in ('below', 'above'):                          # it also covers w / 2 + 2 h either side
        for l in _nearest(model, zs, side == 'below'):
            h = abs(model.cu_z[l] - zs)
            if ref.covers(l, pt):
                reach = w / 2 + 2 * h
                full = ref.covers(l, pt + n * reach) and ref.covers(l, pt - n * reach)
                found[side] = (l, h, 'full' if full else 'partial')
                break
    if not found:
        e.notes.append('no reference plane above or below')
        return
    for side, (l, h, cov) in found.items():
        if cov == 'partial':
            e.notes.append(f"{l} under it does not extend {w / 2 + 2 * h:.2f} mm either side")
    if len(found) == 2:
        (_, h1, _), (_, h2, _) = found['below'], found['above']
        er, e.tand = _dielectric(model, zs - h1, zs + h2, e)
        e.er, e.h = er, (h1, h2)
        if abs(h1 - h2) <= 0.05 * max(h1, h2):
            e.xs, calc = 'stripline', lambda tt: stripline(w, h1 + h2 + tt, er, tt)
        else:
            e.xs, calc = 'offset stripline', lambda tt: offset_stripline(w, h1, h2, er, tt)
    else:
        side, (lp, h, _) = next(iter(found.items()))
        er, e.tand = _dielectric(model, min(zs, model.cu_z[lp]), max(zs, model.cu_z[lp]), e)
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
                e.cover, e.er_cover = cover, er_c                    # (a coupled group's field solve has it too)
                e.xs, calc = 'embedded microstrip', lambda tt: embedded_microstrip(w, h, er, tt, cover, er_c)
            else:
                e.xs, calc = 'microstrip', lambda tt: microstrip(w, h, er, tt)
    e.z0, e.eeff = calc(t)                                   # the real copper thickness
    e.z0_sheet, e.eeff_sheet = calc(0.0)                     # zero thickness, as openEMS models copper
    e.z0_formula, e.source = e.z0, 'formula'
    e.cut = _cut_args(model, ref, e, pt, n, found)           # 1 s for a board's 2,500: kept for looking at the field
    mode = field_mode()
    why = _beyond_formula(e, found) if mode == 'auto' else None
    if why:
        e.notes.append(f"solved on the board's cut: {why}")
    on_cut = mode == 'all' or bool(why)
    if on_cut or (FIELD_CPW and e.xs == 'cpwg'):
        if DEFER is None:
            _solve_section(e, on_cut)
        else:
            DEFER.append((e, on_cut))                         # the build solves them together (solve_deferred)


FIELD = 'auto'          # the sections whose cross-section (the board's copper cut across them) the 2D field solver
                        # (xsection.py) solves instead of the formulas: 'auto', those whose cut holds what their
                        # formula leaves out (_beyond_formula); 'all' (or True), every one; 'off' (or False), none
FIELD_PLANE = 2.0       # auto: a reference plane has to reach this many times its distance beyond the strip's edges
FIELD_OTHER = 2.0       # auto: copper on another layer counts when it is nearer, sideways, than this x its distance
                        # in z (and not behind a plane that reaches FIELD_PLANE)
FIELD_SIDE = (1.0, 2.0)  # auto: copper beside the strip on its own layer counts when nearer than this x the nearest
                        # plane's distance: over one plane (the formula holds to 4 % nearer still; both sides near
                        # is grounded CPW), between two planes (the stripline formulas know no ground beside them)
FIELD_CPW = True        # grounded CPW sections by the field solver even without FIELD, as the classified cross-section
                        # (strip, gaps, plane): the formula has no copper thickness (10-12 % at 35 um, 0.15 mm gaps)
FIELD_DIFFERS = 0.02    # a field-solved section is a line of its own Z0 in the circuit solvers (Element.field) where
                        # its sheet Z0 is this far from the formula's; else their own models (with dispersion) stay
FIELD_REACH = 5.0       # x the farthest reference height: how far either side of the strip the cut takes copper
DEFER = None            # a list while a build collects the sections for the field solver (builder._build): classify
                        # adds to it instead of solving, and solve_deferred solves them together. None: as they come
PARALLEL = 48           # this many cross-sections or more are solved in worker processes (their start costs about
                        # half a second; a solve 0.03 s)
SNAP = 0.002            # mm: copper closer than this along a cut is one piece; pieces shorter are dropped
BIN_MAX = 1.0           # reference heights: the widest of _snap_distance's bins


def field_mode(v=None):
    """'auto', 'all' or 'off': FIELD (or v: a setting, a flag) as one of its three values."""
    v = FIELD if v is None else v
    if isinstance(v, str):
        v = v.strip().lower()
        if v in ('auto', 'all', 'off'):
            return v
        if v in ('true', 'yes', 'on', '1', 'field'):
            return 'all'
        if v in ('false', 'no', '0', 'formulas', 'none'):
            return 'off'
        raise ValueError(f"the line model's field solver: auto, all or off, not {v!r}")
    return 'all' if v else 'off'


def _beyond_formula(e, found):
    """What the cut across section e holds that its formula leaves out, as a few words (None: nothing that matters,
    the formula can be kept). The formula assumes its reference planes (found: classify's) run on to either side,
    nothing on the other layers in front of them, and nothing beside the strip but a grounded CPW's ground at the gap
    classified. Measured on a real six-layer board's 2,309 single sections (1,230 mm) against every cut solved: the
    sections kept on their formulas are within 3.8 % (0.7 mm of them over 3 %), with 16 % of the sections solved;
    without it 324 mm are over 3 % and 43 mm over 10 %, most of them striplines with ground beside them."""
    w, zs, t, up, side, layers, diel = e.cut
    hs = [h for _, h, _ in found.values()]
    is_ref = lambda dz: any(abs(dz - h) < 1e-4 for h in hs)
    seen, shield = 0, {}
    for z, full, ps in layers:                              # the planes it was classified on
        dz = abs(z - zs)
        if is_ref(dz):
            seen += 1
            reach = max((min(-a, b) - w / 2 for a, b in ps if a <= 0 <= b), default=-w / 2)
            if reach < FIELD_PLANE * dz:
                return (f"the plane {dz:.3g} mm from it ends {reach:.2f} mm beyond its edge" if reach > 0 else
                        f"the plane {dz:.3g} mm from it ends under it")
            shield[z > zs] = dz
    if seen < len(hs):
        return "a plane it was classified on is not across its cut"
    lo, hi = zs - (shield.get(False) or 0.0), zs + (shield.get(True) or 0.0)
    ers = {er for z0, z1, er, _ in diel if min(z1, hi) - max(z0, lo) > 1e-6} if len(hs) == 2 else set()
    if ers and max(ers) > MIXED_ER * min(ers):              # (a formula on the averaged er: Z0 4 % low, the delay
        return f"dielectrics of er {min(ers):.3g} and {max(ers):.3g} between its planes"   # 5.6 % long)
    for z, full, ps in layers:                              # copper on other layers, in front of those planes
        dz = abs(z - zs)
        if is_ref(dz) or dz > shield.get(z > zs, math.inf):
            continue
        for a, b in ps:
            g = 0.0 if a < w / 2 and b > -w / 2 else (a - w / 2 if a > 0 else -w / 2 - b)
            if g < FIELD_OTHER * dz:
                return f"copper {dz:.3g} mm {'above' if z > zs else 'below'} it" + \
                       (f", {g:.2f} mm to the side" if g > 0 else '')
    left = min((-w / 2 - b for a, b in side if b < 0), default=math.inf)
    right = min((a - w / 2 for a, b in side if a > 0), default=math.inf)
    if e.xs == 'cpwg':                                      # solved as classified (_field_cpwg), if that is its cut
        s = getattr(e, 's', None) or 0.0
        if not (s > 0 and all(g / GAP_STEP <= s <= g * GAP_STEP for g in (left, right))):
            return f"its ground is {left:.2f} and {right:.2f} mm away, not {s:.2f} mm either side"
        return None
    near = min(left, right)
    if near < FIELD_SIDE[len(hs) == 2] * min(hs):
        return f"copper {near:.2f} mm beside it" + (' (between two planes)' if len(hs) == 2 else '')
    return None


def cut(ref, pt, n, R):
    """The reference copper on a line across a strip, R mm either side of its centre pt (n: across it): {layer:
    [(u0, u1)]}, the pieces along n from pt, sorted; -inf / inf where copper reaches an end of the cut."""
    seg = LineString([pt - n * R, pt + n * R])
    out = {}
    for layer, g in ref.geom.items():
        if TILE and isinstance(ref, RefCopper):
            tree, pieces = ref._tiled(layer)
            cand = [pieces[k] for k in tree.query(seg)]
        else:
            cand = [g]
        iv = []
        for c in cand:
            hit = seg.intersection(c)
            for p in getattr(hit, 'geoms', [hit]):
                if p.geom_type == 'LineString' and p.length > 1e-9:
                    iv.append(sorted(float(np.dot(np.asarray(q) - pt, n)) for q in (p.coords[0], p.coords[-1])))
        merged = []
        for a, b in sorted(iv):
            if merged and a <= merged[-1][1] + SNAP:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        merged = [(-math.inf if a <= -R + SNAP else a, math.inf if b >= R - SNAP else b)
                  for a, b in merged if b - a >= SNAP]
        if merged:
            out[layer] = merged
    return out


def _cut_copper(model, ref, layer, pt, n, R, W0, W, h0):
    """The copper a cut across a strip (or a coupled group's strips) on `layer` finds, R mm either side of pt: (its
    layer's pieces as found; those beside it, beyond its outer edges W0 apart (W when snapped); every other layer's
    (z, spans the cut, pieces), nearest first, up to the nearest plane that spans the cut on either side; the
    dielectrics). The pieces' ends have their distances from the nearer outer edge snapped (_snap_distance, in
    reference heights h0): similar cuts are one solve."""
    from . import xsection as X
    pieces = cut(ref, pt, n, R)
    zs = model.cu_z[layer]

    def at(u):
        if not math.isfinite(u):
            return u
        d = u - (W0 if u >= 0 else -W0) / 2
        return round((W if u >= 0 else -W) / 2 + math.copysign(_snap_distance(abs(d) / h0, X.SIMILAR) * h0, d), 6)

    def snapped(ps):
        """The pieces with their ends snapped; as they are if that would disorder them (ends snapped across the middle
        of a wide strip, from either side)."""
        got = tuple((at(a), at(b)) for a, b in ps)
        ends = [v for piece in got for v in piece]
        return got if all(p < q for p, q in zip(ends, ends[1:])) else tuple((round(a, 6), round(b, 6)) for a, b in ps)
    layers = []
    for below in (True, False):
        for l in sorted((l for l in pieces if l != layer and (model.cu_z[l] < zs) == below),
                        key=lambda l: abs(model.cu_z[l] - zs)):
            full = pieces[l] == [(-math.inf, math.inf)]
            layers.append((round(model.cu_z[l], 6), full, snapped(pieces[l])))
            if full:
                break                                        # nothing beyond a plane across the whole cut counts
    diel = tuple((round(l['z0'], 6), round(l['z1'], 6), l['er'], l['tand']) for l in model.layers)
    own = pieces.get(layer, ())
    return own, snapped([(a, b) for a, b in own if b < -W0 / 2 or a > W0 / 2]), tuple(layers), diel


def _cut_args(model, ref, e, pt, n, found):
    """_cut_section's arguments for a section: the copper a cut across it finds (_cut_copper), every layer's, out to
    FIELD_REACH reference heights either side."""
    from . import xsection as X
    hs = [h for _, h, _ in found.values()]
    w = X.snap(e.w)                                          # similar cross-sections: one solve (xsection.SIMILAR)
    _, side, layers, diel = _cut_copper(model, ref, e.layer, pt, n, e.w / 2 + max(3 * e.w, FIELD_REACH * max(hs)),
                                        e.w, w, X.snap(min(hs)))
    up = 'below' in found                                    # copper thickness grows away from the plane under it
    return round(w, 6), round(model.cu_z[e.layer], 6), round(model.cu_t.get(e.layer, 0.0), 6), up, side, layers, diel


def group_cut(model, ref, layer, legs, pt, n, hs, up):
    """_strips_section's arguments for a coupled group, cut across its middle: its legs as the strips, and the copper
    the cut finds on every layer, out to FIELD_REACH reference heights beyond its outer edges, as a single
    section's cut has it (_cut_args). legs: [(centre along n from pt, width)], in order, pt on the middle between
    the group's outer edges; hs: its reference heights; up: the copper's thickness grows upward. Widths and gaps
    are snapped as the groups over ideal planes are (xsection.SIMILAR), and the other copper's distances from the
    group's outer edges as a single strip's from its own (_snap_distance): similar groups are one solve. Copper
    of its own layer between two of its lines (a guard trace) is in the cut too, at its place in the gap."""
    from . import xsection as X
    ws = [X.snap(w) for _, w in legs]
    gaps = [X.snap((c1 - w1 / 2) - (c0 + w0 / 2)) for (c0, w0), (c1, w1) in zip(legs, legs[1:])]
    W0 = (legs[-1][0] + legs[-1][1] / 2) - (legs[0][0] - legs[0][1] / 2)      # between its outer edges, as it is
    W = sum(ws) + sum(gaps)                                                   # and snapped
    R = W0 / 2 + max(3 * max(w for _, w in legs), FIELD_REACH * max(hs))
    own, side, layers, diel = _cut_copper(model, ref, layer, pt, n, R, W0, W, X.snap(min(hs)))
    x, strips = -W / 2, []
    for k, w in enumerate(ws):
        strips.append((round(x, 6), round(x + w, 6)))
        x += w + (gaps[k] if k < len(gaps) else 0.0)
    between = []
    for k in range(len(legs) - 1):                           # copper between two lines: a guard trace
        lo, hi = legs[k][0] + legs[k][1] / 2, legs[k + 1][0] - legs[k + 1][1] / 2       # the gap as it is
        s_lo, s_hi = strips[k][1], strips[k + 1][0]                                     # and snapped
        for a, b in own:
            a, b = max(a, lo + SNAP), min(b, hi - SNAP)
            if b - a >= SNAP:
                a = s_lo + X.snap(a - lo)                    # from the line before it, its width: both snapped
                b = min(a + X.snap(b - a), s_hi - SNAP)
                between.append((round(a, 6), round(b, 6)))
    side = tuple(sorted(side + tuple(between)))
    return tuple(strips), round(model.cu_z[layer], 6), round(model.cu_t.get(layer, 0.0), 6), up, side, layers, diel


def _flip(ps):
    """Pieces (a, b) along a cut, mirrored about its middle."""
    return tuple(sorted((-b, -a) for a, b in ps))


def group_keys(args):
    """(_solve_group's arguments for a group's cut with its copper's thickness and as sheets, flipped): a cut and its
    mirror image are one solve, and flipped says the lines' order in it is the reverse of the group's."""
    strips, zs, t, up, side, layers, diel = args
    mirror = (_flip(strips), _flip(side), tuple((z, full, _flip(ps)) for z, full, ps in layers))
    flipped = mirror < (strips, side, layers)
    if flipped:
        strips, side, layers = mirror
    return [(strips, zs, tt, up, side, layers, diel) for tt in (t, 0.0)], flipped


def group_beyond(args, xs, hs):
    """What a coupled group's cut holds that ideal planes leave out (None: nothing that matters), as _beyond_formula
    says it of a single section: the group as one strip between its outer edges. Copper between two of its lines
    always matters."""
    from types import SimpleNamespace
    strips, zs, t, up, side, layers, diel = args
    w = strips[-1][1] - strips[0][0]
    outer = tuple(p for p in side if p[1] <= strips[0][0] or p[0] >= strips[-1][1])
    if len(outer) < len(side):
        a, b = next(p for p in side if p not in outer)
        return f"copper {b - a:.2f} mm wide between its lines"
    probe = SimpleNamespace(cut=(w, zs, t, up, outer, layers, diel), xs=xs, s=None)
    found = {k: (None, h, True) for k, h in zip(('below', 'above'), hs)}
    return _beyond_formula(probe, found)


def _field(e):
    """Z0 and eeff of a section (with its copper's thickness and as a sheet) from the field solver, on its cut."""
    _take(e, _solve_cut, _cut_keys, 'cut')


def _cut_keys(e):
    """_solve_cut's arguments for section e's cut: with its copper's thickness, and as a sheet."""
    w, zs, t, up, side, layers, diel = e.cut
    mirror = (_flip(side), tuple((z, full, _flip(ps)) for z, full, ps in layers))
    side, layers = min((side, layers), mirror)               # a cut and its mirror image: one solve
    return [(w, zs, tt, up, side, layers, diel) for tt in (t, 0.0)]


def _take(e, solve, keys, source):
    """Section e from the field solver: solve(*k) for each of keys(e), [(Z0, eeff) with the copper's thickness, as a
    sheet], noted. False where the solve fails: the formulas' values are kept."""
    try:
        got = [solve(*k) for k in keys(e)]
    except Exception as ex:                                  # noqa: BLE001 (a cross-section it cannot grid)
        e.notes.append(f"field solver failed ({ex}): formula impedance")
        return False
    e.source = source
    e.notes.append(f"field solver: Z0 {got[0][0]:.1f} ohm (formula {e.z0:.1f})")
    e.field = abs(got[1][0] / e.z0_sheet - 1) > FIELD_DIFFERS if e.z0_sheet else True
    (e.z0, e.eeff, *loss), (e.z0_sheet, e.eeff_sheet, *_) = got
    if loss and loss[0] > 0:                                # the mode's own tan d, which the solve gives
        e.tand_eff = loss[0]
    return True


def _field_cpwg(e):
    """A grounded CPW section by the field solver as classify found it: the strip, gaps e.s either side (ground to
    the side of the cut), the plane e.h under it, one dielectric (cached by the snapped sizes)."""
    from . import xsection as X
    if _take(e, _solve_cpwg, _cpwg_keys, 'cpwg'):
        w, s, h, er, t = _cpwg_keys(e)[0]
        e.classified = X.microstrip(w, h, er, t, s=s)


def _cpwg_keys(e):
    """_solve_cpwg's arguments for a grounded CPW section as classified: with its copper's thickness, and as a
    sheet."""
    from . import xsection as X
    s = _snap_distance(e.s / e.h, X.SIMILAR) * e.h            # the gaps, as the cut's copper is (_distance_bins)
    key = (round(X.snap(e.w), 6), round(s, 6), round(e.h, 6), e.er)
    return [key + (t,) for t in (round(e.t, 6), 0.0)]


def _solve_section(e, on_cut):
    """Section e from the field solver: on its cut; a grounded CPW not on its cut (or whose cut would not solve) as
    it was classified."""
    if on_cut:
        _field(e)
    if e.source == 'formula' and FIELD_CPW and e.xs == 'cpwg':
        _field_cpwg(e)


class _Cached:
    """A function's results by its arguments, as functools.lru_cache keeps them (cache_clear(), cache_info().misses:
    the solves made), which can also be told results found elsewhere: solve_deferred's worker processes."""
    def __init__(self, f, maxsize):
        self.f, self.maxsize, self.got, self.misses = f, maxsize, {}, 0
        functools.update_wrapper(self, f)

    def __call__(self, *args):
        if args not in self.got:
            self.put(args, self.f(*args))
        return self.got[args]

    def put(self, args, result):
        if len(self.got) >= self.maxsize:
            del self.got[next(iter(self.got))]                # the oldest
        self.got[args] = result
        self.misses += 1

    def cache_clear(self):
        self.got.clear()
        self.misses = 0

    def cache_info(self):
        from types import SimpleNamespace
        return SimpleNamespace(misses=self.misses, currsize=len(self.got), maxsize=self.maxsize)


def _cached(maxsize):
    return lambda f: _Cached(f, maxsize)


def _job(item):
    """In a worker process: (item, the (Z0, eeff) of one cross-section), None where the solver fails (the section
    that asked then solves it again, for what went wrong)."""
    kind, args = item
    try:
        return item, dict(cut=_solve_cut, cpwg=_solve_cpwg, group=_solve_group)[kind].f(*args)
    except Exception:                                        # noqa: BLE001
        return item, None


def solve_deferred(jobs, workers=None):
    """Solve the sections classify left in DEFER, in the order it met them, their cross-sections in worker processes
    first where there are many (_in_parallel): a real board's hundreds of cuts take a few seconds instead of half
    a minute. Where worker processes cannot start, they are solved one after another, as before."""
    want = []
    for e, on_cut in jobs:
        if on_cut:
            want += [('cut', k) for k in _cut_keys(e)]
        elif e.xs == 'cpwg':
            want += [('cpwg', k) for k in _cpwg_keys(e)]
    _in_parallel(want, workers)
    for e, on_cut in jobs:
        _solve_section(e, on_cut)


def _in_parallel(items, workers=None):
    """The distinct cross-sections of `items` ([(kind, its solve's arguments)]) that no earlier section solved, solved
    in worker processes when there are PARALLEL or more (workers: how many; None: one per fast core, emsdev's
    core_threads), into the caches of their solves."""
    cache = dict(cut=_solve_cut, cpwg=_solve_cpwg, group=_solve_group)
    todo = [item for item in dict.fromkeys(items) if item[1] not in cache[item[0]].got]
    if workers is None:
        try:
            from kicad2ems.emsdev.cpu import core_threads
            workers = core_threads()
        except Exception:                                    # noqa: BLE001
            workers = 1
    workers = min(workers, len(todo) // 8)                   # a worker is worth starting for 8 solves or more
    if len(todo) >= PARALLEL and workers > 1:
        for item, got in _in_workers(todo, workers):
            if got is not None and item[1] not in cache[item[0]].got:
                cache[item[0]].put(item[1], got)


def _in_workers(todo, workers):
    """[(item, result or None)] of the cross-sections `todo`, solved by `workers` python processes (python -m
    kicad2ems.core.lines.cutworker: pickled lists of items on its stdin, their results on its stdout). What a worker
    did not return (it could not start, or died) is left out: the caller solves it itself.

    They are started as commands, not through multiprocessing: that imports the program's main module again in
    every worker, and a program whose main module does not guard itself (a script, a GUI's __main__) would start
    itself again in each. Only a python can be asked to run a module: an embedded interpreter's executable is its
    host program, and gets no workers."""
    import os
    import pickle
    import subprocess
    import sys
    import threading
    exe = sys.executable or ''
    if not os.path.basename(exe).lower().startswith('python'):
        return []
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p),
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')     # a solve is one thread's work
    size = max(1, min(16, len(todo) // (4 * workers)))
    chunks = iter([todo[i:i + size] for i in range(0, len(todo), size)])
    lock, out = threading.Lock(), []

    def serve():
        try:
            p = subprocess.Popen([exe, '-m', 'kicad2ems.core.lines.cutworker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, env=env,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            return
        try:
            while True:
                with lock:
                    chunk = next(chunks, None)
                if chunk is None:
                    break
                pickle.dump(chunk, p.stdin)
                p.stdin.flush()
                got = pickle.load(p.stdout)
                with lock:
                    out.extend(got)
        except Exception:                                    # noqa: BLE001 (a worker that died: its chunk is the caller's)
            pass
        finally:
            try:
                p.stdin.close()
                p.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                p.kill()

    threads = [threading.Thread(target=serve, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


@_cached(1024)
def _solve_cpwg(w, s, h, er, t):
    from . import xsection as X
    return X.microstrip(w, h, er, t, s=s).solve().single()           # (one dielectric: its tan d is the layer's)


@functools.lru_cache(maxsize=8)
def _distance_bins(rel):
    """The edges of _snap_distance's bins, in reference heights from the strip's edge (x), out to FIELD_REACH + 5:
    each bin rel (x + 0.2) e^(1.4 (x - 1/2)) wide beyond half a height, and at most BIN_MAX. Ground beside a microstrip
    moves Z0 by about that much less per height further away, so a bin is worth about rel / 4 of Z0 wherever it is
    (0.1-0.3 % for rel 1 %; ground at 2 heights moves Z0 by 0.9 %, at 4 by less than 0.1 %). The limit is for what the
    rule doesn't cover: a plane's edge that far under a wide strip counts wherever it is."""
    edges = [0.0]
    while edges[-1] < FIELD_REACH + 5:
        x = edges[-1]
        edges.append(x + min(rel * (x + 0.2) * math.exp(1.4 * max(x - 0.5, 0.0)), BIN_MAX))
    return np.asarray(edges)


def _snap_distance(x, rel):
    """x (reference heights from the strip's edge) at the middle of its bin (_distance_bins); x itself when rel is 0
    or x lies beyond the bins."""
    if not rel:
        return x
    edges = _distance_bins(rel)
    i = int(np.searchsorted(edges, x, side='right')) - 1
    return x if i >= len(edges) - 1 else 0.5 * (edges[i] + edges[i + 1])


@_cached(8192)
def _solve_cut(w, zs, t, up, side, layers, diel):
    """(Z0, eeff, tan d) of the cross-section _cut_section builds from a cut (cached: one solve per distinct cut); tan d
    the mode's own, G / (w C)."""
    r = _cut_section(w, zs, t, up, side, layers, diel).solve()
    return (*r.single(), float(r.tand_eff()))


@_cached(4096)
def _solve_group(strips, zs, t, up, side, layers, diel):
    """(C, C_air), the per-unit-length Maxwell capacitance matrices of a coupled group's lines on its cut (cached:
    one solve per distinct cut)."""
    r = _strips_section(strips, zs, t, up, side, layers, diel).solve()
    return r.C, r.Ca


def solve_groups(keys, workers=None):
    """The cuts of coupled groups (group_keys' arguments, any number) solved, those no earlier group solved in
    worker processes when there are PARALLEL or more: _solve_group then has them."""
    _in_parallel([('group', k) for k in keys], workers)


def _cut_section(w, zs, t, up, side, layers, diel):
    """The xsection.CrossSection of a strip w wide and t thick on the copper level zs (thickness growing up when up,
    else down, the stack above or below moved by t: the dielectrics' thicknesses are between the copper), the same
    layer's copper `side` beside it, the other layers' copper (z, spans the cut, pieces) as sheets, in the
    dielectrics."""
    return _strips_section(((-w / 2, w / 2),), zs, t, up, side, layers, diel)


def _strips_section(strips, zs, t, up, side, layers, diel):
    """_cut_section's cross-section with the strips ((x0, x1), in order) as its lines: one, or a coupled group's
    legs."""
    from . import xsection as X
    zmap = (lambda z: z + t if z > zs + 1e-9 else z) if up else (lambda z: z - t if z < zs - 1e-9 else z)
    z0, z1 = (zs, zs + t) if up else (zs - t, zs)
    xs = X.CrossSection()
    for a, b, er, tand in diel:
        xs.dielectrics.append(X.Dielectric(zmap(a), zmap(b), er, tand))
    for a, b in strips:
        xs.conductor(a, b, z0, z1)
    for a, b in side:
        xs.conductor(a, b, z0, z1, ground=True)
    for z, full, ps in layers:
        if full:
            xs.planes.append(zmap(z))
        else:
            for a, b in ps:
                xs.conductor(a, b, zmap(z), ground=True)
    return xs


GAP_STEP = 1.5          # a coplanar gap changing by this factor starts a new section (a tapered gap)
TILE = 4.0              # mm: reference copper cut into squares of this size for ray casts (None: whole planes)
WIDTH_STEP = 1.2        # a copper width changing by this factor starts a new section (a taper)


def _gap_class(model, layer, refs, pt, d, w, local):
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
        parts = [p for p in getattr(seg, 'geoms', [seg]) if p.geom_type == 'LineString']
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
    pad beside the trace, is ignored). A piece widened by copper shapes of its net gets that width. (Sampling
    every 1-4 mm away from any change, densely only where copper corners lie along the trace, was tried: the same
    map, but no faster: 1.0-1.1x on KiCad's CM5 and video demos, 0.9x on the Jetson one.)"""
    from types import SimpleNamespace
    from shapely.ops import substring
    line = LineString(e.pts)
    L = line.length
    n = int(min(max_samples, max(3, math.ceil(L / step))))
    pos = [(i + 0.5) * L / n for i in range(n)]
    if e.region is not None:                               # a strip: the width of its own copper
        wide = e.region
    else:
        wide = netcu.near(e.layer, line.buffer(max(netcu.reach, 10 * e.w) + 0.1)) if netcu is not None else None
    if wide is not None and wide.is_empty:
        wide = None

    def across(s):
        """(point, direction, copper width, centre offset) at distance s along the trace."""
        pt = np.asarray(line.interpolate(s).coords[0])
        d = direction_at(line, s, 0.01)
        w, off = NetCopper.width(netcu, wide, pt, np.array([-d[1], d[0]]), e.w) if wide is not None else (e.w, 0.0)
        return pt, d, w, off

    if TILE:
        local = ref
    else:                                                  # the layer's reference copper near the trace: fast rays
        hmax = max(model.cu_z.values()) - min(model.cu_z.values())
        rmax = max(netcu.reach if netcu is not None else 0.0, 10 * e.w) if wide is not None else e.w
        g = ref.geom.get(e.layer)
        near = {e.layer: g.intersection(line.buffer(rmax + 2 * hmax + 0.1))} if g is not None else {}
        local = SimpleNamespace(geom=near)
    zs = model.cu_z[e.layer]

    def sample(s):
        """(the nearest reference layers below and above covering it, or None each; gap class; width class) at s."""
        pt, d, w, off = across(s)
        r = tuple(next((l for l in _nearest(model, zs, below) if ref.covers(l, pt)), None) for below in (True, False))
        return r, _gap_class(model, e.layer, r, pt + np.array([-d[1], d[0]]) * off, d, w, local), \
            int(round(math.log(w / e.w) / math.log(WIDTH_STEP)))

    refs, gaps, widths = (list(x) for x in zip(*(sample(s) for s in pos)))
    for i in range(1, n - 1):
        if gaps[i - 1] == gaps[i + 1] != gaps[i] and refs[i - 1] == refs[i] == refs[i + 1]:
            gaps[i] = gaps[i - 1]
    antipad = _via_antipads(model, e, pos, refs, L)
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
    for k, s in antipad.items():
        if len(out) > 1:
            out[k].classify_at = np.asarray(line.interpolate(s).coords[0])
    return out


VIA_ANTIPAD = 3.0       # x the line's reference height: the end run over its own via's antipad that keeps the line's
                        # reference (None: none does)


def _via_antipads(model, e, pos, refs, L):
    """A trace that ends at a via crosses that via's antipad last: a short run there whose reference differs from
    the line's is part of the via transition, the plane's edge round it as close as the plane under the line, not
    a line over the next plane down (rf_diffvia: 0.5 mm at 92 ohm instead of 55, worst Sdd11 -9.4 dB against
    openEMS's -15.4). Up to VIA_ANTIPAD x the line's reference height. {0 or -1 (the end piece): the distance
    along the trace to classify it at instead, the line's nearest sample}. A slot the trace crosses (a defected
    ground) is not at a via and keeps its own cross-section."""
    out = {}
    if not VIA_ANTIPAD or len(refs) < 2:
        return out
    zs = model.cu_z[e.layer]
    for end, node in ((0, e.a), (len(refs) - 1, e.b)):
        if not str(node).startswith('via'):
            continue
        step = 1 if end == 0 else -1
        k = end
        while 0 <= k < len(refs) and refs[k] == refs[end]:
            k += step
        if not 0 <= k < len(refs):
            continue                                     # one reference all along: nothing to keep
        hs = [abs(model.cu_z[l] - zs) for l in refs[k] if l is not None]
        cut = 0.5 * (pos[k] + pos[k - step])
        if hs and (cut if end == 0 else L - cut) <= VIA_ANTIPAD * min(hs):
            out[0 if end == 0 else -1] = pos[k]
    return out
