"""The radiated EMC questions' shared parts (kicad2ems.core.radiated) against
closed forms: a clock's and a PRBS's lines against their waveforms' Fourier
series, CISPR 32's limits, the test site's two rays against a dipole and its
image, the far field's grid against the field it samples, the worst linear
polarisation; and both tools run in this process on an analytic board (a short
element over the ground, its field and admittance in closed form: no solver).
mom25d's own checks (reciprocity against a plane wave, Friis): tests/mom25d/
test_radiated.py."""
import json
import math
import os
from types import SimpleNamespace as NS

import numpy as np
import pytest

from kicad2ems.core import radiated as R
from kicad2ems.core.constants import C0, ETA0


def _waveform_lines(x, n_period, harmonics):
    """RMS of the first harmonics of one period sampled as x (n_period
    samples)."""
    X = np.fft.rfft(x[:n_period]) / n_period
    return np.sqrt(2) * np.abs(X[1:harmonics + 1])


def test_a_clocks_lines_are_its_trapezoids_fourier_series():
    f0, amp, rise, duty = 25e6, 3.3, 1e-9, 0.4
    T, N = 1 / f0, 200000
    t = np.arange(N) * T / N
    tr = rise / 0.8
    # 0 -> amp from 0 to tr, high to duty T (the half-swing width), down by tr
    up = np.clip(t / tr, 0, 1)
    down = np.clip((t - duty * T) / tr, 0, 1)
    x = amp * (up - down)
    want = _waveform_lines(x, N, 40)
    f, v = R.trapezoid(f0, amp, rise, duty, fmin=0, fmax=40 * f0)
    got = np.zeros(40)
    got[np.rint(f / f0).astype(int) - 1] = v
    assert np.abs(got - want).max() < 1e-4 * amp
    # 50 %: the even harmonics are not there
    f, _ = R.trapezoid(f0, amp, rise, 0.5, fmin=0, fmax=10 * f0)
    assert np.allclose(f / f0, [1, 3, 5, 7, 9])


