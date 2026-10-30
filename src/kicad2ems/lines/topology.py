"""
The line model's topology, once build() has its sections: the ports' paths (shortest by length, the
branches off them as stubs), coupled lines (parallel sections within COUPLE_H h of each other, cut where
their overlaps start and end) and discontinuities where sections meet (open end, corner, bend, step,
tee, cross, gap).
"""
import functools, heapq, math
import numpy as np
from ..constants import C0
from .elements import Element


def _shortest(lm, adj, src):
    """Dijkstra by physical length: (distance, previous (element, node)) per node."""
    dist, prev, heap = {src: 0.0}, {}, [(0.0, src)]
    while heap:
        d0, u = heapq.heappop(heap)
        if d0 > dist.get(u, math.inf):
            continue
        for i, v in adj.get(u, []):
            nd = d0 + lm.elements[i].length + 1e-9
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, (i, u)
                heapq.heappush(heap, (nd, v))
    return dist, prev


def _paths(lm):
    adj = lm.adjacency()
    names = list(lm.ports)
    done = set()
    for p in names:
        src = lm.ports[p]
        dist, prev = _shortest(lm, adj, src)
        targets = [(q, lm.ports[q]) for q in names if q != p and lm.ports[q] in dist and (q, p) not in done]
        if not targets and not any(p in pair for pair in done):
            far = max((n for n in dist if n != 'gnd'), key=lambda n: dist[n], default=None)
            targets = [(None, far)] if far is not None and far != src else []
            if targets:
                lm.notes.append(f"{p} reaches no other port through traces, pads and parts: its path ends "
                                f"{dist[far]:.2f} mm from it (copper drawn as shapes or zones is not followed: it only widens the tracks it overlaps)")
        for q, dst in targets:
            done.add((p, q))
            seq, node = [], dst
            while node != src:
                i, u = prev[node]
                seq.append((i, u, node))
                node = u
            seq.reverse()
            secs, s0, on_path = [], 0.0, {src} | {n for _, _, n in seq}
            for i, u, v in seq:
                e = lm.elements[i]
                secs.append((e, s0, e.a != u))
                s0 += e.length
            branches = []
            path_elems = {i for i, _, _ in seq}
            for k, (i, u, v) in enumerate(seq):
                for j, _ in adj.get(u, []):
                    if j in path_elems or (k > 0 and j == seq[k - 1][0]):
                        continue
                    L, lab, z = _branch(lm, adj, j, u, on_path)
                    if L < 0.25 and lab.endswith('ends: open)'):
                        continue                            # trace overhang behind a port or past a pad
                    branches.append((secs[k][1], L, lab, z))
            lm.paths.append((p, q, secs, branches))


def _branch(lm, adj, j, start, on_path):
    """Total length, a description and the first Z0 of the branch that leaves the path through element j."""
    e0 = lm.elements[j]
    if e0.kind == 'part' and 'gnd' in (e0.a, e0.b):
        return e0.length, f"{e0.label} {e0.part.value_str()} to ground", None
    seen, stack, total, ends = {j}, [(j, e0.b if e0.a == start else e0.a)], 0.0, []
    while stack:
        i, node = stack.pop()
        e = lm.elements[i]
        total += e.length
        if e.kind == 'part':
            ends.append(f"{e.label} {e.part.value_str()}" + (' to ground' if 'gnd' in (e.a, e.b) else ''))
        nxt = [(k, o) for k, o in adj.get(node, []) if k not in seen and node not in on_path]
        if not nxt and node not in on_path and e.kind != 'part':
            ends.append('open')
        for k, o in nxt:
            seen.add(k)
            stack.append((k, o))
    lab = f"stub ({len(seen)} section{'s' if len(seen) > 1 else ''}, ends: {', '.join(sorted(set(ends))) or 'open'})"
    return total, lab, e0.z0


# ---- coupled lines -------------------------------------------------------------------------------
COUPLE_H = 5.0          # parallel lines couple when their edge gap is at most this many h (striplines: the mean of
COUPLED_XS = ('microstrip', 'stripline', 'offset stripline')   # the heights to the two planes)
FIELD_GROUPS = True     # coupled sections' C from the field solver, every line coupled to every other (False:
                        # group_lc's nearest-neighbour pairs of the formulas)
_COPY = ('xs', 'z0', 'eeff', 'z0_sheet', 'eeff_sheet', 'er', 'h', 's', 't', 'tand', 'offset')


def _couple_h(e):
    return e.h if isinstance(e.h, float) else 0.5 * (e.h[0] + e.h[1])


