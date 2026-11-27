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


def test_a_port_keeps_its_label(tab, app):
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('port', [[5, 5]], name='P1'))
    tab.add(Shape('rect', [[10, 10], [12, 12]]))

    def label_near(x, y):
        p = tab._gfx[0]['items'][1].pos()
        return abs(p.x() - x) < 1 and abs(p.y() - y) < 1
    txt = tab._gfx[0]['items'][1]
    assert label_near(5, 5)
    # the plan drawn again: the port's items kept, and where they were
    tab.select(1)
    tab._refresh()
    assert tab._gfx[0]['items'][1] is txt and label_near(5, 5)
    # dragged: the label goes with it
    _drag(tab, app, (5, 5), (7, 6))
    assert tab.drawing.shapes[0].points == [[7, 6]] and label_near(7, 6)


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


# ---- ferrites and magnets ----
def _circulator(tab, gap=1.0, br=1.2):
    """A ferrite puck (r 3 mm, 0.5 mm thick) on the default board, under a
    magnet (r 5 mm, 4 mm tall) `gap` over it; their indices."""
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('circle', [[0, 0]], radius=3, material='ferrite', z0=1.6,
                  z1=2.1, er=15, tand=2e-4, name='puck'))
    tab.add(Shape('circle', [[0, 0]], radius=5, material='magnet',
                  z0=2.1 + gap, z1=6.1 + gap, br=br, name='magnet'))
    return 0, 1


def _wait(app, tab):
    """The bias readout's timer run out (it is put off a moment)."""
    import time
    end = time.time() + 2
    while tab._bias_timer.isActive() and time.time() < end:
        app.processEvents()
    app.processEvents()


def test_ferrite_and_magnet_fields(tab, app, tmp_path):
    from kicad2ems.core.shapes import Shape, Drawing
    from kicad2ems.gui.drawmagnets import FERRITE_ER
    from kicad2ems.gui.drawtab import COLOURS
    # both offered, in their own colours
    for box in (tab.material, tab.p_material):
        items = [box.itemText(k) for k in range(box.count())]
        assert 'ferrite' in items and 'magnet' in items
    assert len({COLOURS[m] for m in COLOURS}) == len(COLOURS)
    # a new ferrite takes a ferrite's er, not a laminate's
    tab.material.setCurrentText('ferrite')
    s = tab._new_shape('circle', [[0, 0]], radius=2)
    assert s.material == 'ferrite' and s.er == FERRITE_ER
    assert s.ms == 1800 and s.dh == 30
    tab.material.setCurrentText('copper')
    f, m = _circulator(tab)
    vis = lambda key: tab.form.isRowVisible(tab.rows[key])
    tab.select(f)
    assert all(vis(k) for k in ('er', 'tand', 'ms', 'dh', 'bias'))
    assert not vis('br') and not vis('pole') and not vis('layer')
    assert tab.p_ms.value() == 1800 and tab.p_dh.value() == 30
    tab.select(m)
    assert vis('br') and vis('pole') and vis('bias')
    assert not any(vis(k) for k in ('er', 'tand', 'ms', 'dh'))
    assert tab.p_br.value() == 1.2 and tab.p_pole.currentIndex() == 0
    assert 'Br 1.2 T, N up' in tab.list.topLevelItem(m).text(2)
    # the ferrite's fields into the shape, undone and done again
    tab.select(f)
    tab.p_ms.setValue(2200)
    tab.p_dh.setValue(45.5)
    tab._edited()
    assert (tab.drawing.shapes[f].ms, tab.drawing.shapes[f].dh) == (2200,
                                                                    45.5)
    assert '4πMs 2200 G' in tab.list.topLevelItem(f).text(2)
    tab.undo()
    assert (tab.drawing.shapes[f].ms, tab.drawing.shapes[f].dh) == (1800, 30)
    tab.redo()
    assert tab.drawing.shapes[f].ms == 2200
    # the magnet turned over: its remanence signed, the form as it was
    tab.select(m)
    tab.p_br.setValue(1.35)
    tab.p_pole.setCurrentIndex(1)
    tab._edited()
    assert tab.drawing.shapes[m].br == -1.35
    assert tab.drawing.shapes[m].to_dict()['br'] == -1.35
    assert 'N down' in tab.list.topLevelItem(m).text(2)
    tab.select(f)
    tab.select(m)
    assert tab.p_br.value() == 1.35 and tab.p_pole.currentIndex() == 1
    tab.undo()
    assert tab.drawing.shapes[m].br == 1.2
    tab.redo()
    # a dielectric made a ferrite: er 4.3 (unset) becomes 15, a set one stays
    tab.add(Shape('rect', [[5, 5], [8, 8]], material='dielectric', z0=0,
                  z1=1))
    tab.add(Shape('rect', [[9, 5], [12, 8]], material='dielectric', z0=0,
                  z1=1, er=10.2))
    tab.select_many([3, 2])
    tab.p_material.setCurrentText('ferrite')
    tab._edited()
    a, b = tab.drawing.shapes[2], tab.drawing.shapes[3]
    assert (a.material, a.er) == ('ferrite', FERRITE_ER)
    assert (b.material, b.er) == ('ferrite', 10.2)
    # a change of the form applies to every selected ferrite
    tab.p_ms.setValue(5000)
    tab._edited()
    assert tab.drawing.shapes[2].ms == 5000
    assert tab.drawing.shapes[3].ms == 5000
    # written and read back with them
    p = tab.save(str(tmp_path / 'circ.json'))
    d = Drawing.load(p)
    assert d == tab.drawing
    assert d.shapes[m].br == -1.35 and d.shapes[f].dh == 45.5
    # drawn in the plan in their colours (no KeyError, items made)
    assert all(e is not None for e in tab._gfx)


