"""
The analyses: tools that work on the same board as a run but are not
S-parameter solvers (the crosstalk between traces, a rail's DC drop and
impedance, a converter's near field, a circuit on the board's copper, the
board's temperature). Each is an Analysis subclass, listed in ANALYSES by its
module and class: the crosstalk (kicad2ems.core.si), pcbqs's four
(kicad2ems.pcbqs.analyses), the Elmer and Palace tools (kicad2ems.fem.analyses).
A front end lists what the registry holds, builds each one's settings from its
options, starts it as the command it gives and shows what its result folder
holds.

    from kicad2ems.core.analyses import registry, analysis_of
    [a.label for a in registry().values()]
    analysis_of('out/board_pdn_3V3_20261111_120000')
        # ('pdn', False): whose result, and whether a preview

An analysis declares (class attributes)
  name, label, tool       'pdn', 'Power integrity', 'kicad2ems.pcbqs.pdn' (what
                          runs it, as lists show it); name is also its config
                          table [analysis.pdn] and the tag of its run folders
  about                   one line: what it gives
  order                   its place in lists and in a run of several: lowest
                          first, then by name
  previewed               True: it has a preview (the model and its mesh, no
                          solve)
  needs                   the analyses it cannot run without the results of (a
                          front end runs those first)
  uses                    the analyses whose latest result it can start from,
                          when there is one (the temperature from a circuit's
                          losses): not required, as `needs` are
  result, preview_result  the file a finished run (a preview) leaves in its
                          folder ('pdn.json'): how a folder is known as its
                          result
  paths                   the options whose values are paths: relative to the
                          config file they are read from
  page                    False: its settings and its results are all a front
                          end needs to show; True: it has an editor of its own
                          in the GUI (the circuit's schematic)
  provides                the questions it answers (kicad2ems.core.study:
                          QUESTIONS: 'dc', 'pdn', 'thermal' ...), from one run:
                          (); then it is one question of its own name
  engine                  the program's name as a list shows it beside a
                          question ('pcbqs', 'Elmer'); '': its label
  questions               study.Question objects of its own, for what kicad2ems
                          has no question for
  several                 what a study may hold several settings of it by, each
                          a run of its own, as a front end names them ('rail':
                          a board's rails; a config's [[analysis.<name>]]
                          tables, read_sets); '': one. A run's tag(values)
                          tells them apart
  several_keys            the options that differ from one of them to the next
                          (a rail's net, its voltage, its loads): a front end
                          shows them as a table, a row a run, the others shared
  fields                  the bool option that makes it write field dumps too
                          ('fields'); '': none
  env                     environment variables its command runs with (a thread
                          cap), or {}
  variants                its engines, when it is several to choose from beside
                          a question: ((suffix, label, {option: value}), ...),
                          each an engine of the name <name>_<suffix> (suffix
                          '': <name>) that runs it with those settings (the
                          crosstalk's line model alone, or with a full-wave
                          solver: crosstalk_gfdtd); the settings it fixes are
                          not in its options. (): one engine, itself
and implements
  check()                 (usable, one line): its version and origin, or what is
                          missing and how to get it
  options()               its settings: [dict(key, flag, kind, default, help,
                          group, choices)], kind one of 'str', 'int', 'float',
                          'bool' (a switch) or 'list' (repeatable); the form of
                          its tool's `--describe --json`. A row's 'level':
                          'basic', one of the few a run is set up with
                          (Option.level); its 'pick': what the setting names on
                          the board, for a front end to pick there
                          (kicad2ems.core.layout): 'net', 'part', or 'nets',
                          'parts' where it takes several (absent: typed)
  tag(values)             a short name for a run of these settings (the rail,
                          the converter): its folder's
  command(values, board, out, preview=False, results=None)
                          the command line that runs it, [program, argument
                          ...], writing into folder `out`; values: {option key:
                          text, bool or list}; board: the study's board file or
                          None; results: {analysis: the folder of its latest
                          result on this board}, for an analysis that can start
                          from another's (the temperature from a circuit's
                          losses). ValueError says what is missing ("name the
                          rail")
  missing(values, board, results=None)
                          what a run still needs, [dict(text, key)]: the default
                          is what command() raises
  for_questions(values, asked)
                          its settings for a run that is asked only some of its
                          questions (the rail's drop without its impedance): the
                          values, changed where that matters
  status(line)            the progress a line of its output states, or None
                          (most lines)
  finds, discover(values, board), choices(text) what the board can propose
                          settings for ('converters': a front end's "Find
                          converters"), the command that lists them ([program,
                          argument ...]) and its output as [(label, {option key:
                          value})] to pick from; finds '': none
  owns(folder, preview)   whether the folder holds its result (its preview): by
                          default, that its result file is there. An engine
                          whose file another engine also writes looks inside it
  report(folder)          what a result holds, to read: dict(title, summary,
                          sections=[dict(title, text, table= dict(columns=[...],
                          rows=[[...]], formats=[...]), notes=[...])], notes,
                          images, footer), or None; a cell is a number or a
                          string, formats give a number's format per column
                          ('.3g'; None: '.4g'). It may say where a viewer
                          starts: show=dict(field, run) (the dump of that field,
                          and run, with the largest values) and mesh (the plan's
                          mesh)
  curves(folder)          what a result holds, to plot: [dict(title, xlabel,
                          ylabel, logx, logy, info, curves= [dict(name, x, y,
                          role='data' | 'reference' | 'limit', unit)])], or []
  figures(folder)         the figures of its answer by name, [dict(name, value,
                          unit)], for a table of engines against figures: two
                          engines that answer one question give the same
                          quantity the same name (the question says which:
                          study.Question.figures), and may add their own
  suggest(folder)         settings a result proposes for other analyses:
                          [dict(to, label, values, note, need)] (a converter's
                          input rail, for the power-integrity analysis; the
                          converter as a model, for the circuit's), or []; need:
                          the options it could not give a value, to set before a
                          run. A front end offers each on the result, and asks
                          the other's questions with it
  examples()              the examples it ships: [dict(name, config, group,
                          title, files)] (kicad2ems.examples)

The fields of a result (the maps a Viewer turns and cuts) need nothing here: an
analysis writes them as field dumps (kicad2ems.core.fields.dumps: Dump,
write_info; kicad2ems.core.fields.frames: write_npz) under <out>/<name>/exc_<what>/ with its board as
<out>/<name>_board.json, and every front end that shows a run's dumps shows
them.
"""
import importlib
import os

