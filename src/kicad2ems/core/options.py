"""
Settings as data: Option (one setting of a run, declared once in config.CORE or
by a solver) and the converters that turn a config or command-line value
into a setting.

    Option('mom_dmax', 0.3,
           'largest MoM cell along the copper, mm (default {default})',
           float, name='dmax', gui='MoM cell (mm)', group='mom25d')

From the option lists come the Settings fields (config.Settings), the
command-line options (cli.parser), the config keys (config.read_toml: the key at
the top level, the short name under [solver.<name>]) and the GUI's form
(gui.optionrow). The analyses' settings are Options too (Analysis.options):
a pcbqs tool's table (table), a fem tool's parser (from_parser); add_arguments
makes a tool's parser of them, argv the command line of values, and
Option.describe the tool's --describe --json row.
"""
import re
from dataclasses import dataclass

__all__ = ['Option', 'strlist', 'items', 'floatlist', 'floats', 'lines',
           'split', 'table', 'from_parser', 'add_arguments', 'argv']
KINDS = {bool: 'bool', float: 'float', int: 'int', list: 'list'}


@dataclass
class Option:
    """One setting: a Settings field, a command-line option, a config key and,
    with `gui`, a GUI field."""
    # Settings field, --key-with-dashes on the command line, key at the config's
    # top level
    key: str
    default: object = None
    # command-line help; {default} is replaced by the default
    help: str = ''
    # float, int, bool (a switch on the command line), str or list
    type: type = str
    choices: tuple = None
    # value (config or command line) -> setting; raises ValueError saying what
    # is wrong
    conv: object = None
    name: str = None            # its key under [solver.<name>] (default: key)
    gui: str = None             # label of its GUI field (None: no field)
    group: str = None           # command-line and GUI group
    # a path: relative to the config file it is read from
    path: bool = False
    cli: dict = None            # argparse extras: flags, dest, metavar, action
    # 'basic': one of the few a run is set up with (a window shows those first,
    # the rest under "More settings"); None or 'more': the rest
    level: str = None
    # what it names on the board, for a front end to pick there
    # (kicad2ems.core.layout): 'net', 'part', or 'nets', 'parts' (several)
    pick: str = None
    required: bool = False      # a run needs it set

    @property
    def kind(self):
        """'bool', 'float', 'int', 'list' or 'str'."""
        return KINDS.get(self.type, 'str')

    @property
    def flag(self):
        return self.flags[0]

    def describe(self):
        """Its row of a tool's --describe --json (pick and level when set)."""
        return dict(
            key=self.key, flag=self.flag, kind=self.kind, default=self.default,
            help=self.help, group=self.group,
            choices=list(self.choices) if self.choices else None,
            **{k: getattr(self, k) for k in ('level', 'pick')
               if getattr(self, k)})

    @property
    def short(self):
        return self.name or self.key

    @property
    def flags(self):
        return ((self.cli or {}).get('flags')
                or ['--' + self.key.replace('_', '-')])

    def convert(self, value):
        """The setting of a value, or ValueError."""
        if value is None:
            return None
        conv = (self.conv
                or {float: float, int: whole, bool: boolean, list: strlist}.get(
                    self.type, str))
        v = conv(value)
        if self.choices and v not in self.choices:
            raise ValueError(
                f"choose one of {', '.join(map(str, self.choices))}")
        return v


# ---- tools' options ------------------------------------------------------
def table(rows, conv=None):
    """Options of a compact table, [(key, kind, default, help, group[,
    choices])], kind a KINDS name; a list's items split by conv (lines)."""
    types = {v: k for k, v in KINDS.items()} | {'str': str}
    return [
        Option(k, d, h, types[t], tuple(c[0]) if c and c[0] else None,
               group=g, conv=(conv or lines) if t == 'list' else None)
        for k, t, d, h, g, *c in rows
    ]


def from_parser(parser, groups=None, pick=None):
    """The Options of a tool's argparse parser: every option but the help and
    the output folder (a front end gives that); groups and pick: {key: its
    group, its pick}."""
    out = []
    for a in parser._actions:
        if not a.option_strings or a.dest in ('help', 'out'):
            continue
        t = (bool if a.nargs == 0 else
             list if type(a).__name__ == '_AppendAction' else
             a.type if a.type in (float, int) else str)
        flag = a.option_strings[0]
        out.append(Option(
            a.dest, ([] if t is list else False if t is bool else
                     '' if a.default is None else a.default), a.help or '',
            t, tuple(a.choices) if a.choices else None,
            conv=split if t is list else None,
            group=(groups or {}).get(a.dest, 'Solver'),
            cli=(None if flag == '--' + a.dest.replace('_', '-') else
                 dict(flags=[flag])),
            pick=(pick or {}).get(a.dest), required=bool(a.required)))
    return out


def add_arguments(ap, options):
    """Options into an argparse parser, as a tool's command line: a bool a
    switch, a list repeatable."""
    for o in options:
        kw = dict(dest=o.key, help=o.help)
        if o.kind == 'bool':
            kw['action'] = 'store_true'
        elif o.kind == 'list':
            kw.update(action='append', default=[])
        else:
            kw.update(type={'float': float, 'int': int}.get(o.kind, str),
                      default=o.default)
            if o.choices:
                kw['choices'] = list(o.choices)
        ap.add_argument(o.flag, **kw)


def argv(options, values):
    """The command-line arguments of values {option key: text, bool or list},
    in their order (a key of none of the options left out): a switch when
    true; a list an item a flag (its conv splits a text); others when set and
    not the default (a value that starts with a dash joined to its flag)."""
    out, by = [], {o.key: o for o in options}
    for o, v in ((by[k], v) for k, v in values.items() if k in by):
        if o.kind == 'bool':
            if v is True or str(v).strip().lower() in ('1', 'true', 'yes',
                                                      'on'):
                out.append(o.flag)
        elif o.kind == 'list':
            for item in (o.conv or lines)(v) or []:
                out += [o.flag, item]
        else:
            s = '' if v is None else str(v).strip()
            if s and s != str(o.default):
                out += ([f"{o.flag}={s}"] if s.startswith('-') else
                        [o.flag, s])
    return out


# ---- converters -------------------------------------------------------------
def strlist(v):
    """A list of strings: a list, or a comma-separated string. None for an empty
    one."""
    items = v if isinstance(v, (list, tuple)) else str(v).split(',')
    out = [str(x).strip() for x in items if str(x).strip()]
    return out or None


def items(v):
    """A list of strings that may hold commas themselves (port specs, dump
    specs): a list, or one string."""
    return [
        str(x).strip()
        for x in (v if isinstance(v, (list, tuple)) else [v])
        if str(x).strip()
    ]


def lines(v):
    """A list's items: a list, or text an item a line or comma-separated where
    the next item is KEY=... (an item may hold commas: CA1=47e-6,0.02)."""
    if isinstance(v, (list, tuple)):
        v = '\n'.join(map(str, v))
    return [s.strip() for s in re.split(r'\n|,(?=\s*[^\s,=]+\s*=)',
                                        str(v or '')) if s.strip()]


def split(v):
    """A list's items: a list, or text split at lines and commas."""
    if isinstance(v, (list, tuple)):
        v = '\n'.join(map(str, v))
    return [s.strip() for s in re.split(r'[\n,]', str(v or '')) if s.strip()]


def floatlist(v):
    """Floats from a list or a comma-separated string ('1,1.5' or [1, 1.5]);
    None or '' give []."""
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
