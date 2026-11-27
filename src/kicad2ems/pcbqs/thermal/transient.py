"""
The board's temperature over time: C x' = q(t) - A x on grid.py's system (x the
rise over the ambient of every unknown, C the heat capacity of each, J/K),
each heat's share following a profile.

    prof = profile('pulse:2,8@1')       # on 2 s, off 8 s, from 1 s on
    prof(t), prof.before, prof.breaks(t_end)
    out = run(A, C, heats, profiles, t_end, read)   # dict(times_s, ...)

A profile multiplies one heat (a part's loss, the copper's) over time:

    step[:A,B][@T]        A before T (default 0), B from T (default 1)
    off[@T]               on until T, off from it (cooling)
    pulse:ON,OFF[,LOW,HIGH][@T]
                          HIGH (1) for ON s, LOW (0) for OFF s, repeating from
                          T; LOW before T
    <file>                rows of t_s, value (a .csv or .txt: commas, spaces or
                          tabs; # comments); linear between the rows, the first
                          row's value before it and the last's after

A file's values are the share itself, or watts where the caller says (the
thermal tool's --profile for a part: its loss over time). `before` is the value
at t < 0: what a run started from the steady state starts from.

The stepping is TR-BDF2 (Bank et al. 1985; Hosea and Shampine 1996): a
trapezoidal stage to t + gamma h, gamma = 2 - sqrt(2), then BDF2 to t + h. It is
second order and L-stable, so a step far longer than the copper's own time
constants damps them instead of ringing (the trapezoidal rule alone would), and
both stages take the one matrix (2 + sqrt 2) C / h + A, factored once for each
step length. No step crosses a profile's break: within an interval each
profile is smooth, and the steps grow geometrically from its start (each at
most GROWTH of the time since the break), on a ladder of lengths RUNG apart, so
the factors are few and every pulse of a train takes the same ones.

The self-heating of the copper (electrothermal.py): a heat may carry a slope,
dq/dx (W/K at each unknown: the copper's I^2 R at its resistance's temperature
coefficient), which its profile's square scales with it (a current profile);
the matrix is then A - level * diag(slope), the level held over each interval
at its middle value (exact for steps and pulses; a file's ramp moves it within
an interval, left out) and rounded to LEVELS so few matrices are factored.
"""
import math
import os
import time

import numpy as np
from scipy.sparse import diags

GAMMA = 2.0 - math.sqrt(2.0)
KAPPA = 2.0 + math.sqrt(2.0)      # 2 / gamma = (2 - gamma) / (1 - gamma)
GROWTH = 0.2                      # a step: at most this share of the time
#                                   since the interval's start
RUNG = 4.0                        # the step lengths' ladder
FIRST = 1e-4                      # the first step of an interval: this share
#                                   of it (or of the run, if shorter)
FLOOR = 1e-7                      # of the run: no step shorter
LEVELS = 50                       # the self-heating level's rounding: 1/50
MAX_STEPS = 50000


class Profile:
    """A heat's share against time (the module's docstring)."""

    def __init__(self, text, at, values, kind, period=None, watts=False):
        self.text, self.kind = text, kind
        self.at = np.asarray(at, float)          # break times
        self.values = np.asarray(values, float)  # step: (before, after)
        self.period = period                     # pulse: (on, off)
        self.watts = watts                       # a file's values in W

    @property
    def before(self):
        if self.kind == 'file':
            return float(self.values[0])
        if self.kind == 'pulse':
            return float(self.values[0])         # LOW
        return float(self.values[0])

    def __call__(self, t):
        t = np.asarray(t, float)
        if self.kind == 'step':
            return np.where(t < self.at[0], self.values[0], self.values[1])
        if self.kind == 'pulse':
            on, off = self.period
            u = np.mod(t - self.at[0], on + off)
            # (run() takes each interval's values a hair inside it)
            high = (t >= self.at[0]) & (u < on)
            return np.where(high, self.values[1], self.values[0])
        return np.interp(t, self.at, self.values)

    def breaks(self, t_end):
        """The times in (0, t_end) where it jumps or bends."""
        if self.kind == 'step':
            b = self.at
        elif self.kind == 'pulse':
            on, off = self.period
            n = int(max(0.0, t_end - self.at[0]) // (on + off)) + 1
            k = self.at[0] + (on + off) * np.arange(n)
            b = np.sort(np.r_[k, k + on])
        else:
            b = self.at
        return b[(b > 0) & (b < t_end)]

    def describe(self):
        return dict(text=self.text, kind=self.kind, before=self.before)


def _number(s, what):
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"profile {what!r}: {s!r} is not a number") from None


