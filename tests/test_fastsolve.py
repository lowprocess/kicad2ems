"""Fast S-parameters of the line model: scikit-rf and qucsator (each skipped when not installed), the
qucsator netlist and dataset reader, lumped parts."""
import numpy as np
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.solvers.lines.tlines import build
from kicad2ems.solvers.lines.fastsolve import solve, qucs_netlist, read_qucs_dataset, part_impedance, find_qucsator
from kicad2ems.parts import Part

FREQ = np.linspace(1e9, 8e9, 15)
db = lambda x: 20 * np.log10(np.abs(x))


@pytest.fixture(scope='module')
def transition(demo_files):
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    return m, build(m)


def _qucsator():
    try:
        return find_qucsator()
    except ValueError:
        return None


def test_skrf_transition(transition):
    pytest.importorskip('skrf')
    m, lm = transition
    S, sec = solve(lm, m.ports, FREQ, 'skrf')
    assert S.shape == (15, 2, 2) and np.isfinite(S).all()
    assert np.allclose(S[:, 0, 1], S[:, 1, 0], atol=1e-9)                          # reciprocal
    assert (db(S[:, 1, 0]) > -1.0).all() and (db(S[:, 0, 0]) < -15).all()          # a matched-ish thru
    assert (np.abs(S) <= 1 + 1e-9).all()                                          # passive


@pytest.mark.skipif(_qucsator() is None, reason='qucsator not installed')
def test_qucsator_matches_skrf(transition):
    pytest.importorskip('skrf')
    m, lm = transition
    Sq, _ = solve(lm, m.ports, FREQ, 'qucsator')
    Ss, _ = solve(lm, m.ports, FREQ, 'skrf')
    assert np.max(np.abs(db(Sq[:, 1, 0]) - db(Ss[:, 1, 0]))) < 0.3
    assert np.max(np.abs(Sq[:, 0, 0] - Ss[:, 0, 0])) < 0.05


def test_lowpass_follows_the_ideal_circuit(boards):
    pytest.importorskip('skrf')
    base = boards['lowpass']
    m = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None)
    f = np.linspace(0.1e9, 4e9, 40)
    S, _ = solve(build(m), m.ports, f, 'skrf')
    s21 = lambda g: db(np.interp(g, f, np.abs(S[:, 1, 0])))
    assert s21(0.1e9) > -0.5 and -10 < s21(1e9) < -3 and s21(2e9) < -20          # a 1 GHz low-pass


def test_qucsator_netlist(transition, boards):
    m, lm = transition
    text, order = qucs_netlist(lm, m.ports, FREQ)
    assert order == ['P1', 'P2'] and text.count('Pac:') == 2 and 'MLIN:' in text and 'RLCG:' in text
    assert '.SP:SP1 Type="lin" Start="1e+09" Stop="8e+09" Points="15"' in text
    base = boards['lowpass']
    mp = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None)
    text, _ = qucs_netlist(build(mp), mp.ports, FREQ)
    assert 'gnd C="6.8e-12 F"' in text and ' L="8.2e-09 H"' in text              # C2 shunt to ground, L1 / L3


def test_read_qucs_dataset():
    text = ('<Qucs Dataset 1.0>\n<indep frequency 2>\n  +1.0e+09\n  +2.0e+09\n</indep>\n'
            + "".join(f'<dep S[{i},{j}] frequency>\n  +{0.1 * i}e+00-j{0.01 * j}e+00\n  +1e-01+j2e-02\n</dep>\n'
                      for i in (1, 2) for j in (1, 2)))
    f, S = read_qucs_dataset(text, 2)
    assert list(f) == [1e9, 2e9] and S[0, 1, 0] == pytest.approx(0.2 - 0.01j) and S[1, 0, 1] == pytest.approx(0.1 + 0.02j)


def test_part_impedance():
    w = 2 * np.pi * np.array([1e9])
    c = Part('C1', 0.1, 0.5e-9, 6.8e-12, 'F.Cu', 0, 0, [0, 0, 0], [1, 1, 0])
    z = part_impedance(c, w)
    assert z[0] == pytest.approx(0.1 + 1j * (w[0] * 0.5e-9 - 1 / (w[0] * 6.8e-12)))


def test_unknown_backend(transition):
    m, lm = transition
    with pytest.raises(ValueError, match='choose one of'):
        solve(lm, m.ports, FREQ, 'spice')


@pytest.fixture(scope='module')
def launch(boards):
    base = boards['launch']
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', log=lambda *a: None)
    return m, build(m)


def test_qucsator_netlist_coplanar(launch):
    m, lm = launch
    text, _ = qucs_netlist(lm, m.ports, FREQ)
    assert 'CLIN:' in text and 'Backside="Metal"' in text and 'CPWLINE' not in text      # qucsator's CPW element


@pytest.mark.skipif(_qucsator() is None, reason='qucsator not installed')
def test_qucsator_matches_skrf_coplanar(launch):
    pytest.importorskip('skrf')
    m, lm = launch
    Sq, _ = solve(lm, m.ports, FREQ, 'qucsator')
    Ss, _ = solve(lm, m.ports, FREQ, 'skrf')
    assert np.max(np.abs(db(Sq[:, 1, 0]) - db(Ss[:, 1, 0]))) < 0.3
    assert np.max(np.abs(Sq[:, 0, 0] - Ss[:, 0, 0])) < 0.1        # qucsator adds the steps and open ends
