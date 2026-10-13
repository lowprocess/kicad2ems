"""GUI helpers without a display (the Qt window itself needs PySide6 and is checked when installed)."""
import json, math, os, pytest
import numpy as np
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
    """Three pages (Viewer, Run, Results), the settings collapsed, the Solvers window behind ? and Help."""
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
    assert list(w.docks) == ['Viewer', 'Run', 'Results']
    assert w.viewer.view() in ('3D', 'Plan')                         # 3D when OpenGL can be had
    w.viewer.set_view('Plan')
    assert w.viewer.view() == 'Plan' and w.viewer.buttons['Plan'].isChecked()
    assert all(not s.expanded for s in w.setup.sections.values())  # settings collapsed when the window opens
    assert 'set)' in w.setup.sections['openEMS/Field dumps'].head.text()   # the dumps given above count
    assert 'set)' in w.setup.parents['openEMS'][0].head.text()            # and in openEMS's own section
    assert {'Open', 'Save', 'Preview', 'Run', 'Stop'} <= set(w.acts)
    assert w.setup.help_btn.text() == '?' and not w.solvers_dialog.isVisible()
    w.setup.help_btn.click()                                        # ? beside Solvers: the Solvers window
    assert w.solvers_dialog.isVisible() and w.system.isVisibleTo(w.solvers_dialog)
    w.fields.load(str(tmp_path))                                    # an empty folder: no dumps, no error
    assert w.fields.src is None and not w.results.load_antenna(str(tmp_path))
    assert not w.viewer.load(str(tmp_path)) and not w.viewer.buttons['Cut'].isEnabled()
    w.setup.dirty = False
    w.close()


def test_solver_boxes_estimates_and_availability():
    """A preview's estimates beside the solver tick boxes; a solver that cannot run here says why there."""
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    w = MainWindow()
    w.system.stop()
    w.setup.set_estimates(dict(solvers=dict(mom25d=dict(seconds=35, gb=1.3, what='4,657 unknowns'),
                                            openems=dict(seconds=900, gb=0.4))))
    assert w.setup.solver_boxes['mom25d'].text() == 'mom25d (MoM)  ·  ~35 s, 1.3 GB'
    assert w.setup.solver_boxes['openems'].text().endswith('~15 min, 0.4 GB')
    assert 'emerge' not in w.setup.solver_boxes                    # experimental: --experimental only
    w.setup.dirty = False
    w.setup.new()                                                   # another board: the estimates go
    assert w.setup.solver_boxes['mom25d'].text() == 'mom25d (MoM)'
    w.setup.set_availability(dict(solvers=[dict(name='openems', ok=False, what='no CSXCAD'),
                                           dict(name='mom25d', ok=True, what='0.6')]))
    assert 'no CSXCAD' in w.setup.solver_boxes['openems'].toolTip() and w.setup.solver_boxes['mom25d'].toolTip() == ''
    w.setup.dirty = False
    w.close()
    app.processEvents()


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
    assert not rt.sweep.isHidden() and rt.plot.isHidden()                       # mom25d alone: its plot alone
    rt.status(dict(run='P1', step=1000, db=-10.0, fraction=0.1))                # openEMS too: both, one above the other
    assert not rt.sweep.isHidden() and not rt.plot.isHidden() and 'P1' in rt.curves
    rt.reset()
    assert rt.sweep.isHidden() and not rt.plot.isHidden() and not rt.curves
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
                                                                    'solver:qucs', 'solver:scikit']
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


@pytest.fixture(scope='module')
def wilkinson_preview(tmp_path_factory):
    """A preview of the Wilkinson divider: its board, line model (<name>_lines.json), meshes and plots."""
    from kicad2ems.cli import main
    out = tmp_path_factory.mktemp('wilk')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert main([os.path.join(root, 'examples', 'rf_wilkinson.toml'), '--preview', '--out', str(out),
                 '--solvers', 'mom,lines', '--no-report']) == 0
    return str(out)


