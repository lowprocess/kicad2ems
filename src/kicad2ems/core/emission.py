"""
kicad2ems.core.emission - a radiated-emission estimate: the board's ports driven
by a signal (a clock, data, a CW sweep or a spectrum file), the field it makes
at a test site's antenna 3 or 10 m away over a reflecting floor, the antenna's
height scanned and the board turned on a turntable, the largest held, against
CISPR 32's class A or B limits.

    python -m kicad2ems.core.emission board.kicad_pcb --out emi
        --drive P1 --signal clock --f0 25 --rise 1 --amp 3.3
        [--distance 3] [--class B] [--orientation all] [--fmin 0.03 --fmax 6]

The drive (kicad2ems.core.phased's PORT[=amplitude][@phase] items: P1, or a pair
P1+@0,P1-@180) scales the signal at each driven port: its source's open-circuit
voltage, behind the port's load (its reference, or --load), every other port on
its load. mom25d solves the board at each frequency (kicad2ems.core.radiated: one
factor, every port's currents), the drive's far field on a grid of directions
(--step deg), and the site (kicad2ems.core.radiated.site) the field at the
antenna: below 1 GHz the direct ray and the floor's, above it the direct ray
alone (an absorber-lined floor, as CISPR 16-1-4 has above 1 GHz); the vertical
and horizontal polarisations, heights 1-4 m, the turntable round, the board in
--orientation (all: the worst of flat, flipped and on each edge).

The signal's spectrum: a clock's lines (a trapezoid: f0, 10-90 % rise, 0 to amp,
duty), data (PRBS lines while their spacing is at least the receiver's
bandwidth, else random data's PSD in 120 kHz / 1 MHz), a flat CW amplitude, or a
file of lines (f Hz, volts RMS). The field per volt is solved at the lines
themselves when they are no more than --points, else on --points frequencies in
log f and interpolated between them in dB (a resonance narrower than their
spacing is missed). A line reads the same on CISPR's quasi-peak, average and
peak detectors (a CW tone); data's continuous part is its RMS in the bandwidth,
which the peak detector reads several dB above.

Writes <out>/emission.json and <name>_emission.png. Left out: what the board is
connected to (cables, which most often dominate a product's emission: their
common-mode current needs the cable's model and the field-to-line coupling), the
board edge and anything below mom25d's infinite ground, an enclosure.
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

RESULT, FORMAT, VERSION = 'emission.json', 'kicad2ems-emission', 1


def parser():
    p = argparse.ArgumentParser(prog='python -m kicad2ems.core.emission',
                                description=__doc__.split('\n\n')[0].strip())
    R.add_board_args(p, 0.03, 6.0, 41, 3.0)
    p.add_argument('--drive', default=None,
                   help="the driven ports, PORT[=amplitude][@phase deg] "
                   "comma separated (default: the first port)")
    p.add_argument('--signal', default='clock',
                   choices=('clock', 'pattern', 'flat', 'file'))
    p.add_argument('--f0', type=float, default=None, help="MHz, a clock's")
    p.add_argument('--amp', type=float, default=3.3,
                   help="V: the clock's or the data's swing (0 to it); flat: "
                   "the RMS volts at every frequency")
    p.add_argument('--rise', type=float, default=1.0,
                   help="ns, the edges' 10-90 %% time")
    p.add_argument('--duty', type=float, default=0.5, help="a clock's")
    p.add_argument('--rate', type=float, default=None, help="Gb/s, data's")
    p.add_argument('--prbs', default='7',
                   help="data's PRBS order (7, 9, 11, 15, 23, 31) or random")
    p.add_argument('--spectrum', default=None,
                   help="a file of lines: f (Hz), volts RMS")
    p.add_argument('--distance', type=float, default=3.0,
                   help="m, the antenna's distance (3 or 10)")
    p.add_argument('--class', dest='cls', default='B', choices=('A', 'B'),
                   help="CISPR 32's class")
    p.add_argument('--h-eut', type=float, default=0.8,
                   help="m, the board's height over the floor (a table-top "
                   "product's 0.8)")
    p.add_argument('--heights', default='1,4,0.1',
                   help="m, the antenna's scan: from,to,step")
    p.add_argument('--turn-step', type=float, default=5.0,
                   help="deg, the turntable's")
    p.add_argument('--orientation', default='all',
                   choices=tuple(R.ORIENTATION_SETS) + tuple(R.ORIENTATIONS),
                   help="the board on the turntable: flat (its top up), "
                   "upright (on its edge, +y up), all (the worst of six)")
    p.add_argument('--floor', default='auto', choices=('auto', 'on', 'off'),
                   help="the floor's reflection: auto below 1 GHz")
    return p


def signal_of(a):
    prbs = str(a.prbs or '').strip().lower()
    order = None if prbs in ('', 'random', 'none', '0') else int(prbs)
    if order is not None and order not in (7, 9, 11, 15, 23, 31):
        raise ValueError(f"--prbs {a.prbs}: 7, 9, 11, 15, 23, 31 or random")
    return R.Signal(a.signal, f0=(a.f0 or 0) * 1e6 or None, amp=a.amp,
                    rise=(a.rise or 0) * 1e-9, duty=a.duty,
                    rate=(a.rate or 0) * 1e9 or None, prbs=order,
                    path=a.spectrum)


def heights_of(text):
    try:
        v = [float(x) for x in re.split(r'[,\s;]+', str(text).strip()) if x]
    except ValueError:
        v = []
    if len(v) == 1:
        return np.array(v)
    if len(v) != 3 or v[2] <= 0 or v[1] < v[0] or v[0] <= 0:
        raise ValueError(f"--heights {text}: from,to,step in m (1,4,0.1)")
    return np.arange(v[0], v[1] + v[2] / 2, v[2])


def detectors(f, segs):
    """The limits at each f: (qp, av, pk) dBuV/m, nan where none applies."""
    return tuple(R.limit_at(f, segs[k]) for k in ('qp', 'av', 'pk'))


def run(a, echo=print):
    t0 = time.time()
    out = a.out or 'emission'
    os.makedirs(out, exist_ok=True)
    sig = signal_of(a)
    heights = heights_of(a.heights)
    turns = np.arange(0.0, 360.0 - 1e-9, a.turn_step)
    orients = R.ORIENTATION_SETS.get(a.orientation, (a.orientation,))
    pf = R.port_fields(a, out, lambda t: echo(f"emission: {t}"))
    from . import phased
    names = pf.names
    w = phased.parse_drive([x for x in (a.drive or names[0]).split(',')],
                           names)
    if not np.any(w):
        raise ValueError("--drive: no port driven")
    lo, hi = a.fmin * 1e9, a.fmax * 1e9
    lines = sig.lines(lo, hi)
    exact = (lines is not None and 0 < len(lines[0]) <= a.points
             and pf.freq is None)
    if lines is not None and not len(lines[0]):
        raise ValueError(f"{sig.describe()}: no line between {a.fmin:g} and "
                         f"{a.fmax:g} GHz")
    echo(f"emission: {sig.describe()}; driven "
         f"{', '.join(phased.format_drive(w, names))}; "
         + (f"the {len(lines[0])} lines" if exact else
            "the file's frequencies" if pf.freq is not None else
            f"at most {a.points} frequencies (where the field peaks)")
         + f"; {a.distance:g} m, {len(orients)} orientation"
         f"{'s' if len(orients) > 1 else ''}")
    seen = dict(count=0, site=0.0)

    def one(f):
        G = pf.grid(f, a.step, drive=w)
        ts = time.time()
        floor = a.floor == 'on' or (a.floor == 'auto' and f <= 1e9)
        s = R.site(lambda u: G.at(u)[0], f, a.distance, a.h_eut, heights,
                   turns, orients, floor)
        seen.update(count=seen['count'] + 1,
                    site=seen['site'] + time.time() - ts)
        echo(f"emission: {seen['count']} at {f / 1e6:.4g} MHz: "
             f"{float(R.dbuv(s['E'])):.1f} dBuV/m per volt")
        return s
    freq, where = R.sweep(pf, a, one, lambda s: [s['E']],
                          given=lines[0] if exact else None,
                          echo=lambda t: echo(f"emission: {t}"))
    T = [s['E'] for s in where]
    t_site = seen['site']
    T = np.asarray(T)
    # the field at each line (or each frequency) of the signal
    if lines is not None:
        fl, vl = lines
        if exact:
            Tl = T
        else:
            Tl = 10 ** np.interp(np.log(fl), np.log(freq),
                                 np.log10(np.maximum(T, 1e-30)))
        fe, E = fl, Tl * vl
        kind = 'lines'
    else:
        fe, E = freq, T * sig.reading(freq)
        kind = 'continuous' if sig.kind == 'pattern' else 'flat'
    segs = R.limits(a.cls, a.distance)
    qp, av, pk = detectors(fe, segs)
    e_db = R.dbuv(E)
    margin = _least(np.stack([qp - e_db, av - e_db, pk - e_db]))
    ok = ~np.isnan(margin)
    worst = None
    if ok.any():
        k = int(np.nanargmin(np.where(ok, margin, np.nan)))
        worst = dict(f_mhz=float(fe[k] / 1e6), e_dbuv=float(e_db[k]),
                     margin_db=float(margin[k]),
                     limit_dbuv=float(e_db[k] + margin[k]),
                     detector=('quasi-peak' if fe[k] <= 1e9 else 'average'))
        # where the site saw it (the frequency solved nearest)
        j = int(np.argmin(np.abs(np.log(freq) - np.log(fe[k]))))
        worst.update({x: where[j][x] for x in ('orientation', 'turn',
                                               'height', 'pol')})
    res = dict(
        format=FORMAT, version=VERSION, board=a.board or a.array,
        engine=pf.engine, unknowns=pf.unknowns, lower=pf.lower,
        stack=getattr(pf, 'stack', None), ports=names,
        loads={n: float(z) for n, z in zip(names, pf.loads)},
        drive=phased.format_drive(w, names), signal=dict(
            kind=sig.kind, about=sig.describe(), f0=sig.f0, amp=sig.amp,
            rise=sig.rise, duty=sig.duty, rate=sig.rate, prbs=sig.prbs,
            spectrum=sig.path),
        site=dict(distance=a.distance, cls=a.cls, h_eut=a.h_eut,
                  heights=[float(heights[0]), float(heights[-1])],
                  height_step=(float(heights[1] - heights[0])
                               if len(heights) > 1 else 0.0),
                  turn_step=a.turn_step, orientations=list(orients),
                  floor=a.floor),
        solved=dict(
            freq=[float(x) for x in freq],
            per_volt_dbuv=[float(x) for x in R.dbuv(T)],
            orientation=[s['orientation'] for s in where],
            height=[s['height'] for s in where],
            turn=[s['turn'] for s in where],
            pol=[s['pol'] for s in where],
            by_orientation=[s['E_by_orientation'] for s in where]),
        exact=exact, kind=kind,
        emission=dict(freq=[float(x) for x in fe],
                      e_dbuv=[float(x) for x in e_db],
                      qp=_nan(qp), av=_nan(av), pk=_nan(pk),
                      margin=_nan(margin)),
        worst=worst, step_deg=a.step,
        over=int(np.sum(margin[ok] < 0)) if ok.any() else 0,
        peak_dbuv=float(np.max(e_db)) if len(e_db) else None,
        limits={k: [list(x) for x in v] for k, v in segs.items()})
    res['warnings'] = [
        "the board alone: what it is connected to (cables, which most often "
        "dominate a product's emission) is left out: an estimate of the "
        "board's own share"]
    if pf.engine == 'mom25d' and not pf.lower:
        res['warnings'].append(
            "over mom25d's infinite ground: nothing radiates below it and the "
            "board has no edge (a real board's edges and its ground's own "
            "currents radiate too)")
    if lines is not None and not exact:
        res['warnings'].append(
            f"{len(fe)} lines on {len(freq)} frequencies solved: the field per "
            "volt interpolated between them in dB (a resonance between them "
            "is missed; --points for more)")
    if kind == 'continuous':
        res['warnings'].append(
            "data's continuous spectrum: its RMS in the receiver's bandwidth; "
            "the peak detector reads several dB above it")
    res['seconds'] = dict(pf.seconds, site=t_site, total=time.time() - t0)
    name = R.name_of(a)
    try:
        res['images'] = _plot(out, name, res)
    except Exception as e:  # a figure never fails the run
        res['images'] = []
        res['warnings'].append(f"no figure: {e}")
    with open(os.path.join(out, RESULT), 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1)
    if worst:
        echo(f"emission: worst margin {worst['margin_db']:+.1f} dB at "
             f"{worst['f_mhz']:.4g} MHz ({worst['e_dbuv']:.1f} dBuV/m against "
             f"{worst['limit_dbuv']:.1f}, class {a.cls} {worst['detector']} at "
             f"{a.distance:g} m); {res['over']} over the limit")
    echo(f"emission: wrote {os.path.join(out, RESULT)} "
         f"({res['seconds']['total']:.1f} s)")
    return res


def _least(M):
    """The smallest of each column of M, nan where all are."""
    none = np.isnan(M).all(0)
    return np.where(none, np.nan, np.where(np.isnan(M), np.inf, M).min(0))


def _nan(x):
    return [None if not np.isfinite(v) else float(v) for v in np.asarray(x)]


def _plot(out, name, res):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(9, 6.2))
    FigureCanvasAgg(fig)
    ax, ax2 = fig.subplots(2, 1, sharex=True,
                           gridspec_kw=dict(height_ratios=[2, 1]))
    em = res['emission']
    f = np.asarray(em['freq']) / 1e6
    e = np.asarray(em['e_dbuv'])
    lim = min(v for segs in res['limits'].values() for _, _, v in segs)
    lo = (max(np.min(e) - 5, min(lim, np.max(e)) - 60) if len(e) else
          lim - 60)
    top = max(max(v for segs in res['limits'].values() for _, _, v in segs),
              np.max(e) if len(e) else lim) + 6
    if res['kind'] == 'lines':
        ax.vlines(f, lo, e, lw=0.9, color='C0', label='the board')
    else:
        ax.semilogx(f, e, lw=1.2, color='C0', label='the board')
    for det, ls, col in (('qp', '-', 'C3'), ('av', '-', 'C1'),
                         ('pk', '--', 'C1')):
        for a, b, v in res['limits'][det]:
            ax.hlines(v, a / 1e6, b / 1e6, colors=col, linestyles=ls,
                      lw=1.3)
        ax.plot([], [], ls=ls, color=col,
                label={'qp': 'quasi-peak', 'av': 'average',
                       'pk': 'peak'}[det])
    ax.set_xscale('log')
    ax.set_ylim(lo, top)
    w = res['worst']
    ax.set(ylabel='dBuV/m',
           title=f"{name}: class {res['site']['cls']} at "
           f"{res['site']['distance']:g} m"
           + (f", worst margin {w['margin_db']:+.1f} dB at "
              f"{w['f_mhz']:.4g} MHz" if w else ''))
    ax.grid(alpha=0.3, which='both')
    ax.legend(fontsize=8, loc='lower right')
    sv = res['solved']
    ax2.semilogx(np.asarray(sv['freq']) / 1e6, sv['per_volt_dbuv'], lw=1.2,
                 marker='.', ms=3)
    ax2.set(xlabel='MHz', ylabel='dBuV/m per V')
    ax2.grid(alpha=0.3, which='both')
    fig.tight_layout()
    p = os.path.join(out, f"{name}_emission.png")
    fig.savefig(p, dpi=110)
    return [os.path.basename(p)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        run(a)
    except ValueError as e:
        print(f"kicad2ems.core.emission: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


# ---- the question, as the study has it (kicad2ems.core.analyses) ------------
from .analyses import Analysis                                      # noqa: E402
from .susceptibility import (_result, board_options, board_command,  # noqa
                             mom25d_check)


class Emission(Analysis):
    """A radiated-emission estimate (this module)."""
    name, label, tool = ('emission', 'Radiated emission',
                         'kicad2ems.core.emission')
    about = ("the board's ports driven by a clock or data: the field at a "
             "test site's antenna against CISPR 32's limits")
    order = 71
    result = RESULT
    provides = ('emission',)
    engine = 'mom25d'
    paths = ('array', 'spectrum')

    def check(self):
        return mom25d_check()

    def options(self):
        from .options import Option
        o = lambda key, kind, default, help, group='Signal', **kw: Option(
            key, default, help, dict(float=float, int=int).get(kind, str),
            group=group, **kw)
        return [
            o('drive', 'str', '', "the driven ports: PORT[=amplitude]"
              "[@phase] comma separated (empty: the first)", level='basic'),
            o('signal', 'str', 'clock', "clock, pattern (data), flat (a CW "
              "sweep) or file", choices=('clock', 'pattern', 'flat', 'file'),
              level='basic'),
            o('f0', 'float', '', "MHz, a clock's frequency", level='basic'),
            o('amp', 'float', 3.3, "V, the swing (flat: RMS volts)"),
            o('rise', 'float', 1.0, "ns, the edges' 10-90 %"),
            o('duty', 'float', 0.5, "a clock's duty"),
            o('rate', 'float', '', "Gb/s, data's rate"),
            o('prbs', 'str', '7', "data's PRBS order, or random",
              choices=('7', '9', '11', '15', '23', '31', 'random')),
            o('spectrum', 'str', '', "a file of lines: f (Hz), volts RMS",
              path=True),
            o('distance', 'float', 3.0, "m, the antenna's distance (3, 10)",
              'Site', level='basic'),
            o('cispr_class', 'str', 'B', "CISPR 32's class", 'Site',
              choices=('A', 'B'), level='basic'),
            o('orientation', 'str', 'all', "the board on the turntable",
              'Site', choices=tuple(R.ORIENTATION_SETS)),
            o('h_eut', 'float', 0.8, "m, the board over the floor", 'Site'),
            o('heights', 'str', '1,4,0.1', "m, the antenna's scan: "
              "from,to,step", 'Site'),
            o('turn_step', 'float', 5.0, "deg, the turntable's", 'Site'),
            o('floor', 'str', 'auto', "the floor's reflection (auto: below "
              "1 GHz)", 'Site', choices=('auto', 'on', 'off')),
        ] + board_options(fmin=0.03, points=41, step=3.0)

    def tag(self, values):
        sig = values.get('signal') or 'clock'
        what = {'clock': f"{values.get('f0') or ''}MHz",
                'pattern': f"{values.get('rate') or ''}Gbps"}.get(sig, sig)
        return re.sub(r'[^A-Za-z0-9]+', '',
                      f"{values.get('drive') or 'P1'}{what}") or 'run'

    def command(self, values, board, out, preview=False, results=None):
        if (values.get('signal') or 'clock') == 'clock' and \
                values.get('f0') in (None, ''):
            raise ValueError("give the clock's frequency (f0, MHz)")
        cmd = board_command(self.tool, values, board, out,
                            ('drive', 'signal', 'f0', 'amp', 'rise', 'duty',
                             'rate', 'prbs', 'spectrum', 'distance',
                             'orientation', 'h_eut', 'heights', 'turn_step',
                             'floor'))
        if values.get('cispr_class'):
            cmd += ['--class', str(values['cispr_class'])]
        return cmd

    def status(self, line):
        line = line.strip()
        return line[:160] if line.startswith('emission:') else None

    def report(self, folder):
        d = _result(folder, FORMAT, RESULT)
        if d is None:
            return None
        w, st = d['worst'], d['site']
        head = (f"{d['signal']['about']} on {', '.join(d['drive'])}: "
                + (f"worst margin {w['margin_db']:+.1f} dB at "
                   f"{w['f_mhz']:.4g} MHz ({w['e_dbuv']:.1f} dBuV/m against "
                   f"class {st['cls']}'s {w['limit_dbuv']:.1f} "
                   f"{w['detector']} at {st['distance']:g} m)"
                   if w else "no line within CISPR 32's band")
                + (f"; {d['over']} over the limit" if d['over'] else ''))
        em = d['emission']
        order = np.argsort([m if m is not None else np.inf
                            for m in em['margin']])[:12]
        sec = [dict(
            title='The closest to the limit',
            text="The board's field at the antenna, the largest over the "
            "heights, the turntable, the polarisations and the orientations, "
            "against CISPR 32 (a line reads the same on every detector).",
            table=dict(
                columns=['MHz', 'dBuV/m', 'limit', 'margin (dB)'],
                rows=[[em['freq'][k] / 1e6, em['e_dbuv'][k],
                       em['e_dbuv'][k] + em['margin'][k],
                       em['margin'][k]] for k in order
                      if em['margin'][k] is not None],
                formats=['.4g', '.1f', '.1f', '+.1f'])),
            dict(title='The site', table=dict(
                columns=['', ''],
                rows=[['distance', f"{st['distance']:g} m"],
                      ['class', st['cls']],
                      ['the board', f"{st['h_eut']:g} m over the floor, "
                       f"{', '.join(st['orientations'])}"],
                      ['antenna', f"{st['heights'][0]:g}-"
                       f"{st['heights'][1]:g} m every "
                       f"{st['height_step']:g} m, both polarisations"],
                      ['turntable', f"every {st['turn_step']:g} deg"],
                      ['floor', {'auto': 'reflecting below 1 GHz',
                                 'on': 'reflecting', 'off': 'absorbing'}
                       [st['floor']]],
                      ['engine', f"{d['engine']}"
                       + (f", {d['unknowns']:,} unknowns"
                          if d['unknowns'] else '')],
                      ['loads', ', '.join(f"{k} {v:g} ohm"
                                          for k, v in d['loads'].items())]]))]
        return dict(title='Radiated emission', summary=head, sections=sec,
                    notes=[str(x) for x in d.get('warnings', [])],
                    images=[os.path.join(folder, p)
                            for p in d.get('images', [])
                            if os.path.isfile(os.path.join(folder, p))],
                    footer="kicad2ems.core.emission")

    def curves(self, folder):
        d = _result(folder, FORMAT, RESULT)
        if d is None:
            return []
        em = d['emission']
        f = [x / 1e6 for x in em['freq']]
        cs = [dict(name='the board', x=f, y=em['e_dbuv'], unit='dBuV/m')]
        for det in ('qp', 'av', 'pk'):
            for a, b, v in d['limits'][det]:
                cs.append(dict(name={'qp': 'quasi-peak', 'av': 'average',
                                     'pk': 'peak'}[det],
                               x=[a / 1e6, b / 1e6], y=[v, v], role='limit',
                               unit='dBuV/m'))
        return [dict(title=f"Class {d['site']['cls']} at "
                     f"{d['site']['distance']:g} m", xlabel='MHz',
                     ylabel='dBuV/m', logx=True, curves=cs)]

    def figures(self, folder):
        d = _result(folder, FORMAT, RESULT)
        if d is None:
            return []
        out = []
        if d['worst']:
            out += [dict(name='margin', value=d['worst']['margin_db'],
                         unit='dB'),
                    dict(name='at', value=d['worst']['f_mhz'], unit='MHz')]
        if d.get('peak_dbuv') is not None:
            out.append(dict(name='peak', value=d['peak_dbuv'],
                            unit='dBuV/m'))
        out.append(dict(name='over the limit', value=d['over'], unit=''))
        return out
