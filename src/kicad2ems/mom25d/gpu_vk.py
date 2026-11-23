"""
mom25d.gpu_vk - the complex64 LU on a Vulkan GPU (slangpy): mom25d.gpu's backend
on AMD, Intel and NVIDIA, Linux and Windows. Vulkan has no LAPACK, so the
factorisation is the hybrid one of MAGMA's getrf:

  the CPU factors each panel (N - k0 rows, NB columns) with LAPACK's cgetrf
  (partial pivoting, the same pivots and layout as scipy's lu_factor), and
  inverts its unit lower triangle (ctrtri);
  the GPU applies the panel's row interchanges to the other columns, forms
  U12 = L11^-1 A12 (a GEMM with that inverse) and the trailing update
  A22 -= L21 U12 (the O(N^3) part: a register-tiled complex GEMM).

With look-ahead: the GPU updates the next panel's columns first and that panel
goes to the CPU while the GPU runs the rest of the trailing update, so the CPU's
panel and the GPU's update overlap.

The matrix is A = Z^T, column-major (Z's rows are its columns, as LAPACK
factors it): column c in buffer c >> sh at (c & mask) * N, the buffers
at most MAX_BUFFER bytes each (Vulkan drivers cap one buffer at 2-4 GB; 16 GB of
matrix is several). The triangular solves of solve() run on the GPU too: blocked
substitution, the diagonal blocks' inverses from the factorisation, the
off-diagonal blocks as matrix-vector products.

    b = VulkanBackend()
    b.factor(Z)
    X = b.solve(R)
    lu, piv = b.download()
    b.release()
"""
import os
import time

import numpy as np
from scipy.linalg import lapack

# bytes of one matrix buffer (under every driver's maxBufferSize)
MAX_BUFFER = 2 ** 31
# matrix buffers at most (the descriptor's table): 64 GB
TABLE = 32
NB = 256                        # panel width (MOM25D_VK_NB)

SOURCE = (r'''
// a column-major complex matrix: column c in buffer c >> sh, at (c & mask) * ld
struct Mat {
    float2* b[TABLE];
    int sh, mask, ld, r0, c0;   // r0, c0: the submatrix's corner
};

float2* colp(Mat m, int c)
{
    const int cc = c + m.c0;
    return m.b[cc >> m.sh] + (int64_t)(cc & m.mask) * m.ld + m.r0;
}

float2 cmul(float2 a, float2 b)
{
    return float2(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x);
}

uniform Mat A_, B_, C_;
uniform int m_, n_, k_;
uniform float alpha_, beta_;

// C = beta C + alpha A B (complex64): 16 x 16 threads, each GT x GT outputs of
// a TM x TM tile, TK deep per stage
// (GT per GPU: tile(); on an RX 7800 XT 96 x 96 tiles (GT = 6) ran 11.7
// TFLOP/s at the LU's k = 256, 64 x 64 ones 10.9, 128 x 128 ones 9.3; TK = 32
// crashed RADV's shader compiler).
// gemm_flat: A and B each in one buffer (the host checks), addressed without
// the buffer table. Whole tiles load without bounds tests (a workgroup-uniform
// branch).
#define TM (16 * GT)
#define TK 16
groupshared float2 As[TK][TM + 1];
groupshared float2 Bs[TK][TM + 1];

// m's column c, m in one buffer: b[0] is that buffer
float2* flatp(Mat m, int c)
{
    return m.b[0] + (int64_t)((c + m.c0) & m.mask) * m.ld + m.r0;
}

#define GEMM_BODY(COLA, COLB)                                                  \
    const int tx = (int)lid.x, ty = (int)lid.y, l = ty * 16 + tx;              \
    const int m0 = (int)gid.x * TM, n0 = (int)gid.y * TM;                      \
    const bool whole = m0 + TM <= m_ && n0 + TM <= n_;                         \
    float2 acc[GT][GT];                                                        \
    for (int a = 0; a < GT; a++)                                               \
        for (int b = 0; b < GT; b++)                                           \
            acc[a][b] = float2(0.0, 0.0);                                      \
    for (int k0 = 0; k0 < k_; k0 += TK) {                                      \
        if (whole && k0 + TK <= k_) {                                          \
            for (int q = l; q < TM * TK; q += 256) {                           \
                const int r = q % TM, c = q / TM;                              \
                As[c][r] = COLA(A_, k0 + c)[m0 + r];                           \
                const int kk = q % TK, nn = q / TK;                            \
                Bs[kk][nn] = COLB(B_, n0 + nn)[k0 + kk];                       \
            }                                                                  \
        } else {                                                               \
            for (int q = l; q < TM * TK; q += 256) {                           \
                const int r = q % TM, c = q / TM;                              \
                As[c][r] = (m0 + r < m_ && k0 + c < k_)                        \
                               ? COLA(A_, k0 + c)[m0 + r]                      \
                               : float2(0.0, 0.0);                             \
                const int kk = q % TK, nn = q / TK;                            \
                Bs[kk][nn] = (k0 + kk < k_ && n0 + nn < n_)                    \
                                 ? COLB(B_, n0 + nn)[k0 + kk]                  \
                                 : float2(0.0, 0.0);                           \
            }                                                                  \
        }                                                                      \
        GroupMemoryBarrierWithGroupSync();                                     \
        for (int kk = 0; kk < TK; kk++) {                                      \
            float2 av[GT], bv[GT];                                             \
            for (int a = 0; a < GT; a++) av[a] = As[kk][tx + 16 * a];          \
            for (int b = 0; b < GT; b++) bv[b] = Bs[kk][ty + 16 * b];          \
            for (int a = 0; a < GT; a++)                                       \
                for (int b = 0; b < GT; b++) {                                 \
                    acc[a][b].x = fma(av[a].x, bv[b].x,                        \
                                      fma(-av[a].y, bv[b].y, acc[a][b].x));    \
                    acc[a][b].y = fma(av[a].x, bv[b].y,                        \
                                      fma(av[a].y, bv[b].x, acc[a][b].y));     \
                }                                                              \
        }                                                                      \
        GroupMemoryBarrierWithGroupSync();                                     \
    }                                                                          \
    for (int b = 0; b < GT; b++) {                                             \
        const int c = n0 + ty + 16 * b;                                        \
        if (c >= n_) continue;                                                 \
        float2* col = colp(C_, c);                                             \
        for (int a = 0; a < GT; a++) {                                         \
            const int r = m0 + tx + 16 * a;                                    \
            if (r >= m_) continue;                                             \
            col[r] = (beta_ != 0.0 ? beta_ * col[r] : float2(0.0, 0.0)) +      \
                     alpha_ * acc[a][b];                                       \
        }                                                                      \
    }

[shader("compute")] [numthreads(16, 16, 1)]
void gemm(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    GEMM_BODY(colp, colp)
}

// a whole tile of gemm_flat: the next stage's loads into registers before this
// stage's products (their latency behind the arithmetic); k past k_ (a last
// panel narrower than TK stages) loads zeros
void gemm_whole(int tx, int ty, int m0, int n0)
{
    const int l = ty * 16 + tx;
    float2 acc[GT][GT];
    for (int a = 0; a < GT; a++)
        for (int b = 0; b < GT; b++)
            acc[a][b] = float2(0.0, 0.0);
    const int R = TM * TK / 256;
    float2 ra[R], rb[R];
    for (int u = 0; u < R; u++) {
        const int q = l + 256 * u;
        ra[u] = q / TM < k_ ? flatp(A_, q / TM)[m0 + q % TM] : float2(0.0, 0.0);
        rb[u] = q % TK < k_ ? flatp(B_, n0 + q / TK)[q % TK] : float2(0.0, 0.0);
    }
    for (int k0 = 0; k0 < k_; k0 += TK) {
        for (int u = 0; u < R; u++) {
            const int q = l + 256 * u;
            As[q / TM][q % TM] = ra[u];
            Bs[q % TK][q / TK] = rb[u];
        }
        GroupMemoryBarrierWithGroupSync();
        if (k0 + TK < k_) {
            for (int u = 0; u < R; u++) {
                const int q = l + 256 * u;
                const int ka = k0 + TK + q / TM, kb = k0 + TK + q % TK;
                ra[u] = ka < k_ ? flatp(A_, ka)[m0 + q % TM] : float2(0.0, 0.0);
                rb[u] = kb < k_ ? flatp(B_, n0 + q / TK)[kb] : float2(0.0, 0.0);
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
        float2* col = colp(C_, n0 + ty + 16 * b);
        for (int a = 0; a < GT; a++) {
            const int r = m0 + tx + 16 * a;
            col[r] = (beta_ != 0.0 ? beta_ * col[r] : float2(0.0, 0.0)) +
                     alpha_ * acc[a][b];
        }
    }
}

[shader("compute")] [numthreads(16, 16, 1)]
void gemm_flat(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    // uniform in the workgroup
    if ((int)gid.x * TM + TM <= m_ && (int)gid.y * TM + TM <= n_) {
        gemm_whole((int)lid.x, (int)lid.y, (int)gid.x * TM, (int)gid.y * TM);
        return;
    }
    GEMM_BODY(flatp, flatp)
}

// C = A (m x n): panels to and from the staging buffers, U12 into place
[shader("compute")] [numthreads(256, 1, 1)]
void copy(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)m_ * n_) return;
    const int r = (int)(t % m_), c = (int)(t / m_);
    colp(C_, c)[r] = colp(A_, c)[r];
}

// the panel's row interchanges on columns [ca_, ca_ + na_) and
// [cb_, cb_ + n_ - na_) of C_ (C_.c0 = 0), in two passes through tmp:
// rows_[i] <- src_[i]
uniform int* rows_;
uniform int* src_;
uniform float2* tmp_;
uniform int cnt_, ca_, na_, cb_;

int swap_col(int j) { return j < na_ ? ca_ + j : cb_ + (j - na_); }

[shader("compute")] [numthreads(256, 1, 1)]
void swap_gather(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)cnt_ * n_) return;
    const int i = (int)(t % cnt_), j = (int)(t / cnt_);
    tmp_[t] = colp(C_, swap_col(j))[src_[i]];
}

[shader("compute")] [numthreads(256, 1, 1)]
void swap_scatter(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)cnt_ * n_) return;
    const int i = (int)(t % cnt_), j = (int)(t / cnt_);
    colp(C_, swap_col(j))[rows_[i]] = tmp_[t];
}

// U12 = L11^-1 A12 in place (and into C_, U12's work array): forward
// substitution with the unit lower L11 (A_, b x b, k_ = b) on n_ columns of B_,
// TRSM_COLS columns a workgroup, the column block in shared memory, TRSM_LANES
// threads a column; LAPACK's trsm, in order
groupshared float2 Xs[NBMAX][TRSM_COLS + 1];

[shader("compute")] [numthreads(TRSM_COLS * TRSM_LANES, 1, 1)]
void trsm(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int cl = (int)lid.x % TRSM_COLS, lane = (int)lid.x / TRSM_COLS;
    const int col = (int)gid.x * TRSM_COLS + cl;
    const bool ok = col < n_;
    float2* bc = colp(B_, ok ? col : 0);
    for (int i = lane; i < k_; i += TRSM_LANES)
        Xs[i][cl] = ok ? bc[i] : float2(0.0, 0.0);
    GroupMemoryBarrierWithGroupSync();
    for (int i = 0; i < k_ - 1; i++) {
        const float2 xi = Xs[i][cl];
        float2* li = colp(A_, i);                               // L11[:, i]
        for (int j = i + 1 + lane; j < k_; j += TRSM_LANES)
            Xs[j][cl] -= cmul(li[j], xi);
        GroupMemoryBarrierWithGroupSync();
    }
    if (!ok) return;
    float2* uc = colp(C_, col);
    for (int i = lane; i < k_; i += TRSM_LANES) {
        bc[i] = Xs[i][cl];
        uc[i] = Xs[i][cl];
    }
}

// the solves, Z x = R with A = Z^T = P L U:  U^T y = R (forward),
// L^T w = y (backward), x = P w (the host).
// X_: the right-hand sides, N x nrhs column-major (ld N). One block of NB rows
// at a time:
//   tri:  x_b = Dinv_b^T x_b  (Dinv_b: the block's inverse, NB x NB
//         column-major, from the factorisation)
//   upd:  forward: x_i -= sum_{j in b} A(j, i) x_j for i past the block;
//         backward: for i before it
//   (A(j, i) = U^T(i, j) for j <= i, L^T(i, j) for j > i: row i of the
//   transposed factor is column i of A)
uniform float2* X_;
uniform float2* D_;
uniform int nrhs_, blk_, bn_, fwd_;
groupshared float2 xb[NBMAX];
groupshared float2 red[64];

[shader("compute")] [numthreads(256, 1, 1)]
void solve_tri(uint3 tid: SV_DispatchThreadID)
{
    // one workgroup per right-hand side: y = Dinv^T x_b through shared memory
    const int c = (int)tid.y, t = (int)tid.x;
    float2* x = X_ + (int64_t)c * k_ + blk_;
    for (int i = t; i < bn_; i += 256) xb[i] = x[i];
    GroupMemoryBarrierWithGroupSync();
    for (int i = t; i < bn_; i += 256) {
        float2 s = float2(0.0, 0.0);
        // block's Dinv, column i
        float2* d = D_ + (int64_t)blk_ * NBMAX + (int64_t)i * bn_;
        for (int j = 0; j < bn_; j++) s += cmul(d[j], xb[j]);
        x[i] = s;
    }
}

[shader("compute")] [numthreads(64, 1, 1)]
void solve_upd(uint3 tid: SV_DispatchThreadID,
               uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    // one wave of 64 per row i (outside the block), all right-hand sides:
    // x_i -= A(blk.., i) . x_blk
    const int g = (int)gid.x + (int)gid.y * 65535;
    const int i = fwd_ != 0 ? blk_ + bn_ + g : g;
    if (fwd_ != 0 ? i >= k_ : i >= blk_) return;
    // A(blk.., i): contiguous
    float2* col = colp(C_, i) + blk_;
    for (int c = 0; c < nrhs_; c++) {
        float2* x = X_ + (int64_t)c * k_;
        float2 s = float2(0.0, 0.0);
        for (int j = (int)lid.x; j < bn_; j += 64)
            s += cmul(col[j], x[blk_ + j]);
        red[lid.x] = s;
        GroupMemoryBarrierWithGroupSync();
        for (int w = 32; w > 0; w >>= 1) {
            if ((int)lid.x < w) red[lid.x] += red[lid.x + w];
            GroupMemoryBarrierWithGroupSync();
        }
        if (lid.x == 0) x[i] -= red[0];
        GroupMemoryBarrierWithGroupSync();
    }
}
''')
NAMES = ('gemm', 'gemm_flat', 'trsm', 'copy', 'swap_gather', 'swap_scatter',
         'solve_tri', 'solve_upd')

