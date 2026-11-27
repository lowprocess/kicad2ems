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
    design = retune(design, f, s21)     # from a full-wave result of it

The pixel model is coarse (two triangles a pixel), so a full-wave run of the
board differs from it by a few percent of frequency and more in bandwidth:
retune maps the full-wave response's band edges onto the model's and searches
on, the model asked for the spec where the full-wave result will have it
(dfsynth's --em-tune).
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


def _excess(goal, S, lim):
    """dB over the limits at each frequency of S (lim: goal.limits there)."""
    pas, stop, att = lim
    s11 = np.maximum(db(S[:, 0, 0]), db(S[:, 1, 1]))
    s21 = db(S[:, 1, 0])
    return np.where(pas, np.maximum(np.maximum(s11 + goal.rl, 0),
                                    np.maximum(-goal.il - s21, 0)),
                    np.where(stop, np.maximum(s21 + att, 0), 0.0))


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


def retune(design, f, s21_full, s21_model, steps=None, log=None):
    """The design searched on from a full-wave result of it (f Hz, its S21,
    and the pixel model's on the same frequencies): the model is asked for
    the limits at the frequencies the full-wave result has them
    (frequency_map). None where the full-wave response has no band edges
    to map."""
    ff, spec, grid = design.freeform, design.spec, design.grid
    ab = frequency_map(spec, f, s21_full, s21_model)
    if ab is None or not 0.5 < ab[1] < 2.0:
        return None
    a, b = ab
    if log:
        log(f"  the full-wave response at f is the pixel model's at "
            f"{a / 1e9:+.3f} GHz + {b:.3f} f: searching on with the limits "
            "there")
    goal = Goal(spec, rl=ff['goal'].rl, floor=ff['goal'].floor,
                n_stop=N_STOP)
    steps = steps or max(200, ff['steps'] // 2)
    mask, loss, made = _search(grid, goal, design.mask,
                               lambda x: np.maximum(a + b * np.asarray(x),
                                                    1e7), steps,
                               ff['seconds'], ff['smooth'], ff['symmetry'],
                               log, np.random.default_rng(ff['seed'] + 1))
    d = as_design(spec, grid, mask, _notes(grid, mask, loss))
    d.freeform = dict(ff, goal=goal, loss=loss, fmap=(a, b))
    return d
