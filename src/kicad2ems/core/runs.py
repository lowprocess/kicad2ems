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

A run folder is known by its mark, which whoever starts the run leaves in it
(`kicad2ems study`, the GUI) and completes with how it ended:

    mark(folder, engine, asked, preview, config, settings)
    ended(folder, outcome)  # 'done', 'stopped', 'crashed', 'failed, code 1'
    marked(folder)          # dict(engine, questions, preview, ...), or None
    info(folder)            # RunInfo: what the folder is, read from its mark
    index(base, board=None, running=())
                            # [RunInfo] of the runs under base, the newest
                            # first, the board's preview last
    latest_results(base, board, config=None)
                            # {analysis: its newest finished run's folder}
"""
import datetime, glob, json, os, re, sys, time
from dataclasses import dataclass

__all__ = ['toml_value', 'write_toml', 'load_toml', 'Run', 'read_summary',
           'EventTail', 'stop_runs', 'board_name', 'run_dir', 'preview_dir',
           'tool_dir', 'tool_preview_dir', 'find_results', 'update_toml',
           'MARK', 'mark', 'marked', 'ended', 'outcome', 'RunInfo', 'info',
           'index', 'latest_results', 'run_log', 'same_config', 'when_text',
           'process_children', 'descendants', 'tree_of', 'kill_tree']

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
    lines = ["# a run's settings, written by kicad2ems"]
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


def process_children(proc='/proc'):
    """{parent pid: [child pids]} of every process: Linux's /proc, else ps
    (macOS, the BSDs); {} where neither tells."""
    kids = {}
    if os.path.isdir(proc):
        for d in os.listdir(proc):
            if not d.isdigit():
                continue
            try:
                with open(os.path.join(proc, d, 'stat'),
                          encoding='utf-8', errors='replace') as fh:
                    stat = fh.read()
                # the name may hold spaces and brackets
                ppid = int(stat[stat.rindex(')') + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            kids.setdefault(ppid, []).append(int(d))
    elif os.name == 'posix':
        import subprocess
        try:
            text = subprocess.run(['ps', '-A', '-o', 'pid=', '-o', 'ppid='],
                                  capture_output=True, text=True,
                                  timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return kids
        for line in text.splitlines():
            try:
                pid, ppid = (int(x) for x in line.split())
            except ValueError:
                continue
            kids.setdefault(ppid, []).append(pid)
    return kids


def descendants(pid, proc='/proc'):
    """Every process below pid, by parent pid whatever its session or group
    (fem's Palace and Elmer ranks run in sessions of their own), nearest
    first; [] where it cannot be told, and for no process (pid 0 or 1: the
    whole system is below them)."""
    if not pid or int(pid) <= 1:
        return []
    kids, out, todo = process_children(proc), [], [int(pid)]
    while todo:
        for c in kids.get(todo.pop(0), []):
            if c != pid and c not in out:
                out.append(c)
                todo.append(c)
    return out


def _started(pid, proc='/proc'):
    """When a process started (Linux: its stat's starttime, in clock ticks),
    or None: a pid with its start is that process and no later one."""
    try:
        with open(os.path.join(proc, str(pid), 'stat'), encoding='utf-8',
                  errors='replace') as fh:
            stat = fh.read()
        return int(stat[stat.rindex(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def tree_of(pid, proc='/proc'):
    """[(pid, its start)] of every process below pid now (descendants), for a
    kill_tree later: a tool asked to stop may end and leave its children to
    init before it is killed (Linux: each with its start, so that a pid used
    again by another process is not touched; elsewhere [])."""
    out = [(p, _started(p, proc)) for p in descendants(pid, proc)]
    return [(p, t) for p, t in out if t is not None]


def kill_tree(pid, also=()):
    """A run's process and everything below it ended now (SIGKILL; Windows:
    taskkill /T /F): its workers, its openEMS, an analysis's MPI ranks in
    sessions of their own, which killing the process alone had left running
    (their guard's atexit and SIGTERM handler never run under SIGKILL). also:
    tree_of's [(pid, start)] from before (each only while it is that same
    process). Only pid's own descendants, never processes found by name.
    Returns the pids signalled."""
    import signal
    import subprocess
    if os.name == 'nt':
        if pid:
            subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'],
                           capture_output=True,
                           creationflags=getattr(subprocess,
                                                 'CREATE_NO_WINDOW', 0))
        return [int(pid)] if pid else []
    # (the parent first: it starts no more; pid 0: the process has ended,
    # what it left in `also` only)
    todo = [int(pid)] + descendants(pid) if pid and int(pid) > 1 else []
    todo += [p for p, t in also
             if p > 1 and p not in todo and _started(p) == t]
    done = []
    for p in todo:
        try:
            os.kill(p, signal.SIGKILL)
            done.append(p)
        except (ProcessLookupError, PermissionError):
            pass
    return done


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
    its <name>_run.json)."""
    sums = sorted(glob.glob(os.path.join(glob.escape(out_dir), '*_run.json')),
                  key=os.path.getmtime)
    if not sums:
        return []
    with open(sums[-1], encoding='utf-8') as fh:
        files = json.load(fh).get('files', [])
    return [(lab, p if os.path.isabs(p) else os.path.join(out_dir, p))
            for lab, p in files]


# ---- a run folder's mark, and the runs known by it ----
MARK = 'kicad2ems-study.json'
RF = 'rf'  # kicad2ems's own run (the solvers together): its mark's engine
ENDED = '%Y-%m-%d %H:%M:%S'


def mark(folder, engine, asked=(), preview=False, config=None,
         settings=None):
    """Leave the mark of a run in its folder (made if it is not there): the
    engine that makes it, the questions asked of it, whether it is a preview;
    config: the config file it was started from, settings: the settings it ran
    with (a front end's form: JSON, anything else as its text), each kept when
    given. Returns the mark's path."""
    os.makedirs(folder, exist_ok=True)
    d = dict(format='kicad2ems-study-run', version=1, engine=engine,
             questions=list(asked), preview=bool(preview))
    if config:
        d['config'] = os.path.abspath(str(config))
    if settings is not None:
        d['settings'] = json.loads(json.dumps(dict(settings), default=str))
    return _write_mark(folder, d)


def _write_mark(folder, d):
    path = os.path.join(folder, MARK)
    with open(path + '.tmp', 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1)
    os.replace(path + '.tmp', path)  # (the folder's time: the index reads it)
    return path


def outcome(code, stopped=False, crashed=False):
    """How a run ended, as its mark keeps it: 'done', 'stopped', 'crashed',
    'failed, code 1'."""
    return ('stopped' if stopped else 'crashed' if crashed else
            'done' if code == 0 else f"failed, code {code}")


def ended(folder, how):
    """A marked run's end into its mark: how (outcome's words) and when."""
    try:
        with open(os.path.join(folder, MARK), encoding='utf-8') as fh:
            d = json.load(fh)
        _write_mark(folder, dict(d, outcome=how, ended=time.strftime(ENDED)))
    except (OSError, ValueError):
        pass


def marked(folder):
    """The mark of a run's folder: dict(engine, questions, preview), with
    config, settings, outcome and ended when it has them; None (no mark, or not
    readable)."""
    try:
        with open(os.path.join(folder or '', MARK), encoding='utf-8') as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(d, dict) or d.get('format') != 'kicad2ems-study-run'
            or not d.get('engine')):
        return None
    out = dict(engine=str(d['engine']),
               questions=tuple(d.get('questions') or ()),
               preview=bool(d.get('preview')))
    for k in ('config', 'outcome', 'ended'):
        if d.get(k):
            out[k] = str(d[k])
    if isinstance(d.get('settings'), dict):
        out['settings'] = d['settings']
    return out


