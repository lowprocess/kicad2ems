"""
The GUI's settings form, without Qt (testable without a display): its options
and sections (made from the option tables and the solver registry), the solver
tick boxes, and a config read into the form and written back (save_config keeps
the file's comments and order). The run itself (Run, EventTail, stop_runs,
find_results) is kicad2ems.core.runs, which the KiCad plugin uses too.
"""
import os

from kicad2ems.core.runs import write_toml as _write_toml

# the core option groups (options.Option.group) as the form's group boxes; a
# solver's under their own name, its section ('FDTD/Mesh': the Mesh sub-section
# of the FDTD section)
GUI_GROUPS = {
    'board': 'Board', 'band': 'Frequency', 'solvers': 'Solvers',
    'output': 'Output', 'antenna': 'Output', 'field dumps': 'Output'
}
FIRST, LAST = ['Board', 'Frequency', 'Solvers'], ['Output']


def solver_sections(reg=None):
    """{section: [solver names]}, in registry order: the sections of the
    solvers' settings, each greyed while none of its solvers is ticked. A
    section several solvers list (the FDTD one: openEMS's and gfdtd's mesh and
    run settings) has them all."""
    from kicad2ems.core.solvers import registry
    out = {}
    for name, sv in (registry() if reg is None else reg).items():
        for o in sv.options:
            top = GUI_GROUPS.get(o.group, o.group).split('/')[0]
            if (o.gui and top not in FIRST + LAST
                    and name not in out.setdefault(top, [])):
                out[top].append(name)
    return out


def section_title(top, names):
    """A section's heading: its name, and the solvers sharing it unless it is
    one of theirs ('FDTD (openEMS, gfdtd)')."""
    from kicad2ems.core.solvers import registry
    if len(names) < 2 or top in names:
        return top
    return (f"{top} "
            f"({', '.join(registry()[n].label.split(' (')[0] for n in names)})")


SECTION_SOLVERS = {top: set(names) for top, names in solver_sections().items()}
SECTION_ORDER = FIRST + list(SECTION_SOLVERS) + LAST
SECTION_TITLES = {
    top: section_title(top, names)
    for top, names in solver_sections().items()
}
# single settings greyed unless their case holds: the antenna's in antenna mode,
# with a solver that has one
ANTENNA_FIELDS = ('ff_freq', 'drive', 'grid', 'steer', 'taper')
# the line model's qucsator is a setting of the machine, not of a board: the
# Solvers and hardware window
TICK = 'solver:'  # a solver's tick box: solver:<name>


def make_form():
    """The options the GUI edits, in form order: those with a GUI label
    (config.CORE, then each solver's) and a tick box per solver (an Option of
    key solver:<name>) heading the Solvers group; Board and Band first, Output
    last."""
    from kicad2ems.core.config import all_options
    from kicad2ems.core.options import Option
    from kicad2ems.core.solvers import registry, AUTO
    tick = lambda n, label, about='': Option(
        TICK + n, False, ' '.join(str(about).split()), bool, gui=label,
        group='solvers')
    rows = [tick(AUTO, 'auto: fastest full-wave + line model')]
    rows += [
        tick(n, sv.label, getattr(sv, 'description', None) or sv.__doc__
             or sv.label) for n, sv in registry().items()
    ]
    rows += [o for _, o in all_options() if o.gui]
    rank = {g: i for i, g in enumerate(SECTION_ORDER)}
    # the folder first in Output; otherwise stable: the option order within a
    # section
    return sorted(rows, key=lambda o: (rank.get(
        group(o).split('/')[0], len(rank) - 1), o.key != 'out'))


def group(o):
    """An option's group box in the form ('FDTD/Mesh': the Mesh sub-section of
    the FDTD section)."""
    return GUI_GROUPS.get(o.group, o.group)


FORM = make_form()
# the few settings a run is set up with, shown first (Option.level = 'basic':
# kicad2ems's own, and those a solver names in Solver.basic); the rest are
# under "More settings"
BASIC = {o.key for o in FORM if o.level == 'basic'}
SEMICOLON_LISTS = ('ports', 'dump')  # items that may hold commas themselves
# the tick boxes are not settings of their own: the form's values carry solvers
# = the ticked solvers (see solvers_from_form). 'solvers', 'mom' and 'fast'
# belong to the boxes, like the fields
MANAGED = ({o.key
            for o in FORM
            if not o.key.startswith(TICK)} | {'solvers', 'mom', 'fast'})


