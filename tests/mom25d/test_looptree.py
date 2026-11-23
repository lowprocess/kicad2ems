"""
The loop/tree basis (looptree.py, fast.FastOperator._lt_system): its loops
carry no charge, its transforms are the basis's matrices, and the direct solver
in it is the RWG basis's answer where that holds and stays right below, where
that does not.
"""
import numpy as np
import pytest

from kicad2ems.mom25d.multilayer import Layer, LayerStack
from kicad2ems.mom25d.sim import Simulation, Via, ViaPort, Metal
from kicad2ems.mom25d.looptree import LoopTree


def _sim(precision='double', loop_tree='auto', metal=None, dmax=0.4e-3):
    """A strip on M2 over a plane on M1 (an antipad round a via to the ground),
    a via port at each end: one between the strip's pad and the plane (two
    fans), one down to the ground (a fan and the ground); a via from the plane
    to the ground by the first, so its loop closes (at DC: the copper's
    resistance)."""
    from shapely.geometry import box, Point
    st = LayerStack([Layer(0.2e-3, 4.3, 0.02), Layer(0.2e-3, 4.3, 0.02)])
    w, L = 0.35e-3, 8e-3
    v1 = Via(L / 2, 0, 0, 2, 0.1e-3)
    geo2 = [box(-L / 2, -w / 2, L / 2, w / 2), Point(L / 2, 0).buffer(0.25e-3)]
    geo1 = [
        box(-L / 2, -2e-3, L / 2 + 1e-3,
            2e-3).difference(Point(L / 2, 0).buffer(0.5e-3))
    ]
    stitch = Via(-L / 2, 1.2e-3, 0, 1, 0.1e-3)
    sim = Simulation(st, {1: geo1, 2: geo2},
                     [ViaPort(Via(-L / 2, 0, 1, 2, 0.1e-3)),
                      ViaPort(v1)], vias=[stitch], dmax=dmax,
                     dmax_layer={1: 0.6e-3}, w_ref={2: w,
                                                    1: 1e-3}, solver='direct',
                     precision=precision, metal=metal, verbose=False)
    sim.loop_tree, sim.gpu = loop_tree, 'off'
    return sim


def test_the_loops_carry_no_charge_and_the_transforms_are_the_basis():
    sim = _sim()
    lt = LoopTree(sim)
    N = lt.N
    # the via to the ground ends on its node
    assert lt.G in lt.bu or lt.G in lt.bv
    assert len(lt.tree) + len(lt.cot) == N and len(lt.roots) >= 1
    I = np.eye(N)
    Kt, Mm = lt.from_tree(I), lt.from_ideal(I)
    K = Mm @ Kt
    assert np.linalg.matrix_rank(K) == N
    QK = lt.charge_operator(sim) @ K
    # loops: no charge on any triangle
    assert np.abs(QK[:, lt.cot]).max() < 1e-12
    tq = np.abs(QK[:, lt.tree])
    # each tree branch +1 / -1 on its ends
    assert np.allclose(np.sort(tq.max(0)), 1.0)
    rng = np.random.default_rng(1)
    Z = rng.standard_normal((N, N)) + 1j * rng.standard_normal((N, N))
    Z = Z + Z.T
    ref = K.T @ Z @ K
    X = Z.copy()
    lt.to_ideal(X)
    lt.cols(X)
    lt.rows(X)
    assert np.abs(X - ref).max() < 1e-12 * np.abs(ref).max()
    E = rng.standard_normal((N, 2)) + 0j
    assert np.allclose(lt.rows(lt.rhs(E)), K.T @ E, rtol=0,
                       atol=1e-12 * np.abs(K.T @ E).max())


def test_the_charge_block_is_the_tree_s_elastance():
    sim = _sim()
    lt = LoopTree(sim)
    rng = np.random.default_rng(2)
    Mq = rng.standard_normal((lt.Nt, lt.Nt)) + 0j
    Mq = Mq + Mq.T
    a = sim.rwg.area
    A = ((lt.charge_operator(sim)
          @ lt.from_ideal(lt.from_tree(np.eye(lt.N))))[:, lt.tree])
    Phi = A.T @ (Mq / a[:, None] / a[None, :]) @ A
    Z = np.zeros((lt.N, lt.N), complex)
    lt.charge_into(Z, Mq, a)
    assert (np.abs(Z[np.ix_(lt.tree, lt.tree)] - Phi).max()
            < 1e-12 * np.abs(Phi).max())
    assert not Z[lt.cot].any() and not Z[:, lt.cot].any()


