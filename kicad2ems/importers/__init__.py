"""
Board importers: each reads one kind of board file into the same raw form, which board.Board resolves
(KiCad coordinates: mm, y down).

    raw = open_board('board.kicad_pcb')                      # KiCad

The raw form (Imported): path, importer; stack_items [dict(kind copper / diel / mask, name, t (m), er, tand)]
top to bottom; copper_order (copper layer names, top to bottom); items [(layer, net, geometry)] (all the
copper); shapes (the zones and drawn copper among the items); tracks [dict(layer, net, width, pts)]; vias
[dict(x, y, size, drill, layers, net[, pth])]; footprints [dict(ref, value, fab, props, x, y, rot, side, pads,
dnp, exclude_from_sim)] with pads [dict(name, xy, layers, net, geom, rot)]; edges (outline pieces);
drawings {layer: [outline pieces]} (regions drawn on User.1 ...); port_footprints().
"""
import os
import re


class Imported:
    """The raw form every importer produces (see the module)."""
    importer = None

    def __init__(self, path=''):
        self.path = path
        self.stack_items, self.copper_order = [], []
        self.items, self.shapes, self.tracks, self.vias, self.footprints, self.edges = [], [], [], [], [], []
        self.drawings = {}

    def port_footprints(self, pos_refs=None):
        """{n: footprint} for single-ended ports (Fab text P<n>), {n: {'+': fp, '-': fp}} for
        differential ports (Fab text P<n>+ / P<n>- or P<n>P / P<n>N). Reference / Value count for the
        references listed in a pick-and-place file (pos_refs)."""
        from .kicad.nets import DiffLegs
        pat = re.compile(r'^P(\d+)([+\-PN])?$', re.I)
        out = {}

        def put(m, fp):
            n, leg = int(m.group(1)), (m.group(2) or '').upper()
            if leg:
                out.setdefault(n, DiffLegs())['+' if leg in ('+', 'P') else '-'] = fp
            else:
                out[n] = fp
        for fp in self.footprints:
            texts = list(fp['fab']) + ([fp['value'], fp['ref']] if pos_refs is not None and fp['ref'] in pos_refs else [])
            m = next((m for m in (pat.match(str(t).strip()) for t in texts) if m), None)
            if m:
                put(m, fp)
        for n, v in out.items():
            if isinstance(v, DiffLegs) and set(v) != {'+', '-'}:
                raise ValueError(f"differential port P{n} needs both P{n}+ and P{n}- footprints")
        return dict(sorted(out.items()))


def open_board(path, pos=None):
    """The imported board of a file, by its extension: .kicad_pcb (KiCad). pos: the pick-and-place file, for
    an importer whose ports come from it."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.kicad_pcb':
        from .kicad import KiCadBoard
        return KiCadBoard(path)
    raise ValueError(f"{path}: not a board file kicad2ems reads (.kicad_pcb, or the <name>_board.json of an "
                     f"earlier run)")
