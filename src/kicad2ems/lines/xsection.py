"""
A 2D quasi-static field solver for line cross-sections: the per-unit-length Maxwell capacitance of N
conductors over planes, in any stack of dielectrics, with and without the dielectric (C, C_air), and
from them L, Z0, eeff and the even / odd modes of a pair. It covers what the closed-form formulas
cannot: coplanar ground under solder mask or between two planes, coupled lines with copper thickness,
mask that follows the trace, coupling beyond the nearest neighbour, mixed dielectrics.

The method: Laplace's equation div(er grad phi) = 0 on a mesh of rectangles, bilinear finite elements, the box around
the copper grounded. The cells start on the key lines (copper edges, layers, planes) and are halved in x or z only
where they are larger than a size that grows away from the copper's corners, so a corner's fine cells stay near it and
long thin cells fill the layers (mom25d's mesher in 2D: cells growing from the features, edges kept fine). A node on a
coarser neighbour's side is tied to that side's ends, and the matrix reduced to the nodes left, T^T K T (as quads.py
reduces mom25d's). The charge on a conductor is the net flux out of its nodes, so C is symmetric. Dielectric loss is
the perturbation G / w = eps0 Phi^T K_tand Phi of the lossless solution. The tensor grid the solver began on is kept
(MESH = False, _solve_on_grid): the independent check of the mesh, and what potential() solves on.

    xs = CrossSection(dielectrics=[Dielectric(0.0, 0.2, 4.3, 0.02)], planes=[0.0])
    xs.conductor(-0.175, 0.175, 0.2, 0.235)                     # a 0.35 mm strip, 35 um copper
    r = xs.solve()
    z0, eeff = r.single()                                       # one conductor
    f = field(xs)                                               # the field, to look at: f.sample(...), on the mesh
    C, Ca = r.C, r.Ca                                           # F/m, N x N (coupled.mtl_s takes them)

    microstrip(0.35, 0.2, 4.3, t=0.035).solve().single()        # the common cross-sections, built
    coupled_microstrip(0.15, 0.15, 0.2, 4.3).solve().pair()     # (Ze, Zo, eeff_even, eeff_odd)

Units: mm for the geometry, SI for the results (C, L, G per metre). Quasi-static: no dispersion and no
conductor loss. Copper outside the box, and plane thickness, are not modelled (a plane is a sheet at the
height given, its surface facing the lines).
"""
import math
from dataclasses import dataclass, field
import numpy as np
from ..constants import C0, EPS0

MESH = True             # the mesh of rectangles (False: the tensor grid)
MESH_GROW = 0.4         # the mesh: cell size / distance from the nearest copper corner
MESH_FINE = 1 / 128     # the cell at a corner, as a fraction of the feature there (nearest other corner, or layer)
MESH_FAR = 0.01         # the cell at a corner d from the signal copper: at least MESH_FAR x d
MESH_SPLIT = 1.5        # a cell larger than this x the size it may have is halved
MESH_MIX = 0.5          # extrapolation, X + MESH_MIX (X - X_coarse), from the mesh with GROW and FINE doubled. The
                        # corners' error is first order in the cell (weight 1), the grading's second order in
                        # GROW (weight 1 / 3): 0.5 suits both, measured on a real board's cuts
GROW = 1.3              # the tensor grid: cells grow by at most this factor away from an edge
FINE = 1 / 16           # the smallest cell, as a fraction of the smallest feature (width, gap, layer, copper)
FAR = 0.1               # the cells at a copper edge or a layer d from the signal copper: at least FAR x d
REACH = 24.0            # the box reaches this many times the structure's height beyond it (open sides, open top):
                        # its walls take 0.05 % off an open microstrip's Z0 (at 12: 0.18 %)
REFINE = True           # extrapolate from two solves (solve)
SIMILAR = 0.01          # snap(): sizes within this fraction of each other share one value, so that the line model's
                        # caches solve cross-sections this similar once (Z0 moves by less than about a third of it)


@dataclass
class Dielectric:
    """er and tan d between heights z0 and z1 (mm), from x0 to x1 (a layer by default: the whole width)."""
    z0: float
    z1: float
    er: float
    tand: float = 0.0
    x0: float = -math.inf
    x1: float = math.inf


@dataclass
class Conductor:
    """A rectangle of copper (mm): a signal conductor, or ground (coplanar ground, held at 0 V; x0 or x1 may be
    infinite: to the side of the box)."""
    x0: float
    x1: float
    z0: float
    z1: float
    ground: bool = False


