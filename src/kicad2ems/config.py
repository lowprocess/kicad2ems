"""
Run settings: one typed Settings object, built once from the defaults, a TOML file and the command line (in
that order: the command line wins). Everything downstream (pipeline, solvers, report, the GUI) reads
Settings, never raw strings.

The settings are declared once, as options (options.Option): CORE here, the settings of every run, and each
solver's own (solvers/: openEMS's mesh and time stepping, mom25d's cells ...). Settings has a field for each;
the command line, the config keys and the GUI fields are made from the same lists.

A config's keys are the command-line options (dashes or underscores) at the top level; a solver's options
may also sit under its table, [solver.<name>], by their short names (mom25d's mom_dmax is dmax there). Lists
may be TOML arrays or comma-separated strings:

    board = "board.kicad_pcb"       # then:  kicad2ems board.toml
    pos = "board-top.pos"
    nets = ["RF", "GND"]
    fmin = 1.0
    fmax = 6.0
    no_mask = true
    solvers = ["openems", "mom25d"] # default: auto (kicad2openems: openems; see Settings.solver_plan)
    [solver.openems]
    excite = ["P1"]
    air = 5.0
    [solver.mom25d]
    dmax = 0.4

An [openems] table (the older form) is read like the top level, with a note. Paths in the file (board, pos,
compare, out, events, qucsator) are relative to the file's folder; a path that only exists relative to the
working directory (older configs written for the repository root) is still used, with a note. Unknown keys are
reported (with the option they are probably a typo of) and ignored.
"""
import dataclasses, os, sys
from dataclasses import field

from .options import Option, strlist, items, floats, floatlist, boolean
from . import solvers as _solvers_pkg

if sys.version_info >= (3, 11):
    import tomllib
else:                                                           # pragma: no cover
    import tomli as tomllib


class ConfigError(ValueError):
    """A setting that cannot be used (the message names it)."""


def _region(v):
    """x0,y0,x1,y1 / [x0, y0, x1, y1] (rectangle), [[x, y], ...] (polygon) or a drawing layer name."""
    if isinstance(v, str):
        try:
            return floats(4)(v)
        except (TypeError, ValueError):
            if ',' in v or not v.strip():
                raise ValueError("expected x0,y0,x1,y1, a list of [x, y] points or a drawing layer such as User.1")
            return v.strip()
    vals = list(v)
    if vals and all(isinstance(p, (list, tuple)) for p in vals):
        if len(vals) < 3 or any(len(p) != 2 for p in vals):
            raise ValueError("a polygon region needs at least three [x, y] points")
        return [tuple(float(c) for c in p) for p in vals]
    return floats(4)(vals)


def _sides(v):
    from .edges import parse_sides
    return parse_sides(v)


def _steer(v):
    """--steer: one or two phase steps (deg), as a list; None for an empty one."""
    vals = floatlist(v)
    if len(vals) > 2:
        raise ValueError("expected X or X,Y (phase steps in degrees)")
    return vals or None


def _taper(v):
    """--taper: KIND[:SLL] (phased.parse_taper checks it); None for an empty one."""
    from .phased import parse_taper
    v = str(v or '').strip()
    if v:
        parse_taper(v)
    return v or None


def _solvers(v):
    """Solver names (aliases allowed; 'lines' for the line model, 'all' for every solver but the variants of another, as mixed) in
    registry order, or
    None for an empty list."""
    from .solvers import registry, aliases, ordered, LINES, AUTO
    names = dict(aliases(), line=LINES, lines=LINES, fast=LINES, auto=AUTO)
    out = set()
    for x in strlist(v) or []:
        x = x.lower()
        if x == 'all':                                          # every solver, the line model once
            out |= {n for n, sv in registry().items() if sv.consumes != 'network' and getattr(sv, 'in_all', True)} | {LINES}
        elif x in names:
            out.add(names[x])
        else:
            raise ValueError(f"no solver {x!r}: choose from {', '.join(list(registry()) + [LINES, AUTO, 'all'])}")
    return ordered(out) or None


