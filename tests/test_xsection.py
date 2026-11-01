"""The 2D field solver (xsection.py) against exact and closed-form results: Cohn's stripline, single and
coupled; Hammerstad & Jensen's microstrip; Wheeler's thick stripline; grounded CPW; and what the formulas
leave out (solder mask, coplanar ground beside a stripline) in the direction it should go. Its mesh against the patch
test (a linear potential is an exact solution, whatever the hanging nodes), the exact result as it converges, and the
tensor grid."""
import json, math, os
import numpy as np
import pytest
from kicad2ems.constants import C0, EPS0
from kicad2ems.lines import xsection as X
from kicad2ems.lines.formulas import microstrip, stripline, cpwg, ellipk
from kicad2ems.lines.coupled import coupled_stripline, coupled_microstrip, mtl_s


def _cohn(w, b, er):
    """Exact, with eta0 / 4: the usual 30 pi is 0.07 % high (c = 3e8)."""
    k = 1 / math.cosh(math.pi * w / (2 * b))
    return 1 / (4 * C0 * EPS0 * math.sqrt(er)) * ellipk(k) / ellipk(math.sqrt(1 - k * k))


def test_stripline_against_cohn():
    z, ee = X.stripline(0.15, 0.2, 0.2, 4.3).solve().single()
    assert z == pytest.approx(_cohn(0.15, 0.4, 4.3), rel=4e-3)
    assert ee == pytest.approx(4.3)                                           # one dielectric: eeff = er


def test_coupled_stripline_against_cohn():
    got = X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3).solve().pair()
    ref = coupled_stripline(0.1, 0.1, 0.4, 4.3)
    assert got[0] == pytest.approx(ref[0], rel=4e-3) and got[1] == pytest.approx(ref[1], rel=4e-3)


@pytest.mark.parametrize('w,h,er,t', [(0.35, 0.2, 4.3, 0.0), (3.0, 1.6, 4.4, 0.0), (0.4, 0.2104, 4.4, 0.035)])
def test_microstrip_against_hammerstad_jensen(w, h, er, t):
    z, ee = X.microstrip(w, h, er, t).solve().single()
    z0, ee0 = microstrip(w, h, er, t)
    assert z == pytest.approx(z0, rel=4e-3) and ee == pytest.approx(ee0, rel=3e-3)


def test_thick_stripline_against_wheeler():
    z, _ = X.stripline(0.15, 0.2, 0.2, 4.3, t=0.035).solve().single()
    assert z == pytest.approx(stripline(0.15, 0.435, 4.3, 0.035)[0], rel=4e-3)


def test_grounded_cpw_against_conformal_mapping():
    z, ee = X.microstrip(0.5, 1.6, 4.4, s=0.5).solve().single()
    z0, ee0 = cpwg(0.5, 0.5, 1.6, 4.4)
    assert z == pytest.approx(z0, rel=5e-3) and ee == pytest.approx(ee0, rel=3e-3)


def test_coupled_microstrip_and_the_matrices():
    r = X.coupled_microstrip(0.15, 0.15, 0.2, 4.4).solve()
    ze, zo, ee, eo = r.pair()
    ref = coupled_microstrip(0.15, 0.15, 0.2, 4.4)
    assert ze == pytest.approx(ref[0], rel=1e-2) and zo == pytest.approx(ref[1], rel=1e-2)
    assert eo < ee
    assert np.allclose(r.C, r.C.T) and r.C[0, 1] < 0 < r.C[0, 0]             # Maxwell form
    assert r.C[0, 0] == pytest.approx(r.C[1, 1], rel=1e-6)                   # the pair is symmetric
    S = mtl_s(r.C, r.Ca, 4.4, 0.0, 10.0, [1e9])                              # what the line model's MTL takes
    assert S.shape == (1, 4, 4) and np.allclose(S[0] @ S[0].conj().T, np.eye(4), atol=1e-9)   # lossless


def test_what_the_formulas_leave_out():
    bare, _ = X.microstrip(0.35, 0.2, 4.3, 0.035).solve().single()
    masked, _ = X.microstrip(0.35, 0.2, 4.3, 0.035, mask=0.02, er_mask=3.8).solve().single()
    assert 0.94 * bare < masked < 0.99 * bare                                # mask lowers Z0 a few percent
    plain, _ = X.stripline(0.15, 0.2, 0.2, 4.3).solve().single()
    side, _ = X.stripline(0.15, 0.2, 0.2, 4.3, s=0.15).solve().single()
    assert side < plain                                                      # coplanar ground the formulas drop


