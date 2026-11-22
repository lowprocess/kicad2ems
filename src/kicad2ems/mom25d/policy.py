"""
mom25d.policy - how a frequency is solved, as data: choose() takes the sizes,
the memory, the GPU and what failed before, and returns a SolvePlan; fast.py
carries it out. memory_plan is the same rules for a whole run (the solver, the
precision and the memory the log and the estimates give).

The rungs, each a factorisation behind one interface (fast.py):
  vulkan-c64  complex64 LU on the GPU, its triangular solves there
  cpu-c64     complex64 LU on the host (or the GPU's, downloaded)
  cpu-c128    complex128 LU on the host: no refinement
  blr         the compiled block low-rank factor (no dense Z)
A complex64 LU preconditions GMRES on the double operator: the dense double Z
(dense) or the matrix-free one. Where a rung fails at f, the one below it
solves, and so does every frequency up to RETRY x f.
"""
import os
from dataclasses import dataclass

import numpy as np

# a rung that failed at f is passed over up to this x f: it fails at the low
# end of a band, where the MPIE matrix is worst conditioned (a sweep that gave
# up on the GPU for good after its first 1 GHz solve ran the via transition,
# 38 solves, on the CPU: 621 s, against 614 s without a GPU)
RETRY = 1.25
# complex64 LUs kept at most, memory allowing
REUSE_KEEP = 4
# loop_tree 'auto': the loop/tree basis where (k h)^2 is below this (k in
# the densest dielectric, h the median edge), about the ratio of a cell's two
# MPIE parts. The RWG basis's complex64 LU stops preconditioning below ~2e-6
# (the via transition: ~100 MHz), its double LU loses S11's digits below ~1e-13
# (~10 kHz: 3 %, 1 kHz: its sign). The via transition from 100 kHz to 100 MHz:
# 37 s in the RWG basis (6 of 7 solves falling back to double), 7 s
# (1e-3 since 2026-11-22: 1e-5 had left the RWG basis's ill-conditioned band,
# ~1-2 GHz on fine meshes, to the RWG basis: the Thor carrier's In7 group, 24.9k
# unknowns, 106 ports, 208 / 238 s at 1 / 2 GHz in it against 16 / 15 s in the
# loop/tree basis, which was no slower at 5 / 10 GHz (16 / 13.5 against 20 /
# 13.6 s), S within 1.4e-5; it is taken only where its dense matrices fit)
LOOP_TREE_BELOW = float(os.environ.get('MOM25D_LOOP_TREE_BELOW', 1e-3))
# above this, 'auto' precision is mixed
MIXED_MIN_N = 3500
# ... and above this many in the LU (after quads) with a GPU: 1.5-1.9x a solve
# from 1.4k to 3.4k unknowns against double on 8 P-cores (RX 7800 XT), but its
# start-up costs ~1.5 s a run - the break-even of a 15-20 solve sweep (rf_gcpw,
# 3.4k: 7.0 s against 8.7 s; rf_stubnotch coarsened, 1.5k: 4.5 against 3.4)
GPU_MIN_N = int(os.environ.get('MOM25D_GPU_MIN_N', 3000))
# the hybrid (the double Z on the host for the refinement's products, several
# complex64 LUs kept on the GPU) up to this many unknowns; past it the GPU LU
# with the complex64 Z (the refinement matrix-free, one LU kept): the double
# Z's memory traffic (24 N^2 bytes, 20 GB at 28.7k) outgrew what its products
# save. RX 7800 XT, i7-13700K: rf_siw (28.7k) 456 s hybrid against 296 s,
# antenna_slot (28.6k) 165 s against 139 s; antenna_vivaldi (21.9k) 148 s
# hybrid against 171 s
HYBRID_MAX_N = int(os.environ.get('MOM25D_HYBRID_MAX_N', 25000))
# ... and by its half (HALF; 12 N^2 bytes on the host, the GPU's copy 8 N^2):
# up to this many, past it as before. A LimeRFE piece (27.1k unknowns, 17
# ports, 6 GHz, 2026-11-22): 5.9 s hybrid by the half against 17.5 s
# matrix-free, S within 8.7e-8; the memory checks bound it below this
HALF_HYBRID_MAX_N = int(os.environ.get('MOM25D_HALF_HYBRID_MAX_N', 45000))
# from this many unknowns the compiled block low-rank LU where the GPU's hybrid
# does not hold the matrix: a sweep of rf_siw (28.7k) 272 s against 520 s
# tiled, antenna_cp_patch's (40.8k) solve 22.5 s against 30.3 s on the GPU LU,
# antenna_vivaldi (21.9k) without a GPU 202 s against 243 s; in a fifth of the
# memory or less (1.2 GB, 1.2 GB, 2.4 GB)
BLR_MIN_N = int(os.environ.get('MOM25D_BLR_MIN_N', 20000))
# ... and from this many where the CPU factors (no GPU, or none that fits):
# rf_ebg's mesh at 1e9-8e9 Hz, three solves, 8 P-cores (2026-11-20): 10.5k
# dense complex64 16.6 s against block low-rank 19.5 s, 11.4k 19.4 / 21.4,
# 13.3k 26.5 / 25.1, 14.7k 32.9 / 26.7 (S within 4e-7 of each other); a
# sweep of 8 solves at 15.0k 82 s / 56 s and a peak of 15.8 GB / 8.7 GB
BLR_CPU_MIN_N = int(os.environ.get('MOM25D_BLR_CPU_MIN_N', 13000))
# the hybrid's double Z on the GPU too, its products there in double
# precision (the refinement's: a GMRES iteration's cost), where that leaves
# room for two LUs: MOM25D_GPU_DENSE=off keeps them on the host
GPU_DENSE = os.environ.get('MOM25D_GPU_DENSE', 'on') != 'off'
# the GPU's factor complex-symmetric where it can be (gpu_ldl.py: LDL^T without
# pivoting, half an LU's work and memory; the LU where it fails or does not
# precondition, up to RETRY x f): MOM25D_LDL=off for the LU always. RX 7800 XT
# (2026-11-22, the factor alone): 7k 0.108 s against the LU's 0.175, 14k
# 0.485 / 0.883, 20k 1.23 / 2.29
LDL = os.environ.get('MOM25D_LDL', 'on') != 'off'
# the dense double Z kept by its lower half (half.py: 8 N^2 bytes, not 16, and
# half the traffic of its products) where the factor is the LDL^T and nothing
# needs whole rows (the RWG basis without the sides' loops or quad cells; the
# loop/tree basis's transform does): MOM25D_HALF=off keeps it whole
HALF = os.environ.get('MOM25D_HALF', 'on') != 'off'
# the refinement's products with the double Z on the GPU split with the host
# (which holds Z too) from this many columns: with many the products are bound
# by double arithmetic, an RX 7800 XT's about the same as 8 P-cores' (the
# double half at 20k, 106 columns: GPU 0.76 s, host 0.80, split 0.46);
# MOM25D_SPLIT_MIN=0 never splits
SPLIT_MIN = int(os.environ.get('MOM25D_SPLIT_MIN', 16))
# host bytes besides the solver's matrices
SPARE = 0.9e9
PATHS = {('cpu-c128', True): 'double', ('vulkan-c64', True): 'hybrid',
         ('vulkan-c64', False): 'gpu-lu', ('cpu-c64', True): 'cpu-mixed',
         ('cpu-c64', False): 'cpu-mixed', ('blr', False): 'blr'}


