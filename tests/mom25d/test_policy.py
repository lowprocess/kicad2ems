"""policy.choose and memory_plan from a table: the rung, the basis, what is
kept, and the retry rules (a rung that failed at f is passed over up to
RETRY x f)."""
import numpy as np
import pytest

from kicad2ems.mom25d import policy
from kicad2ems.mom25d.policy import RETRY, choose, memory_plan

GPU = (16e9, lambda n: 8.0 * n * n + 0.4e9)  # a 16 GB card, Vulkan's LU


@pytest.fixture(autouse=True)
def whole_z(monkeypatch):
    """The tables below are the whole Z's rules (HALF_CASES the half's)."""
    monkeypatch.setattr(policy, 'HALF', False)
N = 10000
LOW = 0.1 * policy.LOOP_TREE_BELOW
C64, VK = ('rwg', 'cpu-c64'), ('rwg', 'vulkan-c64')

CASES = [
    # (keywords, (rung, dense, keep, basis) or a dict of fields)
    (dict(precision='double', gpu=GPU), ('cpu-c128', True, 0, 'rwg')),
    (dict(), ('cpu-c64', True, 4, 'rwg')),           # kept on the host
    (dict(avail=4e9), ('cpu-c64', False, 0, 'rwg')),  # no room: matrix-free
    (dict(reuse=False), ('cpu-c64', False, 0, 'rwg')),
    (dict(gpu=GPU), ('vulkan-c64', True, 4, 'rwg')),  # the hybrid
    (dict(gpu=GPU, avail=4e9), ('vulkan-c64', False, 0, 'rwg')),
    (dict(gpu=GPU, n=30000), ('vulkan-c64', False, 0, 'rwg')),  # > HYBRID_MAX_N
    (dict(gpu=(1e9, GPU[1])), ('cpu-c64', True, 4, 'rwg')),     # too small
    # ... unless the LUs kept there give their memory back
    (dict(gpu=(1e9, GPU[1]), held={'rwg': (0.0, 4e9)}),
     ('vulkan-c64', True, 4, 'rwg')),
    # the host's kept LUs' memory is theirs: room for them again
    (dict(avail=4e9, held={'rwg': (1.6e9, 0.0)}), ('cpu-c64', True, 1, 'rwg')),
    # the complex64 LU failed at 1 GHz: double up to RETRY x, then mixed
    (dict(f=1.2e9, history={C64: 1e9}), ('cpu-c128', True, 0, 'rwg')),
    (dict(f=1.3e9, history={C64: 1e9}), ('cpu-c64', True, 4, 'rwg')),
    (dict(f=1.2e9, gpu=GPU, history={C64: 1e9}), ('cpu-c128', True, 0, 'rwg')),
    # the GPU's solves failed: factored there, solved on the host, then GPU
    (dict(f=1.2e9, gpu=GPU, history={VK: 1e9}),
     dict(rung='cpu-c64', gpu_factor=True, dense=True, keep=4)),
    (dict(f=1.3e9, gpu=GPU, history={VK: 1e9}),
     dict(rung='vulkan-c64', gpu_factor=False)),
    # one basis's failures are not the other's
    (dict(f=1.2e9, history={('loop/tree', 'cpu-c64'): 1e9}),
     ('cpu-c64', True, 4, 'rwg')),
    # low frequency: the loop/tree basis, dense, where it fits
    (dict(kh2=LOW), ('cpu-c64', True, 4, 'loop/tree')),
    (dict(kh2=LOW, gpu=GPU), ('vulkan-c64', True, 4, 'loop/tree')),
    (dict(kh2=LOW, gpu=GPU, n=30000, nt=20000),
     ('vulkan-c64', True, 1, 'loop/tree')),
    (dict(kh2=LOW, precision='double'), ('cpu-c128', True, 0, 'loop/tree')),
    (dict(kh2=LOW, loop_tree='off'), dict(basis='rwg', why='')),
    (dict(loop_tree='on'), dict(basis='loop/tree')),
    (dict(kh2=LOW, lt_block='quad cells'), dict(basis='rwg', why='quad cells')),
    (dict(kh2=LOW, avail=2e9, nt=8000), dict(basis='rwg')),
    # block low-rank, but the loop/tree basis below its threshold
    (dict(solver='blr'), ('blr', False, 1, 'rwg')),
    (dict(solver='blr', reuse=False), ('blr', False, 0, 'rwg')),
    (dict(solver='blr', kh2=LOW), ('cpu-c64', True, 4, 'loop/tree')),
    (dict(solver='blr', kh2=LOW, lt_block='frills'), ('blr', False, 1, 'rwg')),
    # the sides' loops where a complex64 LU preconditions a double Z
    (dict(sides=True), dict(loops=True, dense=True)),
    (dict(sides=True, avail=4e9), dict(loops=False, dense=False)),
    (dict(sides=True, nq=8000), dict(loops=True)),  # (with quads too)
    (dict(sides=True, precision='double'), dict(loops=False)),
    (dict(sides=True, f=1.2e9, history={C64: 1e9}),
     dict(rung='cpu-c128', loops=True)),
    # the hybrid's double Z on the GPU where two LUs fit beside it
    (dict(gpu=GPU, gpu_dense=True), dict(gpu_dense=True, keep=4)),
    (dict(gpu=GPU, gpu_dense=True, n=20000), dict(gpu_dense=True, keep=2)),
    (dict(gpu=GPU, gpu_dense=True, n=22000),
     dict(rung='vulkan-c64', gpu_dense=False, keep=3)),
    (dict(gpu=GPU, gpu_dense=True, avail=4e9), dict(gpu_dense=False)),
    (dict(gpu_dense=True), dict(gpu_dense=False)),  # (no GPU)
]


