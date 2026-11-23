"""GPU LU path: the solver's GPU code runs end to end on the emulated backend
(NumPy behind the backend interface) and must reproduce the CPU mixed-precision
solver. The Vulkan backend runs on the real GPU where there is one (slangpy and
a Vulkan driver). (Real hardware: run tools/mom25d/gpu_selfcheck.py on the GPU
machine.)"""
import sys
import numpy as np
import pytest
import scipy.linalg as sla

from kicad2ems.mom25d.multilayer import Layer, LayerStack
from kicad2ems.mom25d.sim import Simulation, Via, ViaPort
import kicad2ems.mom25d.gpu as mom25d_gpu
pytest.importorskip('kicad2ems.mom25d._core')


@pytest.mark.usefixtures('rwg_at_4ghz')
def test_gpu_solves_that_stall_are_done_on_the_cpu(clean, S_cpu):
    """The GPU's triangular solves losing a good factor's accuracy (ROCm's
    complex64 trsm on an ill-conditioned factor, gpu.py): the factor is
    downloaded and solved on the CPU, no double-precision LU; near that
    frequency straight away, the GPU still factoring."""
    sim = make_sim('emulate')
    gpu = mom25d_gpu.get_backend('emulate')
    clean.setattr(type(gpu), 'solve',
                  lambda self, R: np.asarray(R, complex) * 1e-3)
    S = sim.sparams(4e9)
    h = sim.fast.history
    assert (np.abs(S - S_cpu).max() < 1e-6 and ('rwg', 'cpu-c64') not in h
            and h[('rwg', 'vulkan-c64')] == 4e9)
    assert not any(r[0] == 'LU factor' for r in sim.log.records)  # no CPU LU
    n = sum(r[0] == 'LU factor (GPU)' for r in sim.log.records)
    sim.reuse = 'off'
    sim.sparams(4.5e9)
    assert sum(r[0] == 'LU factor (GPU)' for r in sim.log.records) == n + 1
    # solved on the CPU at once
    assert sum('did not converge' in m for m in sim.log.notes) == 1


def bad_factor(v):
    """A GPU backend's download() giving a wrong factor (v x the identity: the
    preconditioner R / v; NaN: one that breaks the refinement down)."""
    def download(self):
        n = self.lu.shape[0]
        return (np.asfortranarray(np.eye(n, dtype=np.complex64) * v),
                np.arange(n, dtype=np.int32))
    return download


# ---- small two-layer model with vias (same as the core/solver agreement test)
def make_sim(gpu):
    from shapely.geometry import box, Point
    st = LayerStack([Layer(0.2e-3, 4.3, 0.02), Layer(0.2e-3, 4.3, 0.02)])
    w, L = 0.35e-3, 8e-3
    geo2 = [box(-L / 2, -w / 2, L / 2, w / 2), Point(L / 2, 0).buffer(0.25e-3)]
    geo1 = [
        box(-L / 2, -2e-3, L / 2 + 1e-3,
            2e-3).difference(Point(L / 2, 0).buffer(0.5e-3))
    ]
    return Simulation(st, {1: geo1, 2: geo2}, [
        ViaPort(Via(-L / 2, 0, 1, 2, 0.1e-3)),
        ViaPort(Via(L / 2, 0, 0, 2, 0.1e-3))
    ], dmax=0.4e-3, dmax_layer={1: 0.6e-3}, w_ref={2: w,
                                                   1: 1e-3}, solver='direct',
                      precision='mixed', gpu=gpu, verbose=False)


@pytest.fixture(scope='module')
def S_cpu():
    return make_sim('off').sparams(4e9)


@pytest.fixture
def clean(monkeypatch):
    """The GPU's factor the LU (this module's ladder is the LU's: the
    LDL^T's tests set policy.LDL themselves, ldl)."""
    from kicad2ems.mom25d import policy
    monkeypatch.delenv('MOM25D_GPU', raising=False)
    monkeypatch.setattr(policy, 'LDL', False)
    mom25d_gpu._cache.clear()
    yield monkeypatch
    mom25d_gpu._cache.clear()


@pytest.fixture
def ldl(clean):
    """clean, with the GPU's factor the LDL^T where it can be (the default)."""
    from kicad2ems.mom25d import policy
    clean.setattr(policy, 'LDL', True)
    return clean


def test_the_gpu_path_matches_cpu(clean, S_cpu):
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert any(r[0] == 'LU factor (GPU)'
               for r in sim.log.records), "GPU path not taken"
    # both refined to rtol 1e-6: they agree to round-off of that order
    assert np.abs(S - S_cpu).max() < 1e-6


def test_gpu_too_small_falls_back_to_cpu(clean, S_cpu):
    mom25d_gpu._cache['emulate'] = mom25d_gpu.EmulatedBackend(mem=1e6)
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert not any(r[0] == 'LU factor (GPU)' for r in sim.log.records)
    # the CPU's path: refined to rtol 1e-6, from a Z whose near field is summed
    # by threads in no fixed order
    assert np.abs(S - S_cpu).max() < 1e-6


def test_auto_without_gpu_is_silent_cpu(clean, S_cpu):
    clean.setitem(sys.modules, 'slangpy', None)         # import fails
    assert mom25d_gpu.get_backend('auto') is None
    S = make_sim('auto').sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6


@pytest.mark.usefixtures('rwg_at_4ghz')
def test_gpu_refinement_that_stalls_falls_back_to_double_nearby(clean, S_cpu):
    """A GPU LU too weak to precondition (here: a wrong one) must not cost the
    sweep: the refinement stops after its 30 iterations, the frequency is solved
    in double, and so is everything up to 1.25 x it; above that the GPU is tried
    again (it fails at the low end of a band, and a sweep that gave up for good
    ran on the CPU)."""
    sim = make_sim('emulate')
    gpu = mom25d_gpu.get_backend('emulate')
    # a useless preconditioner, on the GPU and the CPU
    clean.setattr(type(gpu), 'download', bad_factor(1e3))
    clean.setattr(type(gpu), 'solve',
                  lambda self, R: np.asarray(R, complex) * 1e-3)
    S = sim.sparams(4e9)
    # S_cpu: refined to 1e-6, this: double
    assert (np.abs(S - S_cpu).max() < 1e-6
            and sim.fast.history[('rwg', 'cpu-c64')] == 4e9)
    n_gpu = sum(r[0] == 'LU factor (GPU)' for r in sim.log.records)
    sim.sparams(4.5e9)
    # near the failure: double
    assert sum(r[0] == 'LU factor (GPU)' for r in sim.log.records) == n_gpu
    sim.sparams(5.5e9)
    # above 1.25 x: the GPU again
    assert sum(r[0] == 'LU factor (GPU)' for r in sim.log.records) == n_gpu + 1


