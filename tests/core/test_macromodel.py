"""The S-parameters' rational macromodel (kicad2ems.core.macromodel): vector
fitting against a known model and transmission lines in closed form, its DC
point, passivity, and its transient against closed forms and an FFT."""
import numpy as np
import pytest

from kicad2ems.core import macromodel as MM
from kicad2ems.core.deembed import abcd_to_s, line_abcd

C0 = 299792458.0
F = np.linspace(20e6, 20e9, 1000)


def _line(z=50.0, ee=3.5, length=0.1, tand=0.0, r=None, f=F):
    return abcd_to_s(line_abcd(z, ee, length, f, tand, r), 50.0)


def _lossy(f=F):
    """55 ohm, 100 mm, tan d 0.02, a conductor loss 5 sqrt(f / GHz) ohm/m."""
    return _line(55.0, 3.5, 0.1, 0.02, 5.0 * np.sqrt(f / 1e9), f)


def _step(rise):
    """A raised-cosine edge 0 -> 1, rise 10 to 90 %, from t = 0."""
    T = rise / 0.59
    return lambda t: 0.5 * (1 - np.cos(np.pi * np.clip(t / T, 0.0, 1.0)))


def test_a_known_rational_model_is_found_again():
    """S of 10 poles (5 pairs, a 2-port, reciprocal): fitted with 10 poles,
    the samples to 1e-8 and the poles found again."""
    rng = np.random.default_rng(3)
    w = 2 * np.pi * np.linspace(2e9, 18e9, 5)
    p = np.ravel([[-x / 20 + 1j * x, -x / 20 - 1j * x] for x in w])
    R = np.zeros((10, 2, 2), complex)
    for k in range(0, 10, 2):
        r = rng.normal(size=(2, 2)) + 1j * rng.normal(size=(2, 2))
        r = 0.02 * w[k // 2] * (r + r.T) / 2
        R[k], R[k + 1] = r, np.conj(r)
    true = MM.Model(p, R, 0.1 * np.eye(2), 50.0, 20e9)
    m = MM.fit(F, true.S(F), poles=10, passive=False, dc=False)
    assert np.abs(m.S(F) - true.S(F)).max() < 1e-8
    assert np.abs(np.sort_complex(m.poles) - np.sort_complex(p)).max() \
        < 1e-6 * np.abs(p).max()


def test_a_lossy_line_s_fit_and_dc():
    """The lossy line: within FIT_TOL, passive as fitted, its S(0) from the
    lowest samples (the line through at DC: S21 1 within 0.5 %, S11 0)."""
    S = _lossy()
    m = MM.fit(F, S)
    r = m.record()
    assert r['rms_error'] <= MM.FIT_TOL and r['passive'] and r['dc']
    S0 = m.S([0.0])[0]
    assert abs(S0[1, 0] - 1) < 5e-3 and abs(S0[0, 0]) < 5e-3


def test_a_lossless_line_is_made_passive():
    """A lossless line's largest singular value is 1 across the band, so a
    fit is a hair over it here and there: made passive, its samples within
    the margin (1e-3) and a little."""
    S = _line()
    m = MM.fit(F, S)
    r = m.record()
    assert r['passive'] and not r['passive_as_fitted']
    assert np.abs(m.S(F) - S).max() < 1.5e-3


def test_a_model_over_one_is_made_passive_or_refused():
    """A model 1 % and 10 % over passive in band: made passive by its
    samples projected and fitted again, each change at most the excess; past
    scale_max with no round left: refused."""
    m = MM.fit(F, _lossy(), passive=False)
    smax = m.passive()[1]
    for over in (1.01, 1.10):
        k = over / smax
        g = MM.Model(m.poles, m.R * k, m.D * k, 50.0, m.fmax)
        g.enforce()
        assert g.passive()[0]
        assert np.abs(g.S(F) - m.S(F) * k).max() < 1.2 * (over - 1)
    g = MM.Model(m.poles, m.R * 1.1 / smax, m.D * 1.1 / smax, 50.0, m.fmax)
    with pytest.raises(ValueError, match='not passive'):
        g.enforce(max_iter=0, scale_max=0.02)


@pytest.mark.parametrize('load, far', [(MM.Load(50.0), 0.5),
                                       (MM.Load(None), 1.0),
                                       (MM.Load(5.0), 2 * 5 / 55 * 0.5)])
def test_a_line_s_transient_is_the_closed_form(load, far):
    """The lossless line (delay 624 ps) driven by a 1 V step behind 50 ohm:
    the near end at 0.5 V until the reflection, the far end settled at the
    load's share (matched 0.5, open 1.0, 5 ohm 0.091), its half crossed at the
    delay plus half the edge, to a step."""
    m = MM.fit(F, _line())
    tau = 0.1 * np.sqrt(3.5) / C0
    rise, dt = 50e-12, 2e-12
    t = np.arange(0, 3e-9, dt)
    V, I = MM.simulate(m, [MM.Thevenin(_step(rise), 50.0), load], t)
    assert V[int(0.3e-9 / dt), 0] == pytest.approx(0.5, abs=3e-3)
    assert V[-1, 1] == pytest.approx(far, rel=3e-3)
    half = t[np.argmax(V[:, 1] >= 0.5 * V[-1, 1])]
    assert half == pytest.approx(tau + rise / 0.59 / 2, abs=1.5 * dt)
    # (the port's current: the source's)
    assert I[int(0.3e-9 / dt), 0] == pytest.approx(0.5 / 50, rel=5e-3)


def test_a_lossy_line_s_transient_is_its_fft():
    """The lossy line between matched ends, a 100 ps edge: the far end as the
    FFT of the closed form (V2 = S21 E / 2) gives it, within 0.5 % of the
    swing."""
    rise, dt = 100e-12, 1e-12
    t = np.arange(0, 6e-9, dt)
    m = MM.fit(F, _lossy())
    V, _ = MM.simulate(m, [MM.Thevenin(_step(rise), 50.0), MM.Load(50.0)], t)
    # the reference: a pulse long enough to settle, on a window twice as long
    n = 1 << 15
    tt = np.arange(n) * dt
    up, down = _step(rise), _step(rise)
    e = up(tt) - down(tt - 12e-9)
    fk = np.fft.rfftfreq(n, dt)
    S21 = np.ones(len(fk), complex)
    S21[1:] = _lossy(fk[1:])[:, 1, 0]
    v2 = np.fft.irfft(np.fft.rfft(e) * S21 / 2, n)
    assert np.abs(V[:, 1] - v2[:len(t)]).max() < 0.005 * 0.5
