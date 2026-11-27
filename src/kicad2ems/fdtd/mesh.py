"""
Automatic rectilinear mesh for openEMS.

  x / y : thirds rule at Manhattan copper edges (1/3 inside, 2/3 outside the
          metal), end points of oblique edges, via centre +- radius, port
          planes and the board outline kept exactly; lines closer than fine/2
          merged by priority; graded smoothing (<= ratio growth) up to
          lambda/res in the dielectric and in air
  z     : every interface, >= nz cells per dielectric, copper sheets exactly
          on lines, air margin (none below a PEC bottom plane), PML cells added
          only on PML sides; with --thickness on, both faces of each copper
          layer, the stack moved up to make room (thick.py)
"""
import numpy as np
from kicad2ems.core.constants import C0
from .boundaries import SIDES, bottom_is_plane


def _merge(lines, tol, keep=(), prio=None):
    """Cluster lines closer than tol and keep ONE line per cluster: lines in
    `keep` (exact), else the member with the highest priority (ties: the first).
    Clusters do not chain (every member is within tol of the cluster's first
    line) and lines are never averaged, so the thirds-rule line just inside a
    narrow trace survives instead of drifting outside it."""
    lines = np.asarray(lines, float)
    prio = np.zeros(len(lines)) if prio is None else np.asarray(prio, float)
    if len(keep):
        lines = np.concatenate([lines, keep])
        prio = np.concatenate([prio, np.full(len(keep), 1e9)])
    o = np.argsort(lines, kind='stable')
    lines, prio = lines[o], prio[o]
    out, op, cl = [], [], [0]
    for i in range(1, len(lines) + 1):
        if i < len(lines) and lines[i] - lines[cl[0]] <= tol:
            cl.append(i); continue
        c = np.array(cl)
        j = c[np.argmax(prio[c])]
        x, pr = lines[j], prio[j]
        # the winners of two neighbouring clusters can still be closer than tol:
        # keep the stronger one
        while out and x - out[-1] <= tol:
            if pr > op[-1]:
                out.pop(); op.pop()
            else:
                break
        if not out or x - out[-1] > tol:
            out.append(x); op.append(pr)
        cl = [i]
    return np.array(out)


def _middle(left, right, ea, eb, m, ratio):
    """_graded's middle [left[-1], right[-1]]: (its number of even cells,
    whether the jump to a graded cell beside it is still over ratio). The
    count from the smaller graded cell beside it, or one more or fewer where
    that keeps both sides nearer (one fewer only where its cells stay within
    m and the size of a fixed line they touch)."""
    lo, hi = left[-1], right[-1]
    hl = left[-1] - left[-2] if len(left) > 1 else ea / ratio
    gr = right[-2] - right[-1] if len(right) > 1 else eb / ratio
    k = max(1, int(np.ceil((hi - lo) / min(m, ratio * min(hl, gr)) - 1e-9)))
    # the graded cells beside it (a side with none: the cell beyond its fixed
    # line, which smooth_lines checks)
    near = [c for c, n in ((hl, len(left)), (gr, len(right))) if n > 1]

    def worst(n):
        c = (hi - lo) / n
        return max([1.0] + [max(c / h, h / c) for h in near])
    top = min([m] + [e for e, n in ((ea, len(left)), (eb, len(right)))
                     if n == 1])
    k = min((n for n in (k, k + 1, k - 1)
             if n >= 1 and (hi - lo) / n <= top * 1.0001), key=worst)
    return k, worst(k) > ratio * 1.0001


def _graded(a, b, ea, eb, m, ratio):
    """The lines inside [a, b]: cells from ea at a and eb at b, each the last
    times ratio towards the middle, at most m, the middle split evenly (no
    sliver)."""
    L = b - a
    if L <= min(ea, eb, m) * 1.0001:
        return []
    left, right = [a], [b]  # graded from both ends towards the middle
    h, g = ea / ratio, eb / ratio  # (the first cells: ea, eb)
    while True:
        hn, gn = min(h * ratio, m), min(g * ratio, m)
        if right[-1] - left[-1] <= hn + gn:
            break
        if h <= g:
            left.append(left[-1] + hn); h = hn
        else:
            right.append(right[-1] - gn); g = gn
    if len(left) > 1:  # give the last graded cell of each side back
        left.pop()
    if len(right) > 1:
        right.pop()
    for back in range(5):  # ... and split [lo, hi] evenly
        k, bad = _middle(left, right, ea, eb, m, ratio)
        if not bad or back == 4:
            break
        # no split fits beside them (rf_bends: 0.204 mm, two cells over the
        # size beside a fixed line, three 1.46x under the 0.099 graded ones):
        # another graded cell back, the larger side's
        sides = [s for s in (left, right) if len(s) > 1]
        if not sides:
            break
        big = max(sides, key=lambda s: abs(s[-1] - s[-2]))
        big.pop()
    lo, hi = left[-1], right[-1]
    return left[1:] + list(np.linspace(lo, hi, k + 1)[1:-1]) + right[1:][::-1]


