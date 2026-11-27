"""
The static field that biases a drawn ferrite: the drawn permanent magnets'
and the ferrite's own (its demagnetising field), for the ferrite's
gyrotropic permeability (gfdtd's FerriteMaterial). No Qt, no solver.

    src = sources(board)                 # the magnets: [(polygon, z0, z1, M)]
    hz = field_z(src, pts)               # their Hz (A/m) at points (N, 3) mm
    b = bias(board, ps, pts)             # the internal field in the ferrite
    b['hz'], b['sat']                    #   (A/m, NaN where not saturated)
    summary(board)                       # per ferrite: applied and internal
                                         #   field, FMR frequency, saturation

How. The Draw page's solids are prisms: an outline extruded along z. A prism
magnetised uniformly along z (a magnet's rigid magnetisation M = Br / mu0, a
saturated ferrite's Ms) has its magnetic charge on its top and bottom faces
only (+M on the top, -M on the bottom; none on the sides), so its H is the
two faces': a face of charge density s at height zf gives, at a point,

    Hz = s / (4 pi) * Omega

with Omega the solid angle the face subtends there (+ above it, - below):
exact, the polygon's triangles by Van Oosterom and Strackee's formula. Inside
an infinite slab this is -M (a demagnetising factor of 1); on a disc's axis
2 pi (1 - d / sqrt(d^2 + a^2)).

The ferrite is taken saturated along z (its magnetisation the sign of the
magnets' field over it, all of a solid one way), so its internal field is
the magnets' plus its own demagnetising field:

    H0 = Hz_magnets + sign * Hz_ferrite(Ms)

and where H0 has not the sign of the magnetisation the ferrite is not
saturated there (partly magnetised: domains, low-field loss), which this
model does not cover: bias() gives NaN there and the solver leaves those
cells unbiased (a dielectric of mu 1).

What it leaves out: the field's x and y components (the bias is taken along
z: exact on the axis of a round magnet over a round puck, a few per cent off
near their rims), magnetic materials other than the magnets and the ferrite
(no steel pole pieces or return path: the metal is copper, non-magnetic), a
magnet's recoil permeability (about 1.05: rigid), and temperature (Br and
4 pi Ms at 25 C).
"""
import math
import numpy as np

__all__ = ['MU0', 'OE', 'GAMMA', 'sources', 'ferrite_sources', 'field_z',
           'bias', 'summary', 'fmr']

MU0 = 4e-7 * math.pi
OE = 1000.0 / (4 * math.pi)     # A/m in an oersted
GAUSS = 1e-4                    # T in a gauss (4 pi Ms G -> mu0 Ms T)
# Hz per (A/m) of the precession at g = 2: g mu_B / h mu0 (28.0 GHz/T)
GAMMA = 2.0 * 9.2740100783e-24 / 6.62607015e-34 * MU0
# points a chunk (field_z's temporaries: chunk x triangles x 8 doubles)
CHUNK = 4096


def _rings(poly):
    """A shapely polygon's rings as (k, 2) arrays, the outer one anticlockwise
    and the holes clockwise (so their triangles' signed sum is the polygon)."""
    from shapely.geometry.polygon import orient
    g = orient(poly, 1.0)
    rings = [np.asarray(g.exterior.coords, float)[:-1]]
    rings += [np.asarray(r.coords, float)[:-1] for r in g.interiors]
    return rings


def _triangles(poly):
    """(T, 3, 2) fan triangles of the polygon's rings: their signed solid
    angles sum to the polygon's (a fan of a ring of any shape covers its
    inside once, with the right sign)."""
    tri = []
    for r in _rings(poly):
        if len(r) < 3:
            continue
        a = np.repeat(r[:1], len(r) - 2, axis=0)
        tri.append(np.stack([a, r[1:-1], r[2:]], axis=1))
    return np.concatenate(tri) if tri else np.zeros((0, 3, 2))


def _omega_up(tri, zf, pts):
    """Solid angle (N,) of the face (triangles tri at height zf) at pts (N, 3):
    positive above it, negative below (Van Oosterom and Strackee, 1983)."""
    out = np.zeros(len(pts))
    if not len(tri):
        return out
    for c0 in range(0, len(pts), CHUNK):
        p = pts[c0:c0 + CHUNK]
        R = np.empty((len(p), len(tri), 3, 3))
        R[..., :2] = tri[None, :, :, :] - p[:, None, None, :2]
        R[..., 2] = zf - p[:, None, None, 2]
        R1, R2, R3 = R[:, :, 0], R[:, :, 1], R[:, :, 2]
        n1, n2, n3 = (np.linalg.norm(x, axis=-1) for x in (R1, R2, R3))
        num = np.einsum('ntk,ntk->nt', R1, np.cross(R2, R3))
        den = (n1 * n2 * n3 + np.einsum('ntk,ntk->nt', R1, R2) * n3
               + np.einsum('ntk,ntk->nt', R1, R3) * n2
               + np.einsum('ntk,ntk->nt', R2, R3) * n1)
        # (anticlockwise seen from above: positive for a point below)
        out[c0:c0 + CHUNK] = -2 * np.arctan2(num, den).sum(axis=1)
    return out