@dataclass
class CrossSection:
    """Planes (heights of full-width ground sheets), dielectrics (later ones paint over earlier ones;
    air elsewhere) and conductors. Signal conductors are numbered in the order they were added."""
    dielectrics: list = field(default_factory=list)
    planes: list = field(default_factory=list)
    conductors: list = field(default_factory=list)

    def conductor(self, x0, x1, z0, z1=None, ground=False):
        """Add a rectangle of copper (z1 None: a zero-thickness strip at z0)."""
        self.conductors.append(Conductor(x0, x1, z0, z0 if z1 is None else z1, ground))
        return self

    @property
    def signals(self):
        return [c for c in self.conductors if not c.ground]

    def solve(self):
        return solve(self)

    def to_dict(self):
        """The cross-section as plain numbers, for a JSON file (from_dict reads it); an infinite end is None."""
        end = lambda v: v if math.isfinite(v) else None
        return dict(dielectrics=[[d.z0, d.z1, d.er, d.tand, end(d.x0), end(d.x1)] for d in self.dielectrics],
                    planes=list(self.planes),
                    conductors=[[end(c.x0), end(c.x1), c.z0, c.z1, c.ground] for c in self.conductors])

    @classmethod
    def from_dict(cls, d):
        lo = lambda v: -math.inf if v is None else v
        hi = lambda v: math.inf if v is None else v
        return cls([Dielectric(z0, z1, er, tand, lo(x0), hi(x1)) for z0, z1, er, tand, x0, x1 in d['dielectrics']],
                   list(d['planes']),
                   [Conductor(lo(x0), hi(x1), z0, z1, ground) for x0, x1, z0, z1, ground in d['conductors']])


@dataclass
class Result:
    """C, Ca (F/m, N x N Maxwell matrices with and without the dielectric), G_w (S/m per rad/s: G = w G_w)."""
    C: np.ndarray
    Ca: np.ndarray
    G_w: np.ndarray
    nodes: int = 0

    @property
    def L(self):
        """Inductance matrix (H/m): mu0 eps0 Ca^-1."""
        return np.linalg.inv(self.Ca) / C0 ** 2

    def single(self, i=0):
        """(Z0, eeff) of conductor i alone, the others held at ground."""
        c, ca = self.C[i, i], self.Ca[i, i]
        return 1 / (C0 * math.sqrt(c * ca)), c / ca

    def pair(self):
        """(Ze, Zo, eeff_even, eeff_odd) of a symmetric pair (conductors 0 and 1), as coupled.coupled gives
        them; the differential impedance is 2 Zo, the common-mode Ze / 2."""
        out = []
        for s in (1, -1):
            c = self.C[0, 0] + s * self.C[0, 1]
            ca = self.Ca[0, 0] + s * self.Ca[0, 1]
            out.append((1 / (C0 * math.sqrt(c * ca)), c / ca))
        (ze, ee), (zo, eo) = out
        return ze, zo, ee, eo

    def tand_eff(self, i=0):
        """Effective loss tangent of conductor i's line: G / (w C)."""
        return self.G_w[i, i] / self.C[i, i]


def snap(v, rel=None):
    """v on a log scale of steps 1 + rel (SIMILAR by default), at the middle of its step: values within rel / 2 of
    a step's middle become it. 0 and infinities are kept, the sign too."""
    rel = SIMILAR if rel is None else rel
    if not rel or not v or not math.isfinite(v):
        return v
    k = math.log1p(rel)
    return math.copysign(math.exp(round(math.log(abs(v)) / k) * k), v)


# ---- the grid ---------------------------------------------------------------------------------------
def _fill(a, b, ha, hb, hmax):
    """Grid lines strictly between a and b: cells from ha at a and hb at b, growing by GROW up to hmax."""
    L = b - a
    if L <= 0.75 * (ha + hb):
        return []
    left, right, sl, sr = [], [], 0.0, 0.0
    while sl + sr < L:                                  # the smaller side grows first: they meet in between
        if sl <= sr or (right and right[-1] >= hmax and left and left[-1] < right[-1]):
            h = min(left[-1] * GROW if left else ha, hmax)
            left.append(h)
            sl += h
        else:
            h = min(right[-1] * GROW if right else hb, hmax)
            right.append(h)
            sr += h
    steps = np.asarray(left + right[::-1]) * (L / (sl + sr))
    return list(a + np.cumsum(steps)[:-1])


def _axis(keys, sizes, hmax):
    """Sorted grid lines through every key coordinate, graded between them from each key's cell size."""
    keys, sizes = np.asarray(keys, float), np.asarray(sizes, float)
    order = np.argsort(keys)
    keys, sizes = keys[order], sizes[order]
    keep = np.concatenate([[True], np.diff(np.round(keys, 9)) > 0])
    ks, hs = [], []
    for k, h, new in zip(keys, sizes, keep):
        if new:
            ks.append(k)
            hs.append(h)
        else:
            hs[-1] = min(hs[-1], h)
    out = [ks[0]]
    for i in range(len(ks) - 1):
        out += _fill(ks[i], ks[i + 1], hs[i], hs[i + 1], hmax) + [ks[i + 1]]
    return np.asarray(out)