@dataclass(frozen=True)
class SolvePlan:
    """basis: 'rwg' or 'loop/tree'. rung: the factorisation (the module's
    docstring). dense: Z in double, its products the refinement's (else Z in
    complex64, the refinement matrix-free). gpu_factor: factored on the GPU,
    solved on the host (where the GPU's solves failed). keep: LUs kept after
    the solve (a matrix-free one keeps its own alone). loops: the sides'
    verticals as loops (T^T Z T). why: why not the loop/tree basis.
    gpu_dense: the dense Z on the GPU too, the products there. half: the
    dense Z by its lower half (half.Half), the factor an LDL^T of it."""
    basis: str = 'rwg'
    rung: str = 'cpu-c128'
    dense: bool = True
    gpu_factor: bool = False
    keep: int = 0
    loops: bool = False
    why: str = ''
    gpu_dense: bool = False
    half: bool = False

    @property
    def path(self):
        return PATHS[self.rung, self.dense]

    @property
    def on_gpu(self):
        """Whether the GPU factors."""
        return self.rung == 'vulkan-c64' or self.gpu_factor


def gpu_fits(need, free):
    """Whether an LU of `need` bytes fits a GPU with `free`: at least 0.5 GB
    (3 %) to spare: 90 % of the free turned down antenna_cp_patch's 40.8k
    unknowns (14.15 GB of 15.7 GB), which factored there in 19 s with 1.95 GB
    left, against minutes on the CPU."""
    return need <= free - max(0.5e9, 0.03 * free)


