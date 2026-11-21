"""
Board outline from the Edge.Cuts layer.
"""
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union, polygonize


def board_outline(board, with_holes=False):
    """Board outline polygon (KiCad mm, y down) from every Edge.Cuts item -
    lines, arcs, rectangles, polygons, circles and curves, on the board and
    inside footprints - or None. The pieces are snapped to a 1 nm grid, joined
    and polygonized; the largest face is the board. Cutouts (mounting holes,
    slots: the faces inside it) are removed when with_holes, else only the outer
    edge is returned."""
    if not board.edges:
        return None
    edges = list(board.edges) + _bridges(board.edges)
    lines = shapely.set_precision(unary_union(edges), 1e-6)
    faces = [p for p in polygonize(unary_union(lines)) if p.area > 0]
    if not faces:
        return None
    # the face the parts are on, the largest of those (a panel's frame and the
    # strips between it and the board have none: a LimeRFE panel's outline had
    # been a 91 x 3 mm strip between them)
    from shapely.geometry import Point
    pts = [Point(f['x'], f['y']) for f in getattr(board, 'footprints', [])
           if 'x' in f and 'y' in f]
    on = lambda p: sum(Polygon(p.exterior).contains(q) for q in pts)
    outer = Polygon(max(faces, key=lambda p: (on(p), Polygon(p.exterior).area))
                    .exterior)
    if not with_holes:
        return outer
    holes = [
        Polygon(p.exterior)
        for p in faces
        if p.representative_point().within(outer)
        and Polygon(p.exterior).area < outer.area
    ]
    return outer.difference(unary_union(holes)) if holes else outer


# loose ends of the outline closer than this are joined (mm): a KiCad 4 board's
# edges drawn by hand met within 0.01-0.06 mm
BRIDGE = 0.1


def _bridges(edges):
    """Short segments joining each loose end of the outline (an end no other
    edge shares) to the nearest other loose end within BRIDGE."""
    import collections
    import numpy as np
    from shapely.geometry import LineString
    ends = collections.Counter()
    for e in edges:
        c = list(getattr(e, 'coords', []))
        if len(c) >= 2:
            for q in (c[0], c[-1]):
                ends[(round(q[0], 6), round(q[1], 6))] += 1
    loose = np.array([q for q, k in ends.items() if k == 1])
    out, used = [], set()
    for i, q in enumerate(loose):
        if i in used:
            continue
        d = np.hypot(*(loose - q).T)
        d[i] = np.inf
        for j in sorted(used):
            d[j] = np.inf
        j = int(np.argmin(d)) if len(d) else -1
        if j >= 0 and d[j] <= BRIDGE:
            out.append(LineString([tuple(q), tuple(loose[j])]))
            used.update((i, j))
    return out
