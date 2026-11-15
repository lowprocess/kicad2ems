"""
A solver's result kept for a run done again (R-D20): beside the run's other outputs, <name>_<solver>.result.npz
(its S) and .result.json (its key, ports, reference impedances, time, info, comment). A later run in the same
output folder takes it in place of a solve when its key is the same:

    the board every solver gets (<name>_board.json, as this run wrote it), the frequencies, every setting but
    the output ones (CORE's group 'output', and which solvers run), the solver's name and its check line (its
    version and where it is), and a digest of the stack's code that is loaded (kicad2ems, the solver packages,
    their engines): a change to any of them solves again.

--no-reuse solves every solver again. A result that cannot be read is no result (the solver runs).
"""
import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

VERSION = 1
STACK = ('kicad2ems', 'mom25d', 'gfdtd', 'kicad2ems_fdtd', 'emsdev', 'pcbqs', 'kicad2ems_fem', 'openEMS', 'CSXCAD',
         'skrf')
_DIGEST = {}


def code_digest():
    """A digest of the stack's packages loaded now: their Python files and compiled cores."""
    loaded = tuple(sorted(p for p in STACK if p in sys.modules))
    if loaded not in _DIGEST:
        h = hashlib.sha256()
        for name in loaded:
            mod = sys.modules[name]
            where = getattr(mod, '__file__', None)
            if not where:
                continue
            root = Path(where).parent
            h.update(name.encode())
            h.update(str(getattr(mod, '__version__', '')).encode())
            for p in sorted(root.rglob('*')):
                if '__pycache__' in p.parts or p.suffix not in ('.py', '.so', '.pyd', '.cpp', '.slang'):
                    continue
                h.update(str(p.relative_to(root)).encode())
                h.update(p.read_bytes())
        _DIGEST[loaded] = h.hexdigest()[:20]
    return _DIGEST[loaded]


def _skipped():
    from .config import CORE
    return {o.key for o in CORE if o.group == 'output'} | {'solvers', 'mom', 'fast', 'no_reuse', 'notes'}


def key(run, name):
    """The key of solver `name`'s result in this run, or None where the run's board was not written."""
    from .solvers import registry
    board = run.path('_board.json')
    if not os.path.isfile(board):
        return None
    sv = registry()[name]
    skip = _skipped()
    settings = {k: v for k, v in dataclasses.asdict(run.s).items() if k not in skip}
    h = hashlib.sha256()
    h.update(f"v{VERSION} {name}\n".encode())
    h.update(Path(board).read_bytes())
    h.update(np.ascontiguousarray(np.asarray(run.freq, float)).tobytes())
    h.update(json.dumps(settings, sort_keys=True, default=str).encode())
    try:
        h.update(str(sv.check(run.s)[1]).encode())
    except Exception:                                   # noqa: BLE001  (a check that fails: no reuse)
        return None
    h.update(code_digest().encode())
    return h.hexdigest()


def _files(run, name):
    return run.path(f"_{name}.result.json"), run.path(f"_{name}.result.npz")


def load(run, name, k):
    """The Result kept for solver `name` under key k, or None."""
    from .solvers import Result
    meta, data = _files(run, name)
    try:
        m = json.loads(Path(meta).read_text())
        if m.get('key') != k or not os.path.isfile(data):
            return None
        with np.load(data, allow_pickle=False) as z:
            S = z['S']
        if S.shape != (len(run.freq), len(m['names']), len(m['names'])):
            return None
        info = dict(m.get('info') or {}, reused=dict(file=os.path.basename(data), solved=m.get('solved')))
        return Result(S=S, names=list(m['names']), zref=list(m['zref']), seconds=float(m.get('seconds') or 0.0),
                      info=info, comment=m.get('comment', ''))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def keep(run, name, k, r):
    """Solver `name`'s Result r kept under key k for the next run."""
    import time
    meta, data = _files(run, name)
    try:
        np.savez_compressed(data, S=np.asarray(r.S))
        Path(meta).write_text(json.dumps(dict(
            key=k, names=list(r.names), zref=[float(z) for z in np.atleast_1d(r.zref)], seconds=float(r.seconds or 0.0),
            info=r.info, comment=r.comment, solved=time.strftime('%Y-%m-%d %H:%M:%S')), default=str, indent=1))
    except (OSError, TypeError, ValueError):
        for p in (meta, data):
            try:
                os.remove(p)
            except OSError:
                pass