def test_the_loop_tree_solve_is_the_rwg_basis_s_where_that_holds():
    pytest.importorskip('kicad2ems.mom25d._core')
    ref = _sim('double', 'off').sparams(1e9)
    for prec in ('double', 'mixed'):
        sim = _sim(prec, 'on')
        S = sim.sparams(1e9)
        assert any('loop/tree basis:' in n for n in sim.log.notes)
        # (mixed: refined to 1e-6)
        assert (np.abs(S - ref).max()
                < (1e-9 if prec == 'double' else 5e-6)), prec


def test_below_the_rwg_basis_s_reach_it_stays_right():
    """Copper loss, 10 Hz: the loop/tree basis's S11 is the copper's DC
    resistance's, as at 10 kHz, and mixed precision converges in it (no double
    fallback). (The RWG basis there: its double LU 14 % off on this board, its
    complex64 LU no preconditioner.)"""
    pytest.importorskip('kicad2ems.mom25d._core')
    cu = Metal(5.8e7, 35e-6)
    S = {}
    for f in (1e4, 10.0):
        for prec in ('double', 'mixed'):
            sim = _sim(prec, 'auto', metal=cu)
            S[(f, prec)] = sim.sparams(f)
            assert sim.fast._basis_name == 'loop/tree'
            assert (not any('did not converge' in n
                            for n in sim.log.notes)), sim.log.notes
    for k, v in S.items():
        assert (abs(v[0, 0].real - S[(1e4, 'double')][0, 0].real)
                < 1e-3 * abs(S[(1e4, 'double')][0, 0])), k
        assert np.linalg.svd(v, compute_uv=False).max() <= 1 + 1e-9, k


def test_auto_takes_loop_tree_below_its_threshold_and_keeps_each_basis_lus():
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d import fast
    sim = _sim('mixed', 'auto')
    fo = sim.fast
    # (k h)^2 grows as f^2
    lo = 0.5 * np.sqrt(fast.LOOP_TREE_BELOW / fo.kh2(1.0))
    hi = 4 * lo
    assert fo.plan(lo).basis == 'loop/tree' and fo.plan(hi).basis == 'rwg'
    sim.sparams(lo)
    kept_lt = list(fo.st.kept)
    sim.sparams(hi)
    assert fo._basis_name == 'rwg' and all(k not in fo.st.kept for k in kept_lt)
    S_again = sim.sparams(1.05 * lo)
    assert fo._basis_name == 'loop/tree'
    # the loop/tree LU kept, used again
    assert any('reused' in n for n in sim.log.notes)
    S_fresh = _sim('double', 'on').sparams(1.05 * lo)
    assert np.abs(S_again - S_fresh).max() < 1e-5


@pytest.mark.parametrize('f', [1e8, 2e9])
def test_the_transforms_on_the_half_are_the_whole_matrixs(monkeypatch, f):
    """policy.LT_HALF: the loop/tree basis's matrix filled, transformed and
    given its charge block by its half (looptree.transform_half, to_ideal_half,
    charge_into_half) against the whole matrix's transforms, block by block (at
    0.1 GHz its blocks span ~17 decades: an error in the loops' block is
    nothing beside the tree's), and the same right-hand side."""
    import dataclasses
    from kicad2ems.mom25d import policy
    from kicad2ems.mom25d.half import Half
    got = {}
    for on in (True, False):
        monkeypatch.setattr(policy, 'LT_HALF', on)
        sim = _sim(precision='mixed', loop_tree='on')
        fo = sim.fast
        plan = dataclasses.replace(fo.plan(f), half=True)
        assert plan.basis == 'loop/tree'
        fo.setup(f, mode='direct')
        N = sim.Ne + sim.Nv
        E = np.random.default_rng(3).standard_normal((N, 2)) + 0j
        Z, B, _, _ = fo._lt_system(f, E, fo.extra_blocks(f), plan)
        assert isinstance(Z, Half)
        got[on] = (Z.to_full(), B, fo._lt)
        timers = [r[2] for r in sim.log.records if r[0] == 'loop/tree transform']
        assert ('on the half' in timers[0]) == on
    (A, Ba, lt), (W, Bw, _) = got[True], got[False]
    assert len(lt.fan_edges) and len(lt.cot) and len(lt.tree)
    for r in (lt.tree, lt.cot):
        for c in (lt.tree, lt.cot):
            w = W[np.ix_(r, c)]
            assert np.abs(A[np.ix_(r, c)] - w).max() <= 1e-13 * np.abs(w).max()
    assert np.array_equal(Ba, Bw)
