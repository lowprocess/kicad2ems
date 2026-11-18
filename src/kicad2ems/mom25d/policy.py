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
LOOP_TREE_BELOW = 1e-5
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
# from this many unknowns the compiled block low-rank LU where the GPU's hybrid
# does not hold the matrix: a sweep of rf_siw (28.7k) 272 s against 520 s
# tiled, antenna_cp_patch's (40.8k) solve 22.5 s against 30.3 s on the GPU LU,
# antenna_vivaldi (21.9k) without a GPU 202 s against 243 s; in a fifth of the
# memory or less (1.2 GB, 1.2 GB, 2.4 GB)
BLR_MIN_N = int(os.environ.get('MOM25D_BLR_MIN_N', 20000))
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
    verticals as loops (T^T Z T). why: why not the loop/tree basis."""
    basis: str = 'rwg'
    rung: str = 'cpu-c128'
    dense: bool = True
    gpu_factor: bool = False
    keep: int = 0
    loops: bool = False
    why: str = ''

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
           loop_tree='auto', lt_block='', sides=False):
    """The SolvePlan at f. n: unknowns, nq: in the LU (fewer with quads), nt:
    triangles. avail: host bytes free. gpu: None, or (free bytes, need:
    unknowns -> an LU's bytes). precision: the run's ('double', 'mixed').
    history: {(basis, rung): the highest f it failed at}. held: {basis:
    (host, GPU) bytes of the LUs kept, theirs to give back}. kh2: (k h)^2 at
    f. loop_tree: 'auto', 'on', 'off'. lt_block: why the loop/tree basis
    cannot be used (quads, holes). sides: the sides of thick copper have
    verticals."""
    nq, history = nq or n, history or {}
    basis, why = 'rwg', ''
    if loop_tree == 'on' or (loop_tree == 'auto' and kh2 < LOOP_TREE_BELOW):
        need = max(16.0 * (n * n + nt * nt), 24.0 * n * n) + SPARE
        why = lt_block or (f"its matrices need ~{need / 1e9:.1f} GB"
                           if need > 0.8 * avail else '')
        basis = 'rwg' if why else 'loop/tree'
    lt = basis == 'loop/tree'
    near = lambda rung: f <= RETRY * history.get((basis, rung), 0.0)
    h_host, h_gpu = (held or {}).get(basis, (0.0, 0.0))
    avail += h_host
    gpu = gpu and (gpu[0] + h_gpu, gpu[1])
    if solver == 'blr' and not lt:
        return SolvePlan(basis, 'blr', dense=False, keep=int(reuse), why=why)
    m = n if lt else nq
    fits = gpu is not None and gpu_fits(gpu[1](m), gpu[0])
    if precision == 'double' or near('cpu-c64'):
        loops = sides and not lt and precision == 'mixed'
        return SolvePlan(basis, 'cpu-c128', loops=loops, why=why)
    host = 24.0 * n * n if lt else dense_bytes(n, nq)
    hybrid = fits and (lt or (host + SPARE <= 0.8 * avail
                              and nq <= HYBRID_MAX_N))
    if hybrid:
        room = (0.9 * gpu[0] - 0.4e9) // (8.0 * m * m)
    elif not fits:
        room = (0.8 * avail - host - SPARE) // (8.0 * m * m)
    else:  # the GPU's LU, the refinement matrix-free
        room = 0
    keep = int(min(REUSE_KEEP, max(room, 0))) if reuse else 0
    dense = lt or hybrid or keep >= 1
    rung = 'vulkan-c64' if fits and (hybrid or not dense) else 'cpu-c64'
    gf = rung == 'vulkan-c64' and near('vulkan-c64')
    return SolvePlan(basis, 'cpu-c64' if gf else rung, dense, gf, keep,
                     sides and not lt and dense, why)


def memory_plan(n, avail=None, gpu=None, precision='auto', solver='auto',
                n_lu=None):
    """How the solver of n unknowns runs and the memory it needs: dict(solver,
    precision, path, host, gpu, fits) (bytes). gpu: None or (free bytes, need:
    unknowns -> an LU's bytes), the backend's own estimate (device()). path:
    'double' (LU on the CPU), 'hybrid' (complex64 LU on the GPU, the double Z
    on the host for the refinement), 'gpu-lu' (complex64 Z on the host and the
    GPU), 'cpu-mixed' (complex64 LU on the CPU), 'blr' (compressed, no dense
    Z). n_lu: the unknowns the LU factors (fewer with quads). 'auto' takes the
    block low-rank LU from BLR_MIN_N unknowns where the hybrid does not fit,
    and wherever the direct solver does not."""
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
    p = choose(np.inf, n, m, avail=avail, gpu=gpu, precision=precision,
               loop_tree='off').path
    host = {'double': 18.0, 'hybrid': 24.0}.get(p, 10.0) * n * n + SPARE
    dev = gpu[1](m) if p in ('hybrid', 'gpu-lu') else 0.0
    fits = host <= 0.8 * avail
    if solver == 'auto':
        # (quad cells: the direct solver's alone)
        solver = ('blr' if not fits or (p in ('gpu-lu', 'cpu-mixed')
                                        and n >= BLR_MIN_N and m >= n)
                  else 'direct')
    if solver == 'blr':
        # the compiled factor: measured 18-30 % of a complex64 matrix at 22-41k
        # unknowns, with the sparse near field: antenna_cp_patch's run peaked at
        # 5 GB
        p, host, dev = 'blr', 3.0 * n * n + 2e4 * n + SPARE, 0.0
    return dict(solver=solver, precision=precision, path=p, host=host,
                gpu=dev, fits=fits)


def device(b):
    """choose's and memory_plan's gpu for backend b (None: None)."""
    return None if b is None else (b.mem_free(), b.vram_needed)
