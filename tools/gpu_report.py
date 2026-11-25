#!/usr/bin/env python3
"""
The GPU paths' pre-release report (R-C7): CI has no GPU (gfdtd's Vulkan kernels crash llvmpipe, R-C7.2), so on a
machine with them, before a release, this runs what CI cannot and commits what it found:

    python tools/gpu_report.py             # the tests and the boards; writes gpu-report/<date>.md and latest.json
    python tools/gpu_report.py --check     # is gpu-report/latest.json for the GPU parts' code as it is now?

It records the GPUs and drivers (emsdev), the repository's commit and the last commit of each part with GPU code,
the GPU tests of gfdtd, mom25d, pcbqs, emsdev and the FDTD layer (passed, skipped with why, failed), and on two
example boards each GPU backend against the CPU: the largest |dS| between them and the wall times. A GPU test
skipped for want of its backend is said, not hidden.
"""
import argparse, datetime, glob, json, os, platform, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'gpu-report')
# the parts the report vouches for: a commit that touches one of them makes the report out of date
GPU_PARTS = ['gfdtd', 'mom25d', 'pcbqs', 'emsdev', 'fdtd']
TESTS = [                                                                   # (part, pytest arguments)
    ('gfdtd', ['tests/gfdtd/test_gfdtd.py', '-k', 'gpu or vulkan or cupy']),
    ('mom25d', ['tests/mom25d/test_gpu.py']),
    ('pcbqs', ['tests/pcbqs/test_static_backends.py']),
    ('emsdev', ['tests/emsdev', '-k', 'vulkan or gpu or cupy']),
    ('fdtd', ['tests/fdtd', '-k', 'gpu']),
]
BOARDS = ['demo_lowpass', 'rf_coupler']
RUNS = [                                                                    # (name, solver, environment)
    ('gfdtd Vulkan', 'gfdtd', {'GFDTD_BACKEND': 'vulkan'}),
    ('gfdtd CPU', 'gfdtd', {'GFDTD_BACKEND': 'cpu'}),
    ('mom25d Vulkan', 'mom25d', {'MOM25D_GPU': 'vulkan'}),
    ('mom25d CuPy', 'mom25d', {'MOM25D_GPU': 'cupy'}),
    ('mom25d CPU', 'mom25d', {'MOM25D_GPU': 'off'}),
]


def commits():
    """The repository's HEAD, and each GPU part's last commit (src/kicad2ems/<part> and tests/<part>)."""
    out = {'repository': _git()}
    for part in GPU_PARTS:
        out[part] = _git(f'src/kicad2ems/{part}', f'tests/{part}')
    return out


def _git(*paths):
    try:
        args = ['git', '-C', ROOT, 'log', '-1', '--format=%h', 'HEAD', '--', *paths]
        h = subprocess.run(args, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', ROOT, 'status', '--porcelain', '--untracked-files=no', '--', *paths],
                               capture_output=True, text=True).stdout.strip()
        return (h + ('+dirty' if dirty else '')) or None
    except OSError:
        return None


def machine():
    cpu = platform.processor() or ''
    try:
        with open('/proc/cpuinfo', encoding='utf-8') as fh:
            cpu = next((l.split(':', 1)[1].strip() for l in fh if l.startswith('model name')), cpu)
    except OSError:
        pass
    gpus = []
    try:
        from kicad2ems.emsdev import vulkan
        gpus = [dict(name=a.name, kind=a.kind, driver=a.driver, memory_GB=round(a.total / 1e9, 1)) for a in vulkan.adapters()]
    except Exception as e:                                                   # noqa: BLE001
        gpus = [dict(error=f"no Vulkan adapters: {e}")]
    cupy = None
    try:
        import cupy
        cupy = dict(version=cupy.__version__, devices=[cupy.cuda.runtime.getDeviceProperties(i)['name'].decode()
                                                      for i in range(cupy.cuda.runtime.getDeviceCount())])
    except Exception as e:                                                   # noqa: BLE001
        cupy = dict(error=f"{type(e).__name__}: {e}"[:200])
    return dict(os=f"{platform.system()} {platform.release()}", python=platform.python_version(), cpu=cpu,
                vulkan=gpus, cupy=cupy)


def run_tests():
    res = []
    for repo, args in TESTS:
        t0 = time.time()
        p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', *args], cwd=ROOT,
                           capture_output=True, text=True)
        text = p.stdout + p.stderr
        summary = next((l for l in reversed(text.splitlines()) if re.search(r'\d+ (passed|failed|skipped|error)', l)),
                       text.strip().splitlines()[-1] if text.strip() else '')
        count = lambda w: int(m.group(1)) if (m := re.search(rf'(\d+) {w}', summary)) else 0
        skips = sorted({re.sub(r'^SKIPPED \[\d+\] ', '', l).strip() for l in text.splitlines() if l.startswith('SKIPPED')})
        failed = [l.strip() for l in text.splitlines() if l.startswith('FAILED')]
        res.append(dict(repo=repo, args=' '.join(args), passed=count('passed'), failed=count('failed') + count('error'),
                        skipped=count('skipped'), deselected=count('deselected'), skips=skips, failures=failed,
                        seconds=round(time.time() - t0, 1)))
        print(f"  {repo}: {summary}", flush=True)
    return res


