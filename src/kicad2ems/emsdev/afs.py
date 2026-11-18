"""
emsdev.afs - an adaptive frequency sweep with rational (AAA) interpolation,
which mom25d's sweep and pcbqs's S-parameters share (it was mom25d's own).

A solve per frequency is the expensive step; S(f) of a passive structure is a
smooth rational function between its resonances. Fit one barycentric rational
model to all S entries at once (set-valued AAA), and only solve at the frequency
where two successive models disagree most, until they agree everywhere on the
band.

    r = aaa(z, F)  # F (n, m) at z (n,): r(zz) -> (len(zz), m)
    grid, S, fs, Ss, history = adaptive_sweep(solve, fmin, fmax, grid=freqs)

numpy is the one thing it needs (emsdev itself needs nothing: import this module
only where numpy is).
"""
import numpy as np


# The sweep's models stop at FIT of each entry's scale (aaa's tol): above
# the solves' noise (a mixed-precision solve's S: 1e-5 to 1e-4, by the
# roughness of 201 solves a board), well under the sweep's own tolerance. At
# 1e-10 a model chased the noise: with 4 ports (16 entries) it took a support
# at every sample until 36 of them left it free, rf_diffvia's 37 solves; its
# poles between the samples (|S| up to 6) kept two models apart, rf_ebg's 46.
FIT = 3e-4
# an entry's scale: its largest value, at least this x the largest of all (an
# entry near the noise's level otherwise weighs as the others do)
FLOOR = 0.3
# entries below this x the largest are not in the model: straight lines
# between their samples (a cut network's, at the noise's level: fitted, they
# took poles)
SMALL = 1e-3


