"""Drawn shapes (kicad2ems.core.shapes): their geometry, the file, what they do
to a board, the settings they write and their 3D meshes."""
import os
import numpy as np
import pytest
from kicad2ems.core.shapes import (Shape, Drawing, apply, prism, shape_mesh,
                                   stack_of, region_setting, port_settings)

EXAMPLES = os.path.join(os.path.dirname(__file__), '..', '..', 'examples')
HAIRPIN = os.path.join(EXAMPLES, 'hairpin.kicad_pcb')


def test_outlines_of_each_kind():
    assert Shape('rect', [[0, 0], [2, 1]]).outline().area == pytest.approx(2)
    assert Shape('circle', [[0, 0]], radius=1).outline().area == pytest.approx(
        np.pi, rel=0.01)
    tri = Shape('polygon', [[0, 0], [2, 0], [0, 2]])
    assert tri.outline().area == pytest.approx(2)
    # a trace: its centre line's length x width, plus the round ends
    tr = Shape('path', [[0, 0], [10, 0]], width=0.5)
    assert tr.outline().area == pytest.approx(10 * 0.5 + np.pi * 0.25**2,
                                              rel=0.01)
    assert Shape('port', [[1, 2]]).outline() is None
    with pytest.raises(ValueError):
        Shape('polygon', [[0, 0], [1, 1]])
    with pytest.raises(ValueError):
        Shape('rect', [[0, 0], [1, 1]], material='gold')


def test_editing_a_shape():
    c = Shape('circle', [[1, 1]], radius=1)
    # its rim's handle sets the radius
    assert c.handles() == [(1, 1), (2, 1)]
    c.drag_handle(1, 1, 4)
    assert c.radius == pytest.approx(3)
    r = Shape('rect', [[0, 0], [2, 1]]).moved(1, -1)
    assert r.points == [[1, -1], [3, 0]]


def test_the_file_round_trips(tmp_path):
    d = Drawing([
        Shape('rect', [[0, 0], [1, 1]], net='RF'),
        Shape('circle', [[0, 0]], radius=0.2, material='cut', layer='B.Cu'),
        Shape('polygon', [[0, 0], [1, 0], [0, 1]], material='dielectric',
              z0=1.6, z1=2.0, er=3.0, tand=0.001, name='lens'),
        Shape('port', [[0.5, 0.5]], rot=90.0)
    ])
    p = d.save(tmp_path / 'd.json')
    assert Drawing.load(p) == d
    # only what each shape uses is written
    rect = d.to_dict()['shapes'][0]
    assert 'radius' not in rect and 'z0' not in rect and rect['net'] == 'RF'
    with pytest.raises(ValueError):
        Drawing.from_dict(dict(format='other'))


def _board(**kw):
    from kicad2ems.core.importers import open_board
    from kicad2ems.core.board import Board
    return Board(open_board(HAIRPIN), need_ports=False, log=lambda *a: None,
                 **kw)


def test_copper_cuts_and_solids_on_a_board(tmp_path):
    b0 = _board()
    x0, y0, x1, y1 = b0.outline.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    d = Drawing([
        # copper of a net of its own, clear of the filter
        Shape('rect', [[x0 + 0.5, y0 + 0.5], [x0 + 2.5, y0 + 1.5]],
              name='pad'),
        # a hole through the ground plane
        Shape('circle', [[cx, cy]], radius=0.5, material='cut', layer='B.Cu'),
        # off the board: left out
        Shape('rect', [[x1 + 5, y1 + 5], [x1 + 6, y1 + 6]]),
        Shape('rect', [[cx - 3, cy - 3], [cx + 3, cy + 3]], material='metal',
              z0=1.0, z1=3.0),
    ])
    b = _board(shapes=d.save(tmp_path / 'd.json'))
    area = lambda bb, l: sum(p.area for p in bb.copper[l])
    assert area(b0, 'B.Cu') - area(b, 'B.Cu') == pytest.approx(np.pi * 0.25,
                                                               rel=0.02)
    assert area(b, 'F.Cu') - area(b0, 'F.Cu') == pytest.approx(2.0, rel=1e-3)
    assert 'DRAWN_pad' in b.copper_nets['F.Cu']
    assert any('off the board' in n for n in b.notes)
    assert len(b.solids) == 1 and b.solids[0]['material'] == 'metal'
    # board.json keeps the solids
    from kicad2ems.core.board import Board
    again = Board.from_dict(b.to_dict(), log=None)
    assert again.solids == b.solids
    assert b.request['shapes'].endswith('d.json')


