"""
Run settings: one typed Settings object, built once from the defaults, a TOML file and the command
line (in that order: the command line wins). Everything downstream (pipeline, report, a GUI or the
KiCad plugin) reads Settings, never raw strings.

TOML keys are the command-line options (dashes or underscores); openEMS-specific options may also sit
under an [openems] table, so one file can also drive other tools (unknown keys are reported and
ignored). Lists may be TOML arrays or comma-separated strings:

    board = "board.kicad_pcb"       # then:  kicad2openems board.toml
    pos = "board-top.pos"
    nets = ["RF", "GND"]
    fmin = 1.0
    fmax = 6.0
    no_mask = true
    [openems]
    excite = ["P1"]
    air = 5.0

Paths in the file (board, pos, compare, out, events) are relative to the file's folder; a path that
only exists relative to the working directory (older configs written for the repository root) is
still used, with a note.
"""
import dataclasses, os, sys
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:                                                           # pragma: no cover
    import tomli as tomllib


class ConfigError(ValueError):
    """A setting that cannot be used (the message names it)."""


PATH_KEYS = ('board', 'pos', 'compare', 'out', 'events')         # relative to the TOML file
CHOICES = dict(parts=('auto', 'none'), curves=('extremes', 'vertices'), vias=('auto', 'exact', 'centre', 'snap'),
               diel=('fixed', 'wideband'), mode=('sparams', 'antenna'), fast=('skrf', 'qucsator'))
CLI_ONLY = ('check', 'config')


@dataclass
class Settings:
    """Every option of a run, typed. Frequencies in GHz, lengths in mm (as on the command line)."""
    board: str = None
    pos: str = None
    nets: list = None                   # [str] or None: all copper
    region: tuple = None                # (x0, y0, x1, y1) KiCad mm
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
    converge: list = None               # [refinement factors] or None
    converge_tol: tuple = (0.1, 2.0)    # (dB, deg)
    bc: str = 'auto'
    end_db: float = -40.0
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
    fast: str = None                    # 'skrf' | 'qucsator': S-parameters of the line model too
    qucsator: str = None                # path of the qucsator executable (default: on PATH)
    preview: bool = False
    no_report: bool = False
    openems_output: bool = False
    events: str = None
    out: str = 'openems_out'
    notes: list = field(default_factory=list, repr=False)   # remarks from reading the config

    @property
    def antenna(self):
        return self.mode == 'antenna'

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


def _bc(v):
    return ",".join(_strlist(v) or ['auto'])


CONVERT = dict(nets=_strlist, excite=_strlist, dump_port=_strlist, region=_floats(4), converge=_floatlist,
               converge_tol=_floats(2), ff_freq=_floatlist, bc=_bc,
               dump=lambda v: [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])],
               mask=_bool, preview=_bool, no_report=_bool, openems_output=_bool)
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
    for k, v in list(cfg.items()) + list(cfg.get('openems', {}).items()):
        k = k.replace('-', '_')
        if k == 'openems' or isinstance(v, dict):
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


def build_settings(cli=None, config=None):
    """Settings from the defaults, a TOML file (path or None) and command-line values (a dict holding only
    the options that were typed). Raises ConfigError for anything that cannot be used."""
    cli = dict(cli or {})
    values, notes = {}, []
    if config:
        values, ignored, notes = read_toml(config)
        if ignored:
            notes.append(f"ignoring keys not used here: {', '.join(ignored)}")
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
    return s
