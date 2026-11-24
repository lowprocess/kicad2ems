"""The cavity's eigenmodes by finite elements.

Linear triangles give K v = k^2 M v (K: stiffness, M: consistent mass), with
V = 0 at ground vias and open edges elsewhere. The modes in [fmin, fmax] come
from shift-invert Lanczos (ARPACK) about the window: the shifted matrix is
factorised once (SuperLU, or PARDISO when installed) and the number of modes
asked for grows until the window is covered - asking for a guess from the
area alone wastes most of the time on well-stitched boards.
"""
import math
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, eigsh, splu

MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12
C0 = 1 / math.sqrt(MU0 * EPS0)
SIGMA_CU = 5.8e7          # S/m, copper
STATIC = 1e6              # Hz: modes below this are islands' static modes


@dataclass
class SolverConfig:
    backend: str = 'auto'          # 'superlu', 'pardiso' or 'auto' (see _factor)
    max_modes: int = 600           # per plane pair
    tol: float = 0.0               # ARPACK tolerance (0: machine precision)


@dataclass
class Modes:
    f: np.ndarray                  # (m,) Hz
    Q: np.ndarray                  # (m,) dielectric + copper loss (no radiation: an upper bound)
    V: np.ndarray                  # (N, m) voltage at the nodes, RMS 1 over the cavity
    pr: np.ndarray                 # (m,) participation: 1 fills the cavity, ~0 local
    peak: np.ndarray               # (m,) max |V| (x RMS)
    coupling: np.ndarray           # (ports, m) mean |V| over each port via's ring
    zpeak: np.ndarray              # (ports, m) ohm: the cavity's impedance at the port at resonance
    capped: bool = False           # True if max_modes stopped the search early


def assemble(nodes_mm, tris):
    """Stiffness K (dimensionless) and consistent mass M (m^2) of linear
    triangles, and each node's lumped area (m^2)."""
    P = nodes_mm[tris] * 1e-3
    d1, d2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
    area = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    # gradients of the three hat functions (constant per triangle)
    e = np.stack([P[:, 2] - P[:, 1], P[:, 0] - P[:, 2], P[:, 1] - P[:, 0]], 1)
    g = np.stack([-e[..., 1], e[..., 0]], -1) / (2 * area[:, None, None])
    ke = area[:, None, None] * np.einsum('tik,tjk->tij', g, g)
    me = area[:, None, None] / 12 * (np.ones((3, 3)) + np.eye(3))
    rows = np.repeat(tris, 3, axis=1).ravel()
    cols = np.tile(tris, (1, 3)).ravel()
    n = len(nodes_mm)
    K = sp.csr_matrix((ke.ravel(), (rows, cols)), shape=(n, n))
    M = sp.csr_matrix((me.ravel(), (rows, cols)), shape=(n, n))
    lump = np.bincount(tris.ravel(), np.repeat(area / 3, 3), minlength=n)
    return K, M, lump


PARDISO_FROM = 400_000     # unknowns: 'auto' uses PARDISO above this (if installed)


def _factor(A, backend):
    """A function solving A x = b, A factorised once. The eigen-solve is
    hundreds of single right-hand-side solves, memory-bound: PARDISO factorises
    2-3.6x faster but each solve gains little from threads and pays the
    wrapper's overhead, so it only wins on large meshes (measured: 3x slower at
    35k unknowns, 15 % faster at 830k)."""
    if backend == 'pardiso' or (backend == 'auto' and A.shape[0] > PARDISO_FROM):
        try:
            import pypardiso
            ps = pypardiso.PyPardisoSolver(mtype=11)
            A = A.tocsr(); A.sort_indices()
            ps.factorize(A)
            ps._is_already_factorized = lambda _A: True    # skip its per-solve check
            return lambda b: ps.solve(A, b)
        except ImportError:
            if backend == 'pardiso':
                raise
    return splu(A.tocsc()).solve


def k2(f, er):
    """Wavenumber squared (1/m^2) at frequency f in dielectric er."""
    return (2 * math.pi * f) ** 2 * MU0 * EPS0 * er


def solve_modes(mesh, cav, fmin, fmax, cfg=SolverConfig()):
    """The cavity's modes with fmin <= f <= fmax."""
    K, M, lump = assemble(mesh.nodes, mesh.tris)
    free = np.flatnonzero(~mesh.fixed)
    Kf, Mf = K[free][:, free].tocsc(), M[free][:, free].tocsc()
    lo, hi = k2(fmin, cav.er), k2(fmax, cav.er)
    # shift: below the spectrum for a window from ~0 (K + M is then positive
    # definite), else the window's middle
    sigma = -1.0 if fmin < 0.05 * fmax else 0.5 * (lo + hi)
    op = LinearOperator(Kf.shape, matvec=_factor(Kf - sigma * Mf, cfg.backend), dtype=float)
    cap = min(cfg.max_modes, Kf.shape[0] - 2)
    nev, capped = min(24, cap), False
    while True:
        w, X = eigsh(Kf, k=nev, M=Mf, sigma=sigma, which='LM', OPinv=op, tol=cfg.tol)
        # shift-invert finds the modes nearest the shift: once the farthest
        # found is farther than both window edges, the window holds no others
        reach = max(hi - sigma, sigma - lo)
        covered = np.abs(w - sigma).max() >= reach
        if covered or nev >= cap:
            capped = not covered
            break
        # the count of modes grows as k^2 (Weyl): ask for what the found ones
        # extrapolate to, with a margin, rather than doubling round by round
        span = reach / max(np.abs(w - sigma).max(), 1e-30)
        nev = min(cap, max(2 * nev, int(1.3 * nev * span) + 16))
    o = np.argsort(w)
    w, X = w[o], X[:, o]
    f = C0 * np.sqrt(np.maximum(w, 0)) / (2 * math.pi * math.sqrt(cav.er))
    keep = (f >= max(fmin, STATIC)) & (f <= fmax)
    f, X = f[keep], X[:, keep]
    V = np.zeros((len(mesh.nodes), len(f)))
    V[free] = X
    area = lump.sum()
    V /= np.sqrt(np.einsum('im,im->m', V, M @ V) / area)    # RMS 1 over the cavity
    # losses: 1/Q = tan(d) + skin depth / plate spacing
    delta = np.sqrt(2 / (2 * math.pi * np.maximum(f, 1) * MU0 * SIGMA_CU))
    Q = 1 / (cav.tand + delta / (cav.d * 1e-3))
    v2 = V ** 2
    pr = (lump @ v2) ** 2 / (area * (lump @ v2 ** 2))
    coupling = (np.array([np.abs(V[ids]).mean(0) for ids in mesh.ports]) if mesh.ports
                else np.zeros((0, len(f))))
    # the modal term of the cavity's impedance at a port, at resonance:
    # d V^2 Q / (area w eps0 er)
    wm = 2 * math.pi * np.maximum(f, 1)
    zpeak = cav.d * 1e-3 * coupling ** 2 * Q / (area * wm * EPS0 * cav.er)
    return Modes(f, Q, V, pr, np.abs(V).max(0), coupling, zpeak, capped)
