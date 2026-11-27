"""The Draw page (gui/drawtab.py, gui/draw3d.py): shapes drawn with the mouse,
picked, dragged, edited, undone, saved, and written into the window's
settings; several at once (Shift / Ctrl + click, a box), locked and hidden,
through the clipboard; exact edits, arrays, snapping and measuring; the 3D
view's camera, clip, layers, outline, gizmo and grid."""
import os
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('pyqtgraph')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(app):
    import pyqtgraph as pg
    from kicad2ems.gui.drawtab import DrawTab
    # every mouse move delivered (pyqtgraph drops those under 10 ms apart)
    rate = pg.getConfigOption('mouseRateLimit')
    pg.setConfigOption('mouseRateLimit', 0)
    t = DrawTab()
    t.resize(1200, 900)
    t.show()
    t.vb.setRange(xRange=(0, 20), yRange=(0, 20), padding=0)
    app.processEvents()
    yield t
    t.close()
    pg.setConfigOption('mouseRateLimit', rate)


def _settle(tab):
    """The plan painted: its axes take their widths (from their labels) when
    painted, moving the view under them; on a screen that is at once."""
    from PySide6.QtWidgets import QApplication
    tab.plot.grab()
    QApplication.processEvents()


def _at(tab, x, y):
    """The plan's viewport position of model point (x, y)."""
    from PySide6.QtCore import QPointF
    return tab.plot.mapFromScene(tab.vb.mapViewToScene(QPointF(x, y)))


def _click(tab, x, y, button=None, double=False, mods=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    b = button or Qt.LeftButton
    vp = tab.plot.viewport()
    _settle(tab)
    (QTest.mouseDClick if double else QTest.mouseClick)(
        vp, b, mods or Qt.NoModifier, _at(tab, x, y))


def _drag(tab, app, a, b, mods=None, button=None, keys=()):
    """A drag in the plan from model point a to b (keys: typed half way)."""
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    vp, mods = tab.plot.viewport(), mods or Qt.NoModifier
    btn = button or Qt.LeftButton

    def send(kind, xy, held):
        p = QPointF(_at(tab, *xy))
        QApplication.sendEvent(vp, QMouseEvent(
            kind, p, vp.mapToGlobal(p),
            btn if kind != QEvent.MouseMove else Qt.NoButton,
            btn if held else Qt.NoButton, mods))
        app.processEvents()
    _settle(tab)
    QTest.mousePress(vp, btn, mods, _at(tab, *a))
    app.processEvents()
    for k in range(1, 9):
        f = k / 8
        if k == 4:
            for key in keys:
                QTest.keyClick(tab, key)
        send(QEvent.MouseMove, (a[0] + (b[0] - a[0]) * f,
                                a[1] + (b[1] - a[1]) * f), True)
    send(QEvent.MouseButtonRelease, b, False)


def test_shapes_drawn_with_the_mouse(tab):
    from PySide6.QtCore import Qt
    tab.set_tool('rect')
    _click(tab, 2, 2)
    _click(tab, 6, 4)
    tab.set_tool('polygon')
    for x, y in ((10, 10), (14, 10), (12, 14)):
        _click(tab, x, y)
    _click(tab, 12, 14, button=Qt.RightButton)  # ends it
    tab.set_tool('circle')
    _click(tab, 5, 15)
    _click(tab, 7, 15)
    kinds = [s.kind for s in tab.drawing.shapes]
    assert kinds == ['rect', 'polygon', 'circle']
    r, p, c = tab.drawing.shapes
    assert r.points == [[2, 2], [6, 4]]  # snapped to the 0.1 mm grid
    assert len(p.points) == 3 and c.radius == pytest.approx(2)
    assert tab.list.topLevelItemCount() == 3 and tab.sel == 2
    # a click on the rectangle selects it
    tab.set_tool('select')
    _click(tab, 4, 3)
    assert tab.sel == 0 and tab.p_points.isEnabled()


def test_moving_editing_and_undo(tab):
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('rect', [[0, 0], [2, 2]]))
    tab.add(Shape('path', [[5, 5], [9, 5]], width=0.3))
    tab.select(0)
    # dragged by hand: the drag handler as pyqtgraph calls it
    before = tab.drawing.to_dict()
    tab.drawing.shapes[0] = tab.drawing.shapes[0].moved(3, 1)
    tab._commit(before)
    assert tab.drawing.shapes[0].points == [[3, 1], [5, 3]]
    # the form: a trace's width
    tab.select(1)
    tab.p_width.setValue(1.25)
    tab._edited()
    assert tab.drawing.shapes[1].width == 1.25
    tab.undo()
    assert tab.drawing.shapes[1].width == 0.3
    tab.undo()
    assert tab.drawing.shapes[0].points == [[0, 0], [2, 2]]
    tab.redo()
    assert tab.drawing.shapes[0].points == [[3, 1], [5, 3]]
    tab.select(0)
    tab.to_polygon()
    assert tab.drawing.shapes[0].kind == 'polygon'
    tab.delete()
    assert [s.kind for s in tab.drawing.shapes] == ['path']


