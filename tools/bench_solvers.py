#!/usr/bin/env python3
"""
Run time and peak memory of every solver on every example config, each solver in a process of its own:
openEMS, mom25d with the GPU, mom25d on the CPU only (MOM25D_GPU=off) and the line model.

    python tools/bench_solvers.py --openems-python /opt/openems/venv/bin/python
    python tools/bench_solvers.py --only filter hairpin --skip openems

Writes build/bench/results.json and results.md. Run it alone: the timings are wall time, and other work on
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
            'mom25d GPU': (me, ['--solvers', 'mom25d'], {}),
            'mom25d CPU': (me, ['--solvers', 'mom25d'], {'MOM25D_GPU': 'off'}),
            'line model': (me, ['--solvers', 'lines'], {})}


def parse(log, name):
    """What the log says: openEMS cells and timesteps; mom25d unknowns, solves, solver path, GPU memory."""
    info = {}
    m = re.search(r'mesh: \d+ x \d+ x \d+ = ([0-9.]+) M cells', log)
    if m and name == 'openems':
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
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
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


def write_md(results):
    names = ['openems', 'mom25d GPU', 'mom25d CPU', 'line model']
    L = ['| Example | ' + ' | '.join(names) + ' | openEMS model | mom25d model |', '|---' * (len(names) + 3) + '|']
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
        o, mo = rows.get('openems', {}), rows.get('mom25d GPU', {})
        cells.append(f"{o.get('cells', '')} cells, {o.get('steps', '')} steps" if o.get('cells') else '')
        cells.append(f"{mo.get('unknowns', '')} unknowns, {mo.get('solves', '')} solves, {mo.get('path', '')}" if mo.get('unknowns') else '')
        L.append(f"| {cfg} | " + ' | '.join(cells) + ' |')
    open(os.path.join(OUT, 'results.md'), 'w').write('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
