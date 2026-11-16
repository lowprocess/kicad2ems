"""
Causality of band-limited S-parameters (R-E2): the generalised dispersion relations with subtractions (Triverio,
Grivet-Talocia, "Robust causality characterization via generalized dispersion relations", IEEE T-ADVP 2008).

A causal response (exp(+jwt): analytic in the lower half of the w plane) is its own Hilbert transform. With n
subtraction points w_k in the band, P the polynomial through H(w_k) and Pi(w) = prod (w - w_k), the function
(H - P) / Pi is causal too and falls off as 1/w, so

    H(w) = P(w) + Pi(w) / (j pi) PV int H(w') / (Pi(w') (w - w')) dw'                         (over the whole line)

Only the band B = [fmin, fmax] (and its mirror, H(-w) = H(w)*) is known. Its part is computed (the P / Pi share in
closed form, the smooth rest by quadrature with the singularity at w taken out), and the rest is bounded with
|H| <= 1 (a passive S): the truncation bound

    T(w) = |Pi(w)| / pi int_{not B} dw' / (|Pi(w')| |w - w'|)

which the subtractions make small inside the band (the kernel falls off as 1 / w'^(n+1)). The data are causal to
within the evidence where |H - H_r| <= T + the quadrature's own error (from the same reconstruction of a causal
test function on the same samples); a larger miss is a causality violation: a reference plane past the true one
(a delay removed that was not there), a time-reversed or badly interpolated response.

    r = check(freq, S)          # dict: worst (|H - H_r| - bound) over the terms, at which f and term, ...
"""
import numpy as np


def _points(f, n):
    """n subtraction frequencies spread over the band (Chebyshev-like: denser near its ends), snapped to samples."""
    k = np.arange(n)
    x = 0.5 * (1 - np.cos(np.pi * (k + 0.5) / n))
    return np.unique(np.clip(np.round(x * (len(f) - 1)).astype(int), 1, len(f) - 2))


def reconstruct(f, h, n):
    """(H_r, T) on the samples f (Hz, ascending, from fmin > 0) of one term h, with n subtractions."""
    f = np.asarray(f, float)
    h = np.asarray(h, complex)
    ks = _points(f, n)
    wk, hk = f[ks], h[ks]
    m = len(wk)
    Pi = lambda x: np.prod(np.subtract.outer(np.atleast_1d(x), wk), axis=-1)
    dPi = np.array([np.prod([wk[i] - wk[j] for j in range(m) if j != i]) for i in range(m)])
    c = hk / dPi                                                    # P / Pi = sum c_k / (w - w_k)
    def lagrange(x):
        x = np.atleast_1d(x)
        out = np.zeros(len(x), complex)
        for i in range(m):
            li = np.ones(len(x))
            for j in range(m):
                if j != i:
                    li = li * (x - wk[j]) / (wk[i] - wk[j])
            out += hk[i] * li
        return out

    # the whole line from the band and its mirror: w' in B+ (h) and B- (h*), the integrand H / (Pi (w - w'))
    a, b = f[0], f[-1]
    PiB, PiBm = Pi(f), Pi(-f)
    G = (h - lagrange(f)) / np.where(np.abs(PiB) > 0, PiB, np.inf)  # smooth on B+ (H - P vanishes at the w_k)
    G[ks] = 0.0
    for k in ks:                                                    # (its value there: the derivative's ratio)
        G[k] = 0.5 * ((G[k - 1] if k > 0 else 0) + (G[k + 1] if k + 1 < len(f) else 0))
    # H_r and T at every sample. The smooth parts by product integration: G (and on the mirror band H* / Pi) linear
    # between samples, the kernel 1 / (w - w') integrated exactly on each interval (second order; the logarithms at w
    # itself cancel between its two intervals, the principal value)
    x, dx = f, np.diff(f)
    with np.errstate(divide='ignore', invalid='ignore'):
        L = np.log(np.abs(np.subtract.outer(x, x)))
        L[np.arange(len(x)), np.arange(len(x))] = 0.0               # (the PV: they cancel)
        sG = np.diff(G) / dx
        A = G[None, :-1] + sG[None, :] * (x[:, None] - x[None, :-1])     # the linear piece of interval j, at w_i
        Ip = (A * (L[:, :-1] - L[:, 1:])).sum(1) - np.sum(sG * dx)
        for ck, wkk in zip(c, wk):                                  # the P / Pi share, in closed form
            t = ck / (x - wkk) * (np.log(abs((b - wkk) / (a - wkk))) + np.log(np.abs((x - a) / (x - b))))
            Ip += np.where(np.isfinite(t), t, 0.0)
        K = np.conj(h) / PiBm                                       # the mirror band: w' = -x, H = h*
        sK = np.diff(K) / dx
        Im = ((K[None, :-1] - sK[None, :] * (x[:, None] + x[None, :-1])) *
              np.log((x[:, None] + x[None, 1:]) / (x[:, None] + x[None, :-1]))).sum(1) + np.sum(sK * dx)
        Pw = Pi(x)
        return lagrange(x) + Pw / (1j * np.pi) * (Ip + Im), np.abs(Pw) / np.pi * _rest(x, a, b, Pi, m)


