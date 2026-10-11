"""
Run settings: one typed Settings object, built once from the defaults, a TOML file and the command
line (in that order: the command line wins). Everything downstream (pipeline, report, a GUI or the
KiCad plugin) reads Settings, never raw strings.

TOML keys are the command-line options (dashes or underscores); openEMS-specific options may also sit
under an [openems] table, so one file can also drive other tools (unknown keys are reported and
ignored). Lists may be TOML arrays or comma-separated strings:

    board = "board.kicad_pcb"       # then:  kicad2ems board.toml
    pos = "board-top.pos"
    nets = ["RF", "GND"]
    fmin = 1.0
    fmax = 6.0
    no_mask = true
    solvers = ["openems", "mom"]    # default: openEMS only (see Settings.solver_plan)
    [openems]
    excite = ["P1"]
    air = 5.0

Paths in the file (board, pos, compare, out, events, qucsator) are relative to the file's folder; a path that
only exists relative to the working directory (older configs written for the repository root) is
still used, with a note. Unknown keys are reported (with the option they are probably a typo of) and
ignored.
"""
import dataclasses, os, sys
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:                                                           # pragma: no cover
    import tomli as tomllib


class ConfigError(ValueError):
    """A setting that cannot be used (the message names it)."""


PATH_KEYS = ('board', 'pos', 'compare', 'out', 'events', 'qucsator')         # relative to the TOML file
CHOICES = dict(parts=('auto', 'none'), curves=('extremes', 'vertices'), vias=('auto', 'exact', 'centre', 'snap'),
               diel=('fixed', 'wideband'), end_on=('ports', 'energy'), mode=('sparams', 'antenna'),
               fast=('auto', 'skrf', 'qucsator'), mom_port=('auto', 'wave', 'lumped'), mom_mesher=('triangle', 'gmsh'))
CLI_ONLY = ('check', 'config')
# the solvers a run can use (docs/usage.md#solvers): openEMS (3D FDTD), mom25d (2.5D MoM) and the
# transmission-line model solved as a circuit (scikit-rf or qucsator); each name with its aliases
SOLVERS = ('openems', 'mom', 'lines')
SOLVER_ALIASES = dict(openems='openems', fdtd='openems', mom='mom', mom25d='mom', lines='lines', line='lines',
                      fast='lines')


@dataclass
class Settings:
    """Every option of a run, typed. Frequencies in GHz, lengths in mm (as on the command line)."""
    board: str = None
    pos: str = None
    nets: list = None                   # [str], ['auto'] (port nets + ground nets) or None: all copper
    region: object = None               # (x0, y0, x1, y1), [(x, y), ...] or a drawing layer name; KiCad mm
    ports: list = field(default_factory=list)    # extra ports: 'P3=J1.1' (on a pad) or 'P3@x,y[,layer[,rot]]'
    cut_ports: bool = False             # ports where signal tracks cross the region edge
    ground: str = None
    mask: bool = False
    parts: str = 'auto'
    fmin: float = 0.5
    fmax: float = 10.0
    points: int = 401
    res: float = 20.0
    fine: float = None
    air: float = None
    ratio: float = 1.4
    nz: int = 4
    curves: str = 'extremes'
    vias: str = 'auto'
    via_snap: float = 0.2
    edge_walls: list = field(default_factory=list)  # board sides with PEC walls where the planes end
    edge_keepout: float = 0.5           # mm from signal copper kept clear of edge walls
    converge: list = None               # [refinement factors] or None
    converge_tol: tuple = (0.1, 2.0)    # (dB, deg)
    bc: str = 'auto'
    end_db: float = -40.0
    end_on: str = 'ports'               # 'ports': also stop when the port signals have decayed by end_db
    max_steps: int = 1_000_000
    sigma: float = None
    diel: str = 'fixed'
    diel_fref: float = 1.0
    excite: list = None                 # [port names] or None: all
    threads: int = 0
    jobs: int = 1
    mode: str = 'sparams'
    ff_freq: list = field(default_factory=list)      # GHz
    ff_step: float = 2.0
    dump: list = field(default_factory=list)
    dump_port: list = None
    compare: str = None
    solvers: list = None                # ['openems', 'mom', 'lines'] named to run (see solver_plan); None: openEMS
    fast: str = None                    # 'auto' | 'skrf' | 'qucsator': S-parameters of the line model too
    qucsator: str = None                # path of the qucsator executable (default: on PATH)
    mom: bool = False                   # also solve the CSX geometry with mom25d (2.5D MoM): <name>_mom.sNp
    mom_dmax: float = 0.3               # mm: largest MoM cell along the copper (planes: 3x, refined near traces)
    mom_port: str = 'auto'              # 'auto': wave ports where a straight feed allows, else lumped
    mom_mesher: str = 'triangle'        # 'triangle' | 'gmsh'
    mom_solves: int = 60                # most EM solves of the adaptive sweep
    preview: bool = False
    no_report: bool = False
    openems_output: bool = False
    events: str = None
    out: str = 'openems_out'
    notes: list = field(default_factory=list, repr=False)   # remarks from reading the config

    @property
    def antenna(self):
        return self.mode == 'antenna'

    def solver_plan(self):
        """(the solvers this run uses, the ones named in `solvers`), both in SOLVERS order.

        The solvers are those named in `solvers` (default: openEMS), plus mom25d with mom = true and the line
        model with fast = ...; --preview runs no openEMS. A solver named in `solvers` is a result the run is
        for: if it cannot run, the run stops before it starts. One added by --mom / --fast is a reference
        next to the others: if it cannot run, that is a warning."""
        named = list(self.solvers or [])
        run = set(named) if self.solvers is not None else {'openems'}
        if self.mom:
            run.add('mom')
        if self.fast:
            run.add('lines')
        if self.preview:
            run.discard('openems')
        return [x for x in SOLVERS if x in run], [x for x in SOLVERS if x in named]

    @property
    def line_backend(self):
        """The circuit solver of the line model: fast, or 'auto' (qucsator if installed, else scikit-rf)."""
        return self.fast or 'auto'

    def options(self):
        """The options as a plain dict (for the report and the run summary)."""
        return {k: v for k, v in dataclasses.asdict(self).items() if k != 'notes'}


