"""
Where the GUI's runs go: each in a folder of its own under the output folder,
named after the board and the date and time it started, and a preview in one
folder per board that each preview refreshes. A folder then only ever holds one
board's results (a shared one had shown another board's dumps after opening a
config).

    run_dir(base, board)      # <base>/<board>_20261013_141502
    preview_dir(base, board)  # <base>/<board>_preview
    tool_dir(base, board, kind, name)
        # an analysis's: <base>/<board>_pdn_<rail>_20261013_141502
    tool_preview_dir(base, board, kind)  # <base>/<board>_mqs_preview

The runs are known by one index, which the Runs page, the toolbar's Showing,
the steps bar, the study's list and the second Viewer all read:

    index(base, board=None, running=())
        # [RunInfo], the newest first, the board's preview last: every run of
        # the board under base (board None: of every board), the analyses'
        # older results beside the board (pdn_<rail>, mqs_<converter>) too
    in_scope(runs, 'config', config)     # those of a config: see in_scope
    differ(old, now)                     # [(key, old, now)]: settings changed

A run is read from the small files it leaves: its mark (who made it, what was
asked, the config and settings it was started with: core.study.mark), its
settings (gui_run.toml), its summary (<name>_run.json), how the window saw it
end (EXIT); and kept while its folder's time is unchanged (one running is read
each time). Its size is the one thing walked.
"""
import datetime, json, os, re, time
from dataclasses import dataclass
# the folders' names are kicad2ems's (its `kicad2ems study` puts its runs there
# too): kicad2ems.core.runs
from kicad2ems.core.api.frontend import (  # noqa: F401
    board_name, run_dir, preview_dir, tool_dir, tool_preview_dir)

STAMP = '%Y%m%d_%H%M%S'
# how the window's run of it ended: {code, crashed, stopped, ended}
EXIT = 'run_exit.json'
SCOPES = ('config', 'board', 'all')
# settings that say nothing of the run: where it went, its event file
NOISE = ('out', 'events', 'preview')


@dataclass
class RunInfo:
    """A run folder as the index read it."""
    folder: str
    kind: str              # 'rf' (kicad2ems's own), 'preview', an analysis's
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

    @property
    def ok(self):
        return self.outcome == 'done'

    @property
    def preview(self):
        return self.kind == 'preview'

    def about(self):
        """What it answered: 'S-parameters', 'DC drop · P'."""
        qs = ', '.join(question_label(q) for q in self.questions)
        return ' · '.join(x for x in (qs, self.what) if x)

    def short(self):
        """'14:02:31 · gfdtd (GPU FDTD)': a curve's source when compared."""
        return ' · '.join(x for x in (when_text(self.when, seconds=True),
                                      self.what, ', '.join(map(
                                          engine_label, self.engines))) if x)

    def label(self, now=None):
        """One line: '14:02 · S-parameters · gfdtd'; 'preview'."""
        if self.preview:
            return 'preview'
        return ' · '.join(x for x in (when_text(self.when, now), self.about(),
                                      ', '.join(map(engine_label,
                                                    self.engines))) if x)


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


def question_label(q):
    from kicad2ems.core.study import questions
    got = questions().get(q)
    return got.label if got is not None else q


def engine_label(name):
    from kicad2ems.core.study import engines
    got = engines().get(name)
    return got.label if got is not None else name


def tool_titles():
    """{kind: title} of the analyses whose runs are listed: those kicad2ems's
    registry holds, in its order."""
    from kicad2ems.core.api.frontend import analyses
    return {name: a.label for name, a in analyses().items()}


def tool_kind(folder):
    """The analysis ('pdn', 'thermal' ...) whose finished result the folder
    holds, else None (a preview's too)."""
    from kicad2ems.core.api.frontend import analysis_of
    made = analysis_of(folder or '')
    return made[0] if made and not made[1] else None


def _json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _outcome(e, has):
    if e.get('stopped'):
        return 'stopped'
    if e.get('crashed'):
        return 'crashed'
    if e.get('code'):
        return f"failed, code {e.get('code')}"
    return 'done' if has else 'no result'


def ending(folder):
    """A run folder with no results: how it ended ('stopped', 'failed, code 1',
    'crashed', 'no result': the window did not see its end)."""
    return _outcome(_json(os.path.join(folder, EXIT)), False)


