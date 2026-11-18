"""
The 3D field panel: a volume dump as a translucent voxel cloud over the board
(copper, vias, the board's box), in an OpenGL view you orbit (drag), zoom
(wheel) and pan (middle drag, or Ctrl + drag). A frequency-domain dump plays one
period of Re(F e^jphi), a time-domain one its frames; *Save GIF / MP4* writes
the animation as seen from the current camera. Needs PyOpenGL (pip install
PyOpenGL; the gui extra has it).
"""
import os
import sys
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
                               QComboBox, QCheckBox, QSlider, QPushButton,
                               QLabel, QDoubleSpinBox, QFileDialog, QMessageBox,
                               QSpinBox)

from . import volume3d as v3
from . import meshpick as mp

N_PHASE = 24
# the coloured frames kept as the animation plays; past it a frame is coloured
# as it is shown
FRAME_BYTES = 512e6
# the voxels (over every phase) the colour range is taken from on a large volume
# (R-D21)
LEVEL_SAMPLES = 4e6


class _Frames:
    """The animation's coloured frames, each coloured when it is first shown and
    kept while FRAME_BYTES allows (R-D21: every control step had coloured all 24
    phases at once on the GUI thread, 3.2 s and 1.4 GB at 256 voxels)."""

    def __init__(self, make, n, budget=FRAME_BYTES):
        self.make, self.n, self.kept, self.budget = make, int(n), {}, budget

    def __len__(self):
        return self.n

    def __getitem__(self, k):
        k = int(k) % self.n
        rgba = self.kept.get(k)
        if rgba is None:
            rgba = self.make(k)
            if (len(self.kept) + 1) * rgba.nbytes <= self.budget:
                self.kept[k] = rgba
        return rgba

    def __iter__(self):
        return (self[k] for k in range(self.n))


_GL_WHY = None


def gl_problem():
    """'' when the 3D view can draw here, else why not (cached):
    pyqtgraph.opengl needs OpenGL 2.1, and it draws through PyOpenGL, which must
    reach the context Qt made. A Windows VM without the GPU passed through has
    only OpenGL 1.1 (GDI Generic); Qt then falls back to its own software
    renderer (opengl32sw.dll), whose context PyOpenGL (the system's
    opengl32.dll) cannot reach, and every GL call fails. KICAD2EMS_GL=off turns
    the view off, =on skips this check."""
    global _GL_WHY
    if _GL_WHY is None:
        setting = os.environ.get('KICAD2EMS_GL', '').strip().lower()
        if setting in ('0', 'off', 'no', 'false'):
            _GL_WHY = 'turned off (KICAD2EMS_GL=off)'
        elif setting in ('1', 'on', 'yes', 'true'):
            _GL_WHY = ''
        else:
            try:
                _GL_WHY = _gl_probe()
            except Exception as e:  # a broken driver: no view, not no window
                _GL_WHY = f"OpenGL check failed: {type(e).__name__}: {e}"
    return _GL_WHY


def _gl_probe():
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext
    # tests: nothing is drawn
    if QGuiApplication.platformName() in ('offscreen', 'minimal'):
        return ''
    ctx = QOpenGLContext()
    if not ctx.create():
        return ("no OpenGL context: the display driver has no OpenGL (a VM "
                "needs the GPU passed through)")
    surf = QOffscreenSurface()
    surf.setFormat(ctx.format())
    surf.create()
    if not ctx.makeCurrent(surf):
        return "an OpenGL context could not be made current"
    try:
        fmt = ctx.format()
        have = f"{fmt.majorVersion()}.{fmt.minorVersion()}"
        try:
            from OpenGL import GL
            ver, ren = (GL.glGetString(GL.GL_VERSION),
                        GL.glGetString(GL.GL_RENDERER))
        except Exception as e:  # NullFunctionError, GLError: no context here
            ver = ren = None
            why = f" ({type(e).__name__})"
        else:
            why = ''
        if not ver:
            return (
                f"PyOpenGL cannot reach Qt's OpenGL {have} context{why}: Qt "
                "fell back to its software renderer, the display driver's "
                "OpenGL being too old (a VM needs the GPU passed through)"
            )
        dec = lambda b: b.decode(errors='replace') if isinstance(b, bytes
                                                                 ) else str(b)
        if (not ctx.isOpenGLES()
                and (fmt.majorVersion(), fmt.minorVersion()) < (2, 1)):
            return (f"OpenGL {dec(ver)} ({dec(ren)}): the 3D view needs OpenGL "
                    "2.1 (a VM needs the GPU passed through)")
        return ''
    finally:
        ctx.doneCurrent()


_COPPER_SHADER = None


def copper_shader():
    """A shader for the copper slabs: pyqtgraph's 'shaded' inputs, brighter:
    ambient 0.5, a key light from the upper front of the view (two-sided: a wall
    seen from inside the slab is lit too) and a small highlight, so the top
    faces stay copper-coloured and the walls and edges shade as the view
    turns."""
    global _COPPER_SHADER
    if _COPPER_SHADER is None:
        from pyqtgraph.opengl import shaders as S
        _COPPER_SHADER = S.ShaderProgram('kicad2ems_copper', [
            S.VertexShader("""
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
        """),
            S.FragmentShader("""
            #ifdef GL_ES
            precision mediump float;
            #endif
            varying vec4 v_color;
            varying vec3 v_normal;
            void main() {
                vec3 n = normalize(v_normal);
                vec3 l = normalize(vec3(-0.35, 0.55, 1.0));
                float d = abs(dot(n, l));
                float s = pow(max(dot(reflect(-l, n), vec3(0.0, 0.0, 1.0)),
                                  0.0), 24.0);
                vec3 rgb = v_color.rgb * (0.5 + 0.55 * d) + vec3(0.25) * s;
                gl_FragColor = vec4(min(rgb, vec3(1.0)), v_color.a);
            }
        """)
        ])
    return _COPPER_SHADER


