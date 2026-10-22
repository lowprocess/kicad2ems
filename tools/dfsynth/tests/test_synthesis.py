import math
import os

import numpy as np
import pytest

from dfsynth.microstrip import Substrate
from dfsynth.prototype import gvalues, estimate_order
from dfsynth.topologies import Spec, build, KINDS
from dfsynth.calibrate import calibrate, targets, edges, errors, sweep
from dfsynth.circuit import sparams, db
from dfsynth import kicad
from dfsynth.validate import read_touchstone


def test_gvalues_tables():
    # Matthaei / Pozar tables
    assert gvalues('chebyshev', 3, 0.5)[1:4] == pytest.approx([1.5963, 1.0967, 1.5963], abs=1e-4)
    assert gvalues('chebyshev', 5, 0.1)[1:6] == pytest.approx([1.1468, 1.3712, 1.9750, 1.3712, 1.1468], abs=1e-4)
    assert gvalues('butterworth', 4)[1:5] == pytest.approx([0.7654, 1.8478, 1.8478, 0.7654], abs=1e-4)
    assert gvalues('chebyshev', 4, 0.5)[5] == pytest.approx(1.9841, abs=1e-4)


def test_order():
    assert estimate_order('butterworth', 40, 2.0) == 7
    assert estimate_order('chebyshev', 40, 2.0, 0.1) == 6      # N = 5 gives 34.8 dB


def test_microstrip_50ohm():
    sub = Substrate.preset('RO4003C-0.508', t=0)
    w = sub.width(50)
    assert 1.05 < w < 1.2                      # ~1.1 mm, the usual figure for 20 mil RO4003C
    w, s = sub.coupled_synth(70, 38)
    ze, zo, _, _ = sub.coupled(w, s)
    assert (ze, zo) == pytest.approx((70, 38), rel=1e-6)


@pytest.mark.parametrize('kind,topo,f0,bw', [
    ('lowpass', 'stepped', 2e9, 0), ('lowpass', 'stub', 2e9, 0), ('bandpass', 'coupled', 5e9, 0.5e9),
    ('bandpass', 'endcoupled', 10e9, 0.15e9), ('bandpass', 'stub', 3e9, 1.5e9)])
def test_ideal_synthesis(kind, topo, f0, bw):
    """The TEM network of each synthesis lands on the spec (before any calibration)."""
    sub = Substrate.preset('Alumina-0.635' if topo == 'endcoupled' else 'RO4003C-0.508')
    spec = Spec(kind, topo, f0, bw, 'chebyshev', 5, 0.1)
    d = build(spec, sub)
    f = sweep(spec, 4001)
    ef, eb = errors(spec, edges(spec, f, db(sparams(d.ideal, f)[1])))
    assert abs(ef - 1) < 0.05
    if eb is not None:
        assert abs(eb - 1) < 0.08


@pytest.mark.parametrize('kind,topo,f0,bw,sub', [
    ('lowpass', 'stepped', 2e9, 0, 'RO4003C-0.508'), ('lowpass', 'stub', 2e9, 0, 'RO4003C-0.508'),
    ('highpass', 'stub', 2e9, 0, 'RO4003C-0.508'), ('bandpass', 'coupled', 5e9, 0.5e9, 'RO4003C-0.813'),
    ('bandpass', 'hairpin', 2.4e9, 0.3e9, 'FR4-1.6'), ('bandpass', 'endcoupled', 10e9, 0.3e9, 'Alumina-0.635'),
    ('bandpass', 'stub', 3e9, 1.5e9, 'RO4003C-0.508'), ('bandstop', 'stub', 2.4e9, 1e9, 'RO4003C-0.508'),
    ('bandstop', 'stub', 2.4e9, 1.5e9, 'RO4003C-0.508'),
    ('bandpass', 'interdigital', 5e9, 0.5e9, 'RO4003C-0.813'), ('lowpass', 'radial', 2e9, 0, 'RO4003C-0.508'),
    ('bandstop', 'radial', 2.4e9, 2.4e9, 'RO4003C-0.508')])
def test_calibrated_and_written(kind, topo, f0, bw, sub, tmp_path):
    """Each calibrated design lands on the spec and writes a board. The wide band-stop also has its connecting
    lines refined as unit elements: its pass-band match improves, every impedance within the substrate's limits."""
    sub = Substrate.preset(sub)
    spec = Spec(kind, topo, f0, bw, 'chebyshev', 5, 0.1)
    d = calibrate(spec, sub)
    f = sweep(spec, 4001)
    from dfsynth.calibrate import model_response
    _, s21 = model_response(d, f, lossless=True)
    ef, eb = errors(spec, edges(spec, f, db(s21)))
    assert abs(ef - 1) < 2e-3
    if eb is not None:
        assert abs(eb - 1) < 2e-2
    pcb, cfg = kicad.write(d, str(tmp_path / 'f'))
    text = open(pcb).read()
    assert text.count('(') == text.count(')')
    assert text.count('"kicad2ems:RF_Port"') == 2
    assert 'board   = "f.kicad_pcb"' in open(cfg).read()
    if (kind, topo, bw) == ('bandstop', 'stub', 1.5e9):
        pytest.importorskip('scipy')                                # the refine needs it
        from dfsynth.calibrate import passband_rl
        from dfsynth.topologies import _z_limits
        assert len(spec.trim) == 2 * spec.order - 1
        f = sweep(spec, 2401)
        plain = build(Spec(kind, topo, f0, bw, 'chebyshev', 5, 0.1), sub, d.kf, d.kb)
        assert passband_rl(d, f) > passband_rl(plain, f) + 5
        zmin, zmax = _z_limits(sub)
        assert all(zmin - 0.1 < r[1] < zmax + 0.1 for r in d.rows)


