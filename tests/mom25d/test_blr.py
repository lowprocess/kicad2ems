"""
The compiled block low-rank LU (core BLRFactor, fast._CompiledBLR): the
preconditioner of fast._solve_blr, against the direct solver.
"""
import numpy as np
import pytest

from kicad2ems.mom25d.multilayer import Layer, LayerStack
from kicad2ems.mom25d.sim import Simulation, Via, ViaPort


def _sim(solver='blr', dmax=0.25e-3, sides=None):
    """A strip on M2 over a plane on M1 (an antipad round a via to the ground),
    a via port at each end. sides: Simulation's (thick copper's sides)."""
    from shapely.geometry import box, Point
    st = LayerStack([Layer(0.2e-3, 4.3, 0.02), Layer(0.2e-3, 4.3, 0.02)])
    w, L = 0.35e-3, 8e-3
    v1 = Via(L / 2, 0, 0, 2, 0.1e-3)
    geo2 = [box(-L / 2, -w / 2, L / 2, w / 2), Point(L / 2, 0).buffer(0.25e-3)]
    geo1 = [
        box(-L / 2, -2e-3, L / 2 + 1e-3,
            2e-3).difference(Point(L / 2, 0).buffer(0.5e-3))
    ]
    sim = Simulation(st, {1: geo1, 2: geo2},
                     [ViaPort(Via(-L / 2, 0, 1, 2, 0.1e-3)),
                      ViaPort(v1)], dmax=dmax, dmax_layer={1: 0.6e-3},
                     w_ref={2: w, 1: 1e-3}, solver=solver, precision='double',
                     verbose=False, sides=sides)
    sim.gpu, sim.loop_tree = 'off', 'off'
    return sim


def test_the_compiled_blr_solves_as_the_direct_solver():
    """The compiled block low-rank factor gives the direct solver's S."""
    pytest.importorskip('kicad2ems.mom25d._core')
    f = 4e9
    ref = _sim('direct').sparams(f)
    sim = _sim()
    sim.blr_tile, sim.blr_tol, sim.blr_eta = 128, 1e-3, 0.7
    S = sim.sparams(f)
    assert (not any('did not converge' in n
                    for n in sim.log.notes)), sim.log.notes
    assert np.abs(S - ref).max() < 1e-6
    st, N = sim.fast.blr.stats, _sim().Ne + _sim().Nv
    assert st['lowrank'] > 0 and st['singular'] == 0
    assert st['bytes'] < 8.0 * N * N  # less than a complex64 matrix


def test_the_blr_factor_has_the_sides_functions_rows():
    """With thick copper's sides (their functions after the vias') the factor
    has every unknown's row (it had lacked the sides': 'R must have N rows')
    and gives the direct solver's S."""
    pytest.importorskip('kicad2ems.mom25d._core')
    sides = dict(t={1: 35e-6, 2: 35e-6}, up={1: False, 2: True})
    ref = _sim('direct', sides=sides).sparams(4e9)
    sim = _sim(sides=sides)
    assert sim.sides is not None and len(sim.sides) > 0
    S = sim.sparams(4e9)
    assert np.abs(S - ref).max() < 1e-6


def test_a_smaller_eta_compresses_more_tiles_to_the_same_answer():
    """The admissibility (gap > eta x the larger cluster's diameter): a smaller
    eta compresses tiles nearer each other, the answer the same (GMRES's).
    (Compressing the tiles beside the diagonal too, eta < 0, leaves no
    preconditioner on tiles of 128: GMRES did not converge in 500
    iterations; at the defaults, tiles of 512 to 1e-5, it is the default:
    sim.blr_eta -1.)"""
    pytest.importorskip('kicad2ems.mom25d._core')
    f = 4e9
    ref = _sim('direct').sparams(f)
    low = {}
    for eta in (0.7, 0.3):
        sim = _sim()
        sim.blr_tile, sim.blr_tol, sim.blr_eta = 128, 1e-3, eta
        S = sim.sparams(f)
        assert np.abs(S - ref).max() < 1e-6, eta
        assert (not any('did not converge' in n
                        for n in sim.log.notes)), sim.log.notes
        low[eta] = sim.fast.blr.stats['lowrank']
    assert low[0.3] > low[0.7]


