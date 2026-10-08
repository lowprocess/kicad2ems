"""
Geometry helpers for KiCad items: rotation, arcs as polylines, pad outlines (KiCad mm, y down).
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
    """Points along the circular arc start-mid-end (KiCad mm)."""
    p0, pm, p1 = map(np.asarray, (p0, pm, p1))
    A = np.array([[p0[0] - pm[0], p0[1] - pm[1]], [p0[0] - p1[0], p0[1] - p1[1]]]) * 2
    b = np.array([p0 @ p0 - pm @ pm, p0 @ p0 - p1 @ p1])
    try:
        c = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return np.array([p0, p1])
    r = np.linalg.norm(p0 - c)
    a0, am, a1 = (np.arctan2(*(p - c)[::-1]) for p in (p0, pm, p1))
    # choose the sweep that passes through the mid point
    def norm(a): return (a + 2 * np.pi) % (2 * np.pi)
    s1 = norm(a1 - a0); sm = norm(am - a0)
    sweep = s1 if sm <= s1 else s1 - 2 * np.pi
    n = max(4, int(abs(sweep) * r / seg))
    t = a0 + sweep * np.linspace(0, 1, n + 1)
    return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])


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
        return box(-w / 2 + rr, -h / 2 + rr, w / 2 - rr, h / 2 - rr).buffer(rr, quad_segs=4) if rr > 0 else box(-w / 2, -h / 2, w / 2, h / 2)
    if shape == 'custom':
        g = [box(-w / 2, -h / 2, w / 2, h / 2)] if w > 0 else []
        prims = sx.item(pad, 'primitives')
        for pr in (prims[1:] if prims else []):
            if pr[0] in ('gr_poly', 'fp_poly'):
                pts = [(float(p[1]), float(p[2])) for p in sx.item(pr, 'pts')[1:] if p[0] == 'xy']
                wid = sx.val(pr, 'width', 0, float)
                poly = Polygon(pts)
                g.append(poly.buffer(wid / 2) if wid else poly)
            elif pr[0] in ('gr_circle',):
                c = np.array(sx.xy(pr, 'center')); e = np.array(sx.xy(pr, 'end'))
                g.append(Point(*c).buffer(np.linalg.norm(e - c), quad_segs=8))
            elif pr[0] in ('gr_line',):
                g.append(LineString([sx.xy(pr, 'start'), sx.xy(pr, 'end')]).buffer(sx.val(pr, 'width', 0.1, float) / 2))
        return unary_union(g) if g else Point(0, 0).buffer(0.05)
    return box(-w / 2, -h / 2, w / 2, h / 2)              # rect, trapezoid (approx.)


def place(geom, origin, deg):
    """Rotate a local KiCad-mm geometry by a KiCad angle and translate to origin."""
    g = affinity.rotate(geom, -deg, origin=(0, 0))        # KiCad CCW-on-screen == CW in y-down maths
    return affinity.translate(g, *origin)