def test_a_prbs_lines_are_its_spectrum():
    from kicad2ems.core.timedomain import prbs
    order, rate, amp, rise = 7, 1e9, 1.0, 100e-12
    L, over = 2 ** order - 1, 64
    bits = prbs(order, L)
    tr = rise / 0.8
    nr = int(round(tr * rate * over))
    # NRZ, each edge a linear ramp of tr (centred on the bit boundary: a
    # moving average of tr)
    x = np.repeat(bits.astype(float), over) * amp
    k = np.ones(nr) / nr
    xx = np.concatenate([x, x])
    y = np.convolve(xx, k, mode='same')[len(x) // 2:len(x) // 2 + len(x)]
    want = _waveform_lines(np.roll(y, 0), len(y), 300)
    f, v = R.prbs_lines(rate, amp, rise, order, 0, 300 * rate / L)
    got = np.zeros(300)
    got[np.rint(f / (rate / L)).astype(int) - 1] = v
    assert np.abs(got - want).max() < 2e-3 * want.max()
    # the lines' density is random data's PSD as L grows: (L + 1) / L
    fl, vl = R.prbs_lines(rate, amp, rise, 15, 1e8, 1.2e8)
    L15 = 2 ** 15 - 1
    psd = vl ** 2 / (rate / L15)
    assert np.allclose(psd, R.pattern_psd(fl, rate, amp, rise) * (L15 + 1)
                       / L15, rtol=1e-9)


def test_a_signal_reads_lines_or_its_psd_in_the_bandwidth():
    s = R.Signal('pattern', rate=1e9, amp=1.0, rise=1e-10, prbs=7)
    f, v = s.lines(30e6, 1e9)           # 7.9 MHz apart: lines
    assert len(f) and np.allclose(np.diff(f), 1e9 / 127)
    s23 = R.Signal('pattern', rate=1e9, amp=1.0, rise=1e-10, prbs=23)
    assert s23.lines(30e6, 1e9) is None  # 0.12 Hz apart: a PSD
    r = s23.reading(np.array([100e6, 2e9]))
    assert np.allclose(r ** 2, R.pattern_psd([100e6, 2e9], 1e9, 1.0, 1e-10)
                       * np.array([120e3, 1e6]))
    assert np.allclose(R.Signal('flat', amp=0.5).reading([1e8, 1e9]), 0.5)
    with pytest.raises(ValueError, match='frequency'):
        R.Signal('clock')


def test_cispr32_limits():
    b10 = R.limits('B', 10)
    assert b10['qp'] == [(30e6, 230e6, 30.0), (230e6, 1e9, 37.0)]
    assert [v for *_, v in R.limits('B', 3)['qp']] == [40.0, 47.0]
    assert [v for *_, v in R.limits('A', 3)['av']] == [56.0, 60.0]
    # 10 m above 1 GHz: the 3 m limits less 20 log10(10 / 3)
    assert np.isclose(R.limits('B', 10)['av'][0][2], 50 - 20 * math.log10(10 / 3))
    # 1 m below: from 10 m
    assert np.isclose(R.limits('B', 1)['qp'][0][2], 50.0)
    lim = R.limit_at([20e6, 100e6, 230e6, 500e6, 2e9], b10['qp'])
    assert np.isnan(lim[0]) and list(lim[1:4]) == [30.0, 30.0, 37.0]
    assert np.isnan(lim[4])


def test_the_worst_linear_polarisation_is_the_largest_over_angles():
    rng = np.random.default_rng(3)
    Vt = rng.normal(size=50) + 1j * rng.normal(size=50)
    Vp = rng.normal(size=50) + 1j * rng.normal(size=50)
    a = np.radians(np.arange(0, 180, 0.01))
    brute = np.abs(Vt[:, None] * np.cos(a) + Vp[:, None] * np.sin(a)).max(1)
    w, ang = R.worst_linear(Vt, Vp)
    assert np.allclose(w, brute, rtol=1e-6)
    at = np.abs(Vt * np.cos(np.radians(ang)) + Vp * np.sin(np.radians(ang)))
    assert np.allclose(at, w, rtol=1e-9)


def _dipole(p):
    """r E (m, 3) of a dipole p (its far field's shape: the part of p across
    each direction), in the board's directions u."""
    p = np.asarray(p, float)
    return lambda u: p[None] - (u @ p)[:, None] * u


def test_the_site_is_a_dipole_and_its_image():
    """site() with the board flat and still: the direct ray and the floor's,
    against a dipole at h_eut and its image (-px, -py, pz) at -h_eut, each
    one's far field at the antenna."""
    f, d, h0 = 300e6, 3.0, 0.8
    k = 2 * np.pi * f / C0
    hs = np.array([1.0, 1.7, 2.5, 4.0])
    for p in ([0, 0, 1.0], [1.0, 0, 0], [0, 1.0, 0], [0.3, -0.5, 0.8]):
        p = np.array(p)
        img = p * np.array([-1, -1, 1])
        for h in hs:
            ant = np.array([d, 0, h])
            E = 0
            for src, mom in ((np.array([0, 0, h0]), p),
                             (np.array([0, 0, -h0]), img)):
                r = ant - src
                L = np.linalg.norm(r)
                u = r / L
                E = E + (mom - (u @ mom) * u) * np.exp(-1j * k * L) / L
            s = R.site(_dipole(p), f, d, h0, [h], [0.0], ('flat',), True)
            assert np.isclose(s['E'], max(abs(E[2]), abs(E[1])), rtol=1e-12)
            s = R.site(_dipole(p), f, d, h0, [h], [0.0], ('flat',), False)
            L = math.hypot(d, h - h0)
            u = np.array([d, 0, h - h0]) / L
            Ed = (p - (u @ p) * u) / L
            assert np.isclose(s['E'], max(abs(Ed[2]), abs(Ed[1])), rtol=1e-12)
    # two_ray is the same sum
    p = np.array([0, 0, 1.0])
    ud = np.array([d, 0, 1.2 - h0]) / math.hypot(d, 1.2 - h0)
    ur = np.array([d, 0, -1.2 - h0]) / math.hypot(d, 1.2 + h0)
    E = R.two_ray(_dipole(p)(ud[None])[0], _dipole(p)(ur[None])[0], f, d, h0,
                  1.2)
    s = R.site(_dipole(p), f, d, h0, [1.2], [0.0], ('flat',), True)
    assert np.isclose(abs(E[2]), s['E'])


def test_the_turntable_and_the_orientations_find_the_worst():
    """A dipole along the board's x, the antenna level with it, no floor: it
    radiates most across x, which the turntable brings round (90 deg); stood on
    its x edge it points at the zenith: across the antenna's line it is
    vertical."""
    f, d = 1e9, 3.0
    s = R.site(_dipole([1.0, 0, 0]), f, d, 0.8, [0.8], np.arange(0, 360, 5.0),
               ('flat',), False)
    assert np.isclose(s['E'], 1 / d) and s['turn'] % 180 == 90
    assert s['pol'] == 'horizontal'
    s = R.site(_dipole([1.0, 0, 0]), f, d, 0.8, [0.8], [0.0], ('edge-x',),
               False)
    assert np.isclose(s['E'], 1 / d) and s['pol'] == 'vertical'
    s = R.site(_dipole([1.0, 0, 0]), f, d, 0.8, [0.8], [0.0],
               tuple(R.ORIENTATIONS), False)
    assert set(s['E_by_orientation']) == set(R.ORIENTATIONS)


def test_the_grid_samples_a_field_and_is_zero_below_a_half_space():
    rng = np.random.default_rng(1)
    for lower in (True, False):
        th, ph = R.grid_angles(3.0, lower)
        T, P = np.meshgrid(th, ph, indexing='ij')
        u = R.unit(T, P)
        # a field with some structure: two dipoles a wavelength apart
        k = 2 * np.pi
        F = (_dipole([1.0, 0.3, 0.2])(u.reshape(-1, 3)) *
             (1 + 0.7 * np.exp(1j * k * u.reshape(-1, 3) @ [1.0, 0, 0]))[:, None])
        G = R.Grid(th, ph, F.reshape(T.shape + (3,))[None])
        q = rng.normal(size=(400, 3))
        q /= np.linalg.norm(q, axis=1)[:, None]
        want = (_dipole([1.0, 0.3, 0.2])(q) *
                (1 + 0.7 * np.exp(1j * k * q @ [1.0, 0, 0]))[:, None])
        got = G.at(q)[0]
        up = q[:, 2] >= 0
        sel = up if not lower else np.ones(len(q), bool)
        err = np.linalg.norm(got[sel] - want[sel], axis=1).max()
        assert err < 0.01 * np.linalg.norm(want, axis=1).max()
        if not lower:
            assert not got[q[:, 2] < -1e-6].any()


def test_the_received_voltage_of_a_short_element_is_its_effective_length():
    """receive_grid on the far field of a short element h over the ground with
    its image (r E = j eta k I 2h sin(theta) / 4 pi per unit source volt, I = 1
    / (z + Zin)): E0 2h sin(theta) z / (z + Zin)."""
    f, h, z, zin = 1e9, 1e-3, 50.0, -1300j
    k = 2 * np.pi * f / C0
    I = 1 / (z + zin)
    th, ph = R.grid_angles(5.0, False)
    T, P = np.meshgrid(th, ph, indexing='ij')
    Et = 1j * ETA0 * k * I * 2 * h * np.sin(np.radians(T)) / (4 * np.pi)
    E = R.cartesian(Et, 0 * Et, T, P)
    Vt, Vp = R.receive_grid(R.Grid(th, ph, E[None]), [z], f, E0=3.0)
    want = 3.0 * 2 * h * np.sin(np.radians(T)) * abs(z / (z + zin))
    assert np.allclose(np.abs(Vt[0]), want, rtol=1e-12, atol=1e-18)
    assert np.abs(Vp).max() < 1e-12 * want.max()


class _Element(R.PortFields):
    """A board without a solver: P1, a short element h over the ground (its
    admittance and field in closed form), and P2 a second one d away along x,
    not coupled (each sees its own)."""

    def __init__(self, h=1e-3, zin=-1300j, dx=0.05):
        super().__init__(['P1', 'P2'], [50.0, 50.0], False, 'analytic')
        self.h, self.zin, self.dx = h, zin, dx
        self.unknowns, self.pairs = 2, []

    def grid(self, f, step, drive=None):
        k = 2 * np.pi * f / C0
        th, ph = R.grid_angles(step, False)
        T, P = np.meshgrid(th, ph, indexing='ij')
        I = 1 / (self.loads + self.zin)
        u = R.unit(T, P)
        E = []
        for j, x in enumerate((0.0, self.dx)):
            Et = (1j * ETA0 * k * I[j] * 2 * self.h * np.sin(np.radians(T))
                  / (4 * np.pi) * np.exp(1j * k * x * u[..., 0]))
            E.append(R.cartesian(Et, 0 * Et, T, P))
        E = np.array(E)
        if drive is not None:
            E = np.tensordot(np.asarray(drive, complex), E, 1)[None]
        return R.Grid(th, ph, E)


def test_susceptibility_runs_and_reports(tmp_path, monkeypatch):
    from kicad2ems.core import susceptibility as S
    monkeypatch.setattr(R, 'port_fields', lambda a, out, log: _Element())
    a = S.parser().parse_args(['x.kicad_pcb', '--out', str(tmp_path),
                               '--points', '5', '--threshold', '5'])
    res = S.run(a, echo=lambda *x: None)
    v = res['per_volt_per_metre']['P1']['v']
    # at grazing, vertically polarised: 2h z / |z + Zin| per V/m
    assert np.allclose(v, 2e-3 * 50 / abs(50 - 1300j), rtol=1e-9)
    assert res['per_volt_per_metre']['P1']['theta'] == [90.0] * 5
    top = [t for t in res['table'] if t['field'] == 10]
    assert np.isclose(top[0]['mv'], 10 * v[0] * 1e3)
    assert res['flagged'] == [] and os.path.isfile(
        tmp_path / 'x_susceptibility.png')
    an = S.Susceptibility()
    rep = an.report(str(tmp_path))
    assert 'worst' in rep['summary'] and rep['images']
    figs = {d['name']: d['value'] for d in an.figures(str(tmp_path))}
    assert np.isclose(figs['worst at 10 V/m'], top[0]['mv'])
    assert figs['ports over the threshold'] == 0
    assert an.curves(str(tmp_path))[0]['curves'][0]['name'] == 'P1'
    cmd = an.command(dict(field='3,10', threshold=5, load=['P2=1e6']),
                     'b.kicad_pcb', 'out')
    assert cmd[2:4] == ['kicad2ems.core.susceptibility', 'b.kicad_pcb']
    assert ['--load', 'P2=1e6'] == cmd[-2:] and '--threshold' in cmd


def test_emission_runs_against_the_limits(tmp_path, monkeypatch):
    """A 50 MHz clock on P1 (an element over the ground): each line's field
    the solved field per volt times the line's RMS volts; the limits and the
    margin."""
    from kicad2ems.core import emission as EM
    monkeypatch.setattr(R, 'port_fields', lambda a, out, log: _Element())
    a = EM.parser().parse_args(
        ['x.kicad_pcb', '--out', str(tmp_path), '--f0', '50', '--fmax', '1',
         '--orientation', 'flat', '--heights', '1,4,0.5', '--turn-step', '30'])
    res = EM.run(a, echo=lambda *x: None)
    assert res['exact'] and res['kind'] == 'lines'
    f, v = R.trapezoid(50e6, 3.3, 1e-9, 0.5, 30e6, 1e9)
    em = res['emission']
    assert np.allclose(em['freq'], f)
    per_v = 10 ** (np.asarray(res['solved']['per_volt_dbuv']) / 20) * 1e-6
    assert np.allclose(10 ** (np.asarray(em['e_dbuv']) / 20) * 1e-6,
                       per_v * v, rtol=1e-9)
    lim = R.limit_at(f, R.limits('B', 3)['qp'])
    assert np.allclose(em['margin'], lim - np.asarray(em['e_dbuv']))
    w = res['worst']
    assert np.isclose(w['margin_db'], min(em['margin']))
    an = EM.Emission()
    rep = an.report(str(tmp_path))
    assert 'margin' in rep['summary'] and rep['sections'][0]['table']['rows']
    figs = {d['name']: d['value'] for d in an.figures(str(tmp_path))}
    assert np.isclose(figs['margin'], w['margin_db'])
    with pytest.raises(ValueError, match="clock's frequency"):
        an.command(dict(signal='clock'), 'b.kicad_pcb', 'out')
    cmd = an.command(dict(signal='clock', f0=25, cispr_class='A'),
                     'b.kicad_pcb', 'out')
    assert cmd[-2:] == ['--class', 'A'] and '--f0' in cmd
    # interpolated between solved frequencies when the lines outnumber them
    a = EM.parser().parse_args(
        ['x.kicad_pcb', '--out', str(tmp_path / 'b'), '--f0', '10',
         '--points', '7', '--orientation', 'flat', '--heights', '1,4,0.5',
         '--turn-step', '30', '--fmax', '1'])
    res = EM.run(a, echo=lambda *x: None)
    assert not res['exact'] and len(res['solved']['freq']) == 7
    assert any('interpolated' in w for w in res['warnings'])


def test_adaptive_sampling_follows_the_peaks():
    """A field rising 20 dB a decade with a Q 100 resonance at 1.234 GHz: the
    samples gather at the peak (its height within 0.5 dB, which 61 equally
    spaced miss by 4.9 dB); a straight curve gets none beyond
    the start."""
    f0, Q = 1.234e9, 100.0

    def curve(f):
        return f / 1e8 * (1 + 10 / abs(1 + 1j * Q * (f / f0 - f0 / f)))
    fs, vs = R.adaptive(curve, 30e6, 6e9, 61, start=15)
    fine = np.geomspace(30e6, 6e9, 20001)
    true = max(curve(x) for x in fine)
    assert 20 * np.log10(true / vs.max()) < 0.5
    fixed = R.log_grid(30e6, 6e9, 61)
    assert 20 * np.log10(true / max(curve(x) for x in fixed)) > 3
    # gathered at the peak: a third of them within 10 % of it
    assert np.sum(np.abs(fs / f0 - 1) < 0.1) >= len(fs) // 3
    fs, _ = R.adaptive(lambda f: f / 1e8, 30e6, 6e9, 61, start=15)
    assert len(fs) == 15
