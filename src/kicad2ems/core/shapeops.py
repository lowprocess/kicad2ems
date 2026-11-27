"""
Editing several drawn shapes at once (kicad2ems.core.shapes), with no Qt: what
the Draw page's selection, its exact edits, its snapping and its measure tool
do, as data.

    bounds_of(shapes); centre_of(shapes)          # of them all, model mm
    rotated(shapes, 30, about=(x, y))             # copies (about=None: their
    mirrored(shapes, 'x')                         #  centre)
    offsets = aligned(shapes, 'left', keep={2})   # (dx, dy) each; kept ones
    offsets = distributed(shapes, 'x')            #  0, as anchors
    copies = array_row(shapes, 5, dx, dy)         # the new copies alone
    copies = array_grid(shapes, nx, ny, dx, dy)
    copies = array_circle(shapes, 12, (cx, cy), angle=360, turn=True)
    text = to_clipboard(shapes); shapes = from_clipboard(text)  # or None
    snap = Snapper(layout)                        # the board's pads, vias,
    snap.set_shapes(drawing.shapes)               #  track ends and edges,
    hit = snap.find(x, y, tol, skip={3})          #  the shapes' corners...
    m = measure([(x0, y0), (x1, y1), (x2, y2)])   # lengths and angles
    new = combined(shapes, 'union' | 'subtract' | 'intersect')
                                                  # polygons (shapely)
    s = rounded(shape, 0.5) ; s = rounded(shape, 0.5, 'chamfer')
                                                  # its corners
    idx = alike(shapes, i, 'material' | 'layer' | 'net' | 'kind')
    pts = ring_points(polygon)                    # a Shape's points, holes
                                                  #  joined in (a keyhole)

Coordinates are the model's (mm, y up), as core.shapes's; angles are degrees,
anticlockwise.
"""
import json, math
from dataclasses import dataclass
import numpy as np

__all__ = ['bounds_of', 'centre_of', 'rotated', 'mirrored', 'aligned',
           'distributed', 'array_row', 'array_grid', 'array_circle',
           'to_clipboard', 'from_clipboard', 'Snap', 'Snapper', 'measure',
           'ALIGN', 'combined', 'rounded', 'alike', 'ring_points', 'BOOLEANS',
           'ALIKE']

# how shapes line up: (axis, where on it)
ALIGN = dict(left=(0, 'lo'), right=(0, 'hi'), hcentre=(0, 'mid'),
             bottom=(1, 'lo'), top=(1, 'hi'), vcentre=(1, 'mid'))


def bounds_of(shapes):
    """(x0, y0, x1, y1) of the shapes together, or None for none."""
    b = [s.bounds() for s in shapes]
    if not b:
        return None
    b = np.asarray(b, float)
    return (*b[:, :2].min(0), *b[:, 2:].max(0))


def centre_of(shapes):
    """The centre of their bounds (x, y), or None."""
    b = bounds_of(shapes)
    return None if b is None else ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def rotated(shapes, angle, about=None):
    """Copies turned `angle` degrees about `about` (None: their centre)."""
    about = centre_of(shapes) if about is None else about
    return [s.rotated(angle, about) for s in shapes]


def mirrored(shapes, axis, at=None):
    """Copies mirrored together ('x': x reversed, 'y': y reversed) about the
    line at `at` (None: their centre)."""
    if at is None:
        c = centre_of(shapes)
        at = c[0 if axis == 'x' else 1] if c else 0.0
    return [s.mirrored(axis, at) for s in shapes]


def aligned(shapes, how, keep=()):
    """[(dx, dy)] that line the shapes up (ALIGN's keys: left, right,
    hcentre, top, bottom, vcentre) on the bounds of them all; the shapes of
    index in `keep` (locked ones) stay, and count in the bounds."""
    axis, where = ALIGN[how]
    b = np.asarray([s.bounds() for s in shapes], float).reshape(-1, 4)
    lo, hi = b[:, axis], b[:, axis + 2]
    to = (lo.min() if where == 'lo' else hi.max() if where == 'hi' else
          (lo.min() + hi.max()) / 2)
    at = lo if where == 'lo' else hi if where == 'hi' else (lo + hi) / 2
    out = []
    for i, d in enumerate(to - at):
        d = 0.0 if i in keep else round(float(d), 9)
        out.append((d, 0.0) if axis == 0 else (0.0, d))
    return out


