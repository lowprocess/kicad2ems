"""The side panels: study set-up (left) and results (right). They only
collect input and emit signals; MainWindow wires them to the work."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from ..mesh import MeshConfig
from ..solver import SolverConfig
from ..study import StudyConfig


def _spin(lo, hi, value, step, decimals=2, suffix=''):
    s = QDoubleSpinBox()
    s.setRange(lo, hi); s.setDecimals(decimals); s.setSingleStep(step)
    s.setValue(value); s.setSuffix(suffix)
    return s


class StudyPanel(QWidget):
    """Board, ground nets, plane pairs, frequency, mesh and solver settings."""
    open_requested = Signal()
    run_requested = Signal(object)          # a StudyConfig
    cancel_requested = Signal()
    nets_changed = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)

        box = QGroupBox('Board'); f = QVBoxLayout(box)
        self.file = QLabel('No board loaded'); self.file.setWordWrap(True)
        b = QPushButton('Open KiCad / ODB++ / HyperLynx…'); b.clicked.connect(self.open_requested)
        f.addWidget(self.file); f.addWidget(b)
        lay.addWidget(box)

        box = QGroupBox('Ground (reference) nets'); f = QVBoxLayout(box)
        self.nets = QLineEdit(); self.nets.setPlaceholderText('GND, AGND')
        self.nets.editingFinished.connect(self.nets_changed)
        f.addWidget(self.nets)
        lay.addWidget(box)

        box = QGroupBox('Plane pairs'); f = QVBoxLayout(box)
        self.pairs = QListWidget()
        self.pairs.setMinimumHeight(110)
        row = QHBoxLayout()
        for text, state in (('All', Qt.Checked), ('None', Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=0, s=state: self._check_all(s))
            row.addWidget(b)
        f.addWidget(self.pairs); f.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox('Frequency'); f = QFormLayout(box)
        self.fmin = _spin(0, 1000, 1.0, 0.5, suffix=' GHz')
        self.fmax = _spin(0.01, 1000, 10.0, 0.5, suffix=' GHz')
        f.addRow('From', self.fmin); f.addRow('To', self.fmax)
        lay.addWidget(box)

        box = QGroupBox('Mesh'); f = QFormLayout(box)
        m = MeshConfig()
        self.max_cell = _spin(0, 10, m.max_cell, 0.1, 3, ' mm')
        self.max_cell.setSpecialValueText('from λ')
        self.cpw = _spin(4, 200, m.cells_per_wavelength, 2, 0, ' per λ')
        self.angle = _spin(0, 33, m.min_angle, 1, 0, '°')
        self.narrow = _spin(0, 10, m.refine_narrow, 0.1, 2, ' mm')
        self.narrow.setSpecialValueText('off')
        self.narrow_cell = _spin(0.01, 5, m.narrow_cell, 0.05, 3, ' mm')
        f.addRow('Largest cell', self.max_cell); f.addRow('Cells', self.cpw)
        f.addRow('Min. angle', self.angle)
        f.addRow('Refine copper under', self.narrow); f.addRow('…to cells of', self.narrow_cell)
        lay.addWidget(box)

        box = QGroupBox('Solver'); f = QFormLayout(box)
        self.backend = QComboBox(); self.backend.addItems(['auto', 'superlu', 'pardiso'])
        self.max_modes = QSpinBox(); self.max_modes.setRange(1, 10000)
        self.max_modes.setValue(SolverConfig().max_modes)
        f.addRow('Linear solver', self.backend); f.addRow('Max. modes / pair', self.max_modes)
        self.fringe = QCheckBox('Edge fringing (outer layers: field in air)')
        self.radiation = QCheckBox('Radiation loss in Q')
        for w in (self.fringe, self.radiation):
            w.setChecked(True); f.addRow(w)
        lay.addWidget(box)

        row = QHBoxLayout()
        self.run = QPushButton('Run'); self.run.setDefault(True)
        self.run.clicked.connect(lambda: self.run_requested.emit(self.config()))
        self.cancel = QPushButton('Cancel'); self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_requested)
        row.addWidget(self.run); row.addWidget(self.cancel)
        lay.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000)
        lay.addWidget(self.progress)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500)
        lay.addWidget(self.log, 1)

    def _check_all(self, state):
        for i in range(self.pairs.count()):
            self.pairs.item(i).setCheckState(state)

    def set_pairs(self, rows):
        """rows: [(top, bottom, label, checked)]."""
        self.pairs.clear()
        for top, bottom, label, on in rows:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, (top, bottom))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.pairs.addItem(it)

    def ref_nets(self):
        return [n.strip() for n in self.nets.text().split(',') if n.strip()]

    def config(self):
        """The StudyConfig the panel describes."""
        pairs = [self.pairs.item(i).data(Qt.UserRole) for i in range(self.pairs.count())
                 if self.pairs.item(i).checkState() == Qt.Checked]
        return StudyConfig(
            fmin=self.fmin.value() * 1e9, fmax=self.fmax.value() * 1e9,
            ref_nets=self.ref_nets() or None, pairs=pairs or None,
            mesh=MeshConfig(max_cell=self.max_cell.value(), cells_per_wavelength=self.cpw.value(),
                            min_angle=self.angle.value(), refine_narrow=self.narrow.value(),
                            narrow_cell=self.narrow_cell.value()),
            solver=SolverConfig(backend=self.backend.currentText(),
                                max_modes=self.max_modes.value(),
                                fringe=self.fringe.isChecked(),
                                radiation=self.radiation.isChecked()))

    def busy(self, on):
        self.run.setEnabled(not on); self.cancel.setEnabled(on)


class ResultsPanel(QWidget):
    """Which pair and mode to show, how to draw it, animation and export."""
    changed = Signal()                       # any display setting
    pair_changed = Signal(int)
    mode_changed = Signal(int)
    save_image = Signal()
    save_frames = Signal()
    save_data = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.pair = QComboBox(); self.pair.currentIndexChanged.connect(self.pair_changed)
        f.addRow('Plane pair', self.pair)
        lay.addLayout(f)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(0, 0)
        self.mode = QSpinBox(); self.mode.setRange(1, 1)
        self.slider.valueChanged.connect(lambda v: self.mode.setValue(v + 1))
        self.mode.valueChanged.connect(lambda v: self.slider.setValue(v - 1))
        self.slider.valueChanged.connect(self.mode_changed)
        row.addWidget(QLabel('Mode')); row.addWidget(self.slider, 1); row.addWidget(self.mode)
        lay.addLayout(row)
        self.readout = QLabel('–'); self.readout.setWordWrap(True)
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.readout)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['f (GHz)', 'Q', 'Q rad', 'PR %', 'Peak', 'Via', 'Z (Ω)'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)   # via net
        self.table.currentCellChanged.connect(lambda r, *_: r >= 0 and self.slider.setValue(r))
        self.table.setMinimumHeight(200)
        lay.addWidget(self.table, 1)

        box = QGroupBox('Display'); f = QFormLayout(box)
        self.colour = QComboBox()
        self.colour.addItems(['Signed V (nodes show)', '|V|', 'Plane current |J|'])
        self.layer = QComboBox(); self.layer.addItems(['Upper plane', 'Lower plane', 'None'])
        self.scale = _spin(0, 1e6, 0, 0.5, 2, ' × RMS')
        self.scale.setSpecialValueText("mode's own peak")
        self.decades = QSpinBox(); self.decades.setRange(0, 6)
        self.decades.setSpecialValueText('linear'); self.decades.setSuffix(' decades (log)')
        self.fill = QCheckBox('Colour fill'); self.fill.setChecked(True)
        self.overlay = QComboBox()
        self.overlay.addItems(['Current arrows', 'Contour lines', 'Arrows and contours', 'None'])
        self.vias = QCheckBox('Vias'); self.vias.setChecked(True)
        f.addRow('Colour', self.colour); f.addRow('Copper shown', self.layer)
        f.addRow('Full scale', self.scale); f.addRow('Scale', self.decades)
        f.addRow(self.fill); f.addRow('Overlay', self.overlay); f.addRow(self.vias)
        for w in (self.colour, self.layer, self.overlay):
            w.currentIndexChanged.connect(self.changed)
        for w in (self.scale, self.decades):
            w.valueChanged.connect(self.changed)
        for w in (self.fill, self.vias):
            w.toggled.connect(self.changed)
        lay.addWidget(box)

        # contour lines of the coloured quantity: V (nodal lines), |V| or the current
        box = QGroupBox('Contour lines'); f = QFormLayout(box)
        self.per = QSpinBox(); self.per.setRange(1, 50); self.per.setValue(10)
        self.per.setToolTip('Lines every 1/N of full scale (linear), or N per decade (log)')
        self.line_width = _spin(0.5, 6, 1.2, 0.2, 1, ' px')
        self.line_colour = QComboBox(); self.line_colour.addItems(['Dark', 'White', 'Colour scale'])
        self.nodal = QCheckBox('Nodal line (V = 0) in bold'); self.nodal.setChecked(True)
        f.addRow('Lines per scale', self.per); f.addRow('Width', self.line_width)
        f.addRow('Colour', self.line_colour); f.addRow(self.nodal)
        f.addRow(QLabel('Negative V dashed; ticks on the colour bar mark the levels.'))
        self.per.valueChanged.connect(self.changed)
        self.line_width.valueChanged.connect(self.changed)
        self.line_colour.currentIndexChanged.connect(self.changed)
        self.nodal.toggled.connect(self.changed)
        lay.addWidget(box)

        box = QGroupBox('Animation'); f = QFormLayout(box)
        self.animate = QCheckBox('Animate (V ∝ cos ωt, current ∝ sin ωt)')
        self.period = _spin(0.3, 20, 2.4, 0.2, 1, ' s per cycle')
        self.lock = QCheckBox('Keep the view when the mode changes')
        f.addRow(self.animate); f.addRow('Speed', self.period); f.addRow(self.lock)
        lay.addWidget(box)

        row = QHBoxLayout()
        for text, sig in (('Save image…', self.save_image), ('Save frames…', self.save_frames),
                          ('Save data…', self.save_data)):
            b = QPushButton(text); b.clicked.connect(sig); row.addWidget(b)
        lay.addLayout(row)

    def set_pairs(self, names):
        self.pair.blockSignals(True)
        self.pair.clear(); self.pair.addItems(names)
        self.pair.blockSignals(False)

    def set_modes(self, rows):
        """rows: [(f GHz, Q, Q_rad, PR %, peak, via net, Z ohm)] of the shown pair."""
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                txt = v if isinstance(v, str) else (f'{v:.4f}' if j == 0 else
                                                    '–' if v == float('inf') else f'{v:.3g}')
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if j != 5 else Qt.AlignLeft)
                self.table.setItem(i, j, it)
        n = max(len(rows), 1)
        self.slider.setRange(0, n - 1); self.mode.setRange(1, n)
        self.slider.setValue(0)

    def colour_mode(self):
        return ['signed', 'abs', 'current'][self.colour.currentIndex()]

    def display(self):
        """The display settings as BoardView.set_state keywords."""
        ov = self.overlay.currentIndex()
        return dict(colour=self.colour_mode(), vmax=self.scale.value() or None,
                    decades=self.decades.value(), fill=self.fill.isChecked(),
                    arrows=ov in (0, 2), contours=ov in (1, 2), per=self.per.value(),
                    line_width=self.line_width.value(), nodal=self.nodal.isChecked(),
                    line_colour=['dark', 'light', 'value'][self.line_colour.currentIndex()],
                    plane_sign=-1 if self.layer.currentIndex() == 1 else 1)