# the refinement's products in double precision (a module of their own: a GPU
# without shaderFloat64 still factors). Zd_: Z (N x N complex128), row r in
# buffer r >> sh at (r & mask) * ld; V, Y: k x N (column c at c N). zgemv: a
# workgroup of 64 a block of DR rows and DC columns; to_c64: the LU's matrix
# (Mat C_, column c = Z's row c) Z equilibrated by dsc, rounded
DSOURCE = r'''
struct Mat {
    float2* b[TABLE];
    int sh, mask, ld, r0, c0;
};
struct DMat {
    double2* b[TABLE];
    int sh, mask, ld, r0, c0;
};
uniform DMat Zd_;
uniform Mat C_;
// the double Z by its lower half (half.Half; gpu_ldl.HalfDev, 16-byte numbers)
struct DHalf {
    double2* bc[MAXBLK];
    int n, nb;
};
uniform DHalf Hd_;
uniform float2* hp_;
uniform int j0_, w_, ld_;
uniform double2* V_;
uniform double2* Y_;
uniform double* dsc_;
uniform int n_, k_;
#define DR 4
#define DC 4
groupshared double2 part[DR * DC][64];

double2* drow(int r)
{
    return Zd_.b[r >> Zd_.sh] + (int64_t)(r & Zd_.mask) * Zd_.ld;
}

[shader("compute")] [numthreads(64, 1, 1)]
void zgemv(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, r0 = (int)gid.x * DR, c0 = (int)gid.y * DC;
    double2 acc[DR][DC];
    for (int a = 0; a < DR; a++)
        for (int c = 0; c < DC; c++)
            acc[a][c] = double2(0.0, 0.0);
    for (int j = t; j < n_; j += 64) {
        double2 v[DC];
        for (int c = 0; c < DC; c++)
            v[c] = c0 + c < k_ ? V_[(int64_t)(c0 + c) * n_ + j]
                               : double2(0.0, 0.0);
        for (int a = 0; a < DR; a++) {
            const double2 z = drow(min(r0 + a, n_ - 1))[j];
            for (int c = 0; c < DC; c++) {
                acc[a][c].x = fma(z.x, v[c].x, fma(-z.y, v[c].y, acc[a][c].x));
                acc[a][c].y = fma(z.x, v[c].y, fma(z.y, v[c].x, acc[a][c].y));
            }
        }
    }
    for (int a = 0; a < DR; a++)
        for (int c = 0; c < DC; c++)
            part[a * DC + c][t] = acc[a][c];
    GroupMemoryBarrierWithGroupSync();
    for (int w = 32; w > 0; w >>= 1) {
        if (t < w)
            for (int q = 0; q < DR * DC; q++)
                part[q][t] += part[q][t + w];
        GroupMemoryBarrierWithGroupSync();
    }
    if (t < DR * DC) {
        const int r = r0 + t / DC, c = c0 + t % DC;
        if (r < n_ && c < k_)
            Y_[(int64_t)c * n_ + r] = part[t][0];
    }
}

double2 zmul(double2 z, double2 v)
{
    return double2(fma(z.x, v.x, -z.y * v.y), fma(z.x, v.y, z.y * v.x));
}

// zhalf_rows: Y = the half's stored part times V: y_r = sum_(j >= j0) Z[r, j]
// v_j for DR rows r of one block (DR divides nb) and DC right-hand sides, a
// workgroup of 64 (zgemv's)
[shader("compute")] [numthreads(64, 1, 1)]
void zhalf_rows(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, r0 = (int)gid.x * DR, c0 = (int)gid.y * DC;
    const int n = Hd_.n, nb = Hd_.nb, J = r0 / nb, j0 = J * nb, ld = n - j0;
    double2 acc[DR][DC];
    for (int a = 0; a < DR; a++)
        for (int c = 0; c < DC; c++)
            acc[a][c] = double2(0.0, 0.0);
    double2* base = Hd_.bc[J];
    for (int j = j0 + t; j < n; j += 64) {
        double2 v[DC];
        for (int c = 0; c < DC; c++)
            v[c] = c0 + c < k_ ? V_[(int64_t)(c0 + c) * n + j]
                               : double2(0.0, 0.0);
        for (int a = 0; a < DR; a++) {
            const int r = r0 + a;
            const double2 z = r < n ? base[(int64_t)(r - j0) * ld + (j - j0)]
                                    : double2(0.0, 0.0);
            for (int c = 0; c < DC; c++) {
                acc[a][c].x = fma(z.x, v[c].x, fma(-z.y, v[c].y, acc[a][c].x));
                acc[a][c].y = fma(z.x, v[c].y, fma(z.y, v[c].x, acc[a][c].y));
            }
        }
    }
    for (int a = 0; a < DR; a++)
        for (int c = 0; c < DC; c++)
            part[a * DC + c][t] = acc[a][c];
    GroupMemoryBarrierWithGroupSync();
    for (int w = 32; w > 0; w >>= 1) {
        if (t < w)
            for (int q = 0; q < DR * DC; q++)
                part[q][t] += part[q][t + w];
        GroupMemoryBarrierWithGroupSync();
    }
    if (t < DR * DC) {
        const int r = r0 + t / DC, c = c0 + t % DC;
        if (r < n && c < k_)
            Y_[(int64_t)c * n + r] = part[t][0];
    }
}

// zhalf_cols: the mirror of the stored part below the diagonal blocks, y_r +=
// sum_(c < j0(r)) Z[c, r] v_c: a thread a row r (the half's reads coalesced
// across the rows), DC2 right-hand sides, a workgroup 256 rows and at most
// HCH of the blocks before them (gid.z: which), each block's v_c through
// shared memory; its sums into P_ (chunk, column, row), zhalf_sum adds them.
// (A workgroup all its rows' blocks, a triangle of them, was 3x the stored
// rows' kernel: the last row groups' work alone, on a CU's 4 double FMAs a
// clock.)
#define DC2 8
#define HB 256
#define HCH 8
groupshared double2 vs[HB][DC2];
uniform double2* P_;
uniform int nch_;

[shader("compute")] [numthreads(256, 1, 1)]
void zhalf_cols(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, r0 = (int)gid.x * 256, r = r0 + t;
    const int cc = (int)gid.y * DC2, ch = (int)gid.z;
    const int n = Hd_.n, nb = Hd_.nb;
    const int Jr = min(r, n - 1) / nb;
    const int Jend = min(min(r0 + 255, n - 1) / nb, (ch + 1) * HCH);
    double2 acc[DC2];
    for (int q = 0; q < DC2; q++) acc[q] = double2(0.0, 0.0);
    for (int Jc = ch * HCH; Jc < Jend; Jc++) {
        const int j0 = Jc * nb, ld = n - j0, w = min(nb, n - j0);
        GroupMemoryBarrierWithGroupSync();
        for (int q = t; q < HB * DC2; q += 256) {
            const int cl = q % HB, c = q / HB;
            vs[cl][c] = (cl < w && cc + c < k_)
                            ? V_[(int64_t)(cc + c) * n + j0 + cl]
                            : double2(0.0, 0.0);
        }
        GroupMemoryBarrierWithGroupSync();
        if (r < n && Jc < Jr) {
            double2* col = Hd_.bc[Jc] + (r - j0);
            for (int c8 = 0; c8 < w; c8 += 8) {
                double2 z[8];
                [unroll] for (int u = 0; u < 8; u++)
                    z[u] = c8 + u < w ? col[(int64_t)(c8 + u) * ld]
                                      : double2(0.0, 0.0);
                [unroll] for (int u = 0; u < 8; u++)
                    for (int q = 0; q < DC2; q++)
                        acc[q] += zmul(z[u], vs[c8 + u][q]);
            }
        }
    }
    if (r < n)
        for (int q = 0; q < DC2; q++)
            if (cc + q < k_)
                P_[((int64_t)ch * k_ + cc + q) * n + r] = acc[q];
}

// Y += the chunks' sums (zhalf_cols's P_), a thread a (row, column)
[shader("compute")] [numthreads(256, 1, 1)]
void zhalf_sum(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)n_ * k_) return;
    double2 s = double2(0.0, 0.0);
    for (int ch = 0; ch < nch_; ch++) s += P_[(int64_t)ch * n_ * k_ + t];
    Y_[t] += s;
}

// to_c64_hh: block column J of the LDL^T's half (hp_) from the double half's
// (Hd_, the same layout), equilibrated by dsc
uniform int jb_;

[shader("compute")] [numthreads(256, 1, 1)]
void to_c64_hh(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)w_ * ld_) return;
    const int r = (int)(t % ld_), c = (int)(t / ld_);
    const double2 z = Hd_.bc[jb_][t];
    const double s = dsc_[j0_ + c] * dsc_[j0_ + r];
    hp_[t] = float2((float)(z.x * s), (float)(z.y * s));
}

// to_c64_half: block column J of the LDL^T's half (hp_, ld ld_ = n - j0_,
// w_ columns) from Z's rows j0_ .. j0_ + w_, their columns from j0_

[shader("compute")] [numthreads(256, 1, 1)]
void to_c64_half(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)w_ * ld_) return;
    const int r = (int)(t % ld_), c = (int)(t / ld_);
    const double2 z = drow(j0_ + c)[j0_ + r];
    const double s = dsc_[j0_ + c] * dsc_[j0_ + r];
    hp_[t] = float2((float)(z.x * s), (float)(z.y * s));
}

[shader("compute")] [numthreads(256, 1, 1)]
void to_c64(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)n_ * n_) return;
    const int i = (int)(t % n_), c = (int)(t / n_);
    const double2 z = drow(c)[i];
    const double s = dsc_[c] * dsc_[i];
    const int cc = c + C_.c0;
    C_.b[cc >> C_.sh][(int64_t)(cc & C_.mask) * C_.ld + i] =
        float2((float)(z.x * s), (float)(z.y * s));
}

#ifndef DRF
#define DRF 4
#endif
#ifndef DCF
#define DCF 4
#endif
#ifndef DC2F
#define DC2F 8
#endif
// ---- the double half's products in float-float (the GPU's single precision
// units, 32 times its double ones on an RX 7800 XT or a GTX 980 Ti): each
// number hi + lo (two floats, about 48 bits), a product's rounding error kept
// by its FMA (Dekker), a sum's by two-sum (Knuth); the half itself made
// float-float once after its upload (to_ff_half: re_hi, im_hi, re_lo, im_lo
// in the double's 16 bytes), the vectors split on the host
struct FHalf {
    float4* bc[MAXBLK];
    int n, nb;
};
uniform FHalf Hf_;
uniform float4* Vf_;

// opaque: v as it is (zm_ is 0), but the compiler cannot see through it. The
// error-free sums and products are algebraically zero, and both drivers fold
// them (RADV fma(x, y, -x y) to 0, both the two-sum's error; `precise` and
// slang's precise mode do not stop it, 2026-11-23): each step's result passed
// through this, they cannot.
uniform uint zm_;
float opq(float v)
{
    return asfloat(asuint(v) ^ zm_);
}

[shader("compute")] [numthreads(256, 1, 1)]
void to_ff_half(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)w_ * ld_) return;
    const double2 z = Hd_.bc[jb_][t];
    // (opaque: RADV folds z - double(float(z)) to 0, every low part lost)
    const float2 h = float2(opq((float)z.x), opq((float)z.y));
    const float2 l = float2((float)(z.x - (double)h.x),
                            (float)(z.y - (double)h.y));
    Hf_.bc[jb_][t] = float4(h, l);
}

// a b - p exactly without an FMA (Dekker: a, b split into 12-bit halves,
// Veltkamp's, whose products are exact): where the driver's fma is not fused
// (RADV keeps a float fma fused only when told it is exact, which slang's
// SPIR-V does not say; ff_probe tells, fmaok_)
float2 vsplit(float a)
{
    const float c = opq(4097.0 * a);
    const float h = opq(c - opq(c - a));
    return float2(h, a - h);
}

float tprod_err(float a, float b, float p)
{
    const float2 x = vsplit(a), y = vsplit(b);
    return opq(opq(opq(x.x * y.x - p) + x.x * y.y) + x.y * y.x) + x.y * y.y;
}

// a += (xh + xl) (yh + yl), a = (hi, lo) unnormalised; the product's error
// by FMA (ffmac) or Dekker's (ffmacd: _with_dekker's kernels, chosen by
// ff_probe: a select between the two had computed both)
void ffmac(inout float2 a, float xh, float xl, float yh, float yl)
{
    const float p = opq(xh * yh);
    float e = fma(xh, yh, -p);
    e = e + (xh * yl + xl * yh);
    const float s = opq(a.x + p);
    const float b = opq(s - a.x);
    const float t = (a.x - opq(s - b)) + (p - b);
    a.x = s;
    a.y = a.y + (t + e);
}

// (re, im) += z v, z and v float-float complex (re_hi, im_hi, re_lo, im_lo)
void cmac(inout float2 re, inout float2 im, float4 z, float4 v)
{
    ffmac(re, z.x, z.z, v.x, v.z);
    ffmac(re, -z.y, -z.w, v.y, v.w);
    ffmac(im, z.x, z.z, v.y, v.w);
    ffmac(im, z.y, z.w, v.x, v.z);
}

void ffmacd(inout float2 a, float xh, float xl, float yh, float yl)
{
    const float p = opq(xh * yh);
    float e = tprod_err(xh, yh, p);
    e = e + (xh * yl + xl * yh);
    const float s = opq(a.x + p);
    const float b = opq(s - a.x);
    const float t = (a.x - opq(s - b)) + (p - b);
    a.x = s;
    a.y = a.y + (t + e);
}

void cmacd(inout float2 re, inout float2 im, float4 z, float4 v)
{
    ffmacd(re, z.x, z.z, v.x, v.z);
    ffmacd(re, -z.y, -z.w, v.y, v.w);
    ffmacd(im, z.x, z.z, v.y, v.w);
    ffmacd(im, z.y, z.w, v.x, v.z);
}

double2 ffd(float2 re, float2 im)
{
    return double2((double)re.x + (double)re.y, (double)im.x + (double)im.y);
}

// zhalf_rows in float-float (its partial sums double for their reduction;
// DRF rows and DCF columns a thread)
groupshared double2 partf[DRF * DCF][64];
[shader("compute")] [numthreads(64, 1, 1)]
void zhalf_rows_ff(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, r0 = (int)gid.x * DRF, c0 = (int)gid.y * DCF;
    const int n = Hf_.n, nb = Hf_.nb, J = r0 / nb, j0 = J * nb, ld = n - j0;
    float2 ar[DRF][DCF], ai[DRF][DCF];
    for (int a = 0; a < DRF; a++)
        for (int c = 0; c < DCF; c++) {
            ar[a][c] = float2(0.0, 0.0);
            ai[a][c] = float2(0.0, 0.0);
        }
    float4* base = Hf_.bc[J];
    for (int j = j0 + t; j < n; j += 64) {
        float4 v[DCF];
        for (int c = 0; c < DCF; c++)
            v[c] = c0 + c < k_ ? Vf_[(int64_t)(c0 + c) * n + j]
                               : float4(0.0, 0.0, 0.0, 0.0);
        for (int a = 0; a < DRF; a++) {
            const int r = r0 + a;
            const float4 z = r < n ? base[(int64_t)(r - j0) * ld + (j - j0)]
                                   : float4(0.0, 0.0, 0.0, 0.0);
            for (int c = 0; c < DCF; c++)
                cmac(ar[a][c], ai[a][c], z, v[c]);
        }
    }
    for (int a = 0; a < DRF; a++)
        for (int c = 0; c < DCF; c++)
            partf[a * DCF + c][t] = ffd(ar[a][c], ai[a][c]);
    GroupMemoryBarrierWithGroupSync();
    for (int w = 32; w > 0; w >>= 1) {
        if (t < w)
            for (int q = 0; q < DRF * DCF; q++)
                partf[q][t] += partf[q][t + w];
        GroupMemoryBarrierWithGroupSync();
    }
    if (t < DRF * DCF) {
        const int r = r0 + t / DCF, c = c0 + t % DCF;
        if (r < n && c < k_)
            Y_[(int64_t)c * n + r] = partf[t][0];
    }
}

// zhalf_cols in float-float (its chunks' sums into P_, double)
groupshared float4 vsf[HB][DC2F];

[shader("compute")] [numthreads(256, 1, 1)]
void zhalf_cols_ff(uint3 lid: SV_GroupThreadID, uint3 gid: SV_GroupID)
{
    const int t = (int)lid.x, r0 = (int)gid.x * 256, r = r0 + t;
    const int cc = (int)gid.y * DC2F, ch = (int)gid.z;
    const int n = Hf_.n, nb = Hf_.nb;
    const int Jr = min(r, n - 1) / nb;
    const int Jend = min(min(r0 + 255, n - 1) / nb, (ch + 1) * HCH);
    float2 ar[DC2F], ai[DC2F];
    for (int q = 0; q < DC2F; q++) {
        ar[q] = float2(0.0, 0.0);
        ai[q] = float2(0.0, 0.0);
    }
    for (int Jc = ch * HCH; Jc < Jend; Jc++) {
        const int j0 = Jc * nb, ld = n - j0, w = min(nb, n - j0);
        GroupMemoryBarrierWithGroupSync();
        for (int q = t; q < HB * DC2F; q += 256) {
            const int cl = q % HB, c = q / HB;
            vsf[cl][c] = (cl < w && cc + c < k_)
                             ? Vf_[(int64_t)(cc + c) * n + j0 + cl]
                             : float4(0.0, 0.0, 0.0, 0.0);
        }
        GroupMemoryBarrierWithGroupSync();
        if (r < n && Jc < Jr) {
            float4* col = Hf_.bc[Jc] + (r - j0);
            for (int c8 = 0; c8 < w; c8 += 8) {
                float4 z[8];
                [unroll] for (int u = 0; u < 8; u++)
                    z[u] = c8 + u < w ? col[(int64_t)(c8 + u) * ld]
                                      : float4(0.0, 0.0, 0.0, 0.0);
                [unroll] for (int u = 0; u < 8; u++)
                    for (int q = 0; q < DC2F; q++)
                        cmac(ar[q], ai[q], z[u], vsf[c8 + u][q]);
            }
        }
    }
    if (r < n)
        for (int q = 0; q < DC2F; q++)
            if (cc + q < k_)
                P_[((int64_t)ch * k_ + cc + q) * n + r] = ffd(ar[q], ai[q]);
}

// whether fma(x, y, -x y) is the product's exact error here (x = y = 1 +
// 2^-12: 2^-24), into Fp_[0]
uniform float* Fp_;
[shader("compute")] [numthreads(1, 1, 1)]
void ff_probe(uint3 tid: SV_DispatchThreadID)
{
    const float x = opq(1.0 + 1.0 / 4096.0);
    const float p = opq(x * x);
    Fp_[0] = fma(x, x, -p);
}

// rows_to_mat: rows r0_ .. r0_ + rows_ of the half's complex64 (equilibrated:
// half_c64_rows's, in the mapped staging buffer src_; each from its block's
// start j0_, ld_ numbers) into the LU's whole matrix C_ at both places (Z
// symmetric: column c is row c): factor_from_half, no whole copy on the host
uniform float2* src_;
uniform int r0_, rows_;
[shader("compute")] [numthreads(256, 1, 1)]
void rows_to_mat(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)rows_ * ld_) return;
    const int r = r0_ + (int)(t / ld_), c = j0_ + (int)(t % ld_);
    const float2 v = src_[t];
    C_.b[c >> C_.sh][(int64_t)(c & C_.mask) * C_.ld + r] = v;
    C_.b[r >> C_.sh][(int64_t)(r & C_.mask) * C_.ld + c] = v;
}

// to_c64_hh from the float-float half (hi + lo, equilibrated by dsc)
[shader("compute")] [numthreads(256, 1, 1)]
void to_c64_hf(uint3 tid: SV_DispatchThreadID)
{
    const int64_t t = (int64_t)tid.x + (int64_t)tid.y * 65535 * 256;
    if (t >= (int64_t)w_ * ld_) return;
    const int r = (int)(t % ld_), c = (int)(t / ld_);
    const float4 z = Hf_.bc[jb_][t];
    const double s = dsc_[j0_ + c] * dsc_[j0_ + r];
    hp_[t] = float2((float)(((double)z.x + (double)z.z) * s),
                    (float)(((double)z.y + (double)z.w) * s));
}
'''
DNAMES = ('zgemv', 'to_c64', 'to_c64_half', 'zhalf_rows', 'zhalf_cols',
          'zhalf_cols4', 'zhalf_sum', 'to_c64_hh', 'to_ff_half',
          'zhalf_rows_ff', 'zhalf_cols_ff', 'to_c64_hf', 'ff_probe',
          'zhalf_rows_ffd', 'zhalf_cols_ffd', 'rows_to_mat')