def _view_class(gl):
    """GLViewWidget that reports each new OpenGL context (after the first paint
    is set up), and a context it could not set up (on_error(why); it then draws
    nothing).

    No exception may leave initializeGL or paintGL: when one leaves a Python
    override of QOpenGLWidget's, PySide 6.11 drops a reference to the class's
    methods, and a later garbage collection crashes on the freed function
    (pyqtgraph raises there when it finds no usable OpenGL, as on the offscreen
    platform the tests use)."""
    class View(gl.GLViewWidget):
        on_init = None
        on_error = None
        gl_error = ''

        def _failed(self, e):
            self.gl_error = f'{type(e).__name__}: {e}'
            if self.on_error is not None:
                QTimer.singleShot(0, lambda: self.on_error(self.gl_error))

        def initializeGL(self):
            try:
                super().initializeGL()
            except Exception as e:
                self._failed(e)
                return
            self.gl_error = ''
            if self.on_init is not None:
                QTimer.singleShot(0, self.on_init)

        def paintGL(self):
            if self.gl_error:
                return
            try:
                super().paintGL()
            except Exception as e:
                self._failed(e)
    return View


class FrameSlider(QWidget):
    """The frame (a time step or a frequency) as a slider with the frame's label
    beside it; the methods of the QComboBox it replaced (currentIndex,
    setCurrentIndex, addItems, clear, count, currentIndexChanged)."""
    from PySide6.QtCore import Signal
    currentIndexChanged = Signal(int)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal, minimum=0, maximum=0)
        self.label = QLabel('', minimumWidth=90)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.labels = []
        self.slider.valueChanged.connect(self._moved)

    def _moved(self, i):
        self.label.setText(self.labels[i] if 0 <= i < len(self.labels) else '')
        if not self.signalsBlocked():
            self.currentIndexChanged.emit(i)

    def clear(self):
        self.labels = []
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, 0)
        self.label.setText('')

    def addItems(self, labels):
        self.labels += list(labels)
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, max(0, len(self.labels) - 1))
        self._moved(self.slider.value(
        )) if not self.signalsBlocked() else self.label.setText(
            (self.labels[self.slider.value()] if self.labels else ''))
        self.slider.setEnabled(len(self.labels) > 1)

    def count(self):
        return len(self.labels)

    def currentText(self):
        i = self.currentIndex()
        return self.labels[i] if i >= 0 else ''

    def currentIndex(self):
        return self.slider.value() if self.labels else -1

    def setCurrentIndex(self, i):
        if not self.labels:
            return
        i = max(0, min(int(i), len(self.labels) - 1))
        if i == self.slider.value():
            self.label.setText(self.labels[i])
            return
        self.slider.blockSignals(self.signalsBlocked())
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self.label.setText(self.labels[i])


