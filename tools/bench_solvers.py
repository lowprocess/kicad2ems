#!/usr/bin/env python3
"""
Run time and peak memory of every solver on every example config, each solver in a process of its own:
openEMS, gfdtd on the GPU and on the CPU, mom25d with the GPU, mom25d on the CPU only (MOM25D_GPU=off), mom25d
fast (quads on a coarsened mesh) and the line model.

    python tools/bench_solvers.py --openems-python /opt/openems/venv/bin/python     # the critical boards (CRITICAL)
    python tools/bench_solvers.py --all                                             # every example config
    python tools/bench_solvers.py --only filter hairpin --skip openems
    python tools/bench_solvers.py --plot-only --only filter hairpin     # the graph of what was measured

Writes build/bench/results.json, results.md and results.png (time and memory per solver, per example; the median of
--repeat runs, with their range). Run it alone: the timings are wall time, and other work on
the machine slows them. A solver that cannot model a board (mom25d and a gap-fed antenna) is listed as such.
"""
import argparse, glob, json, os, re, subprocess, sys, time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'build', 'bench')
GENERATED = {                                       # config -> the command that writes its board
    'demo_transition': ['examples/make_demo_board.py', 'examples/demo_transition'],
    'demo_lowpass': ['examples/make_demo_board.py', 'examples/demo_lowpass', 'lowpass'],
    'demo_highpass': ['examples/make_demo_board.py', 'examples/demo_highpass', 'highpass'],
    'demo_bandpass': ['examples/make_demo_board.py', 'examples/demo_bandpass', 'bandpass'],
}
for kind in ('patch', 'ifa', 'dipole', 'slot', 'array2x2'):
    GENERATED[f'antenna_{kind}'] = ['examples/make_antennas.py', kind, f'examples/antenna_{kind}']


# one board per regime: a resonant single-layer filter, a narrow coupled gap, via fences in pours, inner planes
# meshed (mom25d's slow case), a via transition through two inner planes, an antenna
CRITICAL = ['hairpin', 'rf_coupler', 'rf_gcpw', 'rf_stripline', 'demo_transition', 'antenna_patch']


def solvers(openems_py):
    me = sys.executable
    return {'openems': (openems_py, ['--solvers', 'openems'], {}),
            'gfdtd GPU': (me, ['--solvers', 'gfdtd', '--gfdtd-backend', 'gpu'], {}),
            'gfdtd CPU': (me, ['--solvers', 'gfdtd', '--gfdtd-backend', 'cpu'], {}),
            'mom25d GPU': (me, ['--solvers', 'mom25d'], {}),
            'mom25d CPU': (me, ['--solvers', 'mom25d'], {'MOM25D_GPU': 'off'}),
            # quad cells at 60 degrees on a coarsened, structured mesh (docs/mom.md#coarsen), the GPU where it pays
            'mom25d fast': (me, ['--solvers', 'mom25d', '--mom-quads', 'on', '--mom-quad-tol', '60',
                                 '--mom-coarsen', '2'], {}),
            'line model': (me, ['--solvers', 'lines'], {})}