def smooth_lines(fixed, maxres, ratio=1.4, fine=None, whole=(),
                 rounds=30, dead=()):
    """Insert lines between fixed lines so that every cell is <= maxres(x) and
    neighbouring cells differ by at most `ratio`, across the fixed lines too.
    maxres: float or callable(x) (taken at each fixed interval's middle).

    Each fixed line has the size its cells may have (e): ratio times the fixed
    gap beside it (a cell that size beside one gap wide), with fine 3 fine
    ratio (a feature's cells as before), carried along the axis in both
    directions growing (ratio - 1) times the distance (as cells growing by
    ratio do), so a fixed gap split into small cells is seen beyond its
    neighbours too. whole: points (a copper edge's x or y) whose fixed
    interval is not split (the thirds-rule pair round the edge: a line inside
    it moves the edge), unless past maxres or wider than fine; a jump beside
    one is left. Each interval is graded from its
    two ends' sizes; where the two cells at a fixed line still differ by more
    than ratio (an interval split into slivers), the line's size is lowered to
    ratio times the smaller and the intervals graded again. Each interval had
    been graded from its neighbouring fixed gaps alone: a gap split into
    0.031 mm cells beside one started at 0.21 mm (6.8x), and 8.9x on rf_ratrace's
    mesh (the review, F42). dead: [(a, b)] spans of metal across the whole
    domain (dead_spans): a fixed interval inside one is not split and sets
    nothing beside it (no field there to grade)."""
    M = maxres if callable(maxres) else (lambda x, m=maxres: m)
    fx = np.unique(np.asarray(fixed, float))
    if len(fx) < 2:
        return fx
    g = np.diff(fx)
    m = np.array([M(0.5 * (a + b)) for a, b in zip(fx[:-1], fx[1:])])
    gone = np.zeros(len(g), bool)
    for a, b in dead:
        gone |= (fx[:-1] >= a - 1e-9) & (fx[1:] <= b + 1e-9)
    cap = np.where(gone, np.inf, ratio * np.minimum(g, m))
    e = np.full(len(fx), np.inf)
    e[:-1] = np.minimum(e[:-1], cap)
    e[1:] = np.minimum(e[1:], cap)
    if fine:
        e = np.minimum(e, 3 * fine * ratio)
    # the cell round a copper edge (its thirds-rule pair) whole: a line inside
    # it moves the edge. A cluster of fixed lines can be past ratio among
    # themselves (demo_transition: 0.042 mm between 0.025 and 0.033, 1.6x whole
    # or halved); mending each jump had halved them in turn through the
    # cluster, into the pair round its stripline's edge (a line 0.004 mm off
    # it: S21 8.6 deg late at 8 GHz)
    feat = np.zeros(len(g), bool)
    if len(whole):
        w = np.asarray(whole, float)
        i = np.searchsorted(fx, w) - 1
        ok = ((i >= 0) & (i < len(g)))
        i, w = i[ok], w[ok]
        inside = (w > fx[i] + 1e-9) & (w < fx[i + 1] - 1e-9)
        feat[i[inside]] = True
        # (a pair spans 2/3 fine: a wider cell round edges is lines merged
        # away, sma_launch's 0.113 mm round two edges with fine 0.050)
        feat &= (g <= m * 1.0001) & (g <= (fine or np.inf) * 1.0001)
    q = ratio - 1
    for _ in range(rounds):
        for j in range(1, len(fx)):
            if not gone[j - 1]:
                e[j] = min(e[j], e[j - 1] + q * g[j - 1])
        for j in range(len(fx) - 2, -1, -1):
            if not gone[j]:
                e[j] = min(e[j], e[j + 1] + q * g[j])
        inner = [[] if feat[i] or gone[i] else
                 _graded(fx[i], fx[i + 1], e[i], e[i + 1], m[i], ratio)
                 for i in range(len(g))]
        first = np.array([(ln[0] if ln else fx[i + 1]) - fx[i]
                          for i, ln in enumerate(inner)])
        last = np.array([fx[i + 1] - (ln[-1] if ln else fx[i])
                         for i, ln in enumerate(inner)])
        lo = np.minimum(last[:-1], first[1:])
        hi = np.maximum(last[:-1], first[1:])
        # (a jump is mended where the larger cell's interval can be split)
        big = np.where(last[:-1] >= first[1:], np.arange(len(g) - 1),
                       np.arange(1, len(g)))
        bad = np.flatnonzero((hi > ratio * lo * 1.0001) & ~feat[big]
                             & ~gone[:-1] & ~gone[1:]) + 1
        if not len(bad):
            break
        e[bad] = np.minimum(e[bad], ratio * lo[bad - 1])
    out = [fx[0]]
    for i, ln in enumerate(inner):
        out += ln + [fx[i + 1]]
    return np.array(out)


def manhattan(d, L, fine):
    """Edges (vectors d, lengths L) treated as Manhattan: at least `fine` long
    and drifting at most fine/10 across their axis. Near-Manhattan edges come
    from simplify() merging a tiny step (a 1.5 um jog where a track meets a
    slightly wider rectangle); they still get the thirds rule."""
    tol = fine / 10
    return (((np.abs(d[..., 0]) <= tol) | (np.abs(d[..., 1]) <= tol)) &
            (L >= fine))


def curve_lines(xy, fine, mode='extremes', step=None, boxes=None, inside=None):
    """Mesh-line candidates of the curved parts of a ring (rounded pad corners,
    round track ends, arcs, circular antipads, oblique edges). Edges that are
    Manhattan and at least `fine` long are handled by the thirds rule instead.
    mode 'extremes': for each run of consecutive curved edges only its two end
    points and its local x / y extremes (a round antipad gives 2 lines per axis
    instead of one per vertex), and with `step` also a line wherever the run has
    gone `step` mm in x or y since the last one: a thin curved track (the
    copper's width as the step) stays connected on the mesh. With the extremes
    alone a rat-race's 0.8 mm ring sat on cells up to 1.8 mm, broke apart, and
    S21 came out -35.8 dB instead of -3.4. 'vertices': every vertex (dense,
    closest to the drawn geometry). boxes: a list that gets (x0, y0, x1, y1) of
    each curved run longer than 4 fine and twice `step` (the copper's width):
    arcs, rings and fans, not the round end of a track or a pad's corner.
    inside: a test of (x, y): only the lines of points it passes (the ground
    copper within the signal zone)."""
    allx, ally = _curve_points(xy, fine, mode, step, boxes)
    if inside is not None:
        allx, ally = ([p for p in allx
                       if inside(*p)], [p for p in ally if inside(*p)])
    return [p[0] for p in allx], [p[1] for p in ally]


