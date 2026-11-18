"""The study: the analyses' order and what follows what (study.py), the Study
dock (the board, the list, the selected analysis's settings) and the window's
Run of the ticked analyses, one after the other."""
import os
import pytest

from kicad2ems.gui import study
from waiting import wait_until


def test_the_steps_a_run_takes_are_kicad2ems_s_plan(monkeypatch):
    """The window's Run is kicad2ems's plan of the questions asked (the
    solvers' run first, then the analyses in the registry's order: thermal
    after the circuit); a preview, the runs that have one; a run that cannot be
    made here left out; an analysis that needs one that did not finish is not
    made (core.study.unmet, as `kicad2ems study` has it)."""
    from kicad2ems.core import analyses
    from kicad2ems.core.study import unmet
    m = study.StudyModel()
    for q in ('thermal', 'circuit', 'nearfield', 'sparams'):
        m.tick(q)
    assert m.jobs() == ['rf', 'mqs', 'circuit', 'thermal']
    assert [st.questions for st in m.steps()][:2] == [('sparams', ),
                                                      ('nearfield', )]
    assert m.jobs(preview=True) == ['rf', 'mqs']
    assert m.jobs(only='thermal') == ['thermal']
    m.set_available('mqs', False, 'not here')
    assert m.jobs() == ['rf', 'circuit', 'thermal']
    reg = analyses.registry()
    monkeypatch.setattr(reg['thermal'], 'needs', ('circuit', ))
    assert (unmet('thermal', {'circuit'}) == ['circuit']
            and unmet('thermal', {'pdn'}) == [] and unmet('rf', {'x'}) == [])


@pytest.fixture
def window(tmp_path, monkeypatch):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    from PySide6.QtCore import QSettings
    from kicad2ems.gui import application
    from kicad2ems.gui.app import MainWindow
    app = application()
    QSettings('kicad2ems', 'gui').clear()
    w = MainWindow()
    # as on a machine with pcbqs, whatever is here
    for k in ('pdn', 'mqs', 'circuit'):
        w.study.set_available(k, True)
        w.study.set_state(k, '')
    yield w, app, MainWindow
    w.setup.dirty = False
    w.system.stop()
    w.meters.stop()
    QSettings('kicad2ems', 'gui').clear()


def test_study_dock(window):
    """The board above the list; the list: the questions by group, who answers
    each, its state; the settings of the selected question's engines under it (a
    panel made from each analysis's options), no Run button of its own; what is
    asked kept for the next window."""
    from PySide6.QtCore import Qt
    from kicad2ems.gui.analysispanel import AnalysisPanel
    w, app, MainWindow = window
    st, tree = w.setup, w.study
    # the list: the window's left column
    assert st.study is tree and st.isAncestorOf(tree)
    groups = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    assert ([g.text(0)
             for g in groups] == ['RF', 'Signal integrity', 'Power', 'Thermal'])
    assert ([[g.child(k).text(0) for k in range(g.childCount())] for g in groups
             ] == [['S-parameters', 'Antenna pattern', 'Trace impedances'],
                   ['Crosstalk'],
                   [
                       'Rail drop', 'Rail impedance', 'Converter near field',
                       'Circuit', 'Coils and ferrite', 'Capacitance'
                   ], ['Temperature']])
    assert (tree.headerItem().text(1) == 'Answered by'
            and tree.headerItem().text(2) == 'Fields')
    by = lambda q: tree.rows[q].text(1).replace('  ▾', '')
    assert ([by(q) for q in ('dc', 'pdn', 'nearfield', 'circuit', 'thermal')
             ] == ['fake'] * 5)
    # two engines
    assert (tree.rows['thermal'].text(1).endswith('▾')
            and not tree.rows['dc'].text(1).endswith('▾'))
    assert (tree.rows['capacitance'].text(3) == 'not installed'
            and 'no engine for it' in tree.rows['capacitance'].toolTip(3))
    assert not tree.rows['capacitance'].flags() & Qt.ItemIsUserCheckable
    # as Run always did
    assert (tree.asked() == dict(sparams=[]) and tree.ticked() == ['rf']
            and tree.current() == 'rf')
    assert (tree.current_question() == 'sparams'
            and '1 asked' in st.study_section.head.text())
    assert (set(st.panels) == {
        'rf', 'pdn', 'thermal', 'elmer_thermal', 'mqs', 'circuit'
    })
    assert (list(
        w.pages) == ['pdn', 'mqs', 'circuit', 'thermal', 'elmer_thermal'])
    assert (not st.rf_box.isHidden() and st.panels['pdn'].isHidden()
            and st.panel_title.text() == 'S-parameters settings')
    # the board: for every question
    assert st.sections['Board'].parentWidget() is not st.rf_box
    # under More settings
    assert (st.rf_box.isAncestorOf(st.more)
            and st.more.isAncestorOf(st.sections['Frequency']))
    # its own dock
    assert (w.setup.inspector.isAncestorOf(st.rf_box)
            and w.settings_dock.widget() is w.setup.inspector)
    tree.select('dc')
    assert (st.rf_box.isHidden() and not st.panels['pdn'].isHidden()
            and tree.current() == 'pdn')
    # one engine
    assert (st.panel_title.text() == 'Rail drop settings'
            and st.engine_titles['pdn'].isHidden())
    pdn, mqs = w.pages['pdn'], w.pages['mqs']
    assert (st.panels['pdn'] is pdn and isinstance(pdn, AnalysisPanel)
            and pdn.isAncestorOf(pdn.w['rail']))
    assert (pdn.run_btn.isHidden() and mqs.run_btn.isHidden()
            and w.pages['circuit'].run_btn.isHidden())
    assert not mqs.find_btn.isHidden()  # it fills the form: it stays
    assert (st.panels['circuit'] is w.pages['circuit'].panel
            and st.panels['circuit'].isAncestorOf(w.pages['circuit'].params))
    assert pdn.board.placeholderText() == "the study's board"
    # no page per analysis: the circuit's editor alone
    assert (list(w.places) == [
        'Viewer', 'Start', 'Results', 'Report', 'Tables', 'Run', 'Runs',
        'Section', 'Filter', 'Circuit'
    ])
    tree.rows['dc'].setCheckState(0, Qt.Checked)
    assert (tree.asked() == dict(sparams=[], dc=[])
            and tree.ticked() == ['rf', 'pdn']
            and '2 asked' in st.study_section.head.text())
    assert (tree.asked_of('pdn') == ('dc', )
            and tree.asked_of('rf') == ('sparams', ))
    # the rail's impedance too: the same run
    tree.rows['pdn'].setCheckState(0, Qt.Checked)
    assert (tree.ticked() == ['rf', 'pdn']
            and tree.asked_of('pdn') == ('dc', 'pdn'))
    w.close()
    w2 = MainWindow()  # the next window: the same questions
    assert (list(w2.study.asked()) == ['sparams', 'dc', 'pdn']
            and w2.study.ticked() == ['rf', 'pdn'])
    w2.system.stop()
    w2.meters.stop()


