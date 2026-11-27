"""
Templates: parametric groups of drawn shapes (kicad2ems.core.shapes) that the
Draw page places with a click and that are then ordinary shapes, edited as any
other. No Qt here: each template's parameters and the shapes it makes.

    t = TEMPLATES['can']; t.name, t.tip, t.params   # Param(key, label, ...)
    v = defaults('circulator', stack, 'F.Cu')       # {key: value}, sized to
                                                    #  the board's stack
    shapes = make('can', v, stack, 'F.Cu', at=(x, y))
    shapes = make('fence', v, stack, 'F.Cu', along=[trace, ...])
    sub = substrate(stack, 'F.Cu')     # the copper's height and thickness and
                                       #  the dielectric under it (h, er, tand)
    w = line_width(50, sub)            # a microstrip's width for 50 ohm

The templates (each's shapes centred on `at`, along x, unless said):

    can         a shield can: a metal box over the board, air inside it (the
                air drawn after the metal hollows it, open at the bottom),
                and a fence of metal posts through the board under its walls
    dr          a dielectric resonator puck beside a microstrip line, the line
                ported at both ends
    circulator  a microstrip Y-junction circulator (examples/rf_circulator's
                design): a ferrite puck set into the substrate, a copper
                junction disc, three arms 120 deg apart with quarter-wave
                transformers, ports at their ends, and a magnet over it
    sma         an edge-launch connector as a square coax: a metal body round
                a PTFE channel and a metal pin onto a 50 ohm line (`at`: the
                board's edge; `facing` the way the connector points)
    absorber    a sheet of lossy dielectric (an absorber's loss as er and
                tan d: the solvers have no magnetic loss)
    fence       metal posts along each side of the selected traces (`along`)

Line widths are worked out on the stack (core.lines.formulas.microstrip, over
the dielectric down to the next copper layer). Heights are the stack's: a
shape on the top layer stands on its copper (cu_z + t) and goes up; on the
bottom layer it hangs below and goes down.
"""
import math
from dataclasses import dataclass, field

__all__ = ['Param', 'Template', 'TEMPLATES', 'defaults', 'make', 'substrate',
           'line_width', 'fence_posts', 'coax_pin']

C0 = 299792458.0


@dataclass
class Param:
    """One of a template's numbers (or a tick, or a choice): its key, label,
    default, unit and range; kind 'float', 'int', 'bool' or 'choice'
    (`choices`)."""
    key: str
    label: str
    default: object
    unit: str = ''
    tip: str = ''
    lo: float = 0.0
    hi: float = 1000.0
    decimals: int = 3
    kind: str = 'float'
    choices: tuple = ()


@dataclass
class Template:
    """A template: its name, what it makes (tip), its parameters and how it
    is placed: 'point' (a click: its centre, or the board's edge for the
    connector) or 'traces' (the selected traces)."""
    key: str
    name: str
    tip: str
    params: tuple
    place: str = 'point'
    prompt: str = ''
    make: object = field(default=None, repr=False)


