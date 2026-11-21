"""Replay the adaptive sweep on a piece's solved S (its <name>_mom25d.result.npz,
201 points over FMIN-FMAX GHz) with each entry's delay taken out before the fit
and put back after, against the sweep as it is: the experiment that set delay
extraction aside (docs/segment-studies.md: the Thor's In7 bus, 16 ports: 72
solves converged without it, the 80-solve cap unconverged with it). usage:
  afs_delay_replay.py RESULT.npz FMIN FMAX [PORTS (every Nth, default 16)]"""
import sys

import numpy as np
from scipy.interpolate import CubicSpline

from kicad2ems.emsdev.afs import adaptive_sweep


def main(path, fmin, fmax, nports=16):
    S = np.load(path)['S']
    sel = list(range(0, S.shape[1], max(1, S.shape[1] // int(nports))))
    S = S[:, sel][:, :, sel][:, :int(nports)][:, :, :int(nports)]
    f = np.linspace(float(fmin) * 1e9, float(fmax) * 1e9, len(S))
    w = 2 * np.pi * f
    cs = CubicSpline(f, S, axis=0)
    gd = -np.gradient(np.unwrap(np.angle(S), axis=0), w, axis=0)
    wt = np.abs(S) ** 2
    tau = np.clip((gd * wt).sum(0) / np.maximum(wt.sum(0), 1e-30), 0, None)
    print(f"{S.shape[1]} ports; delays median {np.median(tau) * 1e9:.3f} ns")
    for name, T in (('as it is', 0 * tau), ('each delay out', tau)):
        _, M, fs, _, hist = adaptive_sweep(
            lambda x: cs(x) * np.exp(1j * 2 * np.pi * x * T), f[0], f[-1],
            grid=f, max_points=80, tol=5e-3, log=lambda *a: None)
        err = np.abs(M * np.exp(-1j * w[:, None, None] * T) - S).max()
        print(f"{name}: {len(fs)} solves, converged "
              f"{hist[-1].get('converged')}, max |S - model| {err:.1e}")


if __name__ == '__main__':
    main(*sys.argv[1:5])