def _extent(xs):
    """(xl, xr, zb, zt, H, span_z): the box around the cross-section, its structure's size, and its height between the
    nearest planes."""
    cs = xs.conductors
    if not xs.signals:
        raise ValueError("a cross-section needs a signal conductor")
    zs = [c.z0 for c in cs] + [c.z1 for c in cs]
    lo, hi = min(zs), max(zs)
    below = [p for p in xs.planes if p <= lo + 1e-12]
    above = [p for p in xs.planes if p >= hi - 1e-12]
    span_z = (max(above) if above else hi) - (min(below) if below else lo)
    fin = [v for c in cs for v in (c.x0, c.x1) if math.isfinite(v)]
    xa, xb = min(fin), max(fin)
    H = max(span_z, xb - xa, 1e-3)
    zb = max(below) if below else lo - REACH * H       # the nearest plane closes the box (none beyond it counts)
    zt = min(above) if above else hi + REACH * H
    return xa - REACH * H, xb + REACH * H, zb, zt, H, span_z


def _grid(xs):
    """(x lines, z lines) of the box around the cross-section."""
    xl, xr, zb, zt, H, span_z = _extent(xs)
    cs = xs.conductors
    zs = [c.z0 for c in cs] + [c.z1 for c in cs]
    lo, hi = min(zs), max(zs)
    fin = [v for c in cs for v in (c.x0, c.x1) if math.isfinite(v)]
    feats = [c.x1 - c.x0 for c in cs] + [c.z1 - c.z0 for c in cs]
    edges = sorted(v for v in set(fin) if xl < v < xr)
    for a in cs:                                        # the gaps between conductors side by side (edges of
        for b in cs:                                    # copper on other levels only add grid lines)
            if a is not b and a.z0 <= b.z1 + 1e-9 and b.z0 <= a.z1 + 1e-9 and b.x0 >= a.x1:
                feats.append(b.x0 - a.x1)
    zk = [zb, zt] + zs + [p for p in xs.planes if zb < p < zt]
    for d in xs.dielectrics:
        zk += [min(max(d.z0, zb), zt), min(max(d.z1, zb), zt)]
        feats.append(d.z1 - d.z0)
    zk_s = np.unique(np.round(zk, 9))
    feats += list(np.diff(zk_s[(zk_s > lo - span_z - 1e-9) & (zk_s < hi + span_z + 1e-9)]))
    xk = [xl, xr] + edges
    for d in xs.dielectrics:
        xk += [x for x in (d.x0, d.x1) if xl < x < xr]
    sig = xs.signals
    sx = (min(c.x0 for c in sig), max(c.x1 for c in sig))
    sz = (min(c.z0 for c in sig), max(c.z1 for c in sig))
    near = lambda v, span: max(span[0] - v, v - span[1], 0.0)          # distance from the signal copper
    h0 = FINE * min(f for f in feats if f > 1e-9)
    hmax = max(H, h0)
    size = lambda v, span: min(max(h0, FAR * near(v, span)), hmax)      # cells coarser away from the signal
    return _axis(xk, [size(v, sx) for v in xk], hmax), _axis(zk, [size(v, sz) for v in zk], hmax)


# ---- the solve --------------------------------------------------------------------------------------
def _laplacian(x, z, eps):
    """The finite-volume operator (nodes x nodes, sparse) for cell values eps (nx-1, nz-1): the flux between
    two neighbouring nodes is eps over the half cells either side of the link, times the dual edge over
    the link length."""
    from scipy.sparse import coo_matrix
    nx, nz = len(x), len(z)
    dx, dz = np.diff(x), np.diff(z)
    idx = np.arange(nx * nz).reshape(nx, nz)
    ez = np.zeros((nx - 1, nz + 1))                      # cells padded with zero-eps rows above and below
    ez[:, 1:-1] = eps * dz[None, :]
    gx = 0.5 * (ez[:, :-1] + ez[:, 1:]) / dx[:, None]    # (nx-1, nz): links along x
    ex = np.zeros((nx + 1, nz - 1))
    ex[1:-1, :] = eps * dx[:, None]
    gz = 0.5 * (ex[:-1, :] + ex[1:, :]) / dz[None, :]    # (nx, nz-1): links along z
    a = np.concatenate([idx[:-1, :].ravel(), idx[:, :-1].ravel()])
    b = np.concatenate([idx[1:, :].ravel(), idx[:, 1:].ravel()])
    g = np.concatenate([gx.ravel(), gz.ravel()])
    n = nx * nz
    rows = np.concatenate([a, b, a, b])
    cols = np.concatenate([a, b, b, a])
    vals = np.concatenate([g, g, -g, -g])
    return coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()


