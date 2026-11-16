"""kicad2ems-gui's tests use kicad2ems's fixtures and example boards, and its stand-in solvers (the GUI's forms
are built from the solver registry: the same solvers on every machine), from tests/core."""
import os, sys

K2E = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))     # the repository's root
sys.path.insert(0, os.path.join(K2E, 'tests', 'core'))      # the shared fixtures, the stand-in solvers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # this folder's own helpers
from k2e_fixtures import *                                          # noqa: E402,F401,F403
import standins                                                     # noqa: E402

standins.install()                                                  # before kicad2ems.core.config is imported

# the analyses: stand-ins too (fake_analyses.py), before kicad2ems.gui lists the registry's; pcbqs's own are run
# through the same window in test_real_analyses.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_analyses                                                # noqa: E402

fake_analyses.install()



# The interpreter's own exit is skipped once pytest has reported: PySide 6.12 crashed tearing down what the tests
# leave (every test passed, then exit 139 on Linux, 127 on Windows, two runs in three). Its QApplication's
# destruction walks every widget wrapper still alive (110k by the last test), and a pyqtgraph scene's items call
# their Python overrides (itemChange) from their C++ destructors after their wrappers are gone (gdb:
# QGraphicsScene::clear -> QGraphicsWidget::~QGraphicsWidget -> a null call). Deleting each test's windows after
# it hit the same in mid-run. pytest's exit status stands.
_status = [0]


def pytest_sessionfinish(session, exitstatus):
    _status[0] = int(exitstatus)


def pytest_unconfigure(config):
    if 'PySide6.QtWidgets' in sys.modules and not os.environ.get('K2E_GUI_TEST_TEARDOWN'):
        sys.stdout.flush()
        sys.stderr.flush()
        # (on Windows the process still ends in an access violation, os._exit or TerminateProcess: Qt's DLLs torn
        # down; kicad2ems's windows workflow reads pytest's junit report there instead of the exit code)
        os._exit(_status[0])
