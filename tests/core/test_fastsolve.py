"""Fast S-parameters of the line model: scikit-rf and qucsator (each skipped
when not installed), the qucsator netlist and dataset reader, lumped parts."""
import numpy as np
import pytest
from kicad2ems.core.importers.kicad import KiCadBoard
from kicad2ems.core.board import Board
from kicad2ems.core.lines.tlines import build
from kicad2ems.core.solvers.lines.fastsolve import solve, qucs_netlist, read_qucs_dataset, part_impedance, find_qucsator
from kicad2ems.core.parts import Part

FREQ = np.linspace(1e9, 8e9, 15)
db = lambda x: 20 * np.log10(np.abs(x))


@pytest.fixture(scope='module')
def transition(demo_files):
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu', log=lambda *a: None)
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
    # reciprocal
    assert np.allclose(S[:, 0, 1], S[:, 1, 0], atol=1e-9)
    # a matched-ish thru
    assert (db(S[:, 1, 0]) > -1.0).all() and (db(S[:, 0, 0]) < -15).all()
    assert (np.abs(S) <= 1 + 1e-9).all()  # passive


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
    m = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos',
              log=lambda *a: None)
    f = np.linspace(0.1e9, 4e9, 40)
    S, _ = solve(build(m), m.ports, f, 'skrf')
    s21 = lambda g: db(np.interp(g, f, np.abs(S[:, 1, 0])))
    # a 1 GHz low-pass
    assert s21(0.1e9) > -0.5 and -10 < s21(1e9) < -3 and s21(2e9) < -20


def test_qucsator_netlist(transition, boards):
    m, lm = transition
    text, order = qucs_netlist(lm, m.ports, FREQ)
    assert (order == ['P1', 'P2'] and text.count('Pac:') == 2
            and 'MLIN:' in text and 'RLCG:' in text)
    assert '.SP:SP1 Type="lin" Start="1e+09" Stop="8e+09" Points="15"' in text
    base = boards['lowpass']
    mp = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos',
               log=lambda *a: None)
    text, _ = qucs_netlist(build(mp), mp.ports, FREQ)
    # C2 shunt to ground, L1 / L3
    assert 'gnd C="6.8e-12 F"' in text and ' L="8.2e-09 H"' in text


def test_a_log_sweep(transition, tmp_path):
    """sweep = log: the run's frequencies evenly on a log scale, qucsator's
    sweep the same, the plot's axis in the band's own unit and logarithmic; the
    line model's two backends agree on them as on a linear sweep."""
    from kicad2ems.core.config import ConfigError, build_settings
    from kicad2ems.core.pipeline import Job
    from kicad2ems.core.plots import plot_results
    from kicad2ems.core.touchstone import band_unit, log_spaced
    f = np.geomspace(1e4, 1e7, 13)
    assert (log_spaced(f) and not log_spaced(FREQ) and not log_spaced(f[:2])
            and not log_spaced([0.0, 1.0, 2.0]))
    assert ([band_unit(v)[1]
             for v in ([1e3, 9e5], [1e4, 1e7], FREQ)] == ['kHz', 'MHz', 'GHz'])
    m, lm = transition
    text, _ = qucs_netlist(lm, m.ports, f)
    assert ('.SP:SP1 Type="log" Start="10000" Stop="10000000" Points="13"'
            in text)
    cfg = tmp_path / 'b.toml'
    cfg.write_text(
        'board = "b.kicad_pcb"\nfmin = 0.00001\nfmax = 0.01\npoints = '
        '13\nsweep = "log"\n'
    )
    s = build_settings(config=str(cfg))
    assert (s.sweep == 'log'
            and np.allclose(Job(s, echo=lambda *a: None).freq, f))
    lin = build_settings(dict(sweep='linear'), str(cfg))
    assert (lin.sweep == 'linear' and np.allclose(
        Job(lin, echo=lambda *a: None).freq, np.linspace(1e4, 1e7, 13)))
    cfg.write_text(
        'board = "b.kicad_pcb"\nfmin = 0\nfmax = 0.01\nsweep = "log"\n')
    with pytest.raises(ConfigError,
                       match='sweep = log: fmin .* must be above 0'):
        build_settings(config=str(cfg))
    S = np.zeros((13, 2, 2), complex) + 0.1
    ax = plot_results(f, S, ['P1', 'P2'], 't').axes[0]
    assert (ax.get_xscale() == 'log' and ax.get_xlabel() == 'Frequency (MHz)'
            and ax.lines[0].get_xdata()[-1] == 10.0)
    ax = (plot_results(FREQ,
                       np.zeros((15, 2, 2), complex) + 0.1, ['P1', 'P2'],
                       't').axes[0])
    assert ax.get_xscale() == 'linear' and ax.get_xlabel() == 'Frequency (GHz)'
    if _qucsator() is not None and pytest.importorskip('skrf'):
        Sq, _ = solve(lm, m.ports, f, 'qucsator')
        Ss, _ = solve(lm, m.ports, f, 'skrf')
        assert np.max(np.abs(Sq - Ss)) < 1e-3


