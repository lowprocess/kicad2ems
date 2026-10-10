"""
kicad2openems GUI, first version (PySide6 + pyqtgraph): set up a run, preview it, watch it, see the
results. Every run is the command-line tool started as a separate process with --events; the GUI
follows its event stream, so a closed window never kills a simulation's results and the GUI needs no
openEMS of its own.

    kicad2openems-gui [config.toml]
"""
import os, sys
from PySide6.QtCore import Qt, QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
                               QFormLayout, QGroupBox, QLineEdit, QCheckBox, QComboBox, QPushButton, QLabel,
                               QPlainTextEdit, QProgressBar, QFileDialog, QScrollArea, QSplitter, QMessageBox)
import pyqtgraph as pg
from .runner import FIELDS, Run, EventTail, stop_runs, settings_values, write_toml

pg.setConfigOptions(antialias=True, background='w', foreground='k')
COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f']


class SetupTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.edits = win, {}
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        groups = {}
        for key, label, kind, group in FIELDS:
            if group not in groups:
                box = QGroupBox(group)
                groups[group] = QFormLayout(box)
                left.addWidget(box)
            if kind == 'bool':
                w = QCheckBox()
            elif isinstance(kind, tuple):
                w = QComboBox()
                w.addItems([c or '(none)' for c in kind])
            else:
                w = QLineEdit()
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
        buttons = QHBoxLayout()
        for text, slot in (('Load config…', self.load), ('Save config…', self.save), ('Preview', win.preview),
                           ('Run', win.run)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        left.addLayout(buttons)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        scroll = QScrollArea()
        scroll.setWidget(lw)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(420)
        self.images = QVBoxLayout()
        iw = QWidget()
        iw.setLayout(self.images)
        right = QScrollArea()
        right.setWidget(iw)
        right.setWidgetResizable(True)
        split = QSplitter()
        split.addWidget(scroll)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)

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
        out = {}
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
                out[key] = [x.strip() for x in t.replace(';', ',').split(',') if x.strip()] if key != 'ports' \
                    else [x.strip() for x in t.split(';') if x.strip()]
            elif key == 'region' and t.replace(',', '').replace('.', '').replace('-', '').replace(' ', '').isdigit():
                out[key] = [float(x) for x in t.split(',')]
            else:
                out[key] = t
        return out

    def set_values(self, vals):
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
                sep = '; ' if key == 'ports' else ','
                w.setText(sep.join(str(x) if not isinstance(x, (list, tuple)) else ' '.join(map(str, x)) for x in v))
            else:
                w.setText(str(v))

    def load(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, 'Load a config', os.getcwd(), 'TOML (*.toml)')
        if path:
            try:
                self.set_values(settings_values(path))
            except Exception as e:
                QMessageBox.warning(self, 'Config', str(e))

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save the config', os.getcwd(), 'TOML (*.toml)')
        if path:
            write_toml(path, self.values())

    def show_images(self, paths):
        while self.images.count():
            it = self.images.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for p in paths:
            lab = QLabel()
            pm = QPixmap(p)
            lab.setPixmap(pm.scaledToWidth(min(pm.width(), 1100), Qt.SmoothTransformation))
            self.images.addWidget(lab)
        self.images.addStretch(1)


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
        self.bars, self.curves, self.hist = {}, {}, {}
        self.plot = pg.PlotWidget(title='field energy / port signal decay')
        self.plot.setLabel('bottom', 'timestep')
        self.plot.setLabel('left', 'dB')
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        lay.addWidget(self.plot, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        lay.addWidget(self.log, 1)

    def reset(self):
        for b in self.bars.values():
            b.deleteLater()
        self.bars, self.hist = {}, {}
        self.plot.clear()
        self.curves = {}
        self.log.clear()

    def status(self, ev):
        run = ev.get('run', '?')
        if run not in self.bars:
            b = QProgressBar()
            b.setFormat(f"{run}: %p%")
            self.bars_box.addWidget(b)
            self.bars[run] = b
            self.curves[run] = self.plot.plot([], [], pen=pg.mkPen(COLORS[len(self.curves) % len(COLORS)], width=2), name=run)
            self.hist[run] = ([], [])
        b = self.bars[run]
        if ev.get('fraction') is not None:
            b.setValue(int(100 * ev['fraction']))
        eta = ev.get('eta')
        db = ev.get('db')
        b.setFormat(f"{run}: %p%  step {ev.get('step', 0):,}" + (f"  {db:.1f} dB" if db is not None else '') +
                    (f"  ETA {int(eta // 60)}m{int(eta % 60):02d}s" if eta else ''))
        if ev.get('step') is not None and db is not None:
            xs, ys = self.hist[run]
            xs.append(ev['step'])
            ys.append(db)
            self.curves[run].setData(xs, ys)


class ResultsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel('no results yet')
        top.addWidget(self.info, 1)
        self.report_btn = QPushButton('Open report')
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.report)))
        top.addWidget(self.report_btn)
        self.folder_btn = QPushButton('Open folder')
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.win.out_dir())))
        top.addWidget(self.folder_btn)
        lay.addLayout(top)
        self.plot = pg.PlotWidget(title='S-parameters')
        self.plot.setLabel('bottom', 'frequency (GHz)')
        self.plot.setLabel('left', '|S| (dB)')
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        lay.addWidget(self.plot)
        self.report = None

    def show(self, files):
        import numpy as np
        from ..touchstone import read_touchstone
        self.plot.clear()
        main = next((p for l, p in files if 'Touchstone' in l and '_fast' not in p), None)
        fast = next((p for l, p in files if p.endswith(tuple(f'_fast.s{n}p' for n in range(1, 9)))), None)
        self.report = next((p for l, p in files if p.endswith('_report.html')), None)
        self.report_btn.setEnabled(bool(self.report))
        shown = []
        for path, style, tag in ((main, Qt.SolidLine, 'openEMS'), (fast, Qt.DotLine, 'line model')):
            if not path or not os.path.exists(path):
                continue
            f, S = read_touchstone(path)
            n = S.shape[1]
            k = 0
            for j in range(n):
                for i in range(n):
                    if i < j or not np.isfinite(S[:, i, j]).all() or not np.abs(S[:, i, j]).any():
                        continue
                    y = 20 * np.log10(np.abs(S[:, i, j]) + 1e-15)
                    self.plot.plot(f / 1e9, y, pen=pg.mkPen(COLORS[k % len(COLORS)], width=2, style=style),
                                   name=f"S{i + 1}{j + 1} {tag}")
                    k += 1
            shown.append(os.path.basename(path))
        self.info.setText(', '.join(shown) if shown else 'no S-parameters in this run')