def test_touchstone_roundtrip(tmp_path):
    from dfsynth.cli import write_model_s2p
    sub = Substrate.preset('RO4003C-0.508')
    spec = Spec('lowpass', 'stepped', 2e9, order=5)
    d = calibrate(spec, sub)
    f = np.linspace(0.1e9, 5e9, 50)
    write_model_s2p(d, str(tmp_path / 'm.s2p'), f)
    f2, s = read_touchstone(str(tmp_path / 'm.s2p'))
    from dfsynth.calibrate import model_response
    s11, s21 = model_response(d, f)
    assert np.allclose(f2, f, rtol=1e-6)
    assert np.allclose(s[:, 1, 0], s21, atol=1e-5) and np.allclose(s[:, 0, 0], s11, atol=1e-5)


@pytest.mark.parametrize('topo,sub,f0,bw', [('coupled', 'RO4003C-0.813', 5e9, 0.5e9),
                                            ('coupled', 'RO4003C-0.508', 5e9, 0.75e9),
                                            ('hairpin', 'FR4-1.6', 2.4e9, 0.3e9),
                                            ('hairpin', 'RO4003C-0.813', 2.4e9, 0.36e9),
                                            ('endcoupled', 'Alumina-0.635', 10e9, 0.3e9)])
def test_resonators_are_separate_copper(topo, sub, f0, bw):
    """Feeds and resonators must not touch across a coupling gap: order N gives N + 2 copper pieces."""
    shapely = pytest.importorskip('shapely')
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    spec = Spec('bandpass', topo, f0, bw, 'chebyshev', 5, 0.1)
    d = calibrate(spec, Substrate.preset(sub))
    u = unary_union([Polygon(p) for p in d.geometry.polys])
    parts = list(u.geoms) if hasattr(u, 'geoms') else [u]
    assert len(parts) == spec.order + 2
    assert all(len(p.interiors) == 0 for p in parts)


def test_mtl_matches_line_and_pair():
    """The multiconductor model reduces to MLine for one strip and MCoupled for a pair, and is passive."""
    from dfsynth.mtl import Array, Nodal
    from dfsynth.circuit import MLine, MCoupled
    sub = Substrate.preset('RO4003C-0.813')
    f = np.linspace(1e9, 9e9, 41)
    net = Nodal()
    a, b = net.nodes(1), net.nodes(1)
    net.mtl(Array(sub, [1.2], []), a, b, 15.0)
    assert np.abs(sparams([net.two_port(a[0], b[0])], f)[1] - sparams([MLine(sub, 1.2, 15.0)], f)[1]).max() < 5e-3
    for s_ in (sub, sub.lossless()):
        net = Nodal()
        a, b = net.nodes(2), net.nodes(2)
        net.mtl(Array(s_, [1.3, 1.3], [0.3]), a, b, 9.0)
        s11, s21 = sparams([net.two_port(a[0], b[1])], f)
        assert np.abs(s21 - sparams([MCoupled(s_, 1.3, 0.3, 9.0, open_ext=False)], f)[1]).max() < 0.01
    assert np.abs(abs(s11) ** 2 + abs(s21) ** 2 - 1).max() < 1e-9          # the lossless one


def test_radial_stub_notch():
    """kicad2ems's rf_radialstub example (60 degrees, 0.75 x a quarter wave at 2.4 GHz on 0.8 mm FR-4):
    openEMS puts the notch at 2.13 GHz; and a small fan is a capacitor."""
    from dfsynth.circuit import MLine, MRadial, radial_admittance
    sub = Substrate(h=0.8, er=4.4, tand=0.02, t=0.0)
    w = sub.width(50)
    r2 = 0.75 * 299792458 / (2.4e9 * math.sqrt(sub.line(w)[1])) / 4 * 1e3
    f = np.linspace(1.5e9, 3e9, 3001)
    s21 = sparams([MLine(sub, w, 5), MRadial(sub, w, r2, 60), MLine(sub, w, 5)], f)[1]
    assert f[np.argmin(abs(s21))] == pytest.approx(2.13e9, rel=0.03)
    assert radial_admittance(sub, w, 3.0, 60, np.array([1e8]))[0].imag > 0


def test_fab_limits_refused(tmp_path):
    from dfsynth.cli import main
    args = ['--type', 'bandpass', '--topology', 'endcoupled', '--f0', '10G', '--bw', '300M', '--order', '3',
            '--substrate', 'Alumina-0.635', '-o', str(tmp_path / 'e')]
    assert main(args) == 2 and not os.path.exists(tmp_path / 'e.kicad_pcb')
    assert main(args + ['--allow-fab-violations']) == 0 and os.path.exists(tmp_path / 'e.kicad_pcb')


