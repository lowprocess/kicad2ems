"""
Board outline from the Edge.Cuts layer.
"""
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union, polygonize


def board_outline(board, with_holes=False):
    """Board outline polygon (KiCad mm, y down) from every Edge.Cuts item - lines, arcs, rectangles,
    polygons, circles and curves, on the board and inside footprints - or None. The pieces are snapped
    to a 1 nm grid, joined and polygonized; the largest face is the board. Cutouts (mounting holes,
    slots: the faces inside it) are removed when with_holes, else only the outer edge is returned."""
    if not board.edges:
        return None
    lines = shapely.set_precision(unary_union(board.edges), 1e-6)
    faces = [p for p in polygonize(unary_union(lines)) if p.area > 0]
    if not faces:
        return None
    outer = Polygon(max(faces, key=lambda p: Polygon(p.exterior).area).exterior)
    if not with_holes:
        return outer
    holes = [Polygon(p.exterior) for p in faces if p.representative_point().within(outer)
             and Polygon(p.exterior).area < outer.area]
    return outer.difference(unary_union(holes)) if holes else outer
