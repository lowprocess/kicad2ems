#!/usr/bin/env python3
"""
The Draw page's speed with many shapes: a via fence of 300 metal posts (an
array of circles through the board) over a real example board, and the times
of what a user does with it, in the plan and in 3D, with the peak memory.

    python tools/gui/draw_bench.py                  # sma_launch, 300 vias
    python tools/gui/draw_bench.py --board B.kicad_pcb --n 600 --repeat 30

Runs offscreen (QT_QPA_PLATFORM=offscreen unless set): the plan is painted
(QWidget.grab) but the 3D view draws nothing there, so its times are what the
page does in Python (meshes, picking, items) and not the GPU's. Each time is
the median of --repeat tries, in ms:

    plan redraw   the plan and the list made again (DrawTab._refresh)
    plan paint    the plan painted (grab)
    plan pick     a click's shape found (DrawTab.hit), on a via and on nothing
    plan drag     one mouse move of a drag of a via (the event and the redraw)
    plan box      a rubber band over the fence
    select all    Ctrl+A: the list, the plan and the 3D view follow
    plan drag all one mouse move of a drag of the whole fence
    array         the fence made from one via (Array: a row), and undone
    3D redraw     the 3D view's items made again (DrawTab._redraw_3d)
    3D pick       the shape under a pixel (DrawTab.pick3d)
    3D drag       one mouse move of a drag of a via in 3D
    3D drag all   one mouse move of a drag of the whole fence in 3D
    peak memory   the process's peak RSS over the board's (MB)

(The page of 2026-11-20 had no box, selection of several or array: those
lines are left out for it.)
"""
import argparse, os, resource, statistics, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))


