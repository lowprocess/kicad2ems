"""The canonical board (board.Board, written as <name>_board.json): what every
solver gets. Its file round trip, and the golden summaries of the example boards
in tests/golden/: a change to the conversion shows up as a reviewable diff there
before any solver runs (KICAD2EMS_UPDATE_GOLDEN=1 rewrites them)."""
import json, os, re
import pytest
from kicad2ems.core.board import Board, FORMAT, VERSION, _plain
from kicad2ems.core.config import build_settings
from kicad2ems.core.pipeline import board_from_settings

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EX = os.path.join(ROOT, 'examples')
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden')
quiet = lambda *a: None

# example configs: the committed boards as they are; the generated ones (demo_*,
# antenna_*) with the board the session fixture writes (the config's settings,
# the fixture's file)
CONFIGS = [
    'rf_interdig', 'hairpin', 'rf_branchline', 'rf_coupler', 'rf_gcpw',
    'rf_steplpf', 'rf_stubnotch', 'rf_wilkinson', 'sma_launch',
    'demo_transition', 'demo_lowpass', 'demo_highpass', 'demo_bandpass',
    'antenna_dipole', 'antenna_ifa', 'antenna_patch', 'antenna_slot',
    'antenna_vivaldi', 'rf_siw', 'rf_msvia', 'rf_ms2cpw', 'rf_slotline',
    'rf_marchand', 'rf_bends', 'rf_serpentine', 'rf_crosstalk',
    'rf_crosstalk_guard', 'rf_pcoupled', 'rf_combline', 'rf_openloop', 'rf_dgs',
    'rf_ebg', 'rf_idc', 'rf_meander', 'rf_spiral'
]
GENERATED = dict(demo_transition='demo', demo_lowpass='lowpass',
                 demo_highpass='highpass', demo_bandpass='bandpass',
                 antenna_dipole='dipole', antenna_ifa='ifa',
                 antenna_patch='patch', antenna_slot='slot',
                 antenna_vivaldi='vivaldi')
EXTRA = dict(diff=dict(ground='B.Cu'), rlc=dict(nets=['RF1', 'RF2', 'GND']),
             launch=dict(nets=['RF', 'GND'], edge_walls='all'))


def board_of(case, boards):
    if case in EXTRA:
        base = boards[case]
        s = build_settings(
            dict(EXTRA[case], board=base + '.kicad_pcb', pos=base + '-top.pos'))
    else:
        cli = {}
        if case in GENERATED:
            base = boards[GENERATED[case]]
            cli = dict(board=base + '.kicad_pcb')
            if os.path.exists(base + '-top.pos'):
                cli['pos'] = base + '-top.pos'
        s = build_settings(cli, os.path.join(EX, case + '.toml'))
    return board_from_settings(s, log=quiet)


def _close(a, b, path=''):
    """Equal up to 1e-3 in the numbers (GEOS versions differ in the last digits
    of unions)."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))}"
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{path}: {len(a)} != {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    elif (isinstance(a, (int, float)) and isinstance(b, (int, float))
          and not isinstance(a, bool)):
        assert abs(a - b) <= 1e-3 + 1e-6 * abs(b), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@pytest.mark.parametrize('case', CONFIGS + sorted(EXTRA))
def test_golden_summary(case, boards):
    got = json.loads(
        json.dumps(board_of(case, boards).summary(), default=_plain))
    path = os.path.join(GOLDEN, case + '.json')
    if os.environ.get('KICAD2EMS_UPDATE_GOLDEN') or not os.path.exists(path):
        os.makedirs(GOLDEN, exist_ok=True)
        # lists of plain values on one line each
        text = json.dumps(got, indent=1)
        text = re.sub(r'\[\s+([^\[\]{}]*?)\s+\]',
                      lambda m: '[' + ' '.join(m.group(1).split()) + ']', text)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text + '\n')
        if not os.environ.get('KICAD2EMS_UPDATE_GOLDEN'):
            pytest.fail(
                f"no golden summary for {case}: written to {path}, check it "
                "and commit it"
            )
    with open(path, encoding='utf-8') as fh:
        _close(got, json.load(fh))


@pytest.mark.parametrize('case', ['launch', 'demo_lowpass', 'diff'])
def test_round_trip(case, boards, tmp_path):
    """board.json holds everything a solver uses: the board read back gives the
    same summary and the same line model (and the same FDTD mesh:
    kicad2ems-fdtd's test_board_json.py)."""
    from kicad2ems.core.lines.tlines import build
    b = board_of(case, boards)
    b2 = Board.load(b.save(str(tmp_path / 'b.json')), log=quiet)
    assert b2.summary() == b.summary()
    assert [p.to_dict() for p in b2.ports] == [p.to_dict() for p in b.ports]
    assert ((len(b2.prims.items), len(b2.prims.shapes), len(b2.prims.tracks),
             len(b2.prims.footprints)) == (len(b.prims.items),
                                           len(b.prims.shapes),
                                           len(b.prims.tracks),
                                           len(b.prims.footprints)))
    assert build(b2).summary() == build(b).summary()


def test_reader_checks_format_and_version(tmp_path):
    d = dict(format=FORMAT, version=VERSION + 1)
    with pytest.raises(ValueError, match='newer'):
        Board.from_dict(d)
    with pytest.raises(ValueError, match='not a kicad2ems-board'):
        Board.from_dict(dict(format='something else', version=1))


def test_a_board_json_is_an_input(boards, run_cli, tmp_path):
    """The board.json of one run is the input of the next: no KiCad file needed,
    its selection kept."""
    base = boards['lowpass']
    code, so, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos',
                            '--out', tmp_path / 'a', '--preview', '--no-report')
    assert code == 0, err
    bj = tmp_path / 'a' / 'lowpass_board.json'
    assert json.loads(bj.read_text(encoding='utf-8'))['format'] == FORMAT
    code, so, err = run_cli(bj, '--nets', 'GND', '--out', tmp_path / 'b',
                            '--preview', '--no-report')
    assert code == 0, err
    assert 'is a resolved board: its own selection holds, not nets' in so
    assert ((tmp_path / 'b' / 'lowpass_board.json').exists()
            and (tmp_path / 'b' / 'lowpass_mesh.png').exists())


