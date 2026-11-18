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
the next plate along the boundary (the current along the side). In mixed
precision the solver takes the verticals as loops (Sides.loops), which keeps
the complex64 LU a good preconditioner.

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
from kicad2ems.pcbqs.panels3d import _rule

from .sing import offplane_pairs

# near pairs: centroids closer than this x the larger triangle's size
NEAR = 4.0


class Sides:
    """The plates of a Simulation's mesh and their functions. t: {interface:
    copper thickness (m)}, up: {interface: True (the plate above the sheet) |
    False (below)}, s_max (m) or None (every boundary edge)."""

    def __init__(self, sim, t, up, s_max=None, gaps=None):
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
        # the boundary nodes on a gap line (a port's gap, a lumped element):
        # the sides break there (the element's current, the port's, is the
        # sheet's edges'; a side through a combline's loading capacitors
        # shorted them, its passband 2.4 -> 6.4 GHz). Through a wave port's
        # sample cuts they run on (across: their share of the cut's current;
        # broken there, a fit's reference line had its Zc 50.9 -> 53.2 ohm)
        self.cut_node = set()
        for k, segs in (gaps or {}).items():
            self.cut_node.update(self._on(sim, k, segs).tolist())
        if self.np_:
            self._functions(sim, po)
        self._near = self._hal = None

    def _on(self, sim, k, segs):
        """The plates' end nodes (ib) on interface k on the segments."""
        on = np.flatnonzero(self.k == k)
        if not len(on) or not segs:
            return np.zeros(0, int)
        ln = shapely.unary_union([shapely.LineString(sg) for sg in segs])
        d = shapely.distance(shapely.points(sim.rwg.nodes[self.ib[on]]), ln)
        return self.ib[on][d < 1e-9]

    def across(self, sim, k, seg, d, tol=0.02):
        """(functions, weights): the current along the sides across the cut
        line seg on interface k, in the direction d (its normal): through the
        vertical section of each plate the line crosses (at its end node, or
        inside it where the cut's end node is not the copper's edge: on boards
        a sliver inside, 0.25 um, the plate's run across), within tol x the
        cut's length of its ends. A half alpha (r - v) through a straight
        section: alpha l ((r_mid - v) . n), n the section's normal in the
        plate."""
        nd = sim.rwg.nodes
        s0, s1 = np.asarray(seg[0], float), np.asarray(seg[1], float)
        c, w = 0.5 * (s0 + s1), np.hypot(*(s1 - s0))
        tt = (s1 - s0) / w
        sa, sb = (nd[self.ia] - c) @ d, (nd[self.ib] - c) @ d
        eps = 1e-9 * w
        sa, sb = np.where(abs(sa) < eps, 0, sa), np.where(abs(sb) < eps, 0, sb)
        # crossed at u in (0, 1]: an end node on the line once
        x = (self.k == k) & (sa != 0) & (sa * sb <= 0)
        u = np.where(x, sa / np.where(x, sa - sb, 1.0), 0.0)
        pt = nd[self.ia] + u[:, None] * (nd[self.ib] - nd[self.ia])
        x &= abs((pt - c) @ tt) <= (0.5 + tol) * w
        fn, wt = [], []
        for i in np.flatnonzero(x):
            tv = (nd[self.ib[i]] - nd[self.ia[i]]) / self.L[i]
            if abs(tv @ d) < 0.3:
                continue
            n = np.r_[np.sign(tv @ d) * tv, 0.0]
            ui, t, h = u[i], self.t[i], self.h[i]
            # the section in the triangles A (0 .. u t) and B (u t .. t)
            for tri, ln, zm in ((2 * i, ui * t, 0.5 * ui * h),
                                (2 * i + 1, (1 - ui) * t,
                                 0.5 * (1 + ui) * h)):
                for j in self._pt_halves.get(tri, ()):
                    fn.append(self.h_fn[j])
                    wt.append(self.h_alpha[j] * ln
                              * ((np.r_[pt[i], zm] - self.h_v[j]) @ n))
        return np.array(fn, int), np.array(wt, float)

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
        H, vert, fold = [], [], []
        nf = 0
        for i in range(P):
            T, A_, B_ = int(self.T[i]), 2 * i, 2 * i + 1
            AT = abs(r.area[T])
            fold.append(nf)
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
                vert.append((nf, i, j))
                nf += 1
        self.N = nf
        # plate i's fold (function fold[i]; its diagonal fold[i] + 1), the
        # verticals (function, plate, next plate)
        self.fold = np.array(fold, int)
        self.vert = np.array(vert, int).reshape(-1, 3)
        self.h_fn = np.array([x[0] for x in H], int)
        kind = np.array([x[1] for x in H], int)
        tri = np.array([x[2] for x in H], int)
        self._pt_halves = {}  # plate triangle: its halves
        for j in np.flatnonzero(kind == 1):
            self._pt_halves.setdefault(int(tri[j]), []).append(j)
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
        # the source points' functions (CSR by point 3 s + q): the half's
        # current (x, y) and charge there, the rule's weight in
        rows = (3 * self.h_src[:, None] + np.arange(3)[None]).ravel()
        wts = self.sw[self.h_src]
        f = self.h_alpha[:, None, None] * wts[..., None] * (
            self.sp[self.h_src] - self.h_v[:, None, :])
        k = np.argsort(rows, kind='stable')
        self.src_csr = (
            np.r_[0, np.cumsum(np.bincount(rows, minlength=3 * len(
                self.src_V)))], np.repeat(self.h_fn, 3)[k],
            f[..., 0].ravel()[k], f[..., 1].ravel()[k],
            (2 * self.h_alpha[:, None] * wts).ravel()[k])
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
        layer's cq, 1 / (4 pi eps0 eps1), G / (4 pi eps0 eps1)): the values of
        one (Nobs, N) pattern (ptr, functions)."""
        if self._near is not None:
            return self._near
        core = _need_core()
        Vo, so, Po, Wo = self._obs_geometry(sim)
        Vs = self.src_V
        ss = np.linalg.norm(Vs - np.roll(Vs, 1, axis=1), axis=2).max(1)
        co, cs = Vo.mean(1), Vs.mean(1)
        to, ts = cKDTree(co), cKDTree(cs)
        # the pairs either way round, once each, sorted (o, s)
        ls = to.query_ball_point(cs, NEAR * ss)
        lo = ts.query_ball_point(co, NEAR * so)
        n = np.fromiter(map(len, ls), int, len(ls))
        m = np.fromiter(map(len, lo), int, len(lo))
        key = np.unique(np.r_[
            np.concatenate([*ls, []]).astype(int) * len(Vs)
            + np.repeat(np.arange(len(Vs)), n),
            np.repeat(np.arange(len(Vo)), m) * len(Vs)
            + np.concatenate([*lo, []]).astype(int)])
        o, s = key // len(Vs), key % len(Vs)
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
            for sel, flip in ((m & ~sw, False), (m & sw, True)):
                if not sel.any():
                    continue
                A, B = Vo[o[sel]], Vs[s[sel]]
                q0, qo, qs, qr = _pm(core, *((B, A) if flip else (A, B)), lev)
                if flip:
                    qo, qs = qs, qo
                m0[sel], mro[sel], mrs[sel], mrr[sel] = q0, qo, qs, qr
        both = (o >= self.Nt) & self.src_pt[s]
        img = np.zeros(len(o))
        if both.any():
            Vm = Vs[s[both]].copy()
            Vm[..., 2] *= -1.0
            Vm = Vm[:, ::-1]  # (keep the triangle counter-clockwise)
            img[both] = _pm(core, Vo[o[both]], Vm, 2, scalar_only=True)
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
        # per pair and source half: the half's contributions (the pairs in
        # order, a pair's halves in their order)
        hs = np.argsort(self.h_src, kind='stable')
        cnt = np.bincount(self.h_src, minlength=len(Vs))
        start = np.r_[0, np.cumsum(cnt)[:-1]]
        pr = np.repeat(np.arange(len(s)), cnt[s])
        hh = hs[start[s[pr]] + np.arange(len(pr))
                - np.repeat(np.cumsum(cnt[s]) - cnt[s], cnt[s])]
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
        # one pattern (CSR by observer) for all of them
        key, inv = np.unique(o[pr] * self.N + self.h_fn[hh],
                             return_inverse=True)
        mk = lambda x: np.bincount(inv, x, len(key))
        ptr = np.searchsorted(key // self.N, np.arange(len(Vo) + 1))
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
        pat = (ptr, (key % self.N).astype(np.int32))
        self._near = (pat, dA, dq, Po, Wo)
        return self._near

    def loops(self, sim):
        """W (N x N, sparse; nonzero in the verticals' columns only, on the
        other unknowns' rows): T = I + W makes each vertical a loop (its
        charge on its plate taken off by the plate's fold, on the next plate's
        triangle by that one's diagonal and fold, the folds' charge on the
        sheet carried by the sheet's edges around their node). A plate's own
        functions are charge-dominated (its triangles t high), their loops
        lost below that in the equilibrated matrix: rf_meander at 0.5 GHz had
        cond 1.4e9 (sheets 3.5e7) and took 84 complex64-LU-preconditioned
        GMRES iterations (sheets 30); in the loops 2.8e8 and 21. (A fold's
        charge on its sheet triangle meets the sides' rule, the edges' the
        strips': the loops keep ~1e-4 of it; cancelled exactly, it took a few
        iterations more off at most.)"""
        r = sim.rwg
        c0 = sim.Ne + sim.Nv - self.N
        edge = {}
        for e, (p, m) in enumerate(zip(r.tp.tolist(), r.tm.tolist())):
            edge[(p, m)], edge[(m, p)] = (e, 1.0), (e, -1.0)
        need = set(self.ib[self.vert[:, 1]].tolist()) if len(self.vert) else ()
        fan = {}
        for t, tr in enumerate(r.tris.tolist()):
            for v in tr:
                if v in need:
                    fan.setdefault(v, []).append(t)
        ri, ci, vv = [], [], []
        ld = np.hypot(self.L, self.t)
        for n, i, j in self.vert.tolist():
            t = self.t[i]
            col = c0 + n
            # the charge integrals (2 alpha x area): a vertical t on A_i, -t
            # on B_j; a diagonal ld on A, -ld on B; a fold L on its sheet
            # triangle, -L on A
            add = [(c0 + self.fold[j] + 1, -t / ld[j]),
                   (c0 + self.fold[j], -t / self.L[j]),
                   (c0 + self.fold[i], t / self.L[i])]
            Ti, Tj = int(self.T[i]), int(self.T[j])
            if Ti != Tj:  # t from Ti to Tj through the triangles at the node
                ts = set(fan[int(self.ib[i])])
                prev, todo = {Ti: None}, [Ti]
                while todo and Tj not in prev:
                    X = todo.pop(0)
                    for Y in ts:
                        if Y not in prev and (X, Y) in edge:
                            prev[Y] = X
                            todo.append(Y)
                Y = Tj
                while Y in prev and prev[Y] is not None:
                    e, sg = edge[(prev[Y], Y)]
                    add.append((e, -t / (sg * r.l[e])))
                    Y = prev[Y]
                if Y != Ti:
                    continue  # (no way round: the vertical stays)
            for row, x in add:
                ri.append(row)
                ci.append(col)
                vv.append(x)
        N = sim.Ne + sim.Nv
        return sparse.csc_matrix((vv, (ri, ci)), (N, N))

    def via_d(self, sim, vk, geo):
        """(N, chains): the via chains' smooth potential (via.ViaKernels.D,
        its E_t = grad D on an interface) tested with each function's charge,
        as sim._via_blocks tests the strips' (3-point rule)."""
        from .fast import _core
        Po, Wo = self.near(sim)[3:]
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
        sim.tables (sim.green_tables(f), as the strips' blocks); the sums in
        the compiled core (sides_blocks)."""
        core = _need_core()
        r, st, w = sim.rwg, sim.st.at(f), 2 * np.pi * f
        (ptr, fn), dA, dq, Po, Wo = self.near(sim)
        Nobs, N = len(Po), self.N
        cA = MU0 / (4 * np.pi)
        # the charge corrections at f: the layers' coefficients
        nQ = np.zeros(len(fn), complex)
        for k, (x1, x2, x3) in dq.items():
            up = self.h[self.k == k][0] > 0
            e1, e2 = ((st.eps(k + 1), st.eps(k)) if up else
                      (st.eps(k), st.eps(k + 1)))
            c1 = 1.0 / (4 * np.pi * EPS0 * e1)
            nQ += x1 / (2 * np.pi * EPS0 * st.eps_sum(k))
            nQ += c1 * x2 + c1 * (e1 - e2) / (e1 + e2) * x3
        lay = sorted(set(sim.layers) | set(self.layers))
        li = {k: i for i, k in enumerate(lay)}
        lt = np.full((len(lay), len(lay)), -1, np.int32)
        tabs, drs = [], []
        for ko in sim.layers:
            for ks in self.layers:
                A, Q, dr = _total(sim.tables[(min(ko, ks), max(ko, ks))])
                lt[li[ko], li[ks]] = len(tabs)
                tabs.append(np.stack([A, Q], 1))
                drs.append(dr)
        olay = np.array([li[k] if k in sim.layers else -1
                         for k in self.obs_layer.tolist()], np.int32)
        slay = np.array([li[k] for k in self.src_layer.tolist()], np.int32)
        hptr, tgt, al, v = self._halves(sim)
        fpos = np.full(Nobs, -1, np.int32)
        fpos[sim.F] = np.arange(len(sim.F))
        sptr, sfn, sfx, sfy, sdq = self.src_csr
        chunk = int(np.clip(2 ** 28 // (80 * N), 64, 2048))
        out, QF = core.sides_blocks(
            Po, Wo, olay, self.sp, slay, lt, tabs, np.array(drs), sptr, sfn,
            sfx, sfy, sdq, ptr, fn, cA * dA[0], cA * dA[1], cA * dA[2],
            cA * dA[3], nQ, hptr, tgt, al, v, fpos, r.Ne + N, N, len(sim.F),
            w, chunk)
        Cw = out[r.Ne:]
        return out[:r.Ne], 0.5 * (Cw + Cw.T), QF

    def _halves(self, sim):
        """The halves by observer (a CSR: ptr, target row, alpha, v (3D)): the
        sheet edges' (T+, T-; rows 0..Ne - 1), the functions' (Ne + n)."""
        if self._hal is None:
            r = sim.rwg
            z = np.zeros((r.Ne, 1))
            obs = np.r_[r.tp, r.tm, self.h_obs]
            k = np.argsort(obs, kind='stable')
            self._hal = (
                np.r_[0, np.cumsum(np.bincount(obs, minlength=len(
                    self.obs_layer)))],
                np.r_[np.arange(r.Ne), np.arange(r.Ne),
                      r.Ne + self.h_fn][k].astype(np.int32),
                np.r_[r.l / (2 * r.area[r.tp]), -r.l / (2 * r.area[r.tm]),
                      self.h_alpha][k],
                np.concatenate([np.c_[r.vp, z], np.c_[r.vm, z], self.h_v])[k])
        return self._hal


def _need_core():
    from .fast import _core
    if _core is None or not hasattr(_core, 'sides_blocks'):
        raise RuntimeError("thickness 'sides' needs mom25d's compiled core "
                           "(python -m kicad2ems.mom25d.core.build)")
    return _core


def _pm(core, Vo, Vs, levels, scalar_only=False):
    """pcbqs.panels3d.pair_moments (outer_levels=levels), the core's."""
    bar, w = _rule(levels)
    return core.pair_moments3d(Vo, Vs, bar, w, scalar_only)


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
