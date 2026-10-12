"""
The 3D field panel: a volume dump as a translucent voxel cloud over the board (copper, vias, the board's box),
in an OpenGL view you orbit (drag), zoom (wheel) and pan (middle drag, or Ctrl + drag). A frequency-domain dump
plays one period of Re(F e^jphi), a time-domain one its frames; *Save GIF / MP4* writes the animation as seen
from the current camera. Needs PyOpenGL (pip install PyOpenGL; the gui extra has it).
"""
import os
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QComboBox, QCheckBox, QSlider,
                               QPushButton, QLabel, QDoubleSpinBox, QFileDialog, QMessageBox, QSpinBox)

from .. import volume3d as v3

N_PHASE = 24


def gl_module():
    """pyqtgraph.opengl, or None (and why) when PyOpenGL is missing."""
    try:
        import pyqtgraph.opengl as gl
        return gl, ''
    except Exception as e:                                          # ImportError: no PyOpenGL
        return None, f"{type(e).__name__}: {e}"


_COPPER_SHADER = None


def copper_shader():
    """A shader for the copper slabs: pyqtgraph's 'shaded' inputs, brighter: ambient 0.5, a key light from the upper
    front of the view (two-sided: a wall seen from inside the slab is lit too) and a small highlight, so the top
    faces stay copper-coloured and the walls and edges shade as the view turns."""
    global _COPPER_SHADER
    if _COPPER_SHADER is None:
        from pyqtgraph.opengl import shaders as S
        _COPPER_SHADER = S.ShaderProgram('kicad2ems_copper', [S.VertexShader("""
            uniform mat4 u_mvp;
            uniform mat3 u_normal;
            attribute vec4 a_position;
            attribute vec3 a_normal;
            attribute vec4 a_color;
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                v_normal = normalize(u_normal * a_normal);
                v_color = a_color;
                gl_Position = u_mvp * a_position;
            }
        """), S.FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                vec3 n = normalize(v_normal);
                vec3 l = normalize(vec3(-0.35, 0.55, 1.0));
                float d = abs(dot(n, l));
                float s = pow(max(dot(reflect(-l, n), vec3(0.0, 0.0, 1.0)), 0.0), 24.0);
                vec3 rgb = v_color.rgb * (0.5 + 0.55 * d) + vec3(0.25) * s;
                gl_FragColor = vec4(min(rgb, vec3(1.0)), v_color.a);
            }
        """)])
    return _COPPER_SHADER


def _view_class(gl):
    """GLViewWidget that reports each new OpenGL context (after the first paint is set up)."""
    class View(gl.GLViewWidget):
        on_init = None

        def initializeGL(self):
            super().initializeGL()
            if self.on_init is not None:
                QTimer.singleShot(0, self.on_init)
    return View


class VolumeTab(QWidget):
    def __init__(self, win=None):
        super().__init__()
        self.win, self.srcs, self.src, self.vol, self.items = win, [], None, None, {}
        self.board, self.mesh_files = None, {}                      # <name>_board.json's geometry; meshes.find()
        self._frames, self._key, self.k = [], None, 0
        lay = QHBoxLayout(self)
        self.gl, why = gl_module()
        if self.gl is None:
            note = QLabel(f"The 3D field view needs PyOpenGL: pip install PyOpenGL (into the Python of the GUI)\n({why})")
            note.setAlignment(Qt.AlignCenter)
            note.setStyleSheet('color: gray')
            lay.addWidget(note)
            return
        side = QVBoxLayout()
        form = QFormLayout()
        self.dump = QComboBox()
        self.frame = QComboBox()
        self.comp = QComboBox()
        self.comp.addItems(['|F|', 'Fx', 'Fy', 'Fz'])
        self.cmap = QComboBox()
        self.cmap.addItems(['turbo', 'inferno', 'plasma', 'viridis', 'hot'])
        self.envelope = QCheckBox('envelope (no animation)')
        self.log = QCheckBox('log scale')
        self.log.setChecked(True)
        self.decades = QDoubleSpinBox()
        self.decades.setRange(0.5, 5)
        self.decades.setSingleStep(0.5)
        self.decades.setValue(1.5)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(80)
        self.gamma = QSlider(Qt.Horizontal)
        self.gamma.setRange(5, 40)                                  # gamma x 10
        self.gamma.setValue(12)
        self.zscale = QDoubleSpinBox()
        self.zscale.setRange(1, 20)
        self.zscale.setValue(2)
        self.zscale.setSuffix(' x')
        self.clip = QDoubleSpinBox()
        self.clip.setRange(90, 100)
        self.clip.setDecimals(2)
        self.clip.setSingleStep(0.1)
        self.clip.setValue(99.5)
        self.clip.setSuffix(' %')
        self.clip.setToolTip('the top of the colour range: this percentile of the values (a port gap takes it all at 100 %)')
        self.res = QSpinBox()
        self.res.setRange(48, 256)
        self.res.setValue(128)
        self.res.setSuffix(' voxels')
        self.copper = QCheckBox('copper')
        self.copper.setChecked(True)
        self.cu_t = QDoubleSpinBox()
        self.cu_t.setRange(0, 5)
        self.cu_t.setDecimals(2)
        self.cu_t.setSingleStep(0.05)
        self.cu_t.setSpecialValueText('flat')
        self.cu_t.setSuffix(' mm')
        self.cu_t.setToolTip('the copper drawn as shaded slabs this thick (on screen; real copper is ~35 um): '
                             'its edges catch the light; 0: flat, unshaded')
        self._cu_t_auto = True
        self.boxc = QCheckBox('board box and vias')
        self.boxc.setChecked(True)
        self.mesh = QComboBox()
        self.mesh.setToolTip("a solver's discretisation of the board (written by a preview or a run): openEMS's "
                             "grid lines, mom25d's triangles, EMerge's element faces on the copper and ports")
        for label, w in (('Dump', self.dump), ('Frame', self.frame), ('Show', self.comp), ('Colours', self.cmap),
                         ('', self.envelope),
                         ('', self.log), ('Decades', self.decades), ('Range top', self.clip), ('Opacity', self.opacity),
                         ('Fade (gamma)', self.gamma), ('Height scale', self.zscale), ('Resolution', self.res),
                         ('', self.copper), ('Copper shown', self.cu_t), ('', self.boxc), ('Mesh', self.mesh)):
            form.addRow(label, w)
        side.addLayout(form)
        self.play_btn = QPushButton('Play')
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._play)
        reset = QPushButton('Reset view')
        reset.clicked.connect(self.reset_camera)
        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(reset)
        side.addLayout(row)
        row = QHBoxLayout()
        for ext in ('gif', 'mp4'):
            b = QPushButton(f"Save {ext.upper()}")
            b.clicked.connect(lambda _=False, e=ext: self.save_dialog(e))
            row.addWidget(b)
        side.addLayout(row)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color: gray')
        side.addWidget(self.status)
        side.addStretch(1)
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(300)
        lay.addWidget(sw)
        self.view = _view_class(self.gl)()
        self.view.on_init = self._new_context
        self.view.setBackgroundColor('w')
        lay.addWidget(self.view, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.dump.currentIndexChanged.connect(self._pick)
        self.frame.currentIndexChanged.connect(self._rebuild)
        self.res.valueChanged.connect(self._rebuild)
        for w in (self.comp, self.cmap):
            w.currentIndexChanged.connect(self._recolour)
        for w in (self.envelope, self.log, self.copper, self.boxc):
            w.toggled.connect(self._recolour)
        self.cu_t.valueChanged.connect(self._cu_t_set)
        self.mesh.currentIndexChanged.connect(lambda *_: self._board())
        for w in (self.decades, self.zscale, self.clip):
            w.valueChanged.connect(self._recolour)
        for w in (self.opacity, self.gamma):
            w.valueChanged.connect(self._recolour)

    def _new_context(self):
        """The view has a new OpenGL context (first shown, or moved: a dock tabbed, floated or docked again, which
        re-creates it): items made in the old one hold its shaders and buffers, so make them anew."""
        for it in list(self.view.items):
            self.view.removeItem(it)
        self.items = {}
        if self._frames:
            self._show(self.k)
            self._board()

    @property
    def geometry(self):
        """The board drawn: the dump's geometry, else the folder's <name>_board.json."""
        return self.src.geometry if self.src is not None else self.board

    # ---- data ----
    def load(self, out_dir):
        """A results folder: its volume dumps, its board and the solvers' meshes (a preview writes those). False:
        nothing to show, or no OpenGL."""
        if self.gl is None:
            return False
        import glob
        from ..solvers.openems.fields import find_dumps
        from .. import meshes
        self.srcs = [s for s in find_dumps(out_dir) if v3.is_volume(s)]
        boards = sorted(glob.glob(os.path.join(out_dir, '*_board.json')), key=os.path.getmtime)
        try:
            self.board = v3.geometry_from_board(boards[-1]) if boards else None
        except (OSError, ValueError, KeyError):
            self.board = None
        self.mesh_files = meshes.find(out_dir)
        keep = self.mesh.currentData()
        self.mesh.blockSignals(True)
        self.mesh.clear()
        self.mesh.addItem('none', None)
        for solver, path in self.mesh_files.items():
            self.mesh.addItem(f"{meshes.LABELS.get(solver, solver)}: {meshes.counts(path)}", solver)
        i = self.mesh.findData(keep)
        self.mesh.setCurrentIndex(max(i, 0))
        self.mesh.blockSignals(False)
        self.dump.blockSignals(True)
        self.dump.clear()
        self.dump.addItems([s.label for s in self.srcs])
        self.dump.blockSignals(False)
        if self.srcs:
            self._pick()
        else:                                                       # the board alone, and its meshes
            self.src, self.vol, self._frames = None, None, []
            for key in list(self.items):
                self.view.removeItem(self.items.pop(key))
            self._auto_cu_t()
            self._board()
            self.reset_camera()
            self.status.setText('no volume dumps: the board' + (' and the solvers\' meshes' if self.mesh_files else ''))
        return bool(self.srcs or (self.board and self.mesh_files))

    def _pick(self, *_):
        i = self.dump.currentIndex()
        if i < 0 or i >= len(self.srcs):
            return
        self.src = self.srcs[i].refresh()
        self.frame.blockSignals(True)
        self.frame.clear()
        self.frame.addItems([self.src.frame_label(k) for k in range(len(self.src))])
        self.frame.setCurrentIndex(0 if self.src.domain == 'FD' else max(0, len(self.src) - 1))
        self.frame.blockSignals(False)
        self.envelope.setEnabled(self.src.domain == 'FD')
        self._auto_cu_t()
        self._board()
        self._rebuild()
        self.reset_camera()

    def _rebuild(self, *_):
        if self.src is None or self.frame.currentIndex() < 0:
            return
        self.vol = v3.Volume(self.src, self.frame.currentIndex(), n=self.res.value())
        self._recolour()

    def _component(self):
        return v3.COMPONENTS[self.comp.currentIndex()]

    def _phases(self):
        """The animation's frames: phases (frequency domain), else None (one frame)."""
        if self.src.domain == 'FD' and not self.envelope.isChecked():
            return list(np.linspace(0, 2 * np.pi, N_PHASE, endpoint=False))
        return [None]

    def _recolour(self, *_):
        """The voxels of every animation frame at the current settings (one colour range for all)."""
        if self.vol is None:
            return
        comp, phases = self._component(), self._phases()
        vals = [self.vol.scalar(comp, p) for p in phases]
        signed = self.vol.signed(comp, phases[0])
        log = self.log.isChecked() and not signed
        lo, hi = v3.levels(vals, signed, log, self.decades.value(), self.clip.value())
        cmap = self.cmap.currentText() if not signed else None          # a signed component: blue - white - red
        self._frames = [v3.colour(v, lo, hi, log, signed, self.opacity.value() / 100, self.gamma.value() / 10, cmap)
                        for v in vals]
        self.k %= len(self._frames)
        self._show(self.k)
        what = f"|{self.src.field}|" if comp == 'mag' else f"{self.src.field}{comp}"
        self.status.setText(f"{what}: {lo:.3g} to {hi:.3g} {self.src.unit}" + (' (log)' if log else '')
                            + f"; grid {' x '.join(map(str, self.vol.data.shape[:3]))}; drag to orbit, wheel to zoom, "
                              f"Ctrl + drag to pan")
        self._board()

    def _show(self, k):
        gl, zs = self.gl, self.zscale.value()
        rgba = self._frames[k]
        it = self.items.get('vol')
        if it is None:
            it = gl.GLVolumeItem(rgba, sliceDensity=2, smooth=True, glOptions='translucent')
            it.setDepthValue(10)                                    # last: its slices would hide the board
            self.view.addItem(it)
            self.items['vol'] = it
        else:
            it.setData(rgba)
        it.resetTransform()
        it.scale(self.vol.step[0], self.vol.step[1], self.vol.step[2] * zs)
        it.translate(self.vol.origin[0], self.vol.origin[1], self.vol.origin[2] * zs)

    def _board(self):
        """Copper (translucent orange), vias and the board's box, at the height scale."""
        gl, zs = self.gl, self.zscale.value()
        for key in [k for k in self.items if k != 'vol']:
            self.view.removeItem(self.items.pop(key))
        g = self.geometry
        if g is None:
            return
        if self.copper.isChecked():
            shown = self.cu_t.value()                               # on screen: the slab before the height scale
            t = shown / zs
            parts = v3.copper_solids(g, t) if shown > 0 else v3.copper_triangles(g)
            for i, (lay, z, verts, faces) in enumerate(parts):
                v = verts.copy()
                v[:, 2] *= zs
                if shown > 0:                                       # lit by the faces' angle to the view
                    md = gl.MeshData(vertexes=v.astype(np.float32), faces=faces.astype(np.uint32))
                    m = gl.GLMeshItem(meshdata=md, color=(0.85, 0.5, 0.2, 0.7), smooth=False, shader=copper_shader(),
                                      glOptions='translucent')
                else:
                    m = gl.GLMeshItem(vertexes=v.astype(np.float32), faces=faces.astype(np.uint32),
                                      color=(0.85, 0.5, 0.15, 0.55), smooth=False, glOptions='translucent')
                m.setDepthValue(-10 + i)
                self.view.addItem(m)
                self.items[f'cu{i}'] = m
            ol = v3.copper_outlines(g)                              # dark edges: the copper reads through the field
            if len(ol):
                ol = ol.copy()
                ol[:, 2] = (ol[:, 2] + 0.5 * t) * zs if shown > 0 else ol[:, 2] * zs   # on the slab's top
                ln = gl.GLLinePlotItem(pos=ol, mode='lines', color=(0.35, 0.18, 0.03, 1.0), width=1.5, antialias=True)
                ln.setDepthValue(-5)
                self.view.addItem(ln)
                self.items['outline'] = ln
        if self.boxc.isChecked():
            b = v3.board_box(g)
            if b is not None:
                x0, y0, x1, y1, z0, z1 = b
                c = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
                pts = []
                for z in (z0, z1):
                    pts += [np.column_stack([c, np.full(5, z * zs)])]
                edges = np.concatenate([np.repeat(pts[0], 2, 0)[1:-1], np.repeat(pts[1], 2, 0)[1:-1],
                                        np.stack([pts[0][:4], pts[1][:4]], 1).reshape(-1, 3)])
                ln = gl.GLLinePlotItem(pos=edges, mode='lines', color=(0.3, 0.3, 0.3, 0.8), width=1, antialias=True)
                ln.setDepthValue(-20)
                self.view.addItem(ln)
                self.items['box'] = ln
            vl = v3.via_lines(g)
            if len(vl):
                vl = vl.copy()
                vl[:, 2] *= zs
                ln = gl.GLLinePlotItem(pos=vl, mode='lines', color=(0.45, 0.3, 0.1, 1.0), width=2, antialias=True)
                ln.setDepthValue(-20)
                self.view.addItem(ln)
                self.items['vias'] = ln
        solver = self.mesh.currentData()
        if solver and solver in self.mesh_files:                    # a solver's mesh as lines over the board
            from .. import meshes
            b = v3.board_box(g)
            seg = meshes.segments(self.mesh_files[solver], box=b, planes=sorted(set((g.get('cu_z') or {}).values())))
            if len(seg):
                seg = seg.copy()
                seg[:, 2] *= zs
                colour = dict(openems=(0.1, 0.3, 0.7, 0.55), mom25d=(0.1, 0.5, 0.2, 0.7),
                              emerge=(0.55, 0.15, 0.55, 0.7)).get(solver, (0.2, 0.2, 0.2, 0.6))
                ln = gl.GLLinePlotItem(pos=seg.astype(np.float32), mode='lines', color=colour, width=1, antialias=True)
                ln.setDepthValue(-1)
                self.view.addItem(ln)
                self.items['mesh'] = ln

    def _cu_t_set(self, *_):
        self._cu_t_auto = False
        self._board()

    def _auto_cu_t(self):
        """The copper's shown thickness for a board: 0.6 % of its larger side (a 32 mm board: 0.2 mm)."""
        b = v3.board_box(self.geometry) if self.geometry is not None else None
        if b is None or not self._cu_t_auto:
            return
        self.cu_t.blockSignals(True)
        self.cu_t.setValue(round(0.006 * max(b[2] - b[0], b[3] - b[1]), 2))
        self.cu_t.blockSignals(False)

    def reset_camera(self):
        zs = self.zscale.value()
        if self.vol is not None:
            lo = self.vol.origin
            hi = lo + self.vol.step * (np.array(self.vol.data.shape[:3]) - 1)
        elif self.geometry is not None and v3.board_box(self.geometry) is not None:
            x0, y0, x1, y1, z0, z1 = v3.board_box(self.geometry)
            lo, hi = np.array([x0, y0, z0]), np.array([x1, y1, z1])
        else:
            return
        c = 0.5 * (lo + hi)
        from PySide6.QtGui import QVector3D
        self.view.setCameraPosition(pos=QVector3D(c[0], c[1], c[2] * zs), distance=1.6 * max(hi[:2] - lo[:2]),
                                    elevation=32, azimuth=-60)

    # ---- animation ----
    def _play(self, on):
        self.play_btn.setText('Pause' if on else 'Play')
        if on:
            self.timer.start(80)
        else:
            self.timer.stop()

    def _tick(self):
        if not self._frames:
            return
        if len(self._frames) > 1:                                   # the phases of a period
            self.k = (self.k + 1) % len(self._frames)
            self._show(self.k)
        elif self.src is not None and self.src.domain == 'TD' and len(self.src) > 1:
            self.frame.setCurrentIndex((self.frame.currentIndex() + 1) % len(self.src))

    def grab_rgb(self):
        """The view as it is drawn now: (h, w, 3) uint8."""
        img = self.view.grabFramebuffer().convertToFormat(QImage.Format_RGB888)
        a = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
        return np.ascontiguousarray(a[:, :3 * img.width()].reshape(img.height(), img.width(), 3))

    def render_frames(self, orbit=0.0):
        """The animation as RGB images from the current camera, at the view's size (orbit: degrees of azimuth
        turned over the animation)."""
        imgs = []
        frames = range(len(self._frames)) if len(self._frames) > 1 else range(1)
        az0 = self.view.opts['azimuth']
        from PySide6.QtWidgets import QApplication
        for n, k in enumerate(frames):
            self._show(k)
            if orbit:
                self.view.setCameraPosition(azimuth=az0 + orbit * n / max(len(frames), 1))
            self.view.update()
            QApplication.processEvents()                            # the new texture reaches the GPU on a repaint
            imgs.append(self.grab_rgb())
        self.view.setCameraPosition(azimuth=az0)
        self._show(self.k)
        return imgs

    def save(self, path, fps=12, orbit=0.0):
        from ..solvers.openems.fields import write_frames
        return write_frames(self.render_frames(orbit), path, fps)

    def save_dialog(self, ext):
        if self.src is None:
            return
        d = os.path.dirname(self.src.path)
        path, _ = QFileDialog.getSaveFileName(self, f"Save {ext.upper()}", os.path.join(d, f"{self.src.name}_3d.{ext}"),
                                              f"{ext.upper()} (*.{ext})")
        if path:
            try:
                self.save(path)
                self.status.setText(f"saved {path}")
            except Exception as e:
                QMessageBox.warning(self, 'Save', f"{type(e).__name__}: {e}")
