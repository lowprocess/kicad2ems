#!/usr/bin/env python3
"""
Figures of the line model's 2D field solver (kicad2ems.lines.xsection) at work:

    python tools/xsection_figures.py [OUT] [--board CM5_MINIMA_3.kicad_pcb]

  xsection_anatomy.png      a coupled pair (the CM5 Minima's HDMI pair): the cross-section and its graded grid, the
                            odd- and even-mode potentials and their field lines
  xsection_cases.png        what the formulas leave out: a plane ending beside or under a microstrip, grounded CPW
                            with and without solder mask, coplanar ground beside a stripline
  xsection_convergence.png  why each cross-section is solved twice: the grid's first-order error, and the
                            extrapolation from the grid and the grid halved
  xsection_cache.png        which cross-sections share a solve: Z0 against the distance to coplanar ground, and the
                            cache's bins, wider where the distance matters less
  xsection_coupling.png     a five-line coupled section: the coupling of the first line to the others, the
                            formulas' nearest neighbours against the field solver's matrix
  xsection_board_cut.png    with --board: a cut across a trace of a real board, where its reference plane ends,
                            and the cross-section solved there

OUT: the folder to write to (default build/xsection_figures).
"""
import argparse, math, os, sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'src'))
import matplotlib                                                       # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt                                         # noqa: E402
from matplotlib.colors import LinearSegmentedColormap                   # noqa: E402
from matplotlib.patches import Rectangle                                # noqa: E402
from kicad2ems.lines import xsection as X                               # noqa: E402
from kicad2ems.lines import formulas as F                               # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#8d8c87', '#e4e3de'
BLUE, ORANGE, AQUA = '#2a78d6', '#eb6834', '#1baf7a'
COPPER, DIEL, MASK = '#3a3936', '#efe9da', '#cfe3d4'
# diverging blue <-> red through a gray midpoint (potential of a pair's odd mode); sequential blue (0..1 V)
DIVERGE = LinearSegmentedColormap.from_list('div', ['#104281', '#3987e5', '#cde2fb', '#f0efec', '#f7cfcf',
                                                    '#e34948', '#8f1d1d'])
SEQ = LinearSegmentedColormap.from_list('seq', ['#f0efec', '#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'])

plt.rcParams.update({'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
                     'text.color': INK, 'axes.labelcolor': INK2, 'xtick.color': INK2, 'ytick.color': INK2,
                     'axes.edgecolor': MUTED, 'axes.titlesize': 10, 'axes.titleweight': 'bold',
                     'axes.titlelocation': 'left', 'font.size': 9, 'axes.spines.top': False,
                     'axes.spines.right': False, 'legend.frameon': False})


def draw_section(ax, xs, window):
    """The dielectrics, planes and conductors of a cross-section, inside window (x0, x1, z0, z1)."""
    x0, x1, z0, z1 = window
    for d in xs.dielectrics:
        a, b = max(d.x0, x0 - 1), min(d.x1, x1 + 1)
        ax.add_patch(Rectangle((a, d.z0), b - a, d.z1 - d.z0, color=MASK if d.er < 4 else DIEL, lw=0, zorder=0))
    for p in xs.planes:
        ax.plot([x0 - 1, x1 + 1], [p, p], color=COPPER, lw=2.2, solid_capstyle='butt', zorder=4)
    for c in xs.conductors:
        a, b = max(c.x0, x0 - 1), min(c.x1, x1 + 1)
        if c.z1 - c.z0 < 1e-9:
            ax.plot([a, b], [c.z0, c.z0], color=COPPER, lw=2.2, solid_capstyle='butt', zorder=4)
        else:
            ax.add_patch(Rectangle((a, c.z0), b - a, c.z1 - c.z0, color=COPPER, lw=0, zorder=4))
    ax.set_xlim(x0, x1)
    ax.set_ylim(z0, z1)
    ax.set_aspect('equal')
    ax.set_xlabel('mm across the line')
    ax.set_ylabel('mm')