# the dense double Z by its lower half (policy.HALF: 12 n^2 bytes on the host
# with the factor's complex64 copy, a host factor's 4 n^2, the GPU's double
# half 8 n^2), where nothing needs whole rows
HALF_CASES = [
    (dict(), dict(rung='cpu-c64', dense=True, keep=4, half=True)),
    # room for it where the whole Z had none: two LDL^Ts kept
    (dict(avail=4e9), dict(dense=True, keep=2, half=True)),
    (dict(gpu=GPU), dict(rung='vulkan-c64', dense=True, half=True)),
    # past HYBRID_MAX_N the hybrid still by its half (to HALF_HYBRID_MAX_N)
    (dict(gpu=GPU, n=30000), dict(rung='vulkan-c64', dense=True, half=True)),
    (dict(gpu=GPU, n=30000, sides=True), dict(dense=True, half=True,
                                             loops=True)),
    # the sides' loops and the loop/tree basis: whole for their transforms,
    # the half after them
    (dict(sides=True), dict(loops=True, half=True)),
    (dict(kh2=LOW), dict(basis='loop/tree', half=True)),
    (dict(kh2=LOW, gpu=GPU, n=30000, nt=20000),
     dict(basis='loop/tree', rung='vulkan-c64', half=True)),
    (dict(gpu=GPU, gpu_dense=True, n=22000),
     dict(gpu_dense=True, keep=2, half=True)),    # (the whole Z's: not there)
    (dict(nq=8000), dict(half=False)),            # quad cells
    (dict(precision='double'), dict(rung='cpu-c128', half=False)),
    (dict(f=1.2e9, history={C64: 1e9}), dict(rung='cpu-c128', half=False)),
]


@pytest.mark.parametrize('kw,want', HALF_CASES)
def test_choose_the_half(monkeypatch, kw, want):
    monkeypatch.setattr(policy, 'HALF', True)
    monkeypatch.setattr(policy, 'LDL', True)
    kw = dict(dict(f=1e9, n=N, avail=30e9, nt=2 * N), **kw)
    p = choose(kw.pop('f'), kw.pop('n'), **kw)
    assert {k: getattr(p, k) for k in want} == want, p


def test_the_half_needs_the_ldl(monkeypatch):
    monkeypatch.setattr(policy, 'HALF', True)
    monkeypatch.setattr(policy, 'LDL', False)
    assert not choose(1e9, N, avail=30e9).half