def test_dielectric_loss_by_filling_factor():
    r = X.microstrip(0.35, 0.2, 4.3, tand=0.02).solve()
    _, ee = r.single()
    q = 4.3 * (ee - 1) / (ee * (4.3 - 1))                                    # the filling-factor result
    assert r.tand_eff() == pytest.approx(0.02 * q, rel=2e-2)


def test_conductors_must_not_touch():
    xs = X.CrossSection(dielectrics=[X.Dielectric(0, 0.2, 4.3)], planes=[0.0])
    xs.conductor(0, 0.2, 0.2).conductor(0.2, 0.4, 0.2)
    with pytest.raises(ValueError):
        xs.solve()


# ---- the mesh --------------------------------------------------------------------------------------------
def _random_section(seed):
    """A cut as a real board has them: strips, thick and not, pieces of ground on several levels, sliver gaps, a plane
    or two, a layer of mask."""
    rng = np.random.default_rng(seed)
    heights = np.cumsum(rng.uniform(0.05, 0.5, 4))
    t = float(rng.choice([0.0, 0.018, 0.035]))
    xs = X.CrossSection(dielectrics=[X.Dielectric(0.0, heights[-1], 4.3, 0.02)], planes=[0.0])
    if rng.random() < 0.5:
        xs.planes.append(float(heights[-1]))
    elif rng.random() < 0.5:
        xs.dielectrics.append(X.Dielectric(heights[-1], heights[-1] + 0.01, 3.8))
    z = float(heights[-2])
    x = -float(rng.uniform(0.2, 1.0))
    for _ in range(rng.integers(1, 5)):                                     # signals side by side
        w = float(rng.uniform(0.05, 0.5))
        xs.conductor(x, x + w, z, z + t)
        x += w + float(rng.uniform(0.01, 0.4))
    xs.conductor(x, math.inf, z, z + t, ground=True)
    x = xs.conductors[0].x0 - float(rng.uniform(0.002, 0.3))
    xs.conductor(-math.inf, x, z, z + t, ground=True)
    for level in heights[:-2]:                                              # ground in pieces on the layers below
        x = -float(rng.uniform(1, 3))
        for _ in range(rng.integers(1, 8)):
            w = float(rng.uniform(0.01, 1.0))
            xs.conductor(x, x + w, float(level), ground=True)
            x += w + float(rng.uniform(0.002, 0.5))
    return xs


SECTIONS = [X.stripline(0.15, 0.2, 0.2, 4.3), X.microstrip(0.4, 0.21, 4.4, 0.035), X.stripline(0.15, 0.2, 0.2, 4.3, t=0.035),
            X.microstrip(0.5, 1.6, 4.4, s=0.5), X.coupled_microstrip(0.15, 0.15, 0.2, 4.4),
            X.microstrip([0.6] * 5, 0.8, 4.4, gap=[0.58, 0.74, 0.74, 0.58]),
            X.coupled_microstrip(0.13, 0.25, 0.0994, 4.45, 0.035, mask=0.01, er_mask=3.8),
            X.microstrip(0.3, 0.2, 4.4, s=0.15, wg=0.2)] + [_random_section(k) for k in range(12)]


@pytest.mark.parametrize('xs', SECTIONS)
@pytest.mark.parametrize('coarse', [False, True])
def test_the_patch_test(xs, coarse):
    """A linear potential is an exact solution, so it leaves no residual at any free node, hanging nodes or not. The
    mesh's first prototype failed this: a tolerance finer than the rounding of its nodes hung cells' corners on their
    own sides (0.01-0.03 here; Z0 up to 78 % low)."""
    cell = X._mesh(xs, 2 * X.MESH_GROW, 2 * X.MESH_FINE) if coarse else X._mesh(xs)
    stiffness, own, x, z, _, _ = X._mesh_system(xs, cell)
    K = stiffness(np.ones(len(cell)))
    for a, b in ((1.0, 0.0), (0.0, 1.0), (0.7, -0.4)):
        phi = a * x + b * z
        scale = np.asarray(abs(K).sum(1)).ravel() * np.abs(phi).max()
        assert (np.abs(K @ phi)[own < 0] / scale[own < 0]).max() < 1e-12