def test_strip_caps_match_closed_forms():
    """The moment-method cross-section agrees with Hammerstad-Jensen and Kirschning-Jansen (zero thickness);
    every strip couples to every other one."""
    from dfsynth.mtl import strip_caps
    from dfsynth.microstrip import C0
    sub = Substrate.preset('RO4003C-0.813', t=0.0)
    for w in (0.4, 1.78, 4.0):
        c, ca = strip_caps([w], [], sub.h, sub.er)[0, 0], strip_caps([w], [], sub.h, 1.0)[0, 0]
        z, ee = sub.line(w)
        assert 1 / (C0 * math.sqrt(c * ca)) == pytest.approx(z, rel=4e-3) and c / ca == pytest.approx(ee, rel=4e-3)
    for s in (0.2, 1.0, 2.0):
        C, Ca = strip_caps([1.78] * 2, [s], sub.h, sub.er), strip_caps([1.78] * 2, [s], sub.h, 1.0)
        ze, zo, _, _ = sub.coupled(1.78, s)
        assert 1 / (C0 * math.sqrt((C[0, 0] + C[0, 1]) * (Ca[0, 0] + Ca[0, 1]))) == pytest.approx(ze, rel=5e-3)
        assert 1 / (C0 * math.sqrt((C[0, 0] - C[0, 1]) * (Ca[0, 0] - Ca[0, 1]))) == pytest.approx(zo, rel=1e-2)
    C = strip_caps([1.78] * 3, [1.6] * 2, sub.h, sub.er)
    assert C[0, 2] < 0 and abs(C[0, 2]) > 0.05 * abs(C[0, 1])               # next-but-one: not negligible


def test_interdigital_space_map():
    """A board that behaves as the model with other trims (and port planes 0.8 mm out): one space-mapping
    step puts the trims where the board then behaves as the target; and the tap necks meet the limits."""
    from dfsynth.calibrate import space_map, _coupled_free, _coupled_spec
    from dfsynth.circuit import MLine
    sub = Substrate.preset('RO4003C-0.813')
    spec = Spec('bandpass', 'interdigital', 5e9, 0.5e9)
    d = build(spec, sub)
    neck = [r for r in d.rows if r[0] == 'tap necks'][0]
    assert neck[2] >= sub.min_width and neck[3] > 0
    shift = np.array([0.04, -0.02, 0.06, -0.015, 0.01, 0.0])          # the board's departure (log trims)
    board = _coupled_spec(spec, _coupled_free(spec) + shift)
    f = np.linspace(4.2e9, 5.8e9, 241)
    s0 = sub.with_(t=0.0)
    ext = MLine(s0, sub.width(50), 0.8)
    s11, s21 = sparams([ext] + build(board, sub).model(s0) + [ext], f)
    new = space_map(spec, spec, sub, 1.0, 1.0, f, s21, s11)
    assert new is not None
    assert _coupled_free(new) == pytest.approx(_coupled_free(spec) - shift, abs=3e-3)


def test_toml_uses_pml(tmp_path):
    d = build(Spec('bandpass', 'coupled', 5e9, 0.5e9), Substrate.preset('RO4003C-0.813'))
    _, cfg = kicad.write(d, str(tmp_path / 'c'))
    assert 'bc      = "PML_8"' in open(cfg).read()


def test_validation_follows_the_run(tmp_path):
    """A kicad2ems run's events reach the log as it runs: its messages, its progress every few seconds."""
    import json
    from dfsynth.validate import Follow
    path = tmp_path / 'events.jsonl'
    ev = [dict(t=0.1, kind='log', text='gfdtd: 1.6 M cells\n  two lines'), dict(t=0.2, kind='stage', name='solve'),
          dict(t=1.0, kind='status', run='P1', step=100, db=-0.0),
          dict(t=5.0, kind='status', run='P1', step=500, db=-2.0),            # within 10 s of the last: quiet
          dict(t=12.0, kind='status', run='P1', step=1200, db=-21.3),
          dict(t=13.0, kind='status', run='mom25d', fraction=0.4, solves=24, f_ghz=2.4, eta=30),
          dict(t=14.0, kind='file', label='x', path='y')]
    out = []
    f = Follow(str(path), out.append)
    path.write_text(''.join(json.dumps(e) + '\n' for e in ev[:3]) + '{"t": 4, "kind": "lo')   # half a line
    f.read()
    assert out == ['    gfdtd: 1.6 M cells', '      two lines', '    solving...', '    P1: step 100, energy -0.0 dB, 1 s']
    path.write_text(''.join(json.dumps(e) + '\n' for e in ev[:3]) + ''.join(json.dumps(e) + '\n' for e in ev[3:]))
    f.pos = len(''.join(json.dumps(e) + '\n' for e in ev[:3]))
    f.read()
    assert out[4:] == ['    P1: step 1200, energy -21.3 dB, 12 s',
                       '    mom25d: 40% (24 frequencies, 2.400 GHz), about 30 s left, 13 s']