# ---- the board's stack ------------------------------------------------------
def substrate(stack, layer):
    """What a template on copper layer `layer` stands on, from a stack
    (core.shapes.stack_of's): dict(z (the copper's lower face), t (its
    thickness), surface (the copper's outer face), up (+1: the layer faces
    up, the board's top half; -1: down), h, er, tand (the dielectric between
    it and the next copper layer inward, er and tand weighted by thickness),
    zref (that layer's z), zb, zt (the board's dielectric bottom and top))."""
    cu = stack.get('cu_z') or {'F.Cu': 1.6}
    z = float(cu.get(layer, max(cu.values())))
    t = float((stack.get('cu_t') or {}).get(layer, 0.035))
    ls = [l for l in stack.get('layers') or [] if l['z1'] > l['z0']]
    zb = min([l['z0'] for l in ls] or [min(cu.values())])
    zt = max([l['z1'] for l in ls] or [max(cu.values())])
    up = 1 if z >= (zb + zt) / 2 else -1
    others = [v for k, v in cu.items() if k != layer]
    inward = [v for v in others if (v < z if up > 0 else v > z)]
    zref = (max(inward) if up > 0 else min(inward)) if inward else (
        zb if up > 0 else zt)
    lo, hi = sorted((zref, z))
    h = hi - lo
    er = tand = 0.0
    for l in ls:
        d = min(hi, l['z1']) - max(lo, l['z0'])
        if d > 0:
            er += d * float(l.get('er', 4.3) or 4.3)
            tand += d * float(l.get('tand', 0.0) or 0.0)
    if h <= 0:  # (one copper layer: the whole board under it)
        h = max(zt - zb, 0.1)
    er, tand = (er / h, tand / h) if er else (4.3, 0.02)
    surface = z + t if up > 0 else z - t
    return dict(z=z, t=t, surface=surface, up=up, h=h, er=er, tand=tand,
                zref=zref, zb=zb, zt=zt)


def _span(sub, a, b, base=None):
    """(z0, z1) of a solid from a to b mm out of the board from `base`
    (default: the copper's outer face), outward (up on the top layer, down on
    the bottom)."""
    base = sub['surface'] if base is None else base
    p, q = base + sub['up'] * a, base + sub['up'] * b
    return round(min(p, q), 6), round(max(p, q), 6)


def _bisect(fn, target, lo, hi, n=60):
    """x in [lo, hi] with fn(x) = target, fn falling as x grows."""
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if fn(mid) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def line_width(z0, sub):
    """A microstrip's width (mm) of impedance z0 ohm over the substrate `sub`
    (substrate's: Hammerstad and Jensen's, the copper's thickness left out as
    examples/make_rf_examples.py leaves it, so that the circulator is the
    example's on its board)."""
    from .lines.formulas import microstrip
    h, er = sub['h'], sub['er']
    return round(_bisect(lambda w: microstrip(w, h, er)[0], z0, 0.002 * h,
                         40 * h), 3)


def _eeff(w, sub):
    from .lines.formulas import microstrip
    return microstrip(w, sub['h'], sub['er'])[1]


def coax_pin(zc, d, er):
    """The side (mm) of a square pin in a square channel d mm across, filled
    with er, for a coax of impedance zc ohm (a round pin's 60/sqrt(er) ln(1.0787
    d / dp) in a square outer, the pin's equivalent diameter 1.18 x its
    side)."""
    return d * 1.0787 / 1.1803 * math.exp(-zc * math.sqrt(er) / 60.0)


# ---- the templates' shapes ----------------------------------------------------
def _shape(*a, **kw):
    from .shapes import Shape
    return Shape(*a, **kw)


def _can(v, sub, layer, at):
    """A metal box w x l, h tall over the board (walls t thick), air inside,
    and posts through the board under its walls."""
    x, y = at
    w, l, h, t = v['w'], v['l'], v['h'], v['t']
    if min(w, l) <= 2 * t or h <= t:
        raise ValueError('the can is smaller than its walls')
    z0, z1 = _span(sub, 0.0, h)
    zi0, zi1 = _span(sub, 0.0, h - t)
    out = [_shape('rect', [[x - w / 2, y - l / 2], [x + w / 2, y + l / 2]],
                  material='metal', z0=z0, z1=z1, name='can'),
           _shape('rect', [[x - w / 2 + t, y - l / 2 + t],
                           [x + w / 2 - t, y + l / 2 - t]],
                  material='air', z0=zi0, z1=zi1, name='can_air')]
    if v.get('fence'):
        # under the walls' middle, evenly round the rectangle, a post at
        # every corner
        a, b = w / 2 - t / 2, l / 2 - t / 2
        ring = [(x - a, y - b), (x + a, y - b), (x + a, y + b), (x - a, y + b)]
        pts = []
        for k in range(4):
            (x0, y0), (x1, y1) = ring[k], ring[(k + 1) % 4]
            n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / v['pitch']))
            pts += [(x0 + (x1 - x0) * j / n, y0 + (y1 - y0) * j / n)
                    for j in range(n)]
        out += [_shape('circle', [[round(px, 6), round(py, 6)]],
                       radius=v['post'] / 2, material='metal', z0=sub['zb'],
                       z1=sub['zt'], name=f"can_post_{k + 1}")
                for k, (px, py) in enumerate(pts)]
    return out