def test_hanging_nodes_interpolate():
    """A cell beside a cell quartered along their side, its nearer quarter halved again: the side's nodes hang, nested,
    on the longest side, and T keeps values linear along it."""
    cell = np.array([[0, 1, 0, 1], [1, 2, 0.5, 1], [1, 2, 0, 0.25], [1, 1.5, 0.25, 0.5], [1.5, 2, 0.25, 0.5]], float)
    x, ix = np.unique(cell[:, :2], return_inverse=True)
    z, iz = np.unique(cell[:, 2:], return_inverse=True)
    T, kept, nodes = X._hanging(x, z, ix.reshape(-1, 2), iz.reshape(-1, 2))
    assert np.allclose(np.asarray(T.sum(1)).ravel(), 1)                      # a partition of unity
    px, pz = np.zeros(T.shape[0]), np.zeros(T.shape[0])
    px[nodes.ravel()] = cell[:, [0, 1, 1, 0]].ravel()
    pz[nodes.ravel()] = cell[:, [2, 2, 3, 3]].ravel()
    hanging = np.setdiff1d(np.arange(T.shape[0]), kept)
    assert {(px[k], pz[k]) for k in hanging} >= {(1.0, 0.25), (1.0, 0.5), (1.5, 0.5)}
    assert np.allclose(T @ (2 * px[kept] + 3 * pz[kept]), 2 * px + 3 * pz)


def test_the_mesh_converges_to_the_exact_stripline():
    """Finer corners and grading approach Cohn's exact result, first order in the corners' cell and second in the
    grading; as shipped it is within 0.15 %."""
    xs = X.stripline(0.15, 0.2, 0.2, 4.3)
    exact = _cohn(0.15, 0.4, 4.3)
    err = [abs(X.Result(*X._solve_mesh(xs, X._mesh(xs, grow, fine))[:3]).single()[0] / exact - 1)
           for grow, fine in ((0.4, 1 / 64), (0.2, 1 / 256), (0.08, 1 / 4096))]
    assert err[0] > 2 * err[1] > 4 * err[2] and err[2] < 3e-4
    assert xs.solve().single()[0] == pytest.approx(exact, rel=1.5e-3)


@pytest.mark.parametrize('xs', SECTIONS[:12])
def test_the_mesh_agrees_with_the_grid(xs):
    """Two solvers that share only the charge's sum: the cross-section's whole C and Ca, in fewer nodes."""
    m, g = xs.solve(), X._solve_on_grid(xs)
    n = len(m.C)
    self_ = np.arange(n)
    assert np.allclose(m.C[self_, self_], g.C[self_, self_], rtol=8e-3) and np.allclose(m.Ca[self_, self_], g.Ca[self_, self_], rtol=8e-3)
    if n > 1:
        big = np.abs(g.C) > 0.02 * np.abs(g.C).max()                         # the coupling that matters
        assert np.allclose(m.C[big], g.C[big], rtol=2.5e-2)
    assert m.nodes < g.nodes


@pytest.mark.parametrize('xs', SECTIONS)
def test_maxwell_form(xs):
    """C symmetric, positive on the diagonal, negative off it, and each line's row summing to its capacitance to ground."""
    r = xs.solve()
    assert np.allclose(r.C, r.C.T) and (np.diag(r.C) > 0).all()
    off = r.C - np.diag(np.diag(r.C))
    assert (off <= 1e-3 * np.abs(r.C).max()).all() and (r.C.sum(1) > 0).all()
    assert (r.C >= r.Ca - 1e-20).diagonal().all()                            # the dielectric only adds


def test_refine_off_is_one_solve(monkeypatch):
    xs = X.microstrip(0.35, 0.2, 4.3)
    both = xs.solve()
    monkeypatch.setattr(X, 'REFINE', False)
    one = xs.solve()
    assert one.nodes == both.nodes
    assert both.single()[0] * 0.985 < one.single()[0] < both.single()[0]     # the grading's error: Z0 low


def test_the_tensor_grid_still_solves(monkeypatch):
    monkeypatch.setattr(X, 'MESH', False)
    assert X.stripline(0.15, 0.2, 0.2, 4.3).solve().single()[0] == pytest.approx(_cohn(0.15, 0.4, 4.3), rel=5e-3)