@dataclass
class RunInfo:
    """A run folder as its mark says."""
    folder: str
    kind: str              # 'rf' (kicad2ems's own) or an analysis's name
    board: str             # the board's name (board_name)
    when: datetime.datetime
    what: str = ''         # what an analysis solved: the rail, the converter
    questions: tuple = ()
    engines: tuple = ()
    # 'done', 'running', 'stopped', 'crashed', 'failed, code 1', 'no result'
    outcome: str = 'done'
    took: float = None     # s
    size: int = 0          # bytes
    settings: dict = None  # what it ran with, None: not known
    config: str = None     # the config file it was started from
    preview: bool = False

    @property
    def ok(self):
        return self.outcome == 'done'

    def about(self):
        """What it answered: 'S-parameters', 'DC drop · P'."""
        from .study import questions
        qs = questions()
        return ' · '.join(x for x in (', '.join(
            qs[q].label if q in qs else q for q in self.questions), self.what)
                          if x)

    def short(self):
        """'14:02:31 · gfdtd (GPU FDTD)': a curve's source when compared."""
        return ' · '.join(x for x in (when_text(self.when, seconds=True),
                                      self.what, self.engine_labels()) if x)

    def engine_labels(self):
        from .study import engines
        es = engines()
        return ', '.join(es[n].label if n in es else n for n in self.engines)

    def label(self, now=None):
        """One line: '14:02 · S-parameters · gfdtd'; 'preview'."""
        if self.preview:
            return 'preview'
        return ' · '.join(x for x in (when_text(self.when, now), self.about(),
                                      self.engine_labels()) if x)