def test_memory_plan_of_the_half(monkeypatch):
    """The half's host bytes (12 n^2 with the factor's copy) and the GPU's
    double half (8 n^2); with sides (their loops, whole first) 16 n^2."""
    monkeypatch.setattr(policy, 'HALF', True)
    monkeypatch.setattr(policy, 'LDL', True)
    n = 20000
    p = memory_plan(n, avail=30e9, gpu=GPU, precision='mixed',
                    solver='direct')
    assert p['path'] == 'hybrid'
    assert p['host'] == pytest.approx(12.0 * n * n + policy.SPARE)
    assert p['gpu'] == pytest.approx(GPU[1](n) + 8.0 * n * n)
    w = memory_plan(n, avail=30e9, gpu=GPU, precision='mixed',
                    solver='direct', sides=True)
    assert w['host'] == pytest.approx(16.0 * n * n + policy.SPARE)


@pytest.mark.parametrize('kw,want', CASES)
def test_choose(kw, want):
    kw = dict(dict(f=1e9, n=N, avail=30e9, nt=2 * N), **kw)
    p = choose(kw.pop('f'), kw.pop('n'), **kw)
    if isinstance(want, tuple):
        want = dict(zip(('rung', 'dense', 'keep', 'basis'), want))
    assert {k: getattr(p, k) for k in want} == want, p


def test_the_reasons_and_paths():
    p = choose(1e9, N, avail=2e9, nt=8000, kh2=LOW)
    assert p.why.startswith('its matrices need ~')
    assert [choose(1e9, N, avail=a, gpu=g).path for a, g in
            ((30e9, None), (30e9, GPU), (4e9, GPU))] == ['cpu-mixed', 'hybrid',
                                                          'gpu-lu']
    assert choose(RETRY * 1e9, N, history={C64: 1e9}).rung == 'cpu-c128'


def test_gpu_fits_keeps_half_a_gigabyte_or_3_percent():
    assert policy.gpu_fits(15.2e9, 15.7e9) and not policy.gpu_fits(
        15.3e9, 15.7e9)
    assert policy.gpu_fits(31e9, 32e9) and not policy.gpu_fits(31.1e9, 32e9)


@pytest.mark.parametrize('kw,want', [
    # (precision, path, solver)
    (dict(n=policy.MIXED_MIN_N - 500), ('double', 'double', 'direct')),
    (dict(n=policy.MIXED_MIN_N + 1000), ('mixed', 'cpu-mixed', 'direct')),
    (dict(n=policy.MIXED_MIN_N + 1000, precision='double'),
     ('double', 'double', 'direct')),
    (dict(n=policy.GPU_MIN_N + 100, gpu=GPU), ('mixed', 'hybrid', 'direct')),
    # the backend's own estimate: Vulkan factors 40k unknowns in place on 16 GB
    (dict(n=40000, gpu=GPU, solver='direct'),
     ('mixed', 'gpu-lu', 'direct')),
    # 'auto': block low-rank from BLR_MIN_N where the hybrid does not hold Z
    (dict(n=40000, gpu=GPU), ('mixed', 'blr', 'blr')),
    (dict(n=20000, gpu=GPU), ('mixed', 'hybrid', 'direct')),
    # without a GPU, from BLR_CPU_MIN_N
    (dict(n=policy.BLR_CPU_MIN_N - 1000), ('mixed', 'cpu-mixed', 'direct')),
    (dict(n=policy.BLR_CPU_MIN_N), ('mixed', 'blr', 'blr')),
    (dict(n=policy.BLR_MIN_N), ('mixed', 'blr', 'blr')),
    (dict(n=40000, avail=8e9, gpu=GPU), ('mixed', 'blr', 'blr')),
    # quads (fewer in the LU): the direct solver's
    (dict(n=policy.BLR_MIN_N, n_lu=15000), ('mixed', 'cpu-mixed', 'direct')),
])
def test_memory_plan(kw, want):
    n = kw.pop('n')
    p = memory_plan(n, **dict(dict(avail=30e9), **kw))
    assert (p['precision'], p['path'], p['solver']) == want, p
    # the compiled factor: a fraction of a complex64 matrix
    assert p['solver'] != 'blr' or p['host'] - policy.SPARE < 8.0 * n * n
    # the LU, and the double Z where choose puts it on the GPU too
    zd = choose(np.inf, n, kw.get('n_lu') or n, avail=30e9, gpu=kw.get('gpu'),
                precision='mixed', loop_tree='off',
                gpu_dense=True).gpu_dense if want[1] == 'hybrid' else False
    m = kw.get('n_lu') or n
    assert p['gpu'] == ((GPU[1](m) if want[1] in ('hybrid', 'gpu-lu') else 0)
                        + (16.0 * m * m if zd else 0))
    off = memory_plan(n, **dict(dict(avail=30e9, gpu_dense=False), **kw))
    assert off['gpu'] == (GPU[1](m) if want[1] in ('hybrid', 'gpu-lu') else 0)


