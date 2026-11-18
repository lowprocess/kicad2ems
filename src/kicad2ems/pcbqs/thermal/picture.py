"""Pictures of thermal results: the temperature of each layer, and the heating
from cold."""
import numpy as np


def _axes(ax, p, title):
    x0, y0, x1, y1 = p.outline.bounds
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)                 # the board's frame has y down the page
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=9)
    ax.tick_params(labelsize=7)


def _draw(ax, res, layer, vmin, vmax, cmap='inferno'):
    ny, nx = res.T[layer].shape
    ext = (res.x0, res.x0 + nx * res.size, res.y0 + ny * res.size, res.y0)
    return ax.imshow(res.T[layer], extent=ext, origin='upper', vmin=vmin,
                     vmax=vmax, cmap=cmap, interpolation='nearest')


def _copper(ax, p, layer):
    g = p.layers[layer].copper
    for q in getattr(g, 'geoms', [g] if g is not None else []):
        if q.geom_type == 'Polygon':
            for ring in [q.exterior, *q.interiors]:
                c = np.asarray(ring.coords)
                ax.plot(c[:, 0], c[:, 1], color='white', lw=0.3, alpha=0.5)


def _labels(ax, res, layer):
    seen = set()
    for s in res.problem.sources:
        if s.layer == layer and s.name not in seen:
            seen.add(s.name)
            c = s.geom.centroid
            ax.annotate(f"{s.name} {res.parts[s.name]['board_C']:.0f}",
                        (c.x, c.y), color='white', fontsize=6, ha='center',
                        va='center')


def picture(res, path, dpi=150):
    """Each layer's temperature (top layer first)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    p = res.problem
    L = len(p.layers)
    x0, y0, x1, y1 = p.outline.bounds
    w = 4.2
    hgt = w * (y1 - y0) / (x1 - x0) + 0.6
    fig, axs = plt.subplots(1, L, figsize=(w * L + 0.8, hgt), squeeze=False,
                            constrained_layout=True)
    vmin, vmax = float(np.nanmin(res.T)), float(np.nanmax(res.T))
    for ax, l in zip(axs[0], reversed(range(L))):
        im = _draw(ax, res, l, vmin, vmax)
        _copper(ax, p, l)
        _labels(ax, res, l)
        _axes(ax, p, f"{p.layers[l].name}: up to {np.nanmax(res.T[l]):.1f} C")
    fig.colorbar(im, ax=axs[0].tolist(), shrink=0.85, label='C')
    fig.savefig(path, dpi=dpi)
    plt.close(fig)


def heating_picture(transient, path, most=8):
    """The heating from cold (grid.heating's): the hottest copper and the `most`
    hottest parts against time."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t = np.asarray(transient['times_s'])
    fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
    ax.plot(t[1:], transient['hottest_C'][1:], 'k--', lw=1.2,
            label='hottest copper')
    top = (sorted(transient['parts'],
                  key=lambda k: -transient['parts'][k][-1])[:most])
    for name in top:
        ax.plot(t[1:], transient['parts'][name][1:], lw=1.2, label=name)
    ax.set_xscale('log')
    ax.set(xlabel='time from cold, s', ylabel='C', title='Heating from cold')
    ax.grid(alpha=0.3, which='both')
    ax.legend(fontsize=8, ncol=2)
    fig.savefig(path, dpi=110)
    plt.close(fig)
