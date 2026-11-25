"""
A channel in time: its TDR, its step response and its eye for a bit pattern at
a data rate, from its S-parameters through a passive macromodel
(core.macromodel), driven and received by behavioural or IBIS buffers
(core.ibis).

    python -m kicad2ems.core.timedomain board.kicad_pcb --near P1 --far P2 \\
        --rate 5 --out out/td                       # the line model's S
    python -m kicad2ems.core.timedomain --sparams ch.s4p --near P1 --far P2 \\
        --near-n P3 --far-n P4 --rate 8 --driver ibis --ibis part.ibs \\
        --ibis-model DQ_34 --receiver ibis --ibis-rx IN_DQ --out out/td

The S-parameters: a Touchstone file (--sparams; a segment result, a measured
one), a run's folder (--from: its Touchstone, the solver named first), or the
board solved here (--solvers, the line model by default) on a grid down to
fmax / points. The channel is the near port (and, a pair, --near-n) to the far
one; every other port is terminated in its reference. A pair is driven by two
complementary drivers and received across the legs (a termination split: each
leg half of it to Vtt).

- TDR: a step of --rise behind each near port's reference (a pair: odd),
  the impedance it sees, Z = z0 V / (E - V) (a pair's 2 z0 Vd / (2 - Vd)),
  against the round trip's time.
- Step: one edge of the driver, the far end's delay (50 % to 50 % from the near
  end), its 10-90 % rise and overshoot.
- Eye: a PRBS (or --bits-file) at --rate after the first bit has settled the
  channel, folded at the far end's latency (the step's): the inner height at
  the best phase, the width at the threshold (the levels' middle), the levels;
  every other port's peak (time-domain crosstalk).

What it leaves out: jitter and noise (the eye is the pattern's alone, no BER
contour), equalization (CTLE, FFE, DFE), IBIS-AMI, a floating differential
termination (split per leg to Vtt), the driver's own supply noise.
"""
import argparse
import json
import math
import os
import re
import sys

import numpy as np

from . import macromodel as MM

__all__ = ['RESULT', 'FORMAT', 'prbs', 'edge', 'delay_of', 'tdr', 'step',
           'eye', 'run', 'main', 'TimeDomain']

RESULT, FORMAT, VERSION = 'timedomain.json', 'kicad2ems-timedomain', 1
# PRBS polynomials x^n + x^m + 1
PRBS = {7: 6, 9: 5, 11: 9, 15: 14, 23: 18, 31: 28}
# a raised-cosine edge's 10-90 % share of its length
RC_10_90 = 0.59


def prbs(n, count):
    """`count` bits of PRBS-n (n in PRBS: 7, 9, 11, 15, 23, 31)."""
    if n not in PRBS:
        raise ValueError(f"PRBS{n}: one of {', '.join(map(str, PRBS))}")
    m = PRBS[n]
    reg = (1 << n) - 1
    out = np.empty(count, int)
    for i in range(count):
        bit = ((reg >> (n - 1)) ^ (reg >> (m - 1))) & 1
        reg = ((reg << 1) | bit) & ((1 << n) - 1)
        out[i] = bit
    return out


def edge(rise):
    """A raised-cosine edge 0 -> 1 from t = 0, 10-90 % in `rise` (s)."""
    T = rise / RC_10_90
    return lambda t: 0.5 * (1 - np.cos(np.pi * np.clip(t / T, 0.0, 1.0)))


def _wave(bits, ui, t0, rise, lo, hi):
    """The source's e(t) of `bits` (first held before t0), raised-cosine
    edges of `rise`."""
    bits = np.asarray(bits, int)
    e = edge(rise)
    starts = t0 + ui * np.arange(1, len(bits))
    steps = np.diff(bits)
    keep = steps != 0
    starts, steps = starts[keep], steps[keep]

    def f(t):
        v = bits[0] + np.sum(steps * e(t - starts)) if len(starts) else bits[0]
        return lo + (hi - lo) * v
    return f


def delay_of(model, i, j, fmax=None):
    """The delay (s) from port j to port i from S_ij's phase slope over the
    lower half of the band (0 where |S_ij| is under 1e-3: no path)."""
    fmax = fmax or model.fmax
    f = np.linspace(fmax / 200, fmax / 2, 200)
    h = model.S(f)[:, i, j]
    if np.abs(h).max() < 1e-3:
        return 0.0
    ph = np.unwrap(np.angle(h))
    slope = np.polyfit(2 * np.pi * f, ph, 1)[0]
    return max(0.0, -float(slope))


def _cross(t, v, level, start=0):
    """The first time v crosses `level` at or after index start (linear
    between samples), or None."""
    v = np.asarray(v)
    s = np.sign(v[start:] - level)
    k = np.flatnonzero(s[1:] != s[:-1])
    if not len(k):
        return None
    k = int(k[0]) + start
    a, b = v[k] - level, v[k + 1] - level
    return float(t[k] + (t[k + 1] - t[k]) * (a / (a - b) if a != b else 0))


