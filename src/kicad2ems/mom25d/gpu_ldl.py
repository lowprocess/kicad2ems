"""
mom25d.gpu_ldl - the complex-symmetric LDL^T factor without pivoting on a
Vulkan GPU (gpu_vk.VulkanBackend's device and plumbing): half an LU's work and
memory. The matrix is half.Half's layout (the lower half by block columns of
NB), on the device as on the host: block column J at its own address, its
rows from J NB, column major.

Per block column k (MAGMA's hybrid scheme, without pivoting the CPU takes the
diagonal block alone):

  the CPU factors the w x w diagonal block A11 = L11 D1 L11^T (half.ldl_small,
  in double), and inverts L11;
  the GPU forms W = A21 L11^-T (a GEMM with that inverse), L21 = W D1^-1 (in
  A21's place) and the trailing update of every later block column's rows
  from its own start, A22 -= W L21^T (syrk_half: the lower half alone).

With look-ahead: the GPU updates the next block column first and sends its
diagonal block to the CPU, which factors it while the GPU runs the rest of the
update. The solves run on the GPU: L y = b by blocks (each block's L11^-1, then
the rows below), y / D, L^T x = y.

    F = factor(be, H)        # H: half.Half (complex64, equilibrated)
    X = solve(be, F, R)
    H = download(be, F)      # the factor on the host (half.ldl_solve's)
"""
import time

import numpy as np

from . import _core
from .half import Half

# block columns at most (the descriptor's table): N up to 256 NB (65k at 256)
MAXBLK = 256

