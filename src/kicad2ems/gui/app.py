"""
kicad2ems GUI (PySide6 + pyqtgraph): set up a study, preview it, watch it, see
the results. The window has the study on the left (the board, the analyses made
of it and the settings of the one selected: study.py) and its pages:

    Viewer   the board in 3D or from above, a solver's mesh, the line model
             coloured by Z0 (pick a section to read it), field dumps in place or
             as cuts (live while the run goes), the far field, the run's plots
    Run      the analyses of the run and where each is, openEMS's energy decay
             and mom25d's adaptive sweep, the log of each
    Results  S-parameters and antenna results as Cartesian, Smith and polar
             plots you add and remove; an analysis's result as its curves, its
             report to read, or its tables to sort and export
    Filter Synthesis
             design a microstrip filter with dfsynth, validate and tune it on
             the solvers, open its board here
    Circuit  the circuit analysis's model as a schematic to edit
             (pcbqs.circuit), when that analysis is installed

The analyses beside S-parameters are those kicad2ems's registry holds
(kicad2ems.core.api.frontend.analyses: pcbqs's power integrity, near field,
circuit and thermal). The window knows none of them by name: each one's settings
are a panel in the study made from its options (analysispanel.py), the toolbar's
Run starts its command with the rest, its maps are field dumps the Viewer shows
and its report and curves are on the Results page.

Run starts the analyses ticked in the study one after the other (run_study),
each after the one it starts from. Every run is the command-line tool started as
a separate process with --events; the GUI follows its event stream, so a closed
window never kills a simulation's results and the GUI needs no openEMS of its
own. The window's size, its docks and splitters, and the Results plots are
remembered between sessions.

    kicad2ems-gui [config.toml]

The window is MainWindow here, made of a part a job (each a class of its own
module, MainWindow's methods as they were): windocks.py its pages, toolbar and
menus; winconfigs.py configs and examples; winstudy.py the study, what a run
still needs and picking on the board; winrun.py the runs as they go;
winresults.py this board's runs and the one shown; winparts.py the small
windows and threads.
"""
import sys
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtWidgets import QMainWindow, QWidget, QMessageBox, QTabWidget
from .analysispanel import AnalysisPanel
from .systempanel import SystemPanel
from .viewer import Viewer
from .statusbar import MemoryMeters
from .filtertab import FilterTab
from .study import ANALYSES, BY_KEY
# (the window's parts, in modules of their own: R-F2)
from .setuptab import SetupTab, editor_page
# (run_notes: as before, app.run_notes)
from .runtab import RunTab, run_notes  # noqa: F401
from .resultstab import ResultsTab
from . import theme
# (LOWER, BESIDE: as before, app.LOWER and app.BESIDE)
from .windocks import (  # noqa: F401
    WindowDocks, EDITORS, PANELS, TITLES, LOWER, BESIDE, STATE_VERSION)
from .winconfigs import WindowConfigs
from .winstudy import WindowStudy
from .winrun import WindowRun
from .winresults import WindowResults
from .winparts import SolversDialog, PageDock, _READING

pg.setConfigOptions(antialias=True, background='w', foreground='k')


