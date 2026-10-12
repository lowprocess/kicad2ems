"""
kicad2ems GUI (PySide6 + pyqtgraph): set up a run, preview it, watch it, see the results: S-parameters,
field dumps (live while the run goes, saved as PNG / GIF / MP4) and antenna far fields. Every run is the command-line tool started as a separate process with --events; the GUI
follows its event stream, so a closed window never kills a simulation's results and the GUI needs no
openEMS of its own.

    kicad2ems-gui [config.toml]
"""
import os, sys
from PySide6.QtCore import Qt, QProcess, QTimer, QUrl, QSettings
from PySide6.QtGui import QDesktopServices, QAction, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLineEdit, QCheckBox, QComboBox, QPushButton, QLabel, QPlainTextEdit, QProgressBar,
                               QFileDialog, QScrollArea, QMessageBox, QDockWidget, QToolBar, QStyle, QStackedWidget)
import pyqtgraph as pg
from .fieldtab import FieldsTab
from .antennatab import AntennaTab
from .runner import (FIELDS, SEMICOLON_LISTS, TICK, Run, EventTail, stop_runs, settings_values, extra_values,
                     find_results, save_config, solvers_from_form, solver_ticks)
from .splot import SPlot
from .panels import Section, ImagePanel
from .systempanel import SystemPanel
from .volumetab import VolumeTab

pg.setConfigOptions(antialias=True, background='w', foreground='k')
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'examples')
MAX_RECENT = 8
PANELS = ('S-parameters', 'Model and mesh', 'Line model', 'Run', 'Fields', 'Antenna')
COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f']


