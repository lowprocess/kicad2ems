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
        ax.axvline(x, color='#1d2b3a', lw=0.3, alpha=0.25, zorder=2)
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


def plot_results(path, f, S, names, title, compare=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    n = S.shape[1]
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.8))
    cols = plt.cm.tab10.colors
    k = 0
    for j in range(n):
        for i in range(n):
            if i < j and n > 1:
                continue
            ax = axs[0] if i == j else axs[1]
            lab = f"S({names[i]},{names[j]})"
            ax.plot(f / 1e9, 20 * np.log10(np.abs(S[:, i, j]) + 1e-15), color=cols[k % 10], lw=1.8, label=lab + ' openEMS')
            if compare is not None and i < compare[1].shape[1] and j < compare[1].shape[1]:
                ax.plot(compare[0] / 1e9, 20 * np.log10(np.abs(compare[1][:, i, j]) + 1e-15), '--', color=cols[k % 10],
                        lw=1.4, label=lab + ' ' + compare[2])
            k += 1
    for ax, t in zip(axs, ('reflection', 'transmission / coupling')):
        ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('|S| (dB)'); ax.set_title(t); ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(title, x=0.02, ha='left', weight='bold')
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