def profile(text, watts=False):
    """A Profile from its text (the module's docstring); watts: a file's
    values are watts, not a share. ValueError says what is wrong."""
    s = str(text).strip()
    head, _, at = s.partition('@')
    t0 = _number(at, s) if at.strip() else 0.0
    if t0 < 0:
        raise ValueError(f"profile {s!r}: its time must not be negative")
    name, _, args = head.partition(':')
    name = name.strip().lower()
    vals = [_number(x, s) for x in args.split(',') if x.strip()]
    if name == 'step':
        if len(vals) not in (0, 2):
            raise ValueError(f"profile {s!r}: step, or step:BEFORE,AFTER")
        return Profile(s, [t0], vals or [0.0, 1.0], 'step')
    if name == 'off':
        if vals:
            raise ValueError(f"profile {s!r}: off@T")
        return Profile(s, [t0], [1.0, 0.0], 'step')
    if name == 'pulse':
        if len(vals) not in (2, 4) or min(vals[:2]) <= 0:
            raise ValueError(
                f"profile {s!r}: pulse:ON,OFF[,LOW,HIGH][@T], ON and OFF in "
                "seconds, positive")
        low, high = (vals[2], vals[3]) if len(vals) == 4 else (0.0, 1.0)
        return Profile(s, [t0], [low, high], 'pulse', (vals[0], vals[1]))
    if os.path.isfile(s):
        rows = []
        with open(s, encoding='utf-8') as fh:
            for line in fh:
                line = line.split('#')[0].replace(',', ' ').split()
                if len(line) < 2:
                    continue
                try:
                    rows.append((float(line[0]), float(line[1])))
                except ValueError:
                    if rows:  # (a header only before the numbers)
                        raise ValueError(
                            f"profile file {s}: {' '.join(line)!r} is not "
                            "t, value") from None
        if not rows:
            raise ValueError(f"profile file {s}: no rows of t, value")
        t, v = np.array(rows).T
        if np.any(np.diff(t) <= 0):
            raise ValueError(f"profile file {s}: its times must increase")
        return Profile(s, t, v, 'file', watts=watts)
    raise ValueError(
        f"profile {s!r}: step[:A,B][@T], off@T, pulse:ON,OFF[,LOW,HIGH][@T] "
        "or a file of t, value")


def constant():
    return Profile('constant', [0.0], [1.0, 1.0], 'step')


def _intervals(profiles, t_end):
    b = {0.0, float(t_end)}
    for p in profiles:
        b.update(float(x) for x in p.breaks(t_end))
    return sorted(b)


def _rung(h, base):
    """The ladder's longest length at most h."""
    k = math.floor(math.log(h / base) / math.log(RUNG) + 1e-9)
    return base * RUNG ** k


