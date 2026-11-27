"""Drawn solids resolved for the volume solvers (kicad2ems.core.solids): a
later solid over an earlier one, over the board's dielectric unless it
encloses the whole board, and their extent."""
from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box, mapping

from kicad2ems.core.solids import pieces, encloses, extent, describe


def board(outline=None, solids=()):
    """A 30 x 12 mm board, 0.5 mm thick."""
    return SimpleNamespace(
        outline=outline if outline is not None else box(0, 0, 30, 12),
        layers=[dict(name='d', z0=0.0, z1=0.5, er=3.5, tand=0.002,
                     kind='diel')],
        cu_z={'B.Cu': 0.0, 'F.Cu': 0.5}, solids=list(solids))


def solid(g, material, z0, z1, name='s', er=1.0, tand=0.0):
    return dict(shape=name, material=material, z0=z0, z1=z1, er=er, tand=tand,
                polygon=mapping(g))


def volume(ps, name=None, material=None):
    return sum(p.polygon.area * (p.z1 - p.z0) for p in ps
               if (name is None or p.name == name)
               and (material is None or p.material == material))


def test_an_air_box_inside_a_metal_box_is_a_shell():
    """The can as two solids: the air drawn after the metal hollows it; the
    board inside stays (both enclose it), and its cut-out takes the air."""
    hole = box(14, 5, 16, 7)
    ol = box(0, 0, 30, 12).difference(hole)
    can = solid(box(-4.5, -4.5, 34.5, 16.5), 'metal', -0.5, 5.0, 'can')
    air = solid(box(-4, -4, 34, 16), 'air', 0.0, 4.5, 'inside')
    ps = pieces(board(ol, [can, air]))
    outer, inner = 39 * 21 * 5.5, 38 * 20 * 4.5
    assert volume(ps, 'can') == pytest.approx(outer - inner)
    # the board's dielectric left out of the air, its cut-out not
    assert volume(ps, 'inside') == pytest.approx(inner - ol.area * 0.5)
    assert all(p.around for p in ps)
    # disjoint: no two pieces overlap
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if min(a.z1, b.z1) - max(a.z0, b.z0) > 1e-9:
                assert a.polygon.intersection(b.polygon).area < 1e-9
    assert describe(ps).startswith('2 drawn solids as ')


def test_drawn_into_the_board_a_solid_takes_the_dielectrics_place():
    """A slot of air and a metal post through the board, a puck on it: each
    whole (none encloses the board), the later over the earlier."""
    slot = solid(box(10, 4, 12, 8), 'air', 0.0, 0.5, 'slot')
    post = solid(box(11, 5, 13, 6), 'metal', -1.0, 2.0, 'post')
    puck = solid(box(20, 4, 24, 8), 'dielectric', 0.5, 1.5, 'puck', er=9.8,
                 tand=1e-4)
    b = board(solids=[slot, post, puck])
    assert not any(encloses(b, d) for d in b.solids)
    ps = pieces(b)
    # the post (later) takes its overlap out of the slot
    assert volume(ps, 'slot') == pytest.approx((8 - 1) * 0.5)
    assert volume(ps, 'post') == pytest.approx(2 * 3)
    assert volume(ps, 'puck') == pytest.approx(16)
    p = next(p for p in ps if p.name == 'puck')
    assert (p.er, p.tand, p.material, p.around) == (9.8, 1e-4, 'dielectric',
                                                     False)
    # the other way round: the slot over the post
    ps = pieces(board(solids=[post, slot]))
    assert volume(ps, 'slot') == pytest.approx(8 * 0.5)
    assert volume(ps, 'post') == pytest.approx(6 - 0.5)


def test_what_encloses_the_board():
    b = board()
    big = box(-1, -1, 31, 13)
    assert encloses(b, solid(big, 'dielectric', 0.0, 0.5))
    assert encloses(b, solid(big, 'air', -2.0, 3.0))
    # not the whole height, not the whole outline
    assert not encloses(b, solid(big, 'air', 0.1, 3.0))
    assert not encloses(b, solid(box(1, -1, 31, 13), 'air', -2.0, 3.0))
    # just the board's own outline and stack: the substrate made air
    just = solid(box(0, 0, 30, 12), 'air', 0.0, 0.5)
    assert not encloses(b, just)
    assert volume(pieces(board(solids=[just]))) == pytest.approx(30 * 12 * 0.5)
    # potting round the board: the board's own dielectric stays inside it
    ps = pieces(board(solids=[solid(big, 'dielectric', -1.0, 1.5, er=2.8)]))
    assert volume(ps) == pytest.approx(32 * 14 * 2.5 - 30 * 12 * 0.5)


def test_sheets_and_empty_solids():
    """Metal of no height is a sheet; a dielectric or air of none, or no area,
    is left out. A later solid through a sheet's height takes it out; one it
    only touches does not."""
    lid = solid(box(0, 0, 10, 10), 'metal', 2.0, 2.0, 'lid')
    flat = solid(box(0, 0, 10, 10), 'dielectric', 1.0, 1.0, 'flat')
    line = dict(solid(box(0, 0, 1, 1), 'air', 0, 1, 'line'),
                polygon=mapping(Polygon([(0, 0), (1, 1), (2, 2)])))
    hole = solid(box(4, 4, 6, 6), 'air', 1.0, 3.0, 'hole')
    under = solid(box(0, 0, 10, 10), 'air', 1.0, 2.0, 'under')
    ps = pieces(board(solids=[lid, flat, line, hole, under]))
    sheet = [p for p in ps if p.name == 'lid']
    assert len(sheet) == 1 and sheet[0].z0 == sheet[0].z1 == 2.0
    assert sheet[0].polygon.area == pytest.approx(100 - 4)
    assert {p.name for p in ps} == {'lid', 'hole', 'under'}


def test_the_extent_is_the_boards_and_its_solids():
    b = board(solids=[solid(box(-5, 2, 3, 20), 'metal', -1.0, 4.0)])
    assert extent(b) == (-5, 0, -1.0, 30, 20, 4.0)
    assert extent(board()) == (0, 0, 0.0, 30, 12, 0.5)
    assert pieces(board()) == []
