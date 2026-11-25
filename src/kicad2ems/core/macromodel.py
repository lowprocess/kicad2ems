"""
A passive rational macromodel of S-parameters, and its response in time.

    m = fit(f, S, z0=50.0)            # vector fitting, common poles (Model)
    m.S(f)                            # the model on any frequencies, DC too
    m.passive()                       # (passive, the largest singular value)
    m.enforce()                       # residues perturbed until passive
    tr = Transient(m, dt)             # the model stepped in time
    V, I = simulate(m, terms, t)      # every port's voltage with terminations

The fit is relaxed vector fitting (Gustavsen and Semlyen 1999, the relaxed
form of Gustavsen 2006): every entry of S on the same poles, so the model is a
state-space system of a few hundred states; the poles stable by construction
(an unstable one flipped), so the response is causal. The samples carry a DC
point where they reach low enough: Re S is even in w and Im S odd, so S(0) is
the constant of Re S fitted as a + b w^2 on the lowest samples (a time-domain
response settles to the model's S(0), which no sample in the band fixes).

Passivity (the model's largest singular value at most 1 at every frequency,
not only the samples': a transient of a non-passive model can grow without
bound) is checked on a dense grid from DC to PASS_TOP x fmax and at each
pole's frequency; where it is not, the residues are perturbed (each entry's
least change over the band that takes every violation's largest singular value
down to 1 - PASS_MARGIN along its singular vectors), until passive.

In time: S's ports are waves at each port's z0, a = (V + z0 I) / 2 sqrt(z0)
incident, b = (V - z0 I) / 2 sqrt(z0) reflected, I into the port. Each pole's
state is stepped exactly for an input linear over the step (recursive
convolution), so b[n] = G a[n] + h[n] (h from the past alone); each port's
termination is a current I(V) into the port at the step (a source behind its
resistance, a load, an IBIS buffer: core.ibis), and the step's waves solve
(Newton where a termination is not linear).
"""
import math

import numpy as np

__all__ = ['Model', 'fit', 'dc_point', 'Transient', 'simulate', 'Thevenin',
           'Load', 'FIT_TOL', 'PASS_TOP', 'PASS_MARGIN']

# the relative RMS error (of the largest |S|) a fit is accepted at
FIT_TOL = 2e-3
# the passivity check's top: x fmax (the band's top), and its margin
PASS_TOP, PASS_MARGIN = 3.0, 1e-3
# vector fitting's pole relocations
ITERATIONS = 6


