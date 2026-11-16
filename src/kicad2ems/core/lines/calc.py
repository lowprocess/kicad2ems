"""
The impedance of a stack-up, without a board: a line's width, its pair's gap, the copper and the dielectrics in;
Z0, the pair's Zdiff and eeff out, from the 2D field solver (xsection), and the width or gap that gives a target.

    line = Line(w=0.35, t=0.035, below=[(0.2, 4.3, 0.02)])                  # microstrip on 0.2 mm of FR-4
    r = calculate(line)                                                     # r['z0'], r['eeff'], r['delay_ps_mm']
    line = fit(line, 50.0)                                                  # the width of 50 ohm
    pair = fit(Line(w=0.15, gap=0.15, t=0.035, below=[(0.1, 4.1, 0.02)]), 100.0, what='zdiff')
    Line.on_board('board.kicad_pcb', 'In2.Cu', w=0.14, gap=0.15)            # that board's stack around In2.Cu
    e = element(line)                                                       # for lines.sections.solve: the field

    python -m kicad2ems.lines.calc --w 0.35 --h 0.2 --er 4.3 --t 0.035
    kicad2ems-z0 --board board.kicad_pcb --layer F.Cu --target 50 --plot z0.png

A Line is one strip, or a symmetric pair (gap), `t` thick, standing on the dielectrics `below` it (from the plane
under it up to the copper's lower face) with the dielectrics `above` over it (from the copper's upper face up; the
first of them also fills in beside the copper; none: air). Thicknesses are between copper faces, as a board's
stack-up gives them and as the line model's cuts take them. `top_plane` closes the stack with a plane (a
stripline), `ground_gap` puts coplanar ground beside the copper, `mask` a solder mask that follows it. The planes
are solid and wide; what the board's copper really does beside a trace is the line model's job (--lines-field).

Quasi-static, as the solver is: no dispersion and no conductor loss (the dielectric's is given). Nothing is snapped
to the line model's 1 % steps here (xsection.SIMILAR): a fitted width is to the tolerance asked for.
"""
import math
from dataclasses import dataclass, field, replace

from . import xsection as X
from ..constants import C0

WHAT = {'z0': 'Z0', 'zdiff': 'Zdiff', 'zcommon': 'Zcommon', 'zodd': 'Zodd', 'zeven': 'Zeven'}
PAIR_ONLY = ('zdiff', 'zcommon', 'zodd', 'zeven')
VARY = ('w', 'gap', 'ground_gap')
LIMITS = dict(w=(0.01, 50.0), gap=(0.01, 20.0), ground_gap=(0.01, 20.0))    # mm: what a fit searches within


def _layers(v):
    out = []
    for l in v or []:
        l = tuple(float(x) for x in l)
        if len(l) not in (2, 3) or l[0] <= 0 or l[1] < 1:
            raise ValueError(f"a dielectric is (thickness mm > 0, er >= 1, tan d): got {l}")
        out.append((l[0], l[1], l[2] if len(l) == 3 else 0.0))
    return out


