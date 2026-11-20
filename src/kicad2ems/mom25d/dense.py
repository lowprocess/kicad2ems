"""
mom25d.dense - the dense matrix's pieces the solvers share: Z scaled and bordered, its LU on
the host or the GPU and their solves, the sides' loops, and batched GMRES (fast.py's, split
out: R-F3)
"""
import numpy as np
import scipy.linalg as sla

from . import _core
from .threads import fast_cores


def _row_blocks(Z, fn):
    """fn(a, b) over blocks of Z's rows (about 32 MB each) on the fast cores:
    NumPy's in-place arithmetic and copies release the GIL, and one block's two
    passes stay in cache. A pass over a dense Z is bound by memory bandwidth,
    which one thread does not fill: at 16k unknowns (4 GB) the equilibration
    took 0.48 s on one, 0.27 s on 8."""
    n = Z.shape[0]
    rows = max(1, (1 << 25) // max(1, Z.shape[1] * Z.itemsize))
    blocks = [(a, min(a + rows, n)) for a in range(0, n, rows)]
    nt = min(len(blocks), fast_cores() or 4)
    if nt <= 1:
        for a, b in blocks:
            fn(a, b)
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(nt) as ex:
        list(ex.map(lambda ab: fn(*ab), blocks))


def _equilibrate(Z, dsc=None):
    """Symmetric diagonal equilibration of Z in place (via fan-charge rows dwarf
    strip rows, most at low f), by dsc or 1 / sqrt|Z_ii|; returns the scales."""
    if dsc is None:
        dsc = 1.0 / np.sqrt(np.abs(np.diagonal(Z)).astype(float) + 1e-300)
    dr = dsc.astype(Z.real.dtype)

    def scale(a, b):
        Z[a:b] *= dr[a:b, None]
        Z[a:b] *= dr[None, :]
    _row_blocks(Z, scale)
    return dsc


def _border(Z, B, C, both=True):
    """Z's border from the vias', holes' and sides' blocks: Z[:Ne, Ne:] = B,
    Z[Ne:, :Ne] = B^T, Z[Ne:, Ne:] = C, a block of rows at a time on the
    fast cores (B^T whole strides through B a column at a time: with the
    sides' 2,176 columns at 10.1k edges 0.2 s of a 3.5 s solve). both=False:
    B^T alone (B and C are Z's own blocks)."""
    Ne = B.shape[0]

    def copy(a, b):
        if a < Ne and both:
            Z[a:min(b, Ne), Ne:] = B[a:min(b, Ne)]
        if b > Ne:
            a = max(a, Ne)
            Z[a:b, :Ne] = B[:, a - Ne:b - Ne].T
            if both:
                Z[a:b, Ne:] = C[a - Ne:b - Ne]
    _row_blocks(Z, copy)


def _loop_transform(Z, W):
    """Z <- T^T Z T in place, T = I + W (W: nonzero in some columns V, on rows
    outside V): Z's columns V, then its rows V; the compiled core's
    (loop_transform: 0.1 s at 16.3k unknowns, 1.1k loops)."""
    V = np.flatnonzero(np.diff(W.indptr))
    nz = np.diff(W.indptr)[V]
    idx = np.zeros((len(V), nz.max()), np.int64)
    cw = np.zeros(idx.shape)
    k = np.arange(idx.shape[1]) < nz[:, None]
    idx[k], cw[k] = W.indices, W.data         # (the columns V's, in order)
    return _core.loop_transform(Z, V.astype(np.int64), idx, cw)


def _c64(Z, dsc):
    """Z equilibrated by dsc as a complex64 copy (the single-precision LU's
    input; scaled in double, then rounded), Z as it was: one pass in the
    compiled core."""
    return _core.scale_c64(Z, np.ascontiguousarray(dsc, float), False)


def _lu(Z):
    """LU of Z^T, in place: Z^T of a C-ordered Z is Fortran-ordered, so LAPACK
    needs no second N^2 copy."""
    return sla.lu_factor(Z.T, overwrite_a=True, check_finite=False)


class HostLU:
    """An LU on the host (_lu's, complex64 or complex128): M(R) = Z^-1 R."""
    on_gpu, device_bytes = False, 0

    def __init__(self, lu):
        self.lu, self.nbytes = lu, lu[0].nbytes

    def __call__(self, R):
        return _lu_solve(self.lu, R)


class GpuLU:
    """A complex64 LU on the GPU, taken from the backend (its work arrays
    freed for the next): M(R) = Z^-1 R there; to_host() downloads it."""
    on_gpu, nbytes = True, 0

    def __init__(self, gpu, n):
        self.gpu, self.lu, self.piv = gpu, gpu.lu, gpu.piv
        self.device_bytes = 8.0 * n * n
        gpu.lu = gpu.piv = None

    def _with(self, fn):
        g = self.gpu
        held, (g.lu, g.piv) = (g.lu, g.piv), (self.lu, self.piv)
        try:
            return fn()
        finally:
            g.lu, g.piv = held

    def __call__(self, R):
        return self._with(lambda: self.gpu.solve(R))

    def to_host(self):
        return HostLU(self._with(self.gpu.download))


def _lu_solve(lu, R):
    """Z^-1 R with _lu's factor (trans=1), in the factor's precision; complex128
    out."""
    return sla.lu_solve(lu, R.astype(lu[0].dtype), trans=1,
                        check_finite=False).astype(complex)


def gmres_batched(A, B, M=None, x0=None, rtol=1e-6, restart=30, maxiter=10,
                  stall=None, reach=None, out=None):
    """
    Right-preconditioned restarted GMRES for k independent systems A x_j = b_j
    run in lockstep, so every operator / preconditioner call is one batched (N x
    k_active) product. A, M: callables on (N x k) arrays. Converged columns drop
    out. Returns (X, iterations per column). stall: (iterations, residual): give
    up when a column's relative residual is still above that after so many
    iterations (a preconditioner that is no inverse: a fresh factorisation takes
    two or three iterations, a broken one all of them). reach: (iterations,
    residual): give up, from the third iteration, when at the rate so far a
    column would still be above that residual after so many (a kept
    factorisation too far from its frequency). out: a dict that receives
    'rel', each column's relative residual |b - A x| / |b| at the end, from
    the products made (b - sum y_j A z_j: exact, and no product more).
    """
    B = np.asarray(B, complex)
    N, k = B.shape
    X = np.zeros((N, k), complex) if x0 is None else np.array(x0, complex)
    M = M or (lambda R: R)
    bn = np.linalg.norm(B, axis=0)
    bn[bn == 0] = 1.0
    iters, act = np.zeros(k, int), np.arange(k)
    rel = np.zeros(k)
    for _ in range(maxiter):
        R = B[:, act] - (A(X[:, act]) if np.any(X[:, act]) else 0)
        beta = np.linalg.norm(R, axis=0)
        rel[act] = beta / bn[act]
        done = beta <= rtol * bn[act]
        act, R, beta = act[~done], R[:, ~done], beta[~done]
        if not len(act):
            break
        ka = len(act)
        V, Z = (np.zeros((restart + 1, N, ka),
                         complex), np.zeros((restart, N, ka), complex))
        AZ = [None] * restart  # A z_j, as made (the end's residual's)
        H, g = (np.zeros((restart + 1, restart, ka),
                         complex), np.zeros((restart + 1, ka), complex))
        cs, sn = (np.zeros((restart, ka),
                           complex), np.zeros((restart, ka), complex))
        g[0], V[0] = beta, R / beta
        live, last, stalled = np.ones(ka, bool), np.full(ka, restart - 1), False
        for j in range(restart):
            Z[j] = M(V[j])
            Wv = AZ[j] = A(Z[j])
            for i in range(j + 1):  # modified Gram-Schmidt
                H[i, j] = hij = np.einsum('nk,nk->k', V[i].conj(), Wv)
                Wv = Wv - V[i] * hij
            H[j + 1, j] = hn = np.linalg.norm(Wv, axis=0)
            V[j + 1] = Wv / np.where(hn > 0, hn, 1)
            for i in range(j):  # apply previous Givens rotations
                H[i, j], H[i + 1, j] = (cs[i] * H[i, j] + sn[i] * H[i + 1, j],
                                        -np.conj(sn[i]) * H[i, j]
                                        + np.conj(cs[i]) * H[i + 1, j])
            den = np.sqrt(np.abs(H[j, j]) ** 2 + np.abs(H[j + 1, j]) ** 2)
            den[den == 0] = 1.0
            # conjugated: [[c, s], [-s*, c*]] [a, b] = [|(a, b)|, 0] for complex
            # a, b (without the conjugates the second entry is not zero and |g|
            # underestimated the residual: 7 iterations "converged" at a true
            # 1.5e-4 where SciPy's GMRES took 12 to 5e-8)
            cs[j], sn[j] = H[j, j].conj() / den, H[j + 1, j].conj() / den
            H[j, j], H[j + 1, j] = cs[j] * H[j, j] + sn[j] * H[j + 1, j], 0
            g[j + 1], g[j] = -np.conj(sn[j]) * g[j], cs[j] * g[j]
            iters[act[live]] += 1
            conv = live & (np.abs(g[j + 1]) <= rtol * bn[act])
            last[conv] = j
            live &= ~conv
            if not live.any():
                break
            res = np.abs(g[j + 1])[live] / bn[act][live]
            if ((stall is not None and j + 1 >= stall[0]
                 and (res > stall[1]).any())
                    or (reach is not None and j + 1 >= 3 and
                        (res ** (reach[0] / (j + 1.0)) > reach[1]).any())):
                last[live], stalled = j, True
                break
        for c in range(ka):  # update each column
            m = last[c] + 1
            y = (np.linalg.solve(np.triu(H[:m, :m,
                                           c]), g[:m, c]) if m else np.zeros(0))
            X[:, act[c]] += Z[:m, :, c].T @ y
            for i in range(m):
                R[:, c] -= AZ[i][:, c] * y[i]
        rel[act] = np.linalg.norm(R, axis=0) / bn[act]
        if stalled:
            break  # no restart either
    if out is not None:
        out['rel'] = rel
    return X, iters.tolist()