def _straight(e):
    p = np.asarray(e.pts)
    if p is None or len(p) < 2:
        return False
    d = p[-1] - p[0]
    L = np.hypot(*d)
    if L < 1e-9:
        return False
    n = np.array([-d[1], d[0]]) / L
    return bool(np.all(np.abs((p - p[0]) @ n) < 1e-4))


def _piece(e, a, b, p0, p1, tag):
    el = Element(e.kind, a, b, float(np.hypot(*(np.asarray(p1) - p0))), layer=e.layer, w=e.w,
                 pts=np.array([p0, p1], float), net=e.net, label=e.label, region=e.region, radial=e.radial)
    for k in _COPY:
        setattr(el, k, getattr(e, k))
    el.notes = list(e.notes)
    return el


def _split_at(e, ts, u):
    """Element e (straight) cut where its coordinate along u passes the values ts: pieces in order a -> b."""
    p0, p1 = np.asarray(e.pts[0], float), np.asarray(e.pts[-1], float)
    ta, tb = float(p0 @ u), float(p1 @ u)
    lo, hi = sorted((ta, tb))
    cuts = sorted(t for t in ts if lo + 1e-4 < t < hi - 1e-4)
    if not cuts:
        return [e]
    if ta > tb:
        cuts = cuts[::-1]
    pts = [p0] + [p0 + (p1 - p0) * (t - ta) / (tb - ta) for t in cuts] + [p1]
    nodes = [e.a] + [f"{e.a}>{e.b}~{t:.4f}" for t in cuts] + [e.b]
    return [_piece(e, nodes[k], nodes[k + 1], pts[k], pts[k + 1], k) for k in range(len(pts) - 1)]


def _couple(lm, model):
    """Parallel straight sections of one cross-section (microstrip or stripline) on one layer within
    COUPLE_H * h of each other: cut where their overlaps start and end, and group the pieces side by side
    into coupled sections."""
    cand = [e for e in lm.elements if e.kind == 'trace' and e.xs in COUPLED_XS and e.length > 1e-3 and _straight(e)]
    if len(cand) < 2:
        return
    info = {}
    for e in cand:
        d = np.asarray(e.pts[-1], float) - np.asarray(e.pts[0], float)
        ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
        info[id(e)] = ang
    # interacting pairs (union-find over the candidates)
    parent = {id(e): id(e) for e in cand}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def geom(e, u, n):
        p = np.asarray(e.pts, float)
        t = sorted((float(p[0] @ u), float(p[-1] @ u)))
        return t[0], t[1], float(p[0] @ n)
    pairs = []
    for i, e in enumerate(cand):
        for f in cand[i + 1:]:
            if e.layer != f.layer or e.xs != f.xs or e.h != f.h:
                continue
            da = abs(info[id(e)] - info[id(f)])
            if min(da, 180 - da) > 1.0:
                continue
            ang = math.radians(info[id(e)])
            u, n = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
            a0, a1, ca = geom(e, u, n)
            b0, b1, cb = geom(f, u, n)
            gap = abs(ca - cb) - 0.5 * (e.w + f.w)
            if gap <= 1e-4 or gap > COUPLE_H * _couple_h(e):
                continue
            if min(a1, b1) - max(a0, b0) < max(0.2, gap):
                continue
            pairs.append((e, f))
            parent[find(id(e))] = find(id(f))
    if not pairs:
        return
    sets = {}
    for e in cand:
        if any(e is x or e is y for x, y in pairs):
            sets.setdefault(find(id(e)), []).append(e)
    for members in sets.values():
        ang = math.radians(info[id(members[0])])
        u, n = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
        # the overlaps' ends, merged within 1 um: rounding apart two ends at the same place (0.57424999 and
        # 0.57425 to 0.5742 / 0.5743) had put the legs of a pair into separate pieces, and nothing coupled
        cuts = []
        for v in sorted(t for e in members for t in geom(e, u, n)[:2]):
            if not cuts or v - cuts[-1] > 1e-3:
                cuts.append(v)
        at = lambda v: int(np.argmin(np.abs(np.asarray(cuts) - v)))
        pieces = {}
        for e in members:
            k = lm.elements.index(e)
            parts = _split_at(e, cuts, u)
            lm.elements[k:k + 1] = parts
            for pc in parts:
                t0, t1, c = geom(pc, u, n)
                pieces.setdefault((at(t0), at(t1)), []).append((c, pc))
        for (i0, i1), row in sorted(pieces.items()):
            t0, t1 = cuts[i0], cuts[i1]
            if t1 - t0 < 1e-3 or len(row) < 2:
                continue
            row.sort(key=lambda r: r[0])
            chain = [row[0]]
            for c, pc in row[1:] + [(None, None)]:
                prev = chain[-1][1]
                gap = None if pc is None else (c - chain[-1][0]) - 0.5 * (prev.w + pc.w)
                if pc is not None and prev.xs == pc.xs and prev.h == pc.h and 1e-4 < gap <= COUPLE_H * _couple_h(prev):
                    chain.append((c, pc))
                    continue
                if len(chain) >= 2:
                    _add_group(lm, chain, u, t0)
                chain = [(c, pc)] if pc is not None else []


