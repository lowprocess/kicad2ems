"""dfsynth's closed-form line models against kicad2ems's own (kicad2ems.lines): the same microstrip must
come out the same in the synthesis and in the line model that validates it. dfsynth keeps its copies so it
runs without kicad2ems; this test is what keeps the two from drifting apart."""
import itertools

import pytest

from dfsynth import microstrip as ms
from dfsynth.microstrip import Substrate

k2e = pytest.importorskip('kicad2ems')
try:
    from kicad2ems.lines import formulas
except ImportError:                                                 # kicad2ems before the tlines split
    from kicad2ems.lines import tlines as formulas
from kicad2ems.lines import coupled

U = (0.05, 0.3, 1.0, 1.5, 3.0, 10.0)                                # w / h either side of H & J's u = 1 branch
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
            assert sub.line(w) == pytest.approx(formulas.microstrip(w, sub.h, sub.er, t), rel=1e-12)


def test_coupled_microstrip():
    """Kirschning & Jansen's coupled pair: dfsynth's Substrate.coupled and kicad2ems's coupled_microstrip."""
    for name in ('RO4003C-0.508', 'RO3010-0.635'):
        sub = Substrate.preset(name, t=0)
        for u, g in itertools.product((0.2, 1.0, 3.0), (0.05, 0.3, 1.0, 3.0)):
            w, s = u * sub.h, g * sub.h
            assert sub.coupled(w, s) == pytest.approx(coupled.coupled_microstrip(w, s, sub.h, sub.er), rel=1e-12)
