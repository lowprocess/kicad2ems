"""
The Draw page: arbitrary shapes drawn over the board, in plan and in 3D, for a
run to add to it (kicad2ems.core.shapes: the drawing as data, and what it does
to the board; core.shapeops: edits of many shapes, snapping, measuring;
core.view3d: the 3D view's numbers).

    Plan    the board's copper on the layer drawn on (the other layers faint),
            and the shapes. Tools: Select, Rectangle and Circle (two clicks),
            Polygon and Trace (a click per point; double-click, right-click or
            Enter ends, Esc drops), Port (a click), Measure (a click per
            point: two give the distance and its angle, a third the angle
            between the legs; Esc clears). Points snap to the board's pads,
            vias, track ends and edges and to the shapes' corners, midpoints
            and centres (a marker shows where; `objects`), else to the grid.
    Select  a click picks a shape, Shift or Ctrl + click adds it or takes it
            off; a drag from a shape moves the selection (X / Y while
            dragging: along that axis only; Esc puts it back), from a handle
            reshapes the one selected; a drag on nothing draws a box (left to
            right: what is wholly inside; right to left: what it touches;
            Shift: added). Ctrl + drag, or the middle button, pans; the wheel
            and the right button zoom.
    3D      the same shapes extruded: copper and cuts as slabs on their layer,
            metal, dielectric and air as solids between their two heights,
            over the board's substrate and copper. A click picks a shape
            (Shift or Ctrl: adds or takes it off; on nothing: none), a drag
            moves the selection on its level (X / Y / Z while dragging: along
            that axis), a drag on the gizmo's arrows along that one, Shift +
            drag on a solid's top or bottom face stretches it, on its side
            lifts it; a drag on nothing orbits, Ctrl + drag pans. The camera's
            presets (Top 7, Front 1, Side 3, Iso 5, Fit F), a clip plane
            (Clip), what is drawn (Show: the board's layers, each material),
            the working level's grid. (gui.draw3d)
    Shapes  the list in the order they are applied (a cut takes out what is
            drawn before it), each shown or hidden (hidden: not drawn or
            picked, still applied) and locked or not (locked: not moved,
            turned or deleted); the selected one's properties (with several
            selected a change applies to them all); Arrange (move by, rotate
            by any angle about a point, mirror, align, distribute) and Array
            (a row, a grid, a circle).

Copy, cut and paste go through the clipboard as kicad2ems-shapes text (into
another drawing, or another session). Use in runs saves the drawing and names
it in the settings (`shapes`); Region from shape and Ports to settings write
those settings (KiCad coordinates: y negated). The status bar has the cursor
(model and KiCad mm), the size being drawn and what it snapped to.

Keys while the page has the focus: S R C P T O M the tools, Delete, Esc,
Enter, Ctrl+Z / Ctrl+Y undo and redo (the settings keep theirs elsewhere),
Ctrl+A all, Ctrl+C / Ctrl+X / Ctrl+V the clipboard, Ctrl+D duplicate, F fit
the selection in both views, 7 1 3 5 the 3D camera, X / Y / Z while dragging.

    tab = DrawTab(win)          # win: the main window, or None (on its own)
    tab.set_board(layout)       # core.layout.board_layout's dict, or None
    tab.add(Shape(...)); tab.drawing; tab.save(path); tab.load(path)
    tab.select(i, 'only' | 'toggle' | 'add'); tab.selected; tab.sel
    tab.move_by(dx, dy); tab.rotate_by(30, about); tab.mirror('x')
    tab.align('left'); tab.distribute('x'); tab.make_array('row', n=5, dx=1)
"""
import functools, os
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QEvent, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QPen, QBrush, QKeySequence, QPainterPath
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
                               QButtonGroup, QLabel, QComboBox, QDoubleSpinBox,
                               QSplitter, QFormLayout, QLineEdit, QSpinBox,
                               QPlainTextEdit, QPushButton, QFileDialog,
                               QGridLayout, QFrame, QMessageBox, QCheckBox,
                               QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QHeaderView, QAbstractItemView,
                               QScrollArea, QApplication)
from kicad2ems.core import shapes as sh
from kicad2ems.core import shapeops as so
from kicad2ems.core.shapes import Shape, Drawing
from . import theme
from .planview import _path, _item
from .volume3d import rings
from .draw3d import Draw3D

__all__ = ['DrawTab']

# tool: (button, shortcut letter, what a click does)
TOOLS = {
    'select': ('Select', 'S', 'click a shape to select it (Shift/Ctrl: add or '
               'take off), drag it to move the selection (X / Y: along one '
               'axis), drag a handle to reshape it, drag on nothing for a box '
               '(left to right: inside it; right to left: touching it); '
               'Ctrl+drag pans; Delete removes'),
    'rect': ('Rectangle', 'R', 'click two opposite corners'),
    'circle': ('Circle', 'C', 'click the centre, then a point on the rim'),
    'polygon': ('Polygon', 'P', 'click each corner; double-click, right-click '
                'or Enter closes it; Esc drops it'),
    'path': ('Trace', 'T', 'click each point of the centre line; double-click,'
             ' right-click or Enter ends it; Esc drops it'),
    'port': ('Port', 'O', 'click where the port goes (on a trace: it points '
             'along it)'),
    'measure': ('Measure', 'M', 'click two points for their distance and '
                'angle, a third for the angle between the legs; Esc clears'),
}
# RGB of each material, in plan and 3D
COLOURS = dict(copper=(214, 140, 64), cut=(225, 60, 60), metal=(140, 150, 165),
               dielectric=(60, 170, 90), air=(110, 165, 235))
PORT_COLOUR = (200, 30, 160)
SELECTED = QColor(0, 120, 215)
SNAP_COLOUR = QColor(255, 0, 200)
MEASURE_COLOUR = QColor(255, 150, 0)
SNAP_PX = 8  # how near a pad, a corner... a point snaps to it, pixels
# the form's fields that a change to the shown shape carries to the rest of
# the selection (not its points or name)
SHARED = ('material', 'layer', 'net', 'width', 'radius', 'z0', 'z1', 'er',
          'tand', 'rot')


def _qc(rgb, a=255):
    return QColor(*rgb, a)


class _Canvas(pg.ViewBox):
    """The plan's view box: the page's tools take the left button's clicks and
    drags; what they leave (a drag in a drawing tool, a Ctrl + drag, the
    middle and right buttons) pans and zooms as usual."""

    def __init__(self, tab):
        super().__init__(lockAspect=True)
        self.tab = tab

    def mouseClickEvent(self, ev):
        if ev.button() in (Qt.LeftButton, Qt.RightButton) and self.tab._click(
                self.mapSceneToView(ev.scenePos()), ev):
            ev.accept()
            return
        super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.LeftButton and self.tab._drag(ev):
            ev.accept()
            return
        super().mouseDragEvent(ev, axis)


