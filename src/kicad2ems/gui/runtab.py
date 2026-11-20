"""The Log page: the runs of a study and where each is, openEMS's energy decay
and mom25d's adaptive sweep, and the log of each (RunTab); run_notes, what a
run's summary says to note. Moved out of app.py (R-F2).

The page shows what has run for the study open, and nothing else: a plot once
its run sends data, the 'Log of' list the runs started; another config opened,
it is blank again (clear)."""
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
                               QPushButton, QLabel, QPlainTextEdit,
                               QProgressBar, QTreeWidget, QTreeWidgetItem,
                               QHeaderView, QStackedWidget)
from .study import title


IDLE = 'nothing run yet'  # the page before anything has run
COLORS = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2',
    '#7f7f7f'
]


def follow(log):
    """A log that keeps its last lines in view while at its end, also behind
    another page (Qt follows only a log on the screen: one looked at after its
    run had stood at its first lines); one scrolled up to read stays where it
    is."""
    bar, end = log.verticalScrollBar(), [0]

    def grown(_lo, hi):
        if bar.value() >= end[0]:
            bar.setValue(hi)
        end[0] = hi
    bar.rangeChanged.connect(grown)


class RunTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.stage = QLabel(IDLE)
        self.stop_btn = QPushButton('Stop', enabled=False, clicked=win.stop)
        top.addWidget(self.stage, 1)
        top.addWidget(self.stop_btn)
        lay.addLayout(top)
        # a study run's analyses: state and time
        self.jobs = QTreeWidget(rootIsDecorated=False)
        self.jobs.setHeaderLabels(['Analysis', 'State', 'Time'])
        self.jobs.header().setStretchLastSection(False)
        fit, stretch = QHeaderView.ResizeToContents, QHeaderView.Stretch
        for c, mode in enumerate((fit, stretch, fit)):
            self.jobs.header().setSectionResizeMode(c, mode)
        self.jobs.hide()
        self.jobs.currentItemChanged.connect(
            lambda cur, _old: cur is not None and self.show_log(
                cur.data(0, Qt.UserRole)))
        self.job_rows = {}
        lay.addWidget(self.jobs)
        self.bars_box = QVBoxLayout()
        lay.addLayout(self.bars_box)
        self.bars, self.curves, self.hist, self._tol = {}, {}, {}, None
        plots = QVBoxLayout()  # one plot per kind of run: their x differ
        self.plot = pg.PlotWidget(
            title='openEMS: field energy / port signal decay')
        self.sweep = pg.PlotWidget(
            title="mom25d: the adaptive sweep's model change (max |dS|)")
        for p, x in ((self.plot, 'timestep'), (self.sweep, 'EM solve')):
            p.setLabel('bottom', x)
            p.setLabel('left', 'dB')
            p.addLegend()
            p.showGrid(x=True, y=True, alpha=0.3)
            plots.addWidget(p, 1)
        self.plot.hide()  # (each once its run sends data: _layout)
        self.sweep.hide()
        lay.addLayout(plots, 2)
        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=5000)
        row = QHBoxLayout()  # whose log: the S-parameter run's, a page's
        self.log_label = QLabel('Log of')
        self.log_of = QComboBox()
        # every run's log, {key: (title, its place in self.logs)}; those listed:
        # the runs started (self.ran)
        self.views = {'rf': (title('rf'), 0)}
        self.ran = []
        self.log_of.currentIndexChanged.connect(
            lambda i: i >= 0 and self.logs.setCurrentIndex(
                self.views[self.log_of.itemData(i)][1]))
        row.addWidget(self.log_label)
        row.addWidget(self.log_of)
        row.addStretch(1)
        lay.addLayout(row)
        self.logs = QStackedWidget()
        follow(self.log)
        self.logs.addWidget(self.log)
        lay.addWidget(self.logs, 1)
        self.log_label.hide()
        self.log_of.hide()

    def add_log(self, key, title, log):
        """Another analysis's log beside the S-parameter run's: its page's own
        widget, shown here (it is nowhere else). Not a second view of its
        document: a QPlainTextEdit sharing another's document never learns
        that it grew (its scroll range stayed 0), and a long run's log stood at
        its first lines."""
        follow(log)
        self.views[key] = (title, self.logs.addWidget(log))

    def started(self, key):
        """A run started: its log listed (in the order they ran), and shown."""
        if key not in self.ran:
            self.ran.append(key)
            self.log_of.blockSignals(True)
            self.log_of.clear()
            for k in self.ran:
                self.log_of.addItem(self.views[k][0], k)
            self.log_of.blockSignals(False)
            # whose log: once there are two to choose from
            self.log_label.setVisible(len(self.ran) > 1)
            self.log_of.setVisible(len(self.ran) > 1)
        self.show_log(key)

    def show_log(self, key):
        """A run's log in view: one that has run (started)."""
        i = self.log_of.findData(key)
        if i >= 0:
            self.log_of.setCurrentIndex(i)
            self.logs.setCurrentIndex(self.views[key][1])

    def clear(self):
        """Nothing run: another study opened (its runs' logs and plots are not
        this one's)."""
        self.reset()
        self.set_jobs([])
        self.ran = []
        self.log_of.clear()
        self.log_label.hide()
        self.log_of.hide()
        self.logs.setCurrentIndex(0)
        self.stage.setText(IDLE)

    def set_jobs(self, jobs):
        """The analyses of a study run [(key, title)], queued; one alone: no
        list (the page as it was)."""
        self.jobs.clear()
        self.job_rows = {}
        for key, title in jobs:
            it = QTreeWidgetItem([title, 'queued', ''])
            it.setData(0, Qt.UserRole, key)
            self.jobs.addTopLevelItem(it)
            self.job_rows[key] = it
        self.jobs.setVisible(len(jobs) > 1)
        if jobs:
            self.jobs.setFixedHeight(
                self.jobs.sizeHintForRow(0) * len(jobs)
                + self.jobs.header().sizeHint().height()
                + 2 * self.jobs.frameWidth() + 4)

    def job_state(self, key, text, seconds=None):
        if text == 'running':
            self.started(key)
        it = self.job_rows.get(key)
        if it is None:
            return
        it.setText(1, text)
        it.setToolTip(1, text)
        f = it.font(1)
        f.setBold(text == 'running')
        it.setFont(1, f)
        if seconds is not None:
            it.setText(2, f"{int(seconds // 60)}:{int(seconds % 60):02d}")
        if text == 'running':
            self.jobs.setCurrentItem(it)  # its log below

    def own_plots(self, on):
        """kicad2ems's own run's plots and bars: away while another analysis
        runs (its log has the page)."""
        if not on:
            for b in self.bars.values():
                b.hide()
            self.plot.hide()
            self.sweep.hide()

    def reset(self):
        for b in self.bars.values():
            b.deleteLater()
        self.bars, self.hist = {}, {}
        self.plot.clear()
        self.sweep.clear()
        self.curves = {}
        self._tol = None
        self.plot.hide()
        self.sweep.hide()
        self.log.clear()

    def _sweep(self, run, ev):
        """mom25d's adaptive sweep, model by model: its largest change and its
        prediction error (max |dS|) in dB against the solve count, and the
        tolerance it stops at."""
        import math
        db = lambda v: 20 * math.log10(max(float(v), 1e-12))
        for key, label, style in (('change', 'model change', Qt.SolidLine),
                                  ('pred', 'prediction error', Qt.DashLine)):
            v = ev.get(key)
            name = f"{run} {label}"
            if name not in self.curves:
                pen = pg.mkPen(COLORS[len(self.curves) % len(COLORS)], width=2,
                               style=style)
                self.curves[name] = self.sweep.plot([], [], pen=pen, symbol='o',
                                                    symbolSize=5, name=name)
                self.hist[name] = ([], [])
            if v is not None and math.isfinite(float(v)):
                xs, ys = self.hist[name]
                xs.append(ev['afs_solves'])
                ys.append(db(v))
                self.curves[name].setData(xs, ys)
        if ev.get('tol') and self._tol is None:
            self._tol = pg.InfiniteLine(
                pos=db(ev['tol']), angle=0,
                pen=pg.mkPen('#c0392b', width=1, style=Qt.DotLine),
                label=f"tolerance {ev['tol']:g}",
                labelOpts=dict(position=0.08, color='#c0392b'))
            self.sweep.addItem(self._tol)
        self._layout()

    def _layout(self):
        """The plots with something in them: openEMS's energy (timesteps) and
        mom25d's sweep (EM solves) one above the other when both run; neither
        while nothing has come in (a run of neither: no plot)."""
        sweep = any(
            k.endswith(('model change', 'prediction error'))
            for k in self.curves)
        energy = any(not k.endswith(('model change', 'prediction error'))
                     for k in self.curves)
        self.sweep.setVisible(sweep)
        self.plot.setVisible(energy)

    def status(self, ev):
        run = ev.get('run', '?')
        if ev.get('change') is not None:  # a step of mom25d's adaptive sweep
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
        what = (
            f"  {ev['solves']} EM solve(s), last at {ev.get('f_ghz') or 0:.3f} "
            "GHz"
            if ev.get('solves') is not None
            # MoM / openEMS
            else f"  step {ev.get('step', 0):,}" +
            (f"  {db:.1f} dB" if db is not None else ''))
        b.setFormat(f"{run}: %p%{what}" + (
            f"  ETA {int(eta // 60)}m{int(eta % 60):02d}s" if eta else ''))
        if ev.get('step') is not None and db is not None:
            # openEMS's energy: a curve once it has data
            if run not in self.curves:
                pen = pg.mkPen(COLORS[len(self.curves) % len(COLORS)], width=2)
                self.curves[run] = self.plot.plot([], [], pen=pen, name=run)
                self.hist[run] = ([], [])
                self._layout()
            xs, ys = self.hist[run]
            xs.append(ev['step'])
            ys.append(db)
            self.curves[run].setData(xs, ys)


def run_notes(sm):
    """(warnings, notes) of a run's summary (<name>_run.json): its warnings (the
    log's WARNING lines), and the notes of the board's conversion (the ground
    plane, ports, parts), of the line model and the log's 'note:' lines (a
    setting ignored, an assumption made), each once and without its prefix."""
    import re
    strip = lambda t: re.sub(
        r'^\s*(?:[\w.-]+:\s+)?(?:WARNING|warning|note):\s*', '', str(t)).strip(
        )
    warns = list(
        dict.fromkeys(
            strip(w) for w in sm.get('warnings') or [] if str(w).strip()))
    notes = list((sm.get('model') or {}).get('notes') or [])
    lines = sm.get('lines')
    if isinstance(lines, dict):
        notes += list(lines.get('notes') or [])
    notes += [
        l for l in sm.get('log') or []
        if re.match(r'^\s*(?:[\w.-]+:\s+)?note:', str(l))
    ]
    notes = [
        n for n in dict.fromkeys(strip(n) for n in notes)
        if n and n not in warns
    ]
    return warns, notes
