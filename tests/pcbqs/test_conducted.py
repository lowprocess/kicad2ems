"""pcbqs.conducted: the networks against the standards' impedances, the limits,
the receiver (a line, CISPR 16-1-1's pulse calibration of the quasi-peak), the
board's capacitance to the reference plane, and through ngspice a square wave
into the networks against its Fourier series, the common mode's path, and the
buck example end to end."""
import json
import math
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import numpy as np
import pytest

from kicad2ems.pcbqs import conducted as C

NGSPICE = pytest.mark.skipif(not shutil.which('ngspice'),
                             reason='ngspice not installed')
EXAMPLES = Path(str(files('kicad2ems.pcbqs.circuit').joinpath('examples')))
LINES = dict(lines=[dict(line='+', parts=dict(C2='LISNP_C2', RX='LISNP_RX')),
                    dict(line='-', parts=dict(C2='LISNN_C2', RX='LISNN_RX'))])


def _off(z, ref):
    return np.abs(np.abs(z) / np.abs(ref) - 1).max()


def test_the_networks_have_the_standards_impedance():
    """CISPR 16-1-2's 50 || (5 + j w 50 uH) within 1 % from 150 kHz (its 20 %
    tolerance at 9 kHz), CISPR 25's 50 || j w 5 uH within 5 % to 108 MHz; the
    phase within the standards' 11.5 degrees."""
    band = np.logspace(np.log10(150e3), np.log10(30e6), 200)
    low = np.logspace(np.log10(9e3), np.log10(150e3), 50)
    auto = np.logspace(np.log10(150e3), np.log10(108e6), 200)
    for net, f, most in (('cispr16', band, 0.01), ('cispr16', low, 0.2),
                         ('cispr25', auto, 0.05)):
        z, ref = C.impedance(net, f), C.standard_impedance(net, f)
        assert _off(z, ref) < most, (net, f[0])
        assert np.degrees(np.abs(np.angle(z / ref))).max() < 11.5
    assert abs(C.standard_impedance('cispr16', [30e6])[0] - 50) < 0.5


def test_the_limits(tmp_path):
    b = C.limits('cispr32', 'B')
    at = lambda rows, f: float(C.limit_at(rows, [f])[0])  # noqa: E731
    assert at(b['qp'], 150e3) == pytest.approx(66)
    # log-linear down to 56 at 0.5 MHz, where the lower applies
    assert at(b['qp'], math.sqrt(150e3 * 500e3)) == pytest.approx(61)
    assert at(b['qp'], 500e3) == 56 and at(b['avg'], 5e6) == 46
    assert at(b['avg'], 10e6) == 50 and math.isnan(at(b['qp'], 100e3))
    assert at(C.limits('cispr32', 'a')['qp'], 1e6) == 73
    c5, c1 = C.limits('cispr25', 5), C.limits('cispr25', '1')
    assert (at(c5['avg'], 90e6) == 18 and at(c5['peak'], 0.2e6) == 70
            and at(c1['peak'], 0.2e6) == 110 and at(c1['avg'], 1e6) == 66)
    assert math.isnan(at(c5['peak'], 0.4e6))   # between LW and MW: none
    for bad in (('cispr32', 'C'), ('cispr25', 6), ('fcc', 'B')):
        with pytest.raises(ValueError):
            C.limits(*bad)
    own = tmp_path / 'lim.csv'
    own.write_text('detector,f1_Hz,f2_Hz,dBuV1,dBuV2\n# mine\n'
                   'qp,150e3,1e6,70,60\navg,1e6,30e6,40\n')
    mine = C.read_limits(own)
    assert at(mine['qp'], 1e6) == 60 and at(mine['avg'], 2e6) == 40
    own.write_text('qp,1e6,150e3,70\n')
    with pytest.raises(ValueError, match='f1_Hz < f2_Hz'):
        C.read_limits(own)


