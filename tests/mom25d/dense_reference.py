"""
NumPy references for mom25d's compiled kernels: what the solver had as its
'dense' path and NumPy kernels before the compiled core was required
(2026-11-18), kept here to check the core against.

    impedance(sim, f)   Z of a Simulation, every triangle pair integrated in
                        NumPy (the static singular parts, the smooth kernels'
                        centroid rule, the 3 / 6-point rules on the near
                        pairs with their extracted parts)
    sparams(sim, f)     sim.sparams(f) solved with that Z (LU)
    far_block(fo, rows, cols)   FastOperator.entry: the centroid rule's block
    fan_columns(fo)     FastOperator._fan_columns: the G_q moments of every
                        triangle with the via fans' (fo.setup(f, 'sparse')
                        first)
"""
from types import SimpleNamespace

import numpy as np

from kicad2ems.emsdev.numerics.base import MU0, EPS0, qform, combine
from kicad2ems.mom25d.base import (singular_moments, gram_matrix, moments7,
                                   quad_moments)
from kicad2ems.mom25d.sing import offplane_pairs, offplane_moments


def _sym(M):
    return 0.5 * (M + M.T)


def _static(sim, f):
    """(SA, SQ, Qs_F): the static singular parts of each layer at f."""
    r, Nt, st = sim.rwg, len(sim.rwg.tris), sim.st.at(f)
    M0, Mrr = np.zeros((Nt, Nt)), np.zeros((Nt, Nt))
    Mr, Qs = np.zeros((Nt, Nt, 2)), np.zeros((Nt, Nt), complex)
    for k in sim.layers:
        idx = sim.tri_idx[k]
        m0, mr, mrr = singular_moments(
            SimpleNamespace(nodes=r.nodes, tris=r.tris[idx], area=r.area[idx]))
        ix = np.ix_(idx, idx)
        M0[ix], Mrr[ix], Mr[ix] = _sym(m0), _sym(mrr), mr
        Qs[ix] = M0[ix] / (2 * np.pi * EPS0 * st.eps_sum(k))
    SA = _sym(MU0 / (4 * np.pi) * combine(r, Mrr, Mr[..., 0], Mr[..., 1], M0))
    return SA, _sym(qform(r.D, Qs, r.D)), Qs[:, sim.F]


def _block(sim, ia, ib, a, b, gt, cache):
    r, st = sim.rwg, sim.st
    ca, cb = r.cent[ia], r.cent[ib]
    rho = np.sqrt(((ca[:, None, :] - cb[None, :, :]) ** 2).sum(-1))
    GA, GQ = gt.AQ(rho)
    xs = a != b and gt.cA != 0   # near-singular cross-layer part extracted
    im = a == b and gt.dimg > 0  # nearest image of a thin layer extracted
    if xs:
        R3 = np.sqrt(rho ** 2 + gt.d ** 2)
        GA, GQ = GA + gt.cA / R3, GQ + gt.cq / R3
    wgt = r.area[ia][:, None] * r.area[ib][None, :]
    out = moments7(GA * wgt, GQ * wgt, ca[:, None], cb[None])
    Ls = abs(st.z[a] - st.z[b]) if a != b else 2 * min(
        st.layers[a - 1].h, (st.layers[a].h if a < st.N else np.inf))
    size = np.maximum(sim.tri_size[ia][:, None], sim.tri_size[ib][None, :])
    p, q = np.nonzero(rho ** 2 + Ls ** 2 < (sim.near_fac * size) ** 2)
    rule = (sim.P3, sim.W3) if a == b else (sim.P6, sim.W6)
    for M, v in zip(out, quad_moments(*rule, ia[p], ib[q], gt)):
        M[p, q] = v
    for flag, dd, cA, cq, allp in ((xs, gt.d, gt.cA, gt.cq, False),
                                   (im, gt.dimg, gt.cAi, gt.cqi, True)):
        if not flag:
            continue
        pp, qq = ((np.repeat(np.arange(len(ia)), len(ib)),
                   np.tile(np.arange(len(ib)), len(ia))) if allp else (p, q))
        if (a, b, dd) not in cache:
            cache[(a, b, dd)] = offplane_pairs(r.nodes, r.tris, ia[pp], ib[qq],
                                               dd)
        for M, v in zip(out, offplane_moments(cA, cq, cache[(a, b, dd)])):
            M[pp, qq] += v
    return out