def test_a_dielectric_of_limited_width():
    """Its corners count as the copper's do; a strip over a block of FR-4, the rest air."""
    xs = X.CrossSection(dielectrics=[X.Dielectric(0.0, 0.2, 10.0, 0.0, -0.3, 0.3)], planes=[0.0])
    xs.conductor(-0.15, 0.15, 0.2)
    z, ee = xs.solve().single()
    fine = X.Result(*X._solve_mesh(xs, X._mesh(xs, 0.1, 1 / 1024))[:3]).single()
    assert z == pytest.approx(fine[0], rel=3e-3) and ee == pytest.approx(fine[1], rel=3e-3)


# ---- the line model on the field solver (lines.reference.FIELD) --------------------------------------------
class _Copper:
    """Reference copper as RefCopper has it (geom per layer), without tiles."""
    def __init__(self, geom):
        self.geom = geom


def test_the_cut_across_a_strip():
    from shapely.geometry import box
    from kicad2ems.lines.reference import cut
    ref = _Copper({'B.Cu': box(-50, -50, 50, 50), 'F.Cu': box(0.4, -50, 0.9, 50).union(box(-50, -50, -0.6, 50))})
    got = cut(ref, np.array([0.0, 0.0]), np.array([1.0, 0.0]), 5.0)
    assert got['B.Cu'] == [(-math.inf, math.inf)]                           # a plane across the whole cut
    assert got['F.Cu'] == [(-math.inf, -0.6), (pytest.approx(0.4), pytest.approx(0.9))]


def test_a_plane_edge_beside_the_strip():
    from kicad2ems.lines.reference import _solve_cut
    diel = ((0.0, 0.2, 4.3, 0.0),)
    full = _solve_cut(0.35, 0.2, 0.0, True, (), ((0.0, True, ((-math.inf, math.inf),)),), diel)
    edge = _solve_cut(0.35, 0.2, 0.0, True, (), ((0.0, False, ((-math.inf, 0.3),)),), diel)
    half = _solve_cut(0.35, 0.2, 0.0, True, (), ((0.0, False, ((-math.inf, 0.0),)),), diel)
    assert full[0] == pytest.approx(microstrip(0.35, 0.2, 4.3)[0], rel=4e-3)
    assert 1.015 * full[0] < edge[0] < 1.04 * full[0]                        # the plane ends 0.125 mm past the edge
    assert half[0] > 1.15 * full[0]                                          # it ends under the strip's centre


@pytest.fixture(scope='module')
def field_interdig():
    import os
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import reference
    from kicad2ems.lines.builder import build
    ex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples')
    m = Board(KiCadBoard(os.path.join(ex, 'rf_interdig.kicad_pcb')), nets=['RF', 'GND'],
              pos_file=os.path.join(ex, 'rf_interdig-all.pos'), log=lambda *a: None)
    from kicad2ems.lines import topology
    was, reference.FIELD = reference.FIELD, True
    try:
        lm = build(m)
    finally:
        reference.FIELD = was
    was, topology.FIELD_GROUPS = topology.FIELD_GROUPS, False                 # the formulas throughout
    try:
        plain = build(m)
    finally:
        topology.FIELD_GROUPS = was
    return m, lm, plain, os.path.join(ex, 'reference', 'rf_interdig_openems.s2p')


def test_field_sections_and_groups(field_interdig):
    m, lm, plain, _ = field_interdig
    traces = [e for e in lm.elements if e.kind == 'trace' and e.group is None and e.z0]
    assert traces and all(any(n.startswith('field solver') for n in e.notes) for e in traces)
    by_id = {(e.a, e.b): e for e in plain.elements}
    for e in traces:                                                         # microstrip on one plane: the formulas'
        assert e.z0_sheet == pytest.approx(by_id[(e.a, e.b)].z0_sheet, rel=1e-2)
    g = lm.groups[0]
    assert g['C'].shape == (5, 5) and g['C'][0, 2] < 0                       # coupled beyond the nearest neighbour
    assert 'C' not in plain.groups[0]


def test_field_groups_against_openems(field_interdig):
    """The five-finger filter's stop bands: the formulas' nearest-neighbour coupling puts them 40-60 dB too deep."""
    pytest.importorskip('skrf')
    from kicad2ems.solvers.lines.fastsolve import solve
    from kicad2ems.touchstone import read_touchstone
    m, lm, plain, ref = field_interdig
    f, Sr = read_touchstone(ref)
    db = lambda x: 20 * np.log10(np.abs(x) + 1e-15)
    rms = [float(np.sqrt(np.mean((db(solve(x, m.ports, f, 'skrf')[0][:, 1, 0]) - db(Sr[:, 1, 0])) ** 2)))
           for x in (lm, plain)]
    assert rms[0] < 12 < 40 < rms[1]


