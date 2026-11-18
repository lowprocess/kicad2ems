# Quasi-static matrix-fill backends

The magnetic and homogeneous-capacitance fills use bounded tiles of unordered
triangle pairs. Layered capacitance uses the same pair integrators for direct
and image terms, with independent observation rows. All paths use double
precision, the same near/far criterion, and analytic source integration for
close pairs. The solver equations and reciprocal matrix assembly are common
to CPU and device backends.

```python
q = MQS(board, nets=nets, terminals=terminals,
        fill_backend='openmp', fill_workers=8, fill_chunk=16384)
r = q.solve(freqs)
print(q.fill_info)  # backend, worker counts, precision, timing, pair counts
```

| Backend | Execution |
|---|---|
| `auto` | OpenMP if the updated core is built; otherwise threaded NumPy |
| `openmp` | GIL-free C++ whole-matrix fill: classification, integration and reciprocal writes in one OpenMP team |
| `threaded` | NumPy integration in a bounded `ThreadPoolExecutor`; ufuncs release the GIL |
| `numpy` | Serial NumPy reference; always one worker |

Build the OpenMP backend with:

```sh
pip install '.[core]'
python -m kicad2ems.pcbqs.core.build
```

An older core without the static kernels is treated as unavailable for this
fill, while remaining usable by the full-wave solver. Explicit selection of
an unavailable backend raises; execution failures never silently retry on a
different backend. Auto does not probe or initialize GPUs. `PCBQS_NO_CORE`
disables automatic compiled-core selection; explicit `openmp` still requests
it, matching the full-wave solver's explicit compiled selection semantics.

## Threads, memory and determinism

`fill_workers` sets the CPU parallelism. Without it, precedence is
`PCBQS_FILL_THREADS`, `PCBQS_OMP_THREADS`, `OMP_NUM_THREADS` (the first team
size), then CPU affinity constrained by Linux cgroup v2 quotas, or CPU count
on platforms without affinity. Explicit counts can exceed that default.
`PCBQS_FILL_BACKEND` selects a backend when the API requests auto.

```sh
PCBQS_FILL_BACKEND=openmp PCBQS_FILL_THREADS=8 python tools/pcbqs/nearfield_demo.py out/
```

The free-space OpenMP fill is one native call: square tiles of unordered
pairs are OpenMP tasks that classify, integrate and write both reciprocal
entries in place (`fill_info['scheduler'] == 'native'`), so no per-tile host
work remains. Other backends keep the host tile scheduler (`'host'`).
The magnetic RWG projection (`fill_info['magnetic']['combine']`) uses a
native gather when the core is built, otherwise sparse products; it writes
each symmetric entry once, with no temporaries beyond the output.

Layered electrostatics with the OpenMP backend runs one native image kernel
per block of observation rows (`fill_info['capacitance']['native_images']`):
each pair sums the spline of the smooth positive images and integrates every
close image (and the direct d = 0 terms) analytically, correcting the spline
at the same quadrature points. Image order is fixed per pair, so results do
not depend on thread count, and agree with the NumPy image loop to roundoff.
Other backends keep the NumPy loop, parallel over row blocks.
Threaded NumPy parallelizes tiles/rows. No global OpenMP or BLAS thread limits
are changed. BLAS solves and field FFTs retain their separate controls.

`fill_chunk` bounds pairs per host tile (host-scheduled backends), not triangle count. The scheduler keeps
at most one task/result per host worker in flight. Integration scratch is
bounded per worker; refined NumPy batches are subdivided further to bound
quadrature temporaries. Dense host matrices still require O(Nt²) storage.

Each pair has exactly one owner. Independent pair reductions retain their
summation order; one host writer fills both reciprocal entries. Tests check
bitwise repeatability across tile sizes and thread counts within a backend,
and numerical agreement between implementations. OpenMP/libm math
can differ from NumPy at floating-point roundoff, so cross-backend bitwise
identity is not required.

`q.fill_info` distinguishes total requested workers, native `kernel_workers`,
and host `tile_workers`. OpenMP also reports the team size observed in a
runtime probe; OpenMP dynamic limits can constrain teams. Capacitance metadata
notes when magnetic moments were reused. Timings cover fill preparation and
moment assembly, excluding RWG projection and the linear solve.

A CuPy GPU fill was tried and removed (2026-11): with its host routing it
took 39.6 s on the layered compressed case, against 0.9 s for OpenMP.

Dense MQS with the native whole-matrix fill and RWG gather (16 threads):

| Triangles | Fill | RWG projection | Assembly | Peak RSS |
|---:|---:|---:|---:|---:|
| 3,747 | 0.47 -> 0.07 s | 1.07 -> 0.04 s | 1.75 -> 0.28 s | 1.36 -> 0.81 GB |
| 6,630 | 1.37 -> 0.16 s | 3.77 -> 0.16 s | 5.92 -> 0.93 s | 4.01 -> 2.24 GB |

## Add a backend

Register a factory (`register_fill_backend`). Names cannot replace built-ins or
already registered providers.

```python
from kicad2ems.pcbqs.static_backends import register_fill_backend
register_fill_backend('my-backend', make_backend)
```

The factory signature is `make_backend(geometry, workers, chunk)`.
`geometry` is a `FillGeometry` containing read-only local nodes, topology,
heights, centroids, radii, triangle areas and prepared quadrature/edge frames.
Keep it alive while using its arrays.

The returned object implements:

