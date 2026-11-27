"""
Drawn metal's own loss in the FDTD model (--sigma). core.solids' metal pieces
are solid PEC (model._solids); with a conductivity, the faces of them that the
field reaches are conducting sheets on the mesh, as thick copper's faces are
(thick.py): each face one side of a conductor, sigma / 4 and twice the
thickness in openEMS's sheet model, whose impedance is then k / (sigma tanh(k
t)), k = (1 + j) / delta: (1 + j) / (sigma delta) for t of many skin depths.

    fs = faces(ps, lines, zm, fine, box)   # [Face(axis, c, geom, two, t, magnet)]

Which faces. The drawn metal is cut at its heights into slabs; in each slab the
metal of one kind (a magnet's, of its own conductivity, or any other) is a
union of polygons in plan:
  - at each height, the metal below not under more metal faces up and the
    metal above not over more metal faces down (z faces, polygons in plan);
  - in each slab, its union's straight sides along x or y (an edge `fine` long
    or more and within fine / 10 of the axis, as the mesh's lines take them)
    not against the other kind's metal (x and y faces, rectangles);
  - none on the box's planes (an enclosure's outer faces: the mesh ends there
    and there is no field outside);
  - curved and oblique sides are left out: their staircase stays PEC (a post,
    a round can's wall), as vias are.

Each face is put on the mesh line of the body's outermost tangential edges (the
last line inside the metal, not the face's own where a merge moved it, as
openEMS and gfdtd take a body's edges by their midpoints) and is left out
where no line is inside. Two faces of opposite sides on one line (a wall or a
plate one cell thick or less) are one sheet between field on both sides (two:
openEMS's sheet of the conductor's thickness t, sigma in full).
"""
from dataclasses import dataclass

import numpy as np

# mm: a coordinate on a line
TOL = 1e-6
# mm2: a face smaller than this is dropped
AREA_MIN = 1e-6


@dataclass
class Face:
    """A conducting sheet on a drawn metal's faces: normal to `axis` at c (a
    mesh line, mm in the mesh's heights); geom (shapely) in plan (x, y) for a
    z face, in (t, z) for an x face (t = y) or a y face (t = x); two: both
    sides of a wall or plate on one line, t (mm) the metal's thickness there;
    magnet: a magnet's (core.solids.MAGNET_SIGMA)."""
    axis: int
    c: float
    geom: object
    two: bool = False
    t: float = 0.0
    magnet: bool = False


def body(p, zm, zl):
    """Whether metal piece p is a body on the mesh (model._solids: a z line
    across its heights), not a sheet on its nearest line."""
    z0, z1 = zm(p.z0), zm(p.z1)
    return bool(z1 - z0 >= 1e-9 and np.any((zl >= z0 - 1e-6)
                                           & (zl <= z1 + 1e-6)))


def _snap(ln, c, sign):
    """The last line at or under c (sign +1: the metal below c), or the first
    at or over it (-1), or None."""
    if sign > 0:
        k = np.flatnonzero(ln <= c + TOL)
        return float(ln[k[-1]]) if len(k) else None
    k = np.flatnonzero(ln >= c - TOL)
    return float(ln[k[0]]) if len(k) else None


def _intervals(g):
    """(t0, t1) of a shapely line geometry's pieces."""
    out = []
    for q in getattr(g, 'geoms', [g]):
        if q.is_empty or q.length <= TOL:
            continue
        c = np.asarray(q.coords, float)
        out.append(c)
    return out


