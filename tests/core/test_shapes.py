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
