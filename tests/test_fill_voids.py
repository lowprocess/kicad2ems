"""fill_voids: the ground copper's voids that the nets left out made, filled (Board._fill_voids): which are kept (a
selected net's copper or via in them; with 'far', the ones beside the signal), what the filled copper is, the
option."""
import json
import pytest
from shapely.geometry import Point, Polygon, box
from kicad2ems.board import Board
from kicad2ems.importers import Imported

MM = 1e-3
ANTIPAD, ANTIPAD_VIA = Point(5, 10).buffer(0.6), Point(25, 15).buffer(0.6)
BESIDE = box(14.5, 10.4, 15.5, 11.4)                    # 1 mm2, 0.3 mm from the trace: its return path
FAR = box(14, 16.5, 16, 17.5)                           # 2 mm2, 6 mm away: another net's clearance
SMALL = box(20, 10.3, 20.3, 10.6)                       # 0.09 mm2 beside the trace: a thermal-relief gap
ROUND_AN_ISLAND = box(23, 3, 27, 7)                     # a void with ground copper standing alone in it
ISLAND = box(24.5, 4.5, 25.5, 5.5)
VOIDS = (ANTIPAD, ANTIPAD_VIA, BESIDE, FAR, SMALL, ROUND_AN_ISLAND)


def raw_board(extra=()):
    """Two layers 0.2 mm apart: a trace on F.Cu from (2, 10) to (28, 10) with a via at its start, another signal
    via at (25, 15) with no pad on B.Cu; B.Cu a ground pour with the voids above; OTHER, a net not selected,
    whose pad sits in FAR."""
    raw = Imported('fake.kicad_pcb')
    raw.stack_items = [dict(kind='copper', name='F.Cu', t=0.035 * MM), dict(kind='diel', name='core', t=0.2 * MM, er=4.3,
                                                                          tand=0.02),
                       dict(kind='copper', name='B.Cu', t=0.035 * MM)]
    raw.copper_order = ['F.Cu', 'B.Cu']
    pour = box(0, 0, 30, 20)
    for v in VOIDS:
        pour = pour.difference(v)
    raw.items = [('B.Cu', 'GND', pour), ('B.Cu', 'GND', ISLAND), ('F.Cu', 'SIG', box(2, 9.9, 28, 10.1)),
                 ('B.Cu', 'SIG', Point(5, 10).buffer(0.3)), ('B.Cu', 'OTHER', Point(15, 17).buffer(0.3)), *extra]
    raw.shapes = [raw.items[0]]
    raw.vias = [dict(x=5.0, y=10.0, size=0.6, drill=0.3, layers=['F.Cu', 'B.Cu'], net='SIG'),
                dict(x=25.0, y=15.0, size=0.6, drill=0.3, layers=['F.Cu', 'B.Cu'], net='SIG')]
    return raw


def holes(b, layer='B.Cu'):
    return [Polygon(r) for p in b.copper[layer] for r in p.interiors]


def held(b, void):
    """The void is still a hole of the B.Cu copper (y is flipped in the model)."""
    c = void.centroid
    return any(h.contains(Point(c.x, -c.y)) for h in holes(b)) or \
        not any(p.contains(Point(c.x, -c.y)) for p in b.copper['B.Cu'])


def build(mode, **kw):
    return Board(raw_board(), nets=['SIG', 'GND'], need_ports=False, log=lambda *a: None, fill_voids=mode, **kw)


def test_off_is_the_copper_as_imported():
    b = build('off')
    assert len(holes(b)) == 6 and b.filled == {} and not any('fill_voids' in n for n in b.notes)
    assert len(b.copper['B.Cu']) == 3                           # the pour, the island, the via's pad


def test_far_fills_what_is_away_from_the_signal_and_what_is_small():
    b = build('far')
    assert [held(b, v) for v in VOIDS] == [True, True, True, False, False, False]
    assert len(b.copper['B.Cu']) == 2                           # the island joined the pour
    n, added = b.filled['B.Cu']
    assert n == 3 and added == pytest.approx(FAR.area + SMALL.area + ROUND_AN_ISLAND.area - ISLAND.area, abs=0.02)
    assert list(b.filled) == ['B.Cu'] and b.copper_nets['B.Cu'].count('GND') == 1
    note, = [x for x in b.notes if 'fill_voids' in x]
    assert note.startswith('fill_voids = far: B.Cu 3 voids (+17.') and 'away from the signal copper' in note
    assert not any(Polygon(r).area < 0.05 for p in b.copper['B.Cu'] for r in p.interiors)      # no slivers


def test_all_keeps_only_what_holds_the_model_s_copper_or_via():
    b = build('all')
    assert [held(b, v) for v in VOIDS] == [True, True, False, False, False, False]
    assert b.filled['B.Cu'][0] == 4 and len(holes(b)) == 2
    assert b.filled['B.Cu'][1] == pytest.approx(sum(v.area for v in VOIDS[2:]) - ISLAND.area, abs=0.02)


def test_a_net_that_is_selected_keeps_its_clearance():
    b = Board(raw_board(), nets=['SIG', 'GND', 'OTHER'], need_ports=False, log=lambda *a: None, fill_voids='all')
    assert held(b, FAR) and b.filled['B.Cu'][0] == 3            # OTHER's pad is the model's now


def test_a_clearance_that_crosses_the_region_s_edge_is_still_a_void():
    """A region cuts the pour and the void: the copper there is filled as on the whole board."""
    region = [10, 14, 15, 19]                                   # cuts FAR in two: in the region it is a notch
    off = Board(raw_board(), nets=['SIG', 'GND'], need_ports=False, log=lambda *a: None, region=region)
    far = Board(raw_board(), nets=['SIG', 'GND'], need_ports=False, log=lambda *a: None, region=region, fill_voids='far')
    area = lambda b: sum(p.area for p in b.copper['B.Cu'])
    assert area(off) == pytest.approx(25 - 1.0, abs=0.01) and not holes(off)
    assert area(far) == pytest.approx(25, abs=0.01) and far.filled['B.Cu'][1] == pytest.approx(1.0, abs=0.01)


def test_it_is_kept_with_the_board_and_is_an_option(tmp_path):
    from kicad2ems.config import build_settings, ConfigError
    b = build('far')
    again = Board.from_dict(json.loads(json.dumps(b.to_dict(), default=list)), log=lambda *a: None)
    assert again.filled == b.filled and again.request['fill_voids'] == 'far'
    assert sum(p.area for p in again.copper['B.Cu']) == pytest.approx(sum(p.area for p in b.copper['B.Cu']))
    assert build_settings(dict(board='b.kicad_pcb')).fill_voids == 'off'
    assert build_settings(dict(board='b.kicad_pcb', fill_voids='far')).fill_voids == 'far'
    with pytest.raises(ConfigError, match='off, far, all'):
        build_settings(dict(board='b.kicad_pcb', fill_voids='some'))
    with pytest.raises(ValueError, match='fill_voids: one of off, far, all'):
        build('some')