def _curve_points(xy, fine, mode='extremes', step=None, boxes=None):
    """curve_lines' candidates as the points they come from: ([(x, y)] for x
    lines, [(x, y)] for y lines)."""
    xy = np.asarray(xy)[:-1]  # closed ring -> unique vertices
    n = len(xy)
    if n < 3:
        return [], []
    d = np.roll(xy, -1, axis=0) - xy  # edge i: vertex i -> i+1
    L = np.hypot(d[:, 0], d[:, 1])
    manh = manhattan(d, L, fine)
    if mode == 'vertices':
        if boxes is not None:
            curve_lines(np.vstack([xy, xy[:1]]), fine, 'extremes', step=step,
                        boxes=boxes)
        idx = sorted({i
                      for i in range(n)
                      if not manh[i]} | {(i + 1) % n
                                         for i in range(n)
                                         if not manh[i]})
        return [tuple(xy[i]) for i in idx], [tuple(xy[i]) for i in idx]
    if not (~manh).any():
        return [], []
    xs, ys = [], []
    # walk runs starting after a Manhattan edge
    start = int(np.argmax(manh)) if manh.any() else 0
    i, seen = (start + 1) % n if manh.any() else 0, 0
    while seen < n:
        if manh[i]:
            i, seen = (i + 1) % n, seen + 1
            continue
        run = [i]  # consecutive curved edges
        j = (i + 1) % n
        while not manh[j] and len(run) < n:
            run.append(j); j = (j + 1) % n
        # vertex indices along the run
        verts = [run[0]] + [(e + 1) % n for e in run]
        pts = xy[verts]
        ext = pts.max(0) - pts.min(0)
        # not a round end or corner
        if boxes is not None and ext.max() >= max(4 * fine, 2 * (step or 0)):
            boxes.append((*pts.min(0), *pts.max(0)))
        # closed curve (circle): its extremes only
        if ext.max() >= fine / 2 and len(run) == n:
            xs += [
                tuple(pts[np.argmin(pts[:, 0])]),
                tuple(pts[np.argmax(pts[:, 0])])
            ]
            ys += [
                tuple(pts[np.argmin(pts[:, 1])]),
                tuple(pts[np.argmax(pts[:, 1])])
            ]
        elif ext.max() >= fine / 2:  # open run; tiny chamfers are ignored
            xs += [tuple(pts[0]), tuple(pts[-1])]
            ys += [tuple(pts[0]), tuple(pts[-1])]
            lx, ly = pts[0]
            for k in range(1, len(pts) - 1):  # local extremes inside the run
                if ((pts[k, 0] - pts[k - 1, 0]) *
                    (pts[k + 1, 0] - pts[k, 0]) < 0
                        or (step and abs(pts[k, 0] - lx) >= step)):
                    xs.append(tuple(pts[k])); lx = pts[k, 0]
                if ((pts[k, 1] - pts[k - 1, 1]) *
                    (pts[k + 1, 1] - pts[k, 1]) < 0
                        or (step and abs(pts[k, 1] - ly) >= step)):
                    ys.append(tuple(pts[k])); ly = pts[k, 1]
        seen += len(run)
        i = j
    return xs, ys


# the cell across a copper edge, as a fraction of `fine` (see _edge_lines)
THIRDS_CELL = 2 / 3
# zone: the ground copper's edges are resolved within ZONE_DIST mm of the
# signal copper, or ZONE_H times the thickest dielectric, whichever is more
# (signal_zone)
ZONE_DIST = 1.0
ZONE_H = 3.0
# mm: a ground ring no larger than this (a pad, an antipad, a via) that touches
# the zone keeps all its lines (rf_diffvia: half of a return via's lines had
# moved its common mode by 0.14)
ZONE_SMALL = 2.0


def signal_zone(m, dist=None):
    """Where the mesh resolves the ground copper's edges (model mm): within
    `dist` of the signal copper (copper of any net but the ground nets, on any
    layer) and of the signal vias. dist None: ZONE_DIST or ZONE_H x the thickest
    dielectric, whichever is more. None when the board has no signal copper
    apart from the ground."""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    gnd = set(getattr(m, 'ground_nets', ()) or ())
    nets = getattr(m, 'copper_nets', {})
    sig = [
        p for lay, ps in m.copper.items()
        for p, n in zip(ps, nets.get(lay, [''] * len(ps))) if n and n not in gnd
    ]
    sig += [
        Point(v[0], v[1]).buffer(v[2])
        for v, s_ in zip(m.vias, getattr(m, 'via_signal', []))
        if s_
    ]
    if not sig:
        return None
    if dist is None:
        h = max([l['z1'] - l['z0'] for l in m.layers if l['kind'] == 'diel']
                + [0.0])
        dist = max(ZONE_DIST, ZONE_H * h)
    return unary_union(sig).buffer(dist, quad_segs=4)


