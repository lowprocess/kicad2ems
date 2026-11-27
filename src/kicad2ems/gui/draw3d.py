"""
The Draw page's 3D view (gui.drawtab's DrawTab takes it as a mixin): the
board and the shapes extruded (a magnet in two halves, the pole on top and
the one below: N red, S blue), picked, boxed and dragged with the mouse, the
camera's presets (orthographic from the top, front and side: pyqtgraph's
projection overridden), a clip plane, what is shown, the selection's outline
and its gizmo (arrows with cone heads), and the working level's grid. The
numbers (the camera's matrices, rays, the axis drag, the box, the clip, the
outlines, the grid) are kicad2ems.core.view3d's, a magnet's halves
gui.drawmagnets'.

    tab.camera('top' | 'front' | 'side' | 'iso')   # fitted to the drawing
                                    # (orthographic but for Iso)
    tab.set_ortho(on)               # the camera orthographic or not
    tab.frame_3d()                  # the selection (else all) filling it
    tab.box_select3d(x0, y0, x1, y1)  # a pixel box's shapes (Shift + drag)
    tab.set_clip(axis, value, keep) # axis 0 / 1 / 2 or None (off)
    tab.set_shown(key, on)          # 'substrate', 'cu:<layer>', a material,
                                    # 'port'
    tab.ray3d(px, py); tab.project3d(p); tab.pick3d(px, py)
"""
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
                               QLabel, QComboBox, QCheckBox, QMenu)
from kicad2ems.core import shapes as sh
from kicad2ems.core import view3d as v3
from . import theme
from . import drawmagnets as dm

__all__ = ['Draw3D', 'SHOWN_MATERIALS']

# what the Show menu turns on and off besides the board's layers
SHOWN_MATERIALS = sh.MATERIALS + ('port',)
AXIS_RGBA = {0: (0.95, 0.25, 0.25, 1.0), 1: (0.25, 0.85, 0.30, 1.0),
             2: (0.30, 0.55, 1.00, 1.0)}
GIZMO_PX = 9  # how near an arrow a press takes it, pixels
GIZMO_HEAD = (0.24, 0.08)  # an arrowhead's length and radius, of the arrow's