def _dr(v, sub, layer, at):
    """A dielectric puck beside a microstrip line, ported at both ends."""
    x, y = at
    w, L, r = v['width'], v['length'], v['r']
    side = 1 if v['side'] == '+y' else -1
    cy = y + side * (w / 2 + v['gap'] + r)
    z0, z1 = _span(sub, 0.0, v['height'], base=sub['z'])
    out = [_shape('path', [[x - L / 2, y], [x + L / 2, y]], width=w,
                  layer=layer, name='line'),
           _shape('circle', [[x, round(cy, 6)]], radius=r,
                  material='dielectric', z0=z0, z1=z1, er=v['er'],
                  tand=v['tand'], name='dr')]
    if v.get('ports'):
        out += [_shape('port', [[x - L / 2 + 0.5, y]], layer=layer, rot=0.0,
                       name='P1'),
                _shape('port', [[x + L / 2 - 0.5, y]], layer=layer,
                       rot=180.0, name='P2')]
    return out


def _circulator(v, sub, layer, at):
    """examples/rf_circulator's design on this stack."""
    cx, cy = at
    w = v['width'] or line_width(50.0, sub)
    rp, rd, arm = v['r_puck'], v['r_disc'], v['arm']
    if not rd < rp < arm:
        raise ValueError('the disc must be inside the puck, the puck inside '
                         'the arms')
    zp0, zp1 = sorted((sub['zref'], sub['z']))
    out = [_shape('circle', [[cx, cy]], radius=rp, material='ferrite',
                  z0=round(zp0, 6), z1=round(zp1, 6), er=v['er_f'],
                  tand=v['tand_f'], ms=v['ms'], dh=v['dh'], name='puck'),
           _shape('circle', [[cx, cy]], radius=rd, layer=layer, net='RF',
                  name='junction')]
    wt = ln = None
    if v['zt'] > 0:
        wt = line_width(v['zt'], sub)
        ln = C0 / (v['f0'] * 1e9 * math.sqrt(_eeff(wt, sub))) / 4e-3
        if rp + ln >= arm:
            raise ValueError(f"the arms ({arm:g} mm) are shorter than the "
                             f"puck and a quarter wave ({rp + ln:.2f} mm)")
    for n, ang in ((1, 180.0), (2, 60.0), (3, 300.0)):
        a = math.radians(ang)
        c, s = math.cos(a), math.sin(a)
        p = lambda r: [round(cx + r * c, 6), round(cy + r * s, 6)]
        out.append(_shape('path', [[cx, cy], p(arm)], width=w, layer=layer,
                          net='RF', name=f"arm{n}"))
        if wt:
            out.append(_shape('path', [p(rp), p(rp + ln)], width=wt,
                              layer=layer, net='RF', name=f"transformer{n}"))
        if v.get('ports'):
            out.append(_shape('port', [p(max(arm - 0.6, rp))], layer=layer,
                              rot=(ang + 180.0) % 360, name=f"P{n}"))
    if v.get('magnet'):
        z0, z1 = _span(sub, v['gap'], v['gap'] + v['mag_len'])
        out.append(_shape('circle', [[cx, cy]], radius=v['r_mag'],
                          material='magnet', z0=z0, z1=z1,
                          br=v['br'] * sub['up'], name='magnet'))
    return out