def _with_dekker(src):
    """DSOURCE with the float-float product kernels again, their products'
    errors by Dekker's split (zhalf_rows_ffd, zhalf_cols_ffd: where the
    GPU's float fma is not fused)."""
    out = src
    for name in ('zhalf_rows_ff', 'zhalf_cols_ff'):
        a = src.index(f'void {name}(')
        a = src.rindex('[shader("compute")]', 0, a)
        b = src.index('\n}\n', a) + 3
        body = src[a:b].replace(f'{name}(', f'{name}d(').replace(
            'cmac(', 'cmacd(')
        out = out + '\n' + body
    return out


def _with_cols4(src):
    """DSOURCE with zhalf_cols again for 4 right-hand sides a thread
    (zhalf_cols4: at k <= 4 the 8-wide one's double FMAs half wasted)."""
    a = src.index('[shader("compute")] [numthreads(256, 1, 1)]\nvoid zhalf_cols(')
    b = src.index('// Y += the chunks\' sums')
    body = (src[a:b].replace('zhalf_cols(', 'zhalf_cols4(')
            .replace('DC2', 'DC4').replace('vs[', 'vs4['))
    return (src[:b] + '#define DC4 4\ngroupshared double2 vs4[HB][DC4];\n'
            + body + src[b:])
DR = DC = 4
# the float-float products' tiles: DRF rows x DCF columns a thread (rows),
# DC2F columns a thread (the mirror)
DRF = int(os.environ.get('MOM25D_VK_FF_DR') or 2)
DCF = int(os.environ.get('MOM25D_VK_FF_DC') or 8)
DC2F = int(os.environ.get('MOM25D_VK_FF_DC2') or 8)


