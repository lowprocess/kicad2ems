"""
mom25d_viz.py - structure / mesh / results / debug views for mom25d_ml.MultiLayerMoM

  plot_stackup(ax, sim)        dielectric cross-section, metal interfaces, grounds
  plot_3d(ax3d, sim)           exploded 3D view of all metal layers (z exaggerated)
  plot_layout(ax, sim)         top view: mesh per layer, ports, reference planes, sample cuts
  plot_current(ax, sim, J)     |J| on the triangles of one layer (dB, single-hue ramp)
  plot_sparams(ax, res)        |S_i1| (dB) vs frequency, direct-labelled
  plot_kernels(ax, sim)        |G_q| vs rho for every interface pair (last frequency)
  plot_timing(ax, sim)         time per solver stage
  dashboard(sim, res, fname)   all of the above + an info panel in one PNG
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# validated categorical slots (light surface) - layer / series identity, fixed order
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948']
INK, INK2, MUTED, GRID = '#0b0b0b', '#52514e', '#8a8984', '#e4e3df'
SURFACE = '#fcfcfb'
COPPER, DIEL, GROUND = '#b87333', '#dfe8d6', '#8c8c8c'
J_CMAP = LinearSegmentedColormap.from_list('j', ['#fbefe8', '#f3b08e', '#eb6834', '#a43d14', '#4d1a07'])
PORT = '#e34948'

plt.rcParams.update({'font.size': 8, 'axes.edgecolor': MUTED, 'axes.labelcolor': INK2,
                     'xtick.color': INK2, 'ytick.color': INK2, 'axes.titlesize': 9,
                     'axes.titleweight': 'bold', 'axes.titlecolor': INK, 'figure.facecolor': SURFACE,
                     'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE})


def layer_color(sim, k):
    return SERIES[sim.layers.index(k) % len(SERIES)]


def _tri_polys(sim, k, scale=1e3):
    r = sim.rwg
    idx = sim.tri_idx[k]
    return r.nodes[r.tris[idx]] * scale, idx


# ---------------------------------------------------------------------------
def plot_stackup(ax, sim):
    st = sim.st
    z = st.z * 1e3
    H = z[-1]
    gh = 0.06 * H
    ax.add_patch(Rectangle((0, -gh), 1, gh, fc=GROUND, ec='none'))
    ax.text(0.5, -gh / 2, 'ground', ha='center', va='center', color='white', fontsize=7)
    for j, l in enumerate(st.layers):
        shade = 1 - 0.08 * min(l.er, 10)
        ax.add_patch(Rectangle((0, z[j]), 1, l.h * 1e3, fc=DIEL, ec='white', lw=1, alpha=shade))
        ax.text(1.04, z[j] + l.h * 1e3 / 2,
                f"{l.name}  {l.h * 1e3:.3g} mm  εr {l.er:g}" + (f"  tanδ {l.tand:g}" if l.tand else ''),
                va='center', fontsize=7, color=INK2)
    if st.top == 'pec':
        ax.add_patch(Rectangle((0, H), 1, gh, fc=GROUND, ec='none'))
        ax.text(0.5, H + gh / 2, 'PEC cover', ha='center', va='center', color='white', fontsize=7)
    else:
        ax.text(0.5, H + 0.08 * H, f'air (εr {st.top_er:g})', ha='center', color=MUTED, fontsize=7)
    for k in sim.layers:
        c = layer_color(sim, k)
        ax.plot([0.2, 0.8], [z[k], z[k]], color=c, lw=4, solid_capstyle='butt')
        ax.text(0.18, z[k], f"M{k}", ha='right', va='center', color=INK, fontsize=8, fontweight='bold')
    ax.set_xlim(-0.05, 2.1)
    ax.set_ylim(-gh * 1.3, H * 1.22 + gh)
    ax.set_xticks([])
    ax.set_ylabel('z (mm)')
    for s in ('top', 'right', 'bottom'):
        ax.spines[s].set_visible(False)
    ax.set_title('Stackup')


def plot_3d(ax, sim, zexag=None):
    r = sim.rwg
    ext = (r.nodes.max(0) - r.nodes.min(0)) * 1e3
    H = sim.st.z[-1] * 1e3
    zexag = zexag or 0.35 * max(ext) / H
    (x0, y0), (x1, y1) = r.nodes.min(0) * 1e3 - 0.5, r.nodes.max(0) * 1e3 + 0.5
    planes = [0.0] + ([H] if sim.st.top == 'pec' else [])
    for zp in planes:
        q = [[(x0, y0, zp * zexag), (x1, y0, zp * zexag), (x1, y1, zp * zexag), (x0, y1, zp * zexag)]]
        ax.add_collection3d(Poly3DCollection(q, fc=GROUND, alpha=0.12, ec=GROUND, lw=0.5))
    for k in sim.layers:
        P, _ = _tri_polys(sim, k)
        zz = sim.st.z[k] * 1e3 * zexag
        polys = [np.column_stack([p, np.full(3, zz)]) for p in P]
        ax.add_collection3d(Poly3DCollection(polys, fc=layer_color(sim, k), alpha=0.85, lw=0))
        ax.text(x1, y1, zz, f"M{k}", color=INK, fontsize=8)
    for p in sim.ports:
        c = _port_centre(p) * 1e3
        ax.scatter([c[0]], [c[1]], [sim.st.z[p.layer] * 1e3 * zexag], color=PORT, s=14, depthshade=False)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_zlim(0, max(H * zexag, 1e-3) * 1.1)
    ax.set_box_aspect((x1 - x0, y1 - y0, max(H * zexag, 1e-3) * 1.1))
    ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)'); ax.set_zticks([])
    ax.view_init(28, -60)
    ax.set_title(f'3D view (z × {zexag:.0f})')
    for a in (ax.xaxis, ax.yaxis, ax.zaxis):
        a.pane.set_facecolor(SURFACE); a.pane.set_edgecolor(GRID)


def _port_centre(p):
    t = 0.5 * (p.span[0] + p.span[1])
    return np.array([p.pos, t]) if p.axis == 'x' else np.array([t, p.pos])


def _port_segment(p, c):
    if p.axis == 'x':
        return [c, c], [p.span[0], p.span[1]]
    return [p.span[0], p.span[1]], [c, c]


def plot_layout(ax, sim, layers=None, mesh=True, ports=True, alpha=0.30):
    layers = layers or sim.layers
    for k in layers:
        P, _ = _tri_polys(sim, k)
        c = layer_color(sim, k)
        ax.add_collection(PolyCollection(P, fc=c, alpha=alpha, ec=c if mesh else 'none', lw=0.25,
                                         label=f"M{k}"))
    if ports:
        for i, (p, s) in enumerate(zip(sim.ports, sim.samples)):
            if p.layer not in layers:
                continue
            for x in s:                                        # wave-fit sample cuts
                xs, ys = _port_segment(p, p.pos + p.dir * x)
                ax.plot(np.array(xs) * 1e3, np.array(ys) * 1e3, color=MUTED, lw=0.6)
            xs, ys = _port_segment(p, p.pos + p.dir * p.ref)  # reference plane
            ax.plot(np.array(xs) * 1e3, np.array(ys) * 1e3, color=INK, lw=1.0, ls='--')
            xs, ys = _port_segment(p, p.pos)                  # gap
            ax.plot(np.array(xs) * 1e3, np.array(ys) * 1e3, color=PORT, lw=2.2)
            c = _port_centre(p) * 1e3
            d = p.dvec * 0.9
            ax.annotate('', xy=c + d, xytext=c, arrowprops=dict(arrowstyle='->', color=PORT, lw=1.2))
            ax.text(*(c - d * 0.9), p.name or f"P{i + 1}", color=INK, fontsize=8, fontweight='bold',
                    ha='center', va='center')
    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)')
    ax.grid(color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    nt = sum(len(sim.tri_idx[k]) for k in layers)
    ax.set_title(f"Mesh & ports ({nt} triangles)")
    if len(layers) > 1:
        ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1), frameon=False, fontsize=7)


def plot_current(ax, sim, I, layer, col=0, vmin_db=-40, cbar=True):
    Jx, Jy = sim.current_density(I[:, col])
    mag = np.hypot(np.abs(Jx), np.abs(Jy))
    db = 20 * np.log10(mag / mag.max() + 1e-30)
    P, idx = _tri_polys(sim, layer)
    pc = PolyCollection(P, array=np.clip(db[idx], vmin_db, 0), cmap=J_CMAP, ec='face', lw=0.1)
    pc.set_clim(vmin_db, 0)
    ax.add_collection(pc)
    ax.autoscale_view(); ax.set_aspect('equal')
    ax.set_xlabel('x (mm)'); ax.set_ylabel('y (mm)')
    if cbar:
        cb = plt.colorbar(pc, ax=ax, fraction=0.04, pad=0.02)
        cb.set_label('|J| (dB rel. max)', color=INK2)
        cb.outline.set_edgecolor(MUTED)
    return pc


def plot_sparams(ax, res, col=0, ylim=(-40, 1)):
    f = res.f / 1e9
    n = res.S.shape[1]
    for i in range(n):
        y = 20 * np.log10(np.abs(res.S[:, i, col]) + 1e-12)
        c = SERIES[i % len(SERIES)]
        ax.plot(f, y, color=c, lw=2, marker='o', ms=3.5, label=f"S{i + 1}{col + 1}")
        yl = np.clip(y[-1], *ylim)
        ax.text(f[-1] + 0.01 * (f[-1] - f[0]), yl, f" S{i + 1}{col + 1}", color=INK, va='center', fontsize=8)
    ax.set_xlim(f[0], f[-1] + 0.1 * (f[-1] - f[0]))
    ax.set_ylim(*ylim)
    ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('|S| (dB)')
    ax.grid(color=GRID, lw=0.5); ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.legend(loc='lower left', frameon=False, fontsize=7, ncol=2)
    ax.set_title(f'S-parameters (port {col + 1} driven)')


def plot_kernels(ax, sim, rmin=1e-5):
    if not sim.tables:
        ax.set_axis_off(); return
    rho = np.logspace(np.log10(rmin), np.log10(sim.rho_max * 0.98), 200)
    peak = 0.0
    for i, ((a, b), gt) in enumerate(sorted(sim.tables.items())):
        _, Q = gt.total(rho)
        peak = max(peak, np.abs(Q).max())
        ax.loglog(rho * 1e3, np.abs(Q), color=SERIES[i % len(SERIES)], lw=2, label=f"M{a}–M{b}")
    floor = peak * 1e-9
    ax.set_ylim(floor, peak * 3)
    ax.text(0.02, 0.03, 'clipped at 1e-9 × peak (integration noise floor)', transform=ax.transAxes,
            fontsize=6.5, color=MUTED)
    ax.set_xlabel('ρ (mm)'); ax.set_ylabel('|G_q|')
    ax.grid(color=GRID, lw=0.5, which='both'); ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=7)
    gt = next(iter(sim.tables.values()))
    ax.set_title(f"Scalar-potential kernels @ {gt.f / 1e9:.2f} GHz")


def plot_timing(ax, sim):
    tot = sim.log.totals()
    names = list(tot)[::-1]
    vals = [tot[n] for n in names]
    ax.barh(names, vals, color=SERIES[0], height=0.6)
    for y, v in enumerate(vals):
        ax.text(v, y, f"  {v:.1f} s", va='center', fontsize=7, color=INK2)
    ax.set_xlim(0, max(vals) * 1.3)
    ax.set_xlabel('seconds (total over sweep)')
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.set_title(f"Run time ({sum(vals):.1f} s)")


def info_lines(sim, res=None):
    r = sim.rwg
    L = [sim.title or 'MultiLayerMoM', '']
    L += sim.st.describe() + ['']
    L.append(f"unknowns    {r.Ne}   triangles {len(r.tris)}")
    for k in sim.layers:
        L.append(f"  M{k}: {len(sim.tri_idx[k])} tri, {int((sim.edge_layer == k).sum())} edges")
    L.append(f"dmax {sim.dmax * 1e3:.3g} mm, {sim.ncross} cells across, grade {sim.grade:g}")
    L.append(f"Z matrix    {r.Ne ** 2 * 16 / 2 ** 20:.0f} MB (complex128)")
    if sim.tables:
        gt = next(iter(sim.tables.values()))
        L.append(f"Sommerfeld  {gt.npts} pts, K={gt.K:.2g} 1/m, ρmax={sim.rho_max * 1e3:.1f} mm")
    if res is not None:
        L += ['', f"sweep       {len(res.f)} pts, {res.f[0] / 1e9:g}–{res.f[-1] / 1e9:g} GHz"]
        L.append(f"port Zref   " + ", ".join(f"{p.zc:.1f}" for p in sim.ports) + " Ω")
        L.append(f"max |S−Sᵀ|          {res.recip.max():.1e}")
        L.append(f"max singular value  {res.passiv.max():.4f}  (≤1 passive)")
        L.append(f"max wave-fit resid. {res.resid.max():.1e}")
    return L


def plot_info(ax, sim, res=None):
    ax.set_axis_off()
    ax.text(0, 1, "\n".join(info_lines(sim, res)), va='top', ha='left', family='monospace',
            fontsize=7, color=INK, transform=ax.transAxes)
    ax.set_title('Run info', loc='left')


def plot_diagnostics(ax, res):
    """Numerical health per frequency: power balance (port 1), reciprocity, wave-fit residual."""
    f = res.f / 1e9
    curves = [(np.abs(1 - np.sum(np.abs(res.S[:, :, 0]) ** 2, axis=1)), '|1 − Σ|S_i1|²|'),
              (res.recip, 'max |S − Sᵀ|'),
              (res.resid.max(1), 'wave-fit residual')]
    for i, (y, lab) in enumerate(curves):
        y = np.maximum(y, 1e-16)
        ax.semilogy(f, y, color=SERIES[i], lw=2, marker='o', ms=3.5, label=lab)
    ax.legend(frameon=False, fontsize=7, loc='center right')
    ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('error')
    ax.grid(color=GRID, lw=0.5); ax.set_axisbelow(True)
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.set_xlim(f[0], f[-1] + 0.6 * (f[-1] - f[0]))
    ax.set_title('Numerical health')
    ax.text(0.98, 0.03, 'power error = loss + radiation\nif not lossless', transform=ax.transAxes, va='bottom', ha='right', fontsize=6.5, color=MUTED)


def dashboard(sim, res=None, fname='dashboard.png', drive=0):
    """One PNG: stackup / 3D / run info, then layout + current per layer, then results & debug."""
    r = sim.rwg
    ext = r.nodes.max(0) - r.nodes.min(0)
    aspect = ext[1] / max(ext[0], 1e-12)
    show_J = res is not None and res.I is not None
    nmid = 1 + (len(sim.layers) if show_J else 0)
    wpanel = 15.0 / max(nmid, 2)
    hmid = float(np.clip(wpanel * aspect * 0.9, 2.6, 7.5))
    nbot = 4 if res is not None else 2
    fig = plt.figure(figsize=(15, 3.4 + hmid + 3.4))
    top, mid, bot = fig.subfigures(3, 1, height_ratios=[3.4, hmid, 3.4], hspace=0.04)
    a = top.subplots(1, 3, gridspec_kw=dict(width_ratios=[1.1, 1.0, 1.1]))
    a[1].remove()
    plot_stackup(a[0], sim)
    plot_3d(top.add_subplot(1, 3, 2, projection='3d'), sim)
    plot_info(a[2], sim, res)
    m = mid.subplots(1, nmid, squeeze=False)[0]
    plot_layout(m[0], sim)
    if show_J:
        pcs = []
        for ax, k in zip(m[1:], sim.layers):
            pcs.append(plot_current(ax, sim, res.I, k, col=drive, cbar=False))
            ax.set_title(f"|J| on M{k} — port {drive + 1} driven, {res.fI / 1e9:.2f} GHz")
        cb = mid.colorbar(pcs[-1], ax=list(m[1:]), fraction=0.025, pad=0.02)
        cb.set_label('|J| (dB rel. max)', color=INK2); cb.outline.set_edgecolor(MUTED)
    b = bot.subplots(1, nbot)
    if res is not None:
        plot_sparams(b[0], res, col=drive)
        plot_diagnostics(b[1], res)
        plot_kernels(b[2], sim)
        plot_timing(b[3], sim)
    else:
        plot_kernels(b[0], sim)
        plot_timing(b[1], sim)
    fig.suptitle(sim.title or 'mom25d', x=0.01, y=1.03, ha='left', fontsize=13, fontweight='bold', color=INK)
    fig.savefig(fname, dpi=115, bbox_inches='tight')
    plt.close(fig)
    return fname