@pytest.mark.usefixtures('rwg_at_4ghz')
def test_gpu_refinement_that_breaks_down_falls_back_to_double(clean, S_cpu):
    """A GMRES breakdown (a NaN residual, as the complex64 LU gave at 0.1 GHz on
    a dfsynth low-pass) is a failure too: NaN compares false against the
    tolerance, and once slipped through as the result."""
    sim = make_sim('emulate')
    gpu = mom25d_gpu.get_backend('emulate')
    clean.setattr(type(gpu), 'download', bad_factor(np.nan))
    clean.setattr(type(gpu), 'solve',
                  lambda self, R: np.full(np.shape(R), np.nan, complex))
    with np.errstate(invalid='ignore'):
        S = sim.sparams(4e9)
    assert (np.isfinite(S).all() and np.abs(S - S_cpu).max() < 1e-6
            and ('rwg', 'cpu-c64') in sim.fast.history)


def test_a_nan_gpu_factor_falls_back(clean, S_cpu):
    """A GPU LU that comes back NaN: the refinement breaks down and the solve is
    redone in double on the CPU."""
    clean.setattr(mom25d_gpu.EmulatedBackend, 'download', bad_factor(np.nan))
    clean.setattr(mom25d_gpu.EmulatedBackend, 'solve',
                  lambda self, R: np.full(np.shape(R), np.nan, complex))
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert np.isfinite(S).all() and np.abs(S - S_cpu).max() < 1e-6


def test_a_nan_solve_is_redone_on_the_cpu_or_refused(clean, S_cpu):
    """Whatever path a NaN takes past the refinement: the currents are solved
    again with the GPU off, and a solve that isn't finite on the CPU either
    raises instead of giving NaN S-parameters."""
    sim = make_sim('emulate')
    real = type(sim)._currents
    clean.setattr(
        sim, '_currents', lambda f, E: real(sim, f, E) *
        (np.nan if sim.gpu != 'off' else 1))
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6 and sim.gpu == 'emulate'
    assert any('not finite - solving it again on the CPU' in n
               for n in sim.log.notes)
    clean.setattr(sim, '_currents', lambda f, E: real(sim, f, E) * np.nan)
    with pytest.raises(FloatingPointError, match='not finite'):
        sim.sparams(4e9)


# ---- the retry ladder (fast._factor, _ladder) with failures injected -------
C64, VK = ('rwg', 'cpu-c64'), ('rwg', 'vulkan-c64')


def lus(sim, detail=''):
    """The LU factorisations logged (detail: those whose detail has it)."""
    return [r for r in sim.log.records
            if r[0].startswith('LU factor') and detail in r[2]]


def weak_host_lu(clean):
    """The host's complex64 LUs too weak to precondition (the double LU as
    it is)."""
    from kicad2ems.mom25d import fast
    real = fast.HostLU.__call__
    clean.setattr(fast.HostLU, '__call__', lambda self, R: (
        np.asarray(R, complex) * 1e-3 if self.lu[0].dtype == np.complex64
        else real(self, R)))


def test_a_gpu_factor_that_raises_is_the_hosts(clean, S_cpu):
    def boom(self, Z):
        raise RuntimeError('device lost')
    clean.setattr(mom25d_gpu.EmulatedBackend, 'factor', boom)
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6 and not sim.fast.history
    assert any('GPU LU failed (RuntimeError: device lost)' in n
               for n in sim.log.notes)
    assert len(lus(sim, 'complex64')) == 1


@pytest.mark.usefixtures('rwg_at_4ghz')
@pytest.mark.parametrize('keep', [4, 0])
def test_a_host_lu_that_does_not_precondition_is_redone_in_double(
        clean, S_cpu, keep):
    """keep 4: the dense double Z factored again in double; 0: matrix-free
    (Z complex64, gone): the frequency solved again, in double. Up to RETRY
    x f in double straight away, above it mixed again."""
    from kicad2ems.mom25d import policy
    clean.setattr(policy, 'REUSE_KEEP', keep)
    weak_host_lu(clean)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6 and sim.fast.history == {C64: 4e9}
    assert sum('did not converge' in n for n in sim.log.notes) == 1
    fills = [r[2] for r in sim.log.records if r[0] == 'direct fill']
    assert ('complex64' in fills[0]) == (keep == 0)
    assert len(fills) == (2 if keep == 0 else 1)
    n = len(lus(sim, 'complex64'))
    sim.sparams(4.5e9)
    assert len(lus(sim, 'complex64')) == n
    assert sum('did not converge' in n for n in sim.log.notes) == 1
    sim.sparams(5.5e9)
    assert len(lus(sim, 'complex64')) == n + 1


@pytest.mark.usefixtures('rwg_at_4ghz')
def test_the_matrix_free_gpu_lu_falls_back_on_the_hosts_solves(clean, S_cpu):
    """The GPU's LU past the hybrid (HYBRID_MAX_N: Z complex64, the
    refinement matrix-free) whose solves stall: the factor downloaded and
    solved on the host, nothing factored again."""
    from kicad2ems.mom25d import policy
    clean.setattr(policy, 'HYBRID_MAX_N', 0)
    gpu = mom25d_gpu.get_backend('emulate')
    clean.setattr(type(gpu), 'solve',
                  lambda self, R: np.asarray(R, complex) * 1e-3)
    sim = make_sim('emulate')
    assert sim.fast.plan(4e9).path == 'gpu-lu'
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6 and sim.fast.history == {VK: 4e9}
    assert [r[0] for r in lus(sim)] == ['LU factor (GPU)']
    p = sim.fast.plan(4.5e9)
    assert p.rung == 'cpu-c64' and p.gpu_factor and not p.dense


