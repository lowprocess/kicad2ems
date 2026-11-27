"""gfdtd's magnetised ferrite (csx.FerriteMaterial, operator._ferrite): its
update against the Polder tensor, a ferrite-filled cavity's resonances
against (w / c)^2 er mu_eff(w) = kc^2, the backends against each other, a long
run, the XML."""
import numpy as np
import pytest
from scipy.optimize import brentq

from kicad2ems.gfdtd import ContinuousStructure, openEMS
from kicad2ems.gfdtd import csx as C
from kicad2ems.gfdtd.operator import build
from kicad2ems.gfdtd.engine import make_backend, core_info

from test_gfdtd import GPUS

C0 = 299792458.0
GAMMA = 2 * np.pi * 35.18e3     # rad/s per A/m (g = 2)
F0, FM, ER = 3e9, 5e9, 15.0     # gamma H0, gamma Ms; the cavity's ferrite
A_MM = 10.0                     # the cavity's side


def _cavity(dx=0.5, steps=30000, sign=1.0, alpha=0.0, bias=None):
    """A 10 x 10 x 1 mm PEC box full of ferrite biased along z (sign), a z
    source near one corner, a z voltage probe near the other."""
    F = openEMS(NrTS=steps, EndCriteria=0)
    F.SetGaussExcite(7e9, 6e9)
    F.SetBoundaryCond(['PEC'] * 6)
    csx = ContinuousStructure()
    F.SetCSX(csx)
    g = csx.GetGrid()
    g.SetDeltaUnit(1e-3)
    g.SetLines('x', np.arange(0, A_MM + 1e-9, dx))
    g.SetLines('y', np.arange(0, A_MM + 1e-9, dx))
    g.SetLines('z', [0, 0.5, 1.0])
    csx.AddFerriteMaterial(
        'fer', epsilon=ER, ms=FM / GAMMA * 2 * np.pi, alpha=alpha,
        gamma=GAMMA,
        bias=sign * F0 / GAMMA * 2 * np.pi if bias is None else bias).AddBox(
            [0, 0, 0], [A_MM, A_MM, 1], priority=1)
    csx.AddExcitation('src', exc_type=0, exc_val=[0, 0, 1]).AddBox(
        [2.5, 3.5, 0], [2.5, 3.5, 1])
    csx.AddProbe('ut', p_type=0).AddBox([6.5, 7.0, 0], [6.5, 7.0, 1])
    return F, csx


def _trace(op, backend, steps):
    be = make_backend(op, backend)
    u = np.zeros(steps)
    for n in range(steps):
        be.step(op.signal[n] if n < len(op.signal) else None)
        be.probe(0)
        u[n] = be.fetch(1)[0, 0]
    return u


def _peaks(u, dt, lo, hi):
    w = np.hanning(2 * len(u))[len(u):]
    nf = 16 * len(u)
    A = np.abs(np.fft.rfft(u * w, nf))
    f = np.fft.rfftfreq(nf, dt)
    band = (f > lo) & (f < hi)
    top = A[band].max()
    return [f[i] for i in np.nonzero(band)[0]
            if A[i] > A[i - 1] and A[i] > A[i + 1] and A[i] > 0.1 * top]


def _mu_eff(f):
    return ((F0 + FM) ** 2 - f ** 2) / (F0 * (F0 + FM) - f ** 2)


def _tm_roots(m, n):
    kc2 = (m * np.pi / (A_MM * 1e-3)) ** 2 + (n * np.pi / (A_MM * 1e-3)) ** 2
    g = lambda f: (2 * np.pi * f / C0) ** 2 * ER * _mu_eff(f) - kc2
    pole, zero = np.sqrt(F0 * (F0 + FM)), F0 + FM
    return (brentq(g, 0.2e9, pole * (1 - 1e-9)),
            brentq(g, zero * (1 + 1e-9), 40e9))