GT = 4  # each thread's outputs: GT x GT (the tile 16 GT square); tile() per GPU
# trsm's workgroup: TRSM_COLS columns, TRSM_LANES threads each. At N = 16k on an
# RX 7800 XT: 4 x 64 69 ms a factorisation, 16 x 16 271 ms, 16 x 8 377 ms (few
# rows a thread between barriers is what pays)
TRSM_COLS = int(os.environ.get('MOM25D_VK_TRSM_COLS') or 4)
TRSM_LANES = int(os.environ.get('MOM25D_VK_TRSM_LANES') or 64)


def tile(device):
    """GT for a GPU (MOM25D_VK_GT overrides it). The update GEMM at k = 256
    (2026-11-19, 12.7k x 12.7k): on an RX 7800 XT GT = 6 (96 x 96 tiles) 11.7
    TFLOP/s against GT = 4's 10.9 (the LU at 14-20k unknowns 4-5 % faster; GT
    = 5 11.6, Gauss's three-product form 11.7 at GT = 4 with a larger error, 8.8
    at GT = 6); on a GTX 980 Ti (Maxwell) GT = 6 was the slower, its LU 7 %."""
    env = os.environ.get('MOM25D_VK_GT', '').strip()
    if env.isdigit():
        return int(env)
    name = str(device).lower()
    return 6 if ('amd' in name or 'radeon' in name) else GT