def test_line_model_drawing(wilkinson_preview):
    """<name>_lines.json: every section with its points, layer height and Z0; paths as ids; the overlay's
    colours, hit test and description."""
    from kicad2ems import overlay as ov
    d = ov.load_lines(wilkinson_preview)
    assert d and d['format'] == 'kicad2ems-lines' and d['path'].endswith('_lines.json')
    els = d['elements']
    assert [e['id'] for e in els] == list(range(len(els)))
    assert all(len(e['pts']) >= 1 and e['z'] == pytest.approx(0.8) for e in els if e['layer'] == 'F.Cu')
    assert [p['port'] for p in d['paths']] == ['P1', 'P1', 'P2'] and all(p['ids'] for p in d['paths'])
    z0 = sorted({round(e['z0']) for e in els if e.get('z0') and e['kind'] == 'trace'})
    assert z0 == [49, 69]                                           # the 50 ohm feeds and the 70.7 ohm arms
    assert ov.reference(d) == 50.0 and ov.span(d) == pytest.approx(19.04, abs=0.05)
    arm = next(e for e in els if e['kind'] == 'trace' and e['z0'] > 60 and len(e['pts']) == 2)
    mid = np.mean(arm['pts'], 0)
    assert ov.nearest(els, mid[0], mid[1], 0.1) == arm['id']
    assert ov.nearest(els, 1e3, 1e3, 0.1) is None and ov.nearest(els, mid[0], mid[1], 0.1, layer='B.Cu') is None
    assert ov.z0_colour(50, 50, 10) == ov.MID and ov.z0_colour(None) == ov.MID
    assert ov.z0_colour(80, 50, 10) == pytest.approx(ov.HIGH) and ov.z0_colour(20, 50, 10) == pytest.approx(ov.LOW)
    assert 'Z0 69.0 Ω' in ov.describe(arm) and 'mm' in ov.describe(arm)
    seg = ov.plan_segments(next(iter(__import__('kicad2ems.meshes', fromlist=['find']).find(wilkinson_preview).values())),
                           (0, -20, 25, 0, 0, 0.8), 0.8)
    assert seg.shape[1] == 2 and len(seg) % 2 == 0 and len(seg) > 0


