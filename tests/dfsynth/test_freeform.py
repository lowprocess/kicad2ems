"""dfsynth.freeform: the pixel grid's matrix, a pattern's exact response from
it and after a change, the goal and the search, the pattern as a Design."""
import json

import numpy as np
import pytest

pytest.importorskip('kicad2ems.mom25d._core')

from kicad2ems.dfsynth.microstrip import Substrate
from kicad2ems.dfsynth.topologies import Spec, build
from kicad2ems.dfsynth import freeform as ff, kicad
from kicad2ems.dfsynth.validate import read_touchstone
from kicad2ems.dfsynth.freeform import seeds
from kicad2ems.dfsynth.freeform.grid import PixelGrid, y_to_s
from kicad2ems.dfsynth.freeform.pivot import (State, Pivoted, transform,
                                              pivot)
from kicad2ems.dfsynth.freeform.search import (Goal, search, perimeter,
                                               corner_contacts,
                                               symmetric_groups)
from kicad2ems.dfsynth.freeform.layout import rectangles, design, response

SUB = Substrate.preset('RO4003C-0.813')
FS = [3e9, 5e9, 7e9]


@pytest.fixture(scope='module')
def grid():
    return PixelGrid(SUB, 10, 8, 0.6, 2)


def pattern(grid, seed=1):
    rng = np.random.default_rng(seed)
    m = rng.random(grid.shape) > 0.45
    for side in (0, 1):
        for i, j in grid.window(side):
            m[i, j] = True
    return m


def test_a_pattern_is_the_full_grids_matrix_without_its_absent_edges(grid):
    """The pattern solved by mom25d on its own (the same pixels meshed alone)
    against the full box's matrix with the pattern's rows and columns: the
    same S, to rounding."""
    from kicad2ems.mom25d.sim import Simulation
    m = pattern(grid)
    p = grid.pitch * 1e-3
    sim0 = grid.sim
    rects = [(i * p, j * p, (i + 1) * p, (j + 1) * p)
             for i in range(grid.feed_l, grid.feed_l + grid.nx)
             for j in range(grid.ny) if m[i - grid.feed_l, j]]
    for side, r0 in enumerate(grid.rows):
        i0 = 0 if side == 0 else grid.feed_l + grid.nx
        rects += [(i * p, j * p, (i + 1) * p, (j + 1) * p)
                  for i in range(i0, i0 + grid.feed_l)
                  for j in range(r0, r0 + grid.feed_w)]
    own = Simulation(sim0.st, {1: rects}, sim0.ports, dmax=p,
                     mesher='tensor', ncross=1, grade=0.0, metal=sim0.metal,
                     verbose=False, gpu='off', precision='double',
                     solver='direct')
    assert own.Ne == grid.active(m).sum() - 2
    S = grid.sparams(m, FS)
    for k, f in enumerate(FS):
        assert np.abs(own.sparams(f) - S[k]).max() < 1e-8
    assert np.abs(S[:, 0, 1] - S[:, 1, 0]).max() < 1e-10       # reciprocal
    assert (np.abs(S) ** 2).sum(1).max() < 1.0 + 1e-9          # and passive


def test_the_full_box_is_mom25ds_own_solution(grid):
    S = grid.sparams(np.ones(grid.shape, bool), [5e9])[0]
    assert np.abs(grid.sim.sparams(5e9) - S).max() < 1e-9
    Z = grid.matrix(5e9)
    assert np.abs(Z - Z.T).max() < 1e-12 * np.abs(Z).max()


def test_a_line_between_the_feeds_is_matched():
    """A through line of the feeds' width: the lumped ports in their vias
    cost little (-25 dB or better at 5 GHz)."""
    g = PixelGrid(SUB, 12, 8, 0.445, 4)
    S = g.sparams(seeds.through(g), [1e9, 5e9])
    assert (20 * np.log10(np.abs(S[:, 0, 0]))).max() < -25
    assert (20 * np.log10(np.abs(S[:, 1, 0]))).min() > -0.15