# ---- conversion of one value (from TOML or the command line) ---------------------------------------
def _strlist(v):
    items = v if isinstance(v, (list, tuple)) else str(v).split(',')
    out = [str(x).strip() for x in items if str(x).strip()]
    return out or None


def _floatlist(v):
    return [float(x) for x in (_strlist(v) or [])]


def _floats(n):
    def conv(v):
        vals = _floatlist(v)
        if len(vals) != n:
            raise ValueError(f"expected {n} numbers separated by commas")
        return tuple(vals)
    return conv


def _bool(v):
    if isinstance(v, bool):
        return v
    if str(v).lower() in ('1', 'true', 'yes', 'on'):
        return True
    if str(v).lower() in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError("expected true or false")


def _region(v):
    """x0,y0,x1,y1 / [x0, y0, x1, y1] (rectangle), [[x, y], ...] (polygon) or a drawing layer name."""
    if isinstance(v, str):
        try:
            return _floats(4)(v)
        except (TypeError, ValueError):
            if ',' in v or not v.strip():
                raise ValueError("expected x0,y0,x1,y1, a list of [x, y] points or a drawing layer such as User.1")
            return v.strip()
    vals = list(v)
    if vals and all(isinstance(p, (list, tuple)) for p in vals):
        if len(vals) < 3 or any(len(p) != 2 for p in vals):
            raise ValueError("a polygon region needs at least three [x, y] points")
        return [tuple(float(c) for c in p) for p in vals]
    return _floats(4)(vals)


def _bc(v):
    return ",".join(_strlist(v) or ['auto'])


def _sides(v):
    from .edges import parse_sides
    return parse_sides(v)


def _solvers(v):
    """Solver names (aliases allowed, 'all' for every one) in SOLVERS order, or None for an empty list."""
    out = set()
    for x in _strlist(v) or []:
        x = x.lower()
        if x == 'all':
            out.update(SOLVERS)
        elif x in SOLVER_ALIASES:
            out.add(SOLVER_ALIASES[x])
        else:
            raise ValueError(f"no solver {x!r}: choose from {', '.join(SOLVERS)} (or all)")
    return [x for x in SOLVERS if x in out] or None


CONVERT = dict(nets=_strlist, excite=_strlist, dump_port=_strlist, region=_region,
               ports=lambda v: [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x).strip()],
               cut_ports=_bool, converge=lambda v: _floatlist(v) or None,
               converge_tol=_floats(2), ff_freq=_floatlist, bc=_bc, edge_walls=_sides, solvers=_solvers,
               dump=lambda v: [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])],
               mask=_bool, mom=_bool, preview=_bool, no_report=_bool, openems_output=_bool)