def test_the_factor_inverts_the_matrix_to_its_tolerance():
    """M Z ~ I: the compiled factor applied to Z x gives x back, as near as the
    compression and complex64 allow (this Z's condition number is ~2e6: 1e-2 at
    any tolerance below 1e-6)."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d.fast import cluster_tiles
    from kicad2ems.mom25d.fast import _CompiledBLR
    sim = _sim()
    fo = sim.fast
    f = 3e9
    fo.setup(f, mode='sparse')
    Sx = fo.extra_blocks(f)
    Ne = sim.Ne
    dZx = (fo.dZ + Sx[:Ne, :Ne]).tocsr()
    tiles, boxes = cluster_tiles(fo._points(), 128)
    raw = fo._raw_entry(dZx)
    N = Ne + sim.Nv
    allu = np.arange(N)
    Z = raw(allu, allu)
    dsc = 1.0 / np.sqrt(np.abs(np.diagonal(Z)))
    Zs = Z * dsc[:, None] * dsc[None, :]
    # (at 1e-3 a rough preconditioner: 0.62 on the hex mesh, 2026-11-15; 0.013
    # at 1e-6)
    for tol, err in ((1e-3, 0.8), (1e-6, 0.05)):
        blr = _CompiledBLR(fo, tiles, boxes, dZx, dsc, tol, 0.7)
        x = np.random.default_rng(3).standard_normal((N, 2)) + 0j
        y = blr(Zs @ x)
        got = np.linalg.norm(y - x) / np.linalg.norm(x)
        assert got < err, tol


def test_a_factor_too_loose_to_precondition_is_made_again_tighter(monkeypatch):
    """A factor GMRES stalls with (fast.BLR_STALL) is factored again at a
    hundredth of the tolerance, kept for the frequencies after; the answer the
    direct solver's."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d import fast
    monkeypatch.setattr(fast, 'BLR_STALL', (4, 1e-3))
    ref = _sim('direct').sparams(4e9)
    sim = _sim()
    sim.blr_tile, sim.blr_tol = 128, 0.3
    S = sim.sparams(4e9)
    assert np.abs(S - ref).max() < 1e-6
    assert any('factored again at 0.003' in n for n in sim.log.notes)
    assert sim.fast._blr_tol < 1e-3


