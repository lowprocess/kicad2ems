"""
dfsynth.freeform: filters of no named shape (`--topology freeform`).

The filter is a box of square pixels between two feeds, each pixel copper or
not, and a search for the pattern whose response meets the spec's prototype:

    grid    the pixel grid and its moment-method matrix (mom25d on a
            structured mesh, filled once a frequency for the whole box)
    pivot   a pattern's exact response from that matrix, and the response
            after any change of a few pixels without solving again
    search  the goal (the prototype as limits at sample frequencies) and
            the search (the best single change, of all of them, at each step)
    seeds   the patterns a search starts from
    layout  a pattern as a dfsynth Design: the board, the footprint, the
            model in dfsynth's validation and plots

    design = synthesise(Spec('bandpass', 'freeform', 5e9, 500e6, order=3),
                        Substrate.preset('RO4003C-0.813'))
    design = rescale(design, 1.04)      # from a full-wave result of it
    design = retune(design, f, full, model)

The pixel model is coarse (two triangles a pixel), so a full-wave run of the
board differs from it: a few percent of frequency, more in bandwidth, some dB
of match. dfsynth's --em-tune corrects from such a run. First rescale: the
pixels made larger by the frequency error, the pattern and so its response's
shape kept. Then retune: the full-wave band edges mapped onto the model's and
the search gone on with, the model asked for the spec where the full-wave
result will have it and for as much more as the full-wave levels lay above
its own. A search on can lose in match what it gains in band (it did, twice
in three band-pass runs), so the round whose full-wave result costs least
is the one kept.
"""
import math
import time

import numpy as np

from ..microstrip import C0
from ..calibrate import band, edges
from ..circuit import db
from .grid import PixelGrid
from .pivot import State
from .search import Goal, search, symmetric_groups
from . import seeds
from .layout import design as as_design, response

# pixels across a guided wavelength (the feed's): a pixel is the 50 ohm
# width over a whole number
PER_WAVELENGTH = 80.0
# a low-pass's or band-stop's box, in guided wavelengths at f0
BOX = (0.17, 0.13)
# matrices are 16 N^2 bytes a frequency: no more of the free memory than this
MEMORY_SHARE = 0.6


def pixel(spec, sub):
    """(pixel side mm, pixels across the feed): the feed's width over the
    whole number that gives about PER_WAVELENGTH pixels a wavelength."""
    w = sub.width(spec.z0)
    lam = C0 / (spec.f0 * math.sqrt(sub.eeff(w, spec.f0))) * 1e3
    k = max(2, int(round(w * PER_WAVELENGTH / lam)))
    return w / k, k


def default_box(spec, sub, p):
    """(nx, ny) pixels: a band-pass's loops with two pixels to spare all
    round, else BOX wavelengths; even numbers."""
    even = lambda n: int(2 * math.ceil(n / 2))
    w = sub.width(spec.z0)
    lam = C0 / (spec.f0 * math.sqrt(sub.eeff(w, spec.f0))) * 1e3
    if band(spec)[0] == 'bandpass':
        # (as wide as the loops and their margins exactly: a start pattern
        # with the feeds' symmetry)
        need, arm = seeds.loops_size(spec, sub, p)
        return need + 4, arm + 6
    return even(BOX[0] * lam / p), even(BOX[1] * lam / p)


def make_grid(spec, sub, box=None, pitch=None, log=None):
    """The grid for a spec: box (mm, mm) or default_box, pitch (mm) or
    pixel(); the feeds where the start pattern wants them."""
    p, k = pixel(spec, sub)
    if pitch:
        k = max(1, int(round(sub.width(spec.z0) / pitch)))
        p = pitch
    for what, lim in (('trace', sub.min_width), ('gap', sub.min_gap)):
        if p < lim:
            raise ValueError(
                f"the pixels are {p:.3f} mm, under the fabrication minimum "
                f"{what} {lim} mm: a larger --pixel")
    nx, ny = ((max(4, int(round(box[0] / p))), max(4, int(round(box[1] / p))))
              if box else default_box(spec, sub, p))
    if ny < k + 2:
        raise ValueError(f"the box is {ny} pixels high, the feed {k} wide")
    rows = None
    if band(spec)[0] == 'bandpass':
        rows = seeds.loops_rows(spec, sub, p, ny, k)
    from kicad2ems.emsdev.memory import available
    n = 3 * nx * ny                       # about: two triangles a pixel
    need = 16.0 * n * n * (9 + 2 * N_STOP + 3)
    if need > MEMORY_SHARE * available():
        raise ValueError(
            f"a box of {nx} x {ny} pixels ({n:,} unknowns) needs about "
            f"{need / 1e9:.0f} GB, {available() / 1e9:.0f} GB are free: a "
            "smaller --box or a larger --pixel")
    if log:
        log(f"  grid: {nx} x {ny} pixels of {p:.3f} mm "
            f"({nx * p:.2f} x {ny * p:.2f} mm), feeds {k} pixels wide")
    return PixelGrid(sub, nx, ny, p, k, rows=rows)