SOURCE = r'''
// the lower half by block columns: block column J (columns J nb ..) at bc[J],
// its rows from J nb, leading dimension n - J nb
struct Half {
    float2* bc[MAXBLK];
    int n, nb;
};

float2 cmul(float2 a, float2 b)
{
    return float2(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x);
}

uniform Half H_;
uniform float2* GA_;
uniform float2* GB_;
uniform int m_, n_, k_, lda_, ldb_, r0_, c0_;

// H(r0_ + i, c0_ + j) -= sum_k A(i, k) B(j, k), where H stores it (row r0_ + i
// at or below column c0_ + j's block start): A (m_ x k_, ld lda_), B (n_ x k_,
// ld ldb_), column major. 16 x 16 threads, each GT x GT outputs of a TM x TM
// tile, TK deep a stage, the next stage's loads in registers before this
// stage's products; tiles wholly above the stored part return at once.
#define TM (16 * GT)
#define TK 16
groupshared float2 As[TK][TM + 1];
groupshared float2 Bs[TK][TM + 1];

[shader("compute")] [numthreads(16, 16, 1)]
void syrk_half(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int tx = (int)lid.x, ty = (int)lid.y, l = ty * 16 + tx;
    const int m0 = (int)gid.x * TM, n0 = (int)gid.y * TM;
    const int nb = H_.nb;
    // the tile's first column's stored rows start at its block's start
    if (r0_ + m0 + TM - 1 < ((c0_ + n0) / nb) * nb) return;
    const bool wm = m0 + TM <= m_, wn = n0 + TM <= n_;
    float2 acc[GT][GT];
    for (int a = 0; a < GT; a++)
        for (int b = 0; b < GT; b++)
            acc[a][b] = float2(0.0, 0.0);
    const int R = TM * TK / 256;
    float2 ra[R], rb[R];
    for (int u = 0; u < R; u++) {
        const int q = l + 256 * u, r = q % TM, k = q / TM;
        ra[u] = (k < k_ && (wm || m0 + r < m_))
                    ? GA_[(int64_t)k * lda_ + m0 + r] : float2(0.0, 0.0);
        rb[u] = (k < k_ && (wn || n0 + r < n_))
                    ? GB_[(int64_t)k * ldb_ + n0 + r] : float2(0.0, 0.0);
    }
    for (int k0 = 0; k0 < k_; k0 += TK) {
        for (int u = 0; u < R; u++) {
            const int q = l + 256 * u;
            As[q / TM][q % TM] = ra[u];
            Bs[q / TM][q % TM] = rb[u];
        }
        GroupMemoryBarrierWithGroupSync();
        if (k0 + TK < k_) {
            for (int u = 0; u < R; u++) {
                const int q = l + 256 * u, r = q % TM, k = k0 + TK + q / TM;
                ra[u] = (k < k_ && (wm || m0 + r < m_))
                            ? GA_[(int64_t)k * lda_ + m0 + r]
                            : float2(0.0, 0.0);
                rb[u] = (k < k_ && (wn || n0 + r < n_))
                            ? GB_[(int64_t)k * ldb_ + n0 + r]
                            : float2(0.0, 0.0);
            }
        }
        for (int kk = 0; kk < TK; kk++) {
            float2 av[GT], bv[GT];
            for (int a = 0; a < GT; a++) av[a] = As[kk][tx + 16 * a];
            for (int b = 0; b < GT; b++) bv[b] = Bs[kk][ty + 16 * b];
            for (int a = 0; a < GT; a++)
                for (int b = 0; b < GT; b++) {
                    acc[a][b].x = fma(av[a].x, bv[b].x,
                                      fma(-av[a].y, bv[b].y, acc[a][b].x));
                    acc[a][b].y = fma(av[a].x, bv[b].y,
                                      fma(av[a].y, bv[b].x, acc[a][b].y));
                }
        }
        GroupMemoryBarrierWithGroupSync();
    }
    for (int b = 0; b < GT; b++) {
        const int j = n0 + ty + 16 * b;
        if (j >= n_) continue;
        const int c = c0_ + j, J = c / nb, j0 = J * nb;
        // (no pointer before its buffer: NVIDIA's driver hung on a column's
        // pointer less j0, indexed from j0 on; RADV took it)
        float2* col = H_.bc[J] + (int64_t)(c - j0) * (H_.n - j0);
        for (int a = 0; a < GT; a++) {
            const int i = m0 + tx + 16 * a, r = r0_ + i;
            if (i < m_ && r >= j0) col[r - j0] -= acc[a][b];
        }
    }
}

// D1_ (ld ld1_) and D2_ (ld ld2_) = S_ (ld lds_) times dv_[j] on column j
// (m_ x n_): L21 = W D1^-1, into A21's place and L21's work array
uniform float2* S_;
uniform float2* D1_;
uniform float2* D2_;
uniform float2* dv_;
uniform int lds_, ld1_, ld2_;

[shader("compute")] [numthreads(256, 1, 1)]
void scale_cols(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)m_ * n_) return;
    const int i = (int)(t % m_), j = (int)(t / m_);
    const float2 v = cmul(S_[(int64_t)j * lds_ + i], dv_[j]);
    D1_[(int64_t)j * ld1_ + i] = v;
    D2_[(int64_t)j * ld2_ + i] = v;
}

// C_ (m_ x n_, ld ldc_) = A_ (ld lda_): the panels to and from the host
uniform float2* CA_;
uniform float2* CC_;
uniform int ldc_;

[shader("compute")] [numthreads(256, 1, 1)]
void copy2(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)m_ * n_) return;
    const int i = (int)(t % m_), j = (int)(t / m_);
    CC_[(int64_t)j * ldc_ + i] = CA_[(int64_t)j * lda_ + i];
}

// ---- the solves: X_ (n x nrhs, column major, ld nx_) ------------------------
// L_: block column J's (its rows from blk_, ld ldl_), T_: its diagonal
// block's L11^-1 (bn_ x bn_, column major); dg_: D^-1 (n)
uniform float2* X_;
uniform float2* L_;
uniform float2* T_;
uniform float2* dg_;
uniform int nx_, nrhs_, blk_, bn_, ldl_, trans_;
groupshared float2 xb[NBMAX];

// x_b = T x_b (trans_ 0) or T^T x_b: a workgroup per right-hand side
[shader("compute")] [numthreads(256, 1, 1)]
void ldl_tri(uint3 tid: SV_DispatchThreadID)
{
    const int c = (int)tid.y, t = (int)tid.x;
    float2* x = X_ + (int64_t)c * nx_ + blk_;
    for (int i = t; i < bn_; i += 256) xb[i] = x[i];
    GroupMemoryBarrierWithGroupSync();
    // 16 loads in flight a thread (one at a time this was 63 us a block at
    // one right-hand side: latency)
    const int si = trans_ != 0 ? bn_ : 1, sj = trans_ != 0 ? 1 : bn_;
    for (int i = t; i < bn_; i += 256) {
        float2 s = float2(0.0, 0.0);
        for (int j0 = 0; j0 < bn_; j0 += 16) {
            float2 v[16];
            [unroll] for (int u = 0; u < 16; u++)
                v[u] = j0 + u < bn_ ? T_[(int64_t)i * si + (int64_t)(j0 + u) * sj]
                                    : float2(0.0, 0.0);
            [unroll] for (int u = 0; u < 16; u++)
                if (j0 + u < bn_) s += cmul(v[u], xb[j0 + u]);
        }
        x[i] = s;
    }
}

// forward: x_i -= sum_j L(i, j) x_(blk + j) for the rows i below the block: a
// thread a row (L's reads coalesced across the rows), every right-hand side,
// L's numbers 16 at a time in flight; x_blk through shared memory FJ at a time
#define FJ 64
groupshared float2 Xs[FJ][RHS];

[shader("compute")] [numthreads(256, 1, 1)]
void ldl_fupd(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x;
    const int i = blk_ + bn_ + ((int)gid.x + (int)gid.y * 65535) * 256 + t;
    const bool ok = i < nx_;
    float2 acc[RHS];
    for (int c = 0; c < RHS; c++) acc[c] = float2(0.0, 0.0);
    float2* li = L_ + (ok ? i - blk_ : 0);
    for (int j0 = 0; j0 < bn_; j0 += FJ) {
        for (int q = t; q < FJ * RHS; q += 256) {
            const int jj = q % FJ, c = q / FJ;
            Xs[jj][c] = (c < nrhs_ && j0 + jj < bn_)
                            ? X_[(int64_t)c * nx_ + blk_ + j0 + jj]
                            : float2(0.0, 0.0);
        }
        GroupMemoryBarrierWithGroupSync();
        for (int jj = 0; jj < FJ && j0 + jj < bn_; jj += 16) {
            float2 v[16];
            [unroll] for (int u = 0; u < 16; u++)
                v[u] = (ok && j0 + jj + u < bn_)
                           ? li[(int64_t)(j0 + jj + u) * ldl_]
                           : float2(0.0, 0.0);
            [unroll] for (int u = 0; u < 16; u++)
                for (int c = 0; c < RHS; c++)
                    acc[c] += cmul(v[u], Xs[jj + u][c]);
        }
        GroupMemoryBarrierWithGroupSync();
    }
    if (ok)
        for (int c = 0; c < nrhs_; c++) X_[(int64_t)c * nx_ + i] -= acc[c];
}

// backward, right-looking: once x_blk is solved, x_j -= sum_(i in the block)
// L(i, j) x_i for every j before the block: a workgroup BJ columns j (of one
// block column: BJ divides nb), the block's rows BR at a time through shared
// memory (each column's rows contiguous: coalesced), each thread a column and
// RHS / 4 right-hand sides; no sums across workgroups
#define BJ 64
#define BR 32
groupshared float2 Lt[BR][BJ + 1];
groupshared float2 Xr[BR][RHS];

[shader("compute")] [numthreads(256, 1, 1)]
void ldl_bupd(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, jl = t % BJ, g = t / BJ;
    const int ja = ((int)gid.x + (int)gid.y * 65535) * BJ;
    if (ja >= blk_) return;
    const int nb = H_.nb, J = ja / nb, j0 = J * nb, ld = H_.n - j0;
    // column ja + c's rows from blk_: base + c ld + r
    float2* base = H_.bc[J] + (int64_t)(ja - j0) * ld + (blk_ - j0);
    float2 acc[RHS / 4];
    for (int u = 0; u < RHS / 4; u++) acc[u] = float2(0.0, 0.0);
    for (int r0 = 0; r0 < bn_; r0 += BR) {
        for (int q = t; q < BR * BJ; q += 256) {
            const int r = q % BR, c = q / BR;
            Lt[r][c] = (r0 + r < bn_ && ja + c < blk_)
                           ? base[(int64_t)c * ld + r0 + r] : float2(0.0, 0.0);
        }
        for (int q = t; q < BR * RHS; q += 256) {
            const int r = q % BR, c = q / BR;
            Xr[r][c] = (c < nrhs_ && r0 + r < bn_)
                           ? X_[(int64_t)c * nx_ + blk_ + r0 + r]
                           : float2(0.0, 0.0);
        }
        GroupMemoryBarrierWithGroupSync();
        for (int r = 0; r < BR; r++) {
            const float2 l = Lt[r][jl];
            for (int u = 0; u < RHS / 4; u++)
                acc[u] += cmul(l, Xr[r][g + 4 * u]);
        }
        GroupMemoryBarrierWithGroupSync();
    }
    const int j = ja + jl;
    if (j < blk_)
        for (int u = 0; u < RHS / 4; u++) {
            const int c = g + 4 * u;
            if (c < nrhs_) X_[(int64_t)c * nx_ + j] -= acc[u];
        }
}

// x <- D^-1 x
[shader("compute")] [numthreads(256, 1, 1)]
void ldl_dscale(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)nx_ * nrhs_) return;
    X_[t] = cmul(X_[t], dg_[(int)(t % nx_)]);
}
'''
NAMES = ('syrk_half', 'scale_cols', 'copy2', 'ldl_tri', 'ldl_fupd',
         'ldl_bupd', 'ldl_dscale')