def parse(log, name):
    """What the log says: openEMS cells and timesteps; mom25d unknowns, solves, solver path, GPU memory."""
    info = {}
    m = re.search(r'mesh: \d+ x \d+ x \d+ = ([0-9.]+) M cells', log)
    if m and name == 'openems':
        info['cells'] = f"{m.group(1)} M"
    m = re.search(r'gfdtd: ([0-9.]+) M cells', log)
    if m and name.startswith('gfdtd'):
        info['cells'] = f"{m.group(1)} M"
    steps = re.findall(r'done in [0-9.]+ s, ~(\d+) timesteps', log)
    if steps:
        info['steps'] = sum(int(s) for s in steps)
    m = re.search(r'mom25d: ([0-9,]+) unknowns', log)
    if m:
        info['unknowns'] = m.group(1)
    m = re.search(r'mom25d: (\d+) EM solves', log)
    if m:
        info['solves'] = int(m.group(1))
    m = re.search(r'mom25d: factorisations: (\d+) on the GPU, (\d+) on the CPU', log)
    if m:                                         # where the solves ran: a GPU run that fell back is marked
        info['lu_gpu'], info['lu_cpu'] = int(m.group(1)), int(m.group(2))
    m = re.search(r'memory: direct solver needs ~[0-9.]+ GB \(\w+ precision, ([\w-]+)(?:, ([0-9.]+) GB on the GPU)?', log)
    if m:
        info['path'] = m.group(1)
        if m.group(2):
            info['gpu_gb'] = float(m.group(2))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--openems-python', default=sys.executable)
    ap.add_argument('--only', nargs='*', help="these configs (names without .toml)")
    ap.add_argument('--all', action='store_true', help="every example config, not just the critical boards")
    ap.add_argument('--repeat', type=int, default=3, help="runs of each solver on each board: the median is reported, "
                    "with the range (default 3)")
    ap.add_argument('--skip', nargs='*', default=[], help="solvers to leave out: openems, 'mom25d GPU', ...")
    ap.add_argument('--plot-only', action='store_true', help="no runs: the graph (and table) of the results so far")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.plot_only:
        results = json.load(open(os.path.join(OUT, 'results.json')))
        write_md(results)
        print('wrote', plot(results, a.only))
        summary(results, [c for c in sorted(results) if not a.only or c in a.only])
        return
    env0 = dict(os.environ, PYTHONPATH=os.path.join(REPO, 'src') + os.pathsep + os.environ.get('PYTHONPATH', ''))
    configs = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(REPO, 'examples', '*.toml')))
    configs = [c for c in configs if (c in a.only if a.only else a.all or c in CRITICAL)]
    path = os.path.join(OUT, 'results.json')
    results = json.load(open(path)) if os.path.exists(path) else {}
    for cfg in configs:
        if cfg in GENERATED and not glob.glob(os.path.join(REPO, 'examples', cfg + '*.kicad_pcb')):
            subprocess.run([sys.executable] + GENERATED[cfg], cwd=REPO, env=env0, capture_output=True)
        for name, (py, args, env) in solvers(a.openems_python).items():
            if name in a.skip:
                continue
            out = os.path.join(OUT, f"{cfg}_{name.replace(' ', '_')}")
            cmd = ['/usr/bin/time', '-f', 'TIME %e %M', py, '-m', 'kicad2ems', os.path.join(REPO, 'examples', cfg + '.toml'),
                   '--no-report', '--out', out] + args
            runs, peaks, lus = [], [], []
            for _ in range(a.repeat):                              # the same run again: how much the times vary
                t = time.time()
                p = subprocess.run(cmd, cwd=REPO, env=dict(env0, **env), capture_output=True, text=True)
                log = p.stdout + p.stderr
                os.makedirs(out, exist_ok=True)
                open(os.path.join(out, f'log{len(runs) + 1}.txt'), 'w').write(log)
                m = re.search(r'TIME ([0-9.]+) (\d+)', log)
                runs.append(float(m.group(1)) if m else time.time() - t)
                lus.append(parse(log, name))
                peaks.append(int(m.group(2)) / 1e6 if m else None)
                print(f"  {cfg:16s} {name:11s} run {len(runs)}/{a.repeat}: {runs[-1]:7.1f} s  "       # as each completes
                      f"{peaks[-1] or 0:5.2f} GB{'  FAILED' if p.returncode else ''}", flush=True)
                if p.returncode:
                    break
            row = dict(code=p.returncode, seconds=float(np.median(runs)), runs=runs,
                       peak_gb=max((g for g in peaks if g is not None), default=None), **parse(log, name))
            if any('lu_cpu' in i for i in lus):          # the worst run: the most factorisations on the CPU
                row['lu_cpu'] = max(i.get('lu_cpu', 0) for i in lus)
                row['lu_gpu'] = min(i.get('lu_gpu', 0) for i in lus)
            if p.returncode:
                row['error'] = next((l.strip() for l in reversed(log.splitlines()) if l.strip() and not l.startswith('TIME')), '')[-160:]
            results.setdefault(cfg, {})[name] = row                # ('suspect': a note added by hand, plotted)
            json.dump(results, open(path, 'w'), indent=1)
            spread = f" ({min(runs):.1f}-{max(runs):.1f})" if len(runs) > 1 else ''
            print(f"{cfg:16s} {name:11s} median {row['seconds']:7.1f} s{spread}, peak {row['peak_gb'] or 0:5.2f} GB"
                  f"{_where(name, row).replace('*', '')} "
                  f"{'' if not p.returncode else 'FAILED: ' + row.get('error', '')}", flush=True)
    write_md(results)
    print('wrote', plot(results, configs))
    summary(results, configs)


