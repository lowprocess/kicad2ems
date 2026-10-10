"""GUI helpers without a display (the Qt window itself needs PySide6 and is checked when installed)."""
import json, os, pytest
from kicad2openems.gui.runner import Run, EventTail, stop_runs, write_toml, settings_values, FIELDS
from kicad2openems.config import build_settings


def test_settings_round_trip(tmp_path):
    vals = dict(board=str(tmp_path / 'b.kicad_pcb'), nets=['RF', 'GND'], fmin=1.0, fmax=6.0, points=201,
                ports=['P3=J1.1', 'P4@1,2,F.Cu'], cut_ports=True, region=[0.0, 0.0, 10.0, 5.0], fast='qucsator',
                end_on='ports', fine=None)
    p = tmp_path / 'c.toml'
    write_toml(p, vals)
    s = build_settings({}, str(p))
    assert s.nets == ['RF', 'GND'] and s.points == 201 and s.ports == ['P3=J1.1', 'P4@1,2,F.Cu'] and s.cut_ports
    assert s.region == (0, 0, 10, 5) and s.fast == 'qucsator' and s.fine is None
    back = settings_values(str(p))
    assert set(back) == {k for k, *_ in FIELDS} and back['fmax'] == 6.0


def test_run_events_and_stop(tmp_path):
    r = Run(dict(board='b.kicad_pcb', fmin=1.0), tmp_path / 'out', preview=True)
    assert r.argv()[-1] == '--preview' and r.argv()[3] == r.toml and os.path.exists(r.toml)
    tail = EventTail(r.events)
    assert tail.read() == []
    with open(r.events, 'w') as fh:
        fh.write(json.dumps(dict(kind='stage', name='model')) + "\n" + '{"kind": "log", "te')   # half a line
    assert [e['kind'] for e in tail.read()] == ['stage']
    with open(r.events, 'a') as fh:
        fh.write('xt": "hello"}\n')
    assert tail.read() == [dict(kind='log', text='hello')]
    d = tmp_path / 'out' / 'openems' / 'exc_P1'
    d.mkdir(parents=True)
    assert stop_runs(str(tmp_path / 'out')) == [str(d)] and (d / 'ABORT').exists()


def test_window_builds(tmp_path):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from kicad2openems.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.setup.set_values(dict(board='x.kicad_pcb', nets=['RF'], fmin=1.0, fast='skrf', cut_ports=True, ports=['P3=J1.1']))
    v = w.setup.values()
    assert v['board'] == 'x.kicad_pcb' and v['nets'] == ['RF'] and v['fast'] == 'skrf' and v['cut_ports'] is True
    assert v['ports'] == ['P3=J1.1']
    w.runtab.status(dict(run='P1', step=1000, db=-12.0, fraction=0.3, eta=65))
    assert w.runtab.bars['P1'].value() == 30
    w.close()


def test_dump_lists_and_extra_settings(tmp_path):
    cfg = tmp_path / 'c.toml'
    cfg.write_text('board = "b.kicad_pcb"\nmode = "antenna"\nair = 7.5\nno_mask = true\n'
                   'dump = ["E@F.Cu@2,6", "J@y=3"]\n[openems]\nbc = "pml_8"\n')
    vals = settings_values(str(cfg))
    assert vals['mode'] == 'antenna' and vals['dump'] == ['E@F.Cu@2,6', 'J@y=3']
    from kicad2openems.gui.runner import extra_values
    extra = extra_values(str(cfg))
    assert extra == dict(air=7.5, mask=False, bc='pml_8')           # kept for the runs: no field in the form
    write_toml(tmp_path / 'r.toml', dict(vals, **extra))
    s = build_settings({}, str(tmp_path / 'r.toml'))
    assert s.air == 7.5 and not s.mask and s.antenna and s.dump == ['E@F.Cu@2,6', 'J@y=3']


def test_window_tabs(tmp_path):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from kicad2openems.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.setup.set_values(dict(dump=['E@F.Cu@2,6', 'H@volume'], mode='antenna'))
    v = w.setup.values()
    assert v['dump'] == ['E@F.Cu@2,6', 'H@volume'] and v['mode'] == 'antenna'
    w.setup.extra = dict(air=3.0)
    assert w.setup.values()['air'] == 3.0
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ['Setup', 'Run', 'Results', 'Fields', 'Antenna']
    w.fields.load(str(tmp_path))                                    # an empty folder: no dumps, no error
    assert w.fields.src is None and not w.antenna.load(str(tmp_path))
    w.close()