def when_text(when, now=None, seconds=False):
    """'14:02' today, '11-17 14:02' this year, else '2025-11-17 14:02'
    (seconds: '14:02:31')."""
    if when is None:
        return ''
    now = now or datetime.datetime.now()
    t = f"{when:%H:%M:%S}" if seconds else f"{when:%H:%M}"
    if when.date() == now.date():
        return t
    return (f"{when:%m-%d} {t}" if when.year == now.year else
            f"{when:%Y-%m-%d} {t}")


def run_log(folder):
    """A run's log as text: the events' log lines, else its process's whole
    output (stdout.log); '' if neither."""
    lines = []
    for f in sorted(
            glob.glob(os.path.join(glob.escape(folder), '*events*.jsonl'))):
        try:
            for raw in open(f, encoding='utf-8', errors='replace'):
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if ev.get('kind') == 'log':
                    lines.append(ev.get('text', ''))
        except OSError:
            pass
    if not lines and os.path.isfile(os.path.join(folder, 'stdout.log')):
        return open(os.path.join(folder, 'stdout.log'), encoding='utf-8',
                    errors='replace').read()
    return '\n'.join(lines)


def _walk(d):
    """(bytes under d, the newest time of its own entries)."""
    size, last, stack, top = 0, 0.0, [d], True
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        size += st.st_size
                    if top:
                        last = max(last, st.st_mtime)
                except OSError:
                    pass
        top = False
    return size, last


def _parse(name, want, kinds):
    """(board, kind, what, when) of a folder's name, or None: not a run's.
    want: the board's name (None: any board's); kinds: the analyses', the
    longest first (elmer_thermal before thermal). A preview's kind:
    'preview'."""
    m = re.match(r'(.+)_(\d{8}_\d{6})$', name)
    if m:
        prefix = m.group(1)
        when = datetime.datetime.strptime(m.group(2), STAMP)
        if want is not None:
            if prefix == want:
                return want, RF, '', when
            for k in kinds:
                if prefix.startswith(f"{want}_{k}_"):
                    return want, k, prefix[len(want) + len(k) + 2:], when
            return None
        for k in kinds:
            i = prefix.find(f"_{k}_")
            if i > 0:
                return prefix[:i], k, prefix[i + len(k) + 2:], when
        return prefix, RF, '', when
    if name.endswith('_preview'):
        prefix = name[:-len('_preview')]
        if want is not None:
            return (want, 'preview', '', None) if prefix == want else None
        if not any(prefix.endswith('_' + k) for k in kinds):
            return prefix, 'preview', '', None
    return None


def _has_result(d, kind, preview):
    """Whether a folder holds the result of a run of that kind: kicad2ems's own
    summary, or what the analysis says it leaves (Analysis.owns)."""
    if kind == RF:
        at = lambda pat: glob.glob(os.path.join(glob.escape(d), pat))
        return bool(preview or at('*_run.json') or at('*_vary.json'))
    from .analyses import registry
    a = registry().get(kind)
    return a is not None and a.owns(d, preview)


def _identity(d, named):
    """(kind, preview, outcome, mark) of a folder: its mark's. A run's own
    outcome is kept there when it ends; a mark written before it was (and a
    folder made before marks were, the one-time reader of those): its result
    there or not. named: the kind its name says, or None."""
    from .study import engines
    m = marked(d)
    if m is not None:
        e = engines().get(m['engine'])
        kind = (getattr(e, 'run', '') or m['engine']) if e else m['engine']
        preview = m['preview']
    else:  # before marks: the name, else whose result it holds
        from .analyses import registry
        kind, preview = (RF, True) if named == 'preview' else (named, False)
        if named is None:
            kind, preview = next(
                ((k, p) for k, a in registry().items()
                 for p in (False, True) if a.owns(d, p)), (None, False))
            if kind is None:
                return None
        m = {}
    how = m.get('outcome') or ('done' if _has_result(d, kind, preview)
                               else 'no result')
    return kind, preview, how, m


