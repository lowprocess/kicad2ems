"""
The solvers: each one a Solver subclass, listed in SOLVERS by its module and
class. The line model solved as a circuit by qucs (qucsator) or scikit
(scikit-rf); the full-wave solvers openems and gfdtd (3D FDTD, the one FDTD
model on openEMS's engine or gfdtd's: kicad2ems.fdtd), mom25d (2.5D Method
of Moments: kicad2ems.mom25d), palace (3D FEM: kicad2ems.fem) and pcbqs
(quasi-static: kicad2ems.pcbqs). Each appears in --solvers, --check, the config
tables and the GUI with no change here.

    from kicad2ems.core.solvers import registry, get
    get('mom').name  # 'mom25d' (aliases: fdtd, mom, qucsator, skrf)
    [sv.label for sv in registry().values()]

A solver declares (class attributes)
  name, aliases, label  'mom25d', ('mom',), 'mom25d (MoM)'; name is also its
                        table [solver.mom25d]
  consumes              'board' (the geometry) or 'network' (the line model,
                        tlines.LineModel)
  previewed             True: --preview prepares it without solving (openEMS:
                        the mesh preview)
  rank                  rough cost: 0 circuit, 1 planar, 2 3D; the cheap solvers
                        run first
  order                 its place in the registry (lists, the GUI, the order of
                        results): lowest first, then by name
  capabilities          {feature: 'yes' | 'no' | 'ignored' | 'approximated:
                        how'}, features in FEATURES
  options               [Option]: its settings. The command-line options, the
                        config keys (top level, or under [solver.<name>] by
                        their short names), the GUI fields and the Settings
                        fields are all made from these lists (and config.CORE
                        for the settings of every run)
and implements
  check(s=None)           (usable, one line: version and origin, or what is
                          missing and how to get it); s: the settings of the
                          run, where one says where the solver is (--qucsator)
  validate(s)             a message when its settings cannot be used together
                          (checked with the settings)
  assess(run)             why it cannot model this board in this run ([]: it
                          can); after the board and the line model are built,
                          before any solver prepares
  prepare(run)            its own form of the board (openEMS: boundaries, mesh),
                          handed to solve()
  solve(run, prep)        Result: S-parameters over the board's ports, as
                          reported
  outputs(run, prep, res) its own extra outputs (plots, files, summary entries);
                          optional
  reusable(run)           whether an earlier run's result may stand for its
                          solve (no outputs() then): by default only where it
                          has no outputs() (kicad2ems.core.reuse)
  deviations(run, prep)   [Deviation]: what it models differently from the
                          board, by its own rules (vias moved onto a mesh, parts
                          split, walls as via rows), for the report and the
                          parity checks
  readback(run, prep)     what its native model holds, read back (parity.py:
                          vias, copper area per layer, parts, ports), or None
  estimate(run)           dict(seconds, gb, what): its rough cost on this board
                          (--solvers auto), or None
  resources()             dict(gpus=[dict(device, backend, api, free, total):
                          GB], why): the GPUs it would run on here and why there
                          are none (sysinfo, the GUI's System panel), or None:
                          the CPU only

`run` is the pipeline's Job. A solver may use run.m (the board), run.s
(Settings: its options are fields), run.freq (Hz), run.lm (the line model),
run.name, run.log(), run.status(run_name, dict) (progress events),
run.path(suffix), run.wrote(label, path), run.figure(fig, suffix, key, label)
and run.summary.
"""
import importlib
from dataclasses import dataclass, field

import numpy as np

# (for the solvers' option tables)
from ..options import Option  # noqa: F401

__all__ = ['LINES', 'AUTO', 'FEATURES', 'Result', 'Deviation', 'Solver',
           'registry', 'get', 'resolve_lines']