def _polder(f, h0_f=F0, fm=FM):
    """Pozar's [mu, j kappa; -j kappa, mu] (bias +z, e^{jwt}), relative."""
    mu = 1 + h0_f * fm / (h0_f ** 2 - f ** 2)
    ka = f * fm / (h0_f ** 2 - f ** 2)
    return np.array([[mu, 1j * ka], [-1j * ka, mu]])


@pytest.mark.parametrize('sign', [1.0, -1.0])
def test_the_update_is_the_polder_tensor(sign):
    """The cell update m+ = P m- + Q (b+ + b-) in steady state at w: h = b - m
    gives b = [mu, j kappa; -j kappa, mu] h, kappa's sign the bias's, to
    O((w dt)^2)."""
    F, csx = _cavity(sign=sign)
    op = build(csx, F.settings())
    p0, p1, q0, q1 = op.ferrite.k[0]
    J = np.array([[0.0, -1.0], [1.0, 0.0]])
    P, Q = p0 * np.eye(2) + p1 * J, q0 * np.eye(2) + q1 * J
    for f in (1e9, 2e9, 6e9, 10e9):
        z = np.exp(2j * np.pi * f * op.dt)
        X = np.linalg.solve(z * np.eye(2) - P, Q * (z + 1))  # m = X b
        mu = np.linalg.inv(np.eye(2) - X)                    # b = mu h
        ref = _polder(f)
        if sign < 0:
            ref = ref.conj()  # kappa reversed (mu real, lossless)
        np.testing.assert_allclose(mu, ref, rtol=2e-3, atol=2e-3 * np.abs(
            ref).max())


def test_free_precession_turns_anticlockwise_about_the_bias():
    """Unbiased by b, m turns about +z anticlockwise seen from above (an
    electron's sense), at 2 atan(wr dt / 2) a step, wr = gamma (H0 + Ms)."""
    F, csx = _cavity()
    op = build(csx, F.settings())
    p0, p1, _, _ = op.ferrite.k[0]
    wr = 2 * np.pi * (F0 + FM)
    assert np.arctan2(p1, p0) == pytest.approx(2 * np.arctan(wr * op.dt / 2),
                                               rel=1e-6)
    assert p0 ** 2 + p1 ** 2 == pytest.approx(1.0, abs=1e-12)  # no loss
    F, csx = _cavity(sign=-1.0)
    assert build(csx, F.settings()).ferrite.k[0][1] == pytest.approx(-p1)


def _spectrum(u, dt):
    w = np.hanning(2 * len(u))[len(u):]
    nf = 16 * len(u)
    return np.fft.rfftfreq(nf, dt), np.abs(np.fft.rfft(u * w, nf))


def test_a_ferrite_filled_cavity_resonates_where_polder_has_it():
    """TM110, TM210 and TM220 each twice (below the pole sqrt(f0 (f0 + fm))
    and above f0 + fm, where mu_eff > 0): within 0.3 % at 0.5 mm (20 cells
    across, 30 a wavelength at 9 GHz; the faces' scheme: 0.07 / 0.04 / 0.16 /
    0.20 / 0.19 / 0.28 %); between the pole and f0 + fm (mu_eff < 0) nothing
    above 1 % of TM110's peak. On the cells' centres alone (scheme
    'centre') the grid's own magnetostatic modes there: 10.5 %."""
    F, csx = _cavity()
    op = build(csx, F.settings())
    assert op.ferrite.faces
    u = _trace(op, 'numpy', 30000)
    lo = _peaks(u, op.dt, 2e9, 4.5e9)
    hi = _peaks(u, op.dt, 8.5e9, 13.5e9)
    for (m, n) in ((1, 1), (2, 1), (2, 2)):
        r_lo, r_hi = _tm_roots(m, n)
        assert min(abs(p / r_lo - 1) for p in lo) < 0.003
        assert min(abs(p / r_hi - 1) for p in hi) < 0.003
    pole, zero = np.sqrt(F0 * (F0 + FM)), F0 + FM
    gap = lambda f: (f > pole * 1.02) & (f < zero * 0.98)
    f, A = _spectrum(u, op.dt)
    main = A[(f > 2e9) & (f < 4.5e9)].max()
    assert A[gap(f)].max() < 0.01 * main
    csx.GetPropertiesByName('fer')[0].scheme = 'centre'
    op = build(csx, F.settings())
    assert not op.ferrite.faces
    f, A = _spectrum(_trace(op, 'numpy', 30000), op.dt)
    assert A[gap(f)].max() > 0.05 * A[(f > 2e9) & (f < 4.5e9)].max()


