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
