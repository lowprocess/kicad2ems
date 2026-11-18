"""
The viewer's cross-section: the field of the line-model section picked, solved
when it is picked (kicad2ems's lines.sections): the field's strength or the
potential in colour, equipotentials, arrows along the field, the copper and the
dielectrics, the solver's mesh on request, and what the section's Z0 is against
the formula's. Zooming samples the mesh again, so the corners stay sharp.
"""
import numpy as np
from PySide6.QtCore import Qt, QRectF, QSignalBlocker, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPen, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QComboBox, QCheckBox,
                               QLabel, QPushButton)
import pyqtgraph as pg
from . import theme
from kicad2ems.core import overlay as ov
from kicad2ems.core.lines import sections
from .panels import FlowLayout

__all__ = ['SectionView']

SIGNAL, GROUND = QColor(214, 140, 64), QColor(120, 120, 120)
SHOWS = [('field strength |E|', 'e'), ('potential', 'phi')]
# equipotentials: this many between 0 and 1 V (and -1 and 0 for a pair's odd
# mode)
LEVELS = 11
ARROWS = 28             # arrows along the window's width


class _Solve(QThread):
    # the request (section id, mode), the View or the error
    done = Signal(object, object)

    def __init__(self, e, key, parent=None):
        super().__init__(parent)
        self.e, self.key = e, key

    def run(self):
        try:
            self.done.emit((self.e['id'], self.key),
                           sections.solve(self.e, self.key))
        # (a cross-section it cannot mesh: say so)
        except Exception as ex:  # noqa: BLE001
            self.done.emit((self.e['id'], self.key), ex)


