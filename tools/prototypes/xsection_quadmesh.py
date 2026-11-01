"""PROTOTYPE, NOT WORKING YET (see HANDOVER.md): right for single lines, wrong with several conductors or thick
copper: the hanging-node constraints fail the patch test.

Prototype: the cross-section on an adaptive rectangle mesh. Cells start on the key lines (copper edges, layers,
planes, the box) and are halved in x or z only where they are larger than a size that grows away from the copper's
corners (coarser for copper far from the signal), so long thin cells fill the layers away from the copper. Bilinear
elements; a node hanging on a coarser neighbour's side is held to the side's ends by linear interpolation."""
import math
import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, identity
from kicad2ems.lines import xsection as X
from kicad2ems.constants import EPS0

R = 9                                                      # decimals: node identity


def _setup(xs, grow, fine, far):
    x, z = X._grid.__wrapped__(xs) if hasattr(X._grid, '__wrapped__') else (None, None)
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
    xl, xr = xa - X.REACH * H, xb + X.REACH * H
    xk = {xl, xr} | {v for v in fin if xl < v < xr}
    zk = {zb, zt} | {v for v in zs if zb <= v <= zt} | {p for p in xs.planes if zb < p < zt}
    for d in xs.dielectrics:
        xk |= {v for v in (d.x0, d.x1) if xl < v < xr}
        zk |= {min(max(v, zb), zt) for v in (d.z0, d.z1)}
    xk, zk = np.unique(np.round(sorted(xk), R)), np.unique(np.round(sorted(zk), R))
    sig = xs.signals
    sx = (min(c.x0 for c in sig), max(c.x1 for c in sig))
    sz = (min(c.z0 for c in sig), max(c.z1 for c in sig))
    corners = np.array([(v, w) for c in cs for v in (c.x0, c.x1) if math.isfinite(v) for w in (c.z0, c.z1)])
    feats = [c.x1 - c.x0 for c in cs if math.isfinite(c.x1 - c.x0)] + [c.z1 - c.z0 for c in cs]
    feats += [d.z1 - d.z0 for d in xs.dielectrics]
    near_z = zk[(zk > sz[0] - 2 * H) & (zk < sz[1] + 2 * H)]
    feats += list(np.diff(near_z))
    for a in cs:                                           # gaps side by side
        for b in cs:
            if a is not b and a.z0 <= b.z1 + 1e-9 and b.z0 <= a.z1 + 1e-9 and b.x0 >= a.x1:
                feats.append(b.x0 - a.x1)
    h0 = fine * min(f for f in feats if f > 1e-9)
    dsig = np.hypot(np.maximum(np.maximum(sx[0] - corners[:, 0], corners[:, 0] - sx[1]), 0),
                    np.maximum(np.maximum(sz[0] - corners[:, 1], corners[:, 1] - sz[1]), 0))
    hc = np.maximum(h0, far * dsig)
    return (xl, xr, zb, zt), xk, zk, corners, hc, H


def cells(xs, grow=1.3, fine=1 / 16, far=0.1):
    """The mesh's cells (n, 4): x0, x1, z0, z1."""
    box, xk, zk, C, hc, H = _setup(xs, grow, fine, far)
    X0, Z0 = np.meshgrid(xk[:-1], zk[:-1], indexing='ij')
    X1, Z1 = np.meshgrid(xk[1:], zk[1:], indexing='ij')
    cell = np.stack([X0.ravel(), X1.ravel(), Z0.ravel(), Z1.ravel()], 1)
    done = []
    g = grow - 1
    while len(cell):
        # the size a cell may have: from the nearest corner (rectangle-to-point distance), capped by H
        dx = np.maximum(np.maximum(cell[:, 0:1] - C[None, :, 0], C[None, :, 0] - cell[:, 1:2]), 0)
        dz = np.maximum(np.maximum(cell[:, 2:3] - C[None, :, 1], C[None, :, 1] - cell[:, 3:4]), 0)
        h = np.minimum((hc[None, :] + g * np.hypot(dx, dz)).min(1), H)
        w, t = cell[:, 1] - cell[:, 0], cell[:, 3] - cell[:, 2]
        sx, sz = w > 1.5 * h, t > 1.5 * h
        done.append(cell[~(sx | sz)])
        out = []
        c = cell[sx & ~sz]
        m = 0.5 * (c[:, 0] + c[:, 1])
        out += [np.stack([c[:, 0], m, c[:, 2], c[:, 3]], 1), np.stack([m, c[:, 1], c[:, 2], c[:, 3]], 1)]
        c = cell[sz & ~sx]
        m = 0.5 * (c[:, 2] + c[:, 3])
        out += [np.stack([c[:, 0], c[:, 1], c[:, 2], m], 1), np.stack([c[:, 0], c[:, 1], m, c[:, 3]], 1)]
        c = cell[sx & sz]
        mx, mz = 0.5 * (c[:, 0] + c[:, 1]), 0.5 * (c[:, 2] + c[:, 3])
        out += [np.stack(v, 1) for v in ((c[:, 0], mx, c[:, 2], mz), (mx, c[:, 1], c[:, 2], mz),
                                          (c[:, 0], mx, mz, c[:, 3]), (mx, c[:, 1], mz, c[:, 3]))]
        cell = np.concatenate(out) if out else np.zeros((0, 4))
    return box, np.concatenate(done)