# the settings of every run (each solver's own are in its plugin): (key, default, help, type, ...)
CORE = [
    Option('solvers', None, "solvers to run, comma-separated: auto (the default: the full-wave solver estimated "
           "cheapest for this board, plus the line model; kicad2openems: openems), openems (3D FDTD), gfdtd (3D FDTD on the GPU or the CPU, openEMS's model; not in all), mom25d (alias mom; 2.5D "
           "MoM: planar boards over a ground plane, no openEMS needed), mixed (mom25d with long straight runs as transmission lines; not in all), qucs or scikit (the transmission-line model as "
           "a circuit, solved by qucsator or scikit-rf: milliseconds), lines (qucs when qucsator is installed, else "
           "scikit) or all. A "
           "named solver that cannot run stops the run before it starts; --mom and --fast add theirs as references",
           list, conv=_solvers, group='solvers', cli=dict(metavar='LIST')),
    Option('mom', False, "also run mom25d, as a reference next to the other solvers (if it cannot run, that is a "
           "warning); works with --preview", bool, group='solvers'),
    Option('fast', None, "also solve the transmission-line model as a circuit, as a reference: skrf (scikit-rf), "
           "qucsator, or auto (qucsator if installed, else scikit-rf)", choices=('auto', 'skrf', 'qucsator'),
           group='solvers'),
    Option('mode', 'sparams', "sparams (default): S-parameters of all ports; antenna: a driven port (several with "
           "--drive), far field (gain, efficiency, patterns); openEMS and mom25d", choices=('sparams', 'antenna'),
           gui='Mode', group='solvers'),
    Option('drive', [], "antenna mode: drive several ports at once, each PORT[=amplitude][@phase in deg] (the "
           "incident waves; the other ports terminated), e.g. P1@0,P2@60: a phased array (mom25d; openEMS drives "
           "the first). The GUI's Results page steers the beam afterwards", list, conv=strlist,
           gui='Antenna: phased drive (P1@0, P2@60 ...; empty: one port)', group='antenna',
           cli=dict(metavar='PORT[=AMP][@DEG],...')),
    Option('grid', None, "antenna mode: the ports' places in a 2D array, for --steer and the GUI's beam steering: "
           "COLSxROWS (the ports in order fill the rows, top row first: 4x4 for P1..P16) or PORT:COL,ROW items "
           "separated by spaces (e.g. 'P1:0,0 P2:1,0 P3:0,1 P4:1,1'); empty: the ports in a line",
           gui='Antenna: array grid (4x4, or P1:0,0 P2:1,0 ...)', group='antenna', cli=dict(metavar='SPEC')),
    Option('steer', None, "antenna mode: phase steps (deg) added to --drive: X[,Y], X per column and Y per row of "
           "--grid (without a grid: X from each driven port to the next), e.g. 30,-45", list,
           conv=_steer, gui='Antenna: phase step X, Y (deg)', group='antenna', cli=dict(metavar='X[,Y]')),
    Option('taper', None, "antenna mode: an amplitude taper multiplied into --drive, for lower sidelobes: uniform, "
           "taylor, chebyshev or cosine, with :SLL for the sidelobe level in dB (taylor and chebyshev; default "
           "25), e.g. taylor:30. Across --grid's columns times its rows, else across the driven ports",
           conv=_taper, gui='Antenna: amplitude taper (taylor:30, chebyshev:25, cosine)', group='antenna',
           cli=dict(metavar='KIND[:SLL]')),
    Option('board', None, ".kicad_pcb, .gbrjob (Gerber job file; ports from --pos), the <name>_board.json of an "
           "earlier run, or a .toml config that names one", path=True, gui='Board (.kicad_pcb)', group='board',
           cli=dict(flags=['board'], nargs='?')),
    Option('pos', None, "pick-and-place file listing the port components", path=True, gui='Pick-and-place file',
           group='board'),
    Option('nets', None, "comma-separated nets to include, or auto (the nets under the ports plus the ground nets; "
           "default: all copper)", list, gui='Nets (comma-separated, or auto)', group='board'),
    Option('region', None, "crop to x0,y0,x1,y1 (KiCad mm) or to the shapes on a drawing layer (e.g. User.1); in a "
           "config also a list of [x, y] points. Ports outside it are left out", object, conv=_region,
           gui='Region (x0,y0,x1,y1 or a drawing layer)', group='board'),
    Option('ports', [], "an extra port: P3=J1.1 (on pad 1 of J1) or P3@x,y[,layer[,rotation]] (KiCad mm); "
           "repeatable", list, conv=items, gui='Extra ports (P3=J1.1, P4@x,y ...)', group='board',
           cli=dict(flags=['--port'], dest='ports', action='append', metavar='SPEC')),
    Option('cut_ports', False, "a port wherever a signal track crosses the region edge", bool,
           gui='Ports where tracks cross the region edge', group='board'),
    Option('ground', None, "copper layer to model as a solid plane over the whole outline", gui='Solid ground layer',
           group='board'),
    Option('mask', False, "include solder mask layers (thin: smaller time step)", bool, group='board'),
    Option('parts', 'auto', "lumped R / L / C parts between two pads on the modelled copper (default auto)",
           choices=('auto', 'none'), group='board'),
    Option('edge_walls', [], "walls where the ground planes end at these board sides, standing in for stitching vias "
           "or edge plating (openEMS: PEC sheets; mom25d: via rows): xmin,xmax,ymin,ymax (KiCad coordinates: "
           "ymin = top) or all; kept --edge-keepout clear of signal copper", list, conv=_sides,
           gui='Edge walls (xmin,xmax,ymin,ymax or all)', group='board', cli=dict(metavar='SIDES')),
    Option('edge_keepout', 0.5, "mm between edge walls and signal copper (default {default})", float, group='board'),
    Option('sigma', None, "copper conductivity S/m (default: lossless; openEMS: conducting sheets, mom25d and the "
           "line model: conductor loss)", float, group='board'),
    Option('fmin', 0.5, "GHz (default {default})", float, gui='f min (GHz)', group='band'),
    Option('fmax', 10.0, "GHz (default {default})", float, gui='f max (GHz)', group='band'),
    Option('points', 401, "frequency points (default {default})", int, gui='Frequency points', group='band'),
    Option('compare', None, "Touchstone file to overlay on the result plot", path=True, group='output'),
    Option('preview', False, "write the model (and openEMS's mesh) preview, the line sections and the report, run "
           "the solvers other than openEMS, then exit", bool, group='output'),
    Option('no_report', False, "do not write <name>_report.html", bool, group='output'),
    Option('dump_animate', None, "frequency-domain dumps (openEMS --dump ...@GHz, mom25d --mom-dump): also write one "
           "period of Re(F e^jphi) at each frequency, the standing waves: gif or mp4 (mp4 needs ffmpeg)",
           choices=('gif', 'mp4'), gui='Animate frequency-domain dumps (gif / mp4; empty: no)', group='field dumps'),
    Option('events', None, "also write the run as JSON lines (stages, log, progress, files) to FILE, for a GUI or a "
           "script", path=True, group='output', cli=dict(metavar='FILE')),
    Option('out', None, "output folder (default: out next to the board; for the <name>_board.json of an earlier run, "
           "its folder)", path=True, gui='Output folder', group='output'),
]
CLI_ONLY = ('check', 'config')


