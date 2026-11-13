"""
kicad2ems.si - crosstalk between traces: the near end (NEXT) and the far end (FEXT), and what in the layout makes it

    python -m kicad2ems.si board.kicad_pcb --nets D0,D1,D2 --out out/xt
        [--aggressor D1] [--near U1] [--mode both|next|fext] [--rise 100] [--limit 3]
        [--solvers lines[,mom25d]] [--ground B.Cu] [--fmax GHz] [--z0 50]

The traces are named by their nets. Each gets a port at its two ends (the ends of its routed copper: a pad where
one is there, else the track's end), the near end the one at the part `--near` names (else the ends nearest the
first trace's first end), and the S-parameters between all of them are solved: by the line model (its coupled
sections from the 2D field solver; milliseconds), and by a full-wave solver beside it when one is named.

With trace a driven at its near end (the aggressor), a victim v's
    NEXT    is S(v near, a near): what comes back at the victim's near end
    FEXT    is S(v far, a near):  what arrives at its far end
over frequency (dB), and in time for an edge of `--rise` ps (10 to 90 %): the largest voltage at each end as a
share of the aggressor's swing, from the S-parameters (a trapezoid's Fourier series through them). Each trace is
the aggressor in turn unless `--aggressor` names them; a victim with several aggressors also gets their sum
(the worst case in time, the power sum in frequency).

The layout's part (crosstalk.json's `sections`): every stretch where an aggressor and a victim run side by side,
with its length, gap and layer, and from its cross-section's capacitance and inductance matrices
    Kc = Cm / C, Kl = Lm / L            the capacitive and the inductive coupling
    NEXT = (Kc + Kl) / 4                saturated: an edge shorter than twice the stretch's delay; below, in
                                        proportion (2 Td / rise)
    FEXT = (Kc - Kl) / 2 x Td / rise    grows with the length; zero where Kc = Kl (stripline: one dielectric)
and what a wider gap would give (the same section solved with the victim moved away). The estimates explain; the
figures reported are the solved network's.

Results: <out>/crosstalk.json (this module's), the run's own files beside it (<name>_<solver>.sNp, <name>_lines.json).
"""
import argparse
import json
import math
import os
import sys

import numpy as np

RESULT = 'crosstalk.json'
INSET = 0.3                 # mm: a port at a track's free end sits this far in from it
GAPS = (1.5, 2.0, 3.0)      # the wider gaps a section is solved again with, as multiples of its own
C0 = 299792458.0


