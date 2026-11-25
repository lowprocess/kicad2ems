"""A kicad2ems run started by another program (kicad2ems.core.runs:
kicad2ems-gui, the KiCad plugin): the settings file it writes, the command line,
the event stream read as it grows, stopping it, and the files of the newest
run."""
import json, os
import pytest
from kicad2ems.core.runs import (Run, EventTail, stop_runs, find_results,
                                 write_toml, toml_value)
from kicad2ems.core.config import build_settings


def test_run_events_and_stop(tmp_path):
    r = Run(dict(board='b.kicad_pcb', fmin=1.0), tmp_path / 'out', preview=True)
    assert (r.argv()[-1] == '--preview' and r.argv()[3] == r.toml
            and os.path.exists(r.toml))
    tail = EventTail(r.events)
    assert tail.read() == []
    with open(r.events, 'w', encoding='utf-8') as fh:
        # half a line
        fh.write(
            json.dumps(dict(kind='stage', name='model')) + "\n"
            + '{"kind": "log", "te')
    assert [e['kind'] for e in tail.read()] == ['stage']
    with open(r.events, 'a', encoding='utf-8') as fh:
        fh.write('xt": "hello"}\n')
    assert tail.read() == [dict(kind='log', text='hello')]
    d = tmp_path / 'out' / 'openems' / 'exc_P1'
    d.mkdir(parents=True)
    assert (stop_runs(str(tmp_path / 'out')) == [str(d)]
            and (d / 'ABORT').exists())


def test_written_settings_read_back(tmp_path):
    vals = dict(board=str(tmp_path / 'b.kicad_pcb'), nets=['RF',
                                                           'GND'], fmin=1.0,
                fmax=6.0, points=201, ports=['P3=J1.1',
                                             'P4@1,2,F.Cu'], cut_ports=True,
                region=[0.0, 0.0, 10.0,
                        5.0], fast='qucsator', fine=None, solvers=[])
    p = tmp_path / 'c.toml'
    write_toml(p, vals)
    s = build_settings({}, str(p))
    assert (s.nets == ['RF', 'GND'] and s.points == 201
            and s.ports == ['P3=J1.1', 'P4@1,2,F.Cu'] and s.cut_ports)
    assert s.region == (0, 0, 10, 5) and s.fast == 'qucsator' and s.fine is None
    # no solver: said, not left to the default
    assert 'solvers = []' in p.read_text(encoding='utf-8')
    assert (toml_value(True) == 'true'
            and toml_value([1, 'a"b']) == '[1, "a\\"b"]')


def test_find_results(tmp_path):
    assert find_results(str(tmp_path)) == []
    (tmp_path / 'b_run.json').write_text(
        json.dumps(
            dict(files=[['S-parameters', 'b_openems.s2p'],
                        ['report', str(tmp_path / 'b_report.html')]])),
        encoding='utf-8')
    assert (find_results(str(tmp_path)) == [
        ('S-parameters', str(tmp_path / 'b_openems.s2p')),
        ('report', str(tmp_path / 'b_report.html'))
    ])


def _tree_script(pidfile):
    """A tool that starts a child in a session of its own (as fem's children
    are) and writes the child's pid."""
    return ("import subprocess, sys, time\n"
            "c = subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(120)'], start_new_session=True)\n"
            f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
            "time.sleep(120)\n")


def _child_pid(pidfile, timeout=30):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if pidfile.exists() and pidfile.read_text().strip():
            return int(pidfile.read_text())
        time.sleep(0.05)
    raise AssertionError('the tool did not start its child')


def _alive(pid):
    """Whether pid runs (a zombie is not running)."""
    try:
        with open(f'/proc/{pid}/stat', encoding='utf-8') as fh:
            return fh.read().rsplit(')', 1)[1].split()[0] not in 'ZX'
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return not os.path.isdir('/proc')
    except ProcessLookupError:
        return False


@pytest.mark.skipif(os.name == 'nt', reason='taskkill /T on Windows')
def test_kill_tree_ends_a_child_in_a_session_of_its_own(tmp_path):
    """kill_tree: the process and what it started, a child in its own
    session too (fem's Palace and Elmer ranks: a kill of the tool alone had
    left them running); and a tree recorded before, once its tool has ended
    on SIGTERM and left the child to init."""
    import signal
    import subprocess
    import sys
    import time
    from kicad2ems.core.runs import descendants, kill_tree, tree_of
    for first_term in (False, True):
        pidfile = tmp_path / f'child{first_term}.pid'
        p = subprocess.Popen([sys.executable, '-c', _tree_script(pidfile)])
        child = None
        try:
            child = _child_pid(pidfile)
            assert child in descendants(p.pid)
            tree = tree_of(p.pid) if os.path.isdir('/proc') else []
            if first_term:  # (the tool ends, the child goes to init)
                p.send_signal(signal.SIGTERM)
                p.wait(10)
                if not tree:
                    continue  # (no /proc: no start to tell it by)
            kill_tree(p.pid, tree)
            p.wait(10)
            end = time.time() + 10
            while _alive(child) and time.time() < end:
                time.sleep(0.05)
            assert not _alive(child)
        finally:
            for q in (p.pid, child):
                try:
                    if q:
                        os.kill(q, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            p.wait(10)