def test_a_port_s_reference_is_ground_copper_and_a_plug_is_not_a_port():
    """A port's reference: the nearest ground net's copper under it, a signal
    trace nearer it passed over (it had been taken, with nothing said); a
    placement file's P1, P2 references alone (plugs) are not ports."""
    from kicad2ems.core.importers.kicad.pnp import pos_port_refs
    rows = [
        dict(ref='P1', val='USB_C', package='USB_C_Receptacle'),
        dict(ref='TP3', val='P2', package='TestPoint'),
        dict(ref='J4', val='SMA', package='RF_Port_SMA')
    ]
    assert pos_port_refs(rows) == {'TP3', 'J4'}


def test_a_port_on_a_trace_at_an_angle(tmp_path):
    """R-E1: a vertical port takes its trace's angle: d along the trace, axis
    and sgn its dominant axis, its sheet normal to that axis and as wide as the
    trace's cut there; it survives board.json; a pad or point port takes its
    track's own direction (snapped to x or y within a degree)."""
    import sys
    import numpy as np
    from kicad2ems.core.board import Port
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core import region as R
    sys.path.insert(0, EX)
    import make_rf_examples as M
    bd, deg, L = M.Board(), 30.0, 16.0
    a = np.radians(deg)
    u = np.array([np.cos(a), -np.sin(a)])  # KiCad y down: up and to the right
    p0, p1 = (np.array([12.0, 12.0]) - u * L / 2,
              np.array([12.0, 12.0]) + u * L / 2)
    bd.track([tuple(p0), tuple(p1)], M.W50)
    bd.port(1, *(p0 + 0.5 * u), deg)
    bd.port(2, *(p1 - 0.5 * u), deg + 180)
    bd.write(str(tmp_path / 'b.kicad_pcb'), 24.0, 24.0)
    m = Board(KiCadBoard(str(tmp_path / 'b.kicad_pcb')), nets=['RF', 'GND'],
              ground='B.Cu', log=lambda *a: None)
    p1, p2 = m.ports
    assert (p1.oblique and np.allclose(p1.d, [np.cos(a), np.sin(a)])
            and (p1.axis, p1.sgn) == (0, 1))
    assert np.allclose(p2.d, -p1.d) and (p2.axis, p2.sgn) == (0, -1)
    assert p1.w == pytest.approx(M.W50, abs=0.01)
    s, e = p1.box()
    assert s[0] == e[0] and e[1] - s[1] == pytest.approx(p1.w / np.cos(a))
    assert 'its trace at 30.0 deg' in m.describe()
    q = Port.from_dict(json.loads(json.dumps(p1.to_dict())))
    assert np.allclose(q.d, p1.d) and q.oblique
    # a board.json from before: its axis
    old = Port.from_dict({k: v for k, v in p1.to_dict().items() if k != 'd'})
    assert not old.oblique and np.allclose(old.d, [1, 0])
    assert (np.allclose(R._track([0.6, 0.8]), [0.6, 0.8])
            and np.allclose(R._track([1.0, 0.01]), [1, 0]))
