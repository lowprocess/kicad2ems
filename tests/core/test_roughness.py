"""--rough: Hammerstad's factor, the equivalent conductivity, and the line model's use of it."""
import numpy as np
import pytest


def test_hammerstad_factor_and_equivalent_conductivity():
    from kicad2ems.roughness import hammerstad, rough_sigma, band_sigma
    from types import SimpleNamespace
    assert hammerstad(1e6, 1.0, 5.8e7) == pytest.approx(1.0, abs=1e-3)       # delta 66 um >> 1 um: smooth
    assert hammerstad(1e12, 1.0, 5.8e7) == pytest.approx(2.0, abs=1e-2)      # delta 66 nm << 1 um: twice the loss
    f = 5e9
    delta = 1 / np.sqrt(np.pi * f * 4e-7 * np.pi * 5.8e7)
    assert hammerstad(f, 1.0, 5.8e7) == pytest.approx(1 + 2 / np.pi * np.arctan(1.4 * (1e-6 / delta) ** 2))
    k = hammerstad(f, 1.0, 5.8e7)
    sr = rough_sigma(5.8e7, 1.0, f)                                 # Rs ~ 1 / sqrt(sigma): K x Rs
    assert np.sqrt(5.8e7 / sr) == pytest.approx(k)
    s = SimpleNamespace(sigma=5.8e7, rough=1.0)
    assert band_sigma(s, 1e9, 9e9) == pytest.approx(rough_sigma(5.8e7, 1.0, 5e9))
    assert band_sigma(SimpleNamespace(sigma=5.8e7, rough=None), 1e9, 9e9) == 5.8e7
    assert band_sigma(SimpleNamespace(sigma=None, rough=1.0), 1e9, 9e9) is None


def test_rough_option_and_capability_row():
    from kicad2ems.config import build_settings, ConfigError
    from kicad2ems.solvers import FEATURES, registry
    assert 'roughness' in FEATURES and registry()['scikit'].capabilities['roughness'].startswith('approximated')
    s = build_settings(dict(board='x.kicad_pcb', sigma=5.8e7, rough=1.5))
    assert s.rough == 1.5
    s = build_settings(dict(board='x.kicad_pcb', rough=1.5))
    assert any('--rough without --sigma' in n for n in s.notes)
    with pytest.raises(ConfigError):
        build_settings(dict(board='x.kicad_pcb', sigma=5.8e7, rough=-1))
