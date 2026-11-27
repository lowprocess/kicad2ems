"""The Draw page's edits as data (kicad2ems.core.shapeops: booleans, rounded
corners, select alike) and its 3D view's orthographic camera, box selection
and arrowheads (kicad2ems.core.view3d)."""
import math
import numpy as np
import pytest
from kicad2ems.core.shapes import Shape
from kicad2ems.core import shapeops as so
from kicad2ems.core import view3d as v3


def _area(ss):
    return sum(s.outline().area for s in ss)


def test_union_subtract_and_intersect():
    plate = Shape('rect', [[0, 0], [10, 10]], net='GND', name='plate')
    tab = Shape('rect', [[8, -2], [12, 12]])
    hole = Shape('circle', [[5, 5]], radius=2)
    u = so.combined([plate, tab], 'union')
    assert len(u) == 1 and u[0].kind == 'polygon'
    assert _area(u) == pytest.approx(100 + 56 - 20)
    # the first's properties kept
    assert (u[0].net, u[0].name, u[0].layer) == ('GND', 'plate', 'F.Cu')
    s = so.combined([plate, hole], 'subtract')
    g = s[0].outline()
    assert len(g.interiors) == 1  # the hole kept, through a cut of no width
    assert g.area == pytest.approx(100 - hole.outline().area, rel=1e-7)
    i = so.combined([plate, hole], 'intersect')
    assert _area(i) == pytest.approx(hole.outline().area, rel=1e-7)
    # two holes; a cut across: two pieces, numbered
    two = so.combined([plate, Shape('circle', [[2.5, 5]], radius=1),
                       Shape('circle', [[7.5, 5]], radius=1)], 'subtract')
    assert len(two) == 1 and len(two[0].outline().interiors) == 2
    halves = so.combined([plate, Shape('rect', [[4, -1], [6, 11]])],
                         'subtract')
    assert [h.name for h in halves] == ['plate_1', 'plate_2']
    assert _area(halves) == pytest.approx(80)
    # no overlap: nothing left
    assert so.combined([plate, Shape('rect', [[20, 20], [21, 21]])],
                       'intersect') == []
    # solids between the same heights, a sheet's layer kept
    a = Shape('rect', [[0, 0], [2, 2]], material='metal', z0=0, z1=1)
    b = Shape('circle', [[2, 2]], radius=1, material='metal', z0=1, z1=0)
    m = so.combined([a, b], 'union')[0]
    assert (m.material, m.z0, m.z1) == ('metal', 0, 1)
    for bad in ([plate], [plate, Shape('rect', [[0, 0], [1, 1]],
                                       material='cut')],
                [plate, Shape('rect', [[0, 0], [1, 1]], layer='B.Cu')],
                [a, Shape('rect', [[0, 0], [1, 1]], material='metal', z0=0,
                          z1=2)],
                [plate, Shape('port', [[1, 1]])]):
        with pytest.raises(ValueError):
            so.combined(bad, 'union')
    with pytest.raises(ValueError):
        so.combined([plate, tab], 'xor')


