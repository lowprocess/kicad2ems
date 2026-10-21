#!/usr/bin/env python3
"""
Run time and peak memory of every solver on every example config, each solver in a process of its own:
openEMS, gfdtd on the GPU and on the CPU, mom25d with the GPU, mom25d on the CPU only (MOM25D_GPU=off) and
the line model.

    python tools/bench_solvers.py --openems-python /opt/openems/venv/bin/python
    python tools/bench_solvers.py --only filter hairpin --skip openems
    python tools/bench_solvers.py --plot-only --only filter hairpin     # the graph of what was measured

Writes build/bench/results.json, results.md and results.png (time and memory per solver, per example). Run it alone: the timings are wall time, and other work on
the machine slows them. A solver that cannot model a board (mom25d and a gap-fed antenna) is listed as such.
"""
import argparse, glob, json, os, re, subprocess, sys, time

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


def solvers(openems_py):
    me = sys.executable
    return {'openems': (openems_py, ['--solvers', 'openems'], {}),
            'gfdtd GPU': (me, ['--solvers', 'gfdtd', '--gfdtd-backend', 'gpu'], {}),
            'gfdtd CPU': (me, ['--solvers', 'gfdtd', '--gfdtd-backend', 'cpu'], {}),
            'mom25d GPU': (me, ['--solvers', 'mom25d'], {}),
            'mom25d CPU': (me, ['--solvers', 'mom25d'], {'MOM25D_GPU': 'off'}),
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
    ap.add_argument('--skip', nargs='*', default=[], help="solvers to leave out: openems, 'mom25d GPU', ...")
    ap.add_argument('--plot-only', action='store_true', help="no runs: the graph (and table) of the results so far")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.plot_only:
        results = json.load(open(os.path.join(OUT, 'results.json')))
        write_md(results)
        print('wrote', plot(results, a.only))
        return
    env0 = dict(os.environ, PYTHONPATH=REPO + os.pathsep + os.environ.get('PYTHONPATH', ''))
    configs = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(REPO, 'examples', '*.toml')))
    configs = [c for c in configs if not a.only or c in a.only]
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
            t = time.time()
            p = subprocess.run(cmd, cwd=REPO, env=dict(env0, **env), capture_output=True, text=True)
            log = p.stdout + p.stderr
            m = re.search(r'TIME ([0-9.]+) (\d+)', log)
            row = dict(code=p.returncode, seconds=float(m.group(1)) if m else time.time() - t,
                       peak_gb=int(m.group(2)) / 1e6 if m else None, **parse(log, name))
            if p.returncode:
                row['error'] = next((l.strip() for l in reversed(log.splitlines()) if l.strip() and not l.startswith('TIME')), '')[-160:]
            results.setdefault(cfg, {})[name] = row
            json.dump(results, open(path, 'w'), indent=1)
            print(f"{cfg:22s} {name:11s} {row['seconds']:7.1f} s {row['peak_gb'] or 0:5.2f} GB "
                  f"{'' if not p.returncode else 'FAILED: ' + row.get('error', '')}", flush=True)
    write_md(results)
    print('wrote', plot(results, configs))


NAMES = ['openems', 'gfdtd GPU', 'gfdtd CPU', 'mom25d GPU', 'mom25d CPU', 'line model']
COLOURS = {'openems': '#1f77b4', 'gfdtd GPU': '#d62728', 'gfdtd CPU': '#ff9896', 'mom25d GPU': '#2ca02c',
           'mom25d CPU': '#98df8a', 'line model': '#7f7f7f'}


def plot(results, configs=None, path=os.path.join(OUT, 'results.png')):
    """Run time (log scale) and peak RAM of each solver on each example: grouped bars, n/a where a solver
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
    for i, n in enumerate(names):
        xs = x - 0.4 + w * (i + 0.5)
        for j, c in enumerate(cfgs):
            r = results[c].get(n)
            if r is None:
                continue
            if r['code']:
                ax_t.text(xs[j], 0.35, 'n/a', rotation=90, ha='center', va='bottom', fontsize=7, color='#888')
                continue
            ax_t.bar(xs[j], r['seconds'], w, color=COLOURS[n], label=n if j == 0 or n not in
                     ax_t.get_legend_handles_labels()[1] else None)
            ax_t.text(xs[j], r['seconds'] * 1.08, f"{r['seconds']:.0f}", ha='center', va='bottom', fontsize=6,
                      rotation=90)
            if r.get('peak_gb') is not None:
                ax_m.bar(xs[j], r['peak_gb'], w, color=COLOURS[n])
    ax_t.set_yscale('log')
    ax_t.set_ylim(0.3, 4 * max(r['seconds'] for c in cfgs for r in results[c].values() if not r['code']))
    ax_t.set_ylabel('wall time (s, log scale)')
    ax_t.set_title('kicad2ems solvers: run time and peak memory per example', pad=24)
    ax_t.grid(axis='y', which='both', alpha=0.25)
    h, l = ax_t.get_legend_handles_labels()
    order = sorted(range(len(l)), key=lambda k: NAMES.index(l[k]))
    ax_t.legend([h[k] for k in order], [l[k] for k in order], ncol=len(names), fontsize=8, loc='lower center',
                bbox_to_anchor=(0.5, 1.0), frameon=False)
    ax_m.set_ylabel('peak RAM (GB)')
    ax_m.grid(axis='y', alpha=0.25)
    ax_m.set_xticks(x, cfgs, rotation=30, ha='right')
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def write_md(results):
    names = NAMES
    L = ['| Example | ' + ' | '.join(names) + ' | FDTD model | mom25d model |', '|---' * (len(names) + 3) + '|']
    for cfg, rows in sorted(results.items()):
        cells = []
        for n in names:
            r = rows.get(n)
            if r is None:
                cells.append('')
            elif r['code']:
                cells.append('n/a')
            else:
                gpu = f" (+{r['gpu_gb']:.1f} GPU)" if n == 'mom25d GPU' and r.get('gpu_gb') else ''
                cells.append(f"{r['seconds']:.0f} s, {r['peak_gb']:.1f} GB{gpu}")
        o, mo = rows.get('openems') or rows.get('gfdtd GPU') or {}, rows.get('mom25d GPU', {})
        cells.append(f"{o.get('cells', '')} cells, {o.get('steps', '')} steps" if o.get('cells') else '')
        cells.append(f"{mo.get('unknowns', '')} unknowns, {mo.get('solves', '')} solves, {mo.get('path', '')}" if mo.get('unknowns') else '')
        L.append(f"| {cfg} | " + ' | '.join(cells) + ' |')
    open(os.path.join(OUT, 'results.md'), 'w').write('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