def test_save_config_keeps_the_file(tmp_path):
    """Saving over a loaded config keeps its comments, tables and unknown keys; paths relative to the file."""
    from kicad2openems.gui.runner import save_config, extra_values
    (tmp_path / 'b.kicad_pcb').write_text('')
    src = tmp_path / 'c.toml'
    src.write_text('# my board\nboard = "b.kicad_pcb"   # the board\npos = "x.pos"\nfmin = 1.0  # GHz\n'
                   'nets = [\n  "RF",\n  "GND",\n]\nfuture_key = 3\n\n[openems]\nair = 5.0   # mm\nend-db = -40\n')
    vals = dict(settings_values(str(src)), **extra_values(str(src)))
    vals.update(fmin=2.0, nets=['RF'], mom=True)
    del vals['pos']                                                 # the field was cleared
    save_config(str(src), vals, template=str(src))
    text = src.read_text()
    assert '# my board' in text and 'board = "b.kicad_pcb"   # the board' in text
    assert 'fmin = 2.0  # GHz' in text and 'pos' not in text and 'future_key = 3' in text
    assert 'air = 5.0   # mm' in text and 'end-db = -40.0' in text and text.index('mom = true') < text.index('[openems]')
    assert '\nout =' not in text and 'use_openems' not in text      # the default folder is not pinned to the cwd
    s = build_settings({}, str(src))
    assert s.fmin == 2.0 and s.nets == ['RF'] and s.mom and s.pos is None and s.air == 5.0
    assert s.board == str(tmp_path / 'b.kicad_pcb')
    new = tmp_path / 'sub' / 'n.toml'                                # a new file: only what differs from the defaults
    new.parent.mkdir()
    save_config(str(new), vals)
    t = new.read_text()
    assert 'board = "../b.kicad_pcb"' in t and 'points' not in t and 'fmin = 2.0' in t
    assert build_settings({}, str(new)).board == str(tmp_path / 'b.kicad_pcb')


def _s2p(path, f, S, names=('IN', 'OUT')):
    with open(path, 'w') as fh:
        fh.write(f"! ports: 1={names[0]}, 2={names[1]}\n# Hz S RI R 50\n")
        for fi, s in zip(f, S):
            fh.write(f"{fi:.6e} " + " ".join(f"{v.real:.9e} {v.imag:.9e}" for v in (s[0, 0], s[1, 0], s[0, 1], s[1, 1])) + "\n")


def test_traces(tmp_path):
    import numpy as np
    from kicad2openems.gui.traces import load_traces, transform, value_at, export_csv, smith_grid, default_visible
    f = np.linspace(1e9, 3e9, 21)
    tau = 0.5e-9                                                    # a matched 0.5 ns line
    S = np.zeros((21, 2, 2), complex)
    S[:, 1, 0] = S[:, 0, 1] = np.exp(-2j * np.pi * f * tau)
    S[:, 0, 0] = S[:, 1, 1] = 0.1
    _s2p(tmp_path / 'b_openems.s2p', f, S)
    _s2p(tmp_path / 'b_mom.s2p', f, S)
    trs = load_traces([('t', str(tmp_path / 'b_openems.s2p')), ('m', str(tmp_path / 'b_mom.s2p'))])
    assert [t.source for t in trs[:4]] == ['openEMS'] * 4 and trs[4].source == 'mom25d'
    s21 = next(t for t in trs if t.param == 'S21')
    assert s21.title == 'S21 (OUT ← IN)' and default_visible(s21) and not default_visible(trs[2])
    assert np.allclose(transform('Group delay (ns)', s21)[1], 0.5)
    s11 = next(t for t in trs if t.param == 'S11')
    assert value_at('|S| (dB)', s11, 2e9) == __import__('pytest').approx(-20.0)
    assert value_at('VSWR', s11, 2e9) == __import__('pytest').approx(1.1 / 0.9)
    assert np.isnan(value_at('|S| (dB)', s11, 5e9)) and value_at('Smith chart', s11, 2e9) == __import__('pytest').approx(0.1)
    export_csv(tmp_path / 'x.csv', [s11, s21], '|S| (dB)', reflections=[s11])
    head = (tmp_path / 'x.csv').read_text().splitlines()[0]
    assert head == 'f_GHz,S11 openEMS dB,S21 openEMS dB,S11 openEMS re,S11 openEMS im'
    assert len(smith_grid()) > 10


