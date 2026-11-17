"""
mom25d.sides - thick copper's sides as vertical plates (thickness 'sides';
experimental)

A sheet carries a trace's current and charge on one face; thick copper also on
its sides. A side holds its charge in the medium beside the copper (the air over
a microstrip), faces the next copper across a gap, and carries part of the
current along the trace: with that current the line's inductance falls as its
capacitance in air rises, and the effective permittivity falls (a 2D Laplace
check, w 0.4 mm on 0.21 mm FR4, t 35 um: sheet 3.391, solid 3.289, sheet and
sides 3.318; the sides' charge without their current 3.454, the wrong way).

Every boundary edge of a layer's mesh that faces copper of the same layer within
s_max (None: every boundary edge) stands up as a plate the copper's thickness t
high, into the layer the copper grows into (fdtd.thick: up; the board's lowest
copper down). A plate is two triangles, (a0, b0, b1) and (a0, b1, a1) (0 on the
sheet, 1 at its top), and carries RWG functions in 3D: the fold (the sheet's
boundary triangle to the plate), the diagonal, and the vertical edge shared with
the next plate along the boundary (the current along the side).

Their blocks: the MPIE's two parts with the layered tables at the horizontal
distance on 3 x 3 points (the far rule; a vertical current's own smooth part
left out), and for the pairs near each other (same layer) the static parts in
3D exactly (pcbqs.panels3d's analytic source integral) in place of the rule's:
mu0 / (4 pi R) for the vector potential, for the charge 1 / (2 pi eps0 eps_sum
R) where one of the two is on the interface, (1 / (4 pi eps0 eps1)) (1/R + G/R')
between two plates (eps1 the plates' medium, eps2 the other side, G = (eps1 -
eps2) / (eps1 + eps2), R' to the image across the interface).
"""
import numpy as np
import shapely
from scipy import sparse
from scipy.spatial import cKDTree

from kicad2ems.emsdev.numerics.base import _BAR3, _W3, EPS0, MU0
from kicad2ems.pcbqs.panels3d import pair_moments

from .sing import offplane_pairs

# near pairs: centroids closer than this x the larger triangle's size
NEAR = 4.0


