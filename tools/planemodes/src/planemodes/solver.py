"""The cavity's eigenmodes by finite elements.

Linear triangles give K v = k^2 M v (K: stiffness, M: consistent mass), with
V = 0 at ground vias and open edges elsewhere. The modes in [fmin, fmax] come
from shift-invert Lanczos (ARPACK) about the window: the shifted matrix is
factorised once (SuperLU) and the number of modes
asked for grows until the window is covered - asking for a guess from the
area alone wastes most of the time on well-stitched boards.

Open edges add a fringe strip (capacitance and current beyond the edge) and
radiate (edges.py): Q combines the dielectric, the copper and the radiation.
"""
import math
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, eigsh, splu

from . import edges as E

MU0 = 4e-7 * math.pi
EPS0 = 8.8541878128e-12
C0 = 1 / math.sqrt(MU0 * EPS0)
SIGMA_CU = 5.8e7          # S/m, copper
STATIC = 1e6              # Hz: modes below this are islands' static modes


@dataclass
class SolverConfig:
    max_modes: int = 600           # per plane pair
    tol: float = 0.0               # ARPACK tolerance (0: machine precision)
    fringe: bool = True            # the edges' fringing field (edges.py)
    radiation: bool = True         # radiation loss in Q (edges.py)


@dataclass
class Modes:
    f: np.ndarray                  # (m,) Hz
    Q: np.ndarray                  # (m,) dielectric + copper + radiation (if enabled)
    V: np.ndarray                  # (N, m) voltage at the nodes, RMS 1 over the cavity
    pr: np.ndarray                 # (m,) participation: 1 fills the cavity, ~0 local
    peak: np.ndarray               # (m,) max |V| (x RMS)
    coupling: np.ndarray           # (ports, m) mean |V| over each port via's ring
    zpeak: np.ndarray              # (ports, m) ohm: the cavity's impedance at the port at resonance
    capped: bool = False           # True if max_modes stopped the search early
    Q_rad: np.ndarray = None       # (m,) radiation alone (inf: not computed)


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


def _factor(A):
    """A function solving A x = b, A factorised once (SuperLU). The eigen-solve
    is hundreds of single right-hand-side solves, memory-bound; PARDISO (MKL)
    was measured slower at every size tried, 147k to 1.1M unknowns (3.8x to
    1.1x), so it is not offered."""
    return splu(A.tocsc()).solve


def k2(f, er):
    """Wavenumber squared (1/m^2) at frequency f in dielectric er."""
    return (2 * math.pi * f) ** 2 * MU0 * EPS0 * er


def solve_modes(mesh, cav, fmin, fmax, cfg=SolverConfig()):
    """The cavity's modes with fmin <= f <= fmax."""
    K, M, lump = assemble(mesh.nodes, mesh.tris)
    edges = E.boundary_edges(mesh.nodes, mesh.tris, mesh.fixed)
    if cfg.fringe:                     # the fringe strip along every open edge
        delta, er_ext = E.fringe(cav.d, cav.er, cav.outer)
        Kb, Mb = E.fringe_matrices(mesh.nodes, edges, delta, er_ext / cav.er)
        K, M = K + Kb, M + Mb
    free = np.flatnonzero(~mesh.fixed)
    Kf, Mf = K[free][:, free].tocsc(), M[free][:, free].tocsc()
    lo, hi = k2(fmin, cav.er), k2(fmax, cav.er)
    # shift: below the spectrum for a window from ~0 (K + M is then positive
    # definite), else the window's middle
    sigma = -1.0 if fmin < 0.05 * fmax else 0.5 * (lo + hi)
    op = LinearOperator(Kf.shape, matvec=_factor(Kf - sigma * Mf), dtype=float)
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
    # losses: 1/Q = tan(d) + skin depth / plate spacing + 1/Q_rad
    skin = np.sqrt(2 / (2 * math.pi * np.maximum(f, 1) * MU0 * SIGMA_CU))
    Q_rad = np.full(len(f), np.inf)
    if cfg.radiation and len(f):
        rad = edges if cav.outer else _on_board_edge(mesh.nodes, edges, cav)
        P = E.radiated_power(mesh.nodes, rad, V, f)
        W = 0.5 * EPS0 * cav.er / (cav.d * 1e-3) * np.einsum('im,im->m', V, M @ V)
        Q_rad = np.where(P > 0, 2 * math.pi * f * W / np.maximum(P, 1e-300), np.inf)
    Q = 1 / (cav.tand + skin / (cav.d * 1e-3) + 1 / Q_rad)
    v2 = V ** 2
    pr = (lump @ v2) ** 2 / (area * (lump @ v2 ** 2))
    coupling = (np.array([np.abs(V[ids]).mean(0) for ids in mesh.ports]) if mesh.ports
                else np.zeros((0, len(f))))
    # the modal term of the cavity's impedance at a port, at resonance:
    # d V^2 Q / (area w eps0 er)
    wm = 2 * math.pi * np.maximum(f, 1)
    zpeak = cav.d * 1e-3 * coupling ** 2 * Q / (area * wm * EPS0 * cav.er)
    return Modes(f, Q, V, pr, np.abs(V).max(0), coupling, zpeak, capped, Q_rad)


def _on_board_edge(nodes, edges, cav, tol=0.3):
    """The open edges lying along the board outline (within `tol` mm)."""
    if cav.board_edge is None or not len(edges):
        return edges[:0]
    import shapely
    mid = 0.5 * (nodes[edges[:, 0]] + nodes[edges[:, 1]])
    d = shapely.distance(cav.board_edge, shapely.points(mid))
    return edges[d <= tol]