def test_pivot_is_its_own_inverse_and_the_transform_by_parts():
    rng = np.random.default_rng(0)
    n = 30
    Z = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    Z = Z + Z.T + 8 * np.eye(n)
    act = rng.random(n) > 0.5
    P = transform(Z, act)
    a = np.flatnonzero(act)
    assert np.abs(P[np.ix_(a, a)]
                  - np.linalg.inv(Z[np.ix_(a, a)])).max() < 1e-12
    B = np.array([1, 7, 12, 20])
    Q = P.copy(order='F')
    pivot(Q, B)
    act2 = act.copy()
    act2[B] ^= True
    assert np.abs(Q - transform(Z, act2)).max() < 1e-10
    pivot(Q, B)
    assert np.abs(Q - P).max() < 1e-10
    # kept as M - U V: the same after pivots held back, and once written
    R = Pivoted(P.copy(order='F'), hold=6)
    for B in ([1, 7, 12, 20], [3], [7, 8], [20, 21, 22], [0]):
        R.pivot(B)
        act[B] ^= True
        want = transform(Z, act)
        i, j = np.meshgrid(np.arange(n), np.arange(n), indexing='ij')
        assert np.abs(R.take(i, j) - want).max() < 1e-9
    assert np.abs(R.array() - want).max() < 1e-9 and R.r == 0


@pytest.mark.parametrize('symmetry', ['none', 'mirror', 'rotate'])
def test_every_moves_response_is_exact(grid, symmetry):
    """The admittance State.trial gives for each move against the pattern
    with that move made, solved from scratch; and the state after moves
    against its pattern from scratch. A symmetric pattern's state is two
    systems of half the size (its even and odd currents): the matrix's own
    mirror images agree to about 1e-5 in S (its quadrature), and so do the
    halves with the whole system."""
    tol = 1e-8 if symmetry == 'none' else 1e-4
    m = pattern(grid, 2)
    g = grid
    if symmetry == 'mirror':
        m = m | m[::-1]
    if symmetry == 'rotate':
        g = PixelGrid(SUB, 10, 8, 0.6, 2, rows=(1, 5))
        m = pattern(g, 2)
        m = m | m[::-1, ::-1]
    groups = symmetric_groups(g, symmetry)
    st = State(g, FS, m, groups, symmetry)
    assert len(st.systems) == (1 if symmetry == 'none' else 2)
    assert sum(s.n for s in st.systems) == g.N
    assert np.abs(y_to_s(st.y()) - g.sparams(m, FS)).max() < tol
    Y = st.trial()
    rng = np.random.default_rng(3)
    for q in rng.choice(len(groups), 6, replace=False):
        m2 = m.copy()
        m2.ravel()[groups[q]] ^= True
        assert np.abs(y_to_s(Y[:, q]) - g.sparams(m2, FS)).max() < tol
    for q in rng.choice(len(groups), 12, replace=False):
        st.apply(q)
    assert st.made == 12
    assert np.abs(y_to_s(st.y()) - g.sparams(st.mask, FS)).max() < tol
    st.add_frequency(4e9)
    assert np.abs(y_to_s(st.y())[-1]
                  - g.sparams(st.mask, [4e9])[0]).max() < tol


def test_a_mesh_without_the_symmetry_is_one_system():
    """An odd number of cells between the ports: the triangles' diagonals
    are not each other's mirror images, and the pattern's pairs of pixels
    are moved in the one whole system (as exact, twice the work)."""
    g = PixelGrid(SUB, 9, 8, 0.6, 2)
    assert len(g.systems('mirror')) == 1
    m = pattern(g, 6)
    m = m | m[::-1]
    groups = symmetric_groups(g, 'mirror')
    st = State(g, FS[:1], m, groups, 'mirror')
    Y = st.trial()
    m2 = m.copy()
    m2.ravel()[groups[7]] ^= True
    assert np.abs(y_to_s(Y[:, 7]) - g.sparams(m2, FS[:1])).max() < 1e-8


def test_symmetry_needs_the_feeds_to_have_it(grid):
    # (feeds on the middle rows have both)
    assert len(symmetric_groups(grid, 'rotate')) == 40
    off = PixelGrid(SUB, 10, 8, 0.6, 2, rows=(1, 1))
    with pytest.raises(ValueError, match='rotate'):
        symmetric_groups(off, 'rotate')
    g = symmetric_groups(off, 'mirror')
    assert sorted(q for grp in g for q in grp) == list(range(80))
    assert ff.default_symmetry(None, off) == 'mirror'
    assert ff.default_symmetry(
        None, PixelGrid(SUB, 10, 8, 0.6, 2, rows=(1, 3))) == 'none'