class Sides:
    """The plates of a Simulation's mesh and their functions. t: {interface:
    copper thickness (m)}, up: {interface: True (the plate above the sheet) |
    False (below)}, s_max (m) or None (every boundary edge)."""

    def __init__(self, sim, t, up, s_max=None, cuts=None):
        r, lt = sim.rwg, sim.tri_layer
        tris, nodes = r.tris, r.nodes
        e = tris[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        owner = np.repeat(np.arange(len(tris)), 3)
        opp = tris[:, [2, 0, 1]].ravel()
        _, inv, cnt = np.unique(np.sort(e, 1), axis=0, return_inverse=True,
                                return_counts=True)
        bd = np.flatnonzero(cnt[inv.ravel()] == 1)
        bd = bd[np.array([t.get(int(k), 0.0) > 0 for k in lt[owner[bd]]],
                         bool)]
        ia, ib = e[bd, 0], e[bd, 1]
        po = nodes[opp[bd]]
        d, w = nodes[ib] - nodes[ia], po - nodes[ia]
        # each edge a -> b with its triangle on the left
        sw = d[:, 0] * w[:, 1] - d[:, 1] * w[:, 0] < 0
        ia, ib = np.where(sw, ib, ia), np.where(sw, ia, ib)
        pa, pb = nodes[ia], nodes[ib]
        tv = pb - pa
        L = np.hypot(tv[:, 0], tv[:, 1])
        n = np.stack([tv[:, 1], -tv[:, 0]], 1) / L[:, None]  # outward
        keep = np.ones(len(bd), bool)
        if s_max is not None:
            mid = 0.5 * (pa + pb)
            for k in np.unique(lt[owner[bd]]):
                on = lt[owner[bd]] == k
                own = shapely.unary_union(
                    shapely.polygons(nodes[tris[lt == k]]))
                shapely.prepare(own)
                s0 = mid[on] + n[on] * 1e-3 * L[on, None]
                s1 = mid[on] + n[on] * s_max
                keep[on] = shapely.intersects(
                    shapely.linestrings(np.stack([s0, s1], 1)), own)
        bd, ia, ib, po, L = bd[keep], ia[keep], ib[keep], po[keep], L[keep]
        self.N = 0
        self.T, self.ia, self.ib = owner[bd], ia, ib
        self.k = lt[self.T]
        self.t = np.array([t[int(k)] for k in self.k], float)
        self.h = self.t * np.array([1.0 if up[int(k)] else -1.0
                                    for k in self.k])
        self.L, self.np_ = L, len(bd)
        self.layers = sorted({int(k) for k in self.k})
        # the boundary nodes on a cut line (a port's gap or sample cut, a
        # lumped element): the sides break there (the element's current, the
        # port's, is the sheet's edges'; a side through a combline's loading
        # capacitors shorted them, its passband 2.4 -> 6.4 GHz)
        self.cut_node = set()
        for k, segs in (cuts or {}).items():
            on = np.flatnonzero(self.k == k)
            if not len(on) or not segs:
                continue
            ln = shapely.unary_union([shapely.LineString(sg) for sg in segs])
            p = sim.rwg.nodes[self.ib[on]]
            d = shapely.distance(shapely.points(p), ln)
            self.cut_node.update(self.ib[on][d < 1e-9].tolist())
        if self.np_:
            self._functions(sim, po)
        self._near = None

    def __len__(self):
        return self.N

    def notes(self, s_max):
        return (f"sides: {self.np_} plates, {self.N} functions on "
                f"{', '.join(f'M{k}' for k in self.layers)} ("
                + ('every copper edge' if s_max is None else
                   f"edges facing copper within {s_max * 1e3:g} mm") + ")")

    # ---- geometry ----------------------------------------------------------
    def _functions(self, sim, po):
        """The plate triangles (PT: 2i, 2i + 1 of plate i), the source triangles
        (the folds' sheet triangles, then the PTs), the functions' halves and
        their matrices on the points."""
        r, P = sim.rwg, self.np_
        a, b = r.nodes[self.ia], r.nodes[self.ib]
        z0, h = np.zeros(P), self.h
        a0, b0 = np.c_[a, z0], np.c_[b, z0]
        a1, b1 = np.c_[a, h], np.c_[b, h]
        self.PT = np.stack([np.stack([a0, b0, b1], 1),
                            np.stack([a0, b1, a1], 1)], 1).reshape(-1, 3, 3)
        self.pt_layer = np.repeat(self.k, 2)
        apt = 0.5 * self.L * self.t  # each PT's area
        nxt = {int(i): j for j, i in enumerate(self.ia)}
        # halves: (function, kind 0 sheet / 1 PT, triangle, alpha, v (3D))
        H = []
        nf = 0
        for i in range(P):
            T, A_, B_ = int(self.T[i]), 2 * i, 2 * i + 1
            AT = abs(r.area[T])
            H += [(nf, 0, T, self.L[i] / (2 * AT), np.r_[po[i], 0.0]),
                  (nf, 1, A_, -self.L[i] / (2 * apt[i]), b1[i])]
            ld = np.hypot(self.L[i], self.t[i])
            H += [(nf + 1, 1, A_, ld / (2 * apt[i]), b0[i]),
                  (nf + 1, 1, B_, -ld / (2 * apt[i]), a1[i])]
            nf += 2
            j = nxt.get(int(self.ib[i]))
            if int(self.ib[i]) in self.cut_node:
                j = None
            if j is not None and j != i:
                H += [(nf, 1, A_, self.t[i] / (2 * apt[i]), a0[i]),
                      (nf, 1, 2 * j + 1, -self.t[j] / (2 * apt[j]), b1[j])]
                nf += 1
        self.N = nf
        self.h_fn = np.array([x[0] for x in H], int)
        kind = np.array([x[1] for x in H], int)
        tri = np.array([x[2] for x in H], int)
        self.h_alpha = np.array([x[3] for x in H], float)
        self.h_v = np.array([x[4] for x in H], float)
        Nt = len(r.tris)
        # observers: the sheet's triangles, then the PTs
        self.h_obs = np.where(kind == 0, tri, Nt + tri)
        # sources: the folds' sheet triangles, then the PTs
        self.src_T = np.unique(tri[kind == 0])
        sT = {int(x): j for j, x in enumerate(self.src_T)}
        nT = len(self.src_T)
        self.h_src = np.where(kind == 0, [sT.get(int(x), -1) for x in tri],
                              nT + tri)
        V2 = np.concatenate([r.nodes[r.tris[self.src_T]],
                             np.zeros((nT, 3, 1))], 2)
        self.src_V = np.concatenate([V2, self.PT])  # (Ns, 3, 3)
        self.src_layer = np.r_[sim.tri_layer[self.src_T], self.pt_layer]
        self.src_pt = np.r_[np.zeros(nT, bool), np.ones(2 * P, bool)]
        area = np.r_[np.abs(r.area[self.src_T]), np.repeat(apt, 2)]
        # 3-point rule on the sources and on the PT observers
        self.sp = np.einsum('qk,skc->sqc', _BAR3, self.src_V)  # (Ns, 3, 3)
        self.sw = area[:, None] * _W3[None]
        Ns = len(self.src_V)
        rows = (3 * self.h_src[:, None] + np.arange(3)[None]).ravel()
        cols = np.repeat(self.h_fn, 3)
        pts = self.sp[self.h_src]                       # (nh, 3, 3)
        wts = self.sw[self.h_src]
        f = self.h_alpha[:, None, None] * wts[..., None] * (
            pts - self.h_v[:, None, :])
        shp = (3 * Ns, self.N)
        self.Fx = sparse.csr_matrix((f[..., 0].ravel(), (rows, cols)), shp)
        self.Fy = sparse.csr_matrix((f[..., 1].ravel(), (rows, cols)), shp)
        self.Dq = sparse.csr_matrix(
            ((2 * self.h_alpha[:, None] * wts).ravel(), (rows, cols)), shp)
        self.op = np.einsum('qk,skc->sqc', _BAR3, self.PT)  # PT observers
        self.ow = np.repeat(apt, 2)[:, None] * _W3[None]
        self.obs_layer = np.r_[sim.tri_layer, self.pt_layer]
        self.obs_V = None
        # the halves as rows: sheet edges (B) and the functions (C) take theirs
        # from the observers' tri-level arrays
        self.Nt = Nt

    def _obs_geometry(self, sim):
        r = sim.rwg
        V2 = np.concatenate([r.nodes[r.tris], np.zeros((len(r.tris), 3, 1))],
                            2)
        V = np.concatenate([V2, self.PT])
        size = np.linalg.norm(V - np.roll(V, 1, axis=1), axis=2).max(1)
        P = np.concatenate([np.concatenate(
            [sim.P3, np.zeros(sim.P3.shape[:2] + (1,))], 2), self.op])
        W = np.concatenate([sim.W3, self.ow])
        return V, size, P, W

    def near(self, sim):
        """Geometry only, once: the static corrections of the near pairs at the
        observers' level, (dM1, dMx, dMy, dMz) for the vector potential
        (x mu0 / 4 pi) and per layer (X1, X2, X3) for the charge (x the
        layer's cq, 1 / (4 pi eps0 eps1), G / (4 pi eps0 eps1)): sparse (Nobs,
        N) each."""
        if self._near is not None:
            return self._near
        Vo, so, Po, Wo = self._obs_geometry(sim)
        Vs = self.src_V
        ss = np.linalg.norm(Vs - np.roll(Vs, 1, axis=1), axis=2).max(1)
        co, cs = Vo.mean(1), Vs.mean(1)
        to, ts = cKDTree(co), cKDTree(cs)
        pairs = set()
        for j, lst in enumerate(to.query_ball_point(cs, NEAR * ss)):
            pairs.update((i, j) for i in lst)
        for i, lst in enumerate(ts.query_ball_point(co, NEAR * so)):
            pairs.update((i, j) for j in lst)
        p = np.array(sorted(pairs), int).reshape(-1, 2)
        o, s = p[:, 0], p[:, 1]
        same = self.obs_layer[o] == self.src_layer[s]
        o, s = o[same], s[same]
        dist = np.linalg.norm(co[o] - cs[s], axis=1)
        m0 = np.zeros(len(o))
        mro, mrs = np.zeros((len(o), 3)), np.zeros((len(o), 3))
        mrr = np.zeros(len(o))
        close = dist < 1.5 * np.maximum(so[o], ss[s])
        # a sheet triangle with a fold's: as the strips' own near pairs have
        # them (sing.offplane_pairs, the 6-point rule over the smaller), so
        # that the fold's charge on its triangle is the strips' charge there
        # (with pair_moments' 1 % other self terms, plates on every edge of a
        # line took 10 % more capacitance than the copper grown flat by t)
        sheet = (o < self.Nt) & ~self.src_pt[s]
        c = np.flatnonzero(sheet)
        if len(c):
            r = sim.rwg
            a0, ao, as_, ar = offplane_pairs(r.nodes, r.tris, o[c],
                                             self.src_T[s[c]], 0.0)
            m0[c], mro[c, :2], mrs[c, :2], mrr[c] = a0, ao, as_, ar
        # the rule over the smaller triangle of a pair, the analytic integral
        # over the larger (as sing.offplane_pairs: a plate's sliver against
        # a 1 mm strip cell the other way round had rf_combline's resonators
        # off by a factor 2.6 in frequency)
        ao, as_ = _areas(Vo[o]), _areas(Vs[s])
        sw = ao > as_
        for lev, m in ((2, close & ~sheet), (1, ~close & ~sheet)):
            for c in np.array_split(np.flatnonzero(m),
                                    max(1, m.sum() // 20000)):
                if not len(c):
                    continue
                x, y = sw[c], ~sw[c]
                for sel, flip in ((c[y], False), (c[x], True)):
                    if not len(sel):
                        continue
                    A, B = Vo[o[sel]], Vs[s[sel]]
                    q0, qo, qs, qr = pair_moments(*((B, A) if flip else
                                                    (A, B)), outer_levels=lev)
                    if flip:
                        qo, qs = qs, qo
                    m0[sel], mro[sel], mrs[sel], mrr[sel] = q0, qo, qs, qr
        both = (o >= self.Nt) & self.src_pt[s]
        img = np.zeros(len(o))
        if both.any():
            Vm = Vs[s[both]].copy()
            Vm[..., 2] *= -1.0
            Vm = Vm[:, ::-1]  # (keep the triangle counter-clockwise)
            img[both] = pair_moments(Vo[o[both]], Vm, outer_levels=2,
                                     scalar_only=True)
        # the rule's static part on these pairs: 1/rho as the tables
        # interpolate it, horizontal distances
        rho = np.linspace(0, sim.rho_max, 8192)
        dr = rho[1]
        inv = 1.0 / np.maximum(rho, dr)
        dx = Po[o][:, :, None, :2] - self.sp[s][:, None, :, :2]
        u = np.minimum(np.hypot(dx[..., 0], dx[..., 1]) / dr, 8191 - 1e-6)
        ii = u.astype(np.int64)
        Tq = (inv[ii] * (1 - (u - ii)) + inv[ii + 1] * (u - ii)) * (
            Wo[o][:, :, None] * self.sw[s][:, None, :])   # (np, 3, 3)
        m0r = Tq.sum((1, 2))
        # per pair and source half: the half's contributions
        hs = [[] for _ in range(len(Vs))]
        for h, sj in enumerate(self.h_src):
            hs[sj].append(h)
        pr, hh = [], []
        for q, sj in enumerate(s):
            for h in hs[sj]:
                pr.append(q); hh.append(h)
        pr, hh = np.array(pr, int), np.array(hh, int)
        al, v = self.h_alpha[hh], self.h_v[hh]
        # exact: M1 = a (mrr - v . mro), M0v = a (mrs - v m0)
        e1 = al * (mrr[pr] - (v * mro[pr]).sum(1))
        e0 = al[:, None] * (mrs[pr] - v * m0[pr][:, None])
        # the rule's: a sum T (r_o,h . (r_s - v)_h), a sum T (r_s - v)_h
        rs = self.sp[s[pr]][..., :2] - v[:, None, :2]       # (n, 3s, 2)
        ro = Po[o[pr]][..., :2]                             # (n, 3o, 2)
        Tp = Tq[pr]                                         # (n, 3o, 3s)
        r1 = al * np.einsum('nab,nac,nbc->n', Tp, ro, rs)
        r0 = al[:, None] * np.einsum('nab,nbc->nc', Tp, rs)
        Nobs = len(Vo)
        sh = (Nobs, self.N)
        row, col = o[pr], self.h_fn[hh]
        mk = lambda x: sparse.csr_matrix((x, (row, col)), sh)
        dA = (mk(e1 - r1), mk(e0[:, 0] - r0[:, 0]), mk(e0[:, 1] - r0[:, 1]),
              mk(e0[:, 2]))
        div = 2 * al
        pp = both[pr]
        lay = self.src_layer[s[pr]]
        dq = {}
        for k in self.layers:
            m = lay == k
            x1 = np.where(pp, -m0r[pr], m0[pr] - m0r[pr]) * div
            dq[k] = (mk(np.where(m, x1, 0.0)),
                     mk(np.where(m & pp, m0[pr] * div, 0.0)),
                     mk(np.where(m & pp, img[pr] * div, 0.0)))
        self._near = (dA, dq, Po, Wo)
        return self._near

    def via_d(self, sim, vk, geo):
        """(N, chains): the via chains' smooth potential (via.ViaKernels.D,
        its E_t = grad D on an interface) tested with each function's charge,
        as sim._via_blocks tests the strips' (3-point rule)."""
        from .fast import _core
        _, _, Po, Wo = self.near(sim)
        out = np.zeros((self.N, len(sim.chains)), complex)
        Hd = sparse.csr_matrix((2 * self.h_alpha, (self.h_fn, self.h_obs)),
                               (self.N, len(Po)))
        for span, ns in geo['by_span'].items():
            for k in self.layers:
                oi = np.flatnonzero(self.obs_layer == k)
                D = vk.D[(k, span)]
                c = _rts(_core, Po[oi, :, 0], Po[oi, :, 1], Wo[oi],
                         geo['x'][ns], geo['y'][ns], D.v, D._dr)
                out[:, ns] += Hd[:, oi] @ c
        return out

    # ---- at a frequency ----------------------------------------------------
    def blocks(self, sim, f):
        """(Bw (Ne, N), Cw (N, N), QF (len(sim.F), N)) at f: the sheet edges
        with the sides' functions, the functions with each other, and the
        potential integral of each function's charge over each via fan
        triangle (their coupling: with sim.dF, / jw). The Green tables are
        sim.tables (sim.green_tables(f), as the strips' blocks). The
        observers a chunk at a time (sma_launch: 11k observers x 3.4k
        functions)."""
        from .fast import _core
        r, st, w = sim.rwg, sim.st.at(f), 2 * np.pi * f
        dA, dq, Po, Wo = self.near(sim)
        Nobs, N = len(Po), self.N
        cA = MU0 / (4 * np.pi)
        # the charge corrections' coefficients at f, per layer
        cq = []
        for k, (x1, x2, x3) in dq.items():
            up = self.h[self.k == k][0] > 0
            e1, e2 = ((st.eps(k + 1), st.eps(k)) if up else
                      (st.eps(k), st.eps(k + 1)))
            c1 = 1.0 / (4 * np.pi * EPS0 * e1)
            cq.append((x1, 1.0 / (2 * np.pi * EPS0 * st.eps_sum(k))))
            cq += [(x2, c1), (x3, c1 * (e1 - e2) / (e1 + e2))]
        tabs = {}
        for ks in self.layers:
            si = np.flatnonzero(self.src_layer == ks)
            rows = (3 * si[:, None] + np.arange(3)[None]).ravel()
            tabs[ks] = (self.sp[si][..., 0].ravel(),
                        self.sp[si][..., 1].ravel(),
                        self.Fx[rows].T.tocsr(), self.Fy[rows].T.tocsr(),
                        self.Dq[rows].T.tocsr())
        Bw = np.zeros((r.Ne, N), complex)
        Cw = np.zeros((N, N), complex)
        Fpos = np.full(Nobs, -1)
        Fpos[sim.F] = np.arange(len(sim.F))
        QF = np.zeros((len(sim.F), N), complex)
        # the halves by observer: the sheet edges' (T+, T-), the functions'
        hal = [(r.tp, np.arange(r.Ne), r.l / (2 * r.area[r.tp]),
                np.c_[r.vp, np.zeros(r.Ne)], True),
               (r.tm, np.arange(r.Ne), -r.l / (2 * r.area[r.tm]),
                np.c_[r.vm, np.zeros(r.Ne)], True),
               (self.h_obs, self.h_fn, self.h_alpha, self.h_v, False)]
        for ko in sim.layers:
            oi = np.flatnonzero(self.obs_layer == ko)
            for c in np.array_split(oi, max(1, len(oi) // 1500)):
                M1, M0x, M0y, Q = (np.zeros((len(c), N), complex)
                                   for _ in range(4))
                px, py, W = Po[c, :, 0], Po[c, :, 1], Wo[c]
                for ks in self.layers:
                    vx, vy, Fx, Fy, Dq = tabs[ks]
                    gt = sim.tables[(min(ko, ks), max(ko, ks))]
                    vA, vQ, dr = _total(gt)
                    K = _rts(_core, px, py, W, vx, vy, vA, dr).T
                    M0x += (Fx @ K).T
                    M0y += (Fy @ K).T
                    M1 += (Fx @ _rts(_core, px, py, W * px, vx, vy, vA,
                                     dr).T).T
                    M1 += (Fy @ _rts(_core, px, py, W * py, vx, vy, vA,
                                     dr).T).T
                    Q += (Dq @ _rts(_core, px, py, W, vx, vy, vQ, dr).T).T
                M1 += cA * dA[0][c].toarray()
                M0x += cA * dA[1][c].toarray()
                M0y += cA * dA[2][c].toarray()
                M0z = cA * dA[3][c].toarray()
                for x, k in cq:
                    Q += k * x[c].toarray()
                loc = np.full(Nobs, -1)
                loc[c] = np.arange(len(c))
                f_ = Fpos[c] >= 0
                QF[Fpos[c][f_]] = Q[f_]
                for tri, row, al, v, sheet in hal:
                    m = np.flatnonzero(loc[tri] >= 0)
                    if not len(m):
                        continue
                    j = loc[tri[m]]
                    val = al[m, None] * (1j * w * (
                        M1[j] - v[m, :1] * M0x[j] - v[m, 1:2] * M0y[j]
                        - v[m, 2:] * M0z[j]) + 2 * Q[j] / (1j * w))
                    if sheet:  # (an edge once in each of T+'s, T-'s)
                        Bw[row[m]] += val
                    else:
                        Cw += sparse.csr_matrix(
                            (np.ones(len(m)), (row[m], np.arange(len(m)))),
                            (N, len(m))) @ val
        return Bw, 0.5 * (Cw + Cw.T), QF


def _areas(V):
    return 0.5 * np.linalg.norm(np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0]),
                                axis=1)


def _total(gt):
    """(A, Q, dr): a Green table's full G_A and G_q (the extracted parts
    added back, 1/rho at rho < dr as at dr) on its own uniform grid."""
    A, Q = gt.A_rest.astype(complex), gt.Q_rest.astype(complex)
    dr = float(gt.rho[1])
    for d, cA, cq in gt.extracted():
        inv = 1.0 / np.maximum(np.hypot(gt.rho, d), dr)
        A, Q = A + cA * inv, Q + cq * inv
    return np.ascontiguousarray(A), np.ascontiguousarray(Q), dr


def _rts(core, px, py, W, vx, vy, table, dr):
    """sum_q table(|P[t, q] - V[v]|) W[t, q] (radial_table_sum: the compiled
    core's, else NumPy's in chunks)."""
    px, py, W = (np.ascontiguousarray(a, float) for a in (px, py, W))
    vx, vy = np.ascontiguousarray(vx, float), np.ascontiguousarray(vy, float)
    if core is not None and hasattr(core, 'radial_table_sum'):
        return core.radial_table_sum(px, py, W, vx, vy,
                                     np.ascontiguousarray(table, complex), dr)
    out = np.zeros((len(px), len(vx)), complex)
    top = len(table) - 1.000001
    for c in range(0, len(px), 256):
        s = slice(c, c + 256)
        d = np.hypot(px[s][:, :, None] - vx, py[s][:, :, None] - vy)
        u = np.minimum(d / dr, top)
        i = u.astype(np.int64)
        fr = u - i
        out[s] = ((table[i] * (1 - fr) + table[i + 1] * fr)
                  * W[s][:, :, None]).sum(1)
    return out
