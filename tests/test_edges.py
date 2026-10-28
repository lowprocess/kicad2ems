"""Edge walls (--edge-walls): PEC sheets where the ground planes end at the chosen board sides, kept
clear of signal copper; settings, solver setup and the run summary."""
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.edges import edge_walls, parse_sides, describe
from kicad2ems.config import build_settings, ConfigError


@pytest.fixture(scope='module')
def launch(boards):
    base = boards['launch']
    return Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', log=lambda *a: None)


def test_parse_sides():
    assert parse_sides('all') == ['xmin', 'xmax', 'ymin', 'ymax']
    assert parse_sides('ymax, XMIN') == ['xmin', 'ymax'] and parse_sides(['xmax']) == ['xmax']
    assert parse_sides(None) == [] and parse_sides('') == []
    with pytest.raises(ValueError, match='unknown side'):
        parse_sides('left')
    assert build_settings(dict(board='b.kicad_pcb', edge_walls='xmin,ymin')).edge_walls == ['xmin', 'ymin']
    with pytest.raises(ConfigError, match='edge-walls'):
        build_settings(dict(board='b.kicad_pcb', edge_walls='top'))


def test_walls_follow_the_planes_and_clear_the_launch(launch):
    m = launch
    walls = edge_walls(m, parse_sides('all'))
    x0, y0, x1, y1 = m.outline.bounds
    zs = sorted(m.cu_z.values())
    left = [w for w in walls if w[0] == 'x' and w[1] < (x0 + x1) / 2]
    right = [w for w in walls if w[0] == 'x' and w[1] > (x0 + x1) / 2]
    assert len(left) == 1 and left[0][2:4] == pytest.approx((y0, y1), abs=0.11)   # the whole left edge
    assert left[0][4:] == pytest.approx((zs[0], zs[-1]))                        # B.Cu to F.Cu
    assert len([w for w in walls if w[0] == 'y']) == 2
    # right edge: the launch pad (y -5 +- 0.8 in model coordinates) and its keepout stay open
    assert right and all(w[3] <= -5.0 - 0.8 - 0.5 + 1e-6 or w[2] >= -5.0 + 0.8 + 0.5 - 1e-6 for w in right)
    # beside the opening the F.Cu pour and B.Cu still meet the edge: walls there join B.Cu to F.Cu
    assert any(w[4:] == pytest.approx((zs[0], zs[-1])) for w in right)
    assert 'xmin 1 piece' in describe(walls, m)
    assert edge_walls(m, []) == []
