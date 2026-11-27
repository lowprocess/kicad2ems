"""
A board's drawn solids (board.solids: core.shapes.apply) resolved into what a
volume solver meshes: disjoint pieces, each an outline extruded between two
heights and of one material. The FDTD model (fdtd.model; its mesh lines:
fdtd.mesh.solid_lines) and Palace's (fem.palace.model) both build from them.

    ps = pieces(board)      # [Piece(material, er, tand, polygon, z0, z1, ...)]
    extent(board)           # (x0, y0, z0, x1, y1, z1) of the board and solids
    enclosure(board, ps, d) # the drawn metal's box, if it closes round the board

How they combine, with each other and with the board:

  - a later solid wins over an earlier one where they overlap (the drawing's
    order): an air box drawn inside a metal box hollows it into a shell;
  - a solid that encloses the whole board (its outline in plan, and the
    dielectric stack's full height, and more than just the board) is around
    the board: inside it the board's dielectric stays as it is, and the
    board's cut-outs take the solid's material (a can and the air inside it,
    a potting compound);
  - any other solid is drawn into the board: where it meets the dielectric it
    takes its place (a slot or a pocket of air, a metal post, an insert of
    another laminate);
  - the board's copper, vias, edge walls, ports and parts stay over every solid
    (a cut takes copper out: the 'cut' material, on a copper layer);
  - a magnet is a metal piece to the RF (Piece.magnet: its remanence; with
    conductor loss, MAGNET_SIGMA) and a ferrite a dielectric one that a
    solver must also magnetise (Piece.ferrite); core.magnets has their static
    field.

Pieces are in the board's own heights (the FDTD maps them with its thick-copper
plan). Nothing is snapped to a mesh here.
"""
from dataclasses import dataclass

__all__ = ['Piece', 'pieces', 'encloses', 'board_volume', 'extent',
           'enclosure', 'describe']

# mm: heights this close are one
ZTOL = 1e-6
# mm2: a piece smaller than this is dropped
AREA_MIN = 1e-6
# S/m: a drawn magnet's conductivity, its faces' loss where the solvers give
# the drawn metal the copper's (sintered NdFeB: 1.4 - 1.6 uohm m; a ceramic
# magnet is a dielectric drawn after it)
MAGNET_SIGMA = 6.7e5


@dataclass
class Piece:
    """One disjoint piece of a solid: `polygon` (shapely, holes allowed, model
    mm) from z0 to z1 (mm, z0 <= z1; equal: a sheet, metal only), of `material`
    (metal, dielectric, air, ferrite; er and tand for a dielectric or a
    ferrite). index: the solid's place in board.solids (the drawing's order);
    name: its shape's; around: it encloses the board (the board's dielectric
    left out of it). ferrite: dict(ms=4 pi Ms G, dh=Oe) of a ferrite; magnet:
    the remanence (T, + along +z) of a magnet, whose material is metal."""
    material: str
    er: float
    tand: float
    polygon: object
    z0: float
    z1: float
    index: int
    name: str
    around: bool = False
    ferrite: dict = None
    magnet: float = None

    @property
    def thickness(self):
        return self.z1 - self.z0


def _solid(d):
    """A board.solids dict as (polygon, z0, z1)."""
    from shapely.geometry import shape
    g = d['polygon']
    g = shape(g) if isinstance(g, dict) else g
    return (g.buffer(0) if not g.is_valid else g, float(d['z0']),
            float(d['z1']))


def board_volume(board):
    """(outline, zb, zt): the board's dielectric (its outline with its
    cut-outs, from the stack's bottom to its top), or None without layers."""
    ls = [l for l in getattr(board, 'layers', None) or [] if l['z1'] > l['z0']]
    if not ls or getattr(board, 'outline', None) is None:
        return None
    return (board.outline, min(l['z0'] for l in ls), max(l['z1'] for l in ls))


def encloses(board, d, vol=None):
    """Whether solid d (a board.solids dict) encloses the whole board: its
    outline round the board's in plan, its heights round the stack's, and
    more than the board itself (a solid of just the board's outline and
    stack takes the dielectric's place: the substrate made air or another
    laminate)."""
    vol = board_volume(board) if vol is None else vol
    if vol is None:
        return False
    from shapely.geometry import Polygon
    g, z0, z1 = _solid(d)
    ol, zb, zt = vol
    whole = Polygon(ol.exterior)
    return (z0 <= zb + ZTOL and z1 >= zt - ZTOL
            and g.buffer(1e-6).covers(whole)
            and (z0 < zb - ZTOL or z1 > zt + ZTOL
                 or g.area > whole.area + AREA_MIN))