def _add_group(lm, chain, u, t0):
    from .coupled import coupled
    gid = len(lm.groups)
    members, gaps = [], []
    for k, (c, e) in enumerate(chain):
        near_first = float(np.asarray(e.pts[0]) @ u) <= float(np.asarray(e.pts[-1]) @ u)
        near, far = (e.a, e.b) if near_first else (e.b, e.a)
        members.append((e, near, far))
        e.group = gid
        e.label = f"{e.label} (coupled {k + 1}/{len(chain)})"
        if k:
            gaps.append((c - chain[k - 1][0]) - 0.5 * (e.w + chain[k - 1][1].w))
    e0 = chain[0][1]
    g = dict(members=members, gaps=gaps, length=e0.length, h=e0.h, er=e0.er, tand=e0.tand, layer=e0.layer, xs=e0.xs,
             pairs=[])
    for k, gap in enumerate(gaps):
        wa, wb = members[k][0].w, members[k + 1][0].w
        ze, zo, ee, eo = coupled(e0.xs, 0.5 * (wa + wb), gap, e0.h, e0.er)
        g['pairs'].append(dict(gap=gap, ze=ze, zo=zo, eeff_e=ee, eeff_o=eo))
    from . import reference
    if FIELD_GROUPS or reference.FIELD:
        _field_group(g, [m[0].w for m in members], e0.t)
    for k, p in enumerate(g['pairs']):
        cp = Element('coupling', members[k][1], members[k + 1][1], 0.0, layer=e0.layer, w=p['gap'], net=e0.net,
                     label=f"coupled to the next line, gap {p['gap']:.3g} mm",
                     pts=np.array([np.asarray(members[k][0].pts).mean(0), np.asarray(members[k + 1][0].pts).mean(0)]))
        cp.xs, cp.group = 'coupled', gid
        cp.notes.append(f"Ze {p['ze']:.1f} / Zo {p['zo']:.1f} ohm over {e0.length:.2f} mm")
        lm.elements.append(cp)
    lm.groups.append(g)


def _pair_modes(C, Ca, k):
    """(Ze, Zo, eeff_e, eeff_o) of lines k and k + 1 of N, the others held at ground (their mean self terms: a pair
    of slightly unequal widths taken as symmetric)."""
    c11, ca11 = 0.5 * (C[k, k] + C[k + 1, k + 1]), 0.5 * (Ca[k, k] + Ca[k + 1, k + 1])
    out = []
    for s in (1, -1):
        c, ca = c11 + s * C[k, k + 1], ca11 + s * Ca[k, k + 1]
        out.append((1 / (C0 * math.sqrt(c * ca)), c / ca))
    (ze, ee), (zo, eo) = out
    return ze, zo, ee, eo


def _field_group(g, widths, t=0.0):
    """The group's per-unit-length C and C_air (sheets, every line coupled to every other) from the field solver,
    in place of group_lc's nearest-neighbour pairs (fastsolve.group_s uses them when present), and its pairs' even /
    odd modes with the copper's thickness t (ze_sheet, zo_sheet: as sheets), in place of the zero-thickness
    formulas'."""
    h = g['h'] if g['xs'] == 'microstrip' else tuple(g['h'])
    from .xsection import snap                               # similar sections: one solve (xsection.SIMILAR)
    ws, gs = tuple(round(snap(w), 6) for w in widths), tuple(round(snap(s), 6) for s in g['gaps'])
    flip = (ws[::-1], gs[::-1]) < (ws, gs)                   # a group and its mirror image: one solve
    key = (g['xs'],) + ((ws[::-1], gs[::-1]) if flip else (ws, gs)) + (h, g['er'])
    try:
        C, Ca = _group_c(*key)
        Ct, Cat = _group_c(*key, round(t, 6)) if t else (C, Ca)
    except Exception:                                        # noqa: BLE001 (the formulas' pairs stay)
        return
    if flip:                                                 # the matrices of the lines in their own order
        C, Ca, Ct, Cat = (M[::-1, ::-1] for M in (C, Ca, Ct, Cat))
    g['C'], g['Ca'] = C.copy(), Ca.copy()
    for k, p in enumerate(g['pairs']):
        p['ze_sheet'], p['zo_sheet'] = _pair_modes(C, Ca, k)[:2]
        p['ze'], p['zo'], p['eeff_e'], p['eeff_o'] = _pair_modes(Ct, Cat, k)


