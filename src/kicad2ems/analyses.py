"""
The analyses, as plugins: tools that work on the same board as a run but are not S-parameter solvers (a rail's DC
drop and impedance, a converter's near field, a circuit on the board's copper, the board's temperature). Each is an
Analysis subclass registered under its name by a package that declares an entry point in the group
'kicad2ems.analyses' (`pdn = "pcbqs.analyses:PowerIntegrity"`): pcbqs's four (the qs extra), kicad2ems-fem's
(the fem extra). kicad2ems has one of its own, the crosstalk between traces (kicad2ems.si: BUILTIN, listed with
the plugins'). A front end lists what the registry holds, builds each one's settings from its options, starts it
as the command it gives and shows what its result folder holds.

    from kicad2ems.analyses import registry, analysis_of
    [a.label for a in registry().values()]
    analysis_of('out/board_pdn_3V3_20261111_120000')        # ('pdn', False): whose result, and whether a preview

An analysis declares (class attributes)
  api                     the kicad2ems.api version it is written against, (major, minor): (2, 5); a plugin
                          imports what it needs from kicad2ems.api.analysis, the rest of kicad2ems is private
  name, label, tool       'pdn', 'Power integrity', 'pcbqs.pdn' (what runs it, as lists show it); name is also its
                          config table [analysis.pdn] and the tag of its run folders
  about                   one line: what it gives
  order                   its place in lists and in a run of several: lowest first, then by name
  previewed               True: it has a preview (the model and its mesh, no solve)
  needs                   the analyses it cannot run without the results of (a front end runs those first)
  result, preview_result  the file a finished run (a preview) leaves in its folder ('pdn.json'): how a folder is
                          known as its result
  paths                   the options whose values are paths: relative to the config file they are read from
  page                    False: its settings and its results are all a front end needs to show; True: it has an
                          editor of its own in the GUI (the circuit's schematic)
and implements
  check()                 (usable, one line): its version and origin, or what is missing and how to get it
  options()               its settings: [dict(key, flag, kind, default, help, group, choices)], kind one of 'str',
                          'int', 'float', 'bool' (a switch) or 'list' (repeatable); the form of its tool's
                          `--describe --json`
  tag(values)             a short name for a run of these settings (the rail, the converter): its folder's
  command(values, board, out, preview=False, results=None)
                          the command line that runs it, [program, argument ...], writing into folder `out`;
                          values: {option key: text, bool or list}; board: the study's board file or None;
                          results: {analysis: the folder of its latest result on this board}, for an analysis that
                          can start from another's (the temperature from a circuit's losses). ValueError says what
                          is missing ("name the rail")
  status(line)            the progress a line of its output states, or None (most lines)
  finds, discover(values, board), choices(text)
                          what the board can propose settings for ('converters': a front end's "Find converters"),
                          the command that lists them ([program, argument ...]) and its output as
                          [(label, {option key: value})] to pick from; finds '': none
  report(folder)          what a result holds, to read: dict(title, summary, sections=[dict(title, text, table=
                          dict(columns=[...], rows=[[...]], formats=[...]), notes=[...])], notes, images, footer),
                          or None; a cell is a number or a string, formats give a number's format per column
                          ('.3g'; None: '.4g'). It may say where a viewer starts: show=dict(field, run) (the dump
                          of that field, and run, with the largest values) and mesh (the plan's mesh)
  curves(folder)          what a result holds, to plot: [dict(title, xlabel, ylabel, logx, logy, info, curves=
                          [dict(name, x, y, role='data' | 'reference' | 'limit', unit)])], or []
  suggest(folder)         settings a result proposes for other analyses: [dict(to, label, values, note, need)]
                          (a converter's input rail, for the power-integrity analysis; the converter as a model,
                          for the circuit's), or []; need: the options it could not give a value, to set before
                          a run. A front end offers each on the result, and asks the other's questions with it
  examples()              the examples it ships: [dict(name, config, group, title, files)] (kicad2ems.examples)
  env                     environment variables its command runs with (a thread cap), or {}
and, since 2.6 (kicad2ems.study: questions and engines),
  provides                the questions it answers (kicad2ems.study.QUESTIONS: 'dc', 'pdn', 'thermal' ...), from one
                          run: (); then it is one question of its own name
  engine                  the program's name as a list shows it beside a question ('pcbqs', 'Elmer'); '': its label
  questions               study.Question objects of its own, for what kicad2ems has no question for
and, since 2.10,
  several                 what a study may hold several settings of it by, each a run of its own, as a front end
                          names them ('rail': a board's rails; a config's [[analysis.<name>]] tables, read_sets);
                          '': one. A run's tag(values) tells them apart
  several_keys            the options that differ from one of them to the next (a rail's net, its voltage, its
                          loads): a front end shows them as a table, a row a run, the others shared
and, since 2.9,
  missing(values, board, results=None)
                          what a run still needs, [dict(text, key)]: the default is what command() raises
  uses                    the analyses whose latest result it can start from, when there is one (the temperature
                          from a circuit's losses): not required, as `needs` are
  an option's 'level'     'basic' in an options() row: one of the few a run is set up with (Option.level)
and, since 2.11,
  an option's 'pick'      what the setting names on the board, for a front end to pick there (kicad2ems.layout):
                          'net', 'part', or 'nets', 'parts' where it takes several; absent: typed
  fields                  the bool option that makes it write field dumps too ('fields'); '': none
  for_questions(values, asked)
                          its settings for a run that is asked only some of its questions (the rail's drop without
                          its impedance): the values, changed where that matters
  owns(folder, preview)   whether the folder holds its result (its preview): by default, that its result file is
                          there. An engine whose file another engine also writes looks inside it
and, since 2.8,
  figures(folder)         the figures of its answer by name, [dict(name, value, unit)], for a table of engines
                          against figures: two engines that answer one question give the same quantity the same
                          name (the question says which: study.Question.figures), and may add their own

The fields of a result (the maps a Viewer turns and cuts) need nothing here: an analysis writes them as field dumps
(kicad2ems.api.solver: Dump, write_info, write_npz) under <out>/<name>/exc_<what>/ with its board as
<out>/<name>_board.json, and every front end that shows a run's dumps shows them.
"""
import os