def test_who_answers_is_chosen_on_the_row(window):
    """Answered by: a menu of the question's engines, 'auto' first; several
    ticked run and are compared. The settings shown are those of the engines
    chosen, each under its name; an engine that cannot run here is not offered,
    and a question nobody can answer is greyed."""
    from PySide6.QtCore import Qt
    w, app, _ = window
    st, tree = w.setup, w.study
    w.show()
    app.processEvents()
    assert ([e.name
             for e in tree.engines['thermal']] == ['thermal', 'elmer_thermal']
            and tree.engines_for('thermal') == ['thermal'])
    texts = lambda q: [(a.text(), a.isChecked(), a.isEnabled())
                       for a in tree.engine_menu(q).actions()
                       if not a.isSeparator()]
    assert (texts('thermal') == [('auto (fake)', True, True),
                                 ('fake  (fake.thermal)', False, True),
                                 ('Elmer  (fake.elmer)', False, True)])
    tree.select('thermal')
    assert (not st.panels['thermal'].isHidden()
            and st.panels['elmer_thermal'].isHidden())
    tree.rows['thermal'].setCheckState(0, Qt.Checked)
    # with the default: both, compared
    tree.choose('thermal', 'elmer_thermal', True)
    assert (tree.choice['thermal'] == ['thermal', 'elmer_thermal']
            and tree.rows['thermal'].text(1) == 'fake + Elmer  ▾')
    assert (tree.ticked() == ['rf', 'thermal', 'elmer_thermal']
            and tree.asked()['thermal'] == ['thermal', 'elmer_thermal'])
    assert (not st.panels['thermal'].isHidden()
            and not st.panels['elmer_thermal'].isHidden())
    assert (not st.engine_titles['thermal'].isHidden() and
            st.engine_titles['elmer_thermal'].text() == 'Elmer  ·  fake.elmer')
    assert (texts('thermal')[0][1] is False
            and [t[1] for t in texts('thermal')[1:]] == [True, True])
    tree.choose('thermal', 'thermal', False)  # Elmer alone
    assert (tree.ticked() == ['rf', 'elmer_thermal']
            and tree.current() == 'elmer_thermal'
            and st.panels['thermal'].isHidden())
    tree.set_state('elmer_thermal', 'running')
    # not this question's engine now: not shown
    tree.set_state('thermal', 'done 10:00')
    assert (tree.rows['thermal'].text(3) == 'running'
            and tree.rows['thermal'].font(3).bold())
    tree.choose('thermal', 'thermal', True)
    tree.set_state('elmer_thermal', 'failed (exit 2)', 'bad')
    assert tree.rows['thermal'].text(3) == 'fake done 10:00 · Elmer failed'
    assert (tree.rows['thermal'].toolTip(3) ==
            'fake: done 10:00\nElmer: failed (exit 2)')
    tree.choose('thermal', 'auto', True)  # back to its default
    assert (tree.choice['thermal'] == [] and tree.ticked() == ['rf', 'thermal']
            and tree.rows['thermal'].text(1) == 'fake  ▾')
    # an engine that cannot run here: the other answers; neither: the question
    # is greyed, unticked
    tree.set_available('thermal', False, 'no ngspice')
    assert (tree.engines_for('thermal') == ['elmer_thermal']
            and tree.ticked() == ['rf', 'elmer_thermal'])
    assert (texts('thermal')[0][0] == 'auto (Elmer)'
            and texts('thermal')[1][2] is False)
    tree.set_available('elmer_thermal', False, 'no Elmer')
    it = tree.rows['thermal']
    assert (it.checkState(0) == Qt.Unchecked and it.text(3) == 'not available'
            and tree.ticked() == ['rf'])
    assert (it.toolTip(3) == 'fake: no ngspice; Elmer: no Elmer'
            and not it.flags() & Qt.ItemIsUserCheckable)
    # still to read
    assert (not st.panels['thermal'].isHidden()
            and not st.panels['elmer_thermal'].isHidden())
    tree.set_available('thermal', True)
    assert it.flags() & Qt.ItemIsUserCheckable and it.text(3) == ''
    w.close()


def test_the_solvers_are_ticked_on_the_rf_rows(window):
    """The solvers' tick boxes are off the form: the S-parameters row's menu
    sets them ('auto' among them), the Antenna row is the run's antenna mode,
    the Trace impedances row the line model; the three are one run."""
    from PySide6.QtCore import Qt
    w, app, _ = window
    st, tree = w.setup, w.study
    assert all(b.isHidden() and b.parentWidget() is st
               for b in st.solver_boxes.values())
    # the section keeps the driven ports
    assert st.edits['mode'][0].isHidden() and 'Solvers' in st.sections
    names = [a.text() for a in tree.engine_menu('sparams').actions()]
    assert (names[0].startswith('auto') and names[1:4]
            == ['openEMS (FDTD)', 'mom25d (MoM)', 'gfdtd (GPU FDTD)'])
    assert (tree.rows['sparams'].text(1) == 'auto  ▾'
            and st.values().get('solvers') is None)
    tree.choose('sparams', 'auto', False)
    tree.choose('sparams', 'mom25d', True)
    tree.choose('sparams', 'qucs', True)
    assert (tree.rows['sparams'].text(1) == 'mom25d + line model  ▾'
            and tree.rows['lines'].text(1) == 'line model  ▾')
    # a setting of the form, as any
    assert st.values()['solvers'] == ['mom25d', 'qucs'] and st.dirty
    # a solver's section: its tick
    assert (st.sections['mom25d'].isEnabled()
            and not st.parents['FDTD'][0].isEnabled())
    # a config's solvers: the rows show them
    w.setup.set_values(dict(solvers=['openems']))
    assert (tree.rows['sparams'].text(1) == 'openEMS  ▾'
            and tree.rows['antenna'].text(1) == 'openEMS  ▾')
    # the antenna: a row of its own, the run's mode
    assert st.values().get('mode') in (None, '', 'sparams')
    tree.rows['antenna'].setCheckState(0, Qt.Checked)
    assert (st.values()['mode'] == 'antenna' and tree.ticked() == ['rf']
            and tree.asked_of('rf') == ('sparams', 'antenna'))
    assert [a.text() for a in tree.engine_menu('antenna').actions()
            ][0].startswith('auto')
    assert (not any(a.text().startswith('line model')
                    for a in tree.engine_menu('antenna').actions()))
    assert any(a.text().startswith('line model')
               for a in tree.engine_menu('sparams').actions())
    tree.rows['antenna'].setCheckState(0, Qt.Unchecked)
    assert st.values().get('mode') in (None, '', 'sparams')
    # a config in antenna mode: the row ticked
    w.setup.set_values(dict(solvers=['openems'], mode='antenna'))
    assert 'antenna' in tree.asked()
    tree.rows['sparams'].setCheckState(0, Qt.Unchecked)
    tree.rows['antenna'].setCheckState(0, Qt.Unchecked)
    assert tree.ticked() == [] and tree.asked() == {}
    # the line model alone: still kicad2ems's run
    tree.rows['lines'].setCheckState(0, Qt.Checked)
    assert tree.ticked() == ['rf'] and tree.asked_of('rf') == ('lines',)
    w.setup.dirty = False
    w.close()


