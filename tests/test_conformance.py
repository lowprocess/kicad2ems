"""Conformance: boards with a known answer, each capable solver within stated tolerances of it, with fast
settings. The lossless microstrip thru (make_demo_board.py thru): |S21| ~ 1, |S11| small, and the delay of a
line of Hammerstad-Jensen's effective permittivity. openEMS runs when KICAD2EMS_CONFORMANCE_OPENEMS=1 (slow:
for a nightly run, not every test run); EMerge when it is installed (KICAD2EMS_EMERGE_PYTHON: its Python)."""
import math, os, subprocess, sys
import numpy as np
import pytest
from kicad2ems.touchstone import read_touchstone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'examples'))
from make_demo_board import THRU                                    # noqa: E402

C0 = 299792458.0
FAST = {'scikit': [], 'qucs': [], 'mom25d': ['--mom-dmax', '0.8', '--mom-solves', '16'],   # converges in 13
        'openems': ['--res', '15', '--end-db', '-30'], 'emerge': ['--emerge-points', '6']}
# tolerances: (|S21| at least, |S11| at most, delay error at most)
TOL = {'scikit': (0.99, 0.08, 0.02), 'qucs': (0.99, 0.08, 0.02), 'mom25d': (0.99, 0.08, 0.02),     # measured: mom25d
       'openems': (0.98, 0.15, 0.03),                    # 0.06 % delay, |S11| 0.03; openEMS +2.0 %, |S11| 0.07
       'emerge': (0.98, 0.15, 0.03)}                     # EMerge (KICAD2EMS_EMERGE_PYTHON): +2.0 %, |S11| 0.07


def eps_eff(w, h, er):
    """Hammerstad & Jensen's static effective permittivity of a zero-thickness microstrip."""
    u = w / h
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


@pytest.fixture(scope='module')
def thru(tmp_path_factory):
    d = tmp_path_factory.mktemp('thru')
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'make_demo_board.py'), str(d / 'thru'), 'thru'],
                          stdout=subprocess.DEVNULL)
    return str(d / 'thru')


@pytest.mark.parametrize('solver', list(FAST))
def test_microstrip_thru(solver, thru, run_cli, tmp_path):
    from kicad2ems.solvers import registry
    if solver == 'openems' and not os.environ.get('KICAD2EMS_CONFORMANCE_OPENEMS'):
        pytest.skip('openEMS conformance: set KICAD2EMS_CONFORMANCE_OPENEMS=1')
    ok, what = registry()[solver].check()
    if not ok:
        pytest.skip(what)
    out = tmp_path / 'out'
    code, so, err = run_cli(thru + '.kicad_pcb', '--pos', thru + '-top.pos', '--fmin', '1', '--fmax', '6', '--points', '21',
                            '--solvers', solver, '--no-report', '--out', out, *FAST[solver])
    assert code == 0, so + err
    f, S = read_touchstone(str(out / f"thru_{solver}.s2p"))
    s21_min, s11_max, delay_tol = TOL[solver]
    s21 = S[:, 1, 0]
    delay = -np.polyfit(2 * np.pi * f, np.unwrap(np.angle(s21)), 1)[0]
    length = THRU['length'] - 2 * min(0.25 * THRU['w'], 0.1)        # between the port planes (just inside the ends)
    ref = length * 1e-3 * math.sqrt(eps_eff(THRU['w'], THRU['h'], THRU['er'])) / C0
    assert np.abs(s21).min() > s21_min, f"{solver}: |S21| down to {np.abs(s21).min():.3f}"
    assert np.abs(S[:, 0, 0]).max() < s11_max, f"{solver}: |S11| up to {np.abs(S[:, 0, 0]).max():.3f}"
    assert abs(delay / ref - 1) < delay_tol, f"{solver}: delay {delay * 1e12:.1f} ps, expected {ref * 1e12:.1f} ps"
