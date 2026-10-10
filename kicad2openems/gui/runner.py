"""
The GUI's link to a run: a kicad2openems process (the command line, run as a separate program) and its
event stream (--events, JSON lines). No Qt here, so it is testable without a display.

    run = Run(settings_dict, out_dir, preview=False)
    run.argv()                     # [python, -m, kicad2openems, <out>/gui_run.toml, --events, ...]
    tail = EventTail(path); tail.read() -> [event dicts]
    stop_runs(out_dir)             # ABORT into every run folder: openEMS stops and finishes normally
"""
import glob, json, os, sys

# settings the GUI edits, in form order: (key, label, kind, group); kind: str / float / int / bool / choice
FIELDS = [
    ('board', 'Board (.kicad_pcb)', 'file', 'Board'),
    ('pos', 'Pick-and-place file', 'file', 'Board'),
    ('nets', 'Nets (comma-separated, or auto)', 'list', 'Board'),
    ('ground', 'Solid ground layer', 'str', 'Board'),
    ('region', 'Region (x0,y0,x1,y1 or a drawing layer)', 'str', 'Board'),
    ('ports', 'Extra ports (P3=J1.1, P4@x,y ...)', 'list', 'Board'),
    ('cut_ports', 'Ports where tracks cross the region edge', 'bool', 'Board'),
    ('edge_walls', 'Edge walls (xmin,xmax,ymin,ymax or all)', 'list', 'Board'),
    ('fmin', 'f min (GHz)', 'float', 'Band'),
    ('fmax', 'f max (GHz)', 'float', 'Band'),
    ('points', 'Frequency points', 'int', 'Band'),
    ('fine', 'Fine cell at copper edges (mm, empty: auto)', 'float', 'Mesh'),
    ('res', 'Cells per wavelength', 'float', 'Mesh'),
    ('nz', 'Cells through each dielectric', 'int', 'Mesh'),
    ('converge', 'Convergence levels (e.g. 1,1.5)', 'list', 'Mesh'),
    ('excite', 'Driven ports (empty: all)', 'list', 'Solver'),
    ('mode', 'Mode', ('sparams', 'antenna'), 'Solver'),
    ('ff_freq', 'Pattern frequencies (GHz, antenna; empty: resonance)', 'list', 'Solver'),
    ('end_db', 'Stop at (dB)', 'float', 'Solver'),
    ('end_on', 'Stop on', ('ports', 'energy'), 'Solver'),
    ('max_steps', 'Timestep limit', 'int', 'Solver'),
    ('threads', 'Threads (0: openEMS default)', 'int', 'Solver'),
    ('fast', 'Line model solver', ('', 'skrf', 'qucsator'), 'Solver'),
    ('dump', 'Dumps (FIELD@REGION[@GHz]; ...)', 'list', 'Field dumps'),
    ('dump_port', 'Runs that record dumps (empty: first driven)', 'list', 'Field dumps'),
    ('out', 'Output folder', 'dir', 'Output'),
]
SEMICOLON_LISTS = ('ports', 'dump')                       # items that may hold commas themselves


def toml_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(toml_value(x) for x in v) + ']'
    return json.dumps(str(v))                              # a TOML basic string is a JSON string


def write_toml(path, values):
    """A flat TOML file of the non-empty settings."""
    lines = ["# written by the kicad2openems GUI"]
    for k, v in values.items():
        if v is None or v == '' or v == [] or (k == 'cut_ports' and v is False):
            continue
        lines.append(f"{k} = {toml_value(v)}")
    with open(path, 'w') as fh:
        fh.write("\n".join(lines) + "\n")


class Run:
    """One kicad2openems run started from the GUI: its settings file, event file and command line."""
    def __init__(self, values, out_dir, preview=False):
        self.values, self.out, self.preview = dict(values), os.path.abspath(out_dir), preview
        os.makedirs(self.out, exist_ok=True)
        self.toml = os.path.join(self.out, 'gui_run.toml')
        self.events = os.path.join(self.out, 'gui_events.jsonl')
        write_toml(self.toml, dict(self.values, out=self.out))
        if os.path.exists(self.events):
            os.remove(self.events)

    def argv(self):
        return [sys.executable, '-m', 'kicad2openems', self.toml, '--events', self.events] + \
               (['--preview'] if self.preview else [])


class EventTail:
    """Reads the new complete lines of an events file on each call."""
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
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


def stop_runs(out_dir):
    """ABORT into every openEMS run folder under out_dir; returns the folders."""
    dirs = glob.glob(os.path.join(out_dir, 'openems', 'exc_*')) + \
        glob.glob(os.path.join(out_dir, 'converge', '*', 'openems', 'exc_*'))
    for d in dirs:
        open(os.path.join(d, 'ABORT'), 'w').close()
    return dirs


def extra_values(path):
    """The settings of a TOML config that the GUI has no field for (kept and passed on to its runs)."""
    from ..config import read_toml
    keys = {k for k, *_ in FIELDS}
    return {k: v for k, v in read_toml(path)[0].items() if k not in keys and k not in ('events', 'preview')}


def find_results(out_dir):
    """(label, path) of the files of the newest finished run in out_dir (from its <name>_openems.json)."""
    sums = sorted(glob.glob(os.path.join(out_dir, '*_openems.json')), key=os.path.getmtime)
    if not sums:
        return []
    with open(sums[-1]) as fh:
        files = json.load(fh).get('files', [])
    return [(lab, p if os.path.isabs(p) else os.path.join(out_dir, p)) for lab, p in files]


def settings_values(path):
    """The GUI's fields from a TOML config (through the same rules as the command line)."""
    from ..config import build_settings
    s = build_settings({}, path)
    out = {}
    for key, *_ in FIELDS:
        v = getattr(s, key, None)
        if isinstance(v, tuple):
            v = list(v)
        out[key] = v
    return out
