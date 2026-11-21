"""Two segment results compared, channel by channel. usage:
  compare_results.py REF/segment.json OTHER/segment.json

Each channel's insertion loss at the report's frequencies (median and worst
difference, the channels open in either left out) and each victim's worst
crosstalk (the larger of its NEXT and FEXT sums), then, where both results have
the same ports, the joined S itself: max |dS|, the through paths' and the
reflections' dB differences. As the Thor's windows were checked against its
groups solved whole (docs/segment-studies.md)."""
import json
import os
import sys

import numpy as np

from kicad2ems.core.touchstone import read_touchstone


def load(p):
    r = json.load(open(p, encoding='utf-8'))
    f, S = read_touchstone(os.path.join(os.path.dirname(p), r['touchstone']))
    return r, f, S


def main(a, b):
    ra, fa, Sa = load(a)
    rb, fb, Sb = load(b)
    A = {c['channel']: c for c in ra['report']['channels']}
    B = {c['channel']: c for c in rb['report']['channels']}
    ok = [k for k in A if k in B and A[k]['il_db'][0] > -100
          and B[k]['il_db'][0] > -100]
    d = np.array([[B[k]['il_db'][i] - A[k]['il_db'][i]
                   for i in range(len(A[k]['il_db']))] for k in ok])
    ghz = ra['report']['ghz']
    print(f"{len(ok)} channels in both (of {len(A)} and {len(B)}); insertion "
          f"loss difference at {ghz} GHz: median {np.round(np.median(abs(d), 0), 3)}"
          f" dB, worst {np.round(abs(d).max(0), 3)} dB")
    va = {v['victim']: max(v['next_sum_pct'], v['fext_sum_pct'])
          for v in ra['report']['victims']}
    vb = {v['victim']: max(v['next_sum_pct'], v['fext_sum_pct'])
          for v in rb['report']['victims']}
    x = np.array([(va[k], vb[k]) for k in va if k in vb])
    if len(x):
        print(f"worst crosstalk per victim (%): median {np.median(x[:, 0]):.3f}"
              f" / {np.median(x[:, 1]):.3f}, difference median "
              f"{np.median(abs(x[:, 1] - x[:, 0])):.4f}, worst "
              f"{abs(x[:, 1] - x[:, 0]).max():.4f} points")
    if ra['ports'] == rb['ports'] and Sa.shape == Sb.shape:
        db = lambda s: 20 * np.log10(np.abs(s) + 1e-15)
        print(f"same ports: max |dS| {np.abs(Sa - Sb).max():.4f}")
        legs = {}
        for x_ in ra['port_nets']:
            legs.setdefault(x_['net'], []).append(ra['ports'].index(x_['port']))
        k = [int(np.argmin(abs(fa - g * 1e9))) for g in ghz]
        th = np.array([[db(Sb[q, j, i]) - db(Sa[q, j, i]) for q in k]
                       for i, j in (v for v in legs.values() if len(v) == 2)])
        rl = np.array([[db(Sb[q, i, i]) - db(Sa[q, i, i]) for q in k]
                       for v in legs.values() for i in v])
        print(f"through paths |dB| median {np.round(np.median(abs(th), 0), 3)}, "
              f"worst {np.round(abs(th).max(0), 3)}; reflections |dB| median "
              f"{np.round(np.median(abs(rl), 0), 2)}")


if __name__ == '__main__':
    main(*sys.argv[1:3])