def test_memory_plan_counts_the_via_blocks_kept():
    """The sweep's interpolation keeps the via columns' blocks (blocks.Anchors:
    KEEP of them, at most a double Z or ANCHOR_ROOM): the host's bytes."""
    n, nv = 6000, 40
    base = memory_plan(n, avail=30e9, precision='double')
    kept = memory_plan(n, avail=30e9, precision='double', nv=nv)
    assert kept['host'] - base['host'] == policy.anchor_bytes(n, nv) > 0
    assert memory_plan(n, avail=30e9, precision='double', nv=nv,
                       interp=False)['host'] == base['host']
    # capped: a double Z (ANCHOR_ROOM where that is more)
    from kicad2ems.mom25d.fast import ANCHOR_ROOM
    assert policy.anchor_bytes(n, n) == max(16.0 * n * n, ANCHOR_ROOM)


def test_memory_plan_counts_the_near_field():
    """The near triangle pairs' bytes (policy.near_bytes): those held over the
    sweep, and for the block low-rank solver the sparse correction's build
    too (a dense Z takes the correction in place)."""
    from kicad2ems.mom25d import near
    from kicad2ems.mom25d.fast import FastOperator
    n, pairs = 6000, 2_000_000
    dense = memory_plan(n, avail=30e9, precision='double', pairs=pairs)
    base = memory_plan(n, avail=30e9, precision='double')
    assert dense['host'] - base['host'] == pairs * policy.NEAR_HELD
    blr = memory_plan(n, avail=30e9, solver='blr', pairs=pairs)
    blr0 = memory_plan(n, avail=30e9, solver='blr')
    # what the piece-at-a-time build keeps, and one piece's peak (near.py)
    assert (blr['host'] - blr0['host']
            == pairs * (policy.NEAR_HELD + FastOperator.NEAR_BYTES)
            + min(FastOperator.NEAR_PIECE_BYTES, near.NEAR_PIECE * pairs))


def test_the_block_low_rank_plan_counts_its_via_blocks_and_says_when_it_does_not_fit():
    """The block low-rank solve fills its via and side blocks whole (B, C:
    complex128), which a dense Z holds in place: counted (a real board's layer,
    37k edges and 10k sides of thick copper, had been killed by the machine
    running out, uncounted), and a plan over the memory available does not
    fit (sim.py then stops, saying what would)."""
    n, nv = 47_000, 10_000
    a = memory_plan(n, avail=500e9, solver='blr', nv=nv, interp=False)
    b = memory_plan(n, avail=500e9, solver='blr', nv=0, interp=False)
    assert a['host'] - b['host'] == policy.via_block_bytes(n, nv) == (
        16.0 * (n - nv) * nv + 16.0 * nv * nv)
    assert a['fits']
    tight = memory_plan(n, avail=0.9 * a['host'], solver='blr', nv=nv,
                        interp=False)
    assert tight['path'] == 'blr' and not tight['fits']


def test_ldl_without_pivoting_solves_a_symmetric_system():
    """mom25d.ldl: L D L^T of a complex-symmetric, diagonally strong matrix
    (as an equilibrated MoM matrix is) solves it as LU does, by blocks."""
    import numpy as np
    from kicad2ems.mom25d import ldl
    rng = np.random.default_rng(3)
    n = 300
    A = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    A = 0.05 * (A + A.T) + np.diag(1 + 0.3j + rng.standard_normal(n) * 0.1)
    b = rng.standard_normal((n, 3)) + 0j
    x = ldl.solve(ldl.factor(A.copy(), nb=64), b)
    assert np.abs(x - np.linalg.solve(A, b)).max() < 1e-10
