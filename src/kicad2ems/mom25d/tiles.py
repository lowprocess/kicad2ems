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


class _CompiledBLR:
    """The compiled core's BLRFactor (core/mom25d_core.cpp): solve, memory,
    stats; the tiles in elimination order, the pairs compressed where their
    clusters are apart (gap > eta x the larger's diameter), the near correction
    and extra blocks as CSR, the via rows and columns, the equilibration. Its
    BLAS calls run on OpenMP's threads, so SciPy's are held at one while it
    factors and solves."""

    on_gpu, device_bytes = False, 0

    def __init__(self, fo, tiles, boxes, dZx, dsc, tol, eta, double=False):
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
                          C, c(dsc, float), float(tol), 0)
        self.stats = self.f.stats()
        self.memory = self.nbytes = self.stats['bytes']

    def __call__(self, b):
        with _one_blas_thread():
            return self.f.solve(np.ascontiguousarray(b, complex))