# folder: (its time, RunInfo)
_SEEN = {}


def _read(d, board, named, what, when, live):
    """A run folder's RunInfo, from its mark, its settings, its summary and its
    size; None: not a run's."""
    got = _identity(d, named)
    if got is None:
        return None
    kind, preview, how, m = got
    settings = m.get('settings')
    if kind == RF:
        if os.path.isfile(os.path.join(d, 'gui_run.toml')):
            try:
                settings = load_toml(os.path.join(d, 'gui_run.toml'))
            except (OSError, ValueError):
                pass
        engines = ()
        for summary in glob.glob(os.path.join(glob.escape(d), '*_run.json')):
            try:
                engines = tuple(read_summary(summary).get('solvers') or ())
            except (OSError, ValueError):
                pass
        if (not engines and settings
                and isinstance(settings.get('solvers'), list)):
            engines = tuple(settings['solvers'])
        questions = m.get('questions') or ('sparams', )
    else:
        from .analyses import registry
        from .study import provided
        engines = (m.get('engine') or kind, )
        a = registry().get(kind)
        questions = m.get('questions') or (provided(a) if a else (kind, ))
    size, last = _walk(d)
    if when is None:  # a preview, a folder named otherwise
        when = datetime.datetime.fromtimestamp(os.path.getmtime(d))
    took = None
    if live:
        took = time.time() - when.timestamp()
    elif m.get('ended'):
        try:
            took = (datetime.datetime.strptime(m['ended'], ENDED)
                    - when).total_seconds()
        except ValueError:
            pass
    elif last and not preview:
        took = last - when.timestamp()
    if took is not None and took < 0:
        took = None
    return RunInfo(d, kind, board, when, what, tuple(questions), engines,
                   'running' if live else how, took, size, settings,
                   m.get('config'), preview)


def _info(d, board, named, what, when, running=()):
    """_read's, kept while the folder and its own files are unchanged (a
    result rewritten in place leaves the folder's time as it was)."""
    try:
        with os.scandir(d) as it:
            stamp = max([os.stat(d).st_mtime_ns]
                        + [e.stat().st_mtime_ns for e in it if e.is_file()])
    except OSError:
        return None
    live = d in running
    hit = _SEEN.get(d)
    if hit is not None and hit[0] == stamp and not live:
        return hit[1]
    got = _read(d, board, named, what, when, live)
    if not live:
        _SEEN[d] = (stamp, got)
    return got


def _kinds():
    from .analyses import registry
    return sorted(registry(), key=len, reverse=True)


def info(folder):
    """The RunInfo of one run folder, wherever it is (None: not a run's)."""
    d = os.path.abspath(str(folder or ''))
    if not os.path.isdir(d):
        return None
    got = _parse(os.path.basename(d), None, _kinds())
    return _info(d, *(got or ('', None, '', None)))


def index(base, board=None, running=()):
    """The runs under base: of the board (None: of every board), the newest
    first and the board's preview last; running: the folders of runs going now
    (only the front end that started them knows)."""
    running = {os.path.abspath(str(d)) for d in running if d}
    kinds = _kinds()
    want = board_name(board) if board else None
    out, previews = [], []
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    for n in names:
        got = _parse(n, want, kinds)
        d = os.path.abspath(os.path.join(base, n))
        if got is None or not os.path.isdir(d):
            continue
        r = _info(d, *got, running)
        if r is not None:
            (previews if r.preview else out).append(r)
    out.sort(key=lambda r: (r.when, os.path.basename(r.folder)), reverse=True)
    return out + sorted(previews, key=lambda r: r.board)


def same_config(a, b):
    """Whether two runs' configs are the same file (None: none, the form as it
    was not saved, or a run from before the mark kept one)."""
    if not a or not b:
        return not a and not b
    return (os.path.normcase(os.path.abspath(a))
            == os.path.normcase(os.path.abspath(b)))


def latest_results(base, board, config=None, runs=None):
    """{analysis: the folder of its newest finished run on this board under
    base}, a run of `config` (None: of none) before the others: what an
    analysis that starts from another's result is given (Analysis.command's
    results). runs: the index, when read already."""
    rs = index(base, board) if runs is None else runs
    out = {}
    for r in [r for r in rs if same_config(r.config, config)] + rs:
        if r.ok and not r.preview and r.kind != RF:
            out.setdefault(r.kind, r.folder)
    return out