from .options import strlist
from .runs import load_toml

__all__ = ['RF', 'KINDS', 'Analysis', 'registry', 'analysis_of', 'read_config',
           'read_sets']

# every analysis, as 'module:class': imported on the first registry() call
ANALYSES = ('kicad2ems.core.si:Crosstalk',
            'kicad2ems.pcbqs.analyses:PowerIntegrity',
            'kicad2ems.pcbqs.analyses:NearField',
            'kicad2ems.pcbqs.analyses:Circuit',
            'kicad2ems.pcbqs.analyses:Thermal',
            'kicad2ems.fem.analyses:ElmerDC',
            'kicad2ems.fem.analyses:ElmerThermal',
            'kicad2ems.fem.analyses:PalaceCap',
            'kicad2ems.fem.analyses:ElmerMag',
            'kicad2ems.fem.analyses:PalaceLoop',
            'kicad2ems.fem.analyses:PalacePDN')
# the S-parameter run's own key in a front end's lists: not an analysis's
RF = 'rf'
KINDS = ('str', 'int', 'float', 'bool', 'list')


class Analysis:
    """An analysis. A subclass sets the declarations (class attributes:
    the module docstring lists them) and overrides what it does differently; a
    front end calls check, options and examples when it starts, tag and command
    to run it, status while it runs, and report, curves and suggest on a folder
    that holds its result."""
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
    variants = ()

    def for_questions(self, values, asked):
        """The settings for a run asked only the questions `asked` of those it
        provides."""
        return values

    def owns(self, folder, preview=False):
        """Whether the folder holds its result (preview: its preview)."""
        f = self.preview_result if preview else self.result
        return bool(f) and os.path.isfile(os.path.join(folder or '', f))

    def check(self):
        """(usable, one line): its version and origin, or what is missing and
        how to get it."""
        return True, ''

    def check_with(self, sets):
        """(usable, one line) of one of its variants (the settings `sets`
        fixes): its own check."""
        return self.check()

    def options(self):
        """Its settings, [dict(key, flag, kind, default, help, group,
        choices)]."""
        return []

    def tag(self, values):
        """A short name for a run of these settings (its folder's)."""
        return 'run'

    def command(self, values, board, out, preview=False, results=None):
        """[program, argument ...] that runs it into folder `out`; ValueError:
        what is missing."""
        raise NotImplementedError(f"{self.name}: command")

    def missing(self, values, board, results=None):
        """What a run of these settings still needs, before it is started:
        [dict(text, key)], key the option it is set by (None: not one option). A
        front end lists them and does not start the run. The default is
        what command() raises; an analysis that can tell more from the board (a
        load with no pad on ground) adds it here, cheaply: it is asked on every
        change. results: as command's, and None for an analysis that is asked
        with this one and has no result yet: what one of `uses` will give is not
        missing."""
        results = dict(results or {})
        to_come = [k for k in self.uses if k in results and results[k] is None]
        try:
            self.command(
                values, board, os.path.join(os.sep, 'nowhere'),
                results={k: v
                         for k, v in results.items()
                         if v is not None})
        except ValueError as e:
            return [] if to_come else [dict(text=str(e), key=None)]
        # (not a thing to ask of the user)
        except Exception:  # noqa: BLE001
            return []
        return []

    def status(self, line):
        """The progress a line of its output states, or None."""
        return None

    def discover(self, values, board):
        """A command that proposes settings from the board, or None: it has
        none."""
        return None

    def choices(self, text):
        """discover's output as [(label, {option key: value})]."""
        return []

    def report(self, folder):
        """What the result in `folder` holds, to read (the module docstring has
        the form), or None."""
        return None

    def curves(self, folder):
        """What the result in `folder` holds, to plot: a list of curve sets."""
        return []

    def figures(self, folder):
        """The figures of the answer in `folder`, by name: [dict(name, value,
        unit)], or []."""
        return []

    def suggest(self, folder):
        """Settings the result in `folder` proposes for other analyses."""
        return []

    def examples(self):
        """The examples it ships: [dict(name, config, group, title, files)]."""
        return []


