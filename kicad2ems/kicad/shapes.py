"""
Geometry helpers for KiCad items: rotation, arcs and curves as polylines, graphic shapes (gr_* / fp_*)
and pad outlines (KiCad mm, y down).
"""
import numpy as np
from shapely.geometry import Polygon, Point, box, LineString
from shapely import affinity
from shapely.ops import unary_union
from . import sexpr as sx


def rot(v, deg):
    """Rotate a KiCad-coordinate vector by a KiCad angle (CCW on screen, y down)."""
    a = np.radians(deg)
    x, y = v
    return np.array([x * np.cos(a) + y * np.sin(a), -x * np.sin(a) + y * np.cos(a)])


def arc_points(p0, pm, p1, seg=0.1):
    """Points along the circular arc start-mid-end (KiCad mm); the first and last points are p0 and p1
    exactly, so arcs join the lines next to them."""
    p0, pm, p1 = map(lambda p: np.asarray(p, float), (p0, pm, p1))
    A = np.array([[p0[0] - pm[0], p0[1] - pm[1]], [p0[0] - p1[0], p0[1] - p1[1]]]) * 2
    b = np.array([p0 @ p0 - pm @ pm, p0 @ p0 - p1 @ p1])
    try:
        c = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return np.array([p0, p1])
    r = np.linalg.norm(p0 - c)
    a0, am, a1 = (np.arctan2(*(p - c)[::-1]) for p in (p0, pm, p1))

    def norm(a):
        return (a + 2 * np.pi) % (2 * np.pi)
    s1, sm = norm(a1 - a0), norm(am - a0)
    sweep = s1 if sm <= s1 else s1 - 2 * np.pi
    n = max(4, int(abs(sweep) * r / seg))
    t = a0 + sweep * np.linspace(0, 1, n + 1)
    pts = np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])
    pts[0], pts[-1] = p0, p1
    return pts


def arc_node_points(node):
    """Points of a gr_arc / fp_arc: start-mid-end (KiCad 6+), or centre (start), end point (end) and
    angle in degrees (KiCad 5)."""
    p0, pm, p1 = sx.xy(node, 'start'), sx.xy(node, 'mid'), sx.xy(node, 'end')
    if pm is not None:
        return arc_points(p0, pm, p1)
    ang = sx.val(node, 'angle', None, float)
    if ang is None or p0 is None or p1 is None:
        return np.array([p for p in (p0, p1) if p is not None])
    c, s = np.asarray(p0, float), np.asarray(p1, float)
    r = np.linalg.norm(s - c)
    a0 = np.arctan2(s[1] - c[1], s[0] - c[0])
    sweep = np.radians(ang)                                   # clockwise on screen = +angle with y down
    n = max(4, int(abs(sweep) * r / 0.1))
    t = a0 + sweep * np.linspace(0, 1, n + 1)
    return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])


def bezier_points(pts, n=24):
    """Cubic Bezier (4 control points) as a polyline."""
    p = np.asarray(pts, float)
    if len(p) != 4:
        return p
    t = np.linspace(0, 1, n + 1)[:, None]
    return (1 - t) ** 3 * p[0] + 3 * (1 - t) ** 2 * t * p[1] + 3 * (1 - t) * t ** 2 * p[2] + t ** 3 * p[3]


def graphic(node, filled_default=True):
    """Shapely geometry of a graphic item (gr_* / fp_* / pad primitive): line, arc, rect, circle, poly or
    curve, in the item's own coordinates. Strokes are buffered by half their width; closed shapes are
    filled unless their fill flag says otherwise (then only the stroke remains). None when it has no
    area (a zero-width line, an unfilled shape without a stroke)."""
    kind = node[0].split('_', 1)[-1]                          # gr_line -> line, fp_poly -> poly
    w = sx.width(node, 0.0)
    if kind in ('line', 'arc', 'curve'):
        if kind == 'line':
            pts = [sx.xy(node, 'start'), sx.xy(node, 'end')]
        elif kind == 'arc':
            pts = arc_node_points(node)
        else:
            pts = bezier_points(sx.pts(node))
        if w <= 0 or len(pts) < 2:
            return None
        return LineString(pts).buffer(w / 2, quad_segs=6)
    if kind == 'rect':
        (x0, y0), (x1, y1) = sx.xy(node, 'start'), sx.xy(node, 'end')
        shape = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    elif kind == 'circle':
        c, e = np.array(sx.xy(node, 'center')), np.array(sx.xy(node, 'end'))
        shape = Point(*c).buffer(np.linalg.norm(e - c), quad_segs=16)
    elif kind in ('poly', 'polygon'):
        pts = sx.pts(node)
        if len(pts) < 3:
            return None
        shape = Polygon(pts).buffer(0)
    else:
        return None
    if sx.filled(node, filled_default):
        return shape.buffer(w / 2, quad_segs=6) if w > 0 else shape
    return shape.exterior.buffer(w / 2, quad_segs=6) if w > 0 else None