def distributed(shapes, axis, keep=()):
    """[(dx, dy)] that space the shapes' centres evenly along 'x' or 'y'
    between the first and the last (in that order along it), which stay;
    those of index in `keep` stay too (the rest spread round them as if they
    had moved)."""
    k = 0 if axis == 'x' else 1
    b = np.asarray([s.bounds() for s in shapes], float).reshape(-1, 4)
    c = (b[:, k] + b[:, k + 2]) / 2
    order = np.argsort(c, kind='stable')
    out = [(0.0, 0.0)] * len(shapes)
    if len(shapes) < 3:
        return out
    want = np.linspace(c[order[0]], c[order[-1]], len(shapes))
    for j, i in enumerate(order):
        d = 0.0 if i in keep else round(float(want[j] - c[i]), 9)
        out[i] = (d, 0.0) if k == 0 else (0.0, d)
    return out


def _named(s, k):
    """A copy's name: its original's with _k (an unnamed shape stays so)."""
    if s.name:
        s.name = f"{s.name}_{k}"
    return s


def array_row(shapes, n, dx, dy):
    """n in a row (the shapes themselves the first), each step (dx, dy) mm
    from the last: the n - 1 new copies of them all."""
    return [_named(s.moved(k * dx, k * dy), k + 1)
            for k in range(1, int(n)) for s in shapes]


def array_grid(shapes, nx, ny, dx, dy):
    """nx by ny (the shapes themselves at the corner), dx along x, dy along
    y: the new copies, row by row."""
    out = []
    for j in range(int(ny)):
        for i in range(int(nx)):
            if i or j:
                out += [_named(s.moved(i * dx, j * dy), j * int(nx) + i + 1)
                        for s in shapes]
    return out


def array_circle(shapes, n, centre, angle=360.0, turn=True):
    """n round `centre` (the shapes themselves the first), `angle` degrees
    from the first to the last (360: the whole way round, evenly), each copy
    turned with it (turn) or only carried round: the new copies."""
    n = int(n)
    full = abs(abs(angle) - 360) < 1e-9
    step = angle / n if full else angle / max(n - 1, 1)
    out = []
    c0 = centre_of(shapes)
    for k in range(1, n):
        a = k * step
        if turn:
            out += [_named(s.rotated(a, centre), k + 1) for s in shapes]
        else:  # their centre carried round, the shapes as they are
            x, y = c0
            r = math.radians(a)
            nx = centre[0] + math.cos(r) * (x - centre[0]) - math.sin(r) * (
                y - centre[1])
            ny = centre[1] + math.sin(r) * (x - centre[0]) + math.cos(r) * (
                y - centre[1])
            out += [_named(s.moved(round(nx - x, 9), round(ny - y, 9)), k + 1)
                    for s in shapes]
    return out


# ---- the clipboard ----------------------------------------------------------
def to_clipboard(shapes):
    """The shapes as a kicad2ems-shapes file's text (what Copy puts on the
    clipboard: it pastes into any drawing, and saves as one)."""
    from .shapes import Drawing
    return json.dumps(Drawing([s.copy() for s in shapes]).to_dict(), indent=1)


def from_clipboard(text):
    """The shapes in a clipboard's text (a kicad2ems-shapes file's), shown
    and unlocked; None when it holds none."""
    from .shapes import Drawing
    try:
        d = json.loads(text)
        shapes = Drawing.from_dict(d).shapes
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    for s in shapes:
        s.hidden = s.locked = False
    return shapes or None


# ---- snapping -----------------------------------------------------------------
@dataclass
class Snap:
    """Where a point snapped to: (x, y), what it is ('pad', 'via', 'track
    end', 'board corner', 'board edge', or a shape's: 'corner', 'midpoint',
    'centre', 'end', 'rim', 'port') and a label to show."""
    x: float
    y: float
    what: str
    label: str

    @property
    def xy(self):
        return [round(self.x, 6), round(self.y, 6)]


