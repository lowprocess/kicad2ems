"""policy.choose and memory_plan from a table: the rung, the basis, what is
kept, and the retry rules (a rung that failed at f is passed over up to
RETRY x f)."""
import pytest

from kicad2ems.mom25d import policy
from kicad2ems.mom25d.policy import RETRY, choose, memory_plan

GPU = (16e9, lambda n: 8.0 * n * n + 0.4e9)  # a 16 GB card, Vulkan's LU
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
    (dict(sides=True, nq=8000), dict(loops=False)),
    (dict(sides=True, precision='double'), dict(loops=False)),
    (dict(sides=True, f=1.2e9, history={C64: 1e9}),
     dict(rung='cpu-c128', loops=True)),
]


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
    (dict(n=15000), ('mixed', 'cpu-mixed', 'direct')),
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
    assert p['solver'] != 'blr' or p['host'] < 8.0 * n * n
    assert p['gpu'] == (GPU[1](n) if want[1] in ('hybrid', 'gpu-lu') else 0)