def field_panel(ax, xs, window, volts=None, cmap=SEQ, vmin=0.0, vmax=1.0, lines=True):
    """The potential (colour, with equipotentials) and field lines of a cross-section, drawn over it."""
    x, z, phi, _ = X.potential(xs, volts)
    x0, x1, z0, z1 = window
    xu = np.linspace(x0, x1, 400)
    zu = np.linspace(z0, z1, max(60, int(400 * (z1 - z0) / (x1 - x0))))
    from scipy.interpolate import RegularGridInterpolator
    P = RegularGridInterpolator((x, z), phi, bounds_error=False, fill_value=0.0)(np.stack(np.meshgrid(xu, zu, indexing='ij'), -1))
    draw_section(ax, xs, window)
    im = ax.pcolormesh(xu, zu, P.T, cmap=cmap, vmin=vmin, vmax=vmax, shading='gouraud', zorder=1, alpha=0.92)
    ax.contour(xu, zu, P.T, levels=np.linspace(vmin, vmax, 21)[1:-1], colors='white', linewidths=0.5, alpha=0.8,
               zorder=2)
    if lines:
        Ez, Ex = np.gradient(-P.T, zu, xu)
        mag = np.hypot(Ex, Ez)
        weak = mag < 0.02 * np.percentile(mag, 99)                       # no lines where there is next to no field
        ax.streamplot(xu, zu, np.ma.masked_where(weak, Ex), np.ma.masked_where(weak, Ez), color=INK,
                      linewidth=0.4, density=1.0, arrowstyle='-', zorder=3)
    for d in xs.dielectrics:                                             # the layers' outlines over the field
        ax.plot([x0, x1], [d.z1, d.z1], color=MUTED, lw=0.5, zorder=3)
    return im


# ---- the figures ------------------------------------------------------------------------------------------------
HDMI = dict(w=0.13, s=0.25, h=0.0994, er=4.45, t=0.035, mask=0.01, er_mask=3.8)


def anatomy(out):
    p = HDMI
    xs = X.coupled_microstrip(p['w'], p['s'], p['h'], p['er'], p['t'], mask=p['mask'], er_mask=p['er_mask'])
    r = xs.solve()
    ze, zo, ee, eo = r.pair()
    zf = F.microstrip(p['w'], p['h'], p['er'])[0]
    from kicad2ems.lines.coupled import coupled_microstrip
    kj = coupled_microstrip(p['w'], p['s'], p['h'], p['er'])
    window = (-0.55, 0.55, -0.02, 0.32)
    fig, axs = plt.subplots(3, 1, figsize=(8.2, 9.6))
    ax = axs[0]
    draw_section(ax, xs, window)
    x, z = X._grid(xs)
    for v in x[(x > window[0]) & (x < window[1])]:
        ax.plot([v, v], [window[2], window[3]], color=BLUE, lw=0.3, alpha=0.55, zorder=5)
    for v in z[(z > window[2]) & (z < window[3])]:
        ax.plot([window[0], window[1]], [v, v], color=BLUE, lw=0.3, alpha=0.55, zorder=5)
    ax.set_title(f"The cross-section and its grid: {len(x)} x {len(z)} lines, graded towards every edge "
                 f"(solved again with every cell halved)", fontsize=9.5)
    ax.text(-0.5, 0.045, 'FR-4 prepreg, 0.099 mm, er 4.45', color=INK2, fontsize=8, zorder=6,
            bbox=dict(fc=DIEL, ec='none', pad=1))
    ax.text(-0.5, 0.012, 'In4.Cu: the reference plane', color=INK2, fontsize=8, zorder=6,
            bbox=dict(fc=DIEL, ec='none', pad=1))
    ax.text(0.28, 0.16, '10 um solder mask over it', color=INK2, fontsize=8, zorder=6,
            bbox=dict(fc=SURFACE, ec='none', pad=1))
    im = field_panel(axs[1], xs, window, [1, -1], DIVERGE, -1, 1)
    axs[1].set_title(f"Odd mode (+1 V / -1 V): Zo {zo:.1f} ohm, Zdiff {2 * zo:.1f} ohm, eeff {eo:.2f}   "
                     f"(zero-thickness formula, no mask: Zdiff {2 * kj[1]:.1f} ohm)", fontsize=9.5)
    fig.colorbar(im, ax=axs[1], shrink=0.75, label='V')
    im = field_panel(axs[2], xs, window, [1, 1], SEQ, 0, 1)
    axs[2].set_title(f"Even mode (+1 V / +1 V): Ze {ze:.1f} ohm, common mode {ze / 2:.1f} ohm, eeff {ee:.2f}",
                     fontsize=9.5)
    fig.colorbar(im, ax=axs[2], shrink=0.75, label='V')
    fig.suptitle("A coupled pair, solved: the CM5 Minima's HDMI pair (0.13 mm lines 0.25 mm apart, 35 um copper)",
                 x=0.02, ha='left', fontsize=11, fontweight='bold')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_anatomy.png'), dpi=150)
    plt.close(fig)


