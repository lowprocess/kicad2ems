"""Every analysis's results in the pages: the run folders of the analyses' tools
(kicad2ems.core.runs), their curves on the Results page (curves.py,
curveplot.py) with other runs over them, a result's three views (its plots,
its report, its tables), the pages that come and go, a second Viewer. The analyses are the
stand-ins (fake_analyses.py)."""
import json
import os
import time

import numpy as np
import pytest

import fake_analyses as FA
from kicad2ems.core import runs
from kicad2ems.gui import curves

_pdn = lambda folder, scale=1.0, rail='P': FA.write('pdn', folder, rail=rail,
                                                    scale=scale)
_mqs = lambda folder: FA.write('mqs', folder, converter='U3')


def test_tool_run_folders(tmp_path):
    base, board = str(tmp_path / 'out'), str(tmp_path / 'b.kicad_pcb')
    t0 = __import__('datetime').datetime(2026, 11, 11, 1, 31, 36)
    a = runs.tool_dir(base, board, 'pdn', '+3V3', now=t0)
    assert os.path.basename(a) == 'b_pdn_3V3_20261111_013136'
    assert runs.tool_preview_dir(base, board,
                                 'mqs').endswith('b_mqs_preview')
    _pdn(a)
    # stopped: no result
    os.makedirs(
        runs.tool_dir(base, board, 'pdn', 'X', now=t0.replace(second=40)))
    # the old place, beside the board: not a run folder any more
    _mqs(str(tmp_path / 'mqs_U3'))
    # another board's
    _pdn(str(tmp_path / 'out' / 'other_pdn_P_20261111_013136'))
    # a preview: not a run
    FA.write('mqs', runs.tool_preview_dir(base, board, 'mqs'),
             preview=True)
    th = FA.write(
        'thermal',
        runs.tool_dir(base, board, 'thermal', 'circuit',
                      now=t0.replace(second=50)))
    found = [r for r in runs.index(base, board) if r.kind != 'rf']
    assert ([(os.path.basename(r.folder), r.kind, r.outcome) for r in found
             ] == [('b_thermal_circuit_20261111_013150', 'thermal', 'done'),
                   ('b_pdn_X_20261111_013140', 'pdn', 'no result'),
                   ('b_pdn_3V3_20261111_013136', 'pdn', 'done')])
    assert (found[2].what == '3V3' and found[2].questions == ('dc', 'pdn')
            and found[2].engines == ('pdn', )
            and found[2].label().endswith(
                'Rail drop, Rail impedance · 3V3 · fake'))
    # one folder alone, wherever it is (no mark: whose result it holds)
    kind = lambda d: getattr(runs.info(d), 'kind', None)
    assert (kind(a) == 'pdn' and kind(str(tmp_path)) is None
            and kind(th) == 'thermal' and runs.info(runs.tool_preview_dir(
                base, board, 'mqs')).preview)
    assert (curves.run_label(a) == '3V3, 11-11 01:31:36'
            and curves.run_label(str(tmp_path / 'mqs_U3')) == 'mqs_U3')


def test_curves(tmp_path):
    cs = curves.load_curves(_pdn(str(tmp_path / 'p')))
    assert (cs.kind == 'pdn'
            and [c.role for c in cs.curves] == ['data', 'reference', 'limit'])
    assert 'rail P' in cs.info and cs.curves[2].name == 'target 50 mΩ'
    u1 = cs.curves[0]
    # between points, along the log axes
    assert curves.value_at(u1, 1e6) == pytest.approx(0.02, rel=0.05)
    assert curves.value_at(u1, 1e3) is None
    ms = curves.load_curves(_mqs(str(tmp_path / 'm')))
    assert (ms.kind == 'mqs' and list(ms.curves[0].y) == [3, 1.5, 0.9]
            and not ms.curves[0].role != 'data')
    assert (curves.load_curves(str(tmp_path)) is None
            and curves.load_sets(str(tmp_path)) == [])
    # several sets: a circuit's waveforms
    sets = curves.load_sets(FA.write('circuit', str(tmp_path / 'c')))
    assert ([x.title for x in sets] == [
        'light: voltages', 'light: currents', 'full: voltages', 'full: currents'
    ])
    assert (not sets[0].logx and not sets[0].logy
            and curves.value_at(sets[0].curves[0], 0.25, False, False) == 1.0)
    # a result with none
    assert curves.load_sets(FA.write('thermal', str(tmp_path / 't'))) == []
    curves.export_csv(str(tmp_path / 'c.csv'), [cs, ms])
    import csv
    head, *rows = list(csv.reader(open(tmp_path / 'c.csv')))
    assert (sum('frequency (Hz)' in h for h in head) == 2 and len(rows) == 21
            and all(len(r) == len(head) for r in rows))
    # the spectrum's 3 points, padded
    assert rows[-1][-1] == '' and rows[0][-1] == '3'


