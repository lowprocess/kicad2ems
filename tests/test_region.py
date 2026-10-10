"""Region and ports engine: rectangle / polygon / drawing-layer regions, nets = auto, ports on pads and
at points, ports where tracks cross the region edge, and the settings that carry them."""
import pytest
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.config import build_settings, ConfigError
from kicad2openems.region import parse_port_spec, region_polygon


def _with_drawing(base, rect):
    text = open(base + '.kicad_pcb').read().rstrip().rstrip(')')
    x0, y0, x1, y1 = rect
    text += (f'  (gr_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.1) (type default)) (fill none) '
             f'(layer "User.1") (uuid "00000000-0000-0000-0000-000000000001"))\n)\n')
    return KiCadBoard(text=text)


def test_region_shapes(demo_files):
    base, _ = demo_files
    b = _with_drawing(base, (0, 0, 17, 10))
    assert region_polygon(b, 'User.1').bounds == pytest.approx((0, 0, 17, 10))
    assert region_polygon(b, [(0, 0), (17, 0), (17, 10)]).area == pytest.approx(85)
    assert region_polygon(b, (17, 10, 0, 0)).bounds == pytest.approx((0, 0, 17, 10))
    with pytest.raises(ValueError, match='no shapes on that layer'):
        region_polygon(b, 'User.2')


def test_auto_nets_cut_ports_and_layer_region(demo_files):
    base, _ = demo_files
    b = _with_drawing(base, (0, 0, 17, 10))
    m = Model(b, nets='auto', pos_file=base + '-top.pos', ground='B.Cu', region='User.1', cut_ports=True,
              log=lambda *a: None)
    assert set(m.sel) == {'RF', 'GND'}
    names = [p.name for p in m.ports]
    assert names == ['P1', 'P3']                                     # P2 is outside; P3 at the cut
    p3 = m.ports[1]
    assert p3.layer == 'In2.Cu' and p3.source.startswith('cut') and p3.c[0] == pytest.approx(17 - 0.0385, abs=2e-3)
    assert p3.sgn == -1                                              # pointing back into the region
    assert any('outside the region' in n for n in m.notes)


def test_pad_and_point_ports(boards):
    base = boards['launch']
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets='auto', pos_file=base + '-top.pos',
              ports=['P3=J1.1', 'P4@5,5'], log=lambda *a: None)
    p = {q.name: q for q in m.ports}
    assert p['P3'].source == 'pad J1.1' and p['P3'].w == pytest.approx(1.6, abs=0.01)
    assert p['P4'].source.startswith('point') and p['P4'].layer == 'F.Cu' and p['P4'].w == pytest.approx(0.32)
    assert p['P4'].sgn == 1                                          # toward the far end of the feed
    with pytest.raises(ValueError, match='no footprint J9'):
        Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ports=['P3=J9.1'],
              log=lambda *a: None)


def test_port_specs_and_settings():
    assert parse_port_spec('p3=J1.1') == ('P3', 'pad', ('J1', '1'))
    assert parse_port_spec('P4@1.5,-2,In2.Cu,180') == ('P4', 'at', (1.5, -2.0, 'In2.Cu', 180.0))
    with pytest.raises(ValueError, match='P<n>=<ref>.<pad>'):
        parse_port_spec('J1.1')
    s = build_settings(dict(board='b.kicad_pcb', region='User.1', ports='P3@1,2,F.Cu', cut_ports='yes', nets='auto'))
    assert s.region == 'User.1' and s.ports == ['P3@1,2,F.Cu'] and s.cut_ports is True and s.nets == ['auto']
    assert build_settings(dict(board='b', region=[[0, 0], [5, 0], [5, 5]])).region == [(0, 0), (5, 0), (5, 5)]
    assert build_settings(dict(board='b', region='0,0,5,5')).region == (0, 0, 5, 5)
    with pytest.raises(ConfigError, match='region'):
        build_settings(dict(board='b', region='1,2,3'))