class Draw3D:
    """The 3D half of DrawTab (its methods read the tab's drawing, selection,
    stack, snap and status)."""

    # ---- the widgets ----
    def _make_3d(self):
        """The 3D view with its toolbar (self.view3d: the view), or a note
        saying why there is none (self.gl None)."""
        from .volumetab import gl_problem, _view_class
        self.shown3d = {k: True for k in ('substrate',) + SHOWN_MATERIALS}
        self.clip = None  # (axis, value, keep)
        # what is drawn: the overlays (grid, outline, gizmo), the board's
        # items (made again when the board, the clip or what is shown of it
        # changes) and each shape's (index: dict(key, mesh, item, look), kept
        # while its mesh is the same)
        self._gl_items, self._board3d, self._board3d_at = [], None, None
        self._board_gl, self._board_gl_key, self._board_ver = [], None, 0
        self._entries3d, self._meshes3d, self._shape_gl = {}, {}, {}
        self._press3d_at, self._drag3d = None, None
        self._outline_gl = self._outline_fixed_gl = None
        self._gizmo_gl = self._gizmo_cone_gl = self._grid_gl = None
        self._gizmo_axes = None  # the axes its items were made for
        self._gizmo = None  # (base, length, axes) as drawn
        self.ortho3d = False  # an orthographic camera (Top, Front, Side)
        self._band3d = None  # a box selection's rubber band
        try:
            import pyqtgraph.opengl as gl
        except Exception as e:  # noqa: BLE001 (no PyOpenGL)
            self.gl, why = None, f"needs PyOpenGL ({type(e).__name__})"
        else:
            self.gl, why = gl, gl_problem()
        if self.gl is None or why:
            self.gl = None
            self.view3d = QLabel(f"The 3D preview is off: {why}",
                                 wordWrap=True, alignment=Qt.AlignCenter,
                                 styleSheet='color: gray')
            return self.view3d
        view = _pick_view(_view_class(self.gl), self)()
        view.setBackgroundColor(theme.colors()['bg'])
        view.setCameraPosition(distance=60, elevation=30, azimuth=-60)
        self.view3d = view
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(self._toolbar_3d())
        v.addWidget(view, 1)
        return box

    def _toolbar_3d(self):
        """The 3D view's tools: a tool bar, what does not fit in its » menu
        (a row of them had made the page 46 px wider at its least)."""
        from PySide6.QtWidgets import QToolBar
        row = QToolBar(movable=False, floatable=False)
        row.setToolButtonStyle(Qt.ToolButtonTextOnly)
        row.setStyleSheet('QToolBar { border: 0px; spacing: 2px; }')
        for name, key, tip in (('Top', '7', 'from above, orthographic'),
                               ('Front', '1', 'from the front (along +y), '
                                'orthographic'),
                               ('Side', '3', 'from the right (along -x), '
                                'orthographic'),
                               ('Iso', '5', 'from a corner, in perspective')):
            row.addWidget(QToolButton(
                text=name, autoRaise=True, toolTip=f"{tip} ({key})",
                clicked=lambda _=False, n=name.lower(): self.camera(n)))
        self.ortho_btn = QToolButton(
            text='Ortho', autoRaise=True, checkable=True, toolTip=(
                'an orthographic camera: no perspective, a size the same near '
                'and far (Top, Front and Side set it, Iso takes it off)'))
        self.ortho_btn.toggled.connect(self.set_ortho)
        row.addWidget(self.ortho_btn)
        row.addWidget(QToolButton(
            text='Fit', autoRaise=True, clicked=self.frame_3d,
            toolTip='the selection (else everything) filling the view (F)'))
        self.show_btn = QToolButton(text='Show', autoRaise=True,
                                    popupMode=QToolButton.InstantPopup,
                                    toolTip='layers and materials drawn in '
                                    '3D, the grid, see-through solids')
        self.show_menu = QMenu(self.show_btn)
        self.show_btn.setMenu(self.show_menu)
        row.addWidget(self.show_btn)
        row.addSeparator()
        self.clip_axis = QComboBox(toolTip=(
            'a plane through the board: what is past it is not drawn or '
            'picked (a section)'))
        self.clip_axis.addItems(['off', 'x', 'y', 'z'])
        self.clip_axis.activated.connect(lambda _: self._clip_changed(True))
        from .drawtab import _spin, _narrow
        self.clip_at = _narrow(_spin(0.0, -1000, 1000, ' mm',
                                     'where the plane is'), 70)
        self.clip_at.valueChanged.connect(lambda _: self._clip_changed())
        self.clip_flip = QCheckBox('flip', toolTip='keep what is past it '
                                   'instead')
        self.clip_flip.toggled.connect(lambda _: self._clip_changed())
        # (one item: the » menu keeps them together)
        clip = QWidget()
        h = QHBoxLayout(clip)
        h.setContentsMargins(2, 0, 0, 0)
        h.setSpacing(3)
        for w in (QLabel('Clip'), self.clip_axis, self.clip_at,
                  self.clip_flip):
            h.addWidget(w)
        row.addWidget(clip)
        self.grid_on = QAction('the grid at the working level', self,
                               checkable=True, checked=True, toolTip=(
                                   'the snap grid at the working level: the '
                                   'layer drawn on, or a new solid\'s '
                                   'bottom'))
        self.grid_on.toggled.connect(lambda _: self._update_grid_3d())
        self.see_through = QAction(
            'see-through metal', self, checkable=True, checked=True,
            toolTip='metal solids and magnets drawn translucent, so what they '
            'cover shows')
        self.see_through.toggled.connect(lambda _: self._3d_timer.start())
        self._fill_show_menu()
        return row

    def _fill_show_menu(self):
        if self.gl is None:
            return
        self.show_menu.clear()
        self.show_acts = {}
        layers = sorted(self.stack['cu_z'], key=lambda l: -self.stack['cu_z'][l])
        entries = [('substrate', "the board's substrate")] + [
            (f"cu:{l}", f"the board's {l}") for l in layers] + [None] + [
            (m, f"drawn {m}" if m != 'port' else 'drawn ports')
            for m in SHOWN_MATERIALS]
        for e in entries:
            if e is None:
                self.show_menu.addSeparator()
                continue
            key, text = e
            a = self.show_menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(self.shown3d.setdefault(key, True))
            a.toggled.connect(lambda on, k=key: self.set_shown(k, on))
            self.show_acts[key] = a
        self.show_menu.addSeparator()
        self.show_menu.addAction(self.grid_on)
        self.show_menu.addAction(self.see_through)

    # ---- what is shown ----
    def set_shown(self, key, on):
        """A layer ('substrate', 'cu:<layer>') or a material (a drawn one, or
        'port') drawn in 3D or not (and picked or not)."""
        self.shown3d[key] = bool(on)
        a = getattr(self, 'show_acts', {}).get(key)
        if a is not None and a.isChecked() != bool(on):
            a.setChecked(bool(on))
        self._3d_timer.start()

    def _shown3d(self, s):
        return (not s.hidden and self.shown3d.get(
            'port' if s.kind == 'port' else s.material, True))

    def set_clip(self, axis=None, value=0.0, keep='below'):
        """The clip plane: model axis 0, 1 or 2 at `value` mm, the side kept
        'below' or 'above'; axis None: none."""
        from PySide6.QtCore import QSignalBlocker
        if self.gl is not None:
            with QSignalBlocker(self.clip_axis), QSignalBlocker(
                    self.clip_at), QSignalBlocker(self.clip_flip):
                self.clip_axis.setCurrentIndex(0 if axis is None else axis + 1)
                self.clip_at.setValue(value)
                self.clip_flip.setChecked(keep == 'above')
        self.clip = None if axis is None else (int(axis), float(value), keep)
        self._3d_timer.start()

    def _clip_changed(self, axis_changed=False):
        k = self.clip_axis.currentIndex() - 1
        if k < 0:
            return self.set_clip(None)
        if axis_changed:  # through the middle of what is drawn
            b = self._bounds3d()
            if b is not None:
                return self.set_clip(k, round((b[k] + b[k + 3]) / 2, 3),
                                     'above' if self.clip_flip.isChecked()
                                     else 'below')
        self.set_clip(k, self.clip_at.value(),
                      'above' if self.clip_flip.isChecked() else 'below')

    # ---- the camera ----
    def _cam(self):
        o = self.view3d.opts
        c = o['center']
        return dict(center=(c.x(), c.y(), c.z()), distance=o['distance'],
                    elevation=o['elevation'], azimuth=o['azimuth'],
                    fov=o['fov'], ortho=self.ortho3d)

    def set_ortho(self, on=True):
        """The camera orthographic (on) or in perspective; the view's middle
        stays as big (an orthographic view is as wide at every depth as the
        perspective one at its centre)."""
        from PySide6.QtCore import QSignalBlocker
        self.ortho3d = bool(on)
        for w in (getattr(self, 'ortho_btn', None),
                  getattr(self, 'acts', {}).get('ortho')):
            if w is not None and w.isChecked() != self.ortho3d:
                with QSignalBlocker(w):
                    w.setChecked(self.ortho3d)
        if self.gl is not None:
            self.view3d.update()
            self._update_gizmo_3d()

    def _set_cam(self, **kw):
        if 'center' in kw:
            kw['center'] = pg.Vector(*kw['center'])
        self.view3d.setCameraParams(**kw)
        self.view3d.update()
        self._update_gizmo_3d()

    def _aspect(self):
        return max(self.view3d.width(), 1) / max(self.view3d.height(), 1)

    def camera(self, name):
        """The camera from the top, front, side (orthographic) or a corner
        (iso, in perspective), fitted to the board and the drawing."""
        if self.gl is None:
            return
        self.set_ortho(name != 'iso')
        b = self._bounds3d() or (-5, -20, 0, 25, 5, 1.6)
        self._set_cam(**v3.preset(name, b, self.view3d.opts['fov'],
                                  self._aspect(), ortho=self.ortho3d))

    def frame_3d(self):
        """The selection (else everything) filling the 3D view, seen from
        where the camera is."""
        if self.gl is None:
            return
        b = self._bounds3d(self.selected or None) or self._bounds3d()
        if b is not None:
            self._set_cam(**v3.frame(b, self._cam(), self._aspect()))

    def _bounds3d(self, which=None):
        """(x0, y0, z0, x1, y1, z1) of the shapes of index `which` (None: the
        board and every shown shape), or None."""
        boxes = []
        idx = range(len(self.drawing.shapes)) if which is None else which
        for i in idx:
            s = self.drawing.shapes[i]
            if s.hidden and which is None:
                continue
            b = self._box3d(i)
            if b is not None:
                boxes.append(b)
        if which is None and self.layout_ and self.layout_.get('outline'):
            from .drawtab import shape_bounds
            x0, y0, x1, y1 = shape_bounds(self.layout_)
            zs = [l['z0'] for l in self.stack['layers']] + [
                l['z1'] for l in self.stack['layers']] or [0.0]
            boxes.append([x0, y0, min(zs), x1, y1, max(zs)])
        if not boxes:
            return None
        b = np.asarray(boxes, float)
        return (*b[:, :3].min(0), *b[:, 3:].max(0))

    def _box3d(self, i):
        """Shape i's 3D box (x0, y0, z0, x1, y1, z1), kept with its mesh."""
        e = self._entries3d.get(i)
        if e is not None:
            if e.get('box') is None:
                v = e['mesh'][0]
                e['box'] = np.r_[v.min(0), v.max(0)]
            return e['box']
        m = _mesh_of(self.drawing.shapes[i], self.stack)
        return None if m is None or not len(m[0]) else np.r_[m[0].min(0),
                                                              m[0].max(0)]

    # ---- drawing it ----
    def _redraw_3d(self):
        """The 3D view as the drawing is: the board's items made again only
        when the board, the clip or what is shown of it changed; a shape whose
        mesh is the same keeps its item (recoloured if its look changed), the
        rest are made anew. (Made anew every time, 300 vias took 106 ms.)"""
        if self.gl is None:
            return
        self._3d_timer.stop()  # (drawn now)
        view = self.view3d
        for it in self._gl_items:
            view.removeItem(it)
        self._gl_items = []
        self._outline_gl = self._outline_fixed_gl = None
        self._grid_gl = None  # (the gizmo's items are kept: placed again)
        on = self.board_on.isChecked()
        bkey = (self._board_ver, self.clip, on, tuple(sorted(
            k for k, v in self.shown3d.items() if v)))
        if bkey != self._board_gl_key:
            for it in self._board_gl:
                view.removeItem(it)
            self._board_gl = [self._mesh(v, f, rgba, opaque) for key, v, f,
                              rgba, opaque in (self._board_clipped() if on
                                               else [])
                              if self.shown3d.get(key, True)]
            self._board_gl_key = bkey
        pool = {}
        for e in self._entries3d.values():
            pool.setdefault(e['key'], []).append(e)
        entries = {}
        for i, s in enumerate(self.drawing.shapes):
            if not self._shown3d(s):
                continue
            key, look = self._mesh_key(s), self._look(i, s)
            got = pool.get(key)
            e = got.pop() if got else None
            if e is None:
                m = self._shape_mesh3d(s)
                if m is None or not len(m[0]):
                    continue
                e = dict(key=key, mesh=m, item=self._mesh(*m, *look),
                         look=look)
            else:
                e['item'].resetTransform()
                if e['look'] != look:
                    _relook(e['item'], look, e['mesh'])
                    e['look'] = look
            entries[i] = e
        for es in pool.values():
            for e in es:
                view.removeItem(e['item'])
        self._entries3d = entries
        self._meshes3d = {i: e['mesh'] for i, e in entries.items()}
        self._shape_gl = {i: e['item'] for i, e in entries.items()}
        self._update_grid_3d()
        self._update_outline_3d()

    def _mesh_key(self, s):
        """What a shape's 3D mesh depends on."""
        from .drawtab import _geo_key
        return (_geo_key(s), s.material, None if s.solid else s.layer,
                (s.z0, s.z1) if s.solid else None, self.clip, self._board_ver)

    def _look(self, i, s):
        """(rgba, opaque) of shape i in 3D: its material's colour, lighter
        when selected (the selection is outlined too); a magnet's (rgba,
        opaque, (zm, top rgba, bottom rgba)): its halves' poles, meeting at
        zm. A ferrite is opaque (a dark ceramic, seen through a magnet over
        it)."""
        mat = 'port' if s.kind == 'port' else s.material
        poles = mat == 'magnet' and s.solid
        key = (mat, i in self.selected, self.see_through.isChecked(),
               (s.br >= 0, (s.z0 + s.z1) / 2) if poles else None)
        # (the same for every via of a fence: worked out once, not 300 times
        # a selection)
        look = _LOOKS.get(key)
        if look is None:
            if len(_LOOKS) > 4096:
                _LOOKS.clear()
            look = _LOOKS[key] = _look_of(*key)
        return look

    def _mesh(self, v, f, rgba, opaque, poles=None):
        from .volumetab import copper_shader
        md = _meshdata(self.gl, v, f, poles)
        it = self.gl.GLMeshItem(
            meshdata=md, color=rgba, smooth=False, shader=copper_shader(),
            glOptions=_gl_options(opaque))
        it.setDepthValue(0 if opaque else 5)
        self.view3d.addItem(it)
        return it

    def _shape_mesh3d(self, s):
        """Shape s's triangles as drawn (core.shapes.shape_mesh's: a sheet a
        slab at its layer, a cut just proud of it, a solid from z0 to z1, a
        port a post), through the clip plane cut and capped (its outline
        clipped, extruded again); a convex one's caps a fan
        (view3d.prism_fast)."""
        from .drawtab import _outline
        if s.kind == 'port':
            m = _mesh_of(s, self.stack)
            if m is None or self.clip is None:
                return m
            v, f = v3.clip_mesh(*m, *self.clip)
            return (v, f) if len(f) else None
        g = _outline(s)
        if g is None:
            return None
        if s.solid:
            z0, z1 = sorted((s.z0, s.z1))
            if z1 - z0 < 1e-9:
                return None
        else:
            z = self.stack['cu_z'].get(s.layer)
            if z is None:
                return None
            h = max(self.stack['cu_t'].get(s.layer, 0.035),
                    sh.SHEET_SHOWN) / 2
            lift = 0.01 if s.material == 'cut' else 0.0
            z0, z1 = z - h - lift, z + h + lift
        poles = s.solid and s.material == 'magnet'
        if self.clip is not None:
            axis, value, keep = self.clip
            if axis == 2:
                r = v3.clip_range(z0, z1, value, keep)
                if r is None:
                    return None
                z0, z1 = r
            else:
                g = v3.clip_outline(g, axis, value, keep)
                if g is None:
                    return None
        if poles:  # (its halves: the faces of each a pole's colour)
            return dm.magnet_mesh(g, z0, z1, (s.z0 + s.z1) / 2)
        return v3.prism_fast(g, z0, z1)

    def _board_clipped(self):
        """The board's meshes [(key, v, f, rgba, opaque)], made once a board
        (and once a clip)."""
        if self._board3d is None:
            self._board3d, self._board3d_at = self._board_meshes(), None
        if self.clip is None:
            return self._board3d
        if self._board3d_at is not None and self._board3d_at[0] == self.clip:
            return self._board3d_at[1]
        axis, value, keep = self.clip
        out = []
        for key, v, f, rgba, opaque in self._board3d:
            if key == 'substrate' and axis != 2:
                from shapely.geometry import shape as geo
                g = v3.clip_outline(geo(self.layout_['outline']), axis, value,
                                    keep)
                m = None if g is None else v3.prism_fast(g, v[:, 2].min(),
                                                         v[:, 2].max())
            else:
                m = v3.clip_mesh(v, f, axis, value, keep)
            if m is not None and len(m[1]):
                out.append((key, *m, rgba, opaque))
        self._board3d_at = (self.clip, out)
        return out

    def _board_meshes(self):
        """The board's substrate and copper as (key, vertices, faces, rgba,
        opaque): key 'substrate' or 'cu:<layer>'."""
        d = self.layout_
        if not d:
            return []
        from shapely.geometry import shape as geo
        from . import volume3d as vol
        out = []
        layers = self.stack['layers']
        if d.get('outline') and layers:
            z0 = min(l['z0'] for l in layers)
            z1 = max(l['z1'] for l in layers)
            try:
                v, f = v3.prism_fast(geo(d['outline']), z0, z1)
                out.append(('substrate', v, f, (0.30, 0.62, 0.36, 0.25),
                            False))
            except Exception:  # noqa: BLE001
                pass
        try:
            geom = vol.geometry_from_board(d)
            for lay, _, v, f in vol.copper_solids(geom, 0.05):
                out.append((f"cu:{lay}", v, f, (0.84, 0.55, 0.25, 1.0), True))
        except Exception:  # noqa: BLE001 (a board without copper)
            pass
        return out

    def _level(self):
        """The working level's height: the layer drawn on, or a new solid's
        bottom."""
        if self.material.currentText() in sh.SOLID:
            return self.z0.value()
        return self.stack['cu_z'].get(self.layer.currentText(), 0.0)

    def _selection_3d(self, was):
        """The shapes selected or no longer (`was`: the selection before)
        coloured again, and the outline and the gizmo moved to the
        selection; the rest stays drawn."""
        if self.gl is None or self._3d_timer.isActive():
            return  # (drawn whole soon)
        ss = self.drawing.shapes
        for i in set(was) ^ set(self.selected):
            e = self._entries3d.get(i)
            if e is not None and i < len(ss):
                e['look'] = self._look(i, ss[i])
                _relook(e['item'], e['look'], e['mesh'])
        self._update_outline_3d()

    def _update_grid_3d(self):
        """The snap grid (made coarser to stay readable) at the working
        level, over the board (else the drawing, else a default area)."""
        if self.gl is None:
            return
        if self._grid_gl is not None:
            self.view3d.removeItem(self._grid_gl)
            self._gl_items.remove(self._grid_gl)
            self._grid_gl = None
        if not self.grid_on.isChecked():
            return
        b = self._bounds3d()
        b2 = (b[0], b[1], b[3], b[4]) if b is not None else (-5, -20, 25, 5)
        g = v3.grid_spec(b2, self.snap.value() or 1.0)
        grid = self.gl.GLGridItem(color=(140, 140, 160, 90))
        grid.setSize(*g['size'])
        grid.setSpacing(g['spacing'], g['spacing'])
        grid.translate(g['center'][0], g['center'][1], self._level())
        self.view3d.addItem(grid)
        self._gl_items.append(grid)
        self._grid_gl = grid
        self.grid_spacing3d = g['spacing']

    def _update_outline_3d(self):
        """The selected shapes outlined (their solids' edges, drawn over
        everything: those that move with a drag apart from the rest), and
        the gizmo at them."""
        if self.gl is None:
            return
        for name in ('_outline_gl', '_outline_fixed_gl'):
            it = getattr(self, name, None)
            if it is not None:
                self.view3d.removeItem(it)
                self._gl_items.remove(it)
            setattr(self, name, None)
        movable = set(self._movable())
        segs = {True: [], False: []}
        for i in self.selected:
            if i >= len(self.drawing.shapes):
                continue
            e = self._entries3d.get(i)
            if e is None:
                continue
            if e.get('edges') is None:
                e['edges'] = self._edges3d(self.drawing.shapes[i], e)
            segs[i in movable].append(e['edges'])
        from .drawtab import SELECTED
        c = SELECTED
        for moving, name in ((True, '_outline_gl'),
                             (False, '_outline_fixed_gl')):
            if not segs[moving]:
                continue
            it = self.gl.GLLinePlotItem(
                pos=np.concatenate(segs[moving]).astype(np.float32),
                mode='lines', width=2.0, antialias=True,
                color=(c.redF(), c.greenF(), c.blueF(), 1.0))
            self.view3d.addItem(it)
            self._gl_items.append(it)
            setattr(self, name, it)
        self._update_gizmo_3d()

    def _edges3d(self, s, e):
        """The outline (segment ends) of shape s as drawn (its entry e)."""
        from .drawtab import _outline
        b = self._box3d_of(e)
        g = _outline(s) if s.kind != 'port' else None
        if g is not None and self.clip is not None and self.clip[0] != 2:
            g = v3.clip_outline(g, *self.clip)
        if g is None:  # a port: its post's box
            from shapely.geometry import box
            g = box(b[0], b[1], b[3], b[4])
        return v3.prism_edges(g, b[2], b[5])

    def _box3d_of(self, e):
        if e.get('box') is None:
            v = e['mesh'][0]
            e['box'] = np.r_[v.min(0), v.max(0)]
        return e['box']

    # ---- the gizmo ----
    def _gizmo_frame(self):
        """(base, length, axes) of the gizmo at the movable selection (its
        centre on top), or None: x and y always, z when they are solids."""
        movable = self._movable()
        if not movable:
            return None
        boxes = [b for b in (self._box3d(i) for i in movable
                             if i in self._entries3d) if b is not None]
        if not boxes:
            return None
        b = np.asarray(boxes)
        lo, hi = b[:, :3].min(0), b[:, 3:].max(0)
        base = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2]])
        px = float(self.view3d.pixelSize(pg.Vector(*base)))
        length = max(70 * px, 1e-3)
        axes = (0, 1) + ((2,) if all(self.drawing.shapes[i].solid
                                     for i in movable) else ())
        return base, length, axes

    def _update_gizmo_3d(self):
        """The gizmo at the movable selection, as long as 70 pixels there:
        its shafts and arrowheads made once for its axes (x and y, or x, y
        and z), at unit size, and placed by a transform (made again at each
        redraw, selection and step of the wheel, they took 0.1-0.3 ms)."""
        if self.gl is None:
            return
        self._gizmo = fr = self._gizmo_frame()
        if fr is None:
            for it in (self._gizmo_gl, self._gizmo_cone_gl):
                if it is not None:
                    it.setVisible(False)
            return
        base, length, axes = fr
        if self._gizmo_gl is None or self._gizmo_axes != axes:
            self._make_gizmo(axes)
        self._place_gizmo()

    def _make_gizmo(self, axes):
        """The gizmo's items, unit-sized from the origin, for these axes."""
        for it in (self._gizmo_gl, self._gizmo_cone_gl):
            if it is not None:
                self.view3d.removeItem(it)
        pos, col, vs, fs, fc = [], [], [], [], []
        for k in axes:
            # a shaft, and a cone at its end (its arrowhead)
            tip = np.zeros(3)
            tip[k] = 1 - GIZMO_HEAD[0]
            pos += [np.zeros(3), tip]
            col += [AXIS_RGBA[k]] * 2
            v, f = v3.arrow_cone(tip, k, *GIZMO_HEAD)
            fs.append(f + sum(len(x) for x in vs))
            vs.append(v)
            fc.append(np.tile(AXIS_RGBA[k], (len(f), 1)))
        it = self.gl.GLLinePlotItem(pos=np.asarray(pos, np.float32),
                                    color=np.asarray(col, np.float32),
                                    mode='lines', width=4.0, antialias=True)
        it.setDepthValue(19)
        self.view3d.addItem(it)
        md = self.gl.MeshData(vertexes=np.concatenate(vs).astype(np.float32),
                              faces=np.concatenate(fs).astype(np.uint32),
                              faceColors=np.concatenate(fc).astype(
                                  np.float32))
        from .volumetab import copper_shader  # (lit as the shapes are)
        cone = self.gl.GLMeshItem(meshdata=md, smooth=False,
                                  shader=copper_shader(), glOptions=_on_top())
        cone.setDepthValue(20)  # (drawn last, over the scene)
        self.view3d.addItem(cone)
        self._gizmo_gl, self._gizmo_cone_gl, self._gizmo_axes = it, cone, axes

    def _place_gizmo(self, d=(0.0, 0.0, 0.0)):
        """The gizmo's items at its base (moved d: a drag), its length."""
        base, length, _ = self._gizmo
        for it in (self._gizmo_gl, self._gizmo_cone_gl):
            it.resetTransform()
            it.translate(*(np.asarray(base, float) + d))
            it.scale(length, length, length)  # (local: first)
            it.setVisible(True)

    def _gizmo_hit(self, x, y):
        """The gizmo's axis (0, 1, 2) whose arrow pixel (x, y) is on, or
        None."""
        fr = self._gizmo
        if fr is None:
            return None
        base, length, axes = fr
        m, w, h = self._mvp(), self.view3d.width(), self.view3d.height()
        best = None
        for k in axes:
            tip = base.copy()
            tip[k] += length
            d, t = v3.pixel_to_segment(m, w, h, base, tip, x, y)
            if d <= GIZMO_PX and t > 0.15 and (best is None or d < best[0]):
                best = (d, k)
        return None if best is None else best[1]

    # ---- picking and editing in 3D ----
    def _mvp(self):
        v = self.view3d
        return v3.mvp(self._cam(), max(v.width(), 1), max(v.height(), 1))

    def ray3d(self, x, y):
        """(origin, unit direction) of the ray under pixel (x, y) of the 3D
        view, model mm."""
        v = self.view3d
        return v3.ray(self._mvp(), max(v.width(), 1), max(v.height(), 1), x,
                      y)

    def project3d(self, p):
        """The 3D view's pixel (x, y) of model point p."""
        v = self.view3d
        return v3.project(self._mvp(), max(v.width(), 1), max(v.height(), 1),
                          p)

    def pick3d(self, x, y):
        """(shape index, model point) under pixel (x, y) of the 3D view, or
        (None, None). Only drawn shapes are picked (not the board; not a
        hidden shape, a material not shown, or what the clip took off)."""
        o, d = self.ray3d(x, y)
        # (each mesh's box first: trying all 300 vias' triangles took 14 ms
        # a pick, and a hover picks on every move)
        i, t = v3.pick_boxed(self._meshes3d, {
            k: self._box3d(k) for k in self._meshes3d}, o, d)
        return (None, None) if i is None else (i, o + t * d)

    def _ensure_3d(self):
        if self._3d_timer.isActive():  # what is drawn, before it is picked
            self._3d_timer.stop()
            self._redraw_3d()

    def _press3d(self, ev):
        """A left press in 3D. On the gizmo: a drag along its axis. On a
        shape: it is selected (Shift: a click toggles it, a drag on a solid
        stretches or lifts it) and a drag moves the selection (True: the
        view does not orbit). On nothing, or with Ctrl, the view orbits or
        pans; a click there (no move) selects none (Ctrl: toggles)."""
        pos = ev.position()
        x, y = pos.x(), pos.y()
        self._press3d_at, self._drag3d = (x, y), None
        self._axis = None
        mods = ev.modifiers()
        if ev.button() != Qt.LeftButton or mods & Qt.ControlModifier:
            return False
        self._ensure_3d()
        ax = self._gizmo_hit(x, y)
        if ax is not None:
            o, d = self.ray3d(x, y)
            base = self._gizmo[0]
            s0 = v3.along_axis(o, d, base, ax)
            if s0 is not None:
                self._drag3d = self._drag_start(kind='axis', axis=ax, at=base,
                                                s0=s0)
                self.status.setText(
                    f"3D: along {'xyz'[ax]} (the gizmo's arrow)")
                return True
        i, at = self.pick3d(x, y)
        if i is None:
            if not mods & Qt.ShiftModifier:
                return False
            # Shift + drag on nothing: a box (not an orbit)
            self._drag3d = dict(kind='box', p0=(x, y), moved=False)
            return True
        s = self.drawing.shapes[i]
        if mods & Qt.ShiftModifier:
            face = None
            if s.solid:
                lo, hi = sorted((s.z0, s.z1))
                tol = 0.02 * max(hi - lo, 1e-3)
                face = ('top' if abs(at[2] - hi) < tol else
                        'bottom' if abs(at[2] - lo) < tol else 'side')
            self._drag3d = self._drag_start(
                kind='face' if face else 'none', i=i, at=at, face=face,
                toggle=True, normal=self._upright(x, y))
            return True
        if i not in self.selected:
            self.select(i)
        if s.locked:
            self._drag3d = self._drag_start(kind='none', i=i, at=at)
            self.status.setText(f"3D: {s.label()} is locked (not moved)")
            return True
        self._drag3d = self._drag_start(kind='move', i=i, at=at, s0=0.0)
        self.view3d.setCursor(Qt.ClosedHandCursor)
        self.status.setText(
            f"3D: {len(self._movable())} shape(s) - drag to move them on their "
            "level (X / Y / Z: along that axis only)" + (
                '; Shift+drag a solid\'s top or bottom face to stretch it, '
                'its side to lift it' if s.solid else ''))
        return True

    def _upright(self, x, y):
        """The normal of the upright plane facing the camera (a lift's), or
        None looking straight down."""
        _, d = self.ray3d(x, y)
        n = np.array([d[0], d[1], 0.0])
        return n / np.linalg.norm(n) if np.linalg.norm(n) > 1e-6 else None

    def _drag_start(self, **st):
        st.update(before=self.drawing.to_dict(), moved=False,
                  orig={j: self.drawing.shapes[j] for j in self._movable()})
        return st

    def _move3d(self, ev):
        """A drag of what was pressed on (True), else a hover (the cursor
        says what a press would do) or the view's own orbit (False)."""
        pos = ev.position()
        st = self._drag3d
        if st is None:
            if ev.buttons() == Qt.NoButton:
                hit = (self._gizmo_hit(pos.x(), pos.y()) is not None
                       or self.pick3d(pos.x(), pos.y())[0] is not None)
                self.view3d.setCursor(Qt.OpenHandCursor if hit else
                                      Qt.ArrowCursor)
            return False
        if st['kind'] == 'none':
            return True
        if st['kind'] == 'box':
            self._box3d_to(st, pos.x(), pos.y())
            return True
        if st.get('toggle') and not st['moved']:  # a Shift drag starts
            if st['i'] not in self.selected:
                self.select(st['i'])
            st['orig'] = {j: self.drawing.shapes[j] for j in self._movable()}
        o, d = self.ray3d(pos.x(), pos.y())
        at, g = st['at'], self.snap.value()
        rnd = (lambda v: round(round(v / g) * g, 6)) if g > 0 else (
            lambda v: v)
        axis = st['axis'] if st['kind'] == 'axis' else (
            'xyz'.index(self._axis) if self._axis else None)
        new = {}
        if axis is not None or st['kind'] == 'move':
            if axis is not None:
                s = v3.along_axis(o, d, at, axis)
                if s is None:
                    return True
                d3 = [0.0, 0.0, 0.0]
                d3[axis] = rnd(s - st.get('s0', 0.0))
            else:  # on its level
                if abs(d[2]) < 1e-9:
                    return True
                p = o + ((at[2] - o[2]) / d[2]) * d
                d3 = [rnd(p[0] - at[0]), rnd(p[1] - at[1]), 0.0]
            self._shift3d(st, tuple(d3))
            return True
        elif st['kind'] == 'face':
            n = st['normal']
            if n is None:  # looking straight down: the mouse's rows
                dz = -(pos.y() - self._press3d_at[1]) * self.view3d.pixelSize(
                    pg.Vector(*at))
            else:
                den = d @ n
                if abs(den) < 1e-9:
                    return True
                dz = (o + ((at - o) @ n / den) * d)[2] - at[2]
            face = None if st['face'] == 'side' else st['face']
            for j, s0 in st['orig'].items():
                new[j] = s0.lifted(rnd(dz), face) if s0.solid else s0
        changed = [j for j, s in new.items()
                   if s.to_dict() != self.drawing.shapes[j].to_dict()]
        for j in changed:
            self.drawing.shapes[j] = new[j]
            self._update_shape_3d(j)
        if changed:
            st['moved'] = True
            self._refresh(listing=False, quick=True)
            self._update_outline_3d()
        return True

    def _cancel_drag3d(self):
        """Esc while dragging in 3D: the shapes back where they were (the
        rest of the drag moves nothing)."""
        st = self._drag3d
        if st['kind'] == 'box':
            self._box3d_hide()
            st['kind'] = 'none'
            self.status.setText('the box dropped (Esc)')
            return
        if 'd' in st:  # (moved by a transform: the drawing is as it was)
            self._shift3d(st, (0.0, 0.0, 0.0))
        elif st['moved']:
            from kicad2ems.core.shapes import Drawing
            self.drawing = Drawing.from_dict(st['before'])
            self._refresh(listing=False)
        st.update(kind='none', moved=False, toggle=False)
        self.status.setText('the drag undone (Esc)')

    def _shift3d(self, st, d3):
        """The shapes dragged shown d3 = (dx, dy, dz) from where they were:
        their 3D items, their outline and the gizmo moved by a transform,
        their plan items by an offset (the drawing changes at the release:
        made again on each move, the whole 300-via fence took 122 ms a
        move)."""
        if d3 == st.get('d'):
            return
        st['d'] = d3
        st['moved'] = any(d3)
        dx, dy, dz = d3
        for j, s0 in st['orig'].items():
            it = self._shape_gl.get(j)
            if it is not None:
                it.resetTransform()
                it.translate(dx, dy, dz if s0.solid else 0.0)
            self._shift_items(j, dx, dy)
        if self._outline_gl is not None:
            self._outline_gl.resetTransform()
            self._outline_gl.translate(dx, dy, dz)
        if self._gizmo is not None and self._gizmo_gl is not None:
            self._place_gizmo((dx, dy, dz))
        self.status.setText(
            f"3D: moved ({dx:g}, {dy:g}" + (f", {dz:g}" if dz else '') +
            ') mm' + (f" along {'xyz'[st['axis']]}" if st['kind'] == 'axis'
                      else f" along {self._axis} only" if self._axis else ''))

    def _shifted(self, st):
        """The drawing as a drag by a transform left it."""
        dx, dy, dz = st.get('d', (0.0, 0.0, 0.0))
        for j, s0 in st['orig'].items():
            s = s0.moved(dx, dy) if dx or dy else s0
            self.drawing.shapes[j] = s.lifted(dz) if dz and s0.solid else s

    def _release3d(self, ev):
        st, at = self._drag3d, self._press3d_at
        self._drag3d = self._press3d_at = None
        self._axis = None
        if st is not None and st['kind'] == 'box':
            pos = ev.position()
            self._box3d_hide()
            (x0, y0), (x1, y1) = st['p0'], (pos.x(), pos.y())
            if abs(x1 - x0) + abs(y1 - y0) >= 4:
                self.box_select3d(x0, y0, x1, y1)
            return
        if st is not None:
            self.view3d.setCursor(Qt.OpenHandCursor)
            if st['moved']:
                if 'd' in st:
                    self._shifted(st)
                self._commit(st['before'])
            elif st.get('toggle'):
                self.select(st['i'], 'toggle')
            return
        pos = ev.position()
        if (at is None or ev.button() != Qt.LeftButton
                or abs(pos.x() - at[0]) + abs(pos.y() - at[1]) >= 4):
            return  # an orbit or a pan
        if ev.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            i, _ = self.pick3d(pos.x(), pos.y())
            if i is not None:
                self.select(i, 'toggle')
            return
        if self.selected:  # a click on nothing (not an orbit): none
            self.select(None)

    # ---- a box selection ----
    def box_select3d(self, x0, y0, x1, y1):
        """The shapes drawn wholly inside the 3D view's pixel box (x0, y0) to
        (x1, y1) selected (dragged right to left: those it touches), as the
        plan's box; what the clip took off, hidden shapes and materials not
        shown are not. The indices."""
        self._ensure_3d()
        v = self.view3d
        crossing = x1 < x0
        hits = sorted(v3.box_pick(self._meshes3d, self._mvp(),
                                  max(v.width(), 1), max(v.height(), 1),
                                  (x0, y0, x1, y1), crossing))
        self.select_many(hits)
        self.status.setText(f"3D: {len(hits)} shape(s) " + (
            'touching the box (right to left)' if crossing else
            'inside the box (left to right)'))
        return hits

    def _box3d_to(self, st, x, y):
        from PySide6.QtCore import QRect, QPoint
        from PySide6.QtWidgets import QRubberBand
        if self._band3d is None:
            self._band3d = QRubberBand(QRubberBand.Rectangle, self.view3d)
        x0, y0 = st['p0']
        self._band3d.setGeometry(QRect(QPoint(round(x0), round(y0)),
                                       QPoint(round(x), round(y))).normalized())
        self._band3d.show()
        self.status.setText('3D: a box, ' + (
            'touching it (right to left)' if x < x0 else
            'inside it (left to right)'))

    def _box3d_hide(self):
        if self._band3d is not None:
            self._band3d.hide()

    def _update_shape_3d(self, i):
        """Shape i's 3D mesh, again (while it is dragged: the rest stays)."""
        s = self.drawing.shapes[i]
        m = self._meshes3d[i] = self._shape_mesh3d(s)
        it = self._shape_gl.get(i)
        e = self._entries3d.get(i)
        if e is not None and m is not None:
            e.update(mesh=m, box=None, edges=None)
        if it is None or m is None:
            return
        look = self._look(i, s)  # (a magnet's halves meet where it is now)
        if e is not None:
            e['look'] = look
        it.setMeshData(meshdata=_meshdata(self.gl, *m, look[2] if len(
            look) > 2 else None))