GROUP = 'kicad2ems.analyses'                # the entry-point group of analyses installed as packages
RF = 'rf'                                   # the S-parameter run's own key in a front end's lists: not a plugin's
# the analysis packages kicad2ems has extras for (pyproject): {extra: the analyses it brings}
EXTRAS = {'qs': ('pdn', 'mqs', 'circuit', 'thermal'), 'fem': ('elmer_dc', 'elmer_thermal', 'elmer_cap', 'palace_cap', 'elmer_mag')}
KINDS = ('str', 'int', 'float', 'bool', 'list')


class Analysis:
    """An analysis plugin. A subclass sets the declarations (class attributes: the module docstring lists them) and
    overrides what it does differently; a front end calls check, options and examples when it starts, tag and
    command to run it, status while it runs, and report, curves and suggest on a folder that holds its result."""
    api = None
    name = ''
    label = ''
    tool = ''
    about = ''
    order = 50
    previewed = False
    needs = ()
    result = ''
    preview_result = ''
    paths = ()
    page = False
    finds = ''
    env = {}
    provides = ()
    engine = ''
    questions = ()
    fields = ''
    uses = ()
    several = ''
    several_keys = ()

    def for_questions(self, values, asked):
        """The settings for a run asked only the questions `asked` of those it provides."""
        return values

    def owns(self, folder, preview=False):
        """Whether the folder holds its result (preview: its preview)."""
        f = self.preview_result if preview else self.result
        return bool(f) and os.path.isfile(os.path.join(folder or '', f))

    def check(self):
        """(usable, one line): its version and origin, or what is missing and how to get it."""
        return True, ''

    def options(self):
        """Its settings, [dict(key, flag, kind, default, help, group, choices)]."""
        return []

    def tag(self, values):
        """A short name for a run of these settings (its folder's)."""
        return 'run'

    def command(self, values, board, out, preview=False, results=None):
        """[program, argument ...] that runs it into folder `out`; ValueError: what is missing."""
        raise NotImplementedError(f"{self.name}: command")

    def missing(self, values, board, results=None):
        """What a run of these settings still needs, before it is started: [dict(text, key)], key the option it is
        set by (None: not one option). A front end lists them and does not start the run (2.9). The default is
        what command() raises; an analysis that can tell more from the board (a load with no pad on ground) adds
        it here, cheaply: it is asked on every change. results: as command's, and None for an analysis that is
        asked with this one and has no result yet: what one of `uses` will give is not missing."""
        results = dict(results or {})
        to_come = [k for k in self.uses if k in results and results[k] is None]
        try:
            self.command(values, board, os.path.join(os.sep, 'nowhere'),
                         results={k: v for k, v in results.items() if v is not None})
        except ValueError as e:
            return [] if to_come else [dict(text=str(e), key=None)]
        except Exception:                                    # noqa: BLE001 (not a thing to ask of the user)
            return []
        return []

    def status(self, line):
        """The progress a line of its output states, or None."""
        return None

    def discover(self, values, board):
        """A command that proposes settings from the board, or None: it has none."""
        return None

    def choices(self, text):
        """discover's output as [(label, {option key: value})]."""
        return []

    def report(self, folder):
        """What the result in `folder` holds, to read (the module docstring has the form), or None."""
        return None

    def curves(self, folder):
        """What the result in `folder` holds, to plot: a list of curve sets."""
        return []

    def figures(self, folder):
        """The figures of the answer in `folder`, by name: [dict(name, value, unit)], or []."""
        return []

    def suggest(self, folder):
        """Settings the result in `folder` proposes for other analyses."""
        return []

    def examples(self):
        """The examples it ships: [dict(name, config, group, title, files)]."""
        return []