def test_drawn_copper_joins_the_net_it_overlaps():
    b = _board()
    lay = 'F.Cu'
    p = max(b.copper[lay], key=lambda q: q.area)
    net = b.copper_nets[lay][b.copper[lay].index(p)]
    x, y = p.representative_point().coords[0]
    n = len(b.copper[lay])
    notes = apply(b, Drawing([Shape('circle', [[x, y]], radius=0.3,
                                    layer=lay)]))
    assert len(b.copper[lay]) == n and b.copper_nets[lay].count(net) >= 1
    assert not any('short' in t for t in notes)
    # a named other net over it: a short, said
    notes = apply(b, Drawing([Shape('circle', [[x, y]], radius=0.3,
                                    layer=lay, net='OTHER')]))
    assert any('short' in t for t in notes)
    with pytest.raises(ValueError, match='no copper layer'):
        apply(b, Drawing([Shape('rect', [[0, 0], [1, 1]], layer='In7.Cu')]))


def test_settings_in_kicad_coordinates():
    r = Shape('rect', [[1, -4], [3, -2]])
    assert region_setting(r) == [1, 2, 3, 4]
    tri = region_setting(Shape('polygon', [[0, 0], [2, 0], [0, -2]]))
    assert [0, 2] in tri and len(tri) == 3
    d = Drawing([Shape('port', [[1, -2]], layer='B.Cu'),
                 Shape('port', [[3, -4]], rot=90)])
    assert port_settings(d, first=3, taken={4}) == ['P3@1,2,B.Cu',
                                                    'P5@3,4,F.Cu,90']


def test_meshes_are_closed():
    from shapely.geometry import Polygon
    ring = Polygon([[0, 0], [4, 0], [4, 4], [0, 4]], [[[1, 1], [1, 3], [3, 3],
                                                       [3, 1]]])
    v, f = prism(ring, 0, 2)
    tri = v[f]
    # the divergence theorem: the outward faces enclose the solid's volume
    vol = np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum()
    assert vol / 6 == pytest.approx((16 - 4) * 2)
    st = stack_of(None)
    sheet = shape_mesh(Shape('rect', [[0, 0], [1, 1]], layer='F.Cu'), st)[0]
    assert sheet[:, 2].min() < 1.6 < sheet[:, 2].max()
    solid = shape_mesh(Shape('rect', [[0, 0], [1, 1]], material='air', z0=0,
                             z1=1.6), st)[0]
    assert solid[:, 2].min() == 0 and solid[:, 2].max() == 1.6
    assert shape_mesh(Shape('port', [[0, 0]]), st) is not None


def test_a_ray_picks_the_nearest_shape():
    from kicad2ems.core.shapes import ray_hit, pick
    st = stack_of(None)
    low = shape_mesh(Shape('rect', [[0, 0], [2, 2]], material='metal', z0=0,
                           z1=1), st)
    high = shape_mesh(Shape('rect', [[0, 0], [2, 2]], material='metal', z0=3,
                            z1=4), st)
    down = ([1, 1, 10], [0, 0, -1])
    assert ray_hit(*low, *down) == pytest.approx(9)
    assert pick({'low': low, 'high': high}, *down) == ('high', pytest.approx(6))
    assert pick({'low': low}, [5, 5, 10], [0, 0, -1]) == (None, None)
    # the ray's origin inside the solid: its far face
    assert ray_hit(*low, [1, 1, 0.5], [0, 0, 1]) == pytest.approx(0.5)


def test_a_solid_lifted_or_stretched():
    s = Shape('rect', [[0, 0], [1, 1]], material='metal', z0=1, z1=2)
    assert (s.lifted(0.5).z0, s.lifted(0.5).z1) == (1.5, 2.5)
    assert (s.lifted(1, 'top').z0, s.lifted(1, 'top').z1) == (1, 3)
    # never past the other face
    b = s.lifted(5, 'bottom')
    assert b.z0 < b.z1 == 2


# ---- the Draw page's edits, snapping and 3D view as data --------------------
def test_a_shape_turned_and_mirrored():
    r = Shape('rect', [[0, 0], [2, 1]])
    q = r.rotated(90, (0, 0))
    assert q.kind == 'rect' and q.points == [[0, 0], [-1, 2]]
    # off a right angle: a polygon of its corners
    p = r.rotated(45, (0, 0))
    assert p.kind == 'polygon' and len(p.points) == 4
    assert p.outline().area == pytest.approx(2)
    assert p.points[1] == pytest.approx([2 ** 0.5, 2 ** 0.5])
    # about its own centre: the centre stays
    c = Shape('circle', [[3, 4]], radius=1).rotated(30)
    assert c.points == [[3, 4]]
    port = Shape('port', [[1, 0]], rot=10.0)
    assert port.rotated(90, (0, 0)).points == [[0, 1]]
    assert port.rotated(90, (0, 0)).rot == 100
    assert port.mirrored('x', 0).rot == 170 and port.mirrored('y', 0).rot == 350
    m = Shape('polygon', [[0, 0], [2, 0], [0, 1]]).mirrored('x', 5)
    assert m.points == [[10, 0], [8, 0], [10, 1]]
    assert Shape('rect', [[0, 0], [2, 1]]).mirrored('y').points == [[0, 1],
                                                                    [2, 0]]


