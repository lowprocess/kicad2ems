"""The examples whose solids are drawn beside their boards (make_rf_examples.py,
the Draw page's shapes): each generator writes its board and the drawing the
repository keeps beside its config, and the board with it is what the example
says (ports, the solids' pieces, a can that closes round the board)."""
import json
import os
import sys

import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), '..', '..', 'examples')


@pytest.fixture(scope='module')
def gen():
    sys.path.insert(0, EXAMPLES)
    try:
        import make_rf_examples
    finally:
        sys.path.remove(EXAMPLES)
    return make_rf_examples


def _made(gen, kind, tmp_path):
    """(the Board with its drawing, the drawing's dict) of example rf_<kind>
    made in tmp_path."""
    from kicad2ems.core.importers import open_board
    from kicad2ems.core.board import Board
    base = tmp_path / f'rf_{kind}'
    gen.KINDS[kind](str(base) + '.kicad_pcb')
    shapes = str(base) + '_shapes.json'
    b = Board(open_board(str(base) + '.kicad_pcb'), shapes=shapes,
              ground='B.Cu', log=lambda *a: None)
    return b, json.load(open(shapes))


@pytest.mark.parametrize('kind', ['dr_bandpass', 'dr_bandstop'])
def test_the_kept_drawing_is_the_generators(gen, kind, tmp_path):
    """examples/rf_<kind>_shapes.json (in the repository, shipped) is what the
    generator writes: a changed generator needs its drawing written again."""
    _, d = _made(gen, kind, tmp_path)
    kept = json.load(open(os.path.join(EXAMPLES, f'rf_{kind}_shapes.json')))
    assert d == kept


@pytest.mark.parametrize('kind,ports', [('dr_bandpass', 2),
                                        ('dr_bandstop', 2)])
def test_a_can_closes_round_the_board(gen, kind, ports, tmp_path):
    """The can: a metal shell round the board (the mesh ends at its outer
    faces), the board's dielectric kept inside it, the lines' ends clear of
    its walls; the puck and its support drawn inside."""
    from kicad2ems.core.solids import pieces, enclosure, extent
    b, d = _made(gen, kind, tmp_path)
    assert len(b.ports) == ports
    ps = pieces(b)
    x0, y0, z0, x1, y1, z1 = extent(b, ps)
    box = enclosure(b, ps, (x0 - 1, y0 - 1, z0 - 1, x1 + 1, y1 + 1, z1 + 1))
    assert box == pytest.approx((x0, y0, z0, x1, y1, z1))
    assert all(p.around for p in ps if p.name in ('can', 'inside'))
    zc = b.cu_z['F.Cu']
    metal = [p.polygon for p in ps
             if p.material == 'metal' and p.z0 <= zc <= p.z1]
    assert metal
    for poly in b.copper['F.Cu']:  # no line touches the can's walls
        assert all(poly.distance(m) > 0.5 for m in metal)
    names = {s['name']: s for s in d['shapes']}
    puck, sup = names['puck'], names['support']
    assert puck['er'] == 38.0 and puck['z0'] == sup['z1']
    assert [p.er for p in ps if p.name == 'puck'] == [38.0]

