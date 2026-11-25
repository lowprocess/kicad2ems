"""RunProcess's stops end what a tool started as well as the tool: a child in a
session of its own (fem's Palace and Elmer ranks), which a kill of the tool
alone had left running."""
import os
import signal
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt',
                                reason='taskkill /T on Windows')

# a tool that starts a child in a session of its own, writes its pid and waits
# (no SIGTERM handler: asked to stop, it ends and leaves the child to init)
TOOL = ("import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'], start_new_session=True)\n"
        "open(sys.argv[1], 'w').write(str(c.pid))\n"
        "time.sleep(120)\n")


@pytest.fixture
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _alive(pid):
    """Whether pid runs (a zombie does not)."""
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


def _wait(app, cond, timeout=30):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    return cond()


@pytest.mark.parametrize('how', ['close', 'stop twice', 'stop, then grace'])
def test_a_hard_stop_ends_the_tool_and_what_it_started(app, tmp_path, how):
    from kicad2ems.gui.runprocess import Command, RunProcess
    pidfile = tmp_path / 'child.pid'
    runner = RunProcess()
    runner.STOP_GRACE_MS = 300
    child = None
    try:
        assert runner.start([Command('tool', [sys.executable, '-c', TOOL,
                                              str(pidfile)],
                                     str(tmp_path / 'run'))]) == ''
        assert _wait(app, lambda: pidfile.exists()
                     and pidfile.read_text().strip())
        child = int(pidfile.read_text())
        if how == 'close':
            runner.kill(3000)
        elif how == 'stop twice':
            runner.stop()
            runner.stop()
        else:  # the tool ends on SIGTERM; its child, after the grace
            runner.stop()
            assert _wait(app, lambda: runner.proc is None)
        assert _wait(app, lambda: not _alive(child), 10)
    finally:
        if child and _alive(child):
            os.kill(child, signal.SIGKILL)
        if runner.proc is not None:
            runner.proc.kill()
            runner.proc.waitForFinished(3000)