def _edge_lines(m, fine, curves, boxes=None, zone=None):
    """Candidate x / y lines from the copper: the board outline, thirds-rule
    pairs at Manhattan edges (third: 2/3 outside the metal, third_in: 1/3
    inside) and the curve lines (see curve_lines; boxes: the curved runs'
    boxes); and the Manhattan edges' own x / y (their pairs' cells kept whole
    by smooth_lines). zone (signal_zone): the ground copper's edges count only
    inside it (signal copper's always)."""
    from shapely.geometry import LineString, Point
    from shapely.prepared import prep
    zp = prep(zone) if zone is not None else None
    gnd = set(getattr(m, 'ground_nets', ()) or ())
    nets = getattr(m, 'copper_nets', {})
    ob = m.outline.bounds
    fixed = {0: [ob[0], ob[2]], 1: [ob[1], ob[3]]}
    third, third_in = {0: [], 1: []}, {0: [], 1: []}
    edges = {0: [], 1: []}
    # The pair straddling an edge spans c = THIRDS_CELL * fine, not fine: with
    # pairs a full `fine` apart, edges closer than ~1.5 fine (a trace end, the
    # via pad around it, the antipad) gave candidate lines within the merge
    # distance (fine / 2) of each other and the merge kept too few of them. On
    # the transition demo that cost 4 ps of delay (138.8 vs 134.7 ps converged);
    # with 2/3 fine the default mesh gives 135.1 ps with fewer cells
    # (docs/meshing.md, 'Validation').
    c = THIRDS_CELL * fine
    for lay, ps in m.copper.items():
        for p, net in zip(ps, nets.get(lay, [''] * len(ps))):
            ground = zp is not None and (not net or net in gnd)
            for ring in [p.exterior] + list(p.interiors):
                xy = np.asarray(ring.coords)
                cut = ground  # filter this ring's lines by the zone
                if ground:
                    b = ring.bounds
                    # small: all of it, or none
                    if max(b[2] - b[0], b[3] - b[1]) <= ZONE_SMALL:
                        if not zp.intersects(ring):
                            continue
                        cut = False
                for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
                    dx, dy = x1 - x0, y1 - y0
                    if (not manhattan(np.array([dx, dy]), np.hypot(dx, dy),
                                      fine)):
                        continue
                    if (cut and
                            not zp.intersects(LineString([(x0, y0),
                                                          (x1, y1)]))):
                        continue
                    # orient(): exterior CCW, holes CW -> metal is on the left
                    # of every edge
                    # edge along y at x ~ x0
                    if abs(dx) <= abs(dy):
                        # metal on +x if going down
                        s, xm = -np.sign(dy), 0.5 * (x0 + x1)
                        third_in[0].append(xm + s * c / 3)
                        third[0].append(xm - s * 2 * c / 3)
                        edges[0].append(xm)
                    else:
                        s, ym = np.sign(dx), 0.5 * (y0 + y1)
                        third_in[1].append(ym + s * c / 3)
                        third[1].append(ym - s * 2 * c / 3)
                        edges[1].append(ym)
                # its width
                cx, cy = curve_lines(
                    xy, fine, curves, step=max(fine, 2 * p.area / p.length),
                    boxes=boxes,
                    inside=((lambda x, y: zp.contains(Point(x, y)))
                            if cut else None))
                fixed[0] += cx; fixed[1] += cy
    return fixed, third, third_in, edges


def _snap_vias(m, mode, mesh_xy, via_snap):
    """Via positions after snapping ('snap' vias move onto the nearest line
    crossing within via_snap mm) and the indices of those too far from any
    line."""
    placed, far = [], []
    for i, ((x, y, r, z0, z1), md) in enumerate(zip(m.vias, mode)):
        if md != 'snap':
            placed.append((x, y, r, z0, z1)); continue
        jx, jy = (np.argmin(np.abs(mesh_xy[0] - x)),
                  np.argmin(np.abs(mesh_xy[1] - y)))
        sx, sy = mesh_xy[0][jx], mesh_xy[1][jy]
        if abs(sx - x) <= via_snap and abs(sy - y) <= via_snap:
            placed.append((float(sx), float(sy), r, z0, z1))
        else:
            placed.append((x, y, r, z0, z1)); far.append(i)
    return placed, far


def signal_sheets(m, plane=0.85):
    """Heights of the copper sheets with signal edges on them: the ports' layers
    and every layer that is not a plane (copper over less than `plane` of the
    board)."""
    area = m.outline.area
    out = {m.cu_z[p.layer] for p in m.ports if p.layer in m.cu_z}
    for lay, ps in m.copper.items():
        if lay in m.cu_z and sum(p.area for p in ps) < plane * area:
            out.add(m.cu_z[lay])
    return sorted(out)


def solid_lines(ps, fine, curves='extremes'):
    """Mesh-line candidates of drawn solids' pieces (core.solids.pieces):
    {0: [(x, metal)], 1: [(y, metal)], 2: [(z, metal)]}. x / y: each Manhattan
    edge's own coordinate (manhattan), a curved run's ends and extremes
    (curve_lines); z: each piece's two heights. metal: a metal piece's face
    (it wins a merge against the copper's thirds-rule lines: a wall thinner
    than the cell keeps a line on it)."""
    out = {0: [], 1: [], 2: []}
    for p in ps:
        mt = p.material == 'metal'
        out[2] += [(p.z0, mt), (p.z1, mt)]
        for ring in [p.polygon.exterior, *p.polygon.interiors]:
            xy = np.asarray(ring.coords, float)
            d = np.diff(xy, axis=0)
            man = manhattan(d, np.hypot(d[:, 0], d[:, 1]), fine)
            for (a, b), (dx, dy), ok in zip(xy[:-1], d, man):
                if ok:
                    out[0 if abs(dx) <= abs(dy) else 1].append(
                        (float(a if abs(dx) <= abs(dy) else b), mt))
            cx, cy = curve_lines(xy, fine, curves)
            out[0] += [(float(v), mt) for v in cx]
            out[1] += [(float(v), mt) for v in cy]
    return out


# the merge priority of a solid's face line (_merge): a metal face over the
# copper's thirds-rule lines (1, 2), a dielectric's or air's under them
SOLID_PRIO = {True: 3.0, False: 0.5}


def _solid_spans(ps, lam0, res, axis):
    """[(lo, hi, cell)]: the largest cell (lambda / sqrt(er) / res) over each
    dielectric piece along an axis (0, 1: its outline's extent; 2: its
    heights)."""
    out = []
    for p in ps:
        if p.material != 'dielectric' or p.er <= 1.0:
            continue
        lo, hi = ((p.polygon.bounds[axis], p.polygon.bounds[axis + 2])
                  if axis < 2 else (p.z0, p.z1))
        out.append((lo, hi, lam0 / np.sqrt(p.er) / res))
    return out