def _paint(xs, cx, cz):
    """(er, er tan d) per cell of centres cx, cz: air, then each dielectric in turn over the cells whose centres it
    holds."""
    er, et = np.ones_like(cx), np.zeros_like(cx)
    for d in xs.dielectrics:
        m = (cx > d.x0) & (cx < d.x1) & (cz > d.z0) & (cz < d.z1)
        er[m], et[m] = d.er, d.er * d.tand
    return er, et


def _owners(xs, x, z, box):
    """Per node at x, z: -1 free, 0 ground (box = xl, xr, zb, zt, the planes, ground conductors), k >= 1 signal
    conductor k - 1."""
    tol = 1e-9
    xl, xr, zb, zt = box
    own = np.full(x.shape, -1, int)
    own[(x == xl) | (x == xr) | (z == zb) | (z == zt)] = 0
    for p in xs.planes:
        own[np.abs(z - p) < tol] = 0
    k = 0
    for c in xs.conductors:
        m = (x >= c.x0 - tol) & (x <= c.x1 + tol) & (z >= c.z0 - tol) & (z <= c.z1 + tol)
        if not m.any():
            raise ValueError(f"conductor {c} falls between grid lines")
        if c.ground:
            own[m] = 0
        else:
            k += 1
            if (own[m] >= 0).any():
                raise ValueError(f"conductor {c} touches ground or another conductor")
            own[m] = k
    return own


def _grid_cells(xs, x, z):
    """What a tensor grid's solve needs: (er, er tan d per cell (nx-1, nz-1), the nodes' owners, raveled)."""
    xc, zc = 0.5 * (x[:-1] + x[1:]), 0.5 * (z[:-1] + z[1:])
    er, et = _paint(xs, *np.meshgrid(xc, zc, indexing='ij'))
    return er, et, _owners(xs, *np.meshgrid(x, z, indexing='ij'), (x[0], x[-1], z[0], z[-1])).ravel()


def _capacitance(stiffness, own, er, et):
    """(C, Ca, G_w) from stiffness(cell values), own (per node: -1 free, 0 ground, k signal conductor k - 1): one sparse
    factorisation with the dielectric and one without (a single dielectric: Ca is C / er), each solved for every signal
    conductor at 1 V with the others at 0."""
    from scipy.sparse.linalg import splu
    n = int(own.max())
    free, fixed = np.flatnonzero(own < 0), np.flatnonzero(own >= 0)
    V = np.zeros((own.size, n))                          # the excitations on the fixed nodes
    for k in range(n):
        V[own == k + 1, k] = 1.0

    def charge(eps):
        A = stiffness(eps)
        lu = splu(A[free][:, free].tocsc(), permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0,
                  options=dict(SymmetricMode=True))           # symmetric positive definite: no pivoting
        phi = V.copy()
        phi[free] = lu.solve(-(A[free][:, fixed] @ V[fixed]))
        Q = A @ phi                                      # net flux out of each node: its charge / eps0
        C = np.array([Q[own == i + 1].sum(0) for i in range(n)]) * EPS0
        return 0.5 * (C + C.T), phi
    C, phi = charge(er)
    Ca = C / er.flat[0] if (er == er.flat[0]).all() else charge(np.ones_like(er))[0]
    G_w = EPS0 * (phi.T @ (stiffness(et) @ phi)) if et.any() else np.zeros_like(C)
    return C, Ca, 0.5 * (G_w + G_w.T)


def _solve_grid(xs, x, z):
    """(C, Ca, G_w, nodes) on one grid."""
    er, et, own = _grid_cells(xs, x, z)
    return (*_capacitance(lambda eps: _laplacian(x, z, eps), own, er, et), own.size)


def potential(xs, volts=None, air=False):
    """(x, z, phi, er): the grid lines (mm), the potential on its nodes (len(x), len(z)) with the signal conductors at
    `volts` (default 1 V on the first, 0 on the others; [1, -1]: a pair's odd mode) and every ground at 0, and er per
    cell; air: without the dielectric. One solve on the grid (no extrapolation): for looking at the field."""
    from scipy.sparse.linalg import spsolve
    x, z = _grid(xs)
    er, _, own = _grid_cells(xs, x, z)
    n = int(own.max())
    volts = [1.0] + [0.0] * (n - 1) if volts is None else list(volts)
    A = _laplacian(x, z, np.ones_like(er) if air else er)
    phi = np.zeros(own.size)
    for k, v in enumerate(volts):
        phi[own == k + 1] = v
    free, fixed = np.flatnonzero(own < 0), np.flatnonzero(own >= 0)
    phi[free] = spsolve(A[free][:, free].tocsc(), -(A[free][:, fixed] @ phi[fixed]))
    return x, z, phi.reshape(len(x), len(z)), er


def _bisect(a):
    return np.sort(np.concatenate([a, 0.5 * (a[:-1] + a[1:])]))