FACING = {'left': 180.0, 'right': 0.0, 'up': 90.0, 'down': 270.0}


def _sma(v, sub, layer, at):
    """An edge launch: drawn facing left (the body out to -x of `at`), then
    turned to `facing`."""
    x, y = at
    d, er, wall, L = v['channel'], v['er'], v['wall'], v['body']
    a = coax_pin(v['zc'], d, er)
    zc = sub['surface'] + sub['up'] * a / 2  # (the pin lies on the line)
    o = d / 2 + wall
    box = lambda x0, x1, hw, hz: dict(points=[[x0, y - hw], [x1, y + hw]],
                                      z0=round(zc - hz, 6),
                                      z1=round(zc + hz, 6))
    out = [_shape('rect', material='metal', name='sma_body',
                  **box(x - L, x, o, o)),
           _shape('rect', material='dielectric', er=er, tand=v['tand'],
                  name='sma_ptfe', **box(x - L, x, d / 2, d / 2)),
           _shape('rect', material='metal', name='sma_pin',
                  **box(x - L, x + v['overlap'], a / 2, a / 2))]
    if v['line'] > 0:
        w = v['width'] or line_width(50.0, sub)
        out.append(_shape('path', [[x, y], [x + v['line'], y]], width=w,
                          layer=layer, net='RF', name='sma_line'))
        if v.get('ports'):
            out.append(_shape('port', [[x + v['line'] - 0.5, y]],
                              layer=layer, rot=180.0, name='P1'))
    turn = FACING[v['facing']] - 180.0
    return [s.rotated(turn, (x, y)) for s in out] if turn else out


def _absorber(v, sub, layer, at):
    x, y = at
    z0, z1 = _span(sub, v['z'], v['z'] + v['t'])
    return [_shape('rect', [[x - v['w'] / 2, y - v['l'] / 2],
                            [x + v['w'] / 2, y + v['l'] / 2]],
                   material='dielectric', z0=z0, z1=z1, er=v['er'],
                   tand=v['tand'], name='absorber')]


def fence_posts(points, offset, pitch, both=True):
    """The posts' centres [(x, y)] along a centre line `points`, `offset` mm
    to its side (both: on each side), `pitch` mm apart at most, evenly from
    end to end."""
    from shapely.geometry import LineString
    line = LineString(points)
    out = []
    for side in ((1, -1) if both else (1,)):
        c = line.offset_curve(side * offset, join_style='round')
        if c.is_empty:
            continue
        for part in getattr(c, 'geoms', [c]):
            n = max(1, math.ceil(part.length / pitch))
            for k in range(n + 1):
                p = part.interpolate(part.length * k / n)
                out.append((round(p.x, 6), round(p.y, 6)))
    return out


def _fence(v, sub, layer, along):
    """Posts through the board along each selected trace."""
    traces = [s for s in along or [] if s.kind == 'path']
    if not traces:
        raise ValueError('select a trace (a path) to fence')
    out = []
    for s in traces:
        off = v['offset']
        if off < s.width / 2 + v['post'] / 2:
            raise ValueError(f"the posts ({v['post']:g} mm) would touch the "
                             f"trace {s.name or ''} ({s.width:g} mm wide): "
                             'move them out')
        base = s.name or 'fence'
        for k, (x, y) in enumerate(fence_posts(s.points, off, v['pitch'],
                                               v['both'])):
            out.append(_shape('circle', [[x, y]], radius=v['post'] / 2,
                              material='metal', z0=sub['zb'], z1=sub['zt'],
                              name=f"{base}_post_{k + 1}"))
    return out


