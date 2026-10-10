"""
Interactive S-parameter plots (pyqtgraph): a rectangular plot (dB, linear, phase, group delay, VSWR) and, beside
it, a Smith chart of the return loss (the reflections S11, S22, ... of the picked traces). Pick the traces
(openEMS, mom25d, line model, compare file), read values under the mouse (both plots follow it), drop markers
(click either plot; drag a marker line to move it, Delete in the table to remove) and export what is shown
(CSV, PNG). Mouse wheel / drag zoom and pan; right-click for pyqtgraph's own menu.
"""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel, QTreeWidget,
                               QTreeWidgetItem, QTableWidget, QTableWidgetItem, QSplitter, QFileDialog, QMessageBox,
                               QHeaderView, QAbstractItemView, QCheckBox)
import pyqtgraph as pg
from .traces import KINDS, SMITH, transform, value_at, fmt_value, nearest, smith_grid, export_csv, default_visible

COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
STYLES = dict(solid=Qt.SolidLine, dashdot=Qt.DashDotLine, dot=Qt.DotLine, dash=Qt.DashLine)


class SPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.traces, self.markers = [], []
        self.z0 = 50.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('Show'))
        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind.currentIndexChanged.connect(self.redraw)
        bar.addWidget(self.kind)
        self.smith_on = QCheckBox('Smith chart (return loss)')
        self.smith_on.setChecked(True)
        self.smith_on.toggled.connect(lambda on: (self.smith.setVisible(on), self.redraw()))
        bar.addWidget(self.smith_on)
        for text, slot, tip in (('Fit', self.fit, 'Zoom to all shown traces'),
                                ('Clear markers', self.clear_markers, 'Remove every marker (or select one and press Delete)'),
                                ('Export CSV…', self.export_csv, 'The shown traces as the plot shows them'),
                                ('Save image…', self.save_image, 'Both plots as PNG')):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        hint = QLabel('click: marker · drag a marker to move it · wheel / drag: zoom, pan')
        hint.setStyleSheet('color: gray')
        bar.addWidget(hint)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(190)
        self.tree.itemChanged.connect(lambda *_: self.redraw())
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', 'frequency (GHz)')
        self.legend = self.plot.addLegend(offset=(-10, 10))
        self.cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen('#888', width=1, style=Qt.DotLine))
        self.dot = pg.ScatterPlotItem(size=9, pen=pg.mkPen('k'), brush=pg.mkBrush(255, 255, 255, 180))
        self.plot.scene().sigMouseMoved.connect(self._moved)
        self.plot.scene().sigMouseClicked.connect(self._clicked)
        self.smith = pg.PlotWidget()
        self.smith.setAspectLocked(True)
        self.smith.hideAxis('left')
        self.smith.hideAxis('bottom')
        self.smith.setTitle('return loss: S11, S22, … (Z for the reference impedance)', size='9pt')
        self.sdot = pg.ScatterPlotItem(size=9, pen=pg.mkPen('k'), brush=pg.mkBrush(255, 255, 255, 180))
        self.smith.scene().sigMouseMoved.connect(self._smith_moved)
        self.smith.scene().sigMouseClicked.connect(self._smith_clicked)
        self.readout = QLabel(' ')
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.readout.setWordWrap(True)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setMaximumHeight(150)
        self.table.keyPressEvent = self._table_key

        plots = QSplitter()
        plots.addWidget(self.plot)
        plots.addWidget(self.smith)
        plots.setStretchFactor(0, 3)
        plots.setStretchFactor(1, 2)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(plots, 1)
        rl.addWidget(self.readout)
        rl.addWidget(self.table)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(right)
        split.setStretchFactor(1, 4)
        lay.addWidget(split, 1)

    # ---- data ----
    def set_traces(self, traces, z0=50.0):
        """Show these traces (a new run): keeps the kind and the markers' frequencies."""
        self.traces, self.z0 = list(traces), z0
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        for k, t in enumerate(self.traces):
            if t.source not in groups:
                g = QTreeWidgetItem([t.source])
                g.setFlags(g.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                self.tree.addTopLevelItem(g)
                groups[t.source] = g
            it = QTreeWidgetItem([t.title])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if default_visible(t) else Qt.Unchecked)
            it.setData(0, Qt.UserRole, k)
            it.setToolTip(0, t.path or '')
            if t.empty:
                it.setDisabled(True)
                it.setToolTip(0, 'not computed (port not excited)')
            groups[t.source].addChild(it)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.redraw()
        self.fit()

    def visible(self):
        """The checked traces (the rectangular plot)."""
        out = []
        for g in range(self.tree.topLevelItemCount()):
            grp = self.tree.topLevelItem(g)
            for c in range(grp.childCount()):
                it = grp.child(c)
                if it.checkState(0) == Qt.Checked and not it.isDisabled():
                    out.append(self.traces[it.data(0, Qt.UserRole)])
        return out

    def reflections(self):
        """The checked reflections (the Smith chart)."""
        return [t for t in self.visible() if t.i == t.j] if self.smith_on.isChecked() else []

    def _color(self, t):
        n = max(int(np.sqrt(len([x for x in self.traces if x.source == t.source]))), 1)
        return COLORS[(t.j * n + t.i) % len(COLORS)]

    def _pen(self, t):
        return pg.mkPen(self._color(t), width=2, style=STYLES[t.style])

    # ---- drawing ----
    def redraw(self):
        kind = self.kind.currentText()
        self.plot.clear()
        self.legend.clear()
        self.plot.setLabel('left', kind)
        for t in self.visible():
            x, y = transform(kind, t)
            self.plot.plot(x, y, pen=self._pen(t), name=t.label)
        self.plot.addItem(self.cursor, ignoreBounds=True)
        self.plot.addItem(self.dot)
        self.dot.setData([], [])
        self.smith.clear()
        for x, y in smith_grid():
            self.smith.plot(x, y, pen=pg.mkPen('#cccccc', width=1))
        for r in (0.2, 0.5, 1, 2, 5):                                # resistance labels on the real axis
            lab = pg.TextItem(f"{r:g}", color='#999999', anchor=(1.1, 0))
            lab.setPos((r - 1) / (r + 1), 0)
            self.smith.addItem(lab)
        for t in self.reflections():
            x, y = transform(SMITH, t)
            self.smith.plot(x, y, pen=self._pen(t))
        self.smith.addItem(self.sdot)
        self.sdot.setData([], [])
        for m in self.markers:
            self._place_marker(m)
        self._update_table()

    def fit(self):
        self.plot.enableAutoRange()
        self.smith.setRange(xRange=(-1.05, 1.05), yRange=(-1.05, 1.05), padding=0.02)

    # ---- cursor ----
    def _moved(self, pos):
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        p = self.plot.getPlotItem().vb.mapSceneToView(pos)
        self._show_at(p.x() * 1e9)

    def _show_at(self, f, head=''):
        """Cursor, dots on both plots and the readout at frequency f (Hz)."""
        kind = self.kind.currentText()
        trs, refl = self.visible(), self.reflections()
        self.cursor.setPos(f / 1e9)
        vals = [(t, value_at(kind, t, f)) for t in trs]
        self.dot.setData([f / 1e9] * len(vals), [v for _, v in vals])
        g = [value_at(SMITH, t, f) for t in refl]
        self.sdot.setData([v.real for v in g], [v.imag for v in g])
        parts = [f"<span style='color:{self._color(t)}'>{t.label}</span> {fmt_value(kind, v)}" for t, v in vals]
        parts += [f"<span style='color:{self._color(t)}'>{t.param} {t.source}</span> Z "
                  + fmt_value(SMITH, v, self.z0).split('Z ')[-1] for t, v in zip(refl, g) if np.isfinite(v)]
        self.readout.setText(f"{head}<b>{f / 1e9:.4f} GHz</b> &nbsp; " + ' &nbsp; '.join(parts))

    def _smith_moved(self, pos):
        if not self.smith.sceneBoundingRect().contains(pos):
            return
        refl = self.reflections()
        if not refl:
            return
        p = self.smith.getPlotItem().vb.mapSceneToView(pos)
        t, k = nearest(refl, p.x(), p.y())
        self._show_at(float(t.f[k]), head=f"nearest: {t.label} &nbsp; ")

    # ---- markers ----
    def _snap(self, f):
        trs = self.visible()
        if not trs:
            return f
        grid = trs[0].f
        return float(grid[np.argmin(np.abs(grid - f))])                  # on a computed frequency

    def _clicked(self, ev):
        if ev.button() != Qt.LeftButton or ev.double() or not self.visible():
            return
        if not self.plot.sceneBoundingRect().contains(ev.scenePos()):
            return
        p = self.plot.getPlotItem().vb.mapSceneToView(ev.scenePos())
        self.add_marker(self._snap(p.x() * 1e9))

    def _smith_clicked(self, ev):
        refl = self.reflections()
        if ev.button() != Qt.LeftButton or ev.double() or not refl:
            return
        p = self.smith.getPlotItem().vb.mapSceneToView(ev.scenePos())
        t, k = nearest(refl, p.x(), p.y())
        self.add_marker(float(t.f[k]))

    def add_marker(self, f):
        m = dict(f=f, name=f"M{1 + max([int(x['name'][1:]) for x in self.markers] or [0])}")
        self.markers.append(m)
        self._place_marker(m)
        self._update_table()
        return m

    def _place_marker(self, m):
        line = pg.InfiniteLine(pos=m['f'] / 1e9, angle=90, movable=True, pen=pg.mkPen('#444', width=1, style=Qt.DashLine),
                               label=m['name'], labelOpts=dict(position=0.95, color='k', fill=(255, 255, 255, 200)))
        line.sigPositionChanged.connect(lambda ln, m=m: self._marker_moved(m, ln))
        self.plot.addItem(line, ignoreBounds=True)
        sc = pg.ScatterPlotItem(size=10, symbol='d', pen=pg.mkPen('k'), brush=pg.mkBrush('#ffd700'))
        txt = pg.TextItem(m['name'], color='k', anchor=(-0.2, 1))
        self.smith.addItem(sc)
        self.smith.addItem(txt)
        m.update(line=line, sc=sc, txt=txt)
        self._move_smith_marker(m)

    def _move_smith_marker(self, m):
        g = [value_at(SMITH, t, m['f']) for t in self.reflections()]
        g = [v for v in g if np.isfinite(v)]
        m['sc'].setData([v.real for v in g], [v.imag for v in g])
        m['txt'].setVisible(bool(g))
        if g:
            m['txt'].setPos(g[0].real, g[0].imag)

    def _marker_moved(self, m, line):
        m['f'] = line.value() * 1e9
        self._move_smith_marker(m)
        self._update_table()

    def clear_markers(self):
        self.markers = []
        self.redraw()

    def _table_key(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
                if r < len(self.markers):
                    del self.markers[r]
            self.redraw()
        else:
            QTableWidget.keyPressEvent(self.table, ev)

    def _update_table(self):
        kind = self.kind.currentText()
        trs, refl = self.visible(), self.reflections()
        cols = [(t, kind) for t in trs] + [(t, SMITH) for t in refl]
        self.table.setColumnCount(1 + len(cols))
        self.table.setHorizontalHeaderLabels(['f (GHz)'] + [t.label if k != SMITH else f"{t.label} (Smith)"
                                                            for t, k in cols])
        self.table.setRowCount(len(self.markers))
        self.table.setVerticalHeaderLabels([m['name'] for m in self.markers])
        for r, m in enumerate(self.markers):
            self.table.setItem(r, 0, QTableWidgetItem(f"{m['f'] / 1e9:.4f}"))
            for c, (t, k) in enumerate(cols):
                self.table.setItem(r, 1 + c, QTableWidgetItem(fmt_value(k, value_at(k, t, m['f']), self.z0)))
        self.table.setVisible(bool(self.markers))

    # ---- export ----
    def export_csv(self, path=None):
        trs = self.visible()
        if not trs:
            QMessageBox.information(self, 'Export', 'No traces shown')
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, 'Export the shown traces', 'sparams.csv', 'CSV (*.csv)')
        if path:
            export_csv(path, trs, self.kind.currentText(), reflections=self.reflections())

    def save_image(self, path=None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, 'Save the plots', 'sparams.png', 'PNG (*.png)')
        if path:
            self.plot.parentWidget().grab().save(path)