# ---- the three runs ---------------------------------------------------------
def _loads(model, keep):
    """Each port's termination: its reference's load, but `keep`'s."""
    return [keep.get(k) or MM.Load(float(model.z0[k]))
            for k in range(model.n)]


def tdr(model, near, near_n=None, rise=50e-12, dt=None, span=None):
    """(t (s, from the edge's half), Z (ohm)) at the near port (a pair:
    differential, odd drive): a 1 V step of `rise` behind its reference."""
    T0 = rise / RC_10_90
    span = span or max(4 * (_round_trip(model, near) or 1e-9), 1e-9)
    dt = dt or min(rise / 10, span / 2000)
    t = np.arange(0.0, span + 2 * T0, dt)
    e = edge(rise)
    z = model.z0
    terms = {near: MM.Thevenin(e, z[near])}
    if near_n is not None:
        terms[near_n] = MM.Thevenin(lambda x: -e(x), z[near_n])
    V, _ = MM.simulate(model, _loads(model, terms), t)
    if near_n is None:
        v = V[:, near]
        Z = z[near] * v / np.maximum(1.0 - v, 1e-6)
    else:
        v = V[:, near] - V[:, near_n]
        zd = z[near] + z[near_n]
        Z = zd * v / np.maximum(2.0 - v, 1e-6)
    return t - T0 / 2, Z


def _round_trip(model, k):
    """Twice the longest delay from port k to another (s): the TDR's
    window."""
    best = 0.0
    for j in range(model.n):
        if j != k:
            best = max(best, delay_of(model, j, k))
    return 2 * best


def step(model, near, far, drivers, receivers, t, near_n=None, far_n=None):
    """Every port's voltage for drivers {port: termination} (an edge each)
    and receivers {port: termination}; (V, the near end's and the far end's
    voltage (a pair's difference))."""
    terms = dict(receivers)
    terms.update(drivers)
    V, _ = MM.simulate(model, _loads(model, terms), t)
    vn = V[:, near] - (V[:, near_n] if near_n is not None else 0.0)
    vf = V[:, far] - (V[:, far_n] if far_n is not None else 0.0)
    return V, vn, vf


def step_figures(t, vn, vf, t_edge):
    """delay (s, the near end's half to the far end's), rise (s, 10-90 %),
    overshoot (%), the levels (before, settled)."""
    i0 = int(np.searchsorted(t, t_edge))
    lo_n, hi_n = float(vn[max(i0 - 1, 0)]), float(vn[-1])
    lo, hi = float(vf[max(i0 - 1, 0)]), float(vf[-1])
    out = dict(before=lo, settled=hi)
    if abs(hi - lo) < 1e-6 or abs(hi_n - lo_n) < 1e-6:
        out.update(delay=None, rise=None, overshoot=None)
        return out
    tn = _cross(t, vn, 0.5 * (lo_n + hi_n), i0)
    tf = _cross(t, vf, 0.5 * (lo + hi), i0)
    t10 = _cross(t, vf, lo + 0.1 * (hi - lo), i0)
    t90 = _cross(t, vf, lo + 0.9 * (hi - lo), i0)
    sgn = 1 if hi > lo else -1
    peak = float((sgn * (vf[i0:] - hi)).max())
    out.update(delay=None if tn is None or tf is None else tf - tn,
               rise=None if t10 is None or t90 is None else t90 - t10,
               overshoot=100 * max(peak, 0.0) / abs(hi - lo),
               t_near=tn, t_far=tf)
    return out


def eye(t, v, bits, ui, t0, latency, bins=None):
    """The eye of v (the far end) for `bits` from t0 (bit k sent over [t0 + k
    ui, ...)), arriving `latency` later: dict(height (V, the inner opening at
    the best phase; <= 0 closed), phase (s, from the bit's start), width (s,
    at the threshold), threshold, levels (the 0s' and the 1s' means at the
    best phase), the folded traces (phase, v) for a figure). bins: phases a UI
    (default: the samples a UI, at most 128: a bin with no sample had ended
    the width there)."""
    bits = np.asarray(bits, int)
    if bins is None:
        bins = int(np.clip(round(ui / float(t[1] - t[0])), 8, 128))
    x = (t - t0 - latency) / ui
    k = np.floor(x).astype(int)
    ok = (k >= 0) & (k < len(bits))
    ph = x[ok] - k[ok]
    vv, b = v[ok], bits[k[ok]]
    if not (b == 1).any() or not (b == 0).any():
        raise ValueError("the pattern's received part has one level only")
    lo, hi = float(vv[b == 0].mean()), float(vv[b == 1].mean())
    th = 0.5 * (lo + hi)
    sign = 1.0 if hi >= lo else -1.0
    j = np.minimum((ph * bins).astype(int), bins - 1)
    inner = np.full(bins, -np.inf)
    at_th = np.zeros(bins, bool)
    for q in range(bins):
        on, off = sign * vv[(j == q) & (b == 1)], sign * vv[(j == q) & (b == 0)]
        if len(on) and len(off):
            inner[q] = on.min() - off.max()
            at_th[q] = on.min() > sign * th and off.max() < sign * th
    best = int(np.argmax(inner))
    at = j == best
    lo, hi = float(vv[at & (b == 0)].mean()), float(vv[at & (b == 1)].mean())
    # the width: the threshold's open bins round the best, circularly
    width = 0
    if at_th[best]:
        width = 1
        for d in (1, -1):
            q = best + d
            while at_th[q % bins] and width < bins:
                width += 1
                q += d
    return dict(height=float(inner[best]), phase=(best + 0.5) / bins * ui,
                width=width / bins * ui, threshold=th, levels=[lo, hi],
                folded=(ph, vv))