class _Settings:
    """Every option of a run, typed. Frequencies in GHz, lengths in mm (as on the command line)."""

    @property
    def antenna(self):
        return self.mode == 'antenna'

    def solver_plan(self, preview=None):
        """(the solvers this run uses, the ones named in `solvers`), both in registry order (solvers.ordered).

        The solvers are those named in `solvers` (default: auto, as if named), plus mom25d with mom = true and the line
        model with fast = ... ('lines' until the run resolves it: qucs when qucsator is installed, else scikit;
        fast = "skrf" / "qucsator" names the line solver). --preview solves no full-wave solver, only the line model
        (openEMS prepares its mesh preview; preview=False: the plan as without --preview).
        A solver named in `solvers` is a result the run is for: if it cannot run, the run stops before it starts.
        One added by --mom / --fast is a reference next to the others: if it cannot run, that is a warning."""
        from .solvers import ordered, registry, LINES, AUTO
        line = {'skrf': 'scikit', 'qucsator': 'qucs'}.get(self.fast, LINES)
        named = [line if x == LINES else x for x in self.solvers] if self.solvers is not None else [AUTO]
        run = set(named)
        if self.mom:
            run.add('mom25d')
        if self.fast:
            run.add(line)
        if self.preview if preview is None else preview:                    # no full-wave solve: the line model
            run = {x for x in run if x in (LINES, AUTO) or registry()[x].consumes != 'board'}
        return ordered(run), ordered(named)

    def options(self):
        """The options as a plain dict (for the report and the run summary)."""
        return {k: v for k, v in dataclasses.asdict(self).items() if k != 'notes'}


def all_options(reg=None):
    """CORE, then every solver's options: [(solver name or None, Option)]."""
    reg = _solvers_pkg.registry() if reg is None else reg
    return [(None, o) for o in CORE] + [(sv.name, o) for sv in reg.values() for o in sv.options]


def settings_class(reg=None):
    """The Settings dataclass: a field per option (CORE and every solver's), plus notes."""
    fields = []
    for _, o in all_options(reg):
        d = o.default
        fields.append((o.key, o.type, field(default_factory=(lambda d=d: list(d))) if isinstance(d, list) else field(default=d)))
    fields.append(('notes', list, field(default_factory=list, repr=False)))       # remarks from reading the config
    return dataclasses.make_dataclass('Settings', fields, bases=(_Settings,),
                                      namespace=dict(__doc__=_Settings.__doc__, __module__=__name__))


Settings = settings_class()
OPTIONS = {o.key: o for _, o in all_options()}
PATH_KEYS = tuple(k for k, o in OPTIONS.items() if o.path)          # relative to the TOML file


def default_out(board):
    """The output folder when none is given: out next to the board file, or the folder of the <name>_board.json
    of an earlier run (whose folder is an output folder already)."""
    base = os.path.dirname(os.path.abspath(board))
    return base if board.lower().endswith('_board.json') else os.path.join(base, 'out')


