"""
A drawn can's own loss against the closed form: a rectangular cavity of copper
(a drawn metal box with an air box inside it, the Draw page's shapes) round a
bare board of air (er 1), two small probes weakly coupled to its lowest mode
(E vertical: Pozar's TE101 with b the height). Its unloaded Q from S21's
resonance, Q0 = QL / (1 - |S21| at the peak) for two equal probes, against
Pozar's Q_c (eq. 6.46) for its size and sigma; Palace's from its eigenmode run
(each port's Q_ext taken out).

    python tools/fdtd/can_q.py make OUT                 # board, drawing, config
    python tools/fdtd/can_q.py run OUT gfdtd [--sigma 5.8e7] [kicad2ems ...]
    python tools/fdtd/can_q.py fit OUT/out/can_q_gfdtd.s2p [--sigma 5.8e7]
    python tools/fdtd/can_q.py modes OUT/out/can_q_palace_modes.json

The cavity is a x d in plan, b tall inside (mm), its walls, floor and lid t
thick; the board (W x H, 1 mm of er 1, no ground plane) sits on its floor gap
mm in from the walls. Each probe is a 1 mm square pad on F.Cu over a 2 mm one
on B.Cu, a lumped port between them, 3 mm from an x wall and centred in y,
where E is sin(3 pi / 32) of its peak: Q_ext about 13 x Q0 (2 mm pads a
quarter of the way in had given 0.0016 x: S21 -0.55 dB, Q0 read through 1 /
(1 - |S21|) = 16).
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
C0, MU0, ETA0 = 299792458.0, 4e-7 * math.pi, 376.730313668
NAME = 'can_q'
# the cavity inside (mm): a along x, d along y, b tall; wall t; the board's
# gap to the walls; the probes' pads, their distance from the x walls
A, D, B, T, GAP, PAD, REF, H, XP = 32.0, 27.0, 10.0, 1.0, 1.0, 1.0, 2.0, 1.0, 3.0


def closed_form(a=A, b=B, d=D, sigma=5.8e7):
    """(f0 Hz, Q_c) of a rectangular cavity's TE101 mode, a x b x d mm, E along
    b, walls of sigma S/m (Pozar, Microwave Engineering, eqs. 6.40, 6.46)."""
    a, b, d = a * 1e-3, b * 1e-3, d * 1e-3
    f0 = C0 / 2 * math.sqrt(1 / a ** 2 + 1 / d ** 2)
    k = 2 * math.pi * f0 / C0
    rs = math.sqrt(2 * math.pi * f0 * MU0 / (2 * sigma))
    q = ((k * a * d) ** 3 * b * ETA0 / (2 * math.pi ** 2 * rs)
         / (2 * a ** 3 * b + 2 * b * d ** 3 + a ** 3 * d + a * d ** 3))
    return f0, q


def make(out):
    """The board, the drawing and the config in folder `out`."""
    sys.path.insert(0, os.path.join(ROOT, 'examples'))
    sys.path.insert(0, os.path.join(ROOT, 'src'))
    import make_rf_examples as mk
    from kicad2ems.core.shapes import Drawing, Shape
    os.makedirs(out, exist_ok=True)
    W, Hb = A - 2 * GAP, D - 2 * GAP
    bd = mk.Board(nets=("", "GND", "P1", "P2"))
    yc = Hb / 2
    for n, x in ((1, XP - GAP), (2, A - XP - GAP)):
        bd.rect(x - PAD / 2, yc - PAD / 2, x + PAD / 2, yc + PAD / 2,
                layer='F.Cu', net=1 + n)
        bd.rect(x - REF / 2, yc - REF / 2, x + REF / 2, yc + REF / 2,
                layer='B.Cu', net=1)
        bd.port(n, x, yc, 0)
    bd.write(os.path.join(out, NAME + '.kicad_pcb'), W, Hb, ground=False,
             sub=(H, 1.0, 0.0, 'AIR'))
    # model coordinates: the board from (0, -Hb) to (W, 0)
    x0, y0, x1, y1 = -GAP, -Hb - GAP, W + GAP, GAP
    Drawing([
        Shape('rect', [[x0 - T, y0 - T], [x1 + T, y1 + T]], material='metal',
              z0=-T, z1=B + T, name='can'),
        Shape('rect', [[x0, y0], [x1, y1]], material='air', z0=0.0, z1=B,
              name='inside'),
    ]).save(os.path.join(out, NAME + '_shapes.json'))
    f0, q = closed_form()
    span = 0.012 * f0 / 1e9
    with open(os.path.join(out, NAME + '.toml'), 'w') as fh:
        fh.write(f"""# tools/fdtd/can_q.py: a {A:g} x {D:g} x {B:g} mm copper cavity drawn round
