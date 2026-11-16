"""The band-limited causality check (kicad2ems.core.causality, R-E2):
generalised dispersion relations with subtractions."""
import numpy as np

from kicad2ems.core.causality import check


def _line(f, delay, q=10.0, f0=2.4e9):
    """A causal two-port of a resonance and a delay: S21 its transmission, S11
    its reflection."""
    s21 = (np.exp(-2j * np.pi * f * delay) * 0.9 / (1 + 1j * q *
                                                    (f / f0 - f0 / f)))
    s11 = (0.3 * np.exp(-2j * np.pi * f * delay * 0.5) * 1j * q *
           (f / f0 - f0 / f) / (1 + 1j * q * (f / f0 - f0 / f)))
    S = np.zeros((len(f), 2, 2), complex)
    S[:, 0, 0] = S[:, 1, 1] = s11
    S[:, 1, 0] = S[:, 0, 1] = s21
    return S


def test_causal_data_pass_and_violations_are_caught():
    """A causal response from 1 GHz passes; time-reversed, or advanced past its
    own delay (a de-embedding past the true reference plane), it is caught; the
    term and frequency are named."""
    f = np.linspace(1e9, 4e9, 401)
    S = _line(f, 0.3e-9)
    assert check(f, S)['worst'] < 0.02
    rev = check(f, np.conj(S))
    assert (rev['worst'] > 0.3 and rev['term'] is not None
            and f[0] < rev['f'] < f[-1])
    adv = check(f, S * np.exp(2j * np.pi * f * 0.9e-9)[:, None, None])
    assert adv['worst'] > 0.1


def test_no_check_without_a_band_above_zero():
    assert check(np.linspace(0, 1e9, 101), np.zeros((101, 1, 1))) is None
