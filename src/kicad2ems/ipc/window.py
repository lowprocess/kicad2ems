"""
The plugin's window: the board KiCad has open, seen from above, its traces
coloured by their impedance (Z0) and read on hover; a region to simulate (drawn,
taken from KiCad's selection, or set up around a set of nets with ports at their
components) and ports (clicked on traces and pads); a run of kicad2ems on them,
its S-parameters against the line model's, or the same set-up opened in
kicad2ems's GUI.

    python plugin/kicad2ems_plugin.py  # what KiCad's toolbar button runs
                                       # (install.py)
"""
import os, re, sys

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess, QUrl, QEvent
from PySide6.QtGui import (QColor, QIcon, QDesktopServices, QLinearGradient,
                           QBrush, QPainter)
from PySide6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QCheckBox, QDoubleSpinBox, QComboBox,
    QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QSplitter,
    QScrollArea, QMessageBox, QFormLayout, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView)
import pyqtgraph as pg

from kicad2ems.core.solvers import registry
from kicad2ems.core import overlay as ov
from kicad2ems.core.runs import Run, EventTail, kill_tree, stop_runs
from kicad2ems.gui.planview import PlanView
from kicad2ems.gui.sectionview import SectionView
from . import model as md
from .netclass import partner

# the solver menu: auto, each full-wave solver installed (kicad2ems's registry),
# the line model
SOLVERS = ([('auto (the fastest full-wave + line model)', ['auto'])] + [
    (sv.label, [n]) for n, sv in registry().items() if sv.consumes == 'board'
] + [('line model only', ['lines'])])


def _button(text, slot=None, tip='', checkable=False):
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def _label(text, colour=None):
    """A word-wrapped label (in `colour`: a note)."""
    lab = QLabel(text)
    lab.setWordWrap(True)
    if colour:
        lab.setStyleSheet(f'color: {colour}')
    return lab


def _row(*widgets, stretch=None):
    """A row of widgets, `stretch` the one that takes the room."""
    row = QHBoxLayout()
    for w in widgets:
        row.addWidget(w, 1 if w is stretch else 0)
    return row


class Loader(QThread):
    """Reads the board (Plan) and maps the impedance of `nets`, a group of nets
    at a time, off the GUI thread. With a plan already read: only the map. nets
    None: none yet (the window chooses once the board is read)."""
    loaded = Signal(object)                 # the Plan
    progress = Signal(object, object)       # the group's nets, its map sections
    failed = Signal(str)

    def __init__(self, text, path, plan=None, nets=None, classes=None,
                 cache=None, field=False):
        super().__init__()
        self.text, self.path, self.plan, self.nets, self.classes = (text, path,
                                                                    plan, nets,
                                                                    classes)
        self.cache, self.field, self._stop = cache, field, False

    def stop(self):
        self._stop = True

    def run(self):
        plan = self.plan
        if plan is None:
            try:
                plan = md.Plan(self.text, self.path, classes=self.classes)
            except Exception as e:  # a board kicad2ems cannot read: say why
                self.failed.emit(f"{type(e).__name__}: {e}")
                return
            self.loaded.emit(plan)
        if not self.nets:
            return
        try:
            n = 0
            for _, _, chunk in plan.map_steps(nets=self.nets,
                                              stop=lambda: self._stop,
                                              cache=self.cache,
                                              field=self.field):
                new = plan.lines['elements'][n:]
                n = len(plan.lines['elements'])
                self.progress.emit(list(chunk), list(new))
        except Exception as e:
            self.failed.emit(f"impedance map: {type(e).__name__}: {e}")