class Snapper:
    """The points a click snaps to: the board's (pads' centres, vias, every
    point of its tracks, its outline's corners, and anywhere on its edges)
    and the drawn shapes' (Shape.snap_points). A point beats an edge; the
    nearest point within the tolerance wins."""

    def __init__(self, layout=None):
        pts, labels = [], []
        d = layout or {}
        prims = d.get('prims') or {}
        for fp in prims.get('footprints') or []:
            for q in fp.get('pads') or []:
                if q.get('xy') is None:
                    continue
                ref = q.get('ref') or fp.get('ref') or ''
                net = f" ({q['net']})" if q.get('net') else ''
                pts.append(q['xy'])
                labels.append(('pad', f"pad {ref}.{q.get('name', '')}{net}"))
        for v in prims.get('vias') or d.get('vias') or []:
            if 'x' in v:
                pts.append((v['x'], v['y']))
                net = f" ({v['net']})" if v.get('net') else ''
                labels.append(('via', f"via{net}"))
        for t in prims.get('tracks') or []:
            p = np.asarray(t['pts'], float).reshape(-1, 2)
            net = f" ({t['net']})" if t.get('net') else ''
            for k, xy in enumerate(p):
                end = k in (0, len(p) - 1)
                pts.append(xy)
                labels.append(('track end' if end else 'track corner',
                               f"track {'end' if end else 'corner'} on "
                               f"{t.get('layer', '')}{net}"))
        segs = []
        if d.get('outline'):
            from shapely.geometry import shape
            g = shape(d['outline'])
            for poly in getattr(g, 'geoms', [g]):
                for ring in [poly.exterior, *poly.interiors]:
                    xy = np.asarray(ring.coords, float)
                    segs.append(np.stack([xy[:-1], xy[1:]], 1))
                    for p in xy[:-1]:
                        pts.append(p)
                        labels.append(('board corner', 'board corner'))
        self.board = np.asarray(pts, float).reshape(-1, 2)
        self.board_labels = labels
        self.edges = (np.concatenate(segs) if segs else np.zeros((0, 2, 2)))
        self._tree = None
        if len(self.board) > 256:
            try:
                from scipy.spatial import cKDTree
                self._tree = cKDTree(self.board)
            except ImportError:  # pragma: no cover (scipy is a dependency)
                pass
        self.set_shapes([])

    def set_shapes(self, shapes):
        """The drawn shapes to snap to (hidden ones are left out)."""
        pts, owner, what = [], [], []
        for i, s in enumerate(shapes):
            if s.hidden:
                continue
            for x, y, w in s.snap_points():
                pts.append((x, y))
                owner.append(i)
                what.append(w)
        self.shape_pts = np.asarray(pts, float).reshape(-1, 2)
        self.owner = np.asarray(owner, int)
        self.what = what
        self.names = [s.name or s.kind for s in shapes]

    def find(self, x, y, tol, skip=(), board=True, shapes=True):
        """The Snap nearest (x, y) within tol mm, or None; the points of the
        shapes of index in `skip` (those being moved) are left out."""
        best, bd = None, tol
        if board and len(self.board):
            if self._tree is not None:
                d, k = self._tree.query((x, y), distance_upper_bound=tol)
                k = None if not np.isfinite(d) else int(k)
            else:
                dd = np.hypot(self.board[:, 0] - x, self.board[:, 1] - y)
                k = int(dd.argmin())
                d = dd[k]
            if k is not None and d <= bd:
                what, label = self.board_labels[k]
                best, bd = Snap(*self.board[k], what, label), d
        if shapes and len(self.shape_pts):
            dd = np.hypot(self.shape_pts[:, 0] - x, self.shape_pts[:, 1] - y)
            if skip:
                dd[np.isin(self.owner, list(skip))] = np.inf
            k = int(dd.argmin())
            if dd[k] <= bd:
                w = self.what[k]
                best = Snap(*self.shape_pts[k], w,
                            f"{w} of {self.names[self.owner[k]]}")
                bd = dd[k]
        if best is None and board and len(self.edges):
            p = _nearest_on_segments(self.edges, x, y)
            if p is not None and math.hypot(p[0] - x, p[1] - y) <= tol:
                best = Snap(p[0], p[1], 'board edge', 'board edge')
        return best


