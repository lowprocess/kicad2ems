"""
The Draw page: arbitrary shapes drawn over the board, in plan and in 3D, for a
run to add to it (kicad2ems.core.shapes: the drawing as data, and what it does
to the board).

    Plan    the board's copper on the layer drawn on (the other layers faint),
            and the shapes; tools: Select (click a shape, drag it, drag its
            handles; Delete), Rectangle and Circle (two clicks), Polygon and
            Trace (a click per point; double-click, right-click or Enter ends,
            Esc drops), Port (a click). Points snap to the grid.
    3D      the same shapes extruded: copper and cuts as slabs on their layer,
            metal, dielectric and air as solids between their two heights,
            over the board's substrate and copper. Click a shape to select it
            (a click on nothing: none), drag it to move it on its level,
            Shift+drag a solid's top or bottom face to stretch it or its side
            to lift it; a drag on nothing orbits, Ctrl+drag pans (picking: a
            ray from the mouse through the camera, against the triangles)
    Shapes  the list in the order they are applied (a cut takes out what is
            drawn before it), the selected one's properties to edit

Use in runs saves the drawing and names it in the settings (`shapes`); Region
from shape and Ports to settings write those settings (KiCad coordinates: y
negated). Ctrl+Z / Ctrl+Y undo and redo here while the page has the focus (the
settings keep theirs elsewhere).

    tab = DrawTab(win)          # win: the main window, or None (on its own)
    tab.set_board(layout)       # core.layout.board_layout's dict, or None
    tab.add(Shape(...)); tab.drawing; tab.save(path); tab.load(path)
"""
import os
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QEvent, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QPen, QBrush, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
                               QButtonGroup, QLabel, QComboBox, QDoubleSpinBox,
                               QSplitter, QListWidget, QFormLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QFileDialog,
                               QGridLayout, QFrame, QMessageBox, QCheckBox)
from kicad2ems.core import shapes as sh
from kicad2ems.core.shapes import Shape, Drawing
from . import theme
from .planview import _path, _item
from .volume3d import rings

__all__ = ['DrawTab']

# tool: (button, shortcut letter, what a click does)
TOOLS = {
    'select': ('Select', 'S', 'click a shape to select it, drag it to move '
               'it, drag a handle to reshape it; Delete removes it'),
    'rect': ('Rectangle', 'R', 'click two opposite corners'),
    'circle': ('Circle', 'C', 'click the centre, then a point on the rim'),
    'polygon': ('Polygon', 'P', 'click each corner; double-click, right-click '
                'or Enter closes it; Esc drops it'),
    'path': ('Trace', 'T', 'click each point of the centre line; double-click,'
             ' right-click or Enter ends it; Esc drops it'),
    'port': ('Port', 'O', 'click where the port goes (on a trace: it points '
             'along it)'),
}
# RGB of each material, in plan and 3D
COLOURS = dict(copper=(214, 140, 64), cut=(225, 60, 60), metal=(140, 150, 165),
               dielectric=(60, 170, 90), air=(110, 165, 235))
PORT_COLOUR = (200, 30, 160)
SELECTED = QColor(0, 120, 215)


def _qc(rgb, a=255):
    return QColor(*rgb, a)


class _Canvas(pg.ViewBox):
    """The plan's view box: the page's tools take the left button's clicks and
    drags; what they leave (a drag on nothing) pans as usual."""

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


