"""KiCad import: S-expressions, stackup, copper, ports, nets, outline."""
import pytest
from kicad2ems.importers.kicad import KiCadBoard, board_outline, resolve_nets, DiffLegs
from kicad2ems.importers.kicad.sexpr import parse_sexpr, items, val
from kicad2ems.board import Board


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
    m = Board(b, nets=['D_P', 'D_N'], pos_file=diff + '-top.pos', ground='B.Cu')
    assert [p.name for p in m.ports] == ['P1+', 'P1-', 'P2+', 'P2-']
    assert [n for n, _ in m.user_ports] == ['P1', 'P2']
    assert all(p.ref_layer == 'B.Cu' and p.w == pytest.approx(0.25, rel=1e-3) for p in m.ports)


def test_port_errors_are_explained(demo_files):
    base, _ = demo_files
    with pytest.raises(ValueError, match='no reference copper'):
        Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF'], pos_file=base + '-top.pos')   # ground net left out


def test_port_fields_both_prefixes():
    from kicad2ems.board import port_field
    assert port_field({'mom.layer': 'In2.Cu'}, 'layer') == 'In2.Cu'
    assert port_field({'port.layer': 'B.Cu', 'mom.layer': 'In2.Cu'}, 'layer') == 'B.Cu'
    assert port_field({}, 'z0', 50) == 50


_HEAD4 = '''(kicad_pcb (version 20240108) (generator "test")
  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.2) (epsilon_r 4.3) (loss_tangent 0.02))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 1.0) (epsilon_r 4.3) (loss_tangent 0.02))
    (layer "In2.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 3" (type "prepreg") (thickness 0.2) (epsilon_r 4.3) (loss_tangent 0.02))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "GND") (net 2 "RF")
  (gr_rect (start 0 0) (end 20 20) (stroke (width 0.05)) (fill no) (layer "Edge.Cuts"))
'''


def _text_board(body):
    return KiCadBoard.from_text(_HEAD4 + body + ')\n')


def test_blind_and_micro_vias_pass_the_layers_between_their_ends():
    """A blind via F.Cu to In2.Cu passes In1.Cu, as a through via passes them all (only through vias were spread
    over the stack: fill_voids then filled a blind via's antipad on In1.Cu, its barrel shorted to the plane)."""
    for kind in ('blind', 'micro', ''):
        b = _text_board(f'(via {kind} (at 10 10) (size 0.6) (drill 0.3) (layers "F.Cu" "In2.Cu") (net 2))')
        assert b.vias[0]['layers'] == ['F.Cu', 'In1.Cu', 'In2.Cu'], kind


def test_what_the_import_read_wrong_before():
    """KiCad 5's zones (filled_polygon without a layer: the zone's), a pad's drill offset (its copper beside its
    hole), an NPTH pad (a hole: no copper), and a zone never filled (left out, and said)."""
    b = _text_board('''(zone (net 1) (net_name GND) (layer In1.Cu) (tstamp 0) (hatch edge 0.508)
      (polygon (pts (xy 0 0) (xy 20 0) (xy 20 20) (xy 0 20)))
      (filled_polygon (pts (xy 0 0) (xy 20 0) (xy 20 20) (xy 0 20))))''')
    assert [(l, n, round(g.area)) for l, n, g in b.items] == [('In1.Cu', 'GND', 400)]
    b = _text_board('''(footprint "p" (layer "F.Cu") (at 5 5 0)
      (pad "1" thru_hole oval (at 0 0) (size 3 1) (drill 0.6 (offset 1 0)) (layers "*.Cu") (net 2 "RF"))
      (pad "" np_thru_hole circle (at 3 3) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask")))''')
    pads = b.footprints[0]['pads']
    assert [round(v, 6) for v in pads[0]['geom'].bounds] == [4.5, 4.5, 7.5, 5.5]
    assert pads[1]['layers'] == [] and all(n == 'RF' for _, n, _ in b.items)
    b = _text_board('(zone (net 1) (net_name "GND") (layers "In1.Cu") (polygon (pts (xy 0 0) (xy 20 0) (xy 20 20))))')
    assert not b.items and len(b.notes) == 1 and 'not filled' in b.notes[0]