def test_the_fields_tick(window):
    """Fields: a tick on the rows whose engines can write field dumps. Ticked
    with none set, a default goes into the settings (an FDTD solver's dump,
    mom25d's current maps, an analysis's switch); unticked, the settings stay
    and the run leaves the dumps out."""
    from PySide6.QtCore import Qt
    w, app, _ = window
    st, tree = w.setup, w.study
    box = lambda q: None if tree.rows[q].data(
        2, Qt.CheckStateRole) is None else tree.rows[q].checkState(2)
    assert (box('sparams') == Qt.Unchecked and box('lines') is None
            and box('thermal') is None and box('dc') is None)
    assert box('circuit') == Qt.Unchecked  # its analysis has a fields switch
    w.setup.set_values(dict(solvers=['openems', 'mom25d'], fmin=2.0, fmax=6.0))
    assert st.edits['dump'][0].text() == '' and tree.fields_on('rf') is False
    tree.rows['sparams'].setCheckState(2, Qt.Checked)
    assert (st.edits['dump'][0].text() == 'E@F.Cu@4'
            and st.edits['mom_dump'][0].text() == '4.0'
            and tree.fields_on('rf'))
    st.edits['dump'][0].setText('H@In1.Cu@3')  # changed in the settings: kept
    tree.rows['sparams'].setCheckState(2, Qt.Unchecked)
    tree.rows['sparams'].setCheckState(2, Qt.Checked)
    assert (st.edits['dump'][0].text() == 'H@In1.Cu@3'
            and tree.study().fields == dict(sparams=True))
    # the line model alone: nothing to dump
    w.setup.set_values(dict(solvers=['qucs']))
    assert box('sparams') is None and not tree.fields_on('rf')
    tree.rows['circuit'].setCheckState(0, Qt.Checked)
    tree.rows['circuit'].setCheckState(2, Qt.Checked)
    page = w.pages['circuit']  # its editor's own switch: the same tick
    assert (page.fields.isChecked() and tree.fields_on('circuit')
            and tree.study().fields['circuit'] is True)
    tree.rows['circuit'].setCheckState(2, Qt.Unchecked)
    assert (not page.fields.isChecked()
            and tree.study().fields['circuit'] is False)
    page.fields.setChecked(True)  # ticked in its settings: the row follows
    assert box('circuit') == Qt.Checked and tree.fields['circuit'] is True
    w.setup.dirty = False
    w.close()