def impedance(sim, f):
    """The (Ne + Nv) square Z of sim at f, in NumPy (the via rows and columns
    from sim._via_blocks with these fan charges)."""
    w, r = 2 * np.pi * f, sim.rwg
    Nt, cache = len(r.tris), {}
    A0, Axo, Ayo, Arr, Q0 = (np.zeros((Nt, Nt), complex) for _ in range(5))
    for (a, b), gt in sim.green_tables(f).items():
        ia, ib = sim.tri_idx[a], sim.tri_idx[b]
        a0, axo, ayo, axs, ays, arr, q0 = _block(sim, ia, ib, a, b, gt, cache)
        ix = np.ix_(ia, ib)
        A0[ix], Axo[ix], Ayo[ix], Arr[ix], Q0[ix] = a0, axo, ayo, arr, q0
        if a != b:
            jx = np.ix_(ib, ia)
            A0[jx], Axo[jx], Ayo[jx], Arr[jx], Q0[jx] = (a0.T, axs.T, ays.T,
                                                         arr.T, q0.T)
    N = sim.Ne + sim.Nv
    Z = np.zeros((N, N), complex)
    SA, SQ, Qs_F = _static(sim, f)
    Q0 = _sym(Q0)
    Z[:sim.Ne, :sim.Ne] = (
        1j * w * (SA + combine(r, _sym(Arr), Axo, Ayo, _sym(A0)))
        + (SQ + qform(r.D, Q0, r.D)) / (1j * w))
    if sim.Nv:
        B, C = sim._via_blocks(f, Q0[:, sim.F] + Qs_F)
        Z[:sim.Ne, sim.Ne:], Z[sim.Ne:, :sim.Ne], Z[sim.Ne:, sim.Ne:] = (B, B.T,
                                                                         C)
    if sim.edge is not None:
        Z += sim.edge.matrix(sim, f).toarray()
    for el, (idx, sig) in zip(sim.lumped, sim.lumped_edges):
        u = np.zeros(N)
        u[idx] = sig * r.l[idx]
        Z += el.Z(f) * np.outer(u, u)
    if sim.metal is not None:
        zs = sim.metal.zs_edges(f, sim.edge_layer)
        Z[:sim.Ne, :sim.Ne] += ((zs[:, None] if np.ndim(zs) else zs)
                                * gram_matrix(r).toarray())
    return _sym(Z)


def _solve(Z, E):
    d = 1.0 / np.sqrt(np.abs(np.diagonal(Z)) + 1e-300)
    return d[:, None] * np.linalg.solve(Z * d[:, None] * d[None, :],
                                        d[:, None] * E)


def sparams(sim, f):
    """sim.sparams(f) with the currents from impedance()'s Z (projected on
    sim's quad cells where it has them)."""
    def currents(f, E):
        Z, T = impedance(sim, f), sim.qT
        if T is None:
            return _solve(Z, E)
        return T @ _solve(np.asarray(T.T @ (T.T @ Z.T).T), np.asarray(T.T @ E))
    sim._currents = currents
    try:
        return sim.sparams(f)
    finally:
        del sim._currents


def _kern(fo, a, b, rho):
    return fo.tables[(min(a, b), max(a, b))].total(rho)


def _far_halves(fo, hr, hc):
    """The centroid rule between half-bases hr and hc, every row with every
    column."""
    w, tr, tc = fo.w, fo.h_tri[hr], fo.h_tri[hc]
    cr, cc = fo.cent[tr][:, None], fo.cent[tc][None]
    rho = np.sqrt(((cr - cc) ** 2).sum(-1))
    lr, lc = fo.layer_of_tri[tr][:, None], fo.layer_of_tri[tc][None]
    GA, GQ = np.zeros(rho.shape, complex), np.zeros(rho.shape, complex)
    for a in np.unique(lr):
        for b in np.unique(lc):
            m = (lr == a) & (lc == b)
            if m.any():
                GA[m], GQ[m] = _kern(fo, a, b, rho[m])
    dot = ((cr - fo.h_v[hr][:, None]) * (cc - fo.h_v[hc][None])).sum(-1)
    return ((fo.h_alpha[hr] * fo.area[tr])[:, None]
            * (fo.h_alpha[hc] * fo.area[tc])[None]
            * (1j * w * dot * GA + 4 * GQ / (1j * w)))


def far_block(fo, rows, cols):
    """FastOperator.entry(rows, cols) in NumPy."""
    Ne, m, n = fo.sim.Ne, len(rows), len(cols)
    H = _far_halves(fo, np.concatenate([rows, rows + Ne]),
                    np.concatenate([cols, cols + Ne]))
    return H[:m, :n] + H[:m, n:] + H[m:, :n] + H[m:, n:]


def fan_columns(fo):
    """FastOperator._fan_columns in NumPy (fo set up with mode 'sparse': its
    near pairs' charge correction _qcorr)."""
    r, F, lt = fo.sim.rwg, fo.sim.F, fo.layer_of_tri
    QF = np.zeros((len(r.tris), len(F)), complex)
    for j, q in enumerate(F):
        rho = np.linalg.norm(r.cent - r.cent[q], axis=1)
        for a in fo.layers:
            m = lt == a
            QF[m, j] = _kern(fo, a, lt[q], rho[m])[1] * r.area[m] * r.area[q]
    pos, sel = {q: j for j, q in enumerate(F)}, np.isin(fo.nQ, F)
    np.add.at(QF, (fo.nP[sel], np.array([pos[q] for q in fo.nQ[sel]], int)),
              fo._qcorr[sel])
    return QF
