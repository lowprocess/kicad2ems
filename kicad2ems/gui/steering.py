"""
The Results page's Beam steering bar, for an antenna result with each port's far field kept (mom25d's
<name>_mom25d_array.npz, phased.py): the drive (PORT[=amplitude][@phase] per port, the run's to start with) and
phase steps on top of it, recombined at once, without solving again. Without a grid the step goes from each
driven port to the next (a line of elements); with one (COLSxROWS, or PORT:COL,ROW per port: a 2D array such as
a 4 x 4 patch array with a feed line per patch) there are two, X per column and Y per row, and a phase map shows
each element's phase and active reflection. The plots (gain over frequency, the far-field cuts) and the 3D
view's pattern follow; Animate sweeps X, Y, or both round a circle (a conical scan).

    bar = SteeringBar()              # compact=True: stacked, for the 3D view's side panel
    bar.set_data(res, data)          # the antenna result (JSON dict) and phased.load's data, or None: hidden
    bar.steered.connect(lambda res, sphere: ...)
    other.mirror(bar)                # another bar showing the same drive, grid and steps (Results page's, 3D view's)
"""
import math
import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)
from .. import phased

STEP_MAX = 180          # deg: the phase steps' range, either way
ANIM_MS = 60            # a frame of Animate
ANIM_DEG = 3            # a step's change per frame (Circle: the scan angle's)
CIRCLE_R = 90           # deg: Circle's radius when both steps are about zero
ANIM_MODES = ('X', 'Y', 'Circle')


class PhaseMap(QWidget):
    """The 2D array's elements as a grid (column right, row down: row 0 the top row), each coloured by its
    phase (the hue; grey: not driven) with its port, phase and active reflection."""
    CELL = 62           # px: a cell's width at most
    ASPECT = 0.86       # a cell's height / width
    MIN_TEXT = 34       # px: narrower cells show the colour alone

    def __init__(self):
        super().__init__()
        self.cells = []                                             # (col, row, name, phase deg or None, dB)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setToolTip("each element of the array: its port, phase (the colour) and active reflection (with its "
                        "neighbours driven too)")

    def set(self, names, grid, a, active):
        self.cells = []
        for n, v, act in zip(names, a, active):
            if n in (grid or {}):
                c, r = grid[n]
                ph = math.degrees(math.atan2(v.imag, v.real)) if abs(v) > 0 else None
                db = 20 * math.log10(max(abs(act), 1e-15)) if np.isfinite(act) else None
                self.cells.append((c, r, n, ph, db))
        self._fit()
        self.update()

    def _fit(self, width=None):
        width = width or (self.width() if self.isVisible() and self.width() > 1 else self.sizeHint().width())
        self.setFixedHeight(self.heightForWidth(width))

    def _dims(self):
        cols = 1 + max(c for c, *_ in self.cells)
        rows = 1 + max(r for _, r, *_ in self.cells)                 # cells at (column, row) x the cell size
        w = min(self.CELL, max(8.0, (self.width() - 2) / cols))
        return cols, rows, w, w * self.ASPECT

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        if not self.cells:
            return 0
        cols = 1 + max(c for c, *_ in self.cells)
        rows = 1 + max(r for _, r, *_ in self.cells)
        return int(math.ceil(rows * min(self.CELL, max(8.0, (width - 2) / cols)) * self.ASPECT)) + 2

    def sizeHint(self):
        if not self.cells:
            return QSize(0, 0)
        cols = 1 + max(c for c, *_ in self.cells)
        return QSize(int(cols * self.CELL) + 2, self.heightForWidth(int(cols * self.CELL) + 2))

    def resizeEvent(self, ev):
        self._fit(ev.size().width())
        super().resizeEvent(ev)

    def paintEvent(self, _):
        if not self.cells:
            return
        _, _, w, h = self._dims()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = p.font()
        f.setPointSizeF(max(6.0, min(f.pointSizeF(), h / 5)))
        p.setFont(f)
        for c, r, name, ph, db in self.cells:
            rect = QRectF(1 + c * w, 1 + r * h, w - 2, h - 2)
            fill = QColor(150, 150, 150, 90) if ph is None else QColor.fromHsvF((ph % 360) / 360, 0.55, 0.95)
            p.setBrush(fill)
            p.setPen(QPen(self.palette().mid().color(), 1))
            p.drawRoundedRect(rect, 3, 3)
            if w >= self.MIN_TEXT:
                p.setPen(Qt.black if ph is not None else self.palette().text().color())
                lines = [name, '-' if ph is None else f"{ph:.0f}°"]
                if db is not None:
                    lines.append(f"{db:.1f} dB" if db > -60 else "< −60 dB")
                p.drawText(rect, Qt.AlignCenter, '\n'.join(lines))
        p.end()