def test_goal_limits_follow_the_prototype():
    spec = Spec('bandpass', 'freeform', 5e9, 500e6, order=3)
    g = Goal(spec)
    assert g.pas.sum() == 9 and g.stop.sum() == 16
    assert g.f[g.pas].min() == pytest.approx(4.756e9, rel=1e-3)
    assert g.f[g.pas].max() == pytest.approx(5.256e9, rel=1e-3)
    # deeper away from the band, down to the floor
    lo = g.att[g.stop & (g.f < 5e9)]
    assert (np.diff(lo) <= 1e-9).all() and lo[0] == 35.0 and 3 <= lo[-1] < 5
    # an ideal response meets it; a through line does not
    S = np.zeros((len(g.f), 2, 2), complex)
    S[g.pas, 1, 0] = S[g.pas, 0, 1] = 1.0
    S[g.stop, 0, 0] = S[g.stop, 1, 1] = 1.0
    assert g.loss(S) == 0.0
    T = np.zeros_like(S)
    T[:, 1, 0] = T[:, 0, 1] = 1.0
    assert g.loss(T) > 100
    lp = Goal(Spec('lowpass', 'freeform', 2e9, order=5))
    assert lp.f[lp.pas].max() == 2e9 and lp.f[lp.stop].min() > 2e9
    # drawn again: the same bands, the skirts' own samples kept
    f0 = g.f.copy()
    g.resample(np.random.default_rng(0))
    assert len(g.f) == len(f0) and np.array_equal(g.f[g.pas], f0[g.pas])
    assert not np.array_equal(g.f, f0)
    assert g.f[g.stop & (g.f < 5e9)].max() == f0[g.stop & (f0 < 5e9)].max()
    assert g.loss(S) == 0.0


def test_outline_and_corner_contacts(grid):
    m = np.zeros(grid.shape, bool)
    assert perimeter(grid, m) == 2 * grid.feed_w        # the feeds' ends
    m[4, 1] = True
    assert perimeter(grid, m) == 2 * grid.feed_w + 4
    assert corner_contacts(grid, m) == 0
    m[5, 2] = True
    assert corner_contacts(grid, m) == 1
    m[5, 1] = True
    assert corner_contacts(grid, m) == 0
    # a pixel at a feed's corner
    m[:] = False
    m[0, grid.rows[0] - 1] = True
    assert corner_contacts(grid, m) == 1


def test_search_lowers_the_cost_and_joins_no_corners(grid):
    spec = Spec('lowpass', 'freeform', 4e9, order=3)
    goal = Goal(spec, n_pass=3, n_stop=2)
    m = seeds.through(grid)
    st = State(grid, goal.f, m, symmetric_groups(grid, 'mirror'), 'mirror')
    c0 = float(goal.loss(y_to_s(st.y())))
    best, c = search(st, goal, steps=25, tabu=5)
    assert c < c0 and np.array_equal(best, st.mask)
    assert corner_contacts(grid, best) == 0
    assert np.array_equal(best, best[::-1])             # still a mirror image
    assert float(goal.loss(grid.sparams(best, goal.f))) == pytest.approx(
        c, abs=1e-6)
    # without tabu: it stops where no move improves
    st.set(m)
    b2, c2 = search(st, goal, steps=500)
    assert c2 < c0 and st.count < 500
    # and a search repeats (its linear algebra on one thread)
    st.set(m)
    b3, c3 = search(st, goal, steps=25, tabu=5)
    assert np.array_equal(b3, best) and c3 == pytest.approx(c, abs=1e-9)


def test_rectangles_cover_the_pattern_once(grid):
    m = pattern(grid, 5)
    cover = np.zeros(grid.shape, int)
    for i0, j0, i1, j1 in rectangles(m):
        cover[i0:i1, j0:j1] += 1
    assert np.array_equal(cover, m.astype(int))
    assert len(rectangles(np.ones((6, 4), bool))) == 1


def test_start_patterns():
    spec = Spec('bandpass', 'freeform', 5e9, 500e6, order=3)
    g = ff.make_grid(spec, SUB)
    assert g.pitch == pytest.approx(SUB.width(50.0) / 4) and g.feed_w == 4
    m = ff.start_pattern(spec, g)
    assert corner_contacts(g, m) == 0 and np.array_equal(m, m[::-1])
    assert ff.default_symmetry(spec, g) == 'mirror'
    # three loops: its pass band near f0 (within 15 %)
    f = np.linspace(4e9, 6.5e9, 51)
    s21 = np.abs(response(g, m, f)[:, 1, 0])
    assert s21.max() > 0.8 and abs(f[s21.argmax()] / 5e9 - 1) < 0.15
    # an even order: the last loop upside down, a half turn's symmetry
    spec4 = Spec('bandpass', 'freeform', 5e9, 500e6, order=4)
    g4 = ff.make_grid(spec4, SUB)
    m4 = ff.start_pattern(spec4, g4)
    assert ff.default_symmetry(spec4, g4) == 'rotate'
    assert np.array_equal(m4, m4[::-1, ::-1])
    # a box too narrow for the loops is said by the start pattern
    narrow = ff.make_grid(spec, SUB, box=(5.0, 10.0))
    with pytest.raises(ValueError, match='pixels of width'):
        ff.start_pattern(spec, narrow)
    with pytest.raises(ValueError, match='fabrication minimum'):
        ff.make_grid(spec, SUB, pitch=0.05)


