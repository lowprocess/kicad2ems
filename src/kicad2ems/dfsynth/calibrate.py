"""
Band-edge measurement, the targets of a spec, and the calibration of a design's kf / kb on its
microstrip line model.

The edges are measured at -3 dB (on a lossy response: 3 dB below the pass-band peak), so the targets
are the -3 dB edges of the ideal response: for a Chebyshev prototype those lie outside the ripple band
edge, at Omega_3 = cosh(acosh(1 / eps) / n).
"""
import math
import time
import numpy as np

from .circuit import sparams, db, MLine
from .prototype import gvalues, lowpass_s21_db
from .topologies import build, highpass_band, prepare, _z_limits, radial_stub_range


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
    if kind == 'bandstop' and spec.topology in ('stub', 'radial'):
        # open stubs map through tan(theta) (Richards): the ripple edge at theta1 = pi / 2 (1 - fbw / 2)
        th3 = math.atan(o3 * math.tan(math.pi / 2 * (1 - fbw / 2)))
        return {'f1': f0 * th3 / (math.pi / 2), 'f2': f0 * (2 - th3 / (math.pi / 2))}
    a = fbw * o3 if kind == 'bandpass' else fbw / o3        # f/f0 - f0/f at the -3 dB edges
    r = math.sqrt(a * a + 4)
    return {'f1': f0 * (r - a) / 2, 'f2': f0 * (r + a) / 2}


def sweep(spec, n=1201):
    kind, f0, fbw = band(spec)
    if kind == 'lowpass':
        return np.linspace(0.02 * f0, 3.0 * f0, n)
    if kind == 'bandstop' and spec.topology == 'radial':
        # a fan's next resonance comes well below 3 f0: the upper pass band is short, so the sweep ends
        # half the lower pass band's width above the stop band
        t = targets(spec)
        return np.linspace(0.05 * f0, min(1.95 * f0, t['f2'] + 0.5 * (t['f1'] - 0.05 * f0)), n)
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


REFINE_SECONDS = 10.0          # the refinement's time limit: past it the best trims so far are kept


class _OutOfTime(Exception):
    pass


def _nelder_mead(cost, x0, seconds, log=None, **kw):
    """scipy's Nelder-Mead on cost from x0, stopped after `seconds` (None: no limit) with the best point it had
    reached. Returns (x, cost)."""
    from scipy.optimize import minimize
    t0 = time.monotonic()
    best = [float(cost(x0)), np.array(x0, float)]
    calls = [0]

    def timed(x):
        if seconds is not None and time.monotonic() - t0 > seconds:
            raise _OutOfTime
        v = cost(x)
        calls[0] += 1
        if v < best[0]:
            best[0], best[1] = float(v), np.array(x, float)
        return v
    try:
        r = minimize(timed, x0, method='Nelder-Mead', **kw)
        return r.x, float(r.fun)
    except _OutOfTime:
        if log:
            log(f"  refine: stopped at the {seconds:g} s time limit ({calls[0]} evaluations), keeping the best so far")
        return best[1], best[0]


def calibrate(spec, sub, kf=1.0, kb=1.0, iters=12, tol=1e-4, log=None, refine_lp=True, refine_time=REFINE_SECONDS):
    """Iterate kf / kb until the lossless line model's -3 dB edges match the targets; then, for the low-pass,
    band-stop and interdigital designs, refine the trims (for at most refine_time seconds)."""
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
    fine = {'lowpass': refine, 'bandstop': refine_bandstop}.get(spec.kind)
    if spec.topology == 'interdigital':
        fine = refine_coupled
    if fine and refine_lp and not spec.trim:
        fine(spec, sub, d.kf, d.kb, log, seconds=refine_time)
        if spec.trim:
            d2 = calibrate(spec, sub, d.kf, d.kb, iters, tol, log, refine_lp=False)
            d2.notes = notes + [x for x in d2.notes if x not in notes]
            return d2
    return d


def _copy(spec):
    from dataclasses import replace
    return replace(spec)


