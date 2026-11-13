"""
The board as drawn, for a front end to show before anything is run: every net's copper by layer, the outline, the
stack and the footprints, with no ports asked for (a board for a rail's drop or a crosstalk question has none).

    from kicad2ems.layout import board_layout, net_at, part_at
    lay = board_layout('board.kicad_pcb')               # Board.to_dict() of all the copper, plus 'footprints'
    net_at(lay, 'F.Cu', x, y)                           # the net whose copper is under a point (model mm), or None
    part_at(lay, 'F.Cu', x, y)                          # the footprint whose pads' box is under it, or None

It is the model board's own dict (model mm: y up, y = -y of KiCad), so what draws a run's board draws this; with
    footprints      [dict(ref, value, layer, box=[x0, y0, x1, y1], pads=[dict(name, net)])]: every footprint with a
                    pad on copper, its box the pads' (a part to pick, a pad's net)
A front end lets an analysis's setting be picked from it where the option says what it holds (an option's `pick`:
'net', 'nets', 'part' or 'parts').
"""
import os


def board_layout(path):
    """The layout of a .kicad_pcb (the module docstring has its form). ValueError: not a board that can be read."""
    from .importers.kicad import KiCadBoard
    from .board import Board
    if not str(path).lower().endswith('.kicad_pcb'):
        raise ValueError(f"{path}: a .kicad_pcb")
    kb = KiCadBoard(path)
    d = Board(kb, need_ports=False, log=lambda *a: None).to_dict()
    fps = []
    for fp in kb.footprints:
        pads = [q for q in fp.get('pads', []) if any(str(l).endswith('.Cu') for l in q.get('layers', ()))]
        if not pads or not fp.get('ref'):
            continue
        b = [q['geom'].bounds for q in pads if q.get('geom') is not None]
        if not b:
            continue
        x0, y0, x1, y1 = min(v[0] for v in b), min(v[1] for v in b), max(v[2] for v in b), max(v[3] for v in b)
        layers = {l for q in pads for l in q.get('layers', ()) if str(l).endswith('.Cu')}
        fps.append(dict(ref=fp['ref'], value=fp.get('value') or '',
                        layer='B.Cu' if layers == {'B.Cu'} else 'F.Cu' if 'F.Cu' in layers else sorted(layers)[0],
                        through=len(layers) > 1, box=[float(x0), float(-y1), float(x1), float(-y0)],      # y up
                        pads=[dict(name=str(q['name']), net=q.get('net') or '') for q in pads]))
    d['footprints'] = fps
    d['layout_of'] = os.path.abspath(path)
    return d


def net_at(layout, layer, x, y):
    """The net whose copper on `layer` is under (x, y) (model mm), or None; a named net before an unnamed one.
    (The layer's shapes are prepared once and kept in the layout, under '_prepared'.)"""
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    by = layout.setdefault('_prepared', {})
    if layer not in by:
        by[layer] = [(it.get('net') or '', prep(shape(it['polygon']))) for it in layout.get('copper', {}).get(layer, [])]
    p = Point(x, y)
    hits = [net for net, g in by[layer] if g.contains(p)]
    return next((n for n in hits if n), hits[0] if hits else None)


def part_at(layout, layer, x, y):
    """The footprint whose pads' box on `layer` is under (x, y): the smallest such box's (a part inside another's
    outline), or None."""
    hits = [f for f in layout.get('footprints', []) if (f['layer'] == layer or f.get('through'))
            and f['box'][0] <= x <= f['box'][2] and f['box'][1] <= y <= f['box'][3]]
    if not hits:
        return None
    return min(hits, key=lambda f: (f['box'][2] - f['box'][0]) * (f['box'][3] - f['box'][1]))['ref']
