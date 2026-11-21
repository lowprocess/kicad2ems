"""The net selections of docs/segment-studies.md, as a comma-separated list for
`python -m kicad2ems.core.segment BOARD --nets "$(python select_nets.py ...)"`.

  select_nets.py pairs BOARD      every leg of a differential pair whose tracks
                                  are on the board (net names ending _P/_N or
                                  +/-, with the other leg present): the Thor
                                  carrier's 288 legs
  select_nets.py rf-wide BOARD    every net with a track at least 0.75 mm wide
                                  on F.Cu or B.Cu, power, ground, GPIO and USB
                                  nets left out: the LimeRFE's GCPW RF nets
                                  (0.79 mm), 470 of them
"""
import collections
import re
import sys

import numpy as np

from kicad2ems.core.importers.kicad import KiCadBoard

TWIN = {'_P': '_N', '_N': '_P', '+': '-', '-': '+'}


def pairs(kb):
    nets = {t['net'] for t in kb.tracks}
    out = []
    for n in sorted(nets):
        m = re.search(r'(_P|_N|\+|-)$', n)
        if m and n[:m.start()] + TWIN[m.group(1)] in nets:
            out.append(n)
    return out


POWER = re.compile(r'^(GND|\+|-)|\dV\d|VCC|VDD|PWR|SUPPLY|_EN|EN_|VREF|BIAS|'
                   r'12EXT|^/PowerSupply|^/GPIO_Subsystem|^/USB', re.I)


def rf_wide(kb, width=0.75):
    wide = collections.defaultdict(float)
    for t in kb.tracks:
        if t['layer'] in ('F.Cu', 'B.Cu') and t['width'] >= width:
            wide[t['net']] += float(np.sum(np.hypot(*np.diff(
                np.asarray(t['pts']), axis=0).T)))
    return sorted(n for n in wide if not POWER.search(n))


if __name__ == '__main__':
    how, board = sys.argv[1:3]
    kb = KiCadBoard(board)
    nets = {'pairs': pairs, 'rf-wide': rf_wide}[how](kb)
    print(f"{len(nets)} nets", file=sys.stderr)
    print(','.join(nets))
