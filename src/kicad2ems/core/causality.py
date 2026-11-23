"""
Causality of band-limited S-parameters (R-E2): the generalised dispersion
relations with subtractions (Triverio, Grivet-Talocia, "Robust causality
characterization via generalized dispersion relations", IEEE T-ADVP 2008).

A causal response (exp(+jwt): analytic in the lower half of the w plane) is its
own Hilbert transform. With n subtraction points w_k in the band, P the
polynomial through H(w_k) and Pi(w) = prod (w - w_k), the function (H - P) / Pi
is causal too and falls off as 1/w, so

    H(w) = P(w) + Pi(w) / (j pi) PV int H(w') / (Pi(w') (w - w')) dw'
        (over the whole line)

Only the band B = [fmin, fmax] (and its mirror, H(-w) = H(w)*) is known. Its
part is computed (the P / Pi share in closed form, the smooth rest by quadrature
with the singularity at w taken out), and the rest is bounded with |H| <= 1 (a
passive S): the truncation bound

    T(w) = |Pi(w)| / pi int_{not B} dw' / (|Pi(w')| |w - w'|)

which the subtractions make small inside the band (the kernel falls off as 1 /
w'^(n+1)). The data are causal to within the evidence where |H - H_r| <= T + the
quadrature's own error (from the same reconstruction of a causal test function
on the same samples); a larger miss is a causality violation: a reference plane
past the true one (a delay removed that was not there), a time-reversed or badly
interpolated response.

    r = check(freq, S)  # dict: worst (|H - H_r| - bound) over the terms, at
                        # which f and term, ...
"""
import numpy as np


def _points(f, n):
    """n subtraction frequencies spread over the band (Chebyshev-like: denser
    near its ends), snapped to samples."""
    k = np.arange(n)
    x = 0.5 * (1 - np.cos(np.pi * (k + 0.5) / n))
    return np.unique(
        np.clip(np.round(x * (len(f) - 1)).astype(int), 1,
                len(f) - 2))


def reconstruct(f, h, n):
    """(H_r, T) on the samples f (Hz, ascending, from fmin > 0) of one term h,
    with n subtractions (_operator's, applied)."""
    R1, R2, T = _operator(np.asarray(f, float), n)
    h = np.asarray(h, complex)
    return R1 @ h + R2 @ np.conj(h), T


# Gauss-Legendre's 64 nodes (_rest's: computed afresh each call they had been
# a third of a 92-port piece's check)
_GL = np.polynomial.legendre.leggauss(64)


def _operator(f, n):
    """The reconstruction as matrices: H_r = R1 h + R2 conj(h) for any term h
    on the samples f, with n subtractions, and its truncation bound T (the
    term's own share nowhere: the subtraction points, the kernels and the
    bound are the band's). (R1, R2, T). The data enter linearly: the
    interpolating polynomial P through h at the w_k, G = (h - P) / Pi on the
    band, its mirror band's conj(h) / Pi; each integral a fixed matrix on
    them (product integration: G linear between samples, the kernel
    1 / (w - w') exact on each interval)."""
    f = np.asarray(f, float)
    F = len(f)
    ks = _points(f, n)
    wk = f[ks]
    m = len(wk)
    Pi = lambda x: np.prod(np.subtract.outer(np.atleast_1d(x), wk), axis=-1)
    dPi = np.array([
        np.prod([wk[i] - wk[j] for j in range(m) if j != i]) for i in range(m)
    ])

    def basis(x):  # Lagrange's basis at x: P(x) = basis(x) @ h[ks]
        x = np.atleast_1d(x)
        out = np.ones((len(x), m))
        for i in range(m):
            for j in range(m):
                if j != i:
                    out[:, i] *= (x - wk[j]) / (wk[i] - wk[j])
        return out
    a, b = f[0], f[-1]
    x, dx = f, np.diff(f)
    PiB, PiBm, Pw = Pi(f), Pi(-f), Pi(f)
    Lb = basis(x)
    I = np.eye(F)
    Sel = I[ks]                      # h -> h[ks]
    P = Lb @ Sel                     # h -> P(x)
    # G = (h - P h) / Pi, zero at the w_k, then each w_k's value its
    # neighbours' mean (in order: an earlier w_k's already set)
    Gm = (I - P) / np.where(np.abs(PiB) > 0, PiB, np.inf)[:, None]
    Gm[ks] = 0.0
    for k in ks:
        Gm[k] = 0.5 * ((Gm[k - 1] if k > 0 else 0) +
                       (Gm[k + 1] if k + 1 < F else 0))
    D = np.diff(I, axis=0) / dx[:, None]     # v -> diff(v) / dx
    with np.errstate(divide='ignore', invalid='ignore'):
        L = np.log(np.abs(np.subtract.outer(x, x)))
        L[np.arange(F), np.arange(F)] = 0.0  # (the PV: they cancel)
        DL = L[:, :-1] - L[:, 1:]
        # sum_j (G_j + sG_j (x_i - x_j)) DL_ij - sum_j sG_j dx_j
        Ip = (DL @ Gm[:-1] + ((x[:, None] - x[None, :-1]) * DL - dx[None, :])
              @ (D @ Gm))
        # the P / Pi share, in closed form: sum_k c_k Q_k(x), c = h[ks] / dPi
        Q = np.zeros((F, m))
        for i, wkk in enumerate(wk):
            q = (1.0 / (x - wkk) * (np.log(abs((b - wkk) / (a - wkk)))
                                    + np.log(np.abs((x - a) / (x - b)))))
            Q[:, i] = np.where(np.isfinite(q), q, 0.0)
        Ip = Ip + (Q / dPi[None, :]) @ Sel
        # the mirror band: w' = -x, H = conj(h): K = conj(h) / Pi(-x)
        Km = np.diag(1.0 / PiBm)
        M = np.log((x[:, None] + x[None, 1:]) / (x[:, None] + x[None, :-1]))
        Im = (M @ Km[:-1] + (dx[None, :] - (x[:, None] + x[None, :-1]) * M)
              @ (D @ Km))
    c = Pw[:, None] / (1j * np.pi)
    return (P + c * Ip, c * Im,
            np.abs(Pw) / np.pi * _rest(x, a, b, Pi, m))


