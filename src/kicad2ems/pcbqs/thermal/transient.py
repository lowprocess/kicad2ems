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
step length. Each step's local error is estimated from the stages' slopes
(Hosea and Shampine's, C^-1 (q - A x) at the three points: matrix-vector
products alone) against ATOL + RTOL x the largest rise: a step over it is taken
again at half the length, one well inside it lets the next double. The lengths
are a ladder of powers of two, so the factors are few and every pulse of a
train reuses them; those kept are held within a memory budget (cache_bytes), the
longest unused given up first, and an interval's last step, which ends on its
break, is solved by conjugate gradients preconditioned with the factor of the
rung below it. No step crosses a profile's break: within an interval each
profile is smooth, and the first step after a break is the last interval's
first.

A grid of more than DIRECT_MAX unknowns is run reduced (reduce): a factor per
step length of the Jetson carrier's 179,000 unknowns is 0.8 GB and 6 s, and a
pulse train wants a dozen (they had held 16 GB). The heats gathered by profile,
the start and the self-heating's direction are the inputs of a rational block
Krylov basis (moments at the steady state, whose factor the steady solve has,
and at two shifts set by the shortest stretch between breaks: two more
factors), the grid projected on it by congruence (PRIMA: passive, the steady
state exact) and diagonalised, and that model stepped as the grid would be, its
temperatures lifted back at each step. On the test boards it is within 1 mK of
the grid stepped itself at every break.

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
# the local error's constant (Hosea and Shampine 1996): 2 |(-3 g^2 + 4 g - 2) /
# (12 (2 - g))|
ERR = 2.0 * abs((-3 * GAMMA ** 2 + 4 * GAMMA - 2) / (12 * (2 - GAMMA)))
ATOL, RTOL = 1e-3, 1e-4           # K, and of the largest rise: a step's error
SAFETY = 2.0                      # a rung up only with this margin
RUNG = 2.0                        # the step lengths' ladder
FIRST = 1e-3                      # an interval's first step: at most this
#                                   share of it
FLOOR = 1e-7                      # of the run: the ladder's foot
LEVELS = 50                       # the self-heating level's rounding: 1/50
MAX_STEPS = 50000
CACHE_MAX = 4 << 30               # bytes of factors kept, at most
# a system larger than this many unknowns is run reduced (reduce): a factor
# per step length of a 179,000-unknown grid (the Jetson's, 10 layers) is 0.8 GB
# and 6 s, and a pulse train wants a dozen
DIRECT_MAX = 40000
MOMENTS = 6                       # the reduced model's moments at each shift
# its shifts, 1/s: times the shortest stretch between breaks over these
SHIFTS = (0.0, 100.0, 1000.0)
CACHE_SHARE = 0.25                # of the memory available, at most


def cache_bytes():
    """The factors' memory budget: CACHE_MAX, or CACHE_SHARE of what is
    available if less (at least one factor is always kept)."""
    try:
        from kicad2ems.emsdev.memory import available
        return int(min(CACHE_MAX, CACHE_SHARE * available()))
    except Exception:  # noqa: BLE001 (no measure: the cap)
        return CACHE_MAX


def _pcg(A, b, solve, x, tol=1e-12, most=60):
    """Conjugate gradients on A x = b from x, preconditioned by solve (a
    factor of a nearby A); stops when the preconditioned residual, the error's
    estimate, is tol of x; None if not within `most` iterations."""
    x = np.array(x, float)
    r = b - A @ x
    z = solve(r)
    p, rz = z.copy(), float(r @ z)
    for _ in range(most):
        if np.linalg.norm(z) <= tol * max(np.linalg.norm(x), 1e-300):
            return x
        Ap = A @ p
        a = rz / float(p @ Ap)
        x += a * p
        r -= a * Ap
        z = solve(r)
        rz, old = float(r @ z), rz
        p = z + (rz / old) * p
    return None


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


def _rung_k(h, base):
    """The ladder's rung at most h: base RUNG^k."""
    return max(0, int(math.floor(math.log(h / base) / math.log(RUNG) + 1e-9)))