def test_hidden_and_locked_are_kept_when_set():
    s = Shape('rect', [[0, 0], [1, 1]])
    assert 'hidden' not in s.to_dict() and 'locked' not in s.to_dict()
    s.hidden = s.locked = True
    assert Shape.from_dict(s.to_dict()) == s


def test_snap_points_of_each_kind():
    r = Shape('rect', [[0, 0], [2, 1]]).snap_points()
    assert (2, 1, 'corner') in r and (1, 0, 'midpoint') in r
    assert (1, 0.5, 'centre') in r and len(r) == 9
    t = Shape('path', [[0, 0], [4, 0], [4, 4]]).snap_points()
    assert [w for *_, w in t].count('end') == 2 and (4, 2, 'midpoint') in t
    c = Shape('circle', [[1, 1]], radius=2).snap_points()
    assert (1, 1, 'centre') in c and (3, 1, 'rim') in c


def test_shapes_edited_together():
    from kicad2ems.core import shapeops as so
    a = Shape('rect', [[0, 0], [1, 1]])
    b = Shape('rect', [[4, 2], [6, 3]])
    c = Shape('circle', [[10, 0]], radius=0.5)
    assert so.bounds_of([a, b]) == (0, 0, 6, 3)
    # turned together about their centre (3, 1.5)
    ra, rb = so.rotated([a, b], 180)
    assert ra.bounds() == pytest.approx((5, 2, 6, 3))
    assert rb.bounds() == pytest.approx((0, 0, 2, 1))
    ma, mb = so.mirrored([a, b], 'x')
    assert ma.bounds() == pytest.approx((5, 0, 6, 1))
    # aligned: to the left of them all; a kept one stays and counts
    assert so.aligned([a, b], 'left') == [(0, 0), (-4, 0)]
    assert so.aligned([a, b], 'top', keep={1}) == [(0, 2), (0, 0)]
    assert so.aligned([a, b], 'hcentre') == [(2.5, 0), (-2, 0)]
    # distributed: centres evenly between the first and the last
    d = so.distributed([c, a, Shape('circle', [[1, 5]], radius=0.1)], 'x')
    assert d[0] == (0, 0) and d[1] == (0, 0) and d[2] == (pytest.approx(4.25),
                                                          0)


def test_arrays_of_shapes():
    from kicad2ems.core import shapeops as so
    via = Shape('circle', [[0, 0]], radius=0.15, material='metal', z0=0,
                z1=1.6, name='v')
    row = so.array_row([via], 5, 0.5, 0)
    assert [s.points[0] for s in row] == [[0.5, 0], [1, 0], [1.5, 0], [2, 0]]
    assert row[0].name == 'v_2' and row[-1].name == 'v_5'
    grid = so.array_grid([via], 3, 2, 1, 2)
    assert len(grid) == 5 and grid[-1].points == [[2, 2]]
    ring = so.array_circle([via.moved(1, 0)], 4, (0, 0))
    assert [s.points[0] for s in ring] == [pytest.approx([0, 1]),
                                           pytest.approx([-1, 0]),
                                           pytest.approx([0, -1])]
    # an arc of 90 degrees: the last copy at its end
    arc = so.array_circle([via.moved(1, 0)], 3, (0, 0), angle=90)
    assert arc[-1].points[0] == pytest.approx([0, 1])
    # carried round, not turned: a rectangle stays a rectangle
    box = Shape('rect', [[1, -0.1], [1.2, 0.1]])
    kept = so.array_circle([box], 8, (0, 0), turn=False)
    assert all(s.kind == 'rect' for s in kept)
    assert so.centre_of([kept[1]]) == pytest.approx((0, 1.1))


def test_the_clipboard_text():
    import json
    from kicad2ems.core import shapeops as so
    a = Shape('rect', [[0, 0], [1, 1]], locked=True, hidden=True, net='RF')
    text = so.to_clipboard([a])
    back = so.from_clipboard(text)
    assert len(back) == 1 and back[0].net == 'RF'
    assert not back[0].locked and not back[0].hidden  # pasted: free
    assert a.locked  # (the original untouched)
    assert so.from_clipboard('hello') is None
    assert so.from_clipboard('{"format": "other"}') is None
    assert Drawing.from_dict(json.loads(text)).shapes


