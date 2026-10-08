"""
Automatic rectilinear mesh for openEMS.

  x / y : thirds rule at Manhattan copper edges (1/3 inside, 2/3 outside the metal), end points of
          oblique edges, via centre +- radius, port planes and the board outline kept exactly;
          lines closer than fine/2 merged by priority; graded smoothing (<= ratio growth) up to
          lambda/res in the dielectric and in air
  z     : every interface, >= nz cells per dielectric, copper sheets exactly on lines, air margin
          (none below a PEC bottom plane), PML cells added only on PML sides
"""
import numpy as np
from .constants import C0
from .boundaries import SIDES, bottom_is_plane


def _merge(lines, tol, keep=(), prio=None):
    """Cluster lines closer than tol and keep ONE line per cluster: lines in `keep` (exact), else the
    member with the highest priority (ties: the first). Clusters do not chain (every member is within
    tol of the cluster's first line) and lines are never averaged, so the thirds-rule line just inside
    a narrow trace survives instead of drifting outside it."""
    lines = np.asarray(lines, float)
    prio = np.zeros(len(lines)) if prio is None else np.asarray(prio, float)
    if len(keep):
        lines = np.concatenate([lines, keep]); prio = np.concatenate([prio, np.full(len(keep), 1e9)])
    o = np.argsort(lines, kind='stable')
    lines, prio = lines[o], prio[o]
    out, op, cl = [], [], [0]
    for i in range(1, len(lines) + 1):
        if i < len(lines) and lines[i] - lines[cl[0]] <= tol:
            cl.append(i); continue
        c = np.array(cl)
        j = c[np.argmax(prio[c])]
        x, pr = lines[j], prio[j]
        # the winners of two neighbouring clusters can still be closer than tol: keep the stronger one
        while out and x - out[-1] <= tol:
            if pr > op[-1]:
                out.pop(); op.pop()
            else:
                break
        if not out or x - out[-1] > tol:
            out.append(x); op.append(pr)
        cl = [i]
    return np.array(out)


def smooth_lines(fixed, maxres, ratio=1.4, fine=None):
    """Insert lines between fixed lines so that every cell is <= maxres(x) and neighbouring cells
    grow by at most `ratio` away from the fixed (feature) lines. maxres: float or callable(x)."""
    M = maxres if callable(maxres) else (lambda x, m=maxres: m)
    fx = np.unique(np.asarray(fixed, float))
    if len(fx) < 2:
        return fx
    gaps = np.diff(fx)
    out = [fx[0]]
    for i in range(len(fx) - 1):
        a, b = fx[i], fx[i + 1]
        m = M(0.5 * (a + b))
        ha = gaps[i - 1] if i > 0 else gaps[i]                      # neighbouring cells set the start size
        hb = gaps[i + 1] if i + 1 < len(gaps) else gaps[i]
        if fine:
            ha, hb = min(ha, fine * 3), min(hb, fine * 3)
        ha, hb = min(ha, m), min(hb, m)
        L = b - a
        if L <= m and L <= ratio * min(ha, hb) * 1.0001:
            out.append(b)
            continue
        left, right = [a], [b]                                 # graded from both ends towards the middle
        h, g = ha, hb
        while True:
            hn, gn = min(h * ratio, m), min(g * ratio, m)
            if right[-1] - left[-1] <= hn + gn:
                break
            if h <= g:
                left.append(left[-1] + hn); h = hn
            else:
                right.append(right[-1] - gn); g = gn
        if len(left) > 1:                                      # give the last graded cell of each side back
            left.pop()
        if len(right) > 1:
            right.pop()
        lo, hi = left[-1], right[-1]                           # ... and split [lo, hi] evenly: no sliver
        hl = left[-1] - left[-2] if len(left) > 1 else ha
        gr = right[-2] - right[-1] if len(right) > 1 else hb
        k = max(1, int(np.ceil((hi - lo) / min(m, ratio * min(hl, gr)) - 1e-9)))
        out += left[1:] + list(np.linspace(lo, hi, k + 1)[1:-1]) + right[1:][::-1] + [b]
    return np.array(out)


