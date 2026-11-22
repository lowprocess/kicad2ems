"""The GPU's two factors on a board at fixed frequencies: the LU and the
complex-symmetric LDL^T (policy.LDL), each a fresh solve (no factor kept), the
largest |dS| between them and each one's seconds. usage:
  mom25d_ldl_compare.py BOARD_board.json GHZ[,GHZ...] [build_board options as
                        key=value, e.g. thickness=grow edges=coarse zone=auto]
(the reference check of 2026-11-22: the example boards' runs and the Thor and
LimeRFE pieces; a <name>_board.json comes from any kicad2ems run)."""
import sys
import time

import numpy as np

from kicad2ems.mom25d import policy
from kicad2ems.mom25d.api import build_board


def main():
    board, ghz = sys.argv[1], [float(x) * 1e9 for x in sys.argv[2].split(',')]
    kw = dict(port='via', log=lambda *a, **k: None)
    for a in sys.argv[3:]:
        k, v = a.split('=', 1)
        try:
            v = float(v)
        except ValueError:
            pass
        kw[k] = v
    if 'dmax' in kw:
        kw['dmax'] *= 1e-3
    res = {}
    for sym in (False, True):
        policy.LDL = sym
        _, sim = build_board(board, min(ghz) / 2, max(ghz), **kw)
        sim.reuse = 'off'
        for f in ghz:
            t = time.time()
            res[sym, f] = sim.sparams(f)
            dt = time.time() - t
            names = {r[0] for r in sim.log.records}
            how = ('LDL^T' if 'LDL^T factor (GPU)' in names else
                   'LU (GPU)' if 'LU factor (GPU)' in names else 'CPU')
            print(f"{'LDL^T' if sym else 'LU'} asked, {f / 1e9:g} GHz: "
                  f"{dt:.2f} s, N = {sim.Ne + sim.Nv}, {how}", flush=True)
    d = max(np.abs(res[True, f] - res[False, f]).max() for f in ghz)
    print(f"{board}: max |dS| LU against LDL^T {d:.2e}")


if __name__ == '__main__':
    main()
