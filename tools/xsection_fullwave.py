#!/usr/bin/env python3
"""
Copper thickness on a coupled pair by 3D FDTD (openEMS), against the field solver: rf_diffvia's two pairs, odd mode, as
sheets and with 35 um copper. Z = sqrt(int V^2 / int I^2) of the wave passing a probe. FDTD's own error at the edges is a
few %, so the ratio thick / sheet is what compares with the field solver's. About 20 s a case on 24 cores:

    LD_LIBRARY_PATH=/opt/openems/lib /opt/openems/venv/bin/python tools/xsection_fullwave.py OUT
"""
import os, sys, numpy as np
from CSXCAD import ContinuousStructure
from openEMS import openEMS

OUT = sys.argv[1]
C0 = 299792458.0


def lines(keys, fine, coarse, grow=1.25):
    """Mesh lines through the keys, fine at each, growing to coarse in between."""
    keys = sorted(set(np.round(keys, 6)))
    out = [keys[0]]
    for a, b in zip(keys[:-1], keys[1:]):
        L = b - a
        left, right, h1, h2, sl, sr = [], [], fine, fine, 0.0, 0.0
        while sl + sr < L:
            if sl <= sr:
                left.append(h1); sl += h1; h1 = min(h1 * grow, coarse)
            else:
                right.append(h2); sr += h2; h2 = min(h2 * grow, coarse)
        steps = np.array(left + right[::-1]) * L / (sl + sr)
        out += list(a + np.cumsum(steps))
    return np.array(out)


def run(name, w, s, zs, t, top, er, cover, length=30.0, side=2.5, nts=22000):
    """A symmetric pair: strips w, gap s, from z = zs up by t, plane at 0; top: a plane there, or (cover) open above, er
    below zs."""
    path = os.path.join(OUT, name)
    os.makedirs(path, exist_ok=True)
    fdtd = openEMS(NrTS=nts, EndCriteria=0)
    f0 = 5e9
    fdtd.SetGaussExcite(f0, f0)
    fdtd.SetBoundaryCond(['PEC', 'PEC', 'MUR', 'MUR', 'PEC', 'PEC' if not cover else 'MUR'])
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)
    x0, x1 = s / 2, s / 2 + w
    grid.AddLine('x', lines([-x1 - side, -x1, -x0, x0, x1, x1 + side], 0.012, 0.15))
    zk = [0.0, zs, zs + t, top if not cover else top]
    grid.AddLine('z', lines(zk, 0.012, 0.15))
    grid.AddLine('y', np.linspace(0, length, int(length / 0.1) + 1))
    diel = csx.AddMaterial('diel', epsilon=er)
    if cover:
        diel.AddBox([-x1 - side, 0, 0], [x1 + side, length, zs])
    else:
        diel.AddBox([-x1 - side, 0, 0], [x1 + side, length, top])
    pec = csx.AddMetal('pec')
    for sgn in (-1, 1):
        a, b = sorted((sgn * x0, sgn * x1))
        pec.AddBox([a, 0, zs], [b, length, zs + t], priority=10)
    y0 = 2.0
    for sgn, nm in ((1, 'p'), (-1, 'n')):
        a, b = sorted((sgn * x0, sgn * x1))
        e = csx.AddExcitation('e' + nm, exc_type=0, exc_val=[0, 0, sgn])
        e.AddBox([a, y0, 0], [b, y0, zs])
    yp = 14.0
    xc = 0.5 * (x0 + x1)
    v = csx.AddProbe('v', p_type=0)
    v.AddBox([xc, yp, 0], [xc, yp, zs])
    i = csx.AddProbe('i', p_type=1, norm_dir=1)
    i.AddBox([x0 - 0.06, yp, zs - 0.06], [x1 + 0.06, yp, zs + t + 0.06])
    fdtd.Run(path, verbose=0, cleanup=True)
    tv, vv = np.loadtxt(os.path.join(path, 'v'), comments='%', unpack=True, usecols=(0, 1))
    ti, ii = np.loadtxt(os.path.join(path, 'i'), comments='%', unpack=True, usecols=(0, 1))
    # the pulse passing the probe, before anything comes back from the far end
    eeff = er
    gate = (2 * (length - yp) + yp - y0) * 1e-3 * np.sqrt(eeff) / C0 + 1.0 / f0
    mv, mi = tv < gate, ti < gate
    Z = np.sqrt(np.trapezoid(vv[mv] ** 2, tv[mv]) / np.trapezoid(ii[mi] ** 2, ti[mi]))
    print(f"{name:22s} Zodd {Z:7.3f}  Zdiff {2 * Z:7.3f}   (gate {gate * 1e12:.0f} ps of {tv[-1] * 1e12:.0f} ps; "
          f"peak V {abs(vv).max():.3f} I {abs(ii).max():.5f})", flush=True)
    return 2 * Z


cases = {}
cases['micro sheet'] = run('micro_sheet', 0.297, 0.2, 0.2, 0.0, 3.2, 4.4, True)
cases['micro thick'] = run('micro_thick', 0.297, 0.2, 0.2, 0.035, 3.2, 4.4, True)
cases['strip sheet'] = run('strip_sheet', 0.157, 0.2, 0.2, 0.0, 0.4, 4.4, False)
cases['strip thick'] = run('strip_thick', 0.157, 0.2, 0.2, 0.035, 0.435, 4.4, False)
print(f"thick / sheet: microstrip {cases['micro thick'] / cases['micro sheet']:.4f}, stripline {cases['strip thick'] / cases['strip sheet']:.4f}")
print("the field solver (docs/transmission-lines.md): microstrip 91.6 / 100.0, stripline 84.2 / 99.9")
