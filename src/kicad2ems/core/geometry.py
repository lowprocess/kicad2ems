"""
Polygon utilities: KiCad (y down) -> model (y up), polygon lists, hole splitting for CSXCAD.
"""
from shapely.geometry import Polygon, LineString
from shapely.ops import split


def polygons(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, 'geoms', []) if isinstance(p, Polygon) and not p.is_empty]


def flip_y(g):
    """KiCad (y down) -> y up."""
    from shapely import affinity
    return affinity.scale(g, 1.0, -1.0, origin=(0, 0))


def split_holes(p):
    """Polygon with holes -> list of hole-free polygons (CSXCAD polygons cannot have holes):
    cut with a vertical line through the first hole, recursively."""
    if not p.interiors:
        return [p]
    hx = Polygon(p.interiors[0]).representative_point().x
    y0, y1 = p.bounds[1] - 1, p.bounds[3] + 1
    parts = split(p, LineString([(hx, y0), (hx, y1)]))
    out = []
    for q in getattr(parts, 'geoms', [parts]):
        if isinstance(q, Polygon) and q.area > 1e-9:
            out += split_holes(q)
    return out
