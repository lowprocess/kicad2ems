"""
The Draw page's 3D view as numbers, with no Qt or OpenGL (so it is tested
where there is no display): pyqtgraph's GLViewWidget camera as matrices, the
ray under a pixel and the pixel of a point, camera presets and framing, the
drag of a point along one axis (the gizmo's, and X / Y / Z while dragging),
a clip plane through the meshes, a solid's outline and the working level's
grid.

    cam = dict(center=(x, y, z), distance=d, elevation=e, azimuth=a, fov=60)
    m = mvp(cam, w, h)                  # 4x4: model mm -> clip space
    o, d = ray(m, w, h, px, py)         # the ray under pixel (px, py)
    px, py = project(m, w, h, p)
    cam.update(preset('top', bounds, fov, w / h))  # bounds (x0, y0, z0, x1,
    cam.update(frame(bounds, cam, w / h))           #  y1, z1)
    s = along_axis(o, d, at, axis)      # how far along axis (0, 1, 2) from
                                        # `at` the ray passes nearest
    v, f = clip_mesh(v, f, axis, value, keep='below')
    g = clip_outline(polygon, axis, value, keep='below')
    v, f = prism_fast(polygon, z0, z1)  # core.shapes.prism, a convex one
                                        # quicker
    lines = prism_edges(polygon, z0, z1)        # (2k, 3) segment ends
    spec = grid_spec((x0, y0, x1, y1), step)    # centre, size, spacing

Camera angles are pyqtgraph's (degrees): elevation above the xy plane,
azimuth from +x anticlockwise to where the camera stands; fov the horizontal
field of view.
"""
import math
import numpy as np

__all__ = ['view_matrix', 'projection_matrix', 'mvp', 'ray', 'project',
           'PRESETS', 'preset', 'frame', 'along_axis', 'pixel_to_segment',
           'clip_mesh', 'clip_outline', 'clip_range', 'prism_fast',
           'prism_edges',
           'grid_spec']

# name: (elevation, azimuth): from above (x right, y up), from the front
# (looking along +y: x right, z up), from the right side (looking along -x:
# y right, z up), and a corner (x, y and z alike)
PRESETS = dict(top=(90.0, -90.0), front=(0.0, -90.0), side=(0.0, 0.0),
               iso=(math.degrees(math.atan(1 / math.sqrt(2))), -45.0))


