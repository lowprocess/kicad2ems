"""planemodes: the physics against closed forms, and both readers on small
boards written here."""
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
    cfg = StudyConfig(fmin=0.5e9, fmax=6e9, ref_nets=['GND'], mesh=MeshConfig(max_cell=0.6))
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
