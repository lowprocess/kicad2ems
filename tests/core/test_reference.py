"""The line model end to end against a stored reference (R-C9): demo_lowpass by
`python -m kicad2ems --solvers lines` in a process of its own (this suite's
solvers are stand-ins), against openEMS's S-parameters of the same board
(tests/data/demo_lowpass_openems.s2p). S21 within 0.046 (qucsator) and 0.036
(scikit-rf) and the -3 dB corner at 0.79 GHz against openEMS's 0.78
(2026-11-20). S11 is not compared: the line model has no model of the parts'
pads, and its S11 is up to 0.22 off."""
import os, shutil, subprocess, sys, glob

import numpy as np
import pytest

from kicad2ems.core.touchstone import read_touchstone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCE = os.path.join(ROOT, 'tests', 'data', 'demo_lowpass_openems.s2p')


@pytest.fixture(scope='module')
def lowpass(tmp_path_factory):
    d = tmp_path_factory.mktemp('lowpass')
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'make_demo_board.py'),
                           str(d / 'demo_lowpass'), 'lowpass'], stdout=subprocess.DEVNULL)
    shutil.copy(os.path.join(ROOT, 'examples', 'demo_lowpass.toml'), d)
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', 'demo_lowpass.toml', '--solvers', 'lines',
                        '--out', 'out', '--no-report', '--no-reuse'], cwd=d, capture_output=True,
                       text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    ts = glob.glob(str(d / 'out' / 'demo_lowpass_*.s2p'))
    assert ts, p.stdout[-2000:]
    return read_touchstone(ts[0])


def test_lowpass_line_model_against_openems(lowpass):
    f, s = lowpass
    fr, sr = read_touchstone(REFERENCE)
    assert s.shape == sr.shape and np.allclose(f, fr)
    d = np.abs(s[:, 1, 0] - sr[:, 1, 0])
    assert d.max() < 0.08, f"S21: |dS| {d.max():.3f} at {f[d.argmax()] / 1e9:.2f} GHz"
    corner = lambda S: f[np.argmax(20 * np.log10(np.abs(S[:, 1, 0])) < -3)]
    assert abs(corner(s) - corner(sr)) < 0.05e9