P = Param
TEMPLATES = {t.key: t for t in (
    Template('can', 'Shield can',
             'a metal box over the board with air inside (its walls and lid '
             't thick), and a fence of metal posts through the board under '
             'its walls (ground vias)',
             (P('w', 'Width (x)', 10.0, 'mm', lo=0.1),
              P('l', 'Length (y)', 8.0, 'mm', lo=0.1),
              P('h', 'Height', 3.0, 'mm', 'over the copper', lo=0.05),
              P('t', 'Walls', 0.2, 'mm', 'the walls\' and the lid\'s '
                'thickness', lo=0.01),
              P('fence', 'Post fence', True, kind='bool',
                tip='metal posts through the board under the walls'),
              P('post', 'Post diameter', 0.3, 'mm', lo=0.01),
              P('pitch', 'Post pitch', 1.0, 'mm', 'at most this far apart',
                lo=0.05)),
             prompt='click the can\'s centre', make=_can),
    Template('dr', 'Dielectric resonator',
             'a dielectric puck (a DR) beside a microstrip line, the line '
             'ported at both ends',
             (P('length', 'Line length', 20.0, 'mm', lo=0.5),
              P('width', 'Line width', 0.0, 'mm', '50 ohm on this stack',
                lo=0.001),
              P('r', 'Puck radius', 3.0, 'mm', lo=0.05),
              P('height', 'Puck height', 2.5, 'mm', 'on the substrate',
                lo=0.01),
              P('er', 'Puck εr', 36.0, '', lo=1.0, hi=500.0),
              P('tand', 'Puck tan δ', 1e-4, '', lo=0.0, hi=1.0, decimals=5),
              P('gap', 'Gap', 1.0, 'mm', 'from the line\'s edge to the puck',
                lo=0.0),
              P('side', 'Side', '+y', kind='choice', choices=('+y', '-y')),
              P('ports', 'Ports at the ends', True, kind='bool')),
             prompt='click the line\'s centre', make=_dr),
    Template('circulator', 'Y-junction circulator',
             'a ferrite puck set into the substrate under a copper junction '
             'disc, three arms 120° apart with quarter-wave transformers, '
             'ports at their ends and a magnet over it (examples/'
             'rf_circulator\'s design, its lines worked out on this stack)',
             (P('f0', 'Centre', 9.6, 'GHz', 'the transformers\' quarter '
                'wave', lo=0.1, hi=200.0),
              P('width', 'Arms\' width', 0.0, 'mm', '50 ohm on this stack',
                lo=0.001),
              P('zt', 'Transformers', 30.0, 'Ω', '0: none', lo=0.0, hi=200.0,
                decimals=1),
              P('arm', 'Arm length', 12.0, 'mm', 'from the centre', lo=0.5),
              P('r_disc', 'Disc radius', 2.45, 'mm', lo=0.05),
              P('r_puck', 'Puck radius', 3.2, 'mm', lo=0.05),
              P('er_f', 'Ferrite εr', 15.0, '', lo=1.0, hi=100.0),
              P('tand_f', 'Ferrite tan δ', 2e-4, '', lo=0.0, hi=1.0,
                decimals=5),
              P('ms', '4πMs', 1200.0, 'G', lo=0.0, hi=20000.0, decimals=1),
              P('dh', 'ΔH', 30.0, 'Oe', lo=0.0, hi=5000.0, decimals=1),
              P('magnet', 'Magnet', True, kind='bool'),
              P('r_mag', 'Magnet radius', 4.0, 'mm', lo=0.05),
              P('gap', 'Magnet gap', 2.0, 'mm', 'over the copper', lo=0.0),
              P('mag_len', 'Magnet length', 3.0, 'mm', lo=0.01),
              P('br', 'Br', 1.2, 'T', lo=0.0, hi=5.0),
              P('ports', 'Ports at the arms', True, kind='bool')),
             prompt='click the junction\'s centre', make=_circulator),
    Template('sma', 'Edge-launch SMA',
             'an edge-launch connector as a square coax (the solids are '
             'prisms): a metal body round a PTFE channel, and a metal pin '
             'lying on a 50 ohm line; click the board\'s edge',
             (P('facing', 'Facing', 'left', kind='choice',
                choices=tuple(FACING)),
              P('zc', 'Coax', 50.0, 'Ω', 'the pin sized for it', lo=10.0,
                hi=150.0, decimals=1),
              P('channel', 'Channel', 4.1, 'mm', 'across (an SMA\'s PTFE: '
                '4.1)', lo=0.1),
              P('er', 'Channel εr', 2.1, '', 'PTFE 2.1', lo=1.0, hi=20.0),
              P('tand', 'Channel tan δ', 2e-4, '', lo=0.0, hi=1.0,
                decimals=5),
              P('wall', 'Body walls', 1.0, 'mm', lo=0.05),
              P('body', 'Body length', 6.0, 'mm', 'off the board', lo=0.1),
              P('overlap', 'Pin on the line', 1.5, 'mm', lo=0.0),
              P('line', 'Line length', 5.0, 'mm', 'from the edge in; 0: '
                'none', lo=0.0),
              P('width', 'Line width', 0.0, 'mm', '50 ohm on this stack',
                lo=0.001),
              P('ports', 'Port at the line\'s end', True, kind='bool')),
             prompt='click the board\'s edge where the pin meets it',
             make=_sma),
    Template('absorber', 'Absorber sheet',
             'a sheet of lossy dielectric (under a lid, say): its εr and '
             'tan δ stand for an absorber\'s loss (the solvers have no '
             'magnetic loss)',
             (P('w', 'Width (x)', 10.0, 'mm', lo=0.01),
              P('l', 'Length (y)', 8.0, 'mm', lo=0.01),
              P('t', 'Thickness', 0.5, 'mm', lo=0.001),
              P('z', 'Over the copper', 2.3, 'mm', 'its bottom', lo=-100.0),
              P('er', 'εr', 10.0, '', lo=1.0, hi=500.0),
              P('tand', 'tan δ', 0.5, '', lo=0.0, hi=10.0)),
             prompt='click the sheet\'s centre', make=_absorber),
    Template('fence', 'Via fence along traces',
             'metal posts through the board along each side of the selected '
             'traces (a fence of ground vias)',
             (P('offset', 'Offset', 0.0, 'mm', 'from the trace\'s centre '
                'line to the posts\'', lo=0.001),
              P('pitch', 'Pitch', 1.0, 'mm', 'at most this far apart',
                lo=0.05),
              P('post', 'Post diameter', 0.3, 'mm', lo=0.01),
              P('both', 'Both sides', True, kind='bool')),
             place='traces', make=_fence),
)}


