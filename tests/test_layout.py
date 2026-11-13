"""
The board as drawn (kicad2ems.layout): what a front end shows before a run and picks nets and parts on, and the
options that say what may be picked.
"""
import os

import pytest

from kicad2ems.layout import board_layout, net_at, part_at
from k2e_fixtures import ROOT

BUS = os.path.join(ROOT, 'examples', 'rf_bus.kicad_pcb')       # three traces D0, D1, D2 over a plane on In1.Cu


def test_the_layout_is_all_the_copper_with_no_ports_asked_for():
    lay = board_layout(BUS)
    assert lay['layout_of'] == os.path.abspath(BUS) and lay['footprints'] == []
    nets = {it['net'] for it in lay['copper']['F.Cu']}
    assert nets == {'D0', 'D1', 'D2'} and 'In1.Cu' in lay['copper'] and not lay.get('ports')
    with pytest.raises(ValueError, match='kicad_pcb'):
        board_layout('board.json')


def test_a_point_names_the_net_whose_copper_is_under_it():
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems import si
    lay = board_layout(BUS)
    ends = {n: si.trace_ends(KiCadBoard(BUS), n) for n in ('D0', 'D1', 'D2')}
    for n, (a, b) in ends.items():                              # (the traces jog together between their ends)
        for e in (a, b):
            assert net_at(lay, 'F.Cu', e['x'], -e['y']) == n    # the model's y is up: -y of KiCad's
    y_d0, y_d1 = -ends['D0'][0]['y'], -ends['D1'][0]['y']
    x = ends['D0'][0]['x']
    assert net_at(lay, 'F.Cu', x, (y_d0 + y_d1) / 2) is None    # the gap between two traces: no copper
    assert net_at(lay, 'B.Cu', x, y_d0) is None                 # a layer with none
    assert net_at(lay, 'In1.Cu', x, y_d0) is not None           # the plane under them
    assert '_prepared' in lay and 'F.Cu' in lay['_prepared']    # the layer's shapes, prepared once


def test_a_point_names_the_part_whose_pads_are_under_it(boards):
    lay = board_layout(boards['lowpass'] + '.kicad_pcb')
    fps = {f['ref']: f for f in lay['footprints']}
    assert {'L1', 'C2', 'L3'} <= set(fps)
    for ref, f in fps.items():
        x0, y0, x1, y1 = f['box']
        assert x1 > x0 and y1 > y0 and f['pads'] and all('net' in p and 'name' in p for p in f['pads'])
        assert part_at(lay, f['layer'], (x0 + x1) / 2, (y0 + y1) / 2) == ref
        assert part_at(lay, f['layer'], x0 - 5.0, y0 - 5.0) != ref
    c2 = fps['C2']
    assert part_at(lay, 'B.Cu', (c2['box'][0] + c2['box'][2]) / 2, (c2['box'][1] + c2['box'][3]) / 2) is None \
        or c2.get('through')                                    # a surface part: on its own layer only
    # a part inside another's box: the smaller is the one meant
    big = dict(ref='U9', value='', layer=c2['layer'], through=False,
               box=[c2['box'][0] - 3, c2['box'][1] - 3, c2['box'][2] + 3, c2['box'][3] + 3], pads=[])
    both = dict(lay, footprints=lay['footprints'] + [big])
    assert part_at(both, c2['layer'], (c2['box'][0] + c2['box'][2]) / 2, (c2['box'][1] + c2['box'][3]) / 2) == 'C2'
    assert part_at(both, c2['layer'], c2['box'][0] - 2, c2['box'][1] - 2) == 'U9'


def test_options_say_what_a_front_end_may_pick_on_the_board():
    from kicad2ems.si import Crosstalk
    from kicad2ems.api import frontend, API_VERSION
    picks = {o['key']: o.get('pick') for o in Crosstalk().options()}
    assert picks['nets'] == 'nets' and picks['aggressor'] == 'nets' and picks['near'] == 'parts'
    assert picks['rise'] is None
    assert frontend.board_layout is board_layout and frontend.net_at is net_at and frontend.part_at is part_at
    assert API_VERSION >= (2, 11)
