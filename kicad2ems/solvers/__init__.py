"""
The solvers, as plugins: each one a Solver subclass registered under its name. Built in: openems (openEMS,
3D FDTD), mom25d (2.5D Method of Moments) and the line model solved as a circuit by qucs (qucsator) or scikit
(scikit-rf). Another solver installs as a package that declares an entry point in the group
'kicad2ems.solvers' (`mysolver = "mypackage.plugin:MySolver"`) and then appears in --solvers, --check, the
config tables and the GUI with no change here.

    from kicad2ems.solvers import registry, get
    get('mom').name                         # 'mom25d' (aliases: fdtd, mom, qucsator, skrf)
    [sv.label for sv in registry().values()]

A solver declares (class attributes)
  name, aliases, label    'mom25d', ('mom',), 'mom25d (MoM)'; name is also its table [solver.mom25d]
  consumes                'board' (the geometry) or 'network' (the line model, tlines.LineModel)
  previewed               True: --preview prepares it without solving (openEMS: the mesh preview)
  rank                    rough cost: 0 circuit, 1 planar, 2 3D; the cheap solvers run first
  capabilities            {feature: 'yes' | 'no' | 'ignored' | 'approximated: how'}, features in FEATURES
  options                 [Option]: its settings. The command-line options, the config keys (top level, or
                          under [solver.<name>] by their short names), the GUI fields and the Settings fields
                          are all made from these lists (and config.CORE for the settings of every run)
and implements
  check(s=None)           (usable, one line: version and origin, or what is missing and how to get it); s: the
                          settings of the run, where one says where the solver is (--qucsator)
  validate(s)             a message when its settings cannot be used together (checked with the settings)
  assess(run)             why it cannot model this board in this run ([]: it can); after the board and the line
                          model are built, before any solver prepares
  prepare(run)            its own form of the board (openEMS: boundaries, mesh), handed to solve()
  solve(run, prep)        Result: S-parameters over the board's ports, as reported
  outputs(run, prep, res) its own extra outputs (plots, files, summary entries); optional
  deviations(run, prep)   [Deviation]: what it models differently from the board, by its own rules (vias moved
                          onto a mesh, parts split, walls as via rows), for the report and the parity checks
  readback(run, prep)     what its native model holds, read back (parity.py: vias, copper area per layer,
                          parts, ports), or None
  estimate(run)           dict(seconds, gb, what): its rough cost on this board (--solvers auto), or None

`run` is the pipeline's Job. A solver may use run.m (the board), run.s (Settings: its options are fields),
run.freq (Hz), run.lm (the line model), run.name, run.log(), run.status(run_name, dict) (progress events),
run.path(suffix), run.wrote(label, path), run.figure(fig, suffix, key, label) and run.summary.
"""
import importlib
from dataclasses import dataclass, field

import numpy as np

from ..options import Option                    # noqa: F401  (plugins import it from here)

GROUP = 'kicad2ems.solvers'                 # the entry-point group of solvers installed as packages
BUILTIN = ('kicad2ems.solvers.openems.plugin:OpenEMS', 'kicad2ems.solvers.mom:MoM25D',
           'kicad2ems.solvers.lines.plugin:Qucs', 'kicad2ems.solvers.lines.plugin:Scikit')
LINES = 'lines'                             # the line model: qucs when qucsator is installed, else scikit
AUTO = 'auto'                               # the cheapest full-wave solver that can model the board, + the line model
# what a solver models, the rows of the capabilities table (--check, docs/architecture.md)
FEATURES = dict(ports='ports on a trace over a plane', gap_ports='gap ports (dipoles, slots)',
                diff_pairs='differential pairs', parts='lumped R / L / C parts', vias='vias',
                outline='board outline and edges',
                planes='inner planes and pours', edge_walls='edge walls', thickness='copper thickness',
                conductor_loss='conductor loss (sigma)', dispersion='dielectric dispersion',
                radiation='radiation, surface waves', cavity='box and cavity effects', antenna='antenna mode, far field')


@dataclass
class Result:
    """A solver's answer, over the board's ports as reported: differential pairs as mixed-mode ports (P1d,
    P1c), the others single-ended, in board order. NaN where a term was not computed."""
    S: np.ndarray               # (nf, n, n)
    names: list                 # port names
    zref: list                  # reference impedance per port, ohms
    seconds: float              # the solver's own time
    info: dict = field(default_factory=dict)    # JSON-friendly facts for the run summary and the report
    comment: str = ''           # the first comment line of its Touchstone file