def refine(spec, sub, kf, kb=1.0, log=None, iters=400, seconds=REFINE_SECONDS):
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
    rx, rf = _nelder_mead(cost, x0, seconds, log, options={'maxiter': iters, 'xatol': 1e-4, 'fatol': 1e-6})
    if rf < c0:
        spec.trim = tuple(float(v) for v in np.r_[rx, rx[:n - half][::-1]])
        if log:
            log(f"  refine: rms error {math.sqrt(c0):.2f} -> {math.sqrt(rf):.2f} dB, trims "
                + ', '.join(f'{t:.3f}' for t in spec.trim))


def passband_rl(design, f, sub=None):
    """Worst return loss (dB) of the lossless line model outside the -3 dB edges of the spec's stop band,
    with a tenth of the band's width as a guard (band-stop)."""
    t = targets(design.spec)
    g = 0.1 * (t['f2'] - t['f1'])
    s11, _ = sparams(design.model((sub or design.sub).lossless()), f, design.spec.z0)
    out = (f < t['f1'] - g) | (f > t['f2'] + g)
    return float(-db(s11[out]).max())


def refine_bandstop(spec, sub, kf, kb, log=None, iters=3000, seconds=REFINE_SECONDS):
    """Band-stop only: the connecting lines are unit elements, not just spacers. Sets the stub and line
    impedances (symmetric factors on the synthesis, within what the substrate can make) so the lossless
    line model follows the Chebyshev response in Richards' variable, |S21|^2 = 1 / (1 + eps^2
    T_n^2(tan theta / tan theta1)): the narrow-band synthesis's pass-band mismatch, which grows with the
    bandwidth, is taken out. Kept only if the pass-band return loss improves. Sets spec.trim."""
    try:
        from scipy.optimize import minimize
    except ImportError:
        if log:
            log("  refine: scipy not installed, skipped")
        return
    n = spec.order
    h, hl = (n + 1) // 2, (n - 1) // 2
    f = sweep(spec, 600)
    th1 = math.pi / 2 * (1 - spec.fbw / 2)
    th = np.pi / 2 * f / spec.f0
    ref = np.array([lowpass_s21_db(spec.response, n, spec.ripple, abs(math.tan(x)) / math.tan(th1)) for x in th])
    t = targets(spec)
    w = np.where((f < t['f1']) | (f > t['f2']), 4.0, 1.0)
    lsub = sub.lossless()
    zmin, zmax = _z_limits(sub)

    def make(x):
        a, b = np.exp(x[:h]), np.exp(x[h:])
        s = _copy(spec)
        s.trim = tuple(float(v) for v in np.r_[a, a[:n - h][::-1], b, b[:n - 1 - hl][::-1]])
        return s

    def cost(x):
        try:
            d = build(make(x), sub, kf, kb)
        except ValueError:
            return 1e6
        y = db(sparams(d.model(lsub), f, spec.z0)[1])
        return float(np.mean(w * (np.maximum(y, -40) - np.maximum(ref, -40)) ** 2))

    # bounds on the log factors: every impedance within what the substrate makes (the synthesis's stubs,
    # unclamped, as bandstop_stub computes them)
    g = gvalues(spec.response, n, spec.ripple)
    zs = [4 * spec.z0 / (math.pi * g[k + 1] * spec.fbw * kb) for k in range(h)]
    zr = zs + [spec.z0] * hl
    bounds = [(math.log(zmin / z), math.log(zmax / z)) for z in zr]
    if spec.topology == 'radial':                             # the stubs: what fans of 15 to 160 degrees make
        lo, hi = radial_stub_range(sub, sub.width(spec.z0), spec.f0 * kf)
        bounds[:h] = [(math.log(lo / z), math.log(hi / z)) for z in zs]
    x0 = np.array([min(max(0.0, lo), hi) for lo, hi in bounds])
    c0 = cost(np.zeros(h + hl))
    rx, rf = _nelder_mead(cost, x0, seconds, log, bounds=bounds, options={'maxiter': iters, 'xatol': 1e-5,
                                                                         'fatol': 1e-8})
    s = make(rx)
    fr = sweep(spec, 1201)
    rl0 = passband_rl(build(_copy(spec), sub, kf, kb), fr)
    rl1 = passband_rl(build(s, sub, kf, kb), fr)
    if rf < c0 and rl1 > rl0 + 0.5:
        spec.trim = s.trim
        if log:
            log(f"  refine: rms error {math.sqrt(c0):.2f} -> {math.sqrt(rf):.2f} dB, pass-band RL "
                f"{rl0:.1f} -> {rl1:.1f} dB, impedance factors " + ', '.join(f'{v:.3f}' for v in spec.trim))
    elif log:
        log(f"  refine: no improvement (pass-band RL {rl0:.1f} -> {rl1:.1f} dB), kept the synthesis")