def _pick_view(base, tab):
    """The 3D view class whose left button the page's 3D editing has first
    (Draw3D._press3d / _move3d / _release3d): what they leave orbits and pans
    as before. Its keys are the page's (Delete, Esc, a tool's letter, undo,
    the camera's digits, X / Y / Z while dragging)."""
    class PickView(base):
        def projectionMatrix(self, region, viewport):
            if not tab.ortho3d:
                return super().projectionMatrix(region, viewport)
            # (view3d.ortho_matrix: as wide as the perspective's centre)
            from PySide6.QtGui import QMatrix4x4
            x0, y0, w, h = viewport
            m = v3.ortho_matrix(self.opts['distance'], self.opts['fov'], w,
                                h, (region[0] - x0, region[1] - y0,
                                    region[2], region[3]))
            return QMatrix4x4(*m.ravel().tolist())

        def pixelSize(self, pos):
            if not tab.ortho3d:
                return super().pixelSize(pos)
            px = v3.ortho_pixel(self.opts['distance'], self.opts['fov'],
                                self.width())
            return (np.full(np.shape(pos)[:-1], px)
                    if isinstance(pos, np.ndarray) else px)

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

        def wheelEvent(self, ev):
            super().wheelEvent(ev)
            tab._update_gizmo_3d()

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


