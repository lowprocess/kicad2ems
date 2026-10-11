"""KiCad reader: board outlines from any Edge.Cuts mix, copper graphics on the board and in footprints,
pad shapes (chamfered, trapezoid, custom primitives), KiCad 5 arcs, polygons with arcs."""
import math
import pytest
from shapely.geometry import Point
from kicad2ems.kicad import KiCadBoard, board_outline

HEAD = '''(kicad_pcb (version 20240108) (generator "test")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3) (loss_tangent 0.02))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "GND") (net 2 "RF")
'''


def board(body):
    return KiCadBoard.from_text(HEAD + body + ')\n')


def lines_rect(x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return "".join(f'  (gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (stroke (width 0.05)) (layer "Edge.Cuts"))\n'
                   for a, b in zip(pts, pts[1:] + pts[:1]))


def test_outline_ignores_mounting_holes_and_cutouts():
    b = board(lines_rect(0, 0, 20, 20) +
              '  (gr_circle (center 1.5 1.5) (end 2 1.5) (stroke (width 0.05)) (fill no) (layer "Edge.Cuts"))\n'
              '  (gr_rect (start 8 8) (end 10 12) (stroke (width 0.05)) (fill no) (layer "Edge.Cuts"))\n')
    ol = board_outline(b)
    assert ol.bounds == pytest.approx((0, 0, 20, 20)) and ol.area == pytest.approx(400)
    holed = board_outline(b, with_holes=True)
    assert holed.area == pytest.approx(400 - 8 - math.pi * 0.25, rel=2e-3)


def test_outline_from_arcs_kicad5_and_footprint_edges():
    # rounded-corner board: lines + KiCad 5 arcs (centre / end / angle); a footprint adds a slot
    r = 2.0
    body = (f'  (gr_line (start {r} 0) (end {20 - r} 0) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_line (start 20 {r}) (end 20 {10 - r}) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_line (start {20 - r} 10) (end {r} 10) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_line (start 0 {10 - r}) (end 0 {r}) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_arc (start {r} {r}) (end 0 {r}) (angle 90) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_arc (start {20 - r} {r}) (end {20 - r} 0) (angle 90) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_arc (start {20 - r} {10 - r}) (end 20 {10 - r}) (angle 90) (layer "Edge.Cuts") (width 0.05))\n'
            f'  (gr_arc (start {r} {10 - r}) (end {r} 10) (angle 90) (layer "Edge.Cuts") (width 0.05))\n'
            '  (footprint "slot" (layer "F.Cu") (at 10 5 90)\n'
            '    (fp_rect (start -1 -0.5) (end 1 0.5) (stroke (width 0.05)) (fill none) (layer "Edge.Cuts")))\n')
    ol = board_outline(board(body))
    assert ol.area == pytest.approx(200 - (4 - math.pi) * r * r, rel=2e-3)
    holed = board_outline(board(body), with_holes=True)
    assert holed.area == pytest.approx(ol.area - 2.0, rel=2e-3)
    assert not holed.contains(Point(10, 5.9)) and holed.contains(Point(10.8, 5.0))      # slot rotated 90 deg


def test_copper_graphics_on_the_board_and_in_footprints():
    b = board(lines_rect(0, 0, 20, 20) +
              '  (gr_line (start 1 1) (end 5 1) (stroke (width 0.4)) (layer "F.Cu") (net 2))\n'
              '  (gr_arc (start 1 3) (mid 3 5) (end 5 3) (stroke (width 0.2)) (layer "F.Cu") (net 2))\n'
              '  (gr_poly (pts (xy 10 10) (xy 12 10) (arc (start 12 10) (mid 13 11) (end 12 12)) (xy 10 12))'
              ' (stroke (width 0)) (fill yes) (layer "B.Cu") (net 1))\n'
              '  (footprint "ant" (layer "F.Cu") (at 15 15 0)\n'
              '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "RF"))\n'
              '    (fp_poly (pts (xy 0 0) (xy 3 0) (xy 3 1) (xy 0 1)) (stroke (width 0)) (fill solid) (layer "F.Cu")))\n')
    rf = [g for l, n, g in b.items if n == 'RF' and l == 'F.Cu']
    assert len(rf) == 4                                                      # line, arc, pad, footprint poly
    assert any(g.area == pytest.approx(4 * 0.4 + math.pi * 0.04, rel=1e-2) for g in rf)
    fp_poly = max(rf, key=lambda g: g.bounds[2])
    assert fp_poly.bounds == pytest.approx((15, 15, 18, 16))                  # takes the pad's net
    gnd = [g for l, n, g in b.items if n == 'GND']
    assert gnd and gnd[0].area > 4 + 0.5 * math.pi * 0.5 * 0.9               # the arc bulge is copper too


@pytest.mark.parametrize('pad,area', [
    ('(pad "1" smd roundrect (at 0 0) (size 2 1) (layers "F.Cu") (roundrect_rratio 0) (chamfer_ratio 0.25) '
     '(chamfer top_left bottom_right))', 2 - 2 * 0.5 * 0.25 ** 2),
    ('(pad "1" smd trapezoid (at 0 0) (size 2 1) (rect_delta 0 0.4) (layers "F.Cu"))', 2.0),
    ('(pad "1" smd custom (at 0 0) (size 0.5 0.5) (layers "F.Cu") (options (clearance outline) (anchor circle)) '
     '(primitives (gr_arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2)) (gr_rect (start -1 -1) (end -0.5 1) (width 0))))',
     None),
])
def test_pad_shapes(pad, area):
    b = board(f'  (footprint "p" (layer "F.Cu") (at 5 5 0)\n    {pad})\n')
    g = b.footprints[0]['pads'][0]['geom']
    if area is not None:
        assert g.area == pytest.approx(area, rel=1e-3)
    else:                                                                    # circle anchor + arc stroke + rect
        assert g.contains(Point(5, 5)) and g.contains(Point(6, 6)) and g.contains(Point(4.25, 5.9))
        assert not g.contains(Point(5.3, 5.3))                               # round anchor, not a square


def test_from_text_rejects_other_files():
    with pytest.raises(ValueError, match='not a .kicad_pcb'):
        KiCadBoard.from_text('(kicad_sch (version 1))')
