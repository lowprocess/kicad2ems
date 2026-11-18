"""
The GUI's field view: the dumps of a run (time-domain frames, frequency-domain
fields), cut at any x / y / z mesh line, played over time or phase, followed
live while openEMS writes them, saved as PNG / GIF / MP4. Reading and drawing
for files live in ..fields (no Qt).
"""
import os
import numpy as np
from PySide6.QtCore import Qt, QRectF, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QComboBox, QPushButton,
                               QLabel, QCheckBox, QSlider, QSpinBox,
                               QFileDialog, QMessageBox, QProgressDialog,
                               QApplication)
import pyqtgraph as pg
from . import theme
from .panels import FlowLayout
from kicad2ems.core.fields import frames as F

COMPONENTS = [('|F|', 'mag'), ('x', 'x'), ('y', 'y'), ('z', 'z')]


class FieldsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.out, self.srcs, self.src, self.pl, self.lim = (win, None,
                                                                      [], None,
                                                                      None,
                                                                      None)
        self._layout_items, self._layout_key = [], None
        self.user_lim = False  # the colour bar was dragged: keep it
        lay = QVBoxLayout(self)

        self.dump = QComboBox(
            minimumWidth=200,
            sizeAdjustPolicy=QComboBox.AdjustToMinimumContentsLengthWithIcon,
            minimumContentsLength=28, currentIndexChanged=self._pick)
        self.live = QCheckBox(
            'Live (follow the newest frame)', checked=True, toolTip=
            'While a run is going, show each time-domain frame as openEMS '
            'writes it'
        )
        self._row(lay, QLabel('Dump'), self.dump,
                  QPushButton('Refresh', clicked=lambda: self.load(self.out)),
                  QPushButton('Open results folder…', clicked=self._open),
                  self.live)

        self.comp = QComboBox()
        self.comp.addItems([c for c, _ in COMPONENTS])
        self.comp.currentIndexChanged.connect(self._new_range)
        self.axis = QComboBox(
            toolTip=
            'Plane dumps have one choice; volume dumps can be cut at any x, y '
            'or z mesh line'
        )
        self.axis.addItems(['x', 'y', 'z'])
        self.axis.currentIndexChanged.connect(self._axis_changed)
        self.slice = QSlider(Qt.Horizontal, maximum=0, minimumWidth=160,
                             valueChanged=self._new_range)
        self.slice_lab = QLabel('', minimumWidth=110)
        self.log = QCheckBox('log scale', checked=True, toggled=self._new_range)
        self.auto = QCheckBox(
            'auto range per frame',
            toolTip='Off: keep the colour range (drag the colour bar to set '
            'it)', toggled=self._new_range)
        self._row(lay, QLabel('Component'), self.comp, QLabel('Cut normal to'),
                  self.axis, self.slice, self.slice_lab, self.log, self.auto)
        self.layout_on = QCheckBox('Layout overlay', checked=True,
                                   toggled=self.draw)
        self.layout_layer = QComboBox(
            toolTip=
            'Project this copper layer under horizontal field maps; the '
            'sampled field height is unchanged', currentIndexChanged=self.draw)
        self.components_on = QCheckBox('Components', checked=True,
                                       toggled=self.draw)
        self.field_opacity = QSlider(Qt.Horizontal, minimum=10, maximum=100,
                                     value=65, maximumWidth=140,
                                     valueChanged=self.draw)
        self._row(
            lay, self.layout_on, QLabel('Layer'), self.layout_layer,
            self.components_on, QLabel('Field opacity'), self.field_opacity,
            QLabel(
                'Cyan: simulated copper · grey: context · gold: selected parts')
        )

        self.view = pg.PlotWidget()
        self.view.setBackground(theme.colors()['bg'])
        self.img = pg.ImageItem()
        self.view.addItem(self.img)
        self.lines = pg.PlotDataItem([], [], pen=pg.mkPen('#7fd1ff', width=1),
                                     connect='finite')
        self.view.addItem(self.lines)
        self.cbar = pg.ColorBarItem(
            values=(0, 1), colorMap=pg.colormap.getFromMatplotlib('inferno'),
            interactive=True)
        self.cbar.setImageItem(self.img, insert_in=self.view.getPlotItem())
        self.cbar.axis.enableAutoSIPrefix(False)
        self.cbar.sigLevelsChangeFinished.connect(self._levels_dragged)
        self.view.scene().sigMouseMoved.connect(self._hover)
        lay.addWidget(self.view, 1)

        self.play_btn = QPushButton('▶ Play', checkable=True,
                                    toggled=self._play)
        self.frame = QSlider(Qt.Horizontal, maximum=0, minimumWidth=160,
                             valueChanged=self.draw)
        self.phase_on = QCheckBox(
            'instantaneous, phase', toggled=self._new_range, toolTip=
            'Frequency domain: Re(F·e^jφ) at the phase below; off: the peak |F|'
        )
        self.phase = QSlider(Qt.Horizontal, maximum=359, minimumWidth=120,
                             valueChanged=self.draw)
        self.frame_lab = QLabel('', minimumWidth=240)
        self._row(lay, self.play_btn, QLabel('frame'), self.frame,
                  self.phase_on, self.phase, self.frame_lab)

        self.readout = QLabel('', minimumWidth=220)
        self.fps = QSpinBox(minimum=1, maximum=60, value=12)
        self.every = QSpinBox(
            minimum=1, maximum=1000,
            toolTip='Time domain: frames used for Play and saved animations')
        self._row(
            lay, self.readout, QLabel('fps'), self.fps,
            QLabel('every n-th frame'), self.every,
            *(QPushButton(text, clicked=lambda _=False, e=ext: self.save(e))
              for text, ext in (('Save PNG…', 'png'), ('Save GIF…', 'gif'),
                                ('Save MP4…', 'mp4'))))

        self.timer = QTimer(self, timeout=self._tick)
        self._set_controls()

    @staticmethod
    def _row(lay, *widgets):
        """A row of controls that wraps when narrow, added to lay."""
        row = FlowLayout()
        for w in widgets:
            row.addWidget(w)
        lay.addLayout(row)

    # ---- sources ----
    def _open(self):
        self.load(
            QFileDialog.getExistingDirectory(
                self, "A results folder (the run's output folder)", self.out
                or os.getcwd()))

    def load(self, out_dir):
        """List the dumps of a results folder (keeps the selection when it is
        still there)."""
        if not out_dir:
            return
        self.out = out_dir
        keep = self.src.label if self.src else None
        self.srcs = F.find_dumps(out_dir)
        labels = [s.label for s in self.srcs]
        with QSignalBlocker(self.dump):
            self.dump.clear()
            self.dump.addItems(
                labels
                or ['(no field dumps in this folder: add some in Setup)'])
            self.dump.setCurrentIndex(
                labels.index(keep) if keep in labels else 0)
        self._pick()

    def _pick(self, *_):
        i = self.dump.currentIndex()
        self.src = self.srcs[i] if 0 <= i < len(self.srcs) else None
        self.lim = None
        # a drop, a rise or a temperature:
        if self.src is not None and (self.src.field == 'V' or self._spans()):
            self.log.setChecked(False)  # linear, as volts and degrees are
        keep = self.layout_layer.currentText()
        cu = (self.src.geometry if self.src else {}).get('cu_z', {})
        with QSignalBlocker(self.layout_layer):
            self.layout_layer.clear()
            self.layout_layer.addItems(sorted(cu, key=lambda l: -cu[l]))
            self.layout_layer.setCurrentIndex(
                max(0, self.layout_layer.findText(keep)))
        self._layout_key = None
        if self.src is None:
            self._draw_layout(None)
        self._set_controls()
        if self.src and len(self.src):
            with QSignalBlocker(self.axis):
                self.axis.setCurrentIndex(F.default_axis(self.src.shape()))
            self._axis_changed()
        else:
            self.img.clear()
            self.lines.setData([], [])
            self.frame_lab.setText('no frames yet' if self.src else '')

    def _set_controls(self):
        src = self.src
        n = len(src) if src else 0
        with QSignalBlocker(self.frame):
            self.frame.setMaximum(max(0, n - 1))
        fd = bool(src) and src.domain == 'FD'
        self.phase_on.setEnabled(fd)
        self.phase.setEnabled(fd)
        self.every.setEnabled(not fd)
        if fd and n:
            self.every.setValue(1)
        elif src and n > 200 and self.every.value() == 1:
            self.every.setValue(max(1, n // 120))

    def _axis_changed(self, *_):
        if not (self.src and len(self.src)):
            return
        n = self.src.shape()[self.axis.currentIndex()]
        with QSignalBlocker(self.slice):
            self.slice.setMaximum(max(0, n - 1))
            self.slice.setValue(n // 2 if n > 1 else 0)
            self.slice.setEnabled(n > 1)
        self._new_range()

    # ---- drawing ----
    def _component(self):
        return COMPONENTS[self.comp.currentIndex()][1]

    def _phase(self):
        return (np.radians(self.phase.value()) if self.src.domain == 'FD'
                and self.phase_on.isChecked() else None)

    def _new_range(self, *_):
        self.lim, self.user_lim = None, False
        self.draw()

    def _levels_dragged(self, *_):
        if self.pl is None:
            return
        lo, hi = self.cbar.levels()
        with QSignalBlocker(self.auto):
            self.auto.setChecked(False)
        self.lim, self.user_lim = ((10**lo, 10**hi) if self._use_log() else
                                   (lo, hi)), True

    def _use_log(self):
        return (self.log.isChecked() and self.pl is not None
                and not self.pl.signed)

    def draw(self, *_):
        src = self.src
        if not (src and len(src)):
            return
        i = min(self.frame.value(), len(src) - 1)
        try:
            mesh, data = src.load(i)
        except Exception as e:  # a frame still being written
            self.frame_lab.setText(f"frame {i}: {e.__class__.__name__}")
            return
        ax = self.axis.currentIndex()
        pl = self.pl = F.plane(mesh, data, ax, self.slice.value(),
                               self._component(), self._phase())
        pos = pl.pos if ax != 1 else -pl.pos
        self.slice_lab.setText(f"{'xyz'[ax]} = {pos:.3f} mm")
        if self.auto.isChecked():
            self.lim = F.levels([pl.img], pl.signed, self.log.isChecked(),
                                span=self._spans())
        elif self.lim is None:
            self.lim = F.levels(self._sample(ax), pl.signed,
                                self.log.isChecked(), span=self._spans())
        lo, hi = self.lim
        img, (u0, v0, du, dv) = pl.resample()
        use_log = self._use_log()
        if use_log:
            lo = max(lo, hi * 1e-12) if hi > 0 else 1e-30
            img, lo, hi = (np.log10(np.maximum(img, lo)), np.log10(lo),
                           np.log10(max(hi, lo * 10)))
        self.cbar.setColorMap(
            pg.colormap.getFromMatplotlib('RdBu_r' if pl.signed else 'inferno'))
        self.img.setImage(img, autoLevels=False)
        self.img.setRect(
            QRectF(u0 - du / 2, v0 - dv / 2, du * img.shape[0],
                   dv * img.shape[1]))
        with QSignalBlocker(self.cbar):
            self.cbar.setLevels((lo, hi))
        what = ((src.field if self._spans() else f"|{src.field}|")
                if self._component() == 'mag' else
                f"{src.field}_{self._component()}")
        scaled = src.domain == 'FD' and src.scale(i)
        self.cbar.axis.setLabel(('log10 ' if use_log else '')
                                + f"{what} ({src.unit}"
                                + (', 1 V incident' if scaled else '') + ')')
        self._draw_layout(ax)
        self.img.setOpacity(self.field_opacity.value()
                            / 100 if self._overlay(ax) else 1.)
        segs = F.outlines(src.geometry, ax, pl.pos)
        if segs:
            self.lines.setData(
                np.concatenate([np.append(u, np.nan) for u, _ in segs]),
                np.concatenate([np.append(v, np.nan) for _, v in segs]))
            self.lines.setPen(
                pg.mkPen(('#7fd1ff' if not pl.signed else '#222222'),
                         width=1 if ax == 2 else 3))
        else:
            self.lines.setData([], [])
        p = self.view.getPlotItem()
        p.invertY(ax == 2)
        p.setAspectLocked(ax == 2)
        p.setLabel('bottom', f"{pl.names[0]} (mm)")
        p.setLabel('left', f"{pl.names[1]} (mm)")
        self.frame_lab.setText(
            f"{i + 1}/{len(src)}: {src.frame_label(i, self._phase())}")

    def _overlay(self, axis):
        """Whether the layout is drawn under the field: a horizontal cut, with
        the overlay on and a layer."""
        return (axis == 2 and self.layout_on.isChecked()
                and self.layout_layer.count() > 0)

    def _draw_layout(self, axis):
        key = (id(self.src), axis, self.layout_layer.currentText(),
               self.layout_on.isChecked(), self.components_on.isChecked())
        if key == self._layout_key:
            return
        for it in self._layout_items:
            self.view.removeItem(it)
        self._layout_items, self._layout_key = [], key
        if self.src and axis == 2 and self.layout_on.isChecked():
            from .layoutscene import layout_items
            self._layout_items = layout_items(
                self.src.geometry, self.layout_layer.currentText(), kicad=True,
                components=self.components_on.isChecked())
            for it in self._layout_items:
                self.view.addItem(it)
        self.layout_layer.setEnabled(axis == 2)
        self.components_on.setEnabled(axis == 2)

    def _spans(self):
        """The dump's colours span its own values (a temperature: from the
        board's coolest point, not from zero)."""
        return (bool(getattr(self.src, 'spans', False))
                and self._component() == 'mag')

    def _sample(self, ax):
        """Images for one colour range over the series: time domain, up to 12
        frames across it; frequency domain with a phase, half a period."""
        src, idx, comp = self.src, self.slice.value(), self._component()
        if src.domain == 'FD':
            mesh, data = src.load(self.frame.value())
            phases = (np.linspace(0, np.pi, 6, endpoint=False)
                      if self._phase() is not None else [None])
            return [F.plane(mesh, data, ax, idx, comp, p).img for p in phases]
        out = []
        for i in sorted(
                set(
                    np.linspace(0,
                                len(src) - 1, min(12, len(src))).astype(int))):
            try:
                out.append(F.plane(*src.load(i), ax, idx, comp).img)
            except Exception:  # a frame being written
                pass
        return out

    def _hover(self, pos):
        pl = self.pl
        if pl is None or not self.view.sceneBoundingRect().contains(pos):
            return
        q = self.view.getPlotItem().vb.mapSceneToView(pos)
        iu, iv = (np.argmin(np.abs(pl.u - q.x())),
                  np.argmin(np.abs(pl.v - q.y())))
        if (pl.u[0] - 1 <= q.x() <= pl.u[-1] + 1
                and pl.v[0] - 1 <= q.y() <= pl.v[-1] + 1):
            self.readout.setText(
                f"{pl.names[0]} = {pl.u[iu]:.3f}, {pl.names[1]} = "
                f"{pl.v[iv]:.3f} mm: {pl.img[iu, iv]:.4g} {self.src.unit}")

    # ---- playing and following a run ----
    def _play(self, on):
        self.play_btn.setText('■ Stop' if on else '▶ Play')
        if on:
            if self.src and self.src.domain == 'FD':
                self.phase_on.setChecked(True)
            self.timer.start(int(1000 / self.fps.value()))
        else:
            self.timer.stop()

    def _tick(self):
        src = self.src
        if not (src and len(src)):
            return
        self.timer.setInterval(int(1000 / self.fps.value()))
        if src.domain == 'FD':
            self.phase.setValue((self.phase.value() + 15) % 360)
        else:
            self.frame.setValue(
                (self.frame.value() + self.every.value()) % len(src))

    def follow(self, out_dir):
        """Called while a run is going: new dumps and frames; with Live, show
        the newest frame."""
        if out_dir != self.out or len(F.find_dumps(out_dir)) != len(self.srcs):
            self.load(out_dir)
        if not self.src:
            return
        n_old = len(self.src)
        self.src.refresh()
        if not n_old and len(self.src):
            self._pick()
        self._set_controls()
        if (self.live.isChecked() and not self.play_btn.isChecked()
                and self.src.domain == 'TD'):
            k = self.src.newest_complete()
            if k is not None and k != self.frame.value():
                if not self.user_lim:
                    self.lim = None  # the range grows with the run
                self.frame.setValue(k)

    # ---- saving ----
    def save(self, ext):
        src = self.src
        if not (src and len(src) and self.pl is not None):
            QMessageBox.information(self, 'Fields', 'No field frame to save')
            return
        base = os.path.join(self.out or os.getcwd(),
                            f"{src.run}_{src.name}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, f"Save {ext.upper()}", base,
                                              f"{ext.upper()} (*.{ext})")
        if not path:
            return
        if not path.lower().endswith('.' + ext):
            path += '.' + ext
        ax, comp, use_log = (self.axis.currentIndex(), self._component(),
                             self._use_log())
        lim = None if self.auto.isChecked() else self.lim
        style = dict(enabled=self._overlay(ax),
                     layer=self.layout_layer.currentText(),
                     components=self.components_on.isChecked(),
                     opacity=self.field_opacity.value() / 100)
        try:
            if ext == 'png':
                from PIL import Image
                what = (f"|{src.field}|"
                        if comp == 'mag' else f"{src.field}_{comp}")
                Image.fromarray(
                    F.render(
                        self.pl, *self.lim, use_log,
                        f"{what}, {self.slice_lab.text()}, "
                        f"{src.frame_label(self.frame.value(), self._phase())} "
                        f"({src.run} driven)",
                        src.geometry, f"{what} ({src.unit})",
                        layout=style)).save(path)
            else:
                td = src.domain == 'TD'
                frames = (list(range(0, len(src), self.every.value()))
                          if td else [self.frame.value()])
                dlg = QProgressDialog(f"Writing {os.path.basename(path)}…",
                                      None, 0,
                                      len(frames) if td else 24, self)
                dlg.setWindowModality(Qt.WindowModal)
                dlg.show()

                def progress(k, n_):
                    dlg.setValue(k)
                    QApplication.processEvents()

                F.save_animation(src, path, ax, self.slice.value(), comp,
                                 frames, fps=self.fps.value(),
                                 log=self.log.isChecked(), lim=lim,
                                 progress=progress, layout=style)
                dlg.close()
        except Exception as e:
            QMessageBox.warning(self, 'Save', str(e))
            return
        self.readout.setText(f"saved {path}")