@dataclass
class Deviation:
    """Something a solver models differently from the board, and says so (parity.py, the report's "What each
    solver saw" table). item names what it is about: 'via:<index>', 'wall:<index>', 'part:<ref>',
    'copper:<layer>', 'outline'."""
    item: str
    feature: str                # the FEATURES key it belongs to
    what: str                   # 'moved onto the mesh', 'split in 2 elements', ...
    amount: float = None        # how much, mm (a move), where it applies
    to: object = None           # what the solver has instead: a via [x, y, r, z0, z1], a wall [axis, c, t0, t1, z0, z1],
                                # a part's element count
    dropped: bool = False       # the item is not in the solver's model
    added: list = None          # vias the solver adds for it ([x, y, r, z0, z1] each: edge walls as via rows)
    layer: str = None           # with area: copper the solver adds on this layer (mm2)
    area: float = None


class Solver:
    name = ''
    aliases = ()
    label = ''
    consumes = 'board'
    previewed = False
    rank = 1
    capabilities = {}
    options = []

    def check(self, s=None):
        return True, ''

    def details(self):
        """(usable, report lines) of a longer check (--check, and when a named solver cannot run)."""
        ok, what = self.check()
        return ok, [what]

    def validate(self, s):
        """A message when its settings in s cannot be used together (None: fine); checked for every run."""
        return None

    def assess(self, run):
        return []

    def prepare(self, run):
        return None

    def solve(self, run, prep):
        raise NotImplementedError(f"{self.name}: solve")

    def outputs(self, run, prep, res):
        pass

    def deviations(self, run, prep):
        return []

    def readback(self, run, prep):
        return None

    def estimate(self, run):
        return None


_registry, broken = None, []                # broken: (entry point, error) of plugins that failed to load


def _load(spec):
    mod, _, cls = spec.partition(':')
    return getattr(importlib.import_module(mod), cls)


def _entry_points():
    from importlib.metadata import entry_points
    return list(entry_points(group=GROUP))


def registry():
    """{name: Solver}: the built-in solvers, then those installed through entry points (in their order). A
    plugin that fails to load, or reuses a name or a setting, is left out and listed in `broken`."""
    global _registry
    if _registry is None:
        reg, keys = {}, set()
        for spec in BUILTIN:
            sv = _load(spec)()
            reg[sv.name] = sv
            keys |= {o.key for o in sv.options}
        names = set(reg) | {a for sv in reg.values() for a in sv.aliases} | {LINES, 'all'}
        from ..config import CORE
        keys |= {o.key for o in CORE}
        for ep in _entry_points():
            try:
                sv = ep.load()()
                clash = ([sv.name] if sv.name in names else []) + [a for a in sv.aliases if a in names] + \
                        [o.key for o in sv.options if o.key in keys]
                if not sv.name or clash:
                    raise ValueError(f"name or settings already taken: {', '.join(clash) or 'no name'}")
            except Exception as e:                                  # a broken plugin never stops kicad2ems
                broken.append((f"{ep.name} = {ep.value}", f"{type(e).__name__}: {e}"))
                continue
            reg[sv.name] = sv
            names |= {sv.name, *sv.aliases}
            keys |= {o.key for o in sv.options}
        _registry = reg
    return _registry


def aliases():
    """{name or alias: name} of every solver."""
    out = {}
    for sv in registry().values():
        out.update({a: sv.name for a in sv.aliases}, **{sv.name: sv.name})
    return out


def get(name):
    """The solver of a name or alias (any case)."""
    n = aliases().get(str(name).lower())
    if n is None:
        raise ValueError(f"no solver {name!r}: choose from {', '.join(registry())}")
    return registry()[n]


def ordered(names):
    """Solver names in registry order; LINES (the line model, not resolved yet) where the line solvers are."""
    order = list(registry())
    first_net = next((i for i, n in enumerate(order) if registry()[n].consumes == 'network'), len(order))
    order.insert(first_net, LINES)
    order.insert(0, AUTO)
    return [n for n in order if n in names]


def resolve_lines(s=None):
    """The solver LINES stands for: qucs when qucsator is installed (its coupled-line and discontinuity
    models), else scikit."""
    q = registry().get('qucs')
    return 'qucs' if q is not None and q.check(s)[0] else 'scikit'


def label(name):
    return {LINES: 'line model', AUTO: 'auto'}.get(name) or registry()[name].label


def capability_table():
    """The capabilities as text lines: one row per feature, one column per solver (--check)."""
    reg = registry()
    cells = {k: [sv.capabilities.get(k, '?').split(':')[0].split(',')[0] for sv in reg.values()] for k in FEATURES}
    cols = [max([len(n)] + [len(c[i]) for c in cells.values()]) for i, n in enumerate(reg)]
    w = max(len(v) for v in FEATURES.values())
    rows = ["  " + " " * w + "  " + "  ".join(f"{n:<{c}}" for n, c in zip(reg, cols))]
    for k, text in FEATURES.items():
        rows.append(f"  {text:<{w}}  " + "  ".join(f"{v:<{c}}" for v, c in zip(cells[k], cols)).rstrip())
    return rows