_FIELDS = {f.name: f for f in dataclasses.fields(Settings) if f.name != 'notes'}


def _int(v):
    f = float(v)
    if f != int(f):
        raise ValueError("expected a whole number")
    return int(f)


def _convert(key, value):
    if value is None:
        return None
    conv = CONVERT.get(key) or {float: float, int: _int, bool: _bool}.get(_FIELDS[key].type, str)
    try:
        v = conv(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{key.replace('_', '-')} = {value!r}: {e}") from None
    if key in CHOICES and v not in CHOICES[key]:
        raise ConfigError(f"{key.replace('_', '-')} = {value!r}: choose one of {', '.join(CHOICES[key])}")
    return v


def resolve_path(value, base, key, notes):
    """A path from the TOML file at `base`: relative to base, or - if only that exists - to the working
    directory (configs written for the repository root), with a note."""
    value = os.path.expanduser(str(value))
    if os.path.isabs(value):
        return value
    here = os.path.normpath(os.path.join(base, value))
    target = here if key not in ('out', 'events') else (os.path.dirname(here) or '.')   # out: its parent must exist
    cwd_target = value if key not in ('out', 'events') else (os.path.dirname(value) or '.')
    if not os.path.exists(target) and os.path.exists(cwd_target) and os.path.normpath(value) != here:
        notes.append(f"{key} = {value!r}: not found next to the config; using it relative to the working "
                     f"directory (write it relative to the config file)")
        return value
    return here


def read_toml(path):
    """(values {option: raw value}, ignored keys, notes) of a TOML config; paths made absolute or
    working-directory relative (see resolve_path)."""
    with open(path, 'rb') as fh:
        try:
            cfg = tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from None
    base = os.path.dirname(os.path.abspath(path))
    values, ignored, notes = {}, [], []
    ours = [t for t, v in cfg.items() if isinstance(v, dict) and t.lower() == 'openems']     # [openems], [OpenEMS]
    for k, v in list(cfg.items()) + [kv for t in ours for kv in cfg[t].items()]:
        k = k.replace('-', '_')
        if isinstance(v, dict):                                     # tables of other tools
            continue
        if k == 'no_mask':
            k, v = 'mask', not _bool(v)
        if k not in _FIELDS:
            ignored.append(k)
            continue
        if k in PATH_KEYS and v:
            v = resolve_path(v, base, k, notes)
        values[k] = v
    return values, ignored, notes


def _suggest(key):
    """An unknown key, with the option it is probably a typo of."""
    import difflib
    near = difflib.get_close_matches(key, list(_FIELDS) + ['no_mask'], n=1, cutoff=0.8)
    return f"{key} (did you mean {near[0]}?)" if near else key


def build_settings(cli=None, config=None):
    """Settings from the defaults, a TOML file (path or None) and command-line values (a dict holding only
    the options that were typed). Raises ConfigError for anything that cannot be used."""
    cli = dict(cli or {})
    values, notes = {}, []
    if config:
        values, ignored, notes = read_toml(config)
        if ignored:
            notes.append("ignoring keys not used here: " + ", ".join(_suggest(k) for k in ignored))
    values.update({k: v for k, v in cli.items() if k not in CLI_ONLY})
    unknown = [k for k in values if k not in _FIELDS]
    if unknown:
        raise ConfigError(f"unknown option(s): {', '.join(unknown)}")
    s = Settings(**{k: _convert(k, v) for k, v in values.items()})
    s.notes = notes
    if s.fmin >= s.fmax:
        raise ConfigError(f"fmin ({s.fmin:g} GHz) must be below fmax ({s.fmax:g} GHz)")
    if s.points < 2:
        raise ConfigError("points must be at least 2")
    if s.converge is not None and (len(set(s.converge)) < 2 or min(s.converge) <= 0):
        raise ConfigError(f"converge needs at least two positive refinement factors, e.g. 1,1.5,2 (got {s.converge})")
    if s.converge and s.antenna:
        raise ConfigError("converge is not available with mode = antenna")
    run, named = s.solver_plan()
    if s.antenna and 'mom' in named:
        raise ConfigError("solvers: mom25d is not available in antenna mode (it models an infinite ground plane)")
    if s.antenna and not s.preview and 'openems' not in run:
        raise ConfigError("mode = antenna needs openEMS: add openems to solvers (or use --preview)")
    return s