# a bare board of air, two weakly coupled probes; TE101 at {f0 / 1e9:.4f} GHz,
# Q_c {q:.0f} with sigma 5.8e7 (Pozar)
board   = "{NAME}.kicad_pcb"
shapes  = "{NAME}_shapes.json"
ground  = "B.Cu"
fmin    = {f0 / 1e9 - span:.4f}
fmax    = {f0 / 1e9 + span:.4f}
points  = 2001
out     = "out"
finish  = "none"

[solver.openems]
bc      = "MUR"
air     = 1.0
excite  = ["P1"]
end_db  = -60
end_on  = "energy"      # the cavity's ring-down, not the probes' pulse
max_steps = 30000000
""")
    print(f"wrote {out}: TE101 {f0 / 1e9:.4f} GHz, Q_c {q:.1f} (copper)")


def fit(f, s21):
    """(f0 Hz, QL, |S21| at f0, rms misfit) of S21 near its peak: A / (1 + 2j
    QL (f / f0 - 1)) + B, fitted to the complex S21 within 4 half widths."""
    from scipy.optimize import least_squares
    k = int(np.argmax(np.abs(s21)))
    pk = np.abs(s21[k])
    half = np.flatnonzero(np.abs(s21) >= pk / math.sqrt(2))
    w = max(f[half[-1]] - f[half[0]], 2 * (f[1] - f[0]))
    sel = np.abs(f - f[k]) <= 4 * w
    fs, ss = f[sel], s21[sel]
    p0 = [f[k], f[k] / w, s21[k].real, s21[k].imag, 0.0, 0.0]

    def model(p, x):
        return ((p[2] + 1j * p[3]) / (1 + 2j * p[1] * (x / p[0] - 1))
                + p[4] + 1j * p[5])

    def res(p):
        r = model(p, fs) - ss
        return np.r_[r.real, r.imag] / pk
    r = least_squares(res, p0, x_scale=[w, f[k] / w, pk, pk, pk, pk])
    p = r.x
    return (p[0], abs(p[1]), abs(p[2] + 1j * p[3]),
            float(np.sqrt(np.mean(r.fun ** 2))))


def report(path, sigma):
    from kicad2ems.core.touchstone import read_touchstone
    f, S = read_touchstone(path)
    f0, ql, peak, mis = fit(f, S[:, 1, 0])
    fc, qc = closed_form(sigma=sigma) if sigma else (closed_form()[0], None)
    q0 = ql / (1 - peak)
    print(f"{os.path.basename(path)}: f0 {f0 / 1e9:.5f} GHz "
          f"({(f0 / fc - 1) * 100:+.2f} % from {fc / 1e9:.5f}), QL {ql:.1f}, "
          f"|S21| {peak:.4f} ({20 * math.log10(peak):.2f} dB), "
          f"Q0 = QL / (1 - |S21|) {q0:.1f}"
          + (f", Q_c {qc:.1f} ({(q0 / qc - 1) * 100:+.1f} %)" if qc else '')
          + f"; fit misfit {mis:.2e}")
    return dict(f0=f0, ql=ql, peak=peak, q0=q0, qc=qc)


def modes(path, sigma):
    d = json.load(open(path))
    fc, qc = closed_form(sigma=sigma)
    for r in d['rows']:
        qext = [v for v in r['q_ext'].values() if v > 0]
        q0 = 1 / (1 / r['Q'] - sum(1 / v for v in qext)) if qext else r['Q']
        print(f"mode {r['mode']}: {r['f_ghz']:.5f} GHz "
              f"({(r['f_ghz'] * 1e9 / fc - 1) * 100:+.2f} %), Q {r['Q']:.1f}, "
              f"Q_ext {', '.join(f'{v:.4g}' for v in qext)}, Q0 {q0:.1f} "
              f"(Q_c {qc:.1f}: {(q0 / qc - 1) * 100:+.1f} %)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('make')
    p.add_argument('out')
    p = sub.add_parser('run')
    p.add_argument('out')
    p.add_argument('solver')
    p.add_argument('--sigma', type=float, default=None)
    p = sub.add_parser('fit')
    p.add_argument('s2p')
    p.add_argument('--sigma', type=float, default=None)
    p = sub.add_parser('modes')
    p.add_argument('json')
    p.add_argument('--sigma', type=float, default=5.8e7)
    a, rest = ap.parse_known_args(argv)
    if a.cmd == 'make':
        make(a.out)
    elif a.cmd == 'run':
        cmd = [sys.executable, '-m', 'kicad2ems', NAME + '.toml', '--solvers',
               a.solver, '--no-report', '--no-reuse']
        if a.sigma:
            cmd += ['--sigma', f"{a.sigma:g}"]
        subprocess.run(cmd + rest, cwd=a.out, check=True)
    elif a.cmd == 'fit':
        report(a.s2p, a.sigma)
    else:
        modes(a.json, a.sigma)


if __name__ == '__main__':
    main()
