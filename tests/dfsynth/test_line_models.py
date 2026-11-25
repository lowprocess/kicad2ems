"""dfsynth's closed-form line models against kicad2ems's own
(kicad2ems.core.lines): the same microstrip must come out the same in the
synthesis and in the line model that validates it. dfsynth keeps its copies so
it runs without kicad2ems; this test is what keeps the two from drifting
apart."""
import itertools

import pytest

from kicad2ems.dfsynth import microstrip as ms
from kicad2ems.dfsynth.microstrip import Substrate

k2e = pytest.importorskip('kicad2ems')
try:
    from kicad2ems.core.lines import formulas
except ImportError:  # kicad2ems before the tlines split
    from kicad2ems.core.lines import tlines as formulas
from kicad2ems.core.lines import coupled

# w / h either side of H & J's u = 1 branch
U = (0.05, 0.3, 1.0, 1.5, 3.0, 10.0)
ER = (2.2, 3.55, 4.4, 9.8)


def test_constants():
    assert ms.ETA0 == formulas.ETA0 and ms.MU0 == formulas.MU0


@pytest.mark.parametrize('u, er', list(itertools.product(U, ER)))
def test_hammerstad_jensen(u, er):
    assert ms._hj_z01(u) == formulas._hj_z01(u)
    assert ms._hj_eeff(u, er) == formulas._hj_eeff(u, er)


@pytest.mark.parametrize('t', (0.0, 0.018, 0.035, 0.07))
def test_microstrip(t):
    for name in ('RO4003C-0.508', 'RT5880-0.254', 'Alumina-0.635'):
        sub = Substrate.preset(name, t=t)
        for u in U:
            w = u * sub.h
            assert (sub.line(w) == pytest.approx(
                formulas.microstrip(w, sub.h, sub.er, t), rel=1e-12))


def test_coupled_microstrip():
    """Kirschning & Jansen's coupled pair: dfsynth's Substrate.coupled and
    kicad2ems's coupled_microstrip."""
    for name in ('RO4003C-0.508', 'RO3010-0.635'):
        sub = Substrate.preset(name, t=0)
        for u, g in itertools.product((0.2, 1.0, 3.0), (0.05, 0.3, 1.0, 3.0)):
            w, s = u * sub.h, g * sub.h
            assert (sub.coupled(w, s) == pytest.approx(
                coupled.coupled_microstrip(w, s, sub.h, sub.er), rel=1e-12))


@pytest.mark.parametrize('r', [0.5, 1.0, 2.0])
def test_the_gap_model_is_continuous_across_its_two_fits(r):
    """Garg & Bahl's even-mode fits meet at s/w = 0.3 by a jump (Ce 30 % up at
    w/h 1, an end-coupled filter's coupling up to 12 % off across it): blended
    over Substrate.GAP_BLEND, continuous and Cg falling with the gap (gap_for
    bisects it); the published fits outside the blend."""
    import math
    import numpy as np
    from kicad2ems.dfsynth.microstrip import Substrate
    sub = Substrate(h=0.2, er=4.4)
    w = r * sub.h
    cp = lambda sw: sub.gap(w, sw * w)
    a, b = cp(0.3 - 1e-9), cp(0.3 + 1e-9)
    assert a[0] == pytest.approx(b[0], rel=1e-6)
    assert a[1] == pytest.approx(b[1], rel=1e-6)
    sws = np.geomspace(0.1, 1.0, 400)
    cg = np.array([cp(x)[1] for x in sws])
    assert (np.diff(cg) < 0).all()
    steps = np.abs(np.diff(np.log([cp(x)[0] for x in sws])))
    assert steps.max() < 3 * np.median(steps)   # no jump left
    # the published fits outside the blend
    def published(sw):
        mo = r * (0.619 * math.log10(r) - 0.3853)
        ko = 4.26 - 1.453 * math.log10(r)
        me, ke = ((0.8675, 2.043 * r ** 0.12) if sw <= 0.3 else
                  (1.565 / r ** 0.16 - 1, 1.97 - 0.03 / r))
        k = w * 1e-15
        co = sw ** mo * math.exp(ko) * (4.4 / 9.6) ** 0.8 * k
        ce = 12 * sw ** me * math.exp(ke) * (4.4 / 9.6) ** 0.9 * k
        return 0.5 * ce, 0.5 * co - 0.25 * ce
    for sw in (0.15, 0.24, 0.36, 0.8):
        assert cp(sw) == pytest.approx(published(sw), rel=1e-12)
