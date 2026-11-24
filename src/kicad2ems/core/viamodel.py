"""
kicad2ems.core.viamodel - a physics-based model of a group of vias between
reference planes (segment.py's via models without a mesh; prototype,
2026-11-20).

The planes are the circuit's ground. Each signal via is a chain of nodes, one
at every plane it passes through and every signal layer it has a pad on:

  - at each node a capacitance to the planes (the barrel, its pads and the
    antipads: an axisymmetric electrostatic solve per via, `capacitances`), each
    plane's share at the node of the via nearest it;
  - between two nodes inside a cavity (two adjacent planes, height d) a series
    element whose voltage is the cavity's: the parallel-plate (TEM) Green's
    function w u d/4 H0^(2)(k rho) couples every via crossing the cavity, a
    segment of height h weighted h/d (a signal layer inside the cavity splits
    the via there: stripline);
  - between an outer pad and the plane under it, the barrel's inductance;
  - a ground via joining the two planes of a cavity shorts it there (its
    voltage 0): the return current;
  - each port (a trace cut at segment.py's circle) at its layer's node, through
    its stub (the trace from the cut to the pad) as a transmission line.

The planes are infinite (no edge, no cavity resonance of a finite board), a
cavity carries its TEM mode alone (thin against the wavelength), losses are the
dielectric's. Unknowns: the vias' nodes and segments, a few hundred at most.

A cavity named in build's `cavities` (a pair of plane layers: segment.py's
--cavities, the board's plane pair solved whole by core.cavities) is the
model's no longer: each of its segments a port instead (its + at the upper
node, the voltage across it the cavity's at the via, its current the barrel's
from the upper plane to the lower), and the box's ground vias in it not
modelled (the board's cavity holds every one). The join then puts the board's
cavity there: its resonances, its coupling to every other via model's vias.
"""
import math

import numpy as np

MU0, EPS0 = 4e-7 * math.pi, 8.8541878128e-12
C0 = 1 / math.sqrt(MU0 * EPS0)


# ---- the board file ----------------------------------------------------------
def _layers(d):
    """{copper layer: z (mm)} and the dielectrics [(z0, z1, er, tand)]."""
    st = d['stack']
    cz = {c['name']: float(c['z']) for c in st['copper']}
    diel = [(float(l['z0']), float(l['z1']), float(l['er']),
             float(l.get('tand') or 0.0)) for l in st['layers']]
    return cz, diel


def _polys(d, layer, nets):
    from shapely.geometry import shape
    return [shape(c['polygon']) for c in d['copper'].get(layer, [])
            if c['net'] in nets]


def planes_of(d, frac=0.5):
    """The reference planes: layers whose ground copper covers `frac` of the
    outline or more, by z (top first)."""
    from shapely.geometry import shape
    cz, _ = _layers(d)
    ground = set(d.get('ground_nets') or ())
    area = shape(d['outline']).area
    out = [l for l in cz if sum(p.area for p in _polys(d, l, ground))
           >= frac * area]
    return sorted(out, key=lambda l: -cz[l])


def eps_between(diel, za, zb):
    """The complex permittivity a vertical field sees between za < zb (mm):
    the layers in series."""
    s, tot = 0.0, 0.0
    for z0, z1, er, tand in diel:
        lo, hi = max(z0, za), min(z1, zb)
        if hi > lo:
            s += (hi - lo) / (er * (1 - 1j * tand))
            tot += hi - lo
    return (tot / s) if s else 1.0 + 0j


