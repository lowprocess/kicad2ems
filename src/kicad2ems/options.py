"""
Settings as data: Option (one setting of a run, declared once in config.CORE or by a solver plugin) and the
converters that turn a config or command-line value into a setting.

    Option('mom_dmax', 0.3, 'largest MoM cell along the copper, mm (default {default})', float, name='dmax',
           gui='MoM cell (mm)', group='mom25d')

From the option lists come the Settings fields (config.Settings), the command-line options (cli.parser), the
config keys (config.read_toml: the key at the top level, the short name under [solver.<name>]) and the GUI
fields (gui.runner.FIELDS).
"""
from dataclasses import dataclass


@dataclass
class Option:
    """One setting: a Settings field, a command-line option, a config key and, with `gui`, a GUI field."""
    key: str                    # Settings field, --key-with-dashes on the command line, key at the config's top level
    default: object = None
    help: str = ''              # command-line help; {default} is replaced by the default
    type: type = str            # float, int, bool (a switch on the command line), str or list
    choices: tuple = None
    conv: object = None         # value (config or command line) -> setting; raises ValueError saying what is wrong
    name: str = None            # its key under [solver.<name>] (default: key)
    gui: str = None             # label of its GUI field (None: no field)
    group: str = None           # command-line and GUI group
    path: bool = False          # a path: relative to the config file it is read from
    cli: dict = None            # argparse extras: flags, dest, metavar, action

    @property
    def short(self):
        return self.name or self.key

    @property
    def flags(self):
        return (self.cli or {}).get('flags') or ['--' + self.key.replace('_', '-')]

    def convert(self, value):
        """The setting of a value, or ValueError."""
        if value is None:
            return None
        conv = self.conv or {float: float, int: whole, bool: boolean, list: strlist}.get(self.type, str)
        v = conv(value)
        if self.choices and v not in self.choices:
            raise ValueError(f"choose one of {', '.join(map(str, self.choices))}")
        return v


# ---- converters ---------------------------------------------------------------------------------------
def strlist(v):
    """A list of strings: a list, or a comma-separated string. None for an empty one."""
    items = v if isinstance(v, (list, tuple)) else str(v).split(',')
    out = [str(x).strip() for x in items if str(x).strip()]
    return out or None


def items(v):
    """A list of strings that may hold commas themselves (port specs, dump specs): a list, or one string."""
    return [str(x).strip() for x in (v if isinstance(v, (list, tuple)) else [v]) if str(x).strip()]


def floatlist(v):
    return [float(x) for x in (strlist(v) or [])]


def floats(n):
    """n numbers: a list or a comma-separated string, as a tuple."""
    def conv(v):
        vals = floatlist(v)
        if len(vals) != n:
            raise ValueError(f"expected {n} numbers separated by commas")
        return tuple(vals)
    return conv


def boolean(v):
    if isinstance(v, bool):
        return v
    if str(v).lower() in ('1', 'true', 'yes', 'on'):
        return True
    if str(v).lower() in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError("expected true or false")


def whole(v):
    f = float(v)
    if f != int(f):
        raise ValueError("expected a whole number")
    return int(f)
