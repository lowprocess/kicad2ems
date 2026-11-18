"""
The Runs page's words for the runs kicad2ems's one index lists
(kicad2ems.core.runs: index, info, RunInfo, read from each folder's mark):

    in_scope(runs, 'config', config)     # those of a config: see in_scope
    differ(old, now)                     # [(key, old, now)]: settings changed
    size_text(n), took_text(s)           # '1.2 GB', '3 min 02 s'
"""
from kicad2ems.core.runs import same_config

SCOPES = ('config', 'board', 'all')
# settings that say nothing of the run: where it went, its event file
NOISE = ('out', 'events', 'preview')


def in_scope(runs, scope, config=None):
    """The runs a scope shows: 'config', those started from the config (the
    file their mark names: with no config, an unsaved form's and the runs from
    before the mark kept one); 'board', 'all': as listed. The preview:
    always."""
    if scope != 'config':
        return list(runs)
    return [r for r in runs if r.preview or same_config(r.config, config)]


def _plain(k, v):
    if isinstance(v, (list, tuple)):
        v = [_plain('', x) for x in v]
        return sorted(v, key=str) if k == 'solvers' else v
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    return str(v)


def norm(values):
    """Settings as a run's file keeps them (core.runs.write_toml): no empty
    ones, no noise (NOISE), the solvers in no order."""
    out = {}
    for k, v in (values or {}).items():
        if (k in NOISE or v is None or v == '' or (v == [] and k != 'solvers')
                or (k in ('cut_ports', 'mom') and v is False)):
            continue
        out[k] = _plain(k, v)
    return out


def differ(old, now):
    """[(key, old, now)] of the settings that differ (both as norm has them;
    None: not set)."""
    a, b = norm(old), norm(now)
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
            if a.get(k) != b.get(k)]


def size_text(n):
    for unit, f in (('GB', 1e9), ('MB', 1e6), ('kB', 1e3)):
        if n >= f:
            return f"{n / f:.{0 if n >= 10 * f else 1}f} {unit}"
    return f"{n} B"


def took_text(s):
    if s is None:
        return ''
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"
