"""The plugin's tests use kicad2ems's fixtures and example boards, from
tests/core."""
import os, sys

# the repository's root
K2E = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# the shared fixtures, the stand-in solvers
sys.path.insert(0, os.path.join(K2E, 'tests', 'core'))
# this folder's own helpers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k2e_fixtures import *  # noqa: E402,F401,F403


def canned_runs(m, mesh, freq, excite, bc, out_dir, **kw):
    """In place of run.run_excitations: a plausible thru (|S21| ~ -0.3 dB with
    130 ps delay, S11 ~ -25 dB)."""
    import numpy as np
    from kicad2ems.fdtd.run import RunResult
    n = len(m.ports)
    r = RunResult(np.full((len(freq), n, n), np.nan + 0j))
    ph = np.exp(-2j * np.pi * freq * 130e-12)
    for k in excite:
        r.S[:, k, k] = 0.056 * ph
        for i in range(n):
            if i != k:
                r.S[:, i, k] = 0.966 * ph
        r.seconds[m.ports[k].name], r.steps[m.ports[k].name] = 12.0, 30000
    return r