def dense_bytes(n, nq):
    """The hybrid's host bytes: the double Z and its complex64 copy; with quads
    (nq < n) Z and the reduced matrix, then the copy."""
    return (max(16.0 * (n * n + nq * nq), 24.0 * nq * nq) if nq < n
            else 24.0 * n * n)


def choose(f, n, nq=None, nt=0, avail=16e9, gpu=None, precision='mixed',
           solver='direct', reuse=True, history=None, held=None, kh2=1.0,
           loop_tree='auto', lt_block='', sides=False, gpu_dense=False,
           half=True):
    """The SolvePlan at f. n: unknowns, nq: in the LU (fewer with quads), nt:
    triangles. avail: host bytes free. gpu: None, or (free bytes, need:
    unknowns -> a factor's bytes, optionally a kept factor's bytes: device()).
    precision: the run's ('double', 'mixed').
    history: {(basis, rung): the highest f it failed at} (rung 'ldl': the
    GPU's LDL^T, fast.py's). held: {basis:
    (host, GPU) bytes of the LUs kept, theirs to give back}. kh2: (k h)^2 at
    f. loop_tree: 'auto', 'on', 'off'. lt_block: why the loop/tree basis
    cannot be used (quads, holes). sides: the sides of thick copper have
    verticals. gpu_dense: the GPU can hold the double Z (GPU_DENSE). half:
    the half may be taken (False: the operator gave it up, fast.py)."""
    nq, history = nq or n, history or {}
    basis, why = 'rwg', ''
    # by its half (HALF, the LDL^T's): filled straight into it, or (the
    # loop/tree basis, the sides' loops: their transforms need the whole)
    # whole first, its half kept after them (16 n^2 at the peak)
    half = half and HALF and LDL and nq == n
    if loop_tree == 'on' or (loop_tree == 'auto' and kh2 < LOOP_TREE_BELOW):
        # (its charge matrix by blocks; the factor's copy the LU's whole or
        # the LDL^T's half)
        need = (16.0 if half else 24.0) * n * n + SPARE
        why = lt_block or (f"its matrices need ~{need / 1e9:.1f} GB"
                           if need > 0.8 * avail else '')
        basis = 'rwg' if why else 'loop/tree'
    lt = basis == 'loop/tree'
    near = lambda rung: f <= RETRY * history.get((basis, rung), 0.0)
    h_host, h_gpu = (held or {}).get(basis, (0.0, 0.0))
    avail += h_host
    gpu = gpu and (gpu[0] + h_gpu,) + tuple(gpu[1:])
    if solver == 'blr' and not lt:
        return SolvePlan(basis, 'blr', dense=False, keep=int(reuse), why=why)
    m = n if lt else nq
    fits = gpu is not None and gpu_fits(gpu[1](m), gpu[0])
    if precision == 'double' or near('cpu-c64'):
        loops = sides and not lt and precision == 'mixed'
        return SolvePlan(basis, 'cpu-c128', loops=loops, why=why)
    # the half: the double Z's 8 n^2 and the factor's complex64 4 n^2; 16
    # n^2 while a transform has it whole
    host = ((16.0 if lt or sides else 12.0) * n * n if half
            else 24.0 * n * n if lt else dense_bytes(n, nq))
    hybrid = fits and (lt or (host + SPARE <= 0.8 * avail and nq <= (
        HALF_HYBRID_MAX_N if half else HYBRID_MAX_N)))
    zd = ((8.0 if half else 16.0) * m * m
          if hybrid and gpu_dense and GPU_DENSE else 0.0)
    if hybrid:
        # (a kept factor's bytes: gpu[2], the backend's; an LU's 8 m^2)
        per = gpu[2](m) if len(gpu) > 2 else 8.0 * m * m
        room = (0.9 * gpu[0] - 0.4e9 - zd) // per
        if room < 2 and zd:  # the LUs kept rather than Z
            zd, room = 0.0, (0.9 * gpu[0] - 0.4e9) // per
    elif not fits:   # (a host factor: the LDL^T's 4 m^2 with the half)
        room = (0.8 * avail - host - SPARE) // ((4.0 if half else 8.0) * m * m)
    else:  # the GPU's LU, the refinement matrix-free
        room = 0
    keep = int(min(REUSE_KEEP, max(room, 0))) if reuse else 0
    dense = lt or hybrid or keep >= 1
    rung = 'vulkan-c64' if fits and (hybrid or not dense) else 'cpu-c64'
    gf = rung == 'vulkan-c64' and near('vulkan-c64')
    return SolvePlan(basis, 'cpu-c64' if gf else rung, dense, gf, keep,
                     sides and not lt and dense, why, gpu_dense=bool(zd),
                     half=half and dense)