def test_the_loop_tree_bases_failures_are_its_own(clean):
    """A complex64 LU failing in the loop/tree basis: that basis's history,
    the RWG basis's untouched; the answer the double LU's."""
    weak_host_lu(clean)
    sim = make_sim('off')
    sim.loop_tree = 'on'
    S = sim.sparams(4e9)
    assert sim.fast.history == {('loop/tree', 'cpu-c64'): 4e9}
    assert sim.fast.plan(4.5e9).rung == 'cpu-c128'
    sim.loop_tree = 'off'
    assert sim.fast.plan(4.5e9).rung == 'cpu-c64'
    ref = make_sim('off')
    ref.loop_tree, ref.precision = 'on', 'double'
    assert np.abs(S - ref.sparams(4e9)).max() < 1e-9


@pytest.mark.usefixtures('rwg_at_4ghz')
@pytest.mark.parametrize('keep,kept', [(4, 2), (1, 1), (0, 1)])
def test_a_kept_lu_that_does_not_precondition_is_replaced(clean, keep, kept):
    """A kept LU tried past its reach: GMRES capped, a new one factored and
    kept beside it (1: in its place; 0: matrix-free, tried before the fill),
    the reach shrunk below that distance; the answer a fresh solve's."""
    from kicad2ems.mom25d import fast, policy
    clean.setattr(policy, 'REUSE_KEEP', keep)
    sim = make_sim('off')
    sim.sparams(4e9)
    st = sim.fast.st
    st.reach = 10.0
    clean.setattr(fast, 'REUSE_ITER', (1, 1))
    S = sim.sparams(8e9)
    assert any('did not precondition' in n for n in sim.log.notes)
    assert st.reach < np.log(2.0) and [k.f for k in st.kept][-1] == 8e9
    assert len(st.kept) == kept
    assert np.abs(S - make_sim('off').sparams(8e9)).max() < 1e-6


def test_a_gpu_asked_for_and_missing_is_said(clean, S_cpu):
    clean.setitem(sys.modules, 'slangpy', None)
    sim = make_sim('vulkan')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert sum('GPU requested but not available' in n
               for n in sim.log.notes) == 1


# ---- the Vulkan backend on the real GPU -------------------------------------
def _vulkan_ok():
    try:
        from kicad2ems.emsdev import gpu as devices
        devices.vulkan_device()
        return True
    except Exception:
        return False


vulkan = pytest.mark.skipif(not _vulkan_ok(),
                            reason='no Vulkan GPU (slangpy and a driver)')


@vulkan
@pytest.mark.parametrize('max_buffer', [None, 64 * 1024])
def test_vulkan_lu_matches_lapack(clean, max_buffer):
    """The hybrid LU against LAPACK: the same pivots and factor (complex64
    round-off), the GPU's solves, the factor downloaded in lu_factor's layout; a
    last panel narrower than the rest, rows interchanged; and (max_buffer) the
    matrix over many buffers, as a large one is."""
    from kicad2ems.mom25d import gpu_vk
    clean.setenv('MOM25D_VK_NB', '64')
    if max_buffer:
        clean.setattr(gpu_vk, 'MAX_BUFFER', max_buffer)
    be = gpu_vk.VulkanBackend()
    rng = np.random.default_rng(3)
    N = 300
    Z = (rng.standard_normal((N, N)) + 1j * rng.standard_normal(
        (N, N))).astype(np.complex64)
    Z += 4 * np.sqrt(N) * np.eye(N, dtype=np.complex64)
    Z[[0, 7]] = Z[[7, 0]]  # pivots away from the diagonal
    be.factor(Z)
    if max_buffer:
        assert len(be.lu[0].bufs) > 4
    lu, piv = be.download()
    ref, rpiv = sla.lu_factor(Z.T.astype(complex))
    assert np.array_equal(piv, rpiv) and lu.flags.f_contiguous
    assert np.abs(lu - ref).max() < 1e-4 * np.abs(ref).max()
    R = rng.standard_normal((N, 3)) + 1j * rng.standard_normal((N, 3))
    x = np.linalg.solve(Z.astype(complex), R)
    assert np.abs(be.solve(R) - x).max() < 1e-4 * np.abs(x).max()
    assert np.abs(be.solve(R[:, 0]) - x[:, 0]).max() < 1e-4 * np.abs(x).max()
    be.release()


@vulkan
@pytest.mark.parametrize('max_buffer', [None, 128 * 1024])
def test_vulkan_double_z_products_and_factor(clean, max_buffer):
    """The double Z on the GPU, staged or read from mapped host memory: its
    products in double precision (NumPy's to round-off), over many buffers
    too; the LU made from it, equilibrated there, the one of the host's
    complex64 copy."""
    from kicad2ems.mom25d import gpu_vk
    clean.setenv('MOM25D_VK_NB', '64')
    if max_buffer:
        clean.setattr(gpu_vk, 'MAX_BUFFER', max_buffer)
    be = gpu_vk.VulkanBackend()
    if not be.dense_ok():
        pytest.skip('no shaderFloat64')
    rng = np.random.default_rng(5)
    N = 301
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z += 4 * np.sqrt(N) * np.eye(N)
    be.put_dense(Z)
    assert not max_buffer or len(be.zd.bufs) > 4
    for k in (1, 3, 6):
        V = rng.standard_normal((N, k)) + 1j * rng.standard_normal((N, k))
        Y = Z @ V
        # (float-float there by default, MOM25D_VK_FF: ~1e-13; double 1e-15)
        assert np.abs(be.mul_dense(V) - Y).max() < 5e-13 * np.abs(Y).max()
    hb = be.host_buffer(16 * N * N)  # (read from mapped memory)
    Zh = hb.view(np.complex128, N * N).reshape(N, N)
    Zh[...] = Z
    be.put_dense(Zh, hb)
    assert np.abs(be.mul_dense(V) - Y).max() < 1e-13 * np.abs(Y).max()
    dsc = 1 / np.sqrt(np.abs(np.diagonal(Z)))
    be.factor_dense(dsc)
    a, pa = be.download()
    be.factor((dsc[:, None] * Z * dsc).astype(np.complex64))
    b, pb = be.download()
    assert np.array_equal(pa, pb) and np.abs(a - b).max() < 1e-5
    be.drop_dense()
    be.release()


