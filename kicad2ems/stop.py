"""
Stopping a run from outside it (the GUI's Stop): a STOP file in the run's output folder.

    arm(out)          # at the start of a run: an old STOP removed, its path in KICAD2EMS_STOP (child processes too)
    disarm()          # at its end: the STOP file and the variable gone
    request(out)      # from another process: ask the run in `out` to stop
    check()           # in a solver, between steps: raises Stopped once a stop was requested

Each solver ends at its next check: mom25d after the frequency it is solving, openEMS at once (its run
watcher writes ABORT, and no further excitation starts). Solvers not started yet are skipped.
"""
import os

ENV = 'KICAD2EMS_STOP'
NAME = 'STOP'


class Stopped(Exception):
    """A stop was requested (request); the solver gives no result."""


def arm(out):
    path = os.path.join(os.path.abspath(out), NAME)
    if os.path.exists(path):
        os.remove(path)
    os.environ[ENV] = path


def disarm():
    path = os.environ.pop(ENV, None)
    if path and os.path.exists(path):
        os.remove(path)


def request(out):
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, NAME), 'w').close()


def requested():
    path = os.environ.get(ENV)
    return bool(path) and os.path.exists(path)


def check():
    if requested():
        raise Stopped('stop requested')