class DrawTab(QWidget):
    changed = Signal()  # the drawing changed

    def __init__(self, win=None):
        super().__init__()
        self.win = win
        self.drawing, self.sel, self.tool = Drawing(), None, 'select'
        self.layout_, self.stack = None, sh.stack_of(None)
        self.file = None  # where the drawing was last saved / loaded
        self.pending, self._drag_state = [], None
        self._undo, self._redo = [], []
        self._items, self._board_items = [], []
        self._gl_items, self._board3d = [], None
        # the 3D view's shapes: {index: (vertices, faces)} to pick, {index: its
        # mesh item}; a press there, a drag of a shape there
        self._meshes3d, self._shape_gl = {}, {}
        self._press3d_at, self._drag3d = None, None
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
        self.handles = pg.ScatterPlotItem(
            size=9, symbol='s', pen=pg.mkPen(SELECTED, width=1.5),
            brush=pg.mkBrush(255, 255, 255))
        self.handles.setZValue(60)
        self.vb.addItem(self.handles)
        self.view3d = self._make_3d()
        views = QSplitter(Qt.Vertical)
        views.addWidget(self.plot)
        views.addWidget(self.view3d)
        views.setSizes([600, 340])
        self.split = QSplitter(objectName='draw')
        self.split.addWidget(views)
        self.split.addWidget(self._side())
        self.split.setStretchFactor(0, 1)
        self.split.setSizes([900, 300])
        lay.addWidget(self.split, 1)
        self.status = QLabel(wordWrap=True)
        lay.addWidget(self.status)
        self._3d_timer = QTimer(self, singleShot=True, interval=120,
                                timeout=self._redraw_3d)
        self.set_tool('select')
        self._refresh()
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
        self.layer.currentTextChanged.connect(lambda _: self._refresh())
        self.material = QComboBox(toolTip=(
            'what a new shape is made of: copper (joins the layer\'s copper), '
            'cut (taken out of it), or a solid between two heights: metal, '
            'dielectric, air'))
        self.material.addItems(sh.MATERIALS)
        self.material.currentTextChanged.connect(self._material_state)
        self.width = _spin(0.5, 0.001, 100, ' mm', 'a new trace\'s width')
        self.z0 = _spin(0.0, -100, 100, ' mm', 'a new solid\'s bottom')
        self.z1 = _spin(1.0, -100, 100, ' mm', 'a new solid\'s top')
        self.snap = _spin(0.1, 0, 10, ' mm', 'points snap to this grid '
                          '(0: no snapping)', decimals=3)
        for text, w in (('Layer', self.layer), ('Material', self.material),
                        ('Width', self.width), ('z', self.z0), ('to', self.z1),
                        ('Snap', self.snap)):
            row.addWidget(QLabel(text))
            row.addWidget(w)
        row.addStretch(1)
        return row

    def _side(self):
        side = QWidget()
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel('Shapes (applied in this order)'))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._listed)
        v.addWidget(self.list, 1)
        grid = QGridLayout()
        for i, (text, slot, tip) in enumerate((
                ('Up', lambda: self._reorder(-1), 'applied earlier'),
                ('Down', lambda: self._reorder(1), 'applied later'),
                ('Duplicate', self.duplicate, 'a copy, 1 grid step along'),
                ('Delete', self.delete, 'remove it (Delete)'),
                ('Rotate 90°', self.rotate, 'about its centre'),
                ('To polygon', self.to_polygon,
                 'its outline as a polygon, to reshape corner by corner'))):
            grid.addWidget(QPushButton(text, clicked=slot, toolTip=tip), i // 2,
                           i % 2)
        v.addLayout(grid)
        # the selected shape's properties
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
        self.see_through = QCheckBox(
            'see-through solids in 3D', checked=True,
            toggled=lambda _: self._3d_timer.start(),
            toolTip='metal solids drawn translucent, so what they cover shows')
        v.addWidget(self.board_on)
        v.addWidget(self.see_through)
        return side

    def _make_3d(self):
        """The 3D view (volumetab's, with its OpenGL checks), or a note saying
        why there is none."""
        from .volumetab import gl_problem, _view_class
        try:
            import pyqtgraph.opengl as gl
        except Exception as e:  # noqa: BLE001 (no PyOpenGL)
            self.gl, why = None, f"needs PyOpenGL ({type(e).__name__})"
        else:
            self.gl, why = gl, gl_problem()
        if self.gl is None or why:
            self.gl = None
            return QLabel(f"The 3D preview is off: {why}", wordWrap=True,
                          alignment=Qt.AlignCenter, styleSheet='color: gray')
        view = _pick_view(_view_class(self.gl), self)()
        view.setBackgroundColor(theme.colors()['bg'])
        view.setCameraPosition(distance=60, elevation=30, azimuth=-60)
        self.zscale = 1.0
        return view

    # ---- the board under the drawing ----
    def set_board(self, layout):
        """The board drawn on (core.layout.board_layout's dict, a run's
        board.json's; None: none, a default two-layer stack)."""
        self.layout_ = layout
        self.stack = sh.stack_of(layout)
        self._board3d = None
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
        self.z0.setValue(top)
        self.z1.setValue(top + 1.0)
        self._refresh(board=True)
        b = (shape_bounds(layout) if layout else None) or self.drawing.bounds()
        if b:
            pad = 0.05 * max(b[2] - b[0], b[3] - b[1], 1)
            self.vb.setRange(xRange=(b[0] - pad, b[2] + pad),
                             yRange=(b[1] - pad, b[3] + pad))

    # ---- tools ----
    def set_tool(self, key):
        self.tool, self.pending = key, []
        self.tool_btns[key].setChecked(True)
        self.status.setText(f"{TOOLS[key][0]}: {TOOLS[key][2]}")
        self._rubber()

    def _material_state(self, m=None):
        solid = (m or self.material.currentText()) in sh.SOLID
        self.z0.setEnabled(solid)
        self.z1.setEnabled(solid)

    def _snap(self, p):
        x, y = p.x(), p.y()
        g = self.snap.value()
        if g > 0:
            x, y = round(x / g) * g, round(y / g) * g
        return [round(x, 6), round(y, 6)]

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

    def _click(self, p, ev):
        """A click of the plan (model mm). True: taken."""
        q = self._snap(p)
        right = ev.button() == Qt.RightButton
        if self.tool == 'select':
            if right:
                return False  # the view's menu
            self.select(self.hit(p.x(), p.y()))
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
        p = self.vb.mapSceneToView(scene_pos)
        x, y = self._snap(p)
        self.status.setText(
            f"x {x:.3f}  y {y:.3f} mm (KiCad {x:.3f}, {-y:.3f})  —  "
            f"{TOOLS[self.tool][0]}: {TOOLS[self.tool][2]}")
        if self.pending:
            self._rubber([x, y])

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

    def _drag(self, ev):
        """A left-button drag: the selected shape's handle, or a shape moved.
        False: a drag on nothing (the view pans)."""
        if self.tool != 'select':
            return False
        p = self.vb.mapSceneToView(ev.scenePos())
        if ev.isStart():
            p0 = self.vb.mapSceneToView(ev.buttonDownScenePos())
            tol = self._tol()
            if self.sel is not None:
                hs = np.asarray(self.drawing.shapes[self.sel].handles())
                d = np.hypot(hs[:, 0] - p0.x(), hs[:, 1] - p0.y())
                if d.min() <= tol:
                    self._drag_state = ('handle', int(d.argmin()),
                                        self.drawing.to_dict())
                    return True
            i = self.hit(p0.x(), p0.y())
            if i is None:
                self._drag_state = None
                return False
            self.select(i)
            self._drag_state = ('move', self._snap(p0),
                                self.drawing.shapes[i], self.drawing.to_dict())
            return True
        st = self._drag_state
        if st is None:
            return False
        q = self._snap(p)
        if st[0] == 'handle':
            self.drawing.shapes[self.sel].drag_handle(st[1], *q)
        else:
            self.drawing.shapes[self.sel] = st[2].moved(q[0] - st[1][0],
                                                        q[1] - st[1][1])
        if ev.isFinish():
            self._drag_state = None
            self._commit(st[-1])
        else:
            self._refresh(listing=False, quick=True)
        return True

    def _tol(self):
        """Six pixels of the plan, in mm."""
        sx, sy = self.vb.viewPixelSize()
        return 6 * max(sx, sy)

    def hit(self, x, y):
        """The shape at (x, y) model mm (the last drawn on top; those of the
        layer shown first), or None."""
        from shapely.geometry import Point
        pt, tol = Point(x, y), self._tol()
        order = sorted(range(len(self.drawing.shapes)),
                       key=lambda i: (self._on_layer(self.drawing.shapes[i]), i))
        for i in reversed(order):
            s = self.drawing.shapes[i]
            g = s.outline()
            d = (pt.distance(Point(s.points[0])) if g is None else
                 g.distance(pt))
            if d <= tol:
                return i
        return None

    def _on_layer(self, s):
        return s.solid or s.layer == self.layer.currentText()

    # ---- editing (each step undoable) ----
    def _commit(self, before):
        """The drawing changed from `before` (its dict): a step to undo."""
        self._undo.append(before)
        self._redo.clear()
        self._refresh()
        self.changed.emit()

    def add(self, shape):
        if shape is None:
            return
        before = self.drawing.to_dict()
        self.drawing.shapes.append(shape)
        self.sel = len(self.drawing.shapes) - 1
        self._commit(before)

    def delete(self):
        if self.sel is None:
            return
        before = self.drawing.to_dict()
        del self.drawing.shapes[self.sel]
        self.sel = None
        self._commit(before)

    def duplicate(self):
        if self.sel is None:
            return
        g = self.snap.value() or 0.5
        self.add(self.drawing.shapes[self.sel].moved(g, -g))

    def rotate(self):
        """The selected shape turned 90 degrees anticlockwise about its
        centre."""
        if self.sel is None:
            return
        s = self.drawing.shapes[self.sel]
        x0, y0, x1, y1 = s.bounds()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        before = self.drawing.to_dict()
        s.points = [[cx - (y - cy), cy + (x - cx)] for x, y in s.points]
        if s.kind == 'port' and s.rot is not None:
            s.rot = (s.rot + 90) % 360
        self._commit(before)

    def to_polygon(self):
        """The selected rectangle, circle or trace as a polygon of its
        outline's corners."""
        if self.sel is None:
            return
        s = self.drawing.shapes[self.sel]
        g = s.outline(segments=32)
        if s.kind == 'polygon' or g is None:
            return
        before = self.drawing.to_dict()
        s.points = [[round(x, 6), round(y, 6)]
                    for x, y in g.exterior.coords[:-1]]
        s.kind = 'polygon'
        self._commit(before)

    def _reorder(self, step):
        i = self.sel
        if i is None or not 0 <= i + step < len(self.drawing.shapes):
            return
        before = self.drawing.to_dict()
        ss = self.drawing.shapes
        ss[i], ss[i + step] = ss[i + step], ss[i]
        self.sel = i + step
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
        if self.sel is not None and self.sel >= len(self.drawing.shapes):
            self.sel = None
        self._refresh()
        self.changed.emit()

    def new(self):
        if self.drawing.shapes:
            before = self.drawing.to_dict()
            self.drawing, self.sel = Drawing(), None
            self._commit(before)
        self.file = None

    # ---- the selected shape ----
    def select(self, i):
        self.sel = i
        with QSignalBlocker(self.list):
            self.list.setCurrentRow(-1 if i is None else i)
        self._fill_form()
        self._refresh(listing=False, board=False)

    def _listed(self, row):
        self.select(row if 0 <= row < len(self.drawing.shapes) else None)

    def _fill_form(self):
        s = self.drawing.shapes[self.sel] if self.sel is not None else None
        for w in self.rows.values():
            w.setEnabled(s is not None)
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
        """The form's values into the selected shape."""
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
                        name=self.p_name.text().strip())
        except ValueError as e:
            self.status.setText(f"not changed: {e}")
            return
        if new.to_dict() == s.to_dict():
            return
        self.drawing.shapes[self.sel] = new
        self._commit(before)

    # ---- keys: the page's own while it has the focus ----
    _KEYS = (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Delete,
             Qt.Key_Backspace)

    def event(self, e):
        # Ctrl+Z / Ctrl+Y here undo the drawing, not the window's settings
        if e.type() == QEvent.ShortcutOverride and (
                e.matches(QKeySequence.Undo) or e.matches(QKeySequence.Redo)
                or e.key() in self._KEYS):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        k = e.key()
        if e.matches(QKeySequence.Undo):
            self.undo()
        elif e.matches(QKeySequence.Redo):
            self.redo()
        elif k == Qt.Key_Escape:
            if self.pending:
                self.pending = []
                self._rubber()
            else:
                self.set_tool('select')
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete()
        elif e.modifiers() == Qt.NoModifier and e.text().upper() in {
                v[1]: k for k, v in TOOLS.items()}:
            self.set_tool({v[1]: k for k, v in TOOLS.items()}[e.text().upper()])
        else:
            super().keyPressEvent(e)

    # ---- drawing it ----
    def _refresh(self, listing=True, board=False, quick=False):
        """The plan (and the list, and soon the 3D view) as the drawing is."""
        if board:
            self._draw_board()
        if listing:
            with QSignalBlocker(self.list):
                self.list.clear()
                self.list.addItems([s.label() for s in self.drawing.shapes])
                self.list.setCurrentRow(-1 if self.sel is None else self.sel)
            self._fill_form()
        for it in self._items:
            self.vb.removeItem(it)
        self._items = []
        for i, s in enumerate(self.drawing.shapes):
            self._items += self._shape_items(s, i == self.sel)
        for it in self._items:
            self.vb.addItem(it)
        hs = (self.drawing.shapes[self.sel].handles()
              if self.sel is not None else [])
        self.handles.setData(pos=np.asarray(hs, float).reshape(-1, 2))
        if not quick:
            self._3d_timer.start()

    def _shape_items(self, s, selected):
        rgb = PORT_COLOUR if s.kind == 'port' else COLOURS[s.material]
        here = self._on_layer(s)
        line = SELECTED if selected else _qc(rgb)
        pen = QPen(line, 2.5 if selected else 1.2,
                   Qt.DashLine if s.solid else Qt.SolidLine)
        if not here:
            pen.setColor(_qc(rgb, 90))
        # a solid (a lid over the whole board) leaves the copper under it seen
        fill = _qc(rgb, 60 if s.material == 'cut' else 35 if s.solid else
                   150 if here else 35)
        brush = QBrush(fill, Qt.BDiagPattern if s.material == 'cut' else
                       Qt.SolidPattern)
        g = s.outline()
        if g is not None:
            it = _item(_path([_rings(g)]), pen, brush, 20 if here else 10)
            return [it]
        x, y = s.points[0]
        r = 4 * self._tol() / 6
        diamond = [[x, y + r], [x + r, y], [x, y - r], [x - r, y]]
        it = _item(_path([[diamond]]), pen, QBrush(_qc(rgb)), 30)
        txt = pg.TextItem(s.name or 'port', color=_qc(rgb), anchor=(0, 1))
        txt.setPos(x + r, y + r)
        return [it, txt]

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

    def _redraw_3d(self):
        if self.gl is None:
            return
        gl, view = self.gl, self.view3d
        for it in self._gl_items:
            view.removeItem(it)
        self._gl_items = []
        if self._board3d is None:
            self._board3d = self._board_meshes()
        for v, f, rgba, opaque in (
                self._board3d if self.board_on.isChecked() else []):
            self._mesh(v, f, rgba, opaque)
        self._meshes3d, self._shape_gl = {}, {}
        for i, s in enumerate(self.drawing.shapes):
            m = self._meshes3d[i] = _mesh_of(s, self.stack)
            if m is not None:
                self._shape_gl[i] = self._mesh(*m, *self._look(i, s))
        if not self._gl_items:
            grid = gl.GLGridItem()
            grid.setSize(40, 40)
            grid.setSpacing(1, 1)
            view.addItem(grid)
            self._gl_items.append(grid)

    def _look(self, i, s):
        """(rgba, opaque) of shape i in 3D."""
        rgb = PORT_COLOUR if s.kind == 'port' else COLOURS[s.material]
        if i == self.sel:
            rgb = (SELECTED.red(), SELECTED.green(), SELECTED.blue())
        a = (0.45 if s.material in ('dielectric', 'air') else 0.55
             if s.material == 'metal' and self.see_through.isChecked() else 1.0)
        return (*(c / 255 for c in rgb), a), a == 1.0

    def _mesh(self, v, f, rgba, opaque):
        from .volumetab import copper_shader
        md = self.gl.MeshData(vertexes=v.astype(np.float32),
                              faces=f.astype(np.uint32))
        it = self.gl.GLMeshItem(
            meshdata=md, color=rgba, smooth=False, shader=copper_shader(),
            glOptions='opaque' if opaque else 'translucent')
        it.setDepthValue(0 if opaque else 5)
        self.view3d.addItem(it)
        self._gl_items.append(it)
        return it

    def _board_meshes(self):
        """The board's substrate and copper as (vertices, faces, rgba,
        opaque), made once per board."""
        d = self.layout_
        if not d:
            return []
        from shapely.geometry import shape as geo
        from . import volume3d as v3
        out = []
        layers = self.stack['layers']
        if d.get('outline') and layers:
            z0 = min(l['z0'] for l in layers)
            z1 = max(l['z1'] for l in layers)
            try:
                v, f = sh.prism(geo(d['outline']), z0, z1)
                out.append((v, f, (0.30, 0.62, 0.36, 0.25), False))
            except Exception:  # noqa: BLE001
                pass
        try:
            geom = v3.geometry_from_board(d)
            for _, _, v, f in v3.copper_solids(geom, 0.05):
                out.append((v, f, (0.84, 0.55, 0.25, 1.0), True))
        except Exception:  # noqa: BLE001 (a board without copper)
            pass
        return out

    # ---- picking and editing in 3D ----
    def ray3d(self, x, y):
        """(origin, unit direction) of the ray under pixel (x, y) of the 3D
        view, model mm."""
        inv = np.linalg.inv(self._mvp())
        nx, ny = 2 * x / self.view3d.width() - 1, 1 - 2 * y / self.view3d.height()
        a, b = (inv @ np.array([nx, ny, z, 1.0]) for z in (-1.0, 1.0))
        a, b = a[:3] / a[3], b[:3] / b[3]
        return a, (b - a) / np.linalg.norm(b - a)

    def project3d(self, p):
        """The 3D view's pixel (x, y) of model point p."""
        c = self._mvp() @ np.array([*p, 1.0])
        c = c[:3] / c[3]
        return ((c[0] + 1) / 2 * self.view3d.width(),
                (1 - c[1]) / 2 * self.view3d.height())

    def _mvp(self):
        v = self.view3d
        vp = (0, 0, max(v.width(), 1), max(v.height(), 1))
        m = v.projectionMatrix(vp, vp) * v.viewMatrix()
        return np.array(m.data(), float).reshape(4, 4).T  # (column-major)

    def pick3d(self, x, y):
        """(shape index, model point) under pixel (x, y) of the 3D view, or
        (None, None). The board itself is not picked: only drawn shapes."""
        o, d = self.ray3d(x, y)
        i, t = sh.pick(self._meshes3d, o, d)
        return (None, None) if i is None else (i, o + t * d)

    def _press3d(self, ev):
        """A left press in 3D: on a shape, it is selected and a drag of it
        starts (True: the view does not orbit); on nothing, the view orbits
        and a click there (no move) leaves nothing selected."""
        pos = ev.position()
        self._press3d_at, self._drag3d = (pos.x(), pos.y()), None
        if (ev.button() != Qt.LeftButton
                or ev.modifiers() & Qt.ControlModifier):  # Ctrl: pan
            return False
        if self._3d_timer.isActive():  # what is drawn, before it is picked
            self._3d_timer.stop()
            self._redraw_3d()
        i, at = self.pick3d(pos.x(), pos.y())
        if i is None:
            return False
        if i != self.sel:
            self.select(i)
        s = self.drawing.shapes[i]
        face = None
        if ev.modifiers() & Qt.ShiftModifier and s.solid:
            lo, hi = sorted((s.z0, s.z1))
            tol = 0.02 * max(hi - lo, 1e-3)
            face = ('top' if abs(at[2] - hi) < tol else
                    'bottom' if abs(at[2] - lo) < tol else 'side')
        o, d = self.ray3d(pos.x(), pos.y())
        # a lift moves on the upright plane through the point facing the
        # camera; a move on the level plane through it
        n = np.array([d[0], d[1], 0.0])
        n = n / np.linalg.norm(n) if np.linalg.norm(n) > 1e-6 else None
        self._drag3d = dict(i=i, at=at, shape=s, face=face, normal=n,
                            before=self.drawing.to_dict(), moved=False)
        self.view3d.setCursor(Qt.ClosedHandCursor)
        self.status.setText(
            f"3D: {s.label()} - drag to move it" + (
                '; Shift+drag a solid\'s top or bottom face to stretch it, '
                'its side to lift it' if s.solid else ''))
        return True

    def _move3d(self, ev):
        """A drag of the shape pressed on (True), else a hover (the cursor
        says what a press would do) or the view's own orbit (False)."""
        pos = ev.position()
        st = self._drag3d
        if st is None:
            if ev.buttons() == Qt.NoButton:
                i, _ = self.pick3d(pos.x(), pos.y())
                self.view3d.setCursor(Qt.OpenHandCursor if i is not None else
                                      Qt.ArrowCursor)
            return False
        o, d = self.ray3d(pos.x(), pos.y())
        at, g = st['at'], self.snap.value()
        rnd = (lambda v: round(v / g) * g) if g > 0 else (lambda v: v)
        if st['face'] is not None:  # lifted or stretched
            n = st['normal']
            if n is None:  # looking straight down: the mouse's rows
                dz = -(pos.y() - self._press3d_at[1]) * self.view3d.pixelSize(
                    pg.Vector(*at))
            else:
                den = d @ n
                if abs(den) < 1e-9:
                    return True
                dz = (o + ((at - o) @ n / den) * d)[2] - at[2]
            new = st['shape'].lifted(rnd(dz), None if st['face'] == 'side' else
                                     st['face'])
        else:  # moved on its level
            if abs(d[2]) < 1e-9:
                return True
            p = o + ((at[2] - o[2]) / d[2]) * d
            new = st['shape'].moved(rnd(p[0] - at[0]), rnd(p[1] - at[1]))
        i = st['i']
        if new.to_dict() != self.drawing.shapes[i].to_dict():
            self.drawing.shapes[i] = new
            st['moved'] = True
            self._refresh(listing=False, quick=True)
            self._update_shape_3d(i)
        return True

    def _release3d(self, ev):
        st, at = self._drag3d, self._press3d_at
        self._drag3d = self._press3d_at = None
        if st is not None:
            self.view3d.setCursor(Qt.OpenHandCursor)
            if st['moved']:
                self._commit(st['before'])
            return
        # a click on nothing (not an orbit): nothing selected
        pos = ev.position()
        if (at is not None and ev.button() == Qt.LeftButton
                and abs(pos.x() - at[0]) + abs(pos.y() - at[1]) < 4
                and self.sel is not None):
            self.select(None)

    def _update_shape_3d(self, i):
        """Shape i's 3D mesh, again (while it is dragged: the rest stays)."""
        s = self.drawing.shapes[i]
        m = self._meshes3d[i] = _mesh_of(s, self.stack)
        it = self._shape_gl.get(i)
        if it is None or m is None:
            return
        it.setMeshData(meshdata=self.gl.MeshData(
            vertexes=m[0].astype(np.float32), faces=m[1].astype(np.uint32)))

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
        self.drawing, self.sel, self.file = d, None, path
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