def test_the_receiver_reads_a_line_as_a_sine_s_rms():
    T = 4e-6
    f = np.arange(1, 200) / T
    a = np.zeros(len(f), complex)
    a[2] = 0.1 * np.exp(0.3j)                       # 750 kHz, 0.1 V peak
    tuned, r, bands = C.receive(f, a, T, 150e3, 30e6)
    k = int(np.argmin(abs(tuned - 750e3)))
    assert bands[k] == 'B' and all(
        r[d][k] == pytest.approx(0.1 / math.sqrt(2)) for d in C.DETECTORS)
    # the lines 250 kHz apart: tuned at the lines alone (each read alone)
    assert len(tuned) == np.count_nonzero((f >= 150e3) & (f <= 30e6))
    assert C._gauss(4.5e3, 9e3) == pytest.approx(0.5)   # -6 dB at RBW / 2
    assert C.dbuv(1e-3) == pytest.approx(60) and C.dbuv(0) == C.FLOOR_DBUV


def _pulses(area, prf, fc, rbw):
    """An impulse train of `area` (V s) at `prf`: its lines to past fc."""
    T = 1 / prf
    k = int((fc + 5 * rbw) * T) + 2
    return np.arange(1, k + 1) / T, np.full(k, 2 * area / T, complex), T


def _read(area, prf, fc, rbw):
    f, a, T = _pulses(area, prf, fc, rbw)
    tuned, r, _ = C.receive(f, a, T, fc - 1, fc + 1)
    k = int(np.argmin(abs(tuned - fc)))
    return {d: float(C.dbuv(v[k])) for d, v in r.items()}


def test_the_quasi_peak_meets_cispr_16_1_1_s_pulse_calibration():
    """Impulses of 13.5 uVs at 25 Hz (band A), 0.316 uVs at 100 Hz (B), 0.044
    uVs at 100 Hz (C) read 66 dBuV quasi-peak, +-1.5 dB; the peak is the
    Gaussian filter's (2 A times its impulse bandwidth), the average 2 A prf;
    band B's response to the repetition rate as the standard's table has it
    (the pulses that read 66 dBuV at 100 Hz): 70.5 dBuV at 1000 Hz (+-1), 59.5
    at 20 Hz (+-1), 56 at 10 Hz (+-1.5)."""
    for band, area, prf, fc in (('A', 13.5e-6, 25, 100e3),
                                ('B', 0.316e-6, 100, 10e6),
                                ('C', 0.044e-6, 100, 100e6)):
        rbw = next(b[3] for b in C.BANDS if b[0] == band)
        got = _read(area, prf, fc, rbw)
        assert abs(got['qp'] - 66) <= 1.5, (band, got)
        width = rbw / 2 * math.sqrt(math.pi / math.log(2))
        assert got['peak'] == pytest.approx(
            float(C.dbuv(2 * area * width / math.sqrt(2))), abs=0.05)
        assert got['avg'] == pytest.approx(
            float(C.dbuv(2 * area * prf / math.sqrt(2))), abs=0.05)
    for prf, want, tol in ((1000, 70.5, 1.0), (20, 59.5, 1.0), (10, 56, 1.5)):
        assert abs(_read(0.316e-6, prf, 10e6, 9e3)['qp'] - want) <= tol


def test_a_plate_s_capacitance_to_a_far_and_a_near_plane():
    """A 20 mm square alone: the unit square's 0.3667874 x 4 pi eps0 a
    (Hwang and Mascagni) within 1 %; a plane 5 mm under it adds to it, and
    more than the parallel plate's eps0 A / h alone."""
    from shapely.geometry import box
    from kicad2ems.pcbqs.pdn.mesh import mesh_sheet
    from kicad2ems.emsdev.numerics.base import EPS0
    nodes, tris, *_ = mesh_sheet(box(0, 0, 20, 20), (), dmax=1.0)
    nodes, tris = np.asarray(nodes) * 1e-3, np.asarray(tris)
    z, owner = np.zeros(len(tris)), np.zeros(len(tris), int)
    alone = C.capacitance_to_plane(nodes, tris, z, owner, -10.0)[0, 0]
    assert alone == pytest.approx(0.3667874 * 4 * math.pi * EPS0 * 0.02,
                                  rel=0.01)
    near = C.capacitance_to_plane(nodes, tris, z, owner, -5e-3)[0, 0]
    assert near > alone and near > EPS0 * 0.02 ** 2 / 5e-3