class DrawTab(QWidget, Draw3D):
    changed = Signal()  # the drawing changed

    def __init__(self, win=None):
        super().__init__()
        self.win = win
        self.drawing, self.selected, self.tool = Drawing(), [], 'select'
        self.layout_, self.stack = None, sh.stack_of(None)
        self.file = None  # where the drawing was last saved / loaded
        self.pending, self._drag_state = [], None
        self.measure_pts, self._pick_point = [], None
        self._axis = None  # a drag held to one axis: 'x', 'y', 'z'
        self._undo, self._redo = [], []
        # the plan's items of each shape (index): dict(geo, style, items),
        # kept while its outline is the same
        self._gfx, self._board_items = [], []
        self.snapper, self._snap_stale, self._snapped = so.Snapper(), True, None
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(self._toolbar())
        # the plan and the 3D view one over the other, the shapes beside
        self.plot = pg.PlotWidget(viewBox=_Canvas(self))
        self.plot.setBackground(theme.colors()['bg'])
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel('bottom', 'x', units='mm')
        self.plot.setLabel('left', 'y', units='mm')
        self.vb = self.plot.getViewBox()
        self.plot.scene().sigMouseMoved.connect(self._moved)
        self.rubber = _item(_path([]), QPen(SELECTED, 1.5, Qt.DashLine),
                            QBrush(QColor(0, 120, 215, 40)), 50)
        self.vb.addItem(self.rubber)
        self.band = _item(QPainterPath(), QPen(SELECTED, 1.2), QBrush(), 55)
        self.vb.addItem(self.band)
        self.handles = pg.ScatterPlotItem(
            size=9, symbol='s', pen=pg.mkPen(SELECTED, width=1.5),
            brush=pg.mkBrush(255, 255, 255))
        self.handles.setZValue(60)
        self.vb.addItem(self.handles)
        self.snap_mark = pg.ScatterPlotItem(
            size=15, symbol='o', pen=pg.mkPen(SNAP_COLOUR, width=2),
            brush=pg.mkBrush(None))
        self.snap_mark.setZValue(70)
        self.vb.addItem(self.snap_mark)
        # the marker and the handles are under the mouse when it clicks
        # there: the click is the page's (a scatter item takes a click on
        # its points)
        for it in (self.snap_mark, self.handles):
            it.mouseClickEvent = lambda ev: None
        self.measure_line = _item(QPainterPath(), QPen(MEASURE_COLOUR, 1.6,
                                                       Qt.DashLine), None, 65)
        self.vb.addItem(self.measure_line)
        bg = QColor(theme.colors()['bg'])
        bg.setAlpha(215)
        self.measure_text = pg.TextItem('', color=MEASURE_COLOUR,
                                        anchor=(0, 1), fill=pg.mkBrush(bg))
        self.measure_text.setZValue(66)
        self.vb.addItem(self.measure_text)
        self._3d_timer = QTimer(self, singleShot=True, interval=120,
                                timeout=self._redraw_3d)
        views = QSplitter(Qt.Vertical)
        views.addWidget(self.plot)
        views.addWidget(self._make_3d())
        views.setSizes([600, 340])
        self.split = QSplitter(objectName='draw')
        self.split.addWidget(views)
        self.split.addWidget(self._side())
        self.split.setStretchFactor(0, 1)
        self.split.setSizes([860, 365])
        lay.addWidget(self.split, 1)
        foot = QHBoxLayout()
        self.coords = QLabel(minimumWidth=330, toolTip=(
            'the cursor (snapped), the size being drawn and what it snapped '
            'to'))
        self.status = QLabel(wordWrap=True)
        # two lines, always: a message that wraps does not shrink the views
        # under the mouse (a 3D drag's projection would move)
        for w in (self.coords, self.status):
            w.setFixedHeight(2 * w.fontMetrics().lineSpacing() + 4)
            w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.coords.setWordWrap(True)
        foot.addWidget(self.coords)
        foot.addWidget(_vline())
        foot.addWidget(self.status, 1)
        lay.addLayout(foot)
        self.set_tool('select')
        self.set_board(None)  # (the default stack's layers to draw on)
        self.vb.setRange(xRange=(-5, 25), yRange=(-20, 5))

    # ---- the widgets ----
    def _toolbar(self):
        row = QHBoxLayout()
        self.tools = QButtonGroup(self, exclusive=True)
        self.tool_btns = {}
        for key, (text, letter, tip) in TOOLS.items():
            b = QToolButton(text=text, checkable=True, autoRaise=True,
                            toolTip=f"{text} ({letter}): {tip}")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self.tools.addButton(b)
            self.tool_btns[key] = b
            row.addWidget(b)
        row.addWidget(_vline())
        self.layer = QComboBox(toolTip='the copper layer drawn on (copper, '
                               'cuts and ports), and shown in the plan')
        self.layer.currentTextChanged.connect(lambda _: self._layer_changed())
        self.material = QComboBox(toolTip=(
            'what a new shape is made of: copper (joins the layer\'s copper), '
            'cut (taken out of it), or a solid between two heights: metal, '
            'dielectric, air'))
        self.material.addItems(sh.MATERIALS)
        self.material.currentTextChanged.connect(self._material_state)
        self.width = _spin(0.5, 0.001, 100, ' mm', 'a new trace\'s width')
        self.z0 = _spin(0.0, -100, 100, ' mm', 'a new solid\'s bottom')
        self.z1 = _spin(1.0, -100, 100, ' mm', 'a new solid\'s top')
        self.z0.valueChanged.connect(lambda _: self._level_changed())
        self.snap = _spin(0.1, 0, 10, ' mm', 'points snap to this grid '
                          '(0: no snapping)', decimals=3)
        self.snap.valueChanged.connect(lambda _: self._level_changed())
        self.osnap = QCheckBox('objects', checked=True, toolTip=(
            'points snap to the board\'s pads, vias, track ends and edges, and '
            'to the shapes\' corners, midpoints and centres (a ring marks '
            f'it), within {SNAP_PX} pixels; else to the grid'))
        self.osnap.toggled.connect(lambda _: self.snap_mark.setData([], []))
        for text, w in (('Layer', self.layer), ('Material', self.material),
                        ('Width', self.width), ('z', self.z0), ('to', self.z1),
                        ('Snap', self.snap)):
            row.addWidget(QLabel(text))
            row.addWidget(w)
        row.addWidget(self.osnap)
        row.addStretch(1)
        return row

    def _side(self):
        side = QSplitter(Qt.Vertical)
        top = QWidget()
        v = QVBoxLayout(top)
        v.setContentsMargins(0, 0, 0, 0)
        self.list = QTreeWidget(
            columnCount=3, rootIsDecorated=False, uniformRowHeights=True,
            selectionMode=QAbstractItemView.ExtendedSelection, toolTip=(
                'the shapes in the order they are applied (a cut takes out '
                'what is drawn before it); Shown: drawn and picked here (a '
                'hidden one still applies), Locked: not moved, turned or '
                'deleted'))
        self.list.setHeaderLabels(['Shown', 'Locked',
                                   'Shapes (applied in this order)'])
        h = self.list.header()
        for c in (0, 1):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setStretchLastSection(True)
        self.list.itemSelectionChanged.connect(self._listed)
        self.list.itemChanged.connect(self._list_toggled)
        v.addWidget(self.list, 1)
        grid = QGridLayout()
        for i, (text, slot, tip) in enumerate((
                ('Up', lambda: self._reorder(-1), 'applied earlier'),
                ('Down', lambda: self._reorder(1), 'applied later'),
                ('Duplicate', self.duplicate,
                 'copies, 1 grid step along (Ctrl+D)'),
                ('Delete', self.delete, 'remove them (Delete; not locked '
                 'ones)'),
                ('Copy', self.copy, 'to the clipboard, as kicad2ems-shapes '
                 'text (Ctrl+C; Ctrl+X cuts)'),
                ('Paste', self.paste, 'shapes from the clipboard (Ctrl+V): '
                 'from this drawing or another'),
                ('Lock', lambda: self.set_locked(None),
                 'lock or unlock the selection: locked shapes are not moved, '
                 'turned or deleted'),
                ('Hide', lambda: self.set_hidden(None),
                 'hide or show the selection: hidden shapes are not drawn or '
                 'picked (a run still applies them)'),
                ('Rotate 90°', self.rotate, 'about their centre'),
                ('To polygon', self.to_polygon,
                 'their outlines as polygons, to reshape corner by corner'))):
            grid.addWidget(QPushButton(text, clicked=slot, toolTip=tip), i // 2,
                           i % 2)
        v.addLayout(grid)
        side.addWidget(top)
        bottom = QWidget()
        v = QVBoxLayout(bottom)
        v.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QTabWidget()
        self.side_tabs.addTab(self._form_page(), 'Shape')
        self.side_tabs.addTab(self._arrange_page(), 'Arrange')
        self.side_tabs.addTab(self._array_page(), 'Array')
        v.addWidget(self.side_tabs, 1)
        v.addWidget(_hline())
        files = QGridLayout()
        for i, (text, slot, tip) in enumerate((
                ('New', self.new, 'an empty drawing'),
                ('Open…', lambda: self.load(), 'a drawing (.json)'),
                ('Save', lambda: self.save(self.file), 'the drawing'),
                ('Save as…', lambda: self.save(None), 'the drawing, as'),
                ('Undo', self.undo, 'Ctrl+Z (on this page)'),
                ('Redo', self.redo, 'Ctrl+Y (on this page)'))):
            files.addWidget(QPushButton(text, clicked=slot, toolTip=tip),
                            i // 2, i % 2)
        v.addLayout(files)
        self.use_btn = QPushButton(
            'Use in runs', clicked=self.use_in_runs, toolTip=(
                'save the drawing and name it in the settings (shapes): a run '
                'adds its copper to the board and takes its cuts out'))
        self.region_btn = QPushButton(
            'Region from shape', clicked=self.region_from_selected,
            toolTip='the selected shape as the region simulated (its bounding '
            'box, unless it is a rectangle)')
        self.ports_btn = QPushButton(
            'Ports to settings', clicked=self.ports_to_settings,
            toolTip='the drawn ports added to the extra ports setting')
        for b in (self.use_btn, self.region_btn, self.ports_btn):
            v.addWidget(b)
            b.setEnabled(self.win is not None)
        self.board_on = QCheckBox('show the board', checked=True,
                                  toggled=lambda _: self._refresh(board=True))
        v.addWidget(self.board_on)
        # (scrolled: the page fits a short dock)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(bottom)
        side.addWidget(scroll)
        side.setSizes([380, 560])
        return side

    def _form_page(self):
        """The selected shape's properties."""
        page = QWidget()
        v = QVBoxLayout(page)
        self.form_note = QLabel(wordWrap=True, styleSheet='color: gray')
        v.addWidget(self.form_note)
        self.form = QFormLayout()
        self.p_name = QLineEdit(placeholderText='(its kind)')
        self.p_material = QComboBox()
        self.p_material.addItems(sh.MATERIALS)
        self.p_layer = QComboBox()
        self.p_net = QLineEdit(placeholderText='(the copper it overlaps)')
        self.p_width = _spin(0.5, 0.001, 100, ' mm')
        self.p_radius = _spin(1.0, 0.001, 1000, ' mm')
        self.p_z0 = _spin(0.0, -100, 100, ' mm')
        self.p_z1 = _spin(1.0, -100, 100, ' mm')
        self.p_er = _spin(4.3, 1, 100, '', decimals=3)
        self.p_tand = _spin(0.0, 0, 1, '', decimals=4)
        self.p_rot = QLineEdit(placeholderText='(along the trace)')
        self.p_points = QPlainTextEdit(
            toolTip='one point a line: x, y (model mm, y up)')
        self.p_points.setMaximumHeight(110)
        self.rows = {}
        for key, text, w in (('name', 'Name', self.p_name),
                             ('material', 'Material', self.p_material),
                             ('layer', 'Layer', self.p_layer),
                             ('net', 'Net', self.p_net),
                             ('width', 'Width', self.p_width),
                             ('radius', 'Radius', self.p_radius),
                             ('z0', 'Bottom z', self.p_z0),
                             ('z1', 'Top z', self.p_z1),
                             ('er', 'εr', self.p_er),
                             ('tand', 'tan δ', self.p_tand),
                             ('rot', 'Rotation (°)', self.p_rot),
                             ('points', 'Points', self.p_points)):
            self.form.addRow(text, w)
            self.rows[key] = w
        for w in (self.p_name, self.p_net, self.p_rot):
            w.editingFinished.connect(self._edited)
        for w in (self.p_width, self.p_radius, self.p_z0, self.p_z1, self.p_er,
                  self.p_tand):
            w.editingFinished.connect(self._edited)
        for w in (self.p_material, self.p_layer):
            w.activated.connect(lambda _: self._edited())
        v.addLayout(self.form)
        v.addWidget(QPushButton('Apply points', clicked=self._edited,
                                toolTip='the points typed above'))
        v.addStretch(1)
        return page

    def _arrange_page(self):
        """Exact edits of the selection: move by, rotate by, mirror, align,
        distribute."""
        page = QWidget()
        g = QGridLayout(page)
        btn = lambda text, slot, tip='': QPushButton(text, clicked=slot,
                                                     toolTip=tip)
        self.mv = [_narrow(_spin(0.0, -1000, 1000, ' mm', tip)) for tip in (
            'dx: along x', 'dy: along y', 'dz: up (solids)')]
        g.addWidget(QLabel('Move by', toolTip='dx, dy, dz'), 0, 0)
        for k, w in enumerate(self.mv):
            g.addWidget(w, 0, 1 + k)
        g.addWidget(btn('Move', lambda: self.move_by(
            *(w.value() for w in self.mv)), 'the selection by this much '
            '(locked ones stay)'), 1, 1, 1, 3)
        self.rot_angle = _narrow(_spin(45.0, -360, 360, '°', 'anticlockwise',
                                       decimals=3))
        self.rot_about = QComboBox(toolTip='the point turned about')
        self.rot_about.addItems(['their centre', 'each its own centre',
                                 'the point'])
        self.rot_xy = [_narrow(_spin(0.0, -1000, 1000, ' mm',
                                     f"the point's {a}")) for a in 'xy']
        g.addWidget(QLabel('Rotate'), 2, 0)
        g.addWidget(self.rot_angle, 2, 1)
        g.addWidget(self.rot_about, 2, 2, 1, 2)
        g.addWidget(QLabel('about', toolTip='the point'), 3, 0)
        g.addWidget(self.rot_xy[0], 3, 1)
        g.addWidget(self.rot_xy[1], 3, 2)
        g.addWidget(btn('Pick', lambda: self._pick(
            self._set_rot_point, 'click the point to rotate about'),
            'click the point in the plan'), 3, 3)
        g.addWidget(btn('Rotate', self._rotate_clicked), 4, 1, 1, 3)
        g.addWidget(QLabel('Mirror'), 5, 0)
        g.addWidget(btn('x', lambda: self.mirror('x'), 'x reversed (left to '
                        'right) about their centre'), 5, 1)
        g.addWidget(btn('y', lambda: self.mirror('y'), 'y reversed (top to '
                        'bottom) about their centre'), 5, 2)
        g.addWidget(QLabel('Align'), 6, 0)
        for k, (how, text) in enumerate((('left', 'Left'), ('hcentre',
                                                             'Centre'),
                                         ('right', 'Right'), ('top', 'Top'),
                                         ('vcentre', 'Middle'),
                                         ('bottom', 'Bottom'))):
            g.addWidget(btn(text, lambda _=False, h=how: self.align(h),
                            f"to the selection's {how} (locked shapes stay, "
                            'and count)'), 6 + k // 3, 1 + k % 3)
        g.addWidget(QLabel('Distribute'), 8, 0)
        g.addWidget(btn('along x', lambda: self.distribute('x'),
                        'centres evenly between the outermost two'), 8, 1)
        g.addWidget(btn('along y', lambda: self.distribute('y'),
                        'centres evenly between the outermost two'), 8, 2)
        g.setRowStretch(9, 1)
        return page

    def _array_page(self):
        """Copies of the selection in a row, a grid or round a circle."""
        page = QWidget()
        v = QVBoxLayout(page)
        f = self.arr_form = QFormLayout()
        self.arr_kind = QComboBox(toolTip='a row (a via fence), a grid '
                                  '(stitching), or round a centre')
        self.arr_kind.addItems(['row', 'grid', 'circle'])
        self.arr_kind.currentTextChanged.connect(self._array_rows)
        self.arr_n = QSpinBox(minimum=2, maximum=10000, value=10,
                              toolTip='how many in all, the selection the '
                              'first (a grid: along x)')
        self.arr_ny = QSpinBox(minimum=1, maximum=10000, value=3,
                               toolTip='how many along y')
        self.arr_dx = _spin(1.0, -1000, 1000, ' mm', 'the step along x')
        self.arr_dy = _spin(0.0, -1000, 1000, ' mm', 'the step along y')
        self.arr_c = [_narrow(_spin(0.0, -1000, 1000, ' mm',
                                    f"the centre's {a}")) for a in 'xy']
        cen = QHBoxLayout()
        for w in self.arr_c:
            cen.addWidget(w)
        cen.addWidget(QPushButton('Pick', clicked=lambda: self._pick(
            self._set_array_centre, 'click the centre of the circle')))
        self.arr_angle = _spin(360.0, -360, 360, '°', 'from the first to the '
                               'last (360: evenly the whole way round)')
        self.arr_turn = QCheckBox('turn each copy', checked=True)
        self.arr_rows = {}
        for key, text, w in (('kind', 'Kind', self.arr_kind),
                             ('n', 'Count', self.arr_n),
                             ('ny', 'Rows (y)', self.arr_ny),
                             ('dx', 'Step x', self.arr_dx),
                             ('dy', 'Step y', self.arr_dy),
                             ('centre', 'Centre', cen),
                             ('angle', 'Angle', self.arr_angle),
                             ('turn', '', self.arr_turn)):
            f.addRow(text, w)
            self.arr_rows[key] = w
        v.addLayout(f)
        v.addWidget(QPushButton('Make the array', clicked=self._array_clicked,
                                toolTip='copies of the selection, added after '
                                'it (one undo step)'))
        v.addStretch(1)
        self._array_rows('row')
        return page

    def _array_rows(self, kind):
        shown = dict(row=('n', 'dx', 'dy'), grid=('n', 'ny', 'dx', 'dy'),
                     circle=('n', 'centre', 'angle', 'turn'))[kind]
        for key, w in self.arr_rows.items():
            if key != 'kind':
                self.arr_form.setRowVisible(w, key in shown)

    # ---- the board under the drawing ----
    def set_board(self, layout):
        """The board drawn on (core.layout.board_layout's dict, a run's
        board.json's; None: none, a default two-layer stack)."""
        self.layout_ = layout
        self.stack = sh.stack_of(layout)
        self._board3d, self._board_ver = None, self._board_ver + 1
        self.snapper, self._snap_stale = so.Snapper(layout), True
        layers = sorted(self.stack['cu_z'], key=lambda l: -self.stack['cu_z'][l])
        keep = self.layer.currentText()
        for box in (self.layer, self.p_layer):
            with QSignalBlocker(box):
                box.clear()
                box.addItems(layers)
        if keep in layers:
            self.layer.setCurrentText(keep)
        top = max([l['z1'] for l in self.stack['layers']] or
                  list(self.stack['cu_z'].values()) or [0.0])
        with QSignalBlocker(self.z0):
            self.z0.setValue(top)
        self.z1.setValue(top + 1.0)
        self._fill_show_menu()
        self._refresh(board=True)
        b = (shape_bounds(layout) if layout else None) or self.drawing.bounds()
        if b:
            pad = 0.05 * max(b[2] - b[0], b[3] - b[1], 1)
            self.vb.setRange(xRange=(b[0] - pad, b[2] + pad),
                             yRange=(b[1] - pad, b[3] + pad))
            x0, y0, x1, y1 = b
            for w, val in zip(self.arr_c + self.rot_xy,
                              ((x0 + x1) / 2, (y0 + y1) / 2) * 2):
                w.setValue(round(val, 3))

    def _layer_changed(self):
        self._refresh()
        self._level_changed()

    def _level_changed(self):
        if self.gl is not None:
            self._update_grid_3d()

    # ---- tools ----
    def set_tool(self, key):
        self.tool, self.pending = key, []
        if key != 'measure':
            self._clear_measure()
        self.tool_btns[key].setChecked(True)
        self.status.setText(f"{TOOLS[key][0]}: {TOOLS[key][2]}")
        self._rubber()

    def _material_state(self, m=None):
        solid = (m or self.material.currentText()) in sh.SOLID
        self.z0.setEnabled(solid)
        self.z1.setEnabled(solid)
        self._level_changed()

    def _snap(self, p, skip=(), objects=None):
        """Point p (a QPointF, or (x, y)) snapped: to the nearest pad, via,
        track end, board edge or shape corner, midpoint or centre within
        SNAP_PX pixels (objects on; the shapes of index in `skip` left out),
        else to the grid. [x, y]; self._snapped says what it snapped to."""
        x, y = (p.x(), p.y()) if hasattr(p, 'x') else p
        self._snapped = None
        if self.osnap.isChecked() if objects is None else objects:
            if self._snap_stale:
                self.snapper.set_shapes(self.drawing.shapes)
                self._snap_stale = False
            hit = self.snapper.find(x, y, self._tol() * SNAP_PX / 6, skip)
            if hit is not None:
                self._snapped = hit
                return hit.xy
        g = self.snap.value()
        if g > 0:
            x, y = round(x / g) * g, round(y / g) * g
        return [round(x, 6), round(y, 6)]

    def _show_snap(self):
        s = self._snapped
        if s is None:
            self.snap_mark.setData([], [])
        else:
            sym = {'corner': 's', 'pad': 's', 'board corner': 's',
                   'midpoint': 't', 'board edge': 'x', 'via': 'o',
                   'centre': 'o', 'rim': 'd', 'end': 'd', 'track end': 'd',
                   'track corner': 'd', 'port': 'd'}.get(s.what, 'o')
            self.snap_mark.setData([s.x], [s.y], symbol=sym)

    def _new_shape(self, kind, points, **kw):
        m = self.material.currentText()
        if kind == 'port':
            m = 'copper'
        args = dict(material=m, layer=self.layer.currentText() or 'F.Cu')
        if kind == 'path':
            args['width'] = self.width.value()
        if m in sh.SOLID:
            args.update(z0=self.z0.value(), z1=self.z1.value())
        args.update(kw)
        try:
            return Shape(kind, points, **args)
        except ValueError as e:
            self.status.setText(str(e))
            return None

    def _pick(self, then, what):
        """The next click of the plan, snapped, given to `then` (a point to
        rotate about, an array's centre)."""
        self._pick_point = then
        self.status.setText(f"{what} (Esc: not now)")

    def _set_rot_point(self, q):
        for w, v in zip(self.rot_xy, q):
            w.setValue(v)
        self.rot_about.setCurrentIndex(2)
        self.status.setText(f"rotate about ({q[0]:g}, {q[1]:g})")

    def _set_array_centre(self, q):
        for w, v in zip(self.arr_c, q):
            w.setValue(v)
        self.arr_kind.setCurrentText('circle')
        self.status.setText(f"the circle's centre ({q[0]:g}, {q[1]:g})")

    def _click(self, p, ev):
        """A click of the plan (model mm). True: taken."""
        right = ev.button() == Qt.RightButton
        if self._pick_point is not None and not right:
            then, self._pick_point = self._pick_point, None
            then(self._snap(p))
            self._show_snap()
            return True
        mods = ev.modifiers()
        if self.tool == 'select':
            if right:
                return False  # the view's menu
            i = self.hit(p.x(), p.y())
            if mods & (Qt.ShiftModifier | Qt.ControlModifier):
                if i is not None:
                    self.select(i, 'toggle')
            else:
                self.select(i)
            return True
        q = self._snap(p)
        if self.tool == 'measure':
            if right:
                self._clear_measure()
                return True
            if len(self.measure_pts) >= 3:
                self.measure_pts = []
            self.measure_pts.append(q)
            self._draw_measure()
            return True
        if right:  # ends a polygon or a trace
            self._finish()
            return True
        if self.tool == 'port':
            self.add(self._new_shape('port', [q]))
            return True
        if ev.double():
            self._finish()
            return True
        if self.pending and q == self.pending[-1]:
            return True
        self.pending.append(q)
        if self.tool == 'rect' and len(self.pending) == 2:
            self.add(self._new_shape('rect', self.pending))
            self.pending = []
        elif self.tool == 'circle' and len(self.pending) == 2:
            (cx, cy), (x, y) = self.pending
            r = float(np.hypot(x - cx, y - cy))
            if r > 0:
                self.add(self._new_shape('circle', [[cx, cy]], radius=r))
            self.pending = []
        self._rubber()
        return True

    def _finish(self):
        """The polygon or trace being drawn, ended."""
        kind, pts = self.tool, self.pending
        self.pending = []
        if kind in ('polygon', 'path') and len(pts) >= sh.LEAST[kind]:
            self.add(self._new_shape(kind, pts))
        self._rubber()

    def _moved(self, scene_pos):
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return
        if self._drag_state is not None:
            return  # (the drag says where it is)
        p = self.vb.mapSceneToView(scene_pos)
        x, y = self._snap(p)
        self._show_snap()
        self._say_at(x, y, self._size_text([x, y]))
        if self.pending:
            self._rubber([x, y])
        elif self.tool == 'measure' and 0 < len(self.measure_pts) < 3:
            self._draw_measure([x, y])

    def _say_at(self, x, y, size=''):
        """The status bar's cursor (model and KiCad mm), the size being drawn
        and what the point snapped to."""
        s = self._snapped
        self.coords.setText(
            f"x {x:.3f}  y {y:.3f} mm (KiCad {x:.3f}, {-y:.3f})" +
            (f"  ·  {size}" if size else '') +
            (f"  ·  snap: {s.label}" if s is not None else ''))

    def _size_text(self, at):
        """What is being drawn to the mouse at `at`, as numbers."""
        pts = self.pending + [at]
        if self.tool == 'measure' and self.measure_pts and len(
                self.measure_pts) < 3:
            return _measure_text(self.measure_pts + [at])
        if len(pts) < 2:
            return ''
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        if self.tool == 'rect':
            return f"w {abs(x1 - x0):.3f} × h {abs(y1 - y0):.3f} mm"
        if self.tool == 'circle':
            r = float(np.hypot(x1 - x0, y1 - y0))
            return f"r {r:.3f} mm (Ø {2 * r:.3f})"
        m = so.measure(pts)
        leg = float(np.hypot(x1 - x0, y1 - y0))
        ang = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
        return (f"leg {leg:.3f} mm at {ang:.1f}°" + (
            f", {m['length']:.3f} mm in all" if len(pts) > 2 else ''))

    def _rubber(self, at=None):
        """The shape being drawn, to the mouse at `at`."""
        pts = self.pending + ([at] if at else [])
        g = None
        if len(pts) >= 2:
            try:
                kind = self.tool
                if kind == 'circle':
                    (cx, cy), (x, y) = pts[:2]
                    s = Shape('circle', [[cx, cy]],
                              radius=max(np.hypot(x - cx, y - cy), 1e-6))
                elif kind == 'polygon' and len(pts) < 3:
                    s = Shape('path', pts, width=0.02)
                elif kind == 'path':
                    s = Shape('path', pts, width=self.width.value())
                else:
                    s = Shape(kind, pts)
                g = s.outline()
            except ValueError:
                g = None
        self.rubber.setPath(_path([_rings(g)] if g is not None else []))

    # ---- measuring ----
    def _draw_measure(self, at=None):
        pts = self.measure_pts + ([at] if at else [])
        self.measure_line.setPath(_polyline(pts))
        if len(pts) >= 2:
            text = _measure_text(pts)
            self.measure_text.setText(text)
            # (on the side of the last point towards the middle of the view)
            (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
            x, y = pts[-1]
            self.measure_text.setAnchor((1 if x > (vx0 + vx1) / 2 else 0,
                                         0 if y > (vy0 + vy1) / 2 else 1))
            self.measure_text.setPos(x, y)
            if at is None:
                self.status.setText(f"Measure: {text}")
        else:
            self.measure_text.setText('')

    def _clear_measure(self):
        self.measure_pts = []
        self.measure_line.setPath(QPainterPath())
        self.measure_text.setText('')

    # ---- dragging in the plan ----
    def _drag(self, ev):
        """A left-button drag in the Select tool: the selected shape's handle,
        the selection moved, or a box drawn. False: the view's own (another
        tool's drag, a Ctrl + drag: it pans)."""
        if ev.isStart():
            if (self.tool != 'select'
                    or ev.modifiers() & Qt.ControlModifier):
                self._drag_state = None
                return False
            self._drag_state = self._drag_begin(ev)
            return self._drag_state is not None
        st = self._drag_state
        if st is None:
            return False
        p = self.vb.mapSceneToView(ev.scenePos())
        st['last'] = (p.x(), p.y())
        self._drag_to(st)
        if ev.isFinish():
            self._drag_end(st)
        return True

    def _drag_begin(self, ev):
        p0 = self.vb.mapSceneToView(ev.buttonDownScenePos())
        x0, y0 = p0.x(), p0.y()
        tol = self._tol()
        before = self.drawing.to_dict()
        self._axis = None
        if len(self.selected) == 1:
            s = self.drawing.shapes[self.sel]
            if not s.locked and not s.hidden:
                hs = np.asarray(s.handles())
                d = np.hypot(hs[:, 0] - x0, hs[:, 1] - y0)
                if d.min() <= tol:
                    return dict(kind='handle', i=self.sel, h=int(d.argmin()),
                                before=before)
        i = self.hit(x0, y0)
        if i is not None and not self.drawing.shapes[i].locked:
            if i not in self.selected:
                self.select(i, 'add' if ev.modifiers() & Qt.ShiftModifier
                            else 'only')
            movable = self._movable()
            # the point grabbed, snapped (to the shape's own corner, say)
            grab = self._snap(p0)
            return dict(kind='move', grab=grab, before=before,
                        orig={j: self.drawing.shapes[j] for j in movable})
        return dict(kind='box', p0=(x0, y0), add=bool(
            ev.modifiers() & Qt.ShiftModifier), before=None)

    def _drag_to(self, st):
        """The drag in `st` to the mouse at st['last']."""
        if st.get('cancelled'):
            return
        x, y = st['last']
        if st['kind'] == 'box':
            (x0, y0), crossing = st['p0'], x < st['p0'][0]
            r = QPainterPath()
            r.addRect(min(x0, x), min(y0, y), abs(x - x0), abs(y - y0))
            self.band.setPath(r)
            c = QColor(40, 170, 80) if crossing else SELECTED
            self.band.setPen(_cosmetic(QPen(c, 1.2, Qt.DashLine if crossing
                                            else Qt.SolidLine)))
            self.band.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(),
                                             30)))
            self.coords.setText(
                f"box {abs(x - x0):.3f} × {abs(y - y0):.3f} mm  ·  " +
                ('touching it (right to left)' if crossing else
                 'inside it (left to right)'))
            return
        if st['kind'] == 'handle':
            q = self._snap((x, y), skip={st['i']})
            self.drawing.shapes[st['i']].drag_handle(st['h'], *q)
            self._show_snap()
            self._say_at(*q)
        else:
            q = self._snap((x, y), skip=set(st['orig']))
            dx, dy = q[0] - st['grab'][0], q[1] - st['grab'][1]
            if self._axis == 'x':
                dy = 0.0
            elif self._axis == 'y':
                dx = 0.0
            st['d'] = dx, dy = round(dx, 6), round(dy, 6)
            # the shapes' items moved (the drawing itself at the end: made
            # again on each move, 300 shapes took 34 ms a move)
            for j in st['orig']:
                for it in self._items_of(j):
                    it.setPos(dx, dy)
            if len(st['orig']) == 1 and len(self.selected) == 1:
                self.handles.setPos(dx, dy)
            self._show_snap()
            self._say_at(*q, f"moved dx {dx:.3f}, dy {dy:.3f} mm" + (
                f" (along {self._axis} only)" if self._axis else ''))
            return
        self._refresh(listing=False, quick=True)

    def _drag_end(self, st):
        self._drag_state, self._axis = None, None
        self.snap_mark.setData([], [])
        if st.get('cancelled'):
            return
        if st['kind'] == 'box':
            self.band.setPath(QPainterPath())
            (x0, y0), (x1, y1) = st['p0'], st['last']
            self.box_select(x0, y0, x1, y1, crossing=x1 < x0, add=st['add'])
            return
        if st['kind'] == 'move':
            dx, dy = st.get('d', (0.0, 0.0))
            for j, s in st['orig'].items():
                self.drawing.shapes[j] = s.moved(dx, dy)
            self.handles.setPos(0, 0)
            if not dx and not dy:
                self._refresh(listing=False, quick=True)
                return
        if self.drawing.to_dict() != st['before']:
            self._commit(st['before'])

    def _cancel_drag(self):
        """Esc while dragging: the shapes back where they were."""
        st = self._drag_state
        if st is None or st['kind'] == 'box':
            if st is not None:
                st['cancelled'] = True
                self.band.setPath(QPainterPath())
            return
        st['cancelled'] = True
        if st['kind'] == 'move':  # (the drawing is as it was: the items not)
            self.handles.setPos(0, 0)
            self._refresh(listing=False, quick=True)
        else:
            self.drawing = Drawing.from_dict(st['before'])
            self._refresh(listing=False)
        self.status.setText('the drag undone (Esc)')

    def _tol(self):
        """Six pixels of the plan, in mm."""
        sx, sy = self.vb.viewPixelSize()
        return 6 * max(sx, sy)

    def hit(self, x, y):
        """The shape at (x, y) model mm (the last drawn on top; those of the
        layer shown first; hidden ones never), or None."""
        from shapely.geometry import Point
        pt, tol = Point(x, y), self._tol()
        order = sorted(range(len(self.drawing.shapes)),
                       key=lambda i: (self._on_layer(self.drawing.shapes[i]), i))
        for i in reversed(order):
            s = self.drawing.shapes[i]
            if s.hidden:
                continue
            g = _outline(s)
            d = (pt.distance(Point(s.points[0])) if g is None else
                 g.distance(pt))
            if d <= tol:
                return i
        return None

    def box_select(self, x0, y0, x1, y1, crossing=False, add=False):
        """The shapes wholly inside the box (crossing: touching it) selected
        (add: as well as those already); hidden ones never."""
        from shapely.geometry import box, Point
        b = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        hits = []
        for i, s in enumerate(self.drawing.shapes):
            if s.hidden:
                continue
            g = _outline(s)
            g = Point(s.points[0]) if g is None else g
            if b.intersects(g) if crossing else b.contains(g):
                hits.append(i)
        self.select_many(hits, add=add)
        self.status.setText(f"{len(hits)} shape(s) in the box" + (
            f", {len(self.selected)} selected in all" if add else ''))
        return hits

    def _on_layer(self, s):
        return s.solid or s.layer == self.layer.currentText()

    # ---- editing (each step undoable) ----
    def _commit(self, before):
        """The drawing changed from `before` (its dict): a step to undo."""
        self._undo.append(before)
        self._redo.clear()
        self._snap_stale = True
        self._refresh()
        self.changed.emit()

    def _movable(self):
        """The selected shapes that may move: not locked, not hidden."""
        ss = self.drawing.shapes
        return [j for j in self.selected
                if j < len(ss) and not ss[j].locked and not ss[j].hidden]

    def _kept_note(self):
        n = len(self.selected) - len(self._movable())
        return f" ({n} locked or hidden: left)" if n else ''

    def _transform(self, fn, what):
        """The movable selection replaced by fn(its shapes) (as many new
        shapes), one undo step. False: nothing to change."""
        idx = self._movable()
        if not idx:
            self.status.setText('select a shape first (one not locked or '
                                'hidden)')
            return False
        note = self._kept_note()
        before = self.drawing.to_dict()
        try:
            new = fn([self.drawing.shapes[j] for j in idx])
        except ValueError as e:
            self.status.setText(f"not changed: {e}")
            return False
        for j, s in zip(idx, new):
            self.drawing.shapes[j] = s
        if self.drawing.to_dict() == before:
            return False
        self._commit(before)
        self.status.setText(f"{what}: {len(idx)} shape(s){note}")
        return True

    def _offsets(self, offsets, what):
        """Each selected shape moved by its (dx, dy) of `offsets` (in the
        selection's order)."""
        by = dict(zip(self.selected, offsets))
        return self._transform(
            lambda ss: [s.moved(*by[j]) for j, s in zip(self._movable(), ss)],
            what)

    def _append(self, shapes, what, keep_selection=False):
        """Shapes added at the end, selected (keep_selection: with the
        selection), one undo step."""
        shapes = [s for s in shapes if s is not None]
        if not shapes:
            return
        before = self.drawing.to_dict()
        n = len(self.drawing.shapes)
        self.drawing.shapes += shapes
        new = list(range(n, n + len(shapes)))
        self.selected = (self.selected + new) if keep_selection else new
        self._commit(before)
        self.status.setText(f"{what}: {len(shapes)} shape(s)")

    def add(self, shape):
        if shape is None:
            return
        before = self.drawing.to_dict()
        self.drawing.shapes.append(shape)
        self.selected = [len(self.drawing.shapes) - 1]
        self._commit(before)

    def delete(self):
        idx = set(self._movable())
        kept = len(self.selected) - len(idx)
        if not idx:
            if self.selected:
                self.status.setText('locked or hidden: not deleted (unlock or '
                                    'show it first)')
            return
        before = self.drawing.to_dict()
        self.drawing.shapes = [s for i, s in enumerate(self.drawing.shapes)
                               if i not in idx]
        self.selected = []
        self._commit(before)
        self.status.setText(f"deleted {len(idx)} shape(s)" + (
            f" ({kept} locked or hidden: kept)" if kept else ''))

    def duplicate(self):
        if not self.selected:
            return
        g = self.snap.value() or 0.5
        copies = [self.drawing.shapes[j].moved(g, -g) for j in self.selected]
        for c in copies:
            c.locked = c.hidden = False
        self._append(copies, 'duplicated')

    def copy(self):
        """The selection to the clipboard (kicad2ems-shapes text)."""
        if not self.selected:
            self.status.setText('select what to copy first')
            return None
        text = so.to_clipboard([self.drawing.shapes[j] for j in
                                self.selected])
        QApplication.clipboard().setText(text)
        self.status.setText(f"copied {len(self.selected)} shape(s)")
        return text

    def cut(self):
        if self.copy() is not None:
            self.delete()

    def paste(self, text=None):
        """The clipboard's shapes (or those of `text`) added, selected: where
        they were, or a grid step along if exactly there already."""
        shapes = so.from_clipboard(
            QApplication.clipboard().text() if text is None else text)
        if not shapes:
            self.status.setText('the clipboard holds no shapes (copy some '
                                'first, here or in another drawing)')
            return []
        here = {_key(s) for s in self.drawing.shapes}
        g = self.snap.value() or 0.5
        for _ in range(100):
            if not all(_key(s) in here for s in shapes):
                break
            shapes = [s.moved(g, -g) for s in shapes]
        self._append(shapes, 'pasted')
        return shapes

    def select_all(self):
        self.select_many([i for i, s in enumerate(self.drawing.shapes)
                          if not s.hidden])

    def set_locked(self, flag=None):
        """The selection locked (True), unlocked (False) or, None, locked
        unless it all is."""
        self._set_flag('locked', flag)

    def set_hidden(self, flag=None):
        """The selection hidden (True), shown (False) or, None, hidden
        unless it all is."""
        self._set_flag('hidden', flag)

    def _set_flag(self, key, flag):
        ss = self.drawing.shapes
        idx = [j for j in self.selected if j < len(ss)]
        if not idx:
            self.status.setText('select the shapes first')
            return
        if flag is None:
            flag = not all(getattr(ss[j], key) for j in idx)
        before = self.drawing.to_dict()
        for j in idx:
            setattr(ss[j], key, bool(flag))
        if self.drawing.to_dict() != before:
            self._commit(before)
            self.status.setText(f"{len(idx)} shape(s) " + (
                ('locked' if flag else 'unlocked') if key == 'locked' else
                ('hidden' if flag else 'shown')))

    def move_by(self, dx=0.0, dy=0.0, dz=0.0):
        """The selection moved by (dx, dy) mm, its solids lifted dz."""
        return self._transform(
            lambda ss: [(s.moved(dx, dy).lifted(dz) if dz and s.solid else
                         s.moved(dx, dy)) for s in ss],
            f"moved by ({dx:g}, {dy:g}" + (f", {dz:g}" if dz else '') + ') mm')

    def rotate_by(self, angle, about=None):
        """The selection turned `angle` degrees anticlockwise: about its
        centre (about None), each about its own ('each'), or about the point
        (x, y)."""
        if about == 'each':
            fn = lambda ss: [s.rotated(angle) for s in ss]
        else:
            fn = lambda ss: so.rotated(ss, angle, about)
        return self._transform(fn, f"turned {angle:g}°")

    def rotate(self):
        """The selection turned 90 degrees anticlockwise about its centre."""
        return self.rotate_by(90)

    def _rotate_clicked(self):
        k = self.rot_about.currentIndex()
        self.rotate_by(self.rot_angle.value(), None if k == 0 else 'each'
                       if k == 1 else tuple(w.value() for w in self.rot_xy))

    def mirror(self, axis):
        """The selection mirrored about its centre: 'x' reverses x, 'y' y."""
        return self._transform(lambda ss: so.mirrored(ss, axis),
                               f"mirrored in {axis}")

    def align(self, how):
        """The selection lined up (core.shapeops.ALIGN: left, right, hcentre,
        top, bottom, vcentre); locked ones stay, as anchors."""
        ss = [self.drawing.shapes[j] for j in self.selected]
        keep = {k for k, j in enumerate(self.selected)
                if j not in self._movable()}
        if len(ss) < 2:
            self.status.setText('select two shapes or more to align')
            return False
        return self._offsets(so.aligned(ss, how, keep), f"aligned {how}")

    def distribute(self, axis):
        """The selection's centres spaced evenly along 'x' or 'y'."""
        ss = [self.drawing.shapes[j] for j in self.selected]
        keep = {k for k, j in enumerate(self.selected)
                if j not in self._movable()}
        if len(ss) < 3:
            self.status.setText('select three shapes or more to distribute')
            return False
        return self._offsets(so.distributed(ss, axis, keep),
                             f"distributed along {axis}")

    def make_array(self, kind, n=2, ny=1, dx=0.0, dy=0.0, centre=(0, 0),
                   angle=360.0, turn=True):
        """Copies of the selection: a row of n (each (dx, dy) on), a grid of
        n by ny (dx, dy apart), or n round `centre` over `angle` degrees
        (turn: each copy turned too). The copies are added after it and
        selected with it."""
        ss = [self.drawing.shapes[j] for j in self.selected]
        if not ss:
            self.status.setText('select what to make an array of first')
            return []
        if kind == 'row':
            new = so.array_row(ss, n, dx, dy)
        elif kind == 'grid':
            new = so.array_grid(ss, n, ny, dx, dy)
        else:
            new = so.array_circle(ss, n, centre, angle, turn)
        for s in new:
            s.locked = s.hidden = False
        self._append(new, f"an array ({kind})", keep_selection=True)
        return new

    def _array_clicked(self):
        k = self.arr_kind.currentText()
        self.make_array(k, n=self.arr_n.value(), ny=self.arr_ny.value(),
                        dx=self.arr_dx.value(), dy=self.arr_dy.value(),
                        centre=tuple(w.value() for w in self.arr_c),
                        angle=self.arr_angle.value(),
                        turn=self.arr_turn.isChecked())

    def to_polygon(self):
        """The selected rectangles, circles and traces as polygons of their
        outlines' corners."""
        def poly(s):
            g = s.outline(segments=32)
            if s.kind in ('polygon', 'port') or g is None:
                return s
            t = s.copy()
            t.points = [[round(x, 6), round(y, 6)]
                        for x, y in g.exterior.coords[:-1]]
            t.kind = 'polygon'
            return t
        return self._transform(lambda ss: [poly(s) for s in ss],
                               'made polygons')

    def _reorder(self, step):
        """The selection applied one place earlier (step -1) or later (+1),
        together."""
        ss = self.drawing.shapes
        sel = set(self.selected)
        order = sorted(sel, reverse=step > 0)
        if not order or not all(0 <= i + step < len(ss) for i in order[:1]):
            return
        before = self.drawing.to_dict()
        where = list(range(len(ss)))
        for i in order:
            j = i + step
            if 0 <= j < len(ss) and where[j] not in sel:
                where[i], where[j] = where[j], where[i]
        self.drawing.shapes = [ss[k] for k in where]
        new_at = {k: n for n, k in enumerate(where)}
        self.selected = [new_at[i] for i in self.selected]
        self._commit(before)

    def undo(self):
        if self._undo:
            self._redo.append(self.drawing.to_dict())
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self.drawing.to_dict())
            self._restore(self._redo.pop())

    def _restore(self, d):
        self.drawing = Drawing.from_dict(d)
        n = len(self.drawing.shapes)
        self.selected = [i for i in self.selected if i < n]
        self._snap_stale = True
        self._refresh()
        self.changed.emit()

    def new(self):
        if self.drawing.shapes:
            before = self.drawing.to_dict()
            self.drawing, self.selected = Drawing(), []
            self._commit(before)
        self.file = None

    # ---- the selection ----
    @property
    def sel(self):
        """The shape the form shows: the last one selected, or None."""
        return self.selected[-1] if self.selected else None

    def select(self, i, how='only'):
        """Shape i selected: alone ('only'; None: nothing), added ('add') or
        added or taken off ('toggle')."""
        cur = [j for j in self.selected if j != i]
        if how == 'only':
            new = [] if i is None else [i]
        elif i is None:
            new = list(self.selected)
        elif how == 'toggle' and i in self.selected:
            new = cur
        else:
            new = cur + [i]
        self._set_selection(new)

    def select_many(self, idx, add=False):
        idx = list(idx)
        self._set_selection(([j for j in self.selected if j not in idx] + idx)
                            if add else idx)

    def _set_selection(self, idx, from_list=False):
        n = len(self.drawing.shapes)
        seen, out = set(), []
        for j in idx:
            if j is not None and 0 <= j < n and j not in seen:
                seen.add(j)
                out.append(j)
        was, self.selected = self.selected, out
        if not from_list:
            self._sync_list_selection()
        self._fill_form()
        # the plan, and in 3D the shapes whose selection changed: nothing
        # else is drawn again
        self._refresh(listing=False, quick=True)
        self._selection_3d(was)

    def _listed(self):
        """The list's selection as the page's."""
        rows = sorted(self.list.indexOfTopLevelItem(it)
                      for it in self.list.selectedItems())
        cur = self.list.indexOfTopLevelItem(self.list.currentItem()) if (
            self.list.currentItem() is not None) else -1
        if cur in rows:  # the one clicked last: the form's
            rows.remove(cur)
            rows.append(cur)
        self._set_selection(rows, from_list=True)

    def _list_toggled(self, item, col):
        """A tick in the list: shown (column 0) or locked (1)."""
        k = self.list.indexOfTopLevelItem(item)
        if not 0 <= k < len(self.drawing.shapes) or col not in (0, 1):
            return
        s = self.drawing.shapes[k]
        on = item.checkState(col) == Qt.Checked
        key, value = ('hidden', not on) if col == 0 else ('locked', on)
        if getattr(s, key) == value:
            return
        before = self.drawing.to_dict()
        setattr(s, key, value)
        self._commit(before)

    def _fill_list(self):
        ss = self.drawing.shapes
        with QSignalBlocker(self.list):
            if self.list.topLevelItemCount() != len(ss):
                self.list.clear()
                items = []
                for _ in ss:
                    it = QTreeWidgetItem()
                    it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled
                                | Qt.ItemIsUserCheckable)
                    items.append(it)
                self.list.addTopLevelItems(items)
            for k, s in enumerate(ss):
                it = self.list.topLevelItem(k)
                it.setText(2, s.label())
                it.setCheckState(0, Qt.Unchecked if s.hidden else Qt.Checked)
                it.setCheckState(1, Qt.Checked if s.locked else Qt.Unchecked)
        self._sync_list_selection()

    def _sync_list_selection(self):
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        m = self.list.selectionModel()
        with QSignalBlocker(self.list), QSignalBlocker(m):
            sel = QItemSelection()
            model = self.list.model()
            for j in self.selected:
                if j < self.list.topLevelItemCount():
                    a = model.index(j, 0)
                    sel.select(a, model.index(j, 2))
            m.select(sel, QItemSelectionModel.ClearAndSelect)
            if self.sel is not None and self.sel < (
                    self.list.topLevelItemCount()):
                m.setCurrentIndex(model.index(self.sel, 2),
                                  QItemSelectionModel.NoUpdate)
                self.list.scrollToItem(self.list.topLevelItem(self.sel))
        self.list.viewport().update()

    def _fill_form(self):
        s = self.drawing.shapes[self.sel] if self.sel is not None else None
        for w in self.rows.values():
            w.setEnabled(s is not None)
        n = len(self.selected)
        self.form_note.setText(
            '' if n < 2 else f"{n} selected: the last picked is shown; a "
            "change here applies to them all (not the points or the name)")
        self.form_note.setVisible(n >= 2)
        if s is None:
            return
        blocks = [QSignalBlocker(w) for w in self.rows.values()]
        self.p_name.setText(s.name)
        self.p_material.setCurrentText(s.material)
        self.p_layer.setCurrentText(s.layer)
        self.p_net.setText(s.net)
        self.p_width.setValue(s.width)
        self.p_radius.setValue(s.radius)
        self.p_z0.setValue(s.z0)
        self.p_z1.setValue(s.z1)
        self.p_er.setValue(s.er)
        self.p_tand.setValue(s.tand)
        self.p_rot.setText('' if s.rot is None else f"{s.rot:g}")
        self.p_points.setPlainText('\n'.join(f"{x:g}, {y:g}"
                                             for x, y in s.points))
        del blocks
        shown = dict(material=s.kind != 'port', net=s.material == 'copper'
                     and s.kind != 'port', layer=not s.solid,
                     width=s.kind == 'path', radius=s.kind == 'circle',
                     z0=s.solid, z1=s.solid, er=s.material == 'dielectric',
                     tand=s.material == 'dielectric', rot=s.kind == 'port')
        for key, w in self.rows.items():
            self.form.setRowVisible(w, shown.get(key, True))

    def _edited(self):
        """The form's values into the shape it shows; a value changed there
        into the rest of the selection too (where it applies)."""
        if self.sel is None:
            return
        s = self.drawing.shapes[self.sel]
        before = self.drawing.to_dict()
        try:
            pts = [[float(c) for c in line.replace(';', ',').split(',')]
                   for line in self.p_points.toPlainText().splitlines()
                   if line.strip()]
            rot = self.p_rot.text().strip()
            new = Shape(s.kind, pts, material=self.p_material.currentText(),
                        layer=self.p_layer.currentText(),
                        net=self.p_net.text().strip(),
                        width=self.p_width.value(),
                        radius=self.p_radius.value(), z0=self.p_z0.value(),
                        z1=self.p_z1.value(), er=self.p_er.value(),
                        tand=self.p_tand.value(),
                        rot=float(rot) if rot else None,
                        name=self.p_name.text().strip(), hidden=s.hidden,
                        locked=s.locked)
        except ValueError as e:
            self.status.setText(f"not changed: {e}")
            return
        if new.to_dict() == s.to_dict():
            return
        changed = {k: getattr(new, k) for k in SHARED
                   if getattr(new, k) != getattr(s, k)}
        self.drawing.shapes[self.sel] = new
        for j in self.selected[:-1]:
            t = self.drawing.shapes[j]
            c = dict(changed)
            if t.kind == 'port':
                c.pop('material', None)
            try:
                self.drawing.shapes[j] = Shape(**{**t.__dict__, **c})
            except ValueError:
                pass
        self._commit(before)

    # ---- keys: the page's own while it has the focus ----
    _KEYS = (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Delete,
             Qt.Key_Backspace)
    _SEQS = (QKeySequence.Undo, QKeySequence.Redo, QKeySequence.Copy,
             QKeySequence.Cut, QKeySequence.Paste, QKeySequence.SelectAll)

    def event(self, e):
        # Ctrl+Z / Ctrl+Y here undo the drawing, not the window's settings
        if e.type() == QEvent.ShortcutOverride and (
                any(e.matches(q) for q in self._SEQS)
                or e.key() in self._KEYS or (
                    e.key() == Qt.Key_D
                    and e.modifiers() == Qt.ControlModifier)):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        k = e.key()
        plain = e.modifiers() & ~Qt.KeypadModifier == Qt.NoModifier
        letter = e.text().upper()
        dragging = self._drag_state is not None or (
            getattr(self, '_drag3d', None) is not None)
        if e.matches(QKeySequence.Undo):
            self.undo()
        elif e.matches(QKeySequence.Redo):
            self.redo()
        elif e.matches(QKeySequence.Copy):
            self.copy()
        elif e.matches(QKeySequence.Cut):
            self.cut()
        elif e.matches(QKeySequence.Paste):
            self.paste()
        elif e.matches(QKeySequence.SelectAll):
            self.select_all()
        elif k == Qt.Key_D and e.modifiers() == Qt.ControlModifier:
            self.duplicate()
        elif k == Qt.Key_Escape:
            if self._drag_state is not None:
                self._cancel_drag()
            elif getattr(self, '_drag3d', None) is not None:
                self._cancel_drag3d()
            elif self._pick_point is not None:
                self._pick_point = None
                self.status.setText('not now')
            elif self.pending:
                self.pending = []
                self._rubber()
            elif self.measure_pts:
                self._clear_measure()
            elif self.tool != 'select':
                self.set_tool('select')
            else:
                self.select(None)
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete()
        elif plain and dragging and letter in ('X', 'Y', 'Z'):
            a = letter.lower()
            self._axis = None if self._axis == a else a
            st = self._drag_state
            if st is not None and st.get('last') and st['kind'] == 'move':
                self._drag_to(st)
            self.status.setText(f"dragged along {a} only" if self._axis else
                                'dragged freely')
        elif plain and letter == 'F':
            self.frame_selection()
        elif plain and letter in ('7', '1', '3', '5'):
            self.camera(dict(zip('7135', ('top', 'front', 'side',
                                          'iso')))[letter])
        elif plain and letter in {v[1]: k for k, v in TOOLS.items()}:
            self.set_tool({v[1]: k for k, v in TOOLS.items()}[letter])
        else:
            super().keyPressEvent(e)

    def frame_selection(self):
        """The selection (else the drawing, else the board) filling the plan
        and the 3D view."""
        ss = [self.drawing.shapes[j] for j in self.selected] or [
            s for s in self.drawing.shapes if not s.hidden]
        b = so.bounds_of(ss) or shape_bounds(self.layout_)
        if b is not None:
            pad = 0.1 * max(b[2] - b[0], b[3] - b[1], 0.5)
            self.vb.setRange(xRange=(b[0] - pad, b[2] + pad),
                             yRange=(b[1] - pad, b[3] + pad), padding=0)
        self.frame_3d()

    # ---- drawing it ----
    def _refresh(self, listing=True, board=False, quick=False):
        """The plan (and the list, and soon the 3D view) as the drawing is."""
        n = len(self.drawing.shapes)
        if any(j >= n for j in self.selected):  # (a drawing set anew)
            self.selected = [j for j in self.selected if j < n]
        if board:
            self._draw_board()
        if listing:
            self._fill_list()
            self._fill_form()
        self._draw_shapes()
        one = (self.drawing.shapes[self.sel] if len(self.selected) == 1
               else None)
        hs = (one.handles() if one is not None and not one.locked
              and not one.hidden else [])
        self.handles.setData(pos=np.asarray(hs, float).reshape(-1, 2))
        if not quick:
            self._3d_timer.start()

    def _items_of(self, i):
        e = self._gfx[i] if i < len(self._gfx) else None
        return e['items'] if e else []

    def _draw_shapes(self):
        """The shapes' plan items as the drawing is: a shape whose outline
        is the same keeps its items (restyled when its look changed: a
        selection), the rest are made anew. (Made anew every time, 300 vias
        took 23 ms a redraw and 34 ms a dragged move.)"""
        pool = {}
        for e in self._gfx:
            if e is not None:
                pool.setdefault(e['geo'], []).append(e)
        sel, out = set(self.selected), []
        port_r = round(4 * self._tol() / 6, 9)
        for i, s in enumerate(self.drawing.shapes):
            if s.hidden:
                out.append(None)
                continue
            geo = (_geo_key(s),) + ((port_r, s.name) if s.kind == 'port'
                                    else ())
            style = (s.material, s.kind == 'port', i in sel,
                     self._on_layer(s))
            got = pool.get(geo)
            e = got.pop() if got else None
            if e is None:
                e = dict(geo=geo, style=None, items=self._make_items(s))
                for it in e['items']:
                    self.vb.addItem(it)
            else:
                for it in e['items']:
                    it.setPos(0, 0)
            if e['style'] != style:
                self._style_items(e['items'], s, i in sel)
                e['style'] = style
            out.append(e)
        for es in pool.values():
            for e in es:
                for it in e['items']:
                    self.vb.removeItem(it)
        self._gfx = out

    def _make_items(self, s):
        """A shape's plan items (unstyled): its outline, or a port's marker
        and its name."""
        g = _outline(s)
        if g is not None:
            return [_item(_qpath([_rings(g)]), z=20)]
        x, y = s.points[0]
        r = 4 * self._tol() / 6
        diamond = [[x, y + r], [x + r, y], [x, y - r], [x - r, y], [x, y + r]]
        txt = pg.TextItem(s.name or 'port', anchor=(0, 1))
        txt.setPos(x + r, y + r)
        return [_item(_qpath([[diamond]]), z=30), txt]

    def _style_items(self, items, s, selected):
        rgb = PORT_COLOUR if s.kind == 'port' else COLOURS[s.material]
        here = self._on_layer(s)
        line = SELECTED if selected else _qc(rgb)
        pen = QPen(line, 2.5 if selected else 1.2,
                   Qt.DashLine if s.solid else Qt.SolidLine)
        if not here and not selected:
            pen.setColor(_qc(rgb, 90))
        pen.setCosmetic(True)
        items[0].setPen(pen)
        if s.kind == 'port':
            items[0].setBrush(QBrush(_qc(rgb)))
            items[1].setColor(_qc(rgb))
            return
        # a solid (a lid over the whole board) leaves the copper under it seen
        fill = _qc(rgb, 60 if s.material == 'cut' else 35 if s.solid else
                   150 if here else 35)
        items[0].setBrush(QBrush(fill, Qt.BDiagPattern if s.material == 'cut'
                                 else Qt.SolidPattern))
        items[0].setZValue(20 if here else 10)

    def _draw_board(self):
        for it in self._board_items:
            self.vb.removeItem(it)
        self._board_items = []
        d = self.layout_
        if not d or not self.board_on.isChecked():
            return
        lay = self.layer.currentText()
        if d.get('outline'):
            self._board_items.append(
                _item(_path([rings(d['outline'])[0]]),
                      QPen(QColor(90, 110, 90), 1.2),
                      QBrush(QColor(60, 140, 70, 25)), -10))
        for l, items in (d.get('copper') or {}).items():
            polys = [poly for it in items for poly in rings(it['polygon'])]
            here = l == lay
            self._board_items.append(
                _item(_path(polys),
                      QPen(QColor(150, 100, 50, 160 if here else 40), .8),
                      QBrush(QColor(214, 140, 64, 110 if here else 25)),
                      -5 if here else -8))
        for it in self._board_items:
            self.vb.addItem(it)

    # ---- files and settings ----
    def save(self, path=None):
        """The drawing written to `path` (None: asked for). The path, or None
        (not saved)."""
        if not path:
            start = self.file or os.path.join(self._folder(), 'drawing.json')
            path, _ = QFileDialog.getSaveFileName(self, 'Save the drawing',
                                                  start, 'Drawing (*.json)')
            if not path:
                return None
        self.drawing.save(path)
        self.file = path
        self.status.setText(f"saved {path}")
        return path

    def load(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, 'Open a drawing',
                                                  self._folder(),
                                                  'Drawing (*.json)')
            if not path:
                return False
        try:
            d = Drawing.load(path)
        except (OSError, ValueError, TypeError, KeyError) as e:
            QMessageBox.warning(self, 'Open a drawing', f"{path}: {e}")
            return False
        before = self.drawing.to_dict()
        self.drawing, self.selected, self.file = d, [], path
        self._commit(before)
        return True

    def _folder(self):
        board = self._setting('board')
        return os.path.dirname(board) if board else os.getcwd()

    def _setting(self, key):
        try:
            w = self.win.setup.edits[key][0]
        except (AttributeError, KeyError):
            return None
        return w.text() if hasattr(w, 'text') else None

    def _set_setting(self, key, value):
        from .optionrow import set_value
        try:
            w = self.win.setup.edits[key][0]
        except (AttributeError, KeyError):
            self.status.setText(f"no {key} setting in this window")
            return False
        set_value(w, value)
        return True

    def use_in_runs(self):
        path = self.save(self.file)
        if path and self._set_setting('shapes', path):
            self.status.setText(
                f"runs add {os.path.basename(path)}'s shapes to the board "
                "(the shapes setting)")

    def region_from_selected(self):
        if self.sel is None:
            self.status.setText('select the shape that outlines the region')
            return
        s = self.drawing.shapes[self.sel]
        x0, y0, x1, y1 = s.bounds()
        # a polygon region is a config's list of points: the form takes a box
        r = [round(v, 4) for v in (x0, -y1, x1, -y0)]
        if self._set_setting('region', ','.join(f"{v:g}" for v in r)):
            self.status.setText(
                f"region {r} (KiCad mm)" + (
                    '' if s.kind == 'rect' else
                    ": the shape's bounding box (a config can hold its corners:"
                    f" region = {sh.region_setting(s)})"))

    def ports_to_settings(self):
        """The drawn ports appended to the extra ports setting (P<n>@x,y,layer:
        numbered after the board's ports and those already there)."""
        import re
        if not self.drawing.ports():
            self.status.setText('draw a port first (the Port tool)')
            return
        # (the field's items are '; '-separated: a spec holds commas)
        have = [t.strip() for t in (self._setting('ports') or '').split(';')
                if t.strip()]
        taken = {int(m.group(1)) for t in have
                 for m in [re.match(r'P(\d+)', t)] if m}
        board_ports = len((self.layout_ or {}).get('ports') or [])
        specs = sh.port_settings(self.drawing, first=board_ports + 1,
                                 taken=taken)
        if self._set_setting('ports', '; '.join(have + specs)):
            self.status.setText(f"ports added: {', '.join(specs)}")


