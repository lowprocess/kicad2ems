"""
The GUI's link to a run: a kicad2ems process (the command line, run as a separate program) and its
event stream (--events, JSON lines). No Qt here, so it is testable without a display.

    run = Run(settings_dict, out_dir, preview=False)
    run.argv()                     # [python, -m, kicad2ems, <out>/gui_run.toml, --events, ...]
    tail = EventTail(path); tail.read() -> [event dicts]
    stop_runs(out_dir)             # STOP for the run (stop.py), ABORT into every openEMS run folder
"""
import glob, json, os, sys

# the option groups (options.Option.group) as the form's group boxes; others under their own name
GUI_GROUPS = {'board': 'Board', 'band': 'Frequency', 'solvers': 'Solvers', 'output': 'Output', 'mom25d': 'Solvers',
              'line model': 'Solvers', 'openEMS antenna mode': 'openEMS', 'openEMS field dumps': 'Field dumps'}
TICK = 'solver:'                                          # a solver's tick box: solver:<name>


def _kind(o):
    """The form widget of an option: file / dir / bool / float / int / list / str, or a tuple of choices."""
    if o.key == 'out':
        return 'dir'
    if o.path:
        return 'file'
    if o.choices:
        return (('',) if o.default is None else ()) + tuple(o.choices)
    return {bool: 'bool', float: 'float', int: 'int', list: 'list'}.get(o.type, 'str')


def make_fields():
    """The settings the GUI edits, in form order: (key, label, kind, group). Made from the options that have a GUI
    label (config.CORE, then each solver's) and a tick box per solver (key solver:<name>) heading the Solvers
    group; Board and Band first, Output last."""
    from ..config import all_options
    from ..solvers import shown, registry, AUTO
    vis = shown()                                                   # experimental solvers: --experimental only
    rows = [(o.key, o.gui, _kind(o), GUI_GROUPS.get(o.group, o.group)) for name, o in all_options()
            if o.gui and (name is None or name in vis or name not in registry())]
    rows = [(TICK + AUTO, 'auto: fastest full-wave + line model', 'bool', 'Solvers')] + \
           [(TICK + n, sv.label, 'bool', 'Solvers') for n, sv in vis.items()] + rows
    order = ['Board', 'Frequency', 'Solvers'] + [g for *_, g in rows if g not in ('Board', 'Frequency', 'Solvers', 'Output')]
    rank = {g: i for i, g in enumerate(dict.fromkeys(order + ['Output']))}
    return sorted(rows, key=lambda r: rank[r[3]])                 # stable: the option order within a group


FIELDS = make_fields()
SEMICOLON_LISTS = ('ports', 'dump')                       # items that may hold commas themselves
# the tick boxes are not settings of their own: the form's values carry solvers = the ticked solvers (see
# solvers_from_form). 'solvers', 'mom' and 'fast' belong to the boxes, like the fields
MANAGED = {k for k, *_ in FIELDS if not k.startswith(TICK)} | {'solvers', 'mom', 'fast'}


def solver_ticks(vals):
    """{solver:<name>: ticked} for a settings dict: the solvers its plan runs (openEMS by default; solvers, mom =
    true and fast name the others; 'lines' as resolved here). solvers = [] ticks none."""
    from ..config import build_settings
    from ..solvers import shown, resolve_lines, LINES, AUTO
    if vals.get('solvers') == []:
        run = []
    else:
        s = build_settings({k: vals.get(k) for k in ('solvers', 'mom', 'fast') if vals.get(k) not in (None, '')})
        run = s.solver_plan(preview=False)[0]
        run = [resolve_lines(s) if x == LINES else x for x in run]
    return {TICK + n: n in run for n in [AUTO] + list(shown())}


def solvers_from_form(vals):
    """The form's values (with the solver tick boxes) as settings: solvers lists the ticked solvers, or is left to
    its default (auto) when that alone is ticked; an empty list when none is. Without tick boxes in vals
    (settings rather than the form's own values), vals as they are."""
    out = dict(vals)
    ticks = [k for k in out if k.startswith(TICK)]
    if not ticks:
        return out
    on = [k[len(TICK):] for k in ticks if out.pop(k)]
    out.pop('mom', None)
    out.pop('fast', None)
    out.pop('solvers', None)
    if on != ['auto']:
        out['solvers'] = on
    return out


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
    lines = ["# written by the kicad2ems GUI"]
    for k, v in solvers_from_form(values).items():
        if v is None or v == '' or (v == [] and k != 'solvers') or (k in ('cut_ports', 'mom') and v is False):
            continue
        lines.append(f"{k} = {toml_value(v)}")
    with open(path, 'w') as fh:
        fh.write("\n".join(lines) + "\n")