def test_save_and_open(tab, tmp_path):
    from kicad2ems.core.shapes import Shape, Drawing
    tab.add(Shape('circle', [[1, 1]], radius=0.5, material='cut'))
    p = tab.save(str(tmp_path / 'd.json'))
    assert Drawing.load(p) == tab.drawing
    tab.new()
    assert not tab.drawing.shapes
    assert tab.load(p) and len(tab.drawing.shapes) == 1


def test_the_window_has_the_page_and_its_settings(app, tmp_path):
    from kicad2ems.gui.app import MainWindow
    from kicad2ems.core.shapes import Shape
    w = MainWindow()
    try:
        assert 'Draw' in w.docks and w.docks['Draw'].isHidden()
        w.acts['Draw'].trigger()
        assert not w.docks['Draw'].isHidden()
        t = w.draw
        t.add(Shape('rect', [[1, -4], [3, -2]]))
        t.add(Shape('port', [[1, -3]], layer='F.Cu'))
        t.select(0)
        t.region_from_selected()
        assert list(w.setup.values()['region']) == [1, 2, 3, 4]
        t.ports_to_settings()
        assert w.setup.values()['ports'] == ['P1@1,3,F.Cu']
        t.file = str(tmp_path / 'shapes.json')
        t.use_in_runs()
        assert w.setup.values()['shapes'] == t.file
    finally:
        w.setup.dirty = False
        w.close()


def _press_3d(tab, app, a, b, mods=None, keys=()):
    """A left-button drag in the 3D view from pixel a to b (a click: a == b;
    keys: typed half way)."""
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    v, mods = tab.view3d, mods or Qt.NoModifier

    def send(kind, xy, held):
        p = QPointF(*xy)
        btn = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
        QApplication.sendEvent(v, QMouseEvent(
            kind, p, v.mapToGlobal(p), btn,
            Qt.LeftButton if held else Qt.NoButton, mods))
        app.processEvents()
    send(QEvent.MouseButtonPress, a, True)
    for k in range(1, 9):
        f = k / 8
        if k == 2:
            for key in keys:
                QTest.keyClick(v, key)
        send(QEvent.MouseMove, (a[0] + (b[0] - a[0]) * f,
                                a[1] + (b[1] - a[1]) * f), True)
    send(QEvent.MouseButtonRelease, b, False)


