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

Coordinates are the model's (mm, y up), as core.shapes's; angles are degrees,
anticlockwise.
"""
import json, math
from dataclasses import dataclass
import numpy as np

__all__ = ['bounds_of', 'centre_of', 'rotated', 'mirrored', 'aligned',
           'distributed', 'array_row', 'array_grid', 'array_circle',
           'to_clipboard', 'from_clipboard', 'Snap', 'Snapper', 'measure',
           'ALIGN']

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