def test_the_double_z_on_the_gpu_and_freed(clean, S_cpu):
    """The hybrid's products on the (emulated) GPU: the answer the host's,
    the LU made from the GPU's copy, which is freed after the solve, the
    next solve's matrix in the same mapped memory; with MOM25D_GPU_DENSE off
    the host's products."""
    from kicad2ems.mom25d import policy
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    gpu = mom25d_gpu.get_backend('emulate')
    assert any(r[0] == 'Z to the GPU' for r in sim.log.records)
    assert gpu.zd is None and np.abs(S - S_cpu).max() < 1e-6
    # the mapped matrix again, cleared first (the far fill adds to it)
    assert np.abs(sim.sparams(4e9) - S_cpu).max() < 1e-6
    clean.setattr(policy, 'GPU_DENSE', False)
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert not any(r[0] == 'Z to the GPU' for r in sim.log.records)
    assert np.abs(S - S_cpu).max() < 1e-6


@vulkan
@pytest.mark.parametrize('sym', [False, True])
def test_vulkan_backend_matches_cpu(clean, S_cpu, sym):
    from kicad2ems.mom25d import policy
    clean.setattr(policy, 'LDL', sym)
    sim = make_sim('vulkan')
    S = sim.sparams(4e9)
    name = 'LDL^T factor (GPU)' if sym else 'LU factor (GPU)'
    assert any(r[0] == name for r in sim.log.records), "GPU path not taken"
    assert np.abs(S - S_cpu).max() < 1e-6


# ---- the LDL^T (gpu_ldl.py; the emulated backend's: half.py's) -------------
def ldls(sim):
    return [r for r in sim.log.records if r[0] == 'LDL^T factor (GPU)']


@pytest.mark.usefixtures('rwg_at_4ghz')
@pytest.mark.parametrize('dense', [True, False])
def test_the_gpu_ldl_path_matches_cpu(ldl, S_cpu, dense):
    """The GPU's factor the LDL^T, the hybrid's (the double Z there, its
    complex64 half made from it) and the GPU's alone (the refinement
    matrix-free): the CPU's answer."""
    from kicad2ems.mom25d import policy
    if not dense:
        ldl.setattr(policy, 'HYBRID_MAX_N', 0)
        ldl.setattr(policy, 'HALF_HYBRID_MAX_N', 0)
    sim = make_sim('emulate')
    assert sim.fast.plan(4e9).path == ('hybrid' if dense else 'gpu-lu')
    S = sim.sparams(4e9)
    assert len(ldls(sim)) == 1 and not lus(sim)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert any('(GPU, LDL^T)' in n for n in sim.log.notes)


@pytest.mark.usefixtures('rwg_at_4ghz')
@pytest.mark.parametrize('dense', [True, False])
def test_an_ldl_that_does_not_precondition_is_the_lu(ldl, S_cpu, dense):
    """An LDL^T too weak to precondition (here: a wrong one): the LU instead,
    on the GPU, and up to RETRY x f straight away; above it the LDL^T again.
    A matrix-free plan's Z is gone: the frequency filled again."""
    from kicad2ems.mom25d import policy
    if not dense:
        ldl.setattr(policy, 'HYBRID_MAX_N', 0)
        ldl.setattr(policy, 'HALF_HYBRID_MAX_N', 0)
    ldl.setattr(mom25d_gpu.EmulatedBackend, 'solve_ldl',
                lambda self, F, R: np.asarray(R, complex) * 1e-3)
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert sim.fast.history == {('rwg', 'ldl'): 4e9}
    assert len(ldls(sim)) == 1 and [r[0] for r in lus(sim)] == [
        'LU factor (GPU)']
    fills = [r for r in sim.log.records if r[0] == 'direct fill']
    assert len(fills) == (1 if dense else 2)
    assert any('the LDL^T did not precondition at 4 GHz' in n
               for n in sim.log.notes)
    sim.reuse = 'off'
    sim.sparams(4.5e9)
    assert len(ldls(sim)) == 1 and len(lus(sim)) == 2
    sim.sparams(5.5e9)
    assert len(ldls(sim)) == 2


@pytest.mark.parametrize('dense', [True, False])
def test_an_ldl_without_a_factor_is_the_lu(ldl, S_cpu, dense):
    """A zero pivot (no factor without pivoting): the GPU's LU, said. The
    matrix-free plan's Z went into the half in place (Half.compact): the
    frequency filled again for the LU."""
    from kicad2ems.mom25d import policy
    def zero(self, *a):
        raise ZeroDivisionError('LDL^T: a zero pivot')
    ldl.setattr(mom25d_gpu.EmulatedBackend, 'factor_ldl', zero)
    ldl.setattr(mom25d_gpu.EmulatedBackend, 'factor_dense_ldl', zero)
    basis = 'loop/tree'      # (this model's at 4 GHz)
    if not dense:
        ldl.setattr(policy, 'HYBRID_MAX_N', 0)
        ldl.setattr(policy, 'HALF_HYBRID_MAX_N', 0)
        ldl.setattr(policy, 'LOOP_TREE_BELOW', 1e-5)
        from kicad2ems.mom25d import fast
        ldl.setattr(fast, 'LOOP_TREE_BELOW', 1e-5)
        basis = 'rwg'
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert [r[0] for r in lus(sim)] == ['LU factor (GPU)']
    assert any('has no factor without pivoting' in n for n in sim.log.notes)
    assert sim.fast.history == {(basis, 'ldl'): 4e9}
    fills = [r for r in sim.log.records if r[0] == 'direct fill']
    assert len(fills) == (1 if dense else 2)


def test_the_ldl_off_is_the_lu(ldl, S_cpu):
    from kicad2ems.mom25d import policy
    ldl.setattr(policy, 'LDL', False)
    sim = make_sim('emulate')
    S = sim.sparams(4e9)
    assert not ldls(sim) and len(lus(sim)) == 1
    assert np.abs(S - S_cpu).max() < 1e-6


@vulkan
@pytest.mark.parametrize('N,max_buffer', [(300, None), (300, 64 * 1024),
                                          (64, None), (1000, None)])
