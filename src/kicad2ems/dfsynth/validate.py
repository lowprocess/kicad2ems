"""
Validation with kicad2ems: runs the written board through its solvers, reads the
Touchstone results, measures the band edges and compares them with the spec and
the line model. em_tune() feeds the measured centre / bandwidth error back into
the design's kf / kb and re-runs.
"""
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np

# (f Hz, S (nf, n, n)) of a .sNp file
from kicad2ems.core.touchstone import read_touchstone
from .calibrate import edges, errors, targets, band, model_response
from .circuit import db


def run_kicad2ems(toml, outdir, solvers='mom,lines', extra=(), log=print):
    """Runs kicad2ems; returns {solver name: .s2p path}."""
    exe = shutil.which('kicad2ems')
    exe = [exe] if exe else [sys.executable, '-m', 'kicad2ems.core.cli']
    events = os.path.join(outdir, 'kicad2ems_events.jsonl')
    out_log = os.path.join(outdir, 'kicad2ems_output.log')
    cmd = (exe + [
        toml, '--solvers', solvers, '--out', outdir, '--no-report', '--events',
        events
    ] + list(extra))
    log('  $ ' + ' '.join(cmd))
    if os.path.exists(events):
        os.remove(events)
    follow = Follow(events, log)
    # its output to a file (no pipe to fill), its events followed
    with open(out_log, 'w') as fh:
        p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                             text=True)
        try:
            while p.poll() is None:
                follow.read()
                time.sleep(0.5)
        except BaseException:  # dfsynth stopped: the solver too
            p.kill()
            raise
    follow.read()
    if p.returncode:
        with open(out_log, errors='replace') as fh:
            tail = fh.read()[-3000:]
        raise RuntimeError(f"kicad2ems failed ({p.returncode}):\n{tail}")
    base = os.path.splitext(os.path.basename(toml))[0]
    out = {}
    for name in sorted(os.listdir(outdir)):
        if name.startswith(base + '_') and name.endswith('.s2p'):
            out[name[len(base) + 1:-4]] = os.path.join(outdir, name)
    return out


def metrics(design, f, s21, s11=None, relative=True):
    """Band edges, centre and bandwidth errors, pass-band loss / match of a
    response."""
    spec = design.spec
    y = db(s21)
    e = edges(spec, f, y, relative=relative)
    ef, eb = errors(spec, e)
    kind, f0, fbw = band(spec)
    m = {
        'edges_GHz': {k: (float(v) / 1e9 if v else None)
                      for k, v in e.items()},
        'centre_error_pct': None if ef is None else float(100 * (ef - 1)),
        'bandwidth_error_pct': None if eb is None else float(100 * (eb - 1))
    }
    if kind == 'lowpass':
        pb = f <= f0
    elif kind == 'bandpass':
        pb = abs(f / f0 - 1) <= fbw / 2
    else:  # outside the target edges, a tenth of the band out
        t = targets(spec)
        g = 0.1 * (t['f2'] - t['f1'])
        pb = (f <= t['f1'] - g) | (f >= t['f2'] + g)
    if np.any(pb):
        m['passband_IL_dB'] = float(-y[pb].max())
        m['passband_IL_worst_dB'] = float(-y[pb].min())
        if s11 is not None:
            m['passband_RL_worst_dB'] = float(-db(s11)[pb].max())
    # the match over the response's own pass band, between (or beyond) the
    # measured -3 dB edges, a tenth of the band inside them: a mistuned filter's
    # mismatch at the spec's band is its skirt, not its match
    own = None
    if kind == 'lowpass' and e.get('fc'):
        own = f <= 0.8 * e['fc']
    elif kind != 'lowpass' and e.get('f1') and e.get('f2'):
        g = 0.1 * (e['f2'] - e['f1'])
        if kind == 'bandpass':
            own = (f >= e['f1'] + g) & (f <= e['f2'] - g)
        else:
            own = (f <= e['f1'] - g) | (f >= e['f2'] + g)
    if s11 is not None and own is not None and np.any(own):
        m['own_passband_RL_worst_dB'] = float(-db(s11)[own].max())
    if kind == 'bandstop':
        sb = abs(f / f0 - 1) <= fbw / 4
        if np.any(sb):
            m['stopband_rejection_dB'] = float(-y[sb].max())
    return m