# ---- the capacitances: an axisymmetric electrostatic solve ------------------
def capacitances(a, pads, planes, diel, span, dr=0.02, dz=0.01, rmax=None,
                 air=1.0):
    """Capacitance (F) from a via (barrel radius a over span (z0, z1), pads
    {z: radius}) to each plane [(z, hole radius or None: a full plane)] (mm):
    {plane z: C}. Finite volumes on (r, z), r symmetric about the axis."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    holes = [b for _, b in planes if b]
    R = rmax or max(3.0, 6 * max(holes + [a] + list(pads.values())))
    zs_all = [z for z, _ in planes] + list(pads) + list(span)
    zlo, zhi = min(zs_all) - air, max(zs_all) + air
    nr, nz = int(round(R / dr)) + 1, int(round((zhi - zlo) / dz)) + 1
    r = np.arange(nr) * dr
    z = zlo + np.arange(nz) * dz
    kz = lambda zz: int(round((zz - zlo) / dz))
    # the permittivity at each z (the layer the point is in; air outside)
    def er_at(zz):
        for z0, z1, er, _ in diel:
            if z0 - 1e-9 <= zz <= z1 + 1e-9:
                return er
        return 1.0
    ez = np.array([er_at(zz) for zz in z])
    # conductors: 0 the via (barrel + pads), 1.. the planes
    cond = -np.ones((nr, nz), int)
    k0, k1 = kz(span[0]), kz(span[1])
    cond[r <= a + 1e-12, min(k0, k1):max(k0, k1) + 1] = 0
    for zp, rp in pads.items():
        cond[r <= rp + 1e-12, kz(zp)] = 0
    for i, (zp, b) in enumerate(planes, 1):
        m = r >= (b if b else -1.0) - 1e-12
        cond[m, kz(zp)] = i
    idx = np.arange(nr * nz).reshape(nr, nz)
    rows, cols, vals = [], [], []
    # r faces: between (i, j) and (i + 1, j): eps at z_j, area 2 pi r_f dz
    rf = (r[:-1] + r[1:]) / 2
    g_r = EPS0 * ez[None, :] * 2 * np.pi * rf[:, None] * 1e-3 * dz / dr
    # z faces: between (i, j) and (i, j + 1): eps at the face, area of ring i
    ezf = 2 / (1 / ez[:-1] + 1 / ez[1:])
    ring = np.where(r > 0, 2 * np.pi * r * dr, np.pi * (dr / 2) ** 2)
    g_z = EPS0 * ezf[None, :] * ring[:, None] * 1e-3 / dz
    for (i0, j0, i1, j1, g) in ((slice(0, -1), slice(None), slice(1, None),
                                 slice(None), g_r),
                                (slice(None), slice(0, -1), slice(None),
                                 slice(1, None), g_z)):
        a_, b_ = idx[i0, j0].ravel(), idx[i1, j1].ravel()
        gg = g.ravel()
        rows += [a_, b_, a_, b_]
        cols += [a_, b_, b_, a_]
        vals += [gg, gg, -gg, -gg]
    N = nr * nz
    A = sparse.csr_matrix((np.concatenate(vals),
                           (np.concatenate(rows), np.concatenate(cols))),
                          shape=(N, N))
    c = cond.ravel()
    free = np.flatnonzero(c < 0)
    fixed = np.flatnonzero(c >= 0)
    phi = np.zeros(N)
    phi[fixed] = (c[fixed] == 0).astype(float)
    rhs = -(A[free][:, fixed] @ phi[fixed])
    phi[free] = spsolve(A[free][:, free].tocsc(), rhs)
    q = A @ phi  # the charge on each node (F x V)
    return {zp: float(-q[c == i].sum()) for i, (zp, _) in
            enumerate(planes, 1)}


# ---- the model ---------------------------------------------------------------
def _hole(d, layer, ground, x, y):
    """The hole radius round (x, y) in a plane's ground copper (mm), None
    where the copper covers it (the via joined to the plane)."""
    from shapely.geometry import Point
    pt = Point(x, y)
    ps = _polys(d, layer, ground)
    if any(p.covers(pt) for p in ps):
        return None
    return min((p.distance(pt) for p in ps), default=None)


def build(d, vias, cavities=()):
    """The model of a via piece's board file d (segment._to_dict's) and its
    signal vias [dict(x, y, net, pad, r)] (segment.via_groups'): a dict the
    solve takes. mm throughout. cavities: plane-layer pairs whose cavity is
    left to the join, each segment in it a port (the module's docstring)."""
    cz, diel = _layers(d)
    ground = set(d.get('ground_nets') or ())
    planes = planes_of(d)
    pz = {l: cz[l] for l in planes}
    sig = []
    for v in vias:
        # its record in the board file: the barrel's radius, its span
        rec = min((u for u in d['vias'] if u['net'] == v['net']),
                  key=lambda u: math.hypot(u['x'] - v['x'], u['y'] - v['y']))
        z0, z1 = sorted((float(rec['z0']), float(rec['z1'])))
        a = float(rec['r'])
        # its pads: the layers in its span where its net's copper covers it
        from shapely.geometry import Point
        pads = {}
        for l, zl in cz.items():
            if l in planes or not (z0 - 1e-6 <= zl <= z1 + 1e-6):
                continue
            if any(p.distance(Point(v['x'], v['y'])) < 1e-6
                   for p in _polys(d, l, {v['net']})):
                pads[l] = float(v.get('pad') or a)
        crossed = [l for l in planes if z0 + 1e-6 < pz[l] < z1 - 1e-6]
        holes = {l: _hole(d, l, ground, v['x'], v['y']) for l in planes}
        sig.append(dict(x=v['x'], y=v['y'], net=v['net'], a=a, z0=z0, z1=z1,
                        pads=pads, crossed=crossed, holes=holes))
    gv = []
    for u in d['vias']:
        if u['net'] not in ground:
            continue
        z0, z1 = sorted((float(u['z0']), float(u['z1'])))
        joined = [l for l in planes if z0 - 1e-6 <= pz[l] <= z1 + 1e-6
                  and _hole(d, l, ground, u['x'], u['y']) is None]
        gv.append(dict(x=u['x'], y=u['y'], a=float(u['r']), joined=joined))
    # each signal via's capacitance to every plane
    for s in sig:
        pl = [(pz[l], s['holes'][l]) for l in planes]
        C = capacitances(s['a'], {cz[l]: rp for l, rp in s['pads'].items()},
                         pl, diel, (s['z0'], s['z1']))
        s['C'] = {l: C[pz[l]] for l in planes}
    # the ports: each at the node of its via on its layer, through its stub
    ports = []
    for p in d['ports']:
        k = min(range(len(sig)), key=lambda i: (sig[i]['net'] != p['net'],
                math.hypot(sig[i]['x'] - p['c'][0], sig[i]['y'] - p['c'][1])))
        s = sig[k]
        stub = max(0.0, math.hypot(s['x'] - p['c'][0], s['y'] - p['c'][1])
                   - s['pads'].get(p['layer'], s['a']))
        ports.append(dict(name=p['name'], via=k, layer=p['layer'],
                          w=float(p['w']), stub=stub,
                          line=_line(p['layer'], float(p['w']), cz, pz,
                                     diel)))
        s['pads'].setdefault(p['layer'], s['a'])
    return dict(cz=cz, diel=diel, planes=planes, pz=pz, sig=sig, gv=gv,
                ports=ports, expose={frozenset(c) for c in cavities})


def _line(layer, w, cz, pz, diel):
    """(Z0, eeff, tand) of a trace of width w on `layer` between its nearest
    planes (microstrip over one, stripline between two)."""
    from .lines.formulas import microstrip, offset_stripline
    z = cz[layer]
    above = [pz[l] - z for l in pz if pz[l] > z + 1e-6]
    below = [z - pz[l] for l in pz if pz[l] < z - 1e-6]
    if above and below:
        h1, h2 = min(below), min(above)
        e = eps_between(diel, z - h1, z + h2)
        z0, ee = offset_stripline(w, h1, h2, e.real)
    else:
        h = min(above or below)
        e = eps_between(diel, z - h, z) if below else eps_between(
            diel, z, z + h)
        z0, ee = microstrip(w, h, e.real)
    return z0, ee, -e.imag / e.real


def _outer_l(h, a):
    """The inductance (H) of a barrel of radius a rising h (mm) from a plane:
    a wire of length h and its image."""
    x = 2 * h / a
    return MU0 * h * 1e-3 / (2 * math.pi) * max(
        0.0, math.asinh(x) - math.sqrt(1 + 1 / x ** 2) + 1 / x)


def solve(model, freqs, z0=50.0):
    """S (nf, n, n) at z0 of the model's ports, and their names: the trace
    ports, then a port for each segment in a cavity left to the join
    (model['cavity_ports'] then lists them: dict(name, cavity (the upper
    plane, the lower), via (its index in model['sig']), x, y, w (its height's
    share of the cavity's)))."""
    from scipy.special import hankel2
    from .deembed import abcd_to_s, connect, line_abcd
    sig, gv, pz, cz = model['sig'], model['gv'], model['pz'], model['cz']
    planes = model['planes']
    # each signal via's nodes: its levels, top first
    nodes, segs = [], []
    for i, s in enumerate(sig):
        lv = sorted({cz[l] for l in s['pads']} | {pz[l] for l in s['crossed']},
                    reverse=True)
        s['nodes'] = {zl: len(nodes) + k for k, zl in enumerate(lv)}
        nodes += [(i, zl) for zl in lv]
        for zu, zl in zip(lv[:-1], lv[1:]):
            segs.append(dict(via=i, up=s['nodes'][zu], lo=s['nodes'][zl],
                             zu=zu, zl=zl))
    zp_sorted = sorted(pz.values(), reverse=True)

    def region(zu, zl):
        """(top plane z, bottom plane z) of the cavity holding (zl, zu), or
        None (outside the planes)."""
        for hi, lo in zip(zp_sorted[:-1], zp_sorted[1:]):
            if lo - 1e-9 <= zl and zu <= hi + 1e-9:
                return hi, lo
        return None
    for sg in segs:
        sg['cav'] = region(sg['zu'], sg['zl'])
    zname = {z: l for l, z in pz.items()}
    expose = model.get('expose') or set()
    joined = lambda cav: (cav is not None
                          and frozenset((zname[cav[0]], zname[cav[1]]))
                          in expose)
    # ground vias: a shorted segment in each cavity whose two planes they join
    # (not in one left to the join: the board's cavity has them all)
    gsegs = []
    for gi, g in enumerate(gv):
        jz = {pz[l] for l in g['joined']}
        for hi, lo in zip(zp_sorted[:-1], zp_sorted[1:]):
            if hi in jz and lo in jz and not joined((hi, lo)):
                gsegs.append(dict(g=gi, cav=(hi, lo)))
    # the segments in a cavity left to the join: a port each
    cports = [k for k, sg in enumerate(segs) if joined(sg['cav'])]
    model['cavity_ports'] = []
    for k in cports:
        sg = segs[k]
        up, low = zname[sg['cav'][0]], zname[sg['cav'][1]]
        model['cavity_ports'].append(dict(
            name=f"cav_{up}_{low}_v{sg['via']}_{k}", cavity=(up, low),
            via=sg['via'], x=sig[sg['via']]['x'], y=sig[sg['via']]['y'],
            w=(sg['zu'] - sg['zl']) / (sg['cav'][0] - sg['cav'][1])))
    # each node's capacitance: a plane's share at the via's node nearest it
    Cn = np.zeros(len(nodes))
    for i, s in enumerate(sig):
        for l in planes:
            zl = pz[l]
            n = min(s['nodes'].items(), key=lambda kv: abs(kv[0] - zl))[1]
            Cn[n] += s['C'][l]
    nn, ns, ng = len(nodes), len(segs), len(gsegs)
    N = nn + ns + ng
    ports = model['ports']
    pn = [sig[p['via']]['nodes'][cz[p['layer']]] for p in ports]
    npt = len(ports) + len(cports)
    out = np.empty((len(freqs), npt, npt), complex)
    # positions and radii of everything that crosses each cavity
    cavs = sorted({sg['cav'] for sg in segs if sg['cav']} |
                  {gs['cav'] for gs in gsegs}, reverse=True)
    for fi, f in enumerate(freqs):
        w = 2 * math.pi * f
        A = np.zeros((N, N), complex)
        # KCL at each node: in from above - out below - jwC V + J = 0
        for k, sg in enumerate(segs):
            A[sg['up'], nn + k] -= 1.0
            A[sg['lo'], nn + k] += 1.0
        A[np.arange(nn), np.arange(nn)] -= 1j * w * Cn
        # KVL per segment: V_up - V_lo - EMF = 0
        for k, sg in enumerate(segs):
            A[nn + k, sg['up']] += 1.0
            A[nn + k, sg['lo']] -= 1.0
        # the EMFs: cavities' couplings and the outer inductances
        for hi, lo in cavs:
            if joined((hi, lo)):
                continue  # (the join's: its segments are ports)
            dmm = hi - lo
            e = eps_between(model['diel'], lo, hi)
            kk = w * math.sqrt(MU0 * EPS0) * np.sqrt(e)
            members = ([(nn + k, sig[sg['via']], (sg['zu'] - sg['zl']) / dmm)
                        for k, sg in enumerate(segs) if sg['cav'] == (hi, lo)]
                       + [(nn + ns + k, gv[gs['g']], 1.0)
                          for k, gs in enumerate(gsegs)
                          if gs['cav'] == (hi, lo)])
            # w mu d / 4 H0^(2)(k rho): its real part the power the via sends
            # into the cavity (w mu d / 4 J0, positive), its imaginary part
            # inductive (-Y0 > 0 near the barrel); with j in front the real
            # part was w mu d / 4 Y0 < 0, a negative resistance (|S21| 1.12)
            pre = w * MU0 * dmm * 1e-3 / 4
            for (ra, va, ha) in members:
                for (rb, vb, hb) in members:
                    rho = (math.hypot(va['x'] - vb['x'], va['y'] - vb['y'])
                           if va is not vb else va['a']) * 1e-3
                    A[ra, rb] -= ha * hb * pre * hankel2(0, kk * rho)
        for k, sg in enumerate(segs):
            if sg['cav'] is None:
                A[nn + k, nn + k] -= 1j * w * _outer_l(
                    sg['zu'] - sg['zl'], sig[sg['via']]['a'])
        # every port terminated in z0 and driven by a unit current in turn:
        # the loaded port voltages Z_L, S = 2 Z_L / z0 - I (a node between two
        # cavity ports has no other path: open-circuit Z has none there)
        G = 1.0 / z0
        B = np.zeros((N, npt), complex)
        for k, n in enumerate(pn):
            A[n, n] -= G
            B[n, k] = -1.0  # (KCL row: ... + J = 0 -> A x = -J)
        for j, k in enumerate(cports):
            up, lo = segs[k]['up'], segs[k]['lo']
            A[nn + k, :] = 0.0
            A[nn + k, nn + k] = 1.0  # (its barrel open: the port carries it)
            A[up, up] -= G
            A[up, lo] += G
            A[lo, lo] -= G
            A[lo, up] += G
            B[up, len(ports) + j] = -1.0
            B[lo, len(ports) + j] = 1.0
        X = np.linalg.solve(A, B)
        ZL = np.concatenate([X[pn, :]] + [
            X[[segs[k]['up']], :] - X[[segs[k]['lo']], :] for k in cports])
        out[fi] = 2.0 / z0 * ZL - np.eye(npt)
    # each port's stub, from the cut to the pad
    S = out
    for k, p in enumerate(ports):
        zc, ee, tand = p['line']
        G = abcd_to_s(line_abcd(zc, ee, p['stub'] * 1e-3, np.asarray(freqs),
                                tand), z0)
        S = connect(S, k, G)
    return S, ([p['name'] for p in ports]
               + [c['name'] for c in model['cavity_ports']])