def cases(out):
    from kicad2ems.lines.reference import _cut_section
    h, w, er = 0.2, 0.35, 4.3
    diel = ((0.0, h, er, 0.0),)
    plane = lambda edge: ((0.0, edge is None, ((-math.inf, math.inf),) if edge is None else ((-math.inf, edge),)),)
    zf = F.microstrip(w, h, er)[0]
    rows = [
        ('Microstrip over a whole plane', _cut_section(w, h, 0.0, True, (), plane(None), diel), f"formula {zf:.1f}"),
        ('The plane ends 0.125 mm past its edge', _cut_section(w, h, 0.0, True, (), plane(0.3), diel),
         f"formula {zf:.1f}"),
        ('The plane ends under its centre', _cut_section(w, h, 0.0, True, (), plane(0.0), diel), f"formula {zf:.1f}"),
        ('Grounded CPW, gaps 0.15 mm', X.microstrip(w, h, er, s=0.15), f"formula {F.cpwg(w, 0.15, h, er)[0]:.1f}"),
        ('The same under 20 um of mask', X.microstrip(w, h, er, s=0.15, mask=0.02, er_mask=3.8),
         'no formula (classify: embedded microstrip, '
         f"{F.embedded_microstrip(w, h, er, 0.0, 0.02, 3.8)[0]:.1f})"),
        ('Stripline, coplanar ground 0.15 mm beside it', X.stripline(0.15, 0.2, 0.2, er, s=0.15),
         f"formula (ground left out) {F.stripline(0.15, 0.4, er)[0]:.1f}"),
    ]
    fig, axs = plt.subplots(3, 2, figsize=(10, 8.4))
    for ax, (title, xs, ref) in zip(axs.flat, rows):
        z0 = xs.solve().single()[0]
        top = max(c.z1 for c in xs.conductors if math.isfinite(c.x0) or True)
        zt = (max(xs.planes) + 0.03) if len([p for p in xs.planes if p > 0.05]) else 0.55
        field_panel(ax, xs, (-0.75, 0.75, -0.03, zt), None, SEQ, 0, 1)
        ax.set_title(f"{title}\nZ0 {z0:.1f} ohm   ({ref})", fontsize=9)
    fig.suptitle('What the formulas leave out: the potential of a 1 V strip and its field lines (0.2 mm FR-4)',
                 x=0.02, ha='left', fontsize=11, fontweight='bold')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_cases.png'), dpi=150)
    plt.close(fig)


def convergence(out):
    k = 1 / math.cosh(math.pi * 0.15 / 0.8)
    exact = 30 * math.pi / math.sqrt(4.3) * F.ellipk(k) / F.ellipk(math.sqrt(1 - k * k))
    xs = X.stripline(0.15, 0.2, 0.2, 4.3)
    x, z = X._grid(xs)
    single, extra = [], []
    prev = None
    for level in range(4):
        C, Ca, _, n = X._solve_grid(xs, x, z)
        zc = 1 / (X.C0 * math.sqrt(C[0, 0] * Ca[0, 0]))
        single.append((n, abs(zc / exact - 1) * 100))
        if prev is not None:
            c2, ca2 = 2 * C[0, 0] - prev[0], 2 * Ca[0, 0] - prev[1]
            extra.append((n + prev[2], abs(1 / (X.C0 * math.sqrt(c2 * ca2)) / exact - 1) * 100))
        prev = (C[0, 0], Ca[0, 0], n)
        x, z = X._bisect(x), X._bisect(z)
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    n1, e1 = zip(*single)
    n2, e2 = zip(*extra)
    ax.loglog(n1, e1, 'o-', color=BLUE, lw=2, ms=8, label='one grid')
    ax.loglog(n2, e2, 's-', color=ORANGE, lw=2, ms=8, label='extrapolated: 2 x (grid halved) - grid')
    slope = lambda n: e1[0] * math.sqrt(n1[0] / n)
    ax.loglog([n1[0], n1[-1]], [slope(n1[0]) * 1.35, slope(n1[-1]) * 1.35], color=MUTED, lw=1, ls=':')
    ax.text(n1[-1], slope(n1[-1]) * 1.6, 'error ~ cell size ~ 1 / sqrt(nodes)', color=INK2, fontsize=8,
            ha='right', va='bottom')
    ax.annotate('the default: a grid and its halving', (n2[0], e2[0]), (n2[0] * 0.45, e2[0] * 0.25),
                arrowprops=dict(arrowstyle='-', color=MUTED, lw=0.8), color=INK2, fontsize=8, ha='left')
    ax.set_xlabel('grid nodes solved')
    ax.set_ylabel('|Z0 - exact| / exact, %')
    ax.set_title(f"Stripline 0.15 mm between planes 0.4 mm apart against Cohn's exact {exact:.2f} ohm")
    ax.grid(True, which='major', color=GRID, lw=0.6)
    ax.legend(loc='lower left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_convergence.png'), dpi=150)
    plt.close(fig)


