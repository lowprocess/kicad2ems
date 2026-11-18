"""The adaptive frequency sweep (emsdev.afs) that mom25d and pcbqs share: a
rational model of all S entries, solved where it is least sure."""
import numpy as np
import pytest

afs = pytest.importorskip('kicad2ems.emsdev.afs')  # (numpy: what it needs)


def resonator(f, f0=3e9, q=40):
    """A series RLC between two 50 ohm ports: S (2, 2) at f, its resonance at f0
    (q: its reactance over 50 ohm)."""
    s = 2j * np.pi * f
    w0 = 2 * np.pi * f0
    L = q * 50 / w0
    C = 1 / (w0 * w0 * L)
    z = 5.0 + s * L + 1 / (s * C)  # 5 ohm of loss
    s21 = 100 / (100 + z)
    return np.array([[1 - s21, s21], [s21, 1 - s21]])


def test_a_rational_function_is_fitted_exactly():
    z = np.linspace(0.5, 6, 40)
    F = np.stack([1 / (z - (2 + 0.1j)), (z + 1) / (z * z + 1)], 1)
    r = afs.aaa(z, F)
    zz = np.linspace(0.6, 5.9, 301)
    want = np.stack([1 / (zz - (2 + 0.1j)), (zz + 1) / (zz * zz + 1)], 1)
    assert np.abs(r(zz) - want).max() < 1e-8 and len(r.support) < 12


def test_the_sweep_finds_a_resonance_between_its_first_samples():
    """A narrow resonance the five first samples step over: the sweep solves
    around it until two models agree, with fewer solves than the grid has
    points; a solve that gives NaN is said, with the caller's hint."""
    grid = np.linspace(1e9, 6e9, 801)
    solves = []
    g, S, fs, Ss, hist = afs.adaptive_sweep(
        lambda f: solves.append(f) or resonator(f), 1e9, 6e9, grid=grid,
        log=lambda *a: None)
    truth = np.array([resonator(f) for f in grid])
    assert (hist[-1]['converged'] and len(fs) < 40
            and np.abs(S - truth).max() < 5e-3)
    assert (np.abs(S[:, 1, 0]).max() == pytest.approx(
        np.abs(truth[:, 1, 0]).max(), rel=1e-3))
    with pytest.raises(FloatingPointError,
                       match=r'non-finite S-parameters \(try this\)'):
        afs.adaptive_sweep(lambda f: resonator(f) * np.nan, 1e9, 6e9,
                           log=lambda *a: None, hint='try this')


def lines(f, n=3, noise=0.0):
    """n matched lines of different lengths side by side (2 n ports, nothing
    between the lines): S (2n, 2n) at f, each solve with its own noise."""
    S = np.zeros((2 * n, 2 * n), complex)
    for k in range(n):
        t = np.exp(-(0.02 + 2j * np.pi) * f * (0.10 + 0.03 * k) * 1e-9)
        S[2 * k, 2 * k + 1] = S[2 * k + 1, 2 * k] = t
    rng = np.random.default_rng(int(f) % 2**31)
    return S + noise * (rng.standard_normal(S.shape)
                        + 1j * rng.standard_normal(S.shape))


def test_many_ports_and_noisy_solves_do_not_cost_the_sweep_its_model():
    """Six ports (36 entries) of solves with a mixed-precision solve's noise
    (1e-4): the model keeps fewer supports than samples (with one at every
    sample it was a constant until 37 solves: rf_bends' sweep stopped at its
    60, a 16-port array's never agreed), stops at the noise's level (fitted to
    1e-10 it put poles between the samples), leaves the entries at that level
    out (straight lines between their samples), and the sweep ends with a
    solve in its widest gap."""
    z = np.linspace(1, 6, 12)
    F = np.array([lines(f * 1e9, noise=1e-4).ravel() for f in z])
    r = afs.aaa(z, F, afs.FIT)
    assert len(r.support) < len(z)
    zz = np.linspace(1.1, 5.9, 97)
    want = np.array([lines(f * 1e9).ravel() for f in zz])
    assert np.abs(r(zz) - want).max() < 2e-3
    assert np.abs(r(z)[:, 0] - F[:, 0]).max() == 0  # (S11: its samples)
    grid = np.linspace(1e9, 6e9, 401)
    g, S, fs, Ss, hist = afs.adaptive_sweep(
        lambda f: lines(f, noise=1e-4), 1e9, 6e9, grid=grid, tol=5e-3,
        max_points=60, log=lambda *a: None)
    truth = np.array([lines(f) for f in grid])
    assert hist[-1]['converged'] and len(fs) <= 16
    assert np.abs(S - truth).max() < 5e-3
    # the last solve: the middle of what was then the widest gap
    rest = np.sort(fs[fs != hist[-2]['f_new']])
    i = np.argmax(np.diff(rest))
    assert hist[-2]['f_new'] == pytest.approx((rest[i] + rest[i + 1]) / 2)