def _sym(x, n):
    """n values from the first ceil(n / 2), mirrored."""
    h = (n + 1) // 2
    x = list(x[:h])
    return x + x[:n - h][::-1]


def _coupled_free(spec):
    """The interdigital's free trims (log): the symmetric gaps, one tap, the symmetric rods."""
    n = spec.order
    ng, nr = n // 2, (n + 1) // 2
    t = np.log(np.array(spec.trim if spec.trim and len(spec.trim) == 2 * n + 1 else [1.0] * (2 * n + 1)))
    return np.r_[t[:ng], t[n - 1], t[n + 1:n + 1 + nr]]


def _coupled_spec(spec, x):
    """A copy of spec with the free trims x (log, see _coupled_free)."""
    n, ng = spec.order, spec.order // 2
    s = _copy(spec)
    s.trim = tuple(float(v) for v in np.exp(np.r_[_sym(x[:ng], n - 1), [x[ng]] * 2, _sym(x[ng + 1:], n)]))
    return s


def refine_coupled(spec, sub, kf, kb, log=None, iters=3000, seconds=REFINE_SECONDS):
    """Coupled-resonator band-pass (interdigital): the closed-form couplings and taps assume synchronous,
    nearest-neighbour resonators; on the multiconductor model the end rods (one neighbour, a tap) and the
    inner ones (two neighbours, and the next-but-one) are neither. Sets the gaps, the taps and each rod's
    length (symmetric factors) for the prototype's match over the band (|S11| no higher than its ripple
    allows) with the band edges on target. Not a fit to the whole prototype response: the couplings past
    the neighbours give the skirts a shape no trim takes out (slower below the band, a transmission zero
    above it). Kept only if the pass-band return loss improves."""
    try:
        from scipy.optimize import minimize
    except ImportError:
        if log:
            log("  refine: scipy not installed, skipped")
        return
    n = spec.order
    t = targets(spec)
    bw = t['f2'] - t['f1']
    f = np.linspace(t['f1'] - 0.4 * bw, t['f2'] + 0.4 * bw, 400)
    om = (f / spec.f0 - spec.f0 / f) / spec.fbw
    ref21 = np.array([lowpass_s21_db(spec.response, n, spec.ripple, abs(o)) for o in om])
    ref11 = 10 * np.log10(np.maximum(1 - 10 ** (ref21 / 10), 1e-6))
    inband = (f > spec.f0 - spec.bw / 2) & (f < spec.f0 + spec.bw / 2)
    # the most |S11| may be: a Chebyshev's ripple level everywhere in the band, a Butterworth's own curve
    limit = np.full(inband.sum(), ref11[inband].max()) if spec.response == 'chebyshev' else \
        np.maximum(ref11[inband], -30)
    lsub = sub.lossless()

    def make(x):
        return _coupled_spec(spec, x)

    def cost(x):
        try:
            d = build(make(x), sub, kf, kb)
        except ValueError:
            return 1e6
        s11, s21 = sparams(d.model(lsub), f, spec.z0)
        ef, eb = errors(spec, edges(spec, f, db(s21)))
        if ef is None or eb is None:
            return 1e3
        over = np.maximum(db(s11[inband]) - limit, 0)
        return float(np.mean(over ** 2) + (100 * math.log(ef)) ** 2 + (30 * math.log(eb)) ** 2)

    x0 = np.zeros(len(_coupled_free(spec)))
    c0 = cost(x0)
    rx, rf = _nelder_mead(cost, x0, seconds, log, options={'maxiter': iters, 'xatol': 1e-5, 'fatol': 1e-8,
                                                           'adaptive': True})
    fr = sweep(spec, 1201)

    def rl(s):
        d = build(s, sub, kf, kb)
        s11, s21 = sparams(d.model(lsub), fr, spec.z0)
        e = edges(spec, fr, db(s21))
        if not e.get('f1') or not e.get('f2'):
            return 0.0
        g = 0.1 * (e['f2'] - e['f1'])
        m = (fr > e['f1'] + g) & (fr < e['f2'] - g)
        return float(-db(s11[m]).max()) if m.any() else 0.0
    s = make(rx)
    rl0, rl1 = rl(_copy(spec)), rl(s)
    if rf < c0 and rl1 > rl0 + 0.5:
        spec.trim = s.trim
        if log:
            log(f"  refine: pass-band RL {rl0:.1f} -> {rl1:.1f} dB, factors "
                + ', '.join(f'{v:.3f}' for v in spec.trim))
    elif log:
        log(f"  refine: no improvement (pass-band RL {rl0:.1f} -> {rl1:.1f} dB), kept the synthesis")