class Model:
    """S(s) = D + sum_k R_k / (s - p_k) (s in rad/s): poles p (np,), complex
    ones in conjugate pairs (p, p*) side by side; residues R (np, n, n), a
    conjugate pole's the conjugate; D (n, n) real; z0 (n,) each port's
    reference (ohm). fmax (Hz): the band the model was fitted on (passivity is
    checked to PASS_TOP times it). info: the fit's record."""

    def __init__(self, poles, R, D, z0, fmax, info=None):
        self.poles = np.asarray(poles, complex)
        self.R = np.asarray(R, complex)
        self.D = np.asarray(D, float)
        self.z0 = np.broadcast_to(np.asarray(z0, float),
                                  (self.D.shape[0],)).copy()
        self.fmax = float(fmax)
        self.info = dict(info or {})

    @property
    def n(self):
        return self.D.shape[0]

    def S(self, f):
        """S (nf, n, n) at f (Hz)."""
        s = 2j * np.pi * np.atleast_1d(np.asarray(f, float))
        g = 1.0 / (s[:, None] - self.poles[None, :])             # (nf, np)
        return self.D[None] + np.einsum('fk,kij->fij', g, self.R)

    def passive(self, n=None):
        """(passive, the largest singular value, its frequency (Hz)) on a
        dense grid from DC to PASS_TOP x fmax and at each pole's frequency."""
        f = self._grid(n)
        sv = np.linalg.svd(self.S(f), compute_uv=False)[:, 0]
        i = int(np.argmax(sv))
        return bool(sv[i] <= 1.0 + 1e-9), float(sv[i]), float(f[i])

    def _grid(self, n=None):
        top = PASS_TOP * self.fmax
        n = n or max(2001, 20 * len(self.poles))
        fp = np.abs(self.poles.imag) / (2 * np.pi)
        fp = fp[fp <= top]
        return np.unique(np.r_[np.linspace(0.0, top, n), fp])

    def enforce(self, max_iter=20, scale_max=0.02):
        """Make the model passive (the module's text): its samples on the
        dense grid projected onto passivity (each singular value above 1 -
        PASS_MARGIN taken down to it) and its residues and D fitted to them
        again on the same poles, until passive; a violation of at most
        scale_max left after max_iter rounds, the whole model scaled down to
        the margin (a gain of at most -0.17 dB). Returns the rounds (0:
        passive already). Raises ValueError where it does not get there.
        (Residues perturbed at the violations alone, as equalities, had
        overshot: a lossless line's 1.0000 to 1.01.)"""
        thr = 1.0 - PASS_MARGIN
        f = self._grid()
        # (normalized as fit's: in rad/s a pole's column is 1e-11 of D's, and
        # the least squares had dropped them: max |dS| 1.1 after it)
        w0 = 2 * np.pi * self.fmax
        s = 2j * np.pi * f / w0
        Phi = np.concatenate([_basis(s, self.poles / w0),
                              np.ones((len(f), 1))], 1)
        # (the band weighted over what lies above it)
        w = np.where(f <= self.fmax, 1.0, 0.3)
        A = _ri(Phi * w[:, None])
        n = self.n
        for it in range(max_iter):
            U, sv, Vh = np.linalg.svd(self.S(f))
            if sv[:, 0].max() <= 1.0 + 1e-9:
                self.info['passivity_rounds'] = it
                return it
            T = np.einsum('kij,kj,kjl->kil', U, np.minimum(sv, thr), Vh)
            B = _ri(T.reshape(len(f), -1) * w[:, None])
            x = np.linalg.lstsq(A, B, rcond=None)[0]
            R = _complex_residues(self.poles, x[:-1]).reshape(-1, n, n) * w0
            D = x[-1].reshape(n, n)
            if np.allclose(self.R, np.swapaxes(self.R, 1, 2), atol=1e-9 * max(
                    1.0, np.abs(self.R).max())):
                R = 0.5 * (R + np.swapaxes(R, 1, 2))
                D = 0.5 * (D + D.T)
            self.R, self.D = R, D
        smax = self.passive()[1]
        if smax <= 1.0 + scale_max:
            k = thr / smax
            self.R, self.D = self.R * k, self.D * k
            self.info.update(passivity_rounds=max_iter,
                             passivity_scaled=round(k, 6))
            return max_iter
        raise ValueError(f"the macromodel is not passive after {max_iter} "
                         f"rounds (largest singular value {smax:.4f}): more "
                         "frequencies, a lower fmax or fewer poles")

    def record(self):
        """What a result file keeps of the model (no arrays)."""
        ok, smax, fs = self.passive()
        return dict(self.info, ports=self.n, poles=int(len(self.poles)),
                    passive=ok, max_singular=round(smax, 6),
                    fmax_ghz=self.fmax / 1e9)


# ---- vector fitting ---------------------------------------------------------
def _pairs(p):
    """Each pole's kind: 0 real, 1 the first of a pair, 2 its conjugate."""
    kind = np.zeros(len(p), int)
    k = 0
    while k < len(p):
        if abs(p[k].imag) > 1e-12 * max(1.0, abs(p[k])):
            kind[k], kind[k + 1] = 1, 2
            k += 2
        else:
            k += 1
    return kind


def _basis(s, p):
    """The real basis (ns, np) on s: 1 / (s - p) for a real pole; for a pair
    (p, p*): 1 / (s - p) + 1 / (s - p*), j / (s - p) - j / (s - p*)."""
    kind = _pairs(p)
    out = np.empty((len(s), len(p)), complex)
    for k, kd in enumerate(kind):
        if kd == 0:
            out[:, k] = 1.0 / (s - p[k])
        elif kd == 1:
            a, b = 1.0 / (s - p[k]), 1.0 / (s - np.conj(p[k]))
            out[:, k], out[:, k + 1] = a + b, 1j * a - 1j * b
    return out


def _real_coeffs(p, R):
    """The residues R (np, ...) as the basis's real coefficients."""
    kind = _pairs(p)
    c = np.empty(R.shape, float)
    for k, kd in enumerate(kind):
        if kd == 0:
            c[k] = R[k].real
        elif kd == 1:
            c[k], c[k + 1] = R[k].real, R[k].imag
    return c


def _complex_residues(p, c):
    """The basis's real coefficients c (np, ...) as residues."""
    kind = _pairs(p)
    R = np.empty(c.shape, complex)
    for k, kd in enumerate(kind):
        if kd == 0:
            R[k] = c[k]
        elif kd == 1:
            R[k] = c[k] + 1j * c[k + 1]
            R[k + 1] = c[k] - 1j * c[k + 1]
    return R