def test_low_frequency_goes_to_the_loop_tree_basis_or_a_double_factor(
        monkeypatch):
    """Below (k h)^2 = LOOP_TREE_BELOW a complex64 factor of the RWG matrix
    cannot hold the loops: the block low-rank path hands the frequency to the
    loop/tree basis's dense solve, and where that has no room factors in double
    precision, compressed to a hundredth of (k h)^2. Both give the direct
    solver's answer."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d import fast
    f = 2e6
    ref_sim = _sim('direct')
    ref_sim.loop_tree = 'auto'
    ref = ref_sim.sparams(f)
    sim = _sim()
    sim.loop_tree, sim.blr_tile = 'auto', 128
    assert sim.fast.kh2(f) < fast.LOOP_TREE_BELOW
    S = sim.sparams(f)
    assert sim.fast._basis_name == 'loop/tree' and np.abs(S - ref).max() < 1e-6
    assert any('in the loop/tree basis, dense' in n for n in sim.log.notes)
    # no room for it
    monkeypatch.setattr(fast.FastOperator, '_lt_block',
                        lambda self: 'no room')
    sim = _sim()
    sim.loop_tree, sim.blr_tile = 'auto', 128
    S = sim.sparams(f)
    assert any('factor in double precision' in n for n in sim.log.notes)
    assert (np.abs(S - ref).max() < 1e-5
            and not any('did not converge' in n for n in sim.log.notes))


def test_an_unconverged_block_low_rank_solve_is_an_error_not_an_answer(
        monkeypatch):
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d import fast
    def stalled(self, A, B, M, restart, maxiter, stall=None):
        self.iters = [30] * B.shape[1]
        return np.zeros(B.shape, complex), False, 0.7
    monkeypatch.setattr(fast.FastOperator, '_refine', stalled)
    sim = _sim()
    sim.blr_tile = 128
    with pytest.raises(FloatingPointError, match='did not converge'):
        sim.sparams(4e9)


def test_the_near_field_built_a_piece_at_a_time_is_the_whole(monkeypatch):
    """The sparse near-field correction built a piece of near pairs at a time
    (near.near_sparse: all the triplets at once had peaked at ~400 bytes a
    pair, 29.6 GB on a real board's layer) is SciPy's sum of all of them, at
    the frequency that builds its structure and at one that reuses it; and so
    is the per-pair via-fan part."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from scipy import sparse
    from kicad2ems.mom25d import near
    sim = _sim()
    fo = sim.fast
    pieces = max(1, len(fo.nP) // 7)  # seven pieces and a bit
    monkeypatch.setattr(near, 'NEAR_PAIRS', pieces)
    for k, f in enumerate((3e9, 4.5e9)):
        fo.setup(f, mode='sparse')
        assert fo._near_pat[0] == ('pieces', len(fo.nP), sim.Ne, pieces)
        assert len(fo._near_pat[1]) == -(-len(fo.nP) // pieces) >= 7
        got, qc = fo.dZ, fo._qcorr
        ri, ci, vv, qref = fo.core.near_coo(fo.g, fo.tb, fo.q, fo.nP32,
                                            fo.nQ32, fo.w)
        m = ri >= 0
        ref = sparse.coo_matrix((vv[m], (ri[m], ci[m])),
                                shape=(sim.Ne, sim.Ne)).tocsr()
        scale = abs(ref).max()
        assert abs(got - ref).max() < 1e-12 * scale, f
        assert np.allclose(qc, qref, rtol=1e-12, atol=0)


def _equilibrated(sim, f):
    """The block low-rank solve's pieces at f: its operator's inputs and the
    equilibrated dense matrix."""
    from kicad2ems.mom25d.fast import cluster_tiles
    fo = sim.fast
    fo.setup(f, mode='sparse')
    Sx = fo.extra_blocks(f)
    Ne = sim.Ne
    dZx = (fo.dZ + Sx[:Ne, :Ne]).tocsr()
    tiles, boxes = cluster_tiles(fo._points(), 128)
    N = Ne + sim.Nv
    allu = np.arange(N)
    Z = fo._raw_entry(dZx)(allu, allu)
    dsc = 1.0 / np.sqrt(np.abs(np.diagonal(Z)))
    return fo, tiles, boxes, dZx, dsc, Z * dsc[:, None] * dsc[None, :]


def test_the_blr_operator_is_the_matrix():
    """The tiles assembled and not factored (factor=False, every
    off-diagonal tile tried): the equilibrated matrix's products to its
    tolerance, in less than a dense complex128 matrix; past a byte budget
    the assembly stops (over)."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d.fast import _CompiledBLR
    sim = _sim()
    fo, tiles, boxes, dZx, dsc, Zs = _equilibrated(sim, 4e9)
    N = len(dsc)
    x = np.random.default_rng(4).standard_normal((N, 3)) + 0j
    for tol, err in ((1e-8, 1e-7), (1e-4, 1e-3)):
        op = _CompiledBLR(fo, tiles, boxes, dZx, dsc, tol, -1.0, double=True,
                          factor=False)
        assert not op.over and op.stats['lowrank'] > 0
        got = np.linalg.norm(op.matvec(x) - Zs @ x) / np.linalg.norm(Zs @ x)
        assert got < err, tol
        assert op.memory < 16.0 * N * N
    op = _CompiledBLR(fo, tiles, boxes, dZx, dsc, 1e-8, -1.0, double=True,
                      factor=False, max_bytes=1e5)
    assert op.over


@pytest.mark.parametrize('case', ['compressed', 'half', 'exact'])
def test_the_blr_solves_operator(monkeypatch, case):
    """GMRES's operator in the block low-rank solve: compressed (BLR_OP_TOL),
    the dense half where compressed it would be larger (a tolerance it
    hardly compresses at), the exact matrix-free products where neither fits
    (BLR_OP_ROOM); each the direct solver's answer."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d import fast
    f = 4e9
    ref = _sim('direct').sparams(f)
    if case == 'compressed':  # (this small model's is larger than its half)
        monkeypatch.setattr(fast.Half, 'bytes',
                            staticmethod(lambda n, nb=256, itemsize=8: 1e12))
    if case == 'half':
        monkeypatch.setattr(fast, 'BLR_OP_TOL', 1e-15)
    if case == 'exact':
        monkeypatch.setattr(fast, 'BLR_OP_ROOM', 0.0)
    sim = _sim()
    sim.blr_tile = 128
    S = sim.sparams(f)
    assert np.abs(S - ref).max() < 1e-6
    notes = ' '.join(sim.log.notes)
    assert ('BLR operator: compressed it would outgrow' in notes) == (
        case == 'half')
    assert ('the exact matrix-free products' in notes) == (case == 'exact')
    assert (any(r[0] == 'BLR operator' for r in sim.log.records)
            == (case != 'exact'))


def test_tiles_by_copper_split_no_piece_with_another():
    """tiles.components / cluster_groups: every unknown in one tile, a
    tile no more than the size asked; a piece of copper at least half a tile
    in tiles of its own; the solve with them (blr_tiles 'copper') the direct
    solver's."""
    pytest.importorskip('kicad2ems.mom25d._core')
    from kicad2ems.mom25d.tiles import cluster_groups, components
    sim = _sim()
    pts = sim.fast._points()
    comp = components(sim, pts)
    assert len(np.unique(comp)) >= 2      # (the strip, the plane)
    tiles, boxes = cluster_groups(pts, comp, 128)
    allu = np.sort(np.concatenate(tiles))
    assert np.array_equal(allu, np.arange(len(pts)))
    assert max(len(t) for t in tiles) <= 128
    big = [c for c in np.unique(comp) if (comp == c).sum() >= 64]
    for t in tiles:
        if any(comp[t[0]] == c for c in big):
            assert len(np.unique(comp[t])) == 1
    ref = _sim('direct').sparams(4e9)
    sim.blr_tiles, sim.blr_tile = 'copper', 128
    assert np.abs(sim.sparams(4e9) - ref).max() < 1e-6