def test_the_networks_go_in_on_the_source_s_footprint():
    from kicad2ems.pcbqs.circuit import model as M
    model = M.load(EXAMPLES / '04_buck.json')
    emc, info = C.insert(model, cm=[('sw', 15e-12)])
    M.validate(emc)
    parts = {p['ref']: p for p in emc['parts']}
    assert parts['V1']['nodes'] == [C.SUPPLY, C.REFERENCE]
    assert parts['LISNP_L1']['nodes'] == ['emc_p_mid', 'in']
    assert parts['LISNN_L1']['nodes'] == ['emc_n_mid', '0']
    assert parts['LISNN_L2']['nodes'] == [C.REFERENCE, 'emc_n_mid']
    assert parts['EMC_CM1']['nodes'] == ['sw', C.REFERENCE]
    b = emc['board']['parts']
    assert (b['V1'] is None and b['LISNP_L1'] == [None, 'J1']
            and b['LISNN_C2'] == ['J1', None] and b['LISNP_L2'] is None)
    assert [r['line'] for r in info['lines']] == ['+', '-']
    assert model['parts'][0]['nodes'] == ['in', '0']    # not changed
    # the + line alone: the board's return is the reference
    plus, info = C.insert(model, network='cispr25', lines=['+'])
    assert info['reference'] == '0' and not any(
        p['ref'].startswith('LISNN') for p in plus['parts'])
    with pytest.raises(ValueError, match='common-mode'):
        C.insert(model, lines=['+'], cm=[('sw', 1e-12)])
    with pytest.raises(ValueError, match='no part is on'):
        C.insert(model, cm=[('nowhere', 1e-12)])
    with pytest.raises(ValueError, match='not a DC voltage source'):
        C.insert(model, source='L1')
    assert C.parse_caps(['sw=15p', 'out = 2.2e-12']) == [
        ('sw', pytest.approx(15e-12)), ('out', pytest.approx(2.2e-12))]
    with pytest.raises(ValueError):
        C.parse_caps(['sw'])


def _square(edge):
    return dict(type='pulse', frequency_Hz=100e3, low_V=-0.5, high_V=0.5,
                duty=0.5, rise_s=edge, fall_s=edge)


def _spectrum(res):
    case = res['cases'][0]
    r = C.case_readings(case['folder'], LINES, case['period_s'], 150e3,
                        res['fmax_Hz'], C.limits('cispr32', 'B'))
    return r['spectrum']


def _trapezoid(f, f0, amplitude, edge):
    """A 50 % square wave's lines (peak) with linear edges."""
    k = np.rint(f / f0)
    return 2 * amplitude * 0.5 * np.abs(np.sinc(k * 0.5) * np.sinc(
        k * edge * f0)), k


@NGSPICE
@pytest.mark.parametrize('network', ['cispr16', 'cispr25'])
def test_a_square_wave_through_the_networks(tmp_path, network):
    """A 1 V square wave behind 100 ohm between the board's supply and return:
    each odd line at the EUT terminals its Fourier series times Z / (R + 2 Z),
    Z the network's impedance, within 2 % to the top of the band (30 / 108
    MHz); L- the same, opposite; no common mode."""
    edge, R = 5e-9, 100.0
    model = dict(format=2, name='square', parts=[
        dict(ref='V1', type='source', nodes=['in', '0'], dc_V=5.0),
        dict(ref='VS', type='source', nodes=['in', '0'], R_ohm=R,
             wave=_square(edge))])
    std, cls = (('cispr25', '5') if network == 'cispr25' else
                ('cispr32', 'B'))
    res = C.run(model, tmp_path, standard=std, klass=cls, log=lambda s: None)
    sp = _spectrum(res)
    f = sp['f']
    a, k = _trapezoid(f, 100e3, 1.0, edge)
    z = C.impedance(network, f)
    odd = (k % 2 == 1) & (f <= res['fmax_Hz'])
    want = a[odd] * np.abs(z[odd] / (R + 2 * z[odd]))
    for name in ('L+', 'L-', 'DM'):
        assert np.abs(np.abs(sp[name][odd]) / want - 1).max() < 0.02, name
    assert (np.abs(sp['CM'][odd]) / np.abs(sp['DM'][odd])).max() < 1e-5
    assert res['cases'][0]['worst'] is not None