def dead_spans(ps, axis, box6):
    """[(a, b)] along an axis where drawn metal (core.solids pieces) fills the
    whole of the domain box6 (x0, y0, z0, x1, y1, z1) across it: a can's floor,
    lid and walls once the mesh ends at its outer faces. No field there: no
    lines inside, nothing to grade beside them (smooth_lines' dead)."""
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union
    metal = [p for p in ps if p.material == 'metal' and p.z1 > p.z0 + 1e-9]
    lo, hi = box6[axis], box6[axis + 3]
    # (a quick no: metal must reach both ends of the domain across the axis)
    ends = [(b, box6[b], box6[b + 3]) for b in range(3) if b != axis]
    span = lambda p, b: ((p.z0, p.z1) if b == 2 else
                         (p.polygon.bounds[b], p.polygon.bounds[b + 2]))
    for b, a0, a1 in ends:
        if not (any(span(p, b)[0] <= a0 + 1e-9 for p in metal)
                and any(span(p, b)[1] >= a1 - 1e-9 for p in metal)):
            return []
    from shapely.prepared import prep
    rect = sbox(box6[0], box6[1], box6[3], box6[4])
    zs = sorted({box6[2], box6[5]} | {z for p in metal for z in (p.z0, p.z1)
                                       if box6[2] < z < box6[5]})
    # each slab's metal, grown a hair and prepared once: every interval
    # below is tested against it (buffering it for each had taken 71 s on a
    # can with 300 posts inside, the covers 0.2 s)
    slabs = [prep(unary_union([p.polygon for p in metal
                               if p.z0 <= za + 1e-9
                               and p.z1 >= zb - 1e-9]).buffer(1e-7))
             for za, zb in zip(zs[:-1], zs[1:])]
    if axis == 2:
        out = [(za, zb) for (za, zb), u in zip(zip(zs[:-1], zs[1:]), slabs)
               if u.covers(rect)]
    else:
        cs = sorted({lo, hi} | {float(v) for p in metal
                                for r in (p.polygon.exterior,
                                          *p.polygon.interiors)
                                for v in np.asarray(r.coords)[:, axis]
                                if lo < v < hi})
        out = []
        for a, b in zip(cs[:-1], cs[1:]):
            strip = (sbox(a, box6[1], b, box6[4]) if axis == 0 else
                     sbox(box6[0], a, box6[3], b))
            if all(u.covers(strip) for u in slabs):
                out.append((a, b))
    merged = []  # touching spans as one
    for a, b in out:
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _check_solids(ps, mesh, log):
    """A metal piece with no mesh line across it on some axis is not in the
    model (the FDTD takes the edges inside a metal): said. build_fdtd puts one
    of no height on its nearest z line."""
    thin = []
    for p in ps:
        if p.material != 'metal':
            continue
        b = p.polygon.bounds
        for k, (lo, hi) in enumerate(((b[0], b[2]), (b[1], b[3]))):
            ln = mesh[k]
            if not np.any((ln >= lo - 1e-6) & (ln <= hi + 1e-6)):
                thin.append(f"{p.name} ({'xy'[k]} {lo:.3f}..{hi:.3f} mm)")
    if thin:
        log("WARNING: drawn metal with no mesh line across it, left out of the "
            f"model: {', '.join(thin)} - make it wider or the cell smaller "
            "(--fine)")
    return thin


