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


def test_a_face_s_full_field_is_its_charge_s_coulomb_integral():
    """field(): a charged rectangle's H (its solid angle along z, its edges'
    log terms across) against the Coulomb integral by quadrature, above,
    below, beside it and near a corner."""
    from scipy import integrate
    # a unit charge density (M = 4 pi) on the face at z = 0, the bottom one
    # far away
    src = [(box(0, 0, 2, 1), -1e6, 0.0, 4 * math.pi)]
    pts = np.array([[0.5, 0.3, 0.4], [2.5, 0.5, 0.2], [1.0, 0.5, -0.3],
                    [-0.4, -0.2, 0.05], [1.9, 0.95, 0.1]])
    h = mg.field(src, pts)
    for p, hv in zip(pts, h):
        ref = [integrate.dblquad(
            lambda y, x: (p[c] - (x, y, 0.0)[c]) / (
                (p[0] - x) ** 2 + (p[1] - y) ** 2 + p[2] ** 2) ** 1.5,
            0, 2, 0, 1, epsabs=1e-11, epsrel=1e-11)[0] for c in range(3)]
        np.testing.assert_allclose(hv, ref, atol=1e-9 * np.abs(ref).max())
    np.testing.assert_allclose(mg.field(src, pts)[:, 2],
                               mg.field_z(src, pts), rtol=1e-12)


def _loops(R, L, M, rho, z, n=400):
    """B / mu0 (rho, z components) of a cylinder magnet (radius R, from z 0
    to L, M along z) as its surface current K = M: current loops (complete
    elliptic integrals) summed over its height by Gauss-Legendre."""
    from scipy.special import ellipk, ellipe
    x, w = np.polynomial.legendre.leggauss(n)
    z0, w = 0.5 * L * (x + 1), 0.5 * L * w
    d = z - z0
    q = (R + rho) ** 2 + d ** 2
    K, E = ellipk(4 * R * rho / q), ellipe(4 * R * rho / q)
    bz = (K + (R ** 2 - rho ** 2 - d ** 2) / ((R - rho) ** 2 + d ** 2) * E
          ) / (2 * math.pi * np.sqrt(q))
    br = d / np.sqrt(q) * (-K + (R ** 2 + rho ** 2 + d ** 2) / (
        (R - rho) ** 2 + d ** 2) * E) / (2 * math.pi * rho)
    return M * (w * br).sum(), M * (w * bz).sum()


def test_a_cylinder_magnet_off_its_axis():
    """The face charges' H against the equivalent surface current's B / mu0
    (outside; inside H = B / mu0 - M): 2e-6 of M with 1024 sides."""
    R, L, M = 3.0, 2.0, 1.2 / mg.MU0
    src = [(Point(0, 0).buffer(R, quad_segs=256), 0.0, L, M)]
    phi = 0.7
    for rho, z in ((1.0, -0.5), (2.5, -0.3), (3.5, 1.0), (4.0, -1.0),
                   (2.0, 3.0), (1.5, 1.0)):
        h = mg.field(src, [(rho * math.cos(phi), rho * math.sin(phi), z)])[0]
        br, bz = _loops(R, L, M, rho, z)
        if rho < R and 0 < z < L:
            bz -= M
        np.testing.assert_allclose(
            h, [br * math.cos(phi), br * math.sin(phi), bz], atol=3e-6 * M)


def _uniform(h):
    return lambda p: np.tile(np.asarray(h, float), (len(np.atleast_2d(p)), 1))


def _prisms(poly, z0, z1, ms=1000.0):
    from types import SimpleNamespace
    return [SimpleNamespace(polygon=poly, z0=z0, z1=z1, index=0,
                            ferrite=dict(ms=ms, dh=30.0))]


def test_the_box_kernel_is_the_box_s_field():
    """_box_kernel's octant mirrored: each offset the field of the voxel's
    faces (field) for M along x, y and z; at its own centre -1/3 (a cube)."""
    d, n = (0.2, 0.3, 0.1), (3, 4, 2)
    K = mg._box_kernel(d, n)
    assert K.shape == (3, 3, 5, 7, 3)
    np.testing.assert_allclose(K[:, :, 2, 3, 1].diagonal().sum(), -1.0,
                               rtol=1e-9)        # the trace: -1
    np.testing.assert_allclose(K, K.transpose(1, 0, 2, 3, 4), atol=1e-12)
    for off in ((1, -2, 1), (-2, 3, 0), (0, 1, -1)):
        p = np.array([[o * dd for o, dd in zip(off, d)]])
        i = tuple(o + m - 1 for o, m in zip(off, n))
        hz = mg.field([(box(-0.1, -0.15, 0.1, 0.15), -0.05, 0.05, 1.0)], p)[0]
        np.testing.assert_allclose(K[(slice(None), 2) + i], hz, atol=1e-12)
        hx = mg.field([(box(-0.15, -0.05, 0.15, 0.05), -0.1, 0.1, 1.0)],
                      p[:, [1, 2, 0]])[0][[2, 0, 1]]
        np.testing.assert_allclose(K[(slice(None), 0) + i], hx, atol=1e-12)


