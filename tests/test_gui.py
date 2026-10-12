"""GUI helpers without a display (the Qt window itself needs PySide6 and is checked when installed)."""
import json, math, os, pytest
from kicad2ems.gui.runner import Run, EventTail, stop_runs, write_toml, settings_values, FIELDS
from kicad2ems.config import build_settings


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
    from kicad2ems.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.setup.set_values(dict(board='x.kicad_pcb', nets=['RF'], fmin=1.0, fast='skrf', cut_ports=True, ports=['P3=J1.1']))
    v = w.setup.values()
    assert v['board'] == 'x.kicad_pcb' and v['nets'] == ['RF'] and v['cut_ports'] is True
    assert v['solvers'] == ['auto', 'scikit'] and 'fast' not in v              # the default (auto) + fast = skrf
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
    from kicad2ems.gui.runner import extra_values
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
    from kicad2ems.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.setup.set_values(dict(dump=['E@F.Cu@2,6', 'H@volume'], mode='antenna'))
    v = w.setup.values()
    assert v['dump'] == ['E@F.Cu@2,6', 'H@volume'] and v['mode'] == 'antenna'
    w.setup.extra = dict(air=3.0)
    assert w.setup.values()['air'] == 3.0
    assert list(w.docks) == ['S-parameters', 'Model and mesh', 'Line model', 'Run', 'Fields', '3D fields', 'Antenna',
                             'Convergence', 'System']
    assert all(not s.expanded for s in w.setup.sections.values())  # settings collapsed when the window opens
    assert 'set)' in w.setup.sections['Field dumps'].head.text()   # the dumps given above count
    assert {'Open', 'Save', 'Preview', 'Run', 'Stop'} <= set(w.acts)
    w.fields.load(str(tmp_path))                                    # an empty folder: no dumps, no error
    assert w.fields.src is None and not w.antenna.load(str(tmp_path))
    w.close()


def test_run_panel_plots_the_mom25d_sweep():
    """mom25d's adaptive-sweep steps (status events with change / pred) go to the Run panel's plot: its model
    change and prediction error in dB per solve, and the tolerance it stops at."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    w = MainWindow()
    rt = w.runtab
    for k, (c, p) in enumerate([(2.0, float('inf')), (0.1, 0.5), (1e-3, 2e-3)], 6):
        rt.status(dict(run='mom25d', fraction=k / 60, solves=k, f_ghz=2.4))     # progress: a bar, no curve
        rt.status(dict(run='mom25d', afs_solves=k, change=c, pred=p, tol=5e-3))
    assert set(rt.curves) == {'mom25d model change', 'mom25d prediction error'}
    xs, ys = rt.hist['mom25d model change']
    assert xs == [6, 7, 8] and abs(ys[-1] + 60) < 1e-9                         # 1e-3: -60 dB
    assert rt.hist['mom25d prediction error'][0] == [7, 8]                       # the first has none
    assert rt._tol is not None and abs(rt._tol.value() - 20 * math.log10(5e-3)) < 1e-9
    assert rt.plot.getPlotItem().getAxis('bottom').labelText == 'EM solve'
    w.close()
    app.processEvents()


def test_save_config_keeps_the_file(tmp_path):
    """Saving over a loaded config keeps its comments, tables and unknown keys; paths relative to the file."""
    from kicad2ems.gui.runner import save_config, extra_values
    (tmp_path / 'b.kicad_pcb').write_text('')
    src = tmp_path / 'c.toml'
    src.write_text('# my board\nboard = "b.kicad_pcb"   # the board\npos = "x.pos"\nfmin = 1.0  # GHz\n'
                   'nets = [\n  "RF",\n  "GND",\n]\nfuture_key = 3\n\n[openems]\nair = 5.0   # mm\nend-db = -40\n')
    vals = dict(settings_values(str(src)), **extra_values(str(src)))
    vals.update(fmin=2.0, nets=['RF'], **{'solver:mom25d': True})
    del vals['pos']                                                 # the field was cleared
    save_config(str(src), vals, template=str(src))
    text = src.read_text()
    assert '# my board' in text and 'board = "b.kicad_pcb"   # the board' in text
    assert 'fmin = 2.0  # GHz' in text and 'pos' not in text and 'future_key = 3' in text
    assert 'air = 5.0   # mm' in text and 'end-db = -40.0' in text
    assert text.index('solvers = ["auto", "mom25d"]') < text.index('[openems]')
    assert '\nout =' not in text and 'solver:' not in text          # the default folder is not pinned to the cwd
    s = build_settings({}, str(src))
    assert s.fmin == 2.0 and s.nets == ['RF'] and s.pos is None and s.air == 5.0
    assert s.solver_plan()[0] == ['auto', 'mom25d']
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
    from kicad2ems.gui.traces import load_traces, transform, value_at, export_csv, smith_grid, default_visible
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
    from kicad2ems.gui.app import MainWindow
    from kicad2ems.gui.traces import load_traces
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
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'examples', f'rf_{name}.toml')
    s = build_settings({}, path)
    m = Board(KiCadBoard(s.board), ground=s.ground, log=lambda *a: None)
    assert len(m.ports) == nports
    if name == 'wilkinson':
        assert len(m.parts) == 1 and abs(m.parts[0].R - 100) < 1e-9
    if name == 'coupler':
        assert len(m.copper['F.Cu']) == 2                           # the gap is open: two separate conductors


def test_gui_solver_boxes(tmp_path, monkeypatch):
    from kicad2ems.gui.runner import settings_values, solvers_from_form, save_config, extra_values, write_toml, FIELDS
    from kicad2ems.solvers.lines import fastsolve
    monkeypatch.setattr(fastsolve.shutil, 'which', lambda name: None)    # no qucsator: lines is scikit
    assert [k for k, *_ in FIELDS if k.startswith('solver:')] == ['solver:auto', 'solver:openems', 'solver:mom25d',
                                                                    'solver:emerge', 'solver:qucs', 'solver:scikit']
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nsolvers = ["mom", "lines"]\n')
    vals = settings_values(str(p))
    assert not vals['solver:openems'] and vals['solver:mom25d'] and vals['solver:scikit'] and not vals['solver:qucs']
    assert 'solvers' not in extra_values(str(p))                    # the tick boxes own it
    s = solvers_from_form(dict(vals, **{'solver:openems': True}))   # openEMS ticked again
    assert not any(k.startswith('solver:') for k in s) and s['solvers'] == ['openems', 'mom25d', 'scikit']
    save_config(str(p), s, template=str(p))
    assert build_settings({}, str(p)).solver_plan()[0] == ['openems', 'mom25d', 'scikit']
    s = solvers_from_form(dict(vals, **{'solver:auto': True, 'solver:mom25d': False, 'solver:scikit': False}))
    assert 'solvers' not in s                                       # auto alone: the default, not written
    save_config(str(p), s, template=str(p))
    assert 'solvers' not in p.read_text() and build_settings({}, str(p)).solver_plan()[0] == ['auto']
    s = solvers_from_form(dict(vals, **{'solver:mom25d': False}))   # only the line model
    assert s['solvers'] == ['scikit']
    write_toml(tmp_path / 'r.toml', s)
    assert build_settings({}, str(tmp_path / 'r.toml')).solver_plan()[0] == ['scikit']
    none = solvers_from_form({k: False for k, *_ in FIELDS if k.startswith('solver:')})
    assert none['solvers'] == []                                    # none: the GUI says so
    p.write_text('board = "b.kicad_pcb"\nsolvers = ["auto"]\n')     # auto has a box of its own (and is the default)
    vals = settings_values(str(p))
    assert vals['solver:auto'] and not vals['solver:openems'] and 'solvers' not in solvers_from_form(vals)   # the default


def test_save_config_in_solver_tables(tmp_path):
    """A [solver.<name>] table: its short names are its settings; another tool's table is left alone."""
    from kicad2ems.gui.runner import settings_values, save_config, extra_values
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\n[solver.mom25d]\nmesher = "triangle"  # MoM\ndmax = 0.4\n'
                 '[other]\nmom_mesher = "x"\n')
    vals = dict(settings_values(str(p)), **extra_values(str(p)))
    assert vals['mom_mesher'] == 'triangle' and vals['mom_dmax'] == 0.4
    save_config(str(p), dict(vals, mom_mesher='gmsh'), template=str(p))
    text = p.read_text()
    assert 'mesher = "gmsh"' in text and '# MoM' in text and 'dmax = 0.4' in text and 'mom_mesher = "x"' in text
    s = build_settings({}, str(p))
    assert s.mom_mesher == 'gmsh' and s.mom_dmax == 0.4