def split(cell):
    mx, mz = 0.5 * (cell[:, 0] + cell[:, 1]), 0.5 * (cell[:, 2] + cell[:, 3])
    c = cell
    return np.concatenate([np.stack(v, 1) for v in ((c[:, 0], mx, c[:, 2], mz), (mx, c[:, 1], c[:, 2], mz),
                                                     (c[:, 0], mx, mz, c[:, 3]), (mx, c[:, 1], mz, c[:, 3]))])


def _inside_copper(xs, cell):
    """Cells inside thick copper: no field, left out."""
    cx, cz = 0.5 * (cell[:, 0] + cell[:, 1]), 0.5 * (cell[:, 2] + cell[:, 3])
    m = np.zeros(len(cell), bool)
    for c in xs.conductors:
        if c.z1 > c.z0:
            m |= (cx > c.x0) & (cx < c.x1) & (cz > c.z0) & (cz < c.z1)
    return m


def _nodes(cell):
    """Node coordinates and each cell's 4 node ids (counter-clockwise from bottom left)."""
    q = np.stack([cell[:, [0, 2]], cell[:, [1, 2]], cell[:, [1, 3]], cell[:, [0, 3]]], 1)   # (n, 4, 2)
    key = np.round(q.reshape(-1, 2), R)
    P, inv = np.unique(key, axis=0, return_inverse=True)
    return P, inv.reshape(-1, 4)


def _constraints(P, cell, ids, fixed=None):
    """M (n x n): each hanging node as the interpolation of the ends of the coarser side it lies on; identity for
    the others."""
    rows, cols, vals = [], [], []
    hanging = np.zeros(len(P), bool)
    for axis in (0, 1):                                    # sides along z (at an x), then along x (at a z)
        o = 1 - axis
        if axis == 0:                                      # vertical sides: x, z0, z1, end node ids
            sides = [(cell[:, 0], cell[:, 2], cell[:, 3], ids[:, 0], ids[:, 3]),
                     (cell[:, 1], cell[:, 2], cell[:, 3], ids[:, 1], ids[:, 2])]
        else:
            sides = [(cell[:, 2], cell[:, 0], cell[:, 1], ids[:, 0], ids[:, 1]),
                     (cell[:, 3], cell[:, 0], cell[:, 1], ids[:, 3], ids[:, 2])]
        line = np.concatenate([np.round(s[0], R) for s in sides])
        a = np.concatenate([s[1] for s in sides])
        b = np.concatenate([s[2] for s in sides])
        ia = np.concatenate([s[3] for s in sides])
        ib = np.concatenate([s[4] for s in sides])
        pl, pv = np.round(P[:, axis], R), P[:, o]
        order = np.lexsort((pv, pl))
        pl_s, pv_s = pl[order], pv[order]
        # per side: the nodes on its line strictly between its ends
        lo = np.searchsorted(pl_s, line, 'left')
        hi = np.searchsorted(pl_s, line, 'right')
        for k in np.flatnonzero(hi - lo > 2):              # a side with more than its two ends on its line
            seg = slice(lo[k], hi[k])
            v = pv_s[seg]
            inside = (v > a[k] + 1e-12) & (v < b[k] - 1e-12)
            if not inside.any():
                continue
            nid = order[seg][inside]
            t = (v[inside] - a[k]) / (b[k] - a[k])
            if fixed is not None:                          # fixed nodes keep their own value
                f = ~fixed[nid]
                nid, t = nid[f], t[f]
                if not len(nid):
                    continue
            rows += [nid, nid]
            cols += [np.full(len(nid), ia[k]), np.full(len(nid), ib[k])]
            vals += [1 - t, t]
            hanging[nid] = True
    n = len(P)
    if rows:
        rows, cols, vals = np.concatenate(rows), np.concatenate(cols), np.concatenate(vals)
        # a node on two coarser sides (collinear, nested): keep the longest side's constraint only
        W = coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsr()
        W.sum_duplicates()
        # rows with more than 2 entries come from nested sides: normalise (both interpolate the same line)
        s = np.asarray(W.sum(1)).ravel()
        s[s == 0] = 1
        W = csr_matrix(W.multiply(1 / s[:, None]))
    else:
        W = csr_matrix((n, n))
    keep = identity(n, format='csr')
    D = csr_matrix((~hanging).astype(float)[:, None] * 0 + 0, shape=(n, 1))  # placeholder
    M = csr_matrix(identity(n).multiply((~hanging).astype(float)[:, None])) + W
    for _ in range(40):                                    # chains of hanging nodes: to the independent ones
        M2 = M @ M
        if (M2 != M).nnz == 0:
            break
        M = M2
    return M, hanging