def space_map(target, current, sub, kf, kb, f, s21, s11, log=None):
    """One aggressive space-mapping step for a design with free trims (interdigital): the trims (and a
    reference-plane shift, the solver's port planes against the model's) with which the zero-thickness
    line model reproduces the complex S21 and S11 of a field-solver run of `current` are extracted; the
    trims of `current` then move by what separates them from those of `target` (the design the line model
    put on the spec), so the next board behaves as `target` does on the model. Fitting the complex response
    (not |S|) tells a gap's error from a tap's, which magnitudes alone leave free to trade. Returns the new
    spec, or None if the fit does not reproduce the run."""
    from scipy.optimize import least_squares
    t = targets(target)
    bw = t['f2'] - t['f1']
    m = (f > t['f1'] - bw) & (f < t['f2'] + bw)
    f, a, b = np.asarray(f)[m], np.asarray(s21)[m], np.asarray(s11)[m]
    s0 = sub.with_(t=0.0)
    w50 = sub.width(target.z0)
    xc, xt = _coupled_free(current), _coupled_free(target)

    def res(y):
        try:
            net = build(_coupled_spec(current, xc + y[:-1]), sub, kf, kb).model(s0)
        except ValueError:
            return np.full(4 * len(f), 10.0)
        ext = MLine(s0, w50, y[-1])
        p11, p21 = sparams([ext] + net + [ext], f, target.z0)
        e = np.r_[p21 - a, p11 - b]
        return np.r_[e.real, e.imag]
    best = None
    for plane in (0.0, 0.5, 1.0):                             # mm: the solver's port planes beyond the model's
        y0 = np.r_[np.zeros(len(xc)), plane]
        r = least_squares(res, y0, diff_step=1e-3)
        if best is None or r.cost < best.cost:
            best = r
    e0 = math.sqrt(np.mean(res(np.zeros(len(xc) + 1)) ** 2))
    e1 = math.sqrt(np.mean(best.fun ** 2))
    xe = xc + best.x[:-1]
    if log:
        log(f"  space mapping: the model fits the run to |dS| {e1:.3f} rms (from {e0:.3f}; reference planes "
            f"{best.x[-1]:+.2f} mm); the board behaves as trims "
            + ', '.join(f'{v:.3f}' for v in np.exp(xe - xt)) + " of the target's")
    if e1 > 0.5 * e0 and e1 > 0.05:
        return None
    if log:
        # the solver's copper is infinitely thin: what the board itself does is the model of the extracted
        # trims with the real copper
        fr = sweep(target, 1201)
        s11, s21 = sparams(build(_coupled_spec(current, xe), sub, kf, kb).model(), fr, target.z0)
        e = edges(target, fr, db(s21), relative=True)
        ef, eb = errors(target, e)
        if ef is not None and eb is not None:
            band = abs(fr - target.f0) <= target.bw / 2
            log(f"  so the board with {sub.t * 1e3:.0f} um copper: centre {100 * (ef - 1):+.2f}%  bw "
                f"{100 * (eb - 1):+.1f}%  RL {-db(s11[band]).max():.1f} dB over the spec band")
    return _coupled_spec(current, xc + (xt - xe))