def aaa(z, F, tol=1e-10, mmax=80):
    """Set-valued AAA (Nakatsukasa, Sete, Trefethen 2018; Lietaert et al. 2022).
    z: (n,) sample points, F: (n, m) values.  Returns r(zz) -> (len(zz), m).
    Supports are added until every sample is within tol of each entry's scale
    (FLOOR), at most as many as the samples determine: (n - supports) m rows
    for supports - 1 weights. (Past that the fit has no rows left: 6 ports' 36
    entries took every sample as a support up to 37 of them, the model one
    constant, rf_bends' 60 solves; 16 ports' never came back from it.) Entries
    under SMALL of the largest: straight lines between the samples."""
    z, F = np.asarray(z, complex), np.asarray(F, complex)
    F = F[:, None] if F.ndim == 1 else F
    n, m = F.shape
    top = max(np.abs(F).max(), 1e-300)
    small = np.abs(F).max(0) < SMALL * top
    if small.any() and not small.all():
        big, o = aaa(z, F[:, ~small], tol, mmax), np.argsort(z.real)
        zo, Fo = z.real[o], F[o][:, small]

        def r(zz):
            x = np.atleast_1d(np.asarray(zz, complex)).real
            out = np.empty((len(x), m), complex)
            out[:, ~small] = big(zz)
            out[:, small] = np.stack(
                [np.interp(x, zo, c.real) + 1j * np.interp(x, zo, c.imag)
                 for c in Fo.T], 1)
            return out
        r.support, r.weights = big.support, big.weights
        return r
    Fs = F / np.maximum(np.abs(F).max(0), FLOOR * top)
    J, sup, R, w = np.ones(n, bool), [], np.tile(Fs.mean(0), (n, 1)), None
    for _ in range(max(1, min(mmax, (n * m + 1) // (m + 1)))):
        err = np.abs(Fs - R).max(1)
        err[~J] = -1
        j = int(np.argmax(err))
        if err[j] <= tol and sup:
            break
        sup.append(j)
        J[j] = False
        zs, Fsup = z[sup], Fs[sup]
        if not J.any():  # (one sample)
            w = np.ones(len(sup))
            break
        C = 1.0 / (z[J][:, None] - zs[None, :])
        A = np.vstack(
            [C * (Fs[J, k][:, None] - Fsup[None, :, k]) for k in range(m)])
        if A.shape[0] < A.shape[1]:
            A = np.vstack([A, np.zeros((A.shape[1] - A.shape[0], A.shape[1]))])
        _, _, Vh = np.linalg.svd(A, full_matrices=False)
        w = Vh[-1].conj()
        R = Fs.copy()
        R[J] = (C @ (w[:, None] * Fsup)) / (C @ w)[:, None]
    zs, Fsup = z[sup], F[sup]

    def r(zz):
        zz = np.atleast_1d(np.asarray(zz, complex))
        with np.errstate(divide='ignore', invalid='ignore'):
            Cz = 1.0 / (zz[:, None] - zs[None, :])
            out = (Cz @ (w[:, None] * Fsup)) / (Cz @ w)[:, None]
        hit = np.isinf(Cz).any(1) | np.isnan(out).any(1)
        for i in np.nonzero(hit)[0]:
            out[i] = Fsup[np.argmin(np.abs(zs - zz[i]))]
        return out
    r.support, r.weights = zs, w
    return r


def _is_log(grid):
    """Whether a frequency grid is spaced in log f (a ratio between neighbours
    rather than a step)."""
    g = np.asarray(grid, float)
    if len(g) < 3 or g[0] <= 0 or g[-1] / g[0] < 10:
        return False
    d, r = np.diff(g), g[1:] / g[:-1]
    return np.ptp(r) < 1e-6 * r.mean() * len(g) or np.ptp(d) > 10 * d.min()


def adaptive_sweep(solve, fmin, fmax, n0=5, tol=2e-3, max_points=40, ngrid=2001,
                   log=print, on_step=None, seed=(), grid=None, hint=None):
    """
    solve(f) -> S (P x P).  Returns (f_grid, S_grid (ngrid, P, P), f_samples,
    S_samples, history): one dict per model after the first, solves (samples so
    far), change (max |dS| from the previous model, on the grid) at f_change
    (Hz), pred (how far the previous model was from the newest sample) and f_new
    (Hz: the next frequency solved, None when the sweep stopped).
    on_step(entry): called with each entry as it is made. Converged when, twice
    in a row, the models from the last two iterations agree to `tol` (max |dS|)
    on the grid and the model predicted the newest sample to `tol` before it was
    solved, the second time a solve in the widest gap between the samples. (The
    model change alone can be zero while the model is still wrong: a sparse fit
    that the new sample does not alter.) seed: frequencies (Hz)
    solved among the first samples, besides the n0 spread over the band (those
    inside it). grid: the frequencies asked for (Hz), the model checked and
    returned there (else ngrid spread linearly); a grid in log f (a sweep of
    decades) has the first samples and the gaps between them in log f too. (A
    log sweep had been solved on a linear grid and interpolated linearly: below
    its first step, decades between two samples.) The last history entry's
    `converged` says whether the models agreed, or the sweep stopped at
    max_points. hint: what to try, said with a solve that gives non-finite
    S-parameters.
    """
    def checked(f):
        S = solve(f)
        # the fit would fail on it with an SVD error
        if not np.isfinite(S).all():
            raise FloatingPointError(
                f"the solve at {f / 1e9:.4g} GHz gave non-finite S-parameters"
                + (f" ({hint})" if hint else ''))
        return S

    grid = (np.linspace(fmin, fmax, ngrid) if grid is None else np.asarray(
        grid, float))
    ngrid, logf = len(grid), _is_log(grid)
    # the axis the gaps are measured on
    u = np.log if logf else (lambda x: np.asarray(x, float))
    iu = np.exp if logf else (lambda x: x)

    def widest_gap():  # the middle of the widest gap between samples
        us = np.sort(u(np.array(fs)))
        i = int(np.argmax(np.diff(us)))
        return float(iu(us[i] + (us[i + 1] - us[i]) / 2))

    first = (np.geomspace(fmin, fmax, n0) if logf else np.linspace(
        fmin, fmax, n0))
    fs = sorted(
        set(first.tolist()) | {float(f)
                               for f in seed
                               if fmin <= f <= fmax})
    Ss = [checked(f) for f in fs]
    P, sc = Ss[0].shape[0], 1e9
    prev, hist, ok, pred_err = None, [], 0, np.inf
    gap_ok = gap_try = False
    while True:
        o = np.argsort(fs)
        fz, Fz = np.array(fs)[o] / sc, np.array([Ss[i].ravel() for i in o])
        model = aaa(fz, Fz, tol=min(FIT, 0.2 * tol))
        Mg = model(grid / sc)
        if prev is not None:
            dif = np.abs(Mg - prev).max(1)
            k = int(np.argmax(dif))
            hist.append(
                dict(solves=len(fs), change=float(dif[k]),
                     f_change=float(grid[k]), pred=float(pred_err), f_new=None))
            if on_step:
                on_step(dict(hist[-1], tol=tol))
            log(f"  AFS: {len(fs)} solves, max model change {dif[k]:.1e} at "
                f"{grid[k] / 1e9:.3f} GHz, prediction error {pred_err:.1e}")
            ok = ok + 1 if dif[k] < tol and pred_err < tol else 0
            gap_ok = gap_ok and ok > 0
            if ok >= 1 and not gap_ok and len(fs) < max_points:
                # agreed: the next solve where the samples are furthest apart
                # (two models can be wrong alike where neither was asked).
                # (After two agreements at the largest change, as until
                # 2026-11-18, a sweep took one more solve: 218 against 206 on
                # 12 boards' dense solves, each model then within 3.6e-3)
                fnew, gap_try = widest_gap(), True
            elif ok >= 2 and gap_ok or len(fs) >= max_points:
                hist[-1]['converged'] = ok >= 2
                break
            else:
                fnew = grid[k]
        else:
            fnew = widest_gap()
        # keep new samples away from
        if (min(abs(u(np.array(fs)) - u(fnew))) < (u(fmax) - u(fmin)) /
            (4 * ngrid)):
            fnew = widest_gap()  # existing ones
        prev = Mg
        if hist:
            hist[-1]['f_new'] = float(fnew)
        Snew = checked(fnew)
        pred_err = float(
            np.abs(model(np.array([fnew / sc]))[0] - Snew.ravel()).max())
        if gap_try:
            gap_ok, gap_try = pred_err < tol, False
            ok = ok if gap_ok else 0
        fs.append(fnew); Ss.append(Snew)
    o = np.argsort(fs)
    return grid, Mg.reshape(ngrid, P, P), np.array(fs)[o], np.array(Ss)[o], hist
