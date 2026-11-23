"""
mom25d.half - a complex-symmetric matrix kept by its lower half, by block
columns, and its LDL^T factor without pivoting (host side; the GPU's is
gpu_ldl.py, on the same layout).

MoM's Z is complex symmetric (Z = Z^T). Its lower half by block columns of nb:
block column J holds columns [J nb, J nb + w_J) of the rows [J nb, N), column
major (its leading dimension ld_J = N - J nb), the blocks one after another in
one flat array. In Z's C-ordered terms that is block row J's right part
(rows [J nb, J nb + w_J), columns [J nb, N)), so `block(J)` (w_J x ld_J,
C order) is Z[J0:J1, J0:] and its transpose the block column. The diagonal
blocks are held whole (both triangles); about N^2 / 2 + N nb / 2 numbers.

    H = Half(n)                     # zeros, complex64
    H = Half.pack(Z, dsc)           # Z (full, C order) equilibrated by dsc
    Y = H.matvec(X)                 # Z X (X: n or n x k)
    ldl_factor(H)                   # in place: L below the diagonal, D on it
    X = ldl_solve(H, B)             # Z^-1 B

The factor is right-looking by block columns: the diagonal block's L11 D1
L11^T, the panel's W = A21 L11^-T and L21 = W D1^-1, and each later block
column's rows from its own start updated by W L21^T (the lower half alone:
half an LU's work and numbers). Without pivoting it relies on the
equilibrated matrix's diagonal; a zero pivot raises ZeroDivisionError, a
non-finite factor FloatingPointError (tools/mom25d_ldl_trial.py: it
preconditions as the LU does on mom25d's boards).
"""
import numpy as np
from scipy.linalg import blas, lapack, solve_triangular

__all__ = ['Half', 'ldl_factor', 'ldl_solve', 'ldl_mul', 'ldl_small', 'NB']

NB = 256   # the GPU's panel width (gpu_vk.NB): one layout for both