def curve_lines(xy, fine, mode='extremes'):
    """Mesh-line candidates of the curved parts of a ring (rounded pad corners, round track ends,
    arcs, circular antipads, oblique edges). Edges that are Manhattan and at least `fine` long are
    handled by the thirds rule instead. mode 'extremes': for each run of consecutive curved edges only
    its two end points and its local x / y extremes (a round antipad gives 2 lines per axis instead of
    one per vertex); 'vertices': every vertex (dense, closest to the drawn geometry)."""
    xy = np.asarray(xy)[:-1]                                        # closed ring -> unique vertices
    n = len(xy)
    if n < 3:
        return [], []
    d = np.roll(xy, -1, axis=0) - xy                                # edge i: vertex i -> i+1
    L = np.hypot(d[:, 0], d[:, 1])
    manh = ((np.abs(d[:, 0]) < 1e-6 * L) | (np.abs(d[:, 1]) < 1e-6 * L)) & (L >= fine)
    if mode == 'vertices':
        idx = sorted({i for i in range(n) if not manh[i]} | {(i + 1) % n for i in range(n) if not manh[i]})
        return [xy[i, 0] for i in idx], [xy[i, 1] for i in idx]
    if not (~manh).any():
        return [], []
    xs, ys = [], []
    start = int(np.argmax(manh)) if manh.any() else 0               # walk runs starting after a Manhattan edge
    i, seen = (start + 1) % n if manh.any() else 0, 0
    while seen < n:
        if manh[i]:
            i, seen = (i + 1) % n, seen + 1
            continue
        run = [i]                                                   # consecutive curved edges
        j = (i + 1) % n
        while not manh[j] and len(run) < n:
            run.append(j); j = (j + 1) % n
        verts = [run[0]] + [(e + 1) % n for e in run]               # vertex indices along the run
        pts = xy[verts]
        ext = pts.max(0) - pts.min(0)
        if ext.max() >= fine / 2 and len(run) == n:                 # closed curve (circle): its extremes only
            xs += [pts[:, 0].min(), pts[:, 0].max()]; ys += [pts[:, 1].min(), pts[:, 1].max()]
        elif ext.max() >= fine / 2:                                 # open run; tiny chamfers are ignored
            xs += [pts[0, 0], pts[-1, 0]]; ys += [pts[0, 1], pts[-1, 1]]
            for k in range(1, len(pts) - 1):                        # local extremes inside the run
                if (pts[k, 0] - pts[k - 1, 0]) * (pts[k + 1, 0] - pts[k, 0]) < 0:
                    xs.append(pts[k, 0])
                if (pts[k, 1] - pts[k - 1, 1]) * (pts[k + 1, 1] - pts[k, 1]) < 0:
                    ys.append(pts[k, 1])
        seen += len(run)
        i = j
    return xs, ys