class SectionView(QWidget):
    def __init__(self, model_line=True):
        """model_line: the read-out also says what the line model has for the
        section (False: a cross-section that is no board's, the impedance
        calculator's)."""
        super().__init__()
        self.model_line = model_line
        self.e, self.v, self.items, self._jobs, self._want, self.ink = (None,
                                                                        None,
                                                                        [], [],
                                                                        None,
                                                                        (255,
                                                                         255,
                                                                         255))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox(
            sizeAdjustPolicy=QComboBox.AdjustToContents,
            currentIndexChanged=self._mode_changed, toolTip=
            'What the section is solved on, and how its conductors are driven')
        self.show_what = QComboBox()
        for label, key in SHOWS:
            self.show_what.addItem(label, key)
        self.show_what.currentIndexChanged.connect(self.redraw)
        self.lines_on = QCheckBox('equipotentials', checked=True,
                                  toggled=self.redraw)
        self.arrows_on = QCheckBox(
            'arrows', checked=True,
            toolTip='The direction of E; every arrow is the same length',
            toggled=self.redraw)
        self.mesh_on = QCheckBox('mesh', toolTip="The field solver's cells",
                                 toggled=self.redraw)
        bar = FlowLayout()
        for w in (QLabel('View'), self.mode, QLabel('Colour'), self.show_what,
                  self.lines_on, self.arrows_on, self.mesh_on,
                  QPushButton('Fit', clicked=self.fit)):
            bar.addWidget(w)
        lay.addLayout(bar)
        self.plot = pg.PlotWidget()
        self.plot.setBackground(theme.colors()['bg'])
        self.plot.setAspectLocked(True)
        self.plot.setLabel('bottom', 'across the trace (mm)')
        self.plot.setLabel('left', 'height (mm)')
        for side in ('bottom', 'left'):
            self.plot.getAxis(side).enableAutoSIPrefix(False)
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        self.cbar = pg.ColorBarItem(
            values=(0, 1), colorMap=pg.colormap.getFromMatplotlib('inferno'),
            interactive=False, width=14)
        self.cbar.setImageItem(self.img, insert_in=self.plot.getPlotItem())
        lay.addWidget(self.plot, 1)
        self.info = QLabel(self._none(), wordWrap=True,
                           textInteractionFlags=Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        # zooming: draw when it stops
        self._later = QTimer(self, singleShot=True, interval=120,
                             timeout=self.redraw)
        self.plot.getViewBox().sigRangeChangedManually.connect(
            lambda *_: self._later.start())

    # ---- the section ----
    def _none(self):
        return ("No section picked: pick one of the line model's in the "
                "Viewer (its list beside the Plan and 3D views, or a click on "
                "it in the plan)"
                if self.model_line else 'No section')

    def show_section(self, e):
        """Section e (a drawing element, or None): its views in the list, the
        first of them solved."""
        self.e, self.v = e, None
        ms = sections.modes(e) if e else []
        with QSignalBlocker(self.mode):
            self.mode.clear()
            for key, label in ms:
                self.mode.addItem(label, key)
        self._clear()
        if not ms:
            self.info.setText((
                self._none() if e is None else
                f"{e.get('label') or e.get('kind')}: no cross-section to "
                "solve (a via, a part, or no reference plane)"))
            return
        self._solve(ms[0][0])

    def _mode_changed(self):
        if self.e and self.mode.currentData():
            self._solve(self.mode.currentData())

    def _solve(self, key):
        self.info.setText('solving…')
        self._want = (self.e['id'], key)
        job = _Solve(self.e, key, self)
        job.done.connect(self._solved)
        job.finished.connect(lambda j=job: self._jobs.remove(j))
        self._jobs.append(job)
        job.start()

    def busy(self):
        return bool(self._jobs)

    def wait_jobs(self, ms=15000):
        """Let the solves going end (a QThread destroyed while it runs aborts
        the program: the window's close)."""
        for job in list(self._jobs):
            job.wait(ms)

    def _solved(self, want, v):
        if want != self._want:  # another section was picked meanwhile
            return
        if isinstance(v, Exception):
            self.v = None
            self._clear()
            self.info.setText(f"the field solver could not solve this one: {v}")
            return
        self.v, e = v, self.e
        self.fit()
        parts = [
            f"εeff {x:.2f}" if k == 'eeff' else f"{k} {x:.1f} Ω"
            for k, x in v.values.items()
            if k != 'nodes'
        ]
        parts.append(f"{v.values['nodes']:,} nodes")
        text = f"<b>{' · '.join(parts)}</b>"
        if self.model_line:
            model = (f"line model: Z0 {e['z0']:.1f} Ω{ov.source_note(e)}"
                     if e.get('z0') else 'no Z0 in the line model')
            text += f"<br>{model}<br>{ov.describe(e)}"
        self.info.setText(text)

    # ---- drawing ----
    def fit(self):
        if self.v:
            x0, x1, z0, z1 = self.v.window
            self.plot.getViewBox().setRange(QRectF(x0, z0, x1 - x0, z1 - z0),
                                            padding=0.02)
            self.redraw()

    def _clear(self):
        for it in self.items:
            self.plot.removeItem(it)
        self.items = []
        self.img.clear()

    def _add(self, it):
        self.plot.addItem(it)
        self.items.append(it)
        return it

    def redraw(self):
        if not self.v:
            return
        self._clear()
        v = self.v
        # the window on screen, sampled at most about 800 across
        vb = self.plot.getViewBox()
        (x0, x1), (z0, z1) = vb.viewRange()
        w, h = max(int(vb.width()), 64), max(int(vb.height()), 64)
        s = min(1.0, 800 / max(w, h))
        nx, nz = max(int(w * s), 32), max(int(h * s), 32)
        x, z, phi, ex, ez, er = v.field.sample(x0, x1, z0, z1, nx, nz)
        rect = QRectF(x0, z0, x1 - x0, z1 - z0)
        top, signed = max(abs(a) for a in v.volts) or 1.0, min(v.volts) < 0
        self.ink = ((255, 255, 255) if self.show_what.currentData() == 'e' else
                    (40, 40, 40))
        if self.show_what.currentData() == 'e':
            mag = np.hypot(ex, ez)
            good = mag[np.isfinite(mag) & (mag > 0)]
            # the corners are singular: they saturate
            hi = float(np.percentile(good, 99.5)) if good.size else 1.0
            data, lim = (np.log10(np.maximum(mag,
                                             hi * 1e-3)), (np.log10(hi * 1e-3),
                                                           np.log10(hi)))
            cmap, label = 'inferno', 'log10 |E| (V/mm, at 1 V)'
        else:
            data, lim = phi, ((-top if signed else 0.0), top)
            cmap, label = 'RdBu_r' if signed else 'viridis', 'potential (V)'
        self.cbar.setColorMap(pg.colormap.getFromMatplotlib(cmap))
        # NaN, copper and beyond the box: clear
        self.img.setImage(data, autoLevels=False, levels=lim)
        self.img.setRect(rect)
        self.cbar.setLevels(lim)
        self.cbar.axis.setLabel(label)
        self._add_dielectrics(er, z, rect)
        if self.lines_on.isChecked():
            self._add_equipotentials(phi, rect, top, signed)
        if self.arrows_on.isChecked():
            self._add_arrows(x, z, ex, ez, x1 - x0)
        if self.mesh_on.isChecked():                    # the solver's cells
            c = v.field.cell
            c = c[(c[:, 1] > rect.left()) & (c[:, 0] < rect.right())
                  & (c[:, 3] > rect.top()) & (c[:, 2] < rect.bottom())]
            xs = np.stack([
                c[:, 0], c[:, 1], c[:, 1], c[:, 1], c[:, 1], c[:, 0], c[:, 0],
                c[:, 0]
            ], 1).ravel()
            zs = np.stack([
                c[:, 2], c[:, 2], c[:, 2], c[:, 3], c[:, 3], c[:, 3], c[:, 3],
                c[:, 2]
            ], 1).ravel()
            self._add(
                pg.PlotCurveItem(xs, zs, connect='pairs', pen=pg.mkPen(
                    (*self.ink, 70), width=1)))
        # the copper over the field, a sheet at least two pixels thick so that
        # it shows; planes across the window
        pixel = max((x1 - x0) / nx, (z1 - z0) / nz)
        for zp in v.xs.planes:
            self._add_box(rect.left(), rect.right(), zp, zp, GROUND, pixel)
        for c in v.xs.conductors:
            a, b = max(c.x0, rect.left()), min(c.x1, rect.right())
            if a < b:
                self._add_box(a, b, c.z0, c.z1,
                              (GROUND if c.ground else SIGNAL), pixel)

    def _add_dielectrics(self, er, z, rect):
        """A line where er changes with height, and each layer's er at the
        window's left."""
        by_height = np.array(
            [np.nan if np.isnan(row).all() else np.nanmax(row) for row in er.T])
        edges = np.flatnonzero(np.abs(np.diff(np.nan_to_num(by_height))) > 1e-6)
        pen = QPen(QColor(*self.ink, 170), 1, Qt.DashLine)
        pen.setCosmetic(True)
        for k in edges:
            h = 0.5 * (z[k] + z[k + 1])
            self._add(
                pg.PlotCurveItem([rect.left(), rect.right()], [h, h], pen=pen))
        for a, b in zip(np.r_[0, edges + 1], np.r_[edges + 1, len(z)]):
            if np.isfinite(by_height[a]):
                t = self._add(
                    pg.TextItem(f"εr {by_height[a]:.3g}", color=self.ink,
                                anchor=(0, 0.5)))
                t.setPos(rect.left(), 0.5 * (z[a] + z[b - 1]))

    def _add_equipotentials(self, phi, rect, top, signed):
        """Line pieces where phi crosses each level (between -top or 0 and top);
        none in copper (NaN there)."""
        vals = (np.linspace(-top if signed else 0.0, top,
                            2 * LEVELS + 1 if signed else LEVELS + 2)[1:-1])
        px, pz = rect.width() / phi.shape[0], rect.height() / phi.shape[1]
        seg = [
            q for val in vals
            for q in pg.functions.isocurve(phi, float(val), connected=False)
        ]
        seg = np.asarray(seg, float).reshape(-1, 2)
        seg = (seg[np.isfinite(seg).all(1)] if len(seg) % 2 else seg.reshape(
            -1, 2, 2)[np.isfinite(seg.reshape(-1, 4)).all(1)].reshape(-1, 2))
        if len(seg):
            self._add(
                pg.PlotCurveItem(rect.left() + (seg[:, 0] + 0.5) * px,
                                 rect.top() + (seg[:, 1] + 0.5) * pz,
                                 connect='pairs', pen=pg.mkPen((*self.ink, 150),
                                                               width=1)))

    def _add_arrows(self, x, z, ex, ez, width):
        """Arrows along E on a coarse grid, one length: the field's strength is
        the colour's to show."""
        nx, nz = len(x), len(z)
        step = max(nx // ARROWS, 1)
        ii, jj = np.meshgrid(np.arange(step // 2, nx, step),
                             np.arange(step // 2, nz, step), indexing='ij')
        ax, az = ex[ii, jj], ez[ii, jj]
        mag = np.hypot(ax, az)
        keep = np.isfinite(mag) & (mag > 0)
        if not keep.any():
            return
        L = 0.7 * width / ARROWS
        u, s = ax[keep] / mag[keep], az[keep] / mag[keep]
        cx, cz = x[ii[keep]], z[jj[keep]]
        head = 0.35 * L
        xs = np.stack([
            cx - u * L / 2, cx + u * L / 2, cx + u * L / 2 - head *
            (u + 0.5 * s), cx + u * L / 2, cx + u * L / 2 - head *
            (u - 0.5 * s), cx + u * L / 2
        ], 1).ravel()
        zs = np.stack([
            cz - s * L / 2, cz + s * L / 2, cz + s * L / 2 - head *
            (s - 0.5 * u), cz + s * L / 2, cz + s * L / 2 - head *
            (s + 0.5 * u), cz + s * L / 2
        ], 1).ravel()
        self._add(
            pg.PlotCurveItem(xs, zs, connect='pairs', pen=pg.mkPen(
                (0, 200, 255, 170), width=1)))

    def _add_box(self, a, b, z0, z1, colour, pixel):
        lo, hi = (z0 - pixel, z1 + pixel) if z1 - z0 < 2 * pixel else (z0, z1)
        it = self._add(pg.QtWidgets.QGraphicsRectItem(a, lo, b - a, hi - lo))
        it.setBrush(QBrush(colour))
        it.setPen(QPen(Qt.NoPen))
        it.setZValue(5)
