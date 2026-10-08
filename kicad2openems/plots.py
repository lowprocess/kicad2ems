"""
Plots: model + mesh preview (no openEMS needed) and S-parameter results (optional overlay).
"""
import numpy as np


def preview(m, mesh, path, title=''):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    lays = [l for l in m.cu_order[::-1] if l in m.copper]
    n = len(lays)
    fig = plt.figure(figsize=(6.5 * min(n, 2) + 1, 4.2 * ((n + 1) // 2) + 3.6))
    gs = fig.add_gridspec((n + 1) // 2 + 1, 2)
    ob = m.outline.bounds
    pad = 0.08 * max(ob[2] - ob[0], ob[3] - ob[1])
    for i, lay in enumerate(lays):
        ax = fig.add_subplot(gs[i // 2, i % 2])
        for x in mesh['x']:
            ax.axvline(x, color='0.82', lw=0.3, zorder=0)
        for y in mesh['y']:
            ax.axhline(y, color='0.82', lw=0.3, zorder=0)
        ax.fill(*m.outline.exterior.xy, color='#e8efe0', zorder=1)
        for p in m.copper[lay]:
            ax.fill(*p.exterior.xy, color='#d4893b', alpha=0.85, lw=0, zorder=2)
            for r in p.interiors:
                ax.fill(*r.xy, color='#e8efe0', lw=0, zorder=3)
        for (x, y, r, z0, z1) in m.vias:
            if z0 - 1e-9 <= m.cu_z[lay] <= z1 + 1e-9:
                ax.add_patch(plt.Circle((x, y), r, color='k', zorder=4))
        for p in m.ports:
            if p.layer == lay:
                s, e = p.box()
                ax.plot([s[0], e[0]], [s[1], e[1]], color='#c0392b', lw=3, zorder=5)
                ax.annotate(p.name, (p.c[0], p.c[1]), xytext=(4, 4), textcoords='offset points', color='#c0392b',
                            fontsize=9, weight='bold', zorder=6)
        ax.set_xlim(ob[0] - pad, ob[2] + pad); ax.set_ylim(ob[1] - pad, ob[3] + pad)
        ax.set_aspect('equal'); ax.set_title(f"{lay} (z = {m.cu_z[lay]:.3f} mm)", fontsize=10)
        ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)')
    ax = fig.add_subplot(gs[-1, :])
    for l in m.layers:
        ax.add_patch(plt.Rectangle((ob[0], l['z0']), ob[2] - ob[0], l['z1'] - l['z0'], color='#cfdcc0', zorder=1))
        ax.text(ob[2] + pad * 0.2, (l['z0'] + l['z1']) / 2, f"{l['name']}  er {l['er']:.2f}", va='center', fontsize=8)
    for lay, z in m.cu_z.items():
        ax.plot([ob[0], ob[2]], [z, z], color='#d4893b', lw=2, zorder=3)
    for z in mesh['z']:
        ax.axhline(z, color='0.6', lw=0.4, zorder=2)
    for x in mesh['x']:
        ax.axvline(x, color='0.85', lw=0.3, zorder=0)
    zt = m.thickness
    ax.set_xlim(ob[0] - pad, ob[2] + pad * 4); ax.set_ylim(-0.6 * zt - 0.2, 1.6 * zt + 0.2)
    ax.set_xlabel('x (mm)'); ax.set_ylabel('z (mm)')
    inf = mesh['info']
    ax.set_title(f"side view, z mesh ({inf['nx']} x {inf['ny']} x {inf['nz']} = {inf['cells'] / 1e6:.2f} M cells, "
                 f"dt {inf['dt_ps']:.3f} ps)", fontsize=10)
    fig.suptitle(f"{title} - openEMS model and mesh", x=0.02, ha='left', weight='bold')
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _draw_smith_grid(ax, r_vals=(0.2, 0.5, 1, 2, 5), x_vals=(0.2, 0.5, 1, 2, 5)):
    """Draw a normalised-impedance Smith chart grid on `ax` (no extra dependencies)."""
    grid = dict(color='0.82', lw=0.6, zorder=0)

    # Constant-resistance circles: sweep reactance, map z -> Gamma
    x_sweep = np.concatenate([-np.logspace(3, -3, 600), [0.0], np.logspace(-3, 3, 600)])
    for r in r_vals:
        z = r + 1j * x_sweep
        g = (z - 1) / (z + 1)
        ax.plot(g.real, g.imag, **grid)

    # Constant-reactance arcs: sweep resistance, map z -> Gamma
    r_sweep = np.concatenate([[0.0], np.logspace(-3, 3, 800)])
    for x in x_vals:
        for s in (1, -1):
            z = r_sweep + 1j * s * x
            g = (z - 1) / (z + 1)
            ax.plot(g.real, g.imag, **grid)

    # Real axis and outer |Gamma| = 1 boundary
    ax.plot([-1, 1], [0, 0], **grid)
    th = np.linspace(0, 2 * np.pi, 721)
    ax.plot(np.cos(th), np.sin(th), color='0.45', lw=1.0, zorder=0)

    # Labels: resistance along the real axis, reactance just outside the rim
    for r in r_vals:
        gr = (r - 1) / (r + 1)
        ax.text(gr, 0.015, f"{r:g}", fontsize=6.5, color='0.4', ha='right', va='bottom', rotation=90)
    for x in x_vals:
        for s in (1, -1):
            g = (1j * s * x - 1) / (1j * s * x + 1)
            ax.text(1.07 * g.real, 1.07 * g.imag, f"{s * x:+g}j", fontsize=6.5, color='0.4',
                    ha='center', va='center')

    ax.set_aspect('equal')
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.axis('off')

def plot_results(path, f, S, names, title, compare=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    n = S.shape[1]
    n_cmp = compare[1].shape[1] if compare is not None else 0
    fig = plt.figure(figsize=(14, 5.8))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1])
    ax = fig.add_subplot(gs[0])     # cartesian |S| in dB, all parameters
    sax = fig.add_subplot(gs[1])    # Smith chart, reflection only
    _draw_smith_grid(sax)
    cols = plt.cm.tab10.colors

    k = 0
    for j in range(n):
        for i in range(j, n):       # lower triangle incl. diagonal (reciprocal network)
            c = cols[k % 10]
            lab = f"S({names[i]},{names[j]})"
            has_cmp = i < n_cmp and j < n_cmp

            # --- cartesian: reflection and transmission together ---
            ax.plot(f / 1e9, 20 * np.log10(np.abs(S[:, i, j]) + 1e-15), color=c,
                    lw=2.0 if i == j else 1.6, label=lab + ' openEMS')
            if has_cmp:
                ax.plot(compare[0] / 1e9, 20 * np.log10(np.abs(compare[1][:, i, j]) + 1e-15), '--',
                        color=c, lw=1.4, label=lab + ' mom25d')

            # --- Smith chart: reflection coefficients only ---
            if i == j:
                g = S[:, i, i]
                sax.plot(g.real, g.imag, color=c, lw=1.8, label=lab + ' openEMS')
                sax.plot(g.real[0], g.imag[0], 'o', color=c, ms=5)      # start frequency
                sax.plot(g.real[-1], g.imag[-1], 's', color=c, ms=5)    # stop frequency
                if has_cmp:
                    gc = compare[1][:, i, i]
                    sax.plot(gc.real, gc.imag, '--', color=c, lw=1.4, label=lab + ' mom25d')
            k += 1

    ax.set_xlabel('Frequency (GHz)')
    ax.set_ylabel('|S| (dB)')
    ax.set_title('S-parameters (reflection: thick, transmission/coupling: thin)', fontsize=10)
    ax.grid(alpha=0.3)
    n_entries = len(ax.get_legend_handles_labels()[0])
    ax.legend(fontsize=8, ncol=2 if n_entries > 8 else 1)

    # Smith legend: traces plus start/stop marker key
    h, l = sax.get_legend_handles_labels()
    h += [Line2D([], [], ls='none', marker='o', color='0.3', ms=5),
          Line2D([], [], ls='none', marker='s', color='0.3', ms=5)]
    l += [f"{f[0] / 1e9:g} GHz", f"{f[-1] / 1e9:g} GHz"]
    sax.legend(h, l, fontsize=7.5, loc='upper left', bbox_to_anchor=(-0.02, 1.02), framealpha=0.85)
    sax.set_title('Reflection (Smith chart)', fontsize=10)

    fig.suptitle(title, x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)