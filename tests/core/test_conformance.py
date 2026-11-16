"""Conformance: boards with a known answer, each capable solver within stated
tolerances of it, with fast settings. The lossless microstrip thru
(make_demo_board.py thru): |S21| ~ 1, |S11| small, and the delay of a line of
Hammerstad-Jensen's effective permittivity (mixed: with the middle of the line
cut out and put back as a line). openEMS runs when
KICAD2EMS_CONFORMANCE_OPENEMS=1 (slow: for a nightly run, not every test run).

The one test of kicad2ems's that runs the real solver packages (the others run
on the stand-ins, standins.py): each run is `python -m kicad2ems` in a process
of its own, and a solver not installed, or not usable here, is skipped."""
import math, os, subprocess, sys
import numpy as np
import pytest
from kicad2ems.core.touchstone import read_touchstone

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'examples'))
from make_demo_board import THRU                                    # noqa: E402

C0 = 299792458.0
# converges in 13
FAST = {
    'scikit': [],
    'qucs': [],
    'mom25d': ['--mom-dmax', '0.8', '--mom-solves', '16'],
    # 12, with a dump
    'mixed': ['--mom-dmax', '0.8', '--mom-solves', '16', '--mom-dump', '3.5'],
    'openems': ['--res', '15', '--end-db', '-30'],
    'gfdtd': ['--res', '15', '--end-db', '-30']
}
# tolerances: (|S21| at least, |S11| at most, delay error at most)
# measured: mom25d
TOL = {
    'scikit': (0.99, 0.08, 0.02),
    'qucs': (0.99, 0.08, 0.02),
    'mom25d': (0.99, 0.08, 0.02),
    # half the line cut out and put back: 0.35 %, |S11| 0.035
    'mixed': (0.99, 0.08, 0.02),
    # 0.06 % delay, |S11| 0.03; openEMS +2.0 %, |S11| 0.07
    'openems': (0.98, 0.15, 0.03),
    # openEMS's model on gfdtd: +2.1 %, |S11| 0.08 (CPU: ~15 s)
    'gfdtd': (0.98, 0.15, 0.03)
}


def eps_eff(w, h, er):
    """Hammerstad & Jensen's static effective permittivity of a zero-thickness
    microstrip."""
    u = w / h
    a = (1 + math.log((u**4 + (u / 52)**2) /
                      (u**4 + 0.432)) / 49 + math.log(1 + (u / 18.1)**3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


@pytest.fixture(scope='module')
def thru(tmp_path_factory):
    d = tmp_path_factory.mktemp('thru')
    subprocess.check_call([
        sys.executable,
        os.path.join(ROOT, 'examples', 'make_demo_board.py'),
        str(d / 'thru'), 'thru'
    ], stdout=subprocess.DEVNULL)
    return str(d / 'thru')


def installed(solver):
    """(usable, what) of a real solver, asked of a kicad2ems in a process of its
    own (this one has the stand-ins)."""
    code = ("from kicad2ems.core.solvers import get\nok, what = get(%r).check()\n"
            "print('ok' if ok else what)" % solver)
    p = subprocess.run([sys.executable, '-c', code], capture_output=True,
                       text=True)
    out = (p.stdout.strip().splitlines()[-1]
           if p.stdout.strip() else p.stderr.strip()[-200:])
    return out == 'ok', out


@pytest.mark.parametrize('solver', list(FAST))
def test_microstrip_thru(solver, thru, tmp_path):
    if (solver == 'openems'
            and not os.environ.get('KICAD2EMS_CONFORMANCE_OPENEMS')):
        pytest.skip('openEMS conformance: set KICAD2EMS_CONFORMANCE_OPENEMS=1')
    ok, what = installed(solver)
    if not ok:
        pytest.skip(what)
    out = tmp_path / 'out'
    args = [
        thru + '.kicad_pcb', '--pos', thru + '-top.pos', '--fmin', '1',
        '--fmax', '6', '--points', '21', '--solvers', solver, '--no-report',
        '--out',
        str(out), *FAST[solver]
    ]
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', *args],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    f, S = read_touchstone(str(out / f"thru_{solver}.s2p"))
    s21_min, s11_max, delay_tol = TOL[solver]
    s21 = S[:, 1, 0]
    delay = -np.polyfit(2 * np.pi * f, np.unwrap(np.angle(s21)), 1)[0]
    # between the port planes (just inside the ends)
    length = THRU['length'] - 2 * min(0.25 * THRU['w'], 0.1)
    ref = (length * 1e-3 * math.sqrt(eps_eff(THRU['w'], THRU['h'], THRU['er']))
           / C0)
    assert (np.abs(s21).min()
            > s21_min), f"{solver}: |S21| down to {np.abs(s21).min():.3f}"
    assert (np.abs(S[:, 0, 0]).max()
            < s11_max), f"{solver}: |S11| up to {np.abs(S[:, 0, 0]).max():.3f}"
    assert (
        abs(delay / ref - 1) < delay_tol
    ), f"{solver}: delay {delay * 1e12:.1f} ps, expected {ref * 1e12:.1f} ps"
    if solver == 'mixed':  # the middle of the line as a line, and the
        import json        # joined solution's currents
        info = (json.loads(
            (out
             / 'thru_run.json').read_text(encoding='utf-8'))['solved']['mixed'])
        # 13 frequencies solved on Linux, 16 on Windows (the adaptive sweep's
        # points follow the rounding): a bound
        assert info['cuts'] == 1 and info['cut_mm'] > 10 and info['solves'] < 20
        assert (out / 'thru_mixed_P1_dump_J_F_Cu_fd_3.5GHz.png').exists()
