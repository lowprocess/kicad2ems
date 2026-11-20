"""The Draw page (gui/drawtab.py): shapes drawn with the mouse, picked, dragged,
edited, undone, saved, and written into the window's settings."""
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
    from kicad2ems.gui.drawtab import DrawTab
    t = DrawTab()
    t.resize(1200, 900)
    t.show()
    t.vb.setRange(xRange=(0, 20), yRange=(0, 20), padding=0)
    app.processEvents()
    yield t
    t.close()


def _at(tab, x, y):
    """The plan's viewport position of model point (x, y)."""
    from PySide6.QtCore import QPointF
    return tab.plot.mapFromScene(tab.vb.mapViewToScene(QPointF(x, y)))


def _click(tab, x, y, button=None, double=False):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    b = button or Qt.LeftButton
    vp = tab.plot.viewport()
    (QTest.mouseDClick if double else QTest.mouseClick)(vp, b, Qt.NoModifier,
                                                        _at(tab, x, y))


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
    assert tab.list.count() == 3 and tab.sel == 2
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
