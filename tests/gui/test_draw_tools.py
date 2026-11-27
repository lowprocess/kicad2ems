"""The Draw page's modelling tools (gui/drawtab.py, gui/draw3d.py): its menus,
the list's icons and the form's groups, templates placed with a click,
booleans and rounded corners, the arrow keys' nudge and select alike; the 3D
view's orthographic camera, arrowheads and box selection."""
import os
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('pyqtgraph')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# (the Draw page's fixtures and mouse helpers)
from test_drawtab import (app, tab, _click, _press_3d,  # noqa: E402,F401
                          _wait, _circulator)


def test_the_page_menus_list_and_form(tab, app):
    """The page's own menus (Drawing, Edit, Modify, Templates, View) do what
    its buttons and keys do; the list has an icon a shape, the form its
    fields in groups, those a shape does not use hidden."""
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui.drawtab import _kind_icon
    bar = tab.layout().menuBar()
    assert [a.text() for a in bar.actions()] == [
        'Drawing', 'Edit', 'Modify', 'Templates', 'View']
    # Use in runs, Region and Ports want the window's settings
    assert not tab.acts['use'].isEnabled() and not tab.use_btn.isEnabled()
    tab.add(Shape('rect', [[0, 0], [4, 2]], net='A'))
    tab.add(Shape('circle', [[8, 8]], radius=1, net='A'))
    tab.add(Shape('rect', [[0, 5], [2, 7]], material='metal', z0=1.6,
                  z1=3))
    tab.acts['all'].trigger()
    assert tab.selected == [0, 1, 2]
    tab.acts['none'].trigger()
    assert tab.selected == []
    tab.select(0)
    tab.acts['duplicate'].trigger()
    assert len(tab.drawing.shapes) == 4
    tab.acts['undo'].trigger()
    assert len(tab.drawing.shapes) == 3
    # the board's tick and the menu's: one setting
    tab.board_act.setChecked(False)
    assert not tab.board_on.isChecked()
    tab.board_on.setChecked(True)
    assert tab.board_act.isChecked()
    # an icon a shape, of its kind in its material's colour, made once
    icons = [tab.list.topLevelItem(k).icon(2) for k in range(3)]
    assert all(not i.isNull() for i in icons)
    assert icons[0].cacheKey() == _kind_icon('rect', 'copper').cacheKey()
    assert icons[0].cacheKey() != icons[2].cacheKey()
    # a change of material changes it
    tab.select(0)
    tab.p_material.setCurrentText('cut')
    tab._edited()
    assert tab.list.topLevelItem(0).icon(2).cacheKey() == _kind_icon(
        'rect', 'cut').cacheKey()
    tab.undo()
    # the form in groups: a copper rectangle has no heights or permittivity
    seen = lambda k: tab.form_groups[k].isVisibleTo(tab.side_tabs)
    tab.select(0)
    assert seen('Shape') and not (seen('Heights') or seen('Material')
                                  or seen('Bias'))
    tab.select(2)  # a metal block: its heights, no permittivity, no size
    assert seen('Heights') and not seen('Material') and not seen('Size')
    tab.select(None)  # nothing: a note, no fields
    assert tab.form_note.isVisibleTo(tab) and 'Nothing' in (
        tab.form_note.text())
    assert not any(seen(k) for k in tab.form_groups)
    # the page stays narrow (CI's screen is 800 px wide: past it the
    # window's docks go side by side) and short
    hint = tab.minimumSizeHint()
    assert hint.width() < 700 and hint.height() < 600