def pieces(board):
    """The drawn solids (board.solids) as disjoint [Piece], by the module's
    rules: in the drawing's order, then by height. A metal solid of no
    thickness is a sheet (z0 = z1); a dielectric or air one of none is left
    out."""
    from shapely.strtree import STRtree
    from .geometry import polygons
    sol = list(getattr(board, 'solids', None) or [])
    if not sol:
        return []
    vol = board_volume(board)
    geo = [_solid(d) for d in sol]
    around = [encloses(board, d, vol) for d in sol]
    keep = [i for i, (g, z0, z1) in enumerate(geo)
            if not g.is_empty and g.area > AREA_MIN
            and (z1 - z0 > ZTOL or sol[i]['material'] in ('metal', 'magnet'))]
    # the heights where what is there can change: every solid's two faces,
    # and the board's where a solid encloses it
    zs = sorted({z for i in keep for z in geo[i][1:]}
                | ({vol[1], vol[2]} if vol and any(around) else set()))
    zs = [z for k, z in enumerate(zs) if k == 0 or z - zs[k - 1] > ZTOL]
    out = {i: [] for i in keep}      # i -> [(polygon, z0, z1)]
    # sheets (metal of no thickness): taken out where a later solid passes
    # through their height
    slabs = [(a, b) for a, b in zip(zs[:-1], zs[1:])]
    for i in keep:
        if geo[i][2] - geo[i][1] <= ZTOL:
            z = geo[i][1]
            # (a sheet on a later solid's face stays: they only touch)
            later = [geo[j][0] for j in keep
                     if j > i and geo[j][1] + ZTOL < z < geo[j][2] - ZTOL]
            g = geo[i][0]
            if later:
                from shapely.ops import unary_union
                g = g.difference(unary_union(later))
            out[i].append((g, z, z))
    for za, zc in slabs:
        live = [i for i in keep
                if geo[i][2] - geo[i][1] > ZTOL
                and geo[i][1] <= za + ZTOL and geo[i][2] >= zc - ZTOL]
        if not live:
            continue
        polys = [geo[i][0] for i in live]
        tree = STRtree(polys)
        board_here = (vol is not None and za >= vol[1] - ZTOL
                      and zc <= vol[2] + ZTOL)
        for k, i in enumerate(live):
            g = polys[k]
            later = [polys[j] for j in tree.query(g) if j > k
                     and polys[j].intersects(g)]
            if later:
                from shapely.ops import unary_union
                g = g.difference(unary_union(later))
            if around[i] and board_here:
                g = g.difference(vol[0])
            if not g.is_empty and g.area > AREA_MIN:
                out[i].append((g, za, zc))
    res = []
    for i in keep:
        d = sol[i]
        runs = []  # one height after another: merged where the outline holds
        for g, za, zc in out[i]:
            if (runs and zc - za > ZTOL and runs[-1][2] - runs[-1][1] > ZTOL
                    and abs(runs[-1][2] - za) <= ZTOL
                    and runs[-1][0].equals(g)):
                runs[-1] = (runs[-1][0], runs[-1][1], zc)
            else:
                runs.append((g, za, zc))
        for g, za, zc in runs:
            for p in polygons(g):
                if p.area > AREA_MIN:
                    mat = d['material']
                    res.append(Piece(
                        material='metal' if mat == 'magnet' else mat,
                        er=float(d.get('er', 1.0) or 1.0),
                        tand=float(d.get('tand', 0.0) or 0.0), polygon=p,
                        z0=za, z1=zc, index=i,
                        name=str(d.get('shape') or f'solid{i + 1}'),
                        around=around[i],
                        ferrite=(dict(ms=float(d.get('ms', 1800.0)),
                                      dh=float(d.get('dh', 30.0)))
                                 if mat == 'ferrite' else None),
                        magnet=(float(d.get('br', 0.0))
                                if mat == 'magnet' else None)))
    return res


def extent(board, ps=None):
    """(x0, y0, z0, x1, y1, z1) mm round the board's outline and stack and its
    solids' pieces (ps: pieces(board))."""
    ps = pieces(board) if ps is None else ps
    x0, y0, x1, y1 = board.outline.bounds
    vol = board_volume(board)
    zs = [z for z in getattr(board, 'cu_z', {}).values()]
    if vol:
        zs += [vol[1], vol[2]]
    zb, zt = (min(zs), max(zs)) if zs else (0.0, 0.0)
    for p in ps:
        a, b, c, d = p.polygon.bounds
        x0, y0, x1, y1 = min(x0, a), min(y0, b), max(x1, c), max(y1, d)
        zb, zt = min(zb, p.z0), max(zt, p.z1)
    return x0, y0, zb, x1, y1, zt