def test_read_qucs_dataset():
    text = (
        '<Qucs Dataset 1.0>\n<indep frequency 2>\n  +1.0e+09\n  '
        '+2.0e+09\n</indep>\n'
        + "".join(
            f'<dep S[{i},{j}] frequency>\n  +{0.1 * i}e+00-j{0.01 * j}e+00\n  '
            '+1e-01+j2e-02\n</dep>\n'
            for i in (1, 2)
            for j in (1, 2)))
    f, S = read_qucs_dataset(text, 2)
    assert (list(f) == [1e9, 2e9] and S[0, 1, 0] == pytest.approx(0.2 - 0.01j)
            and S[1, 0, 1] == pytest.approx(0.1 + 0.02j))


def test_part_impedance():
    w = 2 * np.pi * np.array([1e9])
    c = Part('C1', 0.1, 0.5e-9, 6.8e-12, 'F.Cu', 0, 0, [0, 0, 0], [1, 1, 0])
    z = part_impedance(c, w)
    assert (
        z[0] == pytest.approx(0.1
                              + 1j * (w[0] * 0.5e-9 - 1 / (w[0] * 6.8e-12))))


def test_unknown_backend(transition):
    m, lm = transition
    with pytest.raises(ValueError, match='choose one of'):
        solve(lm, m.ports, FREQ, 'spice')


@pytest.fixture(scope='module')
def launch(boards):
    base = boards['launch']
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', log=lambda *a: None)
    return m, build(m)


def test_qucsator_netlist_coplanar(launch):
    m, lm = launch
    text, _ = qucs_netlist(lm, m.ports, FREQ)
    # qucsator's CPW element
    assert ('CLIN:' in text and 'Backside="Metal"' in text
            and 'CPWLINE' not in text)


@pytest.mark.skipif(_qucsator() is None, reason='qucsator not installed')
def test_qucsator_matches_skrf_coplanar(launch):
    pytest.importorskip('skrf')
    m, lm = launch
    Sq, _ = solve(lm, m.ports, FREQ, 'qucsator')
    Ss, _ = solve(lm, m.ports, FREQ, 'skrf')
    assert np.max(np.abs(db(Sq[:, 1, 0]) - db(Ss[:, 1, 0]))) < 0.3
    # qucsator adds the steps and open ends
    assert np.max(np.abs(Sq[:, 0, 0] - Ss[:, 0, 0])) < 0.1