def _solve_on_grid(xs):
    """The Result on the tensor grid: the edges' field singularity makes its error first order in the cell size, so the
    solve is made on the grid and on the grid with every cell halved, and extrapolated (2 X_fine - X_coarse)."""
    x, z = _grid(xs)
    c1 = _solve_grid(xs, x, z)
    if not REFINE:
        return Result(*c1)
    c2 = _solve_grid(xs, _bisect(x), _bisect(z))
    return Result(*(2 * b - a for a, b in zip(c1[:3], c2[:3])), nodes=c2[3])


# ---- the mesh of rectangles -------------------------------------------------------------------------
def _sizes(xs, fine):
    """(the key lines in x and z, the corners (n, 2) the cells grow from, the cell allowed at each): every corner of
    copper and of a dielectric of limited width."""
    xl, xr, zb, zt = _extent(xs)[:4]
    cs = xs.conductors
    fin = [v for c in cs for v in (c.x0, c.x1) if math.isfinite(v)]
    xk = [xl, xr] + [v for v in fin if xl < v < xr]
    zk = [zb, zt] + [v for c in cs for v in (c.z0, c.z1)] + [p for p in xs.planes if zb < p < zt]
    pts = [(v, w) for c in cs for v in (c.x0, c.x1) if math.isfinite(v) for w in (c.z0, c.z1)]
    for d in xs.dielectrics:
        z0, z1 = min(max(d.z0, zb), zt), min(max(d.z1, zb), zt)
        zk += [z0, z1]
        xk += [v for v in (d.x0, d.x1) if xl < v < xr]
        pts += [(v, w) for v in (d.x0, d.x1) if xl < v < xr for w in (z0, z1) if zb < w < zt]
    xk, zk = np.unique(np.round(xk, 9)), np.unique(np.round(zk, 9))
    P = np.unique(np.round(pts, 9), axis=0)
    D = np.maximum(np.abs(P[:, None, 0] - P[None, :, 0]), np.abs(P[:, None, 1] - P[None, :, 1]))
    D[D < 1e-9] = np.inf
    dz = np.abs(P[:, None, 1] - zk[None, :])
    dz[dz < 1e-9] = np.inf
    feature = np.minimum(D.min(1), dz.min(1))            # the nearest other corner, or layer
    sig = xs.signals
    sx0, sx1 = min(c.x0 for c in sig), max(c.x1 for c in sig)
    sz0, sz1 = min(c.z0 for c in sig), max(c.z1 for c in sig)
    d = np.hypot(np.maximum(np.maximum(sx0 - P[:, 0], P[:, 0] - sx1), 0),
                 np.maximum(np.maximum(sz0 - P[:, 1], P[:, 1] - sz1), 0))
    return xk, zk, P, np.maximum(fine * feature, MESH_FAR * d)


def _mesh(xs, grow=None, fine=None):
    """The cells (n, 4: x0, x1, z0, z1): the tensor of the key lines, less the copper's inside, each halved in x or z
    while it is larger than the size it may have, grown from the nearest corner. Halving one interval always gives the
    same float, and every side comes from the key lines' intervals, so sides on one line are nested and nodes on them
    exactly equal."""
    grow, fine = MESH_GROW if grow is None else grow, MESH_FINE if fine is None else fine
    xk, zk, P, h0 = _sizes(xs, fine)
    X0, Z0 = np.meshgrid(xk[:-1], zk[:-1], indexing='ij')
    X1, Z1 = np.meshgrid(xk[1:], zk[1:], indexing='ij')
    cell = np.stack([X0.ravel(), X1.ravel(), Z0.ravel(), Z1.ravel()], 1)
    cx, cz = 0.5 * (cell[:, 0] + cell[:, 1]), 0.5 * (cell[:, 2] + cell[:, 3])
    inside = np.zeros(len(cell), bool)
    for c in xs.conductors:
        inside |= (c.z1 > c.z0) & (cx > c.x0) & (cx < c.x1) & (cz > c.z0) & (cz < c.z1)
    cell = cell[~inside]
    done = []
    while len(cell):
        dx = np.maximum(np.maximum(cell[:, 0, None] - P[:, 0], P[:, 0] - cell[:, 1, None]), 0)
        dz = np.maximum(np.maximum(cell[:, 2, None] - P[:, 1], P[:, 1] - cell[:, 3, None]), 0)
        h = MESH_SPLIT * (h0 + grow * np.hypot(dx, dz)).min(1)
        sx, sz = cell[:, 1] - cell[:, 0] > h, cell[:, 3] - cell[:, 2] > h
        done.append(cell[~(sx | sz)])
        cell = np.concatenate([_halved(cell[sx & ~sz], 0), _halved(cell[sz & ~sx], 2),
                               _halved(_halved(cell[sx & sz], 0), 2)])
    return np.concatenate(done)


