"""
kicad2ems.core.susceptibility - radiated susceptibility: the voltage a plane
wave puts on each of the board's ports, on its load with the other ports on
theirs, the worst over every direction it may come from and every linear
polarisation, at IEC 61000-4-3's levels (3 and 10 V/m, 80 MHz to 6 GHz).

    python -m kicad2ems.core.susceptibility board.kicad_pcb --out sus
        [--fmin 0.08 --fmax 6 --points 41] [--field 3,10] [--threshold 100]
        [--load P2=1e6] [--step 5] [--array out/x_mom25d_array.npz]

By reciprocity (kicad2ems.core.radiated): mom25d solves the board once a
frequency for a source at every port (one factor), and each port's transmit far
field on its load, the others loaded, is what it receives from that direction:
V_j = 4 pi / (j k0 eta0) z_j E0 (p . F_j). An antenna run's array file
(--array: mom25d's, or an FDTD solver's with every port excited) stands for the
solve at its frequencies. A differential pair's legs give its differential
voltage too (each leg's complex voltage, the same wave). Each level's table: the
worst port voltage over the band (RMS of the unmodulated carrier; with the
standard's 80 % AM at 1 kHz its peak is 1.8 sqrt 2 times that), its frequency,
direction and polarisation; ports above --threshold (mV RMS) are flagged.

Writes <out>/susceptibility.json and <name>_susceptibility.png. Left out: what
the board is connected to (cables, which most often take a product's immunity),
the board edge and anything below mom25d's infinite ground.
"""
import argparse
import json
import math
import os
import re
import sys
import time

import numpy as np

from . import radiated as R

RESULT, FORMAT, VERSION = 'susceptibility.json', 'kicad2ems-susceptibility', 1
# the standard's 80 % AM at 1 kHz: the modulated carrier's peak over the
# unmodulated RMS
AM_PEAK = 1.8 * math.sqrt(2)


def parser():
    p = argparse.ArgumentParser(
        prog='python -m kicad2ems.core.susceptibility',
        description=__doc__.split('\n\n')[0].strip())
    R.add_board_args(p, 0.08, 6.0, 41, 5.0)
    p.add_argument('--field', default='3,10',
                   help="V/m RMS, the levels (default 3,10: IEC 61000-4-3's "
                   "2 and 3)")
    p.add_argument('--threshold', type=float, default=None,
                   help="mV RMS: a port above it is flagged")
    p.add_argument('--ports', default=None,
                   help="the ports reported (comma separated; default all)")
    return p


def levels_of(text):
    try:
        out = [float(x) for x in re.split(r'[,\s;]+', str(text).strip()) if x]
    except ValueError:
        raise ValueError(f"--field {text}: V/m, comma separated (3,10)")
    if not out or min(out) <= 0:
        raise ValueError(f"--field {text}: levels above 0 V/m")
    return out


def worst(Vt, Vp, theta, phi):
    """Per row of Vt, Vp (k, ntheta, nphi): the largest |V| over the grid's
    directions and the linear polarisations, where (theta, phi deg) and the
    polarisation's angle from theta's unit vector (deg)."""
    W, ang = R.worst_linear(Vt, Vp)
    out = []
    for r in range(W.shape[0]):
        i = np.unravel_index(int(np.argmax(W[r])), W[r].shape)
        out.append((float(W[r][i]), float(theta[i[0]]), float(phi[i[1]]),
                    float(ang[r][i])))
    return out