def test_viewer_sections_and_views(wilkinson_preview):
    """The viewer on a preview: the plan and the section list, one selection in every view; the views a folder
    has nothing for disabled."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.viewer import Viewer
    app = application()
    v = Viewer()
    assert v.load(wilkinson_preview)
    assert v.buttons['Plan'].isEnabled() and v.buttons['Images'].isEnabled() and not v.buttons['Cut'].isEnabled()
    assert v.plan.current_layer() == 'F.Cu' and v.plan.layer.count() == 2      # top first
    n = len([e for e in v.lines['elements'] if e['kind'] != 'coupling'])
    rows = sum(v.tree.topLevelItem(k).childCount() for k in range(v.tree.topLevelItemCount()))
    assert rows >= n and v.tree.topLevelItem(0).text(0) == 'P1 → P2'
    v.set_view('Plan')
    assert not v.side.isHidden()
    sec = v.tree.topLevelItem(0).child(1)
    sec.setSelected(True)                                           # the list: the plan and 3D follow
    i = sec.data(0, Qt_UserRole())
    assert v.plan.sel == i and v.volume.sel == i and 'Z0' in v.detail.text()
    v.plan.selected.emit(0)                                         # a click in the plan: the list follows
    assert v.tree.selectedItems()[0].data(0, Qt_UserRole()) == 0
    v.load(wilkinson_preview)                                       # the folder again: nothing selected
    assert v.detail.text().strip() == '' and v.plan.sel is None
    v.select(0)
    v.lines_on.setChecked(False)                                    # the overlay off: no list
    assert v.side.isHidden() and not v.plan.show_lines
    v.set_view('Images')
    assert v.side.isHidden()
    v.plan.mesh.setCurrentIndex(1)                                  # a solver's mesh on the layer
    assert v.plan.mesh.currentData() in ('openems', 'mom25d')


def Qt_UserRole():
    from PySide6.QtCore import Qt
    return Qt.UserRole


def test_results_subplots_and_layout(tmp_path, monkeypatch):
    """Subplots added and removed (Cartesian, Smith, polar), markers on all of them, antenna quantities when a
    folder has antenna results, the layout and the window's shape remembered by the next window."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    from PySide6.QtCore import QSettings
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    from kicad2ems.gui.splot import default_layout
    from kicad2ems.gui.traces import load_traces
    app = application()
    QSettings('kicad2ems', 'gui').clear()
    w = MainWindow()
    sp = w.results.splot
    assert sp.layout_spec() == default_layout()
    f = np.linspace(1e9, 3e9, 11)
    S = np.zeros((11, 2, 2), complex)
    S[:, 0, 0], S[:, 1, 0] = 0.2, 0.9
    _s2p(tmp_path / 'b_openems.s2p', f, S)
    sp.set_traces(load_traces([('t', str(tmp_path / 'b_openems.s2p'))]))
    polar = sp.add_subplot('Polar')
    assert sp.layout_spec()[-1] == 'Polar:S (polar)' and polar.option.count() == 1   # no far field: S only
    sp.add_subplot('Cartesian', 'Phase (deg)')
    assert sp.s_kinds() == ['|S| (dB)', 'Phase (deg)']
    m = sp.add_marker(2e9)
    assert set(m['items']) == {id(p) for p in sp.subplots}          # on every plot
    assert sp.table.columnCount() == 1 + 2 * 2 + 1                  # f, |S| and phase of S11, S21, S11 on Smith
    m['items'][id(sp.subplots[0])][0].setValue(2.4)                  # dragged on one plot: moved on the others
    assert m['f'] == pytest.approx(2.4e9) and m['items'][id(sp.subplots[3])][0].value() == pytest.approx(2.4)
    sp.remove_subplot(polar)
    assert 'Polar:S (polar)' not in sp.layout_spec() and len(sp.subplots) == 3
    res = dict(rows=[dict(f=f0, D_dbi=5.0, G_dbi=3.0, Grealized_dbi=2.5, eff=0.6, Prad=0.6, Pinc=1.0)
                     for f0 in (1e9, 2e9, 3e9)], f_pattern=2e9)
    th, ph = np.arange(0, 181, 5.0), np.arange(0, 360, 5.0)
    sph = dict(freq=np.array([2e9]), theta=th, phi=ph, e_norm=np.ones((1, len(ph), len(th))), dmax=np.array([2.0]))
    sp.set_antenna(res, sph)
    assert sp.subplots[0].option.findText('Gain (dBi)') >= 0
    cuts = sp.add_subplot('Polar', 'Far-field cuts')
    assert cuts.mode == 'Far-field cuts' and cuts.freq.count() == 1 and cuts.freq.isVisibleTo(cuts)
    sp.subplots[0].option.setCurrentText('Gain (dBi)')
    assert 'gain' in [sp.table.horizontalHeaderItem(c).text() for c in range(sp.table.columnCount())]
    spec = sp.layout_spec()
    w.results._mode = 'sparams'
    w.results._save_layout()
    w.resize(760, 590)                                              # fits the offscreen screen (800 x 800)
    w.setup.dirty = False
    w.close()
    w2 = MainWindow()                                               # the next window: the same plots and size
    assert w2.results.splot.layout_spec() == spec and (w2.width(), w2.height()) == (760, 590)
    first = w2.results.splot.subplots[0]
    assert first.mode == '|S| (dB)'                                 # no antenna results here: |S| for now
    w2.results.splot.set_antenna(res, sph)
    assert first.mode == 'Gain (dBi)'                               # and the gain once there are
    w2.setup.dirty = False
    w2.close()
    app.processEvents()


