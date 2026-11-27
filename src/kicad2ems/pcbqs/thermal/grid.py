"""
The board's temperature on a regular grid (finite volumes): square cells of
`pitch` mm over the outline's bounding box, one unknown per cell on the board
per copper layer.

The copper of each layer is drawn onto a raster finer than the cells (1/16 mm
unless asked otherwise). The conductance between two neighbouring cells comes
from that raster: between their centres, each fine row is a chain in series, the
rows in parallel. A slot narrower than a cell still blocks, a trace narrower
than a cell still carries, and nothing has to follow the outline of the copper,
so the cost does not depend on how intricate the copper is.

    r = solve(problem, pitch=0.5)  # problem.Result: r.T (layers, ny, nx) C,
                                   # r.parts, r.hottest
"""
import math
import time

import numpy as np
from scipy.sparse import coo_matrix, diags
from shapely.geometry.polygon import orient

from .problem import Result, part_temperatures, solve_spd

DETAIL = 0.0625     # mm: the fine raster's cell unless asked otherwise
RASTER = 4e7        # the most fine cells a layer's raster may have
OVER = 4            # bands across each fine cell


def _polys(geom):
    return [
        q for q in getattr(geom, 'geoms', [geom])
        if q.geom_type == 'Polygon' and not q.is_empty
    ]


