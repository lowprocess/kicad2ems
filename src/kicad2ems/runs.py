"""
A kicad2ems run started by another program (kicad2ems-gui, the KiCad plugin): its settings as a TOML file, the
command line (run as a separate process), its event stream (--events, JSON lines), stopping it and the files it
wrote. No Qt here.

    run = Run(settings_dict, out_dir, preview=False)
    run.argv()                     # [python, -m, kicad2ems, <out>/gui_run.toml, --events, ...]
    tail = EventTail(path); tail.read() -> [event dicts]
    read_summary(path)             # a <name>_run.json, its format checked (formats.py)
    stop_runs(out_dir)             # STOP for the run (stop.py), ABORT into every FDTD run folder
    find_results(out_dir)          # [(label, path)] of the newest finished run's files
    write_toml(path, settings); toml_value(v)
"""
import glob, json, os, sys


def toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(toml_value(x) for x in v) + ']'
    return json.dumps(str(v))                              # a TOML basic string is a JSON string


def write_toml(path, values):
    """A flat TOML file of the non-empty settings (solvers = [] kept: no solver, as the GUI says so)."""
    lines = ["# written by the kicad2ems GUI"]
    for k, v in values.items():
        if v is None or v == '' or (v == [] and k != 'solvers') or (k in ('cut_ports', 'mom') and v is False):
            continue
        lines.append(f"{k} = {toml_value(v)}")
    with open(path, 'w') as fh:
        fh.write("\n".join(lines) + "\n")


class Run:
    """One kicad2ems run: its settings file, event file and command line."""
    def __init__(self, values, out_dir, preview=False):
        self.values, self.out, self.preview = dict(values), os.path.abspath(out_dir), preview
        os.makedirs(self.out, exist_ok=True)
        self.toml = os.path.join(self.out, 'gui_run.toml')
        self.events = os.path.join(self.out, 'gui_events.jsonl')
        write_toml(self.toml, dict(self.values, out=self.out))
        if os.path.exists(self.events):
            os.remove(self.events)

    def argv(self):
        return [sys.executable, '-m', 'kicad2ems', self.toml, '--events', self.events] + \
               (['--preview'] if self.preview else [])


def read_summary(path):
    """A run summary (<name>_run.json), checked: ValueError when this kicad2ems cannot read its format."""
    from .formats import read
    return read(path, 'kicad2ems-run')


class EventTail:
    """Reads the new complete lines of an events file on each call. The stream's first line names its format
    (formats.py): it is not passed on, and a format this kicad2ems cannot read comes out as a log line saying so."""
    def __init__(self, path):
        self.path, self.pos = path, 0

    def read(self):
        try:
            with open(self.path, 'rb') as fh:
                fh.seek(self.pos)
                chunk = fh.read()
        except OSError:
            return []
        cut = chunk.rfind(b'\n') + 1
        self.pos += cut
        out = []
        for line in chunk[:cut].decode('utf-8', 'replace').splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('kind') == 'format':
                from .formats import check
                why = check(e, 'kicad2ems-events')
                if why:
                    out.append(dict(t=e.get('t', 0.0), kind='log', text=f"WARNING: the run's events: {why}"))
                continue
            out.append(e)
        return out


def stop_runs(out_dir):
    """Ask the run writing to out_dir to stop: its STOP file (every solver checks it, stop.py), and ABORT into
    every FDTD run folder under out_dir (<out>/<engine>/exc_<port>: the engine stops and finishes normally); returns
    those folders."""
    from . import stop
    stop.request(out_dir)
    dirs = glob.glob(os.path.join(out_dir, '*', 'exc_*')) + glob.glob(os.path.join(out_dir, 'converge', '*', '*', 'exc_*'))
    for d in dirs:
        open(os.path.join(d, 'ABORT'), 'w').close()
    return dirs


def find_results(out_dir):
    """(label, path) of the files of the newest finished run in out_dir (from its <name>_run.json, or the
    <name>_openems.json of an older run)."""
    sums = sorted(glob.glob(os.path.join(out_dir, '*_run.json')) + glob.glob(os.path.join(out_dir, '*_openems.json')),
                  key=os.path.getmtime)
    if not sums:
        return []
    with open(sums[-1]) as fh:
        files = json.load(fh).get('files', [])
    return [(lab, p if os.path.isabs(p) else os.path.join(out_dir, p)) for lab, p in files]