```python
parallel_tiles = False  # True only for safe concurrent, internally serial calls

def evaluate(p, q, d, kind, scalar_only):
    # p, q: int32 triangle indices; d: vertical offsets (m)
    # kind: -1 = far three-point product rule; 0/1/2 = analytic
    #       source integration, with that observation refinement level
    # return host numpy float64 array, fully synchronized:
    # scalar_only: (npairs, 1), M0
    # otherwise:   (npairs, 6), M0, Mro_x, Mro_y, Mrs_x, Mrs_y, Mrr
    ...
```

Providers own their execution resources and device synchronization. They must
not mutate geometry or launch uncontrolled nested CPU teams. The engine
validates output shape, dtype and finiteness. Backend exceptions propagate;
the scheduler cancels queued work and waits for running tasks before returning
the error. A provider can implement CUDA, ROCm, Vulkan or another execution
system without changing MQS/PEEC's equations or matrix assembly.

## Reproduce scaling and accuracy checks

```sh
OPENBLAS_NUM_THREADS=1 python tools/pcbqs/benchmark_static_fill.py --dmax .5 --repeat 3 --out fill.json
OPENBLAS_NUM_THREADS=2 python tools/pcbqs/benchmark_converter.py CM5_MINIMA_3.kicad_pcb \
  --fill-backend openmp --fill-workers 2 --out cm5-openmp.json
```

The static benchmark reports median/sample times and moment errors against
the serial NumPy reference. `--scalar` benchmarks capacitance-only filling.
The converter benchmark records fill metadata alongside solve and scan times.

On this two-CPU-quota environment, the 2,838-triangle benchmark gave these
medians over three runs (16,384-pair tiles):

| Backend | CPU workers | Fill time |
|---|---:|---:|
| Serial NumPy | 1 | 3.98 s |
| Threaded NumPy | 2 | 2.33 s |
| OpenMP | 1 | 0.75 s |
| OpenMP | 2 | 0.59 s |

The maximum relative moment-matrix difference from NumPy was 4.5e-16 for
OpenMP, and zero for threaded NumPy. The gain includes compiled integration
and tiling; the pure OpenMP thread-scaling gain here is about 1.27x. Host
classification, matrix writes and preparation remain partly serial, and this
does not predict scaling on larger machines. Raw samples are in
[static-fill.json](benchmarks/static-fill.json).

For the same 9,736-triangle CM5 U702 input loop, OpenMP moment filling took
12.65 s with one thread and 9.56 s with two. The complete assembly/sweep fell
from the previous 66.8 s to 22.4 s, with unchanged geometry and copper-matrix
storage. R/L differed by less than 1.4e-14 relative. These are individual
real-board runs, not controlled medians; see the
[CM5 benchmark report](benchmarks/cm5-u702.md).

## After the fill: the solve's own kernels

The core also holds what the frequency loops did in NumPy, where a profile of each tool
(`tools/pcbqs/perf/workloads.py`) found the time in pcbqs's own Python rather than in LAPACK or the fill. Each has a
NumPy twin in `pcbqs/kernels.py`, which is what runs without the core (or with `PCBQS_NO_CORE`) and what the
tests hold the compiled one to; the results agree to rounding.

| Kernel | What it does | Where |
|---|---|---|
| `peec_system` | the charge solve's system at one frequency, R + jωL with the parts' impedances and the conductors' change of resistance (both sparse), scaled, in one pass | `peec.solve` |
| `loop_system` | the loop impedance on the unknowns kept (the free loops' block, the sources' rows and columns) | `MQS._solve_dense`, `mqs_ports.direct_ports` |
| `solve_symmetric` (NumPy and LAPACK only) | P⁻¹B for the coefficients of potential, by Cholesky where P is real in value | `peec.capacitance_matrix`, `peec.node_potential`, `peec.solve` |

The two systems were built as NumPy expressions: a temporary of the whole matrix per operation, then the block to
be factored copied out of it, and copied once more by SciPy's `lu_factor` (which wants column-major). They are
filled row by row in parallel and factored in their own memory (`kernels.lu_factor_rows`: LAPACK factors the
transpose, which is the same memory read by columns, and `lu_solve_rows` solves with the transpose of that).

| Case | Before | After |
|---|---|---|
| Charge solve, mom25d's demo board, 6,233 unknowns: the system at one frequency | 1.14 s | 0.08 s |
| the same run, 7 frequencies, whole | 20 s, 3.9 GB | 14 to 17 s, 3.1 GB |
| Two-face magnetic solve of the same board (`--thickness`), 7 frequencies, whole | 188 s, 24.9 GB | 147 s, 15.2 GB |
| Copper ports of the 24 V bridge for the circuit tool, one sheet, 11 frequencies | 19.0 s, 2.5 GB | 12.6 s, 2.0 GB |
| Capacitance matrix of two traces on FR4, 14,538 triangles: the solve for the conductors' charges | 12.9 s | 3.9 s |
| the same run, whole | 18.5 s | 8.9 s |
| the same run with its pictures, whole | 38.6 s | 15.7 s |

The coefficients of potential were solved by LAPACK's symmetric indefinite routine (`sysv`), because a layered
stack's images make the matrix complex in type; without loss its imaginary part is zero and the matrix is
positive definite, so it is solved by Cholesky (and as before where it is truly complex, or not definite to
rounding).

What is left in those runs is LAPACK's: the LU of the dense system is 90 % of the two-face solve.
