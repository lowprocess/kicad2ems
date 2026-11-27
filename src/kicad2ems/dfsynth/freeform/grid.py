"""
The pixel grid a free-form filter is drawn on, and its moment-method matrix.

A box of nx x ny square pixels between two feed lines, each pixel copper or
not. mom25d meshes the whole box as copper once (a structured grid, every
pixel the same cells), and its matrix Z at a frequency is filled once: a
pattern's own matrix is that Z without the rows and columns of the edges the
pattern does not have (a Galerkin entry is its two basis functions' alone, so
this is the pattern's mom25d model on that mesh, not an approximation of it).
The ports are lumped, in a via to the ground plane at each feed's outer end.

    grid = PixelGrid(sub, nx=24, ny=24, pitch=0.445, feed_w=4)
    Z = grid.matrix(5e9)                    # (N, N) complex, the full box
    S = grid.sparams(mask, [4e9, 5e9])      # a pattern solved from scratch
"""
import numpy as np


class PixelGrid:
    """sub: a dfsynth Substrate. pitch (mm): a pixel's side. feed_w, feed_l:
    the feeds' width and length in pixels. rows: the first pixel row of the
    left and the right feed (default: both centred). cells: mesh cells across
    a pixel (1: two triangles a pixel)."""

    def __init__(self, sub, nx, ny, pitch, feed_w, feed_l=3, rows=None,
                 cells=1, via_r=0.15):
        from kicad2ems.mom25d.multilayer import Layer, LayerStack
        from kicad2ems.mom25d.sim import Simulation, Metal, Via, ViaPort
        self.sub, self.nx, self.ny, self.pitch = sub, int(nx), int(ny), pitch
        self.feed_w, self.feed_l, self.cells = int(feed_w), int(feed_l), cells
        j0 = (self.ny - self.feed_w) // 2
        self.rows = (j0, j0) if rows is None else tuple(int(r) for r in rows)
        if (min(self.rows) < 0
                or max(self.rows) + self.feed_w > self.ny):
            raise ValueError("a feed is outside the box's rows")
        p, nf = pitch * 1e-3, self.feed_l
        rects = [(i * p, j * p, (i + 1) * p, (j + 1) * p)
                 for i in range(nf, nf + self.nx) for j in range(self.ny)]
        vias = []
        for side, r0 in enumerate(self.rows):
            i0 = 0 if side == 0 else nf + self.nx
            rects += [(i * p, j * p, (i + 1) * p, (j + 1) * p)
                      for i in range(i0, i0 + nf)
                      for j in range(r0, r0 + self.feed_w)]
            # the port: one pixel in from the feed's outer end, on the grid
            # node nearest its centre line
            xv = p if side == 0 else (2 * nf + self.nx - 1) * p
            vias.append(Via(xv, (r0 + self.feed_w // 2) * p, 0, 1,
                            via_r * 1e-3, ring=False, name=f'P{side + 1}'))
        metal = None
        if sub.sigma and np.isfinite(sub.sigma):
            # (a sheet: mom25d's copper here has no thickness but its loss's)
            metal = Metal(sub.sigma, max(sub.t, 1e-3) * 1e-3)
        self.sim = sim = Simulation(
            LayerStack([Layer(sub.h * 1e-3, sub.er, sub.tand)]), {1: rects},
            [ViaPort(v, 50.0, name=v.name) for v in vias], dmax=p / cells,
            mesher='tensor', ncross=cells, grade=0.0, metal=metal,
            verbose=False, gpu='off', precision='double', solver='direct')
        sim.interp = 'off'
        r = sim.rwg
        self.Ne, self.N = sim.Ne, sim.Ne + sim.Nv
        self.ports = np.array([sim.Ne + sim._port_chain(q)
                               for q in sim.ports])
        self.sign = np.array([q.sign for q in sim.ports])
        # each triangle's pixel (i * ny + j; -1: a feed's, always copper)
        i = np.floor(r.cent[:, 0] / p + 1e-9).astype(int) - nf
        j = np.floor(r.cent[:, 1] / p + 1e-9).astype(int)
        box = (i >= 0) & (i < self.nx)
        self.tri_pixel = np.where(box, i * self.ny + j, -1)
        # each unknown's two pixels (the chains': the feeds')
        self.pa = np.full(self.N, -1)
        self.pb = np.full(self.N, -1)
        self.pa[:self.Ne] = self.tri_pixel[r.tp]
        self.pb[:self.Ne] = self.tri_pixel[r.tm]
        self._z = {}

    # ---- patterns ----------------------------------------------------------
    @property
    def shape(self):
        return self.nx, self.ny

    def active(self, mask):
        """The unknowns a pattern has (mask: (nx, ny) bool, [i, j] the pixel
        of column i, row j): the edges whose two triangles are copper."""
        on = np.append(np.asarray(mask, bool).ravel(), True)   # [-1]: feeds
        return on[self.pa] & on[self.pb]

    def window(self, side):
        """The box's pixels a feed runs into (side 0: left, 1: right)."""
        i = 0 if side == 0 else self.nx - 1
        return [(i, j) for j in range(self.rows[side],
                                      self.rows[side] + self.feed_w)]

    # ---- the matrix --------------------------------------------------------
    def matrix(self, f, keep=False):
        """The full box's Z at f (Hz), (N, N) complex; keep: held for the
        next call."""
        if f in self._z:
            return self._z[f]
        Z = self.sim.fast.dense(float(f))
        if keep:
            self._z[f] = Z
        return Z

    def port_y(self, Zinv_pp):
        """The ports' Y (2 x 2) from the ports' block of a pattern's inverse
        matrix (the EMF in a port's via chain, its current the chain's)."""
        return self.sign[:, None] * Zinv_pp * self.sign[None, :]

    def sparams(self, mask, freqs, z0=50.0):
        """S (nf, 2, 2) of a pattern, each frequency solved from scratch."""
        act = self.active(mask)
        idx = np.flatnonzero(act)
        pp = np.searchsorted(idx, self.ports)
        E = np.zeros((len(idx), 2), complex)
        E[pp, [0, 1]] = 1.0
        out = []
        for f in np.atleast_1d(freqs):
            Z = self.matrix(f)
            X = np.linalg.solve(Z[np.ix_(idx, idx)], E)
            out.append(y_to_s(self.port_y(X[pp]), z0))
        return np.array(out)

    # ---- symmetry ----------------------------------------------------------
    def systems(self, symmetry=None):
        """The unknowns as the search's linear systems: one, all of them
        (symmetry None / 'none'); or, where the mesh has the symmetry
        ('mirror': in the plane between the ports, 'rotate': a half turn
        about the box's centre), two of half the size: the currents that
        are their own image and those that are its negative, which a
        pattern with the symmetry does not couple. A mesh without the
        symmetry (an odd number of cells along the box): the one system."""
        full = [System(self, None, self.pa, self.pb, self.ports, self.sign)]
        if symmetry in (None, 'none'):
            return full
        pairs = self._images(symmetry)
        if pairs is None:
            return full
        perm, sg = pairs
        return [System.half(self, perm, sg, parity) for parity in (1, -1)]

    def _images(self, symmetry):
        """(perm, sign): each unknown's image under the symmetry is sign x
        the unknown perm (None: the mesh does not have it)."""
        r, p = self.sim.rwg, self.pitch * 1e-3
        xc = (2 * self.feed_l + self.nx) * p / 2
        yc = self.ny * p / 2
        flip_y = symmetry == 'rotate'
        mid = r.mid.copy()
        mid[:, 0] = 2 * xc - mid[:, 0]
        nrm = r.nrm * np.array([-1.0, 1.0])
        if flip_y:
            mid[:, 1] = 2 * yc - mid[:, 1]
            nrm = -r.nrm
        key = lambda a: (int(round(a[0] / p * 4)), int(round(a[1] / p * 4)))
        where = {key(m): e for e, m in enumerate(r.mid)}
        perm = np.array([where.get(key(m), -1) for m in mid])
        if (perm < 0).any() or len(set(perm)) != self.Ne:
            return None
        sg = np.sign((nrm * r.nrm[perm]).sum(1))
        if (np.abs((nrm * r.nrm[perm]).sum(1)) < 0.99).any():
            return None
        # the image's triangles are the triangles' images (the diagonals)
        tri = {key(c): t for t, c in enumerate(r.cent)}
        cm = r.cent.copy()
        cm[:, 0] = 2 * xc - cm[:, 0]
        if flip_y:
            cm[:, 1] = 2 * yc - cm[:, 1]
        if any(key(c) not in tri for c in cm):
            return None
        # the ports' chains: each other's, the same way up
        ports = self.ports
        return (np.r_[perm, ports[::-1]],
                np.r_[sg, np.ones(len(ports))])

    # ---- geometry ----------------------------------------------------------
    def size(self):
        """(width, height) of the box, mm."""
        return self.nx * self.pitch, self.ny * self.pitch


def y_to_s(Y, z0=50.0):
    """S of Y (..., 2, 2), the ports' currents into the network."""
    a, b, c, d = (z0 * Y[..., 0, 0], z0 * Y[..., 0, 1], z0 * Y[..., 1, 0],
                  z0 * Y[..., 1, 1])
    # (1 - zY)(1 + zY)^-1, the 2 x 2 inverse written out
    det = (1 + a) * (1 + d) - b * c
    S = np.empty(np.shape(a) + (2, 2), complex)
    S[..., 0, 0] = ((1 - a) * (1 + d) + b * c) / det
    S[..., 0, 1] = -2 * b / det
    S[..., 1, 0] = -2 * c / det
    S[..., 1, 1] = ((1 + a) * (1 - d) + b * c) / det
    return S


class System:
    """Some of a grid's currents as a linear system of their own: T (N x n,
    sparse; None: all of them, as they are), each one's two pixels (pa, pb:
    what a pattern needs for it to be there), the ports' unknowns in it and
    their signs. reduce(Z): the grid's matrix in it."""

    def __init__(self, grid, T, pa, pb, ports, sign):
        self.grid, self.T = grid, T
        self.pa, self.pb = np.asarray(pa), np.asarray(pb)
        self.ports, self.sign = np.asarray(ports), np.asarray(sign, float)
        self.n = len(self.pa)

    @classmethod
    def half(cls, grid, perm, sg, parity):
        """The currents equal to parity x their image: for each pair of
        unknowns (e, its image) the sum or difference over root 2, and of
        those that are their own image the ones with that sign."""
        from scipy import sparse
        N = grid.N
        rows, cols, vals, rep = [], [], [], []
        for e in range(N):
            m = perm[e]
            if m > e:
                rows += [e, m]
                cols += [len(rep)] * 2
                vals += [np.sqrt(0.5), parity * sg[e] * np.sqrt(0.5)]
                rep.append(e)
            elif m == e and sg[e] == parity:
                rows.append(e)
                cols.append(len(rep))
                vals.append(1.0)
                rep.append(e)
        rep = np.array(rep)
        T = sparse.csr_matrix((vals, (rows, cols)), shape=(N, len(rep)))
        # the two ports are one pair: its unknown, the first port's sign
        port = np.flatnonzero(rep == grid.ports.min())
        return cls(grid, T, grid.pa[rep], grid.pb[rep], port,
                   grid.sign[:1])

    def reduce(self, Z):
        if self.T is None:
            return Z
        return np.asarray((self.T.T @ (self.T.T @ Z).T).T)

    def active(self, mask):
        on = np.append(np.asarray(mask, bool).ravel(), True)
        return on[self.pa] & on[self.pb]


def combine(ys):
    """(..., 2, 2) port admittance from the systems': one system's own, or
    the even and odd halves' (Y11 = Y22 = (ye + yo) / 2, Y12 = (ye - yo) /
    2)."""
    if len(ys) == 1:
        return ys[0]
    ye, yo = ys[0][..., 0, 0], ys[1][..., 0, 0]
    Y = np.empty(ye.shape + (2, 2), complex)
    Y[..., 0, 0] = Y[..., 1, 1] = (ye + yo) / 2
    Y[..., 0, 1] = Y[..., 1, 0] = (ye - yo) / 2
    return Y