def test_system_panel(tmp_path):
    """The System panel: the machine as kicad2ems.sysinfo reports it, and a preview's estimates against it (a
    solver that cannot run here greyed, memory that does not fit marked)."""
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import json
    from PySide6.QtWidgets import QApplication
    from kicad2ems.gui.systempanel import SystemPanel, fit_note
    from kicad2ems.sysinfo import info
    app = QApplication.instance() or QApplication([])
    p = SystemPanel()
    p.stop()                                                        # the panel's own check: not needed here
    sysinfo = info()
    assert {s['name'] for s in sysinfo['solvers']} >= {'mom25d', 'scikit'} and sysinfo['ram']['total'] > 0
    sysinfo['solvers'] = [dict(name='openems', label='openEMS', ok=False, what='no CSXCAD', consumes='board'),
                          dict(name='mom25d', label='mom25d', ok=True, what='0.6', consumes='board')]
    sysinfo['ram'] = dict(total=32.0, available=20.0)
    sysinfo['gpu'] = dict(device='GPU', backend='cupy', api='ROCm/HIP', free=16.0, total=17.0)
    p.set_info(sysinfo)
    assert p.solvers.topLevelItemCount() == 2
    est = dict(solvers=dict(openems=dict(seconds=60, gb=0.1, what='0.5 M cells'),
                            mom25d=dict(seconds=600, gb=30.0, gpu_gb=8.1, how='tiled', what='45,000 unknowns')))
    (tmp_path / 'b_run.json').write_text(json.dumps(dict(estimates=est)))
    assert p.load_estimates(str(tmp_path)) and p.est_tree.topLevelItemCount() == 2
    oems, mom = (p.est_tree.topLevelItem(i) for i in range(2))
    assert 'not available' in oems.text(4) and '~10 min' == mom.text(1)
    assert 'does not fit' in mom.text(2) and 'fits' in mom.text(3)
    assert fit_note(10, 20) == 'fits' and fit_note(18, 20) == 'tight' and not p.load_estimates(str(tmp_path / 'none'))
