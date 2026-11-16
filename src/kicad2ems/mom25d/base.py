"""
mom25d.base - the pieces of the 2.5D planar Method-of-Moments solver its layers
share beyond the RWG functions and rules of emsdev.numerics.base: the
frequency-independent 1/R moment integrals (the inner integral analytic, Wilton
et al.), the smooth kernels' moments by quadrature, the Gram matrix and the
travelling-wave fits on a port's feed line.

Time convention exp(+j w t).
"""
import numpy as np

from kicad2ems.emsdev.numerics.base import _BAR3, _BAR6, _W3, _W6, combine, edge_frames, quad_pts


def moments7(GA, GQ, ra, rb):
    """The seven moments of the kernels between points ra (observation) and rb
    (source), (..., 2) each: G_A x [1, x_a, y_a, x_b, y_b, r_a . r_b] and G_q
    (combine's triangle-pair moments, unsummed)."""
    return [
        GA, GA * ra[..., 0], GA * ra[..., 1], GA * rb[..., 0], GA * rb[..., 1],
        GA * (ra * rb).sum(-1), GQ
    ]


def quad_moments(P, W, p, q, gt, chunk=20000):
    """(7, len(p)) moments7 of the smooth kernels (gt.AQ) between triangles p
    and q by quadrature (points P, weights W per triangle)."""
    out = np.empty((7, len(p)), complex)
    for c0 in range(0, len(p), chunk):
        s = slice(c0, c0 + chunk)
        pa, pb = P[p[s]][:, :, None, :], P[q[s]][:, None, :, :]
        Wt = W[p[s]][:, :, None] * W[q[s]][:, None, :]
        ga, gq = gt.AQ(np.sqrt(((pa - pb) ** 2).sum(-1)))
        out[:, s] = [m.sum((1, 2)) for m in moments7(ga * Wt, gq * Wt, pa, pb)]
    return out


def singular_moments(rwg, nb=16):
    """
    Frequency-independent 1/R integrals between all triangle pairs, inner
    integral analytic:
      M0[p,q]  = int_p int_q 1/R          Mr[p,q,:] = int_p r  int_q 1/R
      Mrr[p,q] = int_p int_q r.r'/R
    """
    nodes, tris, Ar = rwg.nodes, rwg.tris, rwg.area
    Nt = len(tris)
    V = nodes[tris]
    _, L, tau, un = edge_frames(V)
    P = quad_pts(rwg, _BAR6)
    M0, Mr, Mrr = np.zeros((Nt, Nt)), np.zeros((Nt, Nt, 2)), np.zeros((Nt, Nt))
    for p0 in range(0, Nt, nb):
        sl = slice(p0, min(Nt, p0 + nb))
        r = P[sl]                                   # (nb,Q,2)
        d = V[None, None] - r[:, :, None, None, :]  # (nb,Q,Nt,3,2)
        sm = (d * tau[None, None]).sum(-1)
        sp_ = sm + L[None, None]
        t0 = (d * un[None, None]).sum(-1)
        tt = np.abs(t0)
        ok = tt > 1e-12 * L[None, None]
        ttS = np.where(ok, tt, 1.0)
        lg = np.where(ok, np.arcsinh(sp_ / ttS) - np.arcsinh(sm / ttS), 0.0)
        Rm, Rp = np.sqrt(t0 ** 2 + sm ** 2), np.sqrt(t0 ** 2 + sp_ ** 2)
        I1 = (t0 * lg).sum(-1)                                       # int_q 1/R
        eR = 0.5 * (sp_ * Rp - sm * Rm + t0 ** 2 * lg)
        IR = (eR[..., None] * un[None, None]).sum(-2)  # int_q (r'-r)/R
        W = IR + r[:, :, None, :] * I1[..., None]      # int_q r'/R
        wq = _W6[None, :, None]
        a = Ar[sl, None]
        M0[sl] = a * (wq * I1).sum(1)
        Mr[sl] = (a[..., None] *
                  (wq[..., None] * r[:, :, None, :] * I1[..., None]).sum(1))
        Mrr[sl] = a * (wq * (r[:, :, None, :] * W).sum(-1)).sum(1)
    return M0, Mr, Mrr


def gram_matrix(rwg):
    """int f_m . f_n dS  (for a surface-impedance term Zs * Gram)."""
    Ar, P = rwg.area, quad_pts(rwg, _BAR3)
    m = lambda v: np.diag(Ar * (_W3[None, :] * v).sum(1))
    return combine(rwg, m((P**2).sum(-1)), m(P[..., 0]), m(P[..., 1]),
                   np.diag(Ar))


# ---- The wave fits on a port's feed line ------------------------------------
def fit_gamma(Ik, step):
    """Propagation constant from uniformly spaced samples of a 2-exponential
    current."""
    c = (np.sum((Ik[:-2] + Ik[2:]) * np.conj(Ik[1:-1])) /
         (2 * np.sum(np.abs(Ik[1:-1])**2)))
    g = np.arccosh(c + 0j) / step
    return abs(g.real) + 1j * abs(g.imag)


def fit_waves(Ik, s, gamma):
    """I(s) = a e^{-g s} + b e^{+g s}  ->  (a, b) per excitation."""
    M = np.stack([np.exp(-gamma * s), np.exp(gamma * s)], 1)
    coef, *_ = np.linalg.lstsq(M, Ik, rcond=None)
    return coef[0], coef[1]
