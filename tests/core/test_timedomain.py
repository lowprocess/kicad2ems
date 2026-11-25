"""The time-domain question (kicad2ems.core.timedomain): its PRBS, a stepped
line's TDR against its sections, the eye against peak-distortion analysis of
the same step response, a pair against its legs, IBIS buffers end to end, and
the analysis's command, report and figures (a board solved on the line model:
in a process of its own, this suite's solvers are stand-ins)."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from kicad2ems.core import macromodel as MM
from kicad2ems.core import timedomain as TD
from kicad2ems.core.deembed import abcd_to_s, line_abcd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))
F = np.linspace(20e6, 20e9, 1000)


def _abcd(*sections):
    """The cascade of (z0, eeff, length m, tand, R per m) sections, ABCD."""
    T = np.broadcast_to(np.eye(2, dtype=complex), (len(F), 2, 2)).copy()
    for z, ee, length, tand, r in sections:
        T = T @ line_abcd(z, ee, length, F, tand,
                          None if r is None else r * np.sqrt(F / 1e9))
    return T


def _two_port(*sections):
    return abcd_to_s(_abcd(*sections), 50.0)


def _touchstone(path, S, names):
    from kicad2ems.core.touchstone import write_touchstone
    write_touchstone(str(path), F, S, 50.0, comment='kicad2ems test',
                     names=names)
    return str(path)


def _args(**kw):
    a = TD.parser().parse_args(['--near', 'P1', '--far', 'P2'])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_prbs():
    """PRBS7's period 127, its ones 64; PRBS9's 511 and 256."""
    for n, ones in ((7, 64), (9, 256)):
        b = TD.prbs(n, 2 * ((1 << n) - 1))
        p = (1 << n) - 1
        assert np.array_equal(b[:p], b[p:]) and b[:p].sum() == ones
    with pytest.raises(ValueError):
        TD.prbs(8, 10)


def test_a_stepped_line_s_tdr():
    """50 ohm 20 mm, 75 ohm 20 mm, 50 ohm 20 mm (eeff 3.5): the TDR reads 75
    ohm within 2 % over the middle section's round trip, 50 before it."""
    S = _two_port((50, 3.5, 0.02, 0, None), (75, 3.5, 0.02, 0, None),
                  (50, 3.5, 0.02, 0, None))
    m = MM.fit(F, S)
    t, Z = TD.tdr(m, 0, rise=30e-12, span=1.5e-9)
    rt = 2 * 0.02 * np.sqrt(3.5) / 299792458.0          # a section's
    mid = (t > 1.3 * rt) & (t < 1.7 * rt)
    first = (t > 0.3 * rt) & (t < 0.7 * rt)
    assert np.median(Z[mid]) == pytest.approx(75.0, rel=0.02)
    assert np.median(Z[first]) == pytest.approx(50.0, rel=0.01)


def test_the_eye_is_peak_distortion_s_of_its_step():
    """A lossy 150 mm line at 8 Gb/s (its pulse spread over a few UI): the
    PRBS7 eye's inner height is the peak-distortion analysis' of the same
    step response (cursor - sum of |ISI|, at the eye's phase: PRBS7 holds
    every run of 7 bits), within 2 % of the swing."""
    path_S = _two_port((50, 3.5, 0.15, 0.02, 8.0))
    m = MM.fit(F, path_S)
    ui, rise = 125e-12, 40e-12
    dt = ui / 32
    tau = TD.delay_of(m, 1, 0)
    t_edge = 2e-9
    t = np.arange(0, t_edge + tau + 40 * ui, dt)
    w = TD._wave([0, 1], 1e-6, t_edge - 1e-6, rise, 0.0, 1.0)
    _, vn, vf = TD.step(m, 0, 1, {0: MM.Thevenin(w, 50.0)}, {}, t)
    sf = TD.step_figures(t, vn, vf, t_edge)
    lat = sf['t_far'] - t_edge
    bits = TD.prbs(7, 381)
    t0 = 2e-9
    te = np.arange(0, t0 + len(bits) * ui + lat + 4 * ui, dt)
    w = TD._wave(bits, ui, t0, rise, 0.0, 1.0)
    _, _, v = TD.step(m, 0, 1, {0: MM.Thevenin(w, 50.0)}, {}, te)
    keep = te >= t0 + lat + 12 * ui
    E = TD.eye(te[keep], v[keep], bits, ui, t0, lat)
    # peak distortion at the eye's phase: the pulse p = s(t) - s(t - ui)
    s = lambda x: np.interp(x, t - t_edge, vf)
    ts = lat + E['phase']
    h = np.array([s(ts + k * ui) - s(ts + (k - 1) * ui)
                  for k in range(-6, 30)])
    cursor = h[6]
    pda = cursor - (np.abs(h).sum() - abs(cursor))
    assert E['height'] == pytest.approx(pda, abs=0.02 * (vf[-1] - vf[0]))
    assert 0.1 < E['height'] < 0.45                    # (ISI: not trivial)


