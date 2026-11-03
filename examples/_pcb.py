"""
What the example board generators share (make_rf_examples.py, make_antennas.py, make_demo_board.py): a KiCad
item's uuid, the board items they write alike, and the board file around them.
"""
import uuid


def u():
    return str(uuid.uuid4())


class Items:
    """A board's items as KiCad text (self.items; its nets self.nets): filled rectangles and polygons, vias, track
    segments, port footprints, and write_board, the KiCad 8 file around them."""

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

    def segment(self, x0, y0, x1, y1, w, layer='F.Cu', net=2):
        self.items.append(f'  (segment (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (width {w}) (layer "{layer}") '
                          f'(net {net}) (uuid "{u()}"))')

    def port_footprint(self, ref, text, x, y, rot, z0=None, layer=None, width=None):
        """A port: a footprint with the Fab text `text` (P1, P2+, ...), its rotation into the circuit; z0, layer,
        width: its port.z0, port.layer, port.width fields."""
        L = [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
             f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
             f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))']
        for key, v in (('z0', z0), ('layer', layer), ('width', width)):
            if v:
                v = f"{v:g}" if isinstance(v, (int, float)) else v
                L.append(f'    (property "port.{key}" "{v}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u()}"))')
        L.append(f'    (fp_text user "{text}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )')
        self.items += L

    def write_board(self, path, W, H, hs, er, tand, material='FR4'):
        """The board file: copper F.Cu and B.Cu, with In1.Cu and In2.Cu where hs (the dielectrics' thicknesses, mm,
        top down) has three; the items; a W x H mm outline."""
        cu = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'] if len(hs) == 3 else ['F.Cu', 'B.Cu']
        ids = {'F.Cu': 0, 'In1.Cu': 1, 'In2.Cu': 2, 'B.Cu': 31}
        st = []
        for i, c in enumerate(cu):
            st.append(f'    (layer "{c}" (type "copper") (thickness 0.035))')
            if i < len(hs):
                st.append(f'    (layer "dielectric {i + 1}" (type "core") (thickness {hs[i]}) (material "{material}") '
                          f'(epsilon_r {er:g}) (loss_tangent {tand:g}))')
        L = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
             f'  (general (thickness {sum(hs):g}))\n  (paper "A4")',
             '  (layers ' + ' '.join(f'({ids[c]} "{c}" signal)' for c in cu) +
             ' (37 "F.SilkS" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
             '  (setup (stackup'] + st + [
             '    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))',
             '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(self.nets))]
        L += self.items
        L.append(f'  (gr_rect (start 0 0) (end {W:.4f} {H:.4f}) (stroke (width 0.1) (type default)) (fill none) '
                 f'(layer "Edge.Cuts") (uuid "{u()}"))\n)')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write("\n".join(L) + "\n")
