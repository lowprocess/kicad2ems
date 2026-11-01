"""PROTOTYPE, SET ASIDE (see HANDOVER.md): isotropic triangles need too many nodes in thin layers.

Prototype: the cross-section on an unstructured triangle mesh (P1 finite elements), graded from the copper's
corners and coarsened away from the signal; extrapolated from the mesh and the mesh with every triangle split in 4."""
import math
import numpy as np
from kicad2ems.lines import xsection as X
from kicad2ems.constants import EPS0

TOL = 1e-9


def _box(xs):
    cs = xs.conductors
    zs = [c.z0 for c in cs] + [c.z1 for c in cs]
    lo, hi = min(zs), max(zs)
    below = [p for p in xs.planes if p <= lo + 1e-12]
    above = [p for p in xs.planes if p >= hi - 1e-12]
    span_z = (max(above) if above else hi) - (min(below) if below else lo)
    fin = [v for c in cs for v in (c.x0, c.x1) if math.isfinite(v)]
    xa, xb = min(fin), max(fin)
    H = max(span_z, xb - xa, 1e-3)
    zb = max(below) if below else lo - X.REACH * H
    zt = min(above) if above else hi + X.REACH * H
    return xa - X.REACH * H, xb + X.REACH * H, zb, zt, H


def _union(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + TOL:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def mesh(xs, grow=1.3, fine=1 / 16, far=0.1, gap=0.25):
    import triangle
    xl, xr, zb, zt, H = _box(xs)
    clip = lambda v, a, b: min(max(v, a), b)
    hor, ver = {}, {}                                   # z -> x intervals; x -> z intervals
    def h_(z, a, b):
        if zb - TOL <= z <= zt + TOL and b > a:
            hor.setdefault(round(z, 9), []).append((clip(a, xl, xr), clip(b, xl, xr)))
    def v_(x, a, b):
        if xl - TOL <= x <= xr + TOL and b > a:
            ver.setdefault(round(x, 9), []).append((clip(a, zb, zt), clip(b, zb, zt)))
    h_(zb, xl, xr); h_(zt, xl, xr); v_(xl, zb, zt); v_(xr, zb, zt)
    for p in xs.planes:
        h_(p, xl, xr)
    for d in xs.dielectrics:
        a, b = clip(d.x0, xl, xr), clip(d.x1, xl, xr)
        for z in (d.z0, d.z1):
            if zb < z < zt:
                h_(z, a, b)
        for x in (d.x0, d.x1):
            if math.isfinite(x):
                v_(x, clip(d.z0, zb, zt), clip(d.z1, zb, zt))
    corners, holes = [], []
    for c in xs.conductors:
        a, b = clip(c.x0, xl, xr), clip(c.x1, xl, xr)
        h_(c.z0, a, b)
        if c.z1 > c.z0:
            h_(c.z1, a, b)
            for x in (c.x0, c.x1):
                if math.isfinite(x):
                    v_(x, c.z0, c.z1)
            holes.append((0.5 * (a + b), 0.5 * (c.z0 + c.z1)))
        for x in (c.x0, c.x1):
            if math.isfinite(x):
                corners += [(x, c.z0), (x, c.z1)]
    # the size: fine at the copper's corners (coarser the further a corner is from the signal), growing away
    sig = xs.signals
    sx = (min(c.x0 for c in sig), max(c.x1 for c in sig))
    sz = (min(c.z0 for c in sig), max(c.z1 for c in sig))
    feats = [c.x1 - c.x0 for c in xs.conductors if math.isfinite(c.x1 - c.x0)]
    feats += [c.z1 - c.z0 for c in xs.conductors] + [d.z1 - d.z0 for d in xs.dielectrics]
    zk = sorted(set(round(z, 9) for z in hor))
    feats += [b - a for a, b in zip(zk[:-1], zk[1:]) if sz[0] - 2 * H < a < sz[1] + 2 * H]
    h0 = fine * min(f for f in feats if f > 1e-9)
    C = np.array(corners)
    dsig = np.hypot(np.maximum(np.maximum(sx[0] - C[:, 0], C[:, 0] - sx[1]), 0),
                    np.maximum(np.maximum(sz[0] - C[:, 1], C[:, 1] - sz[1]), 0))
    hc = np.maximum(h0, far * dsig)
    hmax = H
    zl = np.array(sorted(hor))

    def size(px, pz):
        h = min(hmax, float(np.min(hc + (grow - 1) * np.hypot(C[:, 0] - px, C[:, 1] - pz))))
        dz = np.abs(zl - pz)
        dz = dz[dz > 1e-9]
        return max(min(h, gap * float(dz.min())), h0) if len(dz) else h
    # the segments, split where others meet them, sampled at the size
    pts, segs, index = [], [], {}
    def node(x, z):
        k = (round(x, 9), round(z, 9))
        if k not in index:
            index[k] = len(pts)
            pts.append((x, z))
        return index[k]
    def sample(p0, p1):
        (x0, z0), (x1, z1) = p0, p1
        L = math.hypot(x1 - x0, z1 - z0)
        ts, t = [0.0], 0.0
        while True:                                     # march from both ends towards the middle
            h = size(x0 + (x1 - x0) * t / L, z0 + (z1 - z0) * t / L) if L else 1
            if t + 1.5 * h >= L:
                break
            t += h
            ts.append(t)
        ts.append(L)
        # symmetric: the march from the far end too, keep the finer of the two at each place
        tb, t = [L], L
        while True:
            h = size(x0 + (x1 - x0) * t / L, z0 + (z1 - z0) * t / L)
            if t - 1.5 * h <= 0:
                break
            t -= h
            tb.append(t)
        tt = sorted(set(np.round(ts + tb, 12)))
        keep = [tt[0]]
        for v in tt[1:]:
            px, pz = x0 + (x1 - x0) * v / L, z0 + (z1 - z0) * v / L
            if v - keep[-1] >= 0.7 * size(px, pz) or v == L:
                keep.append(v)
        ids = [node(x0 + (x1 - x0) * v / L, z0 + (z1 - z0) * v / L) for v in keep]
        segs.extend(zip(ids[:-1], ids[1:]))
    for z, iv in hor.items():
        ends = {v for ab in iv for v in ab}                 # every piece's ends (a strip on a layer's surface)
        for a, b in _union(iv):
            cuts = {a, b} | {v for v in ends if a < v < b}
            cuts |= {x for x, vv in ver.items() if a < x < b and any(p - TOL <= z <= q + TOL for p, q in vv)}
            cuts = sorted(cuts)
            for u, w in zip(cuts[:-1], cuts[1:]):
                sample((u, z), (w, z))
    for x, iv in ver.items():
        ends = {v for ab in iv for v in ab}
        for a, b in _union(iv):
            cuts = {a, b} | {v for v in ends if a < v < b}
            cuts |= {z for z, hh in hor.items() if a < z < b and any(p - TOL <= x <= q + TOL for p, q in hh)}
            cuts = sorted(cuts)
            for u, w in zip(cuts[:-1], cuts[1:]):
                sample((x, u), (x, w))
    A = dict(vertices=np.array(pts), segments=np.array(segs))
    if holes:
        A['holes'] = np.array(holes)
    T = triangle.triangulate(A, 'pq30a%g' % (hmax * hmax))
    return T['vertices'], T['triangles']


def _split(P, tri):
    """Every triangle split in four at its edges' midpoints."""
    edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), axis=1)
    uniq, inv = np.unique(edges, axis=0, return_inverse=True)
    mid = len(P) + np.arange(len(uniq))
    P2 = np.vstack([P, 0.5 * (P[uniq[:, 0]] + P[uniq[:, 1]])])
    n = len(tri)
    m01, m12, m20 = mid[inv[:n]], mid[inv[n:2 * n]], mid[inv[2 * n:]]
    a, b, c = tri.T
    T2 = np.vstack([np.stack([a, m01, m20], 1), np.stack([m01, b, m12], 1), np.stack([m20, m12, c], 1),
                    np.stack([m01, m12, m20], 1)])
    return P2, T2