def _pick_view(base, tab):
    """The 3D view class whose left button the page's 3D editing has first
    (DrawTab._press3d / _move3d / _release3d): what they leave orbits and pans
    as before. Its keys are the page's (Delete, Esc, a tool's letter, undo)."""
    class PickView(base):
        def mousePressEvent(self, ev):
            super().mousePressEvent(ev)  # (where an orbit starts from)
            tab._press3d(ev)

        def mouseMoveEvent(self, ev):
            if not tab._move3d(ev):
                super().mouseMoveEvent(ev)
            else:
                self.mousePos = ev.position()

        def mouseReleaseEvent(self, ev):
            tab._release3d(ev)
            super().mouseReleaseEvent(ev)

        def keyPressEvent(self, ev):
            if ev.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up,
                            Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
                super().keyPressEvent(ev)  # the camera's keys
            else:
                tab.keyPressEvent(ev)
    return PickView


def _mesh_of(s, stack):
    try:
        return sh.shape_mesh(s, stack)
    except Exception:  # noqa: BLE001 (a shape shapely cannot fill)
        return None


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


def _vline():
    f = QFrame(frameShape=QFrame.VLine, frameShadow=QFrame.Sunken)
    return f


def _hline():
    return QFrame(frameShape=QFrame.HLine, frameShadow=QFrame.Sunken)