def test_vulkan_ldl_matches_the_hosts(clean, N, max_buffer):
    """The GPU's LDL^T against half.ldl_factor: the same factor (complex64
    round-off), the GPU's solves (one and several right-hand sides), the
    factor downloaded; a last block narrower than the rest, one block alone,
    and (max_buffer) the block columns over many buffers."""
    from kicad2ems.mom25d import gpu_vk
    from kicad2ems.mom25d.half import Half, ldl_factor, ldl_solve
    clean.setenv('MOM25D_VK_NB', '64')
    if max_buffer:
        clean.setattr(gpu_vk, 'MAX_BUFFER', max_buffer)
    be = gpu_vk.VulkanBackend()
    rng = np.random.default_rng(4)
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z = Z + Z.T + 4 * np.sqrt(N) * np.eye(N)
    F = be.factor_ldl(Half.pack(Z, nb=be.nb))
    if max_buffer:
        assert len(F.dev.bufs) > 4
    ref = ldl_factor(Half.pack(Z, nb=be.nb))
    got = be.download_ldl(F)
    assert np.abs(got.data - ref.data).max() < 1e-5 * np.abs(ref.data).max()
    R = rng.standard_normal((N, 3)) + 1j * rng.standard_normal((N, 3))
    x = np.linalg.solve(Z, R)
    assert np.abs(be.solve_ldl(F, R) - x).max() < 1e-5 * np.abs(x).max()
    assert np.abs(be.solve_ldl(F, R[:, 0]) - x[:, 0]).max() < (
        1e-5 * np.abs(x).max())
    assert np.abs(ldl_solve(got, R) - x).max() < 1e-5 * np.abs(x).max()
    if be.dense_ok():
        # from the double Z on the GPU, equilibrated there (its buffers as
        # they are: the double Z's table has 32)
        clean.setattr(gpu_vk, 'MAX_BUFFER', 2 ** 31)
        be.put_dense(Z)
        dsc = 1 / np.sqrt(np.abs(np.diagonal(Z)))
        G = be.download_ldl(be.factor_dense_ldl(dsc))
        H = be.download_ldl(be.factor_ldl(Half.pack(Z, dsc, nb=be.nb)))
        assert np.abs(G.data - H.data).max() < 1e-5 * np.abs(H.data).max()
        be.drop_dense()
    be.release()


@vulkan
@pytest.mark.parametrize('N,chunk', [(300, 1 << 28), (1000, 64 * 1024)])
def test_the_double_half_converted_on_its_way_up(clean, N, chunk):
    """factor_ldl(H, dsc) of the double half (gpu_ldl.upload_c64: converted
    and equilibrated a range of rows at a time into the staging buffers, no
    complex64 half on the host): the factor of its complex64 half's upload,
    a chunk smaller than a block's rows too."""
    from kicad2ems.mom25d import gpu_ldl, gpu_vk
    from kicad2ems.mom25d.half import Half
    clean.setenv('MOM25D_VK_NB', '64')
    be = gpu_vk.VulkanBackend()
    rng = np.random.default_rng(5)
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z = Z + Z.T + 4 * np.sqrt(N) * np.eye(N)
    dsc = 1 / np.sqrt(np.abs(np.diagonal(Z)))
    ref = be.download_ldl(be.factor_ldl(Half.pack(Z, dsc, nb=be.nb)))
    real = gpu_ldl.upload_c64
    clean.setattr(gpu_ldl, 'upload_c64',
                  lambda b, H, d: real(b, H, d, chunk=chunk))
    H = Half.pack(Z, dtype=complex, nb=be.nb)
    got = be.download_ldl(be.factor_ldl(H, dsc))
    assert np.abs(got.data - ref.data).max() < 1e-6 * np.abs(ref.data).max()
    be.release()


@vulkan
@pytest.mark.parametrize('N,chunk', [(300, 1 << 28), (700, 40 * 1024)])
def test_the_lu_from_the_double_half(clean, N, chunk):
    """factor_from_half: the double half's rows converted into the staging
    buffers and set at both places of the LU's whole matrix on the GPU (no
    whole copy on the host): the LU of that copy's upload (factor), its
    solves Z's, a chunk smaller than a block's rows too."""
    from kicad2ems.mom25d import gpu_vk
    from kicad2ems.mom25d.half import Half
    clean.setenv('MOM25D_VK_NB', '64')
    be = gpu_vk.VulkanBackend()
    if not be.dense_ok():
        pytest.skip('no shaderFloat64')
    rng = np.random.default_rng(8)
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z = Z + Z.T + 4 * np.sqrt(N) * np.eye(N)
    dsc = 1 / np.sqrt(np.abs(np.diagonal(Z)))
    Zs = Z * dsc[:, None] * dsc[None, :]
    be.factor(Zs.astype(np.complex64))
    ref = be.download()
    be.factor_from_half(Half.pack(Z, dtype=complex, nb=be.nb), dsc,
                        chunk=chunk)
    got = be.download()
    assert np.array_equal(got[1], ref[1])
    assert np.abs(got[0] - ref[0]).max() < 1e-6 * np.abs(ref[0]).max()
    R = rng.standard_normal((N, 2)) + 1j * rng.standard_normal((N, 2))
    x = np.linalg.solve(Zs, R)
    assert np.abs(be.solve(R) - x).max() < 1e-4 * np.abs(x).max()
    be.release()


@vulkan
def test_vulkan_ldl_zero_pivot_raises(clean):
    from kicad2ems.mom25d import gpu_vk
    from kicad2ems.mom25d.half import Half
    clean.setenv('MOM25D_VK_NB', '64')
    be = gpu_vk.VulkanBackend()
    N = 200
    Z = np.eye(N, dtype=complex)
    Z[70, 70] = 0           # (the second block's)
    with pytest.raises(ZeroDivisionError, match='block of 64'):
        be.factor_ldl(Half.pack(Z, nb=be.nb))
    be.release()


def test_far_field_of_several_excitations_at_once_is_each_ones():
    """far_field with I (N x k): every excitation's far field from one set of
    phases, as one column at a time (the vias' included)."""
    from kicad2ems.mom25d.farfield import far_field
    sim = make_sim('off')
    f = 4e9
    I = sim.currents(f, sim.excitation())
    th, ph = np.arange(0, 91, 15.0), np.arange(0, 361, 30.0)
    Et, Ep = far_field(sim, I, f, th, ph)
    assert Et.shape == (I.shape[1], len(ph), len(th))
    for c in range(I.shape[1]):
        e1, p1 = far_field(sim, I[:, c], f, th, ph)
        assert np.allclose(Et[c], e1, rtol=1e-12, atol=1e-14 * np.abs(e1).max())
        assert np.allclose(Ep[c], p1, rtol=1e-12, atol=1e-14 * np.abs(p1).max())