def start_pattern(spec, grid):
    if band(spec)[0] == 'bandpass':
        return seeds.loops(grid, spec)
    return seeds.through(grid)


def default_symmetry(spec, grid):
    r0, r1 = grid.rows
    if r0 == r1:
        return 'mirror'
    return 'rotate' if r1 == grid.ny - grid.feed_w - r0 else 'none'


STAGES = 5
# the price of a pixel side of copper outline, in the cost's dB^2. Measured
# on the default low-pass (2 GHz, order 5, RO4003C-0.508) and band-pass (5
# GHz / 500 MHz, order 3, RO4003C-0.813), the cost on a dense sweep after
# 1500 steps: 0.02: 7.2 and 6.4-7.0 (ragged patterns, a narrow parasitic
# resonance in the low-pass's stop band); 0.05: 2.7, 5.2 and 2.6; 0.1: 6.7
# (the low-pass). The runs were not yet repeatable (search: costs rounded
# since): the low-pass's two at 0.05 are the spread to read the rest by
SMOOTH = 0.05
# stop-band samples on each side of a band (a low-pass: twice this above it)
N_STOP = 14


def _search(grid, goal, mask, fmap, steps, seconds, smooth, symmetry, log,
            rng):
    """The search on `mask` with the goal's limits at the model frequencies
    fmap(goal.f), in STAGES stages: the stop band's samples drawn again for
    each. After each stage
    the pattern is swept densely, and the one with the least cost on its
    dense sweep is kept: (pattern, that cost, changes made)."""
    groups = symmetric_groups(grid, symmetry)
    t0 = time.time()
    out = lambda: bool(seconds) and time.time() - t0 > seconds
    fd = np.linspace(goal.f.min(), goal.f.max(), 401)
    lim = goal.limits(fd)
    dense = lambda m: float(goal.loss(response(grid, m, fmap(fd)), lim))
    best, best_cost, made = np.array(mask, bool), None, 0
    state = None
    for stage in range(STAGES):
        if out():
            break
        if stage:
            goal.resample(rng)
        if state is None:
            state = State(grid, fmap(goal.f), mask, groups, symmetry)
        else:
            state.freqs = [float(x) for x in fmap(goal.f)]
            state.set(mask)
        mask, cost = search(state, goal, steps=steps // STAGES, rng=rng,
                            tabu=30, smooth=smooth, stop=out)
        made = state.made
        c = dense(mask)
        if log:
            log(f"    stage {stage + 1} of {STAGES}: cost {cost:.2f} on the "
                f"samples, {c:.2f} on a dense sweep")
        if best_cost is None or c < best_cost:
            best, best_cost = mask.copy(), c
    if best_cost is None:
        best_cost = dense(best)
    return best, best_cost, made


def _notes(grid, mask, loss):
    i, j = np.nonzero(mask)
    p = grid.pitch
    ext = ((i.max() - i.min() + 1) * p, (j.max() - j.min() + 1) * p)
    w, h = grid.size()
    return [
        f"free-form: box {w:.2f} x {h:.2f} mm ({grid.nx} x {grid.ny} pixels "
        f"of {p:.3f} mm), its copper {ext[0]:.2f} x {ext[1]:.2f} mm",
        ("the pixel model meets every limit asked of it"
         if loss < 1e-9 else
         f"the pixel model misses its limits by {math.sqrt(loss):.1f} dB "
         "(the root of the pass band's mean square excess plus the stop "
         "band's, on a dense sweep)")]