def run(a, echo=print):
    t0 = time.time()
    out = a.out or 'susceptibility'
    os.makedirs(out, exist_ok=True)
    levels = levels_of(a.field)
    pf = R.port_fields(a, out, lambda t: echo(f"susceptibility: {t}"))
    names = pf.names
    show = ([x.strip() for x in a.ports.split(',') if x.strip()]
            if a.ports else list(names))
    miss = [x for x in show if x not in names and
            x not in [n for n, _ in pf.pairs]]
    if miss:
        raise ValueError(f"--ports {', '.join(miss)}: not a port "
                         f"({', '.join(names)})")
    rows = {n: dict(v=[], theta=[], phi=[], pol=[]) for n in names}
    pairs = {n: dict(v=[], theta=[], phi=[], pol=[], legs=legs)
             for n, legs in pf.pairs}
    ix = [[names.index(l) for l in legs] for _, legs in pf.pairs]
    echo(f"susceptibility: {a.fmin * 1e3:.4g}-{a.fmax * 1e3:.4g} MHz, "
         + ("the file's frequencies" if pf.freq is not None else
            f"at most {a.points} frequencies (where the curves peak)")
         + f", {len(names)} ports, directions every {a.step:g} deg "
         f"({pf.engine})")
    seen = dict(count=0, dt=a.step)

    def one(f):
        """Each port's (and pair's) worst at f: [(v, theta, phi, pol)]."""
        G = pf.grid(f, a.step)
        Vt, Vp = R.receive_grid(G, pf.loads, f)
        w = worst(Vt, Vp, G.theta, G.phi)
        if ix:
            Dt = np.array([Vt[p] - Vt[q] for p, q in ix])
            Dp = np.array([Vp[p] - Vp[q] for p, q in ix])
            w += worst(Dt, Dp, G.theta, G.phi)
        seen.update(count=seen['count'] + 1, dt=G.dt)
        echo(f"susceptibility: {seen['count']} at {f / 1e6:.4g} MHz: worst "
             f"{max(x[0] for x in w) * levels[-1] * 1e3:.3g} mV at "
             f"{levels[-1]:g} V/m")
        return w
    freq, got = R.sweep(pf, a, one, lambda w: [x[0] for x in w],
                        echo=lambda t: echo(f"susceptibility: {t}"))
    keys = list(names) + [n for n, _ in pf.pairs]
    for w in got:
        for n, x in zip(keys, w):
            r = rows[n] if n in rows else pairs[n]
            for k, v in zip(('v', 'theta', 'phi', 'pol'), x):
                r[k].append(v)
    res = dict(format=FORMAT, version=VERSION, board=a.board or a.array,
               engine=pf.engine, unknowns=pf.unknowns,
               stack=getattr(pf, 'stack', None), lower=pf.lower,
               freq=[float(x) for x in freq], ports=names,
               loads={n: float(z) for n, z in zip(names, pf.loads)},
               step_deg=a.step, levels=levels, threshold_mv=a.threshold,
               per_volt_per_metre=rows, pairs=pairs, am_peak=AM_PEAK)
    table = []
    for lv in levels:
        for n in [x for x in show if x in rows] + [x for x in show
                                                    if x in pairs]:
            r = rows.get(n) or pairs[n]
            k = int(np.argmax(r['v']))
            v = r['v'][k] * lv
            table.append(dict(
                field=lv, port=n, pair=n in pairs, mv=v * 1e3,
                dbuv=float(R.dbuv(v)), peak_am_mv=v * AM_PEAK * 1e3,
                f_mhz=freq[k] / 1e6, theta=r['theta'][k], phi=r['phi'][k],
                pol_deg=r['pol'][k],
                over=(a.threshold is not None and v * 1e3 > a.threshold)))
    res['table'] = table
    res['flagged'] = sorted({t['port'] for t in table if t['over']})
    res['warnings'] = [
        "the board alone: what it is connected to (cables, which most often "
        "take a product's immunity) is left out"]
    if pf.engine == 'mom25d' and not pf.lower:
        res['warnings'].append(
            "over mom25d's infinite ground: nothing arrives from below it, and "
            "the board has no edge")
    elif pf.engine != 'mom25d':
        res['warnings'].append(
            f"the array file's {len(freq)} frequencies and its directions "
            f"(every {seen['dt']:g} deg): a resonance between them is not seen")
    res['seconds'] = dict(pf.seconds, total=time.time() - t0)
    name = R.name_of(a)
    try:
        res['images'] = _plot(out, name, res)
    except Exception as e:  # a figure never fails the run
        res['images'] = []
        res['warnings'].append(f"no figure: {e}")
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    for t in table:
        if t['field'] == levels[-1]:
            echo(f"susceptibility: {t['port']}: {t['mv']:.3g} mV "
                 f"({t['dbuv']:.1f} dBuV) at {t['f_mhz']:.4g} MHz, theta "
                 f"{t['theta']:g} phi {t['phi']:g} deg, at {t['field']:g} V/m"
                 + (" - over the threshold" if t['over'] else ''))
    echo(f"susceptibility: wrote {os.path.join(out, RESULT)} "
         f"({res['seconds']['total']:.1f} s)")
    return res


