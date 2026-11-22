"""
mom25d.tiles - the block low-rank solver's tiles (clusters of unknowns) and the compiled
core's factor (fast.py's, split out: R-F3)
"""
import numpy as np

from .threads import _one_blas_thread


# ---- the BLR's tiles: a cluster tree ----------------------------------------
class Cluster:
    __slots__ = ('idx', 'lo', 'hi', 'kids')

    def __init__(self, idx, pts, leaf):
        p = pts[idx]
        self.idx, self.lo, self.hi, self.kids = idx, p.min(0), p.max(0), []
        if len(idx) > leaf:  # bisect along the longest side
            o, h = (np.argsort(p[:, np.argmax(self.hi - self.lo)],
                               kind='stable'), len(idx) // 2)
            self.kids = [
                Cluster(idx[o[:h]], pts, leaf),
                Cluster(idx[o[h:]], pts, leaf)
            ]

    @property
    def diam(self):
        return np.linalg.norm(self.hi - self.lo)


def cluster_tiles(pts, tile):
    """The block low-rank solver's tiles: geometric bisection (Cluster) into
    leaves of <= tile points; their index arrays and bounding boxes."""
    def leaves(c):
        return [c] if not c.kids else leaves(c.kids[0]) + leaves(c.kids[1])

    tiles = leaves(Cluster(np.arange(len(pts)), pts, tile))
    return [c.idx for c in tiles], [(c.lo, c.hi) for c in tiles]


def components(sim, pts):
    """The connected piece of copper of each unknown (cluster_tiles's
    groups): the mesh's triangles joined through their RWG edges, an edge its
    triangles' piece, the other unknowns (via chains, holes, slots, sides:
    pts past the edges) their nearest edge's."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    r = sim.rwg
    Nt, Ne = len(r.tris), sim.Ne
    g = coo_matrix((np.ones(Ne), (r.tp, r.tm)), shape=(Nt, Nt))
    _, tri = connected_components(g, directed=False)
    comp = np.empty(len(pts), np.int64)
    comp[:Ne] = tri[r.tp]
    if len(pts) > Ne:
        from scipy.spatial import cKDTree
        _, near = cKDTree(pts[:Ne]).query(pts[Ne:])
        comp[Ne:] = comp[:Ne][near]
    return comp


def cluster_groups(pts, comp, tile):
    """cluster_tiles with each group (comp: a connected piece of copper, an
    unknown's group) bisected on its own, so that no tile spans two pieces
    (a tile of several parallel traces is near every tile of each); groups
    smaller than a tile packed together in the order of their centres (a
    group tree: bisection of the groups' centres), as cluster_tiles's
    leaves. Measured (2026-11-22, the Thor's In7 group, 24.9k unknowns, 5
    GHz, every off-diagonal tile compressed): no better than the geometric
    tiles: the factor 7 % of dense complex64 against 4 %, 3.6 GMRES
    iterations against 5.3, the operator at 1e-8 27 % of complex128 dense
    against 18 %, |dS| 6.4e-6 against 7.5e-7; sim.blr_tiles = 'copper'
    takes it."""
    groups = [np.flatnonzero(comp == c) for c in np.unique(comp)]
    cent = np.array([pts[g].mean(0) for g in groups])
    # the groups in a geometric order: bisect their centres
    order = Cluster(np.arange(len(groups)), cent, 1)

    def leaves(c):
        return [c] if not c.kids else leaves(c.kids[0]) + leaves(c.kids[1])
    seq = [int(c.idx[0]) for c in leaves(order)]
    tiles, small = [], []
    for gi in seq:
        g = groups[gi]
        if len(g) >= tile // 2:
            tiles += [c.idx for c in leaves(Cluster(g, pts, tile))]
        else:
            if sum(len(x) for x in small) + len(g) > tile:
                tiles.append(np.concatenate(small))
                small = []
            small.append(g)
    if small:
        tiles.append(np.concatenate(small))
    boxes = [(pts[t].min(0), pts[t].max(0)) for t in tiles]
    return tiles, boxes


class _CompiledBLR:
    """The compiled core's BLRFactor (core/mom25d_core.cpp): solve, memory,
    stats; the tiles in elimination order, the pairs compressed where their
    clusters are apart (gap > eta x the larger's diameter), the near correction
    and extra blocks as CSR, the via rows and columns, the equilibration. Its
    BLAS calls run on OpenMP's threads, so SciPy's are held at one while it
    factors and solves."""

    on_gpu, device_bytes = False, 0

    def __init__(self, fo, tiles, boxes, dZx, dsc, tol, eta, double=False,
                 factor=True, max_bytes=0.0):
        lo, hi = (np.array([b[0]
                            for b in boxes]), np.array([b[1] for b in boxes]))
        gap = np.linalg.norm(
            np.maximum(
                0, np.maximum(lo[:, None] - hi[None], lo[None] - hi[:, None])),
            axis=2)
        diam = np.linalg.norm(hi - lo, axis=1)
        far = ((gap > eta * np.maximum(diam[:, None], diam[None]))
               & ~np.eye(len(tiles), dtype=bool))
        order = np.concatenate(tiles).astype(np.int32)
        start = np.r_[0, np.cumsum([len(t) for t in tiles])].astype(np.int32)
        sim, c = fo.sim, np.ascontiguousarray
        B = c(fo.B, complex) if sim.Nv else np.zeros((sim.Ne, 0), complex)
        C = c(fo.C, complex) if sim.Nv else np.zeros((0, 0), complex)
        with _one_blas_thread():
            # (double: low frequency, solve_blr)
            make = fo.core.BLRFactor128 if double else fo.core.BLRFactor
            self.f = make(order, start, far.astype(np.uint8), fo.g, fo.tb, fo.w,
                          dZx.indptr.astype(np.int64),
                          dZx.indices.astype(np.int32), c(dZx.data, complex), B,
                          C, c(dsc, float), float(tol), 0, bool(factor),
                          float(max_bytes))
        self.stats = self.f.stats()
        self.memory = self.nbytes = self.stats['bytes']
        self.over = bool(self.stats.get('over', False))  # (past max_bytes)

    def __call__(self, b):
        with _one_blas_thread():
            return self.f.solve(np.ascontiguousarray(b, complex))

    def matvec(self, X):
        """The tiles as assembled times X (factor False: the matrix itself,
        equilibrated, at the compression's tolerance)."""
        with _one_blas_thread():
            return self.f.matvec(np.ascontiguousarray(X, complex))