# ---- the dense double Z by its lower half (policy.HALF, half.py) -----------
def fills(sim):
    return [r[2] for r in sim.log.records if r[0] == 'direct fill']


@pytest.fixture
def half(ldl):
    """ldl, with the dense Z by its lower half where it can be (the
    default), and the RWG basis at 4 GHz."""
    from kicad2ems.mom25d import fast, policy
    ldl.setattr(policy, 'HALF', True)
    ldl.setattr(policy, 'LOOP_TREE_BELOW', 1e-5)
    ldl.setattr(fast, 'LOOP_TREE_BELOW', 1e-5)
    return ldl


@pytest.mark.parametrize('gpu,dense', [('off', False), ('emulate', False),
                                       ('emulate', True)])
def test_the_half_is_the_whole_zs_answer(half, S_cpu, gpu, dense):
    """The half (filled by its rows from their blocks' start, the near field
    and the vias' border into it; its LDL^T, on the GPU or the host; its
    products, on the GPU's copy with gpu_dense) against the whole Z's LU:
    S to the refinement's tolerance."""
    from kicad2ems.mom25d import policy
    half.setattr(policy, 'GPU_DENSE', dense)
    sim = make_sim(gpu)
    p = sim.fast.plan(4e9)
    assert p.half and p.gpu_dense == dense
    S = sim.sparams(4e9)
    assert 'its lower half' in fills(sim)[0]
    assert np.abs(S - S_cpu).max() < 1e-6
    moved = [r[2] for r in sim.log.records if r[0] == 'Z to the GPU']
    assert bool(moved) == dense and (not dense or 'its half' in moved[0])
    half.setattr(policy, 'HALF', False)
    whole = make_sim(gpu)
    assert not whole.fast.plan(4e9).half
    assert np.abs(whole.sparams(4e9) - S).max() < 1e-6


def test_a_half_whose_ldl_does_not_precondition_is_the_lu(half, S_cpu):
    """The host's LDL^T of the half too weak: the LU of its whole complex64
    copy, up to RETRY x f; the answer the CPU's."""
    from kicad2ems.mom25d import fast
    half.setattr(fast.HostLDL, '__call__',
                 lambda self, R: np.asarray(R, complex) * 1e-3)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert sim.fast.history == {('rwg', 'ldl'): 4e9}
    assert [r[2] for r in lus(sim)] == ["N=1747, complex64, the half's whole "
                                        "copy"]


def test_a_half_whose_factors_do_not_precondition_is_solved_in_double(
        half, S_cpu):
    """Neither the LDL^T nor the LU in complex64: the half's own LDL^T in
    double, in place (no whole Z); the next frequency near it the double
    LU's, whole."""
    from kicad2ems.mom25d import fast
    half.setattr(fast.HostLDL, '__call__',
                 lambda self, R: np.asarray(R, complex) * 1e-3)
    half.setattr(fast.HostLU, '__call__',
                 lambda self, R: np.asarray(R, complex) * 1e-3)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert np.abs(S - S_cpu).max() < 1e-6
    assert any(r[0] == 'LDL^T factor' and 'complex128' in r[2]
               for r in sim.log.records)
    assert ('rwg', 'cpu-c64') in sim.fast.history
    sim.reuse = 'off'
    assert not sim.fast.plan(4.5e9).half


def lossy_edges(clean):
    """The extras with an asymmetric metal loss: each edge its own surface
    impedance times the Gram matrix (diag(zs) G: what a finish that differs
    between two edges of a triangle would give)."""
    from scipy import sparse
    from kicad2ems.mom25d import fast
    from kicad2ems.mom25d.base import gram_matrix
    real = fast.FastOperator.extra_blocks

    def lossy(self, f):
        S = real(self, f).tolil()
        sim = self.sim
        Ne = sim.Ne
        zs = (1 + 1j) * np.random.default_rng(0).uniform(0.01, 1.0, Ne)
        G = sparse.csr_matrix(gram_matrix(sim.rwg))
        S[:Ne, :Ne] = S[:Ne, :Ne] + sparse.diags(zs) @ G
        return S.tocsr()
    clean.setattr(fast.FastOperator, 'extra_blocks', lossy)


def test_a_halfs_asymmetric_extras_are_its_products(half):
    """Sparse extras that are not symmetric: their symmetric part in the
    half, the rest in its products; the answer the whole Z's."""
    from kicad2ems.mom25d import policy
    lossy_edges(half)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert sim.fast._Sa is not None and sim.fast.plan(5e9).half
    assert not any('did not' in n for n in sim.log.notes)
    half.setattr(policy, 'HALF', False)
    assert np.abs(make_sim('off').sparams(4e9) - S).max() < 1e-6


def test_a_half_that_cannot_hold_its_extras_is_given_up(half):
    """The double fallback's refinement with the extras' rest failing (here:
    the factor's products broken, the complex64 factors too weak): the half
    given up for that operator, the frequency solved whole."""
    from kicad2ems.mom25d import fast, policy
    lossy_edges(half)
    for M in (fast.HostLDL, fast.HostLU):
        half.setattr(M, '__call__',
                     lambda self, R: np.asarray(R, complex) * 1e-3)
    half.setattr(fast, 'ldl_mul', lambda F, V: np.asarray(V) * 0)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert sim.fast._half_off and not sim.fast.plan(6e9).half
    assert any('too large for the half' in n for n in sim.log.notes)
    half.setattr(policy, 'HALF', False)
    assert np.abs(make_sim('off').sparams(4e9) - S).max() < 1e-6


