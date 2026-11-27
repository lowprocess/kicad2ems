"""
Patterns a free-form search starts from.

A search by single pixels refines a shape; it does not find how many
resonators a band-pass needs, or that a band-stop wants stubs. The start says
that much: a line between the feeds (low-pass, band-stop: the search hangs
its own stubs and patches on it), or for a band-pass the spec's `order` open
half-wave loops side by side, the feeds tapped onto the outer two. Their
lengths come from dfsynth's line formulas; everything else is the search's.
"""
import numpy as np

from ..microstrip import C0


def through(grid):
    """A line of the feeds' width between them (the feeds on one row)."""
    if grid.rows[0] != grid.rows[1]:
        raise ValueError("a line between the feeds: on one row")
    m = np.zeros(grid.shape, bool)
    m[:, grid.rows[0]:grid.rows[0] + grid.feed_w] = True
    return m


def loops_size(spec, sub, p, line=2, gap=1, inner=2):
    """(the pixels of width the loops need, an arm's length in pixels) at a
    pixel of p mm."""
    n = spec.order
    need = n * (2 * line + inner) + (n - 1) * gap
    lam2 = C0 / (2 * spec.f0 * np.sqrt(sub.eeff(line * p, spec.f0))) * 1e3
    # U: two arms and the bar between their centre lines
    return need, max(line + 2, int(round((lam2 / p - (line + inner)) / 2)))


def _arm(arm, ny):
    """An arm's length in a box of ny rows: no longer than the box, and
    with as many rows above it as below (the loops upside down are then
    the others' images under a half turn)."""
    arm = min(arm, ny)
    return arm - (ny - arm) % 2


def loops_rows(spec, sub, p, ny, feed_w, line=2):
    """The feeds' first rows for loops(): one pixel in from the first loop's
    bar (the tap), the last loop's the same from its own bar (a loop upside
    down: from the box's other side)."""
    arm = _arm(loops_size(spec, sub, p, line)[1], ny)
    y0 = (ny - arm) // 2
    r = min(max(y0 + 1, 0), ny - feed_w)
    return (r, r) if spec.order % 2 else (r, ny - feed_w - r)


def loops(grid, spec, line=2, gap=1, inner=2):
    """`spec.order` U-shaped half-wave resonators across the box, alternate
    ones upside down (a hairpin filter's arrangement), `gap` pixels apart,
    their arms `line` pixels wide and `inner` apart; the feeds run straight
    onto the outer arms (a tapped feed). The arms are as long as the half
    wave needs, by dfsynth's eeff of a line that wide, or as the box allows.
    Raises ValueError where the box is too narrow for them."""
    nx, ny = grid.shape
    n, p = spec.order, grid.pitch
    cell = 2 * line + inner
    need, arm = loops_size(spec, grid.sub, p, line, gap, inner)
    if need > nx:
        raise ValueError(
            f"{n} resonators of {line}-pixel lines need {need} pixels of "
            f"width ({need * p:.2f} mm), the box has {nx}")
    arm = _arm(arm, ny)
    m = np.zeros(grid.shape, bool)
    x0 = (nx - need) // 2
    y0 = (ny - arm) // 2
    xs = []
    for k in range(n):
        x = x0 + k * (cell + gap)
        xs.append(x)
        m[x:x + line, y0:y0 + arm] = True
        m[x + cell - line:x + cell, y0:y0 + arm] = True
        yb = y0 if k % 2 == 0 else y0 + arm - line
        m[x:x + cell, yb:yb + line] = True
    # the feeds, onto the outer arms
    r0, r1 = grid.rows
    m[:xs[0] + line, r0:r0 + grid.feed_w] = True
    m[xs[-1] + cell - line:, r1:r1 + grid.feed_w] = True
    return m
