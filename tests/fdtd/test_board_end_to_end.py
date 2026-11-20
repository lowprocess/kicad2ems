"""A board end to end through the FDTD layer on gfdtd's CPU backend (R-C6), against openEMS's S-parameters of the
same board and settings, stored in tests/data/demo_lowpass_openems.s2p.

demo_lowpass (examples/make_demo_board.py lowpass: a 3rd-order Butterworth at 1 GHz of three 0402 parts on a
microstrip) is run as its example config has it, by `python -m kicad2ems` in a process of its own: the import, the
parts, the mesh, the openEMS-style model, gfdtd's run and the Touchstone file. CI has no GPU: GFDTD_BACKEND=cpu
(the compiled core, ~8 s; NumPy without it, slower).

The reference: kicad2ems examples/demo_lowpass.toml --solvers openems (2026-11-19, 69 s). gfdtd's run was then
within 0.029 of it in complex S11 and S21 (the largest at 0.6 GHz; |S21| within 0.15 dB from 1 to 3 GHz)."""
import os, shutil, subprocess, sys

import numpy as np
import pytest

from kicad2ems.core.touchstone import read_touchstone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCE = os.path.join(ROOT, 'tests', 'data', 'demo_lowpass_openems.s2p')
# measured 0.029
TOL = 0.05


def _gfdtd_usable():
    code = ("from kicad2ems.core.solvers import get\n"
            "ok, what = get('gfdtd').check()\nprint('ok' if ok else what)")
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       env={**os.environ, 'GFDTD_BACKEND': 'cpu'})
    out = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr.strip()[-200:]
    return out == 'ok', out


@pytest.fixture(scope='module')
def lowpass_run(tmp_path_factory):
    ok, what = _gfdtd_usable()
    if not ok:
        pytest.skip(f"gfdtd not usable here: {what}")
    d = tmp_path_factory.mktemp('lowpass')
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'make_demo_board.py'),
                           str(d / 'demo_lowpass'), 'lowpass'], stdout=subprocess.DEVNULL)
    shutil.copy(os.path.join(ROOT, 'examples', 'demo_lowpass.toml'), d)
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', 'demo_lowpass.toml', '--solvers', 'gfdtd',
                        '--out', 'out', '--no-report', '--no-reuse'], cwd=d, capture_output=True, text=True,
                       env={**os.environ, 'GFDTD_BACKEND': 'cpu'})
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return read_touchstone(str(d / 'out' / 'demo_lowpass_gfdtd.s2p'))


def test_lowpass_gfdtd_against_openems(lowpass_run):
    f, s = lowpass_run
    fr, sr = read_touchstone(REFERENCE)
    assert s.shape == sr.shape and np.allclose(f, fr)
    for (i, j) in ((0, 0), (1, 0)):
        d = np.abs(s[:, i, j] - sr[:, i, j])
        k = int(d.argmax())
        assert d[k] < TOL, f"S{i + 1}{j + 1}: |dS| {d[k]:.3f} at {f[k] / 1e9:.2f} GHz against openEMS"


def test_lowpass_is_a_lowpass(lowpass_run):
    """What a broken run would not give, whatever the reference: a pass band, the -3 dB point, a stop band. The
    ideal circuit's -3 dB is at 1 GHz; the parts' ESL and pads put the board's at 0.76 GHz (openEMS's too)."""
    f, s = lowpass_run
    db = 20 * np.log10(np.abs(s[:, 1, 0]))
    assert db[f <= 0.5e9].min() > -1.0
    fc = f[np.argmax(db < -3)]
    assert 0.7e9 < fc < 0.82e9, fc
    assert db[(f > 1.8e9) & (f < 2.5e9)].max() < -25