_registry, broken = None, []                # broken: (entry point, error) of plugins that failed to load


BUILTIN = (('crosstalk', 'kicad2ems.si:Crosstalk'),)     # kicad2ems's own analyses: listed with the plugins'


class _BuiltIn:
    """kicad2ems's own analysis as the registry reads an entry point (name, value, load): no package metadata, so
    a checkout that was installed before it was added has it too."""
    def __init__(self, name, value):
        self.name, self.value = name, value

    def load(self):
        import importlib
        module, _, attr = self.value.partition(':')
        return getattr(importlib.import_module(module), attr)


def _entry_points():
    from importlib.metadata import entry_points
    return [_BuiltIn(*b) for b in BUILTIN] + [ep for ep in entry_points(group=GROUP)
                                              if ep.name not in dict(BUILTIN)]


def registry():
    """{name: Analysis}: the analyses installed through entry points, by their `order`. A plugin that fails to
    load, is written for another kicad2ems API (its `api`: kicad2ems.api), has no name or reuses one (or 'rf',
    the S-parameter run's) or declares an option of an unknown kind is left out and listed in `broken`."""
    global _registry
    if _registry is None:
        from .api import compatible
        reg = {}
        for ep in _entry_points():
            try:
                a = ep.load()()
                why = compatible(getattr(a, 'api', None))
                if why:
                    raise ImportError(why)
                if not a.name or a.name == RF or a.name in reg:
                    raise ValueError(f"name already taken: {a.name!r}" if a.name else 'no name')
                bad = [o.get('key', '?') for o in a.options() if o.get('kind') not in KINDS or not o.get('key')]
                if bad:
                    raise ValueError(f"options of an unknown kind: {', '.join(bad)} (one of {', '.join(KINDS)})")
                from .study import QUESTIONS
                own = {q.name for q in getattr(a, 'questions', ()) or ()}
                unknown = [q for q in getattr(a, 'provides', ()) or () if q not in QUESTIONS and q not in own]
                if unknown:
                    raise ValueError(f"provides questions nobody declares: {', '.join(unknown)} "
                                     f"(kicad2ems's: {', '.join(QUESTIONS)}; or its own `questions`)")
            except Exception as e:                                  # a broken plugin never stops kicad2ems
                broken.append((f"{ep.name} = {ep.value}", f"{type(e).__name__}: {e}"))
                continue
            reg[a.name] = a
        _registry = dict(sorted(reg.items(), key=lambda kv: (kv[1].order, kv[0])))
    return _registry


def get(name):
    """The analysis of a name."""
    a = registry().get(str(name).lower())
    if a is None:
        raise ValueError(missing(name) or f"no analysis {name!r}: choose from {', '.join(registry()) or 'none installed'}")
    return a


