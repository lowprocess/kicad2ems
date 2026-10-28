"""
What the example board generators share (make_rf_examples.py, make_antennas.py, make_demo_board.py): a KiCad
item's uuid, and the board items they write alike. Each generator's Board adds its own tracks, ports and file.
"""
import uuid


def u():
    return str(uuid.uuid4())


class Items:
    """A board's items as KiCad text (self.items): filled rectangles and polygons, vias."""

    def rect(self, x0, y0, x1, y1, layer='F.Cu', net=2):
        self.items.append(f'  (gr_rect (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (stroke (width 0) (type solid)) '
                          f'(fill yes) (layer "{layer}") (net {net}) (uuid "{u()}"))')

    def poly(self, pts, layer='F.Cu', net=2):
        xy = ' '.join(f'(xy {x:.4f} {y:.4f})' for x, y in pts)
        self.items.append(f'  (gr_poly (pts {xy}) (stroke (width 0) (type solid)) (fill yes) (layer "{layer}") (net {net}) '
                          f'(uuid "{u()}"))')

    def via(self, x, y, net=1, size=0.6, drill=0.3, layers=('F.Cu', 'B.Cu')):
        """A via; between other layers than F.Cu and B.Cu, blind."""
        kind = '' if layers == ('F.Cu', 'B.Cu') else ' blind'
        self.items.append(f'  (via{kind} (at {x:.4f} {y:.4f}) (size {size}) (drill {drill}) (layers "{layers[0]}" "{layers[1]}") '
                          f'(net {net}) (uuid "{u()}"))')