@vulkan
@pytest.mark.parametrize('max_buffer', [None, 128 * 1024])
def test_vulkan_double_half_products_and_factor(clean, max_buffer):
    """The double Z's half on the GPU: its products (the stored rows', then
    their mirror's) NumPy's to round-off, over many buffers too; the LDL^T
    made from it, equilibrated there, the one of the host's complex64
    half."""
    from kicad2ems.mom25d import _core, gpu_vk
    from kicad2ems.mom25d.half import Half
    clean.setenv('MOM25D_VK_NB', '64')
    if max_buffer:
        clean.setattr(gpu_vk, 'MAX_BUFFER', max_buffer)
    be = gpu_vk.VulkanBackend()
    if not be.dense_ok():
        pytest.skip('no shaderFloat64')
    rng = np.random.default_rng(6)
    N = 301
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z = Z + Z.T + 4 * np.sqrt(N) * np.eye(N)
    H = Half.pack(Z, dtype=complex, nb=be.nb)
    be.put_dense_half(H)
    assert not max_buffer or len(be.zd.bufs) > 4
    for k in (1, 3, 9, 33):
        V = rng.standard_normal((N, k)) + 1j * rng.standard_normal((N, k))
        Y = Z @ V
        assert np.abs(be.mul_dense(V) - Y).max() < 1e-13 * np.abs(Y).max()
    dsc = 1 / np.sqrt(np.abs(np.diagonal(Z)))
    G = be.download_ldl(be.factor_dense_ldl(dsc))
    Hc = Half(N, be.nb, np.complex64, 'empty')
    _core.half_c64(H.data, dsc, Hc.data, be.nb)
    R = be.download_ldl(be.factor_ldl(Hc))
    assert np.abs(G.data - R.data).max() < 1e-6 * np.abs(R.data).max()
    be.drop_dense()
    be.release()


@pytest.mark.parametrize('half_z', [False, True])
def test_the_products_split_with_the_host(clean, half_z):
    """FastOperator._products: from SPLIT_MIN columns the GPU's copy of Z
    takes the first of them and the host the rest, the share moved by their
    rates; the product the whole one's."""
    from kicad2ems.mom25d import policy
    from kicad2ems.mom25d.half import Half
    clean.setattr(policy, 'SPLIT_MIN', 4)
    sim = make_sim('emulate')
    gpu = mom25d_gpu.get_backend('emulate')
    rng = np.random.default_rng(1)
    n = 300
    Z = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    Z = Z + Z.T
    H = Half.pack(Z, dtype=complex, nb=64)
    if half_z:
        gpu.put_dense_half(H)
    else:
        gpu.put_dense(Z)
    host = H.matvec if half_z else (lambda X: Z @ X)
    for k in (1, 3, 10, 40):
        X = rng.standard_normal((n, k)) + 1j * rng.standard_normal((n, k))
        assert np.abs(sim.fast._products(gpu, host, X) - Z @ X).max() < 1e-10
    assert 0.2 <= sim.fast._share <= 0.9
    gpu.drop_dense()


@vulkan
def test_vulkan_products_split_with_the_host(clean):
    from kicad2ems.mom25d import gpu_vk
    from kicad2ems.mom25d.half import Half
    be = gpu_vk.VulkanBackend()
    if not be.dense_ok():
        pytest.skip('no shaderFloat64')
    rng = np.random.default_rng(2)
    n = 700
    Z = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    Z = Z + Z.T
    H = Half.pack(Z, dtype=complex, nb=be.nb)
    be.put_dense_half(H)
    X = rng.standard_normal((n, 70)) + 1j * rng.standard_normal((n, 70))
    for share in (0.0, 0.3, 1.0):
        Y, th, tw = be.mul_dense(X, host=H.matvec, share=share)
        assert np.abs(Y - Z @ X).max() < 1e-12 * np.abs(Z @ X).max()
    be.drop_dense()
    be.release()


@pytest.mark.parametrize('gpu', ['off', 'emulate'])
def test_the_loop_tree_bases_matrix_by_its_half(ldl, S_cpu, gpu):
    """The loop/tree basis's matrix whole for its transform, then by its half
    (Half.compact, the rest given back): the whole one's answer; its charge
    matrix by blocks of triangles (FastOperator.CHARGE_COLS) the same to
    rounding."""
    from kicad2ems.mom25d import fast, policy
    sim = make_sim(gpu)
    p = sim.fast.plan(4e9)
    assert p.basis == 'loop/tree' and p.half
    S = sim.sparams(4e9)
    # (by its half throughout: policy.LT_HALF; whole for the transforms
    # before)
    assert not any(r[0] == 'Z into its half' for r in sim.log.records)
    assert any(r[0] == 'loop/tree transform' and 'on the half' in r[2]
               for r in sim.log.records)
    assert np.abs(S - S_cpu).max() < 1e-6
    ldl.setattr(policy, 'LT_HALF', False)
    after = make_sim(gpu)
    assert np.abs(after.sparams(4e9) - S).max() < 1e-6
    assert any(r[0] == 'Z into its half' for r in after.log.records)
    ldl.setattr(policy, 'LT_HALF', True)
    ldl.setattr(fast.FastOperator, 'CHARGE_COLS', 37)
    blocks = make_sim(gpu).sparams(4e9)
    assert np.abs(blocks - S).max() < 1e-9
    ldl.setattr(policy, 'HALF', False)
    whole = make_sim(gpu)
    assert not whole.fast.plan(4e9).half
    assert np.abs(whole.sparams(4e9) - S).max() < 1e-6


def test_the_sides_loops_matrix_by_its_half(ldl):
    """The sides' loops (thick copper) on the half itself (policy.LOOPS_HALF,
    Half.loop_transform; mixed precision, where they are taken): the matrix
    the whole one's transformed, then halved, and the answer that of the
    whole Z's."""
    import dataclasses
    from kicad2ems.mom25d import policy
    from kicad2ems.mom25d.api import build_board
    from kicad2ems.mom25d.half import Half
    sys.path.insert(0, __file__.rsplit('/', 1)[0])
    from test_board import board, quiet

    def sim():
        _, s = build_board(board('thru'), 1e9, 6e9, port='via',
                           thickness='sides', coarsen=None, log=quiet)
        s.precision = 'mixed'
        return s
    got, S = {}, {}
    for on in (True, False):
        ldl.setattr(policy, 'LOOPS_HALF', on)
        s = sim()
        fo = s.fast
        plan = fo.plan(3e9)
        assert plan.half and plan.loops
        fo.setup(3e9, mode='direct')
        N = s.Ne + s.Nv
        E = np.random.default_rng(2).standard_normal((N, 2)) + 0j
        Z, B, _, _ = fo._rwg_system(3e9, E, fo.extra_blocks(3e9), plan)
        assert isinstance(Z, Half)
        got[on] = (Z.to_full(), B)
        timer = [r[2] for r in s.log.records if r[0] == 'side loops'][0]
        assert ('on the half' in timer) == on
        S[on] = sim().sparams(3e9)
    (A, Ba), (Wm, Bw) = got[True], got[False]
    assert np.abs(A - Wm).max() <= 1e-13 * np.abs(Wm).max()
    assert np.abs(Ba - Bw).max() <= 1e-13 * np.abs(Bw).max()
    ldl.setattr(policy, 'HALF', False)
    whole = sim().sparams(3e9)
    assert np.abs(S[True] - S[False]).max() < 1e-6
    assert np.abs(S[True] - whole).max() < 1e-6