def test_the_design_is_dfsynths(tmp_path, grid):
    """A pattern as a Design: its board's copper is the pattern's and the
    feeds', its model the pixel grid's, and dfsynth's writers take it."""
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
    from kicad2ems.dfsynth.calibrate import model_response
    spec = Spec('lowpass', 'freeform', 4e9, order=3)
    m = pattern(grid, 4)
    d = design(spec, grid, m)
    geo = d.geometry
    cu = unary_union([Polygon(p) for p in geo.polys])
    feed = 5.0
    assert cu.area == pytest.approx(
        m.sum() * grid.pitch ** 2 + 2 * feed * grid.feed_w * grid.pitch)
    assert geo.W == pytest.approx(2 * feed + grid.nx * grid.pitch)
    assert [p[0] for p in geo.ports] == [1, 2]
    assert cu.buffer(1e-6).contains(
        Point(geo.ports[0][1] + 0.1, geo.ports[0][2]))
    # the model on a sweep (a rational fit between solved frequencies)
    # against the pattern solved at 5 GHz
    s11, s21 = model_response(d, np.linspace(3e9, 7e9, 41))
    S = grid.sparams(m, [5e9])[0]
    assert abs(abs(s21[20]) - abs(S[1, 0])) < 5e-3
    assert abs(abs(s11[20]) - abs(S[0, 0])) < 5e-3
    pcb, cfg = kicad.write(d, str(tmp_path / 'ff'))
    text = open(pcb).read()
    assert text.count('(gr_poly') == len(geo.polys)
    assert 'fast' not in open(cfg).read()
    assert (tmp_path / 'ff.kicad_mod').exists()
    with pytest.raises(ValueError, match='searched for'):
        build(spec, SUB)


def test_frequency_map_from_band_edges():
    spec = Spec('bandpass', 'freeform', 5e9, 500e6, order=3)
    f = np.linspace(3e9, 7e9, 801)
    bp = lambda f0, bw: 1 / np.sqrt(1 + ((f / f0 - f0 / f) / (bw / f0)) ** 6)
    a, b = ff.frequency_map(spec, f, bp(5.2e9, 600e6), bp(5.0e9, 500e6))
    # the full-wave band (5.2 GHz, 600 MHz wide) is the model's (5.0, 500)
    assert a + b * 5.2e9 == pytest.approx(5.0e9, rel=2e-3)
    assert b == pytest.approx(500 / 600, rel=0.02)
    assert ff.frequency_map(spec, f, np.full(len(f), 1e-3),
                            bp(5e9, 5e8)) is None
    lp = Spec('lowpass', 'freeform', 4e9, order=3)
    f = np.linspace(0.1e9, 8e9, 801)
    cut = lambda fc: 1 / np.sqrt(1 + (f / fc) ** 6)
    assert ff.frequency_map(lp, f, cut(4.4e9), cut(4e9)) == pytest.approx(
        (0.0, 4 / 4.4), rel=2e-3)


def test_rescale_keeps_the_pattern_and_moves_the_band(grid):
    """The first tuning round: pixels 5 % larger, the same pattern, the
    response 5 % lower in frequency (near enough: the substrate's height
    stays)."""
    spec = Spec('lowpass', 'freeform', 4e9, order=3)
    m = seeds.through(grid)
    m[3:7, 1:7] = True                      # a patch: a low-pass of a kind
    d = design(spec, grid, m, ['a', 'b'])
    d.freeform = dict(loss=1.0, fmap=(0.0, 1.0))
    r = ff.rescale(d, 1.05)
    assert r.grid.pitch == pytest.approx(1.05 * grid.pitch)
    assert np.array_equal(r.mask, m) and r.grid.shape == grid.shape
    assert r.geometry.W - 10.0 == pytest.approx(1.05 * (d.geometry.W - 10.0))
    assert 'scaled by 1.0500' in r.notes[-1]
    # the scaled board's |S21| at f / s is the first's at f for s = 1.05,
    # of the factors tried
    f = np.linspace(2e9, 12e9, 201)
    a0 = np.abs(response(grid, m, f)[:, 1, 0])
    a1 = np.abs(response(r.grid, m, f)[:, 1, 0])
    sel = f < 11e9
    miss = {k: np.abs(np.interp(f[sel] / k, f, a1) - a0[sel]).max()
            for k in (1.0, 1.025, 1.05, 1.075, 1.1)}
    assert min(miss, key=miss.get) == 1.05 and a0.min() < 0.9
    assert ff.rescale(d, 0.1) is None       # under the fabrication minimum
    # where a band is: a low-pass's cut-off, a band-pass's edges' mean
    cut = 1 / np.sqrt(1 + (f / 5e9) ** 6)
    assert ff.centre(spec, f, cut) == pytest.approx(5e9, rel=5e-3)
    bp = Spec('bandpass', 'freeform', 6e9, 600e6, order=3)
    band = 1 / np.sqrt(1 + ((f / 6e9 - 6e9 / f) / 0.1) ** 6)
    assert ff.centre(bp, f, band) == pytest.approx(6e9, rel=5e-3)
    assert ff.centre(bp, f, np.full(len(f), 1e-3)) is None