def _rest(w, a, b, Pi, m):
    """int over (-a, a) and |w'| > b of dw' / (|Pi(w')| |w - w'|) for each w (array), m subtractions: Gauss-Legendre
    on the low gap and on b..10 b in log, the tail beyond in closed form (the integrand as 1 / w'^(m+1) there)."""
    w = np.atleast_1d(w)[:, None]
    x, wq = np.polynomial.legendre.leggauss(64)
    lo = a * x
    s = np.sum(wq * a / (np.abs(Pi(lo)) * np.abs(w - lo)), axis=1)
    u = np.log(b) + (np.log(10 * b) - np.log(b)) * (x + 1) / 2
    jac = (np.log(10 * b) - np.log(b)) / 2 * np.exp(u)
    for sgn in (1, -1):
        t = sgn * np.exp(u)
        s += np.sum(wq * jac / (np.abs(Pi(t)) * np.abs(w - t)), axis=1)
    return s + 2 * (10 * b) ** (-m) / m                              # int_{10b}^inf w'^-(m+1) dw', both sides


def check(freq, S, ns=range(2, 9)):
    """Each S term's causality on the inner 80 % of the band (at its edges the unknown data just past them decide
    it): dict(worst, f, term, n, terms). worst is the largest miss beyond the bound, max(|H - H_r| - T - e_n), over
    the terms and over n subtractions: each n is a test of its own (few: large bounds but the data's own shape
    decides; many: small bounds, but the interpolating polynomial absorbs a violation), so a miss at any one is
    evidence. e_n: the reconstruction's own error on these samples (a causal test response's). 0: causal within the
    evidence."""
    f = np.asarray(freq, float)
    S = np.asarray(S)
    if f[0] <= 0 or len(f) < 16:
        return None
    nt = S.shape[1]
    e = {k: _quadrature_error(f, k) for k in ns}
    mid = slice(len(f) // 10, len(f) - len(f) // 10)
    out, worst = [], (0.0, None, None, None)
    for i in range(nt):
        for j in range(i + 1):
            h = S[:, i, j]
            if not np.all(np.isfinite(h)):
                continue
            best = (-np.inf, None, None, None, None)
            for k in ns:
                Hr, T = reconstruct(f, h, k)
                miss = (np.abs(h - Hr) - T - e[k])[mid]
                ii = int(np.argmax(miss))
                if miss[ii] > best[0]:
                    best = (float(miss[ii]), k, float(f[mid][ii]), float(np.abs(h - Hr)[mid][ii]),
                            float(T[mid][ii] + e[k]))
            out.append(dict(term=(i, j), n=best[1], worst=max(best[0], 0.0), f=best[2], err=best[3], bound=best[4]))
            if best[0] > worst[0]:
                worst = (best[0], best[2], (i, j), best[1])
    return dict(worst=worst[0], f=worst[1], term=worst[2], n=worst[3], terms=out)


def _quadrature_error(f, n):
    """The reconstruction's own error with n subtractions on these samples: causal test responses (a damped
    resonance in the band with a delay, and a smooth low-pass) reconstructed as the data are, their largest miss
    beyond the bound on the inner band, and 1e-3 more."""
    a, b = f[0], f[-1]
    f0 = 0.5 * (a + b)
    tests = [0.8 * np.exp(-2j * np.pi * f * (0.5 / (b - a))) / (1 + 1j * q * (f / f0 - f0 / f)) for q in (5.0, 20.0)]
    tests.append(0.9 / (1 + 1j * f / (2 * b)))
    mid = slice(len(f) // 10, len(f) - len(f) // 10)
    worst = 0.0
    for h in tests:
        Hr, T = reconstruct(f, h, n)
        worst = max(worst, float(np.max((np.abs(h - Hr) - T)[mid])))
    return max(worst, 0.0) + 1e-3
