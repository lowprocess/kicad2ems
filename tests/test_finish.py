"""--finish (kicad2ems.finish): KiCad's names, the finished face's impedance, the share of a line's loss on its
finished faces, and a run's Finish (the stackup's, none under mask or without --sigma)."""
from types import SimpleNamespace

import numpy as np
import pytest

from kicad2ems import finish as F


def test_kicad_names():
    assert F.kicad_name('Immersion gold') == ('enig', None) and F.kicad_name('ENIG')[0] == 'enig'
    assert F.kicad_name('HAL lead-free')[0] == 'hasl' and F.kicad_name('HAL SnPb')[0] == 'hasl-snpb'
    assert F.kicad_name('None') == ('none', None) and F.kicad_name(None)[0] == 'none'
    name, note = F.kicad_name('Hard gold')
    assert name == 'none' and 'not modelled' in note


def test_face_factor():
    """ENIG's nickel: 2-3x bare copper's face resistance at 1 GHz, 4-6x from 3 to 10; silver none; bare 1. The
    resistance ratio, not the real part of the impedance ratio (another phase: 3.5 at 1 GHz)."""
    f = np.array([1e9, 3e9, 10e9])
    enig = F.resistance_ratio(f, F.layers('enig'), 1.0)
    assert F.factor(1e9, F.layers('enig')).real > 3
    assert 2 < enig[0] < 3 and np.all(enig[1:] > 4) and np.all(enig < 6)
    assert np.allclose(F.resistance_ratio(f, F.layers('imag'), 1.0), 1, atol=0.02)
    assert np.allclose(F.factor(f, F.layers('none')), 1)
    assert F.weighted(3e9, F.layers('enig'), 0.0) == pytest.approx(1)
    s = F.band_sigma(5.8e7, F.layers('enig'), 0.4, 1e9, 5e9)
    k = F.resistance_ratio(3e9, F.layers('enig'), 0.4)
    assert s == pytest.approx(5.8e7 / k ** 2)


def test_loss_share():
    """A microstrip's finished faces (the outer face and the sides) take 40-50 % of the strip's loss, less of the
    line's (the plane's face is bare); a wide strip less."""
    strip, line = F.loss_share(0.368, 0.2104, 0.035)
    assert 0.40 < strip < 0.50 and line < strip
    assert F.loss_share(3.0, 0.5, 0.035)[0] < strip


def _board(finish='ENIG'):
    port = SimpleNamespace(layer='F.Cu', w=0.4, z_sig=0.2, z_ref=0.0)
    return SimpleNamespace(copper_finish=finish, cu_order=['B.Cu', 'F.Cu'], cu_z={'B.Cu': 0.0, 'F.Cu': 0.2},
                           cu_t={'B.Cu': 0.035, 'F.Cu': 0.035}, ports=[port])


def test_resolve():
    s = SimpleNamespace(finish='auto', sigma=5.8e7, mask=False)
    fin = F.resolve(s, _board())
    assert fin and fin.name == 'enig' and fin.share('F.Cu') > 0.3 and fin.share('B.Cu') == 0.0  # a plane: none
    assert F.resolve(SimpleNamespace(finish='auto', sigma=None, mask=False), _board()) is None
    assert F.resolve(SimpleNamespace(finish='auto', sigma=5.8e7, mask=False), _board('None')) is None
    masked = F.resolve(SimpleNamespace(finish='enig', sigma=5.8e7, mask=True), _board('None'))
    assert not masked and 'mask' in masked.notes[0]
    with pytest.raises(ValueError):
        F.resolve(SimpleNamespace(finish='gold', sigma=5.8e7, mask=False), _board())


def test_line_model_takes_the_finish():
    from kicad2ems.solvers import registry, FEATURES
    from kicad2ems.solvers.lines.plugin import line_sigma
    assert 'finish' in FEATURES and registry()['scikit'].capabilities['finish'].startswith('approximated')
    run = SimpleNamespace(s=SimpleNamespace(finish='auto', sigma=5.8e7, mask=False, rough=None), m=_board(),
                          freq=np.linspace(1e9, 5e9, 5))
    assert line_sigma(run) < 5.8e7 / 1.5
    run.m = _board('None')
    assert line_sigma(run) == 5.8e7
