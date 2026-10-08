"""
mom25d_geo.py - geometry helpers for the multilayer solver

  * LinePort      : wave port on a straight feed in any in-plane direction (KiCad traces at 45 deg etc.)
  * mesh_polygons : constrained-Delaunay mesh of arbitrary polygons (holes allowed) with
                    - graded edge refinement (inward offset rings, like the tensor mesher's cosine grading)
                    - port gap/cut lines inserted as constraint segments (so RWG edges lie on them)
                    - via points inserted as vertices, with a small ring of radius = via radius around
                      them so the via 'fan' (triangles sharing the via vertex) matches the via footprint
  * to_geometry   : rectangles / shapely geometry -> one cleaned shapely (Multi)Polygon
"""
import numpy as np
import shapely
from shapely.geometry import (Polygon, MultiPolygon, LineString, MultiLineString, Point, box)
from shapely.ops import unary_union
import triangle as tr


# =============================================================================
# Ports in arbitrary directions
# =============================================================================
class LinePort:
    """
    Wave port on a straight feed line.
      layer     : metal interface index
      center    : (x, y) of the gap (delta-gap source) on the feed centre line
      direction : vector pointing INTO the device along the feed (any angle)
      width     : feed width (the gap and sample cuts span this, centred on `center`)
      ref_dist  : distance from the gap to the reference plane, along `direction`
      s0, step  : closest sample cut to the gap, and spacing between cuts
      s1        : farthest sample cut (default = ref_dist). Keep the cuts clear of discontinuities:
                  the reference plane may lie beyond s1 (the fitted waves are shifted to it).
      zc        : reference impedance
    """
    kind = 'wave'

    def __init__(self, layer, center, direction, width, ref_dist, s0=None, step=None, zc=50.0, name='',
                 s1=None):
        self.s1 = s1
        self.layer = layer
        self.c = np.asarray(center, float)
        d = np.asarray(direction, float)
        self.d = d / np.linalg.norm(d)
        self.t = np.array([-self.d[1], self.d[0]])
        self.w, self.ref, self.s0, self.step, self.zc, self.name = width, ref_dist, s0, step, zc, name

    # compatibility with the axis-aligned Port API used by the plotting code
    @property
    def dvec(self):
        return self.d

    def line(self, s):
        """End points of the transverse line at distance s from the gap (s=0: the gap)."""
        p = self.c + self.d * s
        return p - self.t * self.w / 2, p + self.t * self.w / 2

    @property
    def axis_aligned(self):
        return abs(self.d[0]) < 1e-12 or abs(self.d[1]) < 1e-12


def port_from_axis(p):
    """Convert a legacy mom25d.Port / MLPort (axis, pos, span, dir) into a LinePort."""
    tmid = 0.5 * (p.span[0] + p.span[1])
    w = p.span[1] - p.span[0]
    if p.axis == 'x':
        c, d = (p.pos, tmid), (p.dir, 0.0)
    else:
        c, d = (tmid, p.pos), (0.0, p.dir)
    lp = LinePort(p.layer, c, d, w, p.ref, s0=p.s0, step=p.step, zc=p.zc, name=p.name)
    return lp


# =============================================================================
# Geometry
# =============================================================================
def to_geometry(shapes):
    """List of rectangles (x0,y0,x1,y1) and/or shapely geometries -> cleaned (Multi)Polygon."""
    gs = []
    for s in shapes:
        if isinstance(s, (tuple, list)) and len(s) == 4 and np.isscalar(s[0]):
            gs.append(box(*s))
        else:
            gs.append(s)
    g = unary_union(gs).buffer(0)
    return g


def is_manhattan_rects(shapes):
    return all(isinstance(s, (tuple, list)) and len(s) == 4 and np.isscalar(s[0]) for s in shapes)


def _rings(g):
    polys = [g] if isinstance(g, Polygon) else list(getattr(g, 'geoms', []))
    out = []
    for p in polys:
        if p.is_empty or not isinstance(p, Polygon):
            continue
        out.append(LineString(p.exterior.coords))
        out += [LineString(r.coords) for r in p.interiors]
    return out


