"""Two of mom25d's solve paths on a board at fixed frequencies, each a fresh
solve (no factor kept): the largest |dS| between them, each one's seconds and
peak memory growth. usage:
  mom25d_ldl_compare.py BOARD_board.json GHZ[,GHZ...] [--what ldl|half]
                        [build_board options as key=value, e.g.
                        thickness=grow edges=coarse zone=auto dmax=0.5]
  --what ldl   the GPU's LU against its complex-symmetric LDL^T (policy.LDL)
  --what half  the dense double Z whole against by its lower half
               (policy.HALF; both factored as LDL^T)
(the reference checks of 2026-11-22: the example boards' runs and the Thor and
LimeRFE pieces; a <name>_board.json comes from any kicad2ems run)."""
import resource
import sys
import time

import numpy as np

from kicad2ems.mom25d import policy
from kicad2ems.mom25d.api import build_board


def main():
    args = sys.argv[1:]
    what = 'ldl'
    if '--what' in args:
        i = args.index('--what')
        what = args[i + 1]
        del args[i:i + 2]
    board, ghz = args[0], [float(x) * 1e9 for x in args[1].split(',')]
    kw = dict(port='via', log=lambda *a, **k: None)
    for a in args[2:]:
        k, v = a.split('=', 1)
        try:
            v = float(v)
        except ValueError:
            pass
        kw[k] = v
    if 'dmax' in kw:
        kw['dmax'] *= 1e-3
    flag = dict(ldl='LDL', half='HALF')[what]
    names = dict(ldl=('LU', 'LDL^T'), half=('whole', 'half'))[what]
    res = {}
    for on in (False, True):
        setattr(policy, flag, on)
        if what == 'half':
            policy.LDL = True
        _, sim = build_board(board, min(ghz) / 2, max(ghz), **kw)
        for f in ghz:
            # a fresh solve: no factor kept (reuse 'off' would make the
            # CPU's plan the matrix-free one, which keeps none)
            for st in getattr(sim.fast, 'states', {}).values():
                st.kept = []
            r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            t = time.time()
            res[on, f] = sim.sparams(f)
            dt = time.time() - t
            recs = {r[0]: r[2] for r in sim.log.records}
            how = ('LDL^T (GPU)' if 'LDL^T factor (GPU)' in recs else
                   'LU (GPU)' if 'LU factor (GPU)' in recs else
                   'LDL^T (CPU)' if 'LDL^T factor' in recs else 'LU (CPU)')
            fill = recs.get('direct fill', '')
            print(f"{names[on]}, {f / 1e9:g} GHz: {dt:.2f} s, N = "
                  f"{sim.Ne + sim.Nv}, {how}, "
                  f"{'half' if 'half' in fill else 'whole'} Z; peak "
                  f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.1f}"
                  f" GB (+{(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - r0) / 1e6:.1f})",
                  flush=True)
    d = max(np.abs(res[True, f] - res[False, f]).max() for f in ghz)
    print(f"{board}: max |dS| {names[0]} against {names[1]} {d:.2e}")


if __name__ == '__main__':
    main()