@NGSPICE
def test_the_common_mode_s_path(tmp_path):
    """A 10 V square wave on a node with 100 pF to the reference: its current
    through the two networks in parallel back to the board (its input
    capacitor between them) as the closed form has it, within 3 %."""
    edge, Cc, f0 = 5e-9, 100e-12, 100e3
    model = dict(format=2, name='cm', parts=[
        dict(ref='V1', type='source', nodes=['in', '0'], dc_V=5.0),
        dict(ref='C1', type='capacitor', nodes=['in', '0'], value=10e-6),
        dict(ref='VS', type='source', nodes=['x', '0'], R_ohm=1.0,
             wave=dict(_square(edge), low_V=0.0, high_V=10.0))])
    res = C.run(model, tmp_path, cm_caps=[('x', Cc)], log=lambda s: None)
    assert [c['node'] for c in res['cm']] == ['x']
    sp = _spectrum(res)
    f = sp['f']
    a, k = _trapezoid(f, f0, 10.0, edge)
    z, w = C.impedance('cispr16', f), 2j * np.pi * f
    zcm = 1 / (1 / z + 1 / (z + 1 / (w * 10e-6)))
    want = a * np.abs(zcm / (1 / (w * Cc) + 1.0 + zcm))
    odd = (k % 2 == 1) & (f <= 30e6)
    assert np.abs(np.abs(sp['CM'][odd]) / want[odd] - 1).max() < 0.03
    assert (np.abs(sp['DM'][odd]) / np.abs(sp['CM'][odd])).max() < 0.01


@NGSPICE
def test_the_buck_example_end_to_end(tmp_path):
    """The command line on the buck example with a heatsink on its switch
    node: its result, readings, figure; the analysis's report, curves and
    figures from the folder."""
    out = tmp_path / 'emc'
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.pcbqs.conducted',
         str(EXAMPLES / '04_buck.json'), '--out', str(out), '--cm-cap',
         'sw=15p'], capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    res = json.loads((out / 'conducted.json').read_text())
    assert res['format'] == C.FORMAT and res['network'] == 'cispr16'
    case = res['cases'][0]
    # no input filter: the 250 kHz line far over class B's average limit
    worst = case['worst']
    assert (worst['f_Hz'] == 250e3 and worst['detector'] == 'avg'
            and worst['margin_dB'] < -20 and not case['passed'])
    # the board's own copper to the plane, and the heatsink's
    nodes = [c['node'] for c in res['cm']]
    assert nodes.count('sw') == 2 and '0' in nodes
    assert (out / 'conducted.png').is_file()
    rows = (Path(case['folder']) / 'conducted.csv').read_text().splitlines()
    assert rows[0].startswith('f_Hz,band,detector,L+_dBuV,L-_dBuV,DM_dBuV')
    from kicad2ems.core.analyses import registry
    a = registry()['conducted']
    assert a.owns(str(out))
    rep = a.report(str(out))
    assert rep['title'] == 'Conducted emission, CISPR32 class B'
    assert rep['images'] and 'margin' in rep['summary']
    figs = {f['name']: f['value'] for f in a.figures(str(out))}
    assert figs['margin'] == pytest.approx(worst['margin_dB'])
    assert figs['at'] == pytest.approx(0.25) and 'margin QP' in figs
    sets = a.curves(str(out))
    names = [c['name'] for c in sets[0]['curves']]
    assert 'QP limit' in names and 'CM (QP)' in names


def test_the_analysis_command(tmp_path):
    from kicad2ems.core.analyses import registry
    a = registry()['conducted']
    model = EXAMPLES / '04_buck.json'
    cmd = a.command(dict(model=str(model), standard='cispr25',
                         cispr_class='5', cm_cap='sw=15p'), None,
                    str(tmp_path))
    assert cmd[1:5] == ['-u', '-m', 'kicad2ems.pcbqs.conducted', str(model)]
    assert cmd[5:] == ['--out', str(tmp_path), '--standard', 'cispr25',
                       '--cispr-class', '5', '--cm-cap', 'sw=15p',
                       '--jobs', '4']
    with pytest.raises(ValueError, match='circuit model'):
        a.command({}, None, str(tmp_path))
    assert a.tag(dict(model=str(model), standard='cispr25')) == \
        '04_buck_cispr25'
    assert {o.key for o in a.options()} >= {'model', 'standard',
                                             'cispr_class', 'cm_cap'}
