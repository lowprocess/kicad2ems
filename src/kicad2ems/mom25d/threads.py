"""
mom25d.threads - the solvers' threads: the BLAS's and the compiled core's OpenMP on the
fast physical cores (fast.py's, split out: R-F3)
"""
import os
import threading


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


def _one_blas_thread():
    """threadpoolctl's limit of one BLAS thread (a no-op context without it)."""
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        from contextlib import nullcontext
        return nullcontext()
    return threadpool_limits(limits=1, user_api='blas')