def test_the_3d_view_selects_and_edits(tab, app):
    import numpy as np
    import pyqtgraph as pg
    from PySide6.QtCore import Qt
    from kicad2ems.core.shapes import Shape
    if tab.gl is None:
        pytest.skip('no 3D view here')
    tab.resize(1200, 1100)
    app.processEvents()
    tab.add(Shape('rect', [[0, 0], [4, 4]], material='metal', z0=2, z1=3))
    tab.add(Shape('circle', [[10, 2]], radius=1.5, layer='F.Cu'))
    tab.select(None)
    tab._redraw_3d()
    v = tab.view3d
    v.setCameraPosition(distance=40, elevation=35, azimuth=-60)
    v.opts['center'] = pg.Vector(5, 2, 1)
    # the ray under a point's pixel passes through it
    x, y = tab.project3d([1, 2, 3])
    o, d = tab.ray3d(x, y)
    assert np.linalg.norm(np.cross(np.array([1, 2, 3.]) - o, d)) < 1e-6
    P = tab.project3d
    # a click on the block selects it (the list and the form follow)
    _press_3d(tab, app, P([2, 2, 3]), P([2, 2, 3]))
    assert tab.selected == [0] and [
        tab.list.indexOfTopLevelItem(i) for i in tab.list.selectedItems()
    ] == [0]
    # a drag moves it on its level, snapped
    _press_3d(tab, app, P([2, 2, 3]), P([5, 3, 3]))
    assert tab.drawing.shapes[0].points == [[3, 1], [7, 5]]
    # Shift + drag on its top face stretches it
    _press_3d(tab, app, P([5, 3, 3]), P([5, 3, 5]), Qt.ShiftModifier)
    s = tab.drawing.shapes[0]
    assert (s.z0, s.z1) == (2, pytest.approx(5))
    # the disc on F.Cu
    _press_3d(tab, app, P([10, 2, 1.6]), P([10, 2, 1.6]))
    assert tab.sel == 1
    # a click on nothing: nothing selected, the camera where it was
    az = v.opts['azimuth']
    _press_3d(tab, app, (5, 5), (5, 5))
    assert tab.sel is None and v.opts['azimuth'] == az
    # a drag on nothing orbits, and leaves the shapes
    _press_3d(tab, app, (5, 5), (80, 5))
    assert v.opts['azimuth'] != az
    assert tab.drawing.shapes[0].points == [[3, 1], [7, 5]]
    tab.undo()
    assert tab.drawing.shapes[0].z1 == 3


def _three(tab):
    from kicad2ems.core.shapes import Shape
    for x in (2, 6, 10):
        tab.add(Shape('rect', [[x, 2], [x + 2, 4]]))
    tab.add(Shape('circle', [[15, 15]], radius=1))
    tab.select(None)


def test_several_selected_and_moved_together(tab, app):
    from PySide6.QtCore import Qt
    _three(tab)
    # Shift / Ctrl + click adds, and takes off
    _click(tab, 3, 3)
    _click(tab, 7, 3, mods=Qt.ShiftModifier)
    _click(tab, 11, 3, mods=Qt.ControlModifier)
    assert tab.selected == [0, 1, 2] and tab.sel == 2
    _click(tab, 7, 3, mods=Qt.ShiftModifier)
    assert tab.selected == [0, 2]
    # the list follows, and the form says it edits them all
    rows = sorted(tab.list.indexOfTopLevelItem(i)
                  for i in tab.list.selectedItems())
    assert rows == [0, 2] and tab.form_note.isVisibleTo(tab)
    # a drag from one moves both: one undo step
    n = len(tab._undo)
    _drag(tab, app, (3, 3), (4, 5))
    s = tab.drawing.shapes
    assert s[0].points == [[3, 4], [5, 6]] and s[2].points == [[11, 4],
                                                              [13, 6]]
    assert s[1].points == [[6, 2], [8, 4]] and len(tab._undo) == n + 1
    tab.undo()
    assert tab.drawing.shapes[0].points == [[2, 2], [4, 4]]
    # X while dragging: along x only
    _drag(tab, app, (3, 3), (5, 6), keys=[Qt.Key_X])
    assert tab.drawing.shapes[0].points == [[4, 2], [6, 4]]
    tab.undo()
    # Esc while dragging: put back, no step
    n = len(tab._undo)
    _drag(tab, app, (3, 3), (5, 6), keys=[Qt.Key_Escape])
    assert tab.drawing.shapes[0].points == [[2, 2], [4, 4]]
    assert len(tab._undo) == n and tab.selected == [0, 2]
    # a box on nothing: left to right takes what is wholly inside it
    _drag(tab, app, (1, 1), (9, 5))
    assert tab.selected == [0, 1]
    # right to left: what it touches; Shift adds
    _drag(tab, app, (11, 1), (9.5, 5), mods=Qt.ShiftModifier)
    assert sorted(tab.selected) == [0, 1, 2]
    # a click on nothing: none; Ctrl + drag pans, moving nothing
    _click(tab, 15, 8)
    assert tab.selected == []
    x0 = tab.vb.viewRange()[0][0]
    _drag(tab, app, (3, 3), (6, 3), mods=Qt.ControlModifier)
    assert tab.vb.viewRange()[0][0] < x0 - 1
    assert tab.drawing.shapes[0].points == [[2, 2], [4, 4]]
    # a change in the form reaches the whole selection
    tab.select_many([0, 3])
    tab.p_material.setCurrentText('metal')
    tab._edited()
    assert [s.material for s in tab.drawing.shapes] == ['metal', 'copper',
                                                        'copper', 'metal']