def test_templates_placed_with_a_click(tab, app):
    """A template's dialog, then a click places its shapes there (they follow
    the mouse till then; Esc or a right click: not placed), one undo step,
    selected; a fence goes along the selected trace at once."""
    from unittest import mock
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from kicad2ems.core import templates as T
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui.drawtab import _TemplateDialog
    # the dialog: a box a parameter, its values back as they were
    d = T.defaults('circulator', tab.stack, 'F.Cu')
    dlg = _TemplateDialog(T.TEMPLATES['circulator'], d)
    v = dlg.values()
    assert v.keys() == d.keys() and v['magnet'] is True and v['ms'] == 1200
    assert v['width'] == pytest.approx(d['width'], abs=1e-3)
    dlg.boxes['r_mag'].setValue(4.5)
    assert dlg.values()['r_mag'] == 4.5
    dlg.deleteLater()
    # placed with a click, snapped
    assert tab.start_placing('can', dict(w=6, l=4, h=2, t=0.2, fence=True,
                                         post=0.3, pitch=1.0))
    assert not tab.ghost.path().isEmpty()
    tab._moved(tab.vb.mapViewToScene(QPointF(10, 10)))
    assert tab.ghost.isVisible() and tab.ghost.pos().x() == pytest.approx(
        10)
    n0 = len(tab._undo)
    _click(tab, 10.02, 9.98)
    ss = tab.drawing.shapes
    assert ss[0].name == 'can' and ss[0].bounds() == pytest.approx(
        (7, 8, 13, 12))
    assert ss[1].material == 'air' and all(s.name.startswith('can_post')
                                           for s in ss[2:])
    assert tab.selected == list(range(len(ss))) and len(tab._undo) == n0 + 1
    assert tab._placing is None and not tab.ghost.isVisible()
    tab.undo()
    assert tab.drawing.shapes == []
    # Esc, a right click or another tool: not placed
    tab.start_placing('absorber')
    QTest.keyClick(tab, Qt.Key_Escape)
    assert tab._placing is None
    _click(tab, 5, 5)
    assert tab.drawing.shapes == []
    tab.start_placing('dr')
    _click(tab, 5, 5, button=Qt.RightButton)
    assert tab.drawing.shapes == [] and tab._placing is None
    tab.start_placing('dr')
    tab.set_tool('rect')
    assert tab._placing is None
    tab.set_tool('select')
    # values that make no shapes: said, not placed
    assert not tab.start_placing('can', dict(w=0.2, t=0.2))
    assert 'smaller than its walls' in tab.status.text()
    # a fence along the selected trace, at once, kept selected with it
    tab.add(Shape('path', [[2, 2], [12, 2]], width=0.4, name='rf'))
    tab.select(0)
    new = tab.place_template('fence', values=dict(offset=0.8, pitch=1.0,
                                                  post=0.3, both=True))
    assert len(new) == 22 and tab.selected[0] == 0
    assert all(s.material == 'metal' and s.z0 == 0 and s.z1 == 1.6
               for s in new)
    tab.undo()
    tab.select(None)
    assert tab.template_dialog('fence') is None  # no trace selected
    assert 'select a trace' in tab.status.text()
    tab.undo()
    # each template from its menu item: the dialog, then a click
    with mock.patch.object(_TemplateDialog, 'exec',
                           return_value=QDialog.Accepted):
        tab.acts['tpl circulator'].trigger()
    assert tab._placing[0] == 'circulator'
    _click(tab, 10, 10)
    names = [s.name for s in tab.drawing.shapes]
    assert names[:3] == ['puck', 'junction', 'arm1'] and 'magnet' in names
    assert tab.drawing.shapes[0].points == [[10, 10]]
    # the dialog's values kept for the next one on this board and layer
    mem = ('circulator', tab._board_ver, 'F.Cu')
    assert tab._tpl_values[mem]['ms'] == 1200
    # ordinary shapes: the puck is edited as any ferrite
    tab.select(0)
    assert tab.form.isRowVisible(tab.rows['ms'])