# the solves' kernels again for few right-hand sides (RHS 8: their loops a
# quarter as long; GMRES's columns drop out as they converge)
SOLVES = ('ldl_tri', 'ldl_fupd', 'ldl_bupd', 'ldl_dscale')
FEW = 8


def source(gt, nb, rhs):
    return (f"#define GT {int(gt)}\n#define NBMAX {int(nb)}\n#define MAXBLK "
            f"{MAXBLK}\n#define RHS {int(rhs)}\n" + SOURCE)


def kernels(be):
    """The LDL^T kernels of backend be, compiled once (be.k; the solves'
    again for FEW right-hand sides, be.k[name + '_few'])."""
    if 'syrk_half' not in be.k:
        for rhs, tag, names in ((be.SOLVE_RHS, '', NAMES),
                                (FEW, '_few', SOLVES)):
            mod = be.dev.load_module_from_source(
                f"mom25d_ldl_{be.nb}_{be.gt}_{rhs}", source(be.gt, be.nb, rhs))
            for n in names:
                be.k[n + tag] = be.dev.create_compute_pipeline(
                    be.dev.link_program([mod], [mod.entry_point(n)]))
    return be.k


class HalfDev:
    """A half.Half's layout on the device (complex64, or 16-byte numbers):
    the block columns in buffers of at most MAX_BUFFER bytes, none split
    between two; addr[J] each block column's device address."""

    def __init__(self, be, n, nb, itemsize=8):
        from .gpu_vk import MAX_BUFFER
        self.n, self.nb, self.itemsize = int(n), int(nb), int(itemsize)
        j0 = np.arange(0, self.n, self.nb, dtype=np.int64)
        if len(j0) > MAXBLK:
            raise MemoryError(f"mom25d: {n} unknowns are {len(j0)} block "
                              f"columns of {nb} (the GPU's LDL^T: at most "
                              f"{MAXBLK})")
        self.j0 = j0
        self.w = np.minimum(self.nb, self.n - j0)
        self.ld = self.n - j0
        size = self.w * self.ld * self.itemsize
        self.bufs, self.addr, groups = [], [], []
        start, used = 0, 0
        for J, s in enumerate(size):
            if used + s > MAX_BUFFER and used:
                groups.append((start, J, used))
                start, used = J, 0
            used += int(s)
        groups.append((start, len(size), used))
        self.groups = []                  # (first J, end J, buffer)
        for a, b, nbytes in groups:
            buf = be._buffer(nbytes)
            self.bufs.append(buf)
            base, off = buf.device_address, 0
            for J in range(a, b):
                self.addr.append(base + off)
                off += int(size[J])
            self.groups.append((a, b, buf))
        self.nbytes = float(size.sum())

    def desc(self):
        return {'bc': list(self.addr) + [0] * (MAXBLK - len(self.addr)),
                'n': self.n, 'nb': self.nb}

    def at(self, J, row=0, col=0):
        """The device address of block column J's entry (J0 + row, J0 +
        col)."""
        return int(self.addr[J] + self.itemsize * (col * int(self.ld[J])
                                                    + row))


