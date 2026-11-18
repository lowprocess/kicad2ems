"""The Runs page (runspanel.py) and the one index of runs it reads
(runfolders.index): a config's runs and no other's, the one shown, ≠ settings,
deleting several, the toolbar's Showing, the steps bar and the study's list.
The analyses are the stand-ins (fake_analyses.py)."""
import datetime
import os

import pytest

import fake_analyses as FA
from kicad2ems.core import study
from kicad2ems.core.runs import write_toml
from kicad2ems.gui import runfolders as rf

T = datetime.datetime(2026, 11, 17, 10, 0, 0)


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
    w.study.set_available('pdn', True)
    yield w, app
    w.setup.dirty = False
    w.system.stop()
    w.meters.stop()
    w.close()
    QSettings('kicad2ems', 'gui').clear()


def _pdn(base, board, cfg, minutes, rail='P', settings=None, done=True):
    d = rf.tool_dir(base, board, 'pdn', rail,
                    now=T + datetime.timedelta(minutes=minutes))
    if done:
        FA.write('pdn', d, rail=rail)
    study.mark(d, 'pdn', ('dc', ), config=cfg, settings=settings)
    return d


def _rf(base, board, cfg, minutes, values, code=0):
    d = rf.run_dir(base, board, T + datetime.timedelta(minutes=minutes))
    os.makedirs(d)
    write_toml(os.path.join(d, 'gui_run.toml'), dict(values, out=d))
    study.mark(d, 'rf', ('sparams', ), config=cfg)
    if code == 0:
        with open(os.path.join(d, f"{rf.board_name(board)}_run.json"), 'w',
                  encoding='utf-8') as fh:
            fh.write('{"solvers": ["qucs"]}')
    return d


def test_index_scopes_settings(tmp_path):
    """index: a board's runs, the newest first, the preview last; every
    board's; a config's (its mark's file), the unsaved form's those with
    none; differ: what changed, without the noise."""
    base, board = str(tmp_path / 'out'), str(tmp_path / 'b.kicad_pcb')
    a, c = str(tmp_path / 'a.toml'), str(tmp_path / 'c.toml')
    old = _pdn(base, board, None, 0)
    mine = _pdn(base, board, a, 1)
    theirs = _pdn(base, board, c, 2)
    other = _pdn(base, str(tmp_path / 'z.kicad_pcb'), a, 3)
    pv = rf.preview_dir(base, board)
    os.makedirs(pv)
    runs = rf.index(base, board)
    assert [r.folder for r in runs] == [theirs, mine, old, pv]
    assert runs[-1].preview and runs[-1].label() == 'preview'
    assert [r.folder for r in rf.in_scope(runs, 'config', a)] == [mine, pv]
    assert [r.folder for r in rf.in_scope(runs, 'config', None)] == [old, pv]
    assert rf.in_scope(runs, 'board', a) == runs
    every = rf.index(base)
    assert ({r.board for r in every} == {'b', 'z'}
            and other in [r.folder for r in every])
    assert (runs[0].config == c and runs[0].size > 0
            and runs[0].questions == ('dc', ))
    assert rf.differ(dict(fmax=6e9, solvers=['qucs', 'openems'], out='x',
                          events='e', mom=False, name=''),
                     dict(fmax=6e9, solvers=('openems', 'qucs'))) == []
    assert rf.differ(dict(fmax=6e9), dict(fmax=8e9, air=2.0)) == [
        ('air', None, 2.0), ('fmax', 6e9, 8e9)]