def solver_ticks(vals):
    """{solver:<name>: ticked} for a settings dict: the solvers its plan runs
    (openEMS by default; solvers, mom = true and fast name the others; 'lines'
    as resolved here). solvers = [] ticks none."""
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.solvers import registry, resolve_lines, LINES, AUTO
    if vals.get('solvers') == []:
        run = []
    else:
        s = build_settings({
            k: vals.get(k)
            for k in ('solvers', 'mom', 'fast')
            if vals.get(k) not in (None, '')
        })
        run = s.solver_plan(preview=False)[0]
        run = [resolve_lines(s) if x == LINES else x for x in run]
    return {TICK + n: n in run for n in [AUTO] + list(registry())}


def solvers_from_form(vals):
    """The form's values (with the solver tick boxes) as settings: solvers lists
    the ticked solvers, or is left to its default (auto) when that alone is
    ticked; an empty list when none is. Without tick boxes in vals (settings
    rather than the form's own values), vals as they are."""
    out = dict(vals)
    ticks = [k for k in out if k.startswith(TICK)]
    if not ticks:
        return out
    on = [k[len(TICK):] for k in ticks if out.pop(k)]
    for k in ('mom', 'fast', 'solvers'):
        out.pop(k, None)
    if on != ['auto']:
        out['solvers'] = on
    return out


def write_toml(path, values):
    """A flat TOML file of the non-empty settings
    (kicad2ems.core.runs.write_toml), the solver tick boxes as solvers."""
    _write_toml(path, solvers_from_form(values))


def relative(path, base):
    """path relative to base, with '/', where there is a relative path: on
    Windows another drive (or a UNC share) has none, and os.path.relpath's
    ValueError had stopped a config's save halfway (its analyses' tables not
    written); there the path as it is."""
    try:
        return os.path.relpath(path, base).replace(os.sep, '/')
    except ValueError:
        return path


def _rel(path, base):
    """A path written relative to the config's folder where that is possible
    (portable configs), with '/' (Windows reads it too, and the config moves
    between systems)."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # another drive (Windows)
        return path
    # at most one up
    return (path if rel.startswith(os.pardir + os.sep
                                   + os.pardir) else rel.replace(os.sep, '/'))


def save_config(path, values, template=None):
    """Write the GUI's settings to a TOML config at `path`.

    Paths are written relative to the file's folder. With `template` (the config
    that was loaded), its text is kept: comments, order and tables stay, each
    setting gets its new value where it is written (keeping a trailing
    comment), settings whose field was cleared are removed and new ones are
    added above the first table. Without a template, only the settings that
    differ from the defaults are written."""
    from kicad2ems.core.config import Settings, PATH_KEYS, setting_keys
    from kicad2ems.core.runs import load_toml, update_toml
    base = os.path.dirname(os.path.abspath(path))
    vals = {}
    for k, v in solvers_from_form(values).items():
        if k in ('events', 'preview'):
            continue
        if k in PATH_KEYS and isinstance(v, str) and v:
            v = _rel(os.path.abspath(v), base)
        vals[k] = list(v) if isinstance(v, tuple) else v
    empty = lambda v: v is None or v == '' or v == []
    defaults, edits, seen = Settings(), {}, set()
    has = template is not None and os.path.exists(template)
    for table, raw, k, _ in setting_keys(load_toml(template) if has else {}):
        if raw is None:  # a table of its own ([vary]): kept
            seen.add(k)
        elif k in vals or k in MANAGED:  # else a key the GUI does not know
            seen.add(k)
            v = vals.get(k)
            edits[table, raw] = (None if empty(v) else
                                 not v if raw == 'no_mask' else v)
    for k, v in vals.items():
        if not (k in seen or empty(v) or
                (k != 'out' and getattr(defaults, k, object()) == v)):
            edits['', k] = v
    update_toml(path, edits, source=template if has else None, head=[
        "# kicad2ems run configuration (written by the GUI); paths are "
        "relative to this file"])


def extra_values(path):
    """The settings of a TOML config that the GUI has no field for (kept and
    passed on to its runs)."""
    from kicad2ems.core.config import read_toml
    return {
        k: v
        for k, v in read_toml(path)[0].items()
        if k not in MANAGED and k not in ('events', 'preview')
    }


def settings_values(path):
    """The GUI's fields from a TOML config (through the same rules as the
    command line). The solver tick boxes show the solvers the config runs
    (solver_ticks)."""
    from kicad2ems.core.config import build_settings, read_toml
    s = build_settings({}, path)
    out = {}
    for o in FORM:
        v = getattr(s, o.key, None)
        out[o.key] = list(v) if isinstance(v, tuple) else v
    # the default folder: left to the run, not pinned here
    if 'out' not in read_toml(path)[0]:
        out['out'] = None
    out.update(solver_ticks(dict(solvers=s.solvers, mom=s.mom, fast=s.fast)))
    return out