def defaults(key, stack=None, layer='F.Cu', along=None):
    """A template's parameters' values, those that depend on the board (a
    line's 50 ohm width; a fence's offset from its trace) worked out."""
    from .shapes import stack_of
    t = TEMPLATES[key]
    v = {p.key: p.default for p in t.params}
    sub = substrate(stack or stack_of(None), layer)
    if 'width' in v:
        v['width'] = line_width(50.0, sub)
    if key == 'fence':
        ws = [s.width for s in along or [] if s.kind == 'path'] or [0.5]
        v['offset'] = round(max(ws) / 2 + 0.5 + v['post'] / 2, 3)
    return v


def make(key, values=None, stack=None, layer='F.Cu', at=(0.0, 0.0),
         along=None):
    """The shapes of template `key` with `values` (defaults' for those not
    given) on `layer` of `stack` (core.shapes.stack_of's; None: the default
    two-layer board), at point `at` (model mm) or along the traces `along`.
    ValueError: values that make no sense (a can smaller than its walls)."""
    from .shapes import stack_of
    t = TEMPLATES[key]
    v = defaults(key, stack, layer, along)
    v.update(values or {})
    sub = substrate(stack or stack_of(None), layer)
    if t.place == 'traces':
        return t.make(v, sub, layer, along)
    return t.make(v, sub, layer, (float(at[0]), float(at[1])))