def test_the_bias_readout(tab, app):
    import time
    from kicad2ems.core import magnets
    from kicad2ems.gui import drawmagnets as dm
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('circle', [[0, 0]], radius=3, material='ferrite', z0=1.6,
                  z1=2.1, er=15, name='puck'))
    tab.select(0)
    _wait(app, tab)
    assert 'No magnet drawn: the ferrite is unbiased' in tab.p_bias.text()
    assert 'fm = γ4πMs 5.04 GHz' in tab.p_bias.text()
    tab.add(Shape('circle', [[0, 0]], radius=5, material='magnet', z0=3.1,
                  z1=7.1, br=1.2, name='magnet'))
    tab.select(0)
    a = time.perf_counter()
    text = tab.bias_text()
    took = time.perf_counter() - a
    assert took < 1.0, took   # (about 20 ms)
    # core.magnets' numbers, on the board with the drawing's solids
    r, = magnets.summary(dm.bias_board(None, tab.stack,
                                       tab.drawing.shapes)[0])
    assert r['saturated'] == 1.0 and r['internal_oe'][0] > 0
    lo, mean, hi = r['internal_oe']
    assert text.startswith(f"Internal field {lo:.0f} to {hi:.0f} Oe, mean "
                           f"{mean:.0f}, magnetised up (+z), saturated "
                           "throughout")
    assert f"f0 = γH0 {r['f0'] / 1e9:.2f} GHz" in text
    # kept while the solids are the same; the form shows it soon after
    kept = tab._bias_cache
    assert tab.bias_text() == text and tab._bias_cache is kept
    _wait(app, tab)
    assert tab.p_bias.text() == text and tab.p_bias.isVisible()
    # the magnet's: its direction, and the ferrite's line
    tab.select(1)
    _wait(app, tab)
    assert tab.p_bias.text().splitlines() == [
        'Magnetised up (+z): N on top (red), S below (blue).',
        f"puck: {text}"]
    # turned over: the ferrite magnetised down, the same field's size
    tab.p_pole.setCurrentIndex(1)
    tab._edited()
    assert tab._bias_cache is kept  # (not worked out in the edit)
    assert 'magnetised down (−z)' in tab.bias_text()
    assert f"{lo:.0f} to {hi:.0f} Oe" in tab.bias_text()
    # far off, it cannot saturate the puck
    tab.move_by(0, 0, 40)
    tab.select(0)
    assert tab.bias_text().startswith('Not saturated anywhere')
    # a redraw does not work it out (the timer does, if it is seen)
    tab._bias_cache = None
    tab._refresh()
    assert tab._bias_cache is None
    tab.side_tabs.setCurrentIndex(1)  # (the form not seen)
    _wait(app, tab)
    assert tab._bias_cache is None
    tab.side_tabs.setCurrentIndex(0)
    _wait(app, tab)
    assert tab._bias_cache is not None
    # anything else has no readout, and the row is hidden
    tab.add(Shape('rect', [[5, 5], [6, 6]]))
    assert tab.bias_text() == ''
    assert not tab.form.isRowVisible(tab.p_bias)


