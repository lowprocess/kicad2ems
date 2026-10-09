"""
TOML run configuration. Keys are the command-line options (dashes or underscores); openEMS-specific
options may also sit under an [openems] table, so one file can also drive other tools (unknown keys are ignored):

    pos = "board-top.pos"
    nets = ["RF", "GND"]
    fmin = 1.0
    fmax = 6.0
    no_mask = true
    [openems]
    excite = ["P1"]
    air = 5.0

Values given explicitly on the command line win over the file.
"""
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:                                                           # pragma: no cover
    import tomli as tomllib

LIST_KEYS = ('nets', 'region', 'excite', 'dump_port', 'converge', 'converge_tol')
KEEP_LIST = ('dump',)                                          # specs contain commas: stay a list


def apply_toml(args, parser, path, argv=None):
    """Update parsed args from a TOML file, except options typed on the command line."""
    with open(path, 'rb') as fh:
        cfg = tomllib.load(fh)
    typed = set()
    for tok in (argv if argv is not None else sys.argv[1:]):
        if tok.startswith('--'):
            typed.add(tok[2:].split('=')[0].replace('-', '_'))
    known = vars(args)
    ignored = []
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
            setattr(args, k, v)
    return ignored