def summary(results, configs):
    """The run's results as a text table: per board, each solver's median time and peak RAM, the fastest field
    solver marked *."""
    names = [n for n in NAMES if any(n in results.get(c, {}) for c in configs)]
    w = max(len(c) for c in configs) if configs else 10
    print('\nSummary: median wall time / peak RAM of the runs (* the fastest field solver; n/a: cannot model it)')
    print(f"{'':{w}s}  " + '  '.join(f"{n:>17s}" for n in names))
    for c in configs:
        rows = results.get(c, {})
        ok = {n: r['seconds'] for n, r in rows.items() if n in names and n != 'line model' and not r['code']}
        best = min(ok, key=ok.get) if ok else None
        cells = []
        for n in names:
            r = rows.get(n)
            cell = '' if r is None else 'n/a' if r['code'] else \
                f"{r['seconds']:.0f} s / {r['peak_gb']:.1f} GB" + ('*' if n == best else '') + (' c' if _where(n, r) else '')
            cells.append(f"{cell:>17s}")
        print(f"{c:{w}s}  " + '  '.join(cells))
    print("(c: a mom25d GPU run that factored on the CPU, all or some of its solves)")


NAMES = ['openems', 'gfdtd GPU', 'gfdtd CPU', 'mom25d GPU', 'mom25d CPU', 'mom25d fast', 'line model']
COLOURS = {'openems': '#1f77b4', 'gfdtd GPU': '#d62728', 'gfdtd CPU': '#ff9896', 'mom25d GPU': '#2ca02c',
           'mom25d CPU': '#98df8a', 'mom25d fast': '#9467bd', 'line model': '#7f7f7f'}