def test_the_bias_readouts_words():
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui import drawmagnets as dm
    row = dict(name='circle', index=0, applied_oe=(900.0, 1000.0, 1100.0),
               internal_oe=None, saturated=0.0, f0=0.0, fm=5.04e9, sign=1.0)
    fer = Shape('circle', [[0, 0]], radius=3, material='ferrite', z0=0,
                z1=0.5)
    mag = Shape('circle', [[0, 0]], radius=5, material='magnet', z0=1, z1=5,
                br=0.0)
    assert dm.readout([row], [fer], 0).startswith('No magnet drawn')
    assert dm.readout([row], [fer, mag], 0).startswith(
        'The magnets have no remanence (Br 0)')
    mag.br = -1.2
    assert dm.readout([row], [fer, mag], 0).startswith(
        "Not saturated anywhere: the magnets' Hz over it (900 to 1100 Oe, "
        'mean 1000)')
    row.update(internal_oe=(-300.0, -200.0, -100.0), saturated=0.62,
               f0=0.56e9, sign=-1.0)
    t = dm.readout([row], [fer, mag], 0)
    assert t.startswith('Internal field 100 to 300 Oe, mean 200, magnetised '
                        'down (−z), saturated over 62 % of it')
    assert 'f0 = γH0 0.56 GHz' in t
    assert dm.readout([row], [fer, mag], 1).splitlines()[0] == (
        'Magnetised down (−z): S on top (blue), N below (red).')
    assert dm.readout([], [mag], 0).splitlines()[1] == (
        'No ferrite drawn: nothing for it to bias.')
    assert dm.readout([], [fer, mag], 0).startswith('Nothing of it is left')
    assert dm.readout([row], [Shape('rect', [[0, 0], [1, 1]])], 0) == ''
    # (a port is no solid of the board)
    assert dm.solid_index([fer, Shape('port', [[0, 0]]), mag]) == {0: 0, 2: 1}


def test_the_bias_needs_only_the_solids_by_the_ferrite():
    from kicad2ems.core import magnets
    from kicad2ems.core import shapes as sh
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui import drawmagnets as dm
    via = lambda x, y, z1=1.6: Shape('circle', [[x, y]], radius=0.15,
                                     material='metal', z0=0, z1=z1)
    early = via(0, 0, 3)                    # before the puck: under it
    fer = Shape('circle', [[0, 0]], radius=3, material='ferrite', z0=1.6,
                z1=2.1, er=15)
    fence = [via(x, -8) for x in range(-10, 11)]
    through = via(1, 1, 3)                  # after it, through it
    beside = via(10, 10, 3)                 # after it, off it
    lid = Shape('rect', [[-6, -6], [6, 6]], material='dielectric', z0=2.1,
                z1=2.6)                     # after it, only touching
    mag = Shape('circle', [[0, 0]], radius=5, material='magnet', z0=3.1,
                z1=7.1, br=1.2)
    shapes = [early, fer] + fence + [through, beside, lid, mag]
    keep = dm.bearing(shapes)
    n = len(shapes)
    assert keep == [1, n - 4, n - 1]
    b, at = dm.bias_board(None, sh.stack_of(None), shapes)
    assert at == {1: 0, n - 4: 1, n - 1: 2}
    assert [d['material'] for d in b.solids] == ['ferrite', 'metal',
                                                 'magnet']
    # the same bias as with every solid (a through via makes a hole)
    whole = sh.stack_of(None)
    full = dm._Board(None, whole)
    sh.apply(full, sh.Drawing(shapes))
    r0, = magnets.summary(full)
    r1, = magnets.summary(b)
    assert r1['saturated'] == r0['saturated'] == 1.0
    assert r1['applied_oe'] == pytest.approx(r0['applied_oe'], rel=0.02)
    assert r1['internal_oe'] == pytest.approx(r0['internal_oe'], rel=0.02)
    assert dm.readout([r1], shapes, 1, at).startswith('Internal field')