def test_a_transformed_z_with_asymmetric_extras_stays_whole(ldl, S_cpu):
    """Extras with an antisymmetric part: transformed they would not be
    sparse, so the loop/tree basis's matrix stays whole (said); the answer
    the whole one's."""
    from kicad2ems.mom25d import policy
    lossy_edges(ldl)
    sim = make_sim('off')
    S = sim.sparams(4e9)
    assert sim.fast.plan(4e9).basis == 'loop/tree'
    assert not any(r[0] == 'Z into its half' for r in sim.log.records)
    assert any('not symmetric' in n for n in sim.log.notes)
    ldl.setattr(policy, 'HALF', False)
    assert np.abs(make_sim('off').sparams(4e9) - S).max() < 1e-6


def test_two_ports_at_one_place_keep_the_loop_tree_basis_off(clean):
    """Two via chains at one place (two ports there: a segment cut doubled):
    in the loop/tree basis one is a loop through the other, its row zero; the
    plan takes the RWG basis, says why, and solves (0.1 GHz)."""
    from shapely.geometry import box, Point
    st = LayerStack([Layer(0.2e-3, 4.3, 0.02), Layer(0.2e-3, 4.3, 0.02)])
    w, L = 0.35e-3, 8e-3
    geo2 = [box(-L / 2, -w / 2, L / 2, w / 2), Point(L / 2, 0).buffer(0.25e-3)]
    geo1 = [box(-L / 2, -2e-3, L / 2 + 1e-3,
                2e-3).difference(Point(L / 2, 0).buffer(0.5e-3))]
    sim = Simulation(st, {1: geo1, 2: geo2}, [
        ViaPort(Via(-L / 2, 0, 1, 2, 0.1e-3)),
        ViaPort(Via(-L / 2, 0, 1, 2, 0.1e-3)),
        ViaPort(Via(L / 2, 0, 0, 2, 0.1e-3))
    ], dmax=0.4e-3, dmax_layer={1: 0.6e-3}, w_ref={2: w, 1: 1e-3},
        solver='direct', precision='mixed', gpu='off', verbose=False)
    p = sim.fast.plan(1e8)
    assert p.basis == 'rwg' and 'two via chains' in p.why
    S = sim.sparams(1e8)
    assert np.isfinite(S).all()


def test_a_run_without_room_gives_its_share_back(clean, monkeypatch):
    """MOM25D_NO_ROOM_EXIT: where the direct solver does not fit the run's
    memory (here a 1 MB budget), the run exits with that code before the fill
    (core.segment runs it again alone) rather than taking the compressed
    solver."""
    from shapely.geometry import box
    monkeypatch.setenv('EMSDEV_MEM_BUDGET', '1M')
    monkeypatch.setenv('MOM25D_NO_ROOM_EXIT', '75')
    st = LayerStack([Layer(0.5e-3, 4.3, 0.0)])
    with pytest.raises(SystemExit) as e:      # (the plan: when it is set up)
        Simulation(st, {1: [box(-4e-3, -0.5e-3, 4e-3, 0.5e-3)]}, [],
                   dmax=0.5e-3, solver='auto', gpu='off', verbose=False)
    assert e.value.code == 75
    monkeypatch.delenv('MOM25D_NO_ROOM_EXIT')
    monkeypatch.setenv('EMSDEV_MEM_BUDGET', '64G')
    Simulation(st, {1: [box(-4e-3, -0.5e-3, 4e-3, 0.5e-3)]}, [],
               dmax=0.5e-3, solver='auto', gpu='off', verbose=False)


def test_gmres_stops_on_a_broken_preconditioner():
    """A preconditioner giving NaN (a factor without pivoting broken down):
    GMRES stops at once, the columns unconverged and finite, rather than on to
    its cap (each step an exact product on a large piece)."""
    from kicad2ems.mom25d.dense import gmres_batched
    rng = np.random.default_rng(0)
    A = np.eye(40) * 4 + rng.standard_normal((40, 40)) * 0.1
    B = rng.standard_normal((40, 2)) + 0j
    calls = [0]

    def Mbad(R):
        calls[0] += 1
        return np.full_like(R, np.nan)
    out = {}
    X, it = gmres_batched(lambda V: A @ V, B, Mbad, restart=30, maxiter=10,
                          out=out)
    assert calls[0] == 1 and np.isfinite(X).all()
    assert (out['rel'] > 0.5).all()
    X, it = gmres_batched(lambda V: A @ V, B, None, restart=30, maxiter=10,
                          out=out)
    assert (out['rel'] < 1e-6).all()


def test_the_double_rung_checks_its_room(monkeypatch):
    """FastOperator._room: MemoryError where the double LU would not fit (the
    rung after mixed precision failed had none), the exit where the run's
    share may be given back."""
    from kicad2ems.mom25d import fast
    monkeypatch.setattr(fast, 'available_memory', lambda: 4e9)
    fast.FastOperator._room(3e9, 'a small one')
    monkeypatch.delenv('MOM25D_NO_ROOM_EXIT', raising=False)
    with pytest.raises(MemoryError, match='needs ~20.0 GB, 4.0 GB'):
        fast.FastOperator._room(20e9, 'the double LU')
    monkeypatch.setenv('MOM25D_NO_ROOM_EXIT', '75')
    with pytest.raises(SystemExit) as e:
        fast.FastOperator._room(20e9, 'the double LU')
    assert e.value.code == 75