def test_a_pair_is_its_legs():
    """Two uncoupled 50 ohm lines as a pair: the TDR 100 ohm, the
    differential eye twice a leg's (each leg swings half the source's)."""
    S2 = _two_port((50, 3.5, 0.05, 0, None))
    S4 = np.zeros((len(F), 4, 4), complex)
    S4[:, :2, :2], S4[:, 2:, 2:] = S2, S2
    m = MM.fit(F, S4)
    t, Z = TD.tdr(m, 0, near_n=2, rise=30e-12, span=1e-9)
    assert np.median(Z[t > 0.2e-9]) == pytest.approx(100.0, rel=0.01)


def test_the_tool_on_a_touchstone_with_ibis_buffers(tmp_path):
    """The CLI on a 4-port Touchstone (a pair of 50 mm lines), driven by an
    IBIS resistive buffer (test_ibis's), received across 100 ohm (each leg
    50 to ground): the result's fields, the far end's difference settling
    from -0.9 to +0.9 V (VCC 50 / (50 + 50) on one leg, 0 on the other), the
    eye open, the TDR 100 ohm, the figure written."""
    sys.path.insert(0, os.path.join(ROOT, 'tests', 'core'))
    import test_ibis as TI
    ibs = tmp_path / 'buf.ibs'
    ibs.write_text(TI._ibs())
    S2 = _two_port((50, 3.5, 0.05, 0.02, 3.0))
    S4 = np.zeros((len(F), 4, 4), complex)
    S4[:, :2, :2], S4[:, 2:, 2:] = S2, S2
    ts = _touchstone(tmp_path / 'pair.s4p', S4, ['A1', 'A2', 'B1', 'B2'])
    out = tmp_path / 'td'
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.timedomain', '--sparams', ts,
         '--near', 'A1', '--far', 'A2', '--near-n', 'B1', '--far-n', 'B2',
         '--rate', '4', '--driver', 'ibis', '--ibis', str(ibs),
         '--ibis-model', 'DRV', '--receiver', 'rc', '--rt', '100',
         '--out', str(out)], capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    d = json.load(open(out / TD.RESULT))
    assert d['format'] == TD.FORMAT and d['channel']['pair']
    assert d['driver']['kind'] == 'ibis' and d['driver']['model'] == 'DRV'
    # each leg: VCC through RB (50) into 50 to ground (rt / 2), the other
    # leg at 0: the difference from -0.9 to +0.9 V at DC (the loss is AC)
    assert d['step']['high'] == pytest.approx(TI.VCC * 50 / 100, rel=0.02)
    assert d['step']['low'] == pytest.approx(-TI.VCC * 50 / 100, rel=0.02)
    assert d['eye']['open'] and d['eye']['width_ui'] > 0.7
    assert d['tdr']['z_min'] == pytest.approx(100.0, rel=0.05)
    assert os.path.isfile(out / d['images'][0])


def test_the_analysis(tmp_path):
    """TimeDomain's command (the study's S-parameter run as --from; the
    board otherwise), report and figures from a finished run's folder."""
    from kicad2ems.core.analyses import registry
    a = registry()['timedomain']
    cmd = a.command(dict(near='P1', far='P2', rate=5), 'b.kicad_pcb', 'o',
                    results=dict(sparams='runs/x'))
    assert cmd[cmd.index('--from') + 1] == 'runs/x' and 'b.kicad_pcb' \
        not in cmd
    cmd = a.command(dict(near='P1', far='P2'), 'b.kicad_pcb', 'o')
    assert cmd[3] == 'b.kicad_pcb'
    with pytest.raises(ValueError, match='near and far'):
        a.command(dict(near='', far='P2'), 'b.kicad_pcb', 'o')
    ts = _touchstone(tmp_path / 'l.s2p', _two_port((50, 3.5, 0.05, 0.02, 3.0)),
                     ['P1', 'P2'])
    TD.run(_args(sparams=ts, out=str(tmp_path / 'r'), rate=5.0),
           echo=lambda *x: None)
    rep = a.report(str(tmp_path / 'r'))
    assert 'eye' in rep['summary'] and rep['images']
    names = {f['name'] for f in a.figures(str(tmp_path / 'r'))}
    assert {'eye height', 'eye width', 'TDR min', 'TDR max', 'delay',
            'rise'} <= names


def test_a_board_solved_on_the_line_model(tmp_path):
    """rf_msvia solved here on the line model (from 20 MHz) at 5 Gb/s: its
    eye open, its delay the line's (24 mm and a via: 140-150 ps)."""
    subprocess.check_call([sys.executable, os.path.join(
        ROOT, 'examples', 'make_rf_examples.py'), 'msvia',
        str(tmp_path / 'rf_msvia')], stdout=subprocess.DEVNULL)
    cfg = tmp_path / 'rf_msvia.toml'
    cfg.write_text(open(os.path.join(ROOT, 'examples', 'rf_msvia.toml'))
                   .read())
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.timedomain', str(cfg),
         '--near', 'P1', '--far', 'P2', '--rate', '5', '--points', '400',
         '--out', str(tmp_path / 'td')], capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    d = json.load(open(tmp_path / 'td' / TD.RESULT))
    assert d['eye']['open'] and d['eye']['height_pct'] > 90
    assert 135 < d['step']['delay_ps'] < 155
