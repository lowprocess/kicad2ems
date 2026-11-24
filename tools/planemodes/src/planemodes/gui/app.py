"""The main window: wires the panels and the board view to loading, running
and exporting. All computation lives outside gui/ (readers, study, raster)."""
import math
import os
import time

import numpy as np
import pyqtgraph.exporters
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QMainWindow,
                               QMessageBox, QScrollArea)

from .. import export
from ..cavity import coverage, guess_ref_nets
from ..readers import load_board
from ..study import run_study
from .board_view import BoardView
from .panels import ResultsPanel, StudyPanel
from .worker import Task, start


def _dock(title, widget, scroll=False):
    d = QDockWidget(title)
    d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
    if scroll:
        s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(widget)
        widget = s
    d.setWidget(widget)
    return d


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle('planemodes')
        self.resize(1500, 900)
        self.board, self.results, self.cfg, self.task, self.thread = None, [], None, None, None
        self.view = BoardView()
        self.setCentralWidget(self.view)
        self.study, self.res = StudyPanel(), ResultsPanel()
        self.addDockWidget(Qt.LeftDockWidgetArea, _dock('Study', self.study, scroll=True))
        self.study.setMinimumWidth(380)
        self.addDockWidget(Qt.RightDockWidgetArea, _dock('Results', self.res))
        self.res.setMinimumWidth(440)
        self._menus()
        # signals -> actions
        self.study.open_requested.connect(self.open_dialog)
        self.study.run_requested.connect(self.run)
        self.study.cancel_requested.connect(lambda: self.task and self.task.cancel())
        self.study.nets_changed.connect(self._fill_pairs)
        self.res.pair_changed.connect(self.show_pair)
        self.res.mode_changed.connect(self.show_mode)
        self.res.changed.connect(self.display_changed)
        self.res.animate.toggled.connect(self._animate)
        self.res.save_image.connect(self.save_image)
        self.res.save_frames.connect(self.save_frames)
        self.res.save_data.connect(self.save_data)
        self.anim = QTimer(self, interval=33)
        self.anim.timeout.connect(self._tick)
        self._t_last = None
        if path:
            self.load(path)

    def _menus(self):
        m = self.menuBar().addMenu('&File')
        for text, key, fn in (('Open board…', QKeySequence.Open, self.open_dialog),
                              ('Open ODB++ folder…', None, self.open_folder),
                              ('Save image…', None, self.save_image),
                              ('Save data…', QKeySequence.Save, self.save_data),
                              ('Quit', QKeySequence.Quit, self.close)):
            a = QAction(text, self)
            if key:
                a.setShortcut(key)
            a.triggered.connect(fn); m.addAction(a)
        a = QAction('Fit board', self); a.setShortcut('F'); a.triggered.connect(lambda: self.view.fit())
        self.menuBar().addMenu('&View').addAction(a)

    def log(self, text):
        self.study.log.appendPlainText(text)
        self.statusBar().showMessage(text.splitlines()[0], 8000)

    # ---- loading ---------------------------------------------------------------
    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(
            self, 'Open board', '', 'Boards (*.kicad_pcb *.tgz *.tar.gz *.tar *.zip);;All files (*)')
        if p:
            self.load(p)

    def open_folder(self):
        p = QFileDialog.getExistingDirectory(self, 'Open ODB++ job folder')
        if p:
            self.load(p)

    def load(self, path):
        self.log(f'Loading {path} …')
        self._start(lambda prog, stop: load_board(path), self._loaded)

    def _loaded(self, board):
        self.board, self.results = board, []
        self.study.file.setText(f'{os.path.basename(board.source)}\n{len(board.copper_layers)} copper '
                                f'layers, {len(board.nets())} nets, {len(board.vias):,} vias')
        for n in board.notes:
            self.log('Note: ' + n)
        if not self.study.ref_nets():
            self.study.nets.setText(', '.join(guess_ref_nets(board)))
        self._fill_pairs()
        self.res.set_pairs([]); self.res.set_modes([])
        self.view.show_result(None)
        self._drawn = None
        self.view.show_board(board, board.copper_layers[0], self.study.ref_nets())
        self.view.fit()
        self.log(f'Loaded {board.source}')

    def _fill_pairs(self):
        """List every adjacent copper pair; tick those with ground on both."""
        if self.board is None:
            return
        ref = self.study.ref_nets()
        cov = coverage(self.board, ref)
        cl = self.board.copper_layers
        rows = []
        for a, b in zip(cl, cl[1:]):
            d, er, _ = self.board.gap(a, b)
            rows.append((a, b, f'{a} / {b}   {d:.3g} mm, εr {er:.3g}, ground '
                               f'{100 * cov[a]:.0f} % / {100 * cov[b]:.0f} %',
                         cov[a] >= 0.3 and cov[b] >= 0.3))
        self.study.set_pairs(rows)

    # ---- running ---------------------------------------------------------------
    def run(self, cfg):
        if self.board is None:
            return self.log('Open a board first.')
        self.cfg = cfg
        self.study.busy(True)
        self.log(f'Running {len(cfg.pairs or [])} plane pair(s), {cfg.fmin / 1e9:g}–{cfg.fmax / 1e9:g} GHz')
        board = self.board
        self._start(lambda prog, stop: run_study(board, cfg, prog, stop), self._finished)

    def _finished(self, results):
        self.study.busy(False)
        self.results = results
        for r in results:
            self.log(f'{r.cavity.name}: {len(r.modes.f)} modes, {len(r.mesh.nodes):,} nodes, '
                     f'mesh {r.seconds["mesh"]:.1f} s, solve {r.seconds["solve"]:.1f} s'
                     + (' (stopped at the mode limit)' if r.modes.capped else ''))
        self.res.set_pairs([r.cavity.name for r in results])
        if results:
            self.show_pair(0)

    def _start(self, fn, on_done):
        self.task = Task(fn)
        # (slots that are methods of a GUI-thread object run on the GUI thread;
        # a lambda would run on the worker's)
        self.task.progress.connect(self._progress)
        self.task.done.connect(on_done)
        self.task.failed.connect(self._failed)
        self.thread = start(self.task, self)

    def _progress(self, frac, text):
        self.study.progress.setValue(int(1000 * frac))
        self.statusBar().showMessage(text)

    def _failed(self, msg):
        self.study.busy(False)
        self.log('Stopped: ' + msg)
        if msg != 'cancelled':
            QMessageBox.warning(self, 'planemodes', msg.splitlines()[0])

    # ---- showing results ---------------------------------------------------------
    def current(self):
        i = self.res.pair.currentIndex()
        return self.results[i] if 0 <= i < len(self.results) else None

    def show_pair(self, i):
        r = self.current()
        if r is None:
            return
        self._draw_board()
        self.view.show_result(r)
        m = r.modes
        rows = []
        for k in range(len(m.f)):
            top = int(np.argmax(m.zpeak[:, k])) if m.zpeak.size else -1
            top = top if top >= 0 and m.zpeak[top, k] >= 0.01 else -1    # (0.01 ohm: none)
            rows.append((m.f[k] / 1e9, m.Q[k], 100 * m.pr[k], m.peak[k],
                         r.cavity.ports[top][3] if top >= 0 else '',
                         float(m.zpeak[top, k]) if top >= 0 else 0.0))
        self.res.set_modes(rows)
        self.show_mode(0)                   # (zooms to the mode unless the view is kept)

    def show_mode(self, k):
        r = self.current()
        if r is None or not len(r.modes.f):
            self.res.readout.setText('No modes in the frequency range.')
            return
        m = r.modes
        k = min(k, len(m.f) - 1)
        self.view.set_state(mode=k)
        top = int(np.argmax(m.zpeak[:, k])) if m.zpeak.size else -1
        via = (f'strongest via {r.cavity.ports[top][3] or "(no net)"}: {m.zpeak[top, k]:.3g} Ω'
               if top >= 0 and m.zpeak[top, k] >= 0.01 else 'no via drives it')
        self.res.readout.setText(
            f'<b>{m.f[k] / 1e9:.4f} GHz</b> — mode {k + 1} of {len(m.f)} on {r.cavity.name}<br>'
            f'Q {m.Q[k]:.0f} · participation {100 * m.pr[k]:.2g} % · peak {m.peak[k]:.1f}× RMS<br>{via}')
        self.res.table.blockSignals(True); self.res.table.selectRow(k); self.res.table.blockSignals(False)
        if not self.res.lock.isChecked():
            v = np.abs(m.V[:, k])
            pts = r.mesh.nodes[v >= 0.1 * v.max()]
            x0, y0 = pts.min(0); x1, y1 = pts.max(0)
            pad = max(2.0, 0.15 * max(x1 - x0, y1 - y0))
            self.view.fit((x0 - pad, y0 - pad, x1 + pad, y1 + pad))

    def _draw_board(self):
        r = self.current()
        if self.board is None:
            return
        i = self.res.layer.currentIndex()
        layer = None if i == 2 else ((r.cavity.top if i == 0 else r.cavity.bottom) if r
                                     else self.board.copper_layers[0])
        key = (id(self.board), layer, tuple(self.study.ref_nets()), self.res.vias.isChecked())
        if key != getattr(self, '_drawn', None):           # copper paths are slow to rebuild
            self._drawn = key
            self.view.show_board(self.board, layer, key[2], key[3])

    def display_changed(self):
        self._draw_board()
        self.view.set_state(colour=self.res.colour_mode(), vmax=self.res.scale.value() or None,
                            decades=self.res.decades.value(), arrows=self.res.arrows.isChecked(),
                            plane_sign=-1 if self.res.layer.currentIndex() == 1 else 1)

    # ---- animation ---------------------------------------------------------------
    def _animate(self, on):
        self._t_last = None
        if on:
            self.view.set_state(animating=True)
            self.anim.start()
        else:
            self.anim.stop()
            self.view.set_state(phase=0.0, animating=False)

    def _tick(self):
        now = time.perf_counter()
        dt = 0 if self._t_last is None else min(0.1, now - self._t_last)
        self._t_last = now
        ph = (self.view.state['phase'] + 2 * math.pi * dt / self.res.period.value()) % (2 * math.pi)
        self.view.set_state(phase=ph)

    # ---- export ----------------------------------------------------------------
    def _export(self, path):
        ex = pyqtgraph.exporters.ImageExporter(self.view.getPlotItem())
        ex.parameters()['width'] = int(2 * self.view.width())
        ex.export(path)

    def save_image(self):
        p, _ = QFileDialog.getSaveFileName(self, 'Save image', 'mode.png', 'PNG (*.png);;JPEG (*.jpg)')
        if p:
            self._export(p)
            self.log(f'Saved {p}')

    def save_frames(self, folder=None, n=24):
        folder = folder or QFileDialog.getExistingDirectory(self, 'Folder for the frames')
        if not folder:
            return
        was = dict(phase=self.view.state['phase'], animating=self.view.state['animating'])
        for i in range(n):                     # one cycle of the current mode
            self.view.set_state(phase=2 * math.pi * i / n, animating=True)
            QApplication.processEvents()
            self._export(os.path.join(folder, f'frame_{i:03d}.png'))
        self.view.set_state(**was)
        self.log(f'Saved {n} frames to {folder}')

    def save_data(self, folder=None):
        if not self.results:
            return self.log('Run a study first.')
        folder = folder or QFileDialog.getExistingDirectory(self, 'Folder for the results')
        if folder:
            files = export.save_results(self.results, folder, self.board, self.cfg)
            self.log('Saved ' + ', '.join(os.path.basename(f) for f in files))