def test_a_config_s_runs(window, tmp_path, monkeypatch):
    """Opening a config shows its newest run that finished, not another
    config's of the same board; the Runs page lists its runs (This board and
    All the rest), says which ran with other settings than the form's, shows
    one picked and deletes several at once (not one going); the toolbar's
    Showing, the steps bar and the study's list say the run shown and when
    each question was last answered."""
    from PySide6.QtWidgets import QMessageBox
    from kicad2ems.gui import steps as ST
    from kicad2ems.gui.runspanel import C_SET, showing
    from kicad2ems.gui.studytree import C_STATE
    w, app = window
    p = w.runs_panel
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    a, c = tmp_path / 'a.toml', tmp_path / 'c.toml'
    for cfg in (a, c):
        cfg.write_text('board = "b.kicad_pcb"\n\n[study]\nrun = ["dc"]\n\n'
                       '[analysis.pdn]\nrail = "P"\n')
    assert w.setup.load(str(c))
    assert (w.shown is None and w.showing_btn.text() == 'Nothing run yet'
            and {s['key']: s for s in ST.steps_of(w)}['results']['text']
            == 'none yet')
    base, b = w.out_dir(), str(board)
    row = w.pages['pdn'].runs()[0]  # what a run of the form now has
    t1 = _pdn(base, b, str(c), 30, settings=row)
    assert w.setup.load(str(a))
    assert w.shown is None and p.runs() == []
    assert 'one more' not in p.hint.text() and '1 more' in p.hint.text()
    t0 = _pdn(base, b, str(a), 0, settings=row)
    t2 = _pdn(base, b, str(a), 10, rail='Q', settings=dict(row, rail='Q'),
              done=False)
    assert w.setup.load(str(c)) and w.shown == t1
    assert w.setup.load(str(a))  # its newest that finished, not c's newer
    assert (w.shown == t0 and p.selected() == [t0]
            and [r.folder for r in p.runs()] == [t2, t0])
    assert ([r.outcome for r in p.runs()] == ['no result', 'done']
            and p.tree.topLevelItem(1).font(0).bold())
    assert w.showing_btn.text() == showing(p.info(t0)) and 'Rail drop' in (
        w.showing_btn.text())
    assert (ST.steps_of(w)[-1]['text'] == p.info(t0).label()[:46]
            and ST.steps_of(w)[-1]['state'] == 'done')
    # the scopes: the board's, every board's
    p.set_scope('board')
    assert [r.folder for r in p.runs()] == [t1, t2, t0]
    p.set_scope('all')
    assert not p.tree.isColumnHidden(7) and len(p.runs()) == 3
    p.set_scope('config')
    # ≠ settings: the rail Q's run, its tooltip what changed
    assert (p.tree.topLevelItem(0).text(C_SET) == '≠ settings'
            and 'rail: Q -> P' in p.tree.topLevelItem(0).toolTip(C_SET)
            and p.tree.topLevelItem(1).text(C_SET) == '')
    # kicad2ems's own runs: against the form now (its noise left out)
    now = w.run_values(False)
    r1 = _rf(base, b, str(a), 40, now)
    r2 = _rf(base, b, str(a), 50, dict(now, fmax=7.5e9))
    w.refresh_runs()
    cells = {p.tree.topLevelItem(i).data(0, 256):
             (p.tree.topLevelItem(i).text(C_SET),
              p.tree.topLevelItem(i).toolTip(C_SET))
             for i in range(p.tree.topLevelItemCount())}
    assert cells[r1][0] == '' and cells[r2][0] == '≠ settings'
    # one field changed: that key alone
    assert ([l.split(':')[0] for l in cells[r2][1].splitlines()[1:]]
            == ['fmax'] and 'fmax: 7500000000.0 -> ' in cells[r2][1])
    # the preview: current while the form is as it ran, stale with one change
    pv = rf.preview_dir(base, b)
    os.makedirs(pv)
    write_toml(os.path.join(pv, 'gui_run.toml'),
               dict(w.run_values(True), out=pv))
    w.refresh_runs()
    last = p.tree.topLevelItem(p.tree.topLevelItemCount() - 1)
    assert last.text(0) == 'preview' and last.text(3) == 'current'
    assert last.text(C_SET) == ''
    w.setup.edits['fmax'][0].setText('2.5')
    w.update_needed()
    assert last.text(3) == 'stale' and [
        l.split(':')[0] for l in last.toolTip(C_SET).splitlines()[1:]
    ] == ['fmax']
    w.setup.edits['fmax'][0].setText(str(now['fmax']))
    w.update_needed()
    assert last.text(3) == 'current' and not cells[r1][0]
    # the study's list: when each was last answered; a click shows that run
    tree = w.study
    assert (tree.answered['dc'][1] == t0 and tree.answered['sparams'][1] == r2
            and tree.rows['dc'].text(C_STATE).startswith('last '))
    tree._clicked(tree.rows['sparams'], C_STATE)
    assert w.shown == r2 and p.selected() == [r2]
    tree._clicked(tree.rows['dc'], C_STATE)
    assert w.shown == t0
    # several deleted at once, asked once with their sizes; not one going
    asked = []
    monkeypatch.setattr(QMessageBox, 'question', lambda _w, _t, text, *a:
                        asked.append(text) or QMessageBox.Yes)
    told = []
    monkeypatch.setattr(QMessageBox, 'information',
                        lambda *a: told.append(a[2]))
    monkeypatch.setattr(w, 'running_folders', lambda: [t0])
    assert w.delete_runs([t0]) == 0 and told and os.path.isdir(t0)
    assert w.delete_runs([r1, t2, t0]) == 2 and len(asked) == 1
    assert ('Delete 2 runs' in asked[0] and os.path.basename(r1) in asked[0]
            and ' B)' in asked[0] and 'the one going is kept' in asked[0])
    assert (not os.path.exists(r1) and not os.path.exists(t2)
            and os.path.isdir(t0))
    assert [r.folder for r in p.runs() if not r.preview] == [r2, t0]
