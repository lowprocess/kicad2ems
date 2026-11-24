"""The board canvas: outline, one layer's copper, vias, and a mode's field
over them (colour, contour lines, current arrows). Pan and zoom with the
mouse; the field is re-sampled to the screen after each view change.
"""
import math

import numpy as np
import pyqtgraph as pg
import shapely
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsPathItem, QWidget

from ..raster import (CURRENT, DIVERGING, SEQUENTIAL, FieldSampler, arrows, colorize,
                      contour_levels, contours, lut, scale_position)

BG = '#f7f8f7'
GND_FILL, OTHER_FILL, EDGE = QColor(208, 212, 208), QColor(150, 152, 146, 170), QColor(90, 98, 101)


def shape_path(geom):
    """A QPainterPath of a shapely (multi)polygon, holes even-odd."""
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for p in shapely.get_parts(geom):
        if p.geom_type != 'Polygon':
            continue
        for ring in [p.exterior, *p.interiors]:
            c = np.asarray(ring.coords)
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in c]))
            path.closeSubpath()
    return path


def _item(path, brush=None, pen=None, z=0, width=1.0):
    """A path item: fill `brush`, outline `pen` (a colour; cosmetic: `width`
    screen pixels whatever the zoom)."""
    it = QGraphicsPathItem(path)
    it.setBrush(QBrush(brush) if brush is not None else QBrush(Qt.NoBrush))
    pn = QPen(pen) if pen is not None else QPen(Qt.NoPen)
    pn.setCosmetic(True)
    pn.setWidthF(width)
    it.setPen(pn)
    it.setZValue(z)
    return it