def faces(ps, lines, zm=lambda z: z, fine=None, box=None):
    """The drawn metal's faces the field reaches as [Face] (the module's
    rules): ps core.solids pieces, lines the mesh's [x, y, z] (z in the
    mesh's heights), zm a board height in the mesh's (the thick-copper plan),
    fine the mesh's fine cell (sides straighter than fine / 10 and at least
    that long), box (x0, y0, z0, x1, y1, z1) board heights: faces on its
    planes left out (an enclosure's outer ones)."""
    from shapely.geometry import LineString, Polygon, box as sbox
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    from kicad2ems.core.geometry import polygons
    ln = [np.asarray(v, float) for v in lines]
    mine = [p for p in ps if p.material == 'metal' and body(p, zm, ln[2])]
    if not mine:
        return []
    fine = fine or 0.0
    Z = {id(p): (zm(p.z0), zm(p.z1)) for p in mine}
    zs = sorted({round(z, 9) for p in mine for z in Z[id(p)]})
    zs = [z for k, z in enumerate(zs) if k == 0 or z - zs[k - 1] > 1e-9]
    slabs = list(zip(zs[:-1], zs[1:]))
    empty = Polygon()

    def union(sel, a, b):
        g = [p.polygon for p in sel
             if Z[id(p)][0] <= a + 1e-9 and Z[id(p)][1] >= b - 1e-9]
        return unary_union(g) if g else empty
    kinds = {}
    for p in mine:
        kinds.setdefault(p.magnet is not None, []).append(p)
    every = [union(mine, a, b) for a, b in slabs]
    # (one kind: its slabs are every metal's, not made again: half the time
    # on a 300-post fence)
    own = ({g: every for g in kinds} if len(kinds) == 1 else
           {g: [union(sel, a, b) for a, b in slabs]
            for g, sel in kinds.items()})
    bx = None
    if box is not None:
        bx = [box[0], box[1], zm(box[2]), box[3], box[4], zm(box[5])]
    on_box = (lambda a, c: bx is not None
              and (abs(c - bx[a]) <= TOL or abs(c - bx[a + 3]) <= TOL))
    at = {}  # (kind, z) -> the kind's metal in plan at height z (inclusive)

    def metal_at(g, z):
        k = (g, round(z, 9))
        if k not in at:  # the slab z is in, both slabs at a height
            i = int(np.searchsorted(zs, z - 1e-9))
            near = [own[g][j] for j in (i - 1, i) if 0 <= j < len(slabs)
                    and zs[j] - 1e-9 <= z <= zs[j + 1] + 1e-9]
            at[k] = (unary_union(near) if len(near) > 1 else
                     near[0] if near else empty).buffer(TOL)
        return at[k]
    # raw faces: (axis, c on its line, kind) -> {sign: [geometry]}, with
    # each side's own plane for a two-sided sheet's thickness
    raw, plane = {}, {}

    def put(axis, c, g, sign, geo, c0):
        key = (axis, round(c, 9), g)
        raw.setdefault(key, {1: [], -1: []})[sign].append(geo)
        plane.setdefault(key, {1: [], -1: []})[sign].append(c0)
    for i, z in enumerate(zs):  # the z faces
        if on_box(2, z):
            continue
        below = every[i - 1] if i else empty
        above = every[i] if i < len(slabs) else empty
        for g, u in own.items():
            for sign, mt, other in ((1, u[i - 1] if i else empty, above),
                                    (-1, u[i] if i < len(slabs) else empty,
                                     below)):
                if mt.is_empty:
                    continue
                geo = mt.difference(other) if not other.is_empty else mt
                if geo.area <= AREA_MIN:
                    continue
                c = _snap(ln[2], z, sign)
                if c is None:
                    continue
                geo = geo.intersection(metal_at(g, c))
                if geo.area > AREA_MIN:
                    put(2, c, g, sign, geo, z)
    for k, (za, zb) in enumerate(slabs):  # the x and y faces
        for g, u in own.items():
            if u[k].is_empty:
                continue
            oth = own.get(not g, [empty] * len(slabs))[k]
            ob = oth.buffer(TOL) if not oth.is_empty else None
            ub = u[k].buffer(TOL)
            for poly in polygons(u[k]):
                poly = orient(poly, 1.0)  # the metal on each edge's left
                for ring in (poly.exterior, *poly.interiors):
                    xy = np.asarray(ring.coords, float)
                    for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
                        dx, dy = x1 - x0, y1 - y0
                        if (np.hypot(dx, dy) < max(fine, TOL)
                                or min(abs(dx), abs(dy)) > max(fine / 10,
                                                               TOL)):
                            continue
                        if abs(dx) <= abs(dy):  # along y: an x face
                            a, c = 0, 0.5 * (x0 + x1)
                            sign, t0, t1 = (1 if dy > 0 else -1,
                                            min(y0, y1), max(y0, y1))
                        else:                   # along x: a y face
                            a, c = 1, 0.5 * (y0 + y1)
                            sign, t0, t1 = (-1 if dx > 0 else 1,
                                            min(x0, x1), max(x0, x1))
                        if on_box(a, c):
                            continue
                        cs = _snap(ln[a], c, sign)
                        if cs is None:
                            continue
                        seg = (LineString([(c, t0), (c, t1)]) if a == 0 else
                               LineString([(t0, c), (t1, c)]))
                        if ob is not None:  # not against the other kind
                            seg = seg.difference(ob)
                        if seg.is_empty:
                            continue
                        # on its line, where the metal is (inclusive)
                        on = (LineString([(cs, t0 - 1), (cs, t1 + 1)])
                              if a == 0 else
                              LineString([(t0 - 1, cs), (t1 + 1, cs)]))
                        inside = on.intersection(ub)
                        for q in _intervals(seg):
                            ts = sorted(q[:, 1 - a])
                            for r in _intervals(inside):
                                rs = sorted(r[:, 1 - a])
                                lo, hi = max(ts[0], rs[0]), min(ts[-1],
                                                                rs[-1])
                                if hi - lo > TOL:
                                    put(a, cs, g, sign,
                                        sbox(lo, za, hi, zb), c)
    out = []
    for key, by in raw.items():
        a, c, g = key
        P = unary_union(by[1]) if by[1] else empty
        N = unary_union(by[-1]) if by[-1] else empty
        two = P.intersection(N) if not (P.is_empty or N.is_empty) else empty
        one = unary_union([P, N])
        if two.area > AREA_MIN:
            one = one.difference(two)
            t = max(min(plane[key][1]) - max(plane[key][-1]), TOL)
            out.append(Face(a, c, two, True, t, g))
        if one.area > AREA_MIN:
            out.append(Face(a, c, one, False, 0.0, g))
    return out


