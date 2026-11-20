"""
The main window's shape (app.py): its pages as docks of the workspace, which of
them are there (the Start page while there is no board, an editor while its
analysis is ticked), the toolbar and the menus, and the layout remembered
between sessions.
"""
import os
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (QDesktopServices, QAction, QActionGroup,
                           QKeySequence, QIcon)
from PySide6.QtWidgets import QToolBar, QToolButton, QStyle
from .viewer import VIEWS
from .statusbar import EstimateButton
from kicad2ems.core.analyses import registry
from . import theme
from .winparts import ViewerWindow

DOCS = 'https://github.com/lowprocess/kicad2ems/blob/main/docs/index.md'
# an analysis with a page of its own here (the circuit's schematic): {page:
# its key}
EDITORS = {a.label: k for k, a in registry().items()
           if a.page and k in ('circuit', )}
# The pages (each a dock of the workspace, by its name here). A result is on
# pages of its own kind, not one page with a switch: 'Results' is its plots (the
# tab reads Plots), Report what an analysis found, to read, Tables the report's
# tables, 'Run' the runs' progress and logs (the tab reads Log), and 'Runs'
# this board's runs, to pick the one shown and those compared
PANELS = ('Viewer', 'Start', 'Results', 'Report', 'Tables', 'Run', 'Runs',
          'Section', 'Filter', 'Draw', *EDITORS)
# a page's tab title, when not its name
TITLES = {'Filter': 'Filter Synthesis', 'Results': 'Plots', 'Run': 'Log'}
# the pages under the board in a new window's layout
LOWER = ('Report', 'Tables', 'Run', 'Runs', 'Section')
BESIDE = ('Results',)                           # and beside it
# pages closed when the window opens: the toolbar's Filter opens it ('Start'
# comes and goes with the board: _start_follows)
ON_DEMAND = ('Filter', 'Draw')
# pages there only while their analysis is ticked (or running): not opened by
# hand otherwise (View has them greyed)
FOLLOWS_TICK = dict(EDITORS)
WAND = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wand.svg')
STATE_VERSION = 4  # saved dock layouts of older windows: ignored