def _rf_examples():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        'make_rf_examples',
        os.path.join(os.path.dirname(__file__), '..', '..', 'examples',
                     'make_rf_examples.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_stub_shorted_by_ground_vias_is_shorted(tmp_path):
    """A stub that ends in a row of ground vias, a pad's width in from its end
    (as dfsynth draws a short), is a short circuit there: a quarter wave shorted
    stub passes F0 and stops DC. The line model took only the line's own vias,
    so it was an open stub (dfsynth's stub band-pass a band-stop: -80 dB at its
    centre)."""
    pytest.importorskip('skrf')
    E = _rf_examples()
    q, yc = E.quarter(E.W50), E.quarter(E.W50) + 4.0
    W, H = 2 * 8.0 + E.W50, yc + E.W50 / 2 + 3.0
    bd = E.Board()
    bd.track([(0, yc), (W, yc)], E.W50)
    bd.track([(W / 2, yc), (W / 2, yc - q)], E.W50)
    for dx in (-0.4, 0.4):  # two vias across the stub's end, inside it
        bd.via(W / 2 + dx, yc - q + 0.4)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(str(tmp_path / 'short.kicad_pcb'), W, H)
    m = Board(KiCadBoard(str(tmp_path / 'short.kicad_pcb')), nets=['RF', 'GND'],
              ground='B.Cu', log=lambda *a: None)
    lm = build(m)
    vias = [e for e in lm.elements if e.kind == 'via']
    assert (len(vias) == 1 and vias[0].b == 'gnd'
            and '2 in parallel' in vias[0].notes[0])
    f = np.array([0.05e9, E.F0])
    S, _ = solve(lm, m.ports, f, 'skrf')
    # shorted near DC, a pass at its quarter wave
    assert db(S[0, 1, 0]) < -15 and db(S[1, 1, 0]) > -1.0


def test_an_end_gap_joins_its_ports(tmp_path):
    """Two lines end to end across a gap: the gap's series capacitance joins
    them (NaN before: the ports were taken as unconnected), on scikit-rf as a pi
    of Garg and Bahl's capacitances."""
    pytest.importorskip('skrf')
    E = _rf_examples()
    yc, L, g = 5.0, 12.0, 0.2
    W, H = 2 * L + g, 10.0
    bd = E.Board()
    bd.track([(0, yc), (L, yc)], E.W50)
    bd.track([(L + g, yc), (W, yc)], E.W50)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(str(tmp_path / 'gap.kicad_pcb'), W, H)
    m = Board(KiCadBoard(str(tmp_path / 'gap.kicad_pcb')), nets=['RF', 'GND'],
              ground='B.Cu', log=lambda *a: None)
    lm = build(m)
    assert [j['kind'] for j in lm.junctions].count('gap') == 1
    f = np.array([1e9, 4e9, 8e9])
    S, _ = solve(lm, m.ports, f, 'skrf')
    s21 = np.abs(S[:, 1, 0])
    # a series capacitance: a high-pass
    assert np.isfinite(S).all() and (np.diff(s21) > 0).all() and s21[0] < 0.5
    if _qucsator() is not None:
        Sq, _ = solve(lm, m.ports, f, 'qucsator')
        # (scikit-rf: no open-end extension)
        assert np.max(np.abs(db(Sq[:, 1, 0]) - db(S[:, 1, 0]))) < 3.0


def test_a_ground_via_in_a_hole_of_the_line_s_copper_does_not_short_it(
        tmp_path):
    """A strip taken from a pour of the line's net is solid across the pour's
    hole round a ground via: the via is no short (it had been, three times on
    the CM5 board's switching-node pour); in the copper, it is."""
    E = _rf_examples()
    yc, W, H = 5.0, 20.0, 10.0
    for hole, shorts in ((True, 0), (False, 1)):
        bd = E.Board()
        bd.track([(0, yc), (6, yc)], E.W50)
        bd.track([(14, yc), (W, yc)], E.W50)
        outer = [(6, yc - 1.5), (14, yc - 1.5), (14, yc + 1.5), (6, yc + 1.5)]
        # a pour of the line's net
        bd.plane('F.Cu', W, H, net=2, holes=([(10, yc, 0.6)] if hole else []),
                 outer=outer)
        bd.via(10, yc, net=1)
        bd.port(1, 0.5, yc, 0)
        bd.port(2, W - 0.5, yc, 180)
        bd.write(str(tmp_path / 'h.kicad_pcb'), W, H)
        m = Board(KiCadBoard(str(tmp_path / 'h.kicad_pcb')), nets=['RF', 'GND'],
                  ground='B.Cu', log=lambda *a: None)
        lm = build(m)
        assert (len([
            e for e in lm.elements if e.kind == 'via' and e.b == 'gnd'
        ]) == shorts), hole


def test_sigma_is_felt_beyond_microstrip():
    """--sigma reaches a stripline and a coupled section (only microstrip
    sections had felt it); 2 Rs / w per metre, never below the strip's DC
    resistance."""
    pytest.importorskip('skrf')
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.pipeline import board_from_settings
    from kicad2ems.core.solvers.lines.fastsolve import conductor_r
    import os
    assert (conductor_r(0.2, 0.035, 0.0,
                        5.8e7) == pytest.approx(1 / (5.8e7 * 35e-6 * 0.2e-3)))
    rs = np.sqrt(np.pi * 10e9 * 4e-7 * np.pi / 5.8e7)
    assert (conductor_r(0.2, 0.035, 10e9,
                        5.8e7) == pytest.approx(2 * rs / 0.2e-3))
    root = os.path.join(os.path.dirname(__file__), '..', '..', 'examples')
    for name in ('rf_stripline', 'rf_coupler'):
        s = build_settings({}, config=os.path.join(root, name + '.toml'))
        m = board_from_settings(s, log=lambda *a: None)
        lm = build(m)
        f = np.array([5e9])
        lossless, _ = solve(lm, m.ports, f, 'skrf')
        lossy, _ = solve(lm, m.ports, f, 'skrf', sigma=5.8e7)
        assert db(lossy[0, 1, 0]) < db(lossless[0, 1, 0]) - 0.03, name