_LOOKS = {}


def _look_of(mat, sel, see_through, poles):
    """Draw3D._look's (rgba, opaque[, poles]) of material `mat` ('port': a
    port), selected or not; poles (N on top, zm): a magnet's."""
    from .drawtab import COLOURS, PORT_COLOUR
    tint = np.array([120, 190, 255.0])
    lit = lambda rgb: tuple(float(c) / 255 for c in (
        0.55 * np.asarray(rgb, float) + 0.45 * tint if sel else rgb))
    rgb = lit(PORT_COLOUR if mat == 'port' else COLOURS[mat])
    a = (0.45 if mat in ('dielectric', 'air') else 0.55
         if mat in ('metal', 'magnet') and see_through else 1.0)
    if poles is None:
        return (*rgb, a), a == 1.0
    up, zm = poles
    top, bottom = (lit(p) + (a,) for p in dm.poles(1.0 if up else -1.0))
    return (*rgb, a), a == 1.0, (zm, top, bottom)


def _meshdata(gl, v, f, poles=None):
    """The GL mesh data of triangles (v, f); poles (zm, top rgba, bottom
    rgba): a magnet's, each face its half's colour."""
    if poles is None:
        return gl.MeshData(vertexes=v.astype(np.float32),
                           faces=f.astype(np.uint32))
    return gl.MeshData(vertexes=v.astype(np.float32),
                       faces=f.astype(np.uint32),
                       faceColors=dm.pole_colours(v, f, *poles))