class SetupTab(QWidget):
    """The settings: one collapsible section per group (collapsed when the window opens; the header counts the
    settings set), in a scroll area. Open / Save / Preview / Run are the window's toolbar."""
    def __init__(self, win):
        super().__init__()
        from ..config import Settings
        self.win, self.edits, self.extra = win, {}, {}
        self.path, self.dirty, self._loading = None, False, False
        self.defaults = Settings()
        defaults = self.defaults
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(2)
        groups, self.sections, self.group_of = {}, {}, {}
        for key, label, kind, group in FIELDS:
            if group not in groups:
                box = QWidget()
                groups[group] = QFormLayout(box)
                groups[group].setContentsMargins(0, 0, 0, 0)
                self.sections[group] = Section(group, box)
                left.addWidget(self.sections[group])
            self.group_of[key] = group
            if kind == 'bool':
                w = QCheckBox()
                w.toggled.connect(self._changed)
            elif isinstance(kind, tuple):
                w = QComboBox()
                w.addItems([c or '(none)' for c in kind])
                w.currentIndexChanged.connect(self._changed)
            else:
                w = QLineEdit()
                d = getattr(defaults, key, None)
                if d not in (None, '', []) and kind != 'dir':
                    w.setPlaceholderText(f"default: {', '.join(map(str, d)) if isinstance(d, (list, tuple)) else d}")
                w.textChanged.connect(self._changed)
            if kind in ('file', 'dir'):
                row = QHBoxLayout()
                row.addWidget(w)
                b = QPushButton('…')
                b.setFixedWidth(28)
                b.clicked.connect(lambda _=False, k=key, d=(kind == 'dir'): self._browse(k, d))
                row.addWidget(b)
                holder = QWidget()
                holder.setLayout(row)
                row.setContentsMargins(0, 0, 0, 0)
                groups[group].addRow(label, holder)
            else:
                groups[group].addRow(label, w)
            self.edits[key] = (w, kind)
        groups['Field dumps'].addRow('Add a dump', self._dump_builder())
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        scroll = QScrollArea()
        scroll.setWidget(lw)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(380)
        outer.addWidget(scroll)

    def _count(self):
        """Per section: how many of its fields are set (non-empty, a ticked box differing from the default)."""
        counts = dict.fromkeys(self.sections, 0)
        for key, (w, kind) in self.edits.items():
            if kind == 'bool':
                d = getattr(self.defaults, key, False) if not key.startswith(TICK) else key == f"{TICK}auto"
                on = w.isChecked() != bool(d)
            elif isinstance(kind, tuple):
                on = w.currentIndex() > 0 and w.currentText() != str(getattr(self.defaults, key, ''))
            else:
                on = bool(w.text().strip())
            counts[self.group_of[key]] += on
        for g, n in counts.items():
            self.sections[g].set_count(n)

    def _dump_builder(self):
        """FIELD, where (a layer, an x / y / z plane, the volume), GHz -> a spec appended to the dumps."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        fld = QComboBox()
        fld.addItems(['E', 'H', 'J'])
        fld.setToolTip('J: current density (shows the copper currents)')
        kind = QComboBox()
        kind.addItems(['layer', 'x =', 'y =', 'z =', 'volume'])
        at = QComboBox()
        at.setEditable(True)
        at.addItems(['F.Cu', 'B.Cu', 'In1.Cu', 'In2.Cu', 'F.Cu-0.1'])
        at.setToolTip('layer: a copper layer, with an offset in mm (F.Cu-0.1: in the dielectric below); '
                      'x / y / z: mm (KiCad x, y; z from the bottom copper)')
        kind.currentIndexChanged.connect(lambda i: (at.setEnabled(i != 4), at.setEditText(['F.Cu', '0', '0', '0', ''][i])))
        ghz = QLineEdit()
        ghz.setPlaceholderText('GHz (empty: time domain)')
        ghz.setToolTip('Frequencies (GHz, comma-separated): a frequency-domain dump, animated over the phase. '
                       'Empty: a time-domain dump, viewable live during the run')
        add = QPushButton('Add')

        def append():
            k = kind.currentIndex()
            where = 'volume' if k == 4 else (at.currentText().strip() if k == 0 else f"{'xyz'[k - 1]}={at.currentText().strip()}")
            spec = f"{fld.currentText()}@{where}" + (f"@{ghz.text().strip()}" if ghz.text().strip() else '')
            w = self.edits['dump'][0]
            w.setText('; '.join([t for t in [w.text().strip()] if t] + [spec]))
        add.clicked.connect(append)
        for x in (fld, kind, at, ghz, add):
            row.addWidget(x)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def _browse(self, key, folder):
        w = self.edits[key][0]
        if folder:
            p = QFileDialog.getExistingDirectory(self, 'Output folder', w.text() or os.getcwd())
        else:
            flt = 'KiCad board (*.kicad_pcb)' if key == 'board' else 'Pick-and-place (*.pos *.csv);;All files (*)'
            p, _ = QFileDialog.getOpenFileName(self, 'Choose a file', w.text() or os.getcwd(), flt)
        if p:
            w.setText(p)
            if key == 'board' and not self.edits['out'][0].text():
                self.edits['out'][0].setText(os.path.join(os.path.dirname(p), 'out'))

    # ---- values <-> widgets ----
    def values(self):
        """The settings of the form (the solver tick boxes as `solvers`, see runner.solvers_from_form)."""
        out = dict(self.extra)                                       # config settings without a field here
        for key, (w, kind) in self.edits.items():
            if kind == 'bool':
                out[key] = w.isChecked()
                continue
            if isinstance(kind, tuple):
                t = w.currentText()
                out[key] = '' if t == '(none)' else t
                continue
            t = w.text().strip()
            if not t:
                continue
            if kind == 'float':
                out[key] = float(t)
            elif kind == 'int':
                out[key] = int(float(t))
            elif kind == 'list':
                out[key] = [x.strip() for x in t.replace(';', ',').split(',') if x.strip()] \
                    if key not in SEMICOLON_LISTS else [x.strip() for x in t.split(';') if x.strip()]
            elif key == 'region' and t.replace(',', '').replace('.', '').replace('-', '').replace(' ', '').isdigit():
                out[key] = [float(x) for x in t.split(',')]
            else:
                out[key] = t
        return solvers_from_form(out)

    def set_values(self, vals):
        """Fill the form (a loaded config): not an edit, so the settings stay unchanged."""
        loading, self._loading = self._loading, True
        try:
            self._set_values(vals)
        finally:
            self._loading = loading
        self._count()

    def _set_values(self, vals):
        if not any(k.startswith(TICK) for k in vals):              # settings rather than the form's own values
            vals = dict(vals, **solver_ticks(vals))
        for key, (w, kind) in self.edits.items():
            v = vals.get(key)
            if kind == 'bool':
                w.setChecked(bool(v))
            elif isinstance(kind, tuple):
                i = w.findText(v or '(none)')
                w.setCurrentIndex(max(i, 0))
            elif v is None:
                w.setText('')
            elif isinstance(v, (list, tuple)):
                sep = '; ' if key in SEMICOLON_LISTS else ','
                w.setText(sep.join(str(x) if not isinstance(x, (list, tuple)) else ' '.join(map(str, x)) for x in v))
            else:
                w.setText(str(v))

    def _changed(self, *_):
        self._count()
        if not self._loading and not self.dirty:
            self.dirty = True
            self.win.update_title()

    def _start_dir(self):
        if self.path:
            return os.path.dirname(self.path)
        return EXAMPLES if os.path.isdir(EXAMPLES) else os.getcwd()

    def maybe_save(self):
        """True when the form may be replaced: saved, discarded or unchanged."""
        if not self.dirty:
            return True
        r = QMessageBox.question(self, 'Unsaved settings', 'Save the changes to the settings first?',
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new(self):
        if not self.maybe_save():
            return
        self.set_values({})
        self.extra, self.path, self.dirty = {}, None, False
        self.win.update_title()

    def load(self, path=None):
        if path is None and not self.maybe_save():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, 'Open a config', self._start_dir(), 'TOML (*.toml)')
        if not path:
            return False
        from ..config import build_settings
        try:
            vals, extra = settings_values(path), extra_values(path)
            notes = build_settings({}, path).notes
        except Exception as e:
            QMessageBox.warning(self, 'Config', str(e))
            return False
        self.set_values(vals)
        self.extra, self.path, self.dirty = extra, os.path.abspath(path), False
        self.win.remember(self.path)
        self.win.update_title()
        board = vals.get('board')
        if board and not os.path.exists(board):
            notes = notes + [f"board not found: {board} (generated examples: see the commands at the top of the file)"]
        self.win.statusBar().showMessage('; '.join(notes) if notes else f"opened {path}", 0 if notes else 5000)
        out = self.win.out_dir()
        if os.path.isdir(out):                                     # earlier results of this config
            self.win.open_results(out, quiet=True)
        return True

    def save(self):
        if not self.path:
            return self.save_as()
        return self._write(self.path)

    def save_as(self):
        start = self.path or os.path.join(self._start_dir(), 'config.toml')
        path, _ = QFileDialog.getSaveFileName(self, 'Save the config as', start, 'TOML (*.toml)')
        if not path:
            return False
        if not path.endswith('.toml'):
            path += '.toml'
        return self._write(path)

    def _write(self, path):
        try:
            save_config(path, self.values(), template=self.path)       # keeps the comments of the loaded file
        except Exception as e:
            QMessageBox.warning(self, 'Config', f"could not save {path}: {e}")
            return False
        self.path, self.dirty = os.path.abspath(path), False
        self.win.remember(self.path)
        self.win.update_title()
        self.win.statusBar().showMessage(f"saved {path}", 5000)
        return True

    def show_images(self, paths):
        """A preview's plots: into the window's Model & mesh and Line model panels."""
        self.win.show_plots(paths)


class RunTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.stage = QLabel('idle')
        top.addWidget(self.stage, 1)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(win.stop)
        top.addWidget(self.stop_btn)
        lay.addLayout(top)
        self.bars_box = QVBoxLayout()
        lay.addLayout(self.bars_box)
        self.bars, self.curves, self.hist, self._tol = {}, {}, {}, None
        plots = QVBoxLayout()                                       # one plot per kind of run: their x differ
        self.plot = pg.PlotWidget(title='openEMS: field energy / port signal decay')
        self.plot.setLabel('bottom', 'timestep')
        self.plot.setLabel('left', 'dB')
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.sweep = pg.PlotWidget(title="mom25d: the adaptive sweep's model change (max |dS|)")
        self.sweep.setLabel('bottom', 'EM solve')
        self.sweep.setLabel('left', 'dB')
        self.sweep.addLegend()
        self.sweep.showGrid(x=True, y=True, alpha=0.3)
        self.sweep.hide()
        plots.addWidget(self.plot, 1)
        plots.addWidget(self.sweep, 1)
        lay.addLayout(plots, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log, 1)

    def reset(self):
        for b in self.bars.values():
            b.deleteLater()
        self.bars, self.hist = {}, {}
        self.plot.clear()
        self.sweep.clear()
        self.curves = {}
        self._tol = None
        self.plot.show()
        self.sweep.hide()
        self.log.clear()

    def _sweep(self, run, ev):
        """mom25d's adaptive sweep, model by model: its largest change and its prediction error (max |dS|) in dB
        against the solve count, and the tolerance it stops at."""
        import math
        db = lambda v: 20 * math.log10(max(float(v), 1e-12))
        for key, label, style in (('change', 'model change', Qt.SolidLine), ('pred', 'prediction error', Qt.DashLine)):
            v = ev.get(key)
            name = f"{run} {label}"
            if name not in self.curves:
                pen = pg.mkPen(COLORS[len(self.curves) % len(COLORS)], width=2, style=style)
                self.curves[name] = self.sweep.plot([], [], pen=pen, symbol='o', symbolSize=5, name=name)
                self.hist[name] = ([], [])
            if v is not None and math.isfinite(float(v)):
                xs, ys = self.hist[name]
                xs.append(ev['afs_solves'])
                ys.append(db(v))
                self.curves[name].setData(xs, ys)
        if ev.get('tol') and self._tol is None:
            self._tol = pg.InfiniteLine(pos=db(ev['tol']), angle=0, pen=pg.mkPen('#c0392b', width=1, style=Qt.DotLine),
                                        label=f"tolerance {ev['tol']:g}", labelOpts=dict(position=0.08, color='#c0392b'))
            self.sweep.addItem(self._tol)
        self._layout()

    def _layout(self):
        """The plots with something in them: openEMS's energy (timesteps) and mom25d's sweep (EM solves) one
        above the other when both run; the energy plot alone while nothing has come in."""
        sweep = any(k.endswith(('model change', 'prediction error')) for k in self.curves)
        energy = any(not k.endswith(('model change', 'prediction error')) for k in self.curves)
        self.sweep.setVisible(sweep)
        self.plot.setVisible(energy or not sweep)

    def status(self, ev):
        run = ev.get('run', '?')
        if ev.get('change') is not None:                            # a step of mom25d's adaptive sweep
            self._sweep(run, ev)
            return
        if run not in self.bars:
            b = QProgressBar()
            b.setFormat(f"{run}: %p%")
            self.bars_box.addWidget(b)
            self.bars[run] = b
        b = self.bars[run]
        if ev.get('fraction') is not None:
            b.setValue(int(100 * ev['fraction']))
        eta = ev.get('eta')
        db = ev.get('db')
        what = (f"  {ev['solves']} EM solve(s), last at {ev.get('f_ghz') or 0:.3f} GHz" if ev.get('solves') is not None
                else f"  step {ev.get('step', 0):,}" + (f"  {db:.1f} dB" if db is not None else ''))     # MoM / openEMS
        b.setFormat(f"{run}: %p%{what}" + (f"  ETA {int(eta // 60)}m{int(eta % 60):02d}s" if eta else ''))
        if ev.get('step') is not None and db is not None:
            if run not in self.curves:                              # openEMS's energy: a curve once it has data
                pen = pg.mkPen(COLORS[len(self.curves) % len(COLORS)], width=2)
                self.curves[run] = self.plot.plot([], [], pen=pen, name=run)
                self.hist[run] = ([], [])
                self._layout()
            xs, ys = self.hist[run]
            xs.append(ev['step'])
            ys.append(db)
            self.curves[run].setData(xs, ys)


class FieldPanel(QWidget):
    """The field views in one panel: 2D cuts (any dump, a volume cut at any line) and the 3D view (the board, a
    solver's mesh, plane and volume dumps in place, the far field), toggled."""

    def __init__(self, cuts, view3d):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.buttons = {}
        for name, tip in (('3D', 'the board in 3D: meshes, dumps in place, far field'),
                          ('2D', 'a cut through a dump, with its values')):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, n=name: self.set_view(n))
            row.addWidget(b)
            self.buttons[name] = b
        row.addStretch(1)
        lay.addLayout(row)
        self.stack = QStackedWidget()
        self.views = {'3D': view3d, '2D': cuts}
        for w in (view3d, cuts):
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        self.set_view('3D')

    def set_view(self, name):
        self.stack.setCurrentWidget(self.views[name])
        for n, b in self.buttons.items():
            b.setChecked(n == name)

    def view(self):
        return next(n for n, w in self.views.items() if self.stack.currentWidget() is w)


class ResultsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel('no results yet')
        top.addWidget(self.info, 1)
        self.add_btn = QPushButton('Add results folder…')
        self.add_btn.setToolTip('Overlay the S-parameters of another run (another folder), labelled by its folder')
        self.add_btn.clicked.connect(self._add)
        top.addWidget(self.add_btn)
        self.clear_btn = QPushButton('Remove added')
        self.clear_btn.clicked.connect(self._clear_added)
        self.clear_btn.setEnabled(False)
        top.addWidget(self.clear_btn)
        self.folder_btn = QPushButton('Open folder')
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.win.out_dir())))
        top.addWidget(self.folder_btn)
        lay.addLayout(top)
        self.splot = SPlot()
        lay.addWidget(self.splot, 1)
        self.report, self.added, self._files = None, [], []

    def open_report(self):
        if self.report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report))

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, 'Another results folder to overlay', self.win.out_dir())
        if d:
            files = find_results(d)
            if not files:
                QMessageBox.information(self, 'Results', f"No S-parameters in {d}")
                return
            self.added.append((os.path.basename(os.path.normpath(d)) or d, files))
            self.show(self._files)

    def _clear_added(self):
        self.added = []
        self.show(self._files)

    def _load(self):
        d = QFileDialog.getExistingDirectory(self, 'A results folder (the run\'s output folder)', self.win.out_dir())
        if d:
            self.win.open_results(d)

    def show(self, files, compare=None):
        """The S-parameters of a run's files (openEMS, mom25d, line model) and a compare file."""
        import json
        from .traces import load_traces
        self._files = list(files)
        self.report = next((p for l, p in files if p.endswith('_report.html')), None)
        self.clear_btn.setEnabled(bool(self.added))
        z0 = 50.0
        summary = next((p for l, p in files if p.endswith(('_run.json', '_openems.json'))), None)
        if summary and os.path.exists(summary):
            try:
                with open(summary) as fh:
                    sm = json.load(fh)
                compare = compare or (sm.get('settings') or {}).get('compare')
                zr = sm.get('zref')
                z0 = float(zr[0] if isinstance(zr, list) else zr or 50.0)
            except (ValueError, TypeError, OSError):
                pass
        if compare is None:
            compare = self.win.setup.values().get('compare')
        try:
            traces = load_traces(files, compare)
            for label, fs in self.added:                             # other runs: their solvers under the folder's name
                for tr in load_traces(fs):
                    tr.source = f"{label}: {tr.source}"
                    traces.append(tr)
        except Exception as e:                                       # a damaged file must not take the GUI down
            self.info.setText(f"could not read the S-parameters: {e}")
            return
        self.splot.set_traces(traces, z0)
        shown = sorted({os.path.basename(t.path) for t in traces})
        self.info.setText(', '.join(shown) if shown else 'no S-parameters in this run')