def run(A, C, heats, t_end, read, start=None, slope=None, slope_heat=None,
        log=print, tol=None, lift=None):
    """The temperature from x(0) = start (None: the ambient everywhere) to
    t_end, s.

    A: the (n, n) sparse system (W/K); C: (n,) J/K (0: no capacity, a part's own
    node); heats: [(name, q (n,) W, Profile, square)] (square: the profile
    scales it by its square, a current's); read(x, t) -> {name: value} what is
    reported at each step (its parts' temperatures, the hottest copper); slope:
    (n,) W/K, the self-heating of heat `slope_heat` (its name), scaled by that
    heat's level, or an (n, n) matrix of it (a reduced model's); tol: K, each
    step's local error (default ATOL + RTOL x the largest rise so far); lift(x)
    -> the temperatures x stands for (a reduced model's coordinates: reduce),
    for the error, the bound and read. Returns dict(times_s, values={name:
    [..]} (read's, from t = 0), steps, rejected, factors, seconds)."""
    from collections import OrderedDict
    from .problem import factor_spd
    t0 = time.perf_counter()
    n = A.shape[0]
    C = np.asarray(C, float)
    x = np.zeros(n) if start is None else np.asarray(start, float).copy()
    heats = list(heats)
    sl = None
    full = (lambda v: v) if lift is None else lift
    matrix = slope is not None and getattr(slope, 'ndim', 1) == 2
    if slope is not None and (matrix or np.any(slope)):
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
    # the factors kept, oldest use first, within a memory budget (each step
    # length its own: the Jetson's 20 had held 16 GB)
    cache, made = OrderedDict(), [0]
    room = cache_bytes()
    held = [0]
    capacity = C > 0
    inv_c = np.where(capacity, 1.0 / np.where(capacity, C, 1.0), 0.0)

    def factor(k, lev):
        """The factor of the rung k's matrix, made if not kept."""
        key = (k, lev)
        if key in cache:
            cache.move_to_end(key)
            return cache[key][0]
        h = base * RUNG ** k
        M = diags(KAPPA * C / h) + A
        if lev:
            M = M - lev * (slope if matrix else diags(slope))
        lu = factor_spd(M)
        made[0] += 1
        size = 12 * (lu.L.nnz + lu.U.nnz)
        while cache and held[0] + size > room:
            _, (_, s) = cache.popitem(last=False)
            held[0] -= s
        cache[key] = (lu, size)
        held[0] += size
        return lu

    def system(h, k, lev):
        """solve(rhs) for (KAPPA C / h + A - lev S): the rung's factor, or, for
        a step off the ladder (an interval's last), conjugate gradients
        preconditioned by the factor of the rung below (the two a factor of
        RUNG apart at most: a few iterations)."""
        lu = factor(k, lev)
        if abs(h - base * RUNG ** k) <= 1e-12 * h:
            return lu.solve
        M = diags(KAPPA * C / h) + A
        if lev:
            M = M - lev * (slope if matrix else diags(slope))
        M = M.tocsr()

        def solve(r):
            y = _pcg(M, r, lu.solve, lu.solve(r))
            return lu.solve(r) if y is None else y
        return solve

    def Sx(v):
        return slope @ v if matrix else slope * v

    def record(t, x):
        times.append(t)
        for key, v in read(full(x), t).items():
            values.setdefault(key, []).append(v)
    ga = 1.0 / (GAMMA * (2.0 - GAMMA))
    gb = (1.0 - GAMMA) ** 2 / (GAMMA * (2.0 - GAMMA))
    times, values = [], {}
    record(0.0, x)
    steps = rejected = 0
    scale = float(np.abs(full(x)).max()) if len(x) else 0.0
    k_start = None
    for a, b in zip(edges, edges[1:]):
        span = b - a
        lev = 0
        if sl is not None:
            lev = round(level(sl, 0.5 * (a + b)) * LEVELS) / LEVELS
        eps = 1e-9 * max(span, 1e-300)  # (each interval's own values: a hair
        #                                  inside it)
        # the first step: the last interval's first accepted one, at most a
        # FIRST of this one
        k_cap = _rung_k(max(span * FIRST, base), base)
        k = k_cap if k_start is None else min(k_start, k_cap)
        tau, first = 0.0, True
        while tau < span * (1 - 1e-12):
            left = span - tau
            h = base * RUNG ** k
            last = h >= left or left - h < 0.25 * h
            if last:
                h = left
                k = _rung_k(h, base)
            ta = a + tau
            qa = q_at(min(max(ta, a + eps), b - eps))
            qg = q_at(min(ta + GAMMA * h, b - eps))
            qb = q_at(b - eps if last else ta + h)
            Ax = A @ x - (lev * Sx(x) if lev else 0.0)
            solve = system(h, k, lev)
            kc = KAPPA * C / h
            xg = solve(kc * x - Ax + qa + qg)
            x1 = solve(kc * (ga * xg - gb * x) + qb)
            # the local error (Hosea and Shampine): 2 |k| h (f_n / g -
            # f_g / (g (1 - g)) + f_1 / (1 - g)), f = C^-1 (q - A x), over the
            # unknowns with a capacity
            Ag = A @ xg - (lev * Sx(xg) if lev else 0.0)
            A1 = A @ x1 - (lev * Sx(x1) if lev else 0.0)
            s = ((qa - Ax) / GAMMA - (qg - Ag) / (GAMMA * (1.0 - GAMMA)) +
                 (qb - A1) / (1.0 - GAMMA)) * inv_c
            err = ERR * h * float(np.abs(full(s)).max()) if n else 0.0
            big = float(np.abs(full(x1)).max()) if n else 0.0
            bound = tol if tol is not None else ATOL + RTOL * max(scale, big)
            if err > bound and k > 0 and h > base * 1.0001:
                rejected += 1
                k = min(k, _rung_k(h, base)) - 1   # the step again, halved
                if rejected > MAX_STEPS:
                    raise ValueError("the transient's steps keep failing")
                continue
            x = x1
            tau = span if last else tau + h
            steps += 1
            if first:
                k_start, first = k, False
            scale = max(scale, big)
            if steps > MAX_STEPS:
                raise ValueError(
                    f"the transient needs over {MAX_STEPS} steps (a pulse "
                    "train far longer than its pulses): run its average as a "
                    "constant, or a shorter time")
            record(a + tau, x)
            # the next step: a rung up while the error is well inside the
            # bound (second order: a doubled step's is 8 times this one's)
            if not last and err * RUNG ** 3 * SAFETY <= bound:
                k += 1
    out = dict(times_s=times, values=values, steps=steps, rejected=rejected,
               factors=made[0], seconds=time.perf_counter() - t0)
    log(f"thermal transient: {steps} steps to {t_end:g} s ({rejected} "
        f"rejected), {made[0]} factorisations, {out['seconds']:.2f} s")
    return out