def test_meters(tmp_path):
    """The status bar's readings: system RAM from /proc/meminfo, a process tree's memory, the GPU tools' output."""
    from kicad2ems.gui import meters
    mi = tmp_path / 'meminfo'
    mi.write_text('MemTotal:       16000000 kB\nMemFree:  1 kB\nMemAvailable:    4000000 kB\n')
    used, total = meters.ram(str(mi))
    assert total == pytest.approx(16.384) and used == pytest.approx(12.288)
    assert meters.ram(str(tmp_path / 'none')) is None or len(meters.ram(str(tmp_path / 'none'))) == 2   # sysconf
    if os.path.isdir('/proc/self'):
        assert meters.tree_rss(os.getpid()) > 0.01                  # this test process
    assert meters.tree_rss(None) is None and meters.tree_rss(2 ** 30) is None
    assert meters.parse_gpu('nvidia-smi', '2048, 16384, RTX\n') == [pytest.approx((2.147483648, 17.179869184))]
    rocm = 'WARNING: x\n{"card0": {"VRAM Total Memory (B)": "17163091968", "VRAM Total Used Memory (B)": "1000000000"}}'
    assert meters.parse_gpu('/opt/rocm/bin/rocm-smi', rocm) == [pytest.approx((1.0, 17.163091968))]
    assert meters.parse_gpu('nvidia-smi', 'garbage') == [] and meters.duration(263) == '~4 min'


def test_estimate_follows_the_solvers():
    """The toolbar's estimate: auto takes the fastest solver that can run and fits; named solvers add up; auto adds
    no full-wave solver beside a named one (as the pipeline); stale settings and a misfit are marked."""
    from kicad2ems.gui.meters import estimate
    est = dict(solvers=dict(openems=dict(seconds=818, gb=0.05), mom25d=dict(seconds=264, gb=1.3, gpu_gb=0.8)))
    e = estimate(est, {})                                           # the default: auto
    assert [r[0] for r in e['rows']] == ['mom25d'] and e['rows'][0][4] and e['gpu_gb'] == 0.8
    assert estimate(est, {}, usable={'openems'})['rows'][0][0] == 'openems'          # mom25d can't run here
    assert estimate(est, {}, ram_avail=1.0)['rows'][0][0] == 'openems'               # mom25d doesn't fit
    both = estimate(est, dict(solvers=['openems', 'mom25d', 'scikit']))
    assert both['seconds'] == 818 + 264 and both['gb'] == 1.3 and not any(r[4] for r in both['rows'])
    named = estimate(est, dict(solvers=['auto', 'emerge']))         # a named full-wave solver: auto adds none
    assert named is None
    assert estimate(est, dict(solvers=['scikit'])) is None and estimate(None, {}) is None
    assert not estimate(est, dict(solvers=['mom25d']), ram_avail=1.0)['fits']
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    w = MainWindow()
    w.system.stop()
    assert w.est_btn.text().startswith('No estimate')
    w.setup.set_values(dict(board='b.kicad_pcb'))
    w.new_estimates(est)
    assert w.est_btn.text().startswith('~4 min · 1.3 GB · GPU 0.8 GB') and '(auto)' in w.est_btn.text()
    assert 'changed' not in w.est_btn.text()
    w.setup.solver_boxes['openems'].setChecked(True)                # a named solver: auto's choice gives way
    assert w.est_btn.text().startswith('~14 min') and 'changed' not in w.est_btn.text()
    w.setup.edits['out'][0].setText('elsewhere')                   # where results go: the same estimate
    assert 'changed' not in w.est_btn.text()
    w.setup.edits['fmax'][0].setText('9')                           # another setting: the estimate is stale
    assert w.est_btn.text().endswith('· changed')
    w.setup.dirty = False                                           # (else New asks to save first)
    w.setup.new()                                                   # another board: no estimate
    assert w.est_btn.text().startswith('No estimate')
    assert w.meters.ram.format().startswith('RAM ') and not w.meters.run.isVisible()
    w.setup.dirty = False
    w.close()
    app.processEvents()