def _relook(it, look, mesh=None):
    """A shape's GL item recoloured: look (rgba, opaque), or a magnet's (rgba,
    opaque, poles): its faces' colours set again (mesh: its triangles)."""
    rgba, opaque = look[:2]
    if len(look) > 2 and mesh is not None:
        md = it.opts['meshdata']
        md.setFaceColors(dm.pole_colours(*mesh, *look[2]))
        it.meshDataChanged()
    else:
        it.setColor(rgba)
    it.setGLOptions(_gl_options(opaque))
    it.setDepthValue(0 if opaque else 5)


_TRANSLUCENT = _ON_TOP = None


def _on_top():
    """GL options of what is drawn over the whole scene (the gizmo's
    arrowheads): no depth test, the faces turned from the camera culled (a
    cone's far side not drawn over its near side)."""
    global _ON_TOP
    if _ON_TOP is None:
        from OpenGL import GL
        _ON_TOP = {GL.GL_DEPTH_TEST: False, GL.GL_BLEND: False,
                   GL.GL_CULL_FACE: True, 'glCullFace': (GL.GL_BACK,)}
    return _ON_TOP


def _gl_options(opaque):
    """'opaque', or pyqtgraph's 'translucent' with the faces turned away
    from the camera culled: a closed solid (wound outward) seen through
    shows its near faces over what is behind it, not its far faces mixed
    into them in the order its triangles are drawn (streaks on a big
    see-through magnet's walls)."""
    global _TRANSLUCENT
    if opaque:
        return 'opaque'
    if _TRANSLUCENT is None:
        from OpenGL import GL
        from pyqtgraph.opengl.GLGraphicsItem import GLOptions
        _TRANSLUCENT = dict(GLOptions['translucent'])
        _TRANSLUCENT[GL.GL_CULL_FACE] = True
        _TRANSLUCENT['glCullFace'] = (GL.GL_BACK,)
    return _TRANSLUCENT