class Legend(QWidget):
    """The colour scale of the map: blue below each section's target impedance
    (its net class's, else the reference), grey on it, red above; `half` a
    fraction of the target."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(30)
        self.half = 0.1

    def set(self, half):
        self.half = half
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, w, 0)
        for i in range(11):
            r, gg, b = ov.z0_colour((i / 5 - 1) * self.half, 0.0, self.half)
            g.setColorAt(i / 10, QColor.fromRgbF(r, gg, b))
        p.fillRect(0, 0, w, h - 14, QBrush(g))
        p.setPen(QColor('#333'))
        for frac, text in ((0.0, f"{-self.half * 100:.0f} %"),
                           (0.5, 'on target'), (1.0,
                                                f"+{self.half * 100:.0f} %")):
            x = int(frac * (w - 1))
            p.drawText(max(0, min(w - 60, x - 26)), h - 2, text)


class Results(QWidget):
    """A run's S-parameters: |S| in dB of each full-wave solver (solid) and of
    the line model (dashed, the same colour per term), and the largest
    differences between them."""
    COLOURS = [
        '#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf',
        '#8c564b', '#e377c2'
    ]

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        self.title = _label('No results yet: set up a run and press Run')
        v.addWidget(self.title)
        self.plot = pg.PlotWidget(background='w')
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel('bottom', 'frequency', 'GHz')
        self.plot.setLabel('left', '|S|', 'dB')
        self.plot.addLegend(offset=(-10, 10))
        v.addWidget(self.plot, 3)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['against the line model', 'max d|S| dB', 'at GHz', 'max |dS|'])
        self.table.horizontalHeaderItem(1).setToolTip(
            'The largest difference of |S| in dB where the full-wave |S| '
            'is above -20 dB (below that a dB difference says little)')
        self.table.horizontalHeaderItem(3).setToolTip(
            'The largest complex difference of S (linear): phase too')
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        v.addWidget(self.table, 1)
        self.res = None

    def show_results(self, res, title=''):
        self.res = res
        self.plot.clear()
        self.table.setRowCount(0)
        if not res:
            self.title.setText('No results: the run solved nothing')
            return
        full = [s for s in res['solved'] if not s['line']]
        line = next((s for s in res['solved'] if s['line']), None)
        styles = [Qt.SolidLine, Qt.DotLine, Qt.DashDotLine]
        ts = md.terms(res['solved'][0]['names'])
        for k, (i, j, lab) in enumerate(ts):
            c = self.COLOURS[k % len(self.COLOURS)]
            for n, sv in enumerate(full):
                if sv['names'] == res['solved'][0]['names']:
                    self.plot.plot(
                        sv['f'] / 1e9, md.db(sv['S'][:, i, j]),
                        name=f"{lab} {sv['key']}",
                        pen=pg.mkPen(c, width=2, style=styles[n % len(styles)]))
            if line is not None and line['names'] == res['solved'][0]['names']:
                self.plot.plot(line['f'] / 1e9, md.db(line['S'][:, i, j]),
                               name=f"{lab} line model",
                               pen=pg.mkPen(c, width=1.5, style=Qt.DashLine))
        rows = md.comparison(res)
        self.table.setRowCount(len(rows))
        for r, (lab, ddb, f, dv) in enumerate(rows):
            for c, text in enumerate((lab, '-' if ddb is None else f"{ddb:.2f}",
                                      f"{f:.3g}", f"{dv:.3f}")):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        what = ', '.join(
            f"{s['label']}"
            + (f" ({s['seconds']:.0f} s)" if s.get('seconds') else '')
            for s in res['solved'])
        self.title.setText(
            (title + ': ' if title else '') + what
            + ('' if line else '  - no line model result to compare with')
            + ''.join(f"\n{w}" for w in res.get('warnings', [])[:3]))


class PluginPlan(PlanView):
    """The plan view with the plugin's tools: hover a section to read its Z0,
    click it to select its track in KiCad; 'region': two clicks give the
    corners; 'port': a click puts a port on the trace or pad under it; 'net':
    a click picks the net of the trace under it."""
    # x, y (model mm), the tolerance (mm) at this zoom
    clicked_at = Signal(float, float, float)
    # faint: the map stands out
    copper_colour, other_colour = (QColor(214, 140, 64,
                                          70), QColor(214, 140, 64, 20))

    def __init__(self):
        super().__init__()
        self.mode, self.region, self.ports, self.corner = ('inspect', None, [],
                                                           None)
        self._over = []
        self.mesh.hide()
        for w in self.findChildren(QLabel):
            if w.text() == 'Mesh':
                w.hide()
        self.band = pg.PlotCurveItem(
            pen=pg.mkPen('#2a7', width=1.5, style=Qt.DashLine))
        self.band.setZValue(40)
        self.plot.addItem(self.band)

    def set_mode(self, mode):
        self.mode, self.corner = mode, None
        self.band.setData([], [])
        self.plot.setCursor(Qt.CrossCursor if mode !=
                            'inspect' else Qt.ArrowCursor)

    def redraw(self, *a):
        super().redraw(*a)
        for it in self._over:
            self.plot.removeItem(it)
        self._over = []
        if self.region:
            x0, y0, x1, y1 = self.region  # KiCad mm: y down
            c = pg.PlotCurveItem([x0, x1, x1, x0, x0],
                                 [-y0, -y0, -y1, -y1, -y0],
                                 pen=pg.mkPen('#2a7', width=2))
            c.setZValue(40)
            self.plot.addItem(c)
            self._over.append(c)
        lay = self.current_layer()
        for p in self.ports:
            if p['layer'] != lay:
                continue
            t = pg.TextItem(p['name'], color='w', fill=pg.mkBrush('#c0392b'),
                            anchor=(0.5, 0.5))
            t.setPos(p['x'], p['y'])
            t.setZValue(45)
            self.plot.addItem(t)
            self._over.append(t)

    def _view_point(self, scene_pos):
        vb = self.plot.getPlotItem().vb
        p = vb.mapSceneToView(scene_pos)
        return p.x(), p.y(), 6 * vb.viewPixelSize()[0]

    def _moved(self, pos):
        if self.mode == 'region' and self.corner is not None:
            x, y, _ = self._view_point(pos)
            x0, y0 = self.corner
            self.band.setData([x0, x, x, x0, x0], [y0, y0, y, y, y0])
        super()._moved(pos)

    def _clicked(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        if self.mode == 'inspect':
            return super()._clicked(ev)
        x, y, tol = self._view_point(ev.scenePos())
        self.clicked_at.emit(x, y, tol)


class Window(QMainWindow):
    def __init__(self, live=None):
        super().__init__()
        self.live, self.plan, self.loader, self.proc, self.run_out = (live,
                                                                      None,
                                                                      None,
                                                                      None,
                                                                      None)
        self.text, self.session, self.folder, self.stem = (None, md.Session(),
                                                           None, 'board')
        self.lines = dict(format='kicad2ems-lines', version=1, elements=[],
                          paths=[], port_z0={'': 50.0}, relative=True)
        # map sections per net, the nets mapped so far
        self.store, self.mapped = {}, set()
        self.setWindowTitle('kicad2ems for KiCad')
        icon = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'icon-48.png')
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        split = QSplitter()
        self.view = PluginPlan()
        self.section_picked, self.section_dlg, self.section_view = (None, None,
                                                                    None)
        self.view.selected.connect(self._section_clicked)
        self.view.clicked_at.connect(self._tool_click)
        self.results = Results()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.view, 'Board')
        self.tabs.addTab(self.results, 'S-parameters')
        split.addWidget(self.tabs)
        side = QWidget()
        lay = QVBoxLayout(side)
        for box in (self._board_box(), self._map_box(), self._nets_box(),
                    self._region_box(), self._ports_box(), self._run_box(),
                    self._power_box()):
            lay.addWidget(box)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setMinimumWidth(360)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        self.setCentralWidget(split)
        self.resize(1400, 860)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_run)
        # map chunks come faster than a redraw: one every 0.25 s
        self.map_timer = QTimer(self)
        self.map_timer.setSingleShot(True)
        self.map_timer.setInterval(250)
        self.map_timer.timeout.connect(self._show_map)

    # ---- the side panel ----
    def _board_box(self):
        g = QGroupBox('Board')
        v = QVBoxLayout(g)
        self.board_lab = _label('connecting to KiCad ...')
        v.addWidget(self.board_lab)
        v.addWidget(
            _button(
                'Reload from KiCad', self.reload,
                'Read the board again as KiCad has it now (unsaved edits '
                'included). It is read again by itself when this window comes '
                'to the front after the board was edited'
            ))
        self.put_btn = _button(
            'Put on the board', self.put_on_board,
            "Draw the region, the ports (a cross and the name) and markers at "
            "the traces furthest from their class's impedance on a user layer "
            "of the board, in one step KiCad's undo takes back")
        self.put_layer = QComboBox()
        self.put_layer.addItems([f'User.{k}' for k in range(1, 10)]
                                + ['Cmts.User', 'Dwgs.User'])
        self.put_layer.setCurrentText('User.9')
        v.addLayout(_row(self.put_btn, self.put_layer))
        self.gpu_btn = _button(
            'Use the GPU for gfdtd', self.install_gpu,
            "Install slangpy (gfdtd's Vulkan runtime, a download of 40-90 MB) "
            "into the plugin's Python: gfdtd runs on the GPU instead of numpy "
            "on the CPU")
        self.gpu_btn.setVisible(md.gpu_offer() is not None)
        v.addWidget(self.gpu_btn)
        return g

    def put_on_board(self):
        """R-E26: the session's region and ports and the map's worst sections,
        drawn in KiCad."""
        if self.live is None:
            return
        items = md.board_marks(self.session,
                               [e for es in self.store.values() for e in es])
        if not items:
            self.statusBar().showMessage(
                'Nothing to put on the board: no region, ports or map yet',
                6000)
            return
        layer = self.put_layer.currentText()
        try:
            n = self.live.put_on_board(items, layer)
        except Exception as e:  # an older KiCad, the API refusing
            self.statusBar().showMessage(
                f"KiCad did not take them: {type(e).__name__}: {e}", 10000)
            return
        self.statusBar().showMessage(
            f"{n} items on {layer} (Edit > Undo in KiCad takes them back)",
            8000)

    def install_gpu(self):
        """slangpy into this Python (pip), in a process of its own, the button
        gone once it is there."""
        what = md.gpu_offer()
        if what is None:
            self.gpu_btn.setVisible(False)
            return
        self.gpu_btn.setEnabled(False)
        self.statusBar().showMessage(f"installing {what} ...")
        self.gpu_proc = QProcess(self)
        self.gpu_proc.finished.connect(self._gpu_installed)
        self.gpu_proc.start(sys.executable, ['-m', 'pip', 'install', what])

    def _gpu_installed(self, code, _status):
        out = bytes(self.gpu_proc.readAllStandardError()).decode(
            errors='replace').strip().splitlines()
        if code == 0:
            self.gpu_btn.setVisible(False)
            self.statusBar().showMessage(
                'slangpy installed: gfdtd runs on the GPU from the next run',
                10000)
        else:
            self.gpu_btn.setEnabled(True)
            self.statusBar().showMessage(
                f"slangpy did not install: {out[-1] if out else code}", 15000)

    def _map_box(self):
        g = QGroupBox('Impedance map')
        v = QVBoxLayout(g)
        self.map_bar = QProgressBar()
        self.map_bar.setFormat('%v / %m nets')
        v.addWidget(self.map_bar)
        self.legend = Legend()
        v.addWidget(self.legend)
        self.map_lab = _label(
            'Traces coloured by their impedance against their net class\'s '
            'target (50 ohm SE without one), with the copper\'s thickness on '
            'pairs and grounded CPW: hover one to read it, click it to select '
            'its track in KiCad', '#555')
        v.addWidget(self.map_lab)
        self.field_on = QCheckBox('Every section by the field solver')
        self.field_on.setToolTip(
            "Each trace from the board's copper cut across it, rather than the "
            "formulas: what is beside it and any holes in its planes count. "
            "Slower (about 5 x); kept like the map"
        )
        self.field_on.toggled.connect(self._field_toggled)
        v.addLayout(
            _row(
                self.field_on,
                _button('Field across it…', self._open_section,
                        'The field across the section last clicked on the map'),
                stretch=self.field_on))
        self.scope = QComboBox()
        for label, key in (('all nets', 'all'), ('nets selected in KiCad',
                                                 'selection'),
                           ('nets in the region', 'region')):
            self.scope.addItem(label, key)
        self.scope.setToolTip(
            'Which nets the map shows (within the classes ticked below)')
        self.scope.currentIndexChanged.connect(self._filter_changed)
        v.addLayout(
            _row(
                QLabel('Show'), self.scope,
                _button('Update', self._filter_changed,
                        "Read KiCad's selection again"), stretch=self.scope))
        self.class_list = QListWidget()
        self.class_list.setMinimumHeight(90)
        self.class_list.setToolTip(
            'Net classes: tick the ones to map. The target impedance comes '
            'from the class name (50R_SE, 100R_DIFF, 90ohm ...); #PWR nets are '
            'never mapped')
        self.class_list.itemChanged.connect(self._filter_changed)
        v.addWidget(self.class_list)
        return g

    def _nets_box(self):
        g = QGroupBox('Nets to simulate')
        v = QVBoxLayout(g)
        v.addWidget(
            _label(
                'A net, a pair or a set of nets: the region goes around them, '
                'their R / L / C parts stay in (in series: the nets beyond '
                'come too), and every other component on them gets a port',
                '#555'))
        self.pick_btn = _button(
            'Pick', tip=
            'Click traces on the plan: each click adds its net (or takes it '
            'out again)',
            checkable=True)
        v.addLayout(
            _row(
                _button(
                    'From KiCad selection', self._nets_from_selection,
                    "The nets of the items selected in KiCad (a pair's other "
                    "leg comes along)"
                ), self.pick_btn, _button('Clear', lambda: self._set_nets([]))))
        self.nets_lab = _label('none')
        v.addWidget(self.nets_lab)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.0, 50.0)
        self.margin.setDecimals(1)
        self.margin.setSuffix(' mm')
        self.margin.setValue(md.NET_MARGIN)
        self.margin.setToolTip(
            'How far the region reaches beyond the nets\' copper and parts '
            '(the planes and ground beside them)')
        self.setup_btn = _button('Set up region and ports', self._setup_nets)
        v.addLayout(
            _row(QLabel('Board around them'), self.margin, self.setup_btn))
        self.setup_lab = _label(' ', '#333')
        v.addWidget(self.setup_lab)
        self.picked = []
        return g

    def _region_box(self):
        g = QGroupBox('Region to simulate')
        v = QVBoxLayout(g)
        self.draw_btn = _button('Draw', tip='Click two corners on the plan',
                                checkable=True)
        v.addLayout(
            _row(
                self.draw_btn,
                _button(
                    'From KiCad selection', self._region_from_selection,
                    'The box around the items selected in KiCad, 1 mm beyond'),
                _button('Clear', lambda: self._set_region(None, by_hand=True))))
        self.region_lab = QLabel('the whole board')
        v.addWidget(self.region_lab)
        self.cut = QCheckBox('Ports where signal tracks cross the region edge')
        self.cut.toggled.connect(self._cut_changed)
        v.addWidget(self.cut)
        return g

    def _ports_box(self):
        g = QGroupBox('Ports')
        v = QVBoxLayout(g)
        self.port_btn = _button('Add: click a trace or a pad', checkable=True)
        v.addWidget(self.port_btn)
        self.port_list = QListWidget()
        self.port_list.setMinimumHeight(110)
        v.addWidget(self.port_list)
        v.addWidget(_button('Remove the selected port', self._remove_port))
        self.draw_btn.toggled.connect(lambda on: self._tool('region', on))
        self.port_btn.toggled.connect(lambda on: self._tool('port', on))
        self.pick_btn.toggled.connect(lambda on: self._tool('net', on))
        return g

    def _power_box(self):
        """R-E26: pcbqs's DC drop and temperature of the board (kicad2ems's
        analyses), and their maps drawn in KiCad as bands, a user layer each."""
        g = QGroupBox('Power: DC drop, temperature')
        f = QFormLayout(g)
        self.rail = QComboBox()
        self.rail.setEditable(True)
        self.rail.setToolTip(
            "the rail's net (its tree grows through series parts); the loads: "
            "the parts' pdn.load fields")
        f.addRow('Rail', self.rail)
        self.volts = QDoubleSpinBox()
        self.volts.setRange(0.1, 1000.0)
        self.volts.setValue(3.3)
        self.volts.setSuffix(' V')
        f.addRow('Volts', self.volts)
        self.loads = QLineEdit()
        self.loads.setPlaceholderText(
            "U3=1.2, U7=0.4 (A for the drop, W for the temperature)")
        self.loads.setToolTip(
            "REF=VALUE, comma-separated: for the DC drop the loads' currents "
            "in A (empty: the parts' own pdn.load fields); for the temperature "
            "the parts' losses in W (empty: a circuit run's)")
        f.addRow('Loads / losses', self.loads)
        self.power_btns = [
            _button(
                label, lambda _=False, n=name: self.run_power(n),
                f"pcbqs's {label.lower()} of this board, as kicad2ems runs it "
                "(Open in kicad2ems has every "
                "setting)") for name, label in md.POWER
        ]
        f.addRow(_row(*self.power_btns))
        self.maps = QComboBox()
        self.maps.setToolTip(
            "a map of the last result: drawn as bands on User.1 ... User.8, "
            "the legend on User.8"
        )
        f.addRow('Map', self.maps)
        self.map_btn = _button('Map on the board', self.map_on_board)
        self.map_btn.setEnabled(False)
        f.addRow(self.map_btn)
        self.power_lab = _label('')
        f.addRow(self.power_lab)
        return g

    def _list_maps(self, folder):
        self.maps.clear()
        for label, path, unit in md.map_sources(folder):
            self.maps.addItem(f"{label} ({unit})", (path, label, unit))
        self.map_btn.setEnabled(self.maps.count() > 0 and self.live is not None)

    def run_power(self, name):
        """An analysis (pdn: the DC drop; thermal) of the board as KiCad has it
        now, in <work>/<name>."""
        if self.plan is None or self.folder is None:
            return
        board = os.path.join(self.folder, f"{self.stem}.kicad_pcb")
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(board, 'w', encoding='utf-8') as fh:
                fh.write(self.text)
        except OSError as e:
            QMessageBox.warning(self, 'kicad2ems', str(e))
            return
        values = dict(rail=self.rail.currentText().strip(),
                      volts=float(self.volts.value()))
        loads = [x.strip() for x in self.loads.text().split(',') if x.strip()]
        if loads:
            values['load' if name == 'pdn' else 'loss'] = loads
        if name == 'pdn':
            # (the drop: its map; the impedance is the window's)
            values['no_pdn'] = True
        out = os.path.join(self.folder, name)
        argv, missing = md.analysis_command(name, values, board, out)
        if argv is None:
            self.power_lab.setText('Needs: ' + '; '.join(missing)
                                   + ' (Open in kicad2ems has every setting)')
            return
        self.power_out = out
        self.power_proc = QProcess(self)
        self.power_proc.setProcessChannelMode(QProcess.MergedChannels)
        self.power_last = ''
        def said():
            text = bytes(self.power_proc.readAllStandardOutput()).decode(
                errors='replace').strip()
            if text:
                self.power_last = text.splitlines()[-1][:200]
                self.power_lab.setText(self.power_last)
        self.power_proc.readyReadStandardOutput.connect(said)
        self.power_proc.finished.connect(self._power_done)
        for b in self.power_btns:
            b.setEnabled(False)
        self.power_lab.setText(f"{name}: starting ...")
        self.power_proc.start(argv[0], argv[1:])

    def _power_done(self, code, _status):
        for b in self.power_btns:
            b.setEnabled(True)
        self.power_lab.setText('done' if code == 0 else
                               f"failed: {self.power_last or f'exit {code}'}")
        self._list_maps(self.power_out)

    def map_on_board(self):
        """The chosen map's bands drawn on the board (one commit: KiCad's undo
        takes it back)."""
        data = self.maps.currentData()
        if self.live is None or not data:
            return
        path, label, unit = data
        levels, polys = md.map_bands(path)
        if not polys:
            self.statusBar().showMessage('That map is empty', 6000)
            return
        x0, y0 = ((self.plan.m.outline.bounds[2] + 3.0,
                   -self.plan.m.outline.bounds[3]) if self.plan else (0.0, 0.0))
        items = md.map_marks(levels, polys, label, unit, x0, y0)
        try:
            n = self.live.put_on_board(items, md.MAP_LAYERS[-1])
        except Exception as e:  # an older KiCad, the API refusing
            self.statusBar().showMessage(
                f"KiCad did not take it: {type(e).__name__}: {e}", 10000)
            return
        self.statusBar().showMessage(
            f"{label}: {n} items on {md.MAP_LAYERS[0]} ... {md.MAP_LAYERS[-1]} "
            "(Edit > Undo in KiCad takes them back)", 8000)

    def _run_box(self):
        g = QGroupBox('Simulation')
        f = QFormLayout(g)
        self.fmin, self.fmax = QDoubleSpinBox(), QDoubleSpinBox()
        for w, val in ((self.fmin, 0.5), (self.fmax, 10.0)):
            w.setRange(0.001, 500.0)
            w.setDecimals(3)
            w.setSuffix(' GHz')
            w.setValue(val)
            w.valueChanged.connect(self._save)
        f.addRow('f min', self.fmin)
        f.addRow('f max', self.fmax)
        self.solver = QComboBox()
        for label, names in SOLVERS:
            self.solver.addItem(label, names)
        self.solver.currentIndexChanged.connect(self._save)
        f.addRow('Solver', self.solver)
        self.run_btn, self.stop_btn = (_button('Run', self.run),
                                       _button('Stop', self.stop))
        self.stop_btn.setEnabled(False)
        f.addRow(
            _row(
                self.run_btn, self.stop_btn,
                _button(
                    'Open in kicad2ems', self.open_gui,
                    'The same set-up in kicad2ems\'s own window: every '
                    'setting, the results and the field views')))
        self.run_bar = QProgressBar()
        self.run_bar.setRange(0, 1000)
        f.addRow(self.run_bar)
        self.run_lab = _label(' ')
        f.addRow(self.run_lab)
        self.report_btn = _button('Open the report', self._open_report)
        self.report_btn.setEnabled(False)
        f.addRow(self.report_btn)
        self.report = None
        return g

    # ---- the board ----
    def connect_kicad(self):
        from .ipc import Live, LinkError
        if self.live is None:
            try:
                self.live = Live.connect()
            except LinkError as e:
                self.board_lab.setText(str(e))
                return False
        self._bind_board()
        self.reload()
        return True

    def _bind_board(self):
        """The board KiCad has open now: its stem, its folder (its session and
        runs) and its session."""
        self.stem = self.live.stem()
        self.folder = os.path.join((self.live.project_dir() or os.getcwd()),
                                   'kicad2ems', self.stem)
        self.session = (md.Session.load(md.session_path(self.folder, self.stem))
                        or md.Session())
        self._show_session()
        self._bound = self.live.board_path()

    def reload(self):
        if self.live is None:
            # (KiCad, or its API, was not up when the window opened: Reload
            # had done nothing, and the window had to be opened again)
            self.connect_kicad()
            return
        if self.loader is not None:
            self.loader.stop()
            self.loader.wait()
        try:
            self.live.refresh()
            self.text = self.live.board_text()
            path, version = self.live.board_path(), self.live.version()
        except Exception as e:  # KiCad closed the board, or went away
            self.board_lab.setText(
                f"cannot read the board from KiCad: {type(e).__name__}: {e}")
            return
        if path != getattr(self, '_bound', None):
            # another board in KiCad's editor now: its own session, folder and
            # runs (the last board's region and ports had been applied to it,
            # and its session, config and runs written over the last one's)
            self._bind_board()
        self.board_lab.setText(f"{path}\nKiCad {version}: reading ...")
        self.lines['elements'], self.store, self.mapped = [], {}, set()
        self.map_bar.setValue(0)
        try:
            classes = self.live.net_classes()
        except Exception:  # an older KiCad: the project file instead
            classes = None
        self.loader = Loader(self.text, self.live.board_path(), classes=classes)
        self.loader.loaded.connect(self._loaded)
        self.loader.failed.connect(lambda msg: self.board_lab.setText(
            f"{self.live.board_path()}\n{msg}"))
        self.loader.start()

    def _loaded(self, plan):
        self.plan = plan
        self.lines['port_z0'] = dict(plan.lines['port_z0'])
        m = plan.m
        self.board_lab.setText(
            f"{self.live.board_path()}\nKiCad {self.live.version()}: "
            f"{len(m.cu_order)} copper layers, {len(plan.signal)} signal nets, "
            f"ground {', '.join(m.ground_nets) or '-'}"
        )
        self.view.region, self.view.ports = (self.session.region,
                                             self.session.ports)
        self.view.set_board(plan.board, self.lines)
        self._list_classes()
        self._list_ports()
        self.picked = [n for n in self.session.nets if n in plan.signal]
        self._show_nets()
        # the power box's rails: the nets but signal ones
        keep = self.rail.currentText()
        self.rail.clear()
        nets = sorted({it[1]
                       for it in m.prims.items
                       if it[1]} - set(m.ground_nets))
        power = [
            n for n in nets if re.search(
                r'^\+|^V(CC|DD|IN|BAT|BUS|SYS)|^\d+V\d*$|^P\d+V', n, re.I)
        ]
        self.rail.addItems(power + [n for n in nets if n not in power])
        if keep:
            self.rail.setCurrentText(keep)
        for name, _ in md.POWER:  # an earlier result's maps
            d = os.path.join(self.folder or '', name)
            if os.path.isdir(d):
                self._list_maps(d)
        res = (md.results(os.path.join(self.folder, 'out'), self.stem)
               if self.folder else None)
        if res:
            self.results.show_results(res, 'the last run')
        if len(plan.signal) > md.LARGE:
            self.map_lab.setText(
                f"{len(plan.signal)} signal nets: a large board. The map "
                "starts with the classes that name an impedance (or KiCad's "
                "selection); tick others below (all of them may take minutes)")
        self._filter_changed()

    def _list_classes(self):
        """The class filter: each class with its nets and target, ticked as the
        session had them (first time: all; on a large board the classes with a
        target, or none and KiCad's selection)."""
        rows = self.plan.classes()
        keep = self.session.classes
        if keep is None:
            targeted = [c for c, _, t in rows if t]
            keep = ([c for c, _, _ in rows]
                    if len(self.plan.signal) <= md.LARGE else targeted)
            if len(self.plan.signal) > md.LARGE and not targeted:
                self.scope.blockSignals(True)
                self.scope.setCurrentIndex(self.scope.findData('selection'))
                self.scope.blockSignals(False)
        self.class_list.blockSignals(True)
        self.class_list.clear()
        for c, nets, t in rows:
            it = QListWidgetItem(
                f"{c}  -  {len(nets)} net{'s' if len(nets) != 1 else ''}"
                + (f", {t}" if t else ''))
            it.setData(Qt.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c in keep else Qt.Unchecked)
            self.class_list.addItem(it)
        if self.plan.disabled:
            it = QListWidgetItem(
                f"#PWR  -  {len(self.plan.disabled)} net(s), never mapped")
            it.setFlags(it.flags() & ~Qt.ItemIsEnabled
                        & ~Qt.ItemIsUserCheckable)
            it.setToolTip(', '.join(self.plan.disabled))
            self.class_list.addItem(it)
        self.class_list.blockSignals(False)

    def _checked_classes(self):
        items = [
            self.class_list.item(i) for i in range(self.class_list.count())
        ]
        return [
            it.data(Qt.UserRole)
            for it in items
            if it.data(Qt.UserRole) and it.checkState() == Qt.Checked
        ]

    def _visible_nets(self):
        """The nets the map shows: the ticked classes', within the scope (all,
        KiCad's selection, the region)."""
        nets = self.plan.nets_of(self._checked_classes())
        scope = self.scope.currentData()
        if scope == 'selection':
            try:
                sel = self.live.selection_nets() if self.live else set()
            except Exception:  # KiCad went away
                sel = set()
            nets = [n for n in nets if n in sel]
        elif scope == 'region':
            inside = (set(self.plan.nets_in(self.session.region))
                      if self.session.region else set())
            nets = [n for n in nets if n in inside]
        return nets

    def _filter_changed(self, *_):
        """Show what the filter asks for; map (in the background) the nets of it
        that are not mapped yet."""
        if self.plan is None:
            return
        self.session.classes = self._checked_classes()
        self._save()
        want = self._visible_nets()
        missing = [n for n in want if n not in self.mapped]
        busy = self.loader is not None and self.loader.isRunning()
        if (missing
                and not (busy and set(missing) <= set(self.loader.nets or []))):
            if busy:
                self.loader.stop()
                self.loader.wait()
            # MapCache
            cache = (os.path.join(self.folder, f"{self.stem}_impedance.json.gz")
                     if self.folder else None)
            self.loader = Loader(None, None, plan=self.plan, nets=missing,
                                 cache=cache, field=self.session.lines_field)
            self.loader.progress.connect(self._mapped)
            self.loader.finished.connect(self._map_done)
            self.loader.failed.connect(lambda msg: self.map_lab.setText(msg))
            self.loader.start()
        self._show_map()

    def _field_toggled(self, on):
        """The map from the field solver, or back: every net again (from the
        cache where it has them)."""
        self.session.lines_field = bool(on)
        if self.loader is not None and self.loader.isRunning():
            self.loader.stop()
            self.loader.wait()
        self.mapped.clear()
        self.store.clear()
        self._filter_changed()

    def _open_section(self):
        if self.section_dlg is None:
            self.section_dlg = QDialog(self)
            self.section_dlg.setWindowTitle('The field across the section')
            self.section_dlg.resize(900, 560)
            self.section_view = SectionView()
            QVBoxLayout(self.section_dlg).addWidget(self.section_view)
        self.section_view.show_section(self.section_picked)
        self.section_dlg.show()
        self.section_dlg.raise_()

    def _mapped(self, chunk, new):
        for n in chunk:
            self.mapped.add(n)
            self.store.setdefault(n, [])
        for e in new:
            self.store.setdefault(e.get('net'), []).append(e)
        if not self.map_timer.isActive():
            self.map_timer.start()

    def _map_done(self):
        if self.map_timer.isActive():
            self.map_timer.stop()
            self._show_map()

    def _show_map(self):
        """The view's map: the stored sections of the visible nets, numbered as
        the view expects."""
        want = self._visible_nets()
        els = []
        for n in want:
            for e in self.store.get(n, []):
                els.append(dict(e, id=len(els)))
        self.lines['elements'] = els
        z0 = [e['z0'] for e in els if e.get('z0')]
        self.lines['z0_range'] = [min(z0), max(z0)] if z0 else None
        done = sum(1 for n in want if n in self.mapped)
        self.map_bar.setRange(0, max(len(want), 1))
        self.map_bar.setValue(done if want else 0)
        self.map_bar.setFormat('%v / %m nets' if want else 'no nets to show')
        self.legend.set(md.legend(self.lines)[1])
        self.view.redraw()

    # ---- tools ----
    def _tool(self, mode, on):
        if on:
            for m, other in (('region', self.draw_btn), ('port', self.port_btn),
                             ('net', self.pick_btn)):
                if m != mode:
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
            self.tabs.setCurrentWidget(self.view)
        self.view.set_mode(mode if on else 'inspect')
        self.statusBar().showMessage({
            'region': 'Click the first corner of the region',
            'port': 'Click a trace or a pad: a port goes there',
            'net': 'Click a trace: its net is added (or taken out)'
        }.get(self.view.mode, ''))

    def _tool_click(self, x, y, tol):
        if self.plan is None:
            return
        if self.view.mode == 'region':
            if self.view.corner is None:
                self.view.corner = (x, y)
                self.statusBar().showMessage('Click the opposite corner')
                return
            x0, y0 = self.view.corner
            # back to inspecting (clears the band)
            self.draw_btn.setChecked(False)
            self._set_region(md.region_of_box(x0, -y0, x, -y, margin=0.0),
                             by_hand=True)
        elif self.view.mode == 'net':
            i = ov.nearest(self.lines['elements'], x, y, tol,
                           layer=self.view.current_layer())
            net = (self.lines['elements'][i].get('net')
                   if i is not None else None)
            if not net:
                self.statusBar().showMessage(
                    'No mapped trace here (on the layer shown)', 5000)
                return
            pair = [n for n in (net, partner(net)) if n in self.plan.signal]
            self._set_nets([n for n in self.picked if n not in pair] if net in
                           self.picked else self.picked + pair)
        elif self.view.mode == 'port':
            try:
                p = self.plan.port_at(
                    x, y, self.view.current_layer(), tol, self.session,
                    elements=[e for es in self.store.values() for e in es])
            except ValueError as e:
                self.statusBar().showMessage(f"No port here: {e}", 6000)
                return
            self.session.ports.append(p)
            if (self.session.run_nets and p['net']
                    and p['net'] not in self.session.run_nets):
                # a net run: its copper comes into the run
                self.session.run_nets.append(p['net'])
            self._list_ports()
            self.view.redraw()
            self._save()
            warn = ('  - outside the region: left out of the run' if md.outside(
                self.session, p) else '')
            self.statusBar().showMessage(
                f"{p['name']} on {p['what']}  ({p['spec']}){warn}", 8000)

    def _set_region(self, region, by_hand=False):
        # no longer a net run: kicad2ems's auto nets
        if by_hand and self.session.run_nets:
            self.session.drop_nets()
            self.setup_lab.setText(
                '(the region was changed by hand: the run takes the nets under '
                'the ports)'
            )
        self.session.region = region
        self.view.region = region
        self.view.redraw()
        self._list_ports()
        if self.scope.currentData() == 'region':
            self._filter_changed()
        self._show_session()
        self._save()

    def _region_from_selection(self):
        box = self.live.selection_box() if self.live else None
        if box is None:
            self.statusBar().showMessage('Nothing is selected in KiCad', 5000)
            return
        self._set_region(md.region_of_box(*box, margin=1.0), by_hand=True)

    # ---- nets to simulate ----
    def _set_nets(self, nets):
        self.picked = list(dict.fromkeys(nets))
        self._show_nets()

    def _show_nets(self):
        self.nets_lab.setText(', '.join(self.picked) if self.picked else 'none')
        self.setup_btn.setEnabled(bool(self.picked))
        if self.session.run_nets and set(self.session.nets) == set(self.picked):
            self.setup_lab.setText(
                f"set up: {', '.join(self.session.run_nets)}; "
                f"{len(self.session.ports)} port(s)"
            )

    def _nets_from_selection(self):
        try:
            sel = self.live.selection_nets() if self.live else set()
        except Exception:  # KiCad went away
            sel = set()
        sig = set(self.plan.signal) if self.plan else set()
        nets = sorted({q for n in sel for q in (n, partner(n)) if q in sig})
        if not nets:
            self.statusBar().showMessage(
                'No signal nets are selected in KiCad (select tracks, pads or '
                'a net)',
                5000)
            return
        self._set_nets(nets)

    def _setup_nets(self):
        if self.plan is None or not self.picked:
            return
        try:
            setup = self.plan.setup_for_nets(self.picked,
                                             margin=self.margin.value())
        except ValueError as e:
            QMessageBox.warning(self, 'kicad2ems', str(e))
            return
        self.session.use(setup)
        self.session.margin = self.margin.value()
        self.view.region, self.view.ports = (self.session.region,
                                             self.session.ports)
        self.view.redraw()
        self._list_ports()
        self._show_session()
        self._save()
        if self.scope.currentData() == 'region':
            self._filter_changed()
        self.setup_lab.setText(setup.describe())

    def _cut_changed(self, on):
        self.session.cut_ports = on
        self._save()

    def _remove_port(self):
        it = self.port_list.currentItem()
        name = it.data(Qt.UserRole) if it else None
        if not name:
            return
        self.session.ports = [
            p for p in self.session.ports if p['name'] != name
        ]
        self.view.ports = self.session.ports
        self._list_ports()
        self.view.redraw()
        self._save()

    def _list_ports(self):
        self.port_list.clear()
        for name in (self.plan.board_ports if self.plan else []):
            it = QListWidgetItem(f"{name}  (port footprint on the board)")
            it.setForeground(QColor('#777'))
            self.port_list.addItem(it)
        for p in self.session.ports:
            out = md.outside(self.session, p)
            it = QListWidgetItem(f"{p['name']}  {p['what']}  [{p['layer']}]"
                                 + ('  - outside the region' if out else ''))
            it.setData(Qt.UserRole, p['name'])
            if out:
                it.setForeground(QColor('#b04020'))
            self.port_list.addItem(it)

    def _section_clicked(self, i):
        if i is None or self.plan is None:
            return
        e = self.section_picked = self.lines['elements'][i]
        if self.section_dlg is not None and self.section_dlg.isVisible():
            self.section_view.show_section(e)
        if self.live is None:
            return
        layer, net, a, b = self.plan.section_track(e)
        try:
            found = self.live.show_track(layer, net, a, b)
        except Exception as ex:  # KiCad went away
            found = False
            self.statusBar().showMessage(f"KiCad: {type(ex).__name__}: {ex}",
                                         5000)
        if found:
            self.statusBar().showMessage(
                f"selected in KiCad: the {net} track on {layer}", 4000)

    # ---- the session ----
    def _show_session(self):
        s = self.session
        self.region_lab.setText(
            'the whole board' if not s.region else
            f"x {s.region[0]:g} .. {s.region[2]:g}, y {s.region[1]:g} .. "
            f"{s.region[3]:g} mm (KiCad)"
        )
        for w, v in ((self.field_on, bool(s.lines_field)),
                     (self.cut, bool(s.cut_ports)), (self.fmin, s.fmin),
                     (self.fmax, s.fmax), (self.margin, s.margin)):
            w.blockSignals(True)
            (w.setChecked if isinstance(w, QCheckBox) else w.setValue)(v)
            w.blockSignals(False)
        i = next((k for k, (_, names) in enumerate(SOLVERS)
                  if names == list(s.solvers)), 0)
        self.solver.blockSignals(True)
        self.solver.setCurrentIndex(i)
        self.solver.blockSignals(False)
        self.cut.setEnabled(bool(s.region))

    def _save(self, *_):
        s = self.session
        s.fmin, s.fmax, s.solvers = (self.fmin.value(), self.fmax.value(),
                                     self.solver.currentData())
        self.cut.setEnabled(bool(s.region))
        if self.folder:
            s.save(md.session_path(self.folder, self.stem))

    # ---- runs ----
    def _values(self):
        if self.plan is None:
            raise ValueError('the board is not read yet')
        self._save()
        return md.run_values(self.session, self.plan, self.text, self.folder,
                             self.stem)

    def run(self):
        try:
            vals = self._values()
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, 'kicad2ems', str(e))
            return
        md.write_config(vals, os.path.join(self.folder, f"{self.stem}.toml"))
        self.run_out = vals['out']
        r = Run(vals, self.run_out)
        self.tail = EventTail(r.events)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._run_error)
        argv = r.argv()
        self.proc.start(argv[0], argv[1:])
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.report_btn.setEnabled(False)
        self.run_bar.setValue(0)
        self.run_lab.setText('starting ...')
        self.timer.start(300)

    def _poll_run(self):
        for ev in self.tail.read():
            k = ev.get('kind')
            if k == 'stage':
                self.run_lab.setText(ev.get('name', ''))
            elif k == 'log':
                self.run_lab.setText(ev.get('text', '')[:200])
            elif k == 'status' and ev.get('fraction') is not None:
                self.run_bar.setValue(int(1000 * ev['fraction']))
                eta = ev.get('eta')
                self.run_lab.setText(
                    f"{ev.get('run', '')}: step {ev.get('step', 0):,}"
                    + (f", ~{eta:.0f} s left" if eta else ''))
            elif (k == 'file'
                  and str(ev.get('path', '')).endswith('_report.html')):
                self.report = ev['path']

    def _finished(self, code, _status):
        self.timer.stop()
        self._poll_run()
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.run_bar.setValue(1000 if code == 0 else self.run_bar.value())
        self.report_btn.setEnabled(
            bool(self.report and os.path.exists(self.report)))
        if code == 0:
            self.run_lab.setText(f"done: results in {self.run_out}")
            res = md.results(self.run_out, self.stem)
            self.results.show_results(res, 'this run')
            if res:
                self.tabs.setCurrentWidget(self.results)
        else:
            tail = (bytes(self.proc.readAll()).decode(
                'utf-8', 'replace').strip().splitlines()[-4:])
            self.run_lab.setText("the run failed:\n" + "\n".join(tail))
        self._run_ended()

    def _run_error(self, err):
        """A run that could not start (no finished signal follows)."""
        if err != QProcess.ProcessError.FailedToStart or self.proc is None:
            return
        self.timer.stop()
        self.run_lab.setText(
            f"the run did not start: {self.proc.errorString()}")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._run_ended()

    def _run_ended(self):
        """No run going (the board is read again on activation: R-E26; the
        process had been kept, and the board never read again after a run)."""
        if self.proc is not None:
            self.proc.deleteLater()
        self.proc = None

    def stop(self):
        if self.run_out:
            stop_runs(self.run_out)
        self.run_lab.setText('stopping ...')

    def open_gui(self):
        try:
            vals = self._values()
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, 'kicad2ems', str(e))
            return
        cfg = md.write_config(vals,
                              os.path.join(self.folder, f"{self.stem}.toml"))
        QProcess.startDetached(sys.executable, ['-m', 'kicad2ems.gui', cfg])
        self.statusBar().showMessage(f"opened {cfg} in kicad2ems", 5000)

    def _open_report(self):
        if self.report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report))

    def event(self, ev):
        """R-E26: brought to the front after the board was edited in KiCad, the
        board is read again."""
        if (ev.type() == QEvent.Type.WindowActivate and self.live is not None
                and self.text is not None and self.proc is None
                and (self.loader is None or not self.loader.isRunning())):
            QTimer.singleShot(0, self._reload_if_changed)
        return super().event(ev)

    def _reload_if_changed(self):
        if self.live is not None and self.live.changed():
            self.statusBar().showMessage(
                'The board changed in KiCad: read again', 6000)
            self.reload()

    def closeEvent(self, ev):
        if self.proc is not None:
            # a run going: asked to stop, then ended with all it started
            # (destroyed with the window, the process alone had been killed)
            if (QMessageBox.question(
                    self, 'A run is going',
                    "A kicad2ems run is going: closing the window stops it "
                    "(its folder keeps what it has written). Stop it and "
                    "close?") != QMessageBox.Yes):
                ev.ignore()
                return
            if self.run_out:
                stop_runs(self.run_out)
            if not self.proc.waitForFinished(3000):
                pid = self.proc.processId()
                if pid:
                    kill_tree(pid)
                self.proc.kill()
                self.proc.waitForFinished(2000)
        if self.loader is not None:
            self.loader.stop()
            self.loader.wait(2000)
        super().closeEvent(ev)


def main(argv=None):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('kicad2ems for KiCad')
    w = Window()
    w.show()
    QTimer.singleShot(0, w.connect_kicad)
    return app.exec()
