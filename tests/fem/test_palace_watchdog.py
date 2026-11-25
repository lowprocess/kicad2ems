"""Palace's run (palace/run.py) with a stand-in palace: a run that hangs without
writing a line is stopped at its timeout and said (the timeout had only begun
once Palace closed its output: a silent hang was waited on for ever), and an
unconverged solve is said and recorded beside the log. No Palace needed."""
import json
import os
import stat
import sys
import time

import pytest

from kicad2ems.fem.palace import run

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='a shell script')


def fake(tmp_path, body):
    p = tmp_path / 'palace'
    p.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return ('native', str(p))


def test_a_silent_hang_is_stopped_at_the_timeout(tmp_path):
    how = fake(tmp_path, "time.sleep(60)")
    t = time.time()
    with pytest.raises(RuntimeError, match='did not finish in 1 s'):
        run.run(str(tmp_path), 'palace.json', np=1, how=how, timeout=1,
                log=lambda *a: None)
    assert time.time() - t < 20


def test_an_unconverged_solve_is_said_and_recorded(tmp_path):
    how = fake(tmp_path, "print('Git changeset ID: abc123')\n"
                         "print('GMRES solver did NOT converge in 100 iterations')\n"
                         "sys.stdout.flush()")
    said = []
    run.run(str(tmp_path), 'palace.json', np=2, how=how, timeout=30,
            log=said.append)
    rec = json.load(open(tmp_path / 'palace.log.json'))
    assert rec['changeset'] == 'abc123' and rec['ranks'] == 2
    assert rec['unconverged'] and any('did not converge' in s for s in said)


def test_a_palace_silent_too_long_is_stopped_without_a_timeout(tmp_path,
                                                                monkeypatch):
    """No caller gives a timeout (a hung MPI job had run for ever): a Palace
    that writes nothing for SILENT_S is stopped and said; one that keeps
    writing runs on past it."""
    monkeypatch.setattr(run, 'SILENT_S', 1.0)
    how = fake(tmp_path, "print('Computing', flush=True)\ntime.sleep(60)")
    t = time.time()
    with pytest.raises(RuntimeError, match='wrote nothing for 1 s'):
        run.run(str(tmp_path), 'palace.json', np=1, how=how,
                log=lambda *a: None)
    assert time.time() - t < 20
    how = fake(tmp_path, "for i in range(6):\n"
                         "    print('It', i, flush=True)\n"
                         "    time.sleep(0.4)")
    run.run(str(tmp_path), 'palace.json', np=1, how=how, log=lambda *a: None)