def reduce(A, C, heats, start=None, slope=None, factor0=None, shifts=(0.0,),
           moments=None):
    """A reduced model of C x' = q(t) - (A - lev S) x (rational block Krylov,
    PRIMA's congruence: Odabasioglu, Celik and Pileggi 1998) for a system too
    large for a factor per step length: the inputs are the heats gathered by
    their profile (those that move together are one), the start and the
    self-heating's own direction; for each shift s, the moments (A + s C)^-1
    (C (A + s C)^-1)^j of them, `moments` deep, made orthonormal. With V that
    basis, V^T A V, V^T C V (positive definite: the congruence keeps it
    passive and stable) and V^T S V, diagonalised (A_r w = lambda C_r w), so
    the model is run's own form: A = diag(lambda), C = 1. It matches the
    response's moments at each shift: at 0 the steady state exactly (any
    profile's long-time limit), at the others the faster changes.

    factor0: A's factor (the steady solve's), reused for the shift 0. Returns
    dict(A, C, heats, start, slope (an m x m matrix, or None), T (n, m): the
    temperatures of the model's coordinates, x = T z), size)."""
    from scipy.sparse import csc_matrix
    from .problem import factor_spd
    n = A.shape[0]
    C = np.asarray(C, float)
    groups, order = {}, []
    for name, q, pr, sq in heats:
        key = (id(pr), sq)
        if key not in groups:
            groups[key] = np.zeros(n)
            order.append(key)
        groups[key] += q
    cols = [groups[k] for k in order if np.any(groups[k])]
    if start is not None and np.any(start):
        cols.append(np.asarray(start, float))
    S = None if slope is None or not np.any(slope) else np.asarray(slope, float)
    B = np.column_stack(cols) if cols else np.zeros((n, 1))
    q = moments or MOMENTS
    blocks = []
    for s in shifts:
        lu = (factor0 if (s == 0 and factor0 is not None) else
              factor_spd((A + diags(s * C)) if s else A))
        W = lu.solve(B)
        if S is not None:  # the self-heating's direction from the first block
            W = np.column_stack([W, lu.solve(S[:, None] * W)])
        for _ in range(q):
            blocks.append(W)
            W = lu.solve(C[:, None] * W)
    V = np.column_stack(blocks)
    # orthonormal, the near-dependent dropped (twice: Gram-Schmidt's loss)
    V /= np.maximum(np.linalg.norm(V, axis=0), 1e-300)
    for _ in range(2):
        V, R = np.linalg.qr(V)
        keep = np.abs(np.diag(R)) > 1e-10 * np.abs(np.diag(R)).max()
        V = V[:, keep]
    Ar = V.T @ (A @ V)
    Cr = V.T @ (C[:, None] * V)
    Ar, Cr = 0.5 * (Ar + Ar.T), 0.5 * (Cr + Cr.T)
    # (C_r is positive definite while no basis vector lies on the unknowns
    # with no capacity alone: none does, A^-1 spreads every one)
    from scipy.linalg import eigh
    lam, Wm = eigh(Ar, Cr)
    T = V @ Wm
    m = T.shape[1]
    out = dict(A=csc_matrix(diags(lam)), C=np.ones(m), T=T, size=m,
               heats=[(name, T.T @ q, pr, sq) for name, q, pr, sq in heats])
    # z0: x0 = T z0 (x0 is in the basis), z0 = W^T C_r V^T x0
    out['start'] = (None if start is None else
                    Wm.T @ (Cr @ (V.T @ np.asarray(start, float))))
    out['slope'] = (None if S is None else
                    csc_matrix(T.T @ (S[:, None] * T)))
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
