#!/usr/bin/env python3
"""
The 2D field solver's mesh (kicad2ems.lines.xsection) measured: docs/transmission-lines.md#field-solver quotes this.

    python tools/xsection_study.py                       # against the references: exact, closed-form, converged
    python tools/xsection_study.py --board BOARD.kicad_pcb [--every N]     # every cross-section of a real board

Against each reference: the shipped mesh and the tensor grid (nodes, time). With --board: each cross-section the line
model asks for on the board, by default and with --lines-field, against the same one converged (corners 1/1024 of
their features, the grading extrapolated from 0.2 and 0.1, no coarser far corners, the box 96 heights out): the
errors and the solver's time. The CM5 Minima demo's board isn't in the repository (CERN-OHL-S, KiCad's demos):
    curl -O https://gitlab.com/kicad/code/kicad/-/raw/master/demos/cm5_minima/CM5_MINIMA_3.kicad_pcb
"""
import argparse, math, multiprocessing, os, sys, time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from kicad2ems.constants import C0, EPS0                                 # noqa: E402
from kicad2ems.lines import coupled as K, formulas as F, xsection as X   # noqa: E402


def cohn(w, b, er):
    """Exact, with eta0 / 4 (the usual 30 pi assumes c = 3e8: 0.07 % high)."""
    k = 1 / math.cosh(math.pi * w / (2 * b))
    return F.ellipk(k) / F.ellipk(math.sqrt(1 - k * k)) / (4 * C0 * EPS0 * math.sqrt(er))


def values(C, Ca):
    """What a line model takes from a cross-section: each line's Z0 and eeff, each neighbouring pair's modes."""
    n, out = len(C), []
    for i in range(n):
        out += [1 / (C0 * math.sqrt(C[i, i] * Ca[i, i])), C[i, i] / Ca[i, i]]
    for i in range(n - 1):
        for s in (1, -1):
            c = 0.5 * (C[i, i] + C[i + 1, i + 1]) + s * C[i, i + 1]
            ca = 0.5 * (Ca[i, i] + Ca[i + 1, i + 1]) + s * Ca[i, i + 1]
            out += [1 / (C0 * math.sqrt(c * ca)), c / ca]
    return np.array(out)


def converged(xs):
    """Values of xs converged: the corners' cells far below their error, the grading extrapolated (second order)."""
    far, reach = X.MESH_FAR, X.REACH
    X.MESH_FAR, X.REACH = 0.0, 96.0
    try:
        a = X._solve_mesh(xs, X._mesh(xs, 0.2, 1 / 1024))
        b = X._solve_mesh(xs, X._mesh(xs, 0.1, 1 / 1024))
    finally:
        X.MESH_FAR, X.REACH = far, reach
    return (4 * values(b[0], b[1]) - values(a[0], a[1])) / 3