def _halved(cell, axis):
    """The cells halved across column axis and axis + 1 (0: in x, 2: in z)."""
    m = 0.5 * (cell[:, axis] + cell[:, axis + 1])
    lo, hi = cell.copy(), cell.copy()
    lo[:, axis + 1] = hi[:, axis] = m
    return np.concatenate([lo, hi])


def _on_sides(order, key, line, a, b, scale):
    """Which nodes lie strictly inside which sides: (side, node) arrays, a side being the stretch a..b along a line (as
    integers), a node's key line x scale + position, order the nodes sorted by key."""
    ks = key[order]
    lo = np.searchsorted(ks, line * scale + a, 'right')
    hi = np.searchsorted(ks, line * scale + b, 'left')
    some = np.flatnonzero(hi > lo)
    if not len(some):
        return some, some
    n = hi[some] - lo[some]
    start = np.repeat(lo[some], n)
    step = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    return np.repeat(some, n), order[start + step]


def _hanging(x, z, ix, iz):
    """(T, independent): T, nodes x independent nodes (sparse), each hanging node as the interpolation of the ends of
    the longest side it lies inside, and those that hang on another side in turn, down to the independent nodes.
    ix, iz: the cells' columns x0, x1 and z0, z1 as integers on the sorted x, z."""
    from scipy.sparse import coo_matrix
    nx, nz = len(x), len(z)
    node = ix[:, [0, 1, 1, 0]] * nz + iz[:, [0, 0, 1, 1]]
    ids = np.unique(node)                                 # sorted by x, then z
    jx, jz = ids // nz, ids % nz
    n = len(ids)
    a, b, t, longest = np.zeros(n, int), np.zeros(n, int), np.zeros(n), np.zeros(n)
    by_z = np.lexsort((jx, jz))
    for key, order, scale, pos, line, s0, s1, along, at in (
            (ids, np.arange(n), nz, jz, ix.T.ravel(), iz[:, 0], iz[:, 1], z, lambda l, q: l * nz + q),
            (jz * nx + jx, by_z, nx, jx, iz.T.ravel(), ix[:, 0], ix[:, 1], x, lambda l, q: q * nz + l)):
        s0, s1 = np.tile(s0, 2), np.tile(s1, 2)           # both sides of every cell along this direction
        side, nd = _on_sides(order, key, line, s0, s1, scale)
        L = along[s1[side]] - along[s0[side]]
        k = np.argsort(L)                                 # the longest last, so that it stays
        side, nd, L = side[k], nd[k], L[k]
        keep = L > longest[nd]
        side, nd, L = side[keep], nd[keep], L[keep]
        longest[nd] = L
        a[nd] = np.searchsorted(ids, at(line[side], s0[side]))
        b[nd] = np.searchsorted(ids, at(line[side], s1[side]))
        t[nd] = (along[pos[nd]] - along[s0[side]]) / L
    hang = longest > 0
    h, own = np.flatnonzero(hang), np.flatnonzero(~hang)
    M = coo_matrix((np.concatenate([np.ones(len(own)), 1 - t[h], t[h]]),
                    (np.concatenate([own, h, h]), np.concatenate([own, a[h], b[h]]))), shape=(n, n)).tocsr()
    T = M
    for _ in range(64):
        if not hang[T.indices].any():
            break
        T = T @ M
    else:
        raise ValueError("the mesh's hanging nodes depend on each other")
    return T[:, own].tocsr(), own, np.searchsorted(ids, node)


_K_X = np.array([[2, -2, -1, 1], [-2, 2, 1, -1], [-1, 1, 2, -2], [1, -1, -2, 2]]) / 6.0     # a bilinear rectangle's
_K_Z = np.array([[2, 1, -1, -2], [1, 2, -2, -1], [-1, -2, 2, 1], [-2, -1, 1, 2]]) / 6.0     # stiffness, h / w and w / h


def _mesh_system(xs, cell):
    """What a solve on the cells needs: (stiffness(cell values), reduced to the independent nodes; their owners and
    x, z; er, er tan d per cell; T and each cell's four nodes, from the independent nodes back to the cells' corners)."""
    from scipy.sparse import coo_matrix
    x, ix = np.unique(cell[:, :2], return_inverse=True)
    z, iz = np.unique(cell[:, 2:], return_inverse=True)
    T, kept, nodes = _hanging(x, z, ix.reshape(-1, 2), iz.reshape(-1, 2))
    n = T.shape[0]
    rows, cols = np.repeat(nodes, 4, axis=1).ravel(), np.tile(nodes, (1, 4)).ravel()
    w, t = cell[:, 1] - cell[:, 0], cell[:, 3] - cell[:, 2]

    def stiffness(eps):
        K = (eps * t / w)[:, None, None] * _K_X + (eps * w / t)[:, None, None] * _K_Z
        return (T.T @ coo_matrix((K.ravel(), (rows, cols)), shape=(n, n)).tocsr() @ T).tocsr()
    px, pz = np.zeros(n), np.zeros(n)
    px[nodes.ravel()] = cell[:, [0, 1, 1, 0]].ravel()
    pz[nodes.ravel()] = cell[:, [2, 2, 3, 3]].ravel()
    own = _owners(xs, px, pz, (x[0], x[-1], z[0], z[-1]))
    return stiffness, own[kept], px[kept], pz[kept], _paint(xs, cell[:, :2].mean(1), cell[:, 2:].mean(1)), (T, nodes)