def run_log(folder):
    """A run's log as text: the events' log lines, else its process's whole
    output (stdout.log); '' if neither."""
    import glob
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
    longest first (elmer_thermal before thermal)."""
    m = re.match(r'(.+)_(\d{8}_\d{6})$', name)
    if m:
        prefix = m.group(1)
        when = datetime.datetime.strptime(m.group(2), STAMP)
        if want is not None:
            if prefix == want:
                return want, 'rf', '', when
            for k in kinds:
                if prefix.startswith(f"{want}_{k}_"):
                    return want, k, prefix[len(want) + len(k) + 2:], when
            return None
        for k in kinds:
            i = prefix.find(f"_{k}_")
            if i > 0:
                return prefix[:i], k, prefix[i + len(k) + 2:], when
        return prefix, 'rf', '', when
    if name.endswith('_preview'):
        prefix = name[:-len('_preview')]
        if want is not None:
            return (want, 'preview', '', None) if prefix == want else None
        if not any(prefix.endswith('_' + k) for k in kinds):
            return prefix, 'preview', '', None
    return None


# folder: (its time, RunInfo)
_SEEN = {}


def _read(d, board, kind, what, when, live):
    """A run folder's RunInfo, from its small files and its size."""
    from kicad2ems.core.study import marked, provided
    mark = marked(d) or {}
    e = _json(os.path.join(d, EXIT))
    settings = mark.get('settings')
    if kind in ('rf', 'preview'):
        toml = os.path.join(d, 'gui_run.toml')
        if os.path.isfile(toml):
            from kicad2ems.core.runs import load_toml
            try:
                settings = load_toml(toml)
            except (OSError, ValueError):
                pass
        summary = os.path.join(d, f"{board}_run.json")
        has = (os.path.isfile(summary)
               or os.path.isfile(os.path.join(d, f"{board}_vary.json")))
        engines = ()
        if os.path.isfile(summary):
            try:
                from kicad2ems.core.api.frontend import read_summary
                engines = tuple(read_summary(summary).get('solvers') or ())
            except (OSError, ValueError):
                pass
        if (not engines and settings
                and isinstance(settings.get('solvers'), list)):
            engines = tuple(settings['solvers'])
        questions = mark.get('questions') or ('sparams', )
    else:
        has = tool_kind(d) == kind
        engines = (mark.get('engine') or kind, )
        from kicad2ems.core.api.frontend import analyses
        a = analyses().get(kind)
        questions = mark.get('questions') or (provided(a) if a else (kind, ))
    size, last = _walk(d)
    if when is None:  # a preview, an older result beside the board
        when = datetime.datetime.fromtimestamp(os.path.getmtime(d))
    took = None
    if live:
        took = time.time() - when.timestamp()
    elif e.get('ended'):
        try:
            took = (datetime.datetime.strptime(e['ended'], '%Y-%m-%d %H:%M:%S')
                    - when).total_seconds()
        except ValueError:
            pass
    elif last and kind != 'preview':
        took = last - when.timestamp()
    if took is not None and took < 0:
        took = None
    return RunInfo(d, kind, board, when, what, tuple(questions), engines,
                   'running' if live else _outcome(e, has), took, size,
                   settings, mark.get('config'))


def _info(d, board, kind, what, when, running):
    try:
        stamp = os.stat(d).st_mtime_ns
    except OSError:
        return None
    live = d in running
    hit = _SEEN.get(d)
    if hit is not None and hit[0] == stamp and not live:
        return hit[1]
    info = _read(d, board, kind, what, when, live)
    if not live:
        _SEEN[d] = (stamp, info)
    return info


def index(base, board=None, running=()):
    """The runs under base: of the board (None: of every board), the newest
    first and the board's preview last; running: the folders of runs going now
    (only the window knows)."""
    running = {os.path.abspath(str(d)) for d in running if d}
    kinds = sorted(tool_titles(), key=len, reverse=True)
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
        info = _info(d, *got, running)
        if info is not None:
            (previews if info.preview else out).append(info)
    beside = os.path.dirname(os.path.abspath(board)) if board else None
    for k in kinds if beside else ():  # where the analyses wrote before
        try:
            found = [n for n in os.listdir(beside) if n.startswith(k + '_')]
        except OSError:
            break
        for n in found:
            d = os.path.join(beside, n)
            if os.path.isdir(d) and tool_kind(d) == k:
                info = _info(d, want, k, n[len(k) + 1:], None, running)
                if info is not None:
                    out.append(info)
    out.sort(key=lambda r: (r.when, os.path.basename(r.folder)), reverse=True)
    return out + sorted(previews, key=lambda r: r.board)


def same_config(a, b):
    """Whether two runs' configs are the same file (None: none, the form as it
    was not saved, or a run from before the mark kept one)."""
    if not a or not b:
        return not a and not b
    return (os.path.normcase(os.path.abspath(a))
            == os.path.normcase(os.path.abspath(b)))


def in_scope(runs, scope, config=None):
    """The runs a scope shows: 'config', those started from the config (the
    file their mark names: with no config, an unsaved form's and the runs from
    before the mark kept one); 'board', 'all': as listed. The preview:
    always."""
    if scope != 'config':
        return list(runs)
    return [r for r in runs if r.preview or same_config(r.config, config)]


def _plain(k, v):
    if isinstance(v, (list, tuple)):
        v = [_plain('', x) for x in v]
        return sorted(v, key=str) if k == 'solvers' else v
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    return str(v)


def norm(values):
    """Settings as a run's file keeps them (core.runs.write_toml): no empty
    ones, no noise (NOISE), the solvers in no order."""
    out = {}
    for k, v in (values or {}).items():
        if (k in NOISE or v is None or v == '' or (v == [] and k != 'solvers')
                or (k in ('cut_ports', 'mom') and v is False)):
            continue
        out[k] = _plain(k, v)
    return out


def differ(old, now):
    """[(key, old, now)] of the settings that differ (both as norm has them;
    None: not set)."""
    a, b = norm(old), norm(now)
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
            if a.get(k) != b.get(k)]


def size_text(n):
    for unit, f in (('GB', 1e9), ('MB', 1e6), ('kB', 1e3)):
        if n >= f:
            return f"{n / f:.{0 if n >= 10 * f else 1}f} {unit}"
    return f"{n} B"


def took_text(s):
    if s is None:
        return ''
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"