def test_a_magnets_two_halves():
    import numpy as np
    from shapely.geometry import Point, box
    from kicad2ems.core.view3d import prism_fast
    from kicad2ems.gui import drawmagnets as dm
    vol = lambda v, f: np.einsum('ij,ij->i', v[f][:, 0], np.cross(
        v[f][:, 1], v[f][:, 2])).sum() / 6
    for g in (Point(0, 0).buffer(5, quad_segs=16), box(0, 0, 2, 1),
              box(0, 0, 4, 4).difference(box(1, 1, 3, 3))):
        v, f = dm.magnet_mesh(g, 1.0, 5.0, 3.0)
        assert vol(v, f) == pytest.approx(g.area * 4, rel=1e-9)
        zc = v[f][:, :, 2]
        # each face wholly in one half, none between them
        assert (((zc >= 3.0 - 1e-12).all(1)) | ((zc <= 3.0 + 1e-12).all(1))
                ).all()
        assert not (np.abs(zc - 3.0) < 1e-12).all(1).any()
        c = dm.pole_colours(v, f, 3.0, (1, 0, 0, 1), (0, 0, 1, 1))
        up = zc.mean(1) > 3.0
        assert c.shape == (len(f), 4) and (c[up, 0] == 1).all() and (
            c[~up, 2] == 1).all() and up.any() and (~up).any()
        # its middle not inside it (a clip): one prism
        v1, f1 = dm.magnet_mesh(g, 1.0, 2.0, 3.0)
        v2, f2 = prism_fast(g, 1.0, 2.0)
        assert np.array_equal(v1, v2) and np.array_equal(f1, f2)
    assert dm.poles(1.2) == (dm.N_RGB, dm.S_RGB)
    assert dm.poles(-0.4) == (dm.S_RGB, dm.N_RGB)


def test_the_3d_view_draws_the_magnets_poles(tab, app):
    import numpy as np
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui import drawmagnets as dm
    if tab.gl is None:
        pytest.skip('no 3D view here')
    f, m = _circulator(tab)
    tab.select(None)
    tab._redraw_3d()
    e, ef = tab._entries3d[m], tab._entries3d[f]
    item = e['item']
    col = item.opts['meshdata'].faceColors()
    v, fc = e['mesh']
    up = v[fc][:, :, 2].mean(1) > 5.1
    n, s = (np.asarray(c) / 255 for c in (dm.N_RGB, dm.S_RGB))
    assert np.allclose(col[up, :3], n) and np.allclose(col[~up, :3], s)
    # the ferrite: one colour, its own, darker than a metal's
    assert ef['item'].opts['meshdata'].faceColors() is None
    metal = Shape('rect', [[0, 0], [1, 1]], material='metal', z0=0, z1=1)
    assert sum(ef['look'][0][:3]) < 0.7 * sum(tab._look(-1, metal)[0][:3])
    # selected: tinted as any selected shape is, the same item; turned over:
    # S on top, the same item
    tab.select(m)
    assert tab._entries3d[m]['item'] is item
    lit = item.opts['meshdata'].faceColors()
    tint = np.array([120, 190, 255]) / 255
    assert np.allclose(lit[up, :3], 0.55 * n + 0.45 * tint)
    assert np.allclose(lit[~up, :3], 0.55 * s + 0.45 * tint)
    tab.p_pole.setCurrentIndex(1)
    tab._edited()
    tab._redraw_3d()
    assert tab._entries3d[m]['item'] is item
    col = item.opts['meshdata'].faceColors()
    assert col[up, 2].mean() > col[up, 0].mean()   # blue (S) on top
    # stretched in 3D: its halves meet at its new middle
    tab.drawing.shapes[m] = tab.drawing.shapes[m].lifted(2.0, 'top')
    tab._update_shape_3d(m)
    v, fc = tab._entries3d[m]['mesh']
    t = tab.drawing.shapes[m]
    col = item.opts['meshdata'].faceColors()
    up = v[fc][:, :, 2].mean(1) > (t.z0 + t.z1) / 2
    assert len(col) == len(fc) and (col[up, 2] > col[up, 0]).all()
    assert (col[~up, 0] > col[~up, 2]).all()
    # the Show menu has them, and hides them
    tab.set_shown('magnet', False)
    tab._redraw_3d()
    assert m not in tab._entries3d and f in tab._entries3d