def _solve_mesh(xs, cell):
    """(C, Ca, G_w, nodes) on the mesh."""
    stiffness, own, _, _, (er, et), _ = _mesh_system(xs, cell)
    return (*_capacitance(stiffness, own, er, et), own.size)


def solve(xs):
    """The Result of a CrossSection: on the mesh, and on the mesh with its grading and corner cells twice as coarse,
    extrapolated (MESH_MIX: within 0.3 % of a converged solve on a real board's cuts, where the mesh alone is up to 0.7 %
    low; without the coarser solve, REFINE = False, 30 % faster)."""
    if not MESH:
        return _solve_on_grid(xs)
    c2 = _solve_mesh(xs, _mesh(xs))
    if not REFINE:
        return Result(*c2)
    c1 = _solve_mesh(xs, _mesh(xs, 2 * MESH_GROW, 2 * MESH_FINE))
    return Result(*(b + MESH_MIX * (b - a) for a, b in zip(c1[:3], c2[:3])), nodes=c2[3])


# ---- the field, to look at -----------------------------------------------------------------------------
class Field:
    """A solved potential on the mesh: cell (n, 4: x0, x1, z0, z1, mm), v (n, 4: the volts at its corners (x0, z0),
    (x1, z0), (x1, z1), (x0, z1)), er per cell. Copper has no cells."""
    def __init__(self, cell, v, er):
        self.cell, self.v, self.er = cell, v, er

    def sample(self, x0, x1, z0, z1, nx, nz):
        """(x, z, phi, ex, ez, er) at the centres of nx x nz pixels of a window, each array (nx, nz): the potential
        (V), the field (V/mm, -grad phi inside the cell: its normal part steps at a dielectric, as it does), er. NaN
        in copper and beyond the box."""
        x = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
        z = z0 + (np.arange(nz) + 0.5) * (z1 - z0) / nz
        phi, ex, ez, er = (np.full((nx, nz), np.nan) for _ in range(4))
        c = self.cell
        i0, i1 = np.searchsorted(x, c[:, 0]), np.searchsorted(x, c[:, 1])
        j0, j1 = np.searchsorted(z, c[:, 2]), np.searchsorted(z, c[:, 3])
        for k in np.flatnonzero((i1 > i0) & (j1 > j0)):       # most cells are smaller than a pixel, or off the window
            w, t = c[k, 1] - c[k, 0], c[k, 3] - c[k, 2]
            u, s = np.ix_((x[i0[k]:i1[k]] - c[k, 0]) / w, (z[j0[k]:j1[k]] - c[k, 2]) / t)
            a, b, d, e = self.v[k]                            # (x0, z0), (x1, z0), (x1, z1), (x0, z1)
            ij = slice(i0[k], i1[k]), slice(j0[k], j1[k])
            phi[ij] = (1 - u) * (1 - s) * a + u * (1 - s) * b + u * s * d + (1 - u) * s * e
            ex[ij] = -((1 - s) * (b - a) + s * (d - e)) / w
            ez[ij] = -((1 - u) * (e - a) + u * (d - b)) / t
            er[ij] = self.er[k]
        return x, z, phi, ex, ez, er

    def energy(self):
        """The stored energy (J/m): 1/2 C V^2 of these volts, on this one mesh."""
        c, v = self.cell, self.v
        w, t = c[:, 1] - c[:, 0], c[:, 3] - c[:, 2]
        return 0.5 * EPS0 * float(np.sum(self.er * (t / w * np.einsum('ni,ij,nj->n', v, _K_X, v) +
                                                    w / t * np.einsum('ni,ij,nj->n', v, _K_Z, v))))


