"""
Copper drawn as shapes (rectangles, polygons, zones) turned into line strips for the line model, so
resonators drawn as polygons (hairpins, interdigital fingers, tapped feeds) become sections.

Only rectilinear shapes (every edge along x or y) are converted; others (a taper, a teardrop, a
rounded pad) keep widening the tracks they overlap (tlines.NetCopper). A shape is cut on the grid of
its vertex coordinates; each cell belongs to the direction in which the copper runs further through
it (its horizontal or vertical run), and connected cells of one direction form a strip: a centreline
through the middle of the strip (a jog where its middle moves), its width the narrowest place, its
own cells for the width along it. Where a strip ends on another, its centreline runs on to the
other's centreline, so lengths are counted to the middle of corners and tees (the convention of
centreline lengths); where both end there it is a corner.

    tracks = shape_strips(poly, 'F.Cu', 'RF')        # model coordinates; dicts like board.tracks
"""
import numpy as np
from shapely.geometry import Polygon, box, LineString, Point
from shapely.ops import unary_union

TOL = 1e-6


def rectilinear(poly, tol=1e-4):
    for ring in [poly.exterior] + list(poly.interiors):
        c = np.asarray(ring.coords)
        d = np.abs(np.diff(c, axis=0))
        if np.any((d[:, 0] > tol) & (d[:, 1] > tol)):
            return False
    return True


def _runs(mask, sizes):
    """Physical length of the contiguous run of True each entry is in (along axis 0 of mask)."""
    out = np.zeros(mask.shape)
    for j in range(mask.shape[1]):
        i = 0
        while i < mask.shape[0]:
            if not mask[i, j]:
                i += 1
                continue
            k = i
            while k + 1 < mask.shape[0] and mask[k + 1, j]:
                k += 1
            out[i:k + 1, j] = sizes[i:k + 1].sum()
            i = k + 1
    return out


def _components(cls, inside, want):
    """4-connected components of cells with cls == want."""
    seen = np.zeros(cls.shape, bool)
    comps = []
    for i0, j0 in zip(*np.nonzero(inside & (cls == want))):
        if seen[i0, j0]:
            continue
        stack, comp = [(i0, j0)], []
        seen[i0, j0] = True
        while stack:
            i, j = stack.pop()
            comp.append((i, j))
            for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if 0 <= a < cls.shape[0] and 0 <= b < cls.shape[1] and not seen[a, b] and inside[a, b] and cls[a, b] == want:
                    seen[a, b] = True
                    stack.append((a, b))
        comps.append(comp)
    return comps


