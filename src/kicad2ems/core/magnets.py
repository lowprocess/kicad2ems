"""
The static field that biases a drawn ferrite: the drawn permanent magnets'
and the ferrite's own (its demagnetising field), for the ferrite's
gyrotropic permeability (gfdtd's FerriteMaterial). No Qt, no solver.

    src = sources(board)                 # the magnets: [(polygon, z0, z1, M)]
    hz = field_z(src, pts)               # their Hz (A/m) at points (N, 3) mm
    h = field(src, pts)                  # their H vector (N, 3)
    f = magnetisation(board)             # the ferrites' magnetisation,
    h, m = f.at(pts)                     #   self-consistent: the internal
                                         #   field and M / Ms at points
    b = bias(board, ps, pts)             # the quick model (along z):
    b['hz'], b['sat']                    #   A/m, NaN where not saturated
    summary(board)                       # per ferrite (the quick model):
                                         #   applied and internal field, FMR
                                         #   frequency, saturation

How. The Draw page's solids are prisms: an outline extruded along z. A prism
magnetised uniformly along z (a magnet's rigid magnetisation M = Br / mu0)
has its magnetic charge on its top and bottom faces only (+M on the top, -M
on the bottom; none on the sides), so its H is the two faces': a face of
charge density s at height zf gives, at a point,

    Hz = s / (4 pi) * Omega,   H_xy = s / (4 pi) * sum over its edges of
                               n ln((Ra + Rb + L) / (Ra + Rb - L))

with Omega the solid angle the face subtends there (+ above it, - below;
the polygon's triangles by Van Oosterom and Strackee's formula) and, across
it, the divergence theorem's line integral of 1 / R round its edges (n each
edge's outward normal, Ra, Rb the distances to its ends, L its length): both
exact. Inside an infinite slab Hz is -M (a demagnetising factor of 1); on a
disc's axis 2 pi (1 - d / sqrt(d^2 + a^2)); off a cylinder magnet's axis it
is its surface current's (current loops) to 3e-6 of M.

The ferrite (magnetisation()) is an ideal soft one on a voxel grid over all
the drawn ferrites (64 across the widest, 4 through the thinnest, each voxel
uniformly magnetised over its share of ferrite): its magnetisation M is the
least energy -Ha . M - 1/2 M . Hd(M) + |M|^2 / (2 chi) with |M| <= Ms, Hd
the voxels' own field (their faces' charges, exact for boxes, an FFT
convolution), found by Nesterov's projected gradient steps. Where the
magnets can, it is saturated (|M| = Ms) along its internal field H = Ha + Hd
(tilted where the magnets' field spreads); elsewhere partly magnetised, H
all but 0 (M / chi: chi 50, the initial susceptibility, makes M unique, the
domains' mean). A sphere: H = Ha - Ms / 3 within 1 %, M = 3 Ha partly; a
thin disc along z: the face model's (below) within 0.5 % (2 % at its rim);
a long cylinder across: N 1/2 within 3 %.

The quick model (bias(), the Draw page's readout) takes the ferrite
saturated along z (its magnetisation the sign of the magnets' field over
it), its internal field the magnets' Hz plus its own faces':

    H0 = Hz_magnets + sign * Hz_ferrite(Ms)

NaN where H0 has not the sign of the magnetisation (not saturated). It is
exact for a z prism magnetised along z; under a small magnet the field
spreads (rf_circulator's puck, self-consistent: magnetised 36 deg off z on
the mean, up to 49, its internal field 540 to 990 Oe, where this model has
350 to 785 along z).

What both leave out: magnetic materials other than the magnets and the
ferrite (no steel pole pieces or return path: the metal is copper,
non-magnetic), a magnet's recoil permeability (about 1.05: rigid),
anisotropy and hysteresis, and temperature (Br and 4 pi Ms at 25 C).
"""
import math
import numpy as np

__all__ = ['MU0', 'OE', 'GAMMA', 'sources', 'ferrite_sources', 'field_z',
           'field', 'magnetisation', 'Ferrites', 'bias', 'summary', 'fmr',
           'magnetised', 'alpha', 'ms_si', 'refusal']

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
        c = (zf - p[:, 2])[:, None]
        A = [tri[None, :, k, 0] - p[:, :1] for k in range(3)]
        B = [tri[None, :, k, 1] - p[:, 1:2] for k in range(3)]
        n = [np.sqrt(A[k] ** 2 + B[k] ** 2 + c * c) for k in range(3)]
        dot = lambda i, j: A[i] * A[j] + B[i] * B[j] + c * c
        # R1 . (R2 x R3): c times twice the triangle's signed area
        num = c * ((A[1] - A[0]) * (B[2] - B[0]) - (B[1] - B[0]) * (A[2] - A[0]))
        den = (n[0] * n[1] * n[2] + dot(0, 1) * n[2] + dot(0, 2) * n[1]
               + dot(1, 2) * n[0])
        # (anticlockwise seen from above: positive for a point below)
        out[c0:c0 + CHUNK] = -2 * np.arctan2(num, den).sum(axis=1)
    return out