class LDLFactor:
    """A GPU LDL^T factor (factor's): the block columns, each diagonal
    block's L11^-1 (for the solves), D^-1; the device memory it holds."""
    on_gpu = True

    def __init__(self, dev, tinv, dinv, n):
        self.dev, self.tinv, self.dinv, self.n = dev, tinv, dinv, n
        self.device_bytes = dev.nbytes + 8.0 * n * dev.nb + 8.0 * n


def vram_needed(N, nb):
    """The factor's half matrix, its work arrays (W, L21: N x nb each), the
    diagonal blocks' inverses, and 0.4 GB besides."""
    return Half.bytes(N, nb) + 8.0 * N * nb * 3 + 0.4e9


def _copy(be, src, lds, dst, ldd, m, n):
    """dst (ld ldd) = src (ld lds), m x n, device or mapped host addresses."""
    if m > 0 and n > 0:
        be._run('copy2', be._flat(m * n), CA_=int(src), CC_=int(dst), m_=m,
                n_=n, lda_=lds, ldc_=ldd)


def upload(be, H, chunk=1 << 28):
    """H (a host half.Half, complex64) onto the device: HalfDev. Its flat
    array in pieces of 256 MB through two mapped host buffers, each buffer's
    block columns contiguous on both sides."""
    kernels(be)
    dev = HalfDev(be, H.n, H.nb)
    data = H.data.view(np.complex64)
    per = chunk // 8
    hs = [be._host(f'hup{i}', chunk) for i in (0, 1)]
    subs, j = [None, None], 0
    for a, b, buf in dev.groups:
        lo, hi = int(H.off[a]), int(H.off[b])
        base = dev.addr[a]
        for s in range(lo, hi, per):
            e, i = min(hi, s + per), j % 2
            if subs[i] is not None:
                be.dev.wait_for_submit(subs[i])
            hs[i].view(np.complex64, e - s)[:] = data[s:e]
            _copy(be, hs[i].address, e - s, base + 8 * (s - lo), e - s,
                  e - s, 1)
            subs[i] = be._submit()
            j += 1
    for x in subs:
        if x is not None:
            be.dev.wait_for_submit(x)
    return dev


