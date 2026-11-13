"""One band from a quasi-static solver and a full-wave one (join.py)."""
import numpy as np
import pytest

from kicad2ems.join import join, pick, weight
from kicad2ems.solvers import Result


def test_the_blend_is_one_side_or_the_other_outside_its_band():
    f = np.geomspace(1e6, 1e10, 401)
    w = weight(f, 1e8)
    assert (w[f < 1e8 / 10 ** 0.2] == 0).all() and (w[f > 1e8 * 10 ** 0.2] == 1).all()
    assert weight(np.array([1e8]), 1e8)[0] == pytest.approx(0.5)
    assert (np.diff(w) >= 0).all()                                  # monotone: no wiggle in the join


def test_two_answers_joined_and_how_far_they_disagree():
    f = np.geomspace(1e6, 1e10, 201)
    lo = np.full((len(f), 2, 2), 0.1 + 0j)
    hi = np.full((len(f), 2, 2), 0.3 + 0j)
    S, info = join(f, lo, hi, 1e8)
    assert np.allclose(S[f < 6e7], 0.1) and np.allclose(S[f > 1.6e8], 0.3)
    assert ((S.real >= 0.1 - 1e-12) & (S.real <= 0.3 + 1e-12)).all()
    assert info['mismatch'] == pytest.approx(0.2) and info['crossover_Hz'] == 1e8
    assert info['blend_Hz'] == pytest.approx([1e8 / 10 ** 0.2, 1e8 * 10 ** 0.2])
    assert info['low_points'] + info['high_points'] < len(f)
    same, info = join(f, lo, lo.copy(), 1e8)                        # where they agree: seamless
    assert np.allclose(same, 0.1) and info['mismatch'] == 0
    hi_nan = hi.copy(); hi_nan[:, 0, 1] = np.nan                    # a term the full-wave one did not compute
    S, _ = join(f, lo, hi_nan, 1e8)
    edge = 1e8 / 10 ** 0.2                                          # where the blend starts
    assert np.isfinite(S[f <= edge, 0, 1]).all() and np.isnan(S[f > edge, 0, 1]).all()


def test_which_two_are_joined():
    class P:
        def __init__(self, consumes='board'):
            self.consumes = consumes
    r = lambda names=('P1', 'P2'), **info: Result(np.zeros((3, 2, 2)), list(names), [50, 50], 1.0, info)
    plugins = dict(openems=P(), mom25d=P(), pcbqs=P(), qucs=P('network'))
    assert pick(dict(openems=r(), mom25d=r()), plugins) is None                       # no quasi-static one
    assert pick(dict(pcbqs=r(valid_to_Hz=2e8)), plugins) is None                      # nothing to join it to
    assert pick(dict(qucs=r(), pcbqs=r(valid_to_Hz=2e8)), plugins) is None            # a line model is neither
    assert pick(dict(openems=r(), mom25d=r(), pcbqs=r(valid_to_Hz=2e8)), plugins) == ('pcbqs', 'mom25d', 2e8)
    assert pick(dict(openems=r(), pcbqs=r(valid_to_Hz=2e8)), plugins) == ('pcbqs', 'openems', 2e8)
    assert pick(dict(mom25d=r(names=('P1d', 'P1c')), pcbqs=r(valid_to_Hz=2e8)), plugins) is None    # other ports
