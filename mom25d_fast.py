"""
mom25d_fast.py - large problems: hierarchical (H-matrix / ACA) far field + sparse near field + GMRES

  Z = Z_far + dZ_near
    Z_far   : every interaction with a 1-point (centroid) rule per triangle, never stored densely:
              admissible cluster pairs are compressed with adaptive cross approximation (ACA),
              the remaining (close) leaf blocks are stored dense
    dZ_near : sparse correction (accurate - centroid) for triangle pairs closer than near_fac cell
              sizes: analytic singular / near-singular parts + higher-order quadrature
  Vias and lumped elements add a few dense rows/columns and sparse entries.
  Solve: GMRES, preconditioned with a sparse LU of the near-field part.
Memory ~ O(N log N) instead of O(N^2); the dense path stays the reference for validation.
"""
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gmres, splu
from scipy.spatial import cKDTree

from mom25d import _BAR3, _W3, _BAR6, _W6
from mom25d_ml import GreenTableML
from mom25d_sing import offplane_pairs


# =============================================================================
# Cluster tree and H-matrix
# =============================================================================
class Cluster:
    __slots__ = ('idx', 'lo', 'hi', 'kids')

    def __init__(self, idx, pts, leaf):
        self.idx = idx
        p = pts[idx]
        self.lo, self.hi = p.min(0), p.max(0)
        self.kids = []
        if len(idx) > leaf:
            ax = np.argmax(self.hi - self.lo)
            o = np.argsort(p[:, ax], kind='stable')
            h = len(idx) // 2
            self.kids = [Cluster(idx[o[:h]], pts, leaf), Cluster(idx[o[h:]], pts, leaf)]

    @property
    def diam(self):
        return np.linalg.norm(self.hi - self.lo)


def _dist(a, b):
    d = np.maximum(0, np.maximum(a.lo - b.hi, b.lo - a.hi))
    return np.linalg.norm(d)


def aca(fill_rows, fill_cols, m, n, tol, max_rank, atol=0.0):
    """Adaptive cross approximation with partial pivoting: block ~ U @ V.
    Stops when the new cross is below tol x (block norm) or below atol per entry (global scale)."""
    U, V = [], []
    used_r, i = set(), 0
    norm2 = 0.0
    for _ in range(min(m, n, max_rank)):
        used_r.add(i)
        row = fill_rows(np.array([i]))[0]
        for u, v in zip(U, V):
            row = row - u[i] * v
        j = int(np.argmax(np.abs(row)))
        if abs(row[j]) < max(1e-300, 0.1 * atol):
            if not U and abs(row[j]) < 0.1 * atol:
                break                                  # block negligible at the global scale
            cand = [r for r in range(m) if r not in used_r]
            if not cand:
                break
            i = cand[0]
            continue
        v = row / row[j]
        col = fill_cols(np.array([j]))[:, 0]
        for uu, vv in zip(U, V):
            col = col - vv[j] * uu
        u = col
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        cross = sum(2 * np.real(np.vdot(uu, u) * np.vdot(v, vv)) for uu, vv in zip(U, V))
        norm2 += cross + (nu * nv) ** 2
        U.append(u); V.append(v)
        if nu * nv <= max(tol * np.sqrt(abs(norm2)), atol * np.sqrt(m * n)):
            break
        a = np.abs(u)
        for r in used_r:
            a[r] = -1
        i = int(np.argmax(a))
    if not U:
        return np.zeros((m, 0), complex), np.zeros((0, n), complex)
    return np.array(U).T, np.array(V)


