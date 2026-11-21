"""How a layer piece's coupling falls with the gap between its nets: each
pair of nets' strongest |S| between their ports (from the piece's solved S)
against the edge-to-edge gap of their tracks on the layer, binned. A floor that
does not fall with the gap is a mode the model carries, not trace coupling
(the Thor's stripline pieces before their stitching vias: -40 to -46 dB at
1.5 mm and more; after, -67 dB median). usage:
  coupling_vs_gap.py BOARD PIECE_FOLDER LAYER
(PIECE_FOLDER: a segment run's piece, holding <name>_board.json and its
S-parameters)."""
import glob
import json
import os
import re
import sys

import numpy as np
from shapely.geometry import LineString
from shapely.ops import unary_union

from kicad2ems.core.importers.kicad import KiCadBoard
from kicad2ems.core.touchstone import read_touchstone


def main(board, folder, layer):
    name = os.path.basename(os.path.normpath(folder))
    run = json.load(open(os.path.join(folder, f"{name}_run.json")))
    f, S = read_touchstone(sorted(glob.glob(os.path.join(folder, '*.s*p')))[0])
    ports = run['ports']
    d = json.load(open(os.path.join(folder, f"{name}_board.json")))
    pnet = {p['name']: p.get('net') for p in d.get('ports', [])}
    kb = KiCadBoard(board)
    nets = sorted({n for n in pnet.values() if n})
    geo = {}
    for t in kb.tracks:
        if t['layer'] == layer and t['net'] in nets and len(t['pts']) > 1:
            geo.setdefault(t['net'], []).append(
                LineString(t['pts']).buffer(t['width'] / 2))
    geo = {n: unary_union(g) for n, g in geo.items()}
    stem = lambda s: re.sub(r'(_P|_N|\+|-)$', '', s)
    db = lambda x: 20 * np.log10(np.abs(x) + 1e-15)
    rows = []
    for i, a in enumerate(nets):
        for b in nets[i + 1:]:
            if stem(a) == stem(b) or a not in geo or b not in geo:
                continue
            ia = [k for k, p in enumerate(ports) if pnet.get(p) == a]
            ib = [k for k, p in enumerate(ports) if pnet.get(p) == b]
            pk = max(db(S[:, x, y]).max() for x in ia for y in ib)
            rows.append((geo[a].distance(geo[b]), pk))
    for lo, hi in ((0, .2), (.2, .45), (.45, .9), (.9, 1.5), (1.5, 99)):
        sel = [p for g, p in rows if lo <= g < hi]
        if sel:
            print(f"gap {lo:.2f}-{hi:.2f} mm: {len(sel):4d} pairs, strongest "
                  f"{max(sel):6.1f} dB, median {np.median(sel):6.1f} dB")


if __name__ == '__main__':
    main(*sys.argv[1:4])