def test_coupled_pairs_with_the_copper():
    """A coupled pair's modes by the field solver: as sheets, the formula's; with the copper, the CM5 Minima's HDMI
    pair (0.13 / 0.25 mm, 0.0994 mm over its plane, 35 um): 13 ohm below the zero-thickness 118 ohm."""
    from kicad2ems.lines import topology
    from kicad2ems.lines.coupled import coupled_microstrip as km
    g = dict(xs='microstrip', h=0.0994, er=4.45, gaps=[0.25], pairs=[dict(gap=0.25)])
    topology._field_group(g, [0.13, 0.13], 0.035)
    p = g['pairs'][0]
    assert 2 * p['zo_sheet'] == pytest.approx(2 * km(0.13, 0.25, 0.0994, 4.45)[1], rel=1e-2)
    assert 2 * p['zo'] == pytest.approx(104.8, abs=0.5) and p['ze'] < p['ze_sheet']
    assert g['C'].shape == (2, 2)


def test_similar_cross_sections_share_a_solve():
    from kicad2ems.lines.reference import _distance_bins, _snap_distance
    assert X.snap(0.1300) == X.snap(0.1302) and X.snap(0.13) != X.snap(0.135)   # 1 % steps
    assert X.snap(0.0) == 0.0 and X.snap(-0.2) == -X.snap(0.2) and X.snap(math.inf) == math.inf
    assert abs(X.snap(0.2) / 0.2 - 1) <= X.SIMILAR / 2
    edges = _distance_bins(X.SIMILAR)
    w = np.diff(edges)
    assert (w > 0).all() and w[-1] > 50 * w[np.searchsorted(edges, 0.5)]        # far copper: wide bins
    assert _snap_distance(0.7001, X.SIMILAR) == _snap_distance(0.7002, X.SIMILAR)
    assert _snap_distance(0.7, 0.0) == 0.7


def test_bins_stay_narrow():
    """Far bins grew without limit, and a copper edge 9 heights from a strip's edge moved hundreds of mm: on the CM5 Minima's
    wide traces, conductors reversed and the solve failed (27 sections), and others moved without a word."""
    from kicad2ems.lines.reference import BIN_MAX, FIELD_REACH, _distance_bins, _snap_distance
    assert np.diff(_distance_bins(X.SIMILAR)).max() <= BIN_MAX
    for x in np.linspace(0.0, FIELD_REACH + 5, 400):
        assert abs(_snap_distance(x, X.SIMILAR) - x) <= BIN_MAX / 2 + 1e-9


def _wide_strip(pieces):
    """A section 20 mm wide over a plane in `pieces` on the layer below, through reference._field."""
    from types import SimpleNamespace as NS
    from kicad2ems.lines import reference
    e = NS(w=20.0, layer='F.Cu', notes=[], z0=2.5, eeff=3.0, z0_sheet=2.5, eeff_sheet=3.0, field=False)
    model = NS(cu_z={'F.Cu': 0.1, 'B.Cu': 0.0}, cu_t={'F.Cu': 0.035}, layers=[dict(z0=0.0, z1=0.1, er=4.4, tand=0.0)])
    was, reference.cut = reference.cut, lambda *a: {'B.Cu': pieces}
    try:
        e.cut = reference._cut_args(model, None, e, np.zeros(2), np.array([1.0, 0.0]), {'below': ('B.Cu', 0.1, 4.4)})
        reference._field(e)
    finally:
        reference.cut = was
    return e


@pytest.mark.parametrize('pieces', [[(-math.inf, -9.05), (-8.9, 8.9), (9.05, math.inf)],     # edges 1 mm in from each side
                                    [(-math.inf, -0.5), (0.5, math.inf)],                    # a slot under the middle
                                    [(-math.inf, -0.002), (0.002, math.inf)]])               # the ends meet across it
def test_a_plane_with_holes_under_a_wide_strip(pieces):
    e = _wide_strip(pieces)
    assert not any('failed' in n for n in e.notes) and any(n.startswith('field solver') for n in e.notes)
    assert 0.8 < e.z0_sheet < 1.1                                            # a parallel plate 20 mm / 0.1 mm wide: 0.9 ohm