def memory_plan(n, avail=None, gpu=None, precision='auto', solver='auto',
                n_lu=None, nv=0, gpu_dense=True, interp=True, pairs=0,
                sides=False):
    """How the solver of n unknowns runs and the memory it needs: dict(solver,
    precision, path, host, gpu, fits) (bytes). gpu: None or (free bytes, need:
    unknowns -> an LU's bytes), the backend's own estimate (device()). path:
    'double' (LU on the CPU), 'hybrid' (complex64 LU on the GPU, the double Z
    on the host for the refinement), 'gpu-lu' (complex64 Z on the host and the
    GPU), 'cpu-mixed' (complex64 LU on the CPU), 'blr' (compressed, no dense
    Z). n_lu: the unknowns the LU factors (fewer with quads). 'auto' takes the
    block low-rank LU from BLR_MIN_N unknowns where the GPU factors without
    the hybrid, from BLR_CPU_MIN_N where the CPU factors, and wherever the
    direct solver does not fit. The GPU's bytes include the
    double Z where choose puts it there too (gpu_dense: the backend's double
    kernels; GPU_DENSE), the host's the via blocks kept for the sweep's
    interpolation (nv via unknowns, interp: on; blocks.Anchors: up to a double
    Z, or ANCHOR_ROOM) and the near field's (pairs: near triangle pairs;
    near_bytes). sides: the sides of thick copper have verticals (their
    loops keep Z whole: no half)."""
    if avail is None:
        from kicad2ems.emsdev.memory import available
        avail = available()
    m = n_lu or n
    fits_gpu = gpu is not None and gpu_fits(gpu[1](m), gpu[0])
    if precision == 'auto':
        # mixed where double does not fit, and above MIXED_MIN_N (GPU_MIN_N
        # with a GPU): complex64 LU + GMRES refinement, half the LU flops and
        # 10 N^2 bytes instead of 18 (demo transition cropped, 8.4k unknowns,
        # 2 cores: 14.0 s a solve against 18.0 s, |dS| <= 7e-6 over 1-8 GHz)
        precision = ('mixed' if m > (GPU_MIN_N if fits_gpu else MIXED_MIN_N)
                     or 18.0 * n * n + SPARE > 0.5 * avail else 'double')
    plan = choose(np.inf, n, m, avail=avail, gpu=gpu, precision=precision,
                  loop_tree='off', gpu_dense=gpu_dense, sides=sides)
    p = plan.path
    host = ((max(12.0 + 4.0 * (plan.keep if p == 'cpu-mixed' else 0),
                 16.0 if sides else 0.0)) * n * n
            if plan.half else
            {'double': 18.0, 'hybrid': 24.0}.get(p, 10.0) * n * n) + SPARE
    host += anchor_bytes(n, nv) if interp else 0.0
    dev = gpu[1](m) if p in ('hybrid', 'gpu-lu') else 0.0
    dev += (8.0 if plan.half else 16.0) * m * m if plan.gpu_dense else 0.0
    fits = host <= 0.8 * avail
    if solver == 'auto':
        # (quad cells: the direct solver's alone)
        big = BLR_MIN_N if p == 'gpu-lu' else BLR_CPU_MIN_N
        solver = ('blr' if not fits or (p in ('gpu-lu', 'cpu-mixed')
                                        and n >= big and m >= n)
                  else 'direct')
    if solver == 'blr':
        # the compiled factor: measured 18-30 % of a complex64 matrix at 22-41k
        # unknowns, with the sparse near field: antenna_cp_patch's run peaked at
        # 5 GB
        p, host, dev = 'blr', 3.0 * n * n + 2e4 * n + SPARE, 0.0
        host += anchor_bytes(n, nv) if interp else 0.0
        # the via and side blocks it fills whole (B: edges x via and side
        # unknowns, C: theirs; complex128): a dense Z holds them in place,
        # this does not (a real board's layer, 37k edges and 10k sides of
        # thick copper: 7.6 GB, uncounted, and the run was killed)
        host += via_block_bytes(n, nv)
        fits = host <= 0.85 * avail
    host += near_bytes(pairs, sparse=p == 'blr')
    if p == 'blr':
        fits = host <= 0.85 * avail
    return dict(solver=solver, precision=precision, path=p, host=host,
                gpu=dev, fits=fits)


