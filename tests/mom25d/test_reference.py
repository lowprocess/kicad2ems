"""mom25d end to end against a stored reference (R-C9): demo_lowpass (a 3rd-order
Butterworth of three 0402 parts on microstrip) as its example config has it, run
by `python -m kicad2ems --solvers mom25d` in a process of its own, against
openEMS's S-parameters of the same board (tests/data/demo_lowpass_openems.s2p).
mom25d was within 0.024 of it in S11 and 0.015 in S21 (2026-11-20, ~3 s on the
CPU)."""
import os, shutil, subprocess, sys

import numpy as np
import pytest

from kicad2ems.core.touchstone import read_touchstone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCE = os.path.join(ROOT, 'tests', 'data', 'demo_lowpass_openems.s2p')
TOL = 0.05  # measured 0.024


@pytest.fixture(scope='module')
def lowpass(tmp_path_factory):
    d = tmp_path_factory.mktemp('lowpass')
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'make_demo_board.py'),
                           str(d / 'demo_lowpass'), 'lowpass'], stdout=subprocess.DEVNULL)
    shutil.copy(os.path.join(ROOT, 'examples', 'demo_lowpass.toml'), d)
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', 'demo_lowpass.toml', '--solvers', 'mom25d',
                        '--out', 'out', '--no-report', '--no-reuse'], cwd=d, capture_output=True,
                       text=True, env={**os.environ, 'MOM25D_GPU': 'off'})
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return read_touchstone(str(d / 'out' / 'demo_lowpass_mom25d.s2p'))


def test_lowpass_mom25d_against_openems(lowpass):
    f, s = lowpass
    fr, sr = read_touchstone(REFERENCE)
    assert s.shape == sr.shape and np.allclose(f, fr)
    for i, j in ((0, 0), (1, 0)):
        d = np.abs(s[:, i, j] - sr[:, i, j])
        k = int(d.argmax())
        assert d[k] < TOL, f"S{i + 1}{j + 1}: |dS| {d[k]:.3f} at {f[k] / 1e9:.2f} GHz"
