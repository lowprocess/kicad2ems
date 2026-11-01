"""
One section of a line model's drawing, to look at (no Qt here): LineModel.drawing gives each section the
cross-sections it can be seen on (e['section']), and this solves one when asked. A whole board's sections would take
a minute; one takes about 0.1 s, 2 s at worst.

    modes(e)                    # [(key, label)]: the views a section has, the first the one its numbers come from
    v = solve(e, key)           # v.field (xsection.Field), v.xs, v.volts, v.window, v.values, v.z0, v.eeff
    x, z, phi, ex, ez, er = v.field.sample(*v.window, 400, 200)

The keys:
    cut          the board's copper across the section, each layer's (reference._cut_section)
    classified   the grounded CPW classify found: the strip, its gaps, the plane
    line         a coupled section, its group's strips: this line at 1 V, the others at 0
    odd, even    the pair it is in: +1 V and -1 V, +1 V and +1 V
"""
from dataclasses import dataclass
from . import xsection as X
from .topology import _pair_modes


VIEW_GROW = 0.1         # the mesh a field is drawn on: graded four times as finely as the one the numbers come from, for
                        # smooth colours (bilinear cells: the field steps from cell to cell)


@dataclass
class View:
    field: X.Field
    xs: X.CrossSection
    volts: list
    window: tuple
    values: dict                # name: value, for the read-out
    z0: float
    eeff: float


def _pair(c):
    """The lines of a coupled leg's pair: it and the next line (the last: the one before)."""
    k, n = c['index'], len(X.CrossSection.from_dict(c['section']).signals)
    return (k, k + 1) if k + 1 < n else (k - 1, k)


def modes(e):
    """[(key, label)] of the views section e has, best first: a coupled pair's odd mode, else the board's cut."""
    s = e.get('section') or {}
    out = []
    if 'coupled' in s:
        out += [('odd', 'pair, odd mode (differential)'), ('even', 'pair, even mode (common)'),
                ('line', 'this line alone, the others at 0 V')]
    if 'cut' in s:
        out.append(('cut', "the board's copper across it"))
    if 'classified' in s:
        out.append(('classified', 'as classified: strip, gaps, plane'))
    return out


def solve(e, key=None):
    """The View of section e in mode `key` (default: modes(e)'s first). ValueError where it has no such view."""
    s = e.get('section') or {}
    have = [k for k, _ in modes(e)]
    key = key or (have[0] if have else None)
    if key not in have:
        raise ValueError(f"section {e.get('label')!r} has no view {key!r}")
    c = s.get('coupled')
    xs = X.CrossSection.from_dict(c['section'] if key in ('odd', 'even', 'line') else s[key])
    r = xs.solve()
    n = len(xs.signals)
    volts, values = [0.0] * n, {}
    if key in ('odd', 'even'):
        a, b = _pair(c)
        ze, zo, ee, eo = _pair_modes(r.C, r.Ca, a)
        volts[a], volts[b] = 1.0, (-1.0 if key == 'odd' else 1.0)
        z0, eeff = (zo, eo) if key == 'odd' else (ze, ee)
        values = {'Zodd': zo, 'Zdiff': 2 * zo} if key == 'odd' else {'Zeven': ze, 'Zcommon': ze / 2}
    else:
        i = c['index'] if key == 'line' else 0
        volts[i] = 1.0
        z0, eeff = r.single(i)
        values = {'Z0': z0}
    values.update(eeff=eeff, nodes=r.nodes)
    return View(X.field(xs, volts, grow=VIEW_GROW), xs, volts, X.window(xs), values, z0, eeff)