def test_grounded_cpw_formula_on_a_wide_strip():
    """tanh saturates to 1 past w / h = 24: the formula gave 1e-16 ohm. It should reach the plate under the strip."""
    z, ee = cpwg(6.7, 0.075, 0.0994, 4.45)
    fine = X.microstrip(6.7, 0.0994, 4.45, s=0.075).solve().single()
    assert z == pytest.approx(fine[0], rel=0.03) and ee == pytest.approx(fine[1], rel=0.03)
    for w in (1.0, 2.0, 2.3, 2.6, 3.0, 5.0):                                 # no step where tanh saturates
        assert 0.75 < cpwg(w, 0.15, 0.0994, 4.45)[0] * w / (cpwg(1.0, 0.15, 0.0994, 4.45)[0] * 1.0) < 1.25


def test_a_group_and_its_mirror_image():
    from kicad2ems.lines import topology
    a = dict(xs='microstrip', h=0.2, er=4.4, gaps=[0.2, 0.4], pairs=[dict(gap=0.2), dict(gap=0.4)])
    b = dict(xs='microstrip', h=0.2, er=4.4, gaps=[0.4, 0.2], pairs=[dict(gap=0.4), dict(gap=0.2)])
    topology._field_group(a, [0.3, 0.3, 0.5])
    hits = topology._group_c.cache_info().hits
    topology._field_group(b, [0.5, 0.3, 0.3])
    assert topology._group_c.cache_info().hits == hits + 1                      # one solve for both
    assert np.allclose(b['C'], a['C'][::-1, ::-1])
    assert b['pairs'][1]['zo'] == pytest.approx(a['pairs'][0]['zo'])


def test_the_potential():
    x, z, phi, er = X.potential(X.coupled_microstrip(0.15, 0.15, 0.2, 4.4), [1, -1])
    assert phi.shape == (len(x), len(z)) and er.shape == (len(x) - 1, len(z) - 1)
    assert phi.max() == pytest.approx(1) and phi.min() == pytest.approx(-1)
    mid = np.argmin(np.abs(x))
    assert np.allclose(phi[mid], 0, atol=1e-6)                                  # the odd mode's electric wall


# ---- the field, to look at ------------------------------------------------------------------------
def test_field_between_plates_is_exact():
    """Two dielectrics between plates: phi linear in each, E steps at the interface with D continuous, copper and
    what lies beyond the box NaN."""
    xs = X.CrossSection(dielectrics=[X.Dielectric(0.0, 0.2, 2.0), X.Dielectric(0.2, 0.6, 4.0)], planes=[0.0])
    xs.planes.append(1.0)
    xs.dielectrics.append(X.Dielectric(0.6, 1.0, 4.0))
    xs.conductor(-40.0, 40.0, 0.6)
    f = X.field(xs)
    x, z, phi, ex, ez, er = f.sample(-1.0, 1.0, -0.1, 0.6, 8, 70)
    # between the plane at 0 and the strip at 0.6: series capacitors of 0.2 / 2 and 0.4 / 4, so half of the volt in each
    below = (z > 0) & (z < 0.2)
    assert ez[0, below] == pytest.approx(-2.5, abs=1e-6) and er[0, below] == pytest.approx(2.0)
    within = (z > 0.2) & (z < 0.6)
    assert ez[0, within] == pytest.approx(-1.25, abs=1e-6) and er[0, within] == pytest.approx(4.0)
    assert phi[0, below] == pytest.approx(2.5 * z[below], abs=1e-6)
    assert ex[0, below] == pytest.approx(0.0, abs=1e-6)
    assert np.isnan(phi[0, z < 0]).all()


def test_field_energy_is_the_capacitance():
    """1/2 V^T C V of the mesh's own C: one line, and a pair driven odd; symmetric volts give an antisymmetric field."""
    xs = X.coupled_microstrip(0.2, 0.2, 0.2, 4.3, 0.035)
    C = X._solve_mesh(xs, X._mesh(xs))[0]
    for volts in ([1.0, 0.0], [1.0, -1.0], [1.0, 1.0]):
        v = np.array(volts)
        assert X.field(xs, volts).energy() == pytest.approx(0.5 * v @ C @ v, rel=1e-9)
    box = X.window(xs)
    x, z, phi, *_ = X.field(xs, [1.0, -1.0]).sample(-box[1], box[1], box[2], box[3], 40, 30)
    assert np.nan_to_num(phi) == pytest.approx(-np.nan_to_num(phi[::-1, :]), abs=1e-9)
    assert 0.9 < np.nanmax(phi) <= 1.0 + 1e-9 and -1.0 - 1e-9 <= np.nanmin(phi) < -0.9      # none beyond the copper's