def plot(results, configs=None, path=os.path.join(OUT, 'results.png')):
    """Run time and peak RAM of each solver on each example: grouped bars, n/a where a solver
    can't model the board."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    cfgs = [c for c in sorted(results) if not configs or c in configs]
    names = [n for n in NAMES if any(n in results[c] for c in cfgs)]
    fig, (ax_t, ax_m) = plt.subplots(2, 1, figsize=(max(9, 1.5 * len(cfgs)), 9), sharex=True)
    x = np.arange(len(cfgs))
    w = 0.8 / len(names)
    t_cap, tmax = _cut(results, cfgs, 'seconds')
    cap, _ = _cut(results, cfgs, 'peak_gb')
    t_top = t_cap or 1.1 * tmax
    any_cpu = False
    for i, n in enumerate(names):
        xs = x - 0.4 + w * (i + 0.5)
        for j, c in enumerate(cfgs):
            r = results[c].get(n)
            if r is None:
                continue
            if r['code']:
                ax_t.text(xs[j], t_top * 0.01, 'n/a', rotation=90, ha='center', va='bottom', fontsize=7, color='#888')
                continue
            odd = bool(r.get('suspect'))                                 # a result not to take at face value
            cpu = bool(_where(n, r))                                     # a GPU run that (partly) ran on the CPU
            t = r['seconds']
            ax_t.bar(xs[j], min(t, t_cap) if t_cap else t, w, color=COLOURS[n],
                     hatch='///' if odd else '...' if cpu else None, edgecolor='k' if odd or cpu else None,
                     label=n if n not in ax_t.get_legend_handles_labels()[1] else None)
            any_cpu = any_cpu or cpu
            if t_cap and t > t_cap:
                _cut_off(ax_t, xs[j], w, t_cap, f"{t:.0f} s" + ('*' if odd else ''))
            else:
                ax_t.text(xs[j], t + t_top * 0.01, f"{t:.0f}" + ('*' if odd else ''), ha='center', va='bottom',
                          fontsize=6, rotation=90)
            if r.get('peak_gb') is not None:
                gb = r['peak_gb']
                ax_m.bar(xs[j], min(gb, cap) if cap else gb, w, color=COLOURS[n])
                if cap and gb > cap:
                    _cut_off(ax_m, xs[j], w, cap, f"{gb:.1f} GB")
    for j, c in enumerate(cfgs):                                     # the fastest field solver: a ring on its bar
        ok = [(r['seconds'], i) for i, n in enumerate(names) if n != 'line model'
              for r in [results[c].get(n)] if r and not r['code']]
        if ok:
            t, i = min(ok)
            ax_t.plot(x[j] - 0.4 + w * (i + 0.5), -0.03 * t_top, marker='^', ms=6, color='k', clip_on=False)
    ax_t.set_ylim(0, t_top)
    ax_t.set_ylabel('wall time (s)' + (', the tallest cut off' if t_cap else '') + '; ▲ the fastest field solver')
    ax_t.set_title('kicad2ems solvers: run time and peak memory per example', pad=24)
    ax_t.grid(axis='y', alpha=0.25)
    h, l = ax_t.get_legend_handles_labels()
    order = sorted(range(len(l)), key=lambda k: NAMES.index(l[k]))
    ax_t.legend([h[k] for k in order], [l[k] for k in order], ncol=len(names), fontsize=8, loc='lower center',
                bbox_to_anchor=(0.5, 1.0), frameon=False)
    ax_m.set_ylabel('peak RAM (GB)' + (', the tallest cut off' if cap else ''))
    if cap:
        ax_m.set_ylim(0, cap)
    ax_m.grid(axis='y', alpha=0.25)
    ax_m.set_xticks(x, cfgs, rotation=30, ha='right')
    odd = {}                                                         # note -> the bars it is about
    for c in cfgs:
        for n in names:
            if results[c].get(n, {}).get('suspect'):
                odd.setdefault((c, results[c][n]['suspect']), []).append(n)
    notes = [f"{', '.join(ns)} on {c}: {note}" for (c, note), ns in sorted(odd.items())]
    if any_cpu:
        notes.append('dotted: a mom25d GPU run that factored on the CPU, all or some of its solves (results.md)')
    if notes:
        fig.text(0.01, 0.005, '* hatched: ' + '; '.join(notes), fontsize=7, va='bottom')
    fig.tight_layout(rect=(0, 0.03 if notes else 0, 1, 1))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _cut(results, cfgs, key):
    """(where to cut the axis, the largest value): an example far above the rest (mom25d on a board with ground
    planes) is cut off at the top and its value written instead, so the others stay readable; None: no cut."""
    top = sorted(max([r[key] for r in results[c].values() if r.get(key) is not None and not r['code']] or [0])
                 for c in cfgs)
    return (1.25 * top[-2] if len(top) > 1 and top[-1] > 2.5 * top[-2] else None), top[-1]


def _cut_off(ax, x, w, cap, label):
    """A bar cut off at the top of the axis: a break across it and its value written on it."""
    ax.text(x, cap * 0.97, label, rotation=90, ha='center', va='top', fontsize=7, color='white', fontweight='bold')
    ax.plot([x - w / 2, x + w / 2], [cap * 0.985, cap * 1.015], color='white', lw=2.5, clip_on=False)


def write_md(results):
    names = NAMES
    L = ['| Example | ' + ' | '.join(names) + ' | Fastest | FDTD model | mom25d model | mom25d fast model |',
         '|---' * (len(names) + 5) + '|']
    for cfg, rows in sorted(results.items()):
        # the fastest field solver in bold (the line model, a circuit model at ~1 s everywhere, is not one)
        ok = {n: r['seconds'] for n, r in rows.items() if n in names and n != 'line model' and not r['code']}
        best = min(ok, key=ok.get) if ok else None
        cells = []
        for n in names:
            r = rows.get(n)
            if r is None:
                cells.append('')
            elif r['code']:
                cells.append('n/a')
            else:
                gpu = f" (+{r['gpu_gb']:.1f} GPU)" if n in ('mom25d GPU', 'mom25d fast') and r.get('gpu_gb') else ''
                rr = r.get('runs') or [r['seconds']]
                span = f" ({min(rr):.0f}–{max(rr):.0f})" if len(rr) > 1 and round(min(rr)) != round(max(rr)) else ''
                cell = f"{r['seconds']:.0f} s{span}, {r['peak_gb']:.1f} GB{gpu}"
                cell = f"**{cell}**" if n == best else cell
                cells.append(cell + _where(n, r))
        cells.append(best or '')
        o, mo = rows.get('openems') or rows.get('gfdtd GPU') or {}, rows.get('mom25d GPU', {})
        cells.append(f"{o.get('cells', '')} cells, {o.get('steps', '')} steps" if o.get('cells') else '')
        for mo in (mo, rows.get('mom25d fast', {})):
            cells.append(f"{mo.get('unknowns', '')} unknowns, {mo.get('solves', '')} solves, {mo.get('path', '')}"
                         if mo.get('unknowns') else '')
        L.append(f"| {cfg} | " + ' | '.join(cells) + ' |')
    L += ['', '*on the CPU*: a GPU run of mom25d that factored on the CPU (the board below its GPU threshold, '
          'sim.GPU_MIN_N, or too large for the GPU); *n of m on the CPU*: solves that fell back to double precision on '
          'the CPU after the complex64 LU failed to precondition (the worst of the runs).']
    open(os.path.join(OUT, 'results.md'), 'w').write('\n'.join(L) + '\n')


def _where(n, r):
    """A GPU run of mom25d that did not (all) run on the GPU, marked: ' · on the CPU' or ' · n of m on the CPU'."""
    if n not in ('mom25d GPU', 'mom25d fast') or 'lu_cpu' not in r:
        return ''
    if not r.get('lu_gpu'):
        return ' · *on the CPU*'
    return f" · *{r['lu_cpu']} of {r['lu_gpu'] + r['lu_cpu']} on the CPU*" if r['lu_cpu'] else ''


if __name__ == '__main__':
    main()