def field_z(srcs, pts):
    """Hz (A/m) at pts (N, 3) mm of prisms magnetised along z: srcs
    [(polygon, z0, z1, M A/m)], each a charge +M on its top face and -M on its
    bottom one."""
    pts = np.atleast_2d(np.asarray(pts, float))
    hz = np.zeros(len(pts))
    for poly, z0, z1, M in srcs:
        if not M or z1 <= z0:
            continue
        tri = _triangles(poly)
        hz += M / (4 * math.pi) * (_omega_up(tri, z1, pts)
                                   - _omega_up(tri, z0, pts))
    return hz


def sources(board, zmap=None):
    """The drawn magnets (board.solids of material 'magnet') as field_z's
    sources, M = Br / mu0; zmap maps their heights (the FDTD's thick-copper
    plan)."""
    from shapely.geometry import shape
    zm = zmap or (lambda z: z)
    out = []
    for d in getattr(board, 'solids', None) or []:
        if d.get('material') != 'magnet' or not d.get('br'):
            continue
        g = d['polygon']
        g = shape(g) if isinstance(g, dict) else g
        out.append((g, zm(float(d['z0'])), zm(float(d['z1'])),
                    float(d['br']) / MU0))
    return out


def ferrite_sources(ps, zmap=None, ms=True):
    """The ferrite pieces (core.solids pieces with .ferrite) as field_z's
    sources at their saturation (M = Ms, along +z), with their solid's
    index."""
    zm = zmap or (lambda z: z)
    return [((p.polygon, zm(p.z0), zm(p.z1),
              p.ferrite['ms'] * GAUSS / MU0 if ms else 1.0), p.index)
            for p in ps if p.ferrite is not None and p.z1 > p.z0]


def bias(board, ps, pts, owner=None, zmap=None):
    """The ferrite's internal static field along z at pts (N, 3) mm, each in
    the ferrite solid owner[k] (an index into board.solids; None: all in
    one). dict(hz: A/m, NaN where not saturated; applied: the magnets' Hz;
    sign: {solid index: +1 / -1, its magnetisation's direction}; sat: bool
    (N,)). With no magnet the ferrite is unbiased: hz all NaN."""
    pts = np.atleast_2d(np.asarray(pts, float))
    owner = (np.zeros(len(pts), int) - 1 if owner is None
             else np.asarray(owner, int))
    src = sources(board, zmap)
    app = field_z(src, pts) if src else np.zeros(len(pts))
    fer = ferrite_sources(ps, zmap)
    hz = app.copy()
    sign = {}
    for idx in sorted({i for _, i in fer}):
        mine = owner == idx if (owner >= 0).any() else np.ones(len(pts), bool)
        s = 1.0 if app[mine].sum() >= 0 else -1.0
        sign[idx] = s
        own = field_z([g for g, i in fer if i == idx], pts)
        # its own field everywhere (the cells of another ferrite feel it too)
        hz += s * own
    s_at = np.array([sign.get(int(o), next(iter(sign.values()), 1.0))
                     for o in owner]) if sign else np.ones(len(pts))
    sat = (hz * s_at > 0) if src else np.zeros(len(pts), bool)
    return dict(hz=np.where(sat, hz, np.nan), applied=app, sign=sign,
                sat=sat, raw=hz)


def fmr(h0, ms_g, g=2.0):
    """(f0, fm) Hz: the precession frequency gamma H0 of internal field h0
    (A/m) and gamma 4 pi Ms of a ferrite of ms_g gauss."""
    k = GAMMA * g / 2
    return k * np.asarray(h0, float), k * ms_g * GAUSS / MU0


def summary(board, ps=None, n=15):
    """Per drawn ferrite solid: dict(name, index, applied_oe (min, mean, max of
    the magnets' Hz over it), internal_oe (the same of H0 where saturated),
    saturated (the share of it), f0 (Hz, gamma H0 at the mean), fm (Hz),
    sign), from a grid of n x n x 3 points over each piece."""
    from shapely.prepared import prep
    from .solids import pieces
    ps = pieces(board) if ps is None else ps
    fer = [p for p in ps if p.ferrite is not None and p.z1 > p.z0]
    if not fer:
        return []
    pts, own = [], []
    for p in fer:
        x0, y0, x1, y1 = p.polygon.bounds
        P = prep(p.polygon)
        from shapely.geometry import Point
        xs, ys = np.linspace(x0, x1, n + 2)[1:-1], np.linspace(y0, y1,
                                                                n + 2)[1:-1]
        zs = p.z0 + (p.z1 - p.z0) * np.array([1 / 6, 1 / 2, 5 / 6])
        for x in xs:
            for y in ys:
                if P.contains(Point(x, y)):
                    for z in zs:
                        pts.append((x, y, z))
                        own.append(p.index)
    if not pts:
        return []
    b = bias(board, ps, np.array(pts), np.array(own))
    out = []
    own = np.array(own)
    for idx in sorted(set(own)):
        m = own == idx
        app, h = b['applied'][m] / OE, b['hz'][m] / OE
        ok = np.isfinite(h)
        p = next(q for q in fer if q.index == idx)
        f0, fm = fmr(abs(np.nanmean(h)) * OE if ok.any() else 0.0,
                     p.ferrite['ms'])
        out.append(dict(
            name=p.name, index=int(idx),
            applied_oe=(float(app.min()), float(app.mean()), float(app.max())),
            internal_oe=((float(np.nanmin(h)), float(np.nanmean(h)),
                          float(np.nanmax(h))) if ok.any() else None),
            saturated=float(ok.mean()), f0=float(f0), fm=float(fm),
            sign=b['sign'].get(int(idx), 1.0)))
    return out