def _geo_key(s):
    """What a shape's outline depends on (hashable)."""
    return (s.kind, tuple(tuple(p) for p in s.points),
            s.radius if s.kind == 'circle' else None,
            s.width if s.kind == 'path' else None)


@functools.lru_cache(maxsize=4096)
def _outline_of(key):
    kind, pts, radius, width = key
    if kind == 'port':
        return None
    try:
        return Shape(kind, [list(p) for p in pts], radius=radius or 1.0,
                     width=width or 0.5).outline()
    except ValueError:
        return None


def _outline(s):
    """s.outline(), kept for its shape (a shape's outline is asked for by
    the plan, the picking, the box and the 3D view: shapely each time took
    a third of a 300-shape redraw)."""
    return _outline_of(_geo_key(s))


def _qpath(polys):
    """A QPainterPath (odd-even fill: holes stay open) of [[outer, hole,
    ...], ...] closed rings, made in one go (pyqtgraph's arrayToQPath; a
    lineTo a point took half a redraw)."""
    rs = [np.asarray(r, float) for rings in polys for r in rings
          if len(r) >= 3]
    if not rs:
        return QPainterPath()
    xy = np.concatenate(rs)
    c = np.ones(len(xy), np.int32)
    c[np.cumsum([len(r) for r in rs]) - 1] = 0
    return pg.functions.arrayToQPath(xy[:, 0], xy[:, 1], connect=c,
                                     finiteCheck=False)


