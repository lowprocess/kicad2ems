"""
Band-edge measurement, the targets of a spec, and the calibration of a design's kf / kb on its
microstrip line model.

The edges are measured at -3 dB (on a lossy response: 3 dB below the pass-band peak), so the targets
are the -3 dB edges of the ideal response: for a Chebyshev prototype those lie outside the ripple band
edge, at Omega_3 = cosh(acosh(1 / eps) / n).
"""
import math
import numpy as np

from .circuit import sparams, db
from .prototype import lowpass_s21_db
from .topologies import build, highpass_band, prepare


def omega3(spec):
    if spec.response == 'butterworth':
        return 1.0
    eps = math.sqrt(10 ** (spec.ripple / 10) - 1)
    return math.cosh(math.acosh(1 / eps) / spec.order)


def band(spec):
    """(kind, f0, fbw) as realised: a high-pass is a wide stub band-pass."""
    if spec.kind == 'highpass':
        f0, fbw = highpass_band(spec)
        return 'bandpass', f0, fbw
    return spec.kind, spec.f0, spec.fbw


def targets(spec):
    """Target -3 dB edges: {'fc': ...} (low-pass) or {'f1': ..., 'f2': ...}."""
    kind, f0, fbw = band(spec)
    o3 = omega3(spec)
    if kind == 'lowpass':
        return {'fc': f0 * o3}
    if spec.kind == 'highpass' or spec.topology == 'stub' and kind == 'bandpass':
        # stub filters map through cos(theta): the ripple edge at theta1 = pi / 2 (1 - fbw / 2)
        th1 = math.pi / 2 * (1 - fbw / 2)
        c = min(0.999, o3 * math.cos(th1))
        th3 = math.acos(c)
        return {'f1': f0 * th3 / (math.pi / 2), 'f2': f0 * (2 - th3 / (math.pi / 2))}
    a = fbw * o3 if kind == 'bandpass' else fbw / o3        # f/f0 - f0/f at the -3 dB edges
    r = math.sqrt(a * a + 4)
    return {'f1': f0 * (r - a) / 2, 'f2': f0 * (r + a) / 2}


def sweep(spec, n=1201):
    kind, f0, fbw = band(spec)
    if kind == 'lowpass':
        return np.linspace(0.02 * f0, 3.0 * f0, n)
    if spec.topology == 'stub':
        return np.linspace(0.05 * f0, 1.95 * f0, n)
    span = max(3 * fbw, 0.3)
    return np.linspace(max(0.05, 1 - span) * f0, (1 + span) * f0, n)


def _cross(f, y, i, j, lvl):
    """Interpolated frequency where y crosses lvl between samples i and j."""
    if y[j] == y[i]:
        return f[i]
    return f[i] + (lvl - y[i]) * (f[j] - f[i]) / (y[j] - y[i])


def edges(spec, f, s21db, relative=False):
    """Measured -3 dB edges ({'fc'} or {'f1', 'f2'}) of a response; relative: 3 dB below the pass-band
    peak instead of -3 dB. Missing edges are None."""
    f, y = np.asarray(f), np.asarray(s21db)
    kind, f0, _ = band(spec)
    if kind == 'lowpass':
        ref = y[f <= f0].max() if relative and np.any(f <= f0) else 0.0
        lvl = ref - 3
        i0 = 0
        for k in range(len(f) - 1):
            if y[k] >= lvl > y[k + 1] and f[k] > 0.2 * f0:
                return {'fc': _cross(f, y, k, k + 1, lvl)}
        return {'fc': None}
    k0 = int(np.argmin(abs(f - f0)))
    if kind == 'bandpass':
        win = abs(f / f0 - 1) < 0.5 * band(spec)[2] + 0.1
        kpk = int(np.argmax(np.where(win, y, -np.inf)))
        ref = y[kpk] if relative else 0.0
        lvl = ref - 3
        inside = y >= lvl
        k0 = kpk
    else:
        ref = (np.median(y[(f < 0.6 * f0) | (f > 1.4 * f0)]) if relative else 0.0)
        lvl = ref - 3
        inside = y < lvl
        win = abs(f / f0 - 1) < 0.5 * band(spec)[2] + 0.05
        k0 = int(np.argmin(np.where(win, y, np.inf)))
    if not inside[k0]:
        return {'f1': None, 'f2': None}
    a = k0
    while a > 0 and inside[a - 1]:
        a -= 1
    b = k0
    while b < len(f) - 1 and inside[b + 1]:
        b += 1
    if kind == 'bandpass':
        # a mistuned pass band dips below the level between its peaks: take the outermost crossings
        # within the band's neighbourhood instead of the region around the highest peak
        idx = np.where(inside & win)[0]
        a, b = min(a, idx.min()), max(b, idx.max())
    f1 = _cross(f, y, a - 1, a, lvl) if a > 0 else None
    f2 = _cross(f, y, b, b + 1, lvl) if b < len(f) - 1 else None
    return {'f1': f1, 'f2': f2}