def _ri(A):
    """A complex system's rows as real ones: [Re A; Im A]."""
    return np.concatenate([A.real, A.imag], 0)


def _start_poles(n, w_lo, w_hi):
    """n starting poles (normalized frequency): complex pairs spread over
    [w_lo, w_hi], damped by 1 %, a real one where n is odd."""
    m = n // 2
    beta = np.linspace(max(w_lo, 1e-3 * w_hi), w_hi, m)
    p = []
    for b in beta:
        p += [-b / 100 + 1j * b, -b / 100 - 1j * b]
    if n % 2:
        p.append(-w_hi / 2 + 0j)
    return np.array(p, complex)


def _relocate(s, F, p, w):
    """One relaxed vector-fitting step: the poles of sigma(s) (its zeros) on
    the responses F (ns, nr), weights w (ns,)."""
    ns, nr = F.shape
    npo = len(p)
    Phi = _basis(s, p)                                         # (ns, np)
    A1 = np.concatenate([Phi, np.ones((ns, 1))], 1) * w[:, None]
    blocks = []
    for i in range(nr):
        A2 = -F[:, i:i + 1] * A1
        A = _ri(np.concatenate([A1, A2], 1))
        # (fast VF: sigma's unknowns from each response's R22)
        Rr = np.linalg.qr(A, mode='r')
        blocks.append(Rr[npo + 1:, npo + 1:])
    # the relaxation: Re sum_s sigma(s) = ns
    norm = np.concatenate([np.real(Phi.sum(0)), [ns]])
    scale = np.sqrt(sum(np.sum(b ** 2) for b in blocks)) / ns
    M = np.concatenate(blocks + [scale * norm[None, :]], 0)
    rhs = np.zeros(M.shape[0])
    rhs[-1] = scale * ns
    x = np.linalg.lstsq(M, rhs, rcond=None)[0]
    ct, dt = x[:npo], x[npo]
    if abs(dt) < 1e-8:
        dt = 1e-8 * (1 if dt >= 0 else -1)
    # sigma's zeros: eig(A - b ct / dt) of the basis's real realization
    kind = _pairs(p)
    Am = np.zeros((npo, npo))
    b = np.zeros(npo)
    for k, kd in enumerate(kind):
        if kd == 0:
            Am[k, k], b[k] = p[k].real, 1.0
        elif kd == 1:
            a_, bt = p[k].real, p[k].imag
            Am[k:k + 2, k:k + 2] = [[a_, bt], [-bt, a_]]
            b[k], b[k + 1] = 2.0, 0.0
    z = np.linalg.eigvals(Am - np.outer(b, ct) / dt)
    z = np.where(z.real > 0, -z.real + 1j * z.imag, z)        # stable
    return _sorted_pairs(z)


def _sorted_pairs(z):
    """Poles as _pairs wants them: real ones, then each pair (Im > 0 first)."""
    z = np.asarray(z, complex)
    tol = 1e-9 * max(1.0, np.abs(z).max())
    real = np.sort(z[np.abs(z.imag) <= tol].real) + 0j
    up = z[z.imag > tol]
    up = up[np.argsort(up.imag)]
    out = list(real)
    for q in up:
        out += [q, np.conj(q)]
    return np.array(out, complex)


def _residues(s, F, p, w):
    """The residues (np, nr) and D (nr,) of F on the poles p (least
    squares, real D)."""
    ns, nr = F.shape
    Phi = _basis(s, p)
    A = _ri(np.concatenate([Phi, np.ones((ns, 1))], 1) * w[:, None])
    B = _ri(F * w[:, None])
    x = np.linalg.lstsq(A, B, rcond=None)[0]
    return _complex_residues(p, x[:-1]), x[-1]


def dc_point(f, S, low=0.05, points=6):
    """S(0) from the lowest samples (Re S even in w: a + b w^2; Im S odd, 0 at
    DC), or None where the samples start above `low` x fmax (a DC from there
    would be a guess)."""
    f = np.asarray(f, float)
    if f[0] > low * f[-1]:
        return None
    k = min(points, len(f))
    w2 = (f[:k] / f[-1]) ** 2
    A = np.stack([np.ones(k), w2], 1)
    re = np.asarray(S[:k]).real.reshape(k, -1)
    a = np.linalg.lstsq(A, re, rcond=None)[0][0]
    return a.reshape(np.shape(S)[1:])