# ---- the traces' ends ---------------------------------------------------------------------------------------
def trace_ends(kb, net, tol=1e-3):
    """The two ends of net's routed copper on board kb (importers.kicad.KiCadBoard), [dict(x, y, layer, pad, at)]:
    the pair of free track ends furthest apart along the tracks (a net of several separate pieces: its longest).
    pad: (ref, name) of the pad the end is on, or None; at: a point INSET mm in from a free end. KiCad mm."""
    import heapq
    tracks = [t for t in kb.tracks if t['net'] == net and len(t['pts']) >= 2]
    if not tracks:
        raise ValueError(f"net {net!r} has no tracks on this board")
    key = lambda p, layer: (round(float(p[0]) / tol), round(float(p[1]) / tol), layer)
    adj, pos = {}, {}

    def edge(a, b, w):
        adj.setdefault(a, []).append((b, w))
        adj.setdefault(b, []).append((a, w))
    for t in tracks:
        pts = np.asarray(t['pts'], float)
        for p, q in zip(pts[:-1], pts[1:]):
            a, b = key(p, t['layer']), key(q, t['layer'])
            pos[a], pos[b] = (float(p[0]), float(p[1]), t['layer']), (float(q[0]), float(q[1]), t['layer'])
            edge(a, b, float(np.hypot(*(q - p))))
    for v in kb.vias:                                               # a via joins its layers at its place
        if v.get('net') not in (net, None, ''):
            continue
        here = [k for k, (x, y, _) in pos.items() if math.hypot(x - v['x'], y - v['y']) <= max(v.get('size', 0) / 2, 0.05)]
        for a in here[1:]:
            edge(here[0], a, 0.0)
    ends = [k for k, nb in adj.items() if len({b for b, _ in nb}) == 1] or list(adj)

    def reach(src):
        dist, heap = {src: 0.0}, [(0.0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for b, w in adj[u]:
                if d + w < dist.get(b, math.inf):
                    dist[b] = d + w
                    heapq.heappush(heap, (d + w, b))
        return dist
    best = None
    for a in ends:
        dist = reach(a)
        for b in ends:
            if b != a and b in dist and (best is None or dist[b] > best[0]):
                best = (dist[b], a, b)
    if best is None:
        raise ValueError(f"net {net!r}: its tracks have no two ends")
    out = []
    for k in best[1:]:
        x, y, layer = pos[k]
        pad = next(((fp['ref'], str(q['name'])) for fp in kb.footprints for q in fp['pads']
                    if q.get('net') == net and layer in q.get('layers', ()) and _on_pad(q, x, y)), None)
        (b, w), = [min(adj[k], key=lambda e: -e[1])]                # the track leaving the end: a point in from it
        bx, by, _ = pos[b]
        s = min(INSET, 0.25 * w) / (w or 1.0)
        out.append(dict(x=x, y=y, layer=layer, pad=pad, at=(x + s * (bx - x), y + s * (by - y)), length=best[0]))
    return out


def _on_pad(q, x, y):
    from shapely.geometry import Point
    try:
        return q['geom'].distance(Point(x, y)) < 1e-3
    except Exception:                                               # noqa: BLE001
        return math.hypot(q['xy'][0] - x, q['xy'][1] - y) < 0.05


def order_ends(ends, near=()):
    """{net: (near end, far end)}: a trace's near end is the one on a pad of a part in `near`; else the one
    nearest the first trace's near end (the first trace's own: its first)."""
    near = {str(r).upper() for r in near}
    out, ref = {}, None
    for net, (a, b) in ends.items():
        on = [e for e in (a, b) if e['pad'] and e['pad'][0].upper() in near]
        if len(on) == 1:
            n = on[0]
        elif ref is None:
            n = a
        else:
            n = min((a, b), key=lambda e: math.hypot(e['x'] - ref['x'], e['y'] - ref['y']))
        out[net] = (n, b if n is a else a)
        ref = ref or n
    return out


def port_specs(ordered):
    """(the --port specs, {port name: (net, 'near' | 'far')}): trace k's near end P(2k+1), its far end P(2k+2)."""
    specs, names = [], {}
    for k, (net, pair) in enumerate(ordered.items()):
        for j, (e, which) in enumerate(zip(pair, ('near', 'far'))):
            name = f"P{2 * k + 1 + j}"
            specs.append(f"{name}={e['pad'][0]}.{e['pad'][1]}" if e['pad'] else
                         f"{name}@{e['at'][0]:.4f},{e['at'][1]:.4f},{e['layer']}")
            names[name] = (net, which)
    return specs, names


# ---- from S-parameters: the crosstalk over frequency and in time -----------------------------------------------
def renormalised(S, zold, znew):
    """S (nf x n x n) at the real port impedances zold (a number or one a port) as at znew (a number): each
    trace terminated in znew."""
    n = S.shape[1]
    zo = np.broadcast_to(np.asarray(zold, float), (n,))
    if np.allclose(zo, znew):
        return S
    I = np.eye(n)
    g = np.diag((znew - zo) / (znew + zo))                          # each port's reflection, new against old
    a = np.diag(np.sqrt(znew / zo) * 2 * zo / (znew + zo))          # sqrt(zn / zo) (1 - g) ... real impedances
    return np.array([np.linalg.inv(a) @ (s - g) @ np.linalg.inv(I - g @ s) @ a for s in S])



def edge_response(f, H, rise_s, taper=0.2):
    """(t, v): the voltage H's port sees for an edge of 10-90 % rise `rise_s` (s) and unit swing at the driven
    port, over the edge and what follows it. f (Hz, rising), H (complex, on f). A trapezoid of a long period is
    put through H as its Fourier series; below f[0] H runs to 0 at DC (coupling has none), the top `taper` of the
    band is faded out."""
    f, H = np.asarray(f, float), np.asarray(H, complex)
    ramp = rise_s / 0.8                                             # 0 to 100 %
    fmax = f[-1]
    T = max(40.0 * ramp, 16.0 / max(f[1] - f[0], 1.0) / 8.0, 4e-9)
    n = 1 << int(math.ceil(math.log2(max(8.0 * fmax * T, 1024))))
    t = np.arange(n) * T / n
    x = np.clip(t / ramp, 0, 1) - np.clip((t - T / 2) / ramp, 0, 1)        # up at 0, down at T/2
    X = np.fft.rfft(x)
    fk = np.arange(len(X)) / T
    ff, HH = np.r_[0.0, f], np.r_[0.0, H]
    Hk = np.interp(fk, ff, HH.real, right=0.0) + 1j * np.interp(fk, ff, HH.imag, right=0.0)
    w = np.clip((fmax - fk) / (taper * fmax), 0, 1)
    Hk *= 0.5 * (1 - np.cos(np.pi * w))
    y = np.fft.irfft(X * Hk, n)
    keep = t < 0.45 * T                                             # the rising edge's part
    return t[keep], y[keep]


def crosstalk(f, S, names, rise_s, aggressors=None):
    """The crosstalk of S (nf x n x n, ports `names`: {port: (net, 'near' | 'far')} in order): [dict(aggressor,
    victim, next_db, fext_db (lists over f), next_peak_db, next_peak_Hz, fext_peak_db, fext_peak_Hz, next_pct,
    fext_pct (the edge's largest share of the swing, %), next_t / next_v, fext_t / fext_v (the edge's waveforms,
    ns and share))]."""
    ports = list(names)
    idx = {v: i for i, v in enumerate(names.values())}
    nets = list(dict.fromkeys(n for n, _ in names.values()))
    db = lambda h: 20 * np.log10(np.maximum(np.abs(h), 1e-12))
    out, waves, t_end = [], [], 0.0
    for a in aggressors or nets:
        src = idx[(a, 'near')]
        for v in nets:
            if v == a:
                continue
            row = dict(aggressor=a, victim=v, driven=ports[src])
            for what, end in (('next', 'near'), ('fext', 'far')):
                h = S[:, idx[(v, end)], src]
                d = db(h)
                t, y = edge_response(f, h, rise_s)
                k = int(np.argmax(np.abs(y)))
                row.update({f"{what}_port": ports[idx[(v, end)]], f"{what}_db": [float(x) for x in d],
                            f"{what}_peak_db": float(d.max()), f"{what}_peak_Hz": float(f[int(d.argmax())]),
                            f"{what}_pct": float(100 * abs(y[k])), f"{what}_sign": float(np.sign(y[k]))})
                big = np.flatnonzero(np.abs(y) > 0.03 * abs(y[k]))      # where something happens
                t_end = max(t_end, float(t[big[-1]]) if len(big) else 0.0)
                waves.append((row, what, t, y))
            out.append(row)
    t_end = 1.5 * t_end + 2 * rise_s                                # every waveform over the same stretch
    for row, what, t, y in waves:
        keep = t <= t_end
        step = max(1, int(keep.sum()) // 500)
        row.update({f"{what}_t_ns": [float(x * 1e9) for x in t[keep][::step]],
                    f"{what}_v": [float(x) for x in y[keep][::step]]})
    return out


def victims(rows, f):
    """Each victim with all its aggressors: [dict(victim, aggressors, next_pct, fext_pct (the sum of their peaks:
    the worst case), psnext_db, psfext_db (their power sum over f), psnext_peak_db, psfext_peak_db)]."""
    out = []
    for v in dict.fromkeys(r['victim'] for r in rows):
        mine = [r for r in rows if r['victim'] == v]
        row = dict(victim=v, aggressors=[r['aggressor'] for r in mine])
        for what in ('next', 'fext'):
            p = sum(10 ** (np.asarray(r[f"{what}_db"]) / 10) for r in mine)
            d = 10 * np.log10(np.maximum(p, 1e-24))
            row.update({f"{what}_pct": float(sum(r[f"{what}_pct"] for r in mine)), f"ps{what}_db": [float(x) for x in d],
                        f"ps{what}_peak_db": float(d.max()), f"ps{what}_peak_Hz": float(np.asarray(f)[int(d.argmax())])})
        out.append(row)
    return out


# ---- the layout: where two traces run side by side ----------------------------------------------------------
def _coupling(xs_dict, i, j):
    """(Kc, Kl, Z0 of line i, the delay per metre) of lines i and j of a coupled cross-section (its dict)."""
    from .lines import xsection as X
    r = X.CrossSection.from_dict(xs_dict).solve()
    L = r.L
    kc = -r.C[i, j] / math.sqrt(r.C[i, i] * r.C[j, j])
    kl = L[i, j] / math.sqrt(L[i, i] * L[j, j])
    z0, eeff = r.single(i)
    return float(kc), float(kl), float(z0), math.sqrt(eeff) / C0


def _moved(xs_dict, i, j, factor):
    """The cross-section with the gap between lines i and j `factor` times as wide: the one on the right, and
    everything right of it, moved away (side by side on one level; else None)."""
    c = [list(x) for x in xs_dict['conductors']]
    a, b = (i, j) if c[i][0] < c[j][0] else (j, i)                  # a left of b
    if abs(c[a][2] - c[b][2]) > 1e-9 or c[b][0] <= c[a][1]:
        return None
    shift, edge = (factor - 1) * (c[b][0] - c[a][1]), c[b][0]
    for x in c:
        if x[0] >= edge - 1e-12:
            x[0] += shift
            x[1] += shift
    return dict(xs_dict, conductors=c)


def sections(lines, nets, rise_s, widen=True):
    """The stretches where two of `nets` run side by side, from a line model's drawing (<name>_lines.json's
    dict): [dict(nets (the two), layer, length_mm, gap_mm (edge to edge; None: on different layers), dz_mm,
    delay_ps, z0, kc, kl, next_pct (the estimate for this edge), next_sat_pct (saturated), saturated, fext_pct,
    wider=[dict(gap_mm, next_pct, fext_pct)])], the longest first."""
    groups = {}
    for e in lines.get('elements', []):
        c = (e.get('section') or {}).get('coupled')
        if e.get('kind') == 'trace' and e.get('group') is not None and c and e.get('net') in nets:
            groups.setdefault(e['group'], []).append(e)
    ramp = rise_s / 0.8
    out = []
    for g, es in groups.items():
        for n, ea in enumerate(es):
            for eb in es[n + 1:]:
                if ea['net'] == eb['net']:
                    continue
                xs = ea['section']['coupled']['section']
                i, j = ea['section']['coupled']['index'], eb['section']['coupled']['index']
                try:
                    kc, kl, z0, tpm = _coupling(xs, i, j)
                except Exception:                                   # noqa: BLE001 (a section the solver cannot take)
                    continue
                ci, cj = xs['conductors'][i], xs['conductors'][j]
                same = abs(ci[2] - cj[2]) < 1e-9
                gap = max(ci[0], cj[0]) - min(ci[1], cj[1])
                length = min(ea['length'], eb['length'])
                td = tpm * length * 1e-3

                def est(kc, kl):
                    kb, kf = (kc + kl) / 4, (kc - kl) / 2
                    return 100 * kb * min(1.0, 2 * td / ramp), 100 * kb, 100 * kf * td / ramp
                nx, sat, fx = est(kc, kl)
                row = dict(nets=[ea['net'], eb['net']], layer=ea['layer'] if ea['layer'] == eb['layer'] else
                           f"{ea['layer']} / {eb['layer']}", length_mm=float(length),
                           gap_mm=float(gap) if same else None, dz_mm=float(abs(ci[2] - cj[2])),
                           delay_ps=float(td * 1e12), z0=z0, kc=kc, kl=kl, next_pct=float(nx), next_sat_pct=float(sat),
                           saturated=bool(2 * td >= ramp), fext_pct=float(fx), wider=[], pts=ea.get('pts'))
                if widen and same:
                    for fac in GAPS:
                        m = _moved(xs, i, j, fac)
                        if m is None:
                            break
                        try:
                            k2c, k2l, _, _ = _coupling(m, i, j)
                        except Exception:                           # noqa: BLE001
                            break
                        n2, _, f2 = est(k2c, k2l)
                        row['wider'].append(dict(gap_mm=float(fac * gap), next_pct=float(n2), fext_pct=float(f2)))
                out.append(row)
    return sorted(out, key=lambda r: -abs(r['next_sat_pct']) * 1e3 - r['length_mm'])


# ---- the run ------------------------------------------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(prog='python -m kicad2ems.si', description=__doc__.split('\n\n')[0].strip())
    p.add_argument('board', help=".kicad_pcb")
    p.add_argument('--nets', required=True, help="the traces, by net, comma-separated (two or more)")
    p.add_argument('--aggressor', default='', help="the driven traces, comma-separated (default: each in turn)")
    p.add_argument('--near', default='', help="the parts at the near (driven) ends, comma-separated (U1); default: "
                   "the ends nearest the first trace's first end")
    p.add_argument('--mode', default='both', choices=('both', 'next', 'fext'),
                   help="what the headline and --limit are about: NEXT, FEXT or the larger of them")
    p.add_argument('--rise', type=float, default=100.0, help="the aggressor's edge, ps (10 to 90 %%)")
    p.add_argument('--limit', type=float, default=None, help="the most crosstalk allowed, %% of the swing")
    p.add_argument('--solvers', default='lines', help="lines (the line model: default), or with a full-wave "
                   "solver beside it: lines,mom25d")
    p.add_argument('--ground', default=None, help="the copper layer taken as a solid plane (B.Cu)")
    p.add_argument('--fmax', type=float, default=None, help="GHz (default: 1 / rise)")
    p.add_argument('--z0', type=float, default=50.0, help="the ports' impedance: each trace's termination")
    p.add_argument('--sigma', type=float, default=None, help="copper conductivity, S/m (default: lossless)")
    p.add_argument('--out', default=None, help="output folder")
    p.add_argument('--describe', action='store_true', help="list the settings and stop")
    p.add_argument('--json', action='store_true', help="with --describe: as JSON")
    return p


def run(a, echo=print):
    """Run the question for parsed arguments `a`; returns the result (crosstalk.json's dict)."""
    from .importers.kicad import KiCadBoard
    from .config import build_settings
    from .pipeline import Job
    from .touchstone import read_touchstone
    nets = [n.strip() for n in a.nets.split(',') if n.strip()]
    if len(nets) < 2:
        raise ValueError("name two traces or more (--nets A,B)")
    kb = KiCadBoard(a.board)
    unknown = [n for n in nets if n not in {t['net'] for t in kb.tracks}]
    if unknown:
        raise ValueError(f"no tracks on net(s) {', '.join(unknown)}")
    ordered = order_ends({n: trace_ends(kb, n) for n in nets}, [r for r in a.near.split(',') if r.strip()])
    specs, names = port_specs(ordered)
    aggressors = [n.strip() for n in a.aggressor.split(',') if n.strip()] or None
    bad = [n for n in aggressors or [] if n not in nets]
    if bad:
        raise ValueError(f"aggressor(s) {', '.join(bad)}: not among the traces ({', '.join(nets)})")
    rise = a.rise * 1e-12
    fmax = a.fmax or min(max(1e-9 / rise, 1.0), 40.0)               # GHz
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(os.path.abspath(a.board)), 'out'))
    os.makedirs(out, exist_ok=True)
    for net, (n, fr) in ordered.items():
        where = lambda e: f"{e['pad'][0]}.{e['pad'][1]}" if e['pad'] else f"({e['x']:.2f}, {e['y']:.2f}) {e['layer']}"
        echo(f"trace {net}: near end {where(n)}, far end {where(fr)}, {n['length']:.1f} mm")
    cli = dict(board=a.board, ports=specs, nets=['auto'], fmin=fmax / 400, fmax=fmax, points=401, out=out,
               solvers=a.solvers, no_report=True)
    if a.ground:
        cli['ground'] = a.ground
    if a.sigma:
        cli['sigma'] = a.sigma
    if 'mom25d' in a.solvers:                                       # a wave port's fit takes the coupling of a
        cli['mom_port'] = 'lumped'                                  # neighbour out (mom25d.model.COUPLED_REACH)
    s = build_settings(cli)
    echo(f"solving {len(names)} ports, {fmax / 400:.3g} to {fmax:g} GHz: {a.solvers}")
    job = Job(s, echo=echo)
    job.run()
    name = job.name if hasattr(job, 'name') else os.path.splitext(os.path.basename(a.board))[0]
    with open(os.path.join(out, f"{name}_run.json"), encoding='utf-8') as fh:
        summary = json.load(fh)
    pnames = summary['ports']
    names = {p: names[p] for p in pnames if p in names}
    if len(names) != 2 * len(nets):
        raise ValueError(f"the run has ports {', '.join(pnames)}: not every trace's two ends ({summary.get('warnings')})")
    solved = {}
    for key, r in summary.get('solved', {}).items():
        if not r.get('touchstone'):
            continue
        f, S = read_touchstone(os.path.join(out, r["touchstone"]))[:2]
        S = renormalised(S, summary.get('zref') or 50.0, a.z0)
        rows = crosstalk(f, S, names, rise, aggressors)
        solved[key] = dict(label=r.get('label', key), touchstone=r['touchstone'], f_Hz=[float(x) for x in f],
                           pairs=rows, victims=victims(rows, f))
    if not solved:
        raise ValueError("no solver gave S-parameters")
    lines_path = os.path.join(out, f"{name}_lines.json")
    secs = []
    if os.path.isfile(lines_path):
        with open(lines_path, encoding='utf-8') as fh:
            secs = sections(json.load(fh), nets, rise)
    first = next((k for k in solved if 'line model' in solved[k]['label']), next(iter(solved)))
    ref = next((k for k in solved if k != first), first)            # a full-wave solver's, when there is one
    res = dict(format='kicad2ems-crosstalk', version=1, board=os.path.abspath(a.board), name=name, nets=nets,
               aggressors=aggressors or nets, mode=a.mode, rise_ps=a.rise, limit_pct=a.limit, z0=a.z0,
               ports={p: dict(net=n, end=e) for p, (n, e) in names.items()},
               ends={net: dict(near=_end(n), far=_end(fr), length_mm=n['length']) for net, (n, fr) in ordered.items()},
               solved=solved, answer=ref, sections=secs, warnings=summary.get('warnings', []))
    res['worst'] = worst(res)
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    w = res['worst']
    echo(f"crosstalk: NEXT {w['next_pct']:.2f} % ({w['next_victim']} from {w['next_aggressor']}), FEXT "
         f"{w['fext_pct']:.2f} % ({w['fext_victim']} from {w['fext_aggressor']}) for a {a.rise:g} ps edge"
         + (f"; limit {a.limit:g} %: {'met' if w['ok'] else 'NOT met'}" if a.limit is not None else ''))
    echo(f"-> {os.path.join(out, RESULT)}")
    return res


def _end(e):
    return dict(x=e['x'], y=e['y'], layer=e['layer'], pad=list(e['pad']) if e['pad'] else None)


def worst(res):
    """The largest NEXT and FEXT of the answer's pairs (one aggressor at a time), and whether the limit is met
    for the mode asked."""
    rows = res['solved'][res['answer']]['pairs']
    n = max(rows, key=lambda r: r['next_pct'])
    f = max(rows, key=lambda r: r['fext_pct'])
    val = dict(next=n['next_pct'], fext=f['fext_pct'], both=max(n['next_pct'], f['fext_pct']))[res['mode']]
    lim = res.get('limit_pct')
    return dict(next_pct=n['next_pct'], next_aggressor=n['aggressor'], next_victim=n['victim'], next_peak_db=n['next_peak_db'],
                fext_pct=f['fext_pct'], fext_aggressor=f['aggressor'], fext_victim=f['victim'], fext_peak_db=f['fext_peak_db'],
                value_pct=val, ok=None if lim is None else bool(val <= lim))


# ---- what the board proposes: the traces that run side by side ------------------------------------------------
def find_pairs(kb, reach=3.0, min_length=3.0, most=12):
    """The pairs of nets whose tracks run side by side on one layer: [dict(nets, layer, length_mm, gap_mm)], the
    longest and closest first. Two tracks count where they are nearly parallel and nearer each other, edge to
    edge, than `reach` times the wider one's width; a pair of less than min_length mm is left out, and nets with
    zones (planes, pours) are not traces."""
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    poured = {z.get('net') for z in getattr(kb, 'shapes', []) if isinstance(z, dict) and z.get('kind') == 'zone'}
    segs = []
    for t in kb.tracks:
        if not t['net'] or t['net'] in poured:
            continue
        pts = np.asarray(t['pts'], float)
        for p, q in zip(pts[:-1], pts[1:]):
            if np.hypot(*(q - p)) > 1e-6:
                segs.append((t['net'], t['layer'], float(t['width']), p, q))
    found = {}
    for layer in {s[1] for s in segs}:
        here = [s for s in segs if s[1] == layer]
        geoms = [LineString([s[3], s[4]]) for s in here]
        tree = STRtree(geoms)
        for i, (net, _, w, p, q) in enumerate(here):
            d = (q - p) / np.hypot(*(q - p))
            for j in tree.query(geoms[i].buffer(reach * w + w)):
                net2, _, w2, p2, q2 = here[j]
                if j <= i or net2 == net:
                    continue
                d2 = (q2 - p2) / np.hypot(*(q2 - p2))
                if abs(d @ d2) < 0.94:                                  # (within 20 degrees of parallel)
                    continue
                gap = geoms[i].distance(geoms[j]) - (w + w2) / 2
                if gap > reach * max(w, w2) or gap < 0:
                    continue
                a, b = sorted(((p2 - p) @ d, (q2 - p) @ d))             # j's extent along i
                over = min(b, np.hypot(*(q - p))) - max(a, 0.0)
                if over <= 0:
                    continue
                key = tuple(sorted((net, net2))) + (layer,)
                f = found.setdefault(key, dict(length=0.0, gap=math.inf))
                f['length'] += over
                f['gap'] = min(f['gap'], gap)
    rows = [dict(nets=list(k[:2]), layer=k[2], length_mm=float(v['length']), gap_mm=float(v['gap']))
            for k, v in found.items() if v['length'] >= min_length and not _legs(*k[:2])]
    return sorted(rows, key=lambda r: -r['length_mm'] / (r['gap_mm'] + 0.05))[:most]


def _legs(a, b):
    """Whether nets a and b are the two legs of one differential pair by their names (D0_P / D0_N, TX+ / TX-):
    coupled on purpose, not crosstalk."""
    import re
    m = [re.fullmatch(r'(.*?)[_.]?([PN+-])', n, re.I) for n in (a, b)]
    return bool(m[0] and m[1] and m[0].group(1) == m[1].group(1)
                and {m[0].group(2).upper(), m[1].group(2).upper()} in ({'P', 'N'}, {'+', '-'}))


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if '--find' in argv:                                            # the board's side-by-side traces, as JSON
        from .importers.kicad import KiCadBoard
        board = next((x for x in argv if not x.startswith('-')), None)
        if not board:
            print("kicad2ems.si: --find needs the board", file=sys.stderr)
            return 2
        print(json.dumps(find_pairs(KiCadBoard(board))))
        return 0
    if '--describe' in argv:
        opts = Crosstalk().options()
        print(json.dumps(opts, indent=1) if '--json' in argv else
              '\n'.join(f"{o['flag']:14s} {o['help']}" for o in opts))
        return 0
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.si: {e}", file=sys.stderr)
        return 2
    return 0


# ---- the question, as the study has it (kicad2ems.analyses) ------------------------------------------------------
from .analyses import Analysis                                      # noqa: E402


def _result(folder):
    try:
        with open(os.path.join(folder or '', RESULT), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if d.get('format') == 'kicad2ems-crosstalk' else None
    except (OSError, ValueError):
        return None


class Crosstalk(Analysis):
    """Crosstalk between traces (this module), kicad2ems's own analysis: built in, not an entry point."""
    api = (2, 10)
    name, label, tool = 'crosstalk', 'Crosstalk', 'kicad2ems.si'
    about = "near- and far-end crosstalk between traces for an edge, and what in the layout makes it"
    order = 15
    result = RESULT
    provides = ('crosstalk',)
    engine = 'line model'
    finds = 'traces side by side'

    def check(self):
        from . import __version__
        return True, f"kicad2ems {__version__}: the line model's coupled sections, a full-wave solver beside it on request"

    def options(self):
        o = lambda key, kind, default, help, group='Traces', **kw: dict(key=key, flag='--' + key.replace('_', '-'),
                                                                        kind=kind, default=default, help=help,
                                                                        group=group, **kw)
        return [
            o('nets', 'list', [], "the traces, by net: two or more (one a line, or comma-separated)", level='basic',
              pick='nets'),
            o('aggressor', 'str', '', "the driven traces, comma-separated (empty: each in turn)", level='basic',
              pick='nets'),
            o('near', 'str', '', "the parts at the near (driven) ends, comma-separated: U1 (empty: the ends nearest "
              "the first trace's first end)", pick='parts'),
            o('mode', 'str', 'both', "what the headline and the limit are about: both (the larger), next (near end) "
              "or fext (far end)", 'Edge', choices=['both', 'next', 'fext'], level='basic'),
            o('rise', 'float', 100.0, "the aggressor's edge, ps (10 to 90 %)", 'Edge', level='basic'),
            o('limit', 'float', '', "the most crosstalk allowed, % of the aggressor's swing (empty: none)", 'Edge',
              level='basic'),
            o('z0', 'float', 50.0, "each trace's termination at both ends, ohm", 'Edge'),
            o('check', 'str', '', "a full-wave solver to solve the same ports beside the line model: mom25d, gfdtd "
              "or openems (empty: the line model alone)", 'Solve', choices=['', 'mom25d', 'gfdtd', 'openems']),
            o('ground', 'str', '', "the copper layer taken as a solid plane (In1.Cu; empty: the copper as drawn)",
              'Solve'),
            o('fmax', 'float', '', "the highest frequency solved, GHz (empty: 1 / rise)", 'Solve'),
            o('sigma', 'float', '', "copper conductivity, S/m (empty: lossless)", 'Solve'),
        ]

    @staticmethod
    def _nets(values):
        v = values.get('nets') or []
        v = v.replace('\n', ',').split(',') if isinstance(v, str) else [x for item in v for x in str(item).split(',')]
        return [x.strip() for x in v if x.strip()]

    def tag(self, values):
        nets = self._nets(values)
        return '_'.join(''.join(ch if ch.isalnum() else '-' for ch in n).strip('-')[-12:] for n in nets[:3]) or 'run'

    def command(self, values, board, out, preview=False, results=None):
        nets = self._nets(values)
        if not board:
            raise ValueError("choose the board")
        if len(nets) < 2:
            raise ValueError("name the traces: two nets or more")
        cmd = [sys.executable, '-m', 'kicad2ems.si', str(board), '--nets', ','.join(nets), '--out', str(out)]
        for key in ('aggressor', 'near', 'mode', 'rise', 'limit', 'z0', 'ground', 'fmax', 'sigma'):
            v = values.get(key)
            if v not in (None, '', []):
                cmd += ['--' + key, str(v)]
        if values.get('check'):
            cmd += ['--solvers', 'lines,' + str(values['check'])]
        return cmd

    def status(self, line):
        line = line.strip()
        return line[:160] if line.startswith(('trace ', 'solving ', 'crosstalk:')) else None

    def discover(self, values, board):
        return [sys.executable, '-m', 'kicad2ems.si', str(board), '--find'] if board else None

    def choices(self, text):
        try:
            found = json.loads(text[text.index('['):])
        except ValueError:
            return []
        return [(f"{r['nets'][0]} and {r['nets'][1]}: {r['length_mm']:.1f} mm side by side on {r['layer']}, "
                 f"{r['gap_mm']:.2f} mm apart", dict(nets=r['nets'])) for r in found]

    # ---- what a result holds --------------------------------------------------------------------------------
    def report(self, folder):
        d = _result(folder)
        if d is None:
            return None
        w, ans = d['worst'], d['solved'][d['answer']]
        ramp = d['rise_ps'] / 0.8
        head = (f"NEXT {w['next_pct']:.2f} % ({w['next_victim']} from {w['next_aggressor']}), FEXT {w['fext_pct']:.2f} % "
                f"({w['fext_victim']} from {w['fext_aggressor']}) of the swing, for a {d['rise_ps']:g} ps edge")
        if d.get('limit_pct') is not None:
            head += f"; limit {d['limit_pct']:g} %: {'met' if w['ok'] else 'NOT met'}"
        sec = [dict(title=f"Crosstalk, one aggressor at a time ({ans['label']})",
                    text=f"Each trace terminated in {d['z0']:g} ohm at both ends; the aggressor driven at its near "
                         f"end. In time: the largest voltage at the victim's end for the edge, as a share of the "
                         f"aggressor's swing. Over frequency: the largest in the band solved.",
                    table=dict(columns=['aggressor', 'victim', 'NEXT (%)', 'NEXT peak (dB)', 'at (GHz)', 'FEXT (%)',
                                        'FEXT peak (dB)', 'at (GHz)'],
                               rows=[[r['aggressor'], r['victim'], r['next_pct'], r['next_peak_db'],
                                      r['next_peak_Hz'] / 1e9, r['fext_sign'] * r['fext_pct'], r['fext_peak_db'],
                                      r['fext_peak_Hz'] / 1e9] for r in ans['pairs']],
                               formats=[None, None, '.2f', '.1f', '.2f', '.2f', '.1f', '.2f']))]
        many = [v for v in ans['victims'] if len(v['aggressors']) > 1]
        if many:
            sec.append(dict(title="Each victim, all its aggressors together",
                            text="In time the sum of each aggressor's largest (every edge at its worst moment); over "
                                 "frequency their power sum.",
                            table=dict(columns=['victim', 'aggressors', 'NEXT (%)', 'FEXT (%)', 'PSNEXT peak (dB)',
                                                'PSFEXT peak (dB)'],
                                       rows=[[v['victim'], ', '.join(v['aggressors']), v['next_pct'], v['fext_pct'],
                                              v['psnext_peak_db'], v['psfext_peak_db']] for v in many],
                                       formats=[None, None, '.2f', '.2f', '.1f', '.1f'])))
        secs = d.get('sections') or []
        if secs:
            notes = []
            for s in secs[:3]:
                a, b = s['nets']
                where = (f"{s['gap_mm']:.2f} mm apart on {s['layer']}" if s['gap_mm'] is not None else
                         f"one over the other ({s['layer']}, {s['dz_mm']:.2f} mm between)")
                sat = (f"the edge's ramp ({ramp:.0f} ps) is shorter than twice the stretch's delay "
                       f"({2 * s['delay_ps']:.0f} ps): NEXT is at its full (Kc + Kl) / 4 = {s['next_sat_pct']:.1f} %, "
                       f"and a shorter run would not lower it until it is under {ramp / (2 * s['delay_ps']) * s['length_mm']:.0f} mm"
                       if s['saturated'] else
                       f"the edge's ramp ({ramp:.0f} ps) is longer than twice the stretch's delay "
                       f"({2 * s['delay_ps']:.0f} ps): NEXT is {s['next_pct']:.1f} % of a full {s['next_sat_pct']:.1f} %, "
                       f"and grows with the length")
                fx = ("FEXT is near zero: the capacitive and the inductive coupling cancel (one dielectric around "
                      "the traces)" if abs(s['kc'] - s['kl']) < 0.1 * max(s['kc'], s['kl'], 1e-9) else
                      f"FEXT {s['fext_pct']:+.1f} % grows with the length and with a faster edge (Kl "
                      f"{'above' if s['kl'] > s['kc'] else 'below'} Kc: {'negative' if s['kl'] > s['kc'] else 'positive'})")
                notes.append(f"{a} and {b} run {s['length_mm']:.1f} mm side by side, {where}: {sat}. {fx}.")
            sec.append(dict(title="Where the layout couples them",
                            text="Every stretch where two of the traces run side by side, with the coupling its "
                                 "cross-section gives (Kc capacitive, Kl inductive) and the estimate that follows "
                                 "for this edge. The estimates explain; the figures above are the solved network's.",
                            table=dict(columns=['traces', 'layer', 'length (mm)', 'gap (mm)', 'delay (ps)', 'Z0 (ohm)',
                                                'Kc', 'Kl', 'NEXT est. (%)', 'FEXT est. (%)'],
                                       rows=[[' / '.join(s['nets']), s['layer'], s['length_mm'],
                                              s['gap_mm'] if s['gap_mm'] is not None else 'over', s['delay_ps'], s['z0'],
                                              s['kc'], s['kl'], s['next_pct'], s['fext_pct']] for s in secs],
                                       formats=[None, None, '.1f', '.2f', '.0f', '.1f', '.3f', '.3f', '.2f', '.2f']),
                            notes=notes))
            wide = [s for s in secs if s.get('wider')][:3]
            if wide:
                rows = []
                for s in wide:
                    rows.append([' / '.join(s['nets']), s['gap_mm'], s['next_pct'], s['fext_pct'], 'as drawn'])
                    rows += [[' / '.join(s['nets']), x['gap_mm'], x['next_pct'], x['fext_pct'],
                              f"{x['gap_mm'] / s['gap_mm']:.1f} x"] for x in s['wider']]
                lim = d.get('limit_pct')
                note = []
                if lim is not None and not w['ok']:
                    s = wide[0]
                    val = lambda x: dict(next=abs(x['next_pct']), fext=abs(x['fext_pct']),
                                         both=max(abs(x['next_pct']), abs(x['fext_pct'])))[d['mode']]
                    ok = next((x for x in s['wider'] if val(x) <= lim), None)
                    note.append(f"{' / '.join(s['nets'])}: a gap of {ok['gap_mm']:.2f} mm would meet the {lim:g} % "
                                f"limit by this estimate" if ok else
                                f"{' / '.join(s['nets'])}: three times the gap does not meet the {lim:g} % limit by "
                                f"this estimate: a shorter run (FEXT), a slower edge, or a ground between them")
                sec.append(dict(title="What a wider gap would give",
                                text="The same cross-sections solved again with the victim moved away.",
                                table=dict(columns=['traces', 'gap (mm)', 'NEXT est. (%)', 'FEXT est. (%)', ''],
                                           rows=rows, formats=[None, '.2f', '.2f', '.2f', None]), notes=note))
        else:
            sec.append(dict(title="Where the layout couples them",
                            text="The line model found no stretch where two of these traces run side by side: what "
                                 "couples them is not a parallel run (a crossing, a shared return path, a gap in "
                                 "the plane), which only a full-wave solver sees."))
        sec.append(dict(title="The traces", table=dict(
            columns=['trace', 'near end', 'far end', 'length (mm)', 'ports'],
            rows=[[n, _where(e['near']), _where(e['far']), e['length_mm'],
                   ', '.join(p for p, q in d['ports'].items() if q['net'] == n)] for n, e in d['ends'].items()],
            formats=[None, None, None, '.1f', None])))
        others = [k for k in d['solved'] if k != d['answer']]
        if others:
            o = d['solved'][others[0]]
            by = {(r['aggressor'], r['victim']): r for r in o['pairs']}
            sec.append(dict(title=f"Against {o['label']}",
                            text=f"The same ports by two solvers: {ans['label']} (the figures above) and {o['label']}.",
                            table=dict(columns=['aggressor', 'victim', 'NEXT (%)', f"NEXT, {o['label']} (%)", 'FEXT (%)',
                                                f"FEXT, {o['label']} (%)"],
                                       rows=[[r['aggressor'], r['victim'], r['next_pct'],
                                              by[(r['aggressor'], r['victim'])]['next_pct'], r['fext_pct'],
                                              by[(r['aggressor'], r['victim'])]['fext_pct']] for r in ans['pairs']
                                             if (r['aggressor'], r['victim']) in by],
                                       formats=[None, None, '.2f', '.2f', '.2f', '.2f'])))
        return dict(title=f"Crosstalk: {', '.join(d['nets'])}", summary=head, sections=sec,
                    notes=[str(x) for x in d.get('warnings', [])],
                    footer=f"kicad2ems.si, {os.path.basename(d['board'])}")

    def curves(self, folder):
        d = _result(folder)
        if d is None:
            return []
        ans = d['solved'][d['answer']]
        f = np.asarray(ans['f_Hz']) / 1e9
        pair = lambda r: f"{r['victim']} from {r['aggressor']}"
        sets = [dict(title=f"The edge at the victims' ends ({d['rise_ps']:g} ps)", xlabel='time (ns)',
                     ylabel="share of the aggressor's swing (%)", logx=False, logy=False,
                     info="what each victim sees for the aggressor's edge: NEXT at its near end, FEXT at its far end",
                     curves=[dict(name=f"{what.upper()} {pair(r)}", x=r[f"{what}_t_ns"],
                                  y=[100 * v for v in r[f"{what}_v"]], role='data', unit='%')
                             for r in ans['pairs'] for what in ('next', 'fext')]
                     + ([dict(name=f"limit {s}{d['limit_pct']:g} %", x=[0, max(ans['pairs'][0]['next_t_ns'])],
                              y=[m * d['limit_pct']] * 2, role='limit', unit='%') for s, m in (('', 1), ('-', -1))]
                        if d.get('limit_pct') is not None else [])),
                dict(title='NEXT over frequency', xlabel='frequency (GHz)', ylabel='NEXT (dB)', logx=False, logy=False,
                     info='S(victim near end, aggressor near end)',
                     curves=[dict(name=pair(r), x=list(f), y=r['next_db'], role='data', unit='dB') for r in ans['pairs']]),
                dict(title='FEXT over frequency', xlabel='frequency (GHz)', ylabel='FEXT (dB)', logx=False, logy=False,
                     info='S(victim far end, aggressor near end)',
                     curves=[dict(name=pair(r), x=list(f), y=r['fext_db'], role='data', unit='dB') for r in ans['pairs']])]
        many = [v for v in ans['victims'] if len(v['aggressors']) > 1]
        if many:
            sets.append(dict(title='Power sum at each victim', xlabel='frequency (GHz)', ylabel='power sum (dB)',
                             logx=False, logy=False, info="every aggressor's crosstalk at one victim, as power",
                             curves=[dict(name=f"PS{what.upper()} {v['victim']}", x=list(f), y=v[f"ps{what}_db"],
                                          role='data', unit='dB') for v in many for what in ('next', 'fext')]))
        for k, o in d['solved'].items():                            # a second solver's: the reference curves
            if k == d['answer']:
                continue
            fo = np.asarray(o['f_Hz']) / 1e9
            for s, what in ((sets[1], 'next'), (sets[2], 'fext')):
                s['curves'] += [dict(name=f"{pair(r)}, {o['label']}", x=list(fo), y=r[f"{what}_db"], role='reference',
                                     unit='dB') for r in o['pairs']]
        return sets

    def figures(self, folder):
        d = _result(folder)
        if d is None:
            return []
        out = []
        for v in d['solved'][d['answer']]['victims']:
            out += [dict(name=f"NEXT {v['victim']}", value=v['next_pct'], unit='%'),
                    dict(name=f"FEXT {v['victim']}", value=v['fext_pct'], unit='%')]
        return out


def _where(e):
    return f"{e['pad'][0]}.{e['pad'][1]}" if e.get('pad') else f"({e['x']:.2f}, {e['y']:.2f}) {e['layer']}"


if __name__ == '__main__':
    sys.exit(main())