def test_snapping_to_the_board_and_the_shapes():
    from kicad2ems.core.layout import board_layout
    from kicad2ems.core import shapeops as so
    lay = board_layout(os.path.join(EXAMPLES, 'sma_launch.kicad_pcb'))
    sn = so.Snapper(lay)
    t = lay['prims']['tracks'][0]
    x, y = t['pts'][0]
    hit = sn.find(x + 0.05, y - 0.05, 0.2)
    assert hit.what == 'track end' and hit.xy == [x, y]
    pad = lay['prims']['footprints'][0]['pads'][0]
    hit = sn.find(pad['xy'][0] + 0.03, pad['xy'][1], 0.2)
    assert hit.what in ('pad', 'via') and hit.xy == pytest.approx(
        list(pad['xy']))
    # the board's edge (y = 0), between corners
    hit = sn.find(15.2, 0.1, 0.2)
    assert hit.what == 'board edge' and hit.xy == pytest.approx([15.2, 0],
                                                                abs=1e-5)
    assert sn.find(15.2, -3.3, 0.2) is None
    # a shape's corner beats the edge; a shape being moved is skipped
    sn.set_shapes([Shape('rect', [[15, -0.05], [16, -1]])])
    assert sn.find(15.02, -0.04, 0.2).what == 'corner'
    assert sn.find(15.02, -0.04, 0.2, skip={0}).what == 'board edge'
    # hidden shapes are not snapped to
    sn.set_shapes([Shape('rect', [[15, -0.05], [16, -1]], hidden=True)])
    assert sn.find(15.5, -1.0, 0.2) is None


def test_measuring():
    from kicad2ems.core.shapeops import measure
    m = measure([(0, 0), (3, 4)])
    assert (m['distance'], m['dx'], m['dy']) == (5, 3, 4)
    assert m['angle'] == pytest.approx(53.130, abs=1e-3) and m['turn'] is None
    m = measure([(1, 0), (0, 0), (0, 2)])
    assert m['turn'] == pytest.approx(90) and m['length'] == pytest.approx(3)
    assert measure([(0, 0)])['angle'] is None


