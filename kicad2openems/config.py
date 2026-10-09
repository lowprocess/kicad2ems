"""
TOML run configuration. Keys are the command-line options (dashes or underscores); openEMS-specific
options may also sit under an [openems] table, so one file can also drive other tools (unknown keys are
ignored):

    board = "board.kicad_pcb"       # then:  kicad2openems board.toml
    pos = "board-top.pos"
    nets = ["RF", "GND"]
    fmin = 1.0
    fmax = 6.0
    no_mask = true
    [openems]
    excite = ["P1"]
    air = 5.0

Values given explicitly on the command line win over the file. Paths in the file (board, pos, compare,
out) are relative to the file's folder; a path that only exists relative to the working directory (older
configs written for the repository root) is still used, with a note.
"""
import os, sys

if sys.version_info >= (3, 11):
    import tomllib
else:                                                           # pragma: no cover
    import tomli as tomllib

LIST_KEYS = ('nets', 'region', 'excite', 'dump_port', 'converge', 'converge_tol')
KEEP_LIST = ('dump',)                                          # specs contain commas: stay a list
PATH_KEYS = ('board', 'pos', 'compare', 'out')                  # relative to the TOML file


def resolve_path(value, base, key, notes):
    """A path from the TOML file at `base`: relative to base, or - if only that exists - to the working
    directory (configs written for the repository root), with a note."""
    value = os.path.expanduser(str(value))
    if os.path.isabs(value):
        return value
    here = os.path.normpath(os.path.join(base, value))
    target = here if key != 'out' else (os.path.dirname(here) or '.')      # out: its parent must exist
    cwd_target = value if key != 'out' else (os.path.dirname(value) or '.')
    if not os.path.exists(target) and os.path.exists(cwd_target) and os.path.normpath(value) != here:
        notes.append(f"{key} = {value!r}: not found next to the config; using it relative to the working "
                     f"directory (write it relative to the config file)")
        return value
    return here


def apply_toml(args, parser, path, argv=None):
    """Update parsed args from a TOML file, except options typed on the command line."""
    with open(path, 'rb') as fh:
        cfg = tomllib.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    typed = set()
    for tok in (argv if argv is not None else sys.argv[1:]):
        if tok.startswith('--'):
            typed.add(tok[2:].split('=')[0].replace('-', '_'))
    if getattr(args, 'board', None):
        typed.add('board')                                         # a board given on the command line wins
    known = vars(args)
    ignored, notes = [], []
    for k, v in list(cfg.items()) + list(cfg.get('openems', {}).items()):
        k = k.replace('-', '_')
        if k == 'openems' or isinstance(v, dict):
            continue
        if k == 'no_mask':
            k, v = 'mask', not v
        if k in LIST_KEYS and isinstance(v, list):
            v = ",".join(map(str, v))
        if k in KEEP_LIST and isinstance(v, str):
            v = [v]
        if k not in known:
            ignored.append(k)
            continue
        if k not in typed:
            if k in PATH_KEYS and v:
                v = resolve_path(v, base, k, notes)
            setattr(args, k, v)
    args.config_notes = notes
    return ignored