def _nearest_on_segments(segs, x, y):
    """The point of segments (m, 2, 2) nearest (x, y)."""
    a, b = segs[:, 0], segs[:, 1]
    ab = b - a
    L = np.einsum('ij,ij->i', ab, ab)
    t = np.where(L > 0, np.einsum('ij,ij->i', np.array([x, y]) - a, ab)
                 / np.where(L > 0, L, 1), 0)
    q = a + np.clip(t, 0, 1)[:, None] * ab
    d = np.hypot(q[:, 0] - x, q[:, 1] - y)
    return None if not len(d) else q[int(d.argmin())]


# ---- measuring ----------------------------------------------------------------
def measure(points):
    """What the measure tool says of its points [(x, y), ...]: dict(length
    (along them all), dx, dy (first to last), distance (first to last),
    angle (the first leg's direction, degrees anticlockwise from +x) and,
    with three points, turn: the angle at the second between the legs to the
    first and the third (0-180)."""
    p = np.asarray(points, float).reshape(-1, 2)
    out = dict(length=0.0, dx=0.0, dy=0.0, distance=0.0, angle=None,
               turn=None)
    if len(p) < 2:
        return out
    legs = np.diff(p, axis=0)
    out['length'] = float(np.hypot(legs[:, 0], legs[:, 1]).sum())
    out['dx'], out['dy'] = (float(v) for v in p[-1] - p[0])
    out['distance'] = float(math.hypot(out['dx'], out['dy']))
    out['angle'] = float(math.degrees(math.atan2(legs[0, 1], legs[0, 0])))
    if len(p) >= 3:
        u, v = p[0] - p[1], p[2] - p[1]
        nu, nv = np.hypot(*u), np.hypot(*v)
        if nu > 0 and nv > 0:
            c = float(np.clip(u @ v / (nu * nv), -1, 1))
            out['turn'] = float(math.degrees(math.acos(c)))
    return out


# ---- booleans, rounded corners, alike -------------------------------------------
# what combined() does: the first shape's outline with the others' ...
BOOLEANS = dict(union='joined to', subtract='taken out of',
                intersect='kept where it meets')
# how shapes are alike (select alike): the same ...
ALIKE = ('material', 'layer', 'net', 'kind')


def _same(shapes):
    """ValueError unless the shapes are of one material, a sheet's on one
    layer and a solid's between the same heights (what a boolean of them
    keeps)."""
    if any(s.kind == 'port' for s in shapes):
        raise ValueError('a port has no area: leave it out')
    mats = {s.material for s in shapes}
    if len(mats) > 1:
        raise ValueError(f"of one material only (these are "
                         f"{', '.join(sorted(mats))})")
    s0 = shapes[0]
    if s0.solid:
        if len({tuple(sorted((s.z0, s.z1))) for s in shapes}) > 1:
            raise ValueError('the solids are not between the same heights')
    elif len({s.layer for s in shapes}) > 1:
        raise ValueError('the shapes are on different layers')


def combined(shapes, op):
    """The shapes' outlines combined into polygons with the first's
    properties: 'union' (all of them), 'subtract' (the first less the
    others), 'intersect' (where they all overlap). A hole is joined to the
    outline by a cut of no width (ring_points: a Shape has one ring). [] when
    nothing is left; ValueError for fewer than two, or shapes not of one
    material and layer (or heights)."""
    from shapely.ops import unary_union
    from .geometry import polygons
    if op not in BOOLEANS:
        raise ValueError(f"boolean {op!r}: one of {', '.join(BOOLEANS)}")
    if len(shapes) < 2:
        raise ValueError('select two shapes or more')
    _same(shapes)
    gs = [s.outline(segments=64) for s in shapes]
    if any(g is None for g in gs):
        raise ValueError('a shape has no area')
    if op == 'union':
        g = unary_union(gs)
    elif op == 'subtract':
        g = gs[0].difference(unary_union(gs[1:]))
    else:
        g = gs[0]
        for h in gs[1:]:
            g = g.intersection(h)
    out = []
    for p in polygons(g):
        if p.area <= 1e-9:
            continue
        for pts in _ringable(p):
            t = shapes[0].copy()
            t.kind, t.points = 'polygon', pts
            t.locked = t.hidden = False
            out.append(t)
    if len(out) > 1 and shapes[0].name:
        for k, t in enumerate(out):
            t.name = f"{shapes[0].name}_{k + 1}"
    return out