def test_window_config_and_plots(tmp_path, monkeypatch):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    import numpy as np
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))   # recent files in a scratch QSettings
    from PySide6.QtWidgets import QApplication
    from kicad2openems.gui.app import MainWindow
    from kicad2openems.gui.traces import load_traces
    app = QApplication.instance() or QApplication([])
    cfg = tmp_path / 'c.toml'
    cfg.write_text('# hello\nboard = "b.kicad_pcb"\nfmin = 1.0\n')
    w = MainWindow(str(cfg))
    assert w.setup.path == str(cfg) and not w.setup.dirty and w.windowTitle().startswith('c.toml -')
    w.setup.edits['fmax'][0].setText('7')
    assert w.setup.dirty and w.windowTitle().startswith('c.toml*')
    assert w.setup.save() and not w.setup.dirty and 'fmax = 7.0' in cfg.read_text() and '# hello' in cfg.read_text()
    assert str(cfg) in w.recent()
    f = np.linspace(1e9, 3e9, 11)
    S = np.full((11, 2, 2), 0.2 + 0j)
    _s2p(tmp_path / 'b_openems.s2p', f, S)
    sp = w.results.splot
    sp.set_traces(load_traces([('t', str(tmp_path / 'b_openems.s2p'))]))
    assert [t.param for t in sp.visible()] == ['S11', 'S21'] and [t.param for t in sp.reflections()] == ['S11']
    sp.add_marker(2e9)
    assert sp.table.rowCount() == 1 and sp.table.item(0, 1).text() == '-13.98 dB'
    sp.kind.setCurrentText('VSWR')
    assert sp.table.item(0, 1).text() == '1.500'
    sp.export_csv(str(tmp_path / 'e.csv'))
    assert (tmp_path / 'e.csv').exists()
    w.setup.dirty = False
    w.close()


@pytest.mark.parametrize('name, nports', [('branchline', 4), ('wilkinson', 3), ('steplpf', 2), ('stubnotch', 2),
                                          ('gcpw', 2), ('coupler', 4)])
def test_rf_examples_load(name, nports):
    """The RF example boards load: ports, the Wilkinson resistor, the coupler's two separate lines."""
    from kicad2openems.kicad import KiCadBoard
    from kicad2openems.model import Model
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'examples', f'rf_{name}.toml')
    s = build_settings({}, path)
    m = Model(KiCadBoard(s.board), ground=s.ground, log=lambda *a: None)
    assert len(m.ports) == nports
    if name == 'wilkinson':
        assert len(m.parts) == 1 and abs(m.parts[0].R - 100) < 1e-9
    if name == 'coupler':
        assert len(m.copper['F.Cu']) == 2                           # the gap is open: two separate conductors


def test_gui_solver_boxes(tmp_path):
    from kicad2openems.gui.runner import settings_values, solvers_from_form, save_config, extra_values, write_toml
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nsolvers = ["mom", "lines"]\n')
    vals = settings_values(str(p))
    assert vals['use_openems'] is False and vals['mom'] is True and vals['fast'] == 'auto'
    assert 'solvers' not in extra_values(str(p))                    # the tick boxes own it
    form = dict(vals, use_openems=True)                                 # openEMS ticked again
    s = solvers_from_form(form)
    assert 'use_openems' not in s and 'solvers' not in s
    save_config(str(p), s, template=str(p))
    assert 'solvers' not in p.read_text()
    back = build_settings({}, str(p))
    assert back.solver_plan()[0] == ['openems', 'mom', 'lines']
    s = solvers_from_form(dict(vals, use_openems=False, mom=False))     # only the line model
    assert s['solvers'] == ['lines']
    write_toml(tmp_path / 'r.toml', s)
    assert build_settings({}, str(tmp_path / 'r.toml')).solver_plan()[0] == ['lines']
    assert solvers_from_form(dict(use_openems=False, mom=False, fast=''))['solvers'] == []   # none: the GUI says so

