"""planemodes: the physics against closed forms, and the readers (KiCad,
ODB++, HyperLynx) on small boards written here."""
import math
import os
import tarfile

import numpy as np
import pytest
import shapely

from planemodes import MeshConfig, SolverConfig, StudyConfig, run_study
from planemodes.model import Board, Layer, Via
from planemodes.readers import load_board
from planemodes.readers import lzw

C0 = 299_792_458.0


def _plate(a=40.0, b=25.0, vias=()):
    """Two GND planes a x b mm, 0.2 mm of er 4.4 between them."""
    g = shapely.box(0, 0, a, b)
    stack = [Layer('F.Cu', 'copper', 0.035), Layer('d1', 'dielectric', 0.2, 4.4, 0.02),
             Layer('B.Cu', 'copper', 0.035)]
    return Board('test', stack, {'F.Cu': {'GND': g}, 'B.Cu': {'GND': g}}, list(vias), g)


def _closed(a, b, er, fmax):
    """The open rectangle's mode frequencies below fmax (static one left out)."""
    c = C0 / (2 * math.sqrt(er))
    f = sorted(c * math.hypot(m / (a * 1e-3), n / (b * 1e-3)) for m in range(12) for n in range(12))
    return np.array([x for x in f if 0 < x <= fmax])


def test_a_rectangle_s_modes_are_the_closed_form_s():
    # the parallel-plate model alone (the edge effects are tested below)
    cfg = StudyConfig(fmin=0.5e9, fmax=6e9, ref_nets=['GND'], mesh=MeshConfig(max_cell=0.6),
                      solver=SolverConfig(fringe=False, radiation=False))
    (r,) = run_study(_plate(), cfg)
    want = _closed(40, 25, 4.4, 6e9)
    assert len(r.modes.f) == len(want)
    assert np.abs(r.modes.f / want - 1).max() < 3e-3
    # each mode RMS 1 over the cavity; a whole-plate mode peaks at 1.4-2x RMS
    assert 1.3 < r.modes.peak[0] < 1.5


def test_a_ground_via_holds_the_voltage_at_zero():
    board = _plate(vias=[Via(20, 12.5, 0.3, 0.6, 'F.Cu', 'B.Cu', 'GND')])
    cfg = StudyConfig(fmin=0.05e9, fmax=3e9, ref_nets=['GND'], mesh=MeshConfig(max_cell=0.8))
    (r,) = run_study(board, cfg)
    assert r.mesh.fixed.any()
    assert np.abs(r.modes.V[r.mesh.fixed]).max() == 0
    # the plate swinging against the one via: a mode below the first open one
    assert r.modes.f[0] < _closed(40, 25, 4.4, 3e9)[0]


def test_a_signal_via_at_a_node_does_not_couple():
    """(1,0) has its node on x = a/2: a via there sees nothing, one in a
    corner sees the most."""
    board = _plate(vias=[Via(20, 12.5, 0.3, 0.6, 'F.Cu', 'B.Cu', 'SIG1'),
                         Via(2, 2, 0.3, 0.6, 'F.Cu', 'B.Cu', 'SIG2')])
    cfg = StudyConfig(fmin=1e9, fmax=2e9, ref_nets=['GND'], mesh=MeshConfig(max_cell=0.6))
    (r,) = run_study(board, cfg)
    z = r.modes.zpeak[:, 0]
    assert z[0] < 0.01 * z[1]


KICAD = '''(kicad_pcb (version 20241229) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.4) (loss_tangent 0.02))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "GND") (net 2 "SIG")
  (gr_rect (start 0 0) (end 30 20) (stroke (width 0.1)) (fill no) (layer "Edge.Cuts"))
  (zone (net 1) (net_name "GND") (layers "F.Cu" "B.Cu") (polygon (pts (xy 0 0) (xy 30 0) (xy 30 20) (xy 0 20)))
    (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 30 0) (xy 30 20) (xy 0 20)))
    (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 30 0) (xy 30 20) (xy 0 20))))
  (segment (start 5 5) (end 25 5) (width 0.4) (layer "F.Cu") (net 2))
  (via (at 10 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
  (footprint "R" (layer "F.Cu") (at 15 15 90)
    (pad "1" smd rect (at 1 0 90) (size 1 2) (layers "F.Cu") (net 2 "SIG")))
)'''


