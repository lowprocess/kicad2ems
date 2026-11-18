"""
A kicad2ems run started by another program (kicad2ems-gui, the KiCad plugin):
its settings as a TOML file, the command line (run as a separate process), its
event stream (--events, JSON lines), stopping it and the files it wrote. No Qt
here.

    run = Run(settings_dict, out_dir, preview=False)
    run.argv()             # [python, -m, kicad2ems, <out>/gui_run.toml,
                           #  --events, ...]
    tail = EventTail(path); tail.read() -> [event dicts]
    read_summary(path)     # a <name>_run.json, its format checked (formats.py)
    stop_runs(out_dir)     # STOP for the run (stop.py), ABORT into every FDTD
                           # run folder
    find_results(out_dir)  # [(label, path)] of the newest finished run's files
    run_dir(base, board), preview_dir(base, board),
    tool_dir(base, board, analysis, what), tool_preview_dir(...)
                           # where a front end's runs go: a folder each under
                           # the output folder
    write_toml(path, settings); toml_value(v); load_toml(path)
    update_toml(path, keys, tables)  # a config edited, its comments kept
"""
import datetime, glob, json, os, re, sys

__all__ = ['toml_value', 'write_toml', 'load_toml', 'Run', 'read_summary',
           'EventTail', 'stop_runs', 'board_name', 'run_dir', 'preview_dir',
           'tool_dir', 'tool_preview_dir', 'find_results', 'update_toml']

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def toml_value(v):
    """A value as TOML text: booleans, numbers and lists of them as they are,
    anything else a quoted string."""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(toml_value(x) for x in v) + ']'
    return json.dumps(str(v))  # a TOML basic string is a JSON string


def write_toml(path, values):
    """A flat TOML file of the non-empty settings (solvers = [] kept: no solver,
    as the GUI says so)."""
    lines = ["# written by the kicad2ems GUI"]
    for k, v in values.items():
        if (v is None or v == '' or (v == [] and k != 'solvers')
                or (k in ('cut_ports', 'mom') and v is False)):
            continue
        lines.append(f"{k} = {toml_value(v)}")
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write("\n".join(lines) + "\n")


def load_toml(path):
    """A TOML file's tables; OSError, or tomllib.TOMLDecodeError (a
    ValueError)."""
    with open(path, 'rb') as fh:
        return tomllib.load(fh)

_HEAD = re.compile(r'^\s*\[\[?\s*([^\[\]]+?)\s*\]')
_KEY = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)')


def _scan(text, start=0, depth=None):
    """Index of the comment of a TOML line (outside strings), or -1; with
    depth, that plus the line's open minus closed brackets."""
    quote = None
    for n, ch in enumerate(text[start:], start):
        if quote:
            quote = None if ch == quote else quote
        elif ch in '"\'':
            quote = ch
        elif ch == '#':
            return n if depth is None else depth
        elif depth is not None:
            depth += (ch == '[') - (ch == ']')
    return -1 if depth is None else depth


def _is_tables(v):
    return isinstance(v, dict) or (isinstance(v, list) and bool(v) and all(
        isinstance(x, dict) for x in v))


def _table_text(name, tab, head=None):
    """[name] with tab's values, then its sub-tables: a dict [name.key], a list
    of them [[name.key]] (a list of one: [name.key]); empty ones left out."""
    plain = [f"{k} = {toml_value(v)}" for k, v in tab.items()
             if not _is_tables(v)]
    out = [head or f"[{name}]", *plain, ''] if plain or head else []
    for k, v in tab.items():
        many = [t for t in (v if isinstance(v, list) else [v])
                if _is_tables(v) and t]
        for t in many:
            sub = f"{name}.{k}"
            out += _table_text(sub, t, f"[[{sub}]]" if len(many) > 1 else None)
    return out


def update_toml(path, keys=None, tables=None, source=None, head=()):
    """Write the TOML file at `path` from the text of `source` (default: the
    file itself; neither there: the comment lines `head`), keeping its comments,
    order and every line not named here:

        keys    {(table, key): value}: the key's value set in place, its
                trailing comment kept; None: its line removed. A new key goes
                after the last one of its table ('': the top level, above the
                first table), or into a new table at the end.
        tables  {name: {key: value | {table} | [{table}, ...]}}: [name] and
                every [name.*] and [[name.*]] replaced by these, where the
                first of them was (else at the end); {}: taken out.

    Not written when nothing changes."""
    src = source if source and os.path.exists(source) else path
    text = (open(src, encoding='utf-8').read() if os.path.exists(src) else
            '\n'.join(head))
    lines, keys = text.splitlines(), dict(keys or {})
    tables = dict(tables or {})
    out, table, skip, ends, at, i = [], '', None, {'': None}, {}, 0
    while i < len(lines):
        line, h = lines[i], _HEAD.match(lines[i])
        if h:
            table = h.group(1).replace('"', '').replace(' ', '')
            ends[''] = len(out) if ends[''] is None else ends['']
            skip = next((n for n in tables
                         if table == n or table.startswith(n + '.')), None)
            if skip:
                at.setdefault(skip, len(out))
            else:
                out.append(line)
                ends[table] = len(out)
            i += 1
            continue
        m = _KEY.match(line)  # (a skipped table's too: its arrays' lines)
        j, depth = i + 1, _scan(line, m.end(), 0) if m else 0
        while depth > 0 and j < len(lines):
            depth, j = _scan(lines[j], 0, depth), j + 1
        if skip:
            pass
        elif m and (table, m.group(2)) in keys:
            v = keys.pop((table, m.group(2)))
            if v is not None:
                new = f"{m.group(1)}{m.group(2)}{m.group(3)}{toml_value(v)}"
                c, one = _scan(lines[j - 1]), j == i + 1
                if c >= 0:  # the comment stays in its column
                    new = (f"{new:<{c if one else 0}}"
                           + ('' if len(new) < c and one else '  ')
                           + lines[j - 1][c:])
                out.append(new)
        else:
            out.extend(lines[i:j])
        if m and not skip:
            ends[table] = len(out)
        i = j
    top = len(out) if ends[''] is None else ends['']
    adds = {}  # {(index in out, a top-level key's): lines}
    for (t, k), v in keys.items():
        if v is not None and t in ends:
            adds.setdefault((ends[t] if t else top, not t), []).append(
                f"{k} = {toml_value(v)}")
        elif v is not None:
            tables.setdefault(t, {})[k] = v
    for name, tab in tables.items():
        block = _table_text(name, tab)
        if name in at or block:
            adds.setdefault((at.get(name, len(out) + 1), False), []).extend(
                block)
    for (n, key_at_top), new in sorted(adds.items(), reverse=True,
                                       key=lambda a: (a[0][0], not a[0][1])):
        if n > len(out):  # the end
            while out and not out[-1].strip():
                out.pop()
            out += ([''] if out else []) + new
            continue
        while key_at_top and n > 0 and not out[n - 1].strip():
            n -= 1  # after the last top-level key, not the gap
        out[n:n] = new
    new_text = '\n'.join(out).rstrip('\n') + '\n'
    if new_text == text and src == path:
        return
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(new_text)
    os.replace(tmp, path)


