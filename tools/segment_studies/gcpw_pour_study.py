"""The coplanar-ground pour study (docs/segment-studies.md): examples/rf_gcpw's
line (1 mm strip, 0.22 mm gaps, fences every 1.2 mm, 0.8 mm FR-4) with its
pours widened to 19 mm each side, and clipped to bands of 3, 1.5 and 0.8 mm
beside the gaps, each solved with mom25d on its fine mesh and with --mom-zone
on; the unknowns and each one's largest |dS| from the wide pour on the fine
mesh. usage: gcpw_pour_study.py OUT_DIR
(2026-11-21: wide 4,301 unknowns; with the zone 3,929 and |dS| 0.004; bands
7,046 / 6,059 / 9,017 unknowns, |dS| 0.005 / 0.012 / 0.009.)"""
import glob
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, '..', '..', 'examples')
sys.path.insert(0, EX)
import make_rf_examples as M  # noqa: E402


def board(path, band=None, H=40.0, w=1.0, length=24.0, pitch=1.2):
    s = round(M.solve(lambda s: -M.cpwg(w, s, M.H_SUB, M.ER)[0], -50.0, 0.05,
                      3.0), 3)
    yc, W = H / 2, length
    bd = M.Board()
    bd.track([(0, yc), (W, yc)], w)
    ga, gb = yc - w / 2 - s, yc + w / 2 + s
    bd.rect(0, 0 if band is None else ga - band, W, ga, net=1)
    bd.rect(0, gb, W, H if band is None else gb + band, net=1)
    n = int((W - 2.0) / pitch)
    for k in range(n + 1):
        x = 1.0 + k * (W - 2.0) / n
        bd.via(x, ga - 0.6)
        bd.via(x, gb + 0.6)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)
    os.replace(path, path + '.kicad_pcb')


def main(out):
    from kicad2ems.core.touchstone import read_touchstone
    os.makedirs(out, exist_ok=True)
    cfg = open(os.path.join(EX, 'rf_gcpw.toml')).read()
    ref, rows = None, []
    for name, band in (('wide', None), ('band3', 3.0), ('band1p5', 1.5),
                       ('band0p8', 0.8)):
        base = os.path.join(out, f'g_{name}')
        board(base, band)
        open(base + '.toml', 'w').write(cfg.replace(
            'rf_gcpw.kicad_pcb', os.path.basename(base) + '.kicad_pcb')
            .replace('points  = 401', 'points  = 201'))
        for mode, extra in (('fine', []), ('zone', ['--mom-zone', 'on'])):
            o = f'{base}_{mode}'
            subprocess.run([sys.executable, '-m', 'kicad2ems.core.cli',
                            base + '.toml', '--solvers', 'mom25d',
                            '--no-report', '--out', o] + extra, check=True,
                           stdout=subprocess.DEVNULL)
            f, S = read_touchstone(glob.glob(f'{o}/*mom25d*.s2p')[0])
            ref = S if ref is None else ref
            res = glob.glob(f'{o}/*mom25d.result.json')[0]
            import json
            n = json.load(open(res))['info']['unknowns']
            rows.append((name, mode, n, float(np.abs(S - ref).max())))
            print(f"{name:8s} {mode:5s} {n:6d} unknowns, max |dS| vs wide-fine "
                  f"{rows[-1][3]:.4f}", flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
