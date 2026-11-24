"""Geometry helpers the readers share: arcs, pad shapes, rotation.

Angles are degrees; a positive rotation turns +x towards -y on screen (KiCad's
y-down convention: counter-clockwise as drawn).
"""
import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

ARC_SEG = 0.1            # mm: chord length used to approximate arcs and circles


def arc_points(start, end, centre, cw):
    """Points along a circular arc from `start` to `end` about `centre`
    (cw: clockwise in the coordinates given)."""
    (x0, y0), (x1, y1), (xc, yc) = start, end, centre
    r = math.hypot(x0 - xc, y0 - yc)
    a0, a1 = math.atan2(y0 - yc, x0 - xc), math.atan2(y1 - yc, x1 - xc)
    if cw:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    if abs(x0 - x1) < 1e-9 and abs(y0 - y1) < 1e-9:   # a full circle
        a1 = a0 + (-2 * math.pi if cw else 2 * math.pi)
    n = max(4, int(abs(a1 - a0) * max(r, 1e-6) / ARC_SEG))
    a = np.linspace(a0, a1, n + 1)
    return np.column_stack([xc + r * np.cos(a), yc + r * np.sin(a)])


def arc3(start, mid, end):
    """Points along the arc through three points (KiCad's start/mid/end)."""
    (ax, ay), (bx, by), (cx, cy) = start, mid, end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:                                  # collinear: a line
        return np.array([start, end])
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay)
          + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx)
          + (cx**2 + cy**2) * (bx - ax)) / d
    # clockwise if the mid point lies on the clockwise path from start to end
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return arc_points(start, end, (ux, uy), cw=cross < 0)


def stroke(points, width, square=False):
    """A track: the polyline `points` widened to `width` (round ends)."""
    if width <= 0:
        return Polygon()
    if len(points) == 1 or np.allclose(points[0], points[-1]) and len(points) == 2:
        return Point(points[0]).buffer(width / 2, quad_segs=8)
    return LineString(points).buffer(width / 2, quad_segs=8,
                                     cap_style='square' if square else 'round')


def pad_shape(kind, w, h, rr=0.0):
    """A pad of size w x h centred at the origin: kind 'circle', 'rect',
    'oval', 'roundrect' (rr: corner radius as a fraction of the short side),
    'diamond', 'octagon'."""
    if kind == 'circle':
        return Point(0, 0).buffer(w / 2, quad_segs=8)
    if kind == 'oval':
        r = min(w, h) / 2
        dx, dy = (w / 2 - r, 0) if w >= h else (0, h / 2 - r)
        return LineString([(-dx, -dy), (dx, dy)]).buffer(r, quad_segs=8) if (dx or dy) \
            else Point(0, 0).buffer(r, quad_segs=8)
    if kind == 'roundrect' and rr > 0:
        r = min(rr * min(w, h), min(w, h) / 2 - 1e-6)
        return box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r).buffer(r, quad_segs=4)
    if kind == 'diamond':
        return Polygon([(w / 2, 0), (0, h / 2), (-w / 2, 0), (0, -h / 2)])
    if kind == 'octagon':
        c = min(w, h) * (1 - 1 / (1 + math.sqrt(2))) / 2
        return Polygon([(-w / 2 + c, -h / 2), (w / 2 - c, -h / 2), (w / 2, -h / 2 + c),
                        (w / 2, h / 2 - c), (w / 2 - c, h / 2), (-w / 2 + c, h / 2),
                        (-w / 2, h / 2 - c), (-w / 2, -h / 2 + c)])
    return box(-w / 2, -h / 2, w / 2, h / 2)            # rect, trapezoid, custom


def place(geom, x, y, angle=0.0, mirror_x=False):
    """`geom` (made at the origin) mirrored, rotated by `angle` and moved to
    (x, y)."""
    if mirror_x:
        geom = affinity.scale(geom, -1, 1, origin=(0, 0))
    if angle:
        geom = affinity.rotate(geom, -angle, origin=(0, 0))   # y down: CCW as drawn
    return affinity.translate(geom, x, y)


def clean(geom):
    """A valid polygonal geometry (slits and self-touching rings repaired)."""
    if geom is None or geom.is_empty:
        return Polygon()
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    polys = [g for g in getattr(geom, 'geoms', [geom])
             if g.geom_type in ('Polygon', 'MultiPolygon')]
    return shapely.union_all(polys) if polys else Polygon()


def ring_polygon(points):
    """A polygon from one closed outline that may be fractured: holes joined
    to the outline by zero-width slits, as KiCad saves zone fills. The
    outline is split into its faces and a face kept if the outline winds
    round it an odd number of times (a slit is crossed twice and cancels)."""
    pts = np.asarray(points, float)
    if len(pts) < 3:
        return Polygon()
    poly = Polygon(pts)
    if poly.is_valid:
        return poly
    closed = np.vstack([pts, pts[:1]])
    faces = shapely.get_parts(shapely.polygonize(shapely.get_parts(
        shapely.union_all(LineString(closed)))))
    x0, y0, x1, y1 = closed[:-1, 0], closed[:-1, 1], closed[1:, 0], closed[1:, 1]
    keep = []
    for f in faces:
        p = f.representative_point()
        span = (y0 > p.y) != (y1 > p.y)                   # edges straddling the ray
        xc = x0[span] + (p.y - y0[span]) * (x1[span] - x0[span]) / (y1[span] - y0[span])
        if np.count_nonzero(xc > p.x) % 2:
            keep.append(f)
    return shapely.union_all(keep) if keep else Polygon()


def polygonize_outline(lines):
    """The board outline from its edge segments ([(x0, y0, x1, y1)] or point
    lists): the polygons they enclose, holes subtracted (cut-outs)."""
    segs = []
    for pts in lines:
        pts = np.round(np.asarray(pts, float), 4)       # join near-coincident ends
        if len(pts) >= 2:
            segs.append(LineString(pts))
    if not segs:
        return None
    polys = list(shapely.get_parts(shapely.polygonize(shapely.get_parts(
        shapely.union_all(segs)))))
    if not polys:
        return None
    polys.sort(key=lambda p: -p.area)
    out = polys[0]
    for p in polys[1:]:                 # inner loops are cut-outs, outer are islands
        out = out.difference(p) if out.contains(p) else out.union(p)
    return out
