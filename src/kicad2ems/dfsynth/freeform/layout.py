"""
A pixel pattern as a dfsynth Design: its copper as rectangles on the board
dfsynth writes (kicad.write: the KiCad board, the kicad2ems configuration, the
footprint), its model the pixel grid's own (the moment-method solution on the
pixels' mesh, swept adaptively), so dfsynth's validation and its plots take it
as any other topology's.
"""
import numpy as np

from ..topologies import Design, Geometry, _feed, _margin


def rectangles(mask):
    """The pattern as rectangles of pixels [(i0, j0, i1, j1)] (i1, j1
    exclusive), none overlapping: each row's runs, those of the same columns
    in rows that follow each other joined."""
    mask = np.asarray(mask, bool)
    nx, ny = mask.shape
    done, open_ = [], {}                # (i0, i1) -> the row its run began
    for j in range(ny + 1):
        runs, i = set(), 0
        while j < ny and i < nx:
            if mask[i, j]:
                i0 = i
                while i < nx and mask[i, j]:
                    i += 1
                runs.add((i0, i))
            else:
                i += 1
        for r in [r for r in open_ if r not in runs]:
            done.append((r[0], open_.pop(r), r[1], j))
        for r in runs:
            open_.setdefault(r, j)
    return sorted(done)


def geometry(grid, mask, spec):
    """The board: the box between two feeds of dfsynth's length, dfsynth's
    margin above and below, the ports 0.5 mm inside its left and right
    edges."""
    sub, p = grid.sub, grid.pitch
    feed, margin = _feed(spec, sub), _margin(sub)
    geo = Geometry()
    for i0, j0, i1, j1 in rectangles(mask):
        geo.rect(feed + i0 * p, j0 * p, feed + i1 * p, j1 * p)
    w = grid.feed_w * p
    ys = [(r + grid.feed_w / 2) * p for r in grid.rows]
    geo.hline(0.0, feed, ys[0], w)
    x1 = feed + grid.nx * p
    geo.hline(x1, x1 + feed, ys[1], w)
    # (the box's rows whole, its empty ones too: the size asked for)
    y0, y1 = -margin, grid.ny * p + margin
    geo.polys = [[(x, y - y0) for x, y in poly] for poly in geo.polys]
    geo.W, geo.H = x1 + feed, y1 - y0
    geo.ports = [(1, 0.5, ys[0] - y0, 0), (2, geo.W - 0.5, ys[1] - y0, 180)]
    return geo


def response(grid, mask, f, tol=2e-3, max_points=60, log=None):
    """S (len(f), 2, 2) of a pattern on the frequencies f (Hz): the pixel
    model solved at the frequencies an adaptive sweep asks for, a rational
    fit between them (emsdev.afs)."""
    from kicad2ems.emsdev.afs import adaptive_sweep
    f = np.asarray(f, float)
    solve = lambda x: grid.sparams(mask, [x])[0]
    out = adaptive_sweep(solve, float(f.min()), float(f.max()), tol=tol,
                         max_points=max_points, grid=f,
                         log=log or (lambda *a, **k: None))
    return out[1]


class _Model:
    """dfsynth's `network` of a free-form design: one element, the ABCD
    matrix of the pixel model's S."""

    def __init__(self, grid, mask, z0):
        self.grid, self.mask, self.z0 = grid, mask, z0
        self.kept = {}

    def s(self, f):
        # (dfsynth asks for the same sweep more than once: with and without
        # the copper's thickness, which this model does not have)
        f = np.asarray(f, float)
        key = (len(f), float(f[0]), float(f[-1]), float(f.sum()))
        if key not in self.kept:
            self.kept[key] = response(self.grid, self.mask, f)
        return self.kept[key]

    def __call__(self, f):
        S = self.s(f)
        s11, s12, s21, s22 = S[:, 0, 0], S[:, 0, 1], S[:, 1, 0], S[:, 1, 1]
        z = self.z0
        return np.stack([
            np.stack([((1 + s11) * (1 - s22) + s12 * s21) / (2 * s21),
                      z * ((1 + s11) * (1 + s22) - s12 * s21) / (2 * s21)],
                     -1),
            np.stack([((1 - s11) * (1 - s22) - s12 * s21) / (2 * s21) / z,
                      ((1 - s11) * (1 + s22) + s12 * s21) / (2 * s21)], -1)
        ], -2)


def design(spec, grid, mask, notes=()):
    """The dfsynth Design of a pattern (its model: the pixel grid's, whatever
    substrate is asked of it)."""
    mask = np.array(mask, bool)
    w, h = grid.size()
    rows = [('box', '', w, h, None),
            ('pixel', '', grid.pitch, grid.pitch, None),
            ('feed', 50.0, grid.feed_w * grid.pitch, _feed(spec, grid.sub),
             None)]
    model = [_Model(grid, mask, spec.z0)]
    d = Design(spec, grid.sub, 1.0, 1.0, geometry(grid, mask, spec), None,
               lambda sub: model, rows, [], list(notes))
    d.grid, d.mask = grid, mask
    return d