def cache(out):
    from kicad2ems.lines.reference import _distance_bins, FIELD_REACH
    h, w, er = 0.0994, 0.13, 4.45
    z_of = lambda x: X.microstrip(w, h, er, s=x * h).solve().single()[0]
    z_none = X.microstrip(w, h, er).solve().single()[0]
    edges = _distance_bins(X.SIMILAR)
    edges = edges[(edges >= 0.2) & (edges <= FIELD_REACH)]
    ze = np.array([z_of(x) for x in edges])
    xd = np.linspace(0.2, FIELD_REACH, 60)
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(7.8, 6.4), sharex=True, gridspec_kw=dict(height_ratios=[2, 1.2]))
    for e in edges:
        ax.axvline(e, color=GRID, lw=0.7, zorder=0)
    ax.plot(xd, [z_of(x) for x in xd], color=BLUE, lw=2)
    ax.axhline(z_none, color=MUTED, lw=1, ls='--')
    ax.text(FIELD_REACH, z_none - 0.5, f"no ground beside it: {z_none:.1f} ohm", ha='right', va='top', color=INK2,
            fontsize=8)
    ax.set_ylabel('Z0, ohm')
    ax.set_title("Z0 of a 0.13 mm microstrip on 0.099 mm FR-4 against the distance to ground beside it;\n"
                 "grey: the cache's bins, narrow where the distance matters", fontsize=9.5)
    err = np.abs(np.diff(ze)) / 2 / ze[:-1] * 100                          # the most snapping to a middle moves Z0
    bx.bar(edges[:-1], err, width=np.diff(edges) * 0.85, align='edge', color=AQUA)
    bx.axhline(X.SIMILAR * 100 / 3, color=MUTED, lw=1, ls=':')
    bx.text(FIELD_REACH, X.SIMILAR * 100 / 3 * 1.06, f"SIMILAR / 3 = {X.SIMILAR * 100 / 3:.2f} %", ha='right',
            va='bottom', color=INK2, fontsize=8)
    bx.set_ylabel('most Z0 moves\nwithin a bin, %')
    bx.set_xlabel('distance from the strip edge to the ground, in reference heights')
    bx.set_title(f"Copper in one bin is moved to its middle: one solve for all of it (SIMILAR = {X.SIMILAR:g})",
                 fontsize=9.5)
    bx.grid(True, axis='y', color=GRID, lw=0.6)
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_cache.png'), dpi=150)
    plt.close(fig)


def coupling(out):
    from kicad2ems.lines.coupled import group_lc
    widths, gaps, h, er = [0.6] * 5, [0.58, 0.74, 0.74, 0.58], 0.8, 4.4
    C, _ = group_lc(widths, gaps, h, er)
    r = X.microstrip(widths, h, er, gap=gaps).solve()
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    j = np.arange(1, 5)
    ax.bar(j - 0.19, -C[0, 1:] / C[0, 0] * 100, width=0.36, color=MUTED, label='formulas (group_lc): nearest '
           'neighbours only')
    ax.bar(j + 0.19, -r.C[0, 1:] / r.C[0, 0] * 100, width=0.36, color=BLUE, label='field solver')
    for k in range(4):
        v = -r.C[0, k + 1] / r.C[0, 0] * 100
        ax.text(j[k] + 0.19, v + 0.4, f"{v:.1f}", ha='center', color=INK2, fontsize=8)
    ax.set_xticks(j, [f"line {k + 2}" for k in range(4)])
    ax.set_ylabel('-C(1, j) / C(1, 1), %')
    ax.set_title("rf_interdig's five fingers: how much of line 1's charge the other lines take", fontsize=9.5)
    ax.text(4.4, 6, "small, but they set the filter's stop bands:\n|S21| against openEMS 51 -> 8.5 dB rms",
            ha='right', color=INK2, fontsize=8)
    ax.legend(loc='upper right')
    ax.grid(True, axis='y', color=GRID, lw=0.6)
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_coupling.png'), dpi=150)
    plt.close(fig)