def test_line_model_relief(wilkinson_preview):
    """The 3D line model: sections as slabs at their width, taller for a higher Z0."""
    from kicad2ems import overlay as ov
    d = ov.load_lines(wilkinson_preview)
    arm = next(e for e in d['elements'] if e['kind'] == 'trace' and e['z0'] > 60 and len(e['pts']) == 2)
    g = ov.section_polygon(arm)
    assert g.area == pytest.approx(arm['w'] * arm['length'], rel=1e-6)          # its width, its length
    assert ov.relief(69, 49, 69) == 1.0 and ov.relief(49, 49, 69) == 0.25 and ov.relief(None, 49, 69) == 0.25
    assert ov.relief(59, 49, 69) == pytest.approx(0.625) and ov.relief(50, 50, 50) == pytest.approx(0.625)
    pytest.importorskip('PySide6')
    pytest.importorskip('OpenGL')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.volumetab import VolumeTab
    app = application()
    v = VolumeTab()
    if v.gl is None:
        pytest.skip('no OpenGL')
    v.set_lines(d)
    assert v.load(wilkinson_preview)
    slabs = [k for k in v.items if k.startswith('ln') and k != 'ln_rims']
    assert len(slabs) >= 20 and 'ln_rims' in v.items and v.relief.value() == pytest.approx(0.38)
    tall = v.items[f"ln{arm['id']}"].opts['meshdata'].vertexes()[:, 2].max()
    feed = next(e for e in d['elements'] if e['kind'] == 'trace' and e['z0'] < 55 and len(e['pts']) == 2)
    low = v.items[f"ln{feed['id']}"].opts['meshdata'].vertexes()[:, 2].max()
    assert tall - low == pytest.approx(0.75 * 0.38, abs=1e-3)       # the step in Z0 is a step in height
    v.lines_on.setChecked(False)
    assert not any(k.startswith('ln') for k in v.items)


def test_parts_in_the_viewer(wilkinson_preview):
    """The Wilkinson's resistor as an 0805 chip: green in 3D with its value, outlined in the plan."""
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.viewer import Viewer
    from kicad2ems.gui.volumetab import _value
    app = application()
    v = Viewer()
    assert v.load(wilkinson_preview)
    r1 = v.plan.board['parts'][0]
    assert r1['ref'] == 'R1' and r1['package'] == '0805' and _value(r1) == '100 Ω'
    assert _value(dict(R=0.4, L=2.7e-8, C=None), main=True) == '27 nH' and _value(dict(R=0.4, L=2.7e-8, C=None)) == \
        '0.4 Ω + 27 nH'
    v.set_view('Plan')
    assert any(isinstance(it, __import__('pyqtgraph').TextItem) and it.toPlainText() == 'R1' for it in v.plan._items)
    vol = v.volume
    if vol.gl is None:
        return
    assert not vol.parts_on.isHidden() and {'part0_0', 'part0_1', 'part0_2', 'part0_label'} <= set(vol.items)
    assert vol.items['part0_label'].text == 'R1 100 Ω'
    strip = next(e['id'] for e in v.lines['elements'] if e['kind'] == 'part')
    assert f"ln{strip}" not in vol.items                            # no line-model strip inside the chip
    vol.parts_on.setChecked(False)
    assert not any(k.startswith('part') for k in vol.items) and f"ln{strip}" in vol.items