class WindowDocks:
    """MainWindow's docks, toolbar and menus."""

    # ---- the window's shape: remembered between sessions ----
    def _splitters(self):
        return {
            'viewer': self.viewer.split, 'results': self.results.splot.split,
            'filter': self.filter.split, 'draw': self.draw.split,
            **{k: p.split
               for k, p in self.editors.items()},
            **({'viewer3d': self.volume.split} if getattr(
                   self.volume, 'split', None) is not None else {})
        }

    def _place_docks(self):
        """The docks where a new window has them: the study over the settings on
        the left; the Viewer on top with the editors and Filter Synthesis tabbed
        behind it, Results and Run tabbed under it."""
        for d in (self.study_dock, self.settings_dock):
            if d.isFloating():
                d.setFloating(False)
            # out of the layout first, as the workspace's docks below: a
            # floating one docked again in place made Qt abort in addDockWidget
            # on Windows CI (its floating dock a native window)
            self.removeDockWidget(d)
            self.addDockWidget(Qt.LeftDockWidgetArea, d)
            if d.isHidden():
                d.show()
        self.splitDockWidget(self.study_dock, self.settings_dock, Qt.Vertical)
        # room for the plots beside the board; else they are the first tab
        # under it
        wide = self._wide()
        lower = [n for n in (() if wide else BESIDE) + LOWER if n in self.docks]
        upper = [n for n in self.docks if n not in LOWER + BESIDE]
        # (only one that was closed is shown: show() on a dock of a window not
        # yet laid out leaves a second tab bar behind, drawn over the page)
        for name in self.docks:
            d = self.docks[name]
            if d.isFloating():
                d.setFloating(False)
            # (Qt 6.12 tabs a dock with another only when it is in no tab
            # group of its own)
            self.work.removeDockWidget(d)
            if d.isHidden():
                d.show()
        for area, group in ((Qt.TopDockWidgetArea, upper),
                            (Qt.BottomDockWidgetArea, lower)):
            first = self.docks[group[0]]
            self.work.addDockWidget(area, first)
            # the plots: beside the board (before the board has tabs: Qt
            # splits a lone dock, and tabs onto a tabbed one)
            if area == Qt.TopDockWidgetArea and wide:
                for name in BESIDE:
                    self.work.splitDockWidget(first, self.docks[name],
                                              Qt.Horizontal)
            for name in group[1:]:
                self.work.tabifyDockWidget(first, self.docks[name])
        self.docks['Viewer'].raise_()
        self.docks[lower[0]].raise_()

    def _wide(self):
        """Whether the workspace has room for the plots beside the board (1,400
        px of it: a 1,920 px screen has, a 1,700 px window was pushed wider by
        the two pages' least widths): the window's width once it is shown, the
        screen's before."""
        scr = self.screen().availableGeometry().width() if self.screen() else 0
        width = self.width() if self.isVisible() else max(scr, 0)
        left = max(self.setup.minimumWidth() + 40, 420)
        return width - left >= 1400

    def _restore_shape(self):
        """The size, position, docks and splitters of the last session; the
        first time, the whole screen."""
        q = self.qsettings
        geo = q.value('window/geometry')
        self._first_time = geo is None or not self.restoreGeometry(geo)
        if self._first_time:
            scr = self.screen().availableGeometry() if self.screen() else None
            if scr is not None and scr.width() > 0:
                # (showMaximized on the first show: showEvent)
                self.setGeometry(scr)
            else:
                self.resize(1500, 950)
        self._page_visibility(opening=True)
        self._first_sizes()
        for key, obj in (('window/state', self), ('work/state', self.work)):
            st = q.value(key)
            if st is not None:
                # (an older window's layout: another version)
                obj.restoreState(st, STATE_VERSION)
        for name, sp in self._splitters().items():
            st = q.value(f"splitter/{name}")
            if st is not None:
                sp.restoreState(st)
        for d in self.docks.values():
            if d.isHidden():
                d.show()  # a page closed last time: back
        self._page_visibility(opening=True)
        # the study: always there when the window opens
        for d in (self.study_dock, self.settings_dock):
            if d.isHidden():
                d.setFloating(False)
                d.show()

    def showEvent(self, ev):
        super().showEvent(ev)
        # no session before this one: the whole screen
        if getattr(self, '_first_time', False):
            self._first_time = False
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
        if not getattr(self, '_tidied', False):
            self._tidied = True
            QTimer.singleShot(0, self._tidy_tabs)
            # (laid out: the pages step aside)
            QTimer.singleShot(0, lambda: self._start_follows(opening=True))

    def _start_follows(self, opening=False):
        """The Start page: there, in front, with the workspace to itself (and
        the Viewer behind it) while no board is chosen and no result is open:
        the result pages step aside. Out of the way once there is a board (View
        brings it back), and back when the study is emptied (New)."""
        has = bool(str(self.setup.values().get('board') or '').strip())
        if has == self._had_board and not opening:
            return
        self._had_board = has
        if has:
            self._start_leaves()
            return
        self.show_panel('Start')
        # a result is shown: its pages stay
        if self.results.out or self.results.analysis:
            return
        # a window not laid out yet has no sizes to keep: once it is shown
        # (showEvent)
        if not self.isVisible():
            return
        pages = [
            n for n in BESIDE + LOWER
            if n in self.docks and not self.docks[n].isHidden()
            and not self.docks[n].isFloating() and n != 'Section'
        ]
        if pages:  # (again after a layout is put back: it shows them)
            old = self._aside or dict(pages=[], front=[], sizes=None)
            self._aside = dict(
                pages=old['pages']
                + [n for n in pages if n not in old['pages']],
                front=[n for n in pages
                       if self.docks[n].in_front()], sizes=self._work_sizes())
            for n in pages:
                self.docks[n].hide()

    def _start_leaves(self, hide=True):
        """A board was chosen, or a result opened: the Start page goes (hide),
        and the result pages that stepped aside for it come back where they
        were."""
        if hide:
            self.docks['Start'].hide()
        aside, self._aside = self._aside, None
        if not aside:
            return
        for n in aside['pages']:
            self.docks[n].show()
        for n in (aside['front'] or [
                n for n in aside['pages']
                if n not in BESIDE or not self._wide()
        ][:1]):
            self.docks[n].raise_()
        if aside['sizes']:
            self._work_sizes(aside['sizes'])
        else:  # (a layout just reset: its first sizes)
            self._first_sizes(work=True)

    def _work_sizes(self, sizes=None):
        """The workspace's split between the board and the pages under and
        beside it: as it is, or (sizes) put back."""
        top, low, plots = (self.docks['Viewer'], self.docks[LOWER[0]],
                           self.docks['Results'])
        beside = (not plots.isHidden() and not plots.isFloating()
                  and plots not in self.work.tabifiedDockWidgets(low)
                  and plots not in self.work.tabifiedDockWidgets(top))
        if sizes is None:
            under = next((self.docks[n]
                          for n in BESIDE + LOWER
                          if not self.docks[n].isHidden() and (
                              n not in BESIDE or not beside)), low)
            return dict(v=(top.height(), under.height(), under.objectName()),
                        h=(top.width(), plots.width()) if beside else None)
        v, h = sizes['v'], sizes['h']
        under = self.docks.get(v[2], low)
        if v[0] > 0 and v[1] > 0 and not under.isHidden():
            self.work.resizeDocks([top, under], [v[0], v[1]], Qt.Vertical)
        if h and beside and h[0] > 0 and h[1] > 0:
            self.work.resizeDocks([top, plots], [h[0], h[1]], Qt.Horizontal)

    def _section_follows(self):
        """The Section page: there (behind what is in front) while the folder
        shown has a line model with cross-sections, gone when it has none."""
        d = self.docks['Section']
        if self.viewer.has_sections():
            if d.isHidden():
                self.show_panel('Section', front=False)
        elif not d.isHidden():
            d.hide()

    def _page_visibility(self, opening=False):
        """The pages that come and go: Filter Synthesis closed when the window
        opens (the toolbar's Filter, or View, opens it); Circuit there while
        Circuit is asked, and gone when it is not (nor can View open it)."""
        if opening:
            for name in ON_DEMAND:
                self.docks[name].hide()
            self._start_follows(opening=True)
            self._section_follows()
        for name, key in FOLLOWS_TICK.items():
            d, on = self.docks[name], self._page_allowed(name)
            d.toggleViewAction().setEnabled(on)
            if on and d.isHidden() and key in self.study.ticked():
                self.show_panel(name, front=False)
            elif not on and not d.isHidden():
                d.hide()

    def _page_allowed(self, name):
        """Whether a page may be shown: one that follows its analysis's tick
        only while it is ticked, or its run is going or queued."""
        key = FOLLOWS_TICK.get(name)
        return (key is None or key in self.study.ticked() or self.job == key
                or key in self.queue)

    def reset_layout(self):
        """The docks where a new window has them, at their first sizes: the
        layout the window was built with, put back as a saved one is."""
        built = getattr(self, '_built_layout', None)
        floating = [
            d for d in (self.study_dock, self.settings_dock,
                        *self.docks.values()) if d.isFloating()
        ]
        # docked first, and Qt left to finish with the floating windows before
        # the layout is touched: on Windows it aborted in whatever came next
        # (addDockWidget, show, restoreState)
        for d in floating:
            d.setFloating(False)
        if floating:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        # the workspace first: put back after the window's docks, at the size
        # they had left it, Qt crashed laying it out (offscreen, its pages'
        # widths near the window's)
        if (built is None or built[2] != self._wide()
                or not (self.work.restoreState(built[1], STATE_VERSION)
                        and self.restoreState(built[0], STATE_VERSION))):
            # (or a window of another width than it was built with)
            self._place_docks()
            self._built_layout = (self.saveState(STATE_VERSION),
                                  self.work.saveState(STATE_VERSION),
                                  self._wide())
        for d in (self.study_dock, self.settings_dock, *self.docks.values()):
            if d.isHidden():
                d.show()  # a page that was closed: back
        self._aside = None
        self._page_visibility(opening=True)
        # (stepped aside for the Start page again: they come back at their
        # first sizes)
        if self._aside:
            self._aside.update(sizes=None, front=[])
        self._first_sizes()
        QTimer.singleShot(0, self._tidy_tabs)

    def _tidy_tabs(self):
        """Qt keeps the tab bars it no longer uses, shown where an earlier
        layout had them (two tab groups put down before the window had its size:
        a second 'Results | Run' drawn over the board). All hidden, and the
        layout made again: it shows those it uses."""
        from PySide6.QtWidgets import QTabBar
        for win in (self, self.work):
            for bar in win.findChildren(QTabBar,
                                        options=Qt.FindDirectChildrenOnly):
                bar.hide()
            win.layout().invalidate()
            win.layout().activate()
        top, low = (self.docks['Viewer'],
                    next((self.docks[n]
                          for n in LOWER
                          if not self.docks[n].isHidden()),
                         self.docks[LOWER[0]]))
        if (not top.isHidden() and not low.isHidden() and not top.isFloating()
                and not low.isFloating()):
            self.work.resizeDocks(
                [top, low],
                [top.height() + 1, max(low.height() - 1, 1)], Qt.Vertical)

    def _first_sizes(self, work=False):
        """The docks' sizes in a new window (work: only the workspace's, the
        board against the pages under and beside it)."""
        w, h = max(self.width(), 900), max(self.height(), 600)
        left = max(self.setup.minimumWidth() + 40,
                   self.setup.inspector.minimumWidth() + 40, 420)
        if not work:
            self.resizeDocks([self.study_dock, self.settings_dock],
                             [left, left], Qt.Horizontal)
            self.resizeDocks([self.study_dock, self.settings_dock],
                             [int(0.42 * h), int(0.58 * h)], Qt.Vertical)
        under = next((self.docks[n]
                      for n in (() if self._wide() else BESIDE) + LOWER
                      if not self.docks[n].isHidden()), self.docks[LOWER[0]])
        self.work.resizeDocks([self.docks['Viewer'], under],
                              [int(0.60 * h), int(0.40 * h)], Qt.Vertical)
        plots, ww = self.docks['Results'], max(w - left, 600)
        # beside the board
        if (not plots.isHidden() and not plots.isFloating() and plots
                not in self.work.tabifiedDockWidgets(self.docks[LOWER[0]])
                and plots not in self.work.tabifiedDockWidgets(
                    self.docks['Viewer'])):
            self.work.resizeDocks(
                [self.docks['Viewer'], plots],
                [int(0.52 * ww), int(0.48 * ww)], Qt.Horizontal)
        if not work:
            self.viewer.split.setSizes([880, 380])
            self.results.splot.split.setSizes([220, 1000])

    def _toolbar(self):
        tb = QToolBar('Main', objectName='Main toolbar',
                      toolButtonStyle=Qt.ToolButtonTextUnderIcon, movable=False)
        st = self.style()
        self.acts = {}
        for name, icon, tip, slot in (
            ('New', 'SP_FileIcon', 'New settings (Ctrl+N)', self.setup.new),
            ('Open', 'SP_DialogOpenButton', 'Open a config (Ctrl+O)',
             lambda: self.setup.load()),
            ('Save', 'SP_DialogSaveButton', 'Save the config (Ctrl+S)',
             self.setup.save), ('Save as', 'SP_DriveFDIcon',
                                'Save the config as (Ctrl+Shift+S)',
                                self.setup.save_as), (None, None, None, None),
            ('Preview', 'SP_FileDialogContentsView',
             'The ticked analyses\' models and meshes (the line model too), '
             'no solve (Ctrl+P)', self.preview),
            ('Run', 'SP_MediaPlay',
             'Run the ticked analyses, one after the other (Ctrl+R)',
             self.run), ('Stop', 'SP_MediaStop',
                         'Stop the run and what is queued after it (Ctrl+.)',
                         self.stop), (None, None, None, None),
            ('Results', 'SP_DirOpenIcon', 'Open a results folder',
             self.results._load), ('Report', 'SP_FileDialogInfoView',
                                   'Open the HTML report of the results',
                                   self.results.open_report), (None, None, None,
                                                               None),
            ('Filter', WAND,
             'Filter Synthesis: design a microstrip filter with dfsynth',
             lambda: self.show_panel('Filter')),
            ('Draw', 'SP_FileDialogDetailedView',
             'Draw shapes over the board (copper, cuts, solids, ports) for '
             'the runs to add', lambda: self.show_panel('Draw'))):
            if name is None:
                tb.addSeparator()
                continue
            a = self.acts[name] = QAction(
                (QIcon(icon) if os.path.isfile(icon) else st.standardIcon(
                    getattr(QStyle.StandardPixmap, icon))), name, self,
                toolTip=tip, triggered=slot)
            tb.addAction(a)
        self.acts['Stop'].setEnabled(False)
        self.acts['Report'].setEnabled(False)
        # whose results the pages show: the Runs page picks
        self.showing_btn = QToolButton(
            text='Nothing run yet', autoRaise=True,
            toolButtonStyle=Qt.ToolButtonTextOnly,
            clicked=lambda: self.show_panel('Runs'))
        tb.addSeparator()
        tb.addWidget(self.showing_btn)
        # beside Run / Stop: what a run would take
        self.est_btn = EstimateButton()
        self.est_btn.clicked.connect(self.show_solvers)
        acts = tb.actions()
        # before Results' separator
        tb.insertWidget(acts[acts.index(self.acts['Results']) - 1],
                        self.est_btn)
        self.addToolBar(Qt.TopToolBarArea, tb)
        from .steps import StepsBar
        # the study's steps: under the toolbar (it moves to the bottom, and
        # hides: View > Steps)
        self.steps = StepsBar(self)
        self.steps.go.connect(self.go_to_step)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.addToolBar(Qt.TopToolBarArea, self.steps)

    def show_panel(self, name, front=True):
        """Open a page (it may have been closed) and, front: bring it to the
        front of its tab group. A floated page that was closed comes back
        docked: on Wayland a separate window can't be raised or placed by the
        app. A page that follows its analysis's tick, not ticked: not shown
        (False)."""
        if not self._page_allowed(name):
            return False
        d = self.docks[name]
        if d.isFloating() and not d.isVisible():
            d.setFloating(False)
        closed = d.isHidden()
        before = [x for x in self.docks.values() if x is not d and x.in_front()]
        d.show()
        home = self.docks[LOWER[0] if name in LOWER + BESIDE else 'Viewer']
        if (closed and not d.isFloating() and name not in BESIDE
                and d is not home
                and d not in self.work.tabifiedDockWidgets(home)):
            # a page that starts closed: tabbed with its own
            self.work.tabifyDockWidget(home, d)
        if front:
            d.raise_()
        else:  # only there: what was in front stays
            for x in before:
                x.raise_()
        return True

    def show_result(self, front=True):
        """The pages of the result shown: its plots there, and what an analysis
        found, its Report (or, with no report, its plots) in front."""
        r = self.results
        self.show_panel(
            'Results', front=(not (r.analysis and r.analysis_report) and front))
        if r.analysis and r.analysis_report:
            self.show_panel(
                (r.mode if r.mode in ('Report', 'Tables') else 'Report'),
                front=front)

    def _fit_floating(self, d):
        """A page just floated: at most 80 % of the screen, centred on the
        window (a floated page as wide as the whole workspace could open partly
        off the screen)."""
        scr = (self.screen() or d.screen()).availableGeometry()
        w, h = (min(d.width(), int(0.8 * scr.width())),
                min(d.height(), int(0.8 * scr.height())))
        d.resize(w, h)
        c = self.frameGeometry().center()
        d.move(max(scr.x(), min(c.x() - w // 2,
                                scr.right() - w)),
               max(scr.y(), min(c.y() - h // 2,
                                scr.bottom() - h)))

    def second_viewer(self, folder=None):
        """A Viewer window of its own, showing another run of the board
        (ViewerWindow; folder: that one); one at a time."""
        if getattr(self, '_viewer2', None) is None:
            self._viewer2 = ViewerWindow(self)
        self._viewer2.refresh(folder)
        self._viewer2.show()
        self._viewer2.raise_()
        return self._viewer2

    def show_solvers(self):
        """The Solvers window: what can run here, the GPU, the memory, the last
        preview's estimates."""
        self.solvers_dialog.show()
        self.solvers_dialog.raise_()
        self.solvers_dialog.activateWindow()

    def _menus(self):
        fm = self.menuBar().addMenu('&File')
        for text, key, slot in (('&New', QKeySequence.New, self.setup.new),
                                ('&Open…', QKeySequence.Open,
                                 lambda: self.setup.load()), (None, None, None),
                                ('&Save', QKeySequence.Save, self.setup.save),
                                ('Save &as…', QKeySequence.SaveAs,
                                 self.setup.save_as)):
            if text is None:
                self.recent_menu = fm.addMenu('Open &recent')
                self.examples_menu = fm.addMenu('&Examples')
                continue
            fm.addAction(QAction(text, self, shortcut=key, triggered=slot))
        fm.addSeparator()
        fm.addAction(
            QAction(QIcon(WAND), 'New &filter (Filter Synthesis)…', self,
                    triggered=lambda: (self.show_panel('Filter')),
                    toolTip='design a microstrip filter with dfsynth'))
        fm.addAction(
            QAction('&Draw shapes…', self,
                    triggered=lambda: self.show_panel('Draw'),
                    toolTip='arbitrary shapes over the board, in plan and 3D'))
        fm.addAction(
            QAction('Open &results folder…', self,
                    triggered=self.results._load))
        fm.addSeparator()
        fm.addAction(
            QAction('&Quit', self, shortcut=QKeySequence.Quit,
                    triggered=self.close))
        # the settings' changes (R-E25); a field being
        em = self.menuBar().addMenu('&Edit')
        # typed in keeps its own Ctrl+Z
        self.undo_act = QAction('&Undo', self, shortcut=QKeySequence.Undo,
                                triggered=self.setup.undo)
        self.redo_act = QAction('&Redo', self, shortcut=QKeySequence.Redo,
                                triggered=self.setup.redo)
        em.addAction(self.undo_act)
        em.addAction(self.redo_act)
        em.aboutToShow.connect(self._edit_menu)
        self.setup.undo_changed.connect(self._edit_menu)
        self._edit_menu()
        vm = self.menuBar().addMenu('&View')
        self.fold_acts = {}  # the study's two docks, by key
        for name, key, d in (('Questions', 'Ctrl+1', self.study_dock),
                             ('Settings', 'Ctrl+2', self.settings_dock)):
            a = d.toggleViewAction()
            a.setText(name)
            a.setShortcut(QKeySequence(key))
            # back, docked
            a.triggered.connect(
                lambda on, d=d: on and d.isFloating() and d.setFloating(False))
            vm.addAction(a)
            self.fold_acts[name] = a
        vm.addAction(self.steps.toggleViewAction())
        vm.addSeparator()
        for name in PANELS:
            if name not in self.docks:
                continue
            a = self.docks[name].toggleViewAction()
            a.triggered.connect(lambda on, d=self.docks[name]: (
                on and d.isFloating() and d.setFloating(False)))
            vm.addAction(a)
        vm.addSeparator()
        for name in VIEWS:
            vm.addAction(
                QAction(
                    f"Viewer: {name}", self, triggered=lambda _=False, n=name:
                    (self.show_panel('Viewer'), self.viewer.set_view(n))))
        vm.addSeparator()
        vm.addAction(
            QAction(
                'A second &Viewer…', self,
                shortcut=QKeySequence('Ctrl+Shift+V'), toolTip=
                ('another run of this board in a window of its own, beside the '
                 'Viewer'
                 ), triggered=self.second_viewer))
        vm.addSeparator()
        # (kept: PySide frees a submenu it is not held)
        self.plots_menu = vm.addMenu('&Plot colours')
        self.plot_acts = QActionGroup(self)
        self.plot_acts.setExclusive(True)
        for key, text in (('system', 'As the &desktop'), ('light', '&Light'),
                          ('dark', '&Dark')):
            a = QAction(text, self, checkable=True,
                        checked=theme.choice() == key)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self.set_plot_colours(k))
            self.plot_acts.addAction(a)
            self.plots_menu.addAction(a)
        vm.addAction(QAction('&Reset layout', self,
                             triggered=self.reset_layout))
        rm = self.menuBar().addMenu('&Run')
        for text, key, slot in (
            ('&Preview', 'Ctrl+P', self.preview), ('&Run', 'Ctrl+R', self.run),
            ('Run the selected &question alone', 'Ctrl+Shift+R',
             lambda: self.run_study(only=self.study.current_question())),
            ('&Stop', 'Ctrl+.', self.stop)):
            rm.addAction(
                QAction(text, self, shortcut=QKeySequence(key), triggered=slot))
        # what is not about the open board's run
        tm = self.menuBar().addMenu('&Tools')
        tm.addAction(
            QAction(
                '&Impedance calculator…', self, triggered=self.show_impedance,
                toolTip=
                ("A line's impedance in a stack-up, and the width of a target: "
                 "the field solver without a board"
                 )))
        hm = self.menuBar().addMenu('&Help')
        hm.addAction(
            QAction('&Solvers and hardware…', self,
                    triggered=self.show_solvers))
        hm.addAction(
            QAction('&Documentation', self,
                    triggered=lambda: QDesktopServices.openUrl(QUrl(DOCS))))
        self._fill_recent()
        self._fill_examples()

    def _edit_menu(self):
        """Undo / Redo enabled and named for what they would change ('Undo
        fmax')."""
        for act, can, redo in ((self.undo_act, self.setup.can_undo(), False),
                               (self.redo_act, self.setup.can_redo(), True)):
            act.setEnabled(can)
            act.setText(
                self.setup.undo_text(redo) if can else (
                    '&Redo' if redo else '&Undo'))

    def set_plot_colours(self, key):
        """The plots light, dark or as the desktop (theme.py), remembered."""
        theme.set_choice(key)
        self.qsettings.setValue('view/plots', theme.choice())
        for a in self.plot_acts.actions():
            a.setChecked(a.data() == theme.choice())

    def show_impedance(self):
        """Tools > Impedance calculator: one window, kept; the open board's
        layers offered as its stack."""
        from .impedance import ImpedanceCalculator
        board = self.setup.values().get('board') or None
        if getattr(self, 'impedance', None) is None:
            self.impedance = ImpedanceCalculator(self, board)
        elif board != self.impedance.board:
            self.impedance.set_board(board)
        self.impedance.show()
        self.impedance.raise_()
        self.impedance.activateWindow()
        return self.impedance

    def update_title(self):
        name = (os.path.basename(self.setup.path)
                if self.setup.path else 'untitled')
        self.setWindowTitle(
            f"{name}{'*' if self.setup.dirty else ''} - kicad2ems")
