"""
Edge walls (--edge-walls): metal on the board edges where ground copper reaches them, standing in
for a row of stitching vias or edge plating (openEMS: PEC sheets; mom25d: rows of vias). Without them
the plane pairs of a test coupon are open at the board edge and can resonate across the board (a half
wave across a 14 mm FR-4 board is ~5 GHz).

A wall runs parallel to a side of the outline's bounding box, where the ground planes end near it
(planes are usually pulled back from the board edge; up to `reach` mm in from the outline). At each
point along it (sampled every `step` mm) it spans from the lowest to the highest copper layer that
has ground copper (any modelled net that is not a signal net) at the edge, and is left out where fewer
than two layers have ground there or where signal copper on any layer comes within `keepout` mm (a
launch pad or a trace running off the edge). Sides are named in KiCad coordinates:

    xmin = left, xmax = right, ymin = top, ymax = bottom (as drawn in KiCad, y down)

    walls = edge_walls(board, ['xmax'], keepout=0.5)   # [(axis, coord, t0, t1, z0, z1)], model mm
"""
import numpy as np
from shapely.geometry import box, LineString

SIDES = ('xmin', 'xmax', 'ymin', 'ymax')


def parse_sides(v):
    """'all' or a list / comma string of xmin, xmax, ymin, ymax -> list of sides (no duplicates)."""
    if v in (None, '', [], ()):
        return []
    items = [s.strip().lower() for s in (v.split(',') if isinstance(v, str) else v) if str(s).strip()]
    if items == ['all']:
        return list(SIDES)
    bad = [s for s in items if s not in SIDES]
    if bad:
        raise ValueError(f"edge walls: unknown side {', '.join(bad)} - use xmin, xmax, ymin, ymax or all")
    return [s for s in SIDES if s in items]


def _signal_nets(board):
    nets = {p.net for p in board.ports if p.net}
    if board.sel:
        nets |= set(board.sel) - board.ground_nets
    return nets


def edge_walls(model, sides, keepout=0.5, step=0.1, depth=0.3, reach=2.0):
    """Wall pieces [(axis, coord, t0, t1, z0, z1)] in model coordinates (mm): a sheet normal to `axis`
    ('x' or 'y') at `coord`, from t0 to t1 along the edge and z0 to z1 in height. The sheet stands
    where the planes end (up to `reach` mm in from the outline), not on the outline itself."""
    from shapely.ops import unary_union
    from shapely.prepared import prep
    if not sides:
        return []
    signal = _signal_nets(model)
    sel = model.sel
    ground, sig = {}, []
    for l, n, g in model.prims.items:
        if l not in model.cu_z or (sel is not None and n not in sel):
            continue
        if n in signal:
            sig.append(g)
        else:
            ground.setdefault(l, []).append(g)
    ground = {l: unary_union(gs).intersection(model.outline) for l, gs in ground.items()}
    if model.ground:                                       # a --ground layer is a plane everywhere
        ground[model.ground] = model.outline
    sig = prep(unary_union(sig)) if sig else None
    x0, y0, x1, y1 = model.outline.bounds                  # model coordinates: y = -KiCad y
    edges = dict(xmin=('x', x0, (y0, y1), +1), xmax=('x', x1, (y0, y1), -1),
                 ymin=('y', y1, (x0, x1), -1), ymax=('y', y0, (x0, x1), +1))   # KiCad top = model y max
    out = []
    for side in sides:
        axis, c, (a, b), inward = edges[side]
        line = LineString([(c, a), (c, b)] if axis == 'x' else [(a, c), (b, c)])
        # the planes are usually pulled back from the board edge: the wall stands where the ground of the
        # layers it joins has begun on all of them (the largest setback within `reach`)
        setback = [g.distance(line) for g in ground.values() if not g.is_empty and g.distance(line) <= reach]
        if len(setback) < 2:
            continue
        cw = c + inward * max(setback)                     # on the innermost plane edge (a mesh line)
        prepared = {l: prep(g) for l, g in ground.items() if not g.is_empty}
        n = max(2, int(np.ceil((b - a) / step)))
        h = (b - a) / n
        ts = a + (np.arange(n) + 0.5) * h
        spans = []
        for t in ts:
            lo, hi = sorted((cw, cw + inward * depth))
            probe = box(lo, t - h / 2, hi, t + h / 2) if axis == 'x' else box(t - h / 2, lo, t + h / 2, hi)
            if not model.outline.contains(probe.centroid) or (sig is not None and sig.intersects(probe.buffer(keepout))):
                spans.append(None)
                continue
            zs = [model.cu_z[l] for l, g in prepared.items() if g.intersects(probe)]
            spans.append((min(zs), max(zs)) if len(zs) >= 2 else None)
        k = 0
        while k < n:                                       # merge equal neighbours into pieces
            if spans[k] is None:
                k += 1
                continue
            j = k
            while j + 1 < n and spans[j + 1] == spans[k]:
                j += 1
            out.append((axis, float(cw), float(ts[k] - h / 2), float(ts[j] + h / 2), float(spans[k][0]), float(spans[k][1])))
            k = j + 1
    return out


def _pt(p):
    from shapely.geometry import Point
    return Point(*p)


def describe(walls, model):
    """One log line: per side, pieces and total length."""
    if not walls:
        return "edge walls: none placed (no edge with ground copper on two layers clear of signal copper)"
    x0, y0, x1, y1 = model.outline.bounds
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    name = lambda w: ('xmin' if w[1] < xc else 'xmax') if w[0] == 'x' else ('ymin' if w[1] > yc else 'ymax')
    by = {}
    for w in walls:
        by.setdefault(name(w), []).append(w)
    return "edge walls: " + ", ".join(f"{s} {len(ws)} piece{'s' * (len(ws) > 1)} {sum(w[3] - w[2] for w in ws):.1f} mm"
                                            for s, ws in by.items())


def snap(walls, grid):
    """The walls moved onto the nearest mesh line across them (a zero-thickness sheet off the grid is
    dropped by openEMS)."""
    out = []
    for axis, c, t0, t1, z0, z1 in walls:
        lines = np.asarray(grid['x' if axis == 'x' else 'y'], float)
        out.append((axis, float(lines[np.argmin(np.abs(lines - c))]), t0, t1, z0, z1))
    return out