def raster(geom, x0, y0, h, nx, ny):
    """The fraction of each h x h cell (ny, nx) from (x0, y0) covered by geom
    (shapely, mm), by signed areas: each cell is cut into OVER bands, and each
    part of an outline within a band adds the area to its right (the cell it
    crosses gets the part of its width beyond the crossing). The total is the
    geometry's area exactly, so thin traces and narrow gaps keep their widths
    wherever they fall on the raster."""
    acc = np.zeros(ny * (nx + 2))
    if geom is None or geom.is_empty:
        return np.zeros((ny, nx))
    U0, V0, U1, V1 = [], [], [], []
    for q in _polys(geom):
        q = orient(q, 1.0)  # outside anticlockwise, holes clockwise
        for ring in [q.exterior, *q.interiors]:
            c = np.asarray(ring.coords)[:, :2]
            U0.append((c[:-1, 0] - x0) / h); V0.append((c[:-1, 1] - y0) / h)
            U1.append((c[1:, 0] - x0) / h); V1.append((c[1:, 1] - y0) / h)
    u0, v0, u1, v1 = (np.concatenate(a) for a in (U0, V0, U1, V1))
    keep = v0 != v1
    u0, v0, u1, v1 = u0[keep], v0[keep], u1[keep], v1[keep]
    # copper lies to the right of an edge that runs down
    sign = np.where(v1 < v0, 1.0, -1.0)
    up = v1 > v0
    ua, va, ub, vb = (np.where(up, u0, u1), np.where(up, v0, v1),
                      np.where(up, u1, u0), np.where(up, v1, v0))
    # band k: [k, k + 1) / OVER
    k0 = np.clip(np.floor(va * OVER), 0, ny * OVER).astype(np.int64)
    k1 = np.clip(np.ceil(vb * OVER), 0, ny * OVER).astype(np.int64)
    n = np.maximum(k1 - k0, 0)
    e = np.repeat(np.arange(len(n)), n)
    k = k0[e] + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
    # the edge's part in the band
    lo, hi = np.maximum(va[e], k / OVER), np.minimum(vb[e], (k + 1) / OVER)
    u = ua[e] + ((lo + hi) / 2 - va[e]) * (ub[e] - ua[e]) / (vb[e] - va[e])
    u = np.clip(u, 0.0, nx)
    c = np.floor(u).astype(np.int64)
    f = u - c
    at = (k // OVER) * (nx + 2) + c
    w = sign[e] * (hi - lo)
    acc += (np.bincount(at, w * (1 - f), minlength=len(acc))
            + np.bincount(at + 1, w * f, minlength=len(acc)))
    return np.clip(np.cumsum(acc.reshape(ny, nx + 2), axis=1)[:, :nx], 0.0, 1.0)


def _between(g, sub, axis):
    """The conductance between neighbouring cells along `axis` (1: x, 0: y) from
    the fine sheet conductance g (W/K per square, 0 where there is no board):
    between the two centres each fine row is a chain in series, the rows in
    parallel. Where the board ends inside a cell, short of its centre, a row's
    chain starts at the board's edge; a slot (board again beyond the centre)
    still breaks the chain."""
    if axis == 0:
        return _between(g.T, sub, 1).T
    # centre to centre needs half cells: split each fine cell in two
    if sub % 2:
        g, sub = np.repeat(np.repeat(g, 2, 0), 2, 1), 2 * sub
    half = sub // 2
    rows, nx = g.shape[0], g.shape[1] // sub
    # each fine row of each cell: its left half, its right half
    c = g.reshape(rows, nx, 2, half)
    with np.errstate(divide='ignore'):
        r = 1.0 / c

    # from the centre outwards: r, on (.., half), nearest the centre first
    def out(r, on, other):
        # off the board, before the first of the board
        lead = np.cumsum(on, axis=-1) == 0
        skip = (lead & ~other.any(axis=-1)[..., None]
                & on.any(axis=-1)[..., None])
        return np.where(skip, 0.0, r).sum(axis=-1)
    on = c > 0
    right = out(r[:, :, 1], on[:, :, 1], on[:, :, 0])
    left = out(r[:, :, 0, ::-1], on[:, :, 0, ::-1], on[:, :, 1])
    with np.errstate(divide='ignore'):
        k = 1.0 / (right[:, :-1] + left[:, 1:])  # (fine rows, nx - 1)
    return k.reshape(rows // sub, sub, nx - 1).sum(axis=1)


def cover(geom, x0, y0, pitch, sub, nx, ny):
    """(rows, columns, fractions of geom's area) of the cells geom lies on."""
    bx0, by0, bx1, by1 = geom.bounds
    c0, r0 = (max(int(math.floor((bx0 - x0) / pitch)),
                  0), max(int(math.floor((by0 - y0) / pitch)), 0))
    c1, r1 = (min(int(math.floor((bx1 - x0) / pitch)) + 1,
                  nx), min(int(math.floor((by1 - y0) / pitch)) + 1, ny))
    if c1 <= c0 or r1 <= r0:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    w, h = c1 - c0, r1 - r0
    a = raster(geom, x0 + c0 * pitch, y0 + r0 * pitch, pitch / sub, w * sub,
               h * sub)
    a = a.reshape(h, sub, w, sub).mean(axis=(1, 3))
    if a.sum() <= 0:  # smaller than the raster sees: the cell of its middle
        p = geom.representative_point()
        c = min(max(int((p.x - x0) / pitch), 0), nx - 1)
        r = min(max(int((p.y - y0) / pitch), 0), ny - 1)
        return np.array([r]), np.array([c]), np.array([1.0])
    r, c = np.nonzero(a)
    return r + r0, c + c0, a[r, c] / a.sum()


def edge_lengths(problem, x0, y0, pitch, live, piece):
    """m of the board's edges in each cell (ny, nx): the outline cut into pieces
    of at most `piece` mm, each given to the cell its middle is in (moved a hair
    into the board, so a piece on a cell's side goes to the cell of the board),
    or, if that cell has no board, to the nearest one that does."""
    ny, nx = live.shape
    out = np.zeros((ny, nx))
    a, b = problem.edges()
    if not len(a) or not live.any():
        return out
    d = b - a
    seg = np.hypot(d[:, 0], d[:, 1])
    keep = seg > 0
    a, d, seg = a[keep], d[keep], seg[keep]
    n = np.maximum(np.ceil(seg / piece).astype(np.int64), 1)
    e = np.repeat(np.arange(len(n)), n)
    t = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + 0.5) / n[e]
    left = np.c_[-d[:, 1], d[:, 0]] / seg[:, None]  # towards the board
    m = a[e] + t[:, None] * d[e] + left[e] * (1e-3 * piece)
    c = np.clip(np.floor((m[:, 0] - x0) / pitch).astype(np.int64), 0, nx - 1)
    r = np.clip(np.floor((m[:, 1] - y0) / pitch).astype(np.int64), 0, ny - 1)
    if not live[r, c].all():
        from scipy.ndimage import distance_transform_edt
        _, (rr, cc) = distance_transform_edt(~live, return_indices=True)
        r, c = rr[r, c], cc[r, c]
    np.add.at(out, (r, c), (seg / n)[e] * 1e-3)
    return out


class Grid:
    """A problem's system on the grid, built once (build): its steady
    temperature for any heats on one factorisation (steady), and its transient
    (transient). x, the rise over the ambient: the board's L x ny x nx cells on
    the board (n of them, each layer's in turn) and then the parts' own nodes
    (Problem.extend)."""

    def place(self, spots):
        """The spots' heat ([problem.Spot]) put into the cell each point lies in
        (the nearest on the board, for one off it), in place of any before
        (electrothermal.py: each pass's copper loss, on the one factorisation).
        """
        self.q_spots, self.s_spots = {}, {}
        for sp in spots:
            c = np.clip(np.floor((sp.x - self.x0) / self.pitch).astype(np.int64),
                        0, self.nx - 1)
            r = np.clip(np.floor((sp.y - self.y0) / self.pitch).astype(np.int64),
                        0, self.ny - 1)
            if not self.live[r, c].all():
                if self._near is None:
                    from scipy.ndimage import distance_transform_edt
                    self._near = distance_transform_edt(
                        ~self.live, return_indices=True)[1]
                r, c = self._near[0][r, c], self._near[1][r, c]
            at = self.idx[sp.layer, r, c]
            qv = self.q_spots.setdefault(sp.name, np.zeros(self.m))
            sv = self.s_spots.setdefault(sp.name, np.zeros(self.m))
            np.add.at(qv, at, sp.W)
            if sp.slope is not None:
                np.add.at(sv, at, sp.slope)

    def heat(self, levels=None):
        """The heat into each unknown (W): each part's loss and each spot set's
        times its level ({name: share}; 1 when not given), and the held
        footprints' ties."""
        levels = levels or {}
        q = self.q_held.copy()
        for name, v in self.q_parts.items():
            q += levels.get(name, 1.0) * v
        for name, v in self.q_spots.items():
            q += levels.get(name, 1.0) * v
        return q

    def slope(self, levels=None):
        """W/K at each unknown: the spots' self-heating at their levels, or
        None."""
        levels = levels or {}
        out = None
        for name, v in self.s_spots.items():
            if v.any():
                out = (0.0 if out is None else out) + levels.get(name, 1.0) * v
        return out

    def factor(self, slope=None):
        """A (less the self-heating, slope) factored, kept for the next
        solve."""
        from .problem import factor_spd
        key = None if slope is None else hash(slope.tobytes())
        if self._lu is None or self._lu[0] != key:
            M = self.A if slope is None else self.A - diags(slope)
            self._lu = (key, factor_spd(M))
        return self._lu[1]

    def steady(self, levels=None):
        """x for the heats at their levels (and their self-heating); ValueError
        where the self-heating outruns the films (thermal runaway: no steady
        temperature)."""
        sl = self.slope(levels)
        x = self.factor(sl).solve(self.heat(levels))
        if sl is not None and (not np.all(np.isfinite(x)) or
                               x.min() < -1e-6 * max(abs(x).max(), 1e-30)):
            raise ValueError(
                "thermal runaway: the copper's loss grows with its temperature "
                "faster than the board gives it off (no steady temperature)")
        return x

    def parts_at(self, x, levels=None):
        """part_temperatures' dict for the rise x, each part's drop through its
        theta at its level ({name: share})."""
        p, by, r = self.p, self.by, x[:self.n]
        extra = self.read(x) if self.read else None
        out = part_temperatures(
            p, lambda s: p.ambient + (float(r[by[id(s)][0]] @ by[id(s)][1])
                                      if by[id(s)] is not None else 0.0),
            extra)
        if levels:
            own = set((extra or {}).get('parts') or {})
            for name, d in out.items():
                lev = levels.get(name, 1.0)
                if name not in own and lev != 1.0:
                    d['part_C'] = float(d['board_C'] + lev *
                                        (d['part_C'] - d['board_C']))
        return out

    def maps(self, x):
        """T (layers, ny, nx), C; NaN off the board."""
        T = np.full((self.L, self.ny, self.nx), np.nan)
        for l in range(self.L):
            T[l][self.live] = self.p.ambient + x[self.idx[l][self.live]]
        return T

    def result(self, whole, seconds, levels=None):
        """problem.Result of the rise `whole`."""
        p, rise = self.p, whole[:self.n]
        sl = self.slope(levels)
        # the heat in: the parts' and the spots' at their levels, with the
        # spots' self-heating (the held ties' are not heat in)
        heat_in = float((self.heat(levels) - self.q_held).sum() +
                        (0.0 if sl is None else sl @ whole))
        extra = self.read(whole) if self.read else None
        res = Result('grid', p, self.maps(whole), self.parts_at(whole, levels),
                     self.n, seconds, float(self.film @ rise), self.pitch,
                     x0=self.x0, y0=self.y0, edge_W=float(self.edge @ rise),
                     extra=extra, in_W=heat_in)
        return res

    def transient(self, seconds, profiles=None, start='cold', log=print,
                  steady=None, currents=(), method='auto'):
        """The temperature over `seconds` (transient.run), each heat by its
        profile ({name: Profile}: a part's, a spot set's, or 'all': every part
        with none of its own), from the ambient (start 'cold') or from the
        steady temperature of the profiles' values before t = 0 ('steady').
        Returns dict(times_s, parts={part: [C]}, hottest_C, to_63_s (the heating
        from cold of constant heats: when each part is 63 % of the way to its
        steady temperature), peak={part: dict(C, at_s, final_C)}, tau_s (the
        board's longest time constants), steps, factors, seconds, start,
        profiles, copper_W (the spots' heat with their self-heating, when
        there are spots)). currents: the parts whose profile is a current's
        (their loss by its square: a series resistor's), as a spot set's
        is. method: 'direct' (the grid itself, a factor per step length),
        'reduced' (transient.reduce's model of it, two more factors), or 'auto':
        reduced above transient.DIRECT_MAX unknowns."""
        from . import transient as TR
        profiles = dict(profiles or {})
        heats_known = sorted(self.q_parts) + sorted(self.q_spots)
        unknown = sorted(set(profiles) - set(heats_known) - {'all'})
        if unknown:
            raise ValueError(
                f"a profile for {', '.join(unknown)}: not a heat of this "
                f"problem (one of {', '.join(heats_known)})")
        every = profiles.pop('all', None)
        prof = {name: profiles.get(name, every) or TR.constant()
                for name in self.q_parts}
        prof.update({name: profiles.get(name) or TR.constant()
                     for name in self.q_spots})
        # (name, q, profile, square: a current's, its heat by the square)
        heats = [(name, v, prof[name], name in currents)
                 for name, v in self.q_parts.items()]
        heats += [(name, v, prof[name], True)
                  for name, v in self.q_spots.items()]
        if self.q_held.any():
            heats.append(('held', self.q_held, TR.constant(), False))
        spots = [k for k, v in self.s_spots.items() if v.any()]
        if len(spots) > 1:
            raise ValueError("the transient takes one self-heating spot set")
        slope = self.s_spots[spots[0]] if spots else None

        def levels(t):
            out = {}
            for name, _, pr, sq in heats:
                lev = float(pr(t))
                out[name] = lev * lev if sq else lev
            return out
        before = {name: (pr.before ** 2 if sq else pr.before)
                  for name, _, pr, sq in heats}
        x0 = None
        if start == 'steady':
            x0 = self.steady(before)
        elif start != 'cold':
            raise ValueError(f"start: cold or steady, not {start!r}")
        n = self.n

        def read(x, t):
            lev = before if t <= 0 else levels(t)
            out = {name: d['part_C']
                   for name, d in self.parts_at(x, lev).items()}
            out['hottest'] = float(self.p.ambient + x[:n].max())
            if self.q_spots:
                out['copper_W'] = float(sum(
                    lev[k] * (v.sum() + self.s_spots[k] @ x)
                    for k, v in self.q_spots.items()))
            return out
        m = self.A.shape[0]
        if method == 'auto':
            method = 'reduced' if m > TR.DIRECT_MAX else 'direct'
        if method not in ('direct', 'reduced'):
            raise ValueError(f"method: direct, reduced or auto, not {method!r}")
        red = None
        if method == 'reduced':
            # the shifts: the steady state's, and those of times a fraction of
            # the shortest stretch between the profiles' breaks
            edges = np.array(TR._intervals([h[2] for h in heats],
                                           float(seconds)))
            short = float(np.diff(edges).min())
            red = TR.reduce(self.A, self.cap, heats, x0, slope, self.factor(),
                            [k / short for k in TR.SHIFTS])
            log(f"thermal transient: a reduced model of {red['size']} of the "
                f"grid's {m:,} unknowns")
            T = red['T']
            out = TR.run(red['A'], red['C'], red['heats'], float(seconds),
                         read, start=red['start'], slope=red['slope'],
                         slope_heat=spots[0] if spots else None, log=log,
                         lift=lambda z: T @ z)
        else:
            out = TR.run(self.A, self.cap, heats, float(seconds), read,
                         start=x0, slope=slope,
                         slope_heat=spots[0] if spots else None, log=log)
        out['method'] = method
        vals = out.pop('values')
        tr = dict(out, start=start,
                  parts={k: vals[k] for k in self.q_parts if k in vals},
                  hottest_C=vals['hottest'],
                  profiles={k: p.describe() for k, p in prof.items()
                            if p.text != 'constant'})
        if 'copper_W' in vals:
            tr['copper_W'] = vals['copper_W']
        t = np.asarray(tr['times_s'])
        tr['peak'] = {
            k: dict(C=float(max(c)), at_s=float(t[int(np.argmax(c))]),
                    final_C=float(c[-1]))
            for k, c in tr['parts'].items()
        }
        tr['to_63_s'] = {}
        flat = all(pr.text == 'constant' for _, _, pr, _ in heats)
        if flat and start == 'cold':
            steady = steady or self.parts_at(self.steady())
            for name, c in tr['parts'].items():
                if name not in steady or steady[name]['part_C'] - c[0] < 0.05:
                    continue            # (held, or no rise to time)
                target = c[0] + 0.632 * (steady[name]['part_C'] - c[0])
                hit = next((i for i, v in enumerate(c) if v >= target), None)
                if hit:
                    a, b = c[hit - 1], c[hit]
                    tr['to_63_s'][name] = float(
                        t[hit - 1] + (t[hit] - t[hit - 1]) * (target - a) /
                        max(b - a, 1e-12))
        last = levels(float(seconds)).get(spots[0], 0.0) if spots else 0.0
        if red is None:
            tr['tau_s'] = TR.time_constants(
                self.A, self.cap, slope=None if slope is None else last * slope)
        else:  # the model's slowest modes (it matches the steady state's
            #     moments: its slowest are the grid's)
            M = red['A'] - (last * red['slope'] if red['slope'] is not None
                            and last else 0.0 * red['A'])
            lam = np.linalg.eigvalsh(M.toarray())
            tr['tau_s'] = sorted((float(1 / v) for v in lam if v > 0),
                                 reverse=True)[:4]
            tr['reduced'] = red['size']
        return tr


def build(problem, pitch=0.5, sub=None, detail=DETAIL):
    """The problem's Grid (pitch: the cells, mm; sub: fine raster cells across
    one, default enough for `detail` mm, the narrowest gap or trace the
    conductances still see; fewer on a board too large for that)."""
    p = problem
    t0 = time.perf_counter()
    if pitch <= 0 or (sub is not None and sub < 1) or detail <= 0:
        raise ValueError('pitch and detail must be positive and sub at least 1')
    x0, y0, x1, y1 = p.outline.bounds
    nx, ny = (max(int(math.ceil((x1 - x0) / pitch - 1e-9)),
                  1), max(int(math.ceil((y1 - y0) / pitch - 1e-9)), 1))
    if sub is None:
        sub = max(2, 2 * int(math.ceil(pitch / detail / 2 - 1e-9)))
        while sub > 2 and nx * ny * sub * sub > RASTER:
            sub -= 2
    L = len(p.layers)
    h = pitch / sub
    board = raster(p.outline, x0, y0, h, nx * sub, ny * sub)
    # m2 of board per cell
    area = board.reshape(ny, sub, nx, sub).mean(axis=(1, 3)) * pitch ** 2 * 1e-6
    live = area > 0
    idx = np.full((L, ny, nx), -1)
    n1 = int(live.sum())
    for l in range(L):
        idx[l][live] = np.arange(n1) + l * n1
    n = L * n1
    rows, cols, vals = [], [], []

    def link(a, b, g):
        rows.append(np.r_[a, b, a, b])
        cols.append(np.r_[a, b, b, a])
        vals.append(np.r_[g, g, -g, -g])
    diag = np.zeros(n)
    # m2 of copper in each cell (its heat capacity)
    cu_area = np.zeros((L, ny, nx))
    for l, lay in enumerate(p.layers):
        g = lay.laminate * board
        if lay.copper is not None:
            rc = raster(lay.copper, x0, y0, h, nx * sub, ny * sub) * board
            g = g + p.k_cu * lay.t * 1e-3 * rc
            cu_area[l] = (rc.reshape(ny, sub, nx, sub).mean(axis=(1, 3))
                          * pitch**2 * 1e-6)
        gx, gy = _between(g, sub, 1), _between(g, sub, 0)
        a, b = idx[l][:, :-1], idx[l][:, 1:]
        m = (a >= 0) & (b >= 0) & (gx > 0)
        link(a[m], b[m], gx[m])
        a, b = idx[l][:-1], idx[l][1:]
        m = (a >= 0) & (b >= 0) & (gy > 0)
        link(a[m], b[m], gy[m])
    for l, gap in enumerate(p.gaps):
        link(idx[l][live], idx[l + 1][live], gap * area[live])
    for v in p.vias:
        c, r = int((v['x'] - x0) / pitch), int((v['y'] - y0) / pitch)
        if not (0 <= c < nx and 0 <= r < ny and live[r, c]):
            continue
        for l in range(v['lo'], v['hi']):
            link(np.array([idx[l, r, c]]), np.array([idx[l + 1, r, c]]),
                 np.array([p.barrel(v, l)]))
    film = np.zeros(n)
    film[idx[0][live]] += p.film[0] * area[live]
    film[idx[-1][live]] += p.film[1] * area[live]
    edge = np.zeros(n)
    if p.edge_film > 0:
        run = edge_lengths(p, x0, y0, pitch, live, h)[live]
        for l in range(L):
            edge[idx[l][live]] += p.edge_conductance(l) * run
    film += edge
    diag += film
    q = np.zeros(n)
    covers = []
    for s in p.sources:
        r, c, f = cover(s.geom, x0, y0, pitch, sub, nx, ny)
        ok = live[r, c]
        if not ok.any():
            covers.append(None)
            continue
        r, c, f = r[ok], c[ok], f[ok] / f[ok].sum()
        np.add.at(q, idx[s.layer, r, c], s.watts * f)
        covers.append((idx[s.layer, r, c], f))
    A = coo_matrix((np.concatenate(vals + [diag]),
                    (np.concatenate(rows + [np.arange(n)]),
                     np.concatenate(cols + [np.arange(n)]))),
                   shape=(n, n)).tocsc()
    by = {id(s): cv for s, cv in zip(p.sources, covers)}
    read = None
    m = n
    if p.air or p.held:  # the parts' own paths to the air, held pads

        def spot(layer, geom):
            r, c, f = cover(geom, x0, y0, pitch, sub, nx, ny)
            ok = live[r, c]
            return ((idx[layer, r[ok],
                         c[ok]], f[ok] / f[ok].sum()) if ok.any() else
                    (np.zeros(0, int), np.zeros(0)))
        A, q, read = p.extend(A, q, by, spot)
        m = A.shape[0]
    # each part's heat on its own (a profile scales it): on its pads, or on its
    # own node (Problem.extend: a part with its path to the air)
    own = [name for name in p.air if name in p.powers]
    q_parts = {}
    for s in p.sources:
        v = q_parts.setdefault(s.name, np.zeros(m))
        if s.name in own:
            v[n + own.index(s.name)] += s.watts
        elif by[id(s)] is not None:
            np.add.at(v, by[id(s)][0], s.watts * by[id(s)][1])
    # what is left of q: the held footprints' ties
    q_held = q - sum(q_parts.values(), np.zeros(m))
    q_held[np.abs(q_held) <= 1e-12 * max(float(abs(q).max()), 1e-30)] = 0.0
    # J/K: the laminate's share and the copper
    from .problem import RHO_C_CU, RHO_C_FR4
    cap = np.zeros(m)
    for l, lay in enumerate(p.layers):
        # an inner layer's share of the laminate (half the distance to each
        # neighbour) takes in its own thickness: where it is copper, that is
        # copper's, not the laminate's (it had been both, 4.8 % too much heat
        # capacity on wpthb against Elmer's 3D mesh; R-E21)
        inner = 0 < l < len(p.layers) - 1
        cap[idx[l][live]] = (area[live] * lay.edge * 1e-3 * RHO_C_FR4 +
                             cu_area[l][live] * lay.t * 1e-3 *
                             (RHO_C_CU - (RHO_C_FR4 if inner else 0.0)))
    g = Grid()
    g.p, g.pitch, g.sub, g.L = p, pitch, sub, L
    g.x0, g.y0, g.nx, g.ny = x0, y0, nx, ny
    g.idx, g.live, g.n, g.A = idx, live, n, A.tocsc()
    g.q_parts, g.q_held, g.m, g._near = q_parts, q_held, m, None
    g.film, g.edge, g.by, g.read = film, edge, by, read
    g.area, g.cu_area, g.cap, g._lu = area, cu_area, cap, None
    g.place(p.spots)
    g.seconds = dict(build=time.perf_counter() - t0)
    return g


def solve(problem, pitch=0.5, sub=None, detail=DETAIL, log=print,
          transient=None, profiles=None, start='cold'):
    """pitch: the cells, mm. sub: fine raster cells across one cell (default:
    enough for `detail` mm, the narrowest gap or trace the conductances still
    see; fewer on a board too large for that). transient: seconds, also the
    temperature over that long (Grid.transient: from cold, or with
    start='steady' from the steady temperature before t = 0; each heat by its
    profile, {name: transient.Profile}; the board's own heat capacity, the
    parts' bodies' left out)."""
    g = build(problem, pitch, sub, detail)
    t1 = time.perf_counter()
    whole = g.steady()
    t2 = time.perf_counter()
    res = g.result(whole, dict(build=g.seconds['build'], solve=t2 - t1))
    res.grid = g
    if transient:
        res.transient = g.transient(float(transient), profiles, start, log,
                                    steady=res.parts)
    log(f"thermal grid: {g.nx} x {g.ny} cells of {pitch:g} mm on {g.L} "
        f"layer(s), {g.n:,} unknowns, {g.seconds['build'] + t2 - t1:.2f} s; "
        f"hottest {res.hottest:.1f} C")
    return res