def ring_points(g, ndigits=6):
    """A shapely Polygon as one ring of points [[x, y], ...] (anticlockwise,
    not closed): its holes joined to it each by a cut of no width (Shape's
    outline, Polygon(points).buffer(0), gives the holes back). The holes are
    joined nearest the outline first, each from its corner nearest a corner
    of the outline or of a hole joined before it (a k-d tree of the corners:
    a plate less 300 posts is 0.11 s, the subtract and all; each hole
    against the ring so far took 6.5 s)."""
    from scipy.spatial import cKDTree
    from shapely.geometry.polygon import orient
    g = orient(g, 1.0)
    rings = [np.asarray(r.coords, float)[:-1]
             for r in [g.exterior, *g.interiors]]
    xy = np.concatenate(rings)
    if len(rings) == 1:
        return np.round(xy, ndigits).tolist()
    at = np.cumsum([0] + [len(r) for r in rings])
    owner = np.repeat(np.arange(len(rings)), [len(r) for r in rings])
    every, outer = cKDTree(xy), cKDTree(rings[0])
    near = [outer.query(h)[0].min() for h in rings[1:]]
    joined = np.zeros(len(rings), bool)
    joined[0] = True
    hang = {}  # a corner (its index in every ring's): [(hole, its corner)]
    k = min(len(owner), 32)
    for r in np.argsort(near, kind='stable') + 1:
        d, i = every.query(rings[r], k=k)
        d = np.where(joined[owner[i]], d, np.inf)
        if np.isfinite(d).any():
            e, j = np.unravel_index(int(d.argmin()), d.shape)
            to = int(i[e, j])
        else:  # (among holes not joined yet: to the outline)
            d, i = outer.query(rings[r])
            e = int(d.argmin())
            to = int(i[e])
        hang.setdefault(to, []).append((int(r), int(e)))
        joined[r] = True
    # each ring from the corner it is joined at (the outline from its first)
    # round to that corner again (a hole); at a corner with holes joined to
    # it, each hole, then the corner once more. A stack of what is still to
    # do, not recursion (a chain of holes may be long): ('ring', r, start,
    # step) or ('corner', its index); the corners' indices into xy
    out, todo = [], [('ring', 0, 0, 0)]
    while todo:
        task = todo.pop()
        if task[0] == 'corner':
            out.append(task[1])
            continue
        _, r, e, s = task
        n, a = len(rings[r]), int(at[r])
        for s in range(s, n + 1 if r else n):
            c = a + (e + s) % n
            out.append(c)
            kids = hang.get(c, ()) if s < n else ()
            if kids:
                todo.append(('ring', r, e, s + 1))
                for h, eh in reversed(kids):
                    todo += [('corner', c), ('ring', h, eh, 0)]
                break
    return np.round(xy[out], ndigits).tolist()


def _ringable(p):
    """Polygon p as the ring_points of polygons they give back: p's, or
    (when a hole's cut would cross another hole) those of p split at its
    holes' centres until each piece's do."""
    from shapely.geometry import Polygon, box
    from .geometry import polygons
    pts = ring_points(p)
    if not p.interiors:
        return [pts]
    # (given back: one polygon of its area, holes and bounds; a symmetric
    # difference too took 90 ms of a plate less 300 posts' 240)
    back = Polygon(pts).buffer(0)
    tol = 1e-6 * max(p.area, 1e-9)
    if (back.geom_type == 'Polygon' and len(back.interiors) == len(
            p.interiors) and abs(back.area - p.area) <= tol
            and np.allclose(back.bounds, p.bounds, atol=1e-5)):
        return [pts]
    x0, y0, x1, y1 = p.bounds
    xc = p.interiors[0].centroid.x
    out = []
    for half in (box(x0 - 1, y0 - 1, xc, y1 + 1),
                 box(xc, y0 - 1, x1 + 1, y1 + 1)):
        for q in polygons(p.intersection(half)):
            if q.area > 1e-9:
                out += _ringable(q)
    return out