@pytest.mark.parametrize('backend', [
    pytest.param('core', marks=pytest.mark.skipif(
        not core_info()[0], reason='no compiled CPU core'))] + GPUS)
def test_backends_agree_on_the_ferrite(backend):
    F, csx = _cavity(steps=4000, alpha=0.01)
    op = build(csx, F.settings())
    a, b = _trace(op, 'numpy', 4000), _trace(op, backend, 4000)
    np.testing.assert_allclose(b, a, atol=1e-4 * np.abs(a).max())


def _puck(alpha):
    """A ferrite puck under a disc and a strip between a PEC floor and Mur
    sides, a graded mesh (the circulator's shape)."""
    F = openEMS(NrTS=10, EndCriteria=0)
    F.SetGaussExcite(8e9, 7e9)
    F.SetBoundaryCond(['MUR'] * 4 + ['PEC', 'MUR'])
    csx = ContinuousStructure()
    F.SetCSX(csx)
    g = csx.GetGrid()
    g.SetDeltaUnit(1e-3)
    xs = np.unique(np.r_[np.arange(-6, -3, 0.5), np.arange(-3, 3, 0.2),
                         np.arange(3, 6.01, 0.5), 3.0])
    g.SetLines('x', xs)
    g.SetLines('y', xs)
    g.SetLines('z', np.unique(np.r_[np.linspace(0, 0.5, 3),
                                    np.arange(0.5, 3.01, 0.4), 3.0]))
    csx.AddFerriteMaterial('fer', epsilon=15, ms=1.43e5, alpha=alpha,
                           gamma=GAMMA, bias=1.0e5).AddCylinder(
                               [0, 0, 0], [0, 0, 0.5], 2.6, priority=2)
    csx.AddMetal('strip').AddBox([-6, -0.2, 0.5], [0, 0.2, 0.5], priority=10)
    csx.AddMetal('disc').AddCylinder([0, 0, 0.5], [0, 0, 0.5], 2.2,
                                     priority=10)
    csx.AddExcitation('src', exc_type=0, exc_val=[0, 0, 1]).AddBox(
        [-5, 0, 0], [-5, 0, 0.5])
    return F, csx