@pytest.fixture
def window(tmp_path, monkeypatch):
    pytest.importorskip('PySide6')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    from PySide6.QtCore import QSettings
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    QSettings('kicad2ems', 'gui').clear()
    w = MainWindow()
    yield w, app
    w.setup.dirty = False
    w.system.stop()
    w.meters.stop()
    w.close()
    QSettings('kicad2ems', 'gui').clear()


def test_results_of_lists_every_analysis_and_compares_runs(window, tmp_path):
    w, app = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board)))
    base = w.out_dir()
    t = __import__('datetime').datetime(2026, 11, 11, 1, 0, 0)
    old = _pdn(runs.tool_dir(base, str(board), 'pdn', 'P', now=t))
    new = _pdn(
        runs.tool_dir(base, str(board), 'pdn', 'P',
                            now=t.replace(minute=5)), scale=2)
    nf = _mqs(runs.tool_dir(base, str(board), 'mqs', 'U3', now=t))
    w.refresh_runs()
    rows = [(r.when.strftime('%H:%M'), r.about(), r.folder)
            for r in w.runs_panel.runs()]
    assert (rows == [('01:05', 'Rail drop, Rail impedance · P', new),
                     ('01:00', 'Rail drop, Rail impedance · P', old),
                     ('01:00', 'Converter near field · U3', nf)])
    w.show_run(new)
    r = w.results
    assert (r.kind() == 'pdn' and r.views.currentWidget() is r.curveplot
            and 'rail P' in r.info.text())
    assert (r.modes() == ['Plots', 'Report', 'Tables'] and r.mode == 'Plots'
            and r.set_box.isHidden())
    assert w.shown == new and w.runs_panel.selected() == [new]
    # another run selected beside it (Ctrl and a click): laid over
    w.runs_panel.tree.topLevelItem(1).setSelected(True)
    assert w.shown == new and r.compared() == [os.path.abspath(old)]
    assert not r.add_folder(nf)  # another kind: not over these
    cp = r.curveplot
    assert [cs.label for cs in cp.sets] == ['P, 11-11 01:05:00',
                                            'P, 11-11 01:00:00']
    cp.add_marker(1e6)
    row = cp.values()[0]
    # this run's U1, the other's (no bare planes)
    assert row[0] == '1: 1 MHz' and len(row) == 3
    assert (float(row[1].split()[0]) == pytest.approx(
        2 * float(row[2].split()[0]), rel=0.02))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    cp.table.selectRow(0)  # Delete in the table: the marker goes
    QTest.keyClick(cp.table, Qt.Key_Delete)
    assert cp.markers == [] and cp.table.isHidden()
    r._fill_compare()  # the Plots page's menu: the one laid over, unticked
    acts = [a for a in r.cmp_menu.actions() if a.isCheckable()]
    assert [a.text() for a in acts] == [os.path.basename(old)]
    acts[0].setChecked(False)
    assert r.compared() == []
    w.show_run(nf)  # the near field: its spectrum, its report
    assert r.kind() == 'mqs' and cp.main.curves[0].name == 'peak |H|'
    assert ('U3' in r.reportview.page.toPlainText()
            and r.info.text() == 'Near field, U3 on VIN: 3 harmonics')
    # nothing of an analysis: S-parameters again
    w.open_results(str(tmp_path), quiet=True)
    assert (r.kind() == 'rf' and r.views.currentWidget() is r.splot
            and r.modes() == [])
    # its pages: empty
    assert (r.reportview.report is None
            and 'No report yet' in r.reportview.page.toPlainText())