def test_level_offsets_and_the_full_wave_cost():
    """What a full-wave result shows over the pixel model is asked of the
    model on top of its limits: the envelopes' difference, never negative,
    capped; and a goal with offsets counts a response that much worse."""
    spec = Spec('bandpass', 'freeform', 5e9, 500e6, order=3)
    f = np.linspace(3.5e9, 6.5e9, 601)
    s21 = 1 / np.sqrt(1 + ((f / 5e9 - 5e9 / f) / 0.1) ** 6)
    good = np.sqrt(np.maximum(1 - s21 ** 2, 0)) * 0.1 + 1e-3
    # the full-wave match 6 dB worse in the band's upper half
    bad = good * np.where((f > 5e9) & (f < 5.25e9), 2.0, 1.0)
    fo, d11, d21 = ff.level_offsets(spec, f, (s21, bad), (s21, good),
                                    (0.0, 1.0))
    assert d11.min() == 0.0 and d21.max() < 1e-9
    assert d11[np.abs(f - 5.12e9).argmin()] == pytest.approx(6.02, abs=0.3)
    assert d11[np.abs(f - 4.8e9).argmin()] == 0.0
    huge = ff.level_offsets(spec, f, (s21, good * 100), (s21, good),
                            (0.0, 1.0))
    assert huge[1].max() == ff.OFFSET_MAX[0]
    g0, g1 = Goal(spec), Goal(spec, offset=(fo, d11, d21))
    S = np.zeros((len(g0.f), 2, 2), complex)
    S[:, 0, 0] = S[:, 1, 1] = 10 ** (-16 / 20)      # 16 dB: 15 is asked
    S[:, 1, 0] = S[:, 0, 1] = np.where(g0.pas, 0.97, 1e-3)
    assert g0.loss(S) == 0.0 and g1.loss(S) > 1.0
    assert len(g1.limits(f)) == 5

    class D:                                        # (a design's goal)
        pass
    D.spec, D.freeform = spec, dict(goal=g0)
    ideal = np.sqrt(np.maximum(1 - s21 ** 2, 1e-12))
    c_good = ff.fullwave_cost(D, f, s21, ideal)
    c_bad = ff.fullwave_cost(D, f, s21, np.maximum(ideal, 0.5))
    assert c_bad > c_good + 10


def test_cli_freeform(tmp_path, capsys):
    """--topology freeform end to end, a short search in a small box: the
    board, the model's Touchstone, the pattern in the design's JSON."""
    from kicad2ems.dfsynth.cli import main
    base = str(tmp_path / 'lp')
    assert main(['--type', 'lowpass', '--topology', 'freeform', '--f0', '4G',
                 '--order', '3', '--substrate', 'RO4003C-0.813', '--box',
                 '6x4.8', '--pixel', '0.6', '--search-steps', '20', '-o',
                 base]) == 0
    out = capsys.readouterr().out
    assert 'free-form' in out and 'search:' in out
    d = json.load(open(base + '_design.json'))
    fr = d['freeform']
    assert fr['pixels'] == [10, 8] and len(fr['pattern']) == 8
    assert all(len(r) == 10 and set(r) <= {'#', '.'} for r in fr['pattern'])
    assert d['spec']['topology'] == 'freeform'
    assert open(base + '.kicad_pcb').read().count('(gr_poly') >= 3
    f, S = read_touchstone(base + '_model.s2p')
    assert len(f) == 801 and np.isfinite(S).all()
    assert main(['--type', 'bandstop', '--topology', 'freeform', '--f0',
                 '4G', '--bw', '1G', '-o', base]) == 2