def _plot(out, name, res):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(8.5, 4.6))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    f = np.asarray(res['freq']) / 1e6
    lv = res['levels'][-1]
    for n, r in list(res['per_volt_per_metre'].items()) + [
            (n + ' (diff.)', r) for n, r in res['pairs'].items()]:
        ax.semilogx(f, R.dbuv(np.asarray(r['v']) * lv), lw=1.2, label=n)
    if res['threshold_mv']:
        ax.axhline(float(R.dbuv(res['threshold_mv'] * 1e-3)), color='C3',
                   ls='--', lw=1, label=f"threshold {res['threshold_mv']:g} mV")
    ax.set(xlabel='MHz', ylabel=f'dBuV on the load at {lv:g} V/m',
           title=f"{name}: the worst over direction and polarisation")
    ax.grid(alpha=0.3, which='both')
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = os.path.join(out, f"{name}_susceptibility.png")
    fig.savefig(p, dpi=110)
    return [os.path.basename(p)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.core.susceptibility: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


# ---- the question, as the study has it (kicad2ems.core.analyses) ------------
from .analyses import Analysis                                      # noqa: E402


def _result(folder, fmt=FORMAT, name=RESULT):
    try:
        with open(os.path.join(folder or '', name), encoding='utf-8') as fh:
            d = json.load(fh)
        return d if d.get('format') == fmt else None
    except (OSError, ValueError):
        return None


def board_options(group='Solve', fmin=0.08, fmax=6.0, points=41, step=5.0):
    """The options both EMC analyses take: the band, the directions, the
    loads, mom25d's cell and stack, an antenna run's array file."""
    from .options import Option
    o = lambda key, kind, default, help, **kw: Option(
        key, default, help, dict(float=float, int=int).get(kind, str),
        group=group, **kw)
    return [
        o('fmin', 'float', fmin, "GHz", level='basic'),
        o('fmax', 'float', fmax, "GHz", level='basic'),
        o('points', 'int', points, "frequencies solved, equally spaced in "
          "log f"),
        o('step', 'float', step, "degrees between the far field's "
          "directions"),
        Option('load', [], "PORT=OHMS: a port's load in place of its "
               "reference (repeat)", list, group=group),
        o('mom_dmax', 'float', '', "mm, mom25d's largest cell (empty: its "
          "own)"),
        o('mom_stack', 'str', '', "mom25d's stack (empty: its own; whole for "
          "a board with no solid ground)", choices=('', 'auto', 'ground',
                                                    'whole')),
        o('array', 'str', '', "an antenna run's array file in place of a "
          "solve (<name>_<solver>_array.npz)", path=True),
    ]


def board_command(module, values, board, out, keys):
    cmd = [sys.executable, '-m', module]
    if values.get('array'):
        cmd += ['--array', str(values['array'])]
    elif board:
        cmd.append(str(board))
    else:
        raise ValueError("choose the board, or an antenna run's array file")
    cmd += ['--out', str(out)]
    for key in keys + ('fmin', 'fmax', 'points', 'step', 'mom_dmax',
                       'mom_stack'):
        v = values.get(key)
        if v not in (None, '', []):
            cmd += ['--' + key.replace('_', '-'), str(v)]
    for v in values.get('load') or []:
        if str(v).strip():
            cmd += ['--load', str(v).strip()]
    return cmd


def mom25d_check():
    from .solvers import get
    try:
        return get('mom25d').check()
    except ValueError as e:
        return False, str(e)


class Susceptibility(Analysis):
    """Radiated susceptibility (this module)."""
    name, label, tool = ('susceptibility', 'Radiated susceptibility',
                         'kicad2ems.core.susceptibility')
    about = ("the voltage a plane wave puts on each port, the worst over "
             "direction and polarisation, at IEC 61000-4-3's levels")
    order = 72
    result = RESULT
    provides = ('susceptibility',)
    engine = 'mom25d'
    paths = ('array',)

    def check(self):
        return mom25d_check()

    def options(self):
        from .options import Option
        return [
            Option('field', '3,10', "V/m RMS, the levels (comma separated)",
                   str, group='Field', level='basic'),
            Option('threshold', '', "mV RMS: a port above it is flagged",
                   float, group='Field', level='basic'),
            Option('ports', '', "the ports reported (empty: all)", str,
                   group='Field'),
        ] + board_options()

    def tag(self, values):
        """The highest level: 10Vm."""
        lv = re.split(r'[,\s;]+', str(values.get('field') or '3,10').strip())
        return re.sub(r'[^A-Za-z0-9]+', '', f"{lv[-1].replace('.', 'p')}Vm") or 'run'

    def command(self, values, board, out, preview=False, results=None):
        return board_command(self.tool, values, board, out,
                             ('field', 'threshold', 'ports'))

    def status(self, line):
        line = line.strip()
        return line[:160] if line.startswith('susceptibility:') else None

    def report(self, folder):
        d = _result(folder)
        if d is None:
            return None
        lv = d['levels'][-1]
        top = [t for t in d['table'] if t['field'] == lv]
        w = max(top, key=lambda t: t['mv']) if top else None
        head = (f"at {lv:g} V/m the worst is {w['port']}: {w['mv']:.3g} mV "
                f"RMS ({w['dbuv']:.1f} dBuV) at {w['f_mhz']:.4g} MHz"
                if w else "no ports")
        if d.get('flagged'):
            head += (f"; over {d['threshold_mv']:g} mV: "
                     f"{', '.join(d['flagged'])}")
        sec = [dict(
            title=f"At {t_lv:g} V/m",
            text="Each port's largest voltage on its load over the band, "
            "every direction and linear polarisation (RMS of the unmodulated "
            "carrier; its peak with 80 % AM is 1.8 sqrt 2 times it).",
            table=dict(
                columns=['port', 'mV RMS', 'dBuV', 'peak, 80 % AM (mV)', 'MHz',
                         'theta', 'phi', 'pol. (deg)', 'over'],
                rows=[[t['port'] + (' (diff.)' if t['pair'] else ''),
                       t['mv'], t['dbuv'], t['peak_am_mv'], t['f_mhz'],
                       t['theta'], t['phi'], t['pol_deg'],
                       'yes' if t['over'] else '']
                      for t in d['table'] if t['field'] == t_lv],
                formats=[None, '.3g', '.1f', '.3g', '.4g', '.0f', '.0f',
                         '.0f', None]))
            for t_lv in d['levels']]
        sec.append(dict(title='The model', table=dict(
            columns=['', ''],
            rows=[['engine', d['engine']],
                  ['unknowns', d['unknowns'] or '-'],
                  ['frequencies', f"{len(d['freq'])}, "
                   f"{d['freq'][0] / 1e6:.4g}-{d['freq'][-1] / 1e6:.4g} MHz"],
                  ['directions', f"every {d['step_deg']:g} deg, "
                   + ('the whole sphere' if d['lower'] else
                      'the half-space above the ground')],
                  ['loads', ', '.join(f"{k} {v:g} ohm"
                                      for k, v in d['loads'].items())]])))
        return dict(title='Radiated susceptibility', summary=head,
                    sections=sec, notes=[str(x) for x in d.get('warnings', [])],
                    images=[os.path.join(folder, p)
                            for p in d.get('images', [])
                            if os.path.isfile(os.path.join(folder, p))],
                    footer="kicad2ems.core.susceptibility")

    def curves(self, folder):
        d = _result(folder)
        if d is None:
            return []
        f = list(np.asarray(d['freq']) / 1e6)
        lv = d['levels'][-1]
        cs = [dict(name=n, x=f, y=[float(x) for x in
                                   R.dbuv(np.asarray(r['v']) * lv)],
                   unit='dBuV')
              for n, r in list(d['per_volt_per_metre'].items())
              + [(k + ' (diff.)', v) for k, v in d['pairs'].items()]]
        if d.get('threshold_mv'):
            cs.append(dict(name='threshold', x=[f[0], f[-1]],
                           y=[float(R.dbuv(d['threshold_mv'] * 1e-3))] * 2,
                           role='limit', unit='dBuV'))
        return [dict(title=f"On each port at {lv:g} V/m", xlabel='MHz',
                     ylabel='dBuV', logx=True, logy=False, curves=cs)]

    def figures(self, folder):
        d = _result(folder)
        if d is None:
            return []
        out = []
        for lv in d['levels']:
            top = [t for t in d['table'] if t['field'] == lv]
            if top:
                out.append(dict(name=f"worst at {lv:g} V/m",
                                value=max(t['mv'] for t in top), unit='mV'))
        lv = d['levels'][-1]
        out += [dict(name=f"{t['port']} at {lv:g} V/m", value=t['mv'],
                     unit='mV') for t in d['table'] if t['field'] == lv]
        if d.get('threshold_mv') is not None:
            out.append(dict(name='ports over the threshold',
                            value=len(d.get('flagged') or []), unit=''))
        return out
