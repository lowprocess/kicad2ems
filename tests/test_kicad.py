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


def test_kicad10_padstacks_unused_layers_and_copper_text():
    """A board saved by KiCad 10 (tests/data/kicad10_padstacks.kicad_pcb, made with pcbnew): pads with a padstack
    (front_inner_back and custom: their shape on each copper layer), vias that remove their unused layers (one
    with a track on In1.Cu keeps its ring there), and copper text in a footprint turned 90 degrees. The areas are
    KiCad's own (TransformShapeToPolygon per layer, TransformTextToPolySet for the text), R-A22."""
    import os
    from shapely.ops import unary_union
    b = KiCadBoard(os.path.join(os.path.dirname(__file__), 'data', 'kicad10_padstacks.kicad_pcb'))
    on = lambda lay, net: unary_union([g for l, n, g in b.items if l == lay and n == net])
    assert on('B.Cu', 'GND').area == pytest.approx(2 * 3.14159 * 0.9 ** 2, rel=0.01)      # both pads round on B.Cu
    assert on('In1.Cu', 'GND').area == pytest.approx(2 * 3.14159 * 0.7 ** 2, rel=0.01)    # 1.4 mm on In1.Cu
    assert on('In2.Cu', 'GND').area == pytest.approx(3.128, rel=0.01)                     # an oval on In2 (custom)
    assert on('F.Cu', 'GND').area == pytest.approx(8.0, rel=0.01)                         # two 2 mm squares
    rings = lambda lay: [g for l, n, g in b.items if l == lay and n == 'SIG' and g.area < 0.3]
    assert len(rings('In1.Cu')) == 1 and rings('In2.Cu') == [] and len(rings('F.Cu')) == 2
    assert any('3 pad and via rings left off' in n for n in b.notes)
    text = [g for l, n, g in b.items if l == 'F.Cu' and n == '' and g.area > 0.3]
    assert len(text) == 1 and text[0].area == pytest.approx(0.938, rel=0.2)                # KiCad's: 0.938 mm2
    assert text[0].centroid.x == pytest.approx(12.799, abs=0.15) and text[0].centroid.y == pytest.approx(7.967, abs=0.15)  # KiCad's
    assert any('stroke font' in n for n in b.notes)


def test_outlined_and_hidden_and_knockout_text():
    """Outlined text (render_cache) is KiCad's polygons, exact; hidden text no copper; knockout text its box less
    the glyphs."""
    b = _text_board('''(gr_text "A" (at 5 5 0) (layer "F.Cu") (effects (font (face "X") (size 1 1)))
      (render_cache "A" 0 (polygon (pts (xy 4 4) (xy 6 4) (xy 6 6) (xy 4 6)) (pts (xy 4.5 4.5) (xy 5.5 4.5) (xy 5.5 5.5) (xy 4.5 5.5)))))
      (gr_text "H" (at 20 5 0) (layer "F.Cu") (hide yes) (effects (font (size 1 1))))
      (gr_text "K" (at 30 5 0) (layer "B.Cu" knockout) (effects (font (size 1 1))))''')
    exact = [g for l, n, g in b.items if l == 'F.Cu']
    assert len(exact) == 1 and exact[0].area == pytest.approx(3.0)                         # 4 - 1: its hole kept
    ko = [g for l, n, g in b.items if l == 'B.Cu']
    assert len(ko) == 1 and ko[0].envelope.contains(ko[0].centroid) and ko[0].area > 0.5


def test_cutouts_are_air_and_a_split_region_says_so():
    """A cut-out inside the outline (Edge.Cuts) is a hole in the board's outline, so the dielectric has none there
    and the FDTD model fills it with air; a region that splits the board keeps its largest piece and says how
    much is left out (R-E6: both had been silent, the cut-out substrate)."""
    raw = _text_board('''(gr_circle (center 10 10) (end 12 10) (stroke (width 0.05)) (fill no) (layer "Edge.Cuts"))
      (gr_rect (start 2 14) (end 18 15) (stroke (width 0.05)) (fill no) (layer "Edge.Cuts"))
      (segment (start 1 5) (end 19 5) (width 0.3) (layer "F.Cu") (net 2))
      (zone (net 1) (net_name "GND") (layer "B.Cu") (polygon (pts (xy 0 0) (xy 20 0) (xy 20 20) (xy 0 20)))
        (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 20 0) (xy 20 20) (xy 0 20))))''')
    b = Board(raw, nets=['RF', 'GND'], need_ports=False, log=lambda *a: None)
    assert len(b.outline.interiors) == 2 and b.outline.area == pytest.approx(400 - 3.14159 * 4 - 16, rel=1e-3)
    assert any('2 cut-out(s)' in n for n in b.notes)
    split = Board(raw, nets=['RF', 'GND'], need_ports=False, region=[3, 12, 17, 16], log=lambda *a: None)
    assert any('splits the board into 2 pieces' in n for n in split.notes) and split.outline.area == pytest.approx(28)
