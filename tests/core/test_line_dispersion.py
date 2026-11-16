"""The line model's wideband dielectric (R-E4, lines.dispersion): the Djordjevic-Sarkar curve, a section's layers
at a frequency, the frozen copies, and the interpolated solve against a solve at each frequency's own materials."""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from kicad2ems.core.board import Board
from kicad2ems.core.importers.kicad import KiCadBoard
from kicad2ems.core.lines import dispersion as D
from kicad2ems.core.lines.elements import Element, LineModel
from kicad2ems.core.lines.tlines import build
from kicad2ems.core.materials import djordjevic_sarkar
from kicad2ems.core.solvers.lines.fastsolve import find_qucsator, solve

FREF = 1e9


def _qucsator():
    try:
        return find_qucsator()
    except ValueError:
        return None


def test_the_curve_holds_the_stackup_values_at_fref_and_is_the_fdtd_layers():
    e = djordjevic_sarkar(4.3, 0.02, FREF, np.array([FREF, 10e9]))
    assert e[0] == pytest.approx(4.3 * (1 - 0.02j), rel=1e-12)
    assert e[1].real < 4.3 and -e[1].imag / e[1].real == pytest.approx(0.02, rel=0.05)    # er falls, tand ~ flat
    fd = pytest.importorskip('kicad2ems.fdtd.dielectric')
    f = np.logspace(6, 11, 11)
    assert np.allclose(djordjevic_sarkar(3.66, 0.0037, 10e9, f), fd.djordjevic_sarkar(3.66, 0.0037, 10e9, f))


def test_a_section_takes_each_layer_s_own_curve_averaged_as_built():
    from kicad2ems.core.lines.reference import mix
    e = Element('trace', 'a', 'b', 10.0)
    e.diel = ((0.2, 3.66, 0.0037, 10e9), (0.1, 4.3, 0.02, None))     # RO4350B core, a generic prepreg (at fref)
    e.er, e.tand = mix(e.diel)
    assert D.section_at(e, FREF, FREF)[0] == pytest.approx(
        mix([(0.2, *D.layer_at(3.66, 0.0037, 10e9, FREF)), (0.1, 4.3, 0.02)])[0])
    lo, hi = D.section_at(e, 0.1e9, FREF), D.section_at(e, 20e9, FREF)
    assert lo[0] > hi[0] and lo[1] > 0 and hi[1] > 0
    lossless = Element('trace', 'a', 'b', 10.0)
    lossless.er, lossless.tand, lossless.diel = 4.0, 0.0, ((0.2, 4.0, 0.0, None),)
    assert D.section_at(lossless, 30e9, FREF) == (4.0, 0.0)


def test_a_frozen_copy_keeps_the_filling_factor_and_the_air_capacitance():
    e = Element('trace', 'p', 'q', 20.0, w=1.5)
    e.xs, e.z0, e.eeff, e.er, e.tand, e.h = 'microstrip', 50.0, 3.2, 4.3, 0.02, 0.8
    e.z0_sheet, e.eeff_sheet = 51.0, 3.25
    lm = LineModel()
    lm.elements, lm.ports, lm.port_z0 = [e], {'P1': 'p', 'P2': 'q'}, {'P1': 50.0, 'P2': 50.0}
    c = D.frozen(lm, 10e9, FREF).elements[0]
    er1 = D.layer_at(4.3, 0.02, FREF, 10e9)[0]
    assert c.er == pytest.approx(er1) and e.er == 4.3                       # the original untouched
    assert (c.eeff - 1) / (c.er - 1) == pytest.approx((3.2 - 1) / (4.3 - 1))
    assert c.z0 * math.sqrt(c.eeff) == pytest.approx(50.0 * math.sqrt(3.2))
    assert c.z0_sheet * math.sqrt(c.eeff_sheet) == pytest.approx(51.0 * math.sqrt(3.25))


def test_nothing_disperses_without_loss():
    e = Element('trace', 'p', 'q', 20.0)
    e.xs, e.z0, e.eeff, e.er, e.tand = 'stripline', 50.0, 4.0, 4.0, 0.0
    lm = LineModel()
    lm.elements = [e]
    assert D.nodes(lm, np.linspace(1e9, 10e9, 11), FREF) is None


@pytest.fixture(scope='module')
def transition(demo_files):
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    return m, build(m)


@pytest.fixture(scope='module')
def pair(boards):
    base = boards['diff']
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['D_P', 'D_N'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    return m, build(m)


def _each_frequency(lm, ports, f, backend):
    """The truth for the method: each frequency solved alone with its own materials."""
    return np.concatenate([solve(D.frozen(lm, x, FREF), ports, [x, x * (1 + 1e-9)], backend)[0][:1] for x in f])


@pytest.mark.parametrize('case', ['transition', 'pair'])
def test_the_interpolated_solve_is_each_frequency_s_own(case, request):
    pytest.importorskip('skrf')
    m, lm = request.getfixturevalue(case)
    assert any(e.diel for e in lm.elements if e.er)
    f = np.linspace(0.5e9, 12e9, 24)
    info = {}
    S, _ = solve(lm, m.ports, f, 'skrf', wideband=FREF, info=info)
    S0, _ = solve(lm, m.ports, f, 'skrf')
    T = _each_frequency(lm, m.ports, f, 'skrf')
    ok = np.isfinite(T)
    assert 3 <= len(info['held']) <= D.K_MAX
    assert np.abs(S - T)[ok].max() < 2e-3
    assert np.abs(S0 - T)[ok].max() > 10 * np.abs(S - T)[ok].max()          # and the dispersion is felt


@pytest.mark.skipif(_qucsator() is None, reason='qucsator not installed')
@pytest.mark.parametrize('case', ['transition', 'pair'])
def test_qucsator_s_interpolated_solve_is_each_frequency_s_own(case, request):
    m, lm = request.getfixturevalue(case)
    f = np.linspace(0.5e9, 12e9, 12)
    S, _ = solve(lm, m.ports, f, 'qucsator', wideband=FREF)
    T = np.array([solve(D.frozen(lm, x, FREF), m.ports, f, 'qucsator')[0][i] for i, x in enumerate(f)])   # (the
    # whole sweep each: qucsator's RLCG sections hold their loss at its centre)
    ok = np.isfinite(T)
    assert np.abs(S - T)[ok].max() < 2e-3


def test_the_plugin_follows_diel_wideband(transition):
    pytest.importorskip('skrf')
    from kicad2ems.core.solvers.lines.plugin import Scikit
    m, lm = transition
    f = np.linspace(1e9, 8e9, 8)
    said = []
    run = lambda diel: SimpleNamespace(lm=lm, m=m, freq=f, log=said.append, name='t',
                                       s=SimpleNamespace(diel=diel, diel_fref=1.0, sigma=None, rough=None, finish=None))
    wide = Scikit().solve(run('wideband'), None)
    fixed = Scikit().solve(run('fixed'), None)
    assert 'wideband dielectric' in said[0] and 'wideband' not in said[1]
    assert np.nanmax(np.abs(wide.S - fixed.S)) > 1e-3