def _face(rings, zf, pts, inplane=True):
    """(Omega (N,), H across (N, 2) or None) of a unit charge density on the
    face (rings: _rings', at height zf) at pts (N, 3), the latter times 4 pi:
    the solid angle as the fan triangles' (_omega_up) and the edges' log
    terms (_inplane), each vertex's distance worked out once."""
    om = np.zeros(len(pts))
    hxy = np.zeros((len(pts), 2)) if inplane else None
    for r in rings:
        k = len(r)
        if k < 3:
            continue
        nxt = np.roll(r, -1, axis=0)
        t = nxt - r
        L = np.hypot(t[:, 0], t[:, 1])
        ok = L > 0
        nrm = np.stack([t[:, 1], -t[:, 0]], axis=1)[ok] / L[ok, None]
        # the fan's triangles (0, i, i + 1): twice their signed areas
        d2 = ((r[1:-1, 0] - r[0, 0]) * (r[2:, 1] - r[0, 1])
              - (r[1:-1, 1] - r[0, 1]) * (r[2:, 0] - r[0, 0]))
        for c0 in range(0, len(pts), CHUNK):
            p = pts[c0:c0 + CHUNK]
            c = (zf - p[:, 2])[:, None]
            A = r[None, :, 0] - p[:, :1]
            B = r[None, :, 1] - p[:, 1:2]
            c2 = c * c
            n = np.sqrt(A * A + B * B + c2)
            d0 = A[:, :1] * A + B[:, :1] * B + c2       # R0 . Ri
            dn = A[:, :-1] * A[:, 1:] + B[:, :-1] * B[:, 1:] + c2   # Ri . Ri+1
            n0 = n[:, :1]
            den = (n0 * n[:, 1:-1] * n[:, 2:] + d0[:, 1:-1] * n[:, 2:]
                   + d0[:, 2:] * n[:, 1:-1] + dn[:, 1:] * n0)
            om[c0:c0 + CHUNK] -= 2 * np.arctan2(c * d2[None, :], den).sum(1)
            if inplane:
                s_ = n + np.roll(n, -1, axis=1)
                g = np.log((s_ + L) / np.maximum(s_ - L, 1e-12 * np.maximum(
                    L, 1e-300)))[:, ok]
                hxy[c0:c0 + CHUNK] += g @ nrm
    return om, hxy


def field_z(srcs, pts):
    """Hz (A/m) at pts (N, 3) mm of prisms magnetised along z: srcs
    [(polygon, z0, z1, M A/m)], each a charge +M on its top face and -M on its
    bottom one."""
    pts = np.atleast_2d(np.asarray(pts, float))
    hz = np.zeros(len(pts))
    for poly, z0, z1, M in srcs:
        if not M or z1 <= z0:
            continue
        rings = _rings(poly)
        hz += M / (4 * math.pi) * (_face(rings, z1, pts, False)[0]
                                   - _face(rings, z0, pts, False)[0])
    return hz


def _edges(poly):
    """(E, 2, 2) the polygon's ring edges (a, b), the outer ring anticlockwise
    and the holes clockwise: each edge's outward normal (out of the face) is
    (b - a) turned clockwise."""
    out = []
    for r in _rings(poly):
        if len(r) >= 3:
            out.append(np.stack([r, np.roll(r, -1, axis=0)], axis=1))
    return np.concatenate(out) if out else np.zeros((0, 2, 2))