def references():
    sw = lambda r: r.single()[0]
    pair = lambda i: (lambda r: r.pair()[i])
    cases = [
        ('stripline 0.15 / 0.4 mm', X.stripline(0.15, 0.2, 0.2, 4.3), sw, cohn(0.15, 0.4, 4.3), 'exact (Cohn)'),
        ('microstrip 0.35 / 0.2 mm', X.microstrip(0.35, 0.2, 4.3), sw, F.microstrip(0.35, 0.2, 4.3)[0], 'Hammerstad & Jensen'),
        ('microstrip 3.0 / 1.6 mm', X.microstrip(3.0, 1.6, 4.4), sw, F.microstrip(3.0, 1.6, 4.4)[0], 'Hammerstad & Jensen'),
        ('microstrip 0.4 / 0.21 mm, 35 um', X.microstrip(0.4, 0.2104, 4.4, 0.035), sw, F.microstrip(0.4, 0.2104, 4.4, 0.035)[0], 'Hammerstad & Jensen'),
        ('stripline, 35 um', X.stripline(0.15, 0.2, 0.2, 4.3, t=0.035), sw, F.stripline(0.15, 0.435, 4.3, 0.035)[0], 'Wheeler'),
        ('grounded CPW 0.5 / 0.5 / 1.6 mm', X.microstrip(0.5, 1.6, 4.4, s=0.5), sw, F.cpwg(0.5, 0.5, 1.6, 4.4)[0], 'Ghione & Naldi'),
        ('grounded CPW 0.3 / 0.15 / 0.2 mm', X.microstrip(0.3, 0.2, 4.4, s=0.15), sw, F.cpwg(0.3, 0.15, 0.2, 4.4)[0], 'Ghione & Naldi'),
        ('coupled microstrip 0.15 / 0.15 / 0.2: Ze', X.coupled_microstrip(0.15, 0.15, 0.2, 4.4), pair(0), K.coupled_microstrip(0.15, 0.15, 0.2, 4.4)[0], 'Kirschning & Jansen'),
        ('coupled microstrip: Zo', X.coupled_microstrip(0.15, 0.15, 0.2, 4.4), pair(1), K.coupled_microstrip(0.15, 0.15, 0.2, 4.4)[1], 'Kirschning & Jansen'),
        ('coupled stripline 0.1 / 0.1 / 0.4: Ze', X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3), pair(0), K.coupled_stripline(0.1, 0.1, 0.4, 4.3)[0], 'Cohn'),
        ('coupled stripline: Zo', X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3), pair(1), K.coupled_stripline(0.1, 0.1, 0.4, 4.3)[1], 'Cohn'),
    ]
    mask = X.coupled_microstrip(0.13, 0.25, 0.0994, 4.45, 0.035, mask=0.01, er_mask=3.8)
    five = X.microstrip([0.6] * 5, 0.8, 4.4, gap=[0.58, 0.74, 0.74, 0.58])
    cases += [('HDMI pair, 35 um, mask: Zo', mask, pair(1), lambda: converged(mask)[6], 'converged'),
              ('five coupled lines: line 0', five, sw, lambda: converged(five)[0], 'converged')]
    print(f"{'case':42s} {'reference':20s} {'value':>8s}   {'mesh':>8s} {'nodes':>6s} {'ms':>4s}   {'grid':>8s} {'nodes':>6s} {'ms':>4s}")
    for name, xs, f, ref, what in cases:
        ref = ref() if callable(ref) else ref
        t = time.perf_counter(); m = xs.solve(); tm = time.perf_counter() - t
        t = time.perf_counter(); g = X._solve_on_grid(xs); tg = time.perf_counter() - t
        print(f"{name:42s} {what:20s} {ref:8.3f}   {(f(m) / ref - 1) * 100:+7.2f}% {m.nodes:6d} {tm * 1e3:4.0f}   "
              f"{(f(g) / ref - 1) * 100:+7.2f}% {g.nodes:6d} {tg * 1e3:4.0f}")


def _judge(xs):
    t = time.perf_counter()
    r = xs.solve()
    tm = time.perf_counter() - t
    t = time.perf_counter()
    g = X._solve_on_grid(xs)
    tg = time.perf_counter() - t
    T = converged(xs)
    err = lambda q: float(np.abs(values(q.C, q.Ca) / T - 1).max() * 100)
    return err(r), err(g), tm, tg, r.nodes, g.nodes, len(xs.conductors)


def board(path, every):
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder, reference
    seen, solve = [], X.solve

    def spy(xs):
        seen.append(xs)
        return solve(xs)
    X.solve = spy
    X.CrossSection.solve = lambda self: spy(self)
    m = Board(KiCadBoard(path), log=lambda *a: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))
    for field in (False, True):
        reference.FIELD = field
        n0, t = len(seen), time.time()
        builder.build(m, nets=nets)
        print(f"{'--lines-field' if field else 'default':14s} {len(nets)} nets: {time.time() - t:6.1f} s, {len(seen) - n0} solves", flush=True)
    X.solve = solve
    X.CrossSection.solve = lambda self: solve(self)
    with multiprocessing.Pool(max(1, (os.cpu_count() or 2) - 2)) as p:
        out = np.array(p.map(_judge, seen[::every], chunksize=4))
    print(f"{len(out)} cross-sections, up to {int(out[:, 6].max())} conductors")
    for name, e, ts, n in (('mesh', out[:, 0], out[:, 2], out[:, 4]), ('tensor grid', out[:, 1], out[:, 3], out[:, 5])):
        print(f"{name:12s} error worst {e.max():.3f} %, p99 {np.percentile(e, 99):.3f} %, median {np.median(e):.3f} %; "
              f"time total {ts.sum():.1f} s, worst {ts.max() * 1e3:.0f} ms, median {np.median(ts) * 1e3:.0f} ms; nodes up to {int(n.max())}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--board', help='a .kicad_pcb: every cross-section its line model asks for')
    ap.add_argument('--every', type=int, default=1, help='with --board: only every Nth cross-section')
    a = ap.parse_args()
    board(a.board, a.every) if a.board else references()