class MainWindow(WindowStudy, WindowDocks, WindowConfigs, WindowRun,
                 WindowResults, QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        # a setting picked on the board (start_pick)
        self._picking, self.drawn = None, None
        self._layout_stamp, self._layout_bad = None, False
        self.setWindowTitle('kicad2ems')
        # a board, a config or a results folder dropped
        self.setAcceptDrops(True)
        self.qsettings = QSettings('kicad2ems', 'gui')
        # before the plots are made (R-E25)
        theme.set_choice(str(self.qsettings.value('view/plots', 'system')))
        self.proc, self.tail, self.files, self.current, self.ticks = (None,
                                                                      None, [],
                                                                      None, 0)
        self._traceback = self._stopping = False
        self.setup, self.runtab, self.results = (SetupTab(self), RunTab(self),
                                                 ResultsTab(self))
        self.viewer = Viewer(self)
        from .startpage import StartPage
        # where a window with no board begins (not `start`: that is the method
        # that starts kicad2ems's run)
        self.start_page = StartPage(self)
        # (the pages that stepped aside for Start)
        self._had_board, self._aside = None, None
        self.filter = FilterTab(self)
        # what runs each analysis of the registry: a settings panel made from
        # its options (analysispanel.py), or, for the one this window has an
        # editor for (the circuit's schematic), that page
        from kicad2ems.core.api.frontend import analyses
        self.registry = analyses()
        self.panels, self.editors = {}, {}
        for a in ANALYSES:
            analysis = self.registry.get(a.key)
            if analysis is None:
                continue
            if a.page and a.key in EDITORS.values():
                self.editors[a.key] = editor_page(a.key, self)
                # its settings: a config holds them too
                if hasattr(self.editors[a.key], 'panel'):
                    self.editors[a.key].panel.changed.connect(
                        lambda: self.setup.touch())
            else:
                self.panels[a.key] = AnalysisPanel(analysis, self)
                # a config holds its settings
                self.panels[a.key].changed.connect(lambda: self.setup.touch())
        self.pages = {
            a.key: (self.panels.get(a.key) or self.editors.get(a.key))
            for a in ANALYSES
            if a.key in self.panels or a.key in self.editors
        }
        self._study()
        # the field views, inside the viewer
        self.fields, self.volume = self.viewer.cuts, self.viewer.volume
        # its Far field section steers too
        if getattr(self.volume, 'steer', None) is not None:
            self.results.link_steering(self.volume.steer)
        # the Solvers window's content: checked now, so the tick boxes can say
        # what cannot run
        self.system = SystemPanel()
        self.system.ready.connect(self.setup.set_availability)
        # the status bar: RAM, GPU, the run's memory
        self.meters = MemoryMeters()
        self.system.ready.connect(self.meters.set_info)
        self.system.ready.connect(lambda *_: self.update_estimate())
        self._est_basis = None  # the settings the estimates were made with
        self.solvers_dialog = SolversDialog(self, self.system)
        # docks, as a studio's: the study (the board and the questions) and the
        # selected question's settings on the left, one over the other; the
        # pages in a workspace of their own: the board's Viewer with the editors
        # tabbed beside it, the Results and Run pages under it. Each moves,
        # tabs, floats and closes (View).
        docking = (QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks
                   | QMainWindow.AllowNestedDocks)
        self.setDockOptions(docking)
        self.study_dock = PageDock('Study', 'Study', self.setup, self)
        self.settings_dock = PageDock('Settings', 'Inspector',
                                      self.setup.inspector, self)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.study_dock)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.settings_dock)
        self.work = QMainWindow(objectName='work', dockOptions=docking)
        self.work.setTabPosition(Qt.AllDockWidgetAreas, QTabWidget.North)
        # the pages take the whole workspace
        self.work.setCentralWidget(QWidget(visible=False))
        widgets = {
            'Viewer': self.viewer, 'Run': self.runtab, 'Results': self.results,
            'Filter': self.filter, 'Report': self.results.reportview,
            'Tables': self.results.tablesview, 'Start': self.start_page,
            'Section': self.viewer.section,
            **{BY_KEY[k].page: p
               for k, p in self.editors.items()}
        }
        self.docks = {}
        for name in PANELS:
            # an analysis's page this window has no editor for
            if name not in widgets:
                continue
            d = PageDock(TITLES.get(name, name), name, widgets[name], self.work)
            d.topLevelChanged.connect(
                lambda floating, d=d: floating and QTimer.singleShot(
                    100, lambda: self._fit_floating(d)))
            # (a page in front leaves the selected question, and so the
            # settings, as they are: the list's highlight drives the dock)
            self.docks[name] = d
        # the line model's cross-section: a page under the board, so the plan it
        # is picked on stays in view; there while the folder shown has sections,
        # solved while it is in view (its tab picked)
        self.docks['Section'].visibilityChanged.connect(
            self.viewer.set_section_shown)
        self.viewer.sections.connect(lambda _has: self._section_follows())
        self.places = self.docks  # (a page by its name: where it is)
        self._place_docks()
        self.setCentralWidget(self.work)
        self._toolbar()
        self._menus()
        self.statusBar().addPermanentWidget(self.meters)
        self.timer = QTimer(self, timeout=self.poll)
        self._needs_timer = QTimer(self, singleShot=True,
                                   timeout=self.update_needed)
        self.setup.needed.picked.connect(self.go_to_need)
        self.study.selected.connect(lambda _q: self.update_needed())
        for page in self.pages.values():
            getattr(page, 'panel', page).changed.connect(self.needs_soon)
        self.update_needed()
        # the layout as built, for Reset layout to put back (Qt's own
        # restoreState: docking a floating dock again by hand, removeDockWidget
        # and addDockWidget, made Qt abort on Windows)
        self._built_layout = (self.saveState(STATE_VERSION),
                              self.work.saveState(STATE_VERSION), self._wide())
        self._restore_shape()
        self.update_title()
        self._study_ready = True
        if config:
            self.setup.load(config)
        # no config: the solvers as kicad2ems's default (auto), which the
        # S-parameters row shows
        else:
            self.setup.set_values({})

    def closeEvent(self, ev):
        # kicad2ems's own run: it ends with the window
        if self.proc is not None and self.current is not None:
            kind = 'preview' if self.current.preview else 'run'
            if (QMessageBox.question(
                    self, 'A run is going',
                    f"kicad2ems's {kind} is going: closing the window stops it "
                    "(its folder keeps what it has written). Stop it and "
                    "close?"
            ) != QMessageBox.Yes):
                ev.ignore()
                return
        if self.setup.maybe_save():
            # the shape kept: with the result pages in it
            self._start_leaves(hide=False)
            q = self.qsettings
            q.setValue('window/geometry', self.saveGeometry())
            q.setValue('window/state', self.saveState(STATE_VERSION))
            q.setValue('work/state', self.work.saveState(STATE_VERSION))
            for name, sp in self._splitters().items():
                q.setValue(f"splitter/{name}", sp.saveState())
            # asked above: stopped, as its second Stop does
            if self.proc is not None:
                self.proc.kill()
                self.proc.waitForFinished(3000)
            # the Section page's and the calculator's solves
            self.viewer.section.wait_jobs()
            if getattr(self, 'impedance', None) is not None:
                self.impedance.wait_jobs()
            # a tool still running: stopped with the window
            for page in self.pages.values():
                if page.proc is not None:
                    page.stop()
                    if page.proc is not None:
                        page.proc.waitForFinished(3000)
            self.system.stop()
            self.meters.stop()
            self.end_pick()
            self.start_page.stop_pictures()
            for t in list(_READING):  # a board still being read: let it end
                t.wait(15000)
            self.solvers_dialog.close()
            if getattr(self, '_viewer2', None) is not None:
                self._viewer2.close()
            ev.accept()
        else:
            ev.ignore()


def install_excepthook():
    """An exception a slot raises: its traceback on stderr, as before, and in a
    dialog that does not wait (R-E25: A1 had shown nothing in the window)."""
    import traceback
    shown = []

    def hook(etype, value, tb):
        text = ''.join(traceback.format_exception(etype, value, tb))
        sys.__stderr__.write(text)
        try:
            box = QMessageBox(QMessageBox.Critical, 'kicad2ems: an error',
                              f"{etype.__name__}: {value}")
            box.setDetailedText(text)
            box.setModal(False)
            box.show()
            shown.append(box)  # (kept: a dialog not referenced is collected)
            del shown[:-5]
        # (never an error of its own)
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = hook
    return hook


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    from . import application
    app = application(sys.argv[:1])
    install_excepthook()
    w = MainWindow(argv[0] if argv else None)
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