def errors(spec, meas):
    """(centre ratio, bandwidth ratio) measured / target (bandwidth None for a low-pass)."""
    t = targets(spec)
    if 'fc' in t:
        return (meas['fc'] / t['fc'] if meas.get('fc') else None), None
    if not meas.get('f1') or not meas.get('f2'):
        return None, None
    fc_m, fc_t = math.sqrt(meas['f1'] * meas['f2']), math.sqrt(t['f1'] * t['f2'])
    return fc_m / fc_t, (meas['f2'] - meas['f1']) / (t['f2'] - t['f1'])


def model_response(design, f, lossless=False, sub=None):
    sub = sub or design.sub
    sub = sub.lossless() if lossless else sub
    s11, s21 = sparams(design.model(sub), f, design.spec.z0)
    return s11, s21


def calibrate(spec, sub, kf=1.0, kb=1.0, iters=12, tol=1e-4, log=None, refine_lp=True):
    """Iterate kf / kb until the lossless line model's -3 dB edges match the targets."""
    notes = prepare(spec)
    tune_bw = spec.kind != 'lowpass'
    f = sweep(spec, 2401)
    d, prev = None, None
    for it in range(iters):
        d = build(_copy(spec), sub, kf, kb)
        _, s21 = model_response(d, f, lossless=True)
        ef, eb = errors(spec, edges(spec, f, db(s21)))
        if ef is None:
            if log:
                log(f"  calibration: no band edge found (kf {kf:.4f}, kb {kb:.4f}); keeping the synthesis")
            d = build(_copy(spec), sub, 1.0, 1.0) if it else d
            break
        if log:
            log(f"  calibration {it}: centre x{ef:.4f}" + (f", bandwidth x{eb:.4f}" if eb else ''))
        done = abs(ef - 1) < tol and (not tune_bw or eb is None or abs(eb - 1) < 10 * tol)
        if done:
            break
        if tune_bw and eb and prev and abs(eb - prev) < 1e-3 and abs(eb - 1) > 0.01:
            notes.append(f"the bandwidth cannot be reached with this topology / substrate: "
                         f"{eb:.2f} x the target on the line model")
            break
        prev = eb
        kf /= ef
        if tune_bw and eb:
            kb /= eb ** 0.8
    d.notes = notes + d.notes
    if spec.kind == 'lowpass' and refine_lp and not spec.trim:
        refine(spec, sub, d.kf, log)
        if spec.trim:
            d2 = calibrate(spec, sub, d.kf, 1.0, iters, tol, log, refine_lp=False)
            d2.notes = notes + [x for x in d2.notes if x not in notes]
            return d2
    return d


def _copy(spec):
    from dataclasses import replace
    return replace(spec)


def refine(spec, sub, kf, log=None, iters=400):
    """Low-pass only: trim the sections' electrical lengths (symmetrically) so the lossless line model
    follows the prototype's response up to 1.3 x the cut-off: the distributed sections' departures from
    lumped L and C (the pass-band ripple growing, the edge softening) are taken out. Sets spec.trim."""
    try:
        from scipy.optimize import minimize
    except ImportError:
        if log:
            log("  refine: scipy not installed, skipped")
        return
    n = spec.order
    half = (n + 1) // 2
    f = np.linspace(0.02, 1.3, 260) * spec.f0
    ref = np.array([lowpass_s21_db(spec.response, n, spec.ripple, fi / spec.f0) for fi in f])
    w = np.where(f <= spec.f0, 4.0, 1.0)
    lsub = sub.lossless()

    def cost(x):
        tr = tuple(np.r_[x, x[:n - half][::-1]])
        s = _copy(spec)
        s.trim = tr
        try:
            d = build(s, sub, kf, 1.0)
        except ValueError:
            return 1e6
        y = db(sparams(d.model(lsub), f, spec.z0)[1])
        return float(np.mean(w * (np.maximum(y, -40) - np.maximum(ref, -40)) ** 2))

    x0 = np.ones(half)
    c0 = cost(x0)
    r = minimize(cost, x0, method='Nelder-Mead', options={'maxiter': iters, 'xatol': 1e-4, 'fatol': 1e-6})
    if r.fun < c0:
        spec.trim = tuple(float(v) for v in np.r_[r.x, r.x[:n - half][::-1]])
        if log:
            log(f"  refine: rms error {math.sqrt(c0):.2f} -> {math.sqrt(r.fun):.2f} dB, trims "
                + ', '.join(f'{t:.3f}' for t in spec.trim))