# ---- the run ----------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(prog='python -m kicad2ems.core.timedomain',
                                description=__doc__.split('\n\n')[0].strip())
    p.add_argument('board', nargs='?', default=None,
                   help=".kicad_pcb or a config (.toml) to solve, when "
                   "--sparams / --from do not give the S-parameters")
    p.add_argument('--sparams', default=None, help="a Touchstone file")
    p.add_argument('--from', dest='run_from', default=None,
                   help="a kicad2ems run's folder: its Touchstone")
    p.add_argument('--solvers', default='lines',
                   help="the solver of the board's S when solved here (lines, "
                   "mom25d, gfdtd, openems)")
    p.add_argument('--fmax', type=float, default=20.0,
                   help="GHz, the band solved here (default 20)")
    p.add_argument('--points', type=int, default=1001,
                   help="frequencies solved here, from fmax / points")
    p.add_argument('--near', required=True, help="the driven port (P1)")
    p.add_argument('--far', required=True, help="the received port (P2)")
    p.add_argument('--near-n', default=None,
                   help="a pair's other driven port")
    p.add_argument('--far-n', default=None,
                   help="a pair's other received port")
    p.add_argument('--rate', type=float, default=2.5, help="Gb/s")
    p.add_argument('--pattern', default='prbs7',
                   help="prbs7, prbs9, prbs11, prbs15, prbs23, prbs31")
    p.add_argument('--bits', type=int, default=None,
                   help="the pattern's length (default: twice PRBS-n's period,"
                   " at most 4000)")
    p.add_argument('--rise', type=float, default=None,
                   help="ps, the behavioural driver's and the TDR's edge, 10-90"
                   " %% (default: 1 / fmax, and at least a fifth of a UI)")
    p.add_argument('--driver', default='ideal', choices=('ideal', 'ibis'),
                   help="ideal: a source of --swing behind --rs; ibis: "
                   "--ibis's --ibis-model")
    p.add_argument('--swing', type=float, default=1.0,
                   help="V, the ideal driver's open-circuit swing (0 to it)")
    p.add_argument('--rs', type=float, default=None,
                   help="ohm, the ideal driver's resistance (default: the "
                   "port's reference)")
    p.add_argument('--ibis', default=None, help="an IBIS file")
    p.add_argument('--ibis-model', default=None,
                   help="the driver's [Model] (or --ibis-pin)")
    p.add_argument('--ibis-pin', default=None, help="the driver's pin")
    p.add_argument('--corner', default='typ', choices=('typ', 'min', 'max'))
    p.add_argument('--receiver', default='z0',
                   choices=('z0', 'rc', 'open', 'ibis'),
                   help="z0: each port's reference; rc: --rt to --vtt beside "
                   "--ct; open: --ct alone; ibis: --ibis-rx")
    p.add_argument('--rt', type=float, default=None,
                   help="ohm, the termination (a pair's across both legs)")
    p.add_argument('--ct', type=float, default=0.0, help="pF at the receiver")
    p.add_argument('--vtt', type=float, default=0.0, help="V, rc's")
    p.add_argument('--ibis-rx', default=None,
                   help="the receiver's [Model] (in --ibis, or --ibis-rx-file)")
    p.add_argument('--ibis-rx-file', default=None)
    p.add_argument('--samples', type=int, default=32,
                   help="time steps a UI (at least)")
    p.add_argument('--out', default=None, help="output folder")
    return p


def sparams_of(a, out, echo=print):
    """(f, S, z0, names, where): the S-parameters as --sparams, --from or a
    solve of the board give them."""
    from .touchstone import port_names, read_touchstone
    path = a.sparams
    if not path and a.run_from:
        path = _run_touchstone(a.run_from, a.solvers if a.solvers != 'lines'
                               else None)
    if not path:
        if not a.board:
            raise ValueError("give --sparams, --from, or the board to solve")
        path = _solve(a, out, echo)
    f, S, z0 = read_touchstone(path, with_zref=True)
    names = port_names(path) or [str(k + 1) for k in range(S.shape[1])]
    return f, S, np.asarray(z0, float), names, path


