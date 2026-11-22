---
name: mom25d-performance
description: Measure, profile and change mom25d's speed and memory — stage timings per frequency, the Vulkan GPU LU benchmark, solver-path thresholds (dense / mixed / GPU hybrid / block low-rank), the memory plan, and accuracy checks that must accompany any speed change. Use before and while optimising mom25d, when a run is slow or runs out of memory, or when choosing policy thresholds.
---

# mom25d performance work

mom25d is in `src/kicad2ems/mom25d/`: `sim.py` (the simulation), `fast.py` (`FastOperator`: fill and solve
plans), `dense.py` (LU, GMRES), `near.py` (near-field pairs), `tiles.py` (block low-rank), `policy.py`
(which path, memory plan), `gpu_vk.py` (Vulkan LU), the C++ core `core/mom25d_core.cpp`.
`ROADMAP.md` → *mom25d: speed* records what was measured and rejected — read it before trying an idea again.

## Rules that save time

1. **Measure before changing.** Find which stage dominates for the board in question.
2. **Compare at fixed frequencies, never through two sweeps.** The adaptive sweep picks different
   frequencies per run; with few solves its fit is unstable. Use `sim.sparams(f)` at the same f.
3. **Benchmark on GPU device 0** (RX 7800 XT). Device 1 (GTX 980 Ti) is bus-limited.
4. **Repeat timings 2–3×**; differences under ~2 % are noise. The first call compiles kernels.
5. **Check accuracy with every speed change**: max |ΔS| against the old path at fixed frequencies, and
   `tests/mom25d` (343 tests, ~100 s with `-n 4`; run with and without `MOM25D_GPU=off`).
6. CI builds the core with `MOM25D_ARCH=-march=x86-64-v2` (no FMA); some numerical edge cases only show
   with that build — rebuild that way to reproduce a CI-only failure, then rebuild native.

## Stage timings at one frequency

```python
from kicad2ems.mom25d.api import build_board
_, sim = build_board('out/rf_ebg_board.json', 1e9, 8e9, port='via', log=lambda *a, **k: None)
sim.gpu = 'off'            # or leave the GPU on; sim.solver = 'blr' / 'direct' to force a path
for f in (4e9, 4.2e9):
    sim.sparams(f)
for k, v in sorted(sim.log.totals().items(), key=lambda kv: -kv[1])[:10]:
    print(f"{v:7.3f} s  {k}")
print(sim.log.notes[-10:])  # GMRES iterations, fallbacks, plan notes
```

A `<name>_board.json` comes from any kicad2ems run (`--preview` is enough). `build_board(..., dmax=...)`
scales the unknowns to scan sizes. Note `build_board`'s defaults differ from a kicad2ems run's mesh
options (cell growth etc.), so its N is not the run's N.

## The Vulkan LU on its own

```python
import numpy as np
from kicad2ems.mom25d.gpu_vk import VulkanBackend
b = VulkanBackend()
Z = (np.random.standard_normal((N, N)) + 1j*np.random.standard_normal((N, N))).astype(np.complex64)
Z += np.eye(N, dtype=np.complex64) * np.sqrt(N)
b.factor(Z); b.factor(Z)               # first call compiles
print(b.stats)                          # seconds, cpu (panels), wait, steps [(k0, cpu, wait)]
rate = 8 * N**3 / 3 / b.stats['seconds'] / 1e12   # TFLOP/s
```

Reference (RX 7800 XT, 2026-11-20): 7k 0.175 s, 14k 0.883 s, 20k 2.29 s. The update GEMM tops out
~11.7 TFLOP/s at k=256. First ~60 % of steps are GPU-bound, the tail CPU-panel-bound. Knobs:
`MOM25D_VK_NB` (panel width, 256 best), `MOM25D_VK_GT` (GEMM tile, 6 on AMD, 4 elsewhere).
`b.download()` gives (lu, piv) to check factors bit-for-bit between variants.

## The Vulkan LDL^T on its own (the GPU's default factor)

```python
from kicad2ems.mom25d import gpu_ldl
from kicad2ems.mom25d.half import Half
Z = Z + Z.T                             # symmetric
F = b.factor_ldl(Half.pack(Z)); F = b.factor_ldl(Half.pack(Z))
print(b.stats)                          # seconds, cpu (diagonal blocks), wait, steps
rate = 8 * N**3 / 6 / b.stats['seconds'] / 1e12
X = b.solve_ldl(F, R)                   # R: N x k
```

Reference (RX 7800 XT, 2026-11-22): 7k 0.108 s, 14k 0.485 s, 20k 1.23 s; solves at 20k 16 / 17 / 30 ms for 1 /
8 / 32 right-hand sides. `tools/mom25d_ldl_compare.py BOARD.json GHZ,...` compares it with the LU at fixed
frequencies on a board. A kernel that works on RADV can hang on NVIDIA's driver: run the Vulkan tests with
`EMSDEV_VULKAN_DEVICE=1` (the 980 Ti) too after touching the shaders (a pointer formed below its buffer hung it).

## Memory

`policy.memory_plan(n, avail, gpu, precision, solver, n_lu, nv, gpu_dense, interp, pairs)` → host/GPU bytes
and the path. Compare against a run's peak RSS (`/usr/bin/time -f %M`, or `resource.getrusage`). To list
what a solve holds, walk `sim.__dict__` / `sim.fast.__dict__` for numpy arrays (near-field `stat`,
`dZ_exact`, `_qcorr` dominate on fine meshes). Spare complex64 LUs kept in free memory (`REUSE_KEEP`) are
an optional cache, not a requirement.

## Correctness traps already found

- Mixed precision must converge to `mixed_tol` itself; accepting a stalled GMRES gave silently wrong
  currents (fixed 2026-11-19, `dense`/`fast._refine`).
- Threaded OpenBLAS calls in parallel Python threads halve the panel speed (BLAS threads are global).
- The near-field correction's cost is its integrals, not its atomic scatter.
- RADV does not overlap work across two queue submissions.

## Recording results

Put measured numbers (board, N, before/after, machine) in the code comment next to a threshold, the
CHANGELOG entry, and the ROADMAP item — including ideas that did not pay, with their numbers.
