#!/usr/bin/env python3
"""Every test suite, locally: one pytest run for each part's folder under tests/
(core, gui, fdtd, gfdtd, mom25d, pcbqs, fem, ipc, dfsynth, emsdev), each in its
own process (their conftests set up their own paths and fixtures). Linux, macOS
and Windows (tools/test_all.sh runs this).

    python tools/test_all.py                   every part
    python tools/test_all.py core mom25d       those parts only (--core: core
                                               alone)
    python tools/test_all.py --openems         also openEMS in the conformance
                                               test (slow;
                                               KICAD2EMS_CONFORMANCE_OPENEMS)
    python tools/test_all.py -- -x -k antenna  pytest arguments, to every suite

Python: $PYTHON, else the active venv's, else this checkout's .venv, else this
one. The runs never see your settings: a scratch config folder (XDG_CONFIG_HOME,
and APPDATA on Windows: the GUI's QSettings, site.toml), the GUI offscreen,
matplotlib without a display. Exit status: 1 when any suite fails."""
import os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = ('core', 'gui', 'fdtd', 'gfdtd', 'mom25d', 'pcbqs', 'fem', 'ipc',
         'dfsynth', 'emsdev')
WINDOWS = sys.platform == 'win32'


def venv_python(venv):
    return (os.path.join(venv, 'Scripts', 'python.exe')
            if WINDOWS else os.path.join(venv, 'bin', 'python'))


def pick_python():
    if os.environ.get('PYTHON'):
        return os.environ['PYTHON']
    if os.environ.get('VIRTUAL_ENV'):
        return venv_python(os.environ['VIRTUAL_ENV'])
    mine = venv_python(os.path.join(ROOT, '.venv'))
    return mine if os.path.isfile(mine) else sys.executable


def summary(log, code):
    lines = [
        l.replace('=', '').strip() for l in log.splitlines() if re.match(
            r'^(=+ )?\d+ (passed|failed|error|deselected|skipped)|.*no tests '
            r'ran',
            l)
    ]
    return lines[-1] if lines else f'exit {code}'


def run_suite(py, name, args, env, results):
    print(f"=== {name} (tests/{name})", flush=True)
    r = subprocess.run([
        py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        os.path.join('tests', name), *args
    ], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding='utf-8', errors='replace')
    print('\n'.join(r.stdout.splitlines()[-25:]), flush=True)
    results.append(f"{name:18s} {summary(r.stdout, r.returncode)}")
    return r.returncode in (0, 5)  # 5: no tests collected (a -k matching none)


def main(argv):
    parts, args = [], []
    if '--' in argv:
        i = argv.index('--')
        argv, args = argv[:i], argv[i + 1:]
    env = dict(os.environ)
    for a in argv:
        if a == '--core':
            parts.append('core')
        elif a == '--openems':
            env['KICAD2EMS_CONFORMANCE_OPENEMS'] = '1'
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        elif a in PARTS:
            parts.append(a)
        else:
            print(
                f"unknown option {a} (the parts: {', '.join(PARTS)}; pytest "
                "arguments go after --)",
                file=sys.stderr)
            return 2
    py = pick_python()
    if (subprocess.run([py, '-c', 'import kicad2ems, pytest'],
                       capture_output=True).returncode):
        print(
            f"{py} cannot import kicad2ems and pytest: install them (pip "
            "install -e '.[test]')",
            file=sys.stderr)
        return 2
    where = subprocess.run([
        py, '-c',
        "import kicad2ems, os; print(os.path.realpath(kicad2ems.__file__))"
    ], capture_output=True, text=True).stdout.strip()
    if (not os.path.normcase(where).startswith(
            os.path.normcase(os.path.realpath(ROOT)) + os.sep)):
        print(
            f"WARNING: {py} imports {where}, not this checkout (pip install -e "
            f"{ROOT})"
        )
    scratch = tempfile.mkdtemp(prefix='k2e-tests-')
    ok = True
    try:
        env.update(XDG_CONFIG_HOME=os.path.join(scratch, 'xdg'),
                   QT_QPA_PLATFORM='offscreen', MPLBACKEND='Agg')
        if WINDOWS:
            env['APPDATA'] = os.path.join(scratch, 'appdata')
        ver = subprocess.run([py, '-V'], capture_output=True,
                             text=True).stdout.strip()
        print(f"python: {py} ({ver})", flush=True)
        results = []
        for name in parts or PARTS:
            ok = run_suite(py, name, args, env, results) and ok
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("\n=== summary")
    print('\n'.join(results))
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
