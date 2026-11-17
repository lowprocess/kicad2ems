"""
mom25d.edge - thick copper's edges as corrected near terms (thickness='edge'),
no unknowns added

A triangle on a copper edge (a side of it on the outline) has its charge and
current spread up a wall, as a thick conductor's edge carries them: its static
1/R with a near triangle becomes 1/R averaged over the height between them (two
edge triangles: the difference of two heights uniform in [0, h]; an edge and an
interior one: one height), the analytic integrals at a few offsets. The vector
and the scalar potential take their own heights (H_A, H_Q, P_Q), fitted once to
2D Laplace solves of solid against sheet copper: the microstrip's Z0 and eeff
(w 0.38 mm on 0.2 mm, er 4.3, t 35 um: -4.2 %, -3.2 %; a stripline's eeff
unchanged). A sparse addition to Z on the edge triangles' near pairs (the via
fans' charges with the edges'); t -> 0 is the sheet.
"""
import numpy as np
from scipy import sparse
from scipy.spatial import cKDTree

from .sing import offplane_pairs

# the heights of the averaging, x the copper's thickness: the vector
# potential's H_A; the scalar's H_Q x (2 e_w / (e_w + e_o))^P_Q, e_w the
# permittivity the copper's side stands in, e_o the other side's (H_Q: L C
# unchanged in a homogeneous medium). Fitted 2026-11-17 on the default mesh:
# H_A to the microstrip's L, P_Q to its C, H_Q to a stripline's eeff; above t
# as an edge cell is wider than the wall. Coarser cells across a strip take
# more (eeff -4.2 % at ncross 3, -3.2 % at 6, -2.4 % at 10)
H_A, H_Q, P_Q = 2.35, 1.08, 0.79
# pairs this many heights beyond touching left out (1/R's change ~ h^2 / R^3)
REACH = 4.0
# Gauss-Legendre points over the height
NG = 4
MU0, EPS0 = 4e-7 * np.pi, 8.8541878128e-12


def edge_triangles(rwg):
    """True for a triangle with a side on no other triangle (the outline)."""
    e = np.sort(np.concatenate([rwg.tris[:, [0, 1]], rwg.tris[:, [1, 2]],
                                rwg.tris[:, [2, 0]]]), 1)
    _, inv, cnt = np.unique(e, axis=0, return_inverse=True,
                            return_counts=True)
    return (cnt[inv.ravel()] == 1).reshape(3, -1).any(0)


def _rule(both, n=NG):
    """Points x (heights over h) and weights of the average over the height
    between two triangles: both on an edge, |u| of two uniform heights (density
    2 (1 - x)); one, a uniform height."""
    x, w = np.polynomial.legendre.leggauss(n)
    x, w = 0.5 * (x + 1), 0.5 * w
    return x, (2 * (1 - x) * w if both else w)