@functools.lru_cache(maxsize=1024)
def _group_c(xs_name, widths, gaps, h, er, t=0.0):
    """(C, C_air) of side-by-side strips t thick (sheets by default; a board's diff pairs are mostly the same few:
    solved once each)."""
    from . import xsection as X
    if xs_name == 'microstrip':
        xs = X.microstrip(list(widths), h, er, t, gap=list(gaps))
    else:
        xs = X.stripline(list(widths), h[0], h[1], er, t, gap=list(gaps))
    r = xs.solve()
    return r.C, r.Ca


# ---- discontinuities -----------------------------------------------------------------------------
def _out_dir(e, end):
    p = np.asarray(e.pts, float)
    d = (p[1] - p[0]) if end == 0 else (p[-2] - p[-1])
    return d / (np.linalg.norm(d) or 1.0)


def _junctions(lm, model):
    """Discontinuities where trace and pad sections meet: open end, corner (90 degrees), bend (other
    angles: no model), step (width change in line), tee, cross; facing open ends close together are a
    gap. Nodes with a port, a via or a part are left out."""
    arms, busy = {}, set(lm.ports.values())
    for e in lm.elements:
        if e.kind in ('trace', 'pad') and e.pts is not None and len(e.pts) >= 2 and e.length > 1e-6:
            arms.setdefault(e.a, []).append((e, 0))
            arms.setdefault(e.b, []).append((e, 1))
        elif e.kind in ('via', 'part'):
            busy |= {e.a, e.b}
    opens = []
    for node, ar in arms.items():
        if node in busy or node == 'gnd':
            continue
        ends = [np.asarray(e.pts[0] if k == 0 else e.pts[-1], float) for e, k in ar]
        xy = np.mean(ends, axis=0)
        dirs = [_out_dir(e, k) for e, k in ar]
        ws = [e.w for e, _ in ar]
        xs = {e.xs for e, _ in ar}
        rec = dict(node=node, arms=ar, widths=ws, xy=xy, xs=xs.pop() if len(xs) == 1 else 'mixed', layer=ar[0][0].layer)
        n = len(ar)
        if n == 1:
            rec['kind'] = 'open'
            opens.append(rec)
        elif n == 2:
            cosang = float(np.clip(np.dot(dirs[0], dirs[1]), -1, 1))
            bend = 180.0 - math.degrees(math.acos(cosang))
            rec['angle'] = bend
            if abs(bend - 90) <= 10:
                rec['kind'] = 'corner'
            elif bend <= 10:
                if max(ws) / min(ws) <= 1.05:
                    continue
                rec['kind'] = 'step'
            else:
                rec['kind'] = 'bend'
        elif n == 3:
            best = max(((i, j) for i in range(3) for j in range(i + 1, 3)), key=lambda ij: -np.dot(dirs[ij[0]], dirs[ij[1]]))
            k3 = ({0, 1, 2} - set(best)).pop()
            rec['arms'] = [ar[best[0]], ar[best[1]], ar[k3]]
            rec['widths'] = [ar[best[0]][0].w, ar[best[1]][0].w, ar[k3][0].w]
            rec['kind'] = 'tee'
        elif n == 4:
            order = sorted(range(4), key=lambda i: math.atan2(dirs[i][1], dirs[i][0]))
            rec['arms'] = [ar[i] for i in order]
            rec['widths'] = [ar[i][0].w for i in order]
            rec['kind'] = 'cross'
        else:
            rec['kind'] = 'junction'
        lm.junctions.append(rec)
    # facing open ends close together: an end gap
    used = set()
    for i, a in enumerate(opens):
        for b in opens[i + 1:]:
            if id(a) in used or id(b) in used or a['layer'] != b['layer']:
                continue
            ea, ka = a['arms'][0]
            eb, kb = b['arms'][0]
            da, db = -_out_dir(ea, ka), -_out_dir(eb, kb)       # pointing out of the line, past its end
            v = b['xy'] - a['xy']
            g = float(np.linalg.norm(v))
            h = ea.h if isinstance(ea.h, float) else 0.0
            if g < 1e-6 or g > 2 * h or np.dot(da, db) > -0.98 or np.dot(v / g, da) < 0.98:
                continue
            a.update(kind='gap', gap=g, other=b)
            used |= {id(a), id(b)}
    lm.junctions = [r for r in lm.junctions if id(r) not in used or r['kind'] == 'gap']
