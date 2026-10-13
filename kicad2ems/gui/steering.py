"""
The Results page's Beam steering bar, for an antenna result with each port's far field kept (mom25d's
<name>_mom25d_array.npz, phased.py): the drive (PORT[=amplitude][@phase] per port, the run's to start with) and
a phase step added from each driven port to the next, recombined at once, without solving again. The plots
(gain over frequency, the far-field cuts) and the 3D view's pattern follow; Animate sweeps the step back and
forth.

    bar = SteeringBar()
    bar.set_data(res, data)          # the antenna result (JSON dict) and phased.load's data, or None: hidden
    bar.steered.connect(lambda res, sphere: ...)
"""
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout)
from .. import phased

STEP_MAX = 180          # deg: the phase step's range, either way
ANIM_MS = 60            # a frame of Animate
ANIM_DEG = 3            # the step's change per frame


class SteeringBar(QGroupBox):
    steered = Signal(object, object)                                # (antenna result, sphere) of the drive

    def __init__(self):
        super().__init__('Beam steering')
        self.setToolTip("Each port's far field was kept: any drive is their sum (exact, with the coupling between "
                        "the elements), recombined without solving again")
        self.res, self.data, self._dir = None, None, 1
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('Drive'))
        self.drive = QLineEdit()
        self.drive.setToolTip("PORT[=amplitude][@phase in degrees] per driven port, comma-separated (incident "
                              "waves; the ports not named are terminated), e.g. P1@0, P2=0.5@90")
        self.drive.editingFinished.connect(self.update)
        row.addWidget(self.drive, 2)
        row.addWidget(QLabel('Phase step'))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(-STEP_MAX, STEP_MAX)
        self.slider.setToolTip("added from each driven port to the next (in port order) on top of the drive's "
                               "phases: the beam tilts across the ports")
        self.spin = QSpinBox()
        self.spin.setRange(-STEP_MAX, STEP_MAX)
        self.spin.setSuffix('°')
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.spin.valueChanged.connect(lambda *_: self.update())
        row.addWidget(self.slider, 3)
        row.addWidget(self.spin)
        self.anim = QPushButton('Animate')
        self.anim.setCheckable(True)
        self.anim.setToolTip(f"sweep the phase step back and forth over ±{STEP_MAX}°")
        self.anim.toggled.connect(self._animate)
        row.addWidget(self.anim)
        self.reset = QPushButton('Reset')
        self.reset.setToolTip("the run's drive, no phase step")
        self.reset.clicked.connect(self._reset)
        row.addWidget(self.reset)
        lay.addLayout(row)
        self.readout = QLabel()
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.readout.setWordWrap(True)
        lay.addWidget(self.readout)
        self.timer = QTimer(self)
        self.timer.setInterval(ANIM_MS)
        self.timer.timeout.connect(self._frame)
        self.hide()

    def set_data(self, res, data):
        """An antenna result and its per-port far fields (phased.load), or None: the bar hidden."""
        self.anim.setChecked(False)
        self.res, self.data = res, data
        self.setVisible(data is not None)
        if data is not None:
            self._reset(emit=False)

    def _reset(self, *_, emit=True):
        d = self.data
        drive = (self.res or {}).get('drive') or phased.format_drive(d['drive'], d['ports'])
        self.drive.setText(', '.join(drive))
        self.spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.spin.setValue(0)
        self.slider.setValue(0)
        self.spin.blockSignals(False)
        self.slider.blockSignals(False)
        self.update(emit=emit)

    def weights(self):
        """The incident waves of the drive and the phase step; ValueError for a drive that does not parse."""
        a = phased.parse_drive([x for x in self.drive.text().split(',')], self.data['ports'])
        if not np.any(np.abs(a) > 0):
            raise ValueError("drive: no port driven")
        return phased.steer(a, self.spin.value())

    def update(self, emit=True):
        if self.data is None:
            return None
        try:
            a = self.weights()
        except ValueError as e:
            self.readout.setText(f"<span style='color:#c62828'>{e}</span>")
            return None
        c = phased.combine(self.data, a)
        res = dict(self.res or {}, rows=c['rows'], peak=c['peak'], drive=phased.format_drive(a, self.data['ports']))
        pk, names = c['peak'], self.data['ports']
        ip = int(np.argmin(np.abs(self.data['freq'] - pk['f'])))
        act = ', '.join(f"{n} {20 * np.log10(max(abs(v), 1e-15)):.1f} dB" for n, v in zip(names, c['active'][ip])
                        if np.isfinite(v))
        r = c['rows'][ip]
        g = f"{pk['gain_dbi']:.2f} dBi" if pk['gain_dbi'] is not None else '-'
        self.readout.setText(f"{', '.join(res['drive'])}: beam at θ {pk['theta']:g}°, φ {pk['phi']:g}°, gain {g}, "
                             f"realized {r['Grealized_dbi'] or float('nan'):.2f} dBi at {pk['f'] / 1e9:.4g} GHz; "
                             f"active reflection {act}")
        if emit:
            self.steered.emit(res, c['sphere'])
        return res, c['sphere']

    def _animate(self, on):
        if on:
            self.timer.start()
        else:
            self.timer.stop()

    def _frame(self):
        v = self.spin.value() + self._dir * ANIM_DEG
        if abs(v) > STEP_MAX:
            self._dir = -self._dir
            v = self.spin.value() + self._dir * ANIM_DEG
        self.spin.setValue(v)