class HMatrix:
    def __init__(self, entry, pts, leaf=48, eta=1.5, tol=1e-4, max_rank=60, atol=0.0):
        self.n = len(pts)
        root = Cluster(np.arange(self.n), pts, leaf)
        self.lowrank, self.dense = [], []
        stack = [(root, root)]
        while stack:
            a, b = stack.pop()
            if max(a.diam, b.diam) < eta * _dist(a, b):
                U, V = aca(lambda r, a=a, b=b: entry(a.idx[r], b.idx),
                           lambda c, a=a, b=b: entry(a.idx, b.idx[c]),
                           len(a.idx), len(b.idx), tol, max_rank, atol)
                if U.shape[1] * (len(a.idx) + len(b.idx)) < len(a.idx) * len(b.idx):
                    self.lowrank.append((a.idx, b.idx, U, V))
                else:
                    self.dense.append((a.idx, b.idx, U @ V))
            elif not a.kids and not b.kids:
                self.dense.append((a.idx, b.idx, entry(a.idx, b.idx)))
            else:
                ka = a.kids or [a]
                kb = b.kids or [b]
                stack += [(x, y) for x in ka for y in kb]

    def finalize(self):
        """Merge all dense leaves into one sparse matrix; stack low-rank factors for batched products."""
        rows, cols, vals = [], [], []
        for r, c, M in self.dense:
            rr, cc = np.meshgrid(r, c, indexing='ij')
            rows.append(rr.ravel()); cols.append(cc.ravel()); vals.append(M.ravel())
        self.D = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                                   shape=(self.n, self.n)) if rows else sparse.csr_matrix((self.n, self.n))
        self.dense_nnz = self.D.nnz
        self.dense = []
        # low rank: y[r] += U (V x[c])  ->  sparse 'V' (all ranks stacked) then sparse 'U'
        if self.lowrank:
            vr, vc, vv, ur, uc, uv, k0 = [], [], [], [], [], [], 0
            for r, c, U, V in self.lowrank:
                k = U.shape[1]
                kk, cc = np.meshgrid(np.arange(k0, k0 + k), c, indexing='ij')
                vr.append(kk.ravel()); vc.append(cc.ravel()); vv.append(V.ravel())
                rr, kk2 = np.meshgrid(r, np.arange(k0, k0 + k), indexing='ij')
                ur.append(rr.ravel()); uc.append(kk2.ravel()); uv.append(U.ravel())
                k0 += k
            self.Vs = sparse.csr_matrix((np.concatenate(vv), (np.concatenate(vr), np.concatenate(vc))), shape=(k0, self.n))
            self.Us = sparse.csr_matrix((np.concatenate(uv), (np.concatenate(ur), np.concatenate(uc))), shape=(self.n, k0))
            self.lr_size = sum(U.size + V.size for _, _, U, V in self.lowrank)
            self.ranks = [U.shape[1] for _, _, U, _ in self.lowrank]
            self.lowrank = []
        else:
            self.Vs = self.Us = None
            self.lr_size, self.ranks = 0, []

    def matvec(self, x):
        y = self.D @ x
        if self.Vs is not None:
            y = y + self.Us @ (self.Vs @ x)
        return y

    def memory(self):
        return 16 * (self.dense_nnz + self.lr_size)

    def stats(self):
        return dict(dense_entries=self.dense_nnz, lowrank_blocks=len(self.ranks),
                    mean_rank=round(float(np.mean(self.ranks)), 1) if self.ranks else 0.0,
                    MB=round(self.memory() / 2 ** 20, 1),
                    compression=round(self.memory() / (16 * self.n ** 2), 3))