def enclosure(board, ps, domain):
    """The box (x0, y0, z0, x1, y1, z1) of the drawn metal when it closes round
    the board: no way through from the board's ports (or the middle of its
    stack, with none) to the faces of `domain` (the same six numbers) other
    than through metal; None when there is one, or when the board pokes out
    of the metal's box. Outside a closed metal shell the fields are nothing:
    a solver need not mesh past it.

    The domain is cut at every metal height; in each slab the space that is
    not metal falls into pieces in plan; a piece touching the domain's sides,
    or in the bottom or top slab, is open, and so is any piece that overlaps
    (in plan, past the metal sheets at their height) an open one in the slab
    above or below."""
    from shapely.geometry import Point, box as sbox
    from shapely.ops import unary_union
    from .geometry import polygons
    metal = [p for p in ps if p.material == 'metal']
    if not metal:
        return None
    X0, Y0, Z0, X1, Y1, Z1 = domain
    bb = [min(p.polygon.bounds[0] for p in metal),
          min(p.polygon.bounds[1] for p in metal),
          min(p.z0 for p in metal),
          max(p.polygon.bounds[2] for p in metal),
          max(p.polygon.bounds[3] for p in metal),
          max(p.z1 for p in metal)]
    b = extent(board, [])
    if not (bb[0] <= b[0] and bb[1] <= b[1] and bb[2] <= b[2]
            and bb[3] >= b[3] and bb[4] >= b[4] and bb[5] >= b[5]):
        return None
    zs = sorted({Z0, Z1} | {z for p in metal for z in (p.z0, p.z1)
                            if Z0 < z < Z1})
    zs = [z for k, z in enumerate(zs) if k == 0 or z - zs[k - 1] > ZTOL]
    rect = sbox(X0, Y0, X1, Y1)
    slabs = []  # [(za, zb, [free piece])]
    for za, zb in zip(zs[:-1], zs[1:]):
        m = unary_union([p.polygon for p in metal
                         if p.z1 - p.z0 > ZTOL and p.z0 <= za + ZTOL
                         and p.z1 >= zb - ZTOL])
        slabs.append((za, zb, polygons(rect.difference(m))))
    n = len(slabs)
    nodes = [(s, c) for s in range(n) for c in range(len(slabs[s][2]))]
    open_ = {(s, c) for s, c in nodes
             if s in (0, n - 1)
             or slabs[s][2][c].intersects(rect.exterior)}
    # the way up from slab s to s + 1, past the sheets at their height
    todo = list(open_)
    while todo:
        s, c = todo.pop()
        g = slabs[s][2][c]
        for t in (s - 1, s + 1):
            if not 0 <= t < n:
                continue
            z = slabs[max(s, t)][0]
            sheets = unary_union([p.polygon for p in metal
                                  if p.z1 - p.z0 <= ZTOL
                                  and abs(p.z0 - z) <= ZTOL])
            for d, h in enumerate(slabs[t][2]):
                if (t, d) in open_:
                    continue
                if g.intersection(h).difference(sheets).area > AREA_MIN:
                    open_.add((t, d))
                    todo.append((t, d))
    seeds = [(float(p.c[0]), float(p.c[1]), 0.5 * (p.z_sig + p.z_ref))
             for p in getattr(board, 'ports', None) or []]
    if not seeds:
        c = board.outline.representative_point()
        seeds = [(c.x, c.y, 0.5 * (b[2] + b[5]))]
    for x, y, z in seeds:
        for s, (za, zb, free) in enumerate(slabs):
            if not za - ZTOL <= z <= zb + ZTOL:
                continue
            for c, g in enumerate(free):
                if g.buffer(1e-6).contains(Point(x, y)) and (s, c) in open_:
                    return None
    return tuple(float(v) for v in bb)


def describe(ps):
    """The pieces in a few words: '3 drawn solids as 4 pieces: metal 2,
    air 1, dielectric 1 (er 9.8)'."""
    if not ps:
        return 'no drawn solids'
    n = len({p.index for p in ps})
    by = {}
    for p in ps:
        by.setdefault(p.material, []).append(p)
    parts = []
    for mat in ('metal', 'dielectric', 'ferrite', 'air'):
        if mat in by:
            ers = sorted({round(p.er, 3) for p in by[mat]})
            mag = sum(p.magnet is not None for p in by[mat])
            parts.append(f"{mat} {len(by[mat])}" + (
                f" (er {', '.join(f'{e:g}' for e in ers)})"
                if mat in ('dielectric', 'ferrite') else
                f" ({mag} magnet{'s' if mag != 1 else ''})" if mag else ''))
    return (f"{n} drawn solid{'s' if n != 1 else ''} as {len(ps)} "
            f"piece{'s' if len(ps) != 1 else ''}: {', '.join(parts)}")
