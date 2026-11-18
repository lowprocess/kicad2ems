"""
mom25d.fast - the solvers: Z filled by the compiled core and solved directly
(LU, double or mixed precision, on the CPU or the GPU) or by the block
low-rank LU and GMRES

  Z = Z_far + dZ_near
    Z_far   : every interaction with a 1-point (centroid) rule per triangle
    dZ_near : sparse correction (accurate - centroid) for triangle pairs
              closer than near_fac cell sizes: analytic singular /
              near-singular parts + higher-order quadrature
  Vias and lumped elements add a few dense rows/columns and sparse entries.
The compiled core is required (its mesher too): python -m
kicad2ems.mom25d.core.build.
"""
import os
import threading
import time
from collections import namedtuple

import numpy as np
import scipy.linalg as sla
from scipy import sparse

from .base import gram_matrix
from . import _core  # python -m kicad2ems.mom25d.core.build
from kicad2ems.emsdev.memory import available as available_memory
from .blocks import Anchors
from .policy import LOOP_TREE_BELOW, RETRY, REUSE_KEEP, SPARE, choose, device


def fast_cores():
    """The physical cores of the fastest class (a hybrid CPU's P-cores; every
    core of the others) among the CPUs this process may use: emsdev's, which the
    other solvers ask too (mom25d had its own rule, 0.9 of the top clock over
    every CPU of the machine, affinity ignored); None where emsdev cannot
    tell."""
    from kicad2ems.emsdev.cpu import core_threads
    return core_threads() or None


_blas_capped = False


def cap_blas_threads():
    """Once: the LU's BLAS (OpenBLAS / MKL, through threadpoolctl) on the fast
    physical cores only. A BLAS LU splits each step evenly over its threads and
    waits for the slowest: on an i7-13700K (8 P-cores with their hyperthreads, 8
    E-cores) its 24 threads made rf_stubnotch's 1.5k-unknown LU 0.62 s against
    0.047 s on 8 (a whole kicad2ems run 10.8 s against 3.3 s), as fast at 5k
    unknowns. MOM25D_BLAS_THREADS (or OPENBLAS_NUM_THREADS / MKL_NUM_THREADS)
    sets it instead; without threadpoolctl nothing is changed."""
    global _blas_capped
    cap_core_threads()  # (its own setting: a BLAS one set by the user had
    if _blas_capped:    # returned before it, so OpenMP ran on every thread;
        return          # and per thread, below)
    _blas_capped = True
    env = os.environ.get('MOM25D_BLAS_THREADS')
    if (not env and (os.environ.get('OPENBLAS_NUM_THREADS')
                     or os.environ.get('MKL_NUM_THREADS'))):
        return
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        return
    n = int(env) if env else fast_cores()
    if n:
        threadpool_limits(limits=n, user_api='blas')


_omp_capped = threading.local()


def cap_core_threads():
    """Once in each thread: the compiled core's OpenMP (the matrix fill, the
    near-field correction) on the fast physical cores only, as the BLAS. Its
    schedule splits the rows over every thread, and on an i7-13700K the E-cores
    and the hyperthreads held the fill back: antenna_vivaldi's (21.9k unknowns)
    took 1.5-3.1 s a frequency on 24 threads, 0.87 s on 8 and 0.79 s on 16, the
    whole fresh solve 8.5 s against 6.9-7.1 s. Per thread: libgomp keeps
    omp_set_num_threads as the calling thread's own, so a cap set once in the
    main thread left a solve in another (kicad2ems's --solver-jobs, a window's
    worker) on all 24 CPUs. MOM25D_OMP_THREADS (or OMP_NUM_THREADS) sets it
    instead; without threadpoolctl nothing is changed."""
    if getattr(_omp_capped, 'done', False):
        return
    _omp_capped.done = True
    env = os.environ.get('MOM25D_OMP_THREADS')
    if not env and os.environ.get('OMP_NUM_THREADS'):
        return
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        return
    n = int(env) if env else fast_cores()
    if n:
        threadpool_limits(limits=n, user_api='openmp')


# bytes the via blocks kept over a sweep may take on a small board (a large
# one: a double Z's; FastOperator._vias). Where a fifth of the unknowns are
# the sides', a double Z's bytes hold 7 or 8 anchors: rf_crosstalk (2.1k
# unknowns) filled 14 of its 15 frequencies, rf_bends (7.1k, 1 to 12 GHz,
# which takes 16 anchors) all 29
ANCHOR_ROOM = 2.0 ** 31
# near-field triplets per piece of the sum into the sparse matrix (_near_csr)
NEAR_CHUNK = 8_000_000