def test_a_long_cylinder_across_a_uniform_field():
    """magnetisation() on a prism, a cylinder 16 radii long across a uniform
    field (N 1/2 in its middle): saturated, H = Ha - Ms / 2 within 3 %;
    partly magnetised, M = Ha / (1/2 + 1/chi) within 5 % on the mean, H
    near 0."""
    Ms = mg.ms_si(1000.0)
    ps = _prisms(Point(0, 0).buffer(1.0, quad_segs=64), -8.0, 8.0)
    for frac in (1.0, 0.3):
        f = mg.magnetisation(None, ps=ps, voxel=(1 / 16, 1 / 16, 0.5),
                             applied=_uniform([frac * Ms, 0, 0]))
        zc = f.lo[2] + np.arange(f.n[2]) * f.d[2]
        mid = (f.fill >= 0.5) & (np.abs(zc) < 2.0)[None, None, :]
        m, h = f.M[0][mid].mean() / Ms, f.H[0][mid].mean() / Ms
        if frac == 1.0:
            assert m == pytest.approx(1.0, abs=2e-3)
            assert h == pytest.approx(0.5, rel=0.03)
        else:
            assert m == pytest.approx(0.3 / (0.5 + 1 / 50.0), rel=0.05)
            assert 0 < h < 0.02
        # (the staircase's rim tilts M a little either way: none on the mean)
        assert abs(f.M[1][mid].mean()) < 1e-6 * Ms


def test_a_thin_disc_in_a_normal_field_is_the_face_model_s():
    """Saturated along z, a disc's internal field is the face model's (exact
    for a uniform M along z) within 0.5 % in its middle and 2 % at its rim;
    there M tilts a little (its own field across), which that model leaves
    out."""
    Ms = mg.ms_si(1000.0)
    g = Point(0, 0).buffer(3.0, quad_segs=64)
    f = mg.magnetisation(None, ps=_prisms(g, 0.0, 0.5),
                         applied=_uniform([0, 0, 1.5 * Ms]))
    assert f.iterations < 50
    pts = np.array([[r, 0, z] for r in (0, 1, 2, 2.8) for z in (0.0625, 0.25)])
    h, m = f.at(pts, exact=True)
    ref = 1.5 * Ms + mg.field_z([(g, 0.0, 0.5, Ms)], pts)
    err = np.abs(h[:, 2] / ref - 1)
    assert err[:4].max() < 5e-3 and err.max() < 0.03
    np.testing.assert_allclose(np.linalg.norm(m, axis=1), 1.0, atol=1e-3)
    assert m[-2, 0] < -0.05          # the rim's bottom layer tilts in
    # h interpolated from the voxels: within 0.2 % of h worked out exactly
    h2, _ = f.at(pts)
    np.testing.assert_allclose(h2, h, atol=2e-3 * np.abs(h).max())


def test_the_puck_under_a_magnet_tilts_and_reverses():
    """The drawn puck under a magnet its own size: its internal field turns
    from z towards the axis near its rim (the magnet's field converging on
    its south face, M with it); reversed, everything is (to the solve's
    tolerance)."""
    puck, mag = _puck_and_magnet(r=3.0, length=2.0)
    f = mg.magnetisation(_Board([puck, mag]))
    pts = np.array([[0.0, 0.0, 0.25], [2.5, 0.0, 0.25], [0.0, -2.5, 0.25]])
    h, m = f.at(pts)
    # (on the axis: across it only the nearest voxel's half-voxel offset)
    assert abs(h[0, :2]).max() < 0.03 * h[0, 2]
    assert h[1, 0] < -0.2 * h[1, 2] and h[2, 1] > 0.2 * h[2, 2]
    tilt = np.degrees(np.arctan2(np.hypot(m[:, 0], m[:, 1]), m[:, 2]))
    assert tilt[0] < 3 and 15 < tilt[1] < 70
    puck, mag = _puck_and_magnet(r=3.0, length=2.0, br=-1.2)
    h2, m2 = mg.magnetisation(_Board([puck, mag])).at(pts)
    np.testing.assert_allclose(h2, -h, atol=1e-3 * np.abs(h).max())
    np.testing.assert_allclose(m2, -m, atol=1e-3)


def test_a_weak_magnet_magnetises_the_puck_partly():
    """Too weak to saturate it: |M| < Ms, the internal field near 0 (M /
    chi: a few per cent of the magnets'), M along the magnet's field."""
    puck, mag = _puck_and_magnet(br=0.2, gap=3.0, r=3.0, length=2.0)
    f = mg.magnetisation(_Board([puck, mag]))
    h, m = f.at(np.array([[0.0, 0.0, 0.25], [1.5, 0.0, 0.25]]))
    r = np.linalg.norm(m, axis=1)
    assert (r < 0.9).all() and (r > 0.05).all()
    assert m[0, 2] > 0 and h[0, 2] > 0
    # in every voxel the solve's fixed point: H = M / chi (the energy's
    # least within |M| < Ms)
    on = f.fill >= 0.5
    np.testing.assert_allclose(f.H[:, on], f.M[:, on] / 50.0,
                               atol=2e-3 * f.Ms.max())
    assert (np.sqrt((f.M[:, on] ** 2).sum(0)) < 0.9 * f.Ms[on]).all()