def run(A, C, heats, t_end, read, start=None, slope=None, slope_heat=None,
        log=print):
    """The temperature from x(0) = start (None: the ambient everywhere) to
    t_end, s.

    A: the (n, n) sparse system (W/K); C: (n,) J/K (0: no capacity, a part's own
    node); heats: [(name, q (n,) W, Profile, square)] (square: the profile
    scales it by its square, a current's); read(x, t) -> {name: value} what is
    reported at each step (its parts' temperatures, the hottest copper); slope:
    (n,) W/K, the self-heating of heat `slope_heat` (its name), scaled by that
    heat's level. Returns dict(times_s, values={name: [..]} (read's, from
    t = 0), steps, factors, seconds)."""
    from .problem import factor_spd
    t0 = time.perf_counter()
    n = A.shape[0]
    C = np.asarray(C, float)
    x = np.zeros(n) if start is None else np.asarray(start, float).copy()
    heats = list(heats)
    sl = None
    if slope is not None and np.any(slope):
        sl = next((h for h in heats if h[0] == slope_heat), None)
        if sl is None:
            raise ValueError(f"the self-heating's heat {slope_heat!r} is not "
                             "among the heats")

    def level(h, t):
        v = float(h[2](t))
        return v * v if h[3] else v

    def q_at(t):
        q = np.zeros(n)
        for h in heats:
            v = level(h, t)
            if v:
                q += v * h[1]
        return q
    edges = _intervals([h[2] for h in heats], t_end)
    base = max(t_end * FLOOR, 1e-12)
    cache = {}

    def factor(h, lev):
        key = (round(h / base, 9), lev)
        if key not in cache:
            M = diags(KAPPA * C / h) + A
            if lev:
                M = M - diags(lev * slope)
            cache[key] = factor_spd(M)
        return cache[key]

    def record(t, x):
        times.append(t)
        for k, v in read(x, t).items():
            values.setdefault(k, []).append(v)
    times, values = [], {}
    record(0.0, x)
    steps = 0
    for a, b in zip(edges, edges[1:]):
        span = b - a
        lev = 0
        if sl is not None:
            lev = round(level(sl, 0.5 * (a + b)) * LEVELS) / LEVELS
        tau = 0.0
        first = max(min(span, t_end) * FIRST, base)
        while tau < span * (1 - 1e-12):
            left = span - tau
            h = _rung(max(first, GROWTH * tau), base)
            if h >= left or left - h < 0.5 * h:
                h = left            # the interval's last step: its rest
            ta = a + tau
            tg, tb = ta + GAMMA * h, ta + h
            # (the interval's own values at its ends: a hair inside)
            eps = 1e-9 * max(span, 1e-300)
            qa = q_at(min(max(ta, a + eps), b - eps))
            qg, qb = q_at(min(tg, b - eps)), q_at(b - eps if left == h else tb)
            if lev:
                Ax = A @ x - lev * slope * x
            else:
                Ax = A @ x
            lu = factor(h, lev)
            kc = KAPPA * C / h
            xg = lu.solve(kc * x - Ax + qa + qg)
            # BDF2: (C / (d h) + A) x1 = C (a xg - b x) / (d h) + q1,
            # 1 / d = KAPPA, a = 1 / (g (2 - g)), b = (1 - g)^2 / (g (2 - g))
            ga = 1.0 / (GAMMA * (2.0 - GAMMA))
            gb = (1.0 - GAMMA) ** 2 / (GAMMA * (2.0 - GAMMA))
            x = lu.solve(kc * (ga * xg - gb * x) + qb)
            tau = span if h == left else tau + h
            steps += 1
            if steps > MAX_STEPS:
                raise ValueError(
                    f"the transient needs over {MAX_STEPS} steps (a pulse "
                    "train far longer than its pulses): run its average as a "
                    "constant, or a shorter time")
            record(a + tau, x)
    out = dict(times_s=times, values=values, steps=steps, factors=len(cache),
               seconds=time.perf_counter() - t0)
    log(f"thermal transient: {steps} steps to {t_end:g} s, {len(cache)} "
        f"factorisations, {out['seconds']:.2f} s")
    return out


def time_constants(A, C, k=4, slope=None):
    """The board's k longest time constants, s: the largest mu of C v = mu A v
    (A symmetric positive definite; unknowns with no capacity are none of
    them)."""
    from scipy.sparse.linalg import LinearOperator, eigsh
    from .problem import factor_spd
    M = A if slope is None else A - diags(slope)
    C = np.asarray(C, float)
    n = len(C)
    if n < 3 or not C.any():
        return []
    lu = factor_spd(M)
    k = min(k, max(1, int((C > 0).sum()) - 2), n - 2)
    if k < 1:
        return []
    try:
        mu = eigsh(diags(C).tocsc(), k=k, M=M.tocsc(), which='LA',
                   Minv=LinearOperator((n, n), matvec=lu.solve,
                                       dtype=float),
                   return_eigenvectors=False, tol=1e-6, maxiter=5000)
    except Exception:  # noqa: BLE001 (a figure of the run, never its fault)
        return []
    return sorted((float(m) for m in mu if m > 0), reverse=True)