def run_boards(work):
    import numpy as np
    from kicad2ems.core.touchstone import read_touchstone
    ex = os.path.join(ROOT, 'examples')
    out = []
    for b in BOARDS:
        S = {}
        for name, solver, env in RUNS:
            o = os.path.join(work, b, name.replace(' ', '_'))
            os.makedirs(o, exist_ok=True)
            t0 = time.time()
            p = subprocess.run([sys.executable, '-m', 'kicad2ems', f'{b}.toml', '--solvers', solver,
                                '--mom-port', 'lumped', '--out', o, '--no-report', '--no-reuse'], cwd=ex,
                               env={**os.environ, **env}, capture_output=True, text=True)
            wall = time.time() - t0
            log = p.stdout + p.stderr
            ts = glob.glob(os.path.join(o, f'{b}_{solver}.s*p'))
            fell = 'falling back' in log or 'on the CPU instead' in log or 'running on the CPU' in log
            row = dict(board=b, run=name, rc=p.returncode, seconds=round(wall, 1), fallback=fell,
                       note=(log.strip().splitlines() or [''])[-1][:160] if p.returncode else None)
            if ts and p.returncode == 0:
                f, s = read_touchstone(ts[0])
                S[name] = (np.asarray(f), np.asarray(s))
            out.append(row)
            print(f"  {b} {name}: rc {p.returncode}, {wall:.1f} s" + (' (fell back)' if fell else ''), flush=True)
        for row in out:                                                     # each run against its solver's CPU run
            if row['board'] != b or row['run'] not in S:
                continue
            ref = f"{row['run'].split()[0]} CPU"
            if ref in S and ref != row['run']:
                (fa, sa), (fb, sb) = S[row['run']], S[ref]
                if sa.shape == sb.shape and np.allclose(fa, fb):
                    row['max_dS_vs_cpu'] = float(np.abs(sa - sb).max())
    return out


def write(rep):
    os.makedirs(OUT, exist_ok=True)
    day = rep['date'][:10]
    with open(os.path.join(OUT, 'latest.json'), 'w', encoding='utf-8') as fh:
        json.dump(rep, fh, indent=1)
    L = [f"# GPU report, {day}", '',
         'What CI cannot run (no GPU there), run before a release (`python tools/gpu_report.py`).', '',
         f"Machine: {rep['machine']['cpu']}, {rep['machine']['os']}, Python {rep['machine']['python']}", '']
    L += ['| GPU (Vulkan) | Driver | Memory |', '|---|---|---|']
    L += [f"| {g.get('name', g.get('error'))} | {g.get('driver', '')} | {g.get('memory_GB', '')} GB |"
          for g in rep['machine']['vulkan']]
    cu = rep['machine']['cupy']
    L += ['', f"CuPy: {cu.get('version')} on {', '.join(cu.get('devices', []))}" if 'version' in cu else
          f"CuPy: not usable here ({cu.get('error')}): its tests and runs are skipped", '']
    L += ['## Commits', '', '| Part | Last commit |', '|---|---|']
    L += [f"| {k} | {v} |" for k, v in rep['commits'].items()]
    L += ['', '## GPU tests', '', '| Part | Passed | Failed | Skipped | Time |', '|---|---|---|---|---|']
    L += [f"| {t['repo']} | {t['passed']} | {t['failed']} | {t['skipped']} | {t['seconds']} s |" for t in rep['tests']]
    for t in rep['tests']:
        for s in t['skips']:
            L.append(f"- {t['repo']} skipped: {s}")
        for f in t['failures']:
            L.append(f"- **{t['repo']} failed**: {f}")
    L += ['', '## The GPU backends against the CPU', '',
          '| Board | Run | Time | Largest \\|ΔS\\| against its CPU run | Note |', '|---|---|---|---|---|']
    for r in rep['boards']:
        d = r.get('max_dS_vs_cpu')
        note = ('fell back to the CPU' if r['fallback'] else '') + (f" exit {r['rc']}: {r['note']}" if r['rc'] else '')
        L.append(f"| {r['board']} | {r['run']} | {r['seconds']} s | {'' if d is None else f'{d:.1e}'} | {note} |")
    path = os.path.join(OUT, f'{day}.md')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    return path


def check(strict=False):
    """Whether gpu-report/latest.json is for the GPU parts' last commits. Returns the line it prints."""
    fn = os.path.join(OUT, 'latest.json')
    if not os.path.exists(fn):
        msg = "GPU report: none yet (python tools/gpu_report.py on a machine with the GPUs, before a release)"
    else:
        rep = json.load(open(fn, encoding='utf-8'))
        now = commits()
        old = [f"{r} {rep['commits'].get(r)} -> {now.get(r)}" for r in GPU_PARTS
               if (rep['commits'].get(r) or '').replace('+dirty', '') != (now.get(r) or '').replace('+dirty', '')]
        bad = sum(t['failed'] for t in rep.get('tests', []))
        msg = (f"GPU report {rep['date'][:10]}: " + ("for older commits (" + "; ".join(old) + "): run "
               "tools/gpu_report.py before a release" if old else "for the GPU parts' code as it is now")
               + (f"; {bad} GPU test(s) failed in it" if bad else ''))
    print(msg)
    if strict and ('older' in msg or 'none yet' in msg or 'failed' in msg):
        sys.exit(1)
    return msg


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--check', action='store_true', help="only say whether the report is for the GPU parts' code as it is now")
    ap.add_argument('--strict', action='store_true', help="with --check: exit 1 when it is not")
    ap.add_argument('--work', default=os.path.join(os.path.expanduser('~'), '.cache', 'kicad2ems-gpu-report'),
                    help="where the board runs go (not the repository)")
    ap.add_argument('--no-boards', action='store_true', help="the tests only")
    a = ap.parse_args()
    if a.check:
        check(a.strict)
        return
    rep = dict(date=datetime.datetime.now().isoformat(timespec='seconds'), commits=commits(), machine=machine())
    print("GPU tests:", flush=True)
    rep['tests'] = run_tests()
    rep['boards'] = [] if a.no_boards else (print("Boards:", flush=True) or run_boards(a.work))
    print("wrote", write(rep))


if __name__ == '__main__':
    main()