def test_cross_section_as_plain_numbers():
    """to_dict / from_dict through strict JSON: infinite ends too; the same result."""
    xs = X.microstrip(0.3, 0.2, 4.3, 0.035, tand=0.02, s=0.2, mask=0.02)
    d = json.loads(json.dumps(xs.to_dict(), allow_nan=False))
    back = X.CrossSection.from_dict(d)
    assert back == xs
    assert back.solve().single() == pytest.approx(xs.solve().single(), rel=1e-12)


def test_window_shows_the_copper_and_stays_in_the_box():
    xs = X.stripline(0.2, 0.2, 0.3, 4.3, 0.035)
    x0, x1, z0, z1 = X.window(xs)
    assert z0 == pytest.approx(0.0) and z1 == pytest.approx(0.535) and x0 < -0.1 and x1 > 0.1
    x0, x1, z0, z1 = X.window(X.microstrip(0.3, 0.2, 4.3))
    assert z0 == pytest.approx(0.0) and z1 > 0.2 and x1 - x0 > 0.3


def test_each_section_carries_what_shows_its_field():
    """The drawing's sections: where their Z0 came from and the formula's, the cuts solving to --lines-field's values,
    a coupled leg's modes to the group's, grounded CPW's classified section to its Z0; build(field=) restores the flag."""
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder, reference, sections
    from kicad2ems import overlay as ov
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    m = Board(KiCadBoard(os.path.join(root, 'examples', 'rf_diffvia.kicad_pcb')), log=lambda *a: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))
    d = json.loads(json.dumps(builder.build(m, nets=nets).drawing(m.cu_z), allow_nan=False))
    legs = [e for e in d['elements'] if e['kind'] == 'trace' and e.get('section') and 'coupled' in e['section']]
    assert legs and legs[0]['source'] == 'formula' and legs[0]['z0_formula'] == legs[0]['z0']
    assert [k for k, _ in sections.modes(legs[0])][:3] == ['odd', 'even', 'line']
    odd = sections.solve(legs[0])
    assert odd.values['Zdiff'] == pytest.approx(2 * odd.z0) and odd.volts == [1.0, -1.0]
    assert sorted({round(sections.solve(e).values['Zdiff']) for e in legs}) == [84, 92]
    assert sections.solve(legs[0], 'even').volts == [1.0, 1.0] and sections.solve(legs[0], 'line').volts.count(1.0) == 1
    assert 'formula' in ov.describe(legs[0])
    with pytest.raises(ValueError):
        sections.solve(legs[0], 'nothing')
    assert sections.modes(dict(kind='via')) == [] and ov.source_note(dict(kind='via')) == ''

    field = builder.build(m, nets=nets, field=True)
    assert reference.FIELD is False
    by_cut = field.drawing(m.cu_z)['elements']
    traces = [e for e in by_cut if e['kind'] == 'trace' and e['source'] == 'cut']
    assert traces and all(sections.solve(e, 'cut').z0 == pytest.approx(e['z0'], rel=1e-5) for e in traces[:5])
    assert 'the board' in ov.describe(traces[0]) and 'formula' in ov.describe(traces[0])


def test_classified_grounded_cpw_section():
    """A grounded CPW's section, as classified: the strip, its gaps, the plane; its Z0 what the line model took."""
    from kicad2ems.lines import reference, sections
    from kicad2ems.lines.elements import Element, LineModel, _section
    e = Element('trace', 'a', 'b', 5.0, w=0.3, layer='F.Cu', pts=np.array([[0.0, 0.0], [5.0, 0.0]]))
    e.xs, e.s, e.h, e.er, e.t, e.z0, e.z0_sheet, e.eeff = 'cpwg', 0.15, 0.2, 4.3, 0.035, 60.0, 60.0, 3.0
    e.z0_formula = 60.0
    reference._field_cpwg(e)
    assert e.source == 'cpwg' and e.z0_formula == 60.0 and e.z0 != 60.0
    d = dict(kind='trace', label='x', section=_section(e))
    assert [k for k, _ in sections.modes(d)] == ['classified']
    assert sections.solve(d).z0 == pytest.approx(e.z0, rel=1e-9)