def test_run_folders(tmp_path):
    """Each GUI run in a folder of its own (<board>_<date>_<time>), a preview in <board>_preview; a board's runs
    are only its own, the newest first, and only those that wrote a summary."""
    import datetime
    from kicad2ems.gui.runfolders import run_dir, preview_dir, runs, board_name
    base = str(tmp_path)
    assert board_name('/x/rf_coupler.kicad_pcb') == 'rf_coupler' and board_name('a/b_board.json') == 'b'
    t = datetime.datetime(2026, 10, 13, 14, 15, 2)
    d1 = run_dir(base, 'x/rf_coupler.kicad_pcb', t)
    d2 = run_dir(base, 'x/rf_coupler.kicad_pcb', t + datetime.timedelta(minutes=5))
    other = run_dir(base, 'x/rf_coupler2.kicad_pcb', t)              # another board, a name that starts the same
    assert os.path.basename(d1) == 'rf_coupler_20261013_141502'
    assert preview_dir(base, 'x/rf_coupler.kicad_pcb') == os.path.join(base, 'rf_coupler_preview')
    for d, solvers in ((d1, ['openems']), (d2, ['mom25d', 'qucs']), (other, ['openems'])):
        os.makedirs(d)
        name = os.path.basename(d).rsplit('_', 2)[0]
        json.dump(dict(solvers=solvers), open(os.path.join(d, f"{name}_run.json"), 'w'))
    os.makedirs(run_dir(base, 'x/rf_coupler.kicad_pcb', t + datetime.timedelta(hours=1)))   # stopped: no summary
    rs = runs(base, 'x/rf_coupler.kicad_pcb')
    assert [d for d, _ in rs] == [d2, d1]
    assert rs[0][1] == '2026-10-13 14:20:02 (mom25d, qucs)'


def test_experimental_solvers_hidden_without_the_flag(tmp_path, monkeypatch):
    """EMerge (experimental) is out of the GUI unless it was started with --experimental (KICAD2EMS_EXPERIMENTAL):
    no tick box, no settings, no row in the Solvers window, no trace, no mesh; with it, all back."""
    from kicad2ems.gui.runner import make_fields, solver_ticks
    from kicad2ems.gui.traces import load_traces
    from kicad2ems import sysinfo, meshes
    from kicad2ems.solvers import shown, registry
    import numpy as np
    from kicad2ems.touchstone import write_touchstone
    f = np.linspace(1e9, 2e9, 3)
    S = np.zeros((3, 1, 1), complex)
    files = []
    for solver in ('mom25d', 'emerge'):
        p = str(tmp_path / f"b_{solver}.s1p")
        write_touchstone(p, f, S, [50.0])
        files.append((f"S-parameters ({solver})", p))
        np.savez(tmp_path / f"b_mesh_{solver}.npz", kind='tris', nodes=np.zeros((3, 3)), tris=np.array([[0, 1, 2]]))
    for on, expect in ((False, False), (True, True)):
        if on:
            monkeypatch.setenv('KICAD2EMS_EXPERIMENTAL', '1')
        else:
            monkeypatch.delenv('KICAD2EMS_EXPERIMENTAL', raising=False)
        keys = [k for k, *_ in make_fields()]
        assert ('solver:emerge' in keys) == expect
        assert ('solver:emerge' in solver_ticks({})) == expect
        assert ('emerge' in [s['name'] for s in sysinfo.solvers()]) == expect
        assert ('emerge' in {t.source for t in load_traces(files)}) == expect
        assert ('emerge' in meshes.find(str(tmp_path))) == expect
        assert ('emerge' in shown()) == expect and 'emerge' in registry()        # the command line keeps it


def test_gui_flag_sets_experimental(monkeypatch):
    """kicad2ems-gui --experimental: the flag is taken out of the arguments and shown to the runs it starts."""
    import kicad2ems.gui as g
    monkeypatch.delenv('KICAD2EMS_EXPERIMENTAL', raising=False)
    seen = {}
    monkeypatch.setattr('kicad2ems.gui.app.main', lambda argv: seen.update(argv=argv) or 0)
    assert g.main(['b.toml', '--experimental']) == 0
    assert seen['argv'] == ['b.toml'] and os.environ['KICAD2EMS_EXPERIMENTAL'] == '1'