@pytest.mark.skipif(not core_info()[0], reason='no compiled CPU core')
def test_a_puck_in_open_sides_stays_stable():
    """Lossless, after the pulse the field's energy only falls (its
    magnetostatic modes ring, radiating slowly; checked to 200,000 steps on
    the GPU); damped (alpha 0.005) it falls far faster."""
    last = {}
    for alpha in (0.0, 0.005):
        F, csx = _puck(alpha)
        op = build(csx, F.settings())
        be = make_backend(op, 'core')
        W = []
        for n in range(30000):
            be.step(op.signal[n] if n < len(op.signal) else None)
            if n % 250 == 0:
                W.append(be.energy())
        W = np.array(W)
        q = [x.max() for x in np.array_split(W[len(op.signal) // 250 + 8:],
                                             4)]
        assert np.isfinite(W).all()
        assert q[3] <= q[2] <= q[1] <= q[0] <= W.max()
        last[alpha] = q[3] / W.max()
    assert last[0.005] < 1e-2 and last[0.005] < 0.25 * last[0.0]


def test_unsaturated_cells_are_left_unbiased_and_the_table_is_read():
    """A bias table read at the cells' centres (nearest point); NaN there:
    the cell is not magnetised, and said."""
    F, csx = _cavity()
    xs = np.arange(0.25, A_MM, 0.5)
    hz = np.full((len(xs), len(xs), 2), 8.0e4)
    hz[:4] = np.nan                       # x < 2 mm: not saturated
    hz[-1] = -6.0e4                       # the last column: the other way
    p = csx.GetPropertiesByName('fer')[0]
    p.bias = dict(x=xs, y=xs, z=np.array([0.25, 0.75]), hz=hz)
    op = build(csx, F.settings())
    fe = op.ferrite
    assert fe.nc == 800 - 4 * 20 * 2
    assert any('160 of 800 cells not saturated' in n for n in op.notes)
    ci = fe.ijk[0]
    h0 = fe.h0 * fe.u[:, 2]          # (|H0|, along u)
    assert set(np.unique(h0[ci == 19])) == {-6.0e4}
    assert set(np.unique(h0[ci < 19])) == {8.0e4}
    assert any('+/-z' in n for n in op.notes)


def test_the_ferrite_in_xml_both_ways(tmp_path):
    F, csx = _cavity()
    xs = np.array([0.25, 5.0, 9.75])
    p = csx.GetPropertiesByName('fer')[0]
    p.bias = dict(x=xs, y=xs, z=np.array([0.5]),
                  hz=np.arange(9.0).reshape(3, 3, 1) * 1e4)
    path = str(tmp_path / 'm.xml')
    F.Write2XML(path)
    F2 = openEMS()
    F2.ReadFromXML(path)
    q = [x for x in F2.csx.GetAllProperties()
         if isinstance(x, C.FerriteMaterial)][0]
    assert (q.epsilon, q.alpha) == (ER, 0.0)
    assert q.ms == pytest.approx(p.ms, rel=1e-6)
    assert q.gamma == pytest.approx(GAMMA, rel=1e-6)
    np.testing.assert_allclose(q.bias['hz'], p.bias['hz'], rtol=1e-6)
    np.testing.assert_allclose(q.bias['x'], xs, rtol=1e-6)
    # a uniform bias too
    F, csx = _cavity(sign=-1.0)
    F.Write2XML(path)
    F2 = openEMS()
    F2.ReadFromXML(path)
    q = [x for x in F2.csx.GetAllProperties()
         if isinstance(x, C.FerriteMaterial)][0]
    assert q.bias == pytest.approx(-F0 / GAMMA * 2 * np.pi, rel=1e-6)


def _cavity_x(dx=0.5, steps=30000, bias=None, alpha=0.0):
    """_cavity turned by the cyclic permutation (x, y, z) -> (y, z, x) (a
    rotation): 1 x 10 x 10 mm, biased along +x, the source and the probe
    along x."""
    F = openEMS(NrTS=steps, EndCriteria=0)
    F.SetGaussExcite(7e9, 6e9)
    F.SetBoundaryCond(['PEC'] * 6)
    csx = ContinuousStructure()
    F.SetCSX(csx)
    g = csx.GetGrid()
    g.SetDeltaUnit(1e-3)
    g.SetLines('x', [0, 0.5, 1.0])
    g.SetLines('y', np.arange(0, A_MM + 1e-9, dx))
    g.SetLines('z', np.arange(0, A_MM + 1e-9, dx))
    csx.AddFerriteMaterial(
        'fer', epsilon=ER, ms=FM / GAMMA * 2 * np.pi, alpha=alpha, gamma=GAMMA,
        bias=([F0 / GAMMA * 2 * np.pi, 0, 0] if bias is None else bias)
    ).AddBox([0, 0, 0], [1, A_MM, A_MM], priority=1)
    csx.AddExcitation('src', exc_type=0, exc_val=[1, 0, 0]).AddBox(
        [0, 2.5, 3.5], [1, 2.5, 3.5])
    csx.AddProbe('ut', p_type=0).AddBox([0, 6.5, 7.0], [1, 6.5, 7.0])
    return F, csx


def test_a_cavity_biased_along_x_is_the_z_cavity_turned():
    """The general update (all three components about u) on the cavity
    turned to x gives the z cavity's trace (the fast path's) to float32's
    rounding: the same modes with the axes' roles swapped."""
    F, csx = _cavity(steps=6000)
    op = build(csx, F.settings())
    assert op.ferrite.na == 2
    Fx, cx = _cavity_x(steps=6000)
    opx = build(cx, Fx.settings())
    assert opx.ferrite.na == 3 and opx.ferrite.e.shape[1] == 6
    np.testing.assert_allclose(opx.ferrite.u, [[1, 0, 0]] * opx.ferrite.nc)
    a, b = _trace(op, 'numpy', 6000), _trace(opx, 'numpy', 6000)
    np.testing.assert_allclose(b, a, atol=2e-5 * np.abs(a).max())


def _rot(u):
    """A rotation taking z to the unit vector u."""
    u = np.asarray(u, float) / np.linalg.norm(u)
    a = np.cross([0, 0, 1.0], u)
    s, c = np.linalg.norm(a), u[2]
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    a /= s
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + s * K + (1 - c) * K @ K


def _cell_mu(k, f, dt):
    """B = mu h of the general cell update (k: p0 p1 q0 q1 ux uy uz qs) in
    steady state at f: m = X b, X = (z - P)^-1 Q (z + 1) + qs, mu = (1 -
    X)^-1."""
    p0, p1, q0, q1 = k[:4]
    u, qs = np.asarray(k[4:7]), k[7]
    U = np.array([[0, -u[2], u[1]], [u[2], 0, -u[0]], [-u[1], u[0], 0]])
    Pp = np.eye(3) - np.outer(u, u)
    P, Q = p0 * np.eye(3) + p1 * U, q0 * Pp + q1 * U
    z = np.exp(2j * np.pi * f * dt)
    X = np.linalg.solve(z * np.eye(3) - P, Q * (z + 1)) + qs * np.eye(3)
    return np.linalg.inv(np.eye(3) - X)


def test_the_tilted_update_is_the_polder_tensor_turned():
    """Biased along u = (1, 2, 2) / 3: the cell's mu is R Polder R^T (mu 1
    along u), to O((w dt)^2)."""
    F, csx = _cavity()
    p = csx.GetPropertiesByName('fer')[0]
    u = np.array([1.0, 2.0, 2.0]) / 3
    p.bias = u * F0 / GAMMA * 2 * np.pi
    op = build(csx, F.settings())
    fe = op.ferrite
    assert fe.na == 3
    np.testing.assert_allclose(fe.k[0][4:7], u, rtol=1e-12)
    R = _rot(u)
    for f in (1e9, 2e9, 6e9, 10e9):
        ref = np.eye(3, dtype=complex)
        ref[:2, :2] = _polder(f)
        ref = R @ ref @ R.T
        mu = _cell_mu(fe.k[0], f, op.dt)
        np.testing.assert_allclose(mu, ref, atol=2e-3 * np.abs(ref).max())


def test_a_partly_magnetised_cell_is_green_and_sandy_s():
    """M / Ms = r < 1 (the table's m): mu_s = mu_d + (1 - mu_d) r^1.5 across
    and along u (mu_d Schlomann's at freq), kappa Rado's r wm / w; r -> 0 the
    demagnetised mu_d alone."""
    from kicad2ems.gfdtd.operator import schlomann
    F, csx = _cavity()
    p = csx.GetPropertiesByName('fer')[0]
    xs = np.arange(0.25, A_MM, 0.5)
    shape = (len(xs), len(xs), 2)
    u = np.array([0.0, 0.6, 0.8])
    fq = 9e9
    for r in (0.6, 1e-4):
        p.freq = fq
        p.bias = dict(x=xs, y=xs, z=np.array([0.25, 0.75]),
                      hz=np.zeros(shape), **{
                          c: np.full(shape, r * v) for c, v in zip(
                              ('mx', 'my', 'mz'), u)})
        op = build(csx, F.settings())
        fe = op.ferrite
        assert fe.nc == 800 and fe.na == 3
        mu_d = schlomann(FM, fq)
        mu_s = mu_d + (1 - mu_d) * r ** 1.5
        assert fe.mu_s[0] == pytest.approx(mu_s, rel=1e-9)
        R = _rot(u)
        for f in (6e9, 9e9, 12e9):
            ka = r * FM / f
            ref = R @ np.array([[mu_s, -1j * ka, 0], [1j * ka, mu_s, 0],
                                [0, 0, mu_s]]) @ R.T
            mu = _cell_mu(fe.k[0], f, op.dt)
            np.testing.assert_allclose(mu, ref, atol=3e-3 * np.abs(ref).max())
    assert any('partly magnetised' in n for n in op.notes)


@pytest.mark.parametrize('backend', [
    pytest.param('core', marks=pytest.mark.skipif(
        not core_info()[0], reason='no compiled CPU core'))] + GPUS)
def test_backends_agree_on_the_tilted_and_the_partial_ferrite(backend):
    F, csx = _cavity(steps=4000, alpha=0.01)
    p = csx.GetPropertiesByName('fer')[0]
    xs = np.arange(0.25, A_MM, 0.5)
    X, Y, _ = np.meshgrid(xs, xs, [0, 1], indexing='ij')
    # tilted, and partly magnetised for x > 7 mm
    ux, uy = 0.3 * np.cos(X), 0.3 * np.sin(Y)
    uz = np.sqrt(1 - ux ** 2 - uy ** 2)
    r = np.where(X > 7, 0.5, 1.0)
    h0 = F0 / GAMMA * 2 * np.pi
    p.bias = dict(x=xs, y=xs, z=np.array([0.25, 0.75]), hx=h0 * ux,
                  hy=h0 * uy, hz=h0 * uz, mx=r * ux, my=r * uy, mz=r * uz)
    p.freq = 9e9
    op = build(csx, F.settings())
    assert op.ferrite.na == 3 and (op.ferrite.mr < 0.9).sum() == 6 * 20 * 2
    a, b = _trace(op, 'numpy', 4000), _trace(op, backend, 4000)
    np.testing.assert_allclose(b, a, atol=1e-4 * np.abs(a).max())


def test_the_vector_and_partial_bias_in_xml_both_ways(tmp_path):
    F, csx = _cavity()
    xs = np.array([0.25, 5.0, 9.75])
    p = csx.GetPropertiesByName('fer')[0]
    t = {c: np.arange(9.0).reshape(3, 3, 1) * (k + 1) * 1e3
         for k, c in enumerate(('hx', 'hy', 'hz'))}
    t.update({c: np.full((3, 3, 1), v) for c, v in zip(('mx', 'my', 'mz'),
                                                        (0.1, 0.2, 0.3))})
    p.bias = dict(x=xs, y=xs, z=np.array([0.5]), **t)
    p.freq = 9.4e9
    path = str(tmp_path / 'm.xml')
    F.Write2XML(path)
    F2 = openEMS()
    F2.ReadFromXML(path)
    q = [x for x in F2.csx.GetAllProperties()
         if isinstance(x, C.FerriteMaterial)][0]
    assert q.freq == pytest.approx(9.4e9)
    for c in C.FerriteMaterial.TABLE:
        np.testing.assert_allclose(q.bias[c], p.bias[c], rtol=1e-6)
    p.bias = np.array([1e4, -2e4, 3e4])
    F.Write2XML(path)
    F2 = openEMS()
    F2.ReadFromXML(path)
    q = [x for x in F2.csx.GetAllProperties()
         if isinstance(x, C.FerriteMaterial)][0]
    np.testing.assert_allclose(q.bias, [1e4, -2e4, 3e4], rtol=1e-6)