def auto_mesh(m, fmin, fmax, res=20, fine=None, air=5.0, ratio=1.4, bc=('MUR',) * 6, nz_min=4,
              curves='extremes', vias='auto', via_snap=0.2, log=print):
    """Mesh lines (mm) for model m. Returns dict(x, y, z, vias, info).

    curves: 'extremes' (default) or 'vertices' - see curve_lines().
    vias:   'exact'  lines at the centre and +-radius of every via (round barrel resolved)
            'centre' one line per axis through the centre (barrel modelled on that line)
            'snap'   no lines of their own: each via moves onto the nearest existing line crossing if it
                     is within via_snap mm (else it gets centre lines)
            'auto'   (default) 'exact' for vias on the port (signal) nets, 'snap' for all others
                     (ground stitching / fence vias)"""
    er_max = max([l['er'] for l in m.layers] + [1.0])
    lam0 = C0 / fmax * 1e3                                          # mm
    M_air, M_die = lam0 / res, lam0 / np.sqrt(er_max) / res
    widths = [p.w for p in m.ports]
    h_min = min([l['z1'] - l['z0'] for l in m.layers if l['kind'] == 'diel'] + [1.0])
    if fine is None:
        fine = float(np.clip(min(widths + [h_min * 2]) / 4, 0.01, M_die / 4))
    air = 5.0 if air is None else air
    npml = [int(b.split('_')[1]) if b.startswith('PML_') else 0 for b in bc]
    ext = [air + npml[i] * M_air for i in range(6)]                 # per side: air margin (+ PML cells)
    bottom_pec = bc[4] == 'PEC' and bottom_is_plane(m)
    if bottom_pec:
        ext[4] = 0.0                                                # PEC wall = the bottom plane itself
    ob = m.outline.bounds
    fixed = {0: [ob[0], ob[2]], 1: [ob[1], ob[3]]}
    third = {0: [], 1: []}
    third_in = {0: [], 1: []}                                       # thirds-rule line inside the metal
    # Manhattan metal edges: thirds rule (1/3 inside the metal, 2/3 outside); curves: see curve_lines
    for lay, ps in m.copper.items():
        for p in ps:
            for ring in [p.exterior] + list(p.interiors):
                xy = np.asarray(ring.coords)
                for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
                    dx, dy = x1 - x0, y1 - y0
                    Ls = np.hypot(dx, dy)
                    if Ls < fine:
                        continue
                    # orient(): exterior CCW, holes CW -> metal is on the left of every edge
                    if abs(dx) < 1e-6 * Ls:                         # edge along y at x = x0
                        s = -np.sign(dy)                            # metal on +x if going down
                        third_in[0].append(x0 + s * fine / 3); third[0].append(x0 - s * 2 * fine / 3)
                    elif abs(dy) < 1e-6 * Ls:
                        s = np.sign(dx)
                        third_in[1].append(y0 + s * fine / 3); third[1].append(y0 - s * 2 * fine / 3)
                cx, cy = curve_lines(xy, fine, curves)
                fixed[0] += cx; fixed[1] += cy
    # vias
    sig = getattr(m, 'via_signal', [False] * len(m.vias))
    mode = ['exact' if (vias == 'auto' and s_) else ('snap' if vias == 'auto' else vias) for s_ in sig]
    for (x, y, r, z0, z1), md in zip(m.vias, mode):
        if md == 'exact':
            fixed[0] += [x - r, x, x + r]; fixed[1] += [y - r, y, y + r]
        elif md == 'centre':
            fixed[0].append(x); fixed[1].append(y)
    keep = {0: [ob[0], ob[2]], 1: [ob[1], ob[3]]}                  # exact: board outline and port planes
    for p in m.ports:                                               # (the port width snaps to the edge lines)
        keep[p.axis].append(p.c[p.axis])

    def xy_lines(extra):
        out = {}
        for k in (0, 1):
            pts = _merge(np.concatenate([fixed[k], extra[k], third[k], third_in[k]]), fine / 2, keep=keep[k],
                         prio=np.r_[np.zeros(len(fixed[k]) + len(extra[k])), np.ones(len(third[k])),
                                    np.full(len(third_in[k]), 2.0)])
            lo, hi = ob[k], ob[2 + k]
            pts = np.concatenate([[lo - ext[2 * k]], pts, [hi + ext[2 * k + 1]]])
            Mf = (lambda x, lo=lo, hi=hi: M_die if lo - 1e-9 <= x <= hi + 1e-9 else M_air)
            out[k] = smooth_lines(pts, Mf, ratio, fine)
        return out

    def snap(mesh_xy):
        placed, far = [], []
        for i, ((x, y, r, z0, z1), md) in enumerate(zip(m.vias, mode)):
            if md != 'snap':
                placed.append((x, y, r, z0, z1)); continue
            jx, jy = np.argmin(np.abs(mesh_xy[0] - x)), np.argmin(np.abs(mesh_xy[1] - y))
            sx, sy = mesh_xy[0][jx], mesh_xy[1][jy]
            if abs(sx - x) <= via_snap and abs(sy - y) <= via_snap:
                placed.append((float(sx), float(sy), r, z0, z1))
            else:
                placed.append((x, y, r, z0, z1)); far.append(i)
        return placed, far

    mesh = xy_lines({0: [], 1: []})
    placed, far = snap(mesh)
    if far:                                                         # too far from any line: give them centre lines
        mesh = xy_lines({0: [m.vias[i][0] for i in far], 1: [m.vias[i][1] for i in far]})
        placed, _ = snap(mesh)
    moved = [np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(placed, m.vias)]
    nsnap = sum(1 for md in mode if md == 'snap')
    # z: interfaces, nz_min cells (at least) per dielectric, graded air above and below
    zs = sorted({0.0, m.thickness} | {l['z0'] for l in m.layers} | {l['z1'] for l in m.layers} | set(m.cu_z.values()))
    zl = []
    for z0, z1 in zip(zs[:-1], zs[1:]):
        lay = next((l for l in m.layers if l['z0'] <= z0 + 1e-9 and l['z1'] >= z1 - 1e-9), None)
        Mz = M_die if lay else M_air
        n = max(nz_min, int(np.ceil((z1 - z0) / Mz)))
        zl += list(np.linspace(z0, z1, n + 1))
    zl = _merge(np.concatenate([np.round(zl, 9), zs]), 1e-7)
    for lay, zc in m.cu_z.items():                                  # snap: sheets must lie exactly on a line
        i = np.argmin(np.abs(zl - zc))
        assert abs(zl[i] - zc) < 1e-6, f"{lay} at z={zc} is not on a z mesh line"
        zl[i] = zc
    zfix = np.concatenate([[zl[0] - ext[4]] if ext[4] > 0 else [], zl, [zl[-1] + ext[5]]])
    mesh[2] = smooth_lines(zfix, lambda z: M_die if 0 <= z <= m.thickness else M_air, ratio)
    nx, ny, nz = (len(mesh[k]) for k in range(3))
    dmin = [np.diff(mesh[k]).min() * 1e-3 for k in range(3)]
    dt = 1 / (C0 * np.sqrt(sum(1 / d ** 2 for d in dmin)))
    info = dict(nx=nx, ny=ny, nz=nz, cells=nx * ny * nz, fine_mm=fine, maxres_die_mm=M_die, maxres_air_mm=M_air,
                air_mm=air, bc=list(bc), curves=curves, vias=vias, vias_snapped=nsnap,
                via_max_move_mm=float(max(moved, default=0.0)), dt_ps=dt * 1e12, min_cell_mm=[d * 1e3 for d in dmin],
                mem_gb=nx * ny * nz * 150e-9)
    log(f"mesh: {nx} x {ny} x {nz} = {nx * ny * nz / 1e6:.2f} M cells (fine {fine:.3f} mm, max {M_die:.2f} mm in "
        f"dielectric / {M_air:.2f} mm in air, air margin {air:.1f} mm), boundaries "
        f"{' '.join(f'{n}={b}' for n, b in zip(SIDES, bc))}" + (" (PEC on the bottom plane)" if bottom_pec else "") +
        f", time step {dt * 1e12:.3f} ps, ~{info['mem_gb']:.1f} GB")
    for p in m.ports:                                               # every port trace resolved across its width
        b = 1 - p.axis
        lines = mesh[b]
        n_in = int(np.sum((lines > p.c[b] - p.w / 2 + 1e-9) & (lines < p.c[b] + p.w / 2 - 1e-9)))
        info.setdefault('lines_across_ports', {})[p.name] = n_in
        if n_in < 2:
            log(f"WARNING: only {n_in} mesh line(s) across the {p.w:.3f} mm trace at {p.name} - use a smaller --fine")
    if nsnap:
        log(f"vias: {nsnap} snapped onto existing lines (moved <= {info['via_max_move_mm']:.3f} mm), "
            f"{len(m.vias) - nsnap} with their own lines")
    return dict(x=mesh[0], y=mesh[1], z=mesh[2], vias=placed, info=info)