# every solver, as 'module:class': imported on the first registry() call
SOLVERS = ('kicad2ems.core.solvers.lines.solver:Qucs',
           'kicad2ems.core.solvers.lines.solver:Scikit',
           'kicad2ems.fdtd.openems.solver:OpenEMS',
           'kicad2ems.fdtd.gfdtd:GFDTD',
           'kicad2ems.mom25d.solvers.mom:MoM25D',
           'kicad2ems.fem.palace.solver:Palace',
           'kicad2ems.pcbqs.analyses:QuasiStatic')
# the solver --mom adds as a reference
MOM = 'mom25d'
# the line model: qucs when qucsator is installed, else scikit
LINES = 'lines'
# the cheapest full-wave solver that can model the board, + the line model
AUTO = 'auto'
# what a solver models, the rows of the capabilities table (--check,
# docs/architecture.md)
FEATURES = dict(
    ports='ports on a trace over a plane',
    gap_ports='gap ports (dipoles, slots)', diff_pairs='differential pairs',
    parts='lumped R / L / C parts', vias='vias',
    outline='board outline and edges', planes='inner planes and pours',
    edge_walls='edge walls', thickness='copper thickness',
    conductor_loss='conductor loss (sigma)',
    roughness='copper roughness (rough)', finish='surface finish (finish)',
    dispersion='dielectric dispersion', radiation='radiation, surface waves',
    cavity='box and cavity effects', antenna='antenna mode, far field',
    solids='drawn solids (metal, dielectric, air)')


@dataclass
class Result:
    """A solver's answer, over the board's ports as reported: differential pairs
    as mixed-mode ports (P1d, P1c), the others single-ended, in board order. NaN
    where a term was not computed."""
    S: np.ndarray               # (nf, n, n)
    names: list                 # port names
    zref: list                  # reference impedance per port, ohms
    seconds: float              # the solver's own time
    # JSON-friendly facts for the run summary and the report
    info: dict = field(default_factory=dict)
    comment: str = ''           # the first comment line of its Touchstone file


@dataclass
class Deviation:
    """Something a solver models differently from the board, and says so
    (parity.py, the report's "What each solver saw" table). item names what it
    is about: 'via:<index>', 'wall:<index>', 'part:<ref>', 'copper:<layer>',
    'outline'."""
    item: str
    feature: str          # the FEATURES key it belongs to
    what: str             # 'moved onto the mesh', 'split in 2 elements', ...
    amount: float = None  # how much, mm (a move), where it applies
    # what the solver has instead: a via [x, y, r, z0, z1], a wall [axis, c, t0,
    # t1, z0, z1], a part's element count
    to: object = None
    dropped: bool = False       # the item is not in the solver's model
    # vias the solver adds for it ([x, y, r, z0, z1] each: edge walls as via
    # rows)
    added: list = None
    # with area: copper the solver adds on this layer (mm2)
    layer: str = None
    area: float = None


