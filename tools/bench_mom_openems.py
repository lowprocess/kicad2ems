"""
Runtime and agreement benchmark: openEMS (3D FDTD) against mom25d (2.5D MoM) on the example boards.

    python tools/bench_mom_openems.py [--boards filter,hairpin,...] [--out bench] [--mesher triangle]

Each board is one full kicad2ems run with --mom (the same converted geometry for both solvers), in
its own output folder. The table: model size (FDTD cells / MoM unknowns), wall time of each solver (MoM:
mesh + matrix setup + adaptive sweep), EM solves and timesteps, and the agreement (largest |dS| and
transmission |dB| difference, delay difference) from the run summary. Results also go to <out>/bench.json
and <out>/bench.md.
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
BOARDS = {
    'filter': ('filter.toml', None),
    'hairpin': ('hairpin.toml', None),
    'demo_transition': ('demo_transition.toml', ['demo_transition']),
    'demo_lowpass': ('demo_lowpass.toml', ['demo_lowpass', 'lowpass']),
    'sma_launch': ('sma_launch.toml', None),
}


def run_board(name, out, mesher, extra):
    toml, gen = BOARDS[name]
    if gen and not os.path.exists(os.path.join(EX, gen[0] + '.kicad_pcb')):
        subprocess.check_call([sys.executable, os.path.join(EX, 'make_demo_board.py'), os.path.join(EX, gen[0])] + gen[1:],
                              stdout=subprocess.DEVNULL)
    od = os.path.join(out, name)
    cmd = [sys.executable, '-m', 'kicad2ems', os.path.join(EX, toml), '--mom', '--mom-mesher', mesher, '--out', od,
           '--no-report'] + extra
    t0 = time.time()
    with open(os.path.join(out, f"{name}.log"), 'w') as log:
        code = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    wall = time.time() - t0
    sm_path = next((os.path.join(od, f) for f in os.listdir(od) if f.endswith('_run.json')), None) if os.path.isdir(od) else None
    if code or not sm_path:
        return dict(board=name, error=f"exit {code}, see {name}.log", wall_s=wall)
    sm = json.load(open(sm_path))
    runs, mo = sm.get('runs', {}), sm['solved'].get('mom25d', {})          # compared with openEMS, the first solver
    cmp_ = mo.get('compare', [])
    tx = [r for r in cmp_ if r['max_ddb'] is not None]
    return dict(board=name, wall_s=wall, cells=sm['mesh']['cells'], dt_ps=sm['mesh']['dt_ps'],
                openems_s=sum(r.get('seconds') or 0 for r in runs.values()), excitations=len(runs),
                timesteps=sum(r.get('steps') or 0 for r in runs.values()),
                mom_s=mo.get('seconds'), mom_unknowns=mo.get('unknowns'), mom_solves=mo.get('solves'),
                mom_ports=",".join(f"{p['name']}:{p['kind']}" for p in mo.get('ports', [])),
                max_dS=max((r['max_dvec'] for r in cmp_), default=None),
                max_ddb_tx=max((r['max_ddb'] for r in tx), default=None),
                ddelay_ps=max((abs(r['ddelay_ps']) for r in tx if r['ddelay_ps'] is not None), default=None),
                mom_error=None if mo else 'no MoM result (see the log)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--boards', default=",".join(BOARDS))
    ap.add_argument('--out', default=os.path.join(ROOT, 'bench'))
    ap.add_argument('--mesher', default='triangle')
    ap.add_argument('extra', nargs='*', help='more kicad2ems options (after --)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res = []
    for b in a.boards.split(','):
        print(f"{b} ...", flush=True)
        res.append(run_board(b, a.out, a.mesher, a.extra))
        print("  " + json.dumps(res[-1]), flush=True)
        json.dump(res, open(os.path.join(a.out, 'bench.json'), 'w'), indent=1)
    f = lambda v, fmt: '–' if v is None else format(v, fmt)
    lines = ["| board | FDTD cells | openEMS (s) | runs × steps | MoM unknowns | MoM (s) | EM solves | MoM ports | "
             "speed-up | max \\|ΔS\\| | max Δ dB (S21) | Δ delay (ps) |", "|" + "---|" * 12]
    for r in res:
        if 'error' in r:
            lines.append(f"| {r['board']} | {r['error']} |" + " |" * 10)
            continue
        sp = r['openems_s'] / r['mom_s'] if r.get('mom_s') else None
        lines.append(f"| {r['board']} | {r['cells'] / 1e6:.2f} M | {r['openems_s']:.0f} | {r['excitations']} × "
                     f"{r['timesteps'] // max(r['excitations'], 1):,} | {f(r['mom_unknowns'], ',')} | {f(r['mom_s'], '.0f')} | "
                     f"{f(r['mom_solves'], 'd')} | {r['mom_ports']} | {f(sp, '.1f')}× | {f(r['max_dS'], '.3f')} | "
                     f"{f(r['max_ddb_tx'], '.2f')} | {f(r['ddelay_ps'], '.1f')} |")
    md = "\n".join(lines)
    open(os.path.join(a.out, 'bench.md'), 'w').write(md + "\n")
    print(md)


if __name__ == '__main__':
    main()
