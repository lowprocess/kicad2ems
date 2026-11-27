"""core.magnets: the drawn magnets' field and the ferrite's bias, against the
closed forms (a slab, a disc's solid angle, a cylinder magnet on its axis)."""
import math
import numpy as np
import pytest
from shapely.geometry import Point, box

from kicad2ems.core import magnets as mg


def test_a_slab_inside_is_minus_m_and_outside_nothing():
    M = 1.0e5
    src = [(box(-2000, -2000, 2000, 2000), 0.0, 1.0, M)]
    hz = mg.field_z(src, [(0, 0, 0.5), (0, 0, 3.0), (0, 0, -2.0)]) / M
    assert hz[0] == pytest.approx(-1.0, abs=1e-3)   # demagnetising factor 1
    assert abs(hz[1]) < 2e-3 and abs(hz[2]) < 2e-3


def test_a_disc_subtends_its_solid_angle_on_its_axis():
    a = 3.0
    tri = mg._triangles(Point(0, 0).buffer(a, quad_segs=256))
    for d in (0.3, 1.0, 4.0):
        om = mg._omega_up(tri, 0.0, np.array([[0, 0, d], [0, 0, -d]]))
        ref = 2 * math.pi * (1 - d / math.hypot(d, a))
        assert om[0] == pytest.approx(ref, rel=1e-5)
        assert om[1] == pytest.approx(-ref, rel=1e-5)


def test_a_holed_face_is_its_outer_ring_less_the_hole():
    ring = Point(0, 0).buffer(4.0, quad_segs=128).difference(
        Point(0, 0).buffer(1.5, quad_segs=128))
    d = 1.0
    om = mg._omega_up(mg._triangles(ring), 0.0, np.array([[0, 0, d]]))[0]
    ref = (2 * math.pi * (1 - d / math.hypot(d, 4.0))
           - 2 * math.pi * (1 - d / math.hypot(d, 1.5)))
    assert om == pytest.approx(ref, rel=1e-4)


def test_a_cylinder_magnet_on_its_axis():
    R, L, Br = 3.0, 2.0, 1.2
    src = [(Point(0, 0).buffer(R, quad_segs=256), 0.0, L, Br / mg.MU0)]
    for z in (0.5, 1.0, 2.0):  # below its south face, field up
        ref = Br / 2 * ((z + L) / math.hypot(R, z + L)
                        - z / math.hypot(R, z)) / mg.MU0
        assert mg.field_z(src, [(0, 0, -z)])[0] == pytest.approx(ref,
                                                                  rel=1e-5)
    # inside it the H opposes the magnetisation (B = mu0 (H + M) > 0)
    h = mg.field_z(src, [(0, 0, 1.0)])[0]
    assert -Br / mg.MU0 < h < 0


def test_gamma_is_2_8_mhz_an_oersted():
    assert mg.GAMMA * mg.OE == pytest.approx(2.8e6, rel=1e-3)
    f0, fm = mg.fmr(1000 * mg.OE, 1800.0)
    assert f0 == pytest.approx(2.80e9, rel=1e-3)
    assert fm == pytest.approx(5.04e9, rel=1e-3)


class _Board:
    def __init__(self, solids):
        from shapely.geometry import mapping
        self.outline = box(-10, -10, 10, 10)
        self.layers = [dict(z0=0.0, z1=0.5)]
        self.cu_z = {'F.Cu': 0.5, 'B.Cu': 0.0}
        self.solids = [dict(d, polygon=mapping(d['polygon'])) for d in solids]


def _puck_and_magnet(br=1.2, gap=1.0, r=5.0, length=4.0):
    puck = dict(shape='puck', material='ferrite', z0=0.0, z1=0.5, er=15.0,
                tand=2e-4, ms=1800.0, dh=30.0,
                polygon=Point(0, 0).buffer(3.0, quad_segs=64))
    mag = dict(shape='magnet', material='magnet', z0=0.5 + gap,
               z1=0.5 + gap + length, er=1.0, tand=0.0, br=br,
               polygon=Point(0, 0).buffer(r, quad_segs=64))
    return puck, mag


def test_the_puck_is_biased_by_the_magnet_less_its_own_field():
    from kicad2ems.core.solids import pieces
    puck, mag = _puck_and_magnet()
    b = _Board([puck, mag])
    ps = pieces(b)
    assert [p.material for p in ps] == ['ferrite', 'metal']
    assert ps[1].magnet == 1.2 and ps[0].ferrite['ms'] == 1800.0
    pt = np.array([[0.0, 0.0, 0.25]])
    r = mg.bias(b, ps, pt, owner=[0])
    Ms = 1800 * mg.GAUSS / mg.MU0
    own = mg.field_z([(Point(0, 0).buffer(3.0, quad_segs=64), 0.0, 0.5,
                       Ms)], pt)[0]
    assert r['sign'] == {0: 1.0}
    assert r['hz'][0] == pytest.approx(r['applied'][0] + own, rel=1e-9)
    assert -Ms < own < -0.7 * Ms     # a thin puck: N_z near 1
    # reversed, the same field the other way
    puck, mag = _puck_and_magnet(br=-1.2)
    b2 = _Board([puck, mag])
    r2 = mg.bias(b2, pieces(b2), pt, owner=[0])
    assert r2['sign'] == {0: -1.0}
    assert r2['hz'][0] == pytest.approx(-r['hz'][0], rel=1e-9)


def test_a_weak_magnet_leaves_the_puck_unsaturated():
    from kicad2ems.core.solids import pieces
    puck, mag = _puck_and_magnet(br=0.2, gap=3.0, r=3.0, length=2.0)
    b = _Board([puck, mag])
    r = mg.bias(b, pieces(b), np.array([[0.0, 0.0, 0.25]]), owner=[0])
    assert not r['sat'][0] and np.isnan(r['hz'][0])
    s = mg.summary(b)
    assert len(s) == 1 and s[0]['saturated'] == 0.0
    assert s[0]['internal_oe'] is None


def test_summary_of_a_biased_puck():
    puck, mag = _puck_and_magnet()
    s = mg.summary(_Board([puck, mag]))[0]
    assert s['name'] == 'puck' and s['saturated'] == 1.0
    lo, mean, hi = s['internal_oe']
    # the field varies across it (magnet and demagnetising field both fall off)
    assert 900 < lo < mean < hi < 2000
    # a magnet only the puck's size saturates the middle, not the rim
    puck, mag = _puck_and_magnet(r=3.0, length=2.0)
    assert 0.3 < mg.summary(_Board([puck, mag]))[0]['saturated'] < 0.9
    assert s['f0'] == pytest.approx(2.8e6 * mean, rel=1e-2)
    assert s['fm'] == pytest.approx(5.04e9, rel=1e-3)


def test_no_magnet_no_bias():
    from kicad2ems.core.solids import pieces
    puck, _ = _puck_and_magnet()
    b = _Board([puck])
    r = mg.bias(b, pieces(b), np.array([[0.0, 0.0, 0.25]]), owner=[0])
    assert np.isnan(r['hz'][0])