def _near_csr(ri, ci, vv, n, owner):
    """The n x n CSR of the core's near-field triplets, duplicates summed. The
    structure is built once and kept on `owner` (3.4 s on the SMA launch's 84 M
    triplets); each frequency then only sums its values into it, a piece at a
    time (0.6 s, against 3.1 s for SciPy's COO -> CSR every frequency)."""
    pat = getattr(owner, '_near_pat', None)
    if pat is None or pat[0] != (len(ri), n):
        # the CSR structure of the triplets (row < 0: unused), the same at every
        # frequency (a pair's nine slots are the edges of its two triangles):
        # the used slots in CSR order, and where each entry's run of them starts
        slot = np.flatnonzero(ri >= 0)
        key = ri[slot].astype(np.int64) * n + ci[slot]
        o = np.argsort(key)
        slot, key = slot[o].astype(np.int32), key[o]
        del o
        first = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        key = key[first]
        indptr = np.r_[0, np.cumsum(np.bincount(key // n, minlength=n))].astype(
            np.int32)
        pat = owner._near_pat = ((len(ri), n),
                                 (indptr, (key % n).astype(np.int32), slot,
                                  first.astype(np.int32)))
    indptr, indices, slot, first = pat[1]
    data = np.empty(len(first), vv.dtype)
    a = 0
    while a < len(first):  # whole entries per piece: about NEAR_CHUNK slots
        b = min(len(first), int(np.searchsorted(first, first[a] + NEAR_CHUNK)))
        s1 = first[b] if b < len(first) else len(slot)
        data[a:b] = np.add.reduceat(vv[slot[first[a]:s1]],
                                    first[a:b] - first[a])
        a = b
    M = sparse.csr_matrix((data, indices, indptr), shape=(n, n))
    M.has_canonical_format = True
    return M


# ---- the BLR's tiles: a cluster tree ----------------------------------------
class Cluster:
    __slots__ = ('idx', 'lo', 'hi', 'kids')

    def __init__(self, idx, pts, leaf):
        p = pts[idx]
        self.idx, self.lo, self.hi, self.kids = idx, p.min(0), p.max(0), []
        if len(idx) > leaf:  # bisect along the longest side
            o, h = (np.argsort(p[:, np.argmax(self.hi - self.lo)],
                               kind='stable'), len(idx) // 2)
            self.kids = [
                Cluster(idx[o[:h]], pts, leaf),
                Cluster(idx[o[h:]], pts, leaf)
            ]

    @property
    def diam(self):
        return np.linalg.norm(self.hi - self.lo)


def cluster_tiles(pts, tile):
    """The block low-rank solver's tiles: geometric bisection (Cluster) into
    leaves of <= tile points; their index arrays and bounding boxes."""
    def leaves(c):
        return [c] if not c.kids else leaves(c.kids[0]) + leaves(c.kids[1])

    tiles = leaves(Cluster(np.arange(len(pts)), pts, tile))
    return [c.idx for c in tiles], [(c.lo, c.hi) for c in tiles]


# ---- Fast operator for a Simulation -----------------------------------------
# refinement iterations before giving up on the complex64 LU (2 on the demo
# transition; a stall ran all 300 of restart 30 x 10: 150 s on 8.4k unknowns
# against 18 s for a double LU)
MIXED_MAX_ITER = 30
# a fresh complex64 LU on the GPU is given up when the residual is above 0.1
# after 4 iterations: where ROCm's triangular solves lose an ill-conditioned
# factor (gpu.py) it stays at 1 for all 30 (the via transition with its planes
# meshed, 13.9k unknowns at 1 GHz: 3.8 s for 0.5 s)
FRESH_STALL = (4, 0.1)
# GMRES iterations a kept LU may take (FastOperator._reused): the cap, from
# the cost of a fresh solve, is clamped to this range
REUSE_ITER = (8, 60)
# ... and it is given up as soon as, at the rate so far, its residual would
# still be this x the accepted one after them (rf_ebg, 17.3k unknowns, a
# product of its double Z 0.3 s: a kept LU that ended its 8 iterations at
# 0.28 was 3 s for nothing, and 5 of 15 did)
REUSE_SLACK = 3.0
# the block low-rank factor is given up when the residual is above 1e-2 after
# 30 GMRES iterations (a good one takes 3-16), and factored again at a
# hundredth of the tolerance
BLR_STALL = (30, 1e-2)
BLR_TOL_MIN = 1e-9       # ... down to this
# a block low-rank solve whose GMRES ends above this residual is an error, not
# an answer
BLR_GIVE_UP = 1e-2


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


# a kept factorisation: its frequency, M (HostLU, GpuLU, _CompiledBLR), the
# equilibration, the seconds a fresh solve took, whether the sides' loops
# transformed its matrix
Kept = namedtuple('Kept', 'f M dsc cost loops')


class _State:
    """A basis's kept factorisations and what was learnt about them: reach,
    the |ln(f / f0)| a kept one is tried within; t_iter, the seconds a
    GMRES iteration takes (what caps a kept one's iterations)."""

    def __init__(self):
        self.kept, self.reach, self.t_iter = [], np.log(2.0), None

    def nearest(self, f, loops=False, gpu=True):
        """The kept one nearest f (gpu False: not one on the GPU), if within
        reach; or None."""
        ks = [k for k in self.kept
              if k.loops == loops and (gpu or not k.M.on_gpu)]
        k = min(ks, key=lambda k: abs(np.log(f / k.f)), default=None)
        return (k if k is not None and abs(np.log(f / k.f)) <= self.reach
                else None)

    def drop(self, gpu=False):
        """Drop the most redundant kept one (gpu: of those on the GPU): the
        one nearest another in frequency."""
        idx = [i for i, k in enumerate(self.kept) if not gpu or k.M.on_gpu]
        lf = np.log([k.f for k in self.kept])
        gap = [min((abs(lf[i] - y) for j, y in enumerate(lf) if j != i),
                   default=0.0) for i in idx]
        del self.kept[idx[int(np.argmin(gap))]]

    def held(self):
        """The kept ones' (host, GPU) bytes."""
        return (sum(k.M.nbytes for k in self.kept),
                sum(k.M.device_bytes for k in self.kept))


class FastOperator:
    """Builds Z for a mom25d.sim.Simulation with the compiled core and solves
    with it (solve: policy.choose's plan at each frequency)."""

    def __init__(self, sim, near_fac=None):
        r = sim.rwg
        self.sim, self.mixed_tol = sim, 1e-6
        self.near_fac = near_fac or sim.near_fac
        # per basis ('rwg', 'loop/tree'): the kept LUs (_State); {(basis,
        # rung): the highest frequency it failed at} (policy.choose)
        self.states, self.history = {}, {}
        self._said = set()  # the notes given once
        # the frequency dZ_exact, B and C were set up for
        self._ready = None
        # half-basis description: half h is edge h on its T+ (h < Ne), edge h -
        # Ne on its T-: triangle, alpha, free vertex; and the halves on each
        # triangle (-1: none)
        self.h_tri = np.concatenate([r.tp, r.tm])
        self.h_alpha = np.concatenate(
            [r.l / (2 * r.area[r.tp]), -r.l / (2 * r.area[r.tm])])
        self.h_v = np.vstack([r.vp, r.vm])
        self.tri_halves, fill = (-np.ones(
            (len(r.tris), 3), int), np.zeros(len(r.tris), int))
        for h, t in enumerate(self.h_tri):
            self.tri_halves[t, fill[t]] = h
            fill[t] += 1
        self.layer_of_tri, self.cent, self.area, self.layers = (sim.tri_layer,
                                                                r.cent, r.area,
                                                                sim.layers)
        self.lidx = {k: i for i, k in enumerate(self.layers)}
        self.dZ_exact, self.core = None, _core
        # the loop/tree basis (_solve_loop_tree)
        self._lt, self._basis_name = None, 'rwg'
        self._near_pairs()
        # the compiled core's geometry
        Ne, H, c = sim.Ne, self.tri_halves, np.ascontiguousarray
        self.g = dict(
            ep=r.tp.astype(np.int32), em=r.tm.astype(np.int32),
            lt=np.array([self.lidx[k] for k in self.layer_of_tri],
                        np.int32), te=np.where(H >= 0, H % Ne,
                                               -1).astype(np.int32),
            ta=np.where(H >= 0, self.h_alpha[H],
                        0.0), tv=np.where((H >= 0)[..., None], self.h_v[H],
                                          0.0), ap=self.h_alpha[:Ne].copy(),
            am=self.h_alpha[Ne:].copy(), vp=c(self.h_v[:Ne]),
            vm=c(self.h_v[Ne:]), cent=c(r.cent), area=r.area.copy())
        self.q = dict(nodes=c(r.nodes), tris=r.tris.astype(np.int32),
                      P3=c(sim.P3), W3=c(sim.W3), P6=c(sim.P6),
                      W6=c(sim.W6))
        self.nP32, self.nQ32 = (self.nP.astype(np.int32),
                                self.nQ.astype(np.int32))
        self._near_pat = None  # _near_csr: the structure of these pairs
        # the via blocks kept over a sweep; the frequency's weights of them
        self.anchors, self._kw = Anchors(), None
        # frequencies whose via blocks were filled, and taken between them
        self.nblocks = [0, 0]
        # the near pairs' analytic parts (geometry only, most of a pair's
        # cost) kept across frequencies by the core: 96 bytes a pair; the
        # via transition's 11.3 M pairs, 1.1 GB: its near field into the
        # dense Z 0.29 s a frequency against 1.10 s
        if 96.0 * len(self.nP) <= 0.1 * available_memory():
            self.q.update(stat=np.zeros((len(self.nP), 12)),
                          stat_ready=False)

    # ---- near pairs (geometry only) -----------------------------------------
    def _near_pairs(self):
        sim, r = self.sim, self.sim.rwg
        size = sim.tri_size
        # (sorted pairs, near if within either's radius)
        self.nP, self.nQ = _core.near_pairs(
            np.ascontiguousarray(r.cent, float),
            np.ascontiguousarray(size, float),
            np.ascontiguousarray(sim.st.z[self.layer_of_tri], float),
            float(self.near_fac))
        sim.log.note(
            f"fast solver: {len(self.nP)} near triangle pairs "
            f"({len(self.nP) / len(size):.0f} per triangle)"
        )

    # ---- kernels ------------------------------------------------------------
    def entry(self, rows, cols):
        """The far field's (centroid-rule) block Z[rows, cols] of the edges."""
        return self.core.far_block(np.asarray(rows, np.int32),
                                   np.asarray(cols, np.int32), self.g, self.tb,
                                   self.w)

    # ---- near correction ----------------------------------------------------
    def _fan_columns(self):
        """Accurate G_q moments between every triangle and the via fan triangles
        (Nt x |F|)."""
        F = self.sim.F
        QF = self.core.fan_q(self.g, self.tb, np.asarray(F, np.int32))
        # exact correction for near pairs only (same split as the rest of the
        # operator)
        pos, sel = {q: j for j, q in enumerate(F)}, np.isin(self.nQ, F)
        np.add.at(QF,
                  (self.nP[sel], np.array([pos[q] for q in self.nQ[sel]], int)),
                  self._qcorr[sel])
        return QF

    def _near_exact(self):
        """The core's exact near-field correction (sparse, double); keeps its
        via-fan part (_qcorr)."""
        ri, ci, vv, self._qcorr = self.core.near_coo(self.g, self.tb, self.q,
                                                     self.nP32, self.nQ32,
                                                     self.w)
        self._near_kept()
        return _near_csr(ri, ci, vv, self.sim.Ne, self)

    # a near triangle pair's peak memory in the sparse correction: its nine
    # triplets from the core (24 B each), their CSR structure's sort (20 B each)
    # and the CSR (20 B each)
    NEAR_BYTES = 400.0

    def _near_kept(self):
        """After a pass of the core's near_core: its analytic parts are in
        q['stat']."""
        if 'stat' in self.q:
            self.q['stat_ready'] = True

    def _vias(self, f, Z=None, z=True):
        """The via rows / columns B and block C. Filled: the sides' part
        (sim._side_blocks) and the rest, the chains' (sim._via_blocks with
        this operator's fan columns), both kept (blocks.Anchors: in at most
        a double Z's bytes, or ANCHOR_ROOM, and where the memory
        policy.choose's rules ask for stays available: the hybrid's
        matrices (z: Z is there), the LUs a CPU's plan keeps, and the next
        fill's own arrays). Between the frequencies kept, as setup found:
        the sides' part from them, and the chains' where theirs holds too,
        else filled (the via kernels' own tables are rougher than the
        Green tables: antenna_array4x4_phased's 16 chains 1e-5 between
        seven anchors of a 20 % band, its sides 1e-9). Z (complex128):
        into its border, B and C not kept."""
        sim, an = self.sim, self.anchors
        if not sim.Nv:
            return
        Ne, Nv, N = sim.Ne, sim.Nv, sim.Ne + sim.Nv
        c0 = Nv - (len(sim.sides) if sim.sides is not None else 0)
        kw, ok = self._kw or (None, [False] * 4)
        B = Z[:Ne, Ne:] if Z is not None else np.empty((Ne, Nv), complex)
        if not (ok[2] and ok[3]):
            with sim.log.timer('via kernels', f"{c0} chains"):
                Bc, C = sim._via_blocks(f, self._fan_columns(), loss=False,
                                        sides=False)
            B[:, :c0] = Bc[:, :c0]
        self.nblocks[kw is not None] += 1
        if kw is None:
            Bw, Cw = (sim._side_blocks(f) if c0 < Nv else
                      (np.zeros((Ne, 0), complex), np.zeros_like(C)))
            if getattr(sim, 'interp', 'auto') != 'off':
                lus = (REUSE_KEEP if getattr(sim, 'plan', {}).get('path')
                       == 'cpu-mixed' else 0)
                d = 1.0 / np.sqrt(np.abs(np.diagonal(C) + np.diagonal(Cw))
                                  + 1e-300)
                an.add(f, [Bw, Cw, Bc[:, :c0], C], self._s(f),
                       {k: self.tb[k] for k in ('A', 'Q', 'cA', 'cq', 'cAi',
                                                'cqi')},
                       [(None, d[c0:]), (d, d), (None, d[:c0]), (d, d)],
                       max(16.0 * N * N, ANCHOR_ROOM),
                       (14.0 + 8.0 * lus + (0.0 if z else 16.0)) * N * N
                       + 2.0 * 16.0 * Nv * N + 1.25 * SPARE)
        else:
            with sim.log.timer('via kernels (interpolated)',
                               f"{len(kw)} of {len(an.f)} frequencies"):
                s = self._s(f)
                Bw, Cw = an.mix(0, kw, s, B[:, c0:]), an.mix(1, kw, s)
                if ok[2] and ok[3]:
                    an.mix(2, kw, s, B[:, :c0])
                    C = an.mix(3, kw, s)
        if kw is None:
            B[:, c0:] = Bw
        C += Cw
        n = np.arange(len(sim.chains))
        C[n, n] += sim._via_loss(f)
        if Z is not None:
            Z[Ne:, Ne:] = C
            _border(Z, B, C, both=False)
        else:
            self.B, self.C = B, C

    def _s(self, f):
        """What the via blocks are smooth with (blocks.py): jw and the
        permittivity about the metal interface of the most triangles."""
        sim = self.sim
        k = max(sim.layers, key=lambda k: len(sim.tri_idx[k]))
        return 2j * np.pi * f * sim.st.at(f).eps_sum(k)

    # ---- assemble & solve ---------------------------------------------------
    def matvec_exact(self, X, Sx=None):
        """Z X in double precision without storing Z, X: N or N x k (far:
        triangle-aggregated, one kernel pass for all columns; near: dZ_exact, or
        on the fly)."""
        Ne, core, X = self.sim.Ne, self.core, np.asarray(X, complex)
        Xe = np.ascontiguousarray(X[:Ne])
        per_column = (lambda fn: fn(Xe) if X.ndim == 1 else np.stack(
            [fn(np.ascontiguousarray(Xe[:, j])) for j in range(X.shape[1])], 1))
        if X.ndim == 2:
            far = core.matvec_far_multi(Xe, self.g, self.tb, self.w)
        else:
            far = per_column(
                lambda x: core.matvec_far(x, self.g, self.tb, self.w))
        near = (self.dZ_exact @ Xe if self.dZ_exact is not None
                else per_column(lambda x: core.near_matvec(
                    x, self.g, self.tb, self.q, self.nP32, self.nQ32, self.w)))
        if self.dZ_exact is None:
            self._near_kept()
        return self._bordered(
            X, far + near + (Sx[:Ne, :Ne] @ Xe if Sx is not None else 0))

    def _bordered(self, X, Ye):
        """Z X from Ye, the edge-edge block's product: the via rows and columns
        (B, C) added."""
        Ne, Y = self.sim.Ne, np.empty(X.shape, complex)
        Y[:Ne] = Ye
        if self.sim.Nv:
            Y[:Ne] += self.B @ X[Ne:]
            Y[Ne:] = self.B.T @ X[:Ne] + self.C @ X[Ne:]
        return Y

    def setup(self, f, mode='direct'):
        """mode: 'direct' (the tables only: the core fills Z), 'sparse' (the
        tables and the sparse near field, dZ)."""
        sim, an = self.sim, self.anchors
        self.f, self.w = f, 2 * np.pi * f
        # between the frequencies whose via blocks were kept (blocks.py):
        # theirs and the tables', as one
        kw = (an.at(f) if sim.Nv and getattr(sim, 'interp', 'auto')
              != 'off' and self.kh2(f) >= LOOP_TREE_BELOW else None)
        # (the sides' blocks' estimate, or without sides the chains': the
        # tables' too)
        if kw is not None and not all(kw[1][:2] if sim.sides is not None
                                      else kw[1][2:]):
            kw = None
        self._kw = kw
        if kw is not None:
            self.tb = dict(self.tb, **an.tables(kw[0], f, self._s(f)))
        else:
            self._tables(f)
        if mode == 'direct':
            return
        # MemoryError, saying so, where the sparse near-field correction cannot
        # fit: memory_plan counts the solver's own matrices, not it, and on a
        # fine mesh of several layers it is the larger (antenna_aperture's whole
        # stack, 78k unknowns: 37 M near pairs, ~15 GB, which a 16 GB cap killed
        # without a word)
        need, avail = self.NEAR_BYTES * len(self.nP), available_memory()
        if need > 0.85 * avail:
            raise MemoryError(
                f"the near-field correction of {sim.Ne:,} unknowns needs "
                f"~{need / 1e9:.1f} GB ({len(self.nP):,} near triangle pairs), "
                f"{avail / 1e9:.1f} GB available: larger cells (a coarser "
                "dmax, or coarsen) on the widest copper")
        with sim.log.timer('near-field correction'):
            self.dZ = self._near_exact()
        self._vias(f, z=False)

    def _tables(self, f):
        """The Green tables at f as the compiled core takes them (tb)."""
        self.tables = self.sim.green_tables(f)
        keys = sorted(self.tables)
        T = [self.tables[k] for k in keys]
        tab = np.zeros((len(self.layers),) * 2, np.int32)
        for i, (a, b) in enumerate(keys):
            tab[self.lidx[a], self.lidx[b]] = tab[self.lidx[b],
                                                  self.lidx[a]] = i
        get = lambda attr, dt=float: np.array(
            [dt(getattr(t, attr)) for t in T])
        self.tb = dict(
            A=np.stack([t.A_rest for t in T]).astype(complex),
            Q=np.stack([t.Q_rest for t in T]).astype(complex),
            dr=np.array([t.rho[1] for t in T]), d=get('d'), dimg=get('dimg'),
            cA=get('cA', complex), cq=get('cq', complex),
            cAi=get('cAi', complex), cqi=get('cqi', complex), tab=tab,
            kind=np.array([0 if a == b else (1 if t.cA != 0 else 2)
                           for (a, b), t in zip(keys, T)], np.int32))
        if ('stat' in self.q
                and not np.array_equal(self.tb['kind'],
                                       self.q.get('stat_kind'))):
            # other parts extracted: computed again
            self.q.update(stat_ready=False, stat_kind=self.tb['kind'])

    def extra_blocks(self, f):
        """Lumped elements and metal loss: a sparse N x N addition to Z."""
        sim, Ne, N = self.sim, self.sim.Ne, self.sim.Ne + self.sim.Nv
        S = sparse.lil_matrix((N, N), dtype=complex)
        for el, (idx, sig) in zip(sim.lumped, sim.lumped_edges):
            u = sig * sim.rwg.l[idx]
            S[np.ix_(idx, idx)] = (S[np.ix_(idx, idx)].toarray()
                                   + el.Z(f) * np.outer(u, u))
        if sim.metal is not None:
            if sim._gram is None:
                sim._gram = sparse.csr_matrix(gram_matrix(sim.rwg))
            # per edge where a finish covers some layers
            zs = sim.metal.zs_edges(f, sim.edge_layer)
            S[:Ne, :Ne] = (S[:Ne, :Ne] +
                           (sparse.diags(zs) @ sparse.csr_matrix(sim._gram) if
                            np.ndim(zs) else zs * sparse.csr_matrix(sim._gram)))
        if getattr(sim, 'edge', None) is not None:
            # thick copper's edges (mom25d.edge)
            return (S.tocsr() + sim.edge.matrix(sim, f)).tocsr()
        return S.tocsr()

    def _refine(self, A, B, M, restart, maxiter, stall=None, timed=True,
                reach=None):
        """Batched GMRES on A Y = B preconditioned with M, to mixed_tol: (Y,
        converged, largest relative residual: B - A Y from the products GMRES
        made, no product of its own). Not converged includes a breakdown
        (NaN). stall: gmres_batched's, for a fresh factorisation
        (FRESH_STALL); reach: its, for a kept one. timed: the seconds an
        iteration took kept (_t_iter: what caps a kept LU's iterations)."""
        t, got = time.time(), {}
        Y, self.iters = gmres_batched(A, B, M=M, rtol=self.mixed_tol,
                                      restart=restart, maxiter=maxiter,
                                      stall=stall, reach=reach, out=got)
        rel = got['rel']
        if timed:
            self.st.t_iter = ((time.time() - t)
                            / max(1, max(self.iters, default=1)))
        return Y, bool((rel <= 100 * self.mixed_tol).all()), rel.max()

    def solve(self, f, E):
        """The currents at f for excitation columns E, as policy.choose plans:
        Z filled (dense double, or complex64 with the refinement
        matrix-free), in the RWG or the loop/tree basis; a kept LU tried,
        else a fresh one factored and refined, down the rungs where it
        fails (_ladder)."""
        sim, plan = self.sim, self.plan(f)
        st = self.st = self.states.setdefault(plan.basis, _State())
        if plan.basis != self._basis_name:  # the other's GPU LUs freed
            old = self.states.get(self._basis_name)
            if old is not None:
                old.kept = [k for k in old.kept if not k.M.on_gpu]
            self._basis_name = plan.basis
        if sim.reuse == 'off':
            st.kept = []
        if plan.rung == 'blr':
            return self._solve_blr(f, E)
        if sim.solver == 'blr':
            # low frequency: the RWG matrix's loops are below a complex64
            # factor's rounding, at any compression (antenna_vivaldi, 21.9k
            # unknowns, at 50 MHz: the factor made three times, to no
            # compression at all, GMRES still at a residual of 0.95: 84 s).
            # The loop/tree basis's dense solve, where it fits
            self._once('blr-lt', "block low-rank solver: frequencies below "
                       f"(k h)^2 = {LOOP_TREE_BELOW:g} ({f / 1e9:.4g} GHz "
                       "here) in the loop/tree basis, dense")
        self.setup(f, mode='direct')
        Sx, self.iters = self.extra_blocks(f), []
        if not plan.dense:
            X = self._reuse(f, E, Sx)
            if X is not None:
                return X
        if not (plan.dense and plan.keep):
            st.kept = []  # their memory is the new Z's
        t0 = time.time()
        Z, B, up, A = (self._lt_system(f, E, Sx) if plan.basis == 'loop/tree'
                       else self._rwg_system(f, E, Sx, plan))
        dsc = 1.0 / np.sqrt(np.abs(np.diagonal(Z)).astype(float) + 1e-300)
        d = dsc[:, None]
        B = d * B
        if plan.rung == 'cpu-c128':
            _equilibrate(Z, dsc)
            with sim.log.timer('LU factor', f"N={len(dsc)}"):
                lu = _lu(Z)
            del Z
            with sim.log.timer('LU solve'):
                return up(d * _lu_solve(lu, B))
        box = [Z]  # (the ladder frees it)
        del Z
        G = self._put_dense(plan, box[0])
        try:
            if plan.dense:
                # the double Z as filled, equilibrated in its products and in
                # its complex64 copy (_c64): scaled in place it was two passes
                # over it, 0.19 s of a 3.5 s solve at 12.3k unknowns
                A = ((lambda V: d * G.mul_dense(d * V)) if G is not None else
                     (lambda V: d * (box[0] @ (d * V))))
                k = st.nearest(f, plan.loops, gpu=not plan.gpu_factor)
                if k is not None:
                    # Z^-1 ~ D0 Z0s^-1 D0: Zs^-1 ~ (D0 / D) Z0s^-1 (D0 / D)
                    r = (k.dsc / dsc)[:, None]
                    Y = self._reused(f, k, A, B, lambda R: r * k.M(r * R))
                    if Y is not None:
                        return up(dsc[:, None] * Y)
                # (a dense LU's cost: its factor and refinement, which a kept
                # one saves; a matrix-free one's: the fill too)
                t0 = time.time()
            else:
                _equilibrate(box[0], dsc)
                Am = A
                A = lambda V: d * Am(d * V)
            Y = self._ladder(f, plan, box, A, B, dsc, t0)
        finally:
            if G is not None:
                G.drop_dense()
        if Y is None:  # matrix-free: Z is gone; f's plan is double now
            return self.solve(f, E)
        return up(dsc[:, None] * Y)

    def _put_dense(self, plan, Z):
        """The backend holding a copy of the double Z for the refinement's
        products (plan.gpu_dense; the GPU's LU made from it), or None: the
        host's."""
        if not plan.gpu_dense:
            return None
        gpu = self._backend()
        try:
            with self.sim.log.timer('Z to the GPU', f"N={len(Z)}, double"):
                zh = getattr(self, '_zh', None)
                gpu.put_dense(Z, zh if zh is not None and Z.flags.c_contiguous
                              and Z.ctypes.data == zh.array.ctypes.data
                              else None)
            return gpu
        except Exception as e:  # (out of device memory)
            gpu.drop_dense()
            self._once('gpu-dense', "the double-precision Z on the GPU failed"
                       f" ({type(e).__name__}: {e}): the refinement's "
                       "products on the host")
            return None

    def plan(self, f):
        """policy.choose's SolvePlan at f, from the simulation and what this
        operator holds and learnt; its notes once."""
        sim = self.sim
        gpu = self._backend() if sim.precision == 'mixed' else None
        held = {b: s.held() for b, s in self.states.items()}
        s = getattr(sim, 'sides', None)
        plan = choose(
            f, sim.Ne + sim.Nv, sim.qT.shape[1] if sim.qT is not None else None,
            nt=len(sim.rwg.tris), avail=available_memory(), gpu=device(gpu),
            precision=sim.precision, solver=sim.solver,
            reuse=sim.reuse != 'off', history=self.history, held=held,
            kh2=self.kh2(f), loop_tree=getattr(sim, 'loop_tree', 'auto'),
            lt_block=self._lt_block(), sides=s is not None and len(s.vert) > 0,
            gpu_dense=gpu is not None and gpu.dense_ok())
        if plan.why:
            self._once('lt', f"loop/tree basis not used at {f / 1e9:.4g} GHz "
                       f"((k h)^2 = {self.kh2(f):.1e}): {plan.why}")
        if sim.precision == 'mixed' and sim.gpu not in ('off', 'auto', None):
            if gpu is None:
                from .gpu import get_backend
                self._once('gpu', "GPU requested but not available: "
                           + "; ".join(get_backend.why))
        if gpu is not None and plan.rung != 'blr':
            n = sim.Ne + sim.Nv if plan.basis == 'loop/tree' else (
                sim.qT.shape[1] if sim.qT is not None else sim.Ne + sim.Nv)
            how = {'hybrid': "complex64 LU there, double-precision refinement "
                   + ("products there too" if plan.gpu_dense
                      else "on the host"),
                   'gpu-lu': "complex64 LU there, the refinement matrix-free"}
            self._once('gpu', (
                f"GPU: {gpu.device} via {gpu.name}, {gpu.mem_free() / 1e9:.1f}"
                f" GB free, LU needs ~{gpu.vram_needed(n) / 1e9:.1f} GB: "
                + how.get(plan.path, "factoring on the CPU")))
        return plan

    def _once(self, key, note):
        if key not in self._said:
            self._said.add(key)
            self.sim.log.note(note)

    def _failed(self, basis, rung, f):
        k = (basis, rung)
        self.history[k] = max(self.history.get(k, 0.0), f)
        return self.history[k]

    def _rwg_system(self, f, E, Sx, plan):
        """The RWG basis's Z at f (dense: complex128; else complex64, with the
        exact near field kept sparse for the matrix-free products), the
        right-hand side, up (Z's currents to the edges'), and the exact
        operator (matrix-free) in Z's basis."""
        sim = self.sim
        Ne, Nv, N, T = sim.Ne, sim.Nv, sim.Ne + sim.Nv, sim.qT
        dt = np.complex128 if plan.dense else np.complex64
        with sim.log.timer(
                'direct fill',
                f"N={N}, {np.dtype(dt).name}, {self.core.num_threads()} threads"
        ):
            # (gpu_dense: in the GPU's mapped host memory, _put_dense's)
            Z = (self._host_matrix(N) if plan.gpu_dense else
                 np.zeros((N, N), dt))
            (self.core.fill_far_c64 if dt == np.complex64 else
             self.core.fill_far)(Z, self.g, self.tb, self.w)
        ready = self._ready == f  # a kept LU's attempt set them up
        with sim.log.timer('near-field correction'):
            if dt == np.complex64:
                # the exact near correction (sparse, double) kept for the
                # refinement's products
                if not ready:
                    self.dZ_exact = self._near_exact()
                Dc = self.dZ_exact.tocoo()
                # one entry per (row, col): no add.at needed
                Z[Dc.row, Dc.col] += Dc.data.astype(dt)
                del Dc
            else:
                self.dZ_exact = None
                self._qcorr = self.core.near_into_dense(Z, self.g, self.tb,
                                                        self.q, self.nP32,
                                                        self.nQ32, self.w)
                self._near_kept()
        if plan.dense:  # (B, C into Z: the refinement's products are Z's)
            self._vias(f, Z)
        else:
            if not ready:
                self._vias(f)
            if Nv:
                _border(Z, self.B, self.C)
        self._ready = None
        if Sx.nnz:
            Sc = Sx.tocoo()
            np.add.at(Z, (Sc.row, Sc.col), Sc.data.astype(dt))
        up, down = self._updown()
        if T is not None:
            # the quad-cell / line-function subspace (quads.py): Z_q = T^T Z T,
            # E_q = T^T E, I = T I_q
            from .quads import project
            with sim.log.timer('quad projection',
                               f"{T.shape[0]} -> {T.shape[1]}"):
                if not hasattr(self, '_qC'):
                    self._qC = T[sim.quad_info['elim']].tocsr()
                Z = project(Z, sim.quad_info['keep'], sim.quad_info['elim'],
                            self._qC)
        W = self._side_loops() if plan.loops else None
        if W is not None:
            # the sides' verticals as loops (sides.Sides.loops) where the
            # complex64 LU preconditions a double Z (a double LU does not
            # need them)
            with sim.log.timer('side loops', f"{len(sim.sides.vert)} loops"):
                _loop_transform(Z, W)
            up0, down0 = up, down
            up = lambda X: up0(X + W @ X)
            down = lambda X: (lambda Y: Y + W.T @ Y)(down0(X))
        return Z, down(E), up, (
            lambda V: down(self.matvec_exact(up(V), Sx)))

    def _updown(self):
        """(up, down): currents from the quad cells' to the edges', and the
        excitation from the edges' to the cells' (T, T^T; or none)."""
        T = self.sim.qT
        return (((lambda X: X), (lambda X: X)) if T is None else
                ((lambda X: T @ X), (lambda X: np.asarray(T.T @ X))))

    def _factor(self, plan, Z, dsc):
        """plan's fresh complex64 LU of Z equilibrated by dsc (a complex64 Z
        is so already): on the GPU where it asks (the host's where that
        raises), downloaded for gpu_factor."""
        sim, n = self.sim, len(Z)
        c64 = lambda: Z if Z.dtype == np.complex64 else _c64(Z, dsc)
        if plan.on_gpu:
            gpu = self._backend()
            while (not gpu.fits(n)
                   and any(k.M.on_gpu for k in self.st.kept)):
                self.st.drop(gpu=True)  # room for the new one
                gpu.release()
            with sim.log.timer('LU factor (GPU)', f"N={n}, {gpu.name}"):
                try:
                    if gpu.zd is not None:  # (_put_dense's: Z there)
                        gpu.factor_dense(dsc)
                    else:
                        gpu.factor(c64())
                    M = GpuLU(gpu, n)
                    return M.to_host() if plan.gpu_factor else M
                except Exception as e:  # driver / unsupported dtype
                    sim.log.note(
                        f"GPU LU failed ({type(e).__name__}: {e}) - factoring "
                        "on the CPU")
                finally:
                    gpu.release()
        with sim.log.timer('LU factor', f"N={n}, complex64"):
            return HostLU(_lu(c64()))

    def _ladder(self, f, plan, box, A, B, dsc, t0):
        """A fresh complex64 LU of box's Z (equilibrated; a matrix-free
        plan's is freed after it), GMRES on A Y = B with it, down the rungs
        where that does not converge: the GPU's solves (they lose an
        ill-conditioned factor's accuracy, gpu.py) -> the factor solved on
        the host -> a complex128 LU of Z (None for a matrix-free plan: Z is
        gone). Each failure is the rung's up to RETRY x f. Y, equilibrated;
        the LU kept where it converged."""
        sim, st, timed = self.sim, self.st, True
        M = self._factor(plan, box[0], dsc)
        if not plan.dense:
            box.clear()  # (in the LU, or uploaded)
        while True:
            with sim.log.timer('refinement (GMRES, ' + (
                    'dense double)' if plan.dense else 'exact matvec)')):
                Y, ok, rel = self._refine(A, B, M, MIXED_MAX_ITER, 1,
                                          FRESH_STALL if M.on_gpu else None,
                                          timed)
            sim.log.note(f"mixed precision{' (GPU)' if M.on_gpu else ''}: "
                         f"GMRES iterations per port {self.iters}")
            if ok or not M.on_gpu:
                break
            sim.log.note(
                "the GPU's solves with its LU did not converge at "
                f"{f / 1e9:.4g} GHz (residual {rel:.1e}) - the factor solved "
                f"on the CPU up to {RETRY * f / 1e9:.4g} GHz")
            bad = RETRY * self._failed(plan.basis, 'vulkan-c64', f)
            st.kept = [k for k in st.kept if not k.M.on_gpu or k.f > bad]
            M, timed = M.to_host(), False
        if ok:
            keep = plan.keep if plan.dense else int(sim.reuse != 'off')
            # (factored on the GPU, solved here: not kept)
            if keep and (M.on_gpu or not plan.on_gpu):
                st.kept.append(Kept(f, M, dsc, time.time() - t0, plan.loops))
            while len(st.kept) > keep:
                st.drop()
            return Y
        # the single-precision LU can be too weak a preconditioner (fine meshes
        # at low frequency: the MPIE matrix is ill-conditioned); an unconverged
        # refinement returns garbage
        sim.log.note(
            f"WARNING mixed precision did not converge at {f / 1e9:.4g} GHz "
            f"(residual {rel:.1e}) - solving in double precision up to "
            f"{RETRY * f / 1e9:.4g} GHz")
        self._failed(plan.basis, 'cpu-c64', f)
        if not box:
            return None
        Z, M = box.pop(), None  # (M's memory: the double LU's)
        _equilibrate(Z, dsc)
        with sim.log.timer('LU factor', f"N={len(dsc)}"):
            return _lu_solve(_lu(Z), B)

    def _reused(self, f, k, A, B, M, setup=0.0):
        """GMRES on A Y = B with kept k's preconditioner M, capped at what a
        fresh solve costs (k.cost, less the seconds the setup took): Y, or
        None. The reach learnt from it."""
        st, sim = self.st, self.sim
        cap = int(np.clip(0.8 * (k.cost - setup)
                          / max(st.t_iter or 1e-3, 1e-6), *REUSE_ITER))
        with sim.log.timer(
                'refinement (GMRES, reused factorisation)',
                f"the one of {k.f / 1e9:.4g} GHz, at most {cap} iterations"):
            Y, ok, rel = self._refine(
                A, B, M, cap, 1,
                reach=(cap, REUSE_SLACK * 100 * self.mixed_tol))
        d = abs(np.log(f / k.f))
        if ok and max(self.iters) <= cap // 2:
            st.reach = max(st.reach, 1.25 * d)  # try further from f0
        elif not ok:
            st.reach = min(st.reach, 0.8 * d)  # not as far as this again
        if ok:
            sim.log.note(f"reused LU ({k.f / 1e9:.4g} GHz): GMRES iterations "
                         f"per port {self.iters}")
            return Y
        sim.log.note(
            f"the LU of {k.f / 1e9:.4g} GHz did not precondition "
            f"{f / 1e9:.4g} GHz in {max(self.iters)} of at most {cap} "
            f"iterations (residual {rel:.1e}) - a new one")
        return None

    def _reuse(self, f, E, Sx):
        """A solve without a fill: GMRES on the exact operator at f (sparse
        near field), preconditioned with the kept LU nearest in frequency
        (and its equilibration). Z changes smoothly with f, so the LU of one
        frequency preconditions the band: on the via transition (13.5k
        unknowns) the 5 GHz LU took 6-30 iterations from 1 to 10 GHz, 4-11 s a
        solve against 13 s with its own fill and LU. None where there is none
        near or it does not converge (the fresh solve then keeps this near
        field and these vias)."""
        sim, k = self.sim, self.st.nearest(f)
        if k is None:
            return None
        up, down = self._updown()
        t0 = time.time()
        if self._ready != f:
            with sim.log.timer('near-field correction'):
                self.dZ_exact = self._near_exact()
            self._vias(f, z=False)
        d = k.dsc[:, None]
        Y = self._reused(
            f, k, lambda V: d * down(self.matvec_exact(up(d * V), Sx)),
            d * down(E), k.M, time.time() - t0)
        if Y is not None:
            return up(d * Y)
        self.st.kept, self._ready = [], f
        return None

    def _lt_block(self):
        """Why the loop/tree basis cannot be used here, or ''."""
        sim = self.sim
        return ('quad cells' if sim.qT is not None else
                'holes of PEC planes (frills)' if sim.frills else
                'openings of PEC planes (slots)' if getattr(sim, 'slots', None)
                else 'the sides of thick copper' if getattr(
                    sim, 'sides', None) is not None else
                'the edges of thick copper' if getattr(
                    sim, 'edge', None) is not None else '')

    def _side_loops(self):
        """sides.Sides.loops' W of the simulation's sides, once; None
        without verticals."""
        if not hasattr(self, '_sloops'):
            sim = self.sim
            s = getattr(sim, 'sides', None)
            W = s.loops(sim) if s is not None and len(s.vert) else None
            if W is not None and sim.qT is not None:
                # among the quad cells' unknowns (a loop through a quad's
                # diagonal stays a vertical: quads 'all')
                keep = sim.quad_info['keep']
                gone = np.ones(sim.Ne + sim.Nv)
                gone[keep] = 0.0
                W = W.tocsc()
                W = W @ sparse.diags((W.T @ gone == 0).astype(float))
                W = W.tocsr()[keep][:, keep].tocsc()
                W.eliminate_zeros()
            self._sloops = W
        return self._sloops

    # ---- the loop/tree basis (looptree.py): low frequency -------------------
    def _lt_system(self, f, E, Sx):
        """The loop/tree basis's matrix (looptree.py): M = K_s^T Z_A K_s + the
        tree's charge block, Z_A filled with the tables' charge side zeroed,
        the charge block from the potentials between triangles; its
        right-hand side, and up: the basis's currents to the edges'."""
        sim, core, w = self.sim, self.core, self.w
        if self._lt is None:  # the basis's graph, once a mesh
            from .looptree import LoopTree
            with sim.log.timer('loop/tree basis'):
                lt = self._lt = LoopTree(sim)
            sim.log.note(
                f"loop/tree basis: {len(lt.cot)} loops, {len(lt.tree)} tree "
                f"branches (depth {lt.depth}, {len(lt.roots)} "
                f"root{'s' if len(lt.roots) != 1 else ''})")
        lt = self._lt
        Ne, Nv, N, Nt = sim.Ne, sim.Nv, sim.Ne + sim.Nv, len(sim.rwg.tris)
        tb = self.tb
        z = np.zeros_like
        # the current's part alone
        tbA = dict(tb, Q=z(tb['Q']), cq=z(tb['cq']), cqi=z(tb['cqi']))
        with sim.log.timer(
                'direct fill',
                f"N={N}, complex128, the current's part (loop/tree), "
                f"{core.num_threads()} threads"):
            Z = np.zeros((N, N), complex)
            core.fill_far(Z, self.g, tbA, w)
        with sim.log.timer('near-field correction'):
            core.near_into_dense(Z, self.g, tbA, self.q, self.nP32, self.nQ32,
                                 w)
            self._near_kept()
        if Nv:
            with sim.log.timer('via kernels',
                               f"{Nv} chains, without their end charges"):
                B, C = sim._via_blocks(f, None, charges=False)
            Z[:Ne, Ne:], Z[Ne:, :Ne], Z[Ne:, Ne:] = B, B.T, C
        if Sx.nnz:
            Sc = Sx.tocoo()
            np.add.at(Z, (Sc.row, Sc.col), Sc.data)
        with sim.log.timer('loop/tree transform',
                           f"{len(lt.cot)} loops, depth {lt.depth}"):
            lt.to_ideal(Z, lambda fn: _row_blocks(Z, fn))
            lt.cols(Z)                                      # Z K
            lt.rows(Z)                                      # K^T Z K
            sc = lt.scales(w)

            def scale(a, b):
                Z[a:b] *= sc[a:b, None]
                Z[a:b] *= sc[None, :]
            _row_blocks(Z, scale)
        with sim.log.timer('charge matrix', f"{Nt} triangles"):
            Mq = core.fan_q(self.g, tb, np.arange(Nt, dtype=np.int32))
            # (a pair once)
            Mq[self.nP, self.nQ] += core.near_q(self.g, tb, self.q, self.nP32,
                                                self.nQ32, w)
            lt.charge_into(Z, Mq, sim.rwg.area)
            del Mq
        return (Z, sc[:, None] * lt.rows(lt.rhs(E)),
                lambda Y: lt.from_ideal(lt.from_tree(sc[:, None] * Y)), None)

    def kh2(self, f):
        """(k h)^2 at f: k in the densest dielectric, h the mesh's median edge -
        about the ratio of a cell's jw part of the MPIE to its 1/(jw) part."""
        from kicad2ems.emsdev.numerics.base import C0
        k = 2 * np.pi * f / C0 * np.sqrt(self.sim.st.max_er())
        return float((k * np.median(self.sim.rwg.l)) ** 2)

    def _backend(self):
        """The GPU backend sim.gpu asks for, when it is available (None for
        'off')."""
        if self.sim.gpu == 'off':
            return None
        from .gpu import get_backend
        return get_backend(self.sim.gpu)

    def _host_matrix(self, N):
        """An N x N complex128 Z in the GPU's mapped host memory (the
        backend's host_buffer), kept for the next solve's."""
        zh = getattr(self, '_zh', None)
        if zh is None or zh.nbytes < 16 * N * N:
            self._zh = None  # (the old one freed first)
            try:
                self._zh = zh = self._backend().host_buffer(16 * N * N)
            except MemoryError:  # (no mapped memory that large)
                return np.zeros((N, N), complex)
        return zh.view(np.complex128, N * N).reshape(N, N)

    def release_host(self):
        """Free _host_matrix's mapped memory (16 N^2 bytes) after a sweep."""
        self._zh = None

    def _points(self):
        """Every unknown's place: the edges' midpoints, the via chains' axes,
        the PEC planes' holes'."""
        sim = self.sim
        border = ([[v.x, v.y] for v, _, _ in sim.chains] + [
            list(fr.xy)
            for fr in getattr(sim, 'frills', ())
            for _ in range(fr.count)
        ] + [list(m) for sl in getattr(sim, 'slots', ()) for m in sl.rwg.mid])
        return np.vstack([sim.rwg.mid] + ([np.array(border)] if border else []))

    def _raw_entry(self, dZx):
        """entry(rows, cols): the dense block of Z (far field from the core, the
        sparse near correction and extra blocks dZx, the via rows and columns),
        any unknowns, original numbering."""
        Ne, Nv = self.sim.Ne, self.sim.Nv

        def raw(rows, cols):
            M = np.zeros((len(rows), len(cols)), complex)
            re, ce = rows < Ne, cols < Ne
            if re.any() and ce.any():
                r_, c_ = rows[re], cols[ce]
                M[np.ix_(
                    re, ce)] = (self.core.far_block(r_.astype(
                        np.int32), c_.astype(np.int32), self.g, self.tb, self.w)
                                + dZx[r_][:, c_].toarray())
            if Nv:  # (empty selections assign nothing)
                M[np.ix_(re, ~ce)] = self.B[rows[re]][:, cols[~ce] - Ne]
                M[np.ix_(~re, ce)] = self.B[cols[ce]][:, rows[~re] - Ne].T
                M[np.ix_(~re, ~ce)] = (self.C[np.ix_(rows[~re] - Ne,
                                                     cols[~ce] - Ne)])
            return M
        return raw

    def _solve_blr(self, f, E):
        """Block low-rank LU preconditioner + GMRES with the exact matrix-free
        operator (no stored Z)."""
        sim, st = self.sim, self.st
        Ne, N = sim.Ne, sim.Ne + sim.Nv
        tol, tile, eta = sim.blr_tol, sim.blr_tile, sim.blr_eta
        self.setup(f, mode='sparse')
        self.dZ_exact, self._ready, self.iters = self.dZ, f, []
        Sx = self.extra_blocks(f)
        X = self._reuse(f, E, Sx)
        self._ready = None
        if X is not None:
            return X
        st.kept, t_fresh = [], time.time()
        dZx = (self.dZ + Sx[:Ne, :Ne]).tocsr()
        tiles, boxes = cluster_tiles(self._points(), tile)
        raw = self._raw_entry(dZx)
        # low frequency, and no room for the loop/tree basis's dense matrix: the
        # factor in double precision, its compression a hundredth of (k h)^2
        # (the loops' share of the matrix: what a looser one truncates away)
        low = (getattr(sim, 'loop_tree', 'auto') != 'off'
               and self.kh2(f) < LOOP_TREE_BELOW)
        if low:
            tol = min(tol, 0.01 * self.kh2(f))
            self._once('blr-low', "block low-rank solver: below (k h)^2 = "
                       f"{LOOP_TREE_BELOW:g} ({f / 1e9:.4g} GHz here) its "
                       f"factor in double precision, compressed to {tol:.1e} "
                       "(the loop/tree basis's dense matrix does not fit)")
        diag = np.empty(N, complex)
        for t in tiles:
            diag[t] = np.diagonal(raw(t, t))
        dsc = 1.0 / np.sqrt(np.abs(diag) + 1e-300)
        # tighter after a factor that did not precondition
        tol = min(tol, getattr(self, '_blr_tol', tol))
        while True:
            with sim.log.timer(
                    'BLR assemble + factor',
                    f"N={N}, {len(tiles)} tiles, tolerance {tol:g}, compiled"):
                self.blr = blr = _CompiledBLR(self, tiles, boxes, dZx, dsc, tol,
                                              eta, double=low)
            sim.log.note(
                f"BLR: {blr.memory / 2 ** 20:.0f} MB "
                f"({100 * blr.memory / (8 * N * N):.0f}% of complex64 dense, "
                f"{100 * blr.memory / (16 * N * N):.0f}% of complex128), "
                f"{blr.stats['lowrank']} low-rank tiles, mean rank "
                f"{blr.stats['rank_sum'] / max(1, blr.stats['lowrank']):.0f}"
                +
                f"; assembled {blr.stats['seconds_assemble']:.1f} s, factored "
                f"{blr.stats['seconds_factor']:.1f} s")
            if blr.stats['singular']:
                sim.log.note(
                    f"WARNING BLR: {blr.stats['singular']} diagonal tiles "
                    "singular - GMRES may not converge"
                )
            with sim.log.timer('GMRES (BLR preconditioned)'):
                # the ports' columns together (gmres_batched): one pass of the
                # far kernel a product for all of them (rf_siw, 28.7k unknowns,
                # 2 ports: 11.6 s column by column, 5.0 s together)
                Y, ok, rel = self._refine(
                    lambda V: dsc[:, None] * self.matvec_exact(
                        dsc[:, None] * V, Sx), dsc[:, None] * E, blr, 50,
                    10, BLR_STALL)
            sim.log.note(f"BLR: GMRES iterations per port {list(self.iters)}")
            if ok or tol <= (1e-4 * self.kh2(f) if low else BLR_TOL_MIN):
                break
            # too loose a compression for this matrix (a fine mesh, a resonance:
            # antenna_cp_patch, 40.8k unknowns, at 1e-3: residual 0.6 after 500
            # iterations; at 1e-5: 7 iterations): a hundredth of it, here and
            # for the frequencies after
            sim.log.note(
                f"the BLR factor at tolerance {tol:g} left GMRES at residual "
                f"{rel:.1e} after {max(self.iters, default=0)} iterations at "
                f"{f / 1e9:.4g} GHz - factored again at {tol / 100:g}"
            )
            tol = self._blr_tol = tol / 100
        if not ok and not rel <= BLR_GIVE_UP:
            # no answer: an unconverged GMRES's currents are not S-parameters to
            # write (antenna_vivaldi at 50 MHz through a complex64 factor: S11
            # 1.000 where it is 0.978 - 0.193j)
            raise FloatingPointError(
                f"mom25d: the block low-rank solve at {f / 1e9:.4g} GHz did "
                f"not converge (residual {rel:.1e} with a factor compressed to "
                f"{tol:.1e}, N = {N}): a coarser mesh (--mom-dmax), or a "
                "machine with room for the direct solver"
            )
        if not ok:
            sim.log.note(
                f"WARNING BLR: GMRES stopped at a residual of {rel:.1e} at "
                f"{f / 1e9:.4g} GHz"
            )
        elif sim.reuse != 'off':
            st.kept = [Kept(f, blr, dsc, time.time() - t_fresh, False)]
        return dsc[:, None] * Y

class _CompiledBLR:
    """The compiled core's BLRFactor (core/mom25d_core.cpp): solve, memory,
    stats; the tiles in elimination order, the pairs compressed where their
    clusters are apart (gap > eta x the larger's diameter), the near correction
    and extra blocks as CSR, the via rows and columns, the equilibration. Its
    BLAS calls run on OpenMP's threads, so SciPy's are held at one while it
    factors and solves."""

    on_gpu, device_bytes = False, 0

    def __init__(self, fo, tiles, boxes, dZx, dsc, tol, eta, double=False):
        lo, hi = (np.array([b[0]
                            for b in boxes]), np.array([b[1] for b in boxes]))
        gap = np.linalg.norm(
            np.maximum(
                0, np.maximum(lo[:, None] - hi[None], lo[None] - hi[:, None])),
            axis=2)
        diam = np.linalg.norm(hi - lo, axis=1)
        far = ((gap > eta * np.maximum(diam[:, None], diam[None]))
               & ~np.eye(len(tiles), dtype=bool))
        order = np.concatenate(tiles).astype(np.int32)
        start = np.r_[0, np.cumsum([len(t) for t in tiles])].astype(np.int32)
        sim, c = fo.sim, np.ascontiguousarray
        B = c(fo.B, complex) if sim.Nv else np.zeros((sim.Ne, 0), complex)
        C = c(fo.C, complex) if sim.Nv else np.zeros((0, 0), complex)
        with _one_blas_thread():
            # (double: low frequency, solve_blr)
            make = fo.core.BLRFactor128 if double else fo.core.BLRFactor
            self.f = make(order, start, far.astype(np.uint8), fo.g, fo.tb, fo.w,
                          dZx.indptr.astype(np.int64),
                          dZx.indices.astype(np.int32), c(dZx.data, complex), B,
                          C, c(dsc, float), float(tol), 0)
        self.stats = self.f.stats()
        self.memory = self.nbytes = self.stats['bytes']

    def __call__(self, b):
        with _one_blas_thread():
            return self.f.solve(np.ascontiguousarray(b, complex))


def _one_blas_thread():
    """threadpoolctl's limit of one BLAS thread (a no-op context without it)."""
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        from contextlib import nullcontext
        return nullcontext()
    return threadpool_limits(limits=1, user_api='blas')


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