def test_duplicate_delete_and_the_clipboard(tab, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from kicad2ems.gui.drawtab import DrawTab
    _three(tab)
    tab.select_many([0, 2])
    n = len(tab._undo)
    tab.duplicate()
    assert len(tab.drawing.shapes) == 6 and tab.selected == [4, 5]
    assert tab.drawing.shapes[4].points == [[2.1, 1.9], [4.1, 3.9]]
    tab.delete()
    assert len(tab.drawing.shapes) == 4 and len(tab._undo) == n + 2
    tab.undo()
    assert len(tab.drawing.shapes) == 6
    tab.undo()
    # copy, and paste into another drawing (as another session would)
    tab.select_many([0, 2])
    tab.setFocus()
    QTest.keyClick(tab, Qt.Key_C, Qt.ControlModifier)
    other = DrawTab()
    try:
        other.paste()
        assert [s.points for s in other.drawing.shapes] == [
            [[2, 2], [4, 4]], [[10, 2], [12, 4]]]
    finally:
        other.close()
    # pasted where they came from: a grid step along
    QTest.keyClick(tab, Qt.Key_V, Qt.ControlModifier)
    assert len(tab.drawing.shapes) == 6
    assert tab.drawing.shapes[4].points == [[2.1, 1.9], [4.1, 3.9]]
    # Ctrl+A, Delete; Ctrl+Z
    QTest.keyClick(tab, Qt.Key_A, Qt.ControlModifier)
    assert len(tab.selected) == 6
    QTest.keyClick(tab, Qt.Key_Delete)
    assert not tab.drawing.shapes
    QTest.keyClick(tab, Qt.Key_Z, Qt.ControlModifier)
    assert len(tab.drawing.shapes) == 6
    # not shapes: said, nothing pasted
    assert tab.paste('hello') == [] and 'no shapes' in tab.status.text()


def test_locked_and_hidden_shapes(tab, app):
    from PySide6.QtCore import Qt
    _three(tab)
    lst = tab.list
    # hidden from the list's tick: not drawn, not picked, not boxed
    lst.topLevelItem(1).setCheckState(0, Qt.Unchecked)
    assert tab.drawing.shapes[1].hidden
    assert tab.hit(7, 3) is None
    assert sum(e is not None for e in tab._gfx) == 3  # (not drawn)
    _drag(tab, app, (1, 1), (13, 5))
    assert tab.selected == [0, 2]
    tab.undo()  # (the tick is a step too)
    assert not tab.drawing.shapes[1].hidden
    # locked: picked, not moved, turned or deleted
    lst.topLevelItem(0).setCheckState(1, Qt.Checked)
    assert tab.drawing.shapes[0].locked
    tab.select_many([0, 1])
    tab.move_by(1, 0)
    assert tab.drawing.shapes[0].points == [[2, 2], [4, 4]]
    assert tab.drawing.shapes[1].points == [[7, 2], [9, 4]]
    assert 'locked' in tab.status.text()
    _drag(tab, app, (3, 3), (3, 6))  # from the locked one: a box, no move
    assert tab.drawing.shapes[0].points == [[2, 2], [4, 4]]
    tab.select(0)
    tab.delete()
    assert len(tab.drawing.shapes) == 4
    # the buttons: the selection unlocked, hidden; saved with the drawing
    tab.select_many([0, 2])
    tab.set_locked(None)  # (one is: both are)
    assert tab.drawing.shapes[0].locked and tab.drawing.shapes[2].locked
    tab.set_locked(None)
    assert not tab.drawing.shapes[0].locked and not tab.drawing.shapes[2].locked
    tab.set_hidden(None)
    d = tab.drawing.to_dict()['shapes']
    assert d[0]['hidden'] and d[2]['hidden'] and 'hidden' not in d[1]


def test_exact_edits_and_arrays(tab, app):
    import pytest
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('rect', [[0, 0], [2, 1]]))
    tab.add(Shape('rect', [[4, 3], [5, 5]], material='metal', z0=1, z1=2))
    tab.select_many([0, 1])
    tab.mv[0].setValue(1)
    tab.mv[2].setValue(0.5)
    tab.side_tabs.setCurrentIndex(1)
    btn = [b for b in tab.side_tabs.currentWidget().findChildren(
        __import__('PySide6.QtWidgets').QtWidgets.QPushButton)
        if b.text() == 'Move'][0]
    btn.click()
    s = tab.drawing.shapes
    assert s[0].points == [[1, 0], [3, 1]] and s[1].z1 == 2.5
    # turned 90 degrees about a point: (1, 0) -> (0, 1)
    tab.rotate_by(90, (0, 0))
    assert tab.drawing.shapes[0].bounds() == pytest.approx((-1, 1, 0, 3))
    tab.undo()
    # off a right angle a rectangle becomes a polygon
    tab.select(0)
    tab.rotate_by(30, 'each')
    assert tab.drawing.shapes[0].kind == 'polygon'
    tab.undo()
    tab.select_many([0, 1])
    tab.mirror('y')
    assert tab.drawing.shapes[0].points == [[1, 5], [3, 4]]
    tab.undo()
    tab.align('left')
    assert tab.drawing.shapes[1].bounds()[0] == 1
    tab.align('top')
    assert tab.drawing.shapes[0].bounds()[3] == 5
    tab.undo()
    tab.undo()
    # arrays from the panel: a row of 5 vias, then a ring of 8
    tab.add(Shape('circle', [[10, 10]], radius=0.2, material='metal', z0=0,
                  z1=1.6, name='via'))
    tab.arr_kind.setCurrentText('row')
    tab.arr_n.setValue(5)
    tab.arr_dx.setValue(0.5)
    tab.arr_dy.setValue(0)
    tab._array_clicked()
    xs = [s.points[0][0] for s in tab.drawing.shapes[2:]]
    assert xs == [10, 10.5, 11, 11.5, 12] and len(tab.selected) == 5
    tab.select(2)
    tab.make_array('circle', n=8, centre=(10, 8))
    assert len(tab.drawing.shapes) == 14
    # (the via is at 90 degrees round it: its 2nd copy at 180)
    assert tab.drawing.shapes[-6].points[0] == pytest.approx([8, 8])
    tab.make_array('grid', n=3, ny=2, dx=1, dy=1)
    assert len(tab.drawing.shapes) == 14 + 5 * 8
    # distribute along x: the inner ones spaced evenly between the ends
    tab.select_many([2, 3, 6])
    tab.distribute('x')
    assert tab.drawing.shapes[3].points[0][0] == pytest.approx(11)