def fit(f, S, z0=50.0, poles=None, tol=FIT_TOL, dc=True, passive=True,
        max_poles=400, log=None):
    """The macromodel of S (nf, n, n) on f (Hz, rising, above 0): `poles`
    poles, or (None) the fewest of 8, 12, 16 ... up to max_poles that fit
    within tol (RMS of |S - model| over the samples, of the largest |S|); a
    DC point added (dc_point) where the samples reach low enough; made passive
    (Model.enforce) where it is not. Raises ValueError where no fit is within
    tol."""
    f = np.asarray(f, float)
    S = np.asarray(S, complex)
    if S.ndim != 3 or S.shape[1] != S.shape[2] or len(f) != len(S):
        raise ValueError("S must be (nf, n, n) on f")
    if f[0] <= 0 or np.any(np.diff(f) <= 0):
        raise ValueError("frequencies must rise from above 0")
    n = S.shape[1]
    w0 = 2 * np.pi * f[-1]                      # (normalized: fmax at 1)
    fs, Fs = f, S
    d0 = dc_point(f, S) if dc else None
    if d0 is not None:
        fs, Fs = np.r_[0.0, f], np.concatenate([d0[None], S], 0)
    s = 2j * np.pi * fs / w0
    F = Fs.reshape(len(fs), -1)
    w = np.ones(len(fs))
    if d0 is not None:
        w[0] = 10.0                              # (the DC point held)
    big = max(float(np.abs(S).max()), 1e-12)
    w_lo = 2 * np.pi * f[0] / w0
    best = None
    counts = ([int(poles)] if poles else
              [c for c in [8, 12, 16, 24, 32, 48, 64, 96, 128, 160, 200, 260,
                           330, 400] if c <= max_poles])
    for npo in counts:
        p = _start_poles(npo, w_lo, 1.0)
        for _ in range(ITERATIONS):
            p = _relocate(s, F, p, w)
        R, D = _residues(s, F, p, w)
        model = (D[None, :] + _basis(s, p) @ _real_coeffs(p, R))
        err = float(np.sqrt(np.mean(np.abs(model[d0 is not None:]
                                           - F[d0 is not None:]) ** 2)) / big)
        if log:
            log(f"macromodel: {npo} poles, RMS error {err:.2e}")
        if best is None or err < best[0]:
            best = (err, p, R, D)
        if err <= tol:
            break
    err, p, R, D = best
    if err > tol:
        raise ValueError(
            f"no macromodel within {tol:g} of the samples (best {err:.2e} with "
            f"{len(p)} poles): more frequencies, or a narrower band")
    m = Model(p * w0, R.reshape(len(p), n, n) * w0, D.reshape(n, n).real, z0,
              f[-1], info=dict(rms_error=err, dc=d0 is not None,
                               fmin_ghz=f[0] / 1e9))
    ok, smax, fs_ = m.passive()
    m.info.update(passive_as_fitted=ok, max_singular_as_fitted=round(smax, 6))
    if passive and not ok:
        rounds = m.enforce()
        after = m.S(f)
        m.info['rms_error_passive'] = float(
            np.sqrt(np.mean(np.abs(after - S) ** 2)) / big)
        if log:
            log(f"macromodel: made passive in {rounds} round(s) (largest "
                f"singular value {smax:.4f} at {fs_ / 1e9:.3g} GHz), RMS error "
                f"{m.info['rms_error_passive']:.2e}")
    return m


def _local_peaks(x, level):
    """The indices of x's local maxima above level (one per run above it)."""
    above = x > level + 1e-9
    out, k = [], 0
    while k < len(x):
        if above[k]:
            j = k
            while j < len(x) and above[j]:
                j += 1
            out.append(k + int(np.argmax(x[k:j])))
            k = j
        else:
            k += 1
    return np.array(out, int)