def via_block_bytes(n, nv):
    """The block low-rank solve's via and side blocks, filled whole: B ((n -
    nv) x nv) and C (nv x nv), complex128."""
    nv = max(0, int(nv))
    return 16.0 * (n - nv) * nv + 16.0 * nv * nv


# a near triangle pair's bytes held across the sweep: its analytic parts kept
# (12 float64), its charge correction (complex128) and its indices (int64 and
# int32, twice); rf_ebg, 9.3 M pairs: 1.3 GB
NEAR_HELD = 12 * 8 + 16 + 2 * (8 + 4)


def near_bytes(pairs, sparse=False):
    """The near field's host bytes for `pairs` near triangle pairs: those held,
    and, for a solver that keeps the correction sparse (sparse: the block
    low-rank LU, a matrix-free refinement), its peak while that is built
    (fast.FastOperator.NEAR_BYTES: what its piece-at-a-time build keeps, and
    one piece's peak, near.near_sparse). A dense Z takes the correction in
    place."""
    if pairs <= 0:
        return 0.0
    from .fast import FastOperator
    from .near import NEAR_PIECE
    return pairs * NEAR_HELD + (
        pairs * FastOperator.NEAR_BYTES
        + min(FastOperator.NEAR_PIECE_BYTES, NEAR_PIECE * pairs)
        if sparse else 0.0)


def anchor_bytes(n, nv):
    """The host's bytes of the via blocks the sweep keeps to interpolate them
    (blocks.Anchors: KEEP anchors, each the via columns' blocks less the
    first's, complex64: n x nv of them and two nv x nv), capped as fast.py
    caps them: a double Z, or ANCHOR_ROOM where that is more."""
    if nv <= 0:
        return 0.0
    from .blocks import KEEP
    from .fast import ANCHOR_ROOM
    return min(KEEP * 8.0 * (n * nv + 2.0 * nv * nv),
               max(16.0 * n * n, ANCHOR_ROOM))


def device(b, ldl=None):
    """choose's and memory_plan's gpu for backend b (None: None): (free
    bytes, a factor's need, a kept factor's bytes), the LDL^T's where it is
    taken (LDL, and the backend has it)."""
    if b is None:
        return None
    if ldl is None:
        ldl = LDL
    if ldl and hasattr(b, 'vram_needed_ldl'):
        from .half import Half
        nb = getattr(b, 'nb', 256)
        return (b.mem_free(), b.vram_needed_ldl,
                lambda m: Half.bytes(m, nb) + 8.0 * m * nb + 8.0 * m)
    return (b.mem_free(), b.vram_needed, lambda m: 8.0 * m * m)