def test_hidden_named_solver_kept_on_saving(tmp_path, monkeypatch):
    """A config naming EMerge, opened without --experimental: no tick box for it, but the form's settings keep it
    (saving the config doesn't drop it)."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.delenv('KICAD2EMS_EXPERIMENTAL', raising=False)
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    w = MainWindow()
    w.setup.set_values(dict(board='b.kicad_pcb', solvers=['mom', 'emerge']))
    assert 'emerge' not in w.setup.solver_boxes and w.setup.solver_boxes['mom25d'].isChecked()
    assert w.setup.values()['solvers'] == ['mom25d', 'emerge']
    w.setup.set_values({})                                          # another config: nothing kept from this one
    assert w.setup.values().get('solvers') in (None, ['auto'])
    w.hide()
    app.processEvents()


def test_solver_sections(tmp_path):
    """Each solver's settings in a section of its own (openEMS's in Mesh, Run and Field dumps sub-sections), the
    shared ones apart (Antenna; the dump settings both use under Output), qucsator not a board's setting (the
    Solvers and hardware window); a section greyed while none of its solvers is ticked."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    w = MainWindow()
    st = w.setup
    if not any(b.isChecked() for b in st.solver_boxes.values()) and not st.edits['solver:auto'][0].isChecked():
        assert not st.parents['openEMS'][0].isEnabled() and not st.sections['mom25d'].isEnabled()   # first launch
        assert not st.edits['ff_freq'][0].isEnabled()
    assert st.group_of['res'] == 'openEMS/Mesh' and st.group_of['mom_dmax'] == 'mom25d'
    assert st.group_of['excite'] == 'Solvers' and st.group_of['ff_freq'] == 'Output' and 'Antenna' not in st.sections
    assert st.group_of['dump_port'] == st.group_of['dump_animate'] == 'Output'
    assert 'qucsator' not in st.group_of and 'Line model' not in st.sections and 'Field dumps' not in st.sections
    assert 'mom_dmax' not in [k for k, g in st.group_of.items() if g == 'Solvers']
    assert w.system.qucsator.text() == ''                           # the Solvers window's, empty: the PATH's
    st.set_values(dict(board='b.kicad_pcb', solvers=['mom25d']))
    oems, mom = st.parents['openEMS'][0], st.sections['mom25d']
    assert not oems.isEnabled() and mom.isEnabled() and 'openEMS' in oems.toolTip()
    assert st.sections['Output'].isEnabled() and not st.edits['ff_freq'][0].isEnabled()    # S-parameters: no pattern
    st.edits['mode'][0].setCurrentText('antenna')
    assert st.edits['ff_freq'][0].isEnabled() and st.field_labels['ff_freq'].isEnabled()
    st.solver_boxes['openems'].setChecked(True)
    assert oems.isEnabled()
    st.set_values(dict(board='b.kicad_pcb'))                        # auto: openEMS, mom25d and the line model may run
    assert oems.isEnabled() and mom.isEnabled()
    w.hide()
    app.processEvents()


def test_solvers_window_saves_qucsator(tmp_path, monkeypatch):
    """The Solvers and hardware window's qucsator field: saved for the user (sitecfg), then the solvers checked
    again; emptied, the PATH's again."""
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    from kicad2ems import sitecfg
    monkeypatch.setattr(sitecfg, '_cache', None)
    from kicad2ems.gui import application
    from kicad2ems.gui.systempanel import SystemPanel
    app = application()
    p = SystemPanel()
    p.stop()
    checks = []
    monkeypatch.setattr(p, 'refresh', lambda: checks.append(1))
    p.qucsator.setText('/opt/qucs/bin/qucsator_rf')
    p._save_programs()
    assert sitecfg.get('qucsator') == '/opt/qucs/bin/qucsator_rf' and checks == [1]
    p._save_programs()                                              # unchanged: no new check
    assert checks == [1]
    p.qucsator.setText('')
    p._save_programs()
    assert sitecfg.get('qucsator') is None and checks == [1, 1]
    monkeypatch.setattr(sitecfg, '_cache', None)