def _rel(path, base):
    """A path written relative to the config's folder where that is possible (portable configs)."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:                                     # another drive (Windows)
        return path
    return path if rel.startswith(os.pardir + os.sep + os.pardir) else rel     # at most one folder up


def _value_end(lines, i, start):
    """Index after the last line of a value that starts at lines[i][start:] (multi-line arrays)."""
    depth, j, text = 0, i, lines[i][start:]
    while True:
        in_str = None
        for ch in text:
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in '"\'':
                in_str = ch
            elif ch == '#':
                break
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
        if depth <= 0 or j + 1 >= len(lines):
            return j + 1
        j += 1
        text = lines[j]


def save_config(path, values, template=None):
    """Write the GUI's settings to a TOML config at `path`.

    Paths are written relative to the file's folder. With `template` (the config that was loaded), its text
    is kept: comments, order and tables stay, each known key gets its new value in place (keeping a trailing
    comment), keys whose field was cleared are removed and new keys are added above the first table.
    Without a template, only the settings that differ from the defaults are written."""
    from ..config import Settings, PATH_KEYS
    base = os.path.dirname(os.path.abspath(path))
    vals = {}
    for k, v in solvers_from_form(values).items():
        if k in ('events', 'preview'):
            continue
        if k in PATH_KEYS and isinstance(v, str) and v:
            v = _rel(os.path.abspath(v), base)
        if isinstance(v, tuple):
            v = list(v)
        vals[k] = v
    empty = lambda v: v is None or v == '' or v == []
    defaults = Settings()
    if template is None or not os.path.exists(template):
        lines = ["# kicad2ems run configuration (written by the GUI); paths are relative to this file"]
        for k, v in vals.items():
            if empty(v) or (k != 'out' and getattr(defaults, k, object()) == v):
                continue
            if k == 'mask':
                lines.append(f"no_mask = {toml_value(not v)}")
            else:
                lines.append(f"{k} = {toml_value(v)}")
        text = "\n".join(lines) + "\n"
    else:
        import re
        src = open(template).read().splitlines()
        out, seen, i, first_table, table = [], set(), 0, None, {}
        key_re = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)')
        while i < len(src):
            line = src[i]
            head = re.match(r'^\s*\[\s*([^\]]+?)\s*\]', line)
            if head:
                first_table = len(out) if first_table is None else first_table
                table = _table_keys(head.group(1))
            m = key_re.match(line)
            if not m or table is None:                              # not a key, or another tool's table
                out.append(line)
                i += 1
                continue
            raw = m.group(2)
            k = raw.replace('-', '_')
            j = _value_end(src, i, m.end())
            k_val = table.get(k) if table else ('mask' if k == 'no_mask' else k)
            if k_val is None:                                      # not a setting of that solver: untouched
                out.extend(src[i:j])
                i = j
                continue
            if k_val not in vals and k_val in MANAGED:                  # the field was cleared: drop the line
                seen.add(k_val)
                i = j
                continue
            if k_val not in vals:                                  # a key the GUI does not know: untouched
                out.extend(src[i:j])
                i = j
                continue
            seen.add(k_val)
            v = vals[k_val]
            col, c = _comment(src[j - 1])
            if empty(v):
                i = j
                continue
            newv = toml_value(not v) if k == 'no_mask' else toml_value(v)
            new = f"{m.group(1)}{raw}{m.group(3)}{newv}"
            if c:                                                  # the comment stays in its column
                new = f"{new:<{col if j - 1 == i else 0}}" + ('' if len(new) < col and j - 1 == i else '  ') + c
            out.append(new)
            i = j
        extra = []
        for k, v in vals.items():
            if k in seen or empty(v) or getattr(defaults, k, object()) == v:
                continue
            extra.append(f"no_mask = {toml_value(not v)}" if k == 'mask' else f"{k} = {toml_value(v)}")
        if extra:
            at = first_table if first_table is not None else len(out)
            while at > 0 and not out[at - 1].strip():
                at -= 1
            out[at:at] = extra
        text = "\n".join(out) + "\n"
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        fh.write(text)
    os.replace(tmp, path)


def _table_keys(name):
    """How save_config reads the keys of a TOML table: {} for one read like the top level ([openems]), {short
    name: setting} for a solver's [solver.<name>], None for another tool's."""
    from ..solvers import get
    name = name.strip().strip('"')
    if name.lower() == 'openems':
        return {}
    if name.lower().startswith('solver.'):
        try:
            sv = get(name.split('.', 1)[1].strip('"'))
        except ValueError:
            return None
        return {o.short: o.key for o in sv.options} | {o.key: o.key for o in sv.options}
    return None


def _comment(text):
    """(column, '# ...') of the trailing comment of a TOML line (outside strings), or (0, '')."""
    in_str = None
    for n, ch in enumerate(text):
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in '"\'':
            in_str = ch
        elif ch == '#':
            return n, text[n:]
    return 0, ''


class Run:
    """One kicad2ems run started from the GUI: its settings file, event file and command line."""
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
    """Ask the run writing to out_dir to stop: its STOP file (every solver checks it, stop.py), and ABORT into
    every openEMS run folder under out_dir (openEMS stops and finishes normally); returns those folders."""
    from .. import stop
    stop.request(out_dir)
    dirs = glob.glob(os.path.join(out_dir, 'openems', 'exc_*')) + \
        glob.glob(os.path.join(out_dir, 'converge', '*', 'openems', 'exc_*'))
    for d in dirs:
        open(os.path.join(d, 'ABORT'), 'w').close()
    return dirs


def extra_values(path):
    """The settings of a TOML config that the GUI has no field for (kept and passed on to its runs)."""
    from ..config import read_toml
    return {k: v for k, v in read_toml(path)[0].items() if k not in MANAGED and k not in ('events', 'preview')}


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


def settings_values(path):
    """The GUI's fields from a TOML config (through the same rules as the command line). The solver tick
    boxes show the solvers the config runs (solver_ticks)."""
    from ..config import build_settings, read_toml
    s = build_settings({}, path)
    out = {}
    for key, *_ in FIELDS:
        v = getattr(s, key, None)
        if isinstance(v, tuple):
            v = list(v)
        out[key] = v
    if 'out' not in read_toml(path)[0]:                    # the default folder: left to the run, not pinned here
        out['out'] = None
    out.update(solver_ticks(dict(solvers=s.solvers, mom=s.mom, fast=s.fast)))
    return out