def _stiffness(P, cell, ids, eps):
    w, h = cell[:, 1] - cell[:, 0], cell[:, 3] - cell[:, 2]
    A = np.array([[2, -2, -1, 1], [-2, 2, 1, -1], [-1, 1, 2, -2], [1, -1, -2, 2]], float)
    B = np.array([[2, 1, -1, -2], [1, 2, -2, -1], [-1, -2, 2, 1], [-2, -1, 1, 2]], float)
    K = (eps * h / (6 * w))[:, None, None] * A + (eps * w / (6 * h))[:, None, None] * B
    r = np.repeat(ids, 4, axis=1).ravel()
    c = np.tile(ids, (1, 4)).ravel()
    return coo_matrix((K.ravel(), (r, c)), shape=(len(P), len(P))).tocsr()


def _owners(xs, P, box):
    xl, xr, zb, zt = box
    x, z = P[:, 0], P[:, 1]
    tol = 1e-9
    own = np.full(len(P), -1)
    own[(np.abs(x - xl) < tol) | (np.abs(x - xr) < tol) | (np.abs(z - zb) < tol) | (np.abs(z - zt) < tol)] = 0
    for p in xs.planes:
        own[np.abs(z - p) < tol] = 0
    k = 0
    for c in xs.conductors:
        m = (x >= c.x0 - tol) & (x <= c.x1 + tol) & (z >= c.z0 - tol) & (z <= c.z1 + tol)
        if c.ground:
            own[m] = 0
        else:
            k += 1
            own[m] = k
    return own


def _eps(xs, cell):
    cx, cz = 0.5 * (cell[:, 0] + cell[:, 1]), 0.5 * (cell[:, 2] + cell[:, 3])
    er, et = np.ones(len(cell)), np.zeros(len(cell))
    for d in xs.dielectrics:
        m = (cx > d.x0) & (cx < d.x1) & (cz > d.z0) & (cz < d.z1)
        er[m], et[m] = d.er, d.er * d.tand
    return er, et


def _solve_cells(xs, box, cell):
    from scipy.sparse.linalg import splu
    cell = cell[~_inside_copper(xs, cell)]
    P, ids = _nodes(cell)
    own = _owners(xs, P, box)
    M, hanging = _constraints(P, cell, ids, own >= 0)
    indep = np.flatnonzero(~hanging)
    T = M[:, indep]                                        # all nodes from the independent ones
    for k in np.flatnonzero(own >= 0):                     # fixed nodes: themselves
        pass
    io = own[indep]
    n = int(own.max())
    free, fixed = np.flatnonzero(io < 0), np.flatnonzero(io >= 0)
    V = np.zeros((len(indep), n))
    for k in range(n):
        V[io == k + 1, k] = 1.0
    er, et = _eps(xs, cell)
    out = []
    for eps in (er, np.ones_like(er)):
        K = (T.T @ _stiffness(P, cell, ids, eps) @ T).tocsr()
        lu = splu(K[free][:, free].tocsc(), permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0,
                  options=dict(SymmetricMode=True))
        phi = V.copy()
        phi[free] = lu.solve(-(K[free][:, fixed] @ V[fixed]))
        Q = K @ phi
        C = np.array([[Q[io == i + 1, j].sum() for j in range(n)] for i in range(n)]) * EPS0
        out.append((0.5 * (C + C.T), phi))
    (C, phi), (Ca, _) = out
    Kt = (T.T @ _stiffness(P, cell, ids, et) @ T).tocsr()
    G = EPS0 * (phi.T @ (Kt @ phi))
    return C, Ca, 0.5 * (G + G.T), len(indep)


def solve(xs, **kw):
    box, cell = cells(xs, **kw)
    c1 = _solve_cells(xs, box, cell)
    c2 = _solve_cells(xs, box, split(cell))
    return X.Result(*(2 * b - a for a, b in zip(c1[:3], c2[:3])), nodes=c2[3]), c1[3]