def test_a_result_as_its_plots_its_report_or_its_tables(window, tmp_path):
    """The Results page's three views of an analysis's result, whichever
    analysis: the ones the result has."""
    w, app = window
    w.show()
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board)))
    base = w.out_dir()
    th = FA.write('thermal',
                  runs.tool_dir(base, str(board), 'thermal', 'losses'),
                  loss='L1=0.48\nQ2=0.2')
    ci = FA.write('circuit',
                  runs.tool_dir(base, str(board), 'circuit', 'buck'))
    pd = FA.write('pdn', runs.tool_dir(base, str(board), 'pdn', 'P'),
                  note=True)
    r = w.results
    w.refresh_runs()
    w.show_run(th)  # no curves: its report in front
    assert (r.kind() == 'thermal' and r.modes() == ['Report', 'Tables']
            and r.mode == 'Report')
    # the report and its tables are pages of their own (docks), which the plots'
    # page fills
    assert (w.docks['Report'].widget() is r.reportview
            and w.docks['Tables'].widget() is r.tablesview)
    assert (r.reportview.mode() == 'Report' and r.tablesview.mode() == 'Tables'
            and not r.cmp_btn.isEnabled())
    assert (
        r.info.text() ==
        'Thermal: hottest copper 73.0 °C  ·  no curves in this result: its '
        'Report page has it'
    )
    assert 'L1' in r.reportview.page.toPlainText()
    r.set_mode('Tables')
    assert (r.mode == 'Tables' and r.tablesview.table()[0] == 'Parts'
            and not w.docks['Tables'].isHidden())
    assert (r.tablesview.grid.rowCount() == 2
            and r.tablesview.grid.item(0, 2).text() == '73.0')
    w.show_run(pd)  # the page kept in mind, when the next result has it
    assert (r.kind() == 'pdn' and r.mode == 'Tables'
            and r.tablesview.table()[0] == 'Loads at DC')
    # its notes: in its report
    assert (r.notes_btn.isHidden()
            and 'a note' in r.reportview.page.toPlainText())
    r.set_mode('Plots')
    assert r.views.currentWidget() is r.curveplot and r.cmp_btn.isEnabled()
    w.show_run(ci)  # several curve sets: one at a time
    assert (r.kind() == 'circuit' and r.mode == 'Plots'
            and not r.set_box.isHidden() and r.set_box.count() == 4)
    assert (r.curveplot.main.title == 'light: voltages'
            and not r.curveplot.main.logx)
    r.set_box.setCurrentIndex(3)
    assert (r.curveplot.main.title == 'full: currents'
            and r.curves is r.curve_sets[3])
    r.set_mode('Report')
    assert ('Stand-in circuit' in r.reportview.page.toPlainText()
            and r.tablesview.has_tables())
    w.show_run(th)  # no plots there: its report
    assert r.mode == 'Report' and r.set_box.isHidden()


def test_pages_that_come_and_go(window):
    w, app = window
    w.show()
    app.processEvents()
    assert not w.docks['Filter'].isVisible()  # opened by hand only
    w.acts['Filter'].trigger()
    assert w.docks['Filter'].isVisible()
    w.docks['Filter'].hide()
    app.processEvents()
    assert w.docks['Circuit'].isHidden() and 'circuit' not in w.study.ticked()
    if 'circuit' in w.study.why:
        pytest.skip('pcbqs is not installed: Circuit cannot be ticked')
    # the circuit's editor: tabbed beside the board
    w.study.set_ticked(['rf', 'circuit'])
    app.processEvents()
    assert (not w.docks['Circuit'].isHidden() and w.docks['Circuit']
            in w.work.tabifiedDockWidgets(w.docks['Viewer']))
    # (no board yet: what was in front stays)
    assert w.places['Start'].in_front()
    w.study.set_ticked(['rf'])
    assert w.docks['Circuit'].isHidden()


def test_second_viewer(window, tmp_path):
    w, app = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board)))
    base = w.out_dir()
    t = __import__('datetime').datetime(2026, 11, 11, 1, 0, 0)
    a = _pdn(runs.tool_dir(base, str(board), 'pdn', 'P', now=t))
    b = _pdn(
        runs.tool_dir(base, str(board), 'pdn', 'P',
                            now=t.replace(minute=5)))
    w.refresh_runs()
    w.show_run(b)
    v2 = w.second_viewer()
    # the run before the one shown
    assert v2.folder == a and 'Second run' in v2.which.text()
    assert w.second_viewer() is v2
    w.show_run(a)  # the second picked on the Runs page: there
    w.runs_panel.tree.topLevelItem(0).setSelected(True)
    assert v2.folder == b and w.shown == a