def edge_offsets(w_ref, ncross, grade=1.0):
    """Inward offsets mimicking the tensor mesher's graded cells across a strip of width w_ref."""
    k = np.arange(1, ncross)
    u = k / ncross
    u = (1 - grade) * u + grade * (1 - np.cos(np.pi * u)) / 2
    d = w_ref * u
    return [x for x in d if x < 0.5 * w_ref - 1e-12]


# =============================================================================
# Mesher
# =============================================================================
def mesh_polygons(geom, dmax, w_ref, ncross=6, grade=1.0, lines=(), vias=(), amax=None,
                  max_offset_frac=0.45, tol=1e-9):
    """
    geom   : shapely (Multi)Polygon of one metal layer
    dmax   : max edge length along conductors / in open regions
    w_ref  : reference strip width for the edge grading (narrowest trace on the layer)
    lines  : [(p0, p1), ...] constraint segments (port gap and cut lines), clipped to geom
    vias   : [(x, y, radius), ...] points to insert as vertices (+ an octagon of the via radius)
    Returns nodes (N,2), tris (M,3) counter-clockwise, and the list of via vertex ids.
    """
    if geom.is_empty:
        return np.zeros((0, 2)), np.zeros((0, 3), int), []
    linework = _rings(geom)
    for d in edge_offsets(w_ref, ncross, grade):
        inner = geom.buffer(-d, join_style='mitre', mitre_limit=2.0)
        if not inner.is_empty:
            linework += _rings(inner)
    for p0, p1 in lines:
        seg = LineString([p0, p1]).intersection(geom.buffer(tol))
        if not seg.is_empty:
            linework.append(seg)
    vpts = []
    for (x, y, r) in vias:
        vpts.append((x, y))
        if r > 0:
            ring = Point(x, y).buffer(r, quad_segs=2).exterior   # octagon
            linework.append(LineString(ring.coords).intersection(geom.buffer(tol)))
    noded = unary_union([g for g in linework if not g.is_empty])
    pieces = list(getattr(noded, 'geoms', [noded]))
    verts, segs, vid = [], [], {}

    def key(p):
        return (round(p[0] / tol), round(p[1] / tol))

    def add(p):
        k = key(p)
        if k not in vid:
            vid[k] = len(verts)
            verts.append((p[0], p[1]))
        return vid[k]

    for pc in pieces:
        if pc.length < tol:
            continue
        pc = shapely.segmentize(pc, dmax)
        cs = list(pc.coords)
        ids = [add(c) for c in cs]
        segs += [(a, b) for a, b in zip(ids[:-1], ids[1:]) if a != b]
    via_ids = [add(p) for p in vpts]
    # Triangle's switch parser cannot read exponents ('e' is a switch), so work in units of dmax
    org = np.array(verts).mean(0)
    A = dict(vertices=(np.array(verts) - org) / dmax, segments=np.array(segs))
    amax = (amax or 0.5 * dmax ** 2) / dmax ** 2
    T = tr.triangulate(A, f"pQa{amax:.8f}")
    nodes, tris = T['vertices'] * dmax + org, T['triangles']
    cent = nodes[tris].mean(1)
    keep = shapely.contains_xy(geom.buffer(tol), cent[:, 0], cent[:, 1])
    tris = tris[keep]
    # drop unused nodes, fix orientation
    used = np.unique(tris)
    remap = -np.ones(len(nodes), int)
    remap[used] = np.arange(len(used))
    nodes, tris = nodes[used], remap[tris]
    via_ids = [int(remap[i]) if i < len(remap) and remap[i] >= 0 else -1 for i in via_ids]
    V = nodes[tris]
    ar = (V[:, 1, 0] - V[:, 0, 0]) * (V[:, 2, 1] - V[:, 0, 1]) - (V[:, 1, 1] - V[:, 0, 1]) * (V[:, 2, 0] - V[:, 0, 0])
    flip = ar < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    good = np.abs(ar) > 1e-30
    return nodes, tris[good], via_ids