class _Strip:
    def __init__(self, cells, axis, xs, ys):
        self.cells, self.axis = set(cells), axis          # axis 0: runs along x (horizontal), 1: along y
        g = (xs, ys)
        along, across = (0, 1) if axis == 0 else (1, 0)
        by = {}
        for c in cells:
            by.setdefault(c[along], []).append(c[across])
        self.slices = []                                   # (a0, a1, centre, thickness) along the strip
        for k in sorted(by):
            lo, hi = min(by[k]), max(by[k])
            c0, c1 = g[across][lo], g[across][hi + 1]
            self.slices.append((g[along][k], g[along][k + 1], 0.5 * (c0 + c1), c1 - c0))
        self.a0, self.a1 = self.slices[0][0], self.slices[-1][1]
        self.width = min(s[3] for s in self.slices)
        self.region = unary_union([box(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i, j in cells])
        self.ext = {0: None, 1: None}                      # centreline end moved to (start, end)

    def centre_at(self, a):
        for a0, a1, c, _ in self.slices:
            if a0 - TOL <= a <= a1 + TOL:
                return c
        return self.slices[0][2] if a < self.a0 else self.slices[-1][2]

    def pt(self, a, c):
        return (a, c) if self.axis == 0 else (c, a)

    def polyline(self):
        pts = []
        first = self.slices[0]
        start = self.ext[0] if self.ext[0] is not None else self.pt(self.a0, first[2])
        pts.append(start)
        if self.ext[0] is not None:                        # onto the other strip's centreline: then along
            pts.append(self.pt(self.a0, first[2])) if abs(self.ext[0][self.axis] - self.a0) > TOL else None
        prev_c = first[2]
        for a0, a1, c, _ in self.slices[1:]:
            if abs(c - prev_c) > TOL:                      # the middle moves: a jog
                pts += [self.pt(a0, prev_c), self.pt(a0, c)]
            prev_c = c
        end = self.ext[1] if self.ext[1] is not None else self.pt(self.a1, prev_c)
        if self.ext[1] is not None and abs(self.ext[1][self.axis] - self.a1) > TOL:
            pts.append(self.pt(self.a1, prev_c))
        pts.append(end)
        out = [pts[0]]
        for p in pts[1:]:
            if np.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > TOL:
                out.append(p)
        # drop points in the middle of straight runs
        keep = [out[0]]
        for i in range(1, len(out) - 1):
            a, b, c = np.asarray(keep[-1]), np.asarray(out[i]), np.asarray(out[i + 1])
            if abs((b - a)[0] * (c - b)[1] - (b - a)[1] * (c - b)[0]) > 1e-9:
                keep.append(out[i])
        keep.append(out[-1])
        return np.asarray(keep, float)


def shape_strips(poly, layer, net, aspect=1.0):
    """Strips (track dicts with 'region' and 'strip': True) of one rectilinear polygon (model mm)."""
    if poly.is_empty or not rectilinear(poly):
        return []
    xs = np.unique(np.round(np.asarray(poly.exterior.coords)[:, 0], 6))
    ys = np.unique(np.round(np.asarray(poly.exterior.coords)[:, 1], 6))
    for r in poly.interiors:
        xs = np.unique(np.r_[xs, np.round(np.asarray(r.coords)[:, 0], 6)])
        ys = np.unique(np.round(np.r_[ys, np.asarray(r.coords)[:, 1]], 6))
    nx, ny = len(xs) - 1, len(ys) - 1
    if nx < 1 or ny < 1:
        return []
    inside = np.zeros((nx, ny), bool)
    for i in range(nx):
        for j in range(ny):
            inside[i, j] = poly.contains(Point(0.5 * (xs[i] + xs[i + 1]), 0.5 * (ys[j] + ys[j + 1])))
    lh = _runs(inside, np.diff(xs))                        # horizontal run through each cell
    lv = _runs(inside.T, np.diff(ys)).T
    cls = np.where(lh * aspect >= lv, 0, 1)
    strips = [_Strip(c, 0, xs, ys) for c in _components(cls, inside, 0)] + \
             [_Strip(c, 1, xs, ys) for c in _components(cls, inside, 1)]
    owner = {}
    for k, s in enumerate(strips):
        for c in s.cells:
            owner[c] = k
    # strips meeting: the one ending there runs on to the other's centreline
    done = set()
    for k, s in enumerate(strips):
        for (i, j) in s.cells:
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                o = owner.get((i + di, j + dj))
                if o is None or o == k or (min(k, o), max(k, o)) in done:
                    continue
                done.add((min(k, o), max(k, o)))
                _join(s, strips[o], (i, j), (i + di, j + dj), xs, ys)
    out = []
    for s in strips:
        out.append(dict(layer=layer, net=net, width=float(s.width), pts=s.polyline(), region=s.region, strip=True))
    return out


def _join(s, o, cs, co, xs, ys):
    """Connect strips s and o, whose cells cs and co touch: a strip ending at the other (within half the
    other's thickness of the meeting point) runs on to the meeting point; both ending there is a
    corner, one is a T-junction (the other gets a node from tlines' junction split)."""
    if s.axis == o.axis:
        return                                             # same direction never touch (one component)
    h, v = (s, o) if s.axis == 0 else (o, s)
    ch, cv = (cs, co) if s.axis == 0 else (co, cs)
    xm = v.centre_at(0.5 * (ys[cv[1]] + ys[cv[1] + 1]))   # the vertical strip's middle at that row
    ym = h.centre_at(0.5 * (xs[ch[0]] + xs[ch[0] + 1]))   # the horizontal strip's middle at that column
    th = next(t for a0, a1, c, t in h.slices if a0 - TOL <= xs[ch[0]] + TOL and xs[ch[0] + 1] <= a1 + TOL) \
        if any(a0 - TOL <= xs[ch[0]] + TOL and xs[ch[0] + 1] <= a1 + TOL for a0, a1, c, t in h.slices) else h.width
    tv = next((t for a0, a1, c, t in v.slices if a0 - TOL <= ys[cv[1]] + TOL and ys[cv[1] + 1] <= a1 + TOL), v.width)
    for strip, a, t_other in ((h, xm, tv), (v, ym, th)):
        d0, d1 = abs(a - strip.a0), abs(a - strip.a1)
        if min(d0, d1) <= t_other / 2 + 1e-4:
            strip.ext[0 if d0 <= d1 else 1] = (xm, ym)