def test_the_camera_as_matrices():
    from kicad2ems.core import view3d as v3
    cam = dict(center=(5, 2, 1), distance=40, elevation=35, azimuth=-60,
               fov=60)
    m = v3.mvp(cam, 800, 500)
    p = np.array([1.0, 2.0, 3.0])
    x, y = v3.project(m, 800, 500, p)
    o, d = v3.ray(m, 800, 500, x, y)
    assert np.linalg.norm(np.cross(p - o, d)) < 1e-6
    # the camera stands where pyqtgraph puts it: the centre pixel's ray
    # runs from it to the centre
    o, d = v3.ray(m, 800, 500, 400, 250)
    e, a = np.radians(35), np.radians(-60)
    eye = np.array(cam['center']) + 40 * np.array(
        [np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    assert np.linalg.norm(np.cross(eye - np.array(cam['center']), d)) < 1e-6
    # presets: from the top, x runs right and y up the screen
    top = dict(v3.preset('top', (0, 0, 0, 10, 4, 1), 60, 2.0), fov=60)
    mt = v3.mvp(top, 800, 400)
    (x0, y0), (x1, _), (_, y1) = (v3.project(mt, 800, 400, q) for q in
                                  ([0, 0, 0], [10, 0, 0], [0, 4, 0]))
    assert x1 > x0 + 100 and y1 < y0 - 30
    # framed: every corner of the box on the screen
    for q in ((0, 0, 0), (10, 4, 1), (10, 0, 0), (0, 4, 1)):
        px, py = v3.project(mt, 800, 400, q)
        assert 0 < px < 800 and 0 < py < 400
    front = dict(v3.preset('front', (0, 0, 0, 10, 4, 1)), fov=60)
    mf = v3.mvp(front, 800, 400)
    (_, za), (_, zb) = (v3.project(mf, 800, 400, q) for q in ([5, 2, 0],
                                                              [5, 2, 1]))
    assert zb < za  # z up the screen
    side = dict(v3.preset('side', (0, 0, 0, 10, 4, 1)), fov=60)
    ms_ = v3.mvp(side, 800, 400)
    (ya, _), (yb, _) = (v3.project(ms_, 800, 400, q) for q in ([5, 0, 0],
                                                               [5, 4, 0]))
    assert yb > ya  # from the right: y runs right


def test_a_drag_along_one_axis():
    from kicad2ems.core import view3d as v3
    # a ray straight down at (3, 1): along x from (0, 1, 0) it is 3 mm
    assert v3.along_axis([3, 1, 10], [0, 0, -1], [0, 1, 0], 0) == (
        pytest.approx(3))
    # along z, a slanted ray: the nearest point on the upright line
    d = np.array([1.0, 0, -1]) / np.sqrt(2)
    assert v3.along_axis([-5, 0, 7], d, [0, 0, 0], 2) == pytest.approx(2)
    assert v3.along_axis([0, 0, 5], [0, 0, -1], [0, 0, 0], 2) is None
    cam = dict(center=(0, 0, 0), distance=20, elevation=90, azimuth=-90,
               fov=60)
    m = v3.mvp(cam, 400, 400)
    x, y = v3.project(m, 400, 400, [1, 0, 0])
    px, t = v3.pixel_to_segment(m, 400, 400, [0, 0, 0], [2, 0, 0], x, y + 5)
    assert px == pytest.approx(5, abs=0.01) and t == pytest.approx(0.5)


def test_the_clip_plane():
    from shapely.geometry import box
    from kicad2ems.core import view3d as v3
    v, f = prism(box(0, 0, 4, 2), 0, 1)
    cv, cf = v3.clip_mesh(v, f, 0, 1.0)
    assert cv[:, 0].max() == pytest.approx(1.0) and cv[:, 0].min() == 0
    # the kept part's area seen from above: a quarter of the top
    tri = cv[cf]
    up = tri[np.isclose(tri[:, :, 2], 1).all(1)]
    area = 0.5 * np.abs(np.cross(up[:, 1] - up[:, 0],
                                 up[:, 2] - up[:, 0])[:, 2]).sum()
    assert area == pytest.approx(2.0)
    # still wound outward: what is kept of the top faces up
    n = np.cross(up[:, 1] - up[:, 0], up[:, 2] - up[:, 0])
    assert (n[:, 2] > 0).all()
    above = v3.clip_mesh(v, f, 2, 0.5, keep='above')[0]
    assert above[:, 2].min() == pytest.approx(0.5)
    g = v3.clip_outline(box(0, 0, 4, 2), 1, 0.5)
    assert g.area == pytest.approx(2.0)
    assert v3.clip_outline(box(0, 0, 4, 2), 0, -1) is None
    assert v3.clip_range(0, 2, 1.5) == (0, 1.5)
    assert v3.clip_range(0, 2, 3, keep='above') is None


def test_a_convex_prism_as_a_fan():
    from shapely.geometry import box, Point, Polygon
    from kicad2ems.core import view3d as v3

    def volume(v, f):
        t = v[f]
        return np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6

    def outward(v, f, g):
        """Every face's normal points away from the solid's centre line."""
        t = v[f]
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        c = t.mean(1) - [*g.centroid.coords[0], 0.5]
        return ((n * c).sum(1) > -1e-12).all()

    for g in (Point(1, 2).buffer(0.3), box(0, 0, 2, 1),
              Polygon([[0, 0], [2, 0], [2, 1], [0, 1]][::-1])):
        v, f = v3.prism_fast(g, 1, 0)
        assert volume(v, f) == pytest.approx(g.area)
        assert outward(v, f, g)
        assert v[:, 2].min() == 0 and v[:, 2].max() == 1
        assert len(f) < len(prism(g, 0, 1)[1]) + 4
    # not convex, or with a hole: prism's own
    ell = Polygon([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]])
    ring = box(0, 0, 4, 4).difference(box(1, 1, 3, 3))
    for g in (ell, ring):
        v, f = v3.prism_fast(g, 0, 1)
        assert volume(v, f) == pytest.approx(g.area)


def test_outlines_and_the_grid():
    from shapely.geometry import box, Point
    from kicad2ems.core import view3d as v3
    e = v3.prism_edges(box(0, 0, 2, 1), 0, 1)
    # 4 edges top, 4 bottom, 4 uprights
    assert e.shape == (24, 3)
    c = v3.prism_edges(Point(0, 0).buffer(1), 0, 1)
    assert len(c) // 2 == 2 * 64 + 4
    g = v3.grid_spec((0.05, -14, 30, 0), 0.1)
    assert g['spacing'] == pytest.approx(0.5)  # 300 lines of 0.1: too many
    assert g['size'][0] / g['spacing'] <= 120
    x0 = g['center'][0] - g['size'][0] / 2
    assert x0 / g['spacing'] == pytest.approx(round(x0 / g['spacing']))