def test_the_kicad_reader(tmp_path):
    p = tmp_path / 'b.kicad_pcb'
    p.write_text(KICAD)
    b = load_board(p)
    assert b.copper_layers == ['F.Cu', 'B.Cu']
    assert b.gap('F.Cu', 'B.Cu') == pytest.approx((0.2, 4.4, 0.02))
    assert b.copper['B.Cu']['GND'].area == pytest.approx(600, rel=1e-3)
    assert b.copper['F.Cu']['SIG'].area > 20 * 0.4            # the track and the pad
    # the pad: footprint at (15, 15) turned 90 deg, pad offset (1, 0) -> (15, 14)
    assert b.copper['F.Cu']['SIG'].contains(shapely.Point(15, 14))
    (v,) = b.vias
    assert (v.x, v.y, v.net, v.top, v.bottom) == (10, 10, 'GND', 'F.Cu', 'B.Cu')
    assert b.outline.area == pytest.approx(600)


def _odb_job(root):
    """A minimal ODB++ job: two copper layers (inch), a dielectric, nets and
    a drill layer."""
    def w(rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(text)
    w('matrix/matrix', '''STEP {\n COL=1\n NAME=PCB\n}\n
LAYER {\n ROW=1\n CONTEXT=BOARD\n TYPE=SIGNAL\n NAME=TOP\n}\n
LAYER {\n ROW=2\n CONTEXT=BOARD\n TYPE=DIELECTRIC\n NAME=D1\n}\n
LAYER {\n ROW=3\n CONTEXT=BOARD\n TYPE=POWER_GROUND\n NAME=GND2\n}\n
LAYER {\n ROW=4\n CONTEXT=BOARD\n TYPE=DRILL\n NAME=DRILL\n START_NAME=TOP\n END_NAME=GND2\n}\n''')
    w('steps/pcb/layers/d1/attrlist', 'UNITS=INCH\n.layer_dielectric=0.008\n.dielectric_constant=3.5\n.loss_tangent=0.004\n')
    plane = 'S P 0\nOB 0 0 I\nOS 1 0\nOS 1 0.8\nOS 0 0.8\nOE\nSE\n'
    # TOP: the plane, a 10 mil line (net SIG), a 20 mil round pad
    w('steps/pcb/layers/top/features', 'UNITS=INCH\n$0 r10\n$1 r20\n' + plane
      + 'L 0.1 0.1 0.9 0.1 0 P 0\nP 0.5 0.5 1 P 0 0\n')
    w('steps/pcb/layers/gnd2/features', 'UNITS=INCH\n' + plane)
    w('steps/pcb/layers/drill/features', 'UNITS=INCH\n$0 r12\nP 0.5 0.5 0 P 0 0\n')
    w('steps/pcb/profile', 'UNITS=INCH\n' + plane)
    w('steps/pcb/eda/data', 'UNITS=INCH\nLYR top gnd2 drill\nNET GND\nSNT PLN\nFID C 0 0\nFID C 1 0\n'
      'FID C 0 2\nFID H 2 0\nNET SIG\nSNT TRC\nFID C 0 1\n')


@pytest.mark.parametrize('archive', [False, True])
def test_the_odbpp_reader(tmp_path, archive):
    job = tmp_path / 'job'
    _odb_job(str(job))
    path = job
    if archive:
        path = tmp_path / 'job.tgz'
        with tarfile.open(path, 'w:gz') as t:
            t.add(job, arcname='job')
    b = load_board(path)
    assert b.copper_layers == ['top', 'gnd2']
    d, er, tand = b.gap('top', 'gnd2')
    assert (d, er, tand) == pytest.approx((0.2032, 3.5, 0.004))
    assert b.copper['gnd2']['GND'].area == pytest.approx(25.4 * 25.4 * 0.8, rel=1e-3)
    assert set(b.copper['top']) == {'GND', 'SIG'}
    (v,) = b.vias
    assert v.net == 'GND' and (v.top, v.bottom) == ('top', 'gnd2')
    assert v.drill == pytest.approx(0.3048, rel=2e-2)
    assert (v.x, v.y) == pytest.approx((12.7, -12.7), abs=1e-6)     # y flipped to point down


STACKUP_XML = """<?xml version="1.0"?>
<StackupFile Version="8.1" DefaultUnits="MM"><EdaData CompanyName="">
 <Specs><Spec SpecName="pcb">
  <Material MaterialName="TOP"><Conductor ConductorType="COPPER" CopperWeight_oz_ft2="1"/>
   <Default_Thickness Thickness="0.035"/></Material>
  <Material MaterialName="CORE"><Dielectric DielectricType="CORE"><Properties PropertyName="d">
   <Property FrequencyVal="1" DielectricConstant_Dk="3.0" LossTangent_Df="0.003"/></Properties></Dielectric>
   <Default_Thickness Thickness="12" Units="MIL"/></Material>
 </Spec></Specs>
 <Stackup StackupName="pcb"><Group GroupName="g">
  <Layer LayerName="TOP" LayerType="SIGNAL"><SpecRef MaterialSpecName="pcb"><Material MaterialName="TOP"/></SpecRef></Layer>
  <Layer LayerName="CORE" LayerType="DIELECTRIC"><SpecRef MaterialSpecName="pcb"><Material MaterialName="CORE"/></SpecRef></Layer>
  <Layer LayerName="GND2" LayerType="POWER_GROUND"><SpecRef MaterialSpecName="pcb"><Material MaterialName="TOP"/></SpecRef></Layer>
 </Group></Stackup></EdaData></StackupFile>"""


def test_odbpp_negative_plane_thermals_and_stackup_xml(tmp_path):
    """GND2 made NEGATIVE: the profile is copper, a thermal (45 deg spokes)
    joins the GND via, an antipad clears the SIG via; the stack-up comes
    from matrix/stackup.xml (12 mil of er 3.0) over the matrix's D1."""
    _odb_job(str(tmp_path))
    m = tmp_path / 'matrix' / 'matrix'
    m.write_text(m.read_text().replace('NAME=GND2\n', 'NAME=GND2\n POLARITY=NEGATIVE\n'))
    (tmp_path / 'matrix' / 'stackup.xml').write_text(STACKUP_XML)
    (tmp_path / 'steps/pcb/layers/gnd2/features').write_text(
        'UNITS=INCH\n$0 ths40x30x45x4x8\n$1 r40\nP 0.5 0.5 0 P 0 0\nP 0.2 0.2 1 P 0 0\n')
    (tmp_path / 'steps/pcb/layers/drill/features').write_text(
        'UNITS=INCH\n$0 r12\nP 0.5 0.5 0 P 0 0\nP 0.2 0.2 0 P 0 0\n')
    eda = tmp_path / 'steps/pcb/eda/data'
    eda.write_text(eda.read_text() + 'FID H 2 1\n')
    b = load_board(tmp_path)
    assert b.gap('top', 'gnd2') == pytest.approx((12 * 0.0254, 3.0, 0.003))
    plane = b.copper['gnd2']['GND']
    mm = lambda x, y: shapely.Point(x * 25.4, -y * 25.4)
    assert plane.contains(mm(0.5, 0.5))                  # inside the thermal: the pad
    assert not plane.contains(mm(0.2, 0.2))              # the SIG via's antipad
    r = 0.0175                                           # in the thermal's air gap ring
    c, s = math.cos(math.radians(45)), math.sin(math.radians(45))
    assert plane.contains(mm(0.5 + r * c, 0.5 + r * s))  # a spoke
    assert not plane.contains(mm(0.5 + r, 0.5))          # between spokes
    ring = math.pi / 4 * (40 ** 2 - 30 ** 2) - 4 * 8 * 5   # thermal ring less its spokes, mil^2
    hole = math.pi / 4 * 40 ** 2
    want = (1000 * 800 - ring - hole) * 0.0254 ** 2
    assert plane.area == pytest.approx(want, rel=2e-3)


HYP = """{VERSION=2.0}
{UNITS=ENGLISH LENGTH}
* a comment line
{PLANE_SEP=0.005}
{BOARD
(PERIMETER_SEGMENT X1=0 Y1=0 X2=1 Y2=0)
(PERIMETER_SEGMENT X1=1 Y1=0 X2=1 Y2=0.8)
(PERIMETER_SEGMENT X1=1 Y1=0.8 X2=0 Y2=0.8)
(PERIMETER_SEGMENT X1=0 Y1=0.8 X2=0 Y2=0)
}
{STACKUP
(SIGNAL T=0.0014 L=Top)
(DIELECTRIC T=0.008 C=3.5 LT=0.004 L=D1)
(PLANE T=0.0014 L=Bottom)
}
{PADSTACK=gv, 0.012
(MDEF, 0, 0.024, 0.024, 0, M)
}
{PADSTACK=sv, 0.012
(MDEF, 0, 0.024, 0.024, 0, M)
(ADEF, 0, 0.040, 0.040, 0, A)
}
{PADSTACK=smd
(Top, 1, 0.04, 0.02, 90)
}
{NET=GND
(VIA X=0.5 Y=0.4 P=gv)
{POLYGON L=Top T=POUR W=0 ID=1 X=0.6 Y=0.1
(LINE X=0.9 Y=0.1)
(CURVE X1=0.9 Y1=0.1 X2=0.9 Y2=0.3 XC=0.9 YC=0.2 R=0.1)
(LINE X=0.6 Y=0.3)
}
{POLYVOID ID=1 X=0.7 Y=0.15
(LINE X=0.8 Y=0.15)
(LINE X=0.8 Y=0.25)
(LINE X=0.7 Y=0.25)
}
}
{NET=SIG
(SEG X1=0.1 Y1=0.6 X2=0.9 Y2=0.6 W=0.01 L=Top
(VIA X=0.2 Y=0.2 P=sv)
(PIN X=0.3 Y=0.7 R=R1.1 P=smd)
}
"""


def test_the_hyperlynx_reader(tmp_path):
    p = tmp_path / 'b.hyp'
    p.write_text(HYP)
    b = load_board(p)
    assert b.copper_layers == ['Top', 'Bottom']
    assert b.gap('Top', 'Bottom') == pytest.approx((0.2032, 3.5, 0.004))   # C= is er here
    mm = lambda x, y: shapely.Point(x * 25.4, -y * 25.4)               # y flipped
    assert b.outline.area == pytest.approx(25.4 ** 2 * 0.8)
    pour = b.copper['Top']['GND']                     # a box with a half-round end, a void
    assert pour.contains(mm(0.95, 0.2)) and not pour.contains(mm(0.75, 0.2))
    assert pour.area == pytest.approx(25.4 ** 2 * (0.3 * 0.2 + math.pi * 0.01 / 2 - 0.01)
                                      + math.pi * (0.012 * 25.4) ** 2, rel=5e-3)
    sig = b.copper['Top']['SIG']                      # the track (missing ')') and the pin
    assert sig.contains(mm(0.5, 0.6)) and sig.contains(mm(0.3, 0.7 + 0.018))  # 90 deg pad
    # the empty PLANE layer: flooded for GND, the SIG via's antipad cleared
    plane = b.copper['Bottom']['GND']
    assert plane.contains(mm(0.5, 0.4)) and not plane.contains(mm(0.2 + 0.015, 0.2))
    assert {(v.net, v.top, v.bottom) for v in b.vias} == {('GND', 'Top', 'Bottom'),
                                                         ('SIG', 'Top', 'Bottom')}


def _compress(data, maxbits=16):
    """A Unix-compress encoder (block mode), only to test the decoder."""
    table = {bytes([i]): i for i in range(256)}
    nxt, bits, out, acc, nacc, group, codes = 257, 9, bytearray(b'\x1f\x9d' + bytes([0x80 | maxbits])), 0, 0, 0, []
    w = b''
    def emit(code):
        nonlocal acc, nacc
        acc |= code << nacc; nacc += bits
        while nacc >= 8:
            out.append(acc & 0xFF); acc >>= 8; nacc -= 8
    def pad_group(n_codes):            # skip to the end of the 8-code group
        nonlocal acc, nacc
        rem = (8 - n_codes % 8) % 8
        for _ in range(rem):
            emit(0)
    count = 0
    for ch in data:
        wc = w + bytes([ch])
        if wc in table:
            w = wc
            continue
        emit(table[w]); count += 1
        if nxt < (1 << maxbits):
            table[wc] = nxt; nxt += 1
            if nxt > (1 << bits) and bits < maxbits:
                pad_group(count); count = 0; bits += 1
        w = bytes([ch])
    if w:
        emit(table[w])
    if nacc:
        out.append(acc & 0xFF)
    return bytes(out)


def test_lzw_decodes_compress_streams():
    rng = np.random.default_rng(1)
    data = bytes(rng.integers(0, 6, 300_000, dtype=np.uint8)) + b'ODB++ features ' * 5000
    assert lzw.decompress(_compress(data)) == data


def test_an_empty_frequency_window_ends_at_once():
    """No modes between 0.5 and 1.5 GHz (the first is at 1.79): the solve must
    stop after one round, not keep asking for more until max_modes."""
    cfg = StudyConfig(fmin=0.5e9, fmax=1.5e9, ref_nets=['GND'], mesh=MeshConfig(max_cell=0.6),
                      solver=SolverConfig(max_modes=50))
    (r,) = run_study(_plate(), cfg)
    assert len(r.modes.f) == 0 and not r.modes.capped


def _outer_pair(top, h, er, bottom=None):
    """An outer pair: `top` (F.Cu GND) over a larger B.Cu GND plane."""
    bottom = bottom or top.buffer(5 * h + 5, join_style='mitre')
    stack = [Layer('F.Cu', 'copper', 0.035), Layer('d1', 'dielectric', h, er, 0.0),
             Layer('B.Cu', 'copper', 0.035)]
    return Board('test', stack, {'F.Cu': {'GND': top}, 'B.Cu': {'GND': bottom}}, [], bottom)


def test_an_outer_layer_strip_resonates_like_a_microstrip():
    """A half-wave strip, 0.8 x 10 mm on 0.21 mm of FR-4 (an outer pour
    sliver): Hammerstad's eps_eff and open-end extension give its frequency.
    The parallel-plate model alone is ~9 % low; with the fringe within 1.5 %."""
    from planemodes.edges import microstrip
    w, L, h, er = 0.8, 10.0, 0.21, 4.4
    u = w / h
    _, eeff = microstrip(u, er)
    dl = 0.412 * h * (eeff + 0.3) * (u + 0.264) / ((eeff - 0.258) * (u + 0.8))
    want = C0 / (2 * math.sqrt(eeff) * (L + 2 * dl) * 1e-3)
    board = _outer_pair(shapely.box(0, 0, L, w), h, er)
    got = {}
    for fr in (False, True):
        cfg = StudyConfig(fmin=4e9, fmax=10e9, ref_nets=['GND'], pairs=[('F.Cu', 'B.Cu')],
                          mesh=MeshConfig(max_cell=0.1),
                          solver=SolverConfig(fringe=fr, radiation=False))
        (r,) = run_study(board, cfg)
        got[fr] = r.modes.f[0]
    assert got[False] / want - 1 < -0.07
    assert abs(got[True] / want - 1) < 0.015


def test_a_patch_s_radiation_q_is_balanis_two_slot_one():
    """A patch on 1.6 mm FR-4, resonant along L = 30 mm (2.4 GHz), radiating
    from its two W = 20 mm edges: Q_rad against two slots with their mutual
    conductance (Balanis): within 10 %."""
    from scipy.integrate import quad
    from scipy.special import j0
    W, L, h, er = 20.0, 30.0, 1.6, 4.4
    board = _outer_pair(shapely.box(0, 0, L, W), h, er)
    cfg = StudyConfig(fmin=1.5e9, fmax=3e9, ref_nets=['GND'], pairs=[('F.Cu', 'B.Cu')],
                      mesh=MeshConfig(max_cell=0.8),
                      solver=SolverConfig(fringe=False, radiation=True))
    (r,) = run_study(board, cfg)
    f = r.modes.f[0]                                   # TM10: along L (x)
    k0 = 2 * math.pi * f / C0
    s = lambda t: (math.sin(k0 * W * 1e-3 / 2 * math.cos(t)) / math.cos(t)) ** 2 * math.sin(t) ** 3
    G1 = quad(s, 0, math.pi, points=[math.pi / 2])[0] / (120 * math.pi ** 2)
    G12 = quad(lambda t: s(t) * j0(k0 * L * 1e-3 * math.sin(t)), 0, math.pi,
               points=[math.pi / 2])[0] / (120 * math.pi ** 2)
    eps0 = 8.8541878128e-12
    q_balanis = 2 * math.pi * f * eps0 * er * W * L * 1e-3 / (2 * h * 2 * (G1 + G12))
    assert abs(r.modes.Q_rad[0] / q_balanis - 1) < 0.10