def outline_ring(node):
    """The edge a graphic item draws (Edge.Cuts): a LineString / closed ring, whatever its width."""
    kind = node[0].split('_', 1)[-1]
    if kind == 'line':
        return LineString([sx.xy(node, 'start'), sx.xy(node, 'end')])
    if kind == 'arc':
        pts = arc_node_points(node)
        return LineString(pts) if len(pts) >= 2 else None
    if kind == 'curve':
        return LineString(bezier_points(sx.pts(node)))
    if kind == 'rect':
        (x0, y0), (x1, y1) = sx.xy(node, 'start'), sx.xy(node, 'end')
        return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)).exterior
    if kind == 'circle':
        c, e = np.array(sx.xy(node, 'center')), np.array(sx.xy(node, 'end'))
        return Point(*c).buffer(np.linalg.norm(e - c), quad_segs=16).exterior
    if kind in ('poly', 'polygon'):
        pts = sx.pts(node)
        return LineString(pts + pts[:1]) if len(pts) >= 3 else None
    return None


def _chamfer(shape, w, h, ratio, corners):
    """Cut 45 degree chamfers (ratio x the smaller side) off the named corners of a w x h pad."""
    c = ratio * min(w, h)
    if c <= 0:
        return shape
    cuts = {'top_left': (-w / 2, -h / 2, 1, 1), 'top_right': (w / 2, -h / 2, -1, 1),
            'bottom_left': (-w / 2, h / 2, 1, -1), 'bottom_right': (w / 2, h / 2, -1, -1)}
    for name in corners:
        if name in cuts:
            x, y, dx, dy = cuts[name]
            shape = shape.difference(Polygon([(x, y), (x + dx * c, y), (x, y + dy * c)]))
    return shape


def pad_shape(pad):
    """Pad outline in pad-local mm coordinates (before rotation/translation)."""
    shape = pad[3] if len(pad) > 3 else 'rect'
    sz = sx.xy(pad, 'size') or (0, 0)
    w, h = sz
    if shape == 'circle':
        return Point(0, 0).buffer(w / 2, quad_segs=8)
    if shape == 'oval':
        r = min(w, h) / 2
        L = LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)]) if w >= h else LineString([(0, -(h / 2 - r)), (0, h / 2 - r)])
        return L.buffer(r, quad_segs=8) if L.length > 0 else Point(0, 0).buffer(r, quad_segs=8)
    if shape == 'roundrect':
        rr = sx.val(pad, 'roundrect_rratio', 0.25, float) * min(w, h)
        g = (box(-w / 2 + rr, -h / 2 + rr, w / 2 - rr, h / 2 - rr).buffer(rr, quad_segs=4) if rr > 0
             else box(-w / 2, -h / 2, w / 2, h / 2))
        ch = sx.item(pad, 'chamfer')
        if ch is not None:
            g = _chamfer(g, w, h, sx.val(pad, 'chamfer_ratio', 0.2, float), [str(c) for c in ch[1:]])
        return g
    if shape == 'trapezoid':
        dx, dy = sx.xy(pad, 'rect_delta') or (0.0, 0.0)
        hx, hy, ex, ey = w / 2, h / 2, dx / 2, dy / 2
        return Polygon([(-hx - ey, hy + ex), (hx + ey, hy - ex), (hx - ey, -hy + ex), (-hx + ey, -hy - ex)]).buffer(0)
    if shape == 'custom':
        opts = sx.item(pad, 'options')
        anchor = sx.val(opts, 'anchor', 'rect') if opts else 'rect'
        g = []
        if w > 0:
            g.append(Point(0, 0).buffer(w / 2, quad_segs=8) if anchor == 'circle' else box(-w / 2, -h / 2, w / 2, h / 2))
        prims = sx.item(pad, 'primitives')
        for pr in (prims[1:] if prims else []):
            if isinstance(pr, list) and str(pr[0]).startswith('gr_'):
                q = graphic(pr, filled_default=True)
                if q is not None and not q.is_empty:
                    g.append(q)
        return unary_union(g) if g else Point(0, 0).buffer(0.05)
    return box(-w / 2, -h / 2, w / 2, h / 2)              # rect


def place(geom, origin, deg):
    """Rotate a local KiCad-mm geometry by a KiCad angle and translate to origin."""
    g = affinity.rotate(geom, -deg, origin=(0, 0))        # KiCad CCW-on-screen == CW in y-down maths
    return affinity.translate(g, *origin)