def _inplane(edges, zf, pts):
    """(N, 2): 4 pi times the in-plane H of a unit charge density on the face
    (its edges at height zf) at pts (N, 3): H = -grad of its potential, whose
    in-plane part is the edges' line integral of n / R (the divergence
    theorem in the face's plane), each straight edge's n ln((Ra + Rb + L) /
    (Ra + Rb - L)) exactly (Ra, Rb: the distances to its ends, L its
    length)."""
    out = np.zeros((len(pts), 2))
    if not len(edges):
        return out
    a, b = edges[:, 0], edges[:, 1]
    t = b - a
    L = np.hypot(t[:, 0], t[:, 1])
    ok = L > 0
    a, b, t, L = a[ok], b[ok], t[ok], L[ok]
    n = np.stack([t[:, 1], -t[:, 0]], axis=1) / L[:, None]
    for c0 in range(0, len(pts), CHUNK):
        p = pts[c0:c0 + CHUNK]
        dz2 = (zf - p[:, 2:3]) ** 2
        ra = np.sqrt((a[None, :, 0] - p[:, :1]) ** 2
                     + (a[None, :, 1] - p[:, 1:2]) ** 2 + dz2)
        rb = np.sqrt((b[None, :, 0] - p[:, :1]) ** 2
                     + (b[None, :, 1] - p[:, 1:2]) ** 2 + dz2)
        s = ra + rb
        # (on an edge itself the field of a sheet's rim is infinite: the
        # floor keeps it finite there)
        g = np.log((s + L) / np.maximum(s - L, 1e-12 * L))
        out[c0:c0 + CHUNK] = g @ n
    return out