# ---- in time ----------------------------------------------------------------
class Transient:
    """The model stepped at dt (s): each pole's state exact for an input
    linear over the step. b[n] = G a[n] + history(a[n - 1]); advance(a[n])
    after a step's waves are solved."""

    def __init__(self, model, dt):
        p, R = model.poles, model.R
        kind = _pairs(p)
        keep = kind != 2                        # (a pair: its first, twice)
        self.p, self.R = p[keep], R[keep]
        self.m = np.where(kind[keep] == 1, 2.0, 1.0)
        z = np.exp(self.p * dt)
        x = self.p * dt
        small = np.abs(x) < 1e-4
        with np.errstate(divide='ignore', invalid='ignore'):
            # q1 = int_0^dt e^{p (dt - u)} u / dt du, q0 = (z - 1) / p - q1
            q1 = np.where(small, dt * (0.5 + x / 6 + x * x / 24),
                          (z - 1 - x) / (self.p * x))
            qz = np.where(small, dt * (1 + x / 2 + x * x / 6),
                          (z - 1) / self.p)
        self.z, self.q1, self.q0 = z, q1, qz - q1
        self.D = model.D
        self.G = self.D + np.einsum('k,kij->ij', self.m * 1.0,
                                    (self.q1[:, None, None] * self.R).real)
        # (each state: (poles, inputs))
        self.X = np.zeros((len(self.p), model.n), complex)
        self.a = np.zeros(model.n)

    def history(self):
        """h: b's part from the past."""
        Y = self.z[:, None] * self.X + self.q0[:, None] * self.a[None, :]
        return np.einsum('k,kij,kj->i', self.m, self.R, Y).real

    def advance(self, a):
        a = np.asarray(a, float)
        self.X = (self.z[:, None] * self.X + self.q0[:, None] * self.a[None, :]
                  + self.q1[:, None] * a[None, :])
        self.a = a


class Thevenin:
    """A source e(t) (V; a function of time) behind rs (ohm) and, beside
    the port, a capacitance c (F): the current into the port is (e - V) / rs
    - c dV/dt."""

    def __init__(self, e, rs, c=0.0):
        self.e, self.rs, self.c, self.v = e, float(rs), float(c), 0.0

    def iv(self, v, t, dt):
        g = -1.0 / self.rs - self.c / dt
        return self.e(t) / self.rs + self.c * self.v / dt + g * v, g

    def accept(self, v, t, dt):
        self.v = v


class Load:
    """A resistance r (ohm; None: none) to vtt (V) beside a capacitance c (F)
    to ground: the current into the port is -(V - vtt) / r - c dV/dt."""

    def __init__(self, r=None, c=0.0, vtt=0.0):
        self.r, self.c, self.vtt, self.v = r, float(c), float(vtt), 0.0

    def iv(self, v, t, dt):
        g = -(1.0 / self.r if self.r else 0.0) - self.c / dt
        j = (self.vtt / self.r if self.r else 0.0) + self.c * self.v / dt
        return j + g * v, g

    def accept(self, v, t, dt):
        self.v = v


def simulate(model, terms, t, log=None, newton=30, tol=1e-9):
    """Every port's voltage and current (into the port) (len(t), n) for the
    terminations `terms` (one a port: Thevenin, Load, an IBIS buffer: each
    iv(V, t, dt) -> (I into the port, dI/dV) and accept(V, t, dt)), on the
    uniform times t (s) from 0, every port at rest before."""
    t = np.asarray(t, float)
    dt = float(t[1] - t[0])
    if np.any(np.abs(np.diff(t) - dt) > 1e-6 * dt):
        raise ValueError("simulate takes uniform steps")
    n = model.n
    if len(terms) != n:
        raise ValueError(f"{len(terms)} terminations for {n} ports")
    tr = Transient(model, dt)
    Z = np.sqrt(model.z0)
    Zi = 1.0 / Z
    G = tr.G
    I_ = np.eye(n)
    V = np.zeros((len(t), n))
    I = np.zeros((len(t), n))
    a = np.zeros(n)
    hard = 0
    for k, tk in enumerate(t):
        h = tr.history()
        for it in range(newton):
            b = G @ a + h
            v = Z * (a + b)
            cur, g = zip(*(term.iv(vi, tk, dt) for term, vi in zip(terms, v)))
            cur, g = np.array(cur), np.array(g)
            F = Zi * (a - b) - cur
            if np.abs(F).max() <= tol * (1 + np.abs(cur).max()):
                break
            J = Zi[:, None] * (I_ - G) - (g * Z)[:, None] * (I_ + G)
            a = a - np.linalg.solve(J, F)
        else:
            hard += 1
        b = G @ a + h
        v = Z * (a + b)
        for term, vi in zip(terms, v):
            term.accept(vi, tk, dt)
        tr.advance(a)
        V[k], I[k] = v, Zi * (a - b)
    if hard and log:
        log(f"macromodel: {hard} step(s) of {len(t)} short of the Newton "
            "tolerance")
    return V, I