class Run:
    """One kicad2ems run: its settings file, event file and command line."""
    def __init__(self, values, out_dir, preview=False):
        self.values, self.out, self.preview = (dict(values),
                                               os.path.abspath(out_dir),
                                               preview)
        os.makedirs(self.out, exist_ok=True)
        self.toml = os.path.join(self.out, 'gui_run.toml')
        self.events = os.path.join(self.out, 'gui_events.jsonl')
        write_toml(self.toml, dict(self.values, out=self.out))
        if os.path.exists(self.events):
            os.remove(self.events)

    def argv(self):
        """The run's command line: this Python, -m kicad2ems, its TOML file and
        events file, --preview for a preview."""
        return ([
            sys.executable, '-m', 'kicad2ems', self.toml, '--events',
            self.events
        ] + (['--preview'] if self.preview else []))


def read_summary(path):
    """A run summary (<name>_run.json), checked: ValueError when this kicad2ems
    cannot read its format."""
    from .formats import read
    return read(path, 'kicad2ems-run')


class EventTail:
    """Reads the new complete lines of an events file on each call. The stream's
    first line names its format (formats.py): it is not passed on, and a format
    this kicad2ems cannot read comes out as a log line saying so."""
    def __init__(self, path):
        self.path, self.pos = path, 0

    def read(self):
        """The events written since the last call (whole lines only), as dicts;
        [] while the file is not there."""
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
                    out.append(
                        dict(t=e.get('t', 0.0), kind='log',
                             text=f"WARNING: the run's events: {why}"))
                continue
            out.append(e)
        return out


def stop_runs(out_dir):
    """Ask the run writing to out_dir to stop: its STOP file (every solver
    checks it, stop.py), and ABORT into every FDTD run folder under out_dir
    (<out>/<engine>/exc_<port>: the engine stops and finishes normally); returns
    those folders."""
    from . import stop
    stop.request(out_dir)
    dirs = (glob.glob(os.path.join(out_dir, '*', 'exc_*'))
            + glob.glob(os.path.join(out_dir, 'converge', '*', '*', 'exc_*')))
    for d in dirs:
        open(os.path.join(d, 'ABORT'), 'w', encoding='utf-8').close()
    return dirs


STAMP = '%Y%m%d_%H%M%S'                     # a run folder's date and time


def board_name(board):
    """The name a board's run folders take: its file name without the extension
    (and a resolved board's _board)."""
    name = os.path.splitext(os.path.basename(board or 'board'))[0]
    return name[:-len('_board')] if name.endswith('_board') else name


def run_dir(base, board, now=None):
    """The folder of an S-parameter run started now:
    <base>/<board>_20261013_141502."""
    return os.path.join(
        base,
        f"{board_name(board)}_"
        f"{(now or datetime.datetime.now()).strftime(STAMP)}")


def preview_dir(base, board):
    """The folder a board's previews share (each refreshes it):
    <base>/<board>_preview."""
    return os.path.join(base, f"{board_name(board)}_preview")


def tool_dir(base, board, kind, name, now=None):
    """The folder of an analysis's run started now:
    <base>/<board>_<analysis>_<what>_20261013_141502 (what: the rail, the
    converter: Analysis.tag)."""
    what = (''.join(c if c.isalnum() else '_'
                    for c in str(name or '')).strip('_') or 'run')
    return os.path.join(
        base,
        f"{board_name(board)}_{kind}_{what}_"
        f"{(now or datetime.datetime.now()).strftime(STAMP)}")


def tool_preview_dir(base, board, kind):
    """The folder an analysis's previews of a board share:
    <base>/<board>_<analysis>_preview."""
    return os.path.join(base, f"{board_name(board)}_{kind}_preview")


def find_results(out_dir):
    """(label, path) of the files of the newest finished run in out_dir (from
    its <name>_run.json, or the <name>_openems.json of an older run)."""
    sums = sorted(
        glob.glob(os.path.join(out_dir, '*_run.json'))
        + glob.glob(os.path.join(out_dir, '*_openems.json')),
        key=os.path.getmtime)
    if not sums:
        return []
    with open(sums[-1], encoding='utf-8') as fh:
        files = json.load(fh).get('files', [])
    return [(lab, p if os.path.isabs(p) else os.path.join(out_dir, p))
            for lab, p in files]
