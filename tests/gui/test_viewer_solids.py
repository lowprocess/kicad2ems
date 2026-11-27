"""The drawn solids in the Viewer's plan and 3D (gui/planview.py,
gui/volume3d.py, gui/volumetab.py), as the solvers mesh them: core.solids's
pieces."""
import os
import pytest

pytest.importorskip('PySide6')
pytest.importorskip('pyqtgraph')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _board():
    """A 20 x 10 mm two-layer board with a can drawn on it (a metal box, the
    air inside it drawn after) and a magnet in the air: board.json's dict."""
    from shapely.geometry import box, mapping
    from kicad2ems.core.shapes import Shape, Drawing, apply, stack_of
    from kicad2ems.gui.drawmagnets import _Board
    st = stack_of(None)
    b = _Board(box(0, -10, 20, 0), st)
    b.cu_z, b.copper, b.copper_nets = st['cu_z'], {'F.Cu': []}, {'F.Cu': []}
    apply(b, Drawing([
        Shape('rect', [[6, -8], [14, -2]], material='metal', z0=1.635,
              z1=3.635, name='can'),
        Shape('rect', [[6.2, -7.8], [13.8, -2.2]], material='air', z0=1.635,
              z1=3.435, name='can_air'),
        Shape('circle', [[10, -5]], radius=1, material='magnet', z0=1.635,
              z1=2.5)]))
    return dict(outline=mapping(b.outline), solids=b.solids,
                stack=dict(layers=st['layers'], copper=[
                    dict(name=k, z=z, t=0.035) for k, z in st['cu_z'].items()]),
                copper={}, vias=[])


def test_the_pieces_and_their_meshes():
    """A can and the air in it are a metal shell and an air piece; the
    magnet over the air its own (a later solid wins); one mesh a look."""
    from kicad2ems.gui import volume3d as v3
    board = _board()
    ps = v3.solid_pieces(board)
    assert {p.material for p in ps} == {'metal', 'air'}
    assert sum(p.magnet is not None for p in ps) == 1
    # the air took the can's inside: the metal is its walls and lid
    metal = [p for p in ps if p.material == 'metal' and p.magnet is None]
    assert sum(p.polygon.area * (p.z1 - p.z0) for p in metal) == (
        pytest.approx(8 * 6 * 2 - 7.6 * 5.6 * 1.8))
    looks = {m[0]: m for m in v3.solid_meshes(ps, zs=2.0)}
    assert set(looks) == {'metal', 'air', 'magnet'}
    assert looks['air'][1][3] < looks['metal'][1][3] < 1
    assert looks['metal'][2][:, 2].max() == pytest.approx(2 * 3.635)
    # the geometry the 3D view reads (geometry_from_board) carries them
    g = v3.geometry_from_board(board)
    assert len(v3.solid_pieces(g)) == len(ps)
    assert v3.solid_pieces(dict(board, solids=[])) == []


def test_the_plan_draws_them(app):
    """The plan: a dashed outline a material over the copper, a fill under
    it (stronger where the layer shown meets it); a tick turns them off,
    and it is shown only with solids."""
    from PySide6.QtCore import Qt
    from kicad2ems.gui.planview import PlanView
    board = _board()
    pv = PlanView()
    pv.set_board(board)
    assert pv.solids.isVisibleTo(pv)
    items = pv._solid_items(1.6)
    assert len(items) == 6  # (a dashed outline over the copper, a fill under)
    dashed = lambda it: it.pen().style() == Qt.DashLine
    assert len([it for it in pv._items if dashed(it)]) == 3
    pv.solids.setChecked(False)
    assert not [it for it in pv._items if dashed(it)]
    pv.set_board(dict(board, solids=[]))
    assert not pv.solids.isVisibleTo(pv)
    pv.close()