def rects(geom):
    """A union of rectangles (an x or y face's geometry in (t, z)) as
    [(t0, t1, z0, z1)], cut at its heights and the cuts' equal neighbours
    joined."""
    from shapely.geometry import box as sbox
    from kicad2ems.core.geometry import polygons
    out = []
    for poly in polygons(geom):
        zs = sorted({round(v, 9) for r in (poly.exterior, *poly.interiors)
                     for v in np.asarray(r.coords)[:, 1]})
        t0, _, t1, _ = poly.bounds
        runs = {}
        for za, zb in zip(zs[:-1], zs[1:]):
            strip = poly.intersection(sbox(t0 - 1, za, t1 + 1, zb))
            for q in polygons(strip):
                a, _, b, _ = q.bounds
                key = (round(a, 9), round(b, 9))
                if key in runs and abs(runs[key][-1][3] - za) < 1e-9:
                    runs[key][-1][3] = zb
                else:
                    runs.setdefault(key, []).append([a, b, za, zb])
        out += [tuple(r) for rs in runs.values() for r in rs]
    return out


def skin_depth(sigma, f):
    """m: the skin depth at f Hz in a conductor of sigma S/m."""
    return 1.0 / np.sqrt(np.pi * f * 4e-7 * np.pi * sigma)


# a face's conductor in openEMS's sheet model: this many skin depths at the
# fit's top frequency (its impedance is the surface's, within 0.5 % from a
# tenth of that frequency up, against 2-3 % at 100 skin depths: the fit's
# range grows as (t / delta)^2)
FACE_DEPTHS = 30


def add(CSX, fs, sigma, f_fit, priority, magnet_sigma):
    """The faces as CSX conducting sheets: one property for the faces (one
    side of a conductor FACE_DEPTHS skin depths thick at f_fit, sigma / 4
    and twice that in openEMS's model) and one for the two-sided ones (the
    conductor's own thickness, at most twice that), each for metal and for
    magnets (magnet_sigma). Returns {name: sheet edges' area mm2} for the
    log."""
    from kicad2ems.core.geometry import polygons, split_holes
    props, area = {}, {}
    for f in fs:
        s = min(sigma, magnet_sigma) if f.magnet else sigma
        tf = FACE_DEPTHS * skin_depth(s, f_fit)  # m
        kind = 'solid_magnet' if f.magnet else 'solid_metal'
        t = min(f.t * 1e-3, 2 * tf)
        key = (kind, round(t, 12) if f.two else None)
        if key not in props:
            if f.two:  # a conductor between field on both sides
                n = sum(1 for k in props if k[0] == kind and k[1] is not None)
                name = f"{kind}_walls{n + 1 if n else ''}"
                props[key] = (name, CSX.AddConductingSheet(
                    name, conductivity=float(s), thickness=float(t)))
            else:  # one side of one FACE_DEPTHS skin depths thick
                name = f"{kind}_faces"
                props[key] = (name, CSX.AddConductingSheet(
                    name, conductivity=float(s / 4), thickness=float(2 * tf)))
        name, prop = props[key]
        area[name] = area.get(name, 0.0) + float(f.geom.area)
        if f.axis == 2:
            for q in (q for g in polygons(f.geom) for q in split_holes(g)):
                x, y = q.exterior.xy
                prop.AddPolygon([np.asarray(x[:-1], float),
                                 np.asarray(y[:-1], float)], 'z', float(f.c),
                                priority=priority)
            continue
        for t0, t1, z0, z1 in rects(f.geom):
            s_, e_ = (([f.c, t0, z0], [f.c, t1, z1]) if f.axis == 0 else
                      ([t0, f.c, z0], [t1, f.c, z1]))
            prop.AddBox([float(v) for v in s_], [float(v) for v in e_],
                        priority=priority)
    return area