_registry = None


def registry():
    """{name: Analysis}: every analysis of ANALYSES, by its `order` (then its
    name)."""
    global _registry
    if _registry is None:
        found = []
        for spec in ANALYSES:
            module, _, cls = spec.partition(':')
            found.append(getattr(importlib.import_module(module), cls)())
        _registry = {
            a.name: a
            for a in sorted(found, key=lambda a: (a.order, a.name))
        }
    return _registry


def get(name):
    """The analysis of a name."""
    a = registry().get(str(name).lower())
    if a is None:
        raise ValueError(
            f"no analysis {name!r}: choose from {', '.join(registry())}")
    return a


def analysis_of(folder):
    """(name, preview) of the analysis whose result (or preview) the folder
    holds, or None: not an analysis's. The mark whoever started the run left
    says (study.mark: its engine, a variant's being the analysis it runs);
    without one, the first analysis that owns the folder."""
    from .study import marked
    reg = registry()
    m = marked(folder)
    # (a variant's engine: crosstalk_gfdtd is the crosstalk's)
    name = m and next((n for n, a in reg.items()
                       for suffix, *_ in (a.variants or (('', ), ))
                       if m['engine'] == n + ('_' + suffix if suffix else '')),
                      None)
    if name and reg[name].owns(folder, m['preview']):
        return name, m['preview']
    for name, a in reg.items():
        for preview in (False, True):
            if a.owns(folder, preview):
                return name, preview
    return None


def _tables(path):
    """(run, {name: [{option key: value}, ...]}) of a config's [analysis] part:
    every table of each analysis, in the order written (one table: a list of
    one)."""
    table = load_toml(path).get('analysis')
    if not isinstance(table, dict):
        return None, {}
    base = os.path.dirname(os.path.abspath(path))
    run = table.get('run')
    if run is not None:
        run = [x.lower() for x in strlist(run) or []]
    out, reg = {}, registry()
    for name, tabs in table.items():
        tabs = [tabs] if isinstance(tabs, dict) else tabs
        if (not isinstance(tabs, list) or not tabs
                or not all(isinstance(t, dict) for t in tabs)):
            continue
        sets = []
        for tab in tabs:
            vals = {k.replace('-', '_'): v for k, v in tab.items()}
            for k in getattr(reg.get(name.lower()), 'paths', ()):
                v = vals.get(k)
                if (isinstance(v, str) and v
                        and not os.path.isabs(os.path.expanduser(v))):
                    vals[k] = os.path.normpath(
                        os.path.join(base, os.path.expanduser(v)))
            sets.append(vals)
        out[name.lower()] = sets
    return run, out


def read_config(path):
    """The analyses' part of a TOML config: (run, {name: {option key: value}}).

        [analysis]
        run = ["circuit", "thermal"]  # the analyses a study of this config runs
                                      # ("rf": S-parameters)
        [analysis.circuit]
        model = "buck.json"           # its options, by key; a path
                                      # (Analysis.paths) relative to the file

    run: None when the config does not say. A table of an analysis the registry
    does not list is kept as it is (so a front end can write it back); its paths
    are not known, and stay as written. An analysis given several tables
    (read_sets): its first."""
    run, sets = _tables(path)
    return run, {name: tabs[0] for name, tabs in sets.items()}


def read_sets(path):
    """Every table of each analysis of a config: {name: [{option key: value},
    ...]}. An analysis may be given several, each a run of its own in a study (a
    board's rails, one after the other), as an array of tables:

        [[analysis.pdn]]
        rail = "+5V"
        volts = 5
        [[analysis.pdn]]
        rail = "+3V3"
        volts = 3.3

    One table ([analysis.pdn]) is a list of one. Paths as read_config gives
    them."""
    return _tables(path)[1]


def check_lines():
    """--check's lines about the analyses: each one's check."""
    lines = []
    for name, a in registry().items():
        ok, what = a.check()
        lines.append(
            f"  {'ok' if ok else 'missing':7s} {name:<8s} {a.label}: {what}")
    return lines