def _run_touchstone(folder, solver=None):
    """A run folder's Touchstone (solver's, else the first it records)."""
    import glob
    rec = os.path.join(folder, next((x for x in os.listdir(folder)
                                     if x.endswith('_run.json')), ''))
    solved = {}
    if os.path.isfile(rec):
        with open(rec, encoding='utf-8') as fh:
            solved = json.load(fh).get('solved') or {}
    names = [solver] if solver and solver in solved else list(solved)
    for n in names:
        ts = (solved.get(n) or {}).get('touchstone')
        if ts and os.path.isfile(os.path.join(folder, ts)):
            return os.path.join(folder, ts)
    got = sorted(glob.glob(os.path.join(folder, '*.s*p')))
    if not got:
        raise ValueError(f"{folder}: no Touchstone file")
    return got[0]


def _solve(a, out, echo):
    """The board's S on --solvers, from fmax / points to fmax."""
    from .config import build_settings
    from .pipeline import Job
    folder = os.path.join(out, 'sparams')
    fmax = float(a.fmax)
    cli = dict(solvers=a.solvers, out=folder, no_report=True, lean=True,
               fmin=fmax / a.points, fmax=fmax, points=a.points)
    if a.board.lower().endswith('.toml'):
        s = build_settings(cli, config=a.board)
    else:
        s = build_settings(dict(cli, board=a.board))
    echo(f"timedomain: the board's S on {a.solvers}, "
         f"{cli['fmin']:.3g}-{fmax:g} GHz, {a.points} points")
    Job(s, echo=lambda *x: echo(' '.join(str(y) for y in x))).run()
    return _run_touchstone(folder, a.solvers)


def _port(names, p, what):
    if p is None:
        return None
    if p in names:
        return names.index(p)
    if p.isdigit() and 1 <= int(p) <= len(names):
        return int(p) - 1
    raise ValueError(f"--{what} {p}: not a port ({', '.join(names)})")


def _buffers(a, model, near, near_n, far, far_n, bits, ui, t0, rise, lo_hi):
    """({port: driver}, {port: receiver}, record)."""
    from . import ibis as IB
    z = model.z0
    drv, rx, rec = {}, {}, {}
    pair = near_n is not None
    if a.driver == 'ibis':
        if not a.ibis:
            raise ValueError("--driver ibis: give --ibis")
        lib = IB.read(a.ibis)
        b = lib.model(a.ibis_model, a.corner, pin=a.ibis_pin)
        for port, bb in ((near, bits), (near_n, 1 - np.asarray(bits))):
            if port is not None:
                drv[port] = IB.Driver(b, bb, ui, a.corner, t0)
        d0 = drv[near]
        rec['driver'] = dict(kind='ibis', file=os.path.basename(a.ibis),
                             model=b.name, corner=a.corner, vcc=b.vcc(a.corner),
                             package=list(b.package) if b.package else None,
                             edge_table_ps=round(d0.settle_time() * 1e12, 1))
    else:
        lo, hi = lo_hi
        for port, inv in ((near, False), (near_n, True)):
            if port is not None:
                rs = a.rs or float(z[port])
                w = _wave(bits if not inv else 1 - np.asarray(bits), ui, t0,
                          rise, lo, hi)
                drv[port] = MM.Thevenin(w, rs)
        rec['driver'] = dict(kind='ideal', swing=a.swing,
                             rs=a.rs or float(z[near]), rise_ps=rise * 1e12)
    ct = (a.ct or 0.0) * 1e-12
    for port in (far, far_n):
        if port is None:
            continue
        if a.receiver == 'ibis':
            lib = IB.read(a.ibis_rx_file or a.ibis)
            rx[port] = IB.Receiver(lib.model(a.ibis_rx, a.corner))
        elif a.receiver == 'rc':
            rt = a.rt or (float(z[far]) + (float(z[far_n]) if pair else 0.0))
            rx[port] = MM.Load(rt / 2 if pair else rt, ct, a.vtt)
        elif a.receiver == 'open':
            rx[port] = MM.Load(None, ct)
    rec['receiver'] = dict(kind=a.receiver, rt=a.rt, ct_pf=a.ct, vtt=a.vtt,
                           ibis_model=a.ibis_rx)
    return drv, rx, rec