def test_snapping_measuring_and_the_status(tab, app):
    import pytest
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from kicad2ems.core.layout import board_layout
    lay = board_layout(os.path.join(os.path.dirname(__file__), '..', '..',
                                    'examples', 'sma_launch.kicad_pcb'))
    tab.set_board(lay)
    tab.vb.setRange(xRange=(20, 32), yRange=(-12, -2), padding=0)
    app.processEvents()
    # the trace's end: a click near it snaps onto it, marked and said
    tab.set_tool('rect')
    tab._moved(tab.vb.mapViewToScene(_qp(29.06, -6.95)))
    assert 'track end' in tab.coords.text()
    assert len(tab.snap_mark.data) == 1
    _click(tab, 29.06, -6.95)
    tab._moved(tab.vb.mapViewToScene(_qp(27.03, -9.01)))
    assert 'w 2.' in tab.coords.text() and 'h 2.' in tab.coords.text()
    _click(tab, 27.03, -9.01)
    r = tab.drawing.shapes[0]
    assert r.points[0] == [29, -7]  # the track's end, exactly
    # its own corner next: objects off, the grid alone
    tab.osnap.setChecked(False)
    assert tab._snap((29.06, -6.95)) == [29.1, -7.0]
    tab.osnap.setChecked(True)
    assert tab._snap((27.02, -9.0))[0] == pytest.approx(27.0)
    # the measure tool (M): two clicks, then a third for the angle
    tab.setFocus()
    QTest.keyClick(tab, Qt.Key_M)
    assert tab.tool == 'measure'
    tab.osnap.setChecked(False)
    _click(tab, 22, -4)
    _click(tab, 25, -8)
    assert '5.000 mm' in tab.status.text() and '-53.1°' in tab.status.text()
    _click(tab, 25, -4)
    assert 'between the legs' in tab.status.text()
    QTest.keyClick(tab, Qt.Key_Escape)
    assert tab.measure_pts == [] and tab.tool == 'measure'
    QTest.keyClick(tab, Qt.Key_Escape)
    assert tab.tool == 'select'
    # a picked point: the rotation's centre
    tab.side_tabs.setCurrentIndex(1)
    tab._pick(tab._set_rot_point, 'click')
    _click(tab, 24, -5)
    assert [w.value() for w in tab.rot_xy] == [24, -5]
    assert tab.rot_about.currentIndex() == 2


