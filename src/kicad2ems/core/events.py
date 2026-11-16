"""
Run events as JSON lines (--events FILE): one object per line, for a GUI or a script following a run.

    {"t": 0.0, "kind": "format", "format": "kicad2ems-events", "format_version": 1}      (formats.py)
    {"t": 0.12, "kind": "stage", "name": "model"}
    {"t": 0.40, "kind": "log", "text": "mesh: 188 x 115 x 25 = 0.54 M cells ..."}
    {"t": 9.80, "kind": "status", "run": "P1", "run_time": 8.0, "step": 6118, "db": -3.2, "speed": 138.0,
     "fraction": 0.08, "eta": 95.0}
    {"t": 99.1, "kind": "file", "label": "S-parameters (Touchstone)", "path": "out/board_openems.s2p"}
    {"t": 99.3, "kind": "done", "code": 0}

t is seconds since the run started; NaN / infinite numbers are written as null (strict JSON).
Lines are flushed as they are written.
"""
import json, math, threading, time


class EventSink:
    def __init__(self, path):
        self.t0 = time.time()
        self._fh = open(path, 'w', buffering=1, encoding='utf-8')
        self._lock = threading.Lock()                      # the progress monitor writes from its own thread
        from .formats import stamp
        self.emit('format', **stamp({}, 'kicad2ems-events'))

    def emit(self, kind, **data):
        data = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in data.items()}
        line = json.dumps(dict(t=round(time.time() - self.t0, 3), kind=kind, **data), default=_plain)
        with self._lock:
            if not self._fh.closed:
                self._fh.write(line + "\n")

    def close(self):
        with self._lock:
            self._fh.close()


def _plain(v):
    """numpy scalars and other odd values -> JSON."""
    try:
        return v.item()
    except AttributeError:
        return str(v)
