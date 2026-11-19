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
    monkeypatch.delenv('MOM25D_GPU', raising=False)
    mom25d_gpu._cache.clear()
    yield monkeypatch
    mom25d_gpu._cache.clear()


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
        assert np.abs(be.mul_dense(V) - Y).max() < 1e-13 * np.abs(Y).max()
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
def test_vulkan_backend_matches_cpu(clean, S_cpu):
    sim = make_sim('vulkan')
    S = sim.sparams(4e9)
    assert any(r[0] == 'LU factor (GPU)'
               for r in sim.log.records), "GPU path not taken"
    assert np.abs(S - S_cpu).max() < 1e-6


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