def _z_lines(m, ext, M_die, M_air, nz_min, ratio, dz_sheet=None, th=None,
             nz_mask=None, solids=None):
    """z lines: every interface, >= nz_min cells per dielectric (nz_mask, if
    given, through solder mask: a thin layer's cells set the time step), copper
    sheets exactly on lines, graded air (ext[4] below - none on a PEC bottom
    plane - and ext[5] above). dz_sheet (mm): the first cell either side of each
    signal sheet (signal_sheets), graded by `ratio` into the dielectric. th:
    copper thickness (thick.plan): the stack moved as thick.py says, lines at
    both faces of each copper layer and one cell (more only past the
    dielectric's cell) through each copper slab; dz_sheet then on the outer side
    of the faces. solids: dict(z=[(z, metal)], spans=[(z0, z1, cell)], tol,
    dead=[(z0, z1)]): drawn solids' heights (board z, mapped here as the stack
    is), each a line: inside a dielectric layer one that shares the layer's
    cells between its two sides (none within half a cell of another), outside
    the stack one unless within tol of another; the air's extent past them;
    the dielectric pieces' largest cell; the spans of metal across the whole
    domain (smooth_lines' dead)."""
    from . import thick
    zt = ((lambda z: z) if th is None else
          (lambda z: round(thick.z_map(m, th, z), 9)))
    layers = [dict(l, z0=zt(l['z0']), z1=zt(l['z1'])) for l in m.layers]
    top = zt(m.thickness)
    cu = ({lay: (z, z)
           for lay, z in m.cu_z.items()} if th is None else {
               lay: (round(a, 9), round(b, 9))
               for lay, (a, b) in thick.all_faces(m, th).items()
           })
    slabs = {f for f in cu.values() if f[1] > f[0] + 1e-9}
    faces = sorted({z for f in cu.values() for z in f})
    zs = sorted({0.0, top} | {l['z0']
                              for l in layers} | {l['z1']
                                                  for l in layers} | set(faces))
    sheets = [
        z for zc in (signal_sheets(m) if dz_sheet else [])
        for z in cu[thick.layer_at(m, zc)]
    ]
    # drawn solids' faces inside the stack (a pocket's floor, an insert's top)
    inner = sorted({round(zt(z), 9) for z, _ in (solids or {}).get('z', ())
                    if 1e-9 < zt(z) < top - 1e-9})
    zl, extra = [], []
    for z0, z1 in zip(zs[:-1], zs[1:]):
        if (z0, z1) in slabs:  # a copper slab: one cell, or the dielectric's
            zl += list(
                np.linspace(z0, z1,
                            max(1, int(np.ceil((z1 - z0) / M_die))) + 1))
            continue
        lay = next((l for l in layers
                    if l['z0'] <= z0 + 1e-9 and l['z1'] >= z1 - 1e-9), None)
        Mz = M_die if lay else M_air
        n_min = (nz_mask if nz_mask and lay and lay.get('kind') == 'mask'
                 else nz_min)
        n = max(n_min, int(np.ceil((z1 - z0) / Mz)))
        # a solid's face inside the layer: a line on it, the layer's cells
        # shared between its two sides (not --nz cells each, as a layer's
        # own interface would have); one within half a cell of another line
        # is left to it
        guard = 0.5 * (z1 - z0) / n
        cuts = [z0]
        for zc in inner:
            if zc - cuts[-1] > guard and z1 - zc > guard:
                cuts.append(zc)
        cuts.append(z1)
        for a, b in zip(cuts[:-1], cuts[1:]):
            k = max(1, int(np.ceil(n * (b - a) / (z1 - z0) - 1e-9)))
            h = (b - a) / k
            near = [
                zc for zc in (a, b)
                if lay and any(abs(zc - sz) < 1e-9 for sz in sheets)
            ]
            # uniform also when the graded line(s) would leave a sliver (a
            # one-cell layer barely thicker than dz_sheet: a 0.25 um cell at
            # a 92 um mask's top, graded out from, made 411 z lines instead of
            # 25)
            if (not near or dz_sheet >= h
                    or (b - a) < 1.5 * dz_sheet * len(near)):
                zl += list(np.linspace(a, b, k + 1))
                continue
            # graded from dz_sheet at the sheet(s) up to the uniform cell h
            fixed = (
                [a, b]
                + [a + dz_sheet if zc == a else b - dz_sheet for zc in near])
            extra += list(smooth_lines(fixed, h, ratio))
    zl = _merge(np.concatenate([np.round(zl, 9), np.round(extra, 9), zs]), 1e-7)
    for zc in faces:  # snap: sheets must lie exactly on a line
        i = np.argmin(np.abs(zl - zc))
        assert (abs(zl[i] - zc)
                < 1e-6), f"copper at z={zc} is not on a z mesh line"
        zl[i] = zc
    spans, dead, lo, hi = [], [], zl[0], zl[-1]
    if solids:
        # a metal face first: a thin lid keeps its own line over a
        # dielectric's beside it
        for z, _ in sorted(solids['z'], key=lambda t: not t[1]):
            z = zt(z)
            if np.min(np.abs(zl - z)) > solids['tol']:
                zl = np.sort(np.r_[zl, z])
        spans = [(zt(a), zt(b), c) for a, b, c in solids['spans']]
        dead = [(zt(a), zt(b)) for a, b in solids.get('dead', ())]
        # the air margin past the solids' faces, a merged one's too
        heights = [zt(z) for z, _ in solids['z']]
        lo, hi = min(zl[0], min(heights)), max(zl[-1], max(heights))
    zfix = np.concatenate([[lo - ext[4]] if ext[4] > 0 else [], zl,
                           [hi + ext[5] if ext[5] > 0 else zl[-1]]])

    def M(z):
        v = M_die if 0 <= z <= top else M_air
        return min([v] + [c for a, b, c in spans if a - 1e-9 <= z <= b + 1e-9])
    return smooth_lines(zfix, M, ratio, dead=dead)


def _check(m, mesh, info, log):
    """Port traces resolved across their width, lumped-part ends on lines."""
    for p in m.ports:
        b = 1 - p.axis
        lines = mesh[b]
        # (an oblique trace's cut: wider)
        lo, hi = sorted((p.box()[0][b], p.box()[1][b]))
        n_in = int(np.sum((lines > lo + 1e-9) & (lines < hi - 1e-9)))
        info.setdefault('lines_across_ports', {})[p.name] = n_in
        if n_in < 2:
            log(f"WARNING: only {n_in} mesh line(s) across the {p.w:.3f} mm "
                f"trace at {p.name} - use a smaller --fine"
                )
    for pt in getattr(m, 'parts', []):
        lines = mesh[pt.axis]
        ends = [
            np.min(np.abs(lines - v)) < 1e-9
            for _, _, _, s, e in pt.pieces()
            for v in (s[pt.axis], e[pt.axis])
        ]
        if not all(ends):
            log(f"WARNING: the {pt.gap:.3f} mm gap of {pt.ref} is narrower "
                "than half the fine cell - use a smaller --fine")


def signal_gaps(m, near=2.0):
    """The gaps (mm) between the copper the ports sit on and other copper of the
    same layer, closer than `near`: coupled lines, a grounded CPW's slots.
    Copper pieces are the separate polygons of a layer."""
    from shapely.geometry import Point
    out = []
    for p in m.ports:
        polys = m.copper.get(p.layer, [])
        mine = [g for g in polys if g.distance(Point(*p.c)) < 1e-3]
        for g in mine:
            for h in polys:
                if h is not g:
                    d = g.distance(h)
                    if 0 < d < near:
                        out.append(float(d))
    return out


def air_text(air):
    """The air margin as the log says it: '11.5 mm', or per side when they
    differ."""
    if not isinstance(air, (tuple, list)):
        return f"{air:.1f} mm"
    return " ".join(f"{n}={a:g}" for n, a in zip(SIDES, air)) + " mm"


