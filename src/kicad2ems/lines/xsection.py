"""
A 2D quasi-static field solver for line cross-sections: the per-unit-length Maxwell capacitance of N
conductors over planes, in any stack of dielectrics, with and without the dielectric (C, C_air), and
from them L, Z0, eeff and the even / odd modes of a pair. It covers what the closed-form formulas
cannot: coplanar ground under solder mask or between two planes, coupled lines with copper thickness,
mask that follows the trace, coupling beyond the nearest neighbour, mixed dielectrics.

The method: Laplace's equation div(er grad phi) = 0 by finite volumes on a tensor grid graded towards
every conductor edge, the box around it grounded. Conductors and planes are rectangles on grid lines
(a zero-thickness strip is a row of nodes), dielectrics are painted onto the cells. The charge on a
conductor is the net flux out of its nodes, so Gauss's law holds exactly on the grid and C is symmetric.
Dielectric loss is the perturbation G / w = eps0 Phi^T A_tand Phi of the lossless solution.

    xs = CrossSection(dielectrics=[Dielectric(0.0, 0.2, 4.3, 0.02)], planes=[0.0])
    xs.conductor(-0.175, 0.175, 0.2, 0.235)                     # a 0.35 mm strip, 35 um copper
    r = xs.solve()
    z0, eeff = r.single()                                       # one conductor
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

GROW = 1.25             # grid cells grow by at most this factor away from an edge
FINE = 1 / 24           # the smallest cell, as a fraction of the smallest feature (width, gap, layer, copper)
REACH = 25.0            # the box reaches this many times the structure's height beyond it (open sides, open top)
REFINE = True           # extrapolate from the grid and the grid halved (solve)


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


# ---- the grid ---------------------------------------------------------------------------------------
def _fill(a, b, h0, hmax):
    """Grid lines strictly between a and b: cells from h0 at both ends, growing by GROW up to hmax."""
    L = b - a
    if L <= 1.5 * h0:
        return []
    steps, s, h = [], 0.0, h0
    while s < L / 2:
        steps.append(h)
        s += h
        h = min(h * GROW, hmax)
    steps = steps + steps[::-1]
    steps = np.asarray(steps) * (L / sum(steps))
    return list(a + np.cumsum(steps)[:-1])


def _axis(keys, h0, hmax):
    """Sorted grid lines through every key coordinate, graded between them."""
    keys = np.unique(np.round(np.asarray(keys, float), 9))
    out = [keys[0]]
    for a, b in zip(keys[:-1], keys[1:]):
        out += _fill(a, b, h0, hmax) + [b]
    return np.asarray(out)


def _grid(xs):
    """(x lines, z lines) of the box around the cross-section."""
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
    xl, xr = xa - REACH * H, xb + REACH * H
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
    h0 = FINE * min(f for f in feats if f > 1e-9)
    hmax = max(H, h0)
    return _axis(xk, h0, hmax), _axis(zk, h0, hmax)


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


def _paint(xs, x, z):
    """(er, er tan d) per cell: air, then each dielectric in turn over the cells whose centres it holds."""
    xc, zc = 0.5 * (x[:-1] + x[1:]), 0.5 * (z[:-1] + z[1:])
    er = np.ones((len(xc), len(zc)))
    et = np.zeros_like(er)
    for d in xs.dielectrics:
        m = ((xc > d.x0) & (xc < d.x1))[:, None] & ((zc > d.z0) & (zc < d.z1))[None, :]
        er[m] = d.er
        et[m] = d.er * d.tand
    return er, et


def _owners(xs, x, z):
    """Per node: -1 free, 0 ground (the box, the planes, ground conductors), k >= 1 signal conductor k - 1."""
    tol = 1e-9
    own = np.full((len(x), len(z)), -1, int)
    own[0, :] = own[-1, :] = own[:, 0] = own[:, -1] = 0
    for p in xs.planes:
        own[:, np.abs(z - p) < tol] = 0
    k = 0
    for c in xs.conductors:
        mx = (x >= c.x0 - tol) & (x <= c.x1 + tol)
        mz = (z >= c.z0 - tol) & (z <= c.z1 + tol)
        if not mx.any() or not mz.any():
            raise ValueError(f"conductor {c} falls between grid lines")
        if c.ground:
            own[np.ix_(mx, mz)] = 0
        else:
            k += 1
            if (own[np.ix_(mx, mz)] >= 0).any():
                raise ValueError(f"conductor {c} touches ground or another conductor")
            own[np.ix_(mx, mz)] = k
    return own.ravel()


def _solve_grid(xs, x, z):
    """(C, Ca, G_w, nodes) on one grid: one sparse factorisation with the dielectric and one without, each
    solved for every signal conductor at 1 V with the others at 0."""
    from scipy.sparse.linalg import splu
    er, et = _paint(xs, x, z)
    own = _owners(xs, x, z)
    n = int(own.max())
    free = np.flatnonzero(own < 0)
    fixed = np.flatnonzero(own >= 0)
    V = np.zeros((own.size, n))                          # the excitations on the fixed nodes
    for k in range(n):
        V[own == k + 1, k] = 1.0
    out = []
    for eps in (er, np.ones_like(er)):
        A = _laplacian(x, z, eps)
        lu = splu(A[free][:, free].tocsc())
        phi = V.copy()
        phi[free] = lu.solve(-(A[free][:, fixed] @ V[fixed]))
        Q = A @ phi                                      # net flux out of each node: its charge / eps0
        C = np.array([[Q[own == i + 1, j].sum() for j in range(n)] for i in range(n)]) * EPS0
        out.append((0.5 * (C + C.T), phi))
    (C, phi), (Ca, _) = out
    G_w = EPS0 * (phi.T @ (_laplacian(x, z, et) @ phi))
    return C, Ca, 0.5 * (G_w + G_w.T), own.size


def _bisect(a):
    return np.sort(np.concatenate([a, 0.5 * (a[:-1] + a[1:])]))


def solve(xs):
    """The Result of a CrossSection. The edges' field singularity makes the grid's error first order in the
    cell size, so the solve is made on the grid and on the grid with every cell halved, and extrapolated
    (2 X_fine - X_coarse: Z0 within ~0.2 % of exact results where a coarse solve is ~2 % low)."""
    x, z = _grid(xs)
    c1 = _solve_grid(xs, x, z)
    if not REFINE:
        return Result(*c1)
    c2 = _solve_grid(xs, _bisect(x), _bisect(z))
    return Result(*(2 * b - a for a, b in zip(c1[:3], c2[:3])), nodes=c2[3])


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