def run(a, echo=print):
    """The question for parsed arguments `a`; returns timedomain.json's
    dict."""
    out = os.path.abspath(a.out or os.path.join(
        os.path.dirname(os.path.abspath(a.board or a.sparams or '.')), 'out',
        'timedomain'))
    os.makedirs(out, exist_ok=True)
    f, S, z0, names, path = sparams_of(a, out, echo)
    near, far = _port(names, a.near, 'near'), _port(names, a.far, 'far')
    near_n, far_n = (_port(names, a.near_n, 'near-n'),
                     _port(names, a.far_n, 'far-n'))
    if (near_n is None) != (far_n is None):
        raise ValueError("a pair: give both --near-n and --far-n")
    warnings = []
    echo(f"timedomain: {os.path.basename(path)}: {S.shape[1]} ports, "
         f"{f[0] / 1e9:.3g}-{f[-1] / 1e9:.3g} GHz, {len(f)} points")
    model = MM.fit(f, S, z0=z0, log=echo)
    rec_m = model.record()
    if not rec_m.get('dc'):
        warnings.append(
            f"the band starts at {f[0] / 1e9:.3g} GHz: the response's DC "
            "and its slow tail are the model's guess (start the band lower)")
    fmax = f[-1]
    ui = 1e-9 / a.rate
    rise = (a.rise * 1e-12 if a.rise else max(1.0 / fmax, ui / 5))
    if rise < 0.7 / fmax:
        warnings.append(
            f"the edge ({rise * 1e12:.0f} ps) is faster than the band holds "
            f"(0.7 / fmax: {0.7 / fmax * 1e12:.0f} ps): its top is the "
            "model's past its samples")
    tau = delay_of(model, far, near)
    if tau == 0.0:
        warnings.append(f"no path from {a.near} to {a.far} (|S| under 1e-3)")
    dt = min(rise / 8, ui / a.samples, 1 / (8 * fmax))
    echo(f"timedomain: {a.near} -> {a.far}"
         + (f" (pair with {a.near_n} -> {a.far_n})" if near_n is not None
            else '')
         + f": delay {tau * 1e12:.0f} ps, edge {rise * 1e12:.0f} ps, "
         f"{a.rate:g} Gb/s, step {dt * 1e12:.2f} ps")
    res = dict(format=FORMAT, version=VERSION, board=a.board,
               sparams=os.path.abspath(path), ports=names, z0=list(z0),
               channel=dict(near=a.near, far=a.far, near_n=a.near_n,
                            far_n=a.far_n, pair=near_n is not None),
               macromodel=rec_m, delay_ps=tau * 1e12, rate_gbps=a.rate,
               ui_ps=ui * 1e12, rise_ps=rise * 1e12, warnings=warnings)

    # the TDR
    tt, Z = tdr(model, near, near_n, rise=rise, dt=min(dt, rise / 10),
                span=max(2.5 * 2 * tau, 1e-9) + 10 * rise)
    win = (tt > rise) & (tt < 2 * tau + 2 * rise)
    if not win.any():
        win = tt > rise
    res['tdr'] = dict(
        t_ns=_thin(tt * 1e9), z=_thin(Z), z_min=float(Z[win].min()),
        z_max=float(Z[win].max()), t_min_ns=float(tt[win][np.argmin(Z[win])]
                                                  * 1e9),
        t_max_ns=float(tt[win][np.argmax(Z[win])] * 1e9),
        z_end=float(Z[-1]), round_trip_ns=2 * tau * 1e9)
    echo(f"timedomain: TDR {res['tdr']['z_min']:.1f} to "
         f"{res['tdr']['z_max']:.1f} ohm over the channel")

    # the step
    settle = max(6 * tau, 2e-9) + 20 * rise
    lo_hi = (0.0, a.swing)
    t_edge = settle
    ts = np.arange(0.0, settle + 4 * tau + 30 * rise + 2e-9, dt)
    # (one edge at t_edge: the second of two bits a long UI each)
    long = max(ts[-1], 1e-9)
    drv, rx, rec = _buffers(a, model, near, near_n, far, far_n, [0, 1], long,
                            t_edge - long, rise, lo_hi)
    res.update(rec)
    if 'edge_table_ps' in rec['driver'] and rec['driver'][
            'edge_table_ps'] > ui * 1e12:
        warnings.append(
            f"the IBIS edge's table ({rec['driver']['edge_table_ps']:.0f} ps) "
            f"is longer than a UI ({ui * 1e12:.0f} ps): each edge starts where "
            "the last one's Ku is")
    V, vn, vf = step(model, near, far, drv, rx, ts, near_n, far_n)
    sf = step_figures(ts, vn, vf, t_edge)
    lat = ((sf.get('t_far') or (t_edge + tau)) - t_edge)
    res['step'] = dict(
        t_ns=_thin((ts - t_edge) * 1e9), near=_thin(vn), far=_thin(vf),
        delay_ps=None if sf['delay'] is None else sf['delay'] * 1e12,
        rise_ps=None if sf['rise'] is None else sf['rise'] * 1e12,
        overshoot_pct=sf['overshoot'], low=sf['before'], high=sf['settled'],
        latency_ps=lat * 1e12)
    echo("timedomain: step: delay "
         + ('-' if sf['delay'] is None else f"{sf['delay'] * 1e12:.0f} ps")
         + ", rise "
         + ('-' if sf['rise'] is None else f"{sf['rise'] * 1e12:.0f} ps")
         + f", settled {sf['settled']:.3f} V")

    # the eye
    n = int(a.pattern.lower().replace('prbs', '') or 7)
    count = a.bits or min(2 * ((1 << n) - 1), 4000)
    bits = prbs(n, count)
    t0 = settle
    te = np.arange(0.0, t0 + count * ui + lat + 4 * ui, dt)
    drv, rx, _ = _buffers(a, model, near, near_n, far, far_n, bits, ui, t0,
                          rise, lo_hi)
    echo(f"timedomain: eye: {count} bits of PRBS{n}, {len(te):,} steps")
    V, vn, vf = step(model, near, far, drv, rx, te, near_n, far_n)
    skip = int(math.ceil((tau + 4 * rise) / ui)) + 4
    keep = (te >= t0 + lat + skip * ui)
    try:
        E = eye(te[keep], vf[keep], bits, ui, t0, lat)
    except ValueError as e:
        raise ValueError(f"eye: {e} (more --bits)") from None
    swing = abs(E['levels'][1] - E['levels'][0])
    res['eye'] = dict(
        pattern=f"PRBS{n}", bits=count, skipped=skip,
        height_v=E['height'], height_mv=E['height'] * 1e3,
        height_pct=100 * E['height'] / swing if swing else 0.0,
        width_ps=E['width'] * 1e12, width_ui=E['width'] / ui,
        phase_ps=E['phase'] * 1e12, threshold_v=E['threshold'],
        levels=E['levels'], open=E['height'] > 0)
    others = [k for k in range(model.n) if k not in (near, far, near_n, far_n)]
    sw_near = abs(np.percentile(vn[keep], 99) - np.percentile(vn[keep], 1))
    res['crosstalk'] = sorted([
        dict(port=names[k], peak_mv=float(np.abs(V[keep, k]).max() * 1e3),
             pct=float(100 * np.abs(V[keep, k]).max() / sw_near)
             if sw_near else 0.0) for k in others],
        key=lambda r: -r['pct'])
    echo(f"timedomain: eye {E['height'] * 1e3:.1f} mV "
         f"({res['eye']['height_pct']:.0f} % of {swing * 1e3:.0f} mV) by "
         f"{E['width'] * 1e12:.0f} ps ({E['width'] / ui:.2f} UI)")
    name = os.path.splitext(os.path.basename(a.board or path))[0]
    res['name'] = name
    try:
        res['images'] = _plots(out, name, res, (te[keep], vf[keep], t0, lat),
                               ui)
    except Exception as e:  # a figure never fails the run
        warnings.append(f"figures: {type(e).__name__}: {e}")
        res['images'] = []
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    echo(f"timedomain: -> {os.path.join(out, RESULT)}")
    return res