def _rest(w, a, b, Pi, m):
    """int over (-a, a) and |w'| > b of dw' / (|Pi(w')| |w - w'|) for each w
    (array), m subtractions: Gauss-Legendre on the low gap and on b..10 b in
    log, the tail beyond in closed form (the integrand as 1 / w'^(m+1)
    there)."""
    w = np.atleast_1d(w)[:, None]
    x, wq = _GL
    lo = a * x
    s = np.sum(wq * a / (np.abs(Pi(lo)) * np.abs(w - lo)), axis=1)
    u = np.log(b) + (np.log(10 * b) - np.log(b)) * (x + 1) / 2
    jac = (np.log(10 * b) - np.log(b)) / 2 * np.exp(u)
    for sgn in (1, -1):
        t = sgn * np.exp(u)
        s += np.sum(wq * jac / (np.abs(Pi(t)) * np.abs(w - t)), axis=1)
    # int_{10b}^inf w'^-(m+1) dw', both sides
    return s + 2 * (10 * b) ** (-m) / m


def check(freq, S, ns=range(2, 9)):
    """Each S term's causality on the inner 80 % of the band (at its edges the
    unknown data just past them decide it): dict(worst, f, term, n, terms).
    worst is the largest miss beyond the bound, max(|H - H_r| - T - e_n), over
    the terms and over n subtractions: each n is a test of its own (few: large
    bounds but the data's own shape decides; many: small bounds, but the
    interpolating polynomial absorbs a violation), so a miss at any one is
    evidence. e_n: the reconstruction's own error on these samples (a causal
    test response's). 0: causal within the evidence. Every term at once, a
    few products of _operator's matrices with them for each n (a term at a
    time it had been 46 s of a 92-port Thor window's 107)."""
    f = np.asarray(freq, float)
    S = np.asarray(S)
    if f[0] <= 0 or len(f) < 16:
        return None
    nt = S.shape[1]
    mid = slice(len(f) // 10, len(f) - len(f) // 10)
    fm = f[mid]
    pairs = [(i, j) for i in range(nt) for j in range(i + 1)
             if np.all(np.isfinite(S[:, i, j]))]
    if not pairs:
        return dict(worst=0.0, f=None, term=None, n=None, terms=[])
    H = np.stack([S[:, i, j] for i, j in pairs], 1).astype(complex)
    best = np.full(len(pairs), -np.inf)
    bn = np.zeros(len(pairs), int)
    bf, be, bb = (np.zeros(len(pairs)) for _ in range(3))
    for k in ns:
        R1, R2, T = _operator(f, k)
        e = _quadrature_error(f, k, (R1, R2, T))
        err = np.abs(H - (R1 @ H + R2 @ np.conj(H)))
        miss = (err - (T + e)[:, None])[mid]
        ii = np.argmax(miss, axis=0)
        cols = np.arange(len(pairs))
        mm = miss[ii, cols]
        up = mm > best
        best[up], bn[up] = mm[up], k
        bf[up], be[up] = fm[ii[up]], err[mid][ii[up], cols[up]]
        bb[up] = (T[mid] + e)[ii[up]]
    out = [dict(term=pairs[t], n=int(bn[t]), worst=max(float(best[t]), 0.0),
                f=float(bf[t]), err=float(be[t]), bound=float(bb[t]))
           for t in range(len(pairs))]
    w = int(np.argmax(best))
    if best[w] > 0.0:
        return dict(worst=float(best[w]), f=float(bf[w]), term=pairs[w],
                    n=int(bn[w]), terms=out)
    return dict(worst=0.0, f=None, term=None, n=None, terms=out)


def _quadrature_error(f, n, op=None):
    """The reconstruction's own error with n subtractions on these samples:
    causal test responses (a damped resonance in the band with a delay, and a
    smooth low-pass) reconstructed as the data are, their largest miss beyond
    the bound on the inner band, and 1e-3 more."""
    a, b = f[0], f[-1]
    f0 = 0.5 * (a + b)
    tests = [
        0.8 * np.exp(-2j * np.pi * f * (0.5 / (b - a))) /
        (1 + 1j * q * (f / f0 - f0 / f)) for q in (5.0, 20.0)
    ]
    tests.append(0.9 / (1 + 1j * f / (2 * b)))
    mid = slice(len(f) // 10, len(f) - len(f) // 10)
    worst = 0.0
    R1, R2, T = op or _operator(f, n)
    for h in tests:
        Hr = R1 @ h + R2 @ np.conj(h)
        worst = max(worst, float(np.max((np.abs(h - Hr) - T)[mid])))
    return max(worst, 0.0) + 1e-3