def test_the_highlighted_question_drives_the_settings(window):
    """The Settings dock is the highlighted question's, always: a page brought
    to the front leaves both as they are (the Circuit page had switched the
    dock to Circuit settings with S-parameters highlighted); a double-click on
    a question shows its page; a group's heading is no question (a click on it
    selects nothing, the keys step over it)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w, app, _ = window
    w.show()
    app.processEvents()
    st, title = w.study, w.setup.panel_title
    shown = lambda: (st.current_question(), title.text(),
                     [st._q(i) for i in st.selectedItems()])
    # the window opening selects nothing else
    assert shown() == ('sparams', 'S-parameters settings', ['sparams'])
    w.study.set_ticked(['rf', 'circuit'])  # its page comes with its tick
    app.processEvents()
    assert not w.places['Circuit'].isHidden()
    w.show_panel('Circuit')
    app.processEvents()
    assert (shown() == ('sparams', 'S-parameters settings', ['sparams'])
            and w.places['Circuit'].in_front())
    w.show_analysis('pdn')  # one without a page of its own: its settings,
    app.processEvents()     # and where its results are
    assert w.study.current() == 'pdn' and not w.places['Results'].isHidden()
    w.show_analysis('circuit')
    app.processEvents()
    assert (shown() == ('circuit', 'Circuit settings', ['circuit'])
            and w.places['Circuit'].in_front())
    st.setCurrentItem(st.rows['sparams'])  # the user's pick
    w.show_panel('Viewer')
    w.show_panel('Circuit')
    app.processEvents()
    assert shown() == ('sparams', 'S-parameters settings', ['sparams'])
    head = st.rows['circuit'].parent()  # 'Power': a heading
    QTest.mouseClick(st.viewport(), Qt.LeftButton,
                     pos=st.visualItemRect(head).center())
    assert shown() == ('sparams', 'S-parameters settings', ['sparams'])
    st.setCurrentItem(st.rows['lines'])  # the last RF row, then down
    QTest.keyClick(st, Qt.Key_Down)
    app.processEvents()
    assert shown() == ('crosstalk', 'Crosstalk settings', ['crosstalk'])
    QTest.keyClick(st, Qt.Key_Up)
    app.processEvents()
    assert shown()[0] == 'lines'
    w.close()


def test_the_circuit_page_is_there_only_while_asked(window):
    """The Circuit page: there while Circuit is ticked, and otherwise not at
    all (View has it greyed, nothing opens it); a new study's circuit is blank,
    whatever the page had."""
    w, app, _ = window
    w.show()
    app.processEvents()
    d, page = w.places['Circuit'], w.pages['circuit']
    act = d.toggleViewAction()
    assert 'circuit' not in w.study.ticked()
    assert d.isHidden() and not act.isEnabled() and page.is_blank()
    assert w.show_panel('Circuit') is False and d.isHidden()
    w.show_analysis('circuit')  # a double-click on its row: said why
    assert d.isHidden() and 'Tick Circuit' in w.statusBar().currentMessage()
    w.study.set_ticked(['rf', 'circuit'])
    app.processEvents()
    assert not d.isHidden() and act.isEnabled()
    page.new_model('buck')
    w.study.set_ticked(['rf'])
    app.processEvents()
    assert d.isHidden() and not act.isEnabled()
    w.reset_layout()  # (every page back: not this one)
    app.processEvents()
    assert d.isHidden() and not act.isEnabled()
    w.load_analyses(None)  # a new study: no circuit on the page
    assert page.is_blank() and page.model_path is None
    w.close()


def test_the_log_shows_what_ran_and_clears_with_another_study(
        window, monkeypatch):
    """The Log page: nothing before anything runs (no plot, no list of logs);
    the runs that started listed under Log of (not S-parameters in a study
    that does not run them), a plot once its run sends data; another study
    opened: blank again."""
    from kicad2ems.gui.runtab import IDLE
    w, app, _ = window
    rt = w.runtab
    blank = lambda: (rt.stage.text() == IDLE and rt.log_of.count() == 0
                     and rt.log_of.isHidden() and rt.plot.isHidden()
                     and rt.sweep.isHidden() and rt.jobs.isHidden()
                     and rt.logs.currentIndex() == 0)
    assert blank()
    monkeypatch.setattr(w, '_start_job', lambda key, preview: None)
    w.study.set_ticked(['circuit', 'thermal'])
    w.run_study()
    logs = lambda: [rt.log_of.itemText(i) for i in range(rt.log_of.count())]
    # one run: its log, no list to choose from
    assert (logs() == ['Circuit'] and rt.log_of.isHidden()
            and rt.logs.currentIndex() == rt.views['circuit'][1])
    w._job_done('circuit', 0)
    for _ in range(5):
        app.processEvents()
    assert (logs() == ['Circuit', 'Thermal'] and not rt.log_of.isHidden()
            and rt.log_of.currentData() == 'thermal')
    assert rt.plot.isHidden()  # no FDTD run: no energy plot
    rt.status(dict(run='P1', step=1000, db=-10.0, fraction=0.1))
    assert not rt.plot.isHidden()
    rt.log.appendPlainText('the last study\'s line')
    assert not blank()
    w._job_done('thermal', 0)
    for _ in range(5):
        app.processEvents()
    w.load_analyses(None)  # another study
    assert blank() and rt.log.toPlainText() == ''
    w.close()


def test_run_starts_the_ticked_analyses_in_turn(window, monkeypatch):
    """Run: each ticked analysis after the one before ended; one that cannot
    start says why and the rest go on; Stop ends the one running and leaves the
    queued ones; a tool going on a page keeps a second Run away."""
    from PySide6.QtWidgets import QMessageBox
    w, app, _ = window
    started, said = [], []
    monkeypatch.setattr(QMessageBox, 'information',
                        lambda *a: said.append(a[2]))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: said.append(a[2]))
    why = {}

    def start(key, preview):
        started.append((key, preview))
        return why.get(key)
    monkeypatch.setattr(w, '_start_job', start)
    pump = lambda: [app.processEvents() for _ in range(5)]
    w.study.set_ticked(['rf', 'mqs', 'circuit'])
    w.run_study()
    assert (started == [('rf', False)] and w.job == 'rf'
            and w.queue == ['mqs', 'circuit'])
    assert (w.study.state('rf') == 'running'
            and w.study.state('circuit') == 'queued')
    assert (not w.runtab.jobs.isHidden()
            and w.runtab.jobs.topLevelItemCount() == 3)
    assert w.acts['Stop'].isEnabled()
    w.run_study()  # a run is going
    assert said and 'stop it first' in said[-1] and len(started) == 1
    w._job_done('mqs', 0)  # not the one running: nothing
    assert w.job == 'rf'
    w._job_done('rf', 0)
    pump()
    assert (started[-1] == ('mqs', False)
            and w.study.state('rf').startswith('done '))
    w._job_done('mqs', 3)  # failed: the next still runs
    pump()
    assert (w.study.state('mqs') == 'failed (exit 3)'
            and started[-1] == ('circuit', False))
    w._job_done('circuit', 0)
    pump()
    assert w.job is None and not w.queue and not w.acts['Stop'].isEnabled()
    assert w.runtab.job_rows['circuit'].text(2) == '0:00'
    # one that cannot start: why, in the list; the others run
    started.clear()
    why['rf'] = 'Choose a board first'
    w.study.set_ticked(['rf', 'pdn'])
    w.run_study()
    assert started == [('rf', False), ('pdn', False)] and w.job == 'pdn'
    assert w.study.state('rf') == 'not started: Choose a board first'
    # the column: short, the tooltip whole
    assert w.study.rows['sparams'].text(3) == 'not started'
    # Stop: the queued ones are not run
    w._job_done('pdn', 0)
    pump()
    why.clear()
    started.clear()
    stopped = []
    monkeypatch.setattr(w.runner, 'stop', lambda: stopped.append('pdn') or '')
    w.study.set_ticked(['pdn', 'circuit'])
    w.run_study()
    assert w.job == 'pdn'
    w.stop()
    assert (stopped == ['pdn'] and w.queue == []
            and w.study.state('circuit') == 'not run: stopped')
    w._job_done('pdn', 9)  # its process, killed
    pump()
    assert (w.study.state('pdn') == 'stopped' and w.job is None
            and started == [('pdn', False)])
    # Preview: the analyses that have one; one alone: whatever is ticked
    started.clear()
    w.study.set_ticked(['rf', 'pdn', 'mqs'])
    w.preview()
    assert started == [('rf', True)] and w.queue == ['mqs']
    w._job_done('rf', 0)
    pump()
    w._job_done('mqs', 0)
    pump()
    started.clear()
    w.run_study(only='circuit')
    # one job: the page as it was
    assert started == [('circuit', False)] and w.runtab.jobs.isHidden()
    w._job_done('circuit', 0)
    pump()
    w.study.set_ticked([])
    w.run_study()
    assert 'Tick a question' in said[-1] and w.job is None
    w.close()


def run_to_the_end(w, app, seconds=120):
    import time
    wait_until(lambda: not (w.busy()), seconds)
    for _ in range(10):
        app.processEvents()
    assert not w.busy()


def test_an_analysis_runs_from_the_window(window, tmp_path):
    """The toolbar's Run starts a ticked analysis's own command (the stand-in's
    child process): a missing setting is why it did not start; its log is on the
    Run page; a failure is its state. Done, its result is the one shown: listed
    under Results of, its report on the Results page, its map in the Viewer
    where its report says."""
    w, app, _ = window
    w.show()
    pdn, th = w.pages['pdn'], w.pages['thermal']
    w.study.set_ticked(['pdn'])
    w.run()  # no board, no rail: Run says so, and starts nothing
    assert (w.job is None and w.study.state('pdn') == ''
            and w.acts['Run'].text() == 'Run' and w.acts['Run'].toolTip(
            ).startswith('Power integrity still needs: choose a board'))
    w.run_study()  # started all the same (one question alone is):
    # its row says why
    assert (w.job is None
            and w.study.state('pdn').startswith('not started: choose a board'))
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    # the study's board: the panel's too
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    pdn.set_values(dict(rail='FAIL'))
    w.run()
    assert w.job == 'pdn' and w.study.state('pdn') == 'running' and w.busy()
    assert (w.runtab.stage.text() == 'Power integrity: running'
            and w.runtab.plot.isHidden())
    assert w.runtab.log_of.currentData() == 'pdn'
    run_to_the_end(w, app)
    assert (pdn.proc is None and w.job is None
            and w.study.state('pdn') == 'failed (exit 3)')
    assert ('failed' in pdn.status.text()
            and 'the rail has no copper' in pdn.status.text())
    # its log, on the Run page
    assert ('pdn: reading the board'
            in w.runtab.logs.currentWidget().toPlainText())
    pdn.set_values(dict(rail='+3V3'))
    th.set_values(dict(loss='Q1=0.5\nL1=0.2'))
    w.study.set_ticked(['pdn', 'thermal'])
    w.run()
    run_to_the_end(w, app)
    assert (w.study.state('pdn').startswith('done ')
            and w.study.state('thermal').startswith('done '))
    assert (sorted((r.kind, r.what, r.outcome) for r in w.runs_panel.runs())
            == [('pdn', '3V3', 'done'), ('pdn', 'FAIL', 'failed, code 3'),
                ('thermal', 'losses', 'done')])
    assert (w.shown == th.out == w.runs_panel.selected()[0]
            and os.path.basename(th.out).startswith('b_thermal_losses_'))
    r = w.results
    # what it found: in front
    assert (r.kind() == 'thermal' and r.mode == 'Report'
            and w.places['Report'].in_front())
    assert (r.info.text().startswith('Thermal: hottest copper 75.0 °C')
            and th.status.text() == 'done: Thermal: hottest copper 75.0 °C')
    cuts = w.viewer.cuts  # its map: a temperature, shown as one
    assert ([s.label for s in cuts.srcs] == ['thermal board: T F.Cu (static)']
            and cuts.src.unit == '°C')
    # its own range: not from 0
    assert (not cuts.log.isChecked() and cuts.lim == pytest.approx(
        (40.0, 75.0), abs=0.6))
    assert (cuts.cbar.axis.labelText == 'T (°C)'
            and 'steady' in cuts.frame_lab.text())
    assert w.latest_results() == dict(pdn=pdn.out, thermal=th.out)
    w.close()


def test_two_engines_of_one_question_run_and_are_compared(window, tmp_path):
    """The temperature asked of both its engines: each runs with its own
    settings into a folder that says whose it is; the Results page lays the
    figures they name alike side by side, with the difference; the config keeps
    who was asked."""
    from PySide6.QtCore import Qt
    from kicad2ems.core.analyses import analysis_of
    from kicad2ems.core import runs, study
    w, app, _ = window
    w.show()
    tree = w.study
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    cfg = tmp_path / 'b.toml'
    cfg.write_text(
        'board = "b.kicad_pcb"\n\n[study]\nrun = ["dc", '
        '"thermal"]\n[study.thermal]\nengine = ["fake", '
        '"Elmer"]\n\n[analysis.pdn]\nrail = "+3V3"\n\n[analysis.thermal]\nloss '
        '= ["Q1=0.5", "L1=0.2"]\n\n[analysis.elmer_thermal]\nloss = ["Q1=0.5", '
        '"L1=0.2"]\n')
    assert w.setup.load(str(cfg))
    assert (tree.asked() == dict(dc=[], thermal=['thermal', 'elmer_thermal'])
            and tree.ticked() == ['pdn', 'thermal', 'elmer_thermal'])
    assert (tree.current_question() == 'dc'
            and tree.rows['thermal'].text(1) == 'fake + Elmer  ▾')
    pdn, th, el = (w.pages[k] for k in ('pdn', 'thermal', 'elmer_thermal'))
    w.run()
    run_to_the_end(w, app)
    assert all(
        tree.state(k).startswith('done ')
        for k in ('pdn', 'thermal', 'elmer_thermal'))
    assert (tree.rows['thermal'].text(3).startswith('done ')
            and 'Elmer: done' in tree.rows['thermal'].toolTip(3))
    # the drop alone was asked
    mark = runs.marked(pdn.out)  # and the config and settings it ran with
    assert (dict(mark, config=None, settings=None, ended=None) == dict(
        engine='pdn', questions=('dc', ), preview=False, config=None,
        settings=None, outcome='done', ended=None)
            and mark['config'] == str(cfg)
            and mark['settings']['rail'] == '+3V3')

    assert (analysis_of(th.out) == ('thermal', False)
            and analysis_of(el.out) == ('elmer_thermal', False))
    assert (os.path.basename(el.out).startswith('b_elmer_thermal_losses_')
            and runs.marked(el.out)['questions'] == ('thermal', ))
    r = w.results  # the last one's report, the other beside it
    assert r.kind() == 'elmer_thermal' and r.modes() == ['Report', 'Tables']
    titles = [t for t, _ in r.reportview._tables]
    assert (titles[0] == 'Engines compared: Temperature'
            and titles[1:] == ['Parts', 'Layers'])
    table = r.reportview._tables[0][1]
    assert table['columns'] == ['figure', 'unit', 'fake', 'Elmer', 'difference']
    assert (table['rows'] == [['hottest', 'C', 75.0, 77.0, 2.0],
                              ['Q1', 'C', 75.0, 77.0, 2.0],
                              ['L1', 'C', 45.0, 47.0, 2.0]])
    text = r.reportview.page.toPlainText()
    assert ('Engines compared: Temperature' in text
            and os.path.basename(th.out) in text)
    w.show_run(pdn.out)  # a question one engine answered: nothing beside it
    assert [t for t, _ in r.reportview._tables] == ['Loads at DC']
    tree.choose('thermal', 'thermal', False)  # Elmer alone, saved as such
    assert w.setup.dirty and w.setup.save()
    st = study.read_config(str(cfg))
    assert (st.asked == dict(dc=[], thermal=['elmer_thermal'])
            and '[analysis]\nrun' not in cfg.read_text())
    w.close()


def test_the_runs_are_read_once_for_a_settings_change(window, tmp_path,
                                                      monkeypatch):
    """What each analysis still needs asks the latest results, once per analysis
    and row: a run folder is read once while it is unchanged (each ask had read
    them all), again once a run is added."""
    from kicad2ems.core import runs
    import fake_analyses as FA
    w, app, _ = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    out = tmp_path / 'out'
    w.setup.set_values(dict(board=str(board), out=str(out)))
    first = FA.write(
        'circuit', runs.tool_dir(str(out), str(board), 'circuit', 'buck'))
    w.update_needed()  # (the board chosen: its runs listed)
    reads, read = [], runs._read
    monkeypatch.setattr(runs, '_read',
                        lambda d, *a: reads.append(d) or read(d, *a))
    w.refresh_runs()
    assert w.latest_results() == dict(circuit=first) == w.latest_results()
    w.update_needed()
    assert reads == [], reads
    t = (__import__('datetime').datetime.now()
         + __import__('datetime').timedelta(minutes=1))
    second = FA.write(
        'circuit',
        runs.tool_dir(str(out), str(board), 'circuit', 'buck', now=t))
    assert w.latest_results() == dict(circuit=second) and reads == [second]
    w.refresh_runs()
    assert reads == [second]


def test_one_analysis_starts_from_another_s_result(window, tmp_path):
    """What an analysis is given of the others' results (the temperature, a
    circuit's losses): the one shown, else the newest; and what a result
    proposes for another analysis (a converter's input rail), with what it could
    not say to be set before a run."""
    from kicad2ems.core import runs
    import fake_analyses as FA
    w, app, _ = window
    pdn, mqs, th = w.pages['pdn'], w.pages['mqs'], w.pages['thermal']
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.run_study(only='thermal')  # no losses, no circuit run
    assert (w.job is None
            and 'run the Circuit analysis first' in w.study.state('thermal'))
    t = __import__('datetime').datetime(2026, 11, 11, 1, 0, 0)
    old = FA.write(
        'circuit',
        runs.tool_dir(str(tmp_path / 'out'), str(board), 'circuit',
                            'buck', now=t))
    new = FA.write(
        'circuit',
        runs.tool_dir(str(tmp_path / 'out'), str(board), 'circuit',
                            'buck', now=t.replace(minute=5)))
    assert w.latest_results() == dict(circuit=new)
    w.run_study(only='thermal')
    run_to_the_end(w, app)
    assert os.path.basename(th.out).startswith('b_thermal_circuit_')
    assert (f"losses of {os.path.basename(new)}"
            in w.results.reportview.page.toPlainText())
    w.refresh_runs()
    w.show_run(old)  # the one shown: that one
    assert w.latest_results()['circuit'] == old
    # a near field's rail for the power integrity: its voltage unknown, so asked
    # for
    mqs.set_values(dict(converter='U7', vin='VBUS'))
    mqs.set_own(str(board), None)  # a board of its own: the rail's too
    w.run_study(only='mqs')
    run_to_the_end(w, app)
    (b,) = mqs.suggest_btns
    assert b.text() == 'Copy the input rail to Power integrity'
    pdn.set_values(dict(load='U1=2', scale=3))
    b.click()
    assert (pdn.values()['rail'] == 'VBUS' and pdn.values()['load'] == ''
            and pdn.values()['scale'] == '')
    assert (pdn.board.text() == str(board) and pdn.required == {'volts'}
            and w.study.current() == 'pdn')
    # proposed for it: asked, so Run includes it
    assert {'dc', 'pdn'} <= set(w.study.asked())
    assert pdn.status.text() == 'The input rail is copied.' and w.setup.dirty
    w.run_study(only='pdn')
    assert (
        w.job is None and
        w.study.state('pdn').startswith('not started: Set volts before Run'))
    pdn.set_values(dict(volts=12))
    w.run_study(only='pdn')
    run_to_the_end(w, app)
    assert w.study.state('pdn').startswith('done ')
    w.run_study(only='mqs', preview=True)  # a preview: its mesh, in the plan
    run_to_the_end(w, app)
    assert w.viewer.view() == 'Plan' and mqs.status.text() == 'preview ready'
    assert len(mqs.suggest_btns) == 1  # a preview knows the rail too
    w.setup.dirty = False
    w.close()


def test_the_study_dock_closes_and_comes_back(window):
    """The study's dock closes from View (and by its key) and comes back the
    same way; closed when the window was, it is there again in the next one, the
    study being what a window opens on."""
    w, app, MainWindow = window
    w.show()
    app.processEvents()
    act = w.fold_acts['Questions']
    assert act.isEnabled() and act.isChecked() and not w.study_dock.isHidden()
    act.trigger()
    assert w.study_dock.isHidden() and not act.isChecked()
    act.trigger()  # View > Questions: back
    assert not w.study_dock.isHidden() and w.setup.isAncestorOf(w.study)
    act.trigger()
    w.close()  # saved with it closed
    again = MainWindow()
    try:
        again.show()
        app.processEvents()
        assert (not again.study_dock.isHidden()
                and again.fold_acts['Questions'].isChecked())
    finally:
        again.system.stop()
        again.meters.stop()
        again.close()


def test_the_few_settings_first_and_the_rest_found(window):
    """The settings a run is set up with are in view (the registry says which:
    an option's level, a solver's `basic`), those of a solver while it is
    chosen; the others are under "More settings", in their sections, and found
    by a word of their label, their key or their section. The board's: its file
    and extra ports first."""
    from kicad2ems.gui.runner import basic_keys
    w, app, _ = window
    w.show()
    app.processEvents()
    st = w.setup
    first = [k for k, (frm, _) in st.rows.items() if frm is st.basic_form]
    assert (first[:3] == ['fmin', 'fmax', 'points']
            and set(first) | {'board', 'ports'}
            == basic_keys() & set(st.rows) | {'board', 'ports'})
    # the board's own
    assert (st.rows['board'][0] is not st.basic_form
            and st.rows['pos'][0] is st.board_more_form)
    assert (not st.more.expanded
            and st.more.head.text().startswith('More settings'))
    assert st.find.placeholderText() == 'Find a setting'
    solver = next((k for k in first
                   if st.group_of[k].split('/')[0] in ('FDTD', 'mom25d')), None)
    if solver:  # a solver's, in view while it is chosen
        top = st.group_of[solver].split('/')[0]
        assert st.field_labels[solver].text().startswith(top + ': ')
        for name, box in st.solver_boxes.items():
            box.setChecked(False)
        st._update_sections()
        assert not st.basic_form.isRowVisible(st.rows[solver][1])
    st.set_values(dict(fmin=1, fmax=2, air=5))
    # those in view are not counted: they are seen
    assert st.more.head.text() == 'More settings  (1 set)'
    hidden = lambda key: not st.rows[key][0].isRowVisible(st.rows[key][1])
    st.find.setText('sweep')
    assert (not hidden('sweep') and st.sections['Frequency'].expanded
            and not st.sections['Frequency'].isHidden())
    # (the first few stay)
    assert st.sections['Output'].isHidden() and not hidden('fmin')
    # a section's name: all of it
    st.find.setText('output')
    assert (not st.sections['Output'].isHidden() and not hidden('out')
            and st.sections['Frequency'].isHidden())
    st.find.setText('')
    assert (not st.sections['Frequency'].isHidden()
            and not st.sections['Frequency'].expanded and not hidden('sweep'))
    st.focus('sweep')
    assert st.more.expanded and st.sections['Frequency'].expanded
    st.focus('pos')
    assert st.sections['Board'].expanded and st.board_more.expanded
    w.setup.dirty = False


def test_still_needed_and_what_run_says(window, tmp_path):
    """What a run still needs is listed under the selected question's settings,
    each line leading to where it is set, and the Run button (it reads Run,
    always) goes to the first of them instead of starting, its tooltip saying
    which; with nothing missing the tooltip says what it will run, and the
    estimate beside it how many solvers. An analysis says what it needs itself
    (Analysis.missing: what its command raises, by default)."""
    w, app, _ = window
    w.show()
    app.processEvents()
    need, run = w.setup.needed, w.acts['Run']
    w.update_needed()
    assert (run.text() == 'Run' and 'choose the board' in run.toolTip()
            and [m['text'] for m in need.items] == ['choose the board'])
    assert not need.isHidden() and need.title.text() == 'Still needed'
    w.run()  # goes there, starts nothing
    assert (not w.busy() and w.setup.sections['Board'].expanded and
            'still needs: choose the board' in w.statusBar().currentMessage())
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.update_needed()
    assert (run.text() == 'Run' and need.items == []
            and 'S-parameters' in run.toolTip())
    w.setup.set_values(dict(board=str(board), fmin=5, fmax=2))
    w.update_needed()
    assert (run.text() == 'Run' and 'f max is not above f min' in
            run.toolTip().lower() and need.items[0]['key'] == 'fmax')
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.study.set_ticked(['rf', 'pdn'])  # an analysis that needs its rail named
    w.study.select('dc')
    w.update_needed()
    assert (run.text() == 'Run'
            and [m['run'] for m in need.items] == ['pdn'])
    assert run.toolTip().startswith(
        'Power integrity still needs: name the rail')
    w.study.select('sparams')  # the list: the selected question's
    assert (need.items == [] and run.text() == 'Run'
            and 'name the rail' in run.toolTip())
    w.pages['pdn'].set_values(dict(rail='+3V3'))
    w.update_needed()
    assert (run.text() == 'Run' and
            run.toolTip().startswith('Run S-parameters, then Power integrity'))
    # beside it: the solvers it uses (no preview yet: no estimate)
    assert w.est_btn.text().startswith('No estimate · 2 solvers')
    assert 'auto, Power integrity' in w.est_btn.toolTip()
    w.study.set_ticked(['thermal'])  # the temperature alone: it has no losses
    w.study.select('thermal')
    w.update_needed()
    assert ('losses' in need.items[0]['text']
            and run.toolTip().startswith('Thermal still needs: give the parts'))
    assert w.est_btn.text() == 'No estimate · 1 solver\nThermal'
    # with the circuit asked, which runs first and will give them: nothing to
    # ask for
    w.study.set_ticked(['circuit', 'thermal'])
    w.update_needed()
    assert ([m for m in need.items if m['run'] == 'thermal'] == []
            and w.needs(['circuit', 'thermal'])['thermal'] == [])
    # a new circuit is blank: what it lacks (the steps bar says it too)
    assert (w.pages['circuit'].is_blank() and w._needs_all[0]['text'].
            startswith('The circuit is blank'))
    from kicad2ems.gui.steps import steps_of
    (setup,) = [s for s in steps_of(w) if s['key'] == 'setup']
    assert 'still needed: The circuit is blank' in setup['text']
    assert w.est_btn.text() == 'No estimate · 2 solvers\nCircuit + Thermal'
    w.study.set_ticked([])
    w.update_needed()
    assert run.text() == 'Run' and 'Tick a question' in run.toolTip()
    assert w.est_btn.text() == 'No estimate\nnothing asked'
    w.setup.dirty = False


def test_the_window_s_own_run_starts_and_ends(window, tmp_path, monkeypatch):
    """Preview and Run start kicad2ems's own run (MainWindow.start: a Start page
    called `start` once hid it), its events reach the Log, and the window is
    idle after; a start that raises says so on its row and leaves the window
    idle too (it stayed busy, so every later Run said "a run is going")."""
    import sys
    from kicad2ems.core import runs
    w, app, _ = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    script = tmp_path / 'fake_run.py'
    script.write_text(
        "import json, sys\nev = sys.argv[sys.argv.index('--events') + 1]\nwith "
        "open(ev, 'a') as f:\n    f.write(json.dumps(dict(kind='log', "
        "text='fake run here')) + '\\n')\n    "
        "f.write(json.dumps(dict(kind='done', code=0)) + '\\n')\n")
    monkeypatch.setattr(
        runs.Run, 'argv', lambda self:
        [sys.executable,
         str(script), self.toml, '--events', self.events])
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.study.set_ticked(['rf'])
    w.preview()
    assert w.job == 'rf' and w.runner.proc is not None
    run_to_the_end(w, app)
    assert not w.busy() and 'fake run here' in w.runtab.log.toPlainText()
    monkeypatch.setattr(type(w), 'start', lambda self, *a: 1 / 0)
    w.preview()
    assert not w.busy() and 'ZeroDivisionError' in w.study.state('rf')
    # its traceback in the log
    assert 'ZeroDivisionError' in w.runtab.log.toPlainText()


def test_stop_asks_the_run_then_ends_its_process(window, tmp_path, monkeypatch):
    """Stop, pressed once, asks kicad2ems's own run to stop (stop_runs: it ends
    after its current step) and says so; pressed again it ends the process at
    once, and the window is idle after (R-C8: the two-press Stop was
    untested)."""
    import sys
    from kicad2ems.core import runs
    w, app, _ = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    script = tmp_path / 'slow_run.py'  # a run that never ends of itself
    script.write_text(
        "import json, sys, time\nev = sys.argv[sys.argv.index('--events') + "
        "1]\nopen(ev, 'a').write(json.dumps(dict(kind='log', text='slow run "
        "here')) + '\\n')\ntime.sleep(600)\n")
    monkeypatch.setattr(
        runs.Run, 'argv', lambda self:
        [sys.executable,
         str(script), self.toml, '--events', self.events])
    asked = []
    # (Stop is the launcher's: runprocess.py)
    import kicad2ems.gui.runprocess as rp
    monkeypatch.setattr(rp, 'stop_runs', lambda out: asked.append(out))
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.study.set_ticked(['rf'])
    w.preview()
    assert (w.busy() and wait_until(
        lambda: 'slow run here' in w.runtab.log.toPlainText(), 20))
    w.stop()
    assert (asked == [w.current.out] and w.busy()
            and 'press Stop again' in w.runtab.log.toPlainText())
    w.stop()
    assert (wait_until(lambda: not w.busy(), 20)
            and 'ending the process' in w.runtab.log.toPlainText())


def test_a_half_typed_number_is_said_not_frozen(window):
    """A field that is not a number (fmin = "1e"): left out of the values,
    marked, listed as still needed, and Run says so (values() had raised from a
    timer: the list and the Run button froze, saying nothing)."""
    w, app, _ = window
    w.setup.edits['fmin'][0].setText('1e')
    vals = w.setup.values()
    assert 'fmin' not in vals and w.setup.bad == {'fmin': '1e'}
    needs = w.needs(['rf'])['rf']
    assert needs[0]['key'] == 'fmin' and 'not a number' in needs[0]['text']
    w.setup.edits['fmin'][0].setText('1.5')
    assert w.setup.values()['fmin'] == 1.5 and not w.setup.bad


def test_another_board_lists_its_own_runs(window, tmp_path):
    """Results of follows the board: after another board is chosen it lists that
    board's runs, and a run of the last one leaves the pages (they had kept it,
    shown under the new study)."""
    from kicad2ems.core import runs
    import fake_analyses as FA
    w, app, _ = window
    a, b = tmp_path / 'a.kicad_pcb', tmp_path / 'b.kicad_pcb'
    a.write_text('')
    b.write_text('')
    w.setup.set_values(dict(board=str(a), out=str(tmp_path / 'out')))
    w.want_layout()
    run = FA.write(
        'pdn', runs.tool_dir(str(tmp_path / 'out'), str(a), 'pdn', '3V3'))
    w.refresh_runs()
    listed = lambda: [r.folder for r in w.runs_panel.runs()]
    assert run in listed()
    w.viewer.out = run
    w.setup.set_values(dict(board=str(b), out=str(tmp_path / 'out')))
    w.want_layout()
    assert run not in listed() and w.viewer.out is None


def test_a_failed_run_stays_listed_with_its_log_and_can_be_deleted(
        window, tmp_path, monkeypatch):
    """R-E24: a run that ends with an error and no results stays in Results of
    ("failed, code 1"), its whole output in its folder (stdout.log) and its log
    shown when it is picked; Delete run removes its folder once asked."""
    import sys
    from kicad2ems.core import runs
    from PySide6.QtWidgets import QMessageBox
    w, app, _ = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    script = tmp_path / 'bad_run.py'
    script.write_text(
        "import json, sys\nev = sys.argv[sys.argv.index('--events') + "
        "1]\nopen(ev, 'a').write(json.dumps(dict(kind='log', text='it went "
        "wrong here')) + '\\n')\nprint('a line of its own "
        "output')\nsys.exit(1)\n")
    monkeypatch.setattr(
        runs.Run, 'argv', lambda self:
        [sys.executable,
         str(script), self.toml, '--events', self.events])
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.study.set_ticked(['rf'])
    w.run()
    folder = w.current.out
    run_to_the_end(w, app)
    assert ('a line of its own output'
            in (tmp_path / 'out' / os.path.basename(folder)
                / 'stdout.log').read_text())
    (r, ) = [r for r in w.runs_panel.runs() if not r.preview]
    assert r.folder == folder and r.outcome == 'failed, code 1'
    assert w.runs_panel.tree.topLevelItem(0).text(3) == 'failed, code 1'
    assert r.took is not None and r.size > 0
    w.runtab.log.clear()
    w.show_run(folder)
    assert ('it went wrong here' in w.runtab.log.toPlainText()
            and 'failed, code 1' in w.runtab.stage.text())
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    # asked, and told no
    assert not w.delete_runs([folder]) and os.path.isdir(folder)
    monkeypatch.setattr(QMessageBox, 'question',
                        lambda *a, **k: QMessageBox.Yes)
    assert w.delete_runs([folder]) == 1 and not os.path.exists(folder)
    assert folder not in [r.folder for r in w.runs_panel.runs()]


def test_a_run_that_cannot_start_is_said_and_leaves_the_window_idle(
        window, tmp_path, monkeypatch):
    """R-E25: no process (a program that is not there): the Log says why and the
    window is idle (FailedToStart has no finished signal: it had stayed
    busy)."""
    from kicad2ems.core import runs
    w, app, _ = window
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    monkeypatch.setattr(
        runs.Run, 'argv',
        lambda self: [str(tmp_path / 'no-such-program'), self.toml])
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.study.set_ticked(['rf'])
    w.run()
    assert (wait_until(lambda: not w.busy(), 20)
            and 'could not start' in w.runtab.log.toPlainText())


def test_a_dropped_board_or_config_is_taken(window, tmp_path, monkeypatch):
    """R-E25: a .kicad_pcb dropped on the window becomes the board, a .toml
    opens, anything else is refused."""
    from PySide6.QtCore import QUrl, QMimeData
    w, app, _ = window
    board = tmp_path / 'x.kicad_pcb'
    board.write_text('')

    class Drop:
        def __init__(self, path):
            self.m = QMimeData()
            self.m.setUrls([QUrl.fromLocalFile(str(path))])
            self.taken = False

        def mimeData(self):
            return self.m

        def acceptProposedAction(self):
            self.taken = True
    d = Drop(board)
    w.dragEnterEvent(d)
    assert d.taken
    w.dropEvent(Drop(board))
    assert w.setup.values()['board'] == str(board)
    opened = []
    monkeypatch.setattr(type(w), '_open', lambda self, p: opened.append(p))
    w.dropEvent(Drop(tmp_path / 'c.toml'))
    assert opened == [str(tmp_path / 'c.toml')]
    other = Drop(tmp_path / 'notes.txt')
    w.dragEnterEvent(other)
    assert not other.taken


def test_an_exception_in_a_slot_is_shown_not_only_printed(window, capsys):
    """R-E25: sys.excepthook shows the traceback in a dialog that does not wait,
    and still on stderr."""
    import sys
    from kicad2ems.gui.app import install_excepthook
    from PySide6.QtWidgets import QApplication, QMessageBox
    w, app, _ = window
    before = sys.excepthook
    try:
        hook = install_excepthook()
        try:
            raise ValueError('a slot went wrong')
        except ValueError:
            hook(*sys.exc_info())
        boxes = [
            b for b in QApplication.topLevelWidgets()
            if isinstance(b, QMessageBox) and b.isVisible()
        ]
        assert any('a slot went wrong' in b.text() for b in boxes)
        for b in boxes:
            b.close()
    finally:
        sys.excepthook = before


@pytest.fixture
def xt_window(tmp_path, monkeypatch):
    """The window with kicad2ems's own crosstalk question beside the
    stand-ins, openEMS not installed for it, and its command a stand-in's
    child process that writes the arguments it was given."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    import json
    import sys
    from kicad2ems.core import analyses
    from kicad2ems.core.si import Crosstalk
    command = Crosstalk.command

    def child(self, values, board, out, preview=False, results=None):
        argv = command(self, values, board, out)
        code = ("import json, os, sys; os.makedirs(sys.argv[1], exist_ok="
                "True); json.dump(sys.argv[2:], open(os.path.join(sys.argv[1],"
                " 'argv.json'), 'w'))")
        return [sys.executable, '-c', code, out, *argv[1:]]
    monkeypatch.setattr(Crosstalk, 'command', child)
    monkeypatch.setattr(
        Crosstalk, 'check_with', lambda self, sets:
        (False, 'no openEMS') if sets['check'] == 'openems' else (True, ''))
    reg = dict(analyses.registry(), crosstalk=Crosstalk())
    monkeypatch.setattr(analyses, '_registry', reg)
    from PySide6.QtCore import QSettings
    from kicad2ems.gui import application, study as gstudy
    from kicad2ems.gui.app import MainWindow
    monkeypatch.setattr(gstudy, 'TREE', gstudy.tree())
    app = application()
    QSettings('kicad2ems', 'gui').clear()
    w = MainWindow()
    yield w, app, json
    w.setup.dirty = False
    w.system.stop()
    w.meters.stop()
    w.close()
    QSettings('kicad2ems', 'gui').clear()