def _key(s):
    """A shape's identity for 'already there' (its geometry and what it is,
    not whether it is shown or locked)."""
    d = s.to_dict()
    d.pop('hidden', None)
    d.pop('locked', None)
    return repr(sorted(d.items()))


def _measure_text(pts):
    m = so.measure(pts)
    t = (f"{m['distance']:.3f} mm (dx {m['dx']:.3f}, dy {m['dy']:.3f}) at "
         f"{m['angle']:.1f}°") if m['angle'] is not None else ''
    if m['turn'] is not None:
        t = (f"{m['length']:.3f} mm along, {m['turn']:.1f}° between the legs;"
             f" ends {m['distance']:.3f} mm apart")
    return t


def _polyline(pts):
    p = QPainterPath()
    for k, (x, y) in enumerate(pts):
        (p.moveTo if k == 0 else p.lineTo)(x, y)
    return p


def _cosmetic(pen):
    pen.setCosmetic(True)
    return pen


def _rings(g):
    """A shapely Polygon's rings as [outer, hole, ...] point arrays."""
    return [np.asarray(g.exterior.coords)] + [
        np.asarray(r.coords) for r in g.interiors
    ]


def shape_bounds(layout):
    """(x0, y0, x1, y1) of a board dict's outline, or None."""
    o = (layout or {}).get('outline')
    if not o:
        return None
    a = np.asarray(rings(o)[0][0], float)
    return (*a.min(0), *a.max(0))


def _spin(value, lo, hi, suffix, tip='', decimals=3):
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=decimals,
                       singleStep=10 ** -min(decimals, 2), suffix=suffix,
                       toolTip=tip)
    s.setValue(value)
    return s


def _narrow(w, least=56):
    """A spin box that may be narrower than its range's text asks."""
    w.setMinimumWidth(least)
    return w


def _vline():
    f = QFrame(frameShape=QFrame.VLine, frameShadow=QFrame.Sunken)
    return f


def _hline():
    return QFrame(frameShape=QFrame.HLine, frameShadow=QFrame.Sunken)