def source(gt=GT, nbmax=NB):
    return (
        f"#define TABLE {TABLE}\n#define GT {int(gt)}\n#define NBMAX "
        f"{int(nbmax)}\n#define TRSM_COLS {TRSM_COLS}\n#define TRSM_LANES "
        f"{TRSM_LANES}\n"
        + SOURCE)


class _Mat:
    """A column-major complex64 matrix on the device: buffers of whole columns
    (MAX_BUFFER bytes at most)."""
    def __init__(self, be, rows, cols):
        self.rows, self.cols = int(rows), int(cols)
        per = max(1, MAX_BUFFER // (8 * max(self.rows, 1)))
        self.sh = int(np.floor(np.log2(per)))
        self.per = 1 << self.sh
        nbuf = -(-self.cols // self.per)
        if nbuf > TABLE:
            raise MemoryError(
                f"mom25d: a {rows} x {cols} matrix needs {nbuf} GPU buffers "
                f"(at most {TABLE})"
            )
        self.bufs = [
            be._buffer(8 * self.rows * min(self.per, self.cols - i * self.per))
            for i in range(nbuf)
        ]
        self._be = be

    def __del__(self):  # (its buffers to the backend's pool: VulkanBackend)
        try:
            self._be._recycle(self.bufs)
        except Exception:  # noqa: BLE001  (interpreter shutdown)
            pass

    def desc(self, r0=0, c0=0):
        b = ([x.device_address for x in self.bufs] + [0] *
             (TABLE - len(self.bufs)))
        return {
            'b': b, 'sh': self.sh, 'mask': self.per - 1, 'ld': self.rows,
            'r0': int(r0), 'c0': int(c0)
        }

    def download(self):
        """The matrix as (cols, rows) C-ordered: its transpose's memory."""
        return np.concatenate([
            b.to_numpy().view(np.complex64).reshape(-1, self.rows)
            for b in self.bufs
        ])


class VulkanBackend:
    """kicad2ems.mom25d.gpu's backend on a Vulkan GPU: factor (hybrid blocked
    LU), solve (on the GPU), download."""
    name, inplace, hip, api = 'vulkan', True, False, 'Vulkan'
    lu = piv = None

    def __init__(self):
        from kicad2ems.emsdev import gpu as devices, vulkan
        # raises Unavailable: no driver, GPU or slangpy
        self.info = devices.vulkan_device(need=('bda', 'int64'))
        try:
            self.dev = vulkan.slang_device()
            import slangpy as spy
        except Exception as e:
            raise devices.Unavailable(f"vulkan: {e}")
        self.spy, self.device = spy, self.info.name
        self.nb = int(os.environ.get('MOM25D_VK_NB') or NB)
        self.gt = tile(self.info.name)
        self.usage = (spy.BufferUsage.shader_resource
                      | spy.BufferUsage.unordered_access
                      | spy.BufferUsage.copy_source
                      | spy.BufferUsage.copy_destination)
        mod = self.dev.load_module_from_source(
            f"mom25d_lu_{self.nb}_{self.gt}_{TRSM_COLS}_{TRSM_LANES}",
            source(self.gt, self.nb))
        self.k = {
            n: self.dev.create_compute_pipeline(
                self.dev.link_program([mod], [mod.entry_point(n)]))
            for n in NAMES
        }
        self.enc, self._tmp, self._hosts, self._pool = None, {}, {}, []
        self.zd, self._dk = None, None
        from .fast import cap_blas_threads
        cap_blas_threads()  # the panels' LAPACK on the fast cores
        self.stats = {}

    # ---- the interface mom25d.gpu's backends share --------------------------
    def mem_free(self):
        """The device's free memory, and the pool's buffers (idle: _buffer
        takes them before it allocates)."""
        f = self.info.free()
        f = float(f) if f is not None else 0.9 * float(self.info.total or 0)
        return f + float(sum(sz for sz, _ in self._pool))

    def mem_total(self):
        return self.info.total

    def vram_needed(self, N):
        """The matrix, the panel staging, the swap and U12 work arrays, the
        diagonal blocks' inverses."""
        return 8.0 * N * N + 8.0 * N * self.nb * 5 + 0.4e9

    def fits(self, N, extra=0.0):
        """Whether an N-unknown LU fits in the free memory (+ extra: what kept
        factors would give back; policy.gpu_fits). vram_needed counts every
        device buffer factor() makes and 0.4 GB besides."""
        from .policy import gpu_fits
        return gpu_fits(self.vram_needed(N), self.mem_free() + extra)

    def release(self):
        """Free the factor and the work arrays (the device memory; the mapped
        host buffers are kept)."""
        if self.enc is not None or self.lu is not None:
            self._submit(wait=True)
        self.lu = self.piv = None
        self._tmp = {}

    # ---- device plumbing ----------------------------------------------------
    # buffers of at least POOL_MIN bytes go back to the backend's pool when
    # their matrix goes (_Mat, gpu_ldl.HalfDev): slangpy releases a freed
    # buffer only later (when an allocation runs short, it seemed), and the
    # driver's free memory counts it as used till then: after a 24.9k-unknown
    # solve 7.4 GB of a 16 GB card looked taken, and the next frequency's plan
    # left the double Z on the host. Each frequency asks for the same sizes:
    # the pool's are taken first, and mem_free counts them free
    POOL_MIN = 1 << 24

    def _buffer(self, nbytes, data=None):
        need = max(-(-int(nbytes) // 16) * 16, 16)
        b = self._take(need) if need >= self.POOL_MIN else None
        if b is None:
            try:
                b = self.dev.create_buffer(size=need, usage=self.usage)
            except Exception as e:
                if not self._pool:
                    raise MemoryError(
                        f"mom25d: a {nbytes / 1e9:.2f} GB GPU buffer: {e}")
                self._pool = []  # (theirs given back first)
                import gc
                gc.collect()
                try:
                    b = self.dev.create_buffer(size=need, usage=self.usage)
                except Exception as e2:
                    raise MemoryError(
                        f"mom25d: a {nbytes / 1e9:.2f} GB GPU buffer: {e2}")
        if data is not None:
            b.copy_from_numpy(np.ascontiguousarray(data).view(np.uint8).ravel())
        return b

    def _take(self, need):
        """A pooled buffer of at least need bytes and not much more (the
        smallest that fits), or None."""
        fit = [i for i, (sz, _) in enumerate(self._pool)
               if need <= sz <= need + max(need // 4, 1 << 24)]
        if not fit:
            return None
        i = min(fit, key=lambda i: self._pool[i][0])
        return self._pool.pop(i)[1]

    def _recycle(self, bufs):
        """Buffers given back (their matrix gone) into the pool, the oldest
        dropped past the device's memory."""
        for b in bufs:
            if b.size >= self.POOL_MIN:
                self._pool.append((int(b.size), b))
        cap = 0.9 * float(self.info.total or 0)
        while self._pool and sum(sz for sz, _ in self._pool) > cap:
            self._pool.pop(0)

    def drop_pool(self):
        """Give the pool's buffers back to slangpy (for another user of the
        device)."""
        self._pool = []

    def _work(self, name, nbytes):
        """A work buffer kept across the panels (grown as needed)."""
        b = self._tmp.get(name)
        if b is None or b.size < nbytes:
            b = self._tmp[name] = self._buffer(nbytes)
        return b

    def _encoder(self):
        if self.enc is None:
            self.enc = self.dev.create_command_encoder()
        return self.enc

    def _run(self, name, groups, **vars):
        enc = self._encoder()
        p = enc.begin_compute_pass()
        cur = self.spy.ShaderCursor(p.bind_pipeline(self.k[name]))
        for key, v in vars.items():
            cur[key] = v
        p.dispatch_compute(list(groups))
        p.end()
        enc.global_barrier()

    @staticmethod
    def _flat(n, per=256):
        """Workgroups for n threads of `per` (x up to 65535, the rest in y)."""
        g = -(-int(n) // per)
        return (min(g, 65535), -(-g // 65535), 1)

    def _submit(self, wait=False):
        """Submit what is recorded; returns the submission's id (None: nothing
        recorded)."""
        sid = None
        if self.enc is not None:
            sid = self.dev.submit_command_buffer(self.enc.finish())
            self.enc = None
        if wait:
            self.dev.wait_for_idle()
        return sid

    def _gemm(self, A, B, C, m, n, k, alpha=-1.0, beta=1.0):
        """C = beta C + alpha A B (m x n, k deep); gemm_flat where A's k columns
        and B's n are each in one buffer."""
        if m <= 0 or n <= 0 or k <= 0:
            return
        tm = 16 * self.gt
        A, fa = self._one_buffer(A, k)
        B, fb = self._one_buffer(B, n)
        self._run(('gemm_flat' if fa and fb else 'gemm'),
                  (-(-m // tm), -(-n // tm), 1), A_=A, B_=B, C_=C, m_=m, n_=n,
                  k_=k, alpha_=alpha, beta_=beta)

    @staticmethod
    def _one_buffer(d, ncols):
        """(descriptor, flat): d as a one-buffer descriptor (b[0] that buffer,
        c0 within it) when its ncols columns are in one buffer."""
        sh, c0 = d['sh'], d['c0']
        if (c0 >> sh) != ((c0 + max(ncols, 1) - 1) >> sh):
            return d, False
        b = list(d['b'])
        b[0] = b[c0 >> sh]
        return dict(d, b=b, c0=c0 & d['mask']), True

    def _copy(self, A, C, m, n):
        if m > 0 and n > 0:
            self._run('copy', self._flat(m * n), A_=A, C_=C, m_=m, n_=n)

    @staticmethod
    def _flatdesc(addr, rows, r0=0, c0=0):
        """A Mat descriptor of one contiguous column-major array at device
        address addr (device or mapped host)."""
        return {
            'b': [int(addr)] + [0] * (TABLE - 1), 'sh': 30,
            'mask': (1 << 30) - 1, 'ld': int(rows), 'r0': int(r0), 'c0': int(c0)
        }

    # ---- the factorisation --------------------------------------------------
    def _host(self, name, nbytes):
        """Mapped host memory kept across calls (emsdev.vulkan.HostBuffer: the
        kernels copy to and from it by address; slangpy's own read-back runs at
        a fraction of a GB/s on some drivers)."""
        from kicad2ems.emsdev.vulkan import HostBuffer
        h = self._hosts.get(name)
        if h is None or h.nbytes < nbytes:
            if h is not None:
                h.free()
            h = self._hosts[name] = HostBuffer(self.dev, max(int(nbytes), 16))
        return h

    def _upload(self, A, Z, chunk=1 << 28):
        """Z (host complex64, C-ordered: its rows are A's columns) into A:
        column blocks of 256 MB through two mapped host buffers, the copy kernel
        reading each across the bus while the host fills the other. 16 GB/s on
        an RX 7800 XT, where buffer.copy_from_numpy (the buffers filled whole)
        ran at 6.5: 0.6 s -> 0.25 s of each LU at 22k unknowns."""
        N, ncols = A.rows, Z.shape[0]  # Z's rows: A's columns
        cols = max(1, min(ncols, chunk // (8 * N)))
        hs = [self._host(f'up{i}', 8 * N * cols) for i in (0, 1)]
        subs = [None, None]
        for j, c0 in enumerate(range(0, ncols, cols)):
            cc, i = min(cols, ncols - c0), j % 2
            if subs[i] is not None:
                # that buffer's last copy is done
                self.dev.wait_for_submit(subs[i])
            hs[i].view(np.complex64, N * cc).reshape(cc, N)[...] = Z[c0:c0 + cc]
            self._copy(self._flatdesc(hs[i].address, N),
                       A.desc(0, c0), N, cc)
            subs[i] = self._submit()
        for x in subs:
            if x is not None:
                self.dev.wait_for_submit(x)

    def factor(self, Z):
        """Z: host complex64, C-contiguous, equilibrated. Factors A = Z^T in
        place on the GPU (P L U, LAPACK's pivots), keeping the diagonal blocks'
        inverses for solve(). Look-ahead: the next panel's columns are updated
        first and go to the CPU, which factors them while the GPU runs the rest
        of the trailing update."""
        self.release()
        Z = np.asarray(Z, np.complex64)
        t0 = time.perf_counter()
        A = _Mat(self, Z.shape[0], Z.shape[0])
        self._upload(A, Z)
        self._factor(A, t0)

    def _factor(self, A, t0):
        """The blocked LU of the matrix uploaded into A (factor)."""
        N, nb = A.rows, self.nb
        # the panel, down to the CPU and back
        hdn = self._host('panel', 8 * N * nb)
        # by step parity: S2 may read them while the host writes the next step's
        hrows = [self._host(f'rows{i}', 4 * 2 * nb) for i in (0, 1)]
        hsrc = [self._host(f'src{i}', 4 * 2 * nb) for i in (0, 1)]
        tmpS = self._work('swap', 8 * 2 * nb * N)
        U12 = self._work('u12', 8 * nb * N)
        # L11^-1, the panel's (U12 = L11^-1 A12, a GEMM): from the host by
        # step parity, as the rows (S2 may read step k's while the CPU writes
        # step k + 1's)
        hL = [self._host(f'linv{i}', 8 * nb * nb) for i in (0, 1)]
        dL = [self._work(f'linv{i}', 8 * nb * nb) for i in (0, 1)]
        dinv, pending, piv = [], [], np.zeros(N, np.int32)
        t_cpu = t_wait = 0.0
        steps = []                    # per step: (k0, cpu panel, wait)

        def down(k0, b):  # record: panel A[k0:, k0:k0+b] -> hdn
            self._copy(A.desc(k0, k0), self._flatdesc(hdn.address, N - k0),
                       N - k0, b)

        def cpu(k0, b):
            """Factor the panel the GPU left in hdn (N - k0 x b, column-major),
            in place: LAPACK's cgetrf. Keeps its pivots, and (L11, U11) (copies:
            their inverses, the solves', are made while the CPU waits on the
            GPU); returns the rows it moves [(row, from)]."""
            nonlocal t_cpu
            tc = time.perf_counter()
            M = N - k0
            # Fortran (M, b), mapped memory
            P = hdn.view(np.complex64, M * b).reshape(b, M).T
            lu, ip, info = lapack.cgetrf(P, overwrite_a=1)
            if not np.shares_memory(lu, P):
                P[...] = lu
            perm = {}
            for i in range(b):
                j = int(ip[i])
                if j != i:
                    a, c = perm.get(k0 + i, k0 + i), perm.get(k0 + j, k0 + j)
                    perm[k0 + i], perm[k0 + j] = c, a
            # the unit lower L11's inverse now (U12's GEMM takes it), kept for
            # the solves' blocks; U11's is made off the critical path
            Lf, _ = lapack.ctrtri(
                np.tril(P[:b, :b], -1) + np.eye(b, dtype=np.complex64),
                lower=1, unitdiag=1)
            Lf = np.asfortranarray(Lf, np.complex64)
            hL[k0 // nb % 2].view(np.complex64, b * b)[:] = Lf.ravel(order='F')
            pending.append((Lf, np.triu(P[:b, :b])))
            piv[k0:k0 + b] = ip + k0
            t_cpu += time.perf_counter() - tc
            return [(r, s) for r, s in perm.items() if r != s]

        def inverses():  # the solves' diagonal blocks, off the critical path
            while pending:
                Lf, U11 = pending.pop(0)
                Ui, _ = lapack.ctrtri(U11, lower=0, unitdiag=0)
                dinv.append((np.asfortranarray(Ui, np.complex64), Lf))

        # record: the interchanges on these column ranges
        def swaps(step, moved, k0, b, ranges):
            (ca, na), (cb, nbb) = ranges
            n = na + nbb
            if moved and n > 0:
                kw = dict(C_=A.desc(), rows_=hrows[step % 2].address,
                          src_=hsrc[step % 2].address, tmp_=tmpS.device_address,
                          cnt_=len(moved), ca_=ca, na_=na, cb_=cb, n_=n)
                self._run('swap_gather', self._flat(len(moved) * n), **kw)
                self._run('swap_scatter', self._flat(len(moved) * n), **kw)

        def trsm(k0, b, c, nc):  # record: U12[:, c:c+nc] = L11^-1 A12, in place
            # as a GEMM with the CPU's L11^-1 (MAGMA's getrf; the forward
            # substitution ran at ~5 TFLOP/s, 1.3 ms a step at 14k unknowns),
            # then U12 into A12's place
            if nc > 0:
                Li = self._flatdesc(dL[k0 // nb % 2].device_address, b)
                Uc = self._flatdesc(U12.device_address, b, 0, c)
                self._gemm(Li, A.desc(k0, k0 + b + c), Uc, b, nc, b,
                           alpha=1.0, beta=0.0)
                self._copy(Uc, A.desc(k0, k0 + b + c), b, nc)

        b0 = min(nb, N)
        down(0, b0)
        self.dev.wait_for_submit(self._submit())
        moved = cpu(0, b0)
        for step, k0 in enumerate(range(0, N, nb)):
            b, M = min(nb, N - k0), N - k0
            n2 = N - k0 - b
            nb1 = min(nb, n2)
            if moved:
                hrows[step % 2].view(
                    np.int32)[:len(moved)] = [x[0] for x in moved]
                hsrc[step % 2].view(
                    np.int32)[:len(moved)] = [x[1] for x in moved]
            # S1 (the CPU waits on it): the factored panel into place, the next
            # panel's columns interchanged, solved (U12) and updated, that panel
            # down
            self._copy(self._flatdesc(hdn.address, M), A.desc(k0, k0), M, b)
            self._copy(self._flatdesc(hL[step % 2].address, b),
                       self._flatdesc(dL[step % 2].device_address, b), b, b)
            if n2 > 0:
                swaps(step, moved, k0, b, ((k0 + b, nb1), (0, 0)))
                trsm(k0, b, 0, nb1)
                L21 = A.desc(k0 + b, k0)
                self._gemm(L21, self._flatdesc(U12.device_address, b),
                           A.desc(k0 + b, k0 + b), n2, nb1, b)
                down(k0 + b, nb1)
            s1 = self._submit()
            # S2 (while the CPU factors the next panel): the interchanges on the
            # other columns, U12 and the trailing update past the next panel
            swaps(step, moved, k0, b, ((0, k0), (k0 + b + nb1, n2 - nb1)))
            if n2 > nb1:
                trsm(k0, b, nb1, n2 - nb1)
                self._gemm(L21, self._flatdesc(U12.device_address, b, 0, nb1),
                           A.desc(k0 + b, k0 + b + nb1), n2, n2 - nb1, b)
            self._submit()
            if n2 <= 0:
                break
            inverses()
            tw = time.perf_counter()
            self.dev.wait_for_submit(s1)
            tc = time.perf_counter()
            moved = cpu(k0 + b, nb1)
            t_wait += tc - tw
            steps.append((k0, time.perf_counter() - tc, tc - tw))
        inverses()
        Dbuf = []  # the inverses on the device: block bi at bi NB^2
        for which in (0, 1):
            h = np.zeros(len(dinv) * nb * nb, np.complex64)
            for bi, d in enumerate(dinv):
                m = d[which]
                h[bi * nb * nb:bi * nb * nb + m.size] = m.ravel(order='F')
            Dbuf.append(self._buffer(h.nbytes, h))
        order = np.arange(N)  # x = P w: w[order]
        for i in range(N - 1, -1, -1):
            j = piv[i]
            if j != i:
                order[i], order[j] = order[j], order[i]
        self.dev.wait_for_idle()
        self.lu = (A, Dbuf, order)
        self.piv = piv
        self.stats = dict(seconds=time.perf_counter() - t0, cpu=t_cpu,
                          wait=t_wait, steps=steps)

    # ---- the double Z on the GPU: the refinement's products ----------------
    def dense_ok(self):
        """Whether the double-precision kernels compiled (shaderFloat64)."""
        if self._dk is None:
            try:
                from .gpu_ldl import MAXBLK
                mod = self.dev.load_module_from_source(
                    'mom25d_dense', f"#define TABLE {TABLE}\n#define MAXBLK "
                    f"{MAXBLK}\n#define DRF {DRF}\n#define DCF {DCF}\n"
                    f"#define DC2F {DC2F}\n"
                    + _with_dekker(_with_cols4(DSOURCE)))
                for n in DNAMES:
                    self.k[n] = self.dev.create_compute_pipeline(
                        self.dev.link_program([mod], [mod.entry_point(n)]))
                self._dk = True
            except Exception:
                self._dk = False
        return self._dk

    def host_buffer(self, nbytes):
        """Mapped host memory of the caller's (emsdev.vulkan.HostBuffer): a
        matrix filled there is put_dense's across the bus with no staging
        (0.14 s against 0.43 s at 14.7k unknowns), and the fill's first touch
        takes no page faults (np.zeros: 0.33 s)."""
        from kicad2ems.emsdev.vulkan import HostBuffer
        return HostBuffer(self.dev, nbytes)

    def put_dense(self, Z, host=None):
        """Z (host complex128, C-ordered, N x N) onto the GPU for mul_dense and
        factor_dense, until drop_dense: 16 N^2 bytes. host: the host_buffer Z
        is at the start of, read from there."""
        self.drop_dense()
        N = len(Z)
        self.zd = _Mat(self, 2 * N, N)  # Z's row r: column r, 2N float2
        if host is not None:
            self._copy(self._flatdesc(host.address, 2 * N), self.zd.desc(),
                       2 * N, N)
            self.dev.wait_for_submit(self._submit())
        else:
            self._upload(self.zd, np.ascontiguousarray(Z).view(np.complex64))

    # the double half on the GPU as float-float (to_ff_half once after its
    # upload; mul_dense's products then on the single-precision units:
    # MOM25D_VK_FF=off keeps it double)
    FF = os.environ.get('MOM25D_VK_FF', 'on') != 'off'
    # ... from this many columns (the solve's right-hand sides): below it the
    # products are bound by memory, the double's no slower (RX 7800 XT, 20k:
    # 1 column 0.030 s double, 0.038 float-float; 8 columns 0.058 / 0.041;
    # 106 columns 0.757 / 0.545; GTX 980 Ti, 8k: 106 columns 0.584 / 0.414)
    FF_MIN = int(os.environ.get('MOM25D_VK_FF_MIN') or 8)

    def _fma_fused(self):
        """Whether the GPU's float fma is fused (ff_probe: its rounding error
        exact), once: the float-float products' error term from it, else
        Dekker's (RADV: not fused; NVIDIA's: fused)."""
        if getattr(self, '_fmaok', None) is None:
            from . import gpu_ldl
            gpu_ldl.kernels(self)
            buf, h = self._work('ffprobe', 16), self._host('ffprobe', 16)
            self._run('ff_probe', (1, 1, 1), Fp_=buf.device_address, zm_=0)
            gpu_ldl._copy(self, buf.device_address, 1, h.address, 1, 1, 1)
            self.dev.wait_for_submit(self._submit())
            self._fmaok = float(h.view(np.float32, 1)[0]) == 2.0 ** -24
        return self._fmaok

    def put_dense_half(self, H, cols=None):
        """The double Z by its lower half (a host half.Half, complex128) onto
        the GPU for mul_dense and factor_dense_ldl, until drop_dense: 8 N^2
        bytes (FF, for products of FF_MIN columns or more (cols: the solve's,
        None: any): made float-float there, in place)."""
        from . import gpu_ldl
        self.drop_dense()
        self.zd = gpu_ldl.upload(self, H)
        self.zd.ff = False
        if self.FF and (cols is None or cols >= self.FF_MIN):
            d = self.zd.desc()
            for J in range(len(self.zd.j0)):
                w, ld = int(self.zd.w[J]), int(self.zd.ld[J])
                self._run('to_ff_half', self._flat(w * ld), Hd_=d, Hf_=d,
                          jb_=J, w_=w, ld_=ld, zm_=0)
            self._submit(wait=True)
            self.zd.ff = True

    def drop_dense(self):
        if self.zd is not None:
            self._submit(wait=True)
            self.zd = None

    def _zdesc(self):
        return dict(self.zd.desc(), ld=self.zd.cols)

    def _zn(self):
        """put_dense's (or put_dense_half's) Z's unknowns."""
        return self.zd.n if hasattr(self.zd, 'groups') else self.zd.cols

    def mul_dense(self, V, host=None, share=1.0):
        """Z V in double precision on the GPU (Z: put_dense's or
        put_dense_half's; V: N x k), SOLVE_RHS columns a submission. host:
        a function giving Z X on the host: the last (1 - share) of the
        columns are its, computed while the GPU runs the rest (both hold Z;
        with many columns the products are bound by double arithmetic, which
        an RX 7800 XT has about as much of as 8 P-cores). Returns Y, and the
        seconds (host's, and the GPU's beyond them) where host is given."""
        N = self._zn()
        V = np.asarray(V, complex).reshape(N, -1)
        k = V.shape[1]
        kg = k if host is None else int(round(np.clip(share, 0.0, 1.0) * k))
        hs = [self._mul_start(V[:, c:min(kg, c + self.SOLVE_RHS)], i)
              for i, c in enumerate(range(0, kg, self.SOLVE_RHS))]
        t = time.perf_counter()
        Yh = host(V[:, kg:]) if kg < k else None
        th = time.perf_counter() - t
        Yg = [self._mul_finish(h) for h in hs]
        tw = time.perf_counter() - t - th
        Y = np.concatenate(Yg + ([np.asarray(Yh, complex)] if Yh is not None
                                 else []), 1) if k else np.zeros((N, 0), complex)
        return Y if host is None else (Y, th, tw)

    def _mul_start(self, V, i):
        """Record and submit Z V (V: N x k <= SOLVE_RHS) through the mapped
        host buffers of chunk i: (submission, its result's buffer)."""
        N = self._zn()
        k = V.shape[1]
        n2 = 2 * N * k  # (as float2)
        hv, hy = self._host(f'dv{i}', 8 * n2), self._host(f'dy{i}', 8 * n2)
        ff = getattr(self.zd, 'ff', False)
        if ff:  # (each number hi + lo: re_hi, im_hi, re_lo, im_lo)
            Vt = np.asarray(V.T)
            h = Vt.astype(np.complex64)
            l = (Vt - h).astype(np.complex64)
            o = hv.view(np.float32, 4 * N * k).reshape(k, N, 4)
            o[..., 0], o[..., 1], o[..., 2], o[..., 3] = (h.real, h.imag,
                                                          l.real, l.imag)
        else:
            hv.view(np.complex128, N * k).reshape(k, N)[...] = V.T
        Vd, Yd = self._work('dv', 8 * n2), self._work('dy', 8 * n2)
        self._copy(self._flatdesc(hv.address, n2),
                   self._flatdesc(Vd.device_address, n2), n2, 1)
        if hasattr(self.zd, 'groups'):  # (the half: its rows, their mirror)
            d = self.zd.desc()
            kw = (dict(Hf_=d, Vf_=Vd.device_address, zm_=0) if ff else
                  dict(Hd_=d, V_=Vd.device_address))
            kw.update(Y_=Yd.device_address, n_=N, k_=k)
            dk = '' if not ff or self._fma_fused() else 'd'
            self._run('zhalf_rows_ff' + dk if ff else 'zhalf_rows',
                      ((-(-N // DRF), -(-k // DCF), 1) if ff else
                       (-(-N // DR), -(-k // DC), 1)), **kw)
            # the mirror in chunks of 8 blocks (every row group's chunks
            # written, the ones it has none of as zeros), then their sum
            nblk = -(-N // self.zd.nb)
            nch = max(1, -(-(nblk - 1) // 8))
            P = self._work('dp', 16 * nch * N * min(k, self.SOLVE_RHS))
            w = DC2F if ff else 4 if k <= 4 else 8
            self._run('zhalf_cols_ff' + dk if ff else
                      'zhalf_cols4' if w == 4 else 'zhalf_cols',
                      (-(-N // 256), -(-k // w), nch), P_=P.device_address,
                      nch_=nch, **kw)
            self._run('zhalf_sum', self._flat(N * k), P_=P.device_address,
                      nch_=nch, **kw)
        else:
            self._run('zgemv', (-(-N // DR), -(-k // DC), 1),
                      Zd_=self._zdesc(), V_=Vd.device_address,
                      Y_=Yd.device_address, n_=N, k_=k)
        self._copy(self._flatdesc(Yd.device_address, n2),
                   self._flatdesc(hy.address, n2), n2, 1)
        return self._submit(), hy, N, k

    def _mul_finish(self, h):
        sid, hy, N, k = h
        self.dev.wait_for_submit(sid)
        return hy.view(np.complex128, N * k).reshape(k, N).T.copy()

    def factor_from_half(self, H, dsc, chunk=1 << 28):
        """factor() of the double half H (a host half.Half, complex128)
        equilibrated by dsc: its rows converted a range at a time into the
        mapped staging buffers (the core's half_c64_rows) and set at both
        places of the LU's whole matrix on the GPU (rows_to_mat). The LU of an
        LDL^T without a factor had made the complex64 half and its whole copy
        on the host, 12 N^2 beside the double half (a LimeRFE piece of 33k
        unknowns OOM-killed at 21.9 GB)."""
        from . import _core
        if not self.dense_ok():
            raise RuntimeError("factor_from_half: no shaderFloat64 module")
        self.release()
        N, t0 = H.n, time.perf_counter()
        A = _Mat(self, N, N)
        d = np.ascontiguousarray(dsc, np.float64)
        per = chunk // 8
        hs = [self._host(f'hup{i}', chunk) for i in (0, 1)]
        subs, j = [None, None], 0
        for J in range(H.nblk):
            a, b = int(H.j0[J]), int(H.j0[J] + H.w[J])
            ld, r = N - a, a
            while r < b:
                r1 = min(b, r + max(1, per // ld))
                cnt, i = (r1 - r) * ld, j % 2
                if subs[i] is not None:
                    self.dev.wait_for_submit(subs[i])
                _core.half_c64_rows(H.data, d, hs[i].view(np.complex64, cnt),
                                    H.nb, r, r1)
                self._run('rows_to_mat', self._flat(cnt), C_=A.desc(),
                          src_=hs[i].address, r0_=r, rows_=r1 - r, j0_=a,
                          ld_=ld)
                subs[i] = self._submit()
                j += 1
                r = r1
        for x in subs:
            if x is not None:
                self.dev.wait_for_submit(x)
        self._factor(A, t0)

    def factor_dense(self, dsc):
        """factor() of put_dense's Z equilibrated by dsc, its complex64 copy
        made on the GPU (none on the host, nothing more uploaded)."""
        self.release()
        N, t0 = self.zd.cols, time.perf_counter()
        A = _Mat(self, N, N)
        d = self._buffer(8 * N, np.ascontiguousarray(dsc, np.float64))
        self._run('to_c64', self._flat(N * N), Zd_=self._zdesc(),
                  C_=A.desc(), dsc_=d.device_address, n_=N)
        self._factor(A, t0)

    # ---- the complex-symmetric LDL^T (gpu_ldl.py) ---------------------------
    def vram_needed_ldl(self, N):
        """The LDL^T's half matrix, its work arrays, 0.4 GB besides."""
        from .gpu_ldl import vram_needed
        return vram_needed(N, self.nb)

    def fits_ldl(self, N, extra=0.0):
        from .policy import gpu_fits
        return gpu_fits(self.vram_needed_ldl(N), self.mem_free() + extra)

    # factor_ldl takes the double half and its equilibration (converted on
    # the way up: gpu_ldl.upload_c64)
    LDL_FROM_DOUBLE = True

    def factor_ldl(self, H, dsc=None):
        """The LDL^T of a host half.Half (complex64, equilibrated; or the
        double half with dsc, converted on the way up): a gpu_ldl.LDLFactor
        (raises ZeroDivisionError / FloatingPointError where it has no factor
        without pivoting)."""
        from . import gpu_ldl
        return gpu_ldl.factor(self, H, dsc)

    def factor_dense_ldl(self, dsc):
        """factor_ldl of put_dense's Z equilibrated by dsc, its complex64
        half made on the GPU (to_c64_half: nothing more uploaded)."""
        from . import gpu_ldl
        self.release()
        N, t0 = self._zn(), time.perf_counter()
        gpu_ldl.kernels(self)
        dev = gpu_ldl.HalfDev(self, N, self.nb)
        d = self._buffer(8 * N, np.ascontiguousarray(dsc, np.float64))
        half = hasattr(self.zd, 'groups')
        if half and self.zd.nb != self.nb:
            raise ValueError("the double half's blocks are not the GPU's")
        for J in range(len(dev.j0)):
            w, ld = int(dev.w[J]), int(dev.ld[J])
            kw = dict(dsc_=d.device_address, hp_=dev.at(J),
                      j0_=int(dev.j0[J]), w_=w, ld_=ld)
            if half and getattr(self.zd, 'ff', False):
                self._run('to_c64_hf', self._flat(w * ld), Hf_=self.zd.desc(),
                          jb_=J, **kw)
            elif half:
                self._run('to_c64_hh', self._flat(w * ld), Hd_=self.zd.desc(),
                          jb_=J, **kw)
            else:
                self._run('to_c64_half', self._flat(w * ld),
                          Zd_=self._zdesc(), **kw)
        return gpu_ldl._factor(self, dev, t0)

    def solve_ldl(self, F, R):
        from . import gpu_ldl
        return gpu_ldl.solve(self, F, R)

    def download_ldl(self, F):
        from . import gpu_ldl
        return gpu_ldl.download(self, F)

    # ---- solves and the factor on the host ----------------------------------
    def download(self, chunk=1 << 28):
        """The factor on the host, as scipy.linalg.lu_factor's of A = Z^T: (lu,
        piv), Fortran order, 0-based."""
        A = self.lu[0]
        N = A.rows
        # rows = A's columns: out.T is A, Fortran
        out = np.empty((N, N), np.complex64)
        cols = max(1, min(N, chunk // (8 * N)))
        h = self._host('down', 8 * N * cols)
        for c0 in range(0, N, cols):
            cc = min(cols, N - c0)
            self._copy(A.desc(0, c0), self._flatdesc(h.address, N), N, cc)
            self.dev.wait_for_submit(self._submit())
            out[c0:c0 + cc] = h.view(np.complex64, N * cc).reshape(cc, N)
        return out.T, self.piv.copy()

    # the right-hand sides a GPU solve takes at once, and the blocks of the
    # factor it records into one submission: a Thor carrier's 158 ports in one
    # command buffer ran past the driver's watchdog, which reset the context
    # (VK_ERROR_DEVICE_LOST)
    SOLVE_RHS, SOLVE_BLOCKS = 32, 16

    def solve(self, R):
        """Z^-1 R (R: host, N x k or N), on the GPU, SOLVE_RHS columns at a
        time."""
        R = np.asarray(R)
        if R.ndim == 2 and R.shape[1] > self.SOLVE_RHS:
            return np.concatenate([
                self._solve(R[:, c:c + self.SOLVE_RHS])
                for c in range(0, R.shape[1], self.SOLVE_RHS)], 1)
        return self._solve(R)

    def _solve(self, R):
        """Z^-1 R (R: host, N x k or N), on the GPU: U^T y = R, L^T w = y, x = P
        w. complex64 inside, complex128 out; submitted SOLVE_BLOCKS blocks at a
        time."""
        A, Dbuf, order = self.lu
        N, nb = A.rows, self.nb
        R = np.asarray(R)
        one = R.ndim == 1
        R2 = np.asarray(R, np.complex64).reshape(N, -1)
        k = R2.shape[1]
        X = self._work('x', 8 * N * k)
        hx = self._host('x', 8 * N * k)
        hx.view(np.complex64, N * k).reshape(k, N)[...] = R2.T
        self._copy(self._flatdesc(hx.address, N * k),
                   self._flatdesc(X.device_address, N * k), N * k, 1)
        xa = X.device_address
        nblk = -(-N // nb)
        for fwd in (1, 0):
            # U_bb^-1 (forward), L_bb^-1 (backward)
            D = Dbuf[0 if fwd else 1].device_address
            for bi in (range(nblk) if fwd else range(nblk - 1, -1, -1)):
                b0 = bi * nb
                bn = min(nb, N - b0)
                self._run('solve_tri', (1, k, 1), X_=xa, D_=D, nrhs_=k, blk_=b0,
                          bn_=bn, k_=N)
                rest = N - b0 - bn if fwd else b0
                if rest > 0:
                    self._run('solve_upd',
                              (min(rest, 65535), -(-rest // 65535), 1),
                              C_=A.desc(), X_=xa, nrhs_=k, blk_=b0, bn_=bn,
                              k_=N, fwd_=fwd)
                if bi % self.SOLVE_BLOCKS == self.SOLVE_BLOCKS - 1:
                    self._submit()  # (in order on the one queue)
        self._copy(self._flatdesc(X.device_address, N * k),
                   self._flatdesc(hx.address, N * k), N * k, 1)
        self.dev.wait_for_submit(self._submit())
        W = hx.view(np.complex64, N * k).reshape(k, N).T.astype(complex)[order]
        return W[:, 0] if one else W
