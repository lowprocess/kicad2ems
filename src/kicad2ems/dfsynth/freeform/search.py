"""
What a free-form filter is asked for (Goal: the spec's prototype response as
limits at sample frequencies) and the search for a pixel pattern that meets
it (search: every move's exact cost known before one is made, pivot.State).
"""
import math

import numpy as np

from ..prototype import lowpass_s21_db
from ..calibrate import band
from .grid import y_to_s


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-7))


class Goal:
    """Limits at sample frequencies, from a dfsynth Spec's prototype:
    in the pass band |S11| and |S22| under -rl dB and |S21| over -il dB; in
    the stop band |S21| under the prototype's own, taken `guard` nearer the
    band (a skirt that much easier) and no deeper than `floor` dB, from
    where that is `start` dB.

    f (Hz), pas / stop (bool), att (dB, the stop limit)."""

    def __init__(self, spec, rl=15.0, il=3.0, floor=35.0, guard=0.1,
                 n_pass=9, n_stop=8, span=None, start=3.0):
        self.spec, self.rl, self.il, self.floor = spec, rl, il, floor
        self.guard, self.start = guard, start
        self.n_pass, self.n_stop, self.span = n_pass, n_stop, span
        kind, f0, fbw = band(spec)
        if kind not in ('lowpass', 'bandpass'):
            raise ValueError(f"no free-form goal for a {kind}")
        self.kind, self.f0, self.fbw = kind, f0, fbw
        # the stop band starts where the eased prototype is `start` dB down
        self.om_s = 1.0
        while self._att(self.om_s) < start and self.om_s < 50:
            self.om_s *= 1.01
        self.resample()

    def resample(self, rng=None):
        """The sample frequencies set: the pass band's spread evenly, the
        stop band's closer together at the skirts; with rng, each stop
        sample but the skirt's own drawn within its stretch (a search
        between fixed samples learns to put its spurious resonances
        between them: drawn again from time to time, it cannot)."""
        f0, n = self.f0, self.n_stop

        def spread(m):
            # 0 .. 1: evenly, or one drawn in each of m stretches (the
            # first: at 0, the skirt)
            if rng is None:
                return np.arange(m) / (m - 1)
            return (np.arange(m) + np.r_[0.0, rng.random(m - 1)]) / m
        if self.kind == 'lowpass':
            hi = (self.span or (0.0, 3.0))[1] * f0
            fp = np.linspace(0.0, 1.0, self.n_pass + 1)[1:] * f0
            a = self.om_s * f0
            fs = a * (hi / a) ** spread(2 * n)
        else:
            lo, hi = self.span or (max(0.3, 1 - 4 * self.fbw),
                                   min(2.2, 1 + 5 * self.fbw))
            fp = self._f(np.linspace(-1.0, 1.0, self.n_pass))
            f1, f2 = self._f(-self.om_s), self._f(self.om_s)
            fs = np.r_[f1 - spread(n) ** 2 * (f1 - lo * f0),
                       f2 + spread(n) ** 2 * (hi * f0 - f2)]
        f = np.r_[fp, fs]
        order = np.argsort(f)
        self.f = f[order]
        self.pas = np.r_[np.ones(len(fp), bool),
                         np.zeros(len(fs), bool)][order]
        self.stop = ~self.pas
        self.att = np.array([self._att(self.omega(x)) for x in self.f])

    def omega(self, f):
        """The prototype's normalised frequency at f."""
        x, k = f / self.f0, self.kind
        if k == 'lowpass':
            return x
        d = x - 1 / x
        if k == 'bandpass':
            return d / self.fbw
        return math.inf if d == 0 else self.fbw / d

    def _f(self, om):
        """f at the prototype's omega (band-pass and band-stop)."""
        a = (om * self.fbw if self.kind == 'bandpass'
             else self.fbw / np.where(np.asarray(om) == 0, 1e-12, om))
        return self.f0 * (a + np.sqrt(a * a + 4)) / 2

    def _att(self, om):
        s = self.spec
        o = abs(om) * (1 - self.guard)
        if not math.isfinite(o):
            return self.floor
        return min(self.floor,
                   -lowpass_s21_db(s.response, s.order, s.ripple, o))

    def limits(self, f):
        """(pass, stop, att) of this goal at other frequencies f (those
        between its bands: neither)."""
        f = np.atleast_1d(f)
        om = np.array([abs(self.omega(x)) for x in f])
        att = np.array([self._att(o) for o in om])
        pas = om <= 1.0
        if self.kind == 'lowpass':
            pas = f <= self.f0
        return pas, (att >= self.start) & ~pas, att

    def loss(self, S, at=None):
        """The mean squared excess (dB) over the limits, pass and stop band
        each its own mean; S: (nf, ..., 2, 2) on self.f (or with at = (pass,
        stop, att) on other frequencies). 0: every limit met."""
        pas, stop, att = at or (self.pas, self.stop, self.att)
        sh = (-1,) + (1,) * (S.ndim - 3)
        s11 = np.maximum(db(S[..., 0, 0]), db(S[..., 1, 1]))
        s21 = db(S[..., 1, 0])
        ex = (np.maximum(s11 + self.rl, 0) ** 2
              + np.maximum(-self.il - s21, 0) ** 2)
        out = (ex * pas.reshape(sh)).sum(0) / max(pas.sum(), 1)
        ex = np.maximum(s21 + att.reshape(sh), 0) ** 2
        return out + (ex * stop.reshape(sh)).sum(0) / max(stop.sum(), 1)