# =============================================================================
# Fast operator for a Simulation
# =============================================================================
class FastOperator:
    """Builds and applies Z for a mom25d_sim.Simulation without forming it densely."""

    def __init__(self, sim, near_fac=None, tol=1e-4, leaf=64, eta=2.0, max_rank=80, atol_rel=1e-8):
        self.sim = sim
        self.atol_rel = atol_rel
        self.max_rank = max_rank
        r = sim.rwg
        self.near_fac = near_fac or sim.near_fac
        self.tol, self.leaf, self.eta = tol, leaf, eta
        Ne = r.Ne
        # half-basis description: (edge, triangle, alpha, free vertex)
        V = r.nodes[r.tris]
        hv = []
        for tri_of, sgn in ((r.tp, 1.0), (r.tm, -1.0)):
            Vt = V[tri_of]                                        # (Ne,3,2)
            on = np.zeros((Ne, 3), bool)
            for k in range(3):
                on[:, k] = (np.linalg.norm(Vt[:, k] - r.ends[:, 0], axis=1) < 1e-12) | \
                           (np.linalg.norm(Vt[:, k] - r.ends[:, 1], axis=1) < 1e-12)
            free = Vt[np.arange(Ne), np.argmin(on, 1)]
            hv.append((tri_of, sgn * r.l / (2 * r.area[tri_of]), free))
        self.h_tri = np.concatenate([hv[0][0], hv[1][0]])         # half h = e (plus) or e + Ne (minus)
        self.h_alpha = np.concatenate([hv[0][1], hv[1][1]])
        self.h_v = np.concatenate([hv[0][2], hv[1][2]])
        self.layer_of_tri = sim.tri_layer
        self.cent, self.area = r.cent, r.area
        self.layers = sim.layers
        self.lidx = {k: i for i, k in enumerate(self.layers)}
        self._near_pairs()
        self._static_cache = {}

    # ---- near pairs (geometry only) ---------------------------------------------------------------
    def _near_pairs(self):
        sim, r = self.sim, self.sim.rwg
        tree = cKDTree(r.cent)
        size = sim.tri_size
        P, Q = [], []
        rad = self.near_fac * size
        lists = tree.query_ball_point(r.cent, rad)
        for p, qs in enumerate(lists):
            qs = np.asarray(qs, int)
            P.append(np.full(len(qs), p)); Q.append(qs)
        P, Q = np.concatenate(P), np.concatenate(Q)
        # symmetrise: near if within either triangle's radius
        key = np.unique(np.concatenate([P * len(size) + Q, Q * len(size) + P]))
        P, Q = key // len(size), key % len(size)
        la, lb = self.layer_of_tri[P], self.layer_of_tri[Q]
        rho = np.linalg.norm(r.cent[P] - r.cent[Q], axis=1)
        st = sim.st
        dz = np.abs(st.z[la] - st.z[lb])
        keep = rho ** 2 + dz ** 2 < (self.near_fac * np.maximum(size[P], size[Q])) ** 2
        self.nP, self.nQ = P[keep], Q[keep]
        sim.log.note(f"fast solver: {len(self.nP)} near triangle pairs "
                     f"({len(self.nP) / len(size):.0f} per triangle)")

    # ---- kernels ----------------------------------------------------------------------------------
    def _kern(self, a, b, rho):
        gt = self.tables[(min(a, b), max(a, b))]
        GA, GQ = gt.AQ(rho)
        if a == b:
            with np.errstate(divide='ignore'):
                inv = np.where(rho > 0, 1.0 / np.where(rho > 0, rho, 1.0), 0.0)
            GA, GQ = GA + gt.cA * inv, GQ + gt.cq * inv
            if gt.dimg:
                Ri = 1.0 / np.sqrt(rho ** 2 + gt.dimg ** 2)
                GA, GQ = GA + gt.cAi * Ri, GQ + gt.cqi * Ri
        elif gt.cA != 0:
            R3 = 1.0 / np.sqrt(rho ** 2 + gt.d ** 2)
            GA, GQ = GA + gt.cA * R3, GQ + gt.cq * R3
        return GA, GQ

    def _far_halves(self, hr, hc):
        """Centroid-rule interaction between half-bases hr (rows) and hc (cols)."""
        w = self.w
        tr, tc = self.h_tri[hr], self.h_tri[hc]
        cr, cc = self.cent[tr], self.cent[tc]
        rho = np.sqrt(((cr[:, None, :] - cc[None, :, :]) ** 2).sum(-1))
        lr, lc = self.layer_of_tri[tr], self.layer_of_tri[tc]
        GA = np.zeros(rho.shape, complex); GQ = np.zeros(rho.shape, complex)
        for a in np.unique(lr):
            ma = lr == a
            for b in np.unique(lc):
                mb = lc == b
                ga, gq = self._kern(a, b, rho[np.ix_(ma, mb)])
                GA[np.ix_(ma, mb)], GQ[np.ix_(ma, mb)] = ga, gq
        ar, ac = self.h_alpha[hr] * self.area[tr], self.h_alpha[hc] * self.area[tc]
        dr, dc = cr - self.h_v[hr], cc - self.h_v[hc]
        dot = dr @ dc.T
        return np.outer(ar, ac) * (1j * w * dot * GA + 4 * GQ / (1j * w))

    def entry_pairs(self, rows, cols, chunk=200000):
        """Centroid-rule Z for individual (row, col) edge pairs."""
        Ne, w = self.sim.Ne, self.w
        out = np.zeros(len(rows), complex)
        for c0 in range(0, len(rows), chunk):
            rr, cc = rows[c0:c0 + chunk], cols[c0:c0 + chunk]
            for hr_off in (0, Ne):
                for hc_off in (0, Ne):
                    hr, hc = rr + hr_off, cc + hc_off
                    tr, tc = self.h_tri[hr], self.h_tri[hc]
                    rho = np.linalg.norm(self.cent[tr] - self.cent[tc], axis=1)
                    lr, lc = self.layer_of_tri[tr], self.layer_of_tri[tc]
                    GA = np.zeros(len(rr), complex); GQ = np.zeros(len(rr), complex)
                    for a in np.unique(lr):
                        for b in np.unique(lc):
                            m = (lr == a) & (lc == b)
                            if m.any():
                                GA[m], GQ[m] = self._kern(a, b, rho[m])
                    ar = self.h_alpha[hr] * self.area[tr]; ac = self.h_alpha[hc] * self.area[tc]
                    dot = ((self.cent[tr] - self.h_v[hr]) * (self.cent[tc] - self.h_v[hc])).sum(1)
                    out[c0:c0 + chunk] += ar * ac * (1j * w * dot * GA + 4 * GQ / (1j * w))
        return out

    def entry(self, rows, cols):
        Ne = self.sim.Ne
        hr = np.concatenate([rows, rows + Ne]); hc = np.concatenate([cols, cols + Ne])
        H = self._far_halves(hr, hc)
        m, n = len(rows), len(cols)
        return H[:m, :n] + H[:m, n:] + H[m:, :n] + H[m:, n:]

    # ---- near correction ----------------------------------------------------------------------------
    def _near_moments(self, P=None, Q=None, cache=True):
        """Accurate minus centroid moments for triangle pairs (default: all near pairs)."""
        sim, r = self.sim, self.sim.rwg
        if P is None:
            P, Q = self.nP, self.nQ
        else:
            cache = False
        la, lb = self.layer_of_tri[P], self.layer_of_tri[Q]
        n = len(P)
        out = np.zeros((n, 7), complex)        # A0, Axo, Ayo, Axs, Ays, Arr, Q0
        for a in self.layers:
            for b in self.layers:
                m = np.nonzero((la == a) & (lb == b))[0]
                if not len(m):
                    continue
                gt = self.tables[(min(a, b), max(a, b))]
                p, q = P[m], Q[m]
                # smooth rest by quadrature
                Pq, Wq = (sim.P3, sim.W3) if a == b else (sim.P6, sim.W6)
                for c0 in range(0, len(m), 20000):
                    s = slice(c0, c0 + 20000)
                    pa, pb = Pq[p[s]][:, :, None, :], Pq[q[s]][:, None, :, :]
                    Wt = Wq[p[s]][:, :, None] * Wq[q[s]][:, None, :]
                    ga, gq = gt.AQ(np.sqrt(((pa - pb) ** 2).sum(-1)))
                    ga, gq = ga * Wt, gq * Wt
                    out[m[s]] = np.stack([ga.sum((1, 2)), (ga * pa[..., 0]).sum((1, 2)),
                                          (ga * pa[..., 1]).sum((1, 2)), (ga * pb[..., 0]).sum((1, 2)),
                                          (ga * pb[..., 1]).sum((1, 2)), (ga * (pa * pb).sum(-1)).sum((1, 2)),
                                          gq.sum((1, 2))], 1)
                # analytic singular / near-singular parts
                terms = []
                if a == b:
                    terms.append((0.0, gt.cA, gt.cq))
                    if gt.dimg:
                        terms.append((gt.dimg, gt.cAi, gt.cqi))
                elif gt.cA != 0:
                    terms.append((gt.d, gt.cA, gt.cq))
                for d, cA, cq in terms:
                    key = (a, b, d)
                    if not cache:
                        m0, mro, mrs, mrr = offplane_pairs(r.nodes, r.tris, p, q, d)
                    else:
                        if key not in self._static_cache:
                            self._static_cache[key] = offplane_pairs(r.nodes, r.tris, p, q, d)
                        m0, mro, mrs, mrr = self._static_cache[key]
                    out[m] += np.stack([cA * m0, cA * mro[:, 0], cA * mro[:, 1], cA * mrs[:, 0],
                                        cA * mrs[:, 1], cA * mrr, cq * m0], 1)
                # subtract the centroid-rule value used by the far operator
                rho = np.linalg.norm(r.cent[p] - r.cent[q], axis=1)
                GA, GQ = self._kern(a, b, rho)
                wgt = r.area[p] * r.area[q]
                cp, cq_ = r.cent[p], r.cent[q]
                out[m] -= np.stack([GA * wgt, GA * wgt * cp[:, 0], GA * wgt * cp[:, 1], GA * wgt * cq_[:, 0],
                                    GA * wgt * cq_[:, 1], GA * wgt * (cp * cq_).sum(1), GQ * wgt], 1)
        return out

    def _fan_columns(self):
        """Accurate G_q moments between every triangle and the via fan triangles (Nt x |F|)."""
        sim, r = self.sim, self.sim.rwg
        F = sim.F
        lt = self.layer_of_tri
        QF = np.zeros((len(r.tris), len(F)), complex)
        for j, q in enumerate(F):
            rho = np.linalg.norm(r.cent - r.cent[q], axis=1)
            for a in self.layers:
                m = lt == a
                _, GQ = self._kern(a, lt[q], rho[m])
                QF[m, j] = GQ * r.area[m] * r.area[q]
        # accurate (analytic singular + quadrature) correction for every triangle against the fans:
        # the via input impedance is a small difference of large charge terms, so B must be exact
        Pall = np.repeat(np.arange(len(r.tris)), len(F))
        Qall = np.tile(F, len(r.tris))
        corr = self._near_moments(Pall, Qall)[:, 6]
        QF += corr.reshape(len(r.tris), len(F))
        return QF

    def _near_matrix(self):
        """Sparse (Ne x Ne) correction from triangle-pair moments."""
        r, Ne, w = self.sim.rwg, self.sim.Ne, self.w
        M = self._near_moments()
        self._Mnear = M
        # halves on each triangle
        h_of_tri = [[] for _ in range(len(r.tris))]
        for h, t in enumerate(self.h_tri):
            h_of_tri[t].append(h)
        rows, cols, vals = [], [], []
        P, Q = self.nP, self.nQ
        maxh = max(len(x) for x in h_of_tri)
        Hp = -np.ones((len(r.tris), maxh), int)
        for t, hs in enumerate(h_of_tri):
            Hp[t, :len(hs)] = hs
        for i in range(maxh):
            for j in range(maxh):
                hi, hj = Hp[P, i], Hp[Q, j]
                ok = (hi >= 0) & (hj >= 0)
                hi, hj, mm = hi[ok], hj[ok], M[ok]
                ai, aj = self.h_alpha[hi], self.h_alpha[hj]
                vi, vj = self.h_v[hi], self.h_v[hj]
                A = (mm[:, 5] - (vj[:, 0] * mm[:, 1] + vj[:, 1] * mm[:, 2])
                     - (vi[:, 0] * mm[:, 3] + vi[:, 1] * mm[:, 4]) + (vi * vj).sum(1) * mm[:, 0])
                z = ai * aj * (1j * w * A + 4 * mm[:, 6] / (1j * w))
                rows.append(hi % Ne); cols.append(hj % Ne); vals.append(z)
        return sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                                 shape=(Ne, Ne))

    # ---- assemble & solve ---------------------------------------------------------------------------------
    def setup(self, f):
        sim = self.sim
        self.f, self.w = f, 2 * np.pi * f
        self.tables = {}
        for i, a in enumerate(self.layers):
            for b in self.layers[i:]:
                xs = a != b and abs(sim.st.z[a] - sim.st.z[b]) < 3 * sim.dmax
                im = a == b and sim._near_layer(a) < 1.5 * sim.dmax
                with sim.log.timer('Green tables', f"M{a}-M{b} @ {f / 1e9:.2f} GHz"):
                    self.tables[(a, b)] = GreenTableML(f, sim.st, a, b, sim.rho_max, cross_sing=xs, image_sing=im)
        sim.tables = self.tables
        with sim.log.timer('near-field correction'):
            dZ = self._near_matrix()
        if sim.Nv:
            with sim.log.timer('via kernels', f"{sim.Nv} chains"):
                self.B, self.C = sim._via_blocks(f, self._fan_columns())
        with sim.log.timer('H-matrix (ACA)'):
            # global scale: typical self term; far blocks below tol x that per entry are dropped/truncated
            dg = np.abs(self.entry_pairs(np.arange(sim.Ne), np.arange(sim.Ne)) + dZ.diagonal())
            self.atol = self.atol_rel * float(np.median(dg))
            self.H = HMatrix(self.entry, sim.rwg.mid, leaf=self.leaf, eta=self.eta, tol=self.tol,
                             max_rank=self.max_rank, atol=self.atol)
            self.H.finalize()
        # preconditioner: the full operator restricted to the near-field sparsity pattern
        with sim.log.timer('preconditioner build'):
            dZ = dZ.tocoo()
            vals = self.entry_pairs(dZ.row, dZ.col) + dZ.data
            self.Pnear = sparse.csc_matrix((vals, (dZ.row, dZ.col)), shape=dZ.shape)
            self.dZ = dZ.tocsr()
        sim.log.note(f"H-matrix: {self.H.stats()}")

    def extra_blocks(self, f):
        """Via rows/columns, lumped elements and metal loss (dense in the few via unknowns)."""
        sim = self.sim
        N = sim.Ne + sim.Nv
        Ne = sim.Ne
        B = np.zeros((N, sim.Nv), complex) if sim.Nv else None
        S = sparse.lil_matrix((N, N), dtype=complex)
        for el, (idx, sig) in zip(sim.lumped, sim.lumped_edges):
            u = sig * sim.rwg.l[idx]
            S[np.ix_(idx, idx)] = S[np.ix_(idx, idx)].toarray() + el.Z(f) * np.outer(u, u)
        if sim.metal is not None:
            from mom25d import gram_matrix
            if sim._gram is None:
                sim._gram = sparse.csr_matrix(gram_matrix(sim.rwg))
            G = sparse.csr_matrix(sim._gram)
            S[:Ne, :Ne] = S[:Ne, :Ne] + sim.metal.zs(f) * G
        return B, S.tocsr()

    def solve(self, f, E, tol=1e-6, restart=200, maxiter=2000):
        sim = self.sim
        self.setup(f)
        _, Sx = self.extra_blocks(f)
        Ne, Nv = sim.Ne, sim.Nv
        N = Ne + Nv
        Pn = self.Pnear + Sx[:Ne, :Ne]
        if Nv:                                   # bordered system: dense via rows/columns
            Pn = sparse.bmat([[Pn, sparse.csr_matrix(self.B)],
                              [sparse.csr_matrix(self.B.T), sparse.csr_matrix(self.C)]])
        with sim.log.timer('preconditioner LU'):
            lu = splu(sparse.csc_matrix(Pn), permc_spec='COLAMD')

        def mv(x):
            xe = x[:Ne]
            y = np.empty(N, complex)
            y[:Ne] = self.H.matvec(xe) + self.dZ @ xe + Sx[:Ne, :Ne] @ xe
            if Nv:
                y[:Ne] += self.B @ x[Ne:]
                y[Ne:] = self.B.T @ xe + self.C @ x[Ne:]
            return y
        A = LinearOperator((N, N), matvec=mv, dtype=complex)
        M = LinearOperator((N, N), matvec=lu.solve, dtype=complex)
        X = np.zeros((N, E.shape[1]), complex)
        self.iters = []
        with sim.log.timer('GMRES', f"{E.shape[1]} rhs"):
            for j in range(E.shape[1]):
                it = [0]

                def cb(_):
                    it[0] += 1
                x, info = gmres(A, E[:, j], M=M, rtol=tol, restart=restart, maxiter=maxiter,
                                callback=cb, callback_type='pr_norm')
                X[:, j] = x
                self.iters.append(it[0])
        sim.log.note(f"GMRES iterations per port: {self.iters}")
        return X
