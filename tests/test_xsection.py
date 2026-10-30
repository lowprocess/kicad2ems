"""The 2D field solver (xsection.py) against exact and closed-form results: Cohn's stripline, single and
coupled; Hammerstad & Jensen's microstrip; Wheeler's thick stripline; grounded CPW; and what the formulas
leave out (solder mask, coplanar ground beside a stripline) in the direction it should go."""
import math
import numpy as np
import pytest
from kicad2ems.lines import xsection as X
from kicad2ems.lines.formulas import microstrip, stripline, cpwg, ellipk
from kicad2ems.lines.coupled import coupled_stripline, coupled_microstrip, mtl_s


def _cohn(w, b, er):
    k = 1 / math.cosh(math.pi * w / (2 * b))
    return 30 * math.pi / math.sqrt(er) * ellipk(k) / ellipk(math.sqrt(1 - k * k))


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
    assert 2 * p['zo'] == pytest.approx(104.9, abs=0.5) and p['ze'] < p['ze_sheet']
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