def _owners(xs, P, box):
    xl, xr, zb, zt = box
    x, z = P[:, 0], P[:, 1]
    own = np.full(len(P), -1)
    own[(np.abs(x - xl) < 1e-7) | (np.abs(x - xr) < 1e-7) | (np.abs(z - zb) < 1e-7) | (np.abs(z - zt) < 1e-7)] = 0
    for p in xs.planes:
        own[np.abs(z - p) < 1e-7] = 0
    k = 0
    for c in xs.conductors:
        m = (x >= c.x0 - 1e-7) & (x <= c.x1 + 1e-7) & (z >= c.z0 - 1e-7) & (z <= c.z1 + 1e-7)
        if c.ground:
            own[m] = 0
        else:
            k += 1
            own[m] = k
    return own


def _eps(xs, P, tri):
    cx, cz = P[tri].mean(1).T
    er, et = np.ones(len(tri)), np.zeros(len(tri))
    for d in xs.dielectrics:
        m = (cx > d.x0) & (cx < d.x1) & (cz > d.z0) & (cz < d.z1)
        er[m], et[m] = d.er, d.er * d.tand
    return er, et


def _stiffness(P, tri, eps):
    from scipy.sparse import coo_matrix
    p = P[tri]                                          # (nt, 3, 2)
    d = np.stack([p[:, 1] - p[:, 2], p[:, 2] - p[:, 0], p[:, 0] - p[:, 1]], 1)    # opposite edges
    area = 0.5 * np.abs(d[:, 0, 0] * d[:, 1, 1] - d[:, 0, 1] * d[:, 1, 0])
    K = np.einsum('tid,tjd->tij', d, d) / (4 * area)[:, None, None] * eps[:, None, None]
    r = np.repeat(tri, 3, axis=1).ravel()
    c = np.tile(tri, (1, 3)).ravel()
    return coo_matrix((K.ravel(), (r, c)), shape=(len(P), len(P))).tocsr()


