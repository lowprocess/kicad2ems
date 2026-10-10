"""
Plots as matplotlib Figures (no pyplot, no backend state: a GUI can embed them, save() writes a PNG):
model + mesh preview, transmission-line sections, S-parameters (with overlays), convergence, antenna.
"""
import numpy as np
import matplotlib
from matplotlib.figure import Figure
from matplotlib import patches
from matplotlib.lines import Line2D
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.cm import ScalarMappable

TAB10 = matplotlib.colormaps['tab10'].colors


def save(fig, path, dpi=130):
    """Write a figure to a PNG."""
    fig.savefig(path, dpi=dpi)
    return path


def preview(m, mesh, title=''):
    lays = [l for l in m.cu_order[::-1] if l in m.copper]
    n = len(lays)
    fig = Figure(figsize=(6.5 * min(n, 2) + 1, 4.2 * ((n + 1) // 2) + 3.6))
    gs = fig.add_gridspec((n + 1) // 2 + 1, 2)
    ob = m.outline.bounds
    pad = 0.08 * max(ob[2] - ob[0], ob[3] - ob[1])
    for i, lay in enumerate(lays):
        ax = fig.add_subplot(gs[i // 2, i % 2])
        for x in mesh['x']:
            ax.axvline(x, color='#1d2b3a', lw=0.35, alpha=0.35, zorder=4.5)
        for y in mesh['y']:
            ax.axhline(y, color='#1d2b3a', lw=0.35, alpha=0.35, zorder=4.5)
        ax.fill(*m.outline.exterior.xy, color='#e8efe0', zorder=1)
        for p in m.copper[lay]:
            ax.fill(*p.exterior.xy, color='#d4893b', alpha=0.85, lw=0, zorder=2)
            for r in p.interiors:
                ax.fill(*r.xy, color='#e8efe0', lw=0, zorder=3)
        for (x, y, r, z0, z1) in mesh.get('vias', m.vias):
            if z0 - 1e-9 <= m.cu_z[lay] <= z1 + 1e-9:
                ax.add_patch(patches.Circle((x, y), r, color='k', zorder=4))
        for pt in getattr(m, 'parts', []):
            if pt.layer == lay:
                s, e = pt.box()
                ax.add_patch(patches.Rectangle((s[0], s[1]), e[0] - s[0], e[1] - s[1], fill=False, hatch='////',
                                               ec='#6c3483', lw=1.2, zorder=5))
                ax.annotate(pt.ref, ((s[0] + e[0]) / 2, e[1]), xytext=(0, 4), textcoords='offset points',
                            color='#6c3483', fontsize=8, ha='center', weight='bold', zorder=6)
        for p in m.ports:
            if p.layer == lay:
                s, e = p.box()
                if p.kind == 'gap':
                    ax.add_patch(patches.Rectangle((s[0], s[1]), e[0] - s[0], e[1] - s[1], color='#c0392b', zorder=5))
                else:
                    ax.plot([s[0], e[0]], [s[1], e[1]], color='#c0392b', lw=3, zorder=5)
                ax.annotate(p.name, (p.c[0], p.c[1]), xytext=(4, 4), textcoords='offset points', color='#c0392b',
                            fontsize=9, weight='bold', zorder=6)
        ax.set_xlim(ob[0] - pad, ob[2] + pad); ax.set_ylim(ob[1] - pad, ob[3] + pad)
        ax.set_aspect('equal'); ax.set_title(f"{lay} (z = {m.cu_z[lay]:.3f} mm)", fontsize=10)
        ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)')
    ax = fig.add_subplot(gs[-1, :])
    for l in m.layers:
        ax.add_patch(patches.Rectangle((ob[0], l['z0']), ob[2] - ob[0], l['z1'] - l['z0'], color='#cfdcc0', zorder=1))
        ax.text(ob[2] + pad * 0.2, (l['z0'] + l['z1']) / 2, f"{l['name']}  er {l['er']:.2f}", va='center', fontsize=8)
    for lay, z in m.cu_z.items():
        ax.plot([ob[0], ob[2]], [z, z], color='#d4893b', lw=2, zorder=3)
    for z in mesh['z']:
        ax.axhline(z, color='0.6', lw=0.4, zorder=2)
    for x in mesh['x']:
        ax.axvline(x, color='#1d2b3a', lw=0.3, alpha=0.25, zorder=2)
    zt = m.thickness
    ax.set_xlim(ob[0] - pad, ob[2] + pad * 4); ax.set_ylim(-0.6 * zt - 0.2, 1.6 * zt + 0.2)
    ax.set_xlabel('x (mm)'); ax.set_ylabel('z (mm)')
    inf = mesh['info']
    ax.set_title(f"side view, z mesh ({inf['nx']} x {inf['ny']} x {inf['nz']} = {inf['cells'] / 1e6:.2f} M cells, "
                 f"dt {inf['dt_ps']:.3f} ps)", fontsize=10)
    fig.suptitle(f"{title} - openEMS model and mesh", x=0.02, ha='left', weight='bold')
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


# ---- transmission-line sections -------------------------------------------------------------------
def z_norm(z_port):
    """Colour scale for Z0: centred on the port impedance, +-40 %."""
    return TwoSlopeNorm(vcenter=z_port, vmin=0.6 * z_port, vmax=1.4 * z_port)


Z_CMAP = matplotlib.colormaps['coolwarm']


def plot_lines(lm, m, title=''):
    """Layout map of the sections (coloured by Z0, numbered along each path) and the impedance profile
    along each path (Z0 against the distance from its first port). None without paths."""
    if not lm.paths:
        return None
    z_port = next(iter(lm.port_z0.values()), 50.0)
    norm, cmap = z_norm(z_port), Z_CMAP
    npath = len(lm.paths)
    # view: the sections plus a margin (a corner of a large board stays readable)
    pts = np.vstack([np.asarray(e.pts) for e in lm.elements if e.pts is not None and len(e.pts)])
    lo, hi = pts.min(0), pts.max(0)
    mg = max(1.5, 0.08 * float(max(hi - lo)))
    ob = m.outline.bounds
    x0, x1 = max(ob[0], lo[0] - mg), min(ob[2], hi[0] + mg)
    y0, y1 = max(ob[1], lo[1] - mg), min(ob[3], hi[1] + mg)
    span = max(x1 - x0, y1 - y0)
    map_h = float(np.clip(11.5 * (y1 - y0) / max(x1 - x0, 1e-9), 2.6, 8.0))
    fig = Figure(figsize=(13, map_h + 1.4 + 3.0 * npath))
    gs = fig.add_gridspec(1 + npath, 1, height_ratios=[map_h + 0.6] + [3.0] * npath)
    ax = fig.add_subplot(gs[0])
    ax.fill(*m.outline.exterior.xy, color='#f3f5ef', zorder=0)
    layers = {e.layer for e in lm.elements if e.kind in ('trace', 'pad')}
    for lay in layers:                                     # the copper of the section layers, faint
        for p in m.copper.get(lay, []):
            ax.fill(*p.exterior.xy, color='0.86', lw=0, zorder=1)
            for r in p.interiors:
                ax.fill(*r.xy, color='#f3f5ef', lw=0, zorder=1)
    numbered = {}
    for k, (p, q, secs, _) in enumerate(lm.paths):
        for i, (e, s0, _) in enumerate(secs, 1):
            numbered.setdefault(id(e), (k, i))
    from shapely.geometry import LineString
    import matplotlib.patheffects as pe
    edge = [pe.Stroke(linewidth=4.6, foreground='0.15'), pe.Normal()]
    for e in lm.elements:
        col = cmap(norm(e.z0)) if e.z0 else '0.55'
        if e.kind == 'trace' and e.pts is not None and len(e.pts) > 1:
            if e.w < 0.012 * span:                         # too thin to see at true width: a fixed-width line
                ax.plot(*np.asarray(e.pts).T, color=col, lw=3.2, solid_capstyle='butt', zorder=3, path_effects=edge)
            else:
                g = LineString(e.pts).buffer(e.w / 2, cap_style=2)
                for poly in getattr(g, 'geoms', [g]):
                    ax.fill(*poly.exterior.xy, fc=col, ec='0.15', lw=0.6, zorder=3)
        elif e.kind == 'pad' and e.geom is not None:
            for poly in getattr(e.geom, 'geoms', [e.geom]):
                ax.fill(*poly.exterior.xy, fc=col, ec='0.15', lw=0.6, zorder=4)
        elif e.kind == 'via':
            ax.add_patch(patches.Circle(e.pts[0], max(e.w / 2 + 0.05, 0.01 * span), fc=col, ec='0.15', lw=0.8, zorder=5))
        elif e.kind == 'part':
            (xa, ya), (xb, yb) = e.pts
            ax.plot([xa, xb], [ya, yb], color='#6c3483', lw=3, zorder=5)
            ax.annotate(e.label, ((xa + xb) / 2, (ya + yb) / 2), xytext=(0, 7), textcoords='offset points',
                        color='#6c3483', fontsize=8, ha='center', weight='bold', zorder=7)
        if id(e) in numbered and e.pts is not None and (e.length >= 0.04 * span or e.kind in ('via', 'part')):
            k, i = numbered[id(e)]
            c = np.asarray(e.pts).mean(axis=0)
            ax.annotate(f"{i}" if npath == 1 else f"{k + 1}.{i}", c, xytext=(0, -11), textcoords='offset points',
                        fontsize=7, ha='center', va='center', zorder=8,
                        bbox=dict(boxstyle='round,pad=0.15', fc='white', ec='0.4', lw=0.5))
    for name in lm.ports:
        p = next(pp for pp in m.ports if pp.name == name)
        ax.plot(*p.c, 'o', color='#c0392b', ms=5, zorder=9)
        ax.annotate(name, p.c, xytext=(4, 5), textcoords='offset points', color='#c0392b', fontsize=9, weight='bold',
                    zorder=10)
    ax.set_aspect('equal')
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{-v:g}"))      # label in KiCad coordinates
    ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm, KiCad)')
    ax.set_title('sections coloured by Z0 (numbers: order along the path, as in the table)', fontsize=10)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.9, pad=0.01, label='Z0 (ohm)')
    for k, (p, q, secs, branches) in enumerate(lm.paths):
        px = fig.add_subplot(gs[k + 1])
        zs = [e.z0 for e, _, _ in secs if e.z0]
        top = max([z_port * 1.3] + zs) * 1.08
        for i, (e, s0, _) in enumerate(secs, 1):
            s1 = s0 + e.length
            if e.z0:
                px.fill_between([s0, s1], 0, e.z0, step=None, color=cmap(norm(e.z0)), alpha=0.85, lw=0)
                px.plot([s0, s1], [e.z0, e.z0], color='k', lw=1.2)
            else:
                px.axvspan(s0, s1, color='#6c3483' if e.kind == 'part' else '0.7', alpha=0.25, lw=0)
                px.text((s0 + s1) / 2, top * 0.93, e.label if e.kind == 'part' else '?', ha='center', va='top',
                        fontsize=7, color='#6c3483', rotation=90)
            if e.length > 0.04 * max(1e-9, secs[-1][1] + secs[-1][0].length):
                px.text((s0 + s1) / 2, (e.z0 or top * 0.5) / 2, str(i), ha='center', va='center', fontsize=7)
        for at, L, lab, z in branches:
            px.annotate('', (at, 0), (at, top * 0.14), arrowprops=dict(arrowstyle='->', color='0.3', lw=0.8))
            if lab.startswith('stub'):
                ends = lab.split('ends: ')[-1].rstrip(')').split(', ')
                short = 'stub to ' + ', '.join(x.split(' ')[0] + (' (GND)' if 'ground' in x else '') for x in ends)
            else:
                short = lab.split(' ')[0] + (' to GND' if 'ground' in lab else '')
            px.text(at, top * 0.16, short, ha='center', va='bottom', fontsize=7, color='0.25')
        px.axhline(lm.port_z0.get(p, z_port), color='#c0392b', lw=1, ls='--')
        px.set_ylim(0, top)
        px.set_xlim(0, max(1e-6, sum(e.length for e, _, _ in secs)))
        px.set_ylabel('Z0 (ohm)')
        px.set_title(f"{p} -> {q or 'end'}: impedance profile" + (" (arrows: branches)" if branches else ''), fontsize=10)
        px.grid(alpha=0.3)
    px.set_xlabel('distance along the path (mm)')
    fig.suptitle(f"{title} - transmission-line sections (quasi-static, copper thickness included)", x=0.02,
                 ha='left', weight='bold')
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


# ---- S-parameters ------------------------------------------------------------------------------------
def _draw_smith_grid(ax, r_vals=(0.2, 0.5, 1, 2, 5), x_vals=(0.2, 0.5, 1, 2, 5)):
    """Draw a normalised-impedance Smith chart grid on `ax` (no extra dependencies)."""
    grid = dict(color='0.82', lw=0.6, zorder=0)
    x_sweep = np.concatenate([-np.logspace(3, -3, 600), [0.0], np.logspace(-3, 3, 600)])
    for r in r_vals:                                        # constant-resistance circles
        g = (r + 1j * x_sweep - 1) / (r + 1j * x_sweep + 1)
        ax.plot(g.real, g.imag, **grid)
    r_sweep = np.concatenate([[0.0], np.logspace(-3, 3, 800)])
    for x in x_vals:                                        # constant-reactance arcs
        for s in (1, -1):
            g = (r_sweep + 1j * s * x - 1) / (r_sweep + 1j * s * x + 1)
            ax.plot(g.real, g.imag, **grid)
    ax.plot([-1, 1], [0, 0], **grid)
    th = np.linspace(0, 2 * np.pi, 721)
    ax.plot(np.cos(th), np.sin(th), color='0.45', lw=1.0, zorder=0)
    for r in r_vals:
        ax.text((r - 1) / (r + 1), 0.015, f"{r:g}", fontsize=6.5, color='0.4', ha='right', va='bottom', rotation=90)
    for x in x_vals:
        for s in (1, -1):
            g = (1j * s * x - 1) / (1j * s * x + 1)
            ax.text(1.07 * g.real, 1.07 * g.imag, f"{s * x:+g}j", fontsize=6.5, color='0.4', ha='center', va='center')
    ax.set_aspect('equal')
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.axis('off')


def plot_results(f, S, names, title, overlays=(), label='openEMS'):
    """|S| in dB (all parameters) and the reflections on a Smith chart. overlays: [(f, S, label, style)]
    drawn in the same colours (e.g. a Touchstone file to compare, the fast line model)."""
    n = S.shape[1]
    fig = Figure(figsize=(14, 5.8))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1])
    ax = fig.add_subplot(gs[0])
    sax = fig.add_subplot(gs[1])
    _draw_smith_grid(sax)
    k = 0
    for j in range(n):
        for i in range(j, n):                               # lower triangle incl. diagonal (reciprocal network)
            c = TAB10[k % 10]
            lab = f"S({names[i]},{names[j]})"
            k += 1
            if not np.isfinite(S[:, i, j]).any():                   # not excited: nothing to show or compare
                continue
            ax.plot(f / 1e9, 20 * np.log10(np.abs(S[:, i, j]) + 1e-15), color=c, lw=2.0 if i == j else 1.6,
                    label=f"{lab} {label}")
            for fo, So, olab, style in overlays:
                if i < So.shape[1] and j < So.shape[1] and np.isfinite(So[:, i, j]).any():
                    ax.plot(fo / 1e9, 20 * np.log10(np.abs(So[:, i, j]) + 1e-15), style, color=c, lw=1.3,
                            label=f"{lab} {olab}")
            if i == j:
                g = S[:, i, i]
                sax.plot(g.real, g.imag, color=c, lw=1.8, label=f"{lab} {label}")
                sax.plot(g.real[0], g.imag[0], 'o', color=c, ms=5)
                sax.plot(g.real[-1], g.imag[-1], 's', color=c, ms=5)
                for fo, So, olab, style in overlays:
                    if i < So.shape[1] and np.isfinite(So[:, i, i]).any():
                        sax.plot(So[:, i, i].real, So[:, i, i].imag, style, color=c, lw=1.3, label=f"{lab} {olab}")
    ax.set_xlabel('Frequency (GHz)')
    ax.set_ylabel('|S| (dB)')
    ax.set_title('S-parameters (reflection: thick, transmission/coupling: thin)', fontsize=10)
    ax.grid(alpha=0.3)
    n_entries = len(ax.get_legend_handles_labels()[0])
    ax.legend(fontsize=8, ncol=2 if n_entries > 8 else 1)
    h, l = sax.get_legend_handles_labels()
    h += [Line2D([], [], ls='none', marker='o', color='0.3', ms=5), Line2D([], [], ls='none', marker='s', color='0.3', ms=5)]
    l += [f"{f[0] / 1e9:g} GHz", f"{f[-1] / 1e9:g} GHz"]
    sax.legend(h, l, fontsize=7.5, loc='upper left', bbox_to_anchor=(-0.02, 1.02), framealpha=0.85)
    sax.set_title('Reflection (Smith chart)', fontsize=10)
    fig.suptitle(title, x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    return fig


def plot_convergence(f, levels, names, title, floor_db=-40.0):
    """|S| in dB on every mesh level (one colour per parameter, one line style per level) and, below,
    the deviation from the finest level. levels: [(label, S (nf, n, n)), ...] coarse to fine."""
    fig = Figure(figsize=(11, 8))
    ax, dx = fig.subplots(2, 1, sharex=True, gridspec_kw=dict(height_ratios=[1.6, 1]))
    styles = [':', '-.', '--', (0, (5, 1)), '-']
    styles = styles[-len(levels):] if len(levels) <= len(styles) else ['--'] * (len(levels) - 1) + ['-']
    n = levels[-1][1].shape[1]
    ref = levels[-1][1]
    k = 0
    for j in range(n):
        for i in range(j, n):
            c = TAB10[k % 10]
            rdb = 20 * np.log10(np.abs(ref[:, i, j]) + 1e-15)
            for (lab, S), ls in zip(levels, styles):
                sdb = 20 * np.log10(np.abs(S[:, i, j]) + 1e-15)
                ax.plot(f / 1e9, sdb, ls=ls, color=c, lw=1.6 if ls == '-' else 1.2, label=f"S({names[i]},{names[j]}) {lab}")
                if S is not ref:
                    d = np.where(rdb > floor_db, sdb - rdb, np.nan)
                    dx.plot(f / 1e9, d, ls=ls, color=c, lw=1.2, label=f"S({names[i]},{names[j]}) {lab}")
            k += 1
    ax.set_ylabel('|S| (dB)'); ax.grid(alpha=0.3)
    ax.set_title("mesh levels (finest: solid)", fontsize=10)
    ax.legend(fontsize=7.5, ncol=max(1, len(levels)))
    dx.axhline(0, color='0.3', lw=0.8)
    dx.set_ylabel("|S| - |S| finest (dB)"); dx.set_xlabel('Frequency (GHz)'); dx.grid(alpha=0.3)
    dx.set_title(f"deviation from the finest level (where it is above {floor_db:g} dB)", fontsize=10)
    fig.suptitle(title, x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    return fig


# ---- antenna -------------------------------------------------------------------------------------------
def plot_antenna(f, s11, z0, res, title):
    """Input match (|S11|, input impedance) and gain / directivity / efficiency over frequency."""
    fig = Figure(figsize=(12, 8))
    axs = fig.subplots(2, 2)
    fg = f / 1e9
    mt = res['match']
    ax = axs[0, 0]
    ax.plot(fg, 20 * np.log10(np.abs(s11) + 1e-15), color='#1f5f99', lw=1.8)
    ax.axhline(-10, color='0.5', lw=0.8, ls='--')
    ax.axvline(mt['f_res'] / 1e9, color='#c0392b', lw=0.8, ls=':')
    if mt['bw10']:
        ax.axvspan(mt['bw10_lo'] / 1e9, mt['bw10_hi'] / 1e9, color='#1f5f99', alpha=0.08)
    ax.set_ylabel('|S11| (dB)'); ax.set_title(f"input match ({res['port']}, {z0:g} ohm)", fontsize=10)
    ax = axs[0, 1]
    zin = z0 * (1 + s11) / (1 - s11)
    ax.plot(fg, zin.real, color='#1f5f99', lw=1.6, label='R')
    ax.plot(fg, zin.imag, color='#d4893b', lw=1.6, label='X')
    ax.axhline(0, color='0.5', lw=0.8); ax.axhline(z0, color='0.5', lw=0.8, ls='--')
    ax.set_ylim(-3 * z0, 4 * z0)
    ax.set_ylabel('Zin (ohm)'); ax.set_title('input impedance', fontsize=10); ax.legend(fontsize=8)
    rows = res['rows']
    rf = np.array([r['f'] for r in rows]) / 1e9
    val = lambda k: np.array([np.nan if r[k] is None else r[k] for r in rows], float)
    ax = axs[1, 0]
    ax.plot(rf, val('D_dbi'), 'o-', ms=3, color='0.4', lw=1.2, label='directivity')
    ax.plot(rf, val('G_dbi'), 'o-', ms=3, color='#1f5f99', lw=1.6, label='gain')
    ax.plot(rf, val('Grealized_dbi'), 'o-', ms=3, color='#c0392b', lw=1.6, label='realized gain (incl. mismatch)')
    ax.set_ylabel('dBi'); ax.set_xlabel('Frequency (GHz)'); ax.legend(fontsize=8)
    ax.set_title('maximum over all directions', fontsize=10)
    lo = np.nanmin(val('Grealized_dbi')) if np.isfinite(val('Grealized_dbi')).any() else -20
    ax.set_ylim(max(lo, np.nanmax(val('D_dbi')) - 30) - 1, np.nanmax(val('D_dbi')) + 2)
    ax = axs[1, 1]
    ax.plot(rf, 100 * val('eff'), 'o-', ms=3, color='#2e8b57', lw=1.6)
    ax.set_ylim(0, 105); ax.set_ylabel('radiation efficiency (%)'); ax.set_xlabel('Frequency (GHz)')
    ax.set_title('Prad / accepted power', fontsize=10)
    for a in axs.flat:
        a.grid(alpha=0.3)
    for a in axs[0]:
        a.set_xlabel('Frequency (GHz)')
    fig.suptitle(title, x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    return fig


def plot_pattern(res, title, floor_db=-30.0):
    """Gain cuts (polar, dBi) and the 3D gain pattern at the pattern frequency."""
    fig = Figure(figsize=(15, 9))
    gmax = max(max(c['gain_dbi']) for c in res['cuts'].values())
    top = np.ceil(gmax / 5) * 5
    for i, (name, c) in enumerate(res['cuts'].items()):
        ax = fig.add_subplot(2, 3, i + 1, projection='polar')
        ang = np.radians(c['angle'])
        g = np.maximum(np.array(c['gain_dbi']), top + floor_db)
        ax.plot(ang, g, color='#1f5f99', lw=1.8)
        ax.set_ylim(top + floor_db, top)
        ax.set_theta_zero_location('N' if 'theta = 90' not in name else 'E')
        ax.set_theta_direction(-1 if 'theta = 90' not in name else 1)
        ax.set_title(f"{name}: max {max(c['gain_dbi']):.1f} dBi", fontsize=10, pad=14)
        ax.tick_params(labelsize=7)
    sp = res['sphere']
    TH, PH = np.meshgrid(np.radians(sp['theta']), np.radians(sp['phi']), indexing='ij')
    gdb = 10 * np.log10(np.maximum(sp['gain'], 1e-9))
    r = np.clip(gdb - (top + floor_db), 0, None)
    X, Y, Z = r * np.sin(TH) * np.cos(PH), r * np.sin(TH) * np.sin(PH), r * np.cos(TH)
    tx = fig.add_subplot(2, 3, 4)
    tx.axis('off')
    mt = res['match']
    row = min(res['rows'], key=lambda r: abs(r['f'] - res['f_pattern']))
    fmt = lambda v: f"{v:.2f} dBi" if v is not None else '-'
    lines = [f"pattern frequency  {res['f_pattern'] / 1e9:.3f} GHz", '',
             f"resonance          {mt['f_res'] / 1e9:.3f} GHz", f"S11 there          {mt['s11_db']:.1f} dB",
             f"Zin there          {mt['zin'][0]:.1f} {'+' if mt['zin'][1] >= 0 else '-'} j{abs(mt['zin'][1]):.1f} ohm",
             "-10 dB band        " + (f"{mt['bw10_lo'] / 1e9:.3f}-{mt['bw10_hi'] / 1e9:.3f} GHz" if mt['bw10'] else 'none'),
             '', f"directivity        {fmt(row['D_dbi'])}", f"efficiency         {row['eff'] * 100:.1f} %",
             f"gain               {fmt(row['G_dbi'])}", f"realized gain      {fmt(row['Grealized_dbi'])}"]
    tx.text(0.02, 0.95, "\n".join(lines), va='top', family='monospace', fontsize=10, transform=tx.transAxes)
    ax = fig.add_subplot(2, 3, 5, projection='3d')
    norm = Normalize(top + floor_db, top)
    viridis = matplotlib.colormaps['viridis']
    ax.plot_surface(X, Y, Z, facecolors=viridis(norm(gdb)), rstride=1, cstride=1, linewidth=0, shade=False)
    fig.colorbar(ScalarMappable(norm=norm, cmap=viridis), ax=ax, shrink=0.6, pad=0.15, label='gain (dBi)')
    lim = np.abs([X, Y, Z]).max()
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_zlim(-lim, lim)
    ax.set_xlabel('x'); ax.set_ylabel('y'); ax.set_zlabel('z (board normal)')
    ax.set_title('3D gain pattern', fontsize=10)
    fig.suptitle(f"{title} - {res['f_pattern'] / 1e9:.3f} GHz (cut angles: theta from +z, phi from +x)",
                 x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    return fig