def missing(name):
    """Why an analysis kicad2ems knows of cannot be found, and how to install it; None for any other name, or one
    installed."""
    name = str(name).lower()
    extra = next((e for e, names in EXTRAS.items() if name in names), None)
    if extra is None or name in registry():
        return None
    if any(ep.split(' =')[0] in EXTRAS[extra] for ep, _ in broken):
        return f"analysis {name!r} is installed but failed to load (kicad2ems --check)"
    return f"analysis {name!r} is not installed: python -m pip install 'kicad2ems[{extra}]'"


def analysis_of(folder):
    """(name, preview) of the analysis whose result (or preview) the folder holds, or None: not an analysis's. The
    mark whoever started the run left says (study.mark); without one, the first analysis that owns the folder."""
    from .study import marked
    reg = registry()
    m = marked(folder)
    if m and m['engine'] in reg and reg[m['engine']].owns(folder, m['preview']):
        return m['engine'], m['preview']
    for name, a in reg.items():
        for preview in (False, True):
            if a.owns(folder, preview):
                return name, preview
    return None


def _tables(path):
    """(run, {name: [{option key: value}, ...]}) of a config's [analysis] part: every table of each analysis, in the
    order written (one table: a list of one)."""
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:                                                           # pragma: no cover
        import tomli as tomllib
    with open(path, 'rb') as fh:
        cfg = tomllib.load(fh)
    table = cfg.get('analysis')
    if not isinstance(table, dict):
        return None, {}
    base = os.path.dirname(os.path.abspath(path))
    run = table.get('run')
    if run is not None:
        run = [str(x).strip().lower() for x in (run if isinstance(run, (list, tuple)) else str(run).split(','))
               if str(x).strip()]
    out, reg = {}, registry()
    for name, tabs in table.items():
        tabs = [tabs] if isinstance(tabs, dict) else tabs
        if not isinstance(tabs, list) or not tabs or not all(isinstance(t, dict) for t in tabs):
            continue
        sets = []
        for tab in tabs:
            vals = {k.replace('-', '_'): v for k, v in tab.items()}
            for k in getattr(reg.get(name.lower()), 'paths', ()):
                v = vals.get(k)
                if isinstance(v, str) and v and not os.path.isabs(os.path.expanduser(v)):
                    vals[k] = os.path.normpath(os.path.join(base, os.path.expanduser(v)))
            sets.append(vals)
        out[name.lower()] = sets
    return run, out


def read_config(path):
    """The analyses' part of a TOML config: (run, {name: {option key: value}}).

        [analysis]
        run = ["circuit", "thermal"]        # the analyses a study of this config runs ("rf": S-parameters)
        [analysis.circuit]
        model = "buck.json"                 # its options, by key; a path (Analysis.paths) relative to the file

    run: None when the config does not say. A table of an analysis that is not installed is kept as it is (so a
    front end can write it back); its paths are not known, and stay as written. An analysis given several tables
    (read_sets): its first."""
    run, sets = _tables(path)
    return run, {name: tabs[0] for name, tabs in sets.items()}


def read_sets(path):
    """Every table of each analysis of a config: {name: [{option key: value}, ...]}. An analysis may be given
    several, each a run of its own in a study (a board's rails, one after the other), as an array of tables:

        [[analysis.pdn]]
        rail = "+5V"
        volts = 5
        [[analysis.pdn]]
        rail = "+3V3"
        volts = 3.3

    One table ([analysis.pdn]) is a list of one. Paths as read_config gives them."""
    return _tables(path)[1]


def check_lines():
    """--check's lines about the analyses: each one's check, the known ones not installed, the broken plugins."""
    lines = []
    for name, a in registry().items():
        ok, what = a.check()
        lines.append(f"  {'ok' if ok else 'missing':7s} {name:<8s} {a.label}: {what}")
    for extra, names in EXTRAS.items():
        gone = [n for n in names if n not in registry()]
        if gone and missing(gone[0]):
            lines.append(f"  missing {', '.join(gone)}: python -m pip install 'kicad2ems[{extra}]'")
    lines += [f"  BROKEN  {ep}: {err}" for ep, err in broken]
    return lines