class Half:
    """The lower half of a symmetric n x n matrix by block columns of nb (the
    module's docstring). data: the flat array (None: zeros of dtype,
    'empty': uninitialised)."""

    def __init__(self, n, nb=NB, dtype=np.complex64, data=None):
        self.n, self.nb = int(n), int(nb)
        J0 = np.arange(0, self.n, self.nb, dtype=np.int64)
        self.j0 = J0
        self.w = np.minimum(self.nb, self.n - J0)
        self.ld = self.n - J0
        self.off = np.r_[0, np.cumsum(self.w * self.ld)].astype(np.int64)
        self.size = int(self.off[-1])
        if data is None:
            data = np.zeros(self.size, dtype)
        elif isinstance(data, str) and data == 'empty':
            data = np.empty(self.size, dtype)
        if data.size != self.size or data.ndim != 1:
            raise ValueError(f"Half: data of {self.size} numbers, not "
                             f"{data.size}")
        self.data = data

    @property
    def nblk(self):
        return len(self.j0)

    @property
    def dtype(self):
        return self.data.dtype

    @property
    def nbytes(self):
        return self.data.nbytes

    @staticmethod
    def bytes(n, nb=NB, itemsize=8):
        """The bytes of an n x n Half (itemsize: 8 complex64, 16 complex128)."""
        J0 = np.arange(0, int(n), int(nb), dtype=np.int64)
        return float(itemsize * (np.minimum(nb, n - J0) * (n - J0)).sum())

    def block(self, J):
        """Block row J: Z[J0:J1, J0:] (w_J x ld_J, C order, a view)."""
        a, b = self.off[J], self.off[J + 1]
        return self.data[a:b].reshape(int(self.w[J]), int(self.ld[J]))

    def rows(self):
        """(rowptr, c0) of each row r: its first stored number's index in
        data and that number's column (the compiled core's fills)."""
        r = np.arange(self.n, dtype=np.int64)
        J = r // self.nb
        return (self.off[J] + (r - self.j0[J]) * self.ld[J], self.j0[J])

    @classmethod
    def pack(cls, Z, dsc=None, dtype=np.complex64, nb=NB, threads=None):
        """Z's upper half by block rows (= its lower half by block columns),
        times dsc[i] dsc[j] where given, as dtype (complex64 from a C-ordered
        Z: the compiled core's pack_half, one pass)."""
        n = len(Z)
        H = cls(n, nb, dtype, 'empty')
        if (np.dtype(dtype) == np.complex64 and Z.flags.c_contiguous
                and Z.dtype in (np.complex64, np.complex128)):
            from . import _core
            _core.pack_half(Z, np.ascontiguousarray(
                np.ones(n) if dsc is None else dsc, float), H.data, int(nb))
            return H
        d = None if dsc is None else np.asarray(dsc, float)

        def one(J):
            a, b = int(H.j0[J]), int(H.j0[J] + H.w[J])
            blk = H.block(J)
            if d is None:
                blk[...] = Z[a:b, a:]
            else:
                np.multiply(Z[a:b, a:], (d[a:b, None] * d[None, a:]), out=blk,
                            casting='unsafe')
        _each(range(H.nblk), one, threads)
        return H

    @classmethod
    def compact(cls, Z, nb=NB):
        """A complex64 or complex128 Z (C order) as a Half in its own memory
        (the compiled core's compact_half: no second matrix); Z is that
        Half's from then on, its tail unused: given back to the system where
        Z is zeros_mapped's (madvise)."""
        from . import _core
        n = len(Z)
        _core.compact_half(Z, int(nb))
        size = int(Half.bytes(n, nb, 1))
        _release_tail(Z, size * Z.itemsize)
        return cls(n, nb, Z.dtype, Z.reshape(-1)[:size])

    def to_full(self):
        """The whole symmetric matrix (n x n, C order): for checks."""
        n = self.n
        Z = np.zeros((n, n), self.dtype)
        for J in range(self.nblk):
            a, b = int(self.j0[J]), int(self.j0[J] + self.w[J])
            blk = self.block(J)
            Z[a:b, a:] = blk
            Z[b:, a:b] = blk[:, b - a:].T
        return Z

    def diagonal(self):
        """Z's diagonal (a copy)."""
        rp, c0 = self.rows()
        return self.data[rp + np.arange(self.n) - c0].copy()

    def border(self, B, C):
        """The via (and holes', sides') rows and columns: Z[:Ne, Ne:] = B,
        Z[Ne:, :Ne] = B^T, Z[Ne:, Ne:] = C, where the half keeps them (B: Ne
        x Nv, C: Nv x Nv; Ne = n - Nv)."""
        Ne = self.n - B.shape[1]
        for J in range(self.nblk):
            a, b = int(self.j0[J]), int(self.j0[J] + self.w[J])
            blk = self.block(J)
            re = min(b, Ne)
            if a < re:                      # edge rows: their via columns
                blk[:re - a, Ne - a:] = B[a:re]
            ra = max(a, Ne)
            if ra < b:                      # via rows: B^T, then C
                if a < Ne:
                    blk[ra - a:, :Ne - a] = B[a:Ne, ra - Ne:b - Ne].T
                c = max(Ne, a)
                blk[ra - a:, c - a:] = C[ra - Ne:b - Ne, c - Ne:]
        return self

    def add_sparse(self, S):
        """Z += S's symmetric part (S + S^T) / 2 where the half keeps it;
        returns the rest, (S - S^T) / 2 (sparse; None where S is
        symmetric): the products' to add (matvec does not)."""
        from scipy import sparse
        S = sparse.csr_matrix(S)
        Sy = ((S + S.T) * 0.5).tocoo()
        rp, c0 = self.rows()
        k = Sy.col >= c0[Sy.row]
        np.add.at(self.data, rp[Sy.row[k]] + Sy.col[k] - c0[Sy.row[k]],
                  Sy.data[k].astype(self.dtype))
        Sa = ((S - S.T) * 0.5).tocsr()
        Sa.eliminate_zeros()
        return Sa if Sa.nnz and abs(Sa).max() > 0 else None

    def scale(self, d, threads=None):
        """In place: entry (i, j) times d[i] d[j] (d real, or complex for a
        complex matrix: the loop/tree basis's sqrt(jw))."""
        d = np.asarray(d)
        if self.dtype == np.complex128:
            try:  # (one pass a row: half the two NumPy passes' 0.58 s at 33k)
                from . import _core
                _core.half_scale(self.data, *self.rows(),
                                 np.ascontiguousarray(d, complex))
                return self
            except (ImportError, AttributeError):
                pass
        dt = (self.dtype if np.iscomplexobj(d) else
              np.zeros(0, self.dtype).real.dtype)

        def one(J):
            a, b = int(self.j0[J]), int(self.j0[J] + self.w[J])
            blk = self.block(J)
            blk *= d[a:b, None].astype(dt)
            blk *= d[None, a:].astype(dt)
        _each(range(self.nblk), one, threads)
        return self

    def panel(self, J, out=None):
        """Rows J0 .. J1 of the matrix whole (w x n, C order): block J's own
        (its columns from J0) and before J0 the earlier blocks' columns J0 ..
        J1 transposed (the matrix symmetric: their mirror)."""
        a, w = int(self.j0[J]), int(self.w[J])
        R = np.empty((w, self.n), self.dtype) if out is None else out
        R[:, a:] = self.block(J)
        for I in range(J):
            i0, wi = int(self.j0[I]), int(self.w[I])
            R[:, i0:i0 + wi] = self.block(I)[:, a - i0:a - i0 + w].T
        return R

    def set_rows(self, idx, R):
        """The matrix's rows idx whole set to R (len(idx) x n; R's block idx x
        idx symmetric), where the half keeps them: each pair's one place,
        and in a diagonal block both."""
        idx = np.asarray(idx, int)
        J = idx // self.nb
        for b in np.unique(J):
            k = np.flatnonzero(J == b)
            a, w = int(self.j0[b]), int(self.w[b])
            loc = idx[k] - a
            blk = self.block(b)
            blk[loc] = R[k, a:]
            blk[:, loc] = R[k, a:a + w].T
            for I in range(b):
                i0, wi = int(self.j0[I]), int(self.w[I])
                self.block(I)[:, a - i0 + loc] = R[k, i0:i0 + wi].T
        return self

    def loop_transform(self, W, chunk=512):
        """Z <- T^T Z T in place, T = I + W (W: n x n sparse, nonzero in some
        columns V, on rows R outside V: the sides' verticals as loops, the
        via chains' fan paths): only V's rows and columns change. Y = Z[:, V]
        + Z[:, R] W[R, V] (R's rows chunk at a time), Y[V] += W[:, V]^T Y,
        then rows V := Y^T. Its peak beside the half: Y (n x |V|)."""
        from scipy import sparse
        W = sparse.csc_matrix(W)
        V = np.flatnonzero(np.diff(W.indptr))
        if not len(V):
            return self
        Wv = W[:, V].tocsr()
        R = np.flatnonzero(np.diff(Wv.indptr))
        Y = np.ascontiguousarray(self.rows_of(V).T)
        for k in range(0, len(R), chunk):
            sel = R[k:k + chunk]
            Y += self.rows_of(sel).T @ Wv[sel].toarray()
        Y[V] += np.asarray(Wv.T @ Y)
        return self.set_rows(V, Y.T)

    def rows_of(self, idx):
        """The matrix's rows idx whole (len(idx) x n), any rows."""
        idx = np.asarray(idx, int)
        out = np.empty((len(idx), self.n), self.dtype)
        J = idx // self.nb
        for b in np.unique(J):
            k = np.flatnonzero(J == b)
            a = int(self.j0[b])
            loc = idx[k] - a
            out[k, a:] = self.block(b)[loc]
            for I in range(b):
                i0, wi = int(self.j0[I]), int(self.w[I])
                out[np.ix_(k, np.arange(i0, i0 + wi))] = \
                    self.block(I)[:, a - i0 + loc].T
        return out

    def matvec(self, X):
        """Z X, X: n or n x k (in the matrix's precision, or double for a
        complex64 matrix's double right-hand side: the products are
        BLAS's): the block rows' Z[J0:J1, J0:] X[J0:] and their transposes'
        X[J0:J1] into the rows below the diagonal block."""
        X = np.asarray(X)
        one = X.ndim == 1
        X2 = X.reshape(self.n, -1)
        dt = np.result_type(self.dtype, X2.dtype)
        X2 = X2.astype(dt, copy=False)
        Y = np.zeros(X2.shape, dt)
        for J in range(self.nblk):
            a, b = int(self.j0[J]), int(self.j0[J] + self.w[J])
            blk = self.block(J)
            if blk.dtype != dt:
                blk = blk.astype(dt)
            Y[a:b] += blk @ X2[a:]
            if b < self.n:
                Y[b:] += blk[:, b - a:].T @ X2[a:b]
        return Y[:, 0] if one else Y