class MainWindow(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.setWindowTitle('kicad2openems')
        self.resize(1400, 900)
        self.tabs = QTabWidget()
        self.setup, self.runtab, self.results = SetupTab(self), RunTab(self), ResultsTab(self)
        for w, t in ((self.setup, 'Setup'), (self.runtab, 'Run'), (self.results, 'Results')):
            self.tabs.addTab(w, t)
        self.setCentralWidget(self.tabs)
        self.proc, self.tail, self.files, self.current = None, None, [], None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        if config:
            self.setup.load(config)

    def out_dir(self):
        return self.setup.values().get('out') or os.path.join(os.getcwd(), 'out')

    def start(self, preview):
        if self.proc is not None:
            QMessageBox.information(self, 'Busy', 'A run is going: stop it first')
            return
        vals = self.setup.values()
        if not vals.get('board'):
            QMessageBox.warning(self, 'Setup', 'Choose a board first')
            return
        self.current = Run(vals, vals.get('out') or os.path.join(os.path.dirname(vals['board']), 'out'), preview)
        self.files = []
        self.runtab.reset()
        self.tail = EventTail(self.current.events)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._stdout)
        self.proc.finished.connect(self._finished)
        argv = self.current.argv()
        self.proc.start(argv[0], argv[1:])
        self.runtab.stop_btn.setEnabled(True)
        self.runtab.stage.setText('starting…')
        if not preview:
            self.tabs.setCurrentWidget(self.runtab)
        self.timer.start(300)

    def preview(self):
        self.start(True)

    def run(self):
        self.start(False)

    def stop(self):
        if self.current:
            dirs = stop_runs(self.current.out)
            self.runtab.log.appendPlainText(f"stop: ABORT written to {len(dirs)} run folder(s)" if dirs else
                                            "stop: no openEMS run yet - ending the process")
            if not dirs and self.proc:
                self.proc.kill()

    def _stdout(self):
        data = bytes(self.proc.readAllStandardOutput()).decode('utf-8', 'replace')
        for line in data.splitlines():
            if 'Traceback' in line or 'error' in line.lower():
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

    def _finished(self, code, _status):
        self.poll()
        self.timer.stop()
        self.proc = None
        self.runtab.stop_btn.setEnabled(False)
        pngs = [p for l, p in self.files if p.endswith(('_mesh.png', '_tlines.png', '_fast.png'))]
        if pngs:
            self.setup.show_images(pngs)
        if not self.current.preview:
            self.results.show(self.files)
            self.tabs.setCurrentWidget(self.results)
        if code:
            self.runtab.stage.setText(f"ended with code {code} - see the log")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    app = QApplication.instance() or QApplication(sys.argv[:1])
    w = MainWindow(argv[0] if argv else None)
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