def synthesise(spec, sub, box=None, pitch=None, steps=1500, seconds=0.0,
               symmetry=None, rl=15.0, floor=35.0, smooth=SMOOTH, start=None,
               log=None, seed=0):
    """The free-form Design of a spec. box: (width, height) mm, pitch: the
    pixel's side mm (defaults: default_box, pixel). steps: the search's;
    seconds: its time limit (0: none). symmetry: 'mirror', 'rotate', 'none'
    (default: what the feeds have). rl, floor: the pass band's return loss
    and the stop band's deepest limit asked for, dB. smooth: the price of a
    pixel side of copper outline (plainer shapes for more). start: a
    pattern to search on from ((nx, ny) bool) instead of the start
    pattern."""
    spec.check()
    if band(spec)[0] not in ('lowpass', 'bandpass') or spec.kind == 'highpass':
        raise ValueError("free-form: low-pass and band-pass")
    grid = make_grid(spec, sub, box, pitch, log)
    goal = Goal(spec, rl=rl, floor=floor, n_stop=N_STOP)
    mask = start_pattern(spec, grid) if start is None else np.array(start,
                                                                    bool)
    if mask.shape != grid.shape:
        raise ValueError(f"the start pattern is {mask.shape}, the grid "
                         f"{grid.shape}")
    symmetry = symmetry or default_symmetry(spec, grid)
    if log:
        log(f"  search: {steps} steps, {len(goal.f)} frequencies, "
            f"{grid.N:,} unknowns, symmetry {symmetry}")
    t0 = time.time()
    mask, loss, made = _search(grid, goal, mask, lambda f: f, steps, seconds,
                               smooth, symmetry, log,
                               np.random.default_rng(seed))
    if log:
        log(f"  search: {made} changes in {time.time() - t0:.0f} s")
    d = as_design(spec, grid, mask, _notes(grid, mask, loss))
    d.freeform = dict(goal=goal, symmetry=symmetry, steps=steps,
                      seconds=seconds, smooth=smooth, seed=seed, loss=loss,
                      fmap=(0.0, 1.0))
    return d


def frequency_map(spec, f, s21_full, s21_model):
    """(a, b): model frequency = a + b x full-wave frequency, the line
    through the two responses' band edges (3 dB under each one's peak); a
    low-pass: through 0 and the cut-offs. None where an edge is missing."""
    ef = edges(spec, f, db(s21_full), relative=True)
    em = edges(spec, f, db(s21_model), relative=True)
    if 'fc' in ef:
        if not (ef['fc'] and em['fc']):
            return None
        return 0.0, em['fc'] / ef['fc']
    if not all((ef['f1'], ef['f2'], em['f1'], em['f2'])):
        return None
    b = (em['f2'] - em['f1']) / (ef['f2'] - ef['f1'])
    return em['f1'] - b * ef['f1'], b


def centre(spec, f, s21):
    """Where a response's band is, Hz: a band-pass's edges' geometric mean, a
    low-pass's cut-off (3 dB under its peak); None without them."""
    e = edges(spec, f, db(s21), relative=True)
    if 'fc' in e:
        return e['fc'] or None
    return math.sqrt(e['f1'] * e['f2']) if e['f1'] and e['f2'] else None


def rescale(design, ratio, log=None):
    """The design with its pixels `ratio` times the size (the pattern
    untouched): its band 1 / ratio as high, near enough (the substrate's
    height stays), its shape kept. The first correction from a full-wave
    result: ratio = that result's centre over the pixel model's, which the
    spec was searched on. None where the pixels would fall under the
    fabrication minimums."""
    g, sub = design.grid, design.grid.sub
    p = g.pitch * ratio
    if p < max(sub.min_width, sub.min_gap) or not 0.5 < ratio < 2.0:
        return None
    if log:
        log(f"  the full-wave band lies {100 * (ratio - 1):+.1f} % from the "
            f"pixel model's: the pixels scaled to {p:.3f} mm (from "
            f"{g.pitch:.3f}), the pattern kept")
    grid = PixelGrid(sub, g.nx, g.ny, p, g.feed_w, g.feed_l, g.rows, g.cells)
    notes = _notes(grid, design.mask, design.freeform['loss'])[:1] + [
        f"pixels scaled by {ratio:.4f} from a full-wave result"]
    d = as_design(design.spec, grid, design.mask, notes)
    d.freeform = dict(design.freeform)
    return d