def zeros_mapped(n, dtype=complex):
    """An n x n matrix of zeros in an anonymous mapping of its own (mmap: the
    pages zero till touched), so that Half.compact can give the half it no
    longer needs back to the system (the loop/tree basis's transform and the
    sides' loops need the whole matrix; the solve after them its half)."""
    import mmap
    nbytes = max(int(n) * int(n) * np.dtype(dtype).itemsize, 1)
    # private: MADV_DONTNEED frees its pages (a shared anonymous mapping, the
    # default, keeps them)
    flags = getattr(mmap, 'MAP_PRIVATE', 0) | getattr(mmap, 'MAP_ANONYMOUS', 0)
    mm = (mmap.mmap(-1, nbytes, flags=flags) if flags
          else mmap.mmap(-1, nbytes))
    if hasattr(mmap, 'MADV_HUGEPAGE'):
        # (2 MB pages: its first touch, the fill's, faulted 4 KB at a time
        # was 2.2 s against 0.9 at 27k unknowns)
        try:
            mm.madvise(mmap.MADV_HUGEPAGE)
        except OSError:
            pass
    return np.frombuffer(mm, dtype=dtype, count=int(n) * int(n)).reshape(n, n)


def _release_tail(Z, keep):
    """Past its first `keep` bytes, Z's pages back to the system where Z is
    zeros_mapped's (MADV_DONTNEED: zeros again if touched); else nothing."""
    import mmap
    b = Z
    while b is not None and not isinstance(b, mmap.mmap):
        b = getattr(b, 'base', None)
        if isinstance(b, memoryview):
            b = b.obj
    if b is None or not hasattr(b, 'madvise'):
        return
    page = mmap.PAGESIZE
    start = -(-int(keep) // page) * page
    if start < len(b) and hasattr(mmap, 'MADV_DONTNEED'):
        b.madvise(mmap.MADV_DONTNEED, start, len(b) - start)


def _each(items, fn, threads=None):
    """fn over items on the fast cores (NumPy's arithmetic and BLAS release the
    GIL)."""
    items = list(items)
    if threads is None:
        from .threads import fast_cores
        threads = fast_cores() or 4
    if threads <= 1 or len(items) <= 1:
        for x in items:
            fn(x)
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(min(threads, len(items))) as ex:
        list(ex.map(fn, items))


def ldl_small(A, leaf=32):
    """L D L^T of a small dense symmetric A (its lower triangle; complex128
    or complex64), not in place: (L unit lower, Fortran order; d). Recursive
    halves, the leaves unblocked, the rest BLAS on one thread: 2.2 ms at 256
    (the GPU factor's diagonal blocks; OpenBLAS's threads made it 90 ms)."""
    from .threads import _one_blas_thread
    A = np.array(A, order='F')
    d = np.empty(len(A), A.dtype)
    with _one_blas_thread():
        _ldl_rec(A, d, leaf)
    if not np.isfinite(d).all() or not np.isfinite(A).all():
        raise FloatingPointError("LDL^T: a non-finite factor")
    L = np.tril(A, -1)
    L[np.diag_indices(len(A))] = 1
    return L, d


def _ldl_rec(A, d, leaf):
    w = len(A)
    if w <= leaf:
        for j in range(w):
            dj = A[j, j]
            if dj == 0:
                raise ZeroDivisionError("LDL^T: a zero pivot")
            d[j] = dj
            ld = A[j + 1:, j].copy()
            A[j + 1:, j] = ld / dj
            A[j + 1:, j + 1:] -= np.outer(A[j + 1:, j], ld)
        return
    h = w // 2
    _ldl_rec(A[:h, :h], d[:h], leaf)
    L11 = A[:h, :h]
    W = solve_triangular(L11, A[h:, :h].T, lower=True, unit_diagonal=True,
                         check_finite=False).T
    A[h:, :h] = W / d[None, :h]
    A[h:, h:] -= W @ A[h:, :h].T
    _ldl_rec(A[h:, h:], d[h:], leaf)


def ldl_factor(H):
    """L D L^T of the Half H in place (complex64 or complex128): right-looking
    by block columns, the diagonal block in double (ldl_small), each later
    block column's update one BLAS GEMM on BLAS's threads (W and L21 kept
    in C order, so that the rows a block column takes, a suffix, are
    contiguous: SciPy's wrappers copy a strided operand, and hold the GIL, so
    that block columns on threads of their own ran on one core). Returns H."""
    gemm = blas.cgemm if H.dtype == np.complex64 else blas.zgemm
    dt = H.dtype
    for k in range(H.nblk):
        P = H.block(k).T                          # (ld_k x w), F order
        w = int(H.w[k])
        L, d = ldl_small(P[:w, :w].astype(complex))
        P[:w, :w] = np.tril(L, -1) + np.diag(d)
        if w == P.shape[0]:
            break
        Lt = inverse_lower(L).T.astype(dt)       # L11^-T
        W = np.ascontiguousarray(P[w:] @ Lt)      # A21 L11^-T, C order
        L21 = W / d[None, :].astype(dt)
        P[w:] = L21
        k1 = int(H.j0[k]) + w
        for j in range(k + 1, H.nblk):
            a, wj = int(H.j0[j]) - k1, int(H.w[j])
            # block column j (F) -= W[a:] L21[a:a + wj]^T
            gemm(-1.0, W[a:].T, L21[a:a + wj].T, 1.0, H.block(j).T,
                 trans_a=1, overwrite_c=1)
    return H


def ldl_solve(H, B):
    """Z^-1 B with ldl_factor's H (B: n or n x k): L y = B, z = y / D, L^T x
    = z, by blocks; in the factor's precision, complex128 out."""
    B = np.asarray(B)
    one = B.ndim == 1
    Y = np.array(B.reshape(H.n, -1), H.dtype)
    nb = H.nblk
    for k in range(nb):
        a, w = int(H.j0[k]), int(H.w[k])
        P = H.block(k).T
        Y[a:a + w] = solve_triangular(P[:w, :w], Y[a:a + w], lower=True,
                                      unit_diagonal=True, check_finite=False)
        if a + w < H.n:
            Y[a + w:] -= P[w:] @ Y[a:a + w]
    dg = np.concatenate([np.diagonal(H.block(k)[:, :int(H.w[k])])
                         for k in range(nb)])
    Y /= dg[:, None]
    for k in range(nb - 1, -1, -1):
        a, w = int(H.j0[k]), int(H.w[k])
        P = H.block(k).T
        if a + w < H.n:
            Y[a:a + w] -= P[w:].T @ Y[a + w:]
        Y[a:a + w] = solve_triangular(P[:w, :w], Y[a:a + w], lower=True,
                                      trans='T', unit_diagonal=True,
                                      check_finite=False)
    Y = Y.astype(complex)
    return Y[:, 0] if one else Y


def ldl_mul(H, X):
    """L D L^T X with ldl_factor's H: the factored matrix's products from its
    factor (the double fallback's, whose Z the factor overwrote), X n or n x
    k, in the factor's precision, complex128 out."""
    X = np.asarray(X)
    one = X.ndim == 1
    X2 = np.array(X.reshape(H.n, -1), H.dtype)
    T = np.empty_like(X2)
    for k in range(H.nblk):                      # T = L^T X
        a, w = int(H.j0[k]), int(H.w[k])
        P = H.block(k).T
        T[a:a + w] = np.tril(P[:w, :w], -1).T @ X2[a:a + w] + X2[a:a + w]
        if a + w < H.n:
            T[a:a + w] += P[w:].T @ X2[a + w:]
    dg = np.concatenate([np.diagonal(H.block(k)[:, :int(H.w[k])])
                         for k in range(H.nblk)])
    T *= dg[:, None]
    Y = np.zeros_like(T)                         # Y = L T
    for k in range(H.nblk):
        a, w = int(H.j0[k]), int(H.w[k])
        P = H.block(k).T
        Y[a:a + w] += np.tril(P[:w, :w], -1) @ T[a:a + w] + T[a:a + w]
        if a + w < H.n:
            Y[a + w:] += P[w:] @ T[a:a + w]
    Y = Y.astype(complex)
    return Y[:, 0] if one else Y


def inverse_lower(L):
    """The unit lower L's inverse (LAPACK's trtri), its dtype."""
    fn = lapack.ctrtri if L.dtype == np.complex64 else lapack.ztrtri
    Li, info = fn(L, lower=1, unitdiag=1)
    if info:
        raise ZeroDivisionError("LDL^T: a singular diagonal block")
    return Li
