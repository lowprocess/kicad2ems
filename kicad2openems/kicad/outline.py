"""
Board outline from the Edge.Cuts layer.
"""
import numpy as np
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union, linemerge, polygonize
from . import sexpr as sx
from . import shapes as shp


def board_outline(board):
    """Board outline polygon from Edge.Cuts (KiCad mm, y down), or None."""
    segs = []
    for tag in ('gr_line', 'gr_arc', 'gr_rect', 'gr_poly', 'gr_circle'):
        for g in sx.items(board.root, tag):
            if 'Edge.Cuts' not in sx.layers(g):
                continue
            if tag == 'gr_line':
                segs.append(LineString([sx.xy(g, 'start'), sx.xy(g, 'end')]))
            elif tag == 'gr_arc':
                segs.append(LineString(shp.arc_points(sx.xy(g, 'start'), sx.xy(g, 'mid'), sx.xy(g, 'end'))))
            elif tag == 'gr_rect':
                (x0, y0), (x1, y1) = sx.xy(g, 'start'), sx.xy(g, 'end')
                return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            elif tag == 'gr_poly':
                pts = [(float(p[1]), float(p[2])) for p in sx.items(g, 'pts')[0][1:] if p[0] == 'xy'] \
                    if sx.items(g, 'pts') else []
                if len(pts) >= 3:
                    return Polygon(pts).buffer(0)
            else:
                c, e = np.array(sx.xy(g, 'center')), np.array(sx.xy(g, 'end'))
                return Point(*c).buffer(np.linalg.norm(e - c), quad_segs=16)
    if not segs:
        return None
    # snap nearly-touching ends, then polygonize the closed loops
    merged = linemerge(unary_union([s for s in segs]))
    polys = list(polygonize(merged))
    if not polys:
        return None
    return max(polys, key=lambda p: p.area)
