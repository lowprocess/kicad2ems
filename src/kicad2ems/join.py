"""
One band from two solvers: a quasi-static solver below the frequency it says it is valid to, a full-wave one above.

pcbqs solves the copper's currents and charge without radiation: right from DC, where a full-wave solver's
equations lose the inductive part to rounding, and good to about the frequency where the ports' copper is a tenth
of a wavelength long (its `valid_to_Hz`). mom25d (or another full-wave solver) is right from there up. When a run
has both, `join` makes one answer: the quasi-static solver's S below the crossover, the full-wave one's above, and
across a short band around the crossover (a fifth of a decade each side by default) a raised-cosine blend in
log f, so the joined curve has no step. How far the two disagree in that band is said with it: where they agree
the join is seamless; where they do not, one of them is wrong there, and the report says by how much.

    S, info = join(freq, S_low, S_high, crossover)
    pick(results, solvers)          # (low, high, crossover) of a run's results, or None
"""
import numpy as np

BLEND_DECADES = 0.2         # each side of the crossover: the blend's half-width, in decades
PREFERRED_HIGH = ('mom25d',)    # the full-wave solver taken above the crossover when there are several


def weight(freq, crossover, decades=BLEND_DECADES):
    """The full-wave solver's share at each frequency: 0 below crossover / 10^decades, 1 above crossover *
    10^decades, a raised cosine in log f between."""
    x = (np.log10(np.asarray(freq, float)) - np.log10(crossover)) / decades     # -1 .. 1 across the blend
    return np.where(x <= -1, 0.0, np.where(x >= 1, 1.0, 0.5 * (1 - np.cos(np.pi * (np.clip(x, -1, 1) + 1) / 2))))


def join(freq, S_low, S_high, crossover, decades=BLEND_DECADES):
    """(S, info): the two answers joined at the crossover (Hz). info: crossover_Hz, blend_Hz (the band where both
    count), low_points and high_points (frequencies from each alone), mismatch (the largest |S_low - S_high| over
    the blend band, or at the frequency nearest the crossover when no frequency is in it; None when the band is
    entirely one side)."""
    freq = np.asarray(freq, float)
    w = weight(freq, crossover, decades)
    lo, hi = np.asarray(S_low), np.asarray(S_high)
    shape = (-1,) + (1,) * (lo.ndim - 1)
    S = (1 - w).reshape(shape) * np.nan_to_num(lo) + w.reshape(shape) * np.nan_to_num(hi)
    S = np.where(np.isnan(lo) & (w < 1).reshape(shape), np.nan, S)          # a term one side did not compute
    S = np.where(np.isnan(hi) & (w > 0).reshape(shape), np.nan, S)
    band = (w > 0) & (w < 1)
    at = np.flatnonzero(band) if band.any() else (
        [int(np.argmin(np.abs(np.log(freq / crossover))))] if freq[0] <= crossover <= freq[-1] else [])
    diff = np.abs(lo[at] - hi[at]) if len(at) else np.zeros(0)
    mismatch = float(np.nanmax(diff)) if diff.size and np.isfinite(diff).any() else None
    return S, dict(crossover_Hz=float(crossover), blend_Hz=[float(crossover / 10 ** decades), float(crossover * 10 ** decades)],
                   low_points=int((w == 0).sum()), high_points=int((w == 1).sum()), mismatch=mismatch)


def pick(results, plugins):
    """(low, high, crossover) of a run's results {solver: Result}: the quasi-static solver (its info has valid_to_Hz)
    and the full-wave one to join it with (mom25d first; a line model is neither), or None when there are not both,
    or their ports differ."""
    low = next((n for n, r in results.items() if (r.info or {}).get('valid_to_Hz')), None)
    if low is None:
        return None
    full = [n for n in results if n != low and getattr(plugins.get(n), 'consumes', 'board') != 'network'
            and not (results[n].info or {}).get('valid_to_Hz')]
    full = sorted(full, key=lambda n: (n not in PREFERRED_HIGH, list(results).index(n)))
    if not full or results[full[0]].names != results[low].names:
        return None
    return low, full[0], float(results[low].info['valid_to_Hz'])