@dataclass
class Line:
    """A strip (or a symmetric pair) in a stack of dielectrics over a plane. mm; the module docstring has the rest."""
    w: float
    t: float = 0.035
    below: list = field(default_factory=list)       # [(thickness, er, tan d)], the plane under it first
    above: list = field(default_factory=list)       # [(thickness, er, tan d)] from the copper's upper face up
    top_plane: bool = False
    gap: float = None                               # between the two strips of a pair
    ground_gap: float = None                        # coplanar ground this far from the copper's outer edges
    ground_width: float = None                      # its width (None: to the side of the box)
    mask: float = 0.0                               # a solder mask over the dielectric and the copper
    er_mask: float = 3.5
    tand_mask: float = 0.0

    def __post_init__(self):
        self.below, self.above = _layers(self.below), _layers(self.above)
        if not self.below:
            raise ValueError("a line needs a dielectric below it, over its plane: below=[(thickness, er, tan d)]")
        for name in ('w', 'gap', 'ground_gap', 'ground_width'):
            v = getattr(self, name)
            if v is not None and not v > 0:
                raise ValueError(f"{name} has to be positive: {v}")
        if self.t < 0 or self.mask < 0:
            raise ValueError("the copper's thickness and the mask's cannot be negative")
        if self.top_plane and not self.above:
            raise ValueError("a plane above the line needs a dielectric between them: above=[(thickness, er, tan d)]")
        if self.mask and self.above:
            raise ValueError("a solder mask is for the outer layer: not with dielectrics above the copper")

    @property
    def pair(self):
        return self.gap is not None

    @property
    def kind(self):
        """What it is called: 'microstrip', 'embedded microstrip', 'stripline', each maybe 'coplanar'."""
        base = 'stripline' if self.top_plane else ('embedded microstrip' if self.above else 'microstrip')
        return ('coplanar ' if self.ground_gap else '') + base + (' pair' if self.pair else '')

    def to_dict(self):
        return {k: (list(map(list, v)) if k in ('below', 'above') else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    @classmethod
    def on_board(cls, path, layer, w, **kw):
        """A line on copper layer `layer` of a board file's stack-up, between the copper layers next to it taken as
        planes (an outer layer: the one inside it, and its solder mask if the stack-up has one): the dielectrics
        and the copper's thickness from Board Setup > Physical Stackup. An inner layer's copper is taken to be
        pressed into the dielectric over it. kw: gap, ground_gap, t (to override) ..."""
        from ..importers import open_board
        items = open_board(path).stack_items                    # top to bottom, thicknesses in metres
        names = [it['name'] for it in items if it['kind'] == 'copper']
        if not names:
            raise ValueError(f"{path}: no stack-up in the board (Board Setup > Physical Stackup)")
        if layer not in names:
            raise ValueError(f"{path}: no copper layer {layer!r}: {', '.join(names)}")
        i = next(k for k, it in enumerate(items) if it['kind'] == 'copper' and it['name'] == layer)
        diel = lambda it: (it['t'] * 1e3, it.get('er', 4.5), it.get('tand', 0.0))

        def side(seq):
            """The dielectrics from the layer to the next copper, nearest first; whether copper ends them; the mask."""
            out, mask = [], None
            for it in seq:
                if it['kind'] == 'copper':
                    return out, True, mask
                if it['kind'] == 'mask':
                    mask = it
                else:
                    out.append(diel(it))
            return out, False, mask
        up, up_cu, up_mask = side(items[:i][::-1])
        down, down_cu, down_mask = side(items[i + 1:])
        if not up_cu and not down_cu:
            raise ValueError(f"{path}: {layer} is the only copper layer: nothing to take as its plane")
        if down_cu:                                             # the plane under it: the board as drawn
            below, above, closed, mask = down[::-1], up, up_cu, up_mask
        else:                                                   # the bottom layer: looked at from underneath
            below, above, closed, mask = up[::-1], down, down_cu, down_mask
        args = dict(t=items[i]['t'] * 1e3, below=below, above=above, top_plane=closed)
        if mask is not None and not above:
            args.update(mask=mask['t'] * 1e3, er_mask=mask.get('er', 3.3), tand_mask=mask.get('tand', 0.0))
        args.update(kw)
        return cls(w, **args)


def board_layers(path):
    """The copper layers of a board file's stack-up, top first: what Line.on_board takes; [] without a stack-up."""
    from ..importers import open_board
    return [it['name'] for it in open_board(path).stack_items if it['kind'] == 'copper']


def cross_section(line):
    """The xsection.CrossSection of a Line: the plane under it at z = 0, the copper centred on x = 0."""
    z, diel = 0.0, []
    for h, er, tand in line.below:
        diel.append(X.Dielectric(z, z + h, er, tand))
        z += h
    zc = z
    for k, (h, er, tand) in enumerate(line.above):              # the first one holds the copper: its thickness is
        h += line.t if k == 0 else 0.0                          # from the copper's upper face
        diel.append(X.Dielectric(z, z + h, er, tand))
        z += h
    xs = X.CrossSection(dielectrics=diel, planes=[0.0] + ([z] if line.top_plane else []))
    widths = [line.w, line.w] if line.pair else [line.w]
    edges = X._strips(xs, widths, [line.gap] if line.pair else [], zc, line.t)
    if line.ground_gap:
        X._ground_beside(xs, edges, line.ground_gap, zc, line.t, line.ground_width)
    if line.mask > 0:
        xs.dielectrics.append(X.Dielectric(zc, zc + line.mask, line.er_mask, line.tand_mask))
        for c in xs.conductors:                                 # the mask follows the copper: its top and its sides
            xs.dielectrics.append(X.Dielectric(c.z1, c.z1 + line.mask, line.er_mask, line.tand_mask,
                                               c.x0 - line.mask, c.x1 + line.mask))
    return xs


def _formula(line):
    """(name, Z0, eeff) of the closed form for this line alone, or None where there is none (a pair, a mixed
    stack, a mask): the check a calculator's user expects beside the field solver's number."""
    from . import formulas as F
    if line.pair or line.mask or len(line.below) != 1:
        return None
    h, er, _ = line.below[0]
    if not line.above:
        if line.ground_gap:
            return ('grounded CPW (conformal mapping, thin copper)',) + tuple(F.cpwg(line.w, line.ground_gap, h, er))
        return ('Hammerstad-Jensen',) + tuple(F.microstrip(line.w, h, er, line.t))
    if line.ground_gap or len(line.above) != 1:
        return None
    ha, era, _ = line.above[0]
    if line.top_plane and abs(era - er) < 1e-9:
        return ('offset stripline (Wheeler)',) + tuple(F.offset_stripline(line.w, h, ha, er, line.t))
    if not line.top_plane:
        return ('embedded microstrip (Bahl)',) + tuple(F.embedded_microstrip(line.w, h, er, line.t, ha, era))
    return None


def calculate(line, f=None):
    """What the field solver gives for a Line: dict(kind, z0, eeff, delay_ps_mm, c_pf_m, l_nh_m, tand_eff, nodes;
    a pair also zdiff, zcommon, zodd, zeven, eeff_odd, eeff_even, coupling (the voltage coupling of matched lines,
    (Ze - Zo) / (Ze + Zo)), with z0 one strip's, the other grounded; `formula`: (name, Z0, eeff) of the closed
    form where one applies). f (Hz): also loss_db_m, the dielectric's loss at f (the conductor's is not solved)."""
    r = cross_section(line).solve()
    z0, eeff = r.single(0)
    out = dict(kind=line.kind, z0=z0, eeff=eeff, delay_ps_mm=math.sqrt(eeff) / C0 * 1e9,
               c_pf_m=r.C[0, 0] * 1e12, l_nh_m=r.L[0, 0] * 1e9, tand_eff=r.tand_eff(0), nodes=r.nodes)
    if line.pair:
        ze, zo, ee, eo = r.pair()
        out.update(zdiff=2 * zo, zcommon=ze / 2, zodd=zo, zeven=ze, eeff_odd=eo, eeff_even=ee,
                   coupling=(ze - zo) / (ze + zo), delay_ps_mm=math.sqrt(eo) / C0 * 1e9,
                   tand_eff=float((r.G_w[0, 0] - r.G_w[0, 1]) / (r.C[0, 0] - r.C[0, 1])))
    if f:
        ee = out.get('eeff_odd', eeff)                          # a pair: of its differential mode
        out['loss_db_m'] = 8.685889638 * math.pi * f * math.sqrt(ee) / C0 * out['tand_eff']
        out['f'] = f
    formula = _formula(line)
    if formula:
        out['formula'] = formula
    return out


def fit(line, target, what=None, vary='w', tol=1e-3, limits=None):
    """The Line whose `what` ('z0'; a pair's 'zdiff' by default, 'zcommon', 'zodd', 'zeven') is `target` ohm, by
    changing `vary` ('w', 'gap' or 'ground_gap'): a root search around the solver, to `tol` of the target.
    ValueError when no value within the limits (LIMITS, mm) gives it, with what they do give."""
    from scipy.optimize import brentq
    what = what or ('zdiff' if line.pair else 'z0')
    if what not in WHAT or (what in PAIR_ONLY and not line.pair):
        raise ValueError(f"cannot fit {what!r}: " + ("that is a pair's (give a gap)" if what in PAIR_ONLY
                                                     else f"one of {', '.join(WHAT)}"))
    if vary not in VARY or getattr(line, vary) is None:
        raise ValueError(f"cannot vary {vary!r}: " + (f"the line has no {vary}" if vary in VARY
                                                      else f"one of {', '.join(VARY)}"))
    if not target > 0:
        raise ValueError(f"the target has to be positive: {target}")
    lo, hi = limits or LIMITS[vary]
    seen = {}

    def miss(u):                                                # in log(size): Z is near a power law of it
        v = math.exp(u)
        if v not in seen:
            seen[v] = calculate(replace(line, **{vary: v}))[what]
        return seen[v] / target - 1

    a, b = math.log(lo), math.log(hi)
    u = min(max(math.log(getattr(line, vary)), a), b)           # outward from the present value: a few solves
    fu = miss(u)
    if abs(fu) <= tol:
        return replace(line, **{vary: math.exp(u)})
    step, left, right = 0.7, u, u
    f_left = f_right = fu
    while True:
        moved = False
        if left > a:
            left = max(a, left - step)
            f_left, moved = miss(left), True
            if f_left * fu <= 0:
                bracket = (left, min(left + step, u))
                break
        if right < b:
            right = min(b, right + step)
            f_right, moved = miss(right), True
            if f_right * fu <= 0:
                bracket = (max(right - step, u), right)
                break
        if not moved:
            got = sorted(seen.values())
            raise ValueError(f"no {vary} from {lo:g} to {hi:g} mm gives {WHAT[what]} {target:g} Ω: it runs from "
                             f"{got[0]:.1f} to {got[-1]:.1f} Ω there")
        step *= 1.5
    u = brentq(miss, *bracket, xtol=tol / 4, rtol=1e-12)        # d ln Z / d ln size is below 1: tol / 4 in the size
    return replace(line, **{vary: math.exp(u)})


def element(line, r=None):
    """The Line as a drawing element of the line model (lines.sections.solve(e), the GUI's Section view): its
    cross-section to solve and draw, a pair with its odd and even modes."""
    xs = cross_section(line).to_dict()
    e = dict(id='calc', kind=line.kind, label=describe(line),
             section={'coupled': dict(section=xs, index=0)} if line.pair else {'cut': xs})
    if r:
        e.update(z0=r['z0'], eeff=r['eeff'], source='field')
        if r.get('formula'):
            e['z0_formula'] = r['formula'][1]
    return e


def describe(line):
    """One line about a Line: 'microstrip pair: w 0.15, gap 0.15, t 0.035 mm on 0.1 mm er 4.1'."""
    size = [f"w {line.w:.4g}"] + ([f"gap {line.gap:.4g}"] if line.pair else []) + \
           ([f"ground {line.ground_gap:.4g} away"] if line.ground_gap else []) + [f"t {line.t:.4g} mm"]
    stack = lambda ls: ' + '.join(f"{h:.4g} mm εr {er:.3g}" for h, er, _ in ls)
    text = f"{line.kind}: {', '.join(size)} on {stack(line.below)}"
    if line.above:
        text += f", under {stack(line.above)}" + (' and a plane' if line.top_plane else '')
    if line.mask:
        text += f", mask {line.mask:.3g} mm εr {line.er_mask:.3g}"
    return text


def report(line, r):
    """The lines a command prints for calculate()'s result."""
    out = [describe(line)]
    if line.pair:
        out += [f"  Zdiff    {r['zdiff']:8.2f} Ω    (odd mode {r['zodd']:.2f} Ω, εeff {r['eeff_odd']:.3f})",
                f"  Zcommon  {r['zcommon']:8.2f} Ω    (even mode {r['zeven']:.2f} Ω, εeff {r['eeff_even']:.3f})",
                f"  Z0       {r['z0']:8.2f} Ω    one strip, the other grounded (εeff {r['eeff']:.3f})",
                f"  coupling {100 * r['coupling']:8.2f} %    (Ze - Zo) / (Ze + Zo)"]
    else:
        out += [f"  Z0       {r['z0']:8.2f} Ω", f"  εeff     {r['eeff']:8.3f}"]
    out += [f"  delay    {r['delay_ps_mm']:8.3f} ps/mm" + ('  (differential)' if line.pair else ''),
            f"  C        {r['c_pf_m']:8.2f} pF/m    L {r['l_nh_m']:.1f} nH/m" + ('  (one strip)' if line.pair else '')]
    if r['tand_eff']:
        out.append(f"  tan δ    {r['tand_eff']:8.4f}       effective")
    if 'loss_db_m' in r:
        out.append(f"  loss     {r['loss_db_m']:8.3f} dB/m    dielectric, at {r['f'] / 1e9:g} GHz (conductor loss not solved)")
    if 'formula' in r:
        name, z, ee = r['formula']
        out.append(f"  formula  {z:8.2f} Ω    {name}, εeff {ee:.3f} ({(r['z0'] - z) / z * 100:+.1f} % from it)")
    out.append(f"  ({r['nodes']:,} nodes)")
    return out


def plot(line, path, r=None, mode=None):
    """The field across the Line as a picture: |E| in colour, equipotentials, the copper and the dielectrics'
    faces. mode: 'odd' or 'even' for a pair (odd by default)."""
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    from matplotlib.patches import Rectangle
    from . import sections
    e = element(line, r)
    v = sections.solve(e, (mode or 'odd') if line.pair else None)
    x0, x1, z0, z1 = v.window
    nx = 900
    nz = max(120, min(900, int(nx * (z1 - z0) / (x1 - x0))))
    x, z, phi, ex, ez, er = v.field.sample(x0, x1, z0, z1, nx, nz)
    mag = np.hypot(ex, ez)
    top = np.nanpercentile(mag, 99.5)
    fig, ax = plt.subplots(figsize=(9, max(2.6, 9 * (z1 - z0) / (x1 - x0) + 1.3)))
    im = ax.imshow(np.clip(mag, top / 300, top).T, origin='lower', extent=(x0, x1, z0, z1), cmap='magma',
                   norm=LogNorm(top / 300, top), aspect='equal', interpolation='bilinear')
    levels = np.linspace(-1, 1, 21) if min(v.volts) < 0 else np.linspace(0, 1, 11)
    ax.contour(x, z, phi.T, levels=levels[1:-1], colors='w', linewidths=0.5, alpha=0.6)
    for d in v.xs.dielectrics:                                   # the faces between dielectrics
        if math.isinf(d.x0):
            ax.axhline(d.z1, color='w', lw=0.4, alpha=0.35)
    for c in v.xs.conductors:
        a, b = max(c.x0, x0), min(c.x1, x1)
        t = max(c.z1 - c.z0, (z1 - z0) / 300)
        ax.add_patch(Rectangle((a, c.z0), b - a, t, color='0.55' if c.ground else '#d68c40', lw=0))
    for p in v.xs.planes:
        ax.axhline(p, color='0.55', lw=2.5)
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_xlabel('mm')
    ax.set_ylabel('mm')
    head = ' · '.join(f"{k} {val:.2f}" + (' Ω' if k.startswith('Z') else '') for k, val in v.values.items() if k != 'nodes')
    ax.set_title(f"{describe(line)}\n{head}", fontsize=9)
    fig.colorbar(im, ax=ax, label='|E| (V/mm at 1 V)', shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# ---- the command --------------------------------------------------------------------------------------
def _layer_list(text, what):
    """'0.2:4.3:0.02, 0.1:3.8' as [(0.2, 4.3, 0.02), (0.1, 3.8, 0)]."""
    out = []
    for part in str(text).split(','):
        if part.strip():
            try:
                out.append(tuple(float(x) for x in part.split(':')))
            except ValueError:
                raise ValueError(f"{what}: 'thickness:er[:tand], ...' in mm, not {part.strip()!r}") from None
    return out


def parser():
    import argparse
    ap = argparse.ArgumentParser(
        prog='kicad2ems-z0', formatter_class=argparse.RawDescriptionHelpFormatter,
        description="The impedance of a line in a stack-up, by kicad2ems's 2D field solver; with --target, the "
                    "width (or gap) that gives it.",
        epilog="""examples:
  kicad2ems-z0 --w 0.35 --h 0.2 --er 4.3                     a microstrip (35 um copper)
  kicad2ems-z0 --w 0.35 --h 0.2 --er 4.3 --mask 0.02         under solder mask
  kicad2ems-z0 --w 0.15 --gap 0.15 --h 0.1 --er 4.1          a pair: Zdiff
  kicad2ems-z0 --w 0.2 --h 0.2 --er 4.3 --above 0.2          a stripline: a plane 0.2 mm over the copper
  kicad2ems-z0 --w 0.3 --ground-gap 0.2 --h 0.2 --er 4.3     grounded coplanar
  kicad2ems-z0 --h 0.2 --er 4.3 --target 50                  the width of 50 ohm
  kicad2ems-z0 --w 0.15 --gap 0.15 --h 0.1 --er 4.1 --target 100 --vary gap
  kicad2ems-z0 --board b.kicad_pcb --layer In2.Cu --w 0.14   the board's own stack around In2.Cu
  kicad2ems-z0 --w 0.35 --below 0.1:4.1:0.02,0.1:3.6 ...     several dielectrics, the plane's first""")
    g = ap.add_argument_group('the line (mm)')
    g.add_argument('--w', type=float, help='width (with --target and no width: a first guess is made)')
    g.add_argument('--gap', type=float, help="a pair: the gap between its two strips")
    g.add_argument('--t', type=float, help='copper thickness (default 0.035, or the board layer\'s)')
    g.add_argument('--ground-gap', type=float, help='coplanar ground this far from the copper')
    g.add_argument('--ground-width', type=float, help="the coplanar ground's width (default: wide)")
    g = ap.add_argument_group('the stack (mm)')
    g.add_argument('--h', type=float, help='the dielectric between the plane below and the copper')
    g.add_argument('--er', type=float, default=4.3, help='its permittivity (default %(default)s)')
    g.add_argument('--tand', type=float, default=0.02, help='its loss tangent (default %(default)s)')
    g.add_argument('--above', type=float, help="a stripline: the dielectric between the copper's upper face and "
                                               "the plane above")
    g.add_argument('--er-above', type=float, help='its permittivity (default: --er)')
    g.add_argument('--open', action='store_true', help='with --above: no plane over it (an embedded microstrip)')
    g.add_argument('--below', help="the dielectrics under the copper instead of --h: 'thickness:er[:tand], ...', "
                                   "the plane's first")
    g.add_argument('--over', help="the dielectrics over the copper instead of --above, the copper's first (a "
                                  "plane closes them unless --open)")
    g.add_argument('--mask', type=float, default=0.0, help='solder mask thickness (outer layer)')
    g.add_argument('--er-mask', type=float, default=3.5, help='its permittivity (default %(default)s)')
    g.add_argument('--board', help='take the stack from this board file (.kicad_pcb): with --layer')
    g.add_argument('--layer', help="the board's copper layer the line is on (F.Cu, In2.Cu ...)")
    g = ap.add_argument_group('what to find')
    g.add_argument('--target', type=float, help='ohm: change --vary until Z0 (a pair: Zdiff) is this')
    g.add_argument('--what', choices=list(WHAT), help='what --target is of (default: z0, a pair: zdiff)')
    g.add_argument('--vary', choices=VARY, default='w', help='what --target changes (default %(default)s)')
    g.add_argument('--tol', type=float, default=1e-3, help="the fit's tolerance (default %(default)s: 0.1 %%)")
    g.add_argument('--f', type=float, help='GHz: also the dielectric loss there')
    g = ap.add_argument_group('output')
    g.add_argument('--plot', metavar='PNG', help='the field across the line, as a picture')
    g.add_argument('--mode', choices=('odd', 'even'), help="a pair's picture: its odd (default) or even mode")
    g.add_argument('--json', action='store_true', help='the line and its numbers as JSON')
    return ap


def line_of(a):
    """The Line of parsed arguments (parser())."""
    w = a.w
    if w is None:
        if a.target is None or a.vary != 'w':
            raise ValueError("give the width (--w), or a --target to find it")
        w = 0.2                                                 # where the search starts
    kw = {k: v for k, v in (('gap', a.gap), ('ground_gap', a.ground_gap), ('ground_width', a.ground_width)) if v}
    if a.t is not None:
        kw['t'] = a.t
    if a.board:
        if not a.layer:
            raise ValueError("--board needs --layer: the copper layer the line is on")
        if a.mask:
            kw.update(mask=a.mask, er_mask=a.er_mask)
        return Line.on_board(a.board, a.layer, w, **kw)
    if a.layer:
        raise ValueError("--layer is a board's: give --board too")
    below = _layer_list(a.below, '--below') if a.below else ([(a.h, a.er, a.tand)] if a.h else None)
    if not below:
        raise ValueError("give the stack: --h (and --er), or --below, or --board and --layer")
    above = _layer_list(a.over, '--over') if a.over else \
        ([(a.above, a.er if a.er_above is None else a.er_above, a.tand)] if a.above else [])
    return Line(w, below=below, above=above, top_plane=bool(above) and not a.open, mask=a.mask,
                er_mask=a.er_mask, **kw)


def main(argv=None):
    import json
    import sys
    a = parser().parse_args(argv)
    try:
        line = line_of(a)
        if a.target is not None:
            line = fit(line, a.target, a.what, a.vary, a.tol)
        r = calculate(line, a.f * 1e9 if a.f else None)
        if a.plot:
            plot(line, a.plot, r, a.mode)
    except (ValueError, FileNotFoundError) as e:
        print(f"kicad2ems-z0: error: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(dict(line=line.to_dict(), result=r), indent=1))
    else:
        if a.target is not None:
            print(f"{a.vary} = {getattr(line, a.vary):.4f} mm for {WHAT[a.what or ('zdiff' if line.pair else 'z0')]} "
                  f"{a.target:g} Ω")
        print("\n".join(report(line, r)))
        if a.plot:
            print(f"wrote {a.plot}")
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