def _convert(key, value):
    try:
        return OPTIONS[key].convert(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{key.replace('_', '-')} = {value!r}: {e}") from None


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


def _table_items(cfg, notes, ignored):
    """(key, value) of a config: the top level, an [openems] table (the older form, read like the top level) and
    the [solver.<name>] tables (each solver's options by short name or key)."""
    from .solvers import get
    out = []
    for t, v in cfg.items():
        if not isinstance(v, dict):
            out.append((t.replace('-', '_'), v))
        elif t.lower() == 'openems':                                # [openems], [OpenEMS]
            notes.append(f"[{t}]: read like the top level (the older form); openEMS's own settings now go under "
                         f"[solver.openems]")
            out += [(k.replace('-', '_'), x) for k, x in v.items() if not isinstance(x, dict)]
        elif t.lower() == 'solver':
            for name, tab in v.items():
                try:
                    sv = get(name)
                except ValueError:
                    ignored.append(f"[solver.{name}]")
                    continue
                if not isinstance(tab, dict):
                    ignored.append(f"solver.{name}")
                    continue
                by_name = {o.short: o.key for o in sv.options} | {o.key: o.key for o in sv.options}
                for k, x in tab.items():
                    k = k.replace('-', '_')
                    if k in by_name:
                        out.append((by_name[k], x))
                    else:
                        ignored.append(f"solver.{name}.{k}")
        # any other table belongs to another tool
    return out


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
    for k, v in _table_items(cfg, notes, ignored):
        if k == 'no_mask':
            k, v = 'mask', not boolean(v)
        if k not in OPTIONS:
            ignored.append(k)
            continue
        if k in PATH_KEYS and v:
            v = resolve_path(v, base, k, notes)
        values[k] = v
    return values, ignored, notes


def _suggest(key):
    """An unknown key, with the option it is probably a typo of (a solver table's key: its short names)."""
    import difflib
    if key.startswith('solver.') and key.count('.') == 2:
        from .solvers import get
        sv = get(key.split('.')[1])
        near = difflib.get_close_matches(key.split('.')[2], [o.short for o in sv.options], n=1, cutoff=0.8)
        top = key.split('.')[2] in OPTIONS
        return key + (f" (did you mean {near[0]}?)" if near else " (a top-level setting)" if top else '')
    near = difflib.get_close_matches(key, list(OPTIONS) + ['no_mask'], n=1, cutoff=0.8)
    return f"{key} (did you mean {near[0]}?)" if near else key


def build_settings(cli=None, config=None, default_solvers=None):
    """Settings from the defaults, a TOML file (path or None) and command-line values (a dict holding only
    the options that were typed). default_solvers: the solvers when neither names any (kicad2openems: openems;
    otherwise auto). Raises ConfigError for anything that cannot be used."""
    cli = dict(cli or {})
    values, notes = {}, []
    if config:
        values, ignored, notes = read_toml(config)
        if ignored:
            notes.append("ignoring keys not used here: " + ", ".join(_suggest(k) for k in ignored))
    values.update({k: v for k, v in cli.items() if k not in CLI_ONLY})
    unknown = [k for k in values if k not in OPTIONS]
    if unknown:
        raise ConfigError(f"unknown option(s): {', '.join(unknown)}")
    s = Settings(**{k: _convert(k, v) for k, v in values.items()})
    s.notes = notes
    if s.solvers is None and default_solvers:
        s.solvers = _convert('solvers', default_solvers)
    if s.fmin >= s.fmax:
        raise ConfigError(f"fmin ({s.fmin:g} GHz) must be below fmax ({s.fmax:g} GHz)")
    if s.points < 2:
        raise ConfigError("points must be at least 2")
    reg = _solvers_pkg.registry()
    for sv in reg.values():
        msg = sv.validate(s)
        if msg:
            raise ConfigError(msg)
    if s.steer and len(s.steer) > 1 and s.steer[1] and not s.grid:
        raise ConfigError("steer: a Y step needs grid (the ports' columns and rows), e.g. grid = \"4x4\"")
    run, named = s.solver_plan()
    if s.antenna:
        for x in named:
            cap = reg[x].capabilities.get('antenna', 'no') if x in reg else 'yes'
            if cap.startswith('no'):
                raise ConfigError(f"solvers: {reg[x].label} is not available in antenna mode" +
                                  (f" ({cap.split(':', 1)[1].strip()})" if ':' in cap else ''))
        able = [n for n, sv in reg.items() if not sv.capabilities.get('antenna', 'no').startswith('no')]
        if not s.preview and 'auto' not in run and not any(x in able for x in run):
            raise ConfigError(f"mode = antenna needs a solver with a far field: add {' or '.join(able)} to solvers "
                              f"(or use --preview)")
    return s