def _thin(x, n=2000):
    """At most n of x's samples, for the result file."""
    x = np.asarray(x, float)
    k = max(1, len(x) // n)
    return [round(float(v), 6) for v in x[::k]]


def _plots(out, name, res, trace, ui):
    """The TDR, the step and the eye, as one PNG; its path. trace: (t, v,
    t0, latency) of the eye's run (its kept part)."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(11, 3.8))
    FigureCanvasAgg(fig)
    ax1, ax2, ax3 = fig.subplots(1, 3)
    td = res['tdr']
    z0 = res['z0'][0] * (2 if res['channel']['pair'] else 1)
    ax1.plot(td['t_ns'], td['z'], lw=1.2)
    ax1.axhline(z0, color='0.6', lw=0.8, ls='--')
    span = max(abs(td['z_max'] - z0), abs(td['z_min'] - z0), 0.05 * z0)
    ax1.set(xlabel='round trip (ns)', ylabel='Z (ohm)', title='TDR',
            ylim=(z0 - 1.6 * span, z0 + 1.6 * span),
            xlim=(0, max(1.5 * td['round_trip_ns'], 0.2)))
    ax1.grid(alpha=0.3)
    st = res['step']
    ax2.plot(st['t_ns'], st['near'], lw=1.0, label='near end')
    ax2.plot(st['t_ns'], st['far'], lw=1.2, label='far end')
    d = (st['delay_ps'] or 0) / 1e3
    ax2.set(xlabel='t from the edge (ns)', ylabel='V', title='Step',
            xlim=(-0.1 - 0.2 * d, 1.5 * d + 0.5 + 10 * res['rise_ps'] / 1e3))
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    t, v, t0, lat = trace
    x = (t - t0 - lat) / ui
    k = np.floor(x).astype(int)
    for kk in range(int(k.min()), int(k.max()) - 1):
        sel = (x >= kk) & (x <= kk + 2)
        ax3.plot((x[sel] - kk) * ui * 1e12, v[sel], color='C0', lw=0.6,
                 alpha=0.25)
    ey = res['eye']
    ax3.axhline(ey['threshold_v'], color='0.5', lw=0.7, ls=':')
    ax3.set(xlabel='ps (2 UI)', ylabel='V', xlim=(0, 2 * ui * 1e12),
            title=f"Eye {ey['height_mv']:.0f} mV x {ey['width_ps']:.0f} ps")
    ax3.grid(alpha=0.3)
    fig.suptitle(f"{name}: {res['channel']['near']} -> "
                 f"{res['channel']['far']}, {res['rate_gbps']:g} Gb/s")
    fig.tight_layout()
    p = os.path.join(out, f"{name}_timedomain.png")
    fig.savefig(p, dpi=110)
    return [os.path.basename(p)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.core.timedomain: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


# ---- the question, as the study has it (kicad2ems.core.analyses) ------------
from .analyses import Analysis                                      # noqa: E402


def _result(folder):
    try:
        with open(os.path.join(folder or '', RESULT), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if d.get('format') == FORMAT else None
    except (OSError, ValueError):
        return None


class TimeDomain(Analysis):
    """A channel in time: TDR, step and eye (this module)."""
    name, label, tool = ('timedomain', 'Time domain',
                         'kicad2ems.core.timedomain')
    about = ("a channel's TDR, step response and eye for a bit pattern, from "
             "its S-parameters' passive macromodel, ideal or IBIS buffers")
    order = 17
    result = RESULT
    provides = ('timedomain',)
    engine = 'line model'
    paths = ('sparams', 'ibis', 'ibis_rx_file')
    # who gives the S-parameters when the study has none: the line model
    # (seconds), or mom25d
    variants = (('', 'line model', dict(solvers='lines')),
                ('mom25d', 'mom25d', dict(solvers='mom25d')))

    def check(self):
        return self.check_with(dict(solvers='lines'))

    def check_with(self, sets):
        from .solvers import LINES, get, resolve_lines
        name = sets.get('solvers') or 'lines'
        try:
            return get(resolve_lines() if name in (LINES, 'line', 'fast')
                       else name).check()
        except ValueError as e:
            return False, str(e)

    def options(self):
        from .options import Option
        o = lambda key, kind, default, help, group='Channel', **kw: Option(
            key, default, help, dict(float=float, int=int).get(kind, str),
            group=group, **kw)
        return [
            o('near', 'str', 'P1', "the driven port (P1)", level='basic'),
            o('far', 'str', 'P2', "the received port (P2)", level='basic'),
            o('near_n', 'str', '', "a pair's other driven port (empty: "
              "single-ended)"),
            o('far_n', 'str', '', "a pair's other received port"),
            o('sparams', 'str', '',
              "a Touchstone file of the channel (empty: the study's "
              "S-parameters, else the board solved)", path=True),
            o('rate', 'float', 2.5, "data rate, Gb/s", 'Pattern',
              level='basic'),
            o('pattern', 'str', 'prbs7', "the bits: prbs7, prbs9, prbs11, "
              "prbs15, prbs23, prbs31", 'Pattern',
              choices=('prbs7', 'prbs9', 'prbs11', 'prbs15', 'prbs23',
                       'prbs31')),
            o('bits', 'int', '', "the pattern's length (empty: twice its "
              "period, at most 4000)", 'Pattern'),
            o('rise', 'float', '', "the ideal driver's and the TDR's edge, ps "
              "10-90 % (empty: 1 / fmax, at least a fifth of a UI)",
              'Pattern'),
            o('driver', 'str', 'ideal', "ideal (a source behind its "
              "resistance) or ibis", 'Driver', choices=('ideal', 'ibis'),
              level='basic'),
            o('swing', 'float', 1.0, "the ideal driver's swing, V", 'Driver'),
            o('rs', 'float', '', "the ideal driver's resistance, ohm (empty: "
              "the port's reference)", 'Driver'),
            o('ibis', 'str', '', "the IBIS file", 'Driver', path=True),
            o('ibis_model', 'str', '', "the driver's IBIS [Model]", 'Driver'),
            o('corner', 'str', 'typ', "the IBIS corner", 'Driver',
              choices=('typ', 'min', 'max')),
            o('receiver', 'str', 'z0', "z0 (its reference), rc, open or ibis",
              'Receiver', choices=('z0', 'rc', 'open', 'ibis'),
              level='basic'),
            o('rt', 'float', '', "rc's termination, ohm (a pair's across)",
              'Receiver'),
            o('ct', 'float', 0.0, "the receiver's capacitance, pF",
              'Receiver'),
            o('vtt', 'float', 0.0, "rc's termination voltage, V", 'Receiver'),
            o('ibis_rx', 'str', '', "the receiver's IBIS [Model]",
              'Receiver'),
            o('ibis_rx_file', 'str', '', "its IBIS file (empty: the driver's)",
              'Receiver', path=True),
            o('fmax', 'float', 20.0, "the band solved when the board is, GHz",
              'Solve'),
            o('points', 'int', 1001, "frequencies solved, from fmax / points",
              'Solve'),
        ]

    def tag(self, values):
        return re.sub(r'[^A-Za-z0-9]+', '', f"{values.get('near') or 'P1'}"
                      f"{values.get('far') or 'P2'}") or 'run'

    def command(self, values, board, out, preview=False, results=None):
        near, far = values.get('near') or '', values.get('far') or ''
        if not (near and far):
            raise ValueError("name the channel's ports: near and far")
        cmd = [sys.executable, '-m', 'kicad2ems.core.timedomain']
        src = values.get('sparams')
        rf = (results or {}).get('sparams')
        if src:
            cmd += ['--sparams', str(src)]
        elif rf:
            cmd += ['--from', str(rf)]
        elif board:
            cmd.append(str(board))
        else:
            raise ValueError("choose the board, or a Touchstone file")
        cmd += ['--near', near, '--far', far, '--out', str(out)]
        for key in ('near_n', 'far_n', 'rate', 'pattern', 'bits', 'rise',
                    'driver', 'swing', 'rs', 'ibis', 'ibis_model', 'corner',
                    'receiver', 'rt', 'ct', 'vtt', 'ibis_rx', 'ibis_rx_file',
                    'fmax', 'points'):
            v = values.get(key)
            if v not in (None, '', []):
                cmd += ['--' + key.replace('_', '-'), str(v)]
        cmd += ['--solvers', str(values.get('solvers') or 'lines')]
        return cmd

    def status(self, line):
        line = line.strip()
        return line[:160] if line.startswith('timedomain:') else None

    def report(self, folder):
        d = _result(folder)
        if d is None:
            return None
        ch, ey, st, td = d['channel'], d['eye'], d['step'], d['tdr']
        head = (f"{ch['near']} -> {ch['far']}"
                + (f" (a pair with {ch['near_n']} -> {ch['far_n']})"
                   if ch['pair'] else '')
                + f" at {d['rate_gbps']:g} Gb/s: eye "
                f"{ey['height_mv']:.1f} mV ({ey['height_pct']:.0f} % of the "
                f"levels) by {ey['width_ps']:.0f} ps ({ey['width_ui']:.2f} UI)"
                + ('' if ey['open'] else ' (closed)')
                + f"; TDR {td['z_min']:.1f}-{td['z_max']:.1f} ohm; delay "
                + ('-' if st['delay_ps'] is None else
                   f"{st['delay_ps']:.0f} ps"))
        mm = d['macromodel']
        sec = [
            dict(title='The channel and its model', table=dict(
                columns=['', ''],
                rows=[['S-parameters', os.path.basename(d['sparams'])],
                      ['ports', ', '.join(d['ports'])],
                      ['macromodel', f"{mm['poles']} poles, RMS error "
                       f"{mm['rms_error']:.1e}, "
                       + ('passive' if mm['passive_as_fitted']
                          else 'made passive')
                       + (', DC from the lowest samples' if mm['dc']
                          else ', no DC sample')],
                      ['driver', ', '.join(f"{k} {v}" for k, v in
                                           d['driver'].items()
                                           if v not in (None, ''))],
                      ['receiver', ', '.join(f"{k} {v}" for k, v in
                                             d['receiver'].items()
                                             if v not in (None, ''))]])),
            dict(title='Eye', table=dict(
                columns=['pattern', 'height (mV)', 'height (%)',
                         'width (ps)', 'width (UI)', 'levels (V)'],
                rows=[[f"{ey['pattern']}, {ey['bits']} bits",
                       ey['height_mv'], ey['height_pct'], ey['width_ps'],
                       ey['width_ui'],
                       f"{ey['levels'][0]:.3f} / {ey['levels'][1]:.3f}"]],
                formats=[None, '.1f', '.0f', '.0f', '.2f', None])),
            dict(title='Step and TDR', table=dict(
                columns=['delay (ps)', 'rise (ps)', 'overshoot (%)',
                         'TDR min (ohm)', 'TDR max (ohm)'],
                rows=[[st['delay_ps'], st['rise_ps'], st['overshoot_pct'],
                       td['z_min'], td['z_max']]],
                formats=['.0f', '.0f', '.1f', '.1f', '.1f']))]
        if d.get('crosstalk'):
            sec.append(dict(
                title='The other ports during the pattern',
                text="Each other port's peak while the channel carries the "
                "pattern, against the near end's swing.",
                table=dict(columns=['port', 'peak (mV)', '% of the swing'],
                           rows=[[r['port'], r['peak_mv'], r['pct']]
                                 for r in d['crosstalk'][:12]],
                           formats=[None, '.2f', '.2f'])))
        return dict(title=f"Time domain: {ch['near']} -> {ch['far']}",
                    summary=head, sections=sec,
                    notes=[str(x) for x in d.get('warnings', [])],
                    images=[os.path.join(folder, p)
                            for p in d.get('images', [])
                            if os.path.isfile(os.path.join(folder, p))],
                    footer="kicad2ems.core.timedomain")

    def figures(self, folder):
        d = _result(folder)
        if d is None:
            return []
        ey, st, td = d['eye'], d['step'], d['tdr']
        out = [dict(name='eye height', value=ey['height_mv'], unit='mV'),
               dict(name='eye width', value=ey['width_ps'], unit='ps'),
               dict(name='TDR min', value=td['z_min'], unit='ohm'),
               dict(name='TDR max', value=td['z_max'], unit='ohm')]
        if st['delay_ps'] is not None:
            out.append(dict(name='delay', value=st['delay_ps'], unit='ps'))
        if st['rise_ps'] is not None:
            out.append(dict(name='rise', value=st['rise_ps'], unit='ps'))
        return out