def factor(be, H):
    """The LDL^T factor of the host half.Half H (complex64, equilibrated) on
    be's GPU: LDLFactor. Raises ZeroDivisionError / FloatingPointError where a
    diagonal block has no factor without pivoting (the caller's LU then)."""
    if H.nb != be.nb:
        raise ValueError(f"gpu_ldl: the matrix's blocks ({H.nb}) are not the "
                         f"GPU's panels ({be.nb})")
    if be.nb % 64:
        raise ValueError(f"gpu_ldl: panels of {be.nb} (a multiple of 64: the "
                         "backward solve's tiles)")
    be.release()
    t0 = time.perf_counter()
    dev = upload(be, H)
    return _factor(be, dev, t0)


def _factor(be, dev, t0):
    """The blocked LDL^T of the matrix in dev (factor's)."""
    kernels(be)
    N, nb = dev.n, dev.nb
    nblk = len(dev.j0)
    Wb = be._work('ldl_w', 8 * N * nb)
    Lb = be._work('ldl_l21', 8 * N * nb)
    dinv = be._buffer(8 * N)
    hdn = be._host('ldl_dn', 8 * nb * nb)
    # by step parity: the host writes step k + 1's while the GPU may read k's
    hup = [be._host(f'ldl_up{i}', 8 * (2 * nb * nb + nb)) for i in (0, 1)]
    dLt = [be._work(f'ldl_lt{i}', 8 * nb * nb) for i in (0, 1)]
    tinv, steps = [], []
    t_cpu = t_wait = 0.0
    H = dev.desc()

    def down(J):  # record: block column J's diagonal block -> hdn
        w = int(dev.w[J])
        _copy(be, dev.at(J), int(dev.ld[J]), hdn.address, w, w, w)

    def cpu(J):
        """Factor the diagonal block in hdn (the compiled core's ldl_diag, in
        double: 0.5 ms at 256 against 4.4 in NumPy); its packed L \\ D,
        L11^-T and D^-1 into hup[J % 2]; L11^-1 kept for the solves."""
        nonlocal t_cpu
        tc = time.perf_counter()
        w = int(dev.w[J])
        A = hdn.view(np.complex64, w * w).reshape(w, w).T
        h = hup[J % 2].view(np.complex64, 2 * nb * nb + nb)
        Li = np.empty(w * w, np.complex64)
        try:
            _core.ldl_diag(A, h[:w * w], h[nb * nb:nb * nb + w * w],
                           h[2 * nb * nb:2 * nb * nb + w], Li)
        except ValueError as e:     # (a zero pivot: the caller's LU)
            raise ZeroDivisionError(f"{e} in the block of {int(dev.j0[J])}")
        tinv.append(Li)
        t_cpu += time.perf_counter() - tc

    def syrk(r0, c0, m, n, w, off, M2):
        """record: H(r0.., c0..) -= W[off:] L21[off:]^T (m x n, w deep; W
        and L21 M2 x w)."""
        if m > 0 and n > 0:
            tm = 16 * be.gt
            be._run('syrk_half', (-(-m // tm), -(-n // tm), 1), H_=H,
                    GA_=Wb.device_address + 8 * off,
                    GB_=Lb.device_address + 8 * off, m_=m, n_=n, k_=w,
                    lda_=M2, ldb_=M2, r0_=r0, c0_=c0)

    down(0)
    be.dev.wait_for_submit(be._submit())
    cpu(0)
    for k in range(nblk):
        k0, w = int(dev.j0[k]), int(dev.w[k])
        ld, k1 = int(dev.ld[k]), k0 + w
        M2 = N - k1
        h = hup[k % 2]
        # S1 (the CPU waits on it): the factored block into place, W, L21, the
        # next block column's update, its diagonal block down
        _copy(be, h.address, w, dev.at(k), ld, w, w)
        _copy(be, h.address + 8 * nb * nb, w, dLt[k % 2].device_address, w, w,
              w)
        _copy(be, h.address + 16 * nb * nb, w, dinv.device_address + 8 * k0,
              w, w, 1)
        if M2 > 0:
            w1 = int(dev.w[k + 1])
            # W = A21 L11^-T (the backend's GEMM: C = beta C + alpha A B)
            be._gemm(be._flatdesc(dev.at(k, w), ld),
                     be._flatdesc(dLt[k % 2].device_address, w),
                     be._flatdesc(Wb.device_address, M2), M2, w, w,
                     alpha=1.0, beta=0.0)
            be._run('scale_cols', be._flat(M2 * w), S_=Wb.device_address,
                    D1_=dev.at(k, w), D2_=Lb.device_address,
                    dv_=dinv.device_address + 8 * k0, lds_=M2, ld1_=ld,
                    ld2_=M2, m_=M2, n_=w)
            syrk(k1, k1, M2, w1, w, 0, M2)
            down(k + 1)
        s1 = be._submit()
        if M2 <= 0:
            break
        # S2 (while the CPU factors the next block): the rest of the update
        syrk(k1 + w1, k1 + w1, M2 - w1, M2 - w1, w, w1, M2)
        be._submit()
        tw = time.perf_counter()
        be.dev.wait_for_submit(s1)
        tc = time.perf_counter()
        t_wait += tc - tw
        cpu(k + 1)
        steps.append((k0, time.perf_counter() - tc, tc - tw))
    # the diagonal blocks' inverses on the device for the solves: block J at
    # J nb^2 (its w x w, ld w)
    th = np.zeros(nblk * nb * nb, np.complex64)
    for J, Ti in enumerate(tinv):
        th[J * nb * nb:J * nb * nb + Ti.size] = Ti
    tb = be._buffer(th.nbytes, th)
    be.dev.wait_for_idle()
    be.stats = dict(seconds=time.perf_counter() - t0, cpu=t_cpu, wait=t_wait,
                    steps=steps, kind='ldl')
    return LDLFactor(dev, tb, dinv, N)


def solve(be, F, R):
    """Z^-1 R (R: host, N x k or N) with F's factor on the GPU, SOLVE_RHS
    columns at a time: L y = R, y / D, L^T x = y; complex128 out."""
    R = np.asarray(R)
    if R.ndim == 2 and R.shape[1] > be.SOLVE_RHS:
        return np.concatenate([
            _solve(be, F, R[:, c:c + be.SOLVE_RHS])
            for c in range(0, R.shape[1], be.SOLVE_RHS)], 1)
    return _solve(be, F, R)


def _solve(be, F, R):
    kernels(be)
    dev, N, nb = F.dev, F.n, F.dev.nb
    one = R.ndim == 1
    R2 = np.asarray(R, np.complex64).reshape(N, -1)
    k = R2.shape[1]
    X = be._work('ldl_x', 8 * N * k)
    hx = be._host('ldl_x', 8 * N * k)
    hx.view(np.complex64, N * k).reshape(k, N)[...] = R2.T
    _copy(be, hx.address, N * k, X.device_address, N * k, N * k, 1)
    xa = X.device_address
    nblk = len(dev.j0)
    few, rhs = ('_few', FEW) if k <= FEW else ('', be.SOLVE_RHS)
    for fwd in (1, 0):
        for bi in (range(nblk) if fwd else range(nblk - 1, -1, -1)):
            b0, bn, ld = int(dev.j0[bi]), int(dev.w[bi]), int(dev.ld[bi])
            T = F.tinv.device_address + 8 * bi * nb * nb
            rest = N - b0 - bn
            kw = dict(X_=xa, L_=dev.at(bi), T_=T, nx_=N, nrhs_=k, blk_=b0,
                      bn_=bn, ldl_=ld)
            if fwd:
                be._run('ldl_tri' + few, (1, k, 1), trans_=0, **kw)
                if rest > 0:
                    be._run('ldl_fupd' + few, be._flat(rest), **kw)
            else:
                # x_blk (its rows below done), then every row before it
                be._run('ldl_tri' + few, (1, k, 1), trans_=1, **kw)
                if b0 > 0:
                    be._run('ldl_bupd' + few, be._flat(b0, 64),
                            H_=dev.desc(), **kw)
            if bi % be.SOLVE_BLOCKS == be.SOLVE_BLOCKS - 1:
                be._submit()
        if fwd:
            be._run('ldl_dscale' + few, be._flat(N * k), X_=xa,
                    dg_=F.dinv.device_address, nx_=N, nrhs_=k)
    _copy(be, xa, N * k, hx.address, N * k, N * k, 1)
    be.dev.wait_for_submit(be._submit())
    W = hx.view(np.complex64, N * k).reshape(k, N).T.astype(complex)
    return W[:, 0] if one else W


def download(be, F):
    """The factor on the host: a half.Half (complex64) for half.ldl_solve."""
    kernels(be)
    dev = F.dev
    H = Half(dev.n, dev.nb, np.complex64, 'empty')
    chunk = 1 << 28
    per = chunk // 8
    h = be._host('hdown', chunk)
    for a, b, buf in dev.groups:
        lo, hi = int(H.off[a]), int(H.off[b])
        for s in range(lo, hi, per):
            e = min(hi, s + per)
            _copy(be, dev.addr[a] + 8 * (s - lo), e - s, h.address, e - s,
                  e - s, 1)
            be.dev.wait_for_submit(be._submit())
            H.data[s:e] = h.view(np.complex64, e - s)
    return H
