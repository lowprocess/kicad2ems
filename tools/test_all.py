#!/usr/bin/env python3
"""Every test suite, locally: kicad2ems's own (on the stand-in solvers, tests/standins.py: it needs no solver
package), then kicad2ems-gui's and each solver package's from its checkout beside this one (kicad2ems-gui,
kicad2ems-fdtd, mom25d, gfdtd), which test the window and the kicad2ems plugins against this checkout. A package
without a checkout is skipped and said so. Linux, macOS and Windows (tools/test_all.sh runs this).

    python tools/test_all.py                    everything that is here
    python tools/test_all.py --core             kicad2ems's own suite only
    python tools/test_all.py --openems          also openEMS in the conformance test (slow; KICAD2EMS_CONFORMANCE_OPENEMS)
    python tools/test_all.py -- -x -k antenna   pytest arguments, to every suite

Python: $PYTHON, else the active venv's, else this checkout's .venv, else this one. Checkouts: KICAD2EMS_GUI_SRC,
KICAD2EMS_FDTD_SRC, MOM25D_SRC, GFDTD_SRC (default ../<name>). The runs never see your settings: a scratch config
folder (XDG_CONFIG_HOME, and APPDATA on Windows: the GUI's QSettings, site.toml), the GUI offscreen, matplotlib
without a display. Exit status: 1 when any suite fails."""
import os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITES = (('kicad2ems-gui', 'kicad2ems_gui', 'KICAD2EMS_GUI_SRC'), ('kicad2ems-fdtd', 'kicad2ems_fdtd', 'KICAD2EMS_FDTD_SRC'),
          ('mom25d', 'mom25d', 'MOM25D_SRC'), ('gfdtd', 'gfdtd', 'GFDTD_SRC'))
WINDOWS = sys.platform == 'win32'


def venv_python(venv):
    return os.path.join(venv, 'Scripts', 'python.exe') if WINDOWS else os.path.join(venv, 'bin', 'python')


def pick_python():
    if os.environ.get('PYTHON'):
        return os.environ['PYTHON']
    if os.environ.get('VIRTUAL_ENV'):
        return venv_python(os.environ['VIRTUAL_ENV'])
    mine = venv_python(os.path.join(ROOT, '.venv'))
    return mine if os.path.isfile(mine) else sys.executable


def summary(log, code):
    lines = [l.replace('=', '').strip() for l in log.splitlines()
             if re.match(r'^(=+ )?\d+ (passed|failed|error|deselected|skipped)|.*no tests ran', l)]
    return lines[-1] if lines else f'exit {code}'


def run_suite(py, name, folder, args, env, results):
    print(f"=== {name} ({folder})", flush=True)
    r = subprocess.run([py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *args], cwd=folder, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    print('\n'.join(r.stdout.splitlines()[-25:]), flush=True)
    results.append(f"{name:18s} {summary(r.stdout, r.returncode)}")
    return r.returncode in (0, 5)                               # 5: no tests collected (a -k matching none)


def main(argv):
    core_only, args = False, []
    if '--' in argv:
        i = argv.index('--')
        argv, args = argv[:i], argv[i + 1:]
    env = dict(os.environ)
    for a in argv:
        if a == '--core':
            core_only = True
        elif a == '--openems':
            env['KICAD2EMS_CONFORMANCE_OPENEMS'] = '1'
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        else:
            print(f"unknown option {a} (pytest arguments go after --)", file=sys.stderr)
            return 2
    py = pick_python()
    if subprocess.run([py, '-c', 'import kicad2ems, pytest'], capture_output=True).returncode:
        print(f"{py} cannot import kicad2ems and pytest: install them (pip install -e '.[test]')", file=sys.stderr)
        return 2
    scratch = tempfile.mkdtemp(prefix='k2e-tests-')
    try:
        env.update(XDG_CONFIG_HOME=os.path.join(scratch, 'xdg'), QT_QPA_PLATFORM='offscreen', MPLBACKEND='Agg',
                   KICAD2EMS_SRC=ROOT)
        if WINDOWS:
            env['APPDATA'] = os.path.join(scratch, 'appdata')
        ver = subprocess.run([py, '-V'], capture_output=True, text=True).stdout.strip()
        print(f"python: {py} ({ver})", flush=True)
        results = []
        ok = run_suite(py, 'kicad2ems', ROOT, args, env, results)
        if not core_only:
            for name, module, var in SUITES:
                folder = os.environ.get(var) or os.path.join(os.path.dirname(ROOT), name)
                if not os.path.isdir(os.path.join(folder, 'tests')):
                    results.append(f"{name:18s} skipped: no checkout at {folder} ({var})")
                    continue
                # their tests import the installed package: say so when that is not this checkout
                where = subprocess.run([py, '-c', f"import {module}, os; print(os.path.realpath({module}.__file__))"],
                                       capture_output=True, text=True).stdout.strip()
                if not where:
                    results.append(f"{name:18s} skipped: {module} is not installed in {py} (pip install -e {folder})")
                    continue
                real = os.path.normcase(os.path.realpath(folder)) + os.sep
                if not os.path.normcase(where).startswith(real):
                    print(f"WARNING: {name}: {py} imports {where}, not the checkout at {folder} (pip install -e {folder})")
                ok = run_suite(py, name, folder, args, env, results) and ok
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("\n=== summary")
    print('\n'.join(results))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