def test_a_ring_of_points_keeps_the_holes():
    from shapely.geometry import Polygon, Point, box
    rng = np.random.default_rng(1)
    for _ in range(20):
        g = box(0, 0, 20, 10)
        for _ in range(int(rng.integers(1, 6))):
            x, y = rng.uniform(1, 19), rng.uniform(1, 9)
            g = g.difference(Point(x, y).buffer(rng.uniform(0.2, 0.9)))
        for p in getattr(g, 'geoms', [g]):
            back = [Polygon(pts).buffer(0) for pts in so._ringable(p)]
            # (one ring a piece, its points rounded to 1e-6 mm)
            assert all(b.geom_type == 'Polygon' for b in back)
            assert sum(b.area for b in back) == pytest.approx(p.area)
            from shapely.ops import unary_union
            assert unary_union(back).symmetric_difference(p).area < 1e-5
    # 300 holes in one ring, given back
    plate = box(0, 0, 30, 6)
    for k in range(300):
        plate = plate.difference(Point(0.5 + (k % 75) * 29 / 75,
                                       1 + (k // 75) * 1.3).buffer(0.125))
    pts = so.ring_points(plate)
    back = Polygon(pts).buffer(0)
    assert len(back.interiors) == 300
    # (the points rounded to 1e-6 mm along 300 rims, 235 mm in all)
    assert back.symmetric_difference(plate).area < 1e-6 * plate.length


def test_rounded_corners():
    sq = Shape('rect', [[0, 0], [10, 10]])
    f = so.rounded(sq, 1.0)
    assert f.kind == 'polygon'
    assert f.outline().area == pytest.approx(100 - (4 - math.pi), rel=1e-3)
    c = so.rounded(sq, 1.0, 'chamfer')
    assert c.outline().area == pytest.approx(98) and len(c.points) == 8
    # concave corners too: an L of six corners
    L = Shape('polygon', [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]])
    k = 1 - math.pi / 4
    assert so.rounded(L, 1.0).outline().area == pytest.approx(
        64 - 5 * k + k, rel=1e-3)
    assert so.rounded(L, 1.0, 'chamfer').outline().area == pytest.approx(62)
    # a corner's cut held to half its shorter leg (a 2 mm square: r 1)
    small = so.rounded(Shape('rect', [[0, 0], [2, 2]]), 5.0)
    assert small.outline().area == pytest.approx(math.pi, rel=2e-3)
    # a trace's bend: an arc of radius r, its ends kept
    tr = Shape('path', [[0, 0], [10, 0], [10, 10]], width=0.5)
    t = so.rounded(tr, 3.0)
    assert t.kind == 'path' and t.width == 0.5
    assert t.points[0] == [0, 0] and t.points[-1] == [10, 10]
    arc = np.array(t.points[1:-1])
    assert np.hypot(arc[:, 0] - 7, arc[:, 1] - 3) == pytest.approx(3.0)
    m = so.rounded(tr, 2.0, 'chamfer')  # a mitred bend
    assert m.points == [[0, 0], [8, 0], [10, 2], [10, 10]]
    for bad in (Shape('circle', [[0, 0]]), Shape('port', [[0, 0]])):
        with pytest.raises(ValueError):
            so.rounded(bad, 1.0)
    with pytest.raises(ValueError):
        so.rounded(sq, 0)
    # a polygon with a hole (a keyhole): its cut is not rounded
    holed = so.combined([sq, Shape('rect', [[4, 4], [6, 6]])], 'subtract')[0]
    r = so.rounded(holed, 0.5)
    assert len(r.outline().interiors) == 1


def test_select_alike():
    ss = [Shape('rect', [[0, 0], [1, 1]], net='A'),
          Shape('circle', [[0, 0]], net='A', layer='B.Cu'),
          Shape('rect', [[0, 0], [1, 1]], net='B'),
          Shape('rect', [[0, 0], [1, 1]], material='metal', z0=0, z1=1),
          Shape('circle', [[0, 0]], material='air', z0=0, z1=1),
          Shape('port', [[0, 0]]),
          Shape('rect', [[0, 0], [1, 1]], net='A', hidden=True)]
    assert so.alike(ss, 0, 'material') == [0, 1, 2]
    assert so.alike(ss, 0, 'net') == [0, 1]
    assert so.alike(ss, 0, 'layer') == [0, 2, 5]
    assert so.alike(ss, 3, 'layer') == [3, 4]  # every solid
    assert so.alike(ss, 0, 'kind') == [0, 2, 3]
    assert so.alike(ss, 3, 'net') == []
    with pytest.raises(ValueError):
        so.alike(ss, 0, 'colour')


def test_an_orthographic_camera():
    cam = dict(center=(0, 0, 0), distance=50.0, elevation=90.0,
               azimuth=-90.0, fov=60.0, ortho=True)
    m = v3.mvp(cam, 800, 600)
    px = v3.project(m, 800, 600, (10, 0, 0))
    # as wide as the perspective's centre plane, and at every depth
    persp = v3.project(v3.mvp(dict(cam, ortho=False), 800, 600), 800, 600,
                       (10, 0, 0))
    assert px == pytest.approx(persp)
    assert v3.project(m, 800, 600, (10, 0, 7)) == pytest.approx(px)
    assert v3.ortho_pixel(50, 60, 800) == pytest.approx(10 / (px[0] - 400))
    # every ray straight down, from behind anything in the view
    for x, y in ((0, 0), (400, 300), (799, 10)):
        o, d = v3.ray(m, 800, 600, x, y)
        assert d == pytest.approx((0, 0, -1))
        assert o[2] > 100
    # a zoom that puts a solid behind the camera's plane still draws it
    near = v3.mvp(dict(cam, distance=1.0), 800, 600) @ [0, 0, 5.0, 1]
    assert -1 < near[2] / near[3] < 1
    # a region of the view (pyqtgraph's picking): its part of the whole
    sub = v3.ortho_matrix(50, 60, 800, 600, (400, 300, 400, 300))
    full = v3.ortho_matrix(50, 60, 800, 600)
    p = full @ [10, 5, 0, 1.0]
    q = sub @ [10, 5, 0, 1.0]
    assert q[0] == pytest.approx(2 * p[0] - 1)
    # framed: the box fills the view, its depth not counted
    b = (0, 0, 0, 20, 10, 2)
    f = v3.frame(b, cam, 800 / 600)
    mf = v3.mvp(dict(cam, **f), 800, 600)
    xs = [v3.project(mf, 800, 600, (x, y, z))[0] for x in (0, 20)
          for y in (0, 10) for z in (0, 2)]
    assert max(xs) - min(xs) == pytest.approx(800 / 1.1, rel=1e-6)
    assert v3.preset('top', b, ortho=True)['distance'] < v3.preset(
        'top', b)['distance']


def test_box_picking_and_the_arrowheads():
    cam = dict(center=(0, 0, 0), distance=50.0, elevation=90.0,
               azimuth=-90.0, fov=60.0, ortho=True)
    m = v3.mvp(cam, 800, 600)
    cube = lambda x, y: (np.array([[x, y, 0], [x + 1, y + 1, 1],
                                   [x, y + 1, 1], [x + 1, y, 0]], float),
                         np.zeros((0, 3), int))
    meshes = {'a': cube(0, 0), 'b': cube(5, 0), 'c': cube(-30, 0)}
    px = lambda p: v3.project(m, 800, 600, (*p, 0.0))
    (x0, y0), (x1, y1) = px((-0.5, 1.5)), px((6.5, -0.5))
    assert sorted(v3.box_pick(meshes, m, 800, 600, (x0, y0, x1, y1))) == [
        'a', 'b']
    # wholly inside it, or (crossing) touching it
    (x0, y0), (x1, y1) = px((-0.5, 1.5)), px((5.5, -0.5))
    assert v3.box_pick(meshes, m, 800, 600, (x0, y0, x1, y1)) == ['a']
    assert sorted(v3.box_pick(meshes, m, 800, 600, (x1, y0, x0, y1),
                              crossing=True)) == ['a', 'b']
    # a box inside a big shape's outline touches it, no corner in it
    big = {'big': (np.array([[-10, -10, 0], [10, -10, 0], [10, 10, 0],
                             [-10, 10, 0]], float), None)}
    (x0, y0), (x1, y1) = px((-1, 1)), px((1, -1))
    assert v3.box_pick(big, m, 800, 600, (x0, y0, x1, y1), True) == ['big']
    assert v3.box_pick(big, m, 800, 600, (x0, y0, x1, y1)) == []
    # in perspective, a corner behind the camera is never inside
    pm = v3.mvp(dict(cam, ortho=False, distance=5.0), 800, 600)
    behind = {'k': (np.array([[0, 0, 0], [0, 0, 50.0]]), None)}
    assert v3.box_pick(behind, pm, 800, 600, (-1e6, -1e6, 1e6, 1e6)) == []
    xy, front = v3.project_many(pm, 800, 600, [[0, 0, 0], [0, 0, 50.0]])
    assert list(front) == [True, False] and xy[0] == pytest.approx((400, 300))
    # an arrowhead: a closed cone, its faces wound outward
    v, f = v3.arrow_cone((1, 2, 3), 0, 2.0, 0.5, n=12)
    assert v[:, 0].max() == pytest.approx(3.0) and len(f) == 24
    t = v[f]
    nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    out = t.mean(1) - np.array([1.5, 2, 3])
    assert (np.einsum('ij,ij->i', nrm, out) > 0).all()
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]],
                                    f[:, [2, 0]]]), axis=1)
    _, k = np.unique(edges, axis=0, return_counts=True)
    assert (k == 2).all()  # closed: each edge two faces'