def test_the_crosstalk_s_full_wave_solvers_are_its_engines(xt_window,
                                                            tmp_path):
    """Answered by: the line model, or a full-wave solver beside it, one at a
    time (one run: the analysis with its check set), openEMS not offered where
    it is missing; the setting is the engine's, not the form's; saved and read
    back as [study.crosstalk] engine, a config's check of before read as that
    engine; Run gives the analysis --solvers lines,<it> and marks the folder
    with the engine."""
    from kicad2ems.core.study import Study
    from kicad2ems.core import study
    from kicad2ems.core.runs import marked
    w, app, json = xt_window
    tree, xt = w.study, w.pages['crosstalk']
    texts = lambda q: [(a.text(), a.isEnabled())
                       for a in tree.engine_menu(q).actions()
                       if not a.isSeparator()]
    assert (texts('crosstalk') == [
        ('auto (line model)', True),
        ('line model  (kicad2ems.core.si)', True),
        ('line model + gfdtd  (kicad2ems.core.si)', True),
        ('line model + openEMS  (kicad2ems.core.si)', False),
        ('line model + mom25d  (kicad2ems.core.si)', True)
    ])
    assert 'check' not in xt.w
    tree.set_study(Study({'crosstalk': []}))
    assert tree.rows['crosstalk'].text(1) == 'line model  ▾'
    tree.choose('crosstalk', 'crosstalk_gfdtd', True)
    tree.choose('crosstalk', 'crosstalk_mom25d', True)  # in its place
    assert (tree.choice['crosstalk'] == ['crosstalk_mom25d']
            and tree.rows['crosstalk'].text(1) == 'line model + mom25d  ▾')
    assert (tree.jobs() == ['crosstalk']
            and tree.panels_of('crosstalk') == ['crosstalk']
            and tree.sets_of('crosstalk') == dict(check='mom25d')
            and tree.engine_of('crosstalk') == 'crosstalk_mom25d')
    cfg = tmp_path / 'xt.toml'
    cfg.write_text(f"board = '{tmp_path / 'b.kicad_pcb'}'\n", encoding='utf-8')
    xt.set_values(dict(nets='D0\nD1'))
    w.save_analyses(str(cfg))
    text = cfg.read_text(encoding='utf-8')
    assert ('engine = "crosstalk_mom25d"' in text and 'check' not in text
            and study.read_config(str(cfg)).asked == {
                'crosstalk': ['crosstalk_mom25d']})
    old = tmp_path / 'old.toml'
    old.write_text("[study]\nrun = ['crosstalk']\n[analysis.crosstalk]\n"
                   "nets = ['D0', 'D1']\ncheck = 'gfdtd'\n", encoding='utf-8')
    w.load_analyses(str(old))
    assert (tree.choice['crosstalk'] == ['crosstalk_gfdtd']
            and xt.values()['nets'].split() == ['D0', 'D1'])
    board = tmp_path / 'b.kicad_pcb'
    board.write_text('')
    w.setup.set_values(dict(board=str(board), out=str(tmp_path / 'out')))
    w.run()
    run_to_the_end(w, app)
    argv = json.load(open(os.path.join(xt.out, 'argv.json')))
    assert (argv[argv.index('--solvers') + 1] == 'lines,gfdtd'
            and marked(xt.out)['engine'] == 'crosstalk_gfdtd')
    tree.choose('crosstalk', 'crosstalk', True)  # the line model alone
    w.run()
    run_to_the_end(w, app)
    assert '--solvers' not in json.load(open(os.path.join(xt.out,
                                                          'argv.json')))