def field(srcs, pts):
    """The H vector (A/m, (N, 3)) at pts (N, 3) mm of prisms magnetised along
    z (srcs as field_z's): each face's Hz its solid angle, its Hx and Hy its
    edges' log terms (_face)."""
    pts = np.atleast_2d(np.asarray(pts, float))
    h = np.zeros((len(pts), 3))
    for poly, z0, z1, M in srcs:
        if not M or z1 <= z0:
            continue
        rings = _rings(poly)
        k = M / (4 * math.pi)
        o1, x1 = _face(rings, z1, pts)
        o0, x0 = _face(rings, z0, pts)
        h[:, 2] += k * (o1 - o0)
        h[:, :2] += k * (x1 - x0)
    return h


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
    one), the ferrite saturated along z (the quick model: the Draw page's
    readout; magnetisation() has the field's direction and the partly
    magnetised ferrite). dict(hz: A/m, NaN where not saturated; applied: the
    magnets' Hz; sign: {solid index: +1 / -1, its magnetisation's
    direction}; sat: bool (N,)). With no magnet the ferrite is unbiased: hz
    all NaN."""
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


def _box_kernel(d, n):
    """K (3, 3, 2 n0 - 1, 2 n1 - 1, 2 n2 - 1): the H at each offset of the
    voxel grid (spacing d mm, n voxels a side) of one voxel uniformly
    magnetised along each axis with unit M (its faces' charges, exact): K[a,
    b] the a component of the field of M along b. Symmetric in a, b and even
    in the offset (the second derivatives of one potential), so one octant is
    worked out and mirrored."""
    hx, hy, hz = (0.5 * float(v) for v in d)
    offs = [np.arange(m) * float(v) for m, v in zip(n, d)]
    X, Y, Z = np.meshgrid(*offs, indexing='ij')
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    from shapely.geometry import box
    q = np.empty((3, 3) + X.shape)
    # M along z: a z prism; along x, y: the same in the frames (y, z, x) and
    # (z, x, y) (its field's components back in order)
    for b, perm, back, (u, v, w) in (
            (2, [0, 1, 2], [0, 1, 2], (hx, hy, hz)),
            (0, [1, 2, 0], [2, 0, 1], (hy, hz, hx)),
            (1, [2, 0, 1], [1, 2, 0], (hz, hx, hy))):
        h = field([(box(-u, -v, u, v), -w, w, 1.0)], P[:, perm])[:, back]
        q[:, b] = h.T.reshape((3,) + X.shape)
    # mirror the octant: H_a of M_b is odd in x_a and in x_b (even when a ==
    # b) about the voxel
    K = np.empty((3, 3) + tuple(2 * m - 1 for m in n))
    for a in range(3):
        for b in range(3):
            o = q[a, b]
            for ax in range(3):
                sgn = -1.0 if (ax == a) != (ax == b) else 1.0
                o = np.concatenate([sgn * np.flip(o, axis=ax)[
                    tuple(slice(None, -1) if k == ax else slice(None)
                          for k in range(3))], o], axis=ax)
            K[a, b] = o
    return K


class Ferrites:
    """The drawn ferrites' static magnetisation, self-consistent
    (magnetisation()): the voxel grid (lo: its first voxel's centre, d: the
    spacing, mm; n), fill (each voxel's share of ferrite), M (3, n: the
    ferrite's magnetisation in it, A/m, |M| <= Ms), H and Ha (3, n: the
    internal field and the magnets' at its centre, A/m), Ms (A/m), the
    iterations and the last step's change (of Ms)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def at(self, pts, exact=False):
        """(h, m) at pts (N, 3) mm: the internal field (A/m) and M / Ms there,
        m and the ferrite's own field those of the nearest voxel at least
        half ferrite (piecewise constant), the magnets' field (smooth: no
        magnet in the ferrite) interpolated from the voxels' centres
        (trilinear) or, exact, worked out at the points."""
        from scipy.spatial import cKDTree
        from scipy.interpolate import RegularGridInterpolator
        pts = np.atleast_2d(np.asarray(pts, float))
        idx = np.argwhere(self.fill >= 0.5 - 1e-9)
        if not len(idx):
            idx = np.argwhere(self.fill > 0)
        c = self.lo + idx * self.d
        _, k = cKDTree(c).query(pts)
        ii = (slice(None),) + tuple(idx[k].T)
        if exact or min(self.n) < 2:
            ha = self.applied(pts)
        else:
            axes = [self.lo[a] + np.arange(self.n[a]) * self.d[a]
                    for a in range(3)]
            ha = np.stack([RegularGridInterpolator(
                axes, self.Ha[a], bounds_error=False, fill_value=None)(pts)
                for a in range(3)], axis=1)
        h = ha + (self.H[ii] - self.Ha[ii]).T
        return h, (self.M[ii] / self.Ms[ii[1:]]).T


def magnetisation(board, ps=None, zmap=None, voxel=None, applied=None,
                  chi=50.0, tol=1e-4, max_iter=3000):
    """The drawn ferrites' static magnetisation in the magnets' field,
    self-consistent: each ferrite an ideal soft one (no anisotropy, no
    hysteresis) on a voxel grid over all of them (voxel: its spacing, mm, or
    None: 64 across the widest, at least 4 through the thinnest), each voxel
    uniformly magnetised over the share of it that is ferrite (fill: 4 x 4
    points across, its overlap in z). The equilibrium is the least energy
    -Ha . M - 1/2 M . Hd(M) + |M|^2 / (2 chi) with |M| <= Ms in every voxel,
    the demagnetising field Hd the voxels' face charges (exact for uniform
    boxes: _box_kernel, an FFT convolution); projected gradient steps
    (Nesterov's), M <- P(M + H - M / chi): saturated where it can be (|M| =
    Ms along its internal field H), else partly magnetised with H = M / chi
    inside, near 0 (H_a all but cancelled by its own field). chi, the
    initial susceptibility, makes that M unique (the least that carries the
    charge: the domains' mean, no vortex added). applied: a function pts (N,
    3) mm -> H (N, 3) A/m instead of the drawn magnets' field (tests). A
    Ferrites (None: no ferrite)."""
    import shapely
    from .solids import pieces
    ps = pieces(board) if ps is None else ps
    zm = zmap or (lambda z: z)
    fer = [(p.polygon, zm(p.z0), zm(p.z1), ms_si(p.ferrite['ms']))
           for p in ps if p.ferrite is not None and p.z1 > p.z0]
    if not fer:
        return None
    if applied is None:
        src = sources(board, zmap)
        applied = (lambda p: field(src, p)) if src else (
            lambda p: np.zeros((len(np.atleast_2d(p)), 3)))
    x0 = min(g.bounds[0] for g, *_ in fer)
    y0 = min(g.bounds[1] for g, *_ in fer)
    x1 = max(g.bounds[2] for g, *_ in fer)
    y1 = max(g.bounds[3] for g, *_ in fer)
    z0, z1 = min(f[1] for f in fer), max(f[2] for f in fer)
    if voxel is None:
        dxy = max(x1 - x0, y1 - y0) / 64
        d = np.array([dxy, dxy, min(f[2] - f[1] for f in fer) / 4])
    else:
        d = np.broadcast_to(np.asarray(voxel, float), (3,)).copy()
    n = tuple(int(v) for v in np.maximum(np.ceil(np.array(
        [x1 - x0, y1 - y0, z1 - z0]) / d - 1e-9), 1))
    lo = np.array([x0, y0, z0]) + 0.5 * d
    cs = [lo[a] + np.arange(n[a]) * d[a] for a in range(3)]
    # each voxel's share of each ferrite: 4 x 4 points across it in plan,
    # its overlap in z
    sub = (np.arange(4) + 0.5) / 4 - 0.5
    SX = (cs[0][:, None] + sub[None, :] * d[0]).ravel()
    SY = (cs[1][:, None] + sub[None, :] * d[1]).ravel()
    GX, GY = np.meshgrid(SX, SY, indexing='ij')
    fill = np.zeros(n)
    msw = np.zeros(n)
    for g, za, zb, ms in fer:
        inp = shapely.contains_xy(g, GX, GY).reshape(
            n[0], 4, n[1], 4).mean(axis=(1, 3))
        fz = np.clip((np.minimum(cs[2] + d[2] / 2, zb)
                      - np.maximum(cs[2] - d[2] / 2, za)) / d[2], 0, 1)
        f = inp[:, :, None] * fz[None, None, :]
        fill += f
        msw += f * ms
    fill = np.minimum(fill, 1.0)
    on = fill > 0
    Ms = np.where(on, msw / np.maximum(fill, 1e-300), 1.0)
    X, Y, Z = np.meshgrid(*cs, indexing='ij')
    Ha = applied(np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
                 ).T.reshape((3,) + n)
    # the kernel's FFT on the padded grid (a linear convolution)
    K = _box_kernel(d, n)
    N = tuple(2 * m for m in n)
    Kf = np.empty((3, 3) + N[:2] + (N[2] // 2 + 1,), complex)
    for a in range(3):
        for b in range(a, 3):
            Kf[a, b] = Kf[b, a] = np.fft.rfftn(_wrap(K[a, b], n))
    del K

    def demag(M):
        Mf = [np.fft.rfftn(fill * M[b], N, axes=(0, 1, 2)) for b in range(3)]
        return np.stack([np.fft.irfftn(
            Kf[a, 0] * Mf[0] + Kf[a, 1] * Mf[1] + Kf[a, 2] * Mf[2], N,
            axes=(0, 1, 2))
            [:n[0], :n[1], :n[2]] for a in range(3)])

    def project(M):
        r = np.sqrt((M * M).sum(0))
        s = np.where(r > Ms, Ms / np.maximum(r, 1e-300), 1.0)
        return np.where(on, M * s, 0.0)

    # from M = 0 (the steps then add no charge-free part); the step: the
    # gradient's Lipschitz constant, the demagnetising operator's largest
    # eigenvalue (1) and 1 / chi
    M = np.zeros_like(Ha)
    Yk, t = M.copy(), 1.0
    step = 0.95 / (1.0 + 1.0 / chi)
    ms_max = Ms[on].max()
    it, res = 0, np.inf
    for it in range(1, max_iter + 1):
        Mn = project(Yk + step * (Ha + demag(Yk) - Yk / chi))
        res = np.abs(Mn - M).max() / ms_max
        tn = 0.5 * (1 + math.sqrt(1 + 4 * t * t))
        Yk = Mn + (t - 1) / tn * (Mn - M)
        M, t = Mn, tn
        if res < tol:
            break
    return Ferrites(lo=lo, d=d, n=n, fill=fill, M=M, H=Ha + demag(M), Ha=Ha,
                    Ms=Ms, iterations=it, residual=res, applied=applied)


def _wrap(k, n):
    """The kernel k (offsets -(n - 1)..(n - 1) per axis) as the padded
    grid's (2 n) circular layout: offset o at o mod 2 n."""
    out = np.zeros(tuple(2 * m for m in n))
    idx = [np.r_[np.arange(m), np.arange(2 * m - (m - 1), 2 * m)] for m in n]
    src = [np.r_[np.arange(m - 1, 2 * m - 1), np.arange(0, m - 1)]
           for m in n]
    out[np.ix_(*idx)] = k[np.ix_(*src)]
    return out


def magnetised(board):
    """The drawn ferrites' names when a magnet is drawn too ([] when there is
    no ferrite or no magnet: an unbiased ferrite is a dielectric to every
    solver)."""
    sol = getattr(board, 'solids', None) or []
    if not any(d.get('material') == 'magnet' and d.get('br') for d in sol):
        return []
    return [str(d.get('shape') or 'ferrite') for d in sol
            if d.get('material') == 'ferrite']


def refusal(board, solver):
    """A solver's assess() reason for a magnetised ferrite it cannot model
    (None: none drawn)."""
    names = magnetised(board)
    if not names:
        return None
    return (f"drawn ferrite {', '.join(names[:3])} under a magnet: a "
            f"gyrotropic medium, which {solver} does not model; run gfdtd "
            "(--solvers gfdtd), or leave the magnet out (the ferrite a "
            "dielectric)")


def ms_si(ms_g):
    """Saturation magnetisation (A/m) of 4 pi Ms in gauss."""
    return float(ms_g) * GAUSS / MU0


def alpha(dh_oe, f=9.4e9, g=2.0):
    """Gilbert's damping of a ferrite whose FMR linewidth (full width at half
    maximum, Oe) is dh_oe at f Hz (the datasheets': 9.4 GHz): dH = 2 alpha w /
    gamma."""
    return GAMMA * g / 2 * float(dh_oe) * OE / (2 * float(f))


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