# ---- patterns ---------------------------------------------------------------
def padded(grid, mask):
    """mask with a ring of pixels round it: the feeds' ends on, the rest
    off."""
    m = np.zeros((grid.nx + 2, grid.ny + 2), bool)
    m[1:-1, 1:-1] = mask
    for side in (0, 1):
        r = grid.rows[side] + 1
        m[0 if side == 0 else -1, r:r + grid.feed_w] = True
    return m


def corner_contacts(grid, mask):
    """How many places two copper pixels meet at a corner alone: connected or
    not by the etch, and open in the model."""
    m = padded(grid, mask)
    a, b, c, d = m[:-1, :-1], m[1:, :-1], m[:-1, 1:], m[1:, 1:]
    return int(((a & d & ~b & ~c) | (b & c & ~a & ~d)).sum())


def perimeter(grid, mask):
    """The copper outline's length in pixel sides (the box's rim not
    counted)."""
    m = padded(grid, mask)
    return int((m[1:, 1:-1] != m[:-1, 1:-1]).sum()
               + (m[1:-1, 1:] != m[1:-1, :-1])[:, 1:-1].sum())


def symmetric_groups(grid, symmetry):
    """The pixels moved together: each alone (None), with its mirror image in
    the plane between the ports ('mirror') or with its image under a half
    turn about the box's centre ('rotate')."""
    nx, ny = grid.shape
    if symmetry in (None, 'none'):
        return [[q] for q in range(nx * ny)]
    if symmetry not in ('mirror', 'rotate'):
        raise ValueError("symmetry: none, mirror or rotate")
    r0, r1 = grid.rows
    if (r0 != r1) if symmetry == 'mirror' else (
            r1 != ny - grid.feed_w - r0):
        raise ValueError(f"the feeds' rows {grid.rows} do not have the "
                         f"{symmetry} symmetry")
    out = []
    for i in range(nx):
        for j in range(ny):
            q = i * ny + j
            p = ((nx - 1 - i) * ny
                 + (j if symmetry == 'mirror' else ny - 1 - j))
            if q <= p:
                out.append(sorted({q, p}))
    return out


def search(state, goal, steps=2000, smooth=0.0, tabu=0, rng=None, log=None,
           every=100, stop=None):
    """Moves a pattern toward the goal: at each step the cost of every move
    is computed (exact: state.trial), and the best is made. cost = goal.loss
    + smooth x the copper outline's length in pixel sides. Moves leaving two
    pixels joined at a corner alone are not made. tabu = 0: it ends at a
    pattern no single move improves. tabu > 0: the best move is made also
    where it costs (the mildest ascent), and a move made is not made again
    for about `tabu` steps unless it beats the best pattern met, so the
    search leaves such a pattern instead of ending there. Stops after
    `steps`, or when stop() says so. Returns (the best pattern met, its
    cost); the state is left on it."""
    grid, groups = state.grid, state.groups
    rng = rng or np.random.default_rng(0)

    def outline_change():
        # per pixel toggled: its sides at copper it would lose, less those
        # it would gain
        m = padded(grid, state.mask)
        c = m[1:-1, 1:-1]
        diff = sum((c != n).astype(int)
                   for n in (m[:-2, 1:-1], m[2:, 1:-1], m[1:-1, :-2],
                             m[1:-1, 2:]))
        d = (4 - 2 * diff).ravel()
        return np.array([d[g].sum() for g in groups])

    cur = (float(goal.loss(y_to_s(state.y())))
           + smooth * perimeter(grid, state.mask))
    best, best_mask = cur, state.mask.copy()
    until = np.zeros(len(groups), int)
    for step in range(steps):
        if stop is not None and stop():
            break
        c = goal.loss(y_to_s(state.trial()))
        if smooth:
            c = c + smooth * (perimeter(grid, state.mask) + outline_change())
        # (to 1e-6: the matrix's last digits differ from run to run, its
        # fill's threads summing in another order, and the best of two
        # moves that close would be another: the same spec had given
        # another pattern each time, 2.7 and 5.2 dB^2 on the low-pass)
        c = np.where(np.isfinite(c), np.round(c, 6), np.inf)
        if tabu:
            c = np.where((until > step) & (c >= best - 1e-9), np.inf, c)
        corners, chosen = corner_contacts(grid, state.mask), None
        while np.isfinite(c).any():
            m = int(np.argmin(c))
            if not tabu and c[m] >= cur - 1e-9:
                break
            trial = state.mask.copy()
            trial.ravel()[groups[m]] ^= True
            if corner_contacts(grid, trial) > corners:
                c[m] = np.inf
                continue
            chosen = m
            break
        if chosen is None:
            break
        state.apply(chosen)
        cur = float(c[chosen])
        if tabu:
            until[chosen] = step + 1 + int(tabu * rng.uniform(0.5, 1.5))
        if cur < best - 1e-12:
            best, best_mask = cur, state.mask.copy()
        if log and (step + 1) % every == 0:
            log(f"    step {step + 1}: cost {cur:.3f} (best {best:.3f})")
    if not np.array_equal(state.mask, best_mask):
        state.set(best_mask)
    return best_mask, best