def board_cut(out, path):
    """A trace of a real board where its reference plane ends: the board around it, the cut, the cross-section."""
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder, reference
    m = Board(KiCadBoard(path), log=lambda *a: None, need_ports=False)
    nets = sorted(n for n in {t['net'] for t in m.prims.tracks} if 'HDMI' in n)
    seen = []
    orig = reference._field

    def spy(model, ref, e, pt, n, found):
        orig(model, ref, e, pt, n, found)
        if any('does not extend' in s for s in e.notes):
            seen.append((ref, e, np.array(pt), np.array(n), dict(found)))
    reference._field, reference.FIELD = spy, True
    try:
        builder.build(m, nets=nets)
    finally:
        reference._field, reference.FIELD = orig, False
    if not seen:
        print('board cut: no section where the plane ends found')
        return
    def moved(s):
        note = next((x for x in s[1].notes if x.startswith('field solver')), '')
        f = float(note.rsplit('formula ', 1)[1].rstrip(')')) if 'formula' in note else s[1].z0
        return abs(s[1].z0 / f - 1)
    ref, e, pt, n, found = max(seen, key=moved)                          # the flagged section the solve moves most
    keys, got = [], []
    solve = reference._solve_cut
    reference._solve_cut = lambda *a: (keys.append(a), solve(*a))[1]
    try:
        reference._field(m, ref, e, pt, n, found)
    finally:
        reference._solve_cut = solve
    xs = reference._cut_section(*keys[0])
    R = e.w / 2 + max(3 * e.w, reference.FIELD_REACH * max(h for _, h, _ in found.values()))
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw=dict(width_ratios=[1, 1.5]))
    lay = next(iter(found.values()))[0]
    from shapely.geometry import box
    win = box(pt[0] - 2.5 * R, pt[1] - 2.5 * R, pt[0] + 2.5 * R, pt[1] + 2.5 * R)
    g = ref.geom[lay].intersection(win)
    for poly in getattr(g, 'geoms', [g]):
        if poly.geom_type == 'Polygon':
            ax.fill(*poly.exterior.xy, color=DIEL, ec=MUTED, lw=0.6)
            for hole in poly.interiors:
                ax.fill(*hole.xy, color=SURFACE, ec=MUTED, lw=0.6)
    for t in m.prims.tracks:
        if t['net'] == e.net and t['layer'] == e.layer:
            p = np.asarray(t['pts'])
            ax.plot(p[:, 0], p[:, 1], color=BLUE, lw=max(1.0, t.get('w', e.w) * 72 / (5 * R) * 2.5),
                    solid_capstyle='round')
    a, b = pt - n * R, pt + n * R
    ax.plot([a[0], b[0]], [a[1], b[1]], color=ORANGE, lw=1.6)
    ax.plot(*pt, 'o', color=ORANGE, ms=4)
    ax.set_xlim(pt[0] - 2.5 * R, pt[0] + 2.5 * R)
    ax.set_ylim(pt[1] - 2.5 * R, pt[1] + 2.5 * R)
    ax.set_aspect('equal')
    ax.set_title(f"{e.layer} trace of {e.net.split('/')[-1]} (blue) over {lay} (beige); the cut (orange)",
                 fontsize=9)
    ax.set_xlabel('mm')
    zs = m.cu_z[e.layer]
    hh = max(h for _, h, _ in found.values())
    field_panel(bx, xs, (-R * 0.9, R * 0.9, zs - 2.2 * hh, zs + 2.2 * hh), None, SEQ, 0, 1)
    formula = next((s for s in e.notes if s.startswith('field solver')), '')
    flag = next((s for s in e.notes if 'does not extend' in s), '')
    bx.set_title(f"The cut, solved: {formula.replace('field solver: ', '')}\nthe line model's note: {flag}",
                 fontsize=9)
    fig.suptitle("A cut across a real board (KiCad's CM5 Minima demo), where the plane under a trace has holes",
                 x=0.02, ha='left', fontsize=11, fontweight='bold')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'xsection_board_cut.png'), dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('out', nargs='?', default=os.path.join(REPO, 'build', 'xsection_figures'))
    ap.add_argument('--board', help='a .kicad_pcb with HDMI nets for xsection_board_cut.png (the CM5 Minima demo)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for f in (anatomy, cases, convergence, cache, coupling):
        f(a.out)
        print('wrote', f.__name__)
    if a.board:
        board_cut(a.out, a.board)
        print('wrote board_cut')


if __name__ == '__main__':
    main()