def test_booleans_rounded_corners_nudge_and_alike(tab, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from kicad2ems.core.shapes import Shape
    tab.add(Shape('rect', [[0, 0], [10, 10]], net='GND', name='plate'))
    tab.add(Shape('circle', [[5, 5]], radius=2))
    tab.add(Shape('rect', [[20, 0], [22, 2]], material='metal', z0=0, z1=1))
    tab.select_many([0, 1])
    new = tab.combine('subtract')
    assert len(new) == 1 and tab.drawing.shapes[0].kind == 'polygon'
    assert len(tab.drawing.shapes) == 2 and tab.selected == [0]
    g = tab.drawing.shapes[0].outline()
    assert len(g.interiors) == 1 and g.area == pytest.approx(
        100 - 4 * 3.1416, rel=0.01)
    assert tab.drawing.shapes[0].name == 'plate'
    tab.undo()
    assert [s.kind for s in tab.drawing.shapes] == ['rect', 'circle', 'rect']
    # of one material only: said, nothing changed
    tab.select_many([0, 2])
    assert tab.combine('union') is None and 'one material' in (
        tab.status.text())
    assert len(tab.drawing.shapes) == 3
    # a locked one: not combined
    tab.drawing.shapes[1].locked = True
    tab.select_many([0, 1])
    assert tab.combine('union') is None
    tab.drawing.shapes[1].locked = False
    # from the menu
    tab.select_many([0, 1])
    tab.acts['intersect'].trigger()
    assert tab.drawing.shapes[0].outline().area == pytest.approx(
        4 * 3.1416, rel=0.01)
    tab.undo()
    # rounded corners: the selected rectangle filleted, the circle kept
    tab.select_many([0, 1])
    assert tab.round_corners(1.0)
    s0, s1 = tab.drawing.shapes[:2]
    assert s0.kind == 'polygon' and s1.kind == 'circle'
    assert s0.outline().area == pytest.approx(100 - (4 - 3.14159), rel=1e-3)
    tab.undo()
    tab.select(0)
    assert tab.round_corners(2.0, 'chamfer')
    assert len(tab.drawing.shapes[0].points) == 8
    tab.undo()
    # nudged by the arrow keys: a grid step (Shift: ten), one undo step
    tab.select(2)
    tab.plot.setFocus()
    n = len(tab._undo)
    for _ in range(3):
        QTest.keyClick(tab.plot, Qt.Key_Right)
    QTest.keyClick(tab.plot, Qt.Key_Up, Qt.ShiftModifier)
    assert sum(tab.drawing.shapes[2].points, []) == pytest.approx([20.3, 1,
                                                              22.3, 3])
    assert len(tab._undo) == n + 1
    tab.undo()
    assert tab.drawing.shapes[2].points == [[20, 0], [22, 2]]
    # nothing selected: the keys are the view's
    tab.select(None)
    QTest.keyClick(tab.plot, Qt.Key_Left)
    assert len(tab._undo) == n
    # select alike: the same material as the last picked, it still the form's
    tab.add(Shape('rect', [[30, 0], [31, 1]], material='metal', z0=0, z1=1))
    tab.select(2)
    tab.acts['alike material'].trigger()
    assert sorted(tab.selected) == [2, 3] and tab.sel == 2
    tab.select(0)
    tab.select_alike('net')
    assert tab.selected == [0]


def test_the_3d_view_orthographic_arrowheads_and_box(tab, app):
    import numpy as np
    from PySide6.QtCore import Qt
    from kicad2ems.core.shapes import Shape
    if tab.gl is None:
        pytest.skip('no 3D view here')
    tab.resize(1200, 1100)
    app.processEvents()
    v = tab.view3d
    tab.add(Shape('rect', [[0, 0], [4, 4]], material='metal', z0=2, z1=3))
    tab.add(Shape('circle', [[10, 2]], radius=1.5, layer='F.Cu'))
    tab.add(Shape('circle', [[20, 2]], radius=1.0, material='air', z0=0,
                  z1=1.6))
    tab.select(None)
    tab._redraw_3d()
    # Top, Front and Side orthographic, Iso in perspective; the GL's matrix
    # the picking's
    tab.camera('top')
    assert tab.ortho3d and tab.ortho_btn.isChecked()
    assert tab.acts['ortho'].isChecked()
    vp = (0, 0, v.width(), v.height())
    m = np.array((v.projectionMatrix(vp, vp) * v.viewMatrix()).data(),
                 float).reshape(4, 4).T
    assert np.abs(m - tab._mvp()).max() < 1e-6
    # no perspective: a block's top and bottom corners on one pixel
    assert tab.project3d([0, 0, 2]) == pytest.approx(
        tab.project3d([0, 0, 3]))
    assert v.pixelSize(v.opts['center']) == pytest.approx(
        2 * v.opts['distance'] * np.tan(np.radians(v.opts['fov']) / 2)
        / v.width())
    # a click still picks
    _press_3d(tab, app, tab.project3d([2, 2, 3]), tab.project3d([2, 2, 3]))
    assert tab.selected == [0]
    tab.camera('iso')
    assert not tab.ortho3d and not tab.ortho_btn.isChecked()
    tab.ortho_btn.setChecked(True)
    assert tab.ortho3d and tab.acts['ortho'].isChecked()
    # the gizmo's arrowheads: a cone an axis, drawn over the scene
    tab.select(0)
    tab._update_gizmo_3d()
    cone = tab._gizmo_cone_gl
    assert cone is not None and cone.depthValue() > 5
    assert len(cone.opts['meshdata'].faces()) == 3 * 32
    # a drag on an arrow moves its cone with its shaft
    base, length, _ = tab._gizmo
    a = tab.project3d(base + [0.5 * length, 0, 0])
    _press_3d(tab, app, a, (a[0] + 40, a[1]))
    assert tab.drawing.shapes[0].points[0][0] > 0
    tab.undo()
    # a box: Shift + drag on nothing; left to right what is inside it
    tab.camera('top')
    tab.select(None)
    tab._redraw_3d()
    p0, p1 = tab.project3d([-1, 5, 0]), tab.project3d([12, -1, 0])
    _press_3d(tab, app, p0, p1, mods=Qt.ShiftModifier)
    assert tab.selected == [0, 1] and 'inside' in tab.status.text()
    # right to left: what it touches
    q0, q1 = tab.project3d([20, 5, 0]), tab.project3d([11, -1, 0])
    _press_3d(tab, app, q0, q1, mods=Qt.ShiftModifier)
    assert tab.selected == [1, 2] and 'touching' in tab.status.text()
    # Esc drops it
    _press_3d(tab, app, p0, p1, mods=Qt.ShiftModifier, keys=[Qt.Key_Escape])
    assert tab.selected == [1, 2]
    # a drag on nothing without Shift still orbits
    az = v.opts['azimuth']
    _press_3d(tab, app, p0, (p0[0] + 50, p0[1]))
    assert v.opts['azimuth'] != az and tab.selected == [1, 2]


def test_the_bias_map_in_plan(tab, app):
    """With a ferrite (or a magnet) selected, its internal field H0 in colour
    over it in plan (drawmagnets.bias_map: core.magnets.bias on a grid), its
    range under the readout; grey where it is not saturated; gone with
    anything else selected, or when the solids change till it is worked out
    again."""
    import numpy as np
    from kicad2ems.core import magnets
    from kicad2ems.core.shapes import Shape
    from kicad2ems.gui import drawmagnets as dm
    from kicad2ems.gui.drawtab import UNSATURATED
    f, m = _circulator(tab)
    tab.add(Shape('rect', [[8, 8], [9, 9]]))
    # the map's numbers: within the readout's range, none outside the puck
    b, at = dm.bias_board(None, tab.stack, tab.drawing.shapes)
    r, = magnets.summary(b)
    x0, y0, x1, y1, h, inside = dm.bias_map(b, tab.drawing.shapes, f, at)
    assert (x0, y0, x1, y1) == pytest.approx((-3, -3, 3, 3), abs=0.05)
    assert h.shape == (32, 32) and np.isfinite(h[inside]).all()
    assert np.isnan(h[~inside]).all() and 0.7 < inside.mean() < 0.85
    lo, mean, hi = r['internal_oe']
    assert lo * 0.95 < np.nanmin(h) and np.nanmax(h) < hi * 1.05
    # a magnet's map: every ferrite's, the same here
    assert np.allclose(dm.bias_map(b, tab.drawing.shapes, m, at)[4], h,
                       equal_nan=True)
    # drawn with the readout, over the puck
    tab.select(f)
    _wait(app, tab)
    assert tab.bias_img.isVisible() and tab.p_map.isVisible()
    assert f"{np.nanmin(h):.0f} Oe" in tab.p_map.text()
    rect = tab.bias_img.mapRectToView(tab.bias_img.boundingRect())
    assert (rect.left(), rect.bottom()) == pytest.approx((-3, 3), abs=0.05)
    img = tab.bias_img.image
    assert img.shape == (32, 32, 4) and (img[~inside][:, 3] == 0).all()
    # kept while the solids are the same
    key = tab._map_key
    tab.select(m)
    tab.select(f)
    _wait(app, tab)
    assert tab._map_key == key and tab.bias_img.isVisible()
    # anything else selected: gone
    tab.select(2)
    assert not tab.bias_img.isVisible()
    # the magnet far off: the puck not saturated, grey
    tab.select(m)
    tab.move_by(0, 0, 40)
    assert not tab.bias_img.isVisible()  # (stale: gone till worked out)
    tab.select(f)
    _wait(app, tab)
    assert tab.bias_img.isVisible() and 'none of it saturated' in (
        tab.p_map.text())
    img = tab.bias_img.image
    assert (img[inside] == UNSATURATED).all()
    # turned off in View
    tab.acts['map'].setChecked(False)
    assert not tab.bias_img.isVisible()
    tab.select(m)
    tab.select(f)
    _wait(app, tab)
    assert not tab.bias_img.isVisible()