def _rot(angle, axis):
    """4x4 rotation by `angle` degrees about `axis` (right-handed), as
    QMatrix4x4.rotate."""
    x, y, z = np.asarray(axis, float) / np.linalg.norm(axis)
    a = math.radians(angle)
    c, s, t = math.cos(a), math.sin(a), 1 - math.cos(a)
    m = np.eye(4)
    m[:3, :3] = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                 [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                 [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    return m


def _trans(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def view_matrix(center, distance, elevation, azimuth):
    """The camera's view matrix (pyqtgraph's GLViewWidget.viewMatrix, the
    euler rotation)."""
    cx, cy, cz = (float(v) for v in center)
    return (_trans(0, 0, -distance) @ _rot(elevation - 90, (1, 0, 0))
            @ _rot(azimuth + 90, (0, 0, -1)) @ _trans(-cx, -cy, -cz))


def projection_matrix(distance, fov, width, height):
    """The perspective (GLViewWidget.projectionMatrix over the whole view:
    near clip distance / 1000, far distance x 1000)."""
    n, f = distance * 0.001, distance * 1000.0
    r = n * math.tan(0.5 * math.radians(fov))
    t = r * height / width
    m = np.zeros((4, 4))
    m[0, 0], m[1, 1] = n / r, n / t
    m[2, 2], m[2, 3] = -(f + n) / (f - n), -2 * f * n / (f - n)
    m[3, 2] = -1.0
    return m


def mvp(cam, width, height):
    """projection @ view of camera `cam` (center, distance, elevation,
    azimuth, fov) on a view width x height pixels."""
    w, h = max(width, 1), max(height, 1)
    return (projection_matrix(cam['distance'], cam.get('fov', 60), w, h)
            @ view_matrix(cam['center'], cam['distance'], cam['elevation'],
                          cam['azimuth']))


def ray(m, width, height, x, y):
    """(origin, unit direction) of the ray under pixel (x, y) (from the top
    left), model mm; m: mvp."""
    inv = np.linalg.inv(m)
    nx, ny = 2 * x / width - 1, 1 - 2 * y / height
    a, b = (inv @ np.array([nx, ny, z, 1.0]) for z in (-1.0, 1.0))
    a, b = a[:3] / a[3], b[:3] / b[3]
    return a, (b - a) / np.linalg.norm(b - a)


def project(m, width, height, p):
    """The pixel (x, y) of model point p; m: mvp."""
    c = m @ np.array([*p, 1.0])
    c = c[:3] / c[3]
    return ((c[0] + 1) / 2 * width, (1 - c[1]) / 2 * height)


def frame(bounds, cam, aspect, margin=1.1):
    """dict(center, distance) that fit box `bounds` (x0, y0, z0, x1, y1, z1)
    in the view from where `cam` looks (its fov, and the view's width /
    height `aspect`): its bounding sphere inside the narrower field."""
    b = np.asarray(bounds, float)
    c = (b[:3] + b[3:]) / 2
    r = max(float(np.linalg.norm(b[3:] - b[:3])) / 2, 1e-3)
    hx = math.radians(cam.get('fov', 60)) / 2
    hy = math.atan(math.tan(hx) / max(aspect, 1e-6))
    return dict(center=tuple(float(v) for v in c),
                distance=margin * r / math.sin(min(hx, hy)))


def preset(name, bounds, fov=60.0, aspect=1.0):
    """The camera of PRESETS[name] (top, front, side, iso) fitting `bounds`:
    dict(center, distance, elevation, azimuth)."""
    e, a = PRESETS[name]
    cam = dict(elevation=e, azimuth=a, fov=fov)
    return dict(frame(bounds, cam, aspect), elevation=e, azimuth=a)


def along_axis(origin, direction, at, axis):
    """How far (mm, signed) along model axis `axis` (0 x, 1 y, 2 z) from
    point `at` the line through `at` comes nearest the ray: where a drag
    constrained to that axis puts the point. None when the ray runs along
    the axis."""
    u = np.zeros(3)
    u[axis] = 1.0
    o, d, p = (np.asarray(v, float) for v in (origin, direction, at))
    # closest points of p + s u and o + t d
    w = p - o
    b = u @ d
    den = 1.0 - b * b  # (|u| = |d| = 1)
    if den < 1e-9:
        return None
    return float((b * (d @ w) - (u @ w)) / den)


def pixel_to_segment(m, width, height, a, b, x, y):
    """How many pixels pixel (x, y) is from segment a-b (model points) as
    drawn, and where along it (0-1)."""
    pa, pb = (np.asarray(project(m, width, height, p)) for p in (a, b))
    q = np.array([x, y], float)
    ab = pb - pa
    L = ab @ ab
    t = 0.0 if L <= 0 else float(np.clip((q - pa) @ ab / L, 0, 1))
    return float(np.linalg.norm(pa + t * ab - q)), t


def clip_mesh(v, f, axis, value, keep='below'):
    """Triangles (vertices v (n, 3), faces f (m, 3)) cut by the plane
    model[axis] = value, the side `keep` ('below' or 'above') kept: (v, f),
    a triangle across the plane cut along it (no cap)."""
    t = np.asarray(v, float)[np.asarray(f, int)].reshape(-1, 3, 3)
    sgn = 1.0 if keep == 'below' else -1.0
    s = sgn * (t[:, :, axis] - value)  # <= 0: kept
    inside = s <= 0
    n_in = inside.sum(1)
    out = [t[n_in == 3]]
    for k_in in (1, 2):
        sel = n_in == k_in
        if not sel.any():
            continue
        tt, ss, ii = t[sel], s[sel], inside[sel]
        # turn each triangle so that its odd corner (alone on its side) is
        # first, keeping the winding
        odd = np.argmax(ii if k_in == 1 else ~ii, axis=1)
        r = (odd[:, None] + np.arange(3)[None]) % 3
        tt = np.take_along_axis(tt, r[:, :, None], 1)
        ss = np.take_along_axis(ss, r, 1)
        a, b, c = tt[:, 0], tt[:, 1], tt[:, 2]
        sa, sb, sc = ss[:, :1], ss[:, 1:2], ss[:, 2:]
        ab = a + (b - a) * (sa / (sa - sb))
        ac = a + (c - a) * (sa / (sa - sc))
        if k_in == 1:  # a alone kept
            out.append(np.stack([a, ab, ac], 1))
        else:  # a alone cut off: the quad ab, b, c, ac
            out += [np.stack([ab, b, c], 1), np.stack([ab, c, ac], 1)]
    t = np.concatenate(out) if out else np.zeros((0, 3, 3))
    return t.reshape(-1, 3), np.arange(3 * len(t)).reshape(-1, 3)


def clip_outline(g, axis, value, keep='below'):
    """Polygon g with what is past x = value (axis 0) or y = value (axis 1)
    taken off, or None when nothing is left (a prism of it is the clipped
    solid, capped)."""
    from shapely.geometry import box
    x0, y0, x1, y1 = g.bounds
    big = 1e6
    lo, hi = (-big, value) if keep == 'below' else (value, big)
    cut = (box(lo, y0 - 1, hi, y1 + 1) if axis == 0 else
           box(x0 - 1, lo, x1 + 1, hi))
    r = g.intersection(cut)
    if r.is_empty or r.area <= 1e-12:
        return None
    if r.geom_type != 'Polygon':
        from shapely.geometry import MultiPolygon
        parts = [q for q in getattr(r, 'geoms', []) if q.geom_type == 'Polygon'
                 and q.area > 1e-12]
        if not parts:
            return None
        r = parts[0] if len(parts) == 1 else MultiPolygon(parts)
    return r


def clip_range(z0, z1, value, keep='below'):
    """A solid's heights z0..z1 clipped at z = value, or None (all cut)."""
    lo, hi = sorted((float(z0), float(z1)))
    if keep == 'below':
        hi = min(hi, value)
    else:
        lo = max(lo, value)
    return None if hi - lo <= 1e-9 else (lo, hi)


def prism_fast(g, z0, z1):
    """core.shapes.prism (polygon g extruded from z0 to z1: vertices, faces,
    wound outward), its caps a fan from the first corner when g is convex
    with no holes (a via, a pad, a box: shapely's triangulation took three
    quarters of a 300-via 3D view's time); anything else, and a multipolygon
    part by part, is prism's."""
    from .shapes import prism
    if g.geom_type == 'MultiPolygon':
        ms = [prism_fast(p, z0, z1) for p in g.geoms if p.area > 1e-12]
        v = np.concatenate([m[0] for m in ms]) if ms else np.zeros((0, 3))
        return v, np.arange(len(v)).reshape(-1, 3)
    xy = np.asarray(g.exterior.coords, float)[:-1]
    if g.interiors or len(xy) < 3:
        return prism(g, z0, z1)
    e = np.roll(xy, -1, axis=0) - xy
    turn = e[:, 0] * np.roll(e, -1, axis=0)[:, 1] - e[:, 1] * np.roll(
        e, -1, axis=0)[:, 0]
    if turn.sum() < 0:  # clockwise: anticlockwise
        xy = xy[::-1]
        turn = -turn[::-1]
    if (turn < -1e-12 * max(float(np.abs(e).max()), 1e-9) ** 2).any():
        return prism(g, z0, z1)  # not convex
    z0, z1 = sorted((float(z0), float(z1)))
    n = len(xy)
    at = lambda p, z: np.c_[p, np.full(len(p), z)]
    a = np.repeat(xy[:1], n - 2, axis=0)
    b, c = xy[1:-1], xy[2:]
    q = np.roll(xy, -1, axis=0)
    tri = [np.stack([at(a, z1), at(b, z1), at(c, z1)], 1),  # top: up
           np.stack([at(a, z0), at(c, z0), at(b, z0)], 1),  # bottom: down
           np.stack([at(xy, z0), at(q, z0), at(q, z1)], 1),  # walls: out
           np.stack([at(xy, z0), at(q, z1), at(xy, z1)], 1)]
    v = np.concatenate(tri).reshape(-1, 3)
    return v, np.arange(len(v)).reshape(-1, 3)


def prism_edges(g, z0, z1):
    """The edges of polygon g extruded from z0 to z1, as line segments
    ((2k, 3) points in pairs): its rings top and bottom, and an upright at
    every corner where the outline turns (not along a circle's facets)."""
    segs = []
    for poly in getattr(g, 'geoms', [g]):
        for ring in [poly.exterior, *poly.interiors]:
            xy = np.asarray(ring.coords, float)
            a, b = xy[:-1], xy[1:]
            for z in (z0, z1):
                segs += [np.c_[a, np.full(len(a), z)],
                         np.c_[b, np.full(len(b), z)]]
            # uprights where the turn is sharp (> 30 degrees); a round one
            # (no such corner) four, a quarter round apart
            e = b - a
            prev = np.roll(e, 1, axis=0)
            cross = prev[:, 0] * e[:, 1] - prev[:, 1] * e[:, 0]
            dot = (prev * e).sum(1)
            turn = np.abs(np.degrees(np.arctan2(cross, dot)))
            k = a[turn > 30]
            if not len(k) and len(a):
                k = a[::max(1, len(a) // 4)][:4]
            segs += [np.c_[k, np.full(len(k), z0)],
                     np.c_[k, np.full(len(k), z1)]]
    if not segs:
        return np.zeros((0, 3))
    # interleave each segment's two ends
    out = []
    for k in range(0, len(segs), 2):
        p, q = segs[k], segs[k + 1]
        out.append(np.stack([p, q], 1).reshape(-1, 3))
    return np.concatenate(out)


def grid_spec(bounds, step, max_lines=120):
    """A grid over bounds (x0, y0, x1, y1) on the multiples of `step` (made
    coarser by 2, 5, 10... until it has at most max_lines lines a way):
    dict(center=(x, y), size=(sx, sy), spacing)."""
    x0, y0, x1, y1 = (float(v) for v in bounds)
    step = float(step) if step and step > 0 else 1.0
    span = max(x1 - x0, y1 - y0, step)
    k, mult = 0, (1, 2, 5)
    sp = step
    while span / sp > max_lines:
        k += 1
        sp = step * mult[k % 3] * 10 ** (k // 3)
    gx0, gy0 = math.floor(x0 / sp) * sp, math.floor(y0 / sp) * sp
    gx1, gy1 = math.ceil(x1 / sp) * sp, math.ceil(y1 / sp) * sp
    if gx1 - gx0 < sp:
        gx1 = gx0 + sp
    if gy1 - gy0 < sp:
        gy1 = gy0 + sp
    return dict(center=((gx0 + gx1) / 2, (gy0 + gy1) / 2),
                size=(gx1 - gx0, gy1 - gy0), spacing=sp)