def _round_corners(pts, r, how, closed, segments):
    """The corners of polyline pts (closed: a ring) rounded with radius r
    ('fillet') or cut d = r along each leg ('chamfer'); a corner's cut is
    held to half its shorter leg (the radius shrinks to fit). A point that
    is there twice (a keyhole's cut) is left as it is."""
    p = [np.asarray(q, float) for q in pts]
    n = len(p)
    seen = {}
    for q in p:
        k = (round(q[0], 9), round(q[1], 9))
        seen[k] = seen.get(k, 0) + 1
    out = []
    for k in range(n):
        if not closed and k in (0, n - 1):
            out.append(p[k])
            continue
        a, b, c = p[k - 1], p[k], p[(k + 1) % n]
        u, v = b - a, c - b
        lu, lv = np.hypot(*u), np.hypot(*v)
        if (lu < 1e-12 or lv < 1e-12
                or seen[(round(b[0], 9), round(b[1], 9))] > 1):
            out.append(b)
            continue
        u, v = u / lu, v / lv
        cross, dot = u[0] * v[1] - u[1] * v[0], float(u @ v)
        turn = math.atan2(cross, dot)  # how far the line turns there
        if abs(turn) < 1e-6 or abs(abs(turn) - math.pi) < 1e-6:
            out.append(b)
            continue
        # (a leg to an end of a trace is this corner's alone)
        half = (lu if not closed and k == 1 else lu / 2,
                lv if not closed and k == n - 2 else lv / 2)
        if how == 'chamfer':
            t = min(r, *half)
            out += [b - u * t, b + v * t]
            continue
        t = r * math.tan(abs(turn) / 2)  # leg cut for radius r
        t = min(t, *half)
        rr = t / math.tan(abs(turn) / 2)
        # the arc's centre: rr from both legs, on the side the line turns to
        nrm = np.array([-u[1], u[0]]) * (1 if turn > 0 else -1)
        o = b - u * t + nrm * rr
        a0 = math.atan2(*(b - u * t - o)[::-1])
        m = max(2, int(math.ceil(segments * abs(turn) / (2 * math.pi))))
        for j in range(m + 1):
            ang = a0 + turn * j / m
            out.append(o + rr * np.array([math.cos(ang), math.sin(ang)]))
    return [[round(float(x), 6), round(float(y), 6)] for x, y in out]


def rounded(shape, r, how='fillet', segments=64):
    """A copy of a rectangle, polygon or trace with its corners rounded to
    radius r mm (how 'fillet') or cut r mm along each leg ('chamfer'): a
    rectangle becomes a polygon; a trace's centre line is rounded (a curved
    bend, or a mitred one) and its ends kept. ValueError for a circle or a
    port (no corners) or r <= 0."""
    if r <= 0:
        raise ValueError('the radius must be more than 0')
    if how not in ('fillet', 'chamfer'):
        raise ValueError(f"{how!r}: 'fillet' or 'chamfer'")
    if shape.kind in ('circle', 'port'):
        raise ValueError(f"a {shape.kind} has no corners")
    s = shape.copy()
    if shape.kind == 'rect':
        (x0, y0), (x1, y1) = shape.points
        s.kind = 'polygon'
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    else:
        pts = shape.points
    s.points = _round_corners(pts, r, how, s.kind == 'polygon', segments)
    return s


def alike(shapes, i, by):
    """The indices of the shapes like shape i by `by` (ALIKE: the same
    material, copper layer (a solid: any solid), net (copper), or kind);
    hidden ones left out."""
    s = shapes[i]
    if by == 'material':
        key = lambda t: (t.kind == 'port', t.material)
    elif by == 'layer':
        key = lambda t: ('solid',) if t.solid else ('layer', t.layer)
    elif by == 'net':
        key = lambda t: (t.material, t.net) if t.material == 'copper' and (
            t.kind != 'port') else None
    elif by == 'kind':
        key = lambda t: t.kind
    else:
        raise ValueError(f"alike by {by!r}: one of {', '.join(ALIKE)}")
    k = key(s)
    return [j for j, t in enumerate(shapes)
            if not t.hidden and k is not None and key(t) == k]