class ColourBar(QWidget):
    """The colour scale, drawn over the view's top-right corner."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.resize(230, 62)
        self.stops, self.labels, self.title, self.ticks = DIVERGING, ('', '', ''), '', []

    def set(self, stops, labels, title, ticks=()):
        """ticks: positions 0..1 on the bar (the contour levels)."""
        self.stops, self.labels, self.title, self.ticks = stops, labels, title, list(ticks)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(200, 204, 202))); p.setBrush(QColor(255, 255, 255, 225))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        p.setPen(QColor(30, 34, 36))
        p.drawText(8, 16, self.title)
        g = QLinearGradient(8, 0, self.width() - 8, 0)
        for i, c in enumerate(self.stops):
            g.setColorAt(i / (len(self.stops) - 1), QColor(*c))
        p.fillRect(8, 22, self.width() - 16, 10, g)
        w = self.width() - 16
        p.setPen(QPen(QColor(30, 34, 36), 1))
        for t in self.ticks:
            p.drawLine(QPointF(8 + t * w, 20), QPointF(8 + t * w, 34))
        for frac, text, align in ((0, self.labels[0], Qt.AlignLeft), (0.5, self.labels[1], Qt.AlignHCenter),
                                  (1, self.labels[2], Qt.AlignRight)):
            x = 8 + frac * w - (100 if align == Qt.AlignRight else 50 if align == Qt.AlignHCenter else 0)
            p.drawText(QRectF(x, 36, 100, 20), int(align | Qt.AlignVCenter), text)


class BoardView(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent, background=BG)
        self.vb = self.getPlotItem().getViewBox()
        self.vb.setAspectLocked(True)
        self.vb.invertY(True)                        # board y points down
        self.getPlotItem().showGrid(False, False)
        for ax in ('left', 'bottom'):
            self.getPlotItem().getAxis(ax).setLabel(units='mm')
        self.items = {}                              # name -> graphics item
        self.contour_items = []                      # the contour curves
        self.image = pg.ImageItem(axisOrder='row-major')
        self.image.setZValue(5)
        self.addItem(self.image)
        self.sampler, self.result, self.field = None, None, None
        self.state = dict(mode=0, phase=0.0, animating=False, colour='signed', vmax=None,
                          decades=0, arrows=True, plane_sign=1, fill=True, contours=False,
                          per=10, nodal=True, line_width=1.2, line_colour='dark')
        self._timer = QTimer(self, singleShot=True, interval=120)
        self._timer.timeout.connect(self.resample)
        self.vb.sigRangeChanged.connect(lambda *a: self._timer.start())
        self.bar = ColourBar(self)
        self.bar.hide()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if 'bar' in self.__dict__:                 # (pyqtgraph resizes before __init__ ends)
            self.bar.move(self.width() - self.bar.width() - 12, 10)

    # ---- board -------------------------------------------------------------
    def show_board(self, board, layer, ref_nets, show_vias=True):
        """Draw the outline, `layer`'s copper (ground lighter) and the vias."""
        for name in ('outline', 'gnd', 'other', 'vias', 'arrows'):
            if name in self.items:
                self.removeItem(self.items.pop(name))
        if board.outline is not None:
            self.items['outline'] = _item(shape_path(board.outline), QColor('#ffffff'), EDGE, -2)
        if layer:
            per = board.copper.get(layer, {})
            ref = set(ref_nets)
            gnd = shapely.union_all([g for n, g in per.items() if n in ref]) if per else None
            oth = shapely.union_all([g for n, g in per.items() if n not in ref]) if per else None
            if gnd is not None and not gnd.is_empty:
                self.items['gnd'] = _item(shape_path(gnd), GND_FILL, None, -1)
            if oth is not None and not oth.is_empty:
                self.items['other'] = _item(shape_path(oth), OTHER_FILL, None, 6)
        if show_vias and board.vias:
            v = np.array([(q.x, q.y, q.drill) for q in board.vias])
            self.items['vias'] = pg.ScatterPlotItem(
                v[:, 0], v[:, 1], size=np.maximum(v[:, 2], 0.1), pxMode=False,
                pen=pg.mkPen(EDGE, width=1), brush=pg.mkBrush(255, 255, 255, 200))
            self.items['vias'].setZValue(7)
        for name, it in self.items.items():
            self.addItem(it)

    def fit(self, rect=None):
        """Zoom to `rect` (x0, y0, x1, y1), default everything drawn."""
        if rect:
            x0, y0, x1, y1 = rect
            self.vb.setRange(QRectF(x0, y0, x1 - x0, y1 - y0), padding=0.05)
        else:
            self.vb.autoRange(padding=0.03)

    # ---- the mode field ------------------------------------------------------
    def show_result(self, result):
        """Use `result` (a PairResult) for the field overlay."""
        self.result = result
        self.sampler = FieldSampler(result.mesh.nodes, result.mesh.tris) if result else None
        self.field = None
        if result is None:
            self.image.clear(); self.bar.hide(); self._clear_overlays()
        self.resample()

    def _clear_overlays(self):
        for it in self.contour_items + [self.items.pop('arrows', None)]:
            if it is not None:
                self.removeItem(it)
        self.contour_items = []

    def resample(self):
        """Sample the field on the current view at about screen resolution."""
        if self.sampler is None:
            return
        (x0, x1), (y0, y1) = self.vb.viewRange()
        nx0, ny0 = self.sampler.nodes.min(0)
        nx1, ny1 = self.sampler.nodes.max(0)
        x0, x1, y0, y1 = max(x0, nx0), min(x1, nx1), max(y0, ny0), min(y1, ny1)
        if x1 <= x0 or y1 <= y0:
            self.image.clear()
            return
        (vx0, vx1), (vy0, vy1) = self.vb.viewRange()
        px_w, px_h = max(self.vb.width(), 50), max(self.vb.height(), 50)
        w = int(min(1800, px_w * (x1 - x0) / (vx1 - vx0) * 1.2)) or 1
        h = int(min(1800, px_h * (y1 - y0) / (vy1 - vy0) * 1.2)) or 1
        self.sampler.sample(x0, y0, x1, y1, max(w, 2), max(h, 2))
        self.field = None
        self.redraw()

    def set_state(self, **kw):
        """Change what is drawn: mode, phase (rad), colour ('signed', 'abs',
        'current'), vmax (None: the mode's own), decades (0: linear), fill
        (the colour image), arrows, plane_sign (+1 top plane, -1 bottom: the
        current runs opposite), contours (lines of the coloured quantity):
        per (lines per full scale, or per decade on a log scale), nodal (the
        V = 0 line, bold), line_width (px), line_colour ('dark', 'light',
        'value': the colour scale's)."""
        if 'mode' in kw and kw['mode'] != self.state['mode']:
            self.field = None
        self.state.update(kw)
        self.redraw()

    def redraw(self):
        if self.sampler is None or self.sampler.grid is None or self.result is None:
            return
        s, modes = self.state, self.result.modes
        if not len(modes.f):
            self.image.clear(); self.bar.hide(); self._clear_overlays()
            return
        m = min(s['mode'], len(modes.f) - 1)
        v = modes.V[:, m]
        if self.field is None:                       # the mode on this view, once
            self.field = (self.sampler.values(v), self.sampler.current(v))
        V, J = self.field
        # animating: V ~ cos wt and the current ~ sin wt; at rest both at their
        # amplitude (the current's direction at its own peak)
        cos_t, sin_t = ((math.cos(s['phase']), math.sin(s['phase'])) if s['animating']
                        else (1.0, 1.0))
        dec = s['decades']
        if s['colour'] == 'current':
            vmax = s['vmax'] or (float(np.nanquantile(J, 0.99)) if np.isfinite(J).any() else 1.0)
            shown = J * abs(sin_t)
        else:
            vmax = s['vmax'] or float(modes.peak[m])
            shown = V * cos_t if s['colour'] == 'signed' else np.abs(V * cos_t)
        if s['fill']:
            self.image.setImage(colorize(shown, vmax, s['colour'], dec), autoLevels=False)
            x0, y0, x1, y1 = self.sampler.grid['rect']   # after setImage: the scale is
            self.image.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))   # per pixel of this image
            self.image.show()
        else:
            self.image.hide()
        levels = contour_levels(vmax, s['per'], s['colour'], dec) if s['contours'] else []
        # the colour bar: ends of the scale, the floor of a log scale
        fmt = lambda x: f'{x:.3g}'
        lo = vmax * 10 ** -dec
        if s['colour'] == 'signed':
            labels = (f'−{fmt(vmax)}', f'|V| < {fmt(lo)}' if dec else '0', f'+{fmt(vmax)}')
        else:
            labels = (f'< {fmt(lo)}' if dec else '0', fmt((lo * vmax) ** 0.5 if dec else vmax / 2), fmt(vmax))
        title = {'signed': 'V / V_rms', 'abs': '|V| / V_rms',
                 'current': '|grad V| (V_rms/mm) ∝ plane current'}[s['colour']]
        if s['animating']:
            title += f'   ωt = {math.degrees(s["phase"]):3.0f}°'
        stops = {'signed': DIVERGING, 'abs': SEQUENTIAL, 'current': CURRENT}[s['colour']]
        self.bar.set(stops, labels, title, scale_position(np.asarray(levels), vmax, s['colour'], dec))
        self.bar.show()
        self._draw_contours(shown, levels, vmax)
        self._draw_arrows(v, sin_t)

    def _draw_contours(self, shown, levels, vmax):
        """Contour lines of the coloured quantity, one curve per pen."""
        for it in self.contour_items:
            self.removeItem(it)
        self.contour_items = []
        s = self.state
        if not s['contours']:
            return
        nodal = s['nodal'] and s['colour'] == 'signed'
        lines = contours(shown, self.sampler.grid['rect'],
                         np.append(levels, 0.0) if nodal else levels)
        if s['line_colour'] == 'value':
            table = lut({'signed': DIVERGING, 'abs': SEQUENTIAL, 'current': CURRENT}[s['colour']])
            t = scale_position(np.array([lev for lev, _ in lines]), vmax, s['colour'], s['decades'])
            colours = [QColor(*(int(c * 0.8) for c in table[int(k * 255)])) for k in t]
        else:
            colours = [QColor(20, 24, 27) if s['line_colour'] == 'dark' else QColor(255, 255, 255)] * len(lines)
        groups = {}                                  # (colour, width, dashed) -> [xy]
        for (lev, xy), c in zip(lines, colours):
            zero = nodal and lev == 0.0
            w = s['line_width'] * (2.0 if zero else 1.0)
            dashed = not zero and s['colour'] == 'signed' and lev < 0    # negative: dashed
            groups.setdefault((c.rgba(), w, dashed), []).append(xy)
        for (rgba, w, dashed), xys in groups.items():
            pen = pg.mkPen(QColor.fromRgba(rgba), width=w,
                           style=Qt.DashLine if dashed else Qt.SolidLine)
            xy = np.vstack(xys)
            it = pg.PlotCurveItem(xy[:, 0], xy[:, 1], pen=pen, connect='finite', antialias=True)
            it.setZValue(7.5)
            self.addItem(it)
            self.contour_items.append(it)

    def _draw_arrows(self, v, sin_t):
        if 'arrows' in self.items:
            self.removeItem(self.items.pop('arrows'))
        if not self.state['arrows'] or abs(sin_t) < 0.05:
            return
        g = self.sampler.grid
        (x0, x1), _ = self.vb.viewRange()
        mm_per_px = (x1 - x0) / max(self.vb.width(), 1)
        a = arrows(self.sampler, v, spacing_px=28 * g['shape'][1] / max(self.vb.width(), 1))
        if not len(a):
            return
        path = QPainterPath()
        sgn = self.state['plane_sign'] * (1 if sin_t > 0 else -1)
        for x, y, ux, uy, L in a:
            L *= 22 * mm_per_px * abs(sin_t)
            ux, uy = sgn * ux, sgn * uy
            bx, by = x + ux * L / 2, y + uy * L / 2
            path.moveTo(x - ux * L / 2, y - uy * L / 2)
            path.lineTo(bx, by)
            hd = min(5 * mm_per_px, 0.4 * L)            # the head: two short strokes
            path.moveTo(bx - ux * hd - uy * hd * 0.6, by - uy * hd + ux * hd * 0.6)
            path.lineTo(bx, by)
            path.lineTo(bx - ux * hd + uy * hd * 0.6, by - uy * hd - ux * hd * 0.6)
        it = _item(path, None, QColor(20, 24, 27), 8, width=1.3)
        self.items['arrows'] = it
        self.addItem(it)

    def colour_scale(self):
        """(vmax, label) of the colour bar for the current state."""
        if self.result is None or not len(self.result.modes.f):
            return None
        m = min(self.state['mode'], len(self.result.modes.f) - 1)
        if self.state['colour'] == 'current':
            return None
        return self.state['vmax'] or float(self.result.modes.peak[m])