class Solver:
    """A solver. A subclass sets the declarations (class attributes: the
    module docstring lists them) and overrides the steps it does differently;
    the pipeline calls them in the order check, validate, assess, estimate (for
    auto), prepare, solve, deviations and readback (the parity checks),
    outputs."""
    name = ''
    aliases = ()
    label = ''
    in_auto = True              # --solvers auto may pick it
    consumes = 'board'
    previewed = False
    rank = 1
    order = 50
    capabilities = {}
    options = []
    # the keys of its options a run is set up with (its main cell size): a
    # window shows them first, the rest under "More settings" (Option.level)
    basic = ()

    def check(self, s=None):
        """(usable, one line): its version and origin, or what is missing and
        how to get it. s: the run's settings, where one says where the solver is
        (--qucsator); None for --check."""
        return True, ''

    def details(self):
        """(usable, report lines) of a longer check (--check, and when a named
        solver cannot run)."""
        ok, what = self.check()
        return ok, [what]

    def validate(self, s):
        """A message when its settings in s cannot be used together (None:
        fine); checked for every run."""
        return None

    def assess(self, run):
        """Why it cannot model this board in this run, a list of reasons ([]: it
        can). Called after the board and the line model are built, before any
        solver prepares."""
        return []

    def prepare(self, run):
        """Its own form of the board (openEMS: the boundaries and the mesh),
        handed to solve() and the later steps; None when it needs none. A
        preview stops here."""
        return None

    def solve(self, run, prep):
        """The Result: S-parameters over the board's ports as reported
        (differential pairs as mixed-mode ports)."""
        raise NotImplementedError(f"{self.name}: solve")

    def outputs(self, run, prep, res):
        """Its own outputs after the solve (plots, files, summary entries);
        optional."""
        pass

    def reusable(self, run):
        """Whether an earlier run's result may stand for its solve in this run
        (kicad2ems.core.reuse): its outputs() is not called for one, so a solver
        whose outputs need what its solve keeps says no where this run asks for
        them. By default: only a solver with no outputs() of its own."""
        return type(self).outputs is Solver.outputs

    def deviations(self, run, prep):
        """[Deviation]: what it models differently from the board, by its own
        rules (vias moved onto a mesh, parts split, walls as via rows), for the
        report and the parity checks."""
        return []

    def readback(self, run, prep):
        """What its native model holds, read back in the board's terms
        (parity.py: vias, copper area per layer, parts, ports), or None."""
        return None

    def estimate(self, run):
        """dict(seconds, gb, what): its rough cost on this board, for --solvers
        auto, or None."""
        return None

    def resources(self):
        """dict(gpus=[dict(device, backend, api, free, total)], why): the GPUs
        it would run on here (memory in GB) and why there are none (sysinfo, the
        GUI's System panel), or None: the CPU only."""
        return None


_registry = None


def registry():
    """{name: Solver}: every solver of SOLVERS, by its `order` (then its name).
    Solvers may share a setting by listing the same Option object (the FDTD
    solvers' mesh and run settings)."""
    global _registry
    if _registry is None:
        found = []
        for spec in SOLVERS:
            module, _, cls = spec.partition(':')
            found.append(getattr(importlib.import_module(module), cls)())
        _registry = {
            sv.name: sv
            for sv in sorted(found, key=lambda sv: (sv.order, sv.name))
        }
    return _registry


def aliases():
    """{name or alias: name} of every solver."""
    return {
        a: sv.name
        for sv in registry().values()
        for a in (*sv.aliases, sv.name)
    }


def get(name):
    """The solver of a name or alias (any case)."""
    n = aliases().get(str(name).lower())
    if n is None:
        raise ValueError(
            f"no solver {name!r}: choose from {', '.join(registry())}")
    return registry()[n]


def ordered(names):
    """Solver names in registry order; LINES (the line model, not resolved yet)
    where the line solvers are."""
    order = list(registry())
    first_net = next(
        (i for i, n in enumerate(order) if registry()[n].consumes == 'network'),
        len(order))
    order.insert(first_net, LINES)
    order.insert(0, AUTO)
    return [n for n in order if n in names]


def resolve_lines(s=None):
    """The solver LINES stands for: qucs when qucsator is installed (its
    coupled-line and discontinuity models), else scikit."""
    q = registry().get('qucs')
    return 'qucs' if q is not None and q.check(s)[0] else 'scikit'


def label(name):
    return ({LINES: 'line model', AUTO: 'auto'}.get(name)
            or registry()[name].label)


def capability_table():
    """The capabilities as text lines: one row per feature, one column per
    solver (--check)."""
    reg = registry()
    cells = {
        k: [
            sv.capabilities.get(k, '?').split(':')[0].split(',')[0]
            for sv in reg.values()
        ]
        for k in FEATURES
    }
    cols = [
        max([len(n)] + [len(c[i])
                        for c in cells.values()])
        for i, n in enumerate(reg)
    ]
    w = max(len(v) for v in FEATURES.values())
    rows = [
        "  " + " " * w + "  "
        + "  ".join(f"{n:<{c}}" for n, c in zip(reg, cols))
    ]
    for k, text in FEATURES.items():
        rows.append(f"  {text:<{w}}  "
                    + "  ".join(f"{v:<{c}}"
                                for v, c in zip(cells[k], cols)).rstrip())
    return rows
