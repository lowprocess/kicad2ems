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
  select_nets.py netclass BOARD CLASS[,CLASS...]
                                  every net with tracks in those net classes of
                                  the board's project (BOARD.kicad_pro beside
                                  it: its explicit assignments and its patterns,
                                  KiCad's wildcards * and ?): LibreVNA's RF,
                                  ThunderScope's LVDS_ADC, PCIe, FE_50Z, ...
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


def netclass(kb, classes, board):
    """The nets with tracks that the project beside board puts in classes
    (KiCad 7+: net_settings' netclass_assignments, then its
    netclass_patterns in order, the first that matches)."""
    import fnmatch
    import json
    import os
    pro = os.path.splitext(board)[0] + '.kicad_pro'
    ns = json.load(open(pro)).get('net_settings', {})
    want = set(classes)
    asg = {n: (c if isinstance(c, list) else [c])
           for n, c in (ns.get('netclass_assignments') or {}).items()}
    pats = [(p['pattern'], p['netclass'])
            for p in ns.get('netclass_patterns', [])]
    out = []
    for n in sorted({t['net'] for t in kb.tracks}):
        got = asg.get(n)
        if got is None:
            got = [c for pat, c in pats if fnmatch.fnmatchcase(n, pat)][:1]
        if want & set(got):
            out.append(n)
    return out


if __name__ == '__main__':
    how, board = sys.argv[1:3]
    kb = KiCadBoard(board)
    if how == 'netclass':
        nets = netclass(kb, sys.argv[3].split(','), board)
    else:
        nets = {'pairs': pairs, 'rf-wide': rf_wide}[how](kb)
    print(f"{len(nets)} nets", file=sys.stderr)
    print(','.join(nets))