def field(xs, volts=None, air=False, grow=None):
    """The Field of a CrossSection on its mesh, the signal conductors at `volts` (default 1 V on the first, 0 on the
    others; [1, -1]: a pair's odd mode), every ground at 0; air: without the dielectric; grow: the mesh's grading, if not
    MESH_GROW. One solve (no extrapolation): for looking at the field; solve() is what gives the numbers."""
    from scipy.sparse.linalg import spsolve
    cell = _mesh(xs, grow)
    stiffness, own, _, _, (er, _), (T, nodes) = _mesh_system(xs, cell)
    n = int(own.max())
    volts = [1.0] + [0.0] * (n - 1) if volts is None else list(volts)
    A = stiffness(np.ones_like(er) if air else er)
    phi = np.zeros(own.size)
    for k, v in enumerate(volts):
        phi[own == k + 1] = v
    free, fixed = np.flatnonzero(own < 0), np.flatnonzero(own >= 0)
    phi[free] = spsolve(A[free][:, free].tocsc(), -(A[free][:, fixed] @ phi[fixed]))
    return Field(cell, (T @ phi)[nodes], np.ones_like(er) if air else er)


def window(xs):
    """(x0, x1, z0, z1): what is worth looking at, of a cross-section whose box may be many times larger: the signal
    conductors and a few heights of the nearest copper to either side, up to the planes that close the box."""
    sig = xs.signals
    xa, xb = min(c.x0 for c in sig), max(c.x1 for c in sig)
    lo, hi = min(c.z0 for c in sig), max(c.z1 for c in sig)
    levels = list(xs.planes) + [v for c in xs.conductors if c.ground for v in (c.z0, c.z1)]
    gaps = [lo - v for v in levels if v < lo - 1e-9] + [v - hi for v in levels if v > hi + 1e-9]
    h = min(gaps) if gaps else (xb - xa)
    m = max(3 * h, 0.75 * (xb - xa))
    box = _extent(xs)
    return xa - m, xb + m, max(box[2], lo - m), min(box[3], hi + m)


# ---- the common cross-sections ----------------------------------------------------------------------
def _strips(xs, widths, gaps, z, t):
    """Side-by-side signal strips at height z (thickness t), centred on x = 0; returns their x edges."""
    total = sum(widths) + sum(gaps)
    x, edges = -total / 2, []
    for i, w in enumerate(widths):
        xs.conductor(x, x + w, z, z + t)
        edges.append((x, x + w))
        x += w + (gaps[i] if i < len(gaps) else 0.0)
    return edges


def _ground_beside(xs, edges, s, z, t, wg=None):
    """Coplanar ground s beyond the outer edges, wg wide (None: to the box)."""
    big = math.inf if wg is None else wg
    xs.conductor(edges[0][0] - s - big, edges[0][0] - s, z, z + t, ground=True)
    xs.conductor(edges[-1][1] + s, edges[-1][1] + s + big, z, z + t, ground=True)


def microstrip(w, h, er, t=0.0, tand=0.0, mask=0.0, er_mask=3.5, s=None, wg=None, gap=0.0):
    """Microstrip w wide on a substrate h thick over a plane (one strip; widths and gap below for more).
    mask: solder mask thickness over the substrate and the copper (following the strip); s: coplanar ground
    s from each side (grounded CPW), wg wide (None: to the box). Pass w as a list (and gap as a list or one
    value) for coupled strips."""
    widths = list(w) if np.ndim(w) else [w]
    gaps = list(gap) if np.ndim(gap) else [gap] * (len(widths) - 1)
    xs = CrossSection(dielectrics=[Dielectric(0.0, h, er, tand)], planes=[0.0])
    edges = _strips(xs, widths, gaps, h, t)
    if s is not None:
        _ground_beside(xs, edges, s, h, t, wg)
    if mask > 0:
        xs.dielectrics.append(Dielectric(h, h + mask, er_mask))
        for c in xs.conductors:                          # the mask follows the copper: over its top and sides
            xs.dielectrics.append(Dielectric(c.z1, c.z1 + mask, er_mask, 0.0, c.x0 - mask, c.x1 + mask))
    return xs


def stripline(w, h1, h2, er, t=0.0, tand=0.0, s=None, wg=None, gap=0.0):
    """A strip h1 above one plane, its top h2 below the other (centred when h1 = h2 + ...: offset otherwise),
    in one dielectric; s, wg, w as a list and gap as for microstrip."""
    widths = list(w) if np.ndim(w) else [w]
    gaps = list(gap) if np.ndim(gap) else [gap] * (len(widths) - 1)
    top = h1 + t + h2
    xs = CrossSection(dielectrics=[Dielectric(0.0, top, er, tand)], planes=[0.0, top])
    edges = _strips(xs, widths, gaps, h1, t)
    if s is not None:
        _ground_beside(xs, edges, s, h1, t, wg)
    return xs


def coupled_microstrip(w, s, h, er, t=0.0, **kw):
    """A symmetric edge-coupled microstrip pair: strips w, gap s (Result.pair() gives the modes)."""
    return microstrip([w, w], h, er, t, gap=s, **kw)


def coupled_stripline(w, s, h1, h2, er, t=0.0, **kw):
    """A symmetric edge-coupled stripline pair between planes (h1 below the strips, h2 above them)."""
    return stripline([w, w], h1, h2, er, t, gap=s, **kw)