def _qp(x, y):
    from PySide6.QtCore import QPointF
    return QPointF(x, y)


def test_the_3d_view_camera_clip_and_gizmo(tab, app):
    import numpy as np
    import pytest
    import pyqtgraph as pg
    from PySide6.QtCore import Qt
    from kicad2ems.core.shapes import Shape
    if tab.gl is None:
        pytest.skip('no 3D view here')
    tab.resize(1200, 1100)
    app.processEvents()
    v = tab.view3d
    # the page's camera is pyqtgraph's own
    v.setCameraPosition(distance=30, elevation=25, azimuth=-70)
    vp = (0, 0, v.width(), v.height())
    m = np.array((v.projectionMatrix(vp, vp) * v.viewMatrix()).data(),
                 float).reshape(4, 4).T
    assert np.abs(m - tab._mvp()).max() < 1e-4
    tab.add(Shape('rect', [[0, 0], [4, 4]], material='metal', z0=2, z1=3))
    tab.add(Shape('circle', [[10, 2]], radius=1.5, layer='F.Cu'))
    tab.select(None)
    tab._redraw_3d()
    # from the top, fitted: both shapes on the screen, x right, y up
    tab.camera('top')
    (x0, y0), (x1, y1) = tab.project3d([0, 0, 3]), tab.project3d([10, 4, 3])
    assert x1 > x0 and y1 < y0
    for p in ([0, 0, 2], [11.5, 4, 3]):
        px, py = tab.project3d(p)
        assert 0 < px < v.width() and 0 < py < v.height()
    # a click selects; outlined, and the gizmo on it
    _press_3d(tab, app, tab.project3d([2, 2, 3]), tab.project3d([2, 2, 3]))
    assert tab.selected == [0]
    assert tab._outline_gl is not None and len(tab._outline_gl.pos) == 24
    base, length, axes = tab._gizmo
    assert axes == (0, 1, 2) and base[2] == 3
    # Fit: the selection fills the view
    d0 = v.opts['distance']
    tab.frame_3d()
    assert v.opts['distance'] < d0
    assert tuple(v.opts['center']) == pytest.approx((2, 2, 2.5))
    # the gizmo's z arrow lifts it, from the side
    tab.camera('front')
    tab._update_gizmo_3d()
    base, length, _ = tab._gizmo
    a = tab.project3d(base + [0, 0, 0.6 * length])
    _press_3d(tab, app, a, (a[0], a[1] - 40))
    s = tab.drawing.shapes[0]
    assert s.z0 > 2 and s.z1 - s.z0 == pytest.approx(1)
    assert s.points == [[0, 0], [4, 4]]
    tab.undo()
    # Z while dragging the block: it rises instead of sliding
    tab.camera('iso')
    tab._redraw_3d()
    p = tab.project3d([2, 2, 3])
    _press_3d(tab, app, p, (p[0] + 30, p[1] - 30), keys=[Qt.Key_Z])
    s = tab.drawing.shapes[0]
    assert s.points == [[0, 0], [4, 4]] and s.z0 != 2
    tab.undo()
    # Esc while dragging: put back, and the rest of the drag moves nothing
    n = len(tab._undo)
    _press_3d(tab, app, p, (p[0] + 60, p[1]), keys=[Qt.Key_Escape])
    assert tab.drawing.shapes[0].points == [[0, 0], [4, 4]]
    assert len(tab._undo) == n
    # the clip plane: x past 2 is gone, and not picked
    tab.camera('top')
    tab.set_clip(0, 2.0)
    tab._redraw_3d()
    assert tab._meshes3d[0][0][:, 0].max() == pytest.approx(2)
    assert tab.pick3d(*tab.project3d([3, 2, 3]))[0] is None
    assert tab.pick3d(*tab.project3d([1, 2, 3]))[0] == 0
    tab.set_clip(None)
    # metal not shown: neither drawn nor picked
    tab.set_shown('metal', False)
    tab._redraw_3d()
    assert 0 not in tab._meshes3d
    assert tab.pick3d(*tab.project3d([1, 2, 3]))[0] is None
    tab.set_shown('metal', True)
    # the grid at the working level: the layer drawn on, or a solid's bottom
    tab._redraw_3d()
    assert tab._grid_gl is not None
    assert tab._grid_gl.transform().map(pg.Vector(0, 0, 0)).z() == (
        pytest.approx(1.6))
    tab.material.setCurrentText('metal')
    tab.z0.setValue(2.5)
    assert tab._grid_gl.transform().map(pg.Vector(0, 0, 0)).z() == (
        pytest.approx(2.5))


