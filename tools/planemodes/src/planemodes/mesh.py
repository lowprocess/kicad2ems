"""Triangle meshes of cavity regions (the Triangle library, from PyPI).

Cells are sized from the wavelength at fmax unless a maximum is given. Ground
vias are cut out as holes whose rim vertices are held at V = 0; port vias
become rings of mesh edges.

Accuracy is set by the copper's corners and slivers more than by the
wavelength. On the LibreVNA's F.Cu/In1.Cu (against a converged reference):
wavelength cells alone +5.9 % (median frequency error); with Triangle's 25
degree minimum angle +1.9 % in 13 s; also refining copper narrower than 1.2
mm to 0.3 mm +1.7 % in 46 s; 28 degrees, < 2 mm to 0.15 mm +1.0 % in 173 s.
"""
import math
from dataclasses import dataclass

import numpy as np
import shapely
import triangle
from scipy.spatial import cKDTree

C0 = 299_792_458.0


@dataclass
class MeshConfig:
    max_cell: float = 0.0              # mm; 0: lambda(fmax) / cells_per_wavelength
    cells_per_wavelength: float = 12.0
    min_angle: float = 25.0            # degrees (Triangle's quality bound; 0: off)
    refine_narrow: float = 0.0         # mm: copper narrower than this is refined (0: off)
    narrow_cell: float = 0.3           # mm, cell size there
    via_sides: int = 8                 # polygon sides of a via disc


@dataclass
class Mesh:
    nodes: np.ndarray                  # (N, 2) mm
    tris: np.ndarray                   # (M, 3) node indices, counter-clockwise
    fixed: np.ndarray                  # (N,) bool: on a ground via's rim (V = 0)
    ports: list                        # per cavity port: node indices of its ring
    cell: float                        # mm, the cell size used away from refinement


def cell_size(cfg, fmax, er):
    """The largest cell (mm) for modes up to `fmax` in a dielectric of `er`."""
    if cfg.max_cell > 0:
        return cfg.max_cell
    lam = C0 / (fmax * math.sqrt(er)) * 1e3
    return min(2.0, lam / cfg.cells_per_wavelength)


def _ngon(x, y, r, n):
    """A regular n-gon with the perimeter of the circle (x, y, r)."""
    r *= math.pi / (n * math.sin(math.pi / n))
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([x + r * np.cos(a), y + r * np.sin(a)])


def _pslg(region, rings, h):
    """Vertices, segments and hole points for Triangle: the region's outlines
    split at `h`, plus extra closed `rings` (port vias) as constraints."""
    loops = []
    for p in shapely.get_parts(region):
        for ring in [p.exterior, *p.interiors]:
            loops.append(np.asarray(shapely.segmentize(ring, h).coords)[:-1])
    loops += list(rings)
    verts, segs, n = [], [], 0
    for lp in loops:
        k = len(lp)
        if k < 3:
            continue
        verts.append(lp)
        i = np.arange(k) + n
        segs.append(np.column_stack([i, np.roll(i, -1)]))
        n += k
    V = np.vstack(verts)
    S = np.vstack(segs)
    # merge coincident vertices, drop degenerate and repeated segments
    V, inv = np.unique(np.round(V, 7), axis=0, return_inverse=True)
    S = np.sort(inv.reshape(-1)[S], axis=1)
    S = np.unique(S[S[:, 0] != S[:, 1]], axis=0)
    # one point inside every hole of the region (not inside an island in it)
    holes = []
    for p in shapely.get_parts(region):
        for ring in p.interiors:
            gap = shapely.Polygon(ring).difference(region)
            holes += [q.representative_point().coords[0]
                      for q in shapely.get_parts(gap) if q.area > 1e-9]
    return V, S, np.array(holes).reshape(-1, 2)


def mesh_cavity(cav, fmax, cfg=MeshConfig()):
    """Mesh a Cavity for modes up to `fmax` (Hz)."""
    h = cell_size(cfg, fmax, cav.er)
    region = cav.region.simplify(min(0.01, h / 20))
    if cav.shorts:                         # ground vias: holes in the region
        discs = [shapely.Polygon(_ngon(x, y, r, cfg.via_sides)) for x, y, r in cav.shorts]
        region = region.difference(shapely.union_all(discs))
    shapely.prepare(region)
    rings = [_ngon(x, y, r, cfg.via_sides) for x, y, r, _ in cav.ports]
    rings = [rg for rg in rings if region.contains(shapely.Polygon(rg))]
    V, S, H = _pslg(region, rings, h)
    q = f'q{cfg.min_angle:g}' if cfg.min_angle > 0 else ''
    data = dict(vertices=V, segments=S)
    if len(H):
        data['holes'] = H
    out = triangle.triangulate(data, f'p{q}a{math.sqrt(3) / 4 * h * h:.6g}Q')
    if cfg.refine_narrow > 0 and cfg.narrow_cell < h:
        out = _refine_narrow(out, region, cfg, h, q)
    nodes, tris = out['vertices'], out['triangles']
    tree = cKDTree(nodes)
    fixed = np.zeros(len(nodes), bool)
    grow = math.pi / (cfg.via_sides * math.sin(math.pi / cfg.via_sides))
    for x, y, r in cav.shorts:
        fixed[tree.query_ball_point((x, y), r * grow * 1.05 + 1e-6)] = True
    ports = []
    for x, y, r, _ in cav.ports:
        ids = tree.query_ball_point((x, y), r * grow * 1.05 + 1e-6)
        ports.append(np.array(ids or [tree.query((x, y))[1]], int))
    return Mesh(nodes, tris, fixed, ports, h)


def _refine_narrow(out, region, cfg, h, q):
    """Refine the triangles lying on copper narrower than cfg.refine_narrow."""
    w = cfg.refine_narrow / 2
    opened = region.buffer(-w, join_style='mitre').buffer(w, join_style='mitre')
    narrow = region.difference(opened).buffer(cfg.narrow_cell)
    if narrow.is_empty:
        return out
    shapely.prepare(narrow)
    cent = out['vertices'][out['triangles']].mean(1)
    fine = shapely.contains_xy(narrow, cent[:, 0], cent[:, 1])
    area = np.where(fine, math.sqrt(3) / 4 * cfg.narrow_cell ** 2, math.sqrt(3) / 4 * h * h)
    out = dict(out, triangle_max_area=area)
    return triangle.triangulate(out, f'rp{q}aQ')
