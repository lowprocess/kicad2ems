"""Lumped R / L / C parts: value parsing, detection on the board, mesh lines at the element ends and
the CSXCAD calls (fake openEMS: the fake_openems fixture in conftest.py)."""
import numpy as np
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.parts import parse_value, Part


@pytest.fixture(scope='module')
def rlc(boards):
    return boards['rlc']


@pytest.mark.parametrize('text,value', [
    ('4p7', 4.7e-12), ('10k', 1e4), ('1.2nH', 1.2e-9), ('0R5', 0.5), ('100n', 1e-7), ('1M', 1e6), ('47R', 47.0),
    ('4.7uF', 4.7e-6), ('2.2 nH', 2.2e-9), ('100n 50V X7R', 1e-7), ('4k7', 4700.0), ('1,5p', 1.5e-12), ('0', 0.0),
    ('DNP', None), ('R', None), ('tbd', None), ('1.2.3', None)])
def test_parse_value(text, value):
    v = parse_value(text)
    assert v == pytest.approx(value) if value is not None else v is None


def test_parts_detected_and_skipped(rlc):
    m = Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos', log=lambda *a: None)
    assert [p.ref for p in m.parts] == ['C1']
    c1 = m.parts[0]
    assert c1.C == pytest.approx(1.5e-12) and c1.R is None and c1.L is None and not c1.series
    assert c1.axis == 0 and c1.gap == pytest.approx(0.4) and c1.layer == 'F.Cu'
    assert c1.s[0] == pytest.approx(11.8) and c1.e[0] == pytest.approx(12.2) and c1.s[2] == c1.e[2] == m.cu_z['F.Cu']
    notes = "\n".join(m.notes)
    assert 'R9 skipped: DNP' in notes and "L3 skipped: value 'tbd'" in notes
    assert 'C1: lumped 1.5 pF' in m.describe()
    # --parts none -> nothing
    m3 = Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos', parts='none',
               log=lambda *a: None)
    assert not m3.parts


def test_part_topologies():
    s, e = [0, 0, 0], [1, 1, 0]
    assert not Part('L1', None, 1e-9, None, 'F.Cu', 0, 0, s, e).series
    assert Part('C1', 0.1, 0.5e-9, 1e-12, 'F.Cu', 0, 0, s, e).series                 # with ESR / ESL
    assert not Part('R1', 50.0, None, None, 'F.Cu', 0, 0, s, e).series
    assert Part('R2', 0.0, None, None, 'F.Cu', 0, 0, s, e).short
    assert Part('C2', 0.1, 0.5e-9, 1e-12, 'F.Cu', 0, 0, s, e).value_str() == '0.1 ohm + 500 pH + 1 pF'


def test_part_pieces():
    s, e = [1.0, 0, 0.2], [1.4, 0.5, 0.2]
    one = Part('C1', None, None, 1e-12, 'F.Cu', 0.2, 0, s, e).pieces()
    assert len(one) == 1 and one[0][1] == {'C': 1e-12} and not one[0][2]
    (r, rv, rser, rs, re_), (x, xv, xser, xs, xe) = Part('C2', 0.1, 5e-10, 6.8e-12, 'F.Cu', 0.2, 0, s, e).pieces()
    assert rv == {'R': 0.1} and not rser and xv == {'L': 5e-10, 'C': 6.8e-12} and xser
    assert rs[0] == 1.0 and re_[0] == pytest.approx(1.2) and xs[0] == pytest.approx(1.2) and xe[0] == 1.4
    assert Part('L1', None, 8e-9, None, 'F.Cu', 0.2, 0, s, e).pieces()[0][1:3] == ({'L': 8e-9}, False)


def test_lowpass_demo(boards):
    """examples/make_demo_board.py lowpass: L1 / L3 in line (x), C2 to GND along y, ESR / ESL fields; the
    ideal circuit is a 1 GHz Butterworth low-pass with the capacitor's self-resonance notch."""
    base = boards['lowpass']
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF_IN', 'N1', 'RF_OUT', 'GND'], pos_file=base + '-top.pos',
              log=lambda *a: None)
    p = {pt.ref: pt for pt in m.parts}
    assert sorted(p) == ['C2', 'L1', 'L3']
    assert p['L1'].axis == 0 and p['L1'].L == pytest.approx(8.2e-9) and p['L1'].R == pytest.approx(0.4)
    assert p['C2'].axis == 1 and p['C2'].series and p['C2'].L == pytest.approx(0.5e-9)
    assert p['C2'].C == pytest.approx(6.8e-12) and p['C2'].R == pytest.approx(0.1)
    from kicad2ems.touchstone import read_touchstone
    f, S = read_touchstone(base + '_ideal.s2p')
    s21 = lambda g: 20 * np.log10(abs(S[np.argmin(abs(f - g * 1e9)), 1, 0]))
    assert s21(0.1) > -0.3 and -6.5 < s21(1.0) < -2 and s21(2.0) < -15
    srf = 1 / (2 * np.pi * np.sqrt(0.5e-9 * 6.8e-12)) / 1e9                    # ~2.73 GHz: deepest S21
    assert abs(f[np.argmin(abs(S[:, 1, 0]))] / 1e9 - srf) < 0.05


@pytest.mark.parametrize('lib,pkg', [
    ('Resistor_SMD:R_0402_1005Metric', '0402'), ('Capacitor_SMD:C_01005_0402Metric', '01005'),
    ('Inductor_SMD:L_0201_0603Metric', '0201'), ('C_1608Metric', '0603'), ('L_0603', '0603'),
    ('R_2512_6332Metric_Pad1.40x3.35mm', '2512'), ('Resistor_SMD:R_0805_2012Metric', '0805'),
    ('R_Small', None), ('RF:SMA_Edge', None), ('', None), (None, None)])
def test_package_from_footprint_name(lib, pkg):
    from kicad2ems.parts import package
    assert package(lib) == pkg


def test_part_body_true_size(rlc):
    """A part's chip: its size from the footprint name, centred between its pads, kept through board.json (drawn:
    kicad2ems-gui's tests)."""
    from kicad2ems.parts import SIZES
    m = Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos', log=lambda *a: None)
    c1 = m.parts[0]
    assert c1.package == '0402' and tuple(c1.body) == SIZES['0402'] and c1.kind == 'C'
    assert c1.centre[0] == pytest.approx(12.0) and c1.centre[1] == pytest.approx(0.5 * (c1.s[1] + c1.e[1]))
    d = c1.to_dict()
    back = Part.from_dict(d)
    assert back.package == '0402' and list(back.body) == list(c1.body) and back.to_dict() == d
    old = {k: v for k, v in d.items() if k not in ('package', 'body', 'centre')}     # an older board.json
    assert Part.from_dict(old).body is None