# level_offsets: no more than this is asked of the model on top of a limit
# (pass band's |S11|, any |S21|), dB
OFFSET_MAX = (10.0, 8.0)


def level_offsets(spec, f, full, model, ab):
    """(f, d11, d21): how far the full-wave |S11| and |S21| (full = (s21,
    s11) on f) lie above the pixel model's at the mapped frequencies (ab:
    frequency_map's), dB, each curve first replaced by its largest value
    within a sixth of the band about each frequency (the reflection zeros
    and the transmission zeros of the two do not coincide: their
    envelopes are compared); never negative (the model is not let off
    where the full-wave result happens to be better), and capped."""
    a, b = ab
    f = np.asarray(f, float)
    fm = np.clip(a + b * f, f[0], f[-1])
    kind, f0, fbw = band(spec)
    w = (f0 * fbw if kind == 'bandpass' else f0) / 6.0
    n = max(1, int(round(w / (f[1] - f[0]))))

    def envelope(y):
        pad = np.pad(y, n, mode='edge')
        return np.max(np.lib.stride_tricks.sliding_window_view(
            pad, 2 * n + 1), axis=1)

    out = [f]
    for k, cap in ((1, OFFSET_MAX[0]), (0, OFFSET_MAX[1])):
        fw = envelope(db(full[k]))
        mo = envelope(np.interp(fm, f, db(model[k])))
        out.append(np.clip(fw - mo, 0.0, cap))
    return tuple(out)


def fullwave_cost(design, f, s21, s11):
    """The goal's cost of a full-wave response of the design (f Hz, within
    the goal's sweep): what its rounds of tuning are compared by."""
    ff = design.freeform
    goal = Goal(design.spec, rl=ff['goal'].rl, floor=ff['goal'].floor,
                n_stop=N_STOP)
    f = np.asarray(f, float)
    sel = (f >= goal.f.min()) & (f <= goal.f.max())
    S = np.empty((int(sel.sum()), 2, 2), complex)
    S[:, 0, 0] = S[:, 1, 1] = np.asarray(s11)[sel]
    S[:, 0, 1] = S[:, 1, 0] = np.asarray(s21)[sel]
    return float(goal.loss(S, goal.limits(f[sel])))


def retune(design, f, full, model, steps=None, log=None):
    """The design searched on from a full-wave result of it: full = (s21,
    s11) on f (Hz), model = the pixel model's on the same frequencies. The
    model is asked for the limits at the frequencies the full-wave result
    has them (frequency_map), and for as much more as the full-wave
    result's levels lie above its own there (level_offsets). None where
    the full-wave response has no band edges to map."""
    ff, spec, grid = design.freeform, design.spec, design.grid
    ab = frequency_map(spec, f, full[0], model[0])
    if ab is None or not 0.5 < ab[1] < 2.0:
        return None
    a, b = ab
    off = level_offsets(spec, f, full, model, ab)
    if log:
        log(f"  the full-wave response at f is the pixel model's at "
            f"{a / 1e9:+.3f} GHz + {b:.3f} f, up to {off[1].max():.1f} dB "
            f"more of |S11| and {off[2].max():.1f} dB of |S21|: searching "
            "on with the limits there, less those")
    goal = Goal(spec, rl=ff['goal'].rl, floor=ff['goal'].floor,
                n_stop=N_STOP, offset=off)
    steps = steps or max(200, ff['steps'] // 2)
    mask, loss, made = _search(grid, goal, design.mask,
                               lambda x: np.maximum(a + b * np.asarray(x),
                                                    1e7), steps,
                               ff['seconds'], ff['smooth'], ff['symmetry'],
                               log, np.random.default_rng(ff['seed'] + 1))
    d = as_design(spec, grid, mask, _notes(grid, mask, loss))
    d.freeform = dict(ff, goal=goal, loss=loss, fmap=(a, b))
    return d