class MainWindow(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.setWindowTitle('kicad2ems')
        self.resize(1500, 950)
        self.proc, self.tail, self.files, self.current, self.ticks = None, None, [], None, 0
        self._traceback = self._stopping = False
        self.setup, self.runtab, self.results = SetupTab(self), RunTab(self), ResultsTab(self)
        self.fields, self.antenna = FieldsTab(self), AntennaTab(self)
        self.volume = VolumeTab(self)
        self.field_panel = FieldPanel(self.fields, self.volume)     # 2D cuts and the 3D view, one panel
        self.model_img = ImagePanel('the model and the mesh of a preview or a run')
        self.lines_img = ImagePanel('the line model of a preview or a run')
        self.system = SystemPanel()
        # the settings: a dock of the window; the results: one workspace of panels (tabbed, split, floated)
        sd = QDockWidget('Settings', self)
        sd.setObjectName('Settings')
        sd.setWidget(self.setup)
        sd.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, sd)
        self.work = QMainWindow()
        self.work.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.AllowNestedDocks)
        note = QLabel('Preview or run a board: its results open here (View: every panel)')
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet('color: gray')
        self.work.setCentralWidget(note)
        self.note = note
        self.docks = {}
        for name, w in zip(PANELS, (self.results, self.model_img, self.lines_img, self.runtab, self.field_panel,
                                    self.antenna)):
            d = QDockWidget(name, self.work)
            d.setObjectName(name)
            d.setWidget(w)
            self.work.addDockWidget(Qt.TopDockWidgetArea, d)
            if self.docks:
                self.work.tabifyDockWidget(self.docks[PANELS[0]], d)
            d.hide()
            self.docks[name] = d
        # the machine (solvers, GPU, memory) and a preview's estimates: beside the results, open from the start
        d = QDockWidget('System', self.work)
        d.setObjectName('System')
        d.setWidget(self.system)
        self.work.addDockWidget(Qt.RightDockWidgetArea, d)
        self.docks['System'] = d
        self.setCentralWidget(self.work)
        self.qsettings = QSettings('kicad2ems', 'gui')
        self._toolbar()
        self._menus()
        self.statusBar()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.update_title()
        if config:
            self.setup.load(config)

    # ---- toolbar, panels, menus, title, recent files ----
    def _toolbar(self):
        tb = QToolBar('Main')
        tb.setObjectName('Main toolbar')
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        st = self.style()
        self.acts = {}
        for name, icon, tip, slot in (
                ('New', 'SP_FileIcon', 'New settings (Ctrl+N)', self.setup.new),
                ('Open', 'SP_DialogOpenButton', 'Open a config (Ctrl+O)', lambda: self.setup.load()),
                ('Save', 'SP_DialogSaveButton', 'Save the config (Ctrl+S)', self.setup.save),
                ('Save as', 'SP_DriveFDIcon', 'Save the config as (Ctrl+Shift+S)', self.setup.save_as),
                (None, None, None, None),
                ('Preview', 'SP_FileDialogContentsView', 'The model, mesh and line model, no openEMS run (Ctrl+P)',
                 self.preview),
                ('Run', 'SP_MediaPlay', 'Run the solvers (Ctrl+R)', self.run),
                ('Stop', 'SP_MediaStop', 'Stop the run (Ctrl+.)', self.stop),
                (None, None, None, None),
                ('Results', 'SP_DirOpenIcon', 'Open a results folder', self.results._load),
                ('Report', 'SP_FileDialogInfoView', 'Open the HTML report of the results', self.results.open_report)):
            if name is None:
                tb.addSeparator()
                continue
            a = QAction(st.standardIcon(getattr(QStyle.StandardPixmap, icon)), name, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            self.acts[name] = a
        self.acts['Stop'].setEnabled(False)
        self.acts['Report'].setEnabled(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

    def _load_fields(self, out_dir):
        """The Fields panel's 2D cuts and 3D view of a folder; the 3D view first (False: nothing to show)."""
        self.fields.load(out_dir)
        three = self.volume.load(out_dir)
        if three:
            self.field_panel.set_view('3D')
        elif self.fields.srcs:
            self.field_panel.set_view('2D')
        return bool(three or self.fields.srcs)

    def show_panel(self, name, front=True):
        """Open a results panel (it may have been closed) and bring it to the front of its tab group."""
        d = self.docks[name]
        d.show()
        self.note.hide()                                            # the panels take the whole workspace
        if front:
            d.raise_()

    def show_plots(self, paths):
        """A preview's or run's plots into their panels: model and mesh, line model."""
        groups = {'Model and mesh': [p for p in paths if p.endswith(('_model.png', '_mesh.png'))],
                  'Line model': [p for p in paths if p.endswith('_tlines.png')]}
        panels = {'Model and mesh': self.model_img, 'Line model': self.lines_img}
        for name, ps in groups.items():
            if ps:
                panels[name].show_images(ps)
                self.show_panel(name, front=False)
        return [n for n, ps in groups.items() if ps]

    def _menus(self):
        fm = self.menuBar().addMenu('&File')
        for text, key, slot in (('&New', QKeySequence.New, self.setup.new),
                                ('&Open…', QKeySequence.Open, lambda: self.setup.load()),
                                (None, None, None),
                                ('&Save', QKeySequence.Save, self.setup.save),
                                ('Save &as…', QKeySequence.SaveAs, self.setup.save_as)):
            if text is None:
                self.recent_menu = fm.addMenu('Open &recent')
                self.examples_menu = fm.addMenu('&Examples')
                continue
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            fm.addAction(a)
        fm.addSeparator()
        a = QAction('Open &results folder…', self)
        a.triggered.connect(self.results._load)
        fm.addAction(a)
        fm.addSeparator()
        a = QAction('&Quit', self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        fm.addAction(a)
        vm = self.menuBar().addMenu('&View')
        for name in PANELS + ('System',):
            vm.addAction(self.docks[name].toggleViewAction())
        vm.addSeparator()
        a = QAction('Show &all panels', self)
        a.triggered.connect(lambda: [self.show_panel(n, front=False) for n in PANELS])
        vm.addAction(a)
        rm = self.menuBar().addMenu('&Run')
        for text, key, slot in (('&Preview', 'Ctrl+P', self.preview), ('&Run', 'Ctrl+R', self.run),
                                ('&Stop', 'Ctrl+.', self.stop)):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(slot)
            rm.addAction(a)
        self._fill_recent()
        self._fill_examples()

    def _fill_examples(self):
        self.examples_menu.clear()
        files = sorted(f for f in os.listdir(EXAMPLES) if f.endswith('.toml')) if os.path.isdir(EXAMPLES) else []
        for f in files:
            a = QAction(f[:-5], self)
            a.triggered.connect(lambda _=False, p=os.path.join(EXAMPLES, f): self._open(p))
            self.examples_menu.addAction(a)
        self.examples_menu.setEnabled(bool(files))

    def recent(self):
        v = self.qsettings.value('recent', [])
        v = [v] if isinstance(v, str) else list(v or [])
        return [p for p in v if os.path.exists(p)]

    def remember(self, path):
        r = [path] + [p for p in self.recent() if p != path]
        self.qsettings.setValue('recent', r[:MAX_RECENT])
        self._fill_recent()

    def _fill_recent(self):
        self.recent_menu.clear()
        r = self.recent()
        for p in r:
            a = QAction(p, self)
            a.triggered.connect(lambda _=False, p=p: self._open(p))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(r))

    def _open(self, path):
        if self.setup.maybe_save():
            self.setup.load(path)

    def update_title(self):
        name = os.path.basename(self.setup.path) if self.setup.path else 'untitled'
        self.setWindowTitle(f"{name}{'*' if self.setup.dirty else ''} - kicad2ems")

    def closeEvent(self, ev):
        if self.setup.maybe_save():
            self.system.stop()
            ev.accept()
        else:
            ev.ignore()

    def open_results(self, out_dir, quiet=False):
        """Show the results already in a folder, each in its panel: S-parameters, plots, field dumps, antenna."""
        files = find_results(out_dir)
        if files:
            self.results.show(files)
            self.show_panel('S-parameters', front=False)
        self.show_plots(_pngs(out_dir))
        if self._load_fields(out_dir):
            self.show_panel('Fields', front=False)
        ant = self.antenna.load(out_dir)
        if ant:
            self.show_panel('Antenna', front=False)
        self.acts['Report'].setEnabled(bool(self.results.report))
        if files:
            self.show_panel('Antenna' if ant else 'S-parameters')
        if not quiet and not (files or self.fields.srcs or ant):
            QMessageBox.information(self, 'Results', f"No results in {out_dir}")

    def out_dir(self, vals=None):
        """The output folder: the form's, else 'out' next to the board (where a run writes), else in the working
        directory."""
        from ..config import default_out
        vals = self.setup.values() if vals is None else vals
        if vals.get('out'):
            return vals['out']
        board = vals.get('board')
        return default_out(board) if board else os.path.join(os.getcwd(), 'out')

    def start(self, preview):
        if self.proc is not None:
            QMessageBox.information(self, 'Busy', 'A run is going: stop it first')
            return
        vals = self.setup.values()
        if not vals.get('board'):
            QMessageBox.warning(self, 'Setup', 'Choose a board first')
            return
        if not preview and vals.get('solvers') == []:
            QMessageBox.warning(self, 'Setup', 'Choose a solver: openEMS, mom25d or the line model (or Preview)')
            return
        self.current = Run(vals, self.out_dir(vals), preview)
        self.files, self._traceback, self._stopping = [], False, False
        self.runtab.reset()
        self.tail = EventTail(self.current.events)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._stdout)
        self.proc.finished.connect(self._finished)
        argv = self.current.argv()
        self.proc.start(argv[0], argv[1:])
        self.runtab.stop_btn.setEnabled(True)
        self.acts['Stop'].setEnabled(True)
        self.runtab.stage.setText('starting…')
        self.show_panel('Run', front=not preview)
        self.timer.start(300)

    def preview(self):
        self.start(True)

    def run(self):
        self.start(False)

    def stop(self):
        """First press: ask the run to stop (stop.py: mom25d after the frequency it is solving, openEMS at once
        with what it has, later solvers skipped). Second press: end the process."""
        if not (self.current and self.proc):
            return
        if self._stopping:
            self.runtab.log.appendPlainText("stop: ending the process")
            self.proc.kill()
            return
        self._stopping = True
        stop_runs(self.current.out)
        self.runtab.log.appendPlainText("stop: requested - the run ends after its current step "
                                        "(press Stop again to end the process at once)")

    def _stdout(self):
        """The process's own output: the log already comes as events, so only errors - a traceback whole."""
        data = bytes(self.proc.readAllStandardOutput()).decode('utf-8', 'replace')
        for line in data.splitlines():
            self._traceback = self._traceback or line.startswith('Traceback')
            if self._traceback or 'error' in line.lower():
                self.runtab.log.appendPlainText(line)

    def poll(self):
        if not self.tail:
            return
        for ev in self.tail.read():
            k = ev.get('kind')
            if k == 'log':
                self.runtab.log.appendPlainText(ev.get('text', ''))
            elif k == 'stage':
                self.runtab.stage.setText(f"stage: {ev.get('name')}")
            elif k == 'status':
                self.runtab.status(ev)
            elif k == 'file':
                self.files.append((ev.get('label', ''), ev.get('path', '')))
            elif k == 'done':
                self.runtab.stage.setText('done' if ev.get('code') in (0, None) else f"ended with code {ev.get('code')}")
        self.ticks += 1
        if self.current and not self.current.preview and self.ticks % 3 == 0:   # field frames, ~1 s
            try:
                self.fields.follow(self.current.out)
            except Exception as e:                                   # never stop following the run over a frame
                self.runtab.log.appendPlainText(f"field view: {e}")

    def _finished(self, code, _status):
        self.poll()
        self.timer.stop()
        self.proc = None
        self.runtab.stop_btn.setEnabled(False)
        self.acts['Stop'].setEnabled(False)
        shown = self.show_plots([p for _, p in self.files])
        has_s = any(l.startswith('S-parameters') for l, _ in self.files)
        if has_s or not self.current.preview:
            self.results.show(self.files)                            # a preview: the solvers it runs (not openEMS)
            self.show_panel('S-parameters', front=False)
        self.acts['Report'].setEnabled(bool(self.results.report))
        front = 'S-parameters' if has_s else (shown[0] if shown else 'Run')
        fields = self._load_fields(self.current.out)               # dumps, or the board and the solvers' meshes
        if fields:
            self.show_panel('Fields', front=False)
        if not self.current.preview:
            ant = self.antenna.load(self.current.out)
            if ant:
                self.show_panel('Antenna', front=False)
                front = 'Antenna' if self.current.values.get('mode') == 'antenna' else front
        elif fields:                                                # a preview: the board and its meshes in 3D
            front = 'Fields'
        elif shown:
            front = shown[0]
        if self.current.preview and self.system.load_estimates(self.current.out):
            self.docks['System'].show()
            self.docks['System'].raise_()
        self.show_panel(front)
        if code:
            self.runtab.stage.setText(f"ended with code {code} - see the log")


def _pngs(out_dir):
    """The plots of a results folder."""
    import glob
    return sorted(glob.glob(os.path.join(out_dir, '*.png')))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    from . import application
    app = application(sys.argv[:1])
    w = MainWindow(argv[0] if argv else None)
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