def auto_mesh(m, fmin, fmax, res=20, fine=None, air=5.0, ratio=1.4,
              bc=('MUR', ) * 6, nz_min=4, curves='extremes', vias='auto',
              via_snap=0.2, refine=(), z_sheet=1.0, curve_res=6.0, zone=False,
              thickness=False, nz_mask=None, log=print):
    """Mesh lines (mm) for model m. Returns dict(x, y, z, vias, info, thick):
    thick is the copper thickness plan (thick.plan, None: zero-thickness sheets)
    that build_fdtd builds the model with.

    curves: 'extremes' (default) or 'vertices' - see curve_lines().
    vias:   'exact'  lines at the centre and +-radius of every via (round
                     barrel resolved)
            'centre' one line per axis through the centre (barrel modelled on
                     that line)
            'snap'   no lines of their own: each via moves onto the nearest
                     existing line crossing if it is within via_snap mm (else
                     it gets centre lines)
            'auto'   (default) 'exact' for vias on the port (signal) nets,
                     'snap' for all others (ground stitching / fence vias)
    refine: [(x0, y0, x1, y1, step)] boxes (model coordinates, mm) with x and
            y lines every `step`
    z_sheet: the first z cell either side of a copper sheet with signal edges,
             x fine, graded into the dielectric (0: uniform cells, nz_min per
             dielectric). On 0.8 mm FR-4 the default nz of 4 gave a 10 dB
             coupler -8.6 dB (converged -10.24 to -10.29, lossless) and a
             grounded CPW's S11 at 5 GHz -19 dB (about -33); grading from the
             fine cell gave -10.3 and -31.6 dB for 1.4x the run time. The gap
             and edge cells in x / y hardly mattered (docs/meshing.md#z-lines)
    curve_res: over curved copper (curve_lines' boxes) the largest cell is the
             dielectric's / curve_res: a radial stub's fan had cells up to
             1.8 mm and notched at 2.03 GHz instead of 2.13
    zone:   True: the ground copper's edges get mesh lines only near the
             signal copper (signal_zone); far from it the planes' pours,
             antipads and pads are left to the graded mesh. A number: the
             zone's reach (mm)
    thickness: copper with its stackup thickness, the stack moved to make
             room (thick.py); False: sheets

    Drawn solids (board.solids, as core.solids.pieces resolves them): lines at
    their pieces' faces (solid_lines; a metal face wins a merge, a
    dielectric's or air's gives way to the copper's), the dielectric ones'
    own largest cell, and the air margin past them as well as past the
    board."""
    from . import thick
    from kicad2ems.core.solids import pieces as solid_pieces, describe
    th = thick.plan(m) if thickness else None
    sp = solid_pieces(m) if getattr(m, 'solids', None) else []
    er_max = max([l['er'] for l in m.layers] + [1.0])
    lam0 = C0 / fmax * 1e3                                          # mm
    M_air, M_die = lam0 / res, lam0 / np.sqrt(er_max) / res
    widths = [p.w for p in m.ports]
    h_min = min([l['z1'] - l['z0'] for l in m.layers if l['kind'] == 'diel']
                + [1.0])
    if fine is None:
        # a quarter of the narrowest of: the port traces, twice the thinnest
        # dielectric, and the gaps next to the signal copper (the coupler's 0.14
        # mm gap between 1.5 mm lines had had a cell of 0.38 mm)
        fine = float(
            np.clip(
                min(widths + [h_min * 2] + signal_gaps(m)) / 4, 0.01,
                M_die / 4))
    air = 5.0 if air is None else air
    sides = list(air) if isinstance(air, (tuple, list)) else [air] * 6
    npml = [int(b.split('_')[1]) if b.startswith('PML_') else 0 for b in bc]
    # per side: air margin (+ PML cells)
    ext = [sides[i] + npml[i] * M_air for i in range(6)]
    bottom_pec = bc[4] == 'PEC' and bottom_is_plane(m)
    if bottom_pec:
        ext[4] = 0.0  # PEC wall = the bottom plane itself
    ob = m.outline.bounds
    # the domain: the board and its solids, the air margin past both
    dom = list(ob)
    for p in sp:
        b = p.polygon.bounds
        dom = [min(dom[0], b[0]), min(dom[1], b[1]), max(dom[2], b[2]),
               max(dom[3], b[3])]
    shut = None
    if sp:  # drawn metal closed round the board: no air outside it
        from kicad2ems.core.solids import enclosure, extent
        e = extent(m, sp)
        shut = enclosure(m, sp, (dom[0] - sides[0], dom[1] - sides[2],
                                 e[2] - sides[4], dom[2] + sides[1],
                                 dom[3] + sides[3], e[5] + sides[5]))
        if shut is not None:
            dom = [shut[0], shut[1], shut[3], shut[4]]
            ext = [npml[i] * M_air for i in range(6)]
    sl = solid_lines(sp, fine, curves) if sp else {0: [], 1: [], 2: []}
    if shut is not None:  # (what is drawn outside the metal: not meshed)
        sl = {k: [(v, mt) for v, mt in sl[k]
                  if shut[k] - 1e-9 <= v <= shut[k + 3] + 1e-9]
              for k in range(3)}
    dead = {0: [], 1: [], 2: []}  # metal across the whole domain
    if any(p.material == 'metal' for p in sp):
        e = shut if shut is not None else extent(m, sp)
        dead = {k: dead_spans(sp, k, (dom[0] - ext[0], dom[1] - ext[2],
                                      e[2] - ext[4], dom[2] + ext[1],
                                      dom[3] + ext[3], e[5] + ext[5]))
                for k in range(3)}
    boxes = []
    zg = (signal_zone(m, None if zone is True else float(zone))
          if zone is not False and zone is not None else None)
    fixed, third, third_in, edges = _edge_lines(m, fine, curves, boxes, zg)
    sig = getattr(m, 'via_signal', [False] * len(m.vias))
    mode = [vias] * len(sig)  # each via 'exact' / 'centre' / 'snap'
    if vias == 'auto':        # exact on signal nets, snap elsewhere
        mode = ['exact' if s_ else 'snap' for s_ in sig]
    for (x, y, r, z0, z1), md in zip(m.vias, mode):
        if md == 'exact':
            fixed[0] += [x - r, x, x + r]; fixed[1] += [y - r, y, y + r]
        elif md == 'centre':
            fixed[0].append(x); fixed[1].append(y)
    # kept exactly: the board outline, the port planes (the port width snaps to
    # the edge lines) and both ends of every lumped-part piece
    keep = {0: [ob[0], ob[2]], 1: [ob[1], ob[3]]}
    for p in m.ports:
        keep[p.axis] += p.planes()
    for pt in getattr(m, 'parts', []):
        for _, _, _, s, e in pt.pieces():
            keep[pt.axis] += [s[pt.axis], e[pt.axis]]
    for x0, y0, x1, y1, step in refine:  # local refinement boxes
        fixed[0] += list(np.arange(min(x0, x1), max(x0, x1) + step / 2, step))
        fixed[1] += list(np.arange(min(y0, y1), max(y0, y1) + step / 2, step))

    def xy_lines(extra):
        out = {}
        for k in (0, 1):
            sv = [v for v, _ in sl[k]]
            pts = _merge(
                np.concatenate([fixed[k], extra[k], third[k],
                                third_in[k], sv]), fine / 2, keep=keep[k],
                prio=np.r_[np.zeros(len(fixed[k]) + len(extra[k])),
                           np.ones(len(third[k])),
                           np.full(len(third_in[k]), 2.0),
                           [SOLID_PRIO[mt] for _, mt in sl[k]]])
            lo, hi = ob[k], ob[2 + k]
            pts = np.concatenate([[dom[k] - ext[2 * k]], pts,
                                  [dom[2 + k] + ext[2 * k + 1]]])
            spans = [(b[k], b[2 + k]) for b in boxes] if curve_res > 1 else []
            ds = _solid_spans(sp, lam0, res, k)
            Mf = (lambda x, lo=lo, hi=hi, sp=spans, ds=ds: min(
                [M_air if not lo - 1e-9 <= x <= hi + 1e-9 else
                 M_die / curve_res if any(a <= x <= b for a, b in sp) else
                 M_die]
                + [c for a, b, c in ds if a - 1e-9 <= x <= b + 1e-9]))
            out[k] = smooth_lines(pts, Mf, ratio, fine, whole=edges[k],
                                  dead=dead[k])
        return out

    mesh = xy_lines({0: [], 1: []})
    placed, far = _snap_vias(m, mode, mesh, via_snap)
    if far:  # too far from any line: give them centre lines
        mesh = xy_lines(
            {0: [m.vias[i][0] for i in far], 1: [m.vias[i][1] for i in far]})
        placed, far = _snap_vias(m, mode, mesh, via_snap)
        # their lines were merged away: snap them anyway
        if far:
            # (left off the grid, openEMS would drop them)
            placed, _ = _snap_vias(m, mode, mesh, np.inf)
    moved = [np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(placed, m.vias)]
    nsnap = sum(1 for md in mode if md == 'snap')
    mesh[2] = _z_lines(m, ext, M_die, M_air, nz_min, ratio,
                       dz_sheet=(z_sheet * fine if z_sheet else None), th=th,
                       nz_mask=nz_mask,
                       solids=(dict(z=sl[2], spans=_solid_spans(
                           sp, lam0, res, 2), tol=fine / 2, dead=dead[2])
                               if sp else None))
    nx, ny, nz = (len(mesh[k]) for k in range(3))
    dmin = [np.diff(mesh[k]).min() * 1e-3 for k in range(3)]
    dt = 1 / (C0 * np.sqrt(sum(1 / d ** 2 for d in dmin)))
    info = dict(nx=nx, ny=ny, nz=nz, cells=nx * ny * nz, fine_mm=fine,
                maxres_die_mm=M_die, maxres_air_mm=M_air, air_mm=min(sides),
                air_sides_mm=sides, air_text=air_text(air), bc=list(bc),
                curves=curves, vias=vias, vias_snapped=nsnap,
                via_max_move_mm=float(max(moved, default=0.0)), dt_ps=dt * 1e12,
                min_cell_mm=[d * 1e3 for d in dmin],
                mem_gb=nx * ny * nz * 150e-9, thickness='on' if th else 'off')
    if sp:
        info['solids'] = describe(sp)
        info['enclosed'] = shut is not None
    log(f"mesh: {nx} x {ny} x {nz} = {nx * ny * nz / 1e6:.2f} M cells (fine "
        f"{fine:.3f} mm, max {M_die:.2f} mm in dielectric / {M_air:.2f} mm in "
        f"air, air margin {air_text(air)}), boundaries "
        f"{' '.join(f'{n}={b}' for n, b in zip(SIDES, bc))}"
        + (" (PEC on the bottom plane)" if bottom_pec else "")
        + f", time step {dt * 1e12:.3f} ps, ~{info['mem_gb']:.1f} GB"
        + (", copper with its thickness (the stack moved up by it)"
           if th else ""))
    _check(m, mesh, info, log)
    if sp:
        log(f"{info['solids']}, lines at their faces"
            + ("; the metal closes round the board: the mesh ends at its "
               "outer faces (no air outside it)" if shut is not None else ""))
        _check_solids(sp, mesh, log)
    if nsnap:
        log(f"vias: {nsnap} snapped onto existing lines (moved <= "
            f"{info['via_max_move_mm']:.3f} mm), {len(m.vias) - nsnap} with "
            "their own lines"
            + (f"; {len(far)} moved further than --via-snap {via_snap:g} mm "
               "(no line nearer)" if far else ""))
    return dict(x=mesh[0], y=mesh[1], z=mesh[2], vias=placed, info=info,
                thick=th)
