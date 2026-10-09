"""--max-steps: excitation length estimate, steps taken read back from a run folder, the step-limit
warning and the CLI / TOML option (no openEMS needed)."""
import numpy as np
import pytest
from kicad2openems.cli import parser
from kicad2openems.config import apply_toml
from kicad2openems.simulation import pulse_steps, steps_taken, check_steps


def test_pulse_steps():
    # openEMS: Gaussian length 2 * 9 / (2 pi fc), fc = 0.5 (fmax - fmin) 1.05; filter example: 1-8 GHz, dt 57.2 fs
    # -> 1e6 steps = 73.4 pulse lengths (from the openEMS log)
    assert 1e6 / pulse_steps(1e9, 8e9, 0.0572) == pytest.approx(73.4, rel=0.01)
    assert pulse_steps(0.05e9, 0.5e9, 0.0572) > 9 * pulse_steps(1e9, 8e9, 0.0572)   # low frequencies: longer


def _run_folder(path, dt, steps, every=60):
    path.mkdir()
    (path / 'et').write_text("".join(f"{i * dt:.6e}\t0\n" for i in range(50)))
    (path / 'port_ut_1').write_text("% probe\n" + "".join(f"{i * dt:.9e}\t0\n" for i in range(0, steps, every)))


def test_steps_taken_and_warning(tmp_path):
    _run_folder(tmp_path / 'a', 4.8e-13, 12000)
    n = steps_taken(str(tmp_path / 'a'), dt_ps=0.53)                 # 'et' wins over the mesher's estimate
    assert 11900 <= n <= 12000
    log = []
    mesh = {'info': {'dt_ps': 0.53}}
    check_steps('P1', str(tmp_path / 'a'), mesh, 1_000_000, -40, log.append)
    assert not log
    check_steps('P1', str(tmp_path / 'a'), mesh, 12000, -40, log.append)
    assert log and 'step limit' in log[0] and '--max-steps 12000' in log[0]
    assert steps_taken(str(tmp_path)) is None                         # no probe file


def test_cli_and_toml(tmp_path):
    ap = parser()
    assert ap.parse_args(['b.kicad_pcb']).max_steps == 1_000_000
    assert ap.parse_args(['b.kicad_pcb', '--max-steps', '5000000']).max_steps == 5_000_000
    cfg = tmp_path / 'c.toml'
    cfg.write_text('[openems]\nmax-steps = 3000000\n')
    a = ap.parse_args(['b.kicad_pcb'])
    assert apply_toml(a, ap, str(cfg), ['b.kicad_pcb']) == [] and a.max_steps == 3_000_000