class Edges:
    """The edge triangles' near-pair corrections of a Simulation; t: {interface:
    copper thickness (m)}."""

    def __init__(self, sim, t, up):
        r, st = sim.rwg, sim.st
        on = edge_triangles(r)
        tt = np.array([t.get(k, 0.0) for k in sim.tri_layer])
        kq = {}
        for k in sim.layers:
            ew, eo = st.eps(k + 1).real, st.eps(k).real
            if not up.get(k, True):
                ew, eo = eo, ew
            kq[k] = (2 * ew / (ew + eo)) ** P_Q
        h_a, h_q = H_A, H_Q * max(kq.values())
        hq = H_Q * np.array([kq[k] for k in sim.tri_layer])
        on &= tt > 0
        self.n_edge = int(on.sum())
        P, Q = [], []
        tree = cKDTree(r.cent)
        e = np.nonzero(on)[0]
        reach = sim.tri_size[e] + REACH * max(h_a, h_q) * tt[e]
        for p, qs in zip(e, tree.query_ball_point(r.cent[e], reach)):
            qs = np.asarray(qs, int)
            qs = qs[sim.tri_layer[qs] == sim.tri_layer[p]]
            P.append(np.full(len(qs), p)); Q.append(qs)
        P = np.concatenate(P) if P else np.zeros(0, int)
        Q = np.concatenate(Q) if Q else np.zeros(0, int)
        # both ways round (an interior triangle's pair with an edge one)
        n = len(r.tris)
        key = np.unique(np.concatenate([P * n + Q, Q * n + P]))
        self.P, self.Q = key // n, key % n
        P, Q = self.P, self.Q
        both, T, TQ = on[P] & on[Q], tt[P], (tt * hq)[P]
        self.layer = sim.tri_layer[P]
        # the static moments' change: sum_i w_i m(d_i) - m(0)
        base = offplane_pairs(r.nodes, r.tris, P, Q, 0.0)
        self.dA = [np.zeros_like(m) for m in base]
        self.dQ = np.zeros(len(P))
        for b in (True, False):
            s = np.nonzero(both == b)[0]
            x, w = _rule(b)
            for xi, wi in zip(x, w):
                mA = offplane_pairs(r.nodes, r.tris, P[s], Q[s],
                                    xi * h_a * T[s])
                mQ = offplane_pairs(r.nodes, r.tris, P[s], Q[s],
                                    xi * TQ[s], scalar_only=True)
                for d, m in zip(self.dA, mA):
                    d[s] += wi * m
                self.dQ[s] += wi * mQ
        for d, m in zip(self.dA, base):
            d -= m
        self.dQ -= base[0]
        self._halves(sim)

    def __len__(self):
        return len(self.P)

    def _halves(self, sim):
        """The vector potential's part on the RWG functions, and the
        triangles' charges per unknown."""
        r, Ne = sim.rwg, sim.Ne
        h_tri = np.concatenate([r.tp, r.tm])
        alpha = np.concatenate([r.l / (2 * r.area[r.tp]),
                                -r.l / (2 * r.area[r.tm])])
        h_v = np.vstack([r.vp, r.vm])
        halves, fill = -np.ones((len(r.tris), 3), int), np.zeros(len(r.tris),
                                                                  int)
        for h, tr in enumerate(h_tri):
            halves[tr, fill[tr]] = h
            fill[tr] += 1
        m0, mro, mrs, mrr = self.dA
        rows, cols, A = [], [], []
        for i in range(3):
            for j in range(3):
                hi, hj = halves[self.P, i], halves[self.Q, j]
                ok = np.nonzero((hi >= 0) & (hj >= 0))[0]
                hi, hj = hi[ok], hj[ok]
                vi, vj = h_v[hi], h_v[hj]
                A.append(alpha[hi] * alpha[hj] * (
                    mrr[ok] - (vj * mro[ok]).sum(1) - (vi * mrs[ok]).sum(1)
                    + (vi * vj).sum(1) * m0[ok]))
                rows.append(hi % Ne); cols.append(hj % Ne)
        N = Ne + sim.Nv
        self.A = sparse.csr_matrix(
            (np.concatenate(A), (np.concatenate(rows), np.concatenate(cols))),
            shape=(N, N))
        # the triangles' charges per unknown: the edges' divergence and the
        # via chains' on their fans (as the via blocks take them)
        D = sparse.lil_matrix((len(r.tris), N))
        D[:, :Ne] = r.D
        if len(sim.F):
            D[sim.F, Ne:] = sim.dF
        self.D = D.tocsr()

    def matrix(self, sim, f):
        """The N x N sparse addition to Z at f."""
        w, st = 2 * np.pi * f, sim.st.at(f)
        cq = {k: 1.0 / (2 * np.pi * EPS0 * st.eps_sum(k))
              for k in np.unique(self.layer)}
        n = len(sim.rwg.tris)
        Q = sparse.csr_matrix(
            (np.array([cq[k] for k in self.layer]) * self.dQ,
             (self.P, self.Q)), shape=(n, n))
        return (1j * w * MU0 / (4 * np.pi) * self.A
                + (self.D.T @ Q @ self.D) / (1j * w)).tocsr()

    def notes(self):
        return (f"thick copper's edges: {self.n_edge} edge triangles, "
                f"{len(self)} corrected near pairs")