class SteeringBar(QGroupBox):
    steered = Signal(object, object)                                # (antenna result, sphere) of the drive

    def __init__(self, compact=False):
        super().__init__('Beam steering')
        self.setToolTip("Each port's far field was kept: any drive is their sum (exact, with the coupling between "
                        "the elements), recombined without solving again")
        self.res, self.data, self._dir, self._t, self._r = None, None, 1, 0.0, CIRCLE_R
        self._grid = None
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Drive'))
        self.drive = QLineEdit()
        self.drive.setToolTip("PORT[=amplitude][@phase in degrees] per driven port, comma-separated (incident "
                              "waves; the ports not named are terminated), e.g. P1@0, P2=0.5@90")
        self.drive.editingFinished.connect(self.update)
        row.addWidget(self.drive, 2)
        if compact:                                                 # a narrow side panel: a row each
            lay.addLayout(row)
            row = QHBoxLayout()
        row.addWidget(QLabel('Grid'))
        self.grid = QLineEdit()
        self.grid.setPlaceholderText('a line of ports')
        self.grid.setToolTip("the ports' places in a 2D array, for an X step per column and a Y step per row: "
                             "COLSxROWS (the ports in order fill the rows, top row first: 4x4 for P1..P16), or "
                             "PORT:COL,ROW per port separated by spaces (e.g. P1:0,0 P2:1,0 P3:0,1 P4:1,1; "
                             "fractional places for an offset lattice). Empty: one step from each driven port to "
                             "the next")
        self.grid.editingFinished.connect(self.update)
        row.addWidget(self.grid, 1)
        lay.addLayout(row)

        def step_row(label, tip):
            r = QHBoxLayout()
            lab = QLabel(label)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-STEP_MAX, STEP_MAX)
            slider.setToolTip(tip)
            spin = QSpinBox()
            spin.setRange(-STEP_MAX, STEP_MAX)
            spin.setSuffix('°')
            spin.setToolTip(tip)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            spin.valueChanged.connect(lambda *_: self.update())
            r.addWidget(lab)
            if compact:                                             # the slider on a row of its own
                r.addStretch(1)
                r.addWidget(spin)
                lay.addLayout(r)
                r = QHBoxLayout()
                r.addWidget(slider)
            else:
                r.addWidget(slider, 3)
                r.addWidget(spin)
            lay.addLayout(r)
            return lab, slider, spin, r

        self.label_x, self.slider, self.spin, _ = step_row(
            'Phase step', "added on top of the drive's phases: from each driven port to the next (in port order), "
                          "or with a grid per column: the beam tilts across the ports")
        self.label_y, self.slider_y, self.spin_y, _ = step_row(
            'Y step', "with a grid: added per row (row 0 the top one): the beam tilts across the rows")
        row = QHBoxLayout()
        self.anim = QPushButton('Animate')
        self.anim.setCheckable(True)
        self.anim.setToolTip(f"sweep the phase step back and forth over ±{STEP_MAX}° (with a grid: X, Y, or both "
                             f"round a circle, a conical scan)")
        self.anim.toggled.connect(self._animate)
        row.addWidget(self.anim)
        self.anim_mode = QComboBox()
        self.anim_mode.addItems(ANIM_MODES)
        self.anim_mode.setToolTip("what Animate sweeps: the X step, the Y step, or both round a circle (the "
                                  f"steps' size at the start, {CIRCLE_R}° when about zero)")
        self.anim_mode.currentIndexChanged.connect(lambda *_: self._restart_circle())
        row.addWidget(self.anim_mode)
        self.reset = QPushButton('Reset')
        self.reset.setToolTip("the run's drive and grid, no phase steps")
        self.reset.clicked.connect(self._reset)
        row.addWidget(self.reset)
        row.addStretch(1)
        lay.addLayout(row)
        self.map = PhaseMap()
        lay.addWidget(self.map)
        self.readout = QLabel()
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.readout.setWordWrap(True)
        lay.addWidget(self.readout)
        self.timer = QTimer(self)
        self.timer.setInterval(ANIM_MS)
        self.timer.timeout.connect(self._frame)
        self._show_2d(False)
        self.hide()

    # ---- state -----------------------------------------------------------------------------------------
    def set_data(self, res, data):
        """An antenna result and its per-port far fields (phased.load), or None: the bar hidden."""
        self.anim.setChecked(False)
        self.res, self.data = res, data
        self.setVisible(data is not None)
        if data is not None:
            self._reset(emit=False)

    def _show_2d(self, on):
        for w in (self.label_y, self.slider_y, self.spin_y, self.anim_mode, self.map):
            w.setVisible(on)
        self.label_x.setText('X step' if on else 'Phase step')
        if not on and self.anim_mode.currentIndex() != 0:
            self.anim_mode.setCurrentIndex(0)

    def _set_steps(self, x, y):
        """Both steps (and their sliders) without an update each."""
        for w, v in ((self.spin, x), (self.slider, x), (self.spin_y, y), (self.slider_y, y)):
            w.blockSignals(True)
            w.setValue(int(round(v)))
            w.blockSignals(False)

    def _parse_grid(self):
        """The grid field's places (phased.parse_grid), shown or hidden with it; ValueError for a bad one."""
        g = phased.parse_grid(self.grid.text(), self.data['ports'])
        self._grid = g
        self._show_2d(bool(g))
        return g

    def _reset(self, *_, emit=True):
        d = self.data
        drive = (self.res or {}).get('drive') or phased.format_drive(d['drive'], d['ports'])
        self.drive.setText(', '.join(drive))
        self.grid.setText((self.res or {}).get('grid') or '')
        self._set_steps(0, 0)
        self.update(emit=emit)

    def mirror(self, other):
        """Show another bar's drive, grid and phase steps (its plots are already drawn: nothing emitted)."""
        if self.data is None:
            return
        self.drive.setText(other.drive.text())
        self.grid.setText(other.grid.text())
        self.anim_mode.blockSignals(True)
        self.anim_mode.setCurrentIndex(other.anim_mode.currentIndex())
        self.anim_mode.blockSignals(False)
        self._set_steps(other.spin.value(), other.spin_y.value())
        self.update(emit=False)

    def follow_animation(self, on):
        """Another bar started or stopped Animate: the button shows it, this bar's own timer stays off."""
        self.timer.stop()
        self.anim.blockSignals(True)
        self.anim.setChecked(on)
        self.anim.blockSignals(False)

    def weights(self):
        """The incident waves of the drive, the grid and the phase steps; ValueError for a drive or grid that
        does not parse."""
        names = self.data['ports']
        a = phased.parse_drive([x for x in self.drive.text().split(',')], names)
        if not np.any(np.abs(a) > 0):
            raise ValueError("drive: no port driven")
        g = self._parse_grid()
        return phased.steer2d(a, names, g, self.spin.value(), self.spin_y.value() if g else 0)

    def update(self, emit=True):
        if self.data is None:
            return None
        try:
            a = self.weights()
        except ValueError as e:
            self.readout.setText(f"<span style='color:#c62828'>{e}</span>")
            return None
        c = phased.combine(self.data, a)
        names = self.data['ports']
        res = dict(self.res or {}, rows=c['rows'], peak=c['peak'], drive=phased.format_drive(a, names))
        if self._grid:
            res['grid'] = phased.format_grid(self._grid, names)
        else:
            res.pop('grid', None)
        pk = c['peak']
        ip = int(np.argmin(np.abs(self.data['freq'] - pk['f'])))
        act = [(n, 20 * np.log10(max(abs(v), 1e-15))) for n, v in zip(names, c['active'][ip]) if np.isfinite(v)]
        r = c['rows'][ip]
        g = f"{pk['gain_dbi']:.2f} dBi" if pk['gain_dbi'] is not None else '-'
        head = (f"steps X {self.spin.value()}°, Y {self.spin_y.value()}°" if self._grid else
                ', '.join(res['drive']))
        if len(act) > 4:                                            # a large array: the range, the worst named
            worst = max(act, key=lambda t: t[1])
            act_s = f"{min(v for _, v in act):.1f} to {worst[1]:.1f} dB (worst {worst[0]})"
        else:
            act_s = ', '.join(f"{n} {v:.1f} dB" for n, v in act)
        self.readout.setText(f"{head}: beam at θ {pk['theta']:g}°, φ {pk['phi']:g}°, gain {g}, "
                             f"realized {r['Grealized_dbi'] or float('nan'):.2f} dBi at {pk['f'] / 1e9:.4g} GHz; "
                             f"active reflection {act_s}")
        if self._grid:
            self.map.set(names, self._grid, a, c['active'][ip])
        if emit:
            self.steered.emit(res, c['sphere'])
        return res, c['sphere']

    # ---- Animate ---------------------------------------------------------------------------------------
    def _restart_circle(self):
        x, y = self.spin.value(), self.spin_y.value()
        rr = math.hypot(x, y)
        self._r = min(rr, STEP_MAX) if rr >= ANIM_DEG else CIRCLE_R
        self._t = math.atan2(y, x) if rr >= ANIM_DEG else 0.0

    def _animate(self, on):
        if on:
            self._restart_circle()
            self.timer.start()
        else:
            self.timer.stop()

    def _frame(self):
        mode = ANIM_MODES[self.anim_mode.currentIndex()] if self._grid else 'X'
        if mode == 'Circle':
            self._t += math.radians(ANIM_DEG)
            self._set_steps(self._r * math.cos(self._t), self._r * math.sin(self._t))
            self.update()
            return
        spin = self.spin_y if mode == 'Y' else self.spin
        v = spin.value() + self._dir * ANIM_DEG
        if abs(v) > STEP_MAX:
            self._dir = -self._dir
            v = spin.value() + self._dir * ANIM_DEG
        spin.setValue(v)
