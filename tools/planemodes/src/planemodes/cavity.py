"""Plane-pair cavities: the region where two copper layers both carry
reference (ground) copper, the dielectric between them, and the vias.

While the planes are close against the wavelength, the field between them is
TEM: one voltage V(x, y) between the planes, -div grad V = k^2 V over the
region, open (dV/dn = 0) at its edges. A via joining both planes' reference
copper is a short (V = 0 on its disc); any other via through the dielectric is
a port, where a signal drives the cavity.
"""
import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import Point

REF_NET = re.compile(r'^(A|D|P|S)?(GND|VSS|GROUND|0V)(_.*)?$', re.I)


def guess_ref_nets(board):
    """Nets that look like ground: GND, AGND, VSS, GND_RF, ..."""
    return [n for n in board.nets() if REF_NET.match(n)]


@dataclass
class Cavity:
    top: str
    bottom: str
    d: float                  # mm between the planes
    er: float
    tand: float
    region: object            # shapely geometry (mm): both planes' reference copper
    shorts: list = field(default_factory=list)   # [(x, y, r)] ground vias
    ports: list = field(default_factory=list)    # [(x, y, r, net)] other vias
    outer: bool = False       # one plane is an outer layer: its edges fringe into air
    board_edge: object = None # the board outline's boundary (where buried pairs radiate)

    @property
    def name(self):
        return f'{self.top}/{self.bottom}'


def coverage(board, ref_nets):
    """{copper layer: share of the board area its reference copper covers}."""
    x0, y0, x1, y1 = board.bounds()
    area = board.outline.area if board.outline is not None else (x1 - x0) * (y1 - y0)
    return {l: board.layer_copper(l, set(ref_nets)).area / max(area, 1e-9)
            for l in board.copper_layers}


def plane_pairs(board, ref_nets, min_cover=0.3):
    """Adjacent copper layers that both have reference copper over at least
    `min_cover` of the board: the candidate cavities, top down."""
    cov = coverage(board, ref_nets)
    cl = board.copper_layers
    return [(a, b) for a, b in zip(cl, cl[1:])
            if cov[a] >= min_cover and cov[b] >= min_cover]


def make_cavity(board, top, bottom, ref_nets, hole_fill=2.0):
    """The cavity between copper layers `top` and `bottom`. Holes under
    `hole_fill` mm^2 (antipads, small voids) are filled: they barely change a
    plane's inductance, and a via in one becomes a port on the copper."""
    ref = set(ref_nets)
    a, b = board.layer_copper(top, ref), board.layer_copper(bottom, ref)
    region = _fill_holes(a.intersection(b), hole_fill)
    region = shapely.union_all([p for p in shapely.get_parts(region)
                                if p.geom_type == 'Polygon' and p.area > 0.05])
    d, er, tand = board.gap(top, bottom)
    cl = board.copper_layers
    cav = Cavity(top, bottom, d, er, tand, region,
                 outer=cl[0] in (top, bottom) or cl[-1] in (top, bottom),
                 board_edge=board.outline.boundary if board.outline is not None else None)
    if region.is_empty:
        return cav
    shapely.prepare(region)
    for v in board.vias:
        if not board.via_spans(v, top, bottom):
            continue
        if not region.covers(Point(v.x, v.y)):
            continue
        r = max(v.drill / 2, 0.05)
        if v.net in ref:
            cav.shorts.append((v.x, v.y, r))
        else:
            cav.ports.append((v.x, v.y, r, v.net))
    cav.shorts = _merge_close(cav.shorts)
    return cav


def _fill_holes(geom, max_area):
    """`geom` with its interior holes smaller than `max_area` filled."""
    out = []
    for p in shapely.get_parts(geom):
        if p.geom_type != 'Polygon':
            continue
        keep = [r for r in p.interiors if shapely.Polygon(r).area >= max_area]
        out.append(shapely.Polygon(p.exterior, keep))
    return shapely.union_all(out) if out else shapely.Polygon()


def _merge_close(shorts):
    """Ground vias whose discs overlap merged into the larger one (they are
    one hole electrically, and overlapping discs make slivers the mesher
    cannot fill)."""
    if len(shorts) < 2:
        return shorts
    from scipy.spatial import cKDTree
    xy = np.array([s[:2] for s in shorts]); r = np.array([s[2] for s in shorts])
    keep = np.ones(len(shorts), bool)
    for i, j in cKDTree(xy).query_pairs(2.2 * r.max()):
        if keep[i] and keep[j] and np.hypot(*(xy[i] - xy[j])) < 1.1 * (r[i] + r[j]):
            keep[j if r[j] <= r[i] else i] = False
    return [s for s, k in zip(shorts, keep) if k]
