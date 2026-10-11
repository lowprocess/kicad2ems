"""KiCad import: S-expressions, stackup, copper, ports, nets, outline."""
import pytest
from kicad2ems.kicad import KiCadBoard, board_outline, resolve_nets, DiffLegs
from kicad2ems.kicad.sexpr import parse_sexpr, items, val
from kicad2ems.model import Model


def test_sexpr():
    t = parse_sexpr('(a (b 1 "x y") (b 2) (c (d 3.5)))')
    assert t[0] == 'a' and len(items(t, 'b')) == 2 and items(t, 'b')[0][2] == 'x y'
    assert val(items(t, 'c')[0], 'd', cast=float) == 3.5


def test_board_stackup_ports_outline(demo_files):
    base, _ = demo_files
    b = KiCadBoard(base + '.kicad_pcb')
    cu = [it['name'] for it in b.stack_items if it['kind'] == 'copper']
    assert cu == ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
    assert sorted(b.port_footprints()) == [1, 2]
    ol = board_outline(b)
    assert ol is not None and ol.area == pytest.approx(24 * 10, rel=1e-6)


def test_nets_hierarchical_and_unknown():
    assert resolve_nets(['REF_SMA_N'], {'/REF_SMA_N', 'GND'}) == {'/REF_SMA_N'}
    assert resolve_nets(['gnd'], {'/REF', 'GND'}) == {'GND'}
    with pytest.raises(ValueError, match='nets with copper'):
        resolve_nets(['RF_X'], {'/REF', 'GND'})


def test_differential_ports(demo_files):
    _, diff = demo_files
    b = KiCadBoard(diff + '.kicad_pcb')
    fps = b.port_footprints()
    assert all(isinstance(v, DiffLegs) for v in fps.values())
    m = Model(b, nets=['D_P', 'D_N'], pos_file=diff + '-top.pos', ground='B.Cu')
    assert [p.name for p in m.ports] == ['P1+', 'P1-', 'P2+', 'P2-']
    assert [n for n, _ in m.user_ports] == ['P1', 'P2']
    assert all(p.ref_layer == 'B.Cu' and p.w == pytest.approx(0.25, rel=1e-3) for p in m.ports)


def test_port_errors_are_explained(demo_files):
    base, _ = demo_files
    with pytest.raises(ValueError, match='no reference copper'):
        Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF'], pos_file=base + '-top.pos')   # ground net left out


def test_port_fields_both_prefixes():
    from kicad2ems.model import port_field
    assert port_field({'mom.layer': 'In2.Cu'}, 'layer') == 'In2.Cu'
    assert port_field({'port.layer': 'B.Cu', 'mom.layer': 'In2.Cu'}, 'layer') == 'B.Cu'
    assert port_field({}, 'z0', 50) == 50