def test_the_3d_view_keeps_what_did_not_change(tab, app):
    import numpy as np
    import pytest
    from kicad2ems.core import shapes as sh
    from kicad2ems.core.shapes import Shape
    if tab.gl is None:
        pytest.skip('no 3D view here')
    kinds = [Shape('rect', [[0, 0], [2, 1]], layer='F.Cu'),
             Shape('circle', [[5, 5]], radius=0.4, material='cut',
                   layer='B.Cu'),
             Shape('polygon', [[0, 0], [3, 0], [3, 3], [2, 1]],
                   material='dielectric', z0=1.6, z1=2.2),
             Shape('path', [[0, 4], [4, 4], [4, 8]], width=0.3),
             Shape('port', [[1, 1]])]
    # the page's own meshes are core.shapes.shape_mesh's solids
    for s in kinds:
        mine, theirs = tab._shape_mesh3d(s), sh.shape_mesh(s, tab.stack)
        vol = [np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])
                         ).sum() / 6 for t in (mine[0][mine[1]],
                                               theirs[0][theirs[1]])]
        assert vol[0] == pytest.approx(vol[1], rel=1e-9)
        assert mine[0].min(0) == pytest.approx(theirs[0].min(0))
        assert mine[0].max(0) == pytest.approx(theirs[0].max(0))
    for s in kinds:
        tab.add(s)
    tab._redraw_3d()
    items = dict(tab._shape_gl)
    # a shape added: the others keep their items; one moved: only its own
    tab.add(Shape('rect', [[8, 8], [9, 9]], material='metal', z0=0, z1=1))
    tab._redraw_3d()
    assert all(tab._shape_gl[i] is items[i] for i in items)
    tab.select(1)
    tab.move_by(1, 0)
    tab._redraw_3d()
    assert tab._shape_gl[1] is not items[1]
    assert tab._shape_gl[0] is items[0] and tab._shape_gl[2] is items[2]
    # a selection recolours, nothing is made again
    tab.select_many([0, 2])
    assert tab._shape_gl[0] is items[0]
    assert tab._entries3d[0]['look'][0] != tab._entries3d[3]['look'][0]
    # the whole drawing dragged in 3D: moved by a transform, then the
    # drawing at the release, one undo step
    tab.select_all()
    tab._redraw_3d()
    tab.camera('top')
    n = len(tab._undo)
    a = tab.project3d([8.5, 8.5, 1])  # (on the metal block's top)
    b = tab.project3d([10.5, 9.5, 1])
    _press_3d(tab, app, a, b)
    assert tab.drawing.shapes[0].points == [[2, 1], [4, 2]]
    assert tab.drawing.shapes[5].points == [[10, 9], [11, 10]]
    assert len(tab._undo) == n + 1
