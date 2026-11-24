"""Mode fields as images: sampling a mesh field on a pixel grid, colour maps,
and the surface-current arrows. No Qt here - the GUI and image export share it.
"""
import numpy as np
from matplotlib.tri import Triangulation

# colour ramps (RGB 0-255): blue - grey - red for signed V, grey - red for |V|,
# grey - blue for current
DIVERGING = [(29, 79, 143), (42, 120, 214), (240, 239, 236), (227, 73, 72), (143, 31, 31)]
SEQUENTIAL = [(240, 239, 236), (227, 73, 72), (143, 31, 31)]
CURRENT = [(240, 239, 236), (42, 120, 214), (29, 79, 143)]


def lut(stops, n=256):
    """An (n, 3) uint8 table interpolating the colour `stops`."""
    s = np.asarray(stops, float)
    t = np.linspace(0, len(s) - 1, n)
    i = np.minimum(t.astype(int), len(s) - 2)
    return (s[i] + (s[i + 1] - s[i]) * (t - i)[:, None]).astype(np.uint8)


class FieldSampler:
    """Samples nodal fields of one mesh on a pixel grid. sample() does the
    expensive part (which triangle holds each pixel) once per view; values()
    is then a cheap weighted sum, fast enough to animate."""

    def __init__(self, nodes, tris):
        self.nodes, self.tris = nodes, tris
        self.tri = Triangulation(nodes[:, 0], nodes[:, 1], tris)
        self.finder = self.tri.get_trifinder()
        self.grid = None
        # per-triangle gradient operator: grad v = sum_k G[t, k] v[tris[t, k]]
        P = nodes[tris]
        d1, d2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
        det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        e = np.stack([P[:, 2] - P[:, 1], P[:, 0] - P[:, 2], P[:, 1] - P[:, 0]], 1)
        self.G = np.stack([-e[..., 1], e[..., 0]], -1) / det[:, None, None]
        self.area = 0.5 * np.abs(det)

    def sample(self, x0, y0, x1, y1, w, h):
        """Set the grid: w x h pixels over the rectangle (x0, y0)-(x1, y1)."""
        xs = x0 + (np.arange(w) + 0.5) * (x1 - x0) / w
        ys = y0 + (np.arange(h) + 0.5) * (y1 - y0) / h
        X, Y = np.meshgrid(xs, ys)
        t = self.finder(X.ravel(), Y.ravel())
        inside = t >= 0
        tt = t[inside]
        P = self.nodes[self.tris[tt]]
        # barycentric weights of each inside pixel in its triangle
        d1, d2 = P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]
        q = np.column_stack([X.ravel()[inside], Y.ravel()[inside]]) - P[:, 0]
        det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        b1 = (q[:, 0] * d2[:, 1] - q[:, 1] * d2[:, 0]) / det
        b2 = (d1[:, 0] * q[:, 1] - d1[:, 1] * q[:, 0]) / det
        W = np.column_stack([1 - b1 - b2, b1, b2])
        self.grid = dict(shape=(h, w), rect=(x0, y0, x1, y1), inside=inside,
                         tri=tt, idx=self.tris[tt], W=W)

    def values(self, v):
        """The field `v` (per node) on the grid; NaN off the mesh."""
        g = self.grid
        out = np.full(g['inside'].shape, np.nan)
        out[g['inside']] = (v[g['idx']] * g['W']).sum(1)
        return out.reshape(g['shape'])

    def gradient(self, v):
        """grad v per triangle (units of v per mm), shape (triangles, 2)."""
        return np.einsum('tkd,tk->td', self.G, v[self.tris])

    def current(self, v):
        """|grad v| on the grid (proportional to the plane's surface current),
        averaged to the nodes first (area-weighted) so it shades smoothly
        instead of showing each triangle's constant gradient."""
        gr = self.gradient(v)
        n = len(self.nodes)
        acc = np.zeros((n, 2)); wsum = np.zeros(n)
        for k in range(3):
            np.add.at(acc, self.tris[:, k], gr * self.area[:, None])
            np.add.at(wsum, self.tris[:, k], self.area)
        mag = np.hypot(*(acc / np.maximum(wsum, 1e-30)[:, None]).T)
        return self.values(mag)


def colorize(vals, vmax, mode='signed', decades=0, alpha_outside=0):
    """RGBA uint8 image of `vals` (NaN: transparent). mode 'signed' (blue -
    red about 0), 'abs' (|V|) or 'current'. decades > 0: a log scale spanning
    that many decades below vmax."""
    stops = {'signed': DIVERGING, 'abs': SEQUENTIAL, 'current': CURRENT}[mode]
    table = lut(stops)
    a = np.abs(np.nan_to_num(vals)) / max(vmax, 1e-30)
    if decades > 0:
        a = np.clip((np.log10(np.maximum(a, 1e-30)) + decades) / decades, 0, 1)
    a = np.clip(a, 0, 1)
    if mode == 'signed':
        t = 0.5 + 0.5 * np.sign(np.nan_to_num(vals)) * a
    else:
        t = a
    img = np.empty(vals.shape + (4,), np.uint8)
    img[..., :3] = table[(t * 255).astype(int)]
    img[..., 3] = np.where(np.isnan(vals), alpha_outside, 255)
    return img


def arrows(sampler, v, spacing_px, cap_quantile=0.9):
    """Current arrows on a coarse grid of the current view: [(x, y, ux, uy,
    length 0..1)] in mm, from grad V per triangle. The plane's surface current
    is proportional to grad V (the other plane carries it back)."""
    g = sampler.grid
    h, w = g['shape']
    x0, y0, x1, y1 = g['rect']
    step = max(1, int(spacing_px))
    gy, gx = np.mgrid[step // 2:h:step, step // 2:w:step]
    flat = (gy * w + gx).ravel()
    pos = np.full(h * w, -1)
    pos[np.flatnonzero(g['inside'])] = np.arange(g['inside'].sum())
    k = pos[flat]
    ok = k >= 0
    if not ok.any():
        return np.zeros((0, 5))
    grad = sampler.gradient(v)[g['tri'][k[ok]]]
    mag = np.hypot(grad[:, 0], grad[:, 1])
    # the scale from where the current is significant, not the whole view (a
    # local mode leaves most of it near zero)
    sig = mag[mag > 0.02 * mag.max()] if mag.max() > 0 else mag
    cap = np.quantile(sig, cap_quantile) if len(sig) else 1.0
    keep = mag > 0.12 * cap
    xs = x0 + (gx.ravel()[ok][keep] + 0.5) * (x1 - x0) / w
    ys = y0 + (gy.ravel()[ok][keep] + 0.5) * (y1 - y0) / h
    u = grad[keep] / mag[keep, None]
    L = np.clip(mag[keep] / max(cap, 1e-30), 0.3, 1.0)
    return np.column_stack([xs, ys, u, L])
