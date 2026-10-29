#!/usr/bin/env python3
"""
mom25d on multi-layer boards (ROADMAP.md): the plane and pad options against the meshed model and openEMS, on the
boards whose planes mom25d meshes. Each variant runs once (accuracy; its time is one run's, not a benchmark's).

    python tools/mom_planes_study.py --ref build/bench          # openEMS from tools/bench_solvers.py's run
    python tools/mom_planes_study.py --table-only
    python tools/mom_planes_study.py --rescore --ref build/planes_ref      # the runs so far against new references

Per board and variant: the unknowns, the run's time, the largest |dS| from the meshed model (mom25d with every
plane and pad meshed, the same ports) and the agreement with openEMS (tools/compare_solvers.agreement: rms of the complex
reflection difference in dB re 1, rms transmission difference in dB). Writes build/planes/results.json and .md.
"""
import argparse, glob, json, os, re, subprocess, sys, time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'build', 'planes')
sys.path.insert(0, os.path.join(REPO, 'tools'))
from compare_solvers import agreement            # noqa: E402

BOARDS = ['rf_stripline', 'demo_transition', 'rf_marchand', 'rf_diffvia', 'demo_bandpass', 'demo_highpass',
          'demo_lowpass']           # the examples the planes / pads options change (the other 45: nothing to change)
MESH = ['--mom-planes', 'mesh', '--mom-stitching-pads', 'keep']     # every plane and pad meshed (before 2026-10-29)
VARIANTS = {                                      # label: kicad2ems arguments
    'meshed': MESH,
    'planes pec': ['--mom-planes', 'pec', '--mom-stitching-pads', 'keep'],
    'pads drop': ['--mom-planes', 'mesh', '--mom-stitching-pads', 'drop'],
    'planes pec + pads drop (default)': ['--mom-planes', 'pec', '--mom-stitching-pads', 'drop'],
    'zone': MESH + ['--mom-zone', 'on'],
    'zone + quads': MESH + ['--mom-zone', 'on', '--mom-quads', 'on'],
    'all: pec, drop, zone, quads': ['--mom-planes', 'pec', '--mom-stitching-pads', 'drop', '--mom-zone', 'on',
                                    '--mom-quads', 'on'],
}


def touchstone(d, board, solver):
    p = glob.glob(os.path.join(d, f"{board}_{solver}.s*p"))
    return p[0] if p else None


def read_s(path):
    import skrf
    n = skrf.Network(path)
    return n.f, n.s


def run(board, label, args):
    out = os.path.join(OUT, f"{board}_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')}")
    cmd = [sys.executable, '-m', 'kicad2ems', os.path.join(REPO, 'examples', board + '.toml'), '--no-report',
           '--out', out, '--solvers', 'mom25d'] + args
    t = time.time()
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    log = p.stdout + p.stderr
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'log.txt'), 'w').write(log)
    m = re.search(r'mom25d: ([0-9,]+) unknowns', log)
    return dict(dir=out, code=p.returncode, seconds=time.time() - t,
                unknowns=int(m.group(1).replace(',', '')) if m else None,
                error=None if not p.returncode else log.strip().splitlines()[-1][-200:])


def table(res):
    L = ['| board | variant | unknowns | time | max \\|ΔS\\| from meshed | openEMS: reflection (dB) | '
         'openEMS: transmission (dB) |', '|---|---|---|---|---|---|---|']
    for b, rows in res.items():
        for label, r in rows.items():
            if r.get('code'):
                L.append(f"| {b} | {label} | | | failed: {r.get('error', '')} | | |")
                continue
            ag = r.get('agreement') or [None, None]
            f = lambda v, fmt: '' if v is None else format(v, fmt)
            L.append(f"| {b} | {label} | {f(r.get('unknowns'), ',')} | {r['seconds']:.0f} s | {f(r.get('dS'), '.3f')} | "
                     f"{f(ag[0], '.1f')} | {f(ag[1], '.2f')} |")
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--ref', default=os.path.join(REPO, 'build', 'bench'),
                    help="where <board>_openems/<board>_openems.s2p are (tools/bench_solvers.py's out)")
    ap.add_argument('--only', nargs='*', default=BOARDS)
    ap.add_argument('--variants', nargs='*', default=list(VARIANTS))
    ap.add_argument('--table-only', action='store_true')
    ap.add_argument('--rescore', action='store_true', help="no runs: the runs so far scored again (--ref, --only)")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, 'results.json')
    res = json.load(open(path)) if os.path.exists(path) else {}

    def score(b, r, ref):
        s2p = touchstone(r['dir'], b, 'mom25d')
        if s2p and ref:
            r['agreement'] = agreement(s2p, ref, b)
        base = res[b].get('meshed', {}).get('dir')
        if s2p and base and touchstone(base, b, 'mom25d'):
            f0, S0 = read_s(touchstone(base, b, 'mom25d'))
            f1, S1 = read_s(s2p)
            if len(f0) == len(f1) and np.allclose(f0, f1):
                r['dS'] = float(np.abs(S1 - S0).max())
    if a.rescore:
        for b in a.only:
            ref = touchstone(os.path.join(a.ref, f"{b}_openems"), b, 'openems')
            for r in res.get(b, {}).values():
                if not r.get('code'):
                    score(b, r, ref)
        json.dump(res, open(path, 'w'), indent=1)
    elif not a.table_only:
        for b in a.only:
            ref = touchstone(os.path.join(a.ref, f"{b}_openems"), b, 'openems')
            for label in a.variants:
                r = run(b, label, VARIANTS[label])
                res.setdefault(b, {})[label] = r
                score(b, r, ref)
                print(f"{b:16s} {label:28s} {r.get('unknowns') or 0:7,d} unknowns {r['seconds']:6.0f} s  "
                      f"dS {r.get('dS', float('nan')):.3f}  openEMS {r.get('agreement')}"
                      + (f"  FAILED: {r['error']}" if r['code'] else ''), flush=True)
                json.dump(res, open(path, 'w'), indent=1)
    md = table(res)
    open(os.path.join(OUT, 'results.md'), 'w').write(md + '\n')
    print(md)


if __name__ == '__main__':
    main()