class VolumeTab(QWidget):
    def __init__(self, win=None):
        super().__init__()
        self.win, self.srcs, self.src, self.vol, self.items = (win, [], None,
                                                               None, {})
        # <name>_board.json's geometry; meshes.find()
        self.board, self.mesh_files = None, {}
        # (antenna results, sphere) of the folder
        self.ant = None
        self._frames, self.k = [], 0
        # the line model (overlay.load_lines), a section
        self.lines, self.sel = None, None
        lay = QHBoxLayout(self)
        try:
            import pyqtgraph.opengl as gl
            self.gl, why = gl, ''
        except Exception as e:  # ImportError: no PyOpenGL
            self.gl, why = None, f"{type(e).__name__}: {e}"
        problem = gl_problem() if self.gl is not None else ''
        if self.gl is None or problem:
            msg = (
                f"The 3D field view is off: {problem}" if problem else
                "The 3D field view needs PyOpenGL: pip install PyOpenGL (into "
                f"the Python of the GUI)\n({why})"
            )
            if problem:
                print(f"kicad2ems-gui: {msg}", file=sys.stderr)
            self.gl = None
            lay.addWidget(
                QLabel(msg, wordWrap=True, alignment=Qt.AlignCenter,
                       styleSheet='color: gray'))
            return
        side = QVBoxLayout()
        self.dump = QComboBox()
        self.frame = FrameSlider()
        self.comp = QComboBox()
        self.comp.addItems(['|F|', 'Fx', 'Fy', 'Fz'])
        self.cmap = QComboBox()
        self.cmap.addItems(['turbo', 'inferno', 'plasma', 'viridis', 'hot'])
        self.envelope = QCheckBox('envelope (no animation)')
        self.whole = QCheckBox(
            'one colour range for every frame', toolTip=
            ("the colour range over the whole dump (every time step, or every "
             "frequency), so a field that grows and decays shows it; off: each "
             "frame its own range (a frequency domain dump's period always "
             "shares one)"
             ))
        self._ranges = {}  # (dump, settings) -> the whole dump's top
        self.log = QCheckBox('log scale', checked=True)
        self.decades = QDoubleSpinBox(minimum=0.5, maximum=5, singleStep=0.5,
                                      value=1.5)
        self.opacity = QSlider(Qt.Horizontal, minimum=5, maximum=100, value=80)
        # gamma x 10
        self.gamma = QSlider(Qt.Horizontal, minimum=5, maximum=40, value=12)
        self.zscale = QDoubleSpinBox(minimum=1, maximum=20, value=2,
                                     suffix=' x')
        self.clip = QDoubleSpinBox(
            minimum=90, maximum=100, decimals=2, singleStep=0.1, value=99.5,
            suffix=' %', toolTip=
            'the top of the colour range: this percentile of the values (a '
            'port gap takes it all at 100 %)')
        self.res = QSpinBox(minimum=48, maximum=256, value=128,
                            suffix=' voxels')
        self.copper = QCheckBox('copper', checked=True)
        self.cu_t = QDoubleSpinBox(
            minimum=0, maximum=5, decimals=2, singleStep=0.05,
            specialValueText='flat', suffix=' mm',
            toolTip='the copper drawn as shaded slabs this thick (on screen; '
            'real copper is ~35 um): its edges catch the light; 0: flat, '
            'unshaded')
        self._cu_t_auto = True
        self.boxc = QCheckBox('board box and vias', checked=True)
        self.mesh = QComboBox(
            toolTip=
            "a solver's discretisation of the board (written by a preview or a "
            "run): openEMS's grid lines, mom25d's triangles")
        self.solved = QCheckBox(
            'solved mesh',
            styleSheet=f'QCheckBox {{ color: {mp.SOLVED_COLOUR}; }}')
        self.far = QComboBox(
            toolTip=
            "the far-field gain pattern (antenna mode) at a recorded "
            "frequency, over the board: its distance from the board's centre "
            "is the gain, 30 dB of range"
        )
        self.far_size = QDoubleSpinBox(minimum=0.2, maximum=5, singleStep=0.1,
                                       value=0.5, suffix=' x board')
        self.far_up = QDoubleSpinBox(
            minimum=-500, maximum=500, singleStep=1, suffix=' mm', toolTip=
            ("how far above the board's top surface the pattern's centre is "
             "drawn (on screen)"
             ))
        from .steering import SteeringBar
        # the Results page's, linked (app.py)
        self.steer = SteeringBar(compact=True)
        self.steer.steered.connect(lambda *_: self._far_on())
        # the controls: one folding section per subject, shown when the folder
        # has something for it (a field dump, a far field), in a side panel the
        # splitter can narrow or close
        from .panels import Section
        self.play_btn = QPushButton('Play', checkable=True, toggled=self._play)
        self.fps = QSpinBox(
            minimum=1,
            maximum=60,
            value=12,
            toolTip='frames per second of Play and of saved '
            # as the 2D's
            'animations',
            valueChanged=lambda v: self.timer.setInterval(int(1000 / v)))
        self.every = QSpinBox(
            minimum=1, maximum=1000,
            toolTip='Time domain: every n-th time step, for Play and saved '
            'animations (set for a long dump to about 120 frames)')
        saves = QHBoxLayout()
        for ext in ('gif', 'mp4'):
            saves.addWidget(
                QPushButton(f"Save {ext.upper()}",
                            clicked=lambda _=False, e=ext: self.save_dialog(e)))
        self.lines_on = QCheckBox(
            'line model (coloured by Z0)', checked=True,
            toggled=lambda *_: self._board(), toolTip=
            "the line model's sections on their layers; pick one in the "
            "section list"
        )
        self.relief = QDoubleSpinBox(
            minimum=0, maximum=20, decimals=2, singleStep=0.1, suffix=' mm',
            specialValueText='flat', valueChanged=self._relief_set, toolTip=
            ("the line model's tallest section on screen (the highest Z0); the "
             "lowest Z0 stands a quarter as high, so each step in height is a "
             "step in impedance. 0: flat"))
        self._relief_auto = True
        self.parts_on = QCheckBox(
            'Components', checked=True, toggled=lambda *_: self._board(),
            toolTip=
            ('package silhouettes and lumped-part chips; heights may be '
             'approximate. resistors green, capacitors cyan, inductors pink, '
             'with their value'))
        groups = (('Field', True,
                   (('Dump', self.dump), ('Frame', self.frame),
                    ('Show', self.comp), ('Colours',
                                          self.cmap), ('', self.envelope),
                    ('', self.whole), ('', self.log), ('Decades', self.decades),
                    ('Range top', self.clip), ('Opacity', self.opacity),
                    ('Fade (gamma)', self.gamma), ('Resolution',
                                                   self.res), ('fps', self.fps),
                    ('Every n-th frame',
                     self.every), ('', self.play_btn), ('', saves))),
                  ('Board', True,
                   (('Height scale', self.zscale), ('', self.copper),
                    ('Copper shown', self.cu_t), ('', self.boxc),
                    ('Mesh', self.mesh), ('', self.solved), ('', self.lines_on),
                    ('Z0 relief', self.relief), ('', self.parts_on))),
                  ('Far field', True,
                   (('Pattern', self.far), ('Size', self.far_size),
                    ('Height', self.far_up), ('', self.steer))))
        self.sections = {}
        for title, open_, rows in groups:
            box = QWidget()
            f = QFormLayout(box)
            f.setContentsMargins(0, 0, 0, 0)
            f.setRowWrapPolicy(QFormLayout.WrapLongRows)
            f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            for label, w in rows:
                f.addRow(label, w)
            self.sections[title] = Section(title, box, expanded=open_)
            side.addWidget(self.sections[title])
        side.insertWidget(0, QPushButton('Reset view',
                                         clicked=self.reset_camera))
        self.status = QLabel('', wordWrap=True, styleSheet='color: gray')
        side.addWidget(self.status)
        side.addStretch(1)
        sw = QWidget()
        sw.setLayout(side)
        from PySide6.QtWidgets import QScrollArea, QSplitter
        scroll = QScrollArea(widgetResizable=True, minimumWidth=200,
                             horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff)
        scroll.setWidget(sw)
        self.view = _view_class(self.gl)()
        self.view.on_init = self._new_context
        self.view.on_error = lambda why: self.status.setText(
            f'The 3D view could not start OpenGL ({why}): '
            'the Plan view still works.')
        self.view.setBackgroundColor('w')
        self.split = QSplitter(objectName='viewer3d')
        self.split.addWidget(scroll)
        self.split.addWidget(self.view)
        self.split.setStretchFactor(1, 1)
        self.split.setCollapsible(1, False)
        self.split.setSizes([270, 900])
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.split)
        self._fit_sections()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.dump.currentIndexChanged.connect(self._pick)
        self.frame.currentIndexChanged.connect(self._rebuild)
        self.res.valueChanged.connect(self._rebuild)
        for w in (self.comp, self.cmap):
            w.currentIndexChanged.connect(self._recolour)
        for w in (self.envelope, self.whole, self.log, self.copper, self.boxc):
            w.toggled.connect(self._recolour)
        for w in (self.decades, self.clip, self.opacity, self.gamma):
            w.valueChanged.connect(self._recolour)
        self.cu_t.valueChanged.connect(self._cu_t_set)
        self.mesh.currentIndexChanged.connect(
            lambda *_: (self._solved_state(), self._board()))
        self.solved.toggled.connect(lambda *_: self._board())
        self.far.currentIndexChanged.connect(
            lambda *_: (self._board(), self.reset_camera()))
        self.far_size.valueChanged.connect(lambda *_: self._board())
        self.far_up.valueChanged.connect(lambda *_:
                                         (self._board(), self.reset_camera()))
        self.zscale.valueChanged.connect(self._rescale)

    def _new_context(self):
        """The view has a new OpenGL context (first shown, or moved: a dock
        tabbed, floated or docked again, which re-creates it): items made in the
        old one hold its shaders and buffers, so make them anew."""
        for it in list(self.view.items):
            self.view.removeItem(it)
        self.items = {}
        if self._frames:
            self._show(self.k)
            self._board()

    @property
    def geometry(self):
        """The board drawn: the dump's geometry, else the folder's
        <name>_board.json."""
        return self.src.geometry if self.src is not None else self.board

    # ---- data ----
    def load(self, out_dir):
        """A results folder: its volume dumps, its board and the solvers' meshes
        (a preview writes those). False: nothing to show, or no OpenGL."""
        if self.gl is None:
            return False
        import glob
        from kicad2ems.core.fields.frames import find_dumps
        from kicad2ems.core import meshes
        from .antennatab import find_antenna, load_sphere
        self.srcs = [
            s for s in find_dumps(out_dir) if v3.is_volume(s) or v3.is_plane(s)
        ]
        boards = sorted(glob.glob(os.path.join(out_dir, '*_board.json')),
                        key=os.path.getmtime)
        try:
            self.board = v3.geometry_from_board(boards[-1]) if boards else None
        except (OSError, ValueError, KeyError):
            self.board = None
        self.mesh_files = meshes.find(out_dir)
        # its far-field pattern: the newest antenna result with a sphere, into
        # the Far field choice
        self.ant = None
        try:
            found = find_antenna(out_dir)
            if found and found[1]:
                self.ant = (found[0], load_sphere(found[1]))
        except Exception:  # a half-written or missing file: no pattern
            self.ant = None
        with QSignalBlocker(self.far):
            self.far.clear()
            self.far.addItem('off', None)
            for i, f in enumerate(
                    self.ant[1]['freq'] if self.ant is not None else []):
                self.far.addItem(f"gain at {f / 1e9:.4g} GHz", i)
        keep = self.mesh.currentData()
        with QSignalBlocker(self.mesh):
            self.mesh.clear()
            self.mesh.addItem('none', None)
            for solver in mp.solvers(self.mesh_files):
                path = mp.shown(self.mesh_files, solver)
                self.mesh.addItem(
                    f"{meshes.LABELS.get(solver, solver)}: "
                    f"{meshes.counts(path)}",
                    solver)
            self.mesh.setCurrentIndex(max(self.mesh.findData(keep), 0))
        self._solved_state()
        with QSignalBlocker(self.dump):
            self.dump.clear()
            self.dump.addItems([s.label for s in self.srcs])
        if self.srcs:
            self._pick()
        else:  # the board alone, and its meshes
            self.src, self.vol, self._frames = None, None, []
            for key in list(self.items):
                self.view.removeItem(self.items.pop(key))
            self._auto_cu_t()
            self._board()
            self.reset_camera()
            self.status.setText('no volume dumps: the board' + (
                ' and the solvers\' meshes' if self.mesh_files else ''))
        self._fit_sections()
        return bool(self.srcs or (self.board and
                                  (self.mesh_files or self.ant or self.lines)))

    def _pick(self, *_):
        i = self.dump.currentIndex()
        if i < 0 or i >= len(self.srcs):
            return
        # a drop, a rise, a temperature:
        if self.srcs[i].field == 'V' or getattr(self.srcs[i], 'spans', False):
            self.log.setChecked(False)                              # linear
        self.src = self.srcs[i].refresh()
        with QSignalBlocker(self.frame):
            self.frame.clear()
            self.frame.addItems(
                [self.src.frame_label(k) for k in range(len(self.src))])
            self.frame.setCurrentIndex(0 if self.src.domain ==
                                       'FD' else max(0,
                                                     len(self.src) - 1))
        self.envelope.setEnabled(self.src.domain == 'FD')
        # as the 2D view: a long dump about 120 frames
        n, fd = len(self.src), self.src.domain == 'FD'
        self.every.setEnabled(not fd)
        if fd:
            self.every.setValue(1)
        elif n > 200 and self.every.value() == 1:
            self.every.setValue(max(1, n // 120))
        self._auto_cu_t()
        self._board()
        self._rebuild()
        self.reset_camera()

    def _rebuild(self, *_):
        if self.src is None or self.frame.currentIndex() < 0:
            return
        self.vol = v3.Volume(self.src, self.frame.currentIndex(),
                             n=self.res.value())
        self._recolour()

    def _whole_top(self, comp, phases):
        """The top of the colour range over every frame of the dump (each
        frame's `Range top` percentile, the largest), at the current resolution;
        kept per dump and settings."""
        from PySide6.QtWidgets import QApplication
        key = (self.src.path, self.src.name, comp, len(phases),
               self.clip.value(), self.res.value())
        if key not in self._ranges:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                tops = []
                for k in range(len(self.src)):
                    vol = (self.vol if k == self.frame.currentIndex() else
                           v3.Volume(self.src, k, n=self.res.value()))
                    tops.append(
                        v3.levels([vol.scalar(comp, p) for p in phases], True,
                                  False, clip=self.clip.value())[1])
                self._ranges[key] = max(tops)
            finally:
                QApplication.restoreOverrideCursor()
        return self._ranges[key]

    def _rescale(self, *_):
        """The height scale changed: the frames placed again, not coloured
        again."""
        if self.vol is not None and self._frames:
            self._show(self.k)
            self._board()

    def _recolour(self, *_):
        """The animation's frames at the current settings (one colour range for
        all), each coloured when shown."""
        if self.vol is None:
            return
        comp = v3.COMPONENTS[self.comp.currentIndex()]
        # the frames: a period's phases (frequency domain), else None (one
        # frame)
        fd = self.src.domain == 'FD' and not self.envelope.isChecked()
        phases = (list(np.linspace(0, 2 * np.pi, N_PHASE, endpoint=False))
                  if fd else [None])
        # the values the colour range is taken from: every phase's, on a large
        # volume from a regular subsample of its voxels (about LEVEL_SAMPLES
        # over the phases: a percentile of them is the whole's to a few parts in
        # a thousand)
        n = int(np.prod(self.vol.data.shape[:3]))
        step = (int(np.ceil((n * len(phases) / LEVEL_SAMPLES)**(1 / 3))) if n
                * len(phases) > 2 * LEVEL_SAMPLES else 1)
        vals = [(v3.scalar_of(self.vol.data[::step, ::step, ::step],
                              self.vol.complex, comp, p)
                 if step > 1 else self.vol.scalar(comp, p))
                for p in phases]
        signed = self.vol.signed(comp, phases[0])
        log = self.log.isChecked() and not signed
        # a temperature: its own
        span = (bool(getattr(self.src, 'spans', False)) and comp == 'mag'
                and not signed)
        # values' range
        lo, hi = v3.levels(vals, signed, log, self.decades.value(),
                           self.clip.value(), span=span)
        del vals
        whole = self.whole.isChecked() and len(self.src) > 1
        if whole:  # the top over every frame of the dump
            top = self._whole_top(comp, phases)
            lo, hi = ((-top, top) if signed else
                      ((top * 10**-self.decades.value(), top) if log else
                       (0.0, top)))
        # a signed component: blue - white - red
        cmap = self.cmap.currentText() if not signed else None
        vol, paint = (self.vol,
                      dict(lo=lo, hi=hi, log=log, signed=signed,
                           opacity=self.opacity.value() / 100,
                           gamma=self.gamma.value() / 10, cmap=cmap,
                           floor=0.55 if span else 0.0))
        self._frames = _Frames(
            lambda k: v3.colour(vol.scalar(comp, phases[k]), **paint),
            len(phases))
        self.k %= len(self._frames)
        self._show(self.k)
        what = ((self.src.field if span else f"|{self.src.field}|")
                if comp == 'mag' else f"{self.src.field}{comp}")
        self.status.setText(
            f"{what}: {lo:.3g} to {hi:.3g} {self.src.unit}"
            + (' (log)' if log else '') + (' over every frame' if whole else '')
            +
            f"; grid {' x '.join(map(str, self.vol.data.shape[:3]))}; drag to "
            "orbit, wheel to zoom, Ctrl + drag to pan")
        self._board()

    def _put(self, key, item, depth):
        """An item into the view at a depth, kept under key (what is drawn again
        takes it out by its key)."""
        item.setDepthValue(depth)
        self.view.addItem(item)
        self.items[key] = item
        return item

    def _dump_item(self, cls, data, **kw):
        """The dump's item ('vol': a voxel cloud, or a plane's image) showing
        this frame: made anew when the one there is of the other kind. Drawn
        last: its slices would hide the board."""
        it = self.items.get('vol')
        if isinstance(it, cls):
            it.setData(data)
            return it
        if it is not None:
            self.view.removeItem(self.items.pop('vol'))
        return self._put('vol', cls(data, glOptions='translucent', **kw), 10)

    def _show(self, k):
        zs, rgba = self.zscale.value(), self._frames[k]
        if self.vol.plane is not None:  # a plane dump: an image in place
            self._show_plane(rgba)
            return
        it = self._dump_item(self.gl.GLVolumeItem, rgba, sliceDensity=2,
                             smooth=True)
        it.resetTransform()
        it.scale(self.vol.step[0], self.vol.step[1], self.vol.step[2] * zs)
        # voxel k spans k..k+1: its centre on sample k
        o = self.vol.origin - 0.5 * self.vol.step
        it.translate(o[0], o[1], o[2] * zs)

    def _show_plane(self, rgba):
        """A plane dump's frame as an image at its position (normal x, y or z),
        at the height scale."""
        from PySide6.QtGui import QMatrix4x4
        zs, n = self.zscale.value(), self.vol.plane
        a, b = [k for k in range(3) if k != n]
        # (n_a, n_b, 4)
        it = self._dump_item(self.gl.GLImageItem,
                             np.ascontiguousarray(np.take(rgba, 0, axis=n)))
        sc = np.array([1.0, 1.0, zs])
        eu, ev = np.zeros(3), np.zeros(3)
        eu[a], ev[b] = self.vol.step[a] * sc[a], self.vol.step[b] * sc[b]
        en = np.zeros(3)
        en[n] = 1.0
        # pixel k spans k..k+1: its centre on sample k
        o = self.vol.origin * sc - 0.5 * (eu + ev)
        cu = (self.geometry or {}).get('cu_z') or {}
        layer = (getattr(self.src, 'meta', None) or {}).get('label')
        # a layer dump (E@F.Cu): at its copper layer;
        z = cu.get(layer) if n == 2 else None
        # openEMS samples it a cell into the dielectric
        if z is None and n == 2:
            z = next(
                (c for c in cu.values() if abs(self.vol.origin[2] - c) < 1e-3),
                None)
        # on top of what is drawn on its layer: the copper's slab, the line
        # model's slabs
        if z is not None:
            top = 0.5 * self.cu_t.value() if self.copper.isChecked() else 0.0
            if (self.lines and self.lines_on.isChecked() and any(
                    e.get('z') is not None and abs(e['z'] - z) < 1e-3
                    and e.get('kind') != 'coupling'
                    for e in self.lines.get('elements', []))):
                top += 0.005 + max(self.relief.value(), 0.01)
            o[2] = z * zs + top + 0.01
        it.setTransform(
            QMatrix4x4(eu[0], ev[0], en[0], o[0], eu[1], ev[1], en[1], o[1],
                       eu[2], ev[2], en[2], o[2], 0, 0, 0, 1))

    def _far_on(self):
        """Steering from this view with no pattern shown: the pattern
        frequency's."""
        if (self.far.currentData() is None and self.ant is not None
                and self.far.count() > 1):
            f0 = self.ant[0].get('f_pattern', self.ant[1]['freq'][0])
            self.far.setCurrentIndex(
                1
                + int(np.argmin(np.abs(np.asarray(self.ant[1]['freq']) - f0))))

    def set_far(self, res, sph):
        """A new pattern of the folder's antenna (a steered drive): only the far
        field redrawn."""
        if (self.gl is None or self.ant is None
                or len(sph['freq']) != len(self.ant[1]['freq'])):
            return
        self.ant = (res, sph)
        if 'far' in self.items:
            self.view.removeItem(self.items.pop('far'))
        if self.geometry is not None:
            self._far_field(self.geometry, self.zscale.value())

    def _far_field(self, g, zs):
        """The far-field pattern over the board's centre (`Far-field height`
        above its top), the peak `Far-field size` x the board's larger side
        away."""
        i = self.far.currentData()
        if i is None or self.ant is None:
            return
        res, sph = self.ant
        b = v3.board_box(g)
        if b is None:
            return
        x0, y0, x1, y1, z0, z1 = b
        f = float(sph['freq'][i])
        row = min(res['rows'], key=lambda r: abs(r['f'] - f))
        v, faces, col, peak = v3.pattern_mesh(
            sph, i, row['eff'], (0.5 * (x0 + x1), 0.5 *
                                 (y0 + y1), z1 * zs + self.far_up.value()),
            self.far_size.value() * max(x1 - x0, y1 - y0))
        col[:, 3] = 1.0  # opaque: a translucent surface sorts badly
        self._put(
            'far',
            self.gl.GLMeshItem(vertexes=v.astype(np.float32),
                               faces=faces.astype(np.uint32),
                               vertexColors=col.astype(np.float32), smooth=True,
                               glOptions='opaque'), 5)
        self.status.setText(
            f"far field at {f / 1e9:.4g} GHz: peak gain {peak:.2f} dBi (30 dB "
            "of range); drag to orbit, wheel to zoom, Ctrl + drag to pan")

    def _solved_state(self):
        """The "solved mesh" tick box: usable where a run of the solver picked
        wrote the mesh it solved on."""
        from kicad2ems.core import meshes
        solver = self.mesh.currentData()
        self.solved.setEnabled(mp.solved(self.mesh_files, solver) is not None)
        self.solved.setToolTip(mp.tip(self.mesh_files, solver, meshes.counts))

    def _memo(self, objs, key, make):
        """make() once for these geometry objects (compared by identity) and
        settings: the board is rebuilt on every change of a view setting,
        several times while a folder loads, and a large board's copper and parts
        took seconds each time (a minute and more in all, in the window's own
        thread)."""
        cache = self.__dict__.setdefault('_meshes', {})
        k = tuple(id(o) for o in objs) + tuple(key)
        hit = cache.get(k)
        if hit is not None and all(a is b for a, b in zip(hit[0], objs)):
            return hit[1]
        if len(cache) > 20000:
            cache.clear()
        made = make()
        cache[k] = (tuple(objs), made)
        return made

    def _solid(self, key, verts, faces, color, depth, opaque=True):
        """Shaded slabs (copper_shader) of these triangles into the view."""
        md = self.gl.MeshData(vertexes=verts.astype(np.float32),
                              faces=faces.astype(np.uint32))
        self._put(
            key,
            self.gl.GLMeshItem(
                meshdata=md, color=color, smooth=False, shader=copper_shader(),
                glOptions=('opaque' if opaque else 'translucent')), depth)

    def _lines(self, key, pos, color, width, depth):
        """Line segments (pairs of points) into the view."""
        self._put(
            key,
            self.gl.GLLinePlotItem(pos=pos, mode='lines', color=color,
                                   width=width, antialias=True), depth)

    def _label(self, key, pos, text):
        """A label on top: never hidden behind its chip."""
        from OpenGL import GL
        t = self.gl.GLTextItem(pos=pos, text=text, color=(20, 20, 20, 255))
        t.setGLOptions({
            GL.GL_DEPTH_TEST: False, GL.GL_BLEND: True,
            'glBlendFunc': (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        })
        self._put(key, t, 20)

    def _has_parts(self):
        """Whether the board has parts to draw: lumped parts' chips, components'
        packages."""
        return bool((self.board or {}).get('parts')
                    or (self.board or self.geometry or {}).get('components'))

    def _board(self):
        """Copper (translucent orange), vias and the board's box, at the height
        scale."""
        gl, zs = self.gl, self.zscale.value()
        for key in [k for k in self.items if k != 'vol']:
            self.view.removeItem(self.items.pop(key))
        g = self.geometry
        if g is None:
            return
        if self.copper.isChecked():
            # on screen: the slab before the height scale
            shown = self.cu_t.value()
            t = shown / zs
            groups = [(g, (0.85, .5, .2, .7))]
            if 'active_copper' in g:
                groups = [(dict(g, copper=g.get('inactive_copper',
                                                {})), (.55, .59, .63, .22)),
                          (dict(g, copper=g['active_copper']), (.10, .72, .80,
                                                                .65))]
            i = 0
            for geo, rgba in groups:
                parts = self._memo((geo['copper'], g.get('cu_z')),
                                   ('cu', shown > 0, t),
                                   lambda: (v3.copper_solids(geo, t)
                                            if shown > 0
                                            else v3.copper_triangles(geo)))
                for lay, z, verts, faces in parts:
                    v = verts.copy()
                    v[:, 2] *= zs
                    if shown > 0:
                        self._solid(f'cu{i}', v, faces, rgba, -10 + i,
                                    opaque=False)
                    else:
                        self._put(
                            f'cu{i}',
                            gl.GLMeshItem(vertexes=v.astype(np.float32),
                                          faces=faces.astype(np.uint32),
                                          color=rgba, smooth=False,
                                          glOptions='translucent'), -10 + i)
                    i += 1
            # dark edges: the copper reads through the field
            ol = v3.copper_outlines(g)
            if len(ol):
                ol = ol.copy()
                # on the slab's top
                ol[:, 2] = ((ol[:, 2] + 0.5 * t) * zs if shown > 0 else ol[:, 2]
                            * zs)
                self._lines('outline', ol, (0.35, 0.18, 0.03, 1.0), 1.5, -5)
        if self.boxc.isChecked():
            b = v3.board_box(g)
            if b is not None:
                x0, y0, x1, y1, z0, z1 = b
                c = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
                pts = [
                    np.column_stack([c, np.full(5, z * zs)]) for z in (z0, z1)
                ]
                edges = np.concatenate([
                    np.repeat(pts[0], 2, 0)[1:-1],
                    np.repeat(pts[1], 2, 0)[1:-1],
                    np.stack([pts[0][:4], pts[1][:4]], 1).reshape(-1, 3)
                ])
                self._lines('box', edges, (0.3, 0.3, 0.3, 0.8), 1, -20)
            vl = v3.via_lines(g)
            if len(vl):
                vl = vl.copy()
                vl[:, 2] *= zs
                self._lines('vias', vl, (0.45, 0.3, 0.1, 1.0), 2, -20)
        solver = self.mesh.currentData()
        # a solver's mesh as lines over the board
        if solver and mp.shown(self.mesh_files, solver):
            from kicad2ems.core import meshes
            b, planes = (v3.board_box(g),
                         sorted(set((g.get('cu_z') or {}).values())))
            drawn = [('mesh', mp.shown(self.mesh_files, solver), 1,
                      dict(openems=(0.1, 0.3, 0.7, 0.55),
                           mom25d=(0.1, 0.5, 0.2,
                                   0.7)).get(solver, (0.2, 0.2, 0.2, 0.6)))]
            # the run's, in pink over it
            if self.solved.isChecked() and mp.solved(self.mesh_files, solver):
                drawn.append(
                    ('mesh solved', mp.solved(self.mesh_files,
                                              solver), 1.5, mp.SOLVED_RGBA))
            for key, path, width, colour in drawn:
                seg = meshes.segments(path, box=b, planes=planes)
                if len(seg):
                    seg = seg.copy()
                    seg[:, 2] *= zs
                    self._lines(key, seg.astype(np.float32), colour, width,
                                -1 if key == 'mesh' else 0)
        self._line_model(zs)
        self._parts(zs)
        self._far_field(g, zs)
        # a layer dump: on top of them
        if self.vol is not None and self.vol.plane is not None and self._frames:
            self._show_plane(self._frames[self.k])

    def _line_model(self, zs):
        """The line model's sections as slabs at their copper width, standing on
        the copper, coloured by Z0 against the ports' impedance and as tall as
        their Z0 is high (overlay.relief: a quarter of `Z0 relief` at the
        model's lowest Z0, all of it at the highest), so every change of
        impedance is a step; dark rims on their tops; the selected one dark. All
        on screen: the height scale does not stretch them."""
        if not (self.lines and self.lines_on.isChecked()):
            return
        from kicad2ems.core import overlay as ov
        ref, half = ov.reference(self.lines), ov.span(self.lines)
        zs_all = [
            e['z0']
            for e in self.lines['elements']
            if e.get('z0') and e.get('kind') != 'coupling'
        ]
        lo, hi = (min(zs_all), max(zs_all)) if zs_all else (None, None)
        full = max(self.relief.value(), 0.01)  # on screen, mm
        base = 0.5 * self.cu_t.value() if self.copper.isChecked() else 0.0
        chips = self._has_parts() and self.parts_on.isChecked()
        rims = []
        for e in self.lines['elements']:
            if e.get('z') is None or not ov.drawn(e):
                continue
            # the chip is drawn: no strip inside it
            if e.get('kind') == 'part' and chips:
                continue
            g = ov.section_polygon(e)
            if g.is_empty:
                continue
            h = full * ov.relief(e.get('z0'), lo, hi)
            # just above the copper's top: no z-fighting
            bottom = e['z'] * zs + base + 0.005
            parts = [g] if g.geom_type == 'Polygon' else list(g.geoms)
            polys = [[list(p.exterior.coords)]
                     + [list(r.coords) for r in p.interiors] for p in parts]
            solids = v3.copper_solids(
                dict(copper={'s': polys}, cu_z={'s': bottom + 0.5 * h}), h)
            if not solids:
                continue
            _, _, verts, faces = solids[0]
            rgb = ((0.12, 0.12, 0.12) if e['id'] == self.sel else ov.z0_colour(
                e.get('z0'), ref, half))
            self._solid(f"ln{e['id']}", verts, faces, (*rgb, 1.0), 2)
            for p in parts:  # the top's rim, as segment pairs
                r = np.asarray(p.exterior.coords)
                top = np.column_stack([r, np.full(len(r), bottom + h + 0.002)])
                rims.append(np.repeat(top, 2, 0)[1:-1])
        if rims:
            self._lines('ln_rims',
                        np.concatenate(rims).astype(np.float32),
                        (0.1, 0.1, 0.1, 0.9), 1.2, 3)

    def _parts(self, zs):
        """The lumped parts as their chips (volume3d.part_solids): coloured
        body, metal end caps, a label; the components' packages (an active one
        labelled)."""
        g = self.board or self.geometry
        if not self.parts_on.isChecked() or g is None:
            return
        lift = 0.5 * self.cu_t.value() if self.copper.isChecked() else 0.0
        for k, c in enumerate(g.get('components', [])):
            made = self._memo(
                (c, g.get('cu_z')), ('component', zs, lift),
                lambda: v3.component_solids(c, g.get('cu_z', {}), zs, lift))
            for j, (rgba, verts, faces) in enumerate(made):
                self._solid(f'component{k}_{j}', verts, faces, rgba, 4)
            if c.get('active'):
                coords = np.asarray(c['polygon']['coordinates'][0])
                xy = (coords.min(0) + coords.max(0)) / 2
                z = (c['z'] * zs +
                     (-c['height'] - .2 if c.get('side') == 'B' else c['height']
                      + .2))
                self._label(f'component{k}_label', (xy[0], xy[1], z), c['ref'])
        for k, p in enumerate((self.board or {}).get('parts') or []):
            made = v3.part_solids(p, g.get('cu_z') or {}, zs, lift)
            if made is None:
                continue
            solids, (lx, ly, lz) = made
            for j, (role, rgb, verts, faces) in enumerate(solids):
                self._solid(f"part{k}_{j}", verts, faces, (*rgb, 1.0), 4)
            self._label(f"part{k}_label", (lx, ly, lz),
                        f"{p['ref']} {_value(p, main=True)}".strip())

    def set_lines(self, lines):
        """The line model of the folder (overlay.load_lines), or None."""
        self.lines, self.sel = lines, None
        if self.gl is not None:
            self._fit_sections()
            self._board()

    def select(self, i):
        """Mark a section of the line model (None: none)."""
        self.sel = i
        if self.gl is not None:
            self._board()

    def _fit_sections(self):
        """The control sections the folder has something for: the field's with a
        dump, the far field's with an antenna result, the line model's tick box
        with a line model."""
        self.sections['Field'].setVisible(bool(self.srcs))
        self.sections['Far field'].setVisible(self.ant is not None)
        self.lines_on.setVisible(self.lines is not None)
        self.relief.setVisible(self.lines is not None)
        self.parts_on.setVisible(self._has_parts())
        lab = (self.relief.parentWidget().layout().labelForField(self.relief)
               if self.relief.parentWidget() else None)
        if lab is not None:
            lab.setVisible(self.lines is not None)

    def _relief_set(self, *_):
        self._relief_auto = False
        self._board()

    def _cu_t_set(self, *_):
        self._cu_t_auto = False
        self._board()

    def _auto_cu_t(self):
        """The copper's shown thickness for a board, 0.6 % of its larger side (a
        32 mm board: 0.2 mm), and the line model's relief, 1.5 %; each unless
        set by hand."""
        b = v3.board_box(self.geometry) if self.geometry is not None else None
        if b is None:
            return
        for auto, w, share in ((self._cu_t_auto, self.cu_t, 0.006),
                               (self._relief_auto, self.relief, 0.015)):
            if auto:
                with QSignalBlocker(w):
                    w.setValue(round(share * max(b[2] - b[0], b[3] - b[1]), 2))

    def reset_camera(self):
        zs = self.zscale.value()
        if self.vol is not None:
            lo = self.vol.origin
            hi = lo + self.vol.step * (np.array(self.vol.data.shape[:3]) - 1)
        elif (self.geometry is not None
              and v3.board_box(self.geometry) is not None):
            x0, y0, x1, y1, z0, z1 = v3.board_box(self.geometry)
            lo, hi = np.array([x0, y0, z0]), np.array([x1, y1, z1])
        else:
            return
        # the far field reaches past the board
        if self.far.currentData() is not None:
            r = self.far_size.value() * max(hi[0] - lo[0], hi[1] - lo[1])
            c0 = np.array([
                0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1]),
                hi[2] + self.far_up.value() / zs
            ])
            lo, hi = np.minimum(lo, c0 - r), np.maximum(hi, c0 + r)
        c = 0.5 * (lo + hi)
        from PySide6.QtGui import QVector3D
        self.view.setCameraPosition(pos=QVector3D(c[0], c[1], c[2] * zs),
                                    distance=1.6 * max(hi[:2] - lo[:2]),
                                    elevation=32, azimuth=-60)

    # ---- animation ----
    def _play(self, on):
        self.play_btn.setText('Pause' if on else 'Play')
        if on:
            self.timer.start(int(1000 / self.fps.value()))
        else:
            self.timer.stop()

    def _tick(self):
        if not self._frames:
            return
        if len(self._frames) > 1:  # the phases of a period
            self.k = (self.k + 1) % len(self._frames)
            self._show(self.k)
        elif (self.src is not None and self.src.domain == 'TD'
              and len(self.src) > 1):
            self.frame.setCurrentIndex(
                (self.frame.currentIndex() + self.every.value())
                % len(self.src))

    def grab_rgb(self):
        """The view as it is drawn now: (h, w, 3) uint8."""
        img = self.view.grabFramebuffer().convertToFormat(QImage.Format_RGB888)
        a = np.frombuffer(img.constBits(), np.uint8,
                          img.sizeInBytes()).reshape(img.height(),
                                                     img.bytesPerLine())
        return np.ascontiguousarray(a[:, :3 * img.width()].reshape(
            img.height(), img.width(), 3))

    def animated(self):
        """What a saved animation steps through: 'phase' (a frequency-domain
        dump's period), 'time' (a time-domain dump's steps) or None (one frame:
        the envelope, a single time step)."""
        if len(self._frames) > 1:
            return 'phase'
        if (self.src is not None and self.src.domain == 'TD'
                and len(self.src) > 1):
            return 'time'
        return None

    def render_frames(self, orbit=0.0):
        """The animation as RGB images from the current camera, at the view's
        size (orbit: degrees of azimuth turned over the animation): a
        frequency-domain dump's phases, or a time-domain dump's time steps
        (every n-th, as Every n-th frame says). A time-domain dump had given one
        frame: a still 'animation'."""
        from PySide6.QtWidgets import QApplication
        kind = self.animated()
        if kind is None:
            raise ValueError("one frame only: nothing animates"
                             + (" (untick envelope to save the period)"
                                if self.envelope.isChecked() else ""))
        if kind == 'phase':
            steps = list(range(len(self._frames)))
        else:
            steps = list(range(0, len(self.src), self.every.value()))
        az0, t0 = self.view.opts['azimuth'], self.frame.currentIndex()
        imgs = []
        for n, k in enumerate(steps):
            if kind == 'phase':
                self._show(k)
            else:  # the time step's volume, as the slider would
                with QSignalBlocker(self.frame):
                    self.frame.setCurrentIndex(k)
                self._rebuild()
            if orbit:
                self.view.setCameraPosition(azimuth=az0
                                            + orbit * n / max(len(steps), 1))
            self.view.update()
            # the new texture reaches the GPU on a repaint
            QApplication.processEvents()
            imgs.append(self.grab_rgb())
        self.view.setCameraPosition(azimuth=az0)
        if kind == 'time':
            with QSignalBlocker(self.frame):
                self.frame.setCurrentIndex(t0)
            self._rebuild()
        else:
            self._show(self.k)
        return imgs

    def save(self, path, fps=None, orbit=0.0):
        from kicad2ems.core.fields.frames import write_frames
        return write_frames(self.render_frames(orbit), path, fps
                            or self.fps.value())

    def save_dialog(self, ext):
        if self.src is None:
            return
        if self.animated() is None:
            QMessageBox.information(
                self, f"Save {ext.upper()}",
                "Only one frame to save: nothing animates" +
                (". Untick envelope to save the field over a period."
                 if self.envelope.isChecked() else "."))
            return
        d = os.path.dirname(self.src.path)
        path, _ = QFileDialog.getSaveFileName(
            self, f"Save {ext.upper()}",
            os.path.join(d, f"{self.src.name}_3d.{ext}"),
            f"{ext.upper()} (*.{ext})")
        if path:
            try:
                self.save(path)
                self.status.setText(f"saved {path}")
            except Exception as e:
                QMessageBox.warning(self, 'Save', f"{type(e).__name__}: {e}")


def _value(p, main=False):
    """A board.json part's value as text: 100 Ω, 1.5 pF, 2.2 nH (series parts:
    each, joined; main: only its main element's, the capacitance or the
    inductance without the ESR / ESL fields)."""
    def fmt(v, unit, scales):
        f, pre = next(((f, pre) for f, pre in scales if abs(v) >= f * 0.999),
                      scales[-1])
        return f"{v / f:.3g} {pre}{unit}"
    out = []
    if main:
        kind = v3.part_kind(p)
        p = {
            k: (p.get(k) if k == kind or
                (kind == 'short' and k == 'R') else None)
            for k in ('R', 'L', 'C')
        }
    if p.get('R') is not None:
        out.append('0 Ω' if p['R'] == 0 else fmt(p['R'], 'Ω', [(
            1e6, 'M'), (1e3, 'k'), (1, '')]))
    if p.get('L') is not None:
        out.append(fmt(p['L'], 'H', [(1e-6, 'µ'), (1e-9, 'n'), (1e-12, 'p')]))
    if p.get('C') is not None:
        out.append(
            fmt(p['C'], 'F', [(1e-6, 'µ'), (1e-9, 'n'), (1e-12, 'p'),
                              (1e-15, 'f')]))
    return ' + '.join(out)