def fence(n, layout):
    """n metal posts through the board in four rows along its trace (model
    mm), as a list of Shapes."""
    from shapely.geometry import shape as geo
    from kicad2ems.core.shapes import Shape
    x0, y0, x1, y1 = geo(layout['outline']).bounds
    zs = [l['z0'] for l in layout['stack']['layers']] + [
        l['z1'] for l in layout['stack']['layers']]
    z0, z1 = min(zs), max(zs)
    yc = (y0 + y1) / 2
    rows = [yc - 2.0, yc - 1.2, yc + 1.2, yc + 2.0]
    per = -(-n // len(rows))
    pitch = (x1 - x0 - 3.0) / per
    out = []
    for r, y in enumerate(rows):
        for k in range(per):
            if len(out) == n:
                break
            out.append(Shape('circle', [[x0 + 1.5 + k * pitch, y]],
                             radius=min(0.125, 0.4 * pitch), material='metal',
                             z0=z0, z1=z1, name=f"via{len(out) + 1}"))
    return out


def ms(f, repeat):
    t = []
    for _ in range(repeat):
        a = time.perf_counter()
        f()
        t.append((time.perf_counter() - a) * 1e3)
    return statistics.median(t)


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--board', default=os.path.join(REPO, 'examples',
                                                    'sma_launch.kicad_pcb'))
    ap.add_argument('--n', type=int, default=300)
    ap.add_argument('--repeat', type=int, default=15)
    a = ap.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    from kicad2ems.core.layout import board_layout
    from kicad2ems.core.shapes import Drawing
    from kicad2ems.gui.drawtab import DrawTab
    import pyqtgraph as pg
    # every mouse move delivered (pyqtgraph drops those under 10 ms apart)
    pg.setConfigOption('mouseRateLimit', 0)
    app = QApplication.instance() or QApplication([])
    t = time.perf_counter()
    layout = board_layout(a.board)
    load = time.perf_counter() - t
    tab = DrawTab()
    tab.resize(1400, 1000)
    tab.show()
    app.processEvents()
    tab.set_board(layout)
    tab._redraw_3d()
    app.processEvents()
    base = rss_mb()
    shapes = fence(a.n, layout)
    t = time.perf_counter()
    tab.drawing = Drawing(shapes)
    tab._refresh()
    app.processEvents()
    first = time.perf_counter() - t
    out = {}
    out['plan redraw'] = ms(tab._refresh, a.repeat)
    out['plan paint'] = ms(lambda: tab.plot.grab(), a.repeat)
    v = shapes[a.n // 2]
    vx, vy = v.points[0]
    out['plan pick (a via)'] = ms(lambda: tab.hit(vx, vy), a.repeat)
    out['plan pick (nothing)'] = ms(lambda: tab.hit(vx, vy - 0.6), a.repeat)
    assert tab.hit(vx, vy) is not None and tab.hit(vx, vy - 0.6) is None

    def send(w, kind, p, held, mods=Qt.NoModifier):
        p = QPointF(*p)
        btn = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
        QApplication.sendEvent(w, QMouseEvent(
            kind, p, w.mapToGlobal(p), btn,
            Qt.LeftButton if held else Qt.NoButton, mods))

    def at(x, y):
        q = tab.plot.mapFromScene(tab.vb.mapViewToScene(QPointF(x, y)))
        return q.x(), q.y()

    def drag(w, a_, b_, steps, mods=Qt.NoModifier):
        """Per-move times (ms) of a drag from pixel a_ to b_."""
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPoint
        tab.plot.grab()  # (the axes' widths settled)
        app.processEvents()
        if w is vp:  # (a sent press is put off by the axis' width)
            QTest.mousePress(w, Qt.LeftButton, mods, QPoint(*map(round, a_)))
        else:
            send(w, QEvent.MouseButtonPress, a_, True, mods)
        app.processEvents()
        ts = []
        for k in range(1, steps + 1):
            f = k / steps
            p = (a_[0] + (b_[0] - a_[0]) * f, a_[1] + (b_[1] - a_[1]) * f)
            s = time.perf_counter()
            send(w, QEvent.MouseMove, p, True, mods)
            app.processEvents()
            ts.append((time.perf_counter() - s) * 1e3)
        send(w, QEvent.MouseButtonRelease, b_, False, mods)
        app.processEvents()
        return statistics.median(ts[1:])

    vp = tab.plot.viewport()
    tab.set_tool('select')
    before = [s.points for s in tab.drawing.shapes]
    out['plan drag'] = drag(vp, at(vx, vy), at(vx + 1.0, vy + 0.5), a.repeat)
    moved = sum(s.points != p for s, p in zip(tab.drawing.shapes, before))
    tab.undo()
    xs = [s.points[0][0] for s in shapes]
    ys = [s.points[0][1] for s in shapes]
    if hasattr(tab, 'box_select'):
        x0, x1 = min(xs) - 0.5, max(xs) + 0.5
        y0, y1 = min(ys) - 0.5, max(ys) + 0.5
        out['plan box'] = ms(lambda: tab.box_select(x0, y0, x1, y1), a.repeat)
        n_box = len(tab.selected)
        tab.select(None)
    else:
        n_box = None
    many = hasattr(tab, 'select_all')
    if many:
        def sel_all():
            tab.select(None)
            a_ = time.perf_counter()
            tab.select_all()
            return (time.perf_counter() - a_) * 1e3
        out['select all'] = statistics.median(sel_all() for _ in
                                              range(a.repeat))
        out['plan drag all'] = drag(vp, at(vx, vy), at(vx + 1.0, vy + 0.5),
                                    a.repeat)
        moved_all = sum(s.points != p for s, p in zip(tab.drawing.shapes,
                                                       before))
        tab.undo()

        def array():
            keep = tab.drawing
            tab.drawing = Drawing([shapes[0]])
            tab._refresh()
            tab.select(0)
            a_ = time.perf_counter()
            tab.make_array('row', n=a.n, dx=0.1, dy=0)
            app.processEvents()
            t_ = (time.perf_counter() - a_) * 1e3
            tab.drawing = keep
            tab._refresh()
            return t_
        out['array'] = statistics.median(array() for _ in range(5))
        app.processEvents()
    if tab.gl is not None:
        out['3D redraw'] = ms(tab._redraw_3d, a.repeat)
        view = tab.view3d
        view.setCameraPosition(distance=40, elevation=50, azimuth=-90)
        view.opts['center'] = pg.Vector(vx, vy, 0.7)
        z1 = v.z1
        px = tab.project3d([vx, vy, z1])
        out['3D pick (a via)'] = ms(lambda: tab.pick3d(*px), a.repeat)
        assert tab.pick3d(*px)[0] == a.n // 2, tab.pick3d(*px)
        out['3D pick (nothing)'] = ms(lambda: tab.pick3d(5, 5), a.repeat)
        q = tab.project3d([vx + 1.0, vy + 0.5, z1])
        tab.select(None)
        out['3D drag'] = drag(view, px, q, a.repeat)
        moved3 = tab.drawing.shapes[a.n // 2].points != v.points
        tab.undo()
        if many:
            tab.select_all()
            tab._ensure_3d()
            out['3D drag all'] = drag(view, px, q, a.repeat)
            moved3 = (moved3, sum(s.points != p for s, p in zip(
                tab.drawing.shapes, before)))
            tab.undo()
    else:
        moved3 = None
    peak = rss_mb() - base
    print(f"board {os.path.basename(a.board)} (read in {load:.2f} s), "
          f"{a.n} vias, median of {a.repeat}; first drawn in {first * 1e3:.0f} "
          f"ms; dragged: plan {moved} shape(s)"
          + (f" ({moved_all} all)" if many else '') +
          f", 3D {moved3}; box: {n_box}")
    for k, v_ in out.items():
        print(f"  {k:22s} {v_:9.2f} ms")
    print(f"  {'peak memory':22s} {peak:9.1f} MB over the board's "
          f"({rss_mb():.0f} MB in all)")
    tab.close()


if __name__ == '__main__':
    sys.exit(main())