def plot(design, results, path, title=''):
    """S21 / S11 of the line model and of each EM result, with the target
    edges."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5.6))
    t = targets(design.spec)
    colors = ['#1f77b4', '#d62728', '#2ca02c', '#9467bd', '#ff7f0e']
    for i, (name, (f, s21, s11)) in enumerate(results.items()):
        c = colors[i % len(colors)]
        ls = '--' if name.startswith('model') else '-'
        ax.plot(f / 1e9, db(s21), ls, color=c, lw=1.6, label=f'{name} S21')
        if s11 is not None:
            ax.plot(f / 1e9, db(s11), ls, color=c, lw=0.9, alpha=0.6,
                    label=f'{name} S11')
    for k, v in t.items():
        ax.axvline(v / 1e9, color='k', lw=0.8, ls=':')
    ax.axhline(-3, color='k', lw=0.6, ls=':')
    ax.set_ylim(-60, 2)
    ax.set_xlabel('frequency (GHz)')
    ax.set_ylabel('dB')
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2, loc='lower left')
    ax.set_title(
        title or
        f'{design.spec.kind} / {design.spec.topology}: dotted lines = target '
        '-3 dB edges'
    )
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


class Follow:
    """Follows a kicad2ems run's events (--events, JSON lines) into the log as
    it runs: its messages, and its progress every few seconds (an FDTD run: the
    timestep and the energy left; a frequency sweep: the frequencies done)."""

    EVERY = 10.0  # s between progress lines of a run

    def __init__(self, path, log=print):
        # kicad2ems's reader: the stream's format checked
        from kicad2ems.core.runs import EventTail
        self.tail, self.log, self.last = EventTail(path), log, {}

    def read(self):
        for e in self.tail.read():
            self.event(e)

    def event(self, e):
        kind = e.get('kind')
        if kind == 'log':
            for line in str(e.get('text', '')).splitlines():
                self.log('    ' + line)
        elif kind == 'stage' and e.get('name') == 'solve':
            self.log('    solving...')
        elif kind == 'status':
            text = self.progress(e)
            run = e.get('run', '')
            if (text and (e.get('fraction') == 1.0
                          or e['t'] - self.last.get(run, -1e9) >= self.EVERY)):
                self.last[run] = e['t']
                self.log(f"    {run}: {text}")

    @staticmethod
    def progress(e):
        t = f"{e['t']:.0f} s"
        # a frequency sweep
        if e.get('solves') is not None and e.get('fraction') is not None:
            s = f"{100 * e['fraction']:.0f}% ({e['solves']} frequencies"
            s += (f", {e['f_ghz']:.3f} GHz)"
                  if e.get('f_ghz') is not None else ')')
            if e.get('eta'):
                s += f", about {e['eta']:.0f} s left"
            return s + f", {t}"
        if e.get('step') is not None:  # FDTD: the energy decaying
            s = f"step {e['step']}"
            if e.get('db') is not None:
                s += f", energy {e['db']:.1f} dB"
            return s + f", {t}"
        return None


def validate(design, base, solvers='mom,lines', extra=(), log=print, tag=''):
    """Runs kicad2ems on <base>.toml; returns ({name: metrics}, {name: (f, s21,
    s11)})."""
    outdir = base + '_em' + tag
    os.makedirs(outdir, exist_ok=True)
    # this round's results only: the last round's Touchstone files (another
    # design, another solver) had been read with them by run_kicad2ems's
    # listing, and --em-tune corrected the design from a stale response
    stem = os.path.splitext(os.path.basename(base + '.toml'))[0]
    for name in os.listdir(outdir):
        if name.startswith(stem + '_') and name.endswith('.s2p'):
            os.remove(os.path.join(outdir, name))
    files = run_kicad2ems(base + '.toml', outdir, solvers, extra, log)
    results = {}
    for name, path in files.items():
        f_em, s = read_touchstone(path)
        results[name] = (f_em, s[:, 1, 0], s[:, 0, 0])
    if not results:
        raise RuntimeError(f"kicad2ems wrote no S-parameters in {outdir}")
    s11, s21 = model_response(design, f_em)
    model = {'model (dfsynth)': (f_em, s21, s11)}
    if design.sub.t > 0 and design.spec.topology != 'freeform':
        # kicad2ems's solvers treat the copper as infinitely thin: this is the
        # model of what they solve (a free-form design's own model is that
        # already)
        s11, s21 = model_response(design, f_em, sub=design.sub.with_(t=0.0))
        model[MODEL0] = (f_em, s21, s11)
    results = {**model, **results}
    return ({
        name: metrics(design, f, s21, s11)
        for name, (f, s21, s11) in results.items()
    }, results)


MODEL0 = 'model (t=0)'


def predicted(mets, fw):
    """(centre %, bandwidth %) error vs the spec expected of the real board: the
    line model's own error (with the copper thickness) times the full-wave
    result's ratio over the zero-thickness model (the model of what the solver
    sees: the part of the error the line model does not explain)."""
    e, r = (mets[fw]['edges_GHz'], (mets.get(MODEL0)
                                    or mets['model (dfsynth)'])['edges_GHz'])
    rc = rb = None
    if 'fc' in e:
        rc = e['fc'] / r['fc'] if e['fc'] and r['fc'] else None
    elif all((e['f1'], e['f2'], r['f1'], r['f2'])):
        rc, rb = (math.sqrt(e['f1'] * e['f2'] /
                            (r['f1'] * r['f2'])), (e['f2'] - e['f1']) /
                  (r['f2'] - r['f1']))
    m = mets['model (dfsynth)']
    if rc is None or m['centre_error_pct'] is None:
        return None
    c = 100 * ((1 + m['centre_error_pct'] / 100) * rc - 1)
    b = None
    if rb is not None and m['bandwidth_error_pct'] is not None:
        b = 100 * ((1 + m['bandwidth_error_pct'] / 100) * rb - 1)
    return c, b