def _solve_mesh(xs, P, tri, box):
    from scipy.sparse.linalg import splu
    own = _owners(xs, P, box)
    used = np.zeros(len(P), bool)
    used[tri.ravel()] = True
    er, et = _eps(xs, P, tri)
    n = int(own.max())
    free = np.flatnonzero((own < 0) & used)
    fixed = np.flatnonzero(own >= 0)
    V = np.zeros((len(P), n))
    for k in range(n):
        V[own == k + 1, k] = 1.0
    out = []
    for eps in (er, np.ones_like(er)):
        K = _stiffness(P, tri, eps)
        lu = splu(K[free][:, free].tocsc(), permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0,
                  options=dict(SymmetricMode=True))
        phi = V.copy()
        phi[free] = lu.solve(-(K[free][:, fixed] @ V[fixed]))
        Q = K @ phi
        Cm = np.array([[Q[own == i + 1, j].sum() for j in range(n)] for i in range(n)]) * EPS0
        out.append((0.5 * (Cm + Cm.T), phi))
    (C, phi), (Ca, _) = out
    G = EPS0 * (phi.T @ (_stiffness(P, tri, et) @ phi))
    return C, Ca, 0.5 * (G + G.T), int(used.sum())


def solve(xs, **kw):
    xl, xr, zb, zt, _ = _box(xs)
    P, tri = mesh(xs, **kw)
    c1 = _solve_mesh(xs, P, tri, (xl, xr, zb, zt))
    P2, T2 = _split(P, tri)
    c2 = _solve_mesh(xs, P2, T2, (xl, xr, zb, zt))
    return X.Result(*(2 * b - a for a, b in zip(c1[:3], c2[:3])), nodes=c2[3]), c1[3]
