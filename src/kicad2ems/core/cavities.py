"""
kicad2ems.core.cavities - the resonances of a board's plane-pair cavities: the
dielectric between two adjacent reference planes (a stripline layer's, a
microstrip's under its plane), its modes set by the planes' shape and the
ground vias that tie them, and the signal vias that drive them.

While the planes are close against the wavelength (d << lambda) the field
between them is the cavity's TEM one: V(x, y), the voltage between the
planes, with

    -div grad V = k^2 V,   k^2 = w^2 mu0 eps0 er (er: the series one of the
                                                   dielectrics between)

over the area both planes cover (their antipads and small holes filled: a hole
changes the plane's inductance little), the edges open (dV/dn = 0: no current
out; their radiation left out). A via joining the two planes (its barrel on
both planes' copper, not in antipads: a ground via) holds V = 0 on its
barrel's disc; a via through the cavity otherwise (a signal via in its
antipads, a via to one plane) is where a current enters it: its disc one voltage, its coupling to a
mode the mode's voltage there. Linear triangles (pcbqs.pdn.mesh: the vias'
discs as rings of the mesh), the consistent mass matrix, the modes below fmax
by shift-invert Lanczos. Each mode's Q from the dielectric's loss tangent and
the planes' skin depth, 1/Q = tan d + delta / d (radiation from the edges not
counted: an upper bound on Q).

What it answers: where a signal via passes a plane pair, the cavity's modes in
the signal's band and the via's coupling to each (|V| at the via over the
mode's RMS: 1 an average place, 2-3 a mode's peak); the via's distance to its
nearest ground via (a return path close to it keeps the cavity's modes away
from it). The segment study's via models leave exactly this out: the physics-
based model's planes are infinite, a meshed model's a box round the via.

    board = load_board('b.kicad_pcb')         # pcbqs._cli: a board.json dict
    for cav in cavities(board):
        res = cav.modes(10e9)                  # dict: f, Q, couplings, ...
    python -m kicad2ems.core.cavities b.kicad_pcb --fmax 10 --out cav
"""
import math
import os
import sys

import numpy as np

from kicad2ems.emsdev.numerics.base import MU0, EPS0

C0 = 1.0 / math.sqrt(MU0 * EPS0)
SIGMA_CU = 5.8e7
# a layer is a reference plane where its ground nets' copper covers this much
# of the outline
PLANE_FRAC = 0.4
# holes in a plane smaller than this (mm2) are filled: antipads, small voids
HOLE_FILL = 2.0
# the mesh's largest cell: this fraction of the wavelength in the dielectric at
# fmax (and never above DMAX_CAP mm)
CELLS_PER_WAVE = 12
DMAX_CAP = 2.0
# the mesh's smallest angle (degrees): the cells graded round the vias' discs.
# Without it a disc's octagon fans out to far vertices and the linear elements
# miss the log field round it: a via's loop inductance to a ground via 0.5-5
# mm away was 36-71 % of the two-wire formula's (2026-11-24)
QUALITY = 30
# each via's disc meshed at this x its radius: the linear elements' field round
# a disc is that of one ~1.16 x wider (per disc, ln(a_eff / a) = 0.14-0.16 over
# radii 0.1-0.25 mm and cells to 2 mm), so meshed smaller it is the barrel's:
# the loop inductance then within 2 % of the two-wire formula's, 0.5-10 mm
RING = math.exp(-0.15)
# ... those discs every via's where a cavity has at most DISCS ground vias;
# past that, every via's that is no ground via and the ground vias' within NEAR
# mm of one. Each other ground via is a vertex alone, where it is (no disc for
# the refinement: on ThunderScope's In4.Cu/B.Cu, 3,211 ground vias, the mesh
# had 268k vertices with every one a graded disc), tied to the planes as a
# well is in a reservoir model (Peaceman): a vertex alone is a disc of radius
# r_eq to the linear elements, ln r_eq = (sum_j w_j ln r_j - theta) / sum_j
# w_j over its neighbours j (w_j = -K_vj, theta its angle), so the barrel's
# radius a < r_eq is the inductance mu0 d / theta ln(r_eq / a) more between
# the vertex and the planes: theta / ln(r_eq / a) on K's diagonal there (V = 0
# where r_eq is not above a). Held at V = 0 alone, such a vertex had been a
# disc of no set radius (ThunderScope's mode lists tens of % off)
DISCS = 400
NEAR = 3.0
# the outline simplified to this (mm) before meshing: its points closer than
# that to a line through their neighbours had the minimum angle refine round
# them (ThunderScope's In4.Cu/B.Cu: 151k unknowns, 78k so, its first six modes
# within 0.6 % of every disc graded on the outline as drawn). Opened and closed
# instead (0.1 mm), its narrow strips went, and the modes that live in them
SMOOTH = 0.02
# at most this many modes a cavity (Lanczos's memory and time)
MAX_MODES = 600
# a via couples to a mode where |V| there >= this x the mode's RMS
STRONG = 1.0
# a mode below this (Hz) is a static one: a part of the cavity with no ground
# via (its planes not tied there), counted apart
STATIC = 10e6
# a via's resonance reported where the cavity's impedance at it peaks at this
# many ohms or more: in series with the via's return path, 10 ohm in a 50 ohm
# line is -0.8 dB at that frequency, 50 ohm -3.5 dB
Z_FLAG = 10.0


def _shape(g):
    from shapely.geometry import shape
    return shape(g)


def coverage(board):
    """{layer: the fraction of the outline its ground nets' copper covers}
    (the board's ground_nets: ground and the power nets it treats as AC
    ground)."""
    from shapely.ops import unary_union
    ground = set(board.get('ground_nets') or ())
    area = _shape(board['outline']).area
    out = {}
    for c in board['stack']['copper']:
        ps = [_shape(p['polygon']) for p in board['copper'].get(c['name'], [])
              if p.get('net') in ground]
        out[c['name']] = unary_union(ps).area / area if ps else 0.0
    return out


def plane_layers(board, frac=PLANE_FRAC, planes=None):
    """The reference planes: copper layers whose ground copper covers `frac`
    of the outline or more (or the layers named in `planes`). [(layer, z)],
    bottom first."""
    cov = coverage(board)
    z = {c['name']: float(c['z']) for c in board['stack']['copper']}
    names = (list(planes) if planes else
             [l for l, c in cov.items() if c >= frac])
    missing = [l for l in names if l not in z]
    if missing:
        raise ValueError(f"no copper layer {', '.join(missing)}")
    return sorted(((l, z[l]) for l in names), key=lambda lz: lz[1])


def _reference(board, layer, hole_fill):
    """(the layer's ground copper, holes under hole_fill mm2 filled; an
    STRtree of its polygons and their nets, for the net at a point)."""
    import shapely
    from shapely.ops import unary_union
    from kicad2ems.pcbqs.pdn.cavity import fill_holes
    ground = set(board.get('ground_nets') or ())
    polys = [(_shape(p['polygon']), p['net'])
             for p in board['copper'].get(layer, []) if p.get('net') in ground]
    geom = fill_holes(unary_union([g for g, _ in polys]).buffer(0), hole_fill)
    return geom, (shapely.STRtree([g for g, _ in polys]), polys)


def _touches(index, x, y, r):
    """Whether a via's barrel (x, y, radius r) touches the layer's reference
    copper: on it, not in an antipad round it (a hole wider than the barrel:
    no contact)."""
    from shapely.geometry import Point
    tree, _ = index
    return bool(len(tree.query(Point(x, y).buffer(0.5 * r, quad_segs=4),
                               predicate='intersects')))


def cavities(board, frac=PLANE_FRAC, hole_fill=HOLE_FILL, planes=None,
             log=print):
    """The board's plane-pair cavities (adjacent reference planes, any signal
    layers between them inside): [Cavity], bottom first."""
    from kicad2ems.pcbqs.pdn.cavity import dielectric
    cov = coverage(board)
    planes = plane_layers(board, frac, planes)
    log("planes: " + ", ".join(f"{l} ({100 * cov[l]:.0f} %)"
                               for l, _ in planes[::-1]))
    refs = {l: _reference(board, l, hole_fill) for l, _ in planes}
    sig = {c['name']: float(c['z']) for c in board['stack']['copper']}
    out = []
    for (la, za), (lb, zb) in zip(planes, planes[1:]):
        geom = refs[la][0].intersection(refs[lb][0]).buffer(0)
        if geom.is_empty or geom.area < 1.0:
            continue
        d, er, tand = dielectric(board, za, zb)
        inside = [l for l, z in sig.items() if za < z < zb]
        out.append(Cavity(board, (la, lb), (za, zb), geom, d, er, tand,
                          (refs[la][1], refs[lb][1]), inside, log=log))
    return out


def ground_spot(cav, x, y, rmin=0.5, rmax=5.0, clear=0.3, step=0.1):
    """The nearest place (x, y, distance mm) from (x, y), rmin to rmax mm
    away, where a ground via's disc of radius `clear` lies on both planes'
    copper (not in an antipad or void, clear of the other vias), or None."""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    if not hasattr(cav, '_both'):
        a, b = (unary_union([g for g, _ in idx[1]]) for idx in cav.index)
        cav._both = a.intersection(b).buffer(0)
    others = [(v['x'], v['y'], v['r']) for v in cav.shorts + cav.ports]
    for rr in np.arange(rmin, rmax + 1e-9, step):
        for t in np.linspace(0, 2 * math.pi, max(24, int(2 * math.pi * rr
                                                           / step)),
                             endpoint=False):
            px, py = x + rr * math.cos(t), y + rr * math.sin(t)
            if not cav._both.contains(Point(px, py).buffer(clear, 8)):
                continue
            if any(math.hypot(px - ox, py - oy) < clear + orr + 0.1
                   for ox, oy, orr in others):
                continue
            return float(px), float(py), float(rr)
    return None


def _well(Kn, nodes, tris, v, a):
    """The conductance (K's units) that ties vertex v to the planes through a
    via of radius a (mm) there: theta / ln(r_eq / a), r_eq the radius the
    linear elements' vertex alone stands for (Peaceman's well model: see
    DISCS); None where r_eq is not above a (hold it at V = 0)."""
    row = Kn.getrow(v)
    j, k = row.indices, row.data
    m = j != v
    j, w = j[m], -k[m]
    r = np.hypot(*(nodes[j] - nodes[v]).T)
    # its angle: the triangles round it
    tv = tris[(tris == v).any(1)]
    theta = 0.0
    for t in tv:
        o = [u for u in t if u != v]
        e1, e2 = nodes[o[0]] - nodes[v], nodes[o[1]] - nodes[v]
        theta += math.acos(max(-1.0, min(1.0, float(
            e1 @ e2 / (np.hypot(*e1) * np.hypot(*e2))))))
    sw = float(w.sum())
    if sw <= 0 or theta <= 0:
        return None
    lnr = (float(w @ np.log(r)) - theta) / sw
    if lnr <= math.log(a) + 0.05:
        return None
    return theta / (lnr - math.log(a))


class Cavity:
    """A plane pair (bottom, top): its geometry (mm), dielectric (d mm, er,
    tand), the signal layers inside it, and the vias through it: shorts (V = 0:
    a via whose barrel touches both planes' copper) and ports (the others)."""

    def __init__(self, board, layers, zs, geom, d, er, tand, index, inside,
                 log=print):
        from shapely.geometry import Point
        self.layers, self.zs, self.geom = layers, zs, geom
        self.d, self.er, self.tand, self.inside = d, er, tand, inside
        self.log, self.index = log, index
        self.name = f"{layers[1]}/{layers[0]}"
        za, zb = zs
        eps = 1e-6
        self.shorts, self.ports = [], []
        area = geom.buffer(1e-6)
        for v in board.get('vias', []):
            if not (v['z0'] < zb - eps and v['z1'] > za + eps):
                continue  # (not into the cavity)
            if not area.contains(Point(v['x'], v['y'])):
                continue
            rec = dict(x=float(v['x']), y=float(v['y']),
                       r=max(float(v['r']), 0.02), net=v.get('net') or '')
            both = (v['z0'] <= za + eps and v['z1'] >= zb - eps)
            if (both and _touches(index[0], v['x'], v['y'], rec['r'])
                    and _touches(index[1], v['x'], v['y'], rec['r'])):
                self.shorts.append(rec)
            else:
                self.ports.append(rec)
        self.mesh = None

    # ---- the mesh and its matrices --------------------------------------
    def build(self, fmax, dmax=None, cells=CELLS_PER_WAVE):
        """Mesh the cavity for modes up to fmax (Hz), `cells` a wavelength
        there (or dmax mm), and assemble K (the stiffness, dimensionless) and
        M (the mass, m2) on the free unknowns: the shorts' discs out (V = 0),
        each port's disc one unknown. M is the consistent and the lumped mass
        averaged: their dispersion errors cancel (a rectangle's 60 modes at
        lambda / 10, 1.1 % high with either alone, 0.03 % so; 2026-11-24)."""
        from scipy.sparse import coo_matrix
        from kicad2ems.pcbqs.pdn.mesh import mesh_sheet, stiffness, mass
        lam = C0 / (fmax * math.sqrt(self.er)) * 1e3  # mm
        dmax = dmax or min(DMAX_CAP, lam / cells)
        near = np.ones(len(self.shorts), bool)
        if len(self.shorts) > DISCS and self.ports:
            from scipy.spatial import cKDTree
            near = cKDTree([(p['x'], p['y']) for p in self.ports]).query(
                [(q['x'], q['y']) for q in self.shorts])[0] <= NEAR
        pts = ([(q['x'], q['y'], RING * q['r'] if n else 0.0)
                for q, n in zip(self.shorts, near)]
               + [(p['x'], p['y'], RING * p['r']) for p in self.ports])
        self.wells = int((~near).sum())
        geom = self.geom.simplify(SMOOTH) if SMOOTH else self.geom
        nodes, tris, rings, _ = mesh_sheet(geom, pts, dmax, quality=QUALITY)
        # (a vertex no triangle has is no unknown: the refinement's stray
        # points at a degenerate corner had left ThunderScope's K + M
        # singular)
        used = np.zeros(len(nodes), bool)
        used[np.asarray(tris).ravel()] = True
        rings = [None if r is None else ([v for v in r if used[v]] or None)
                 for r in rings]
        ns = len(self.shorts)
        dof = -np.ones(len(nodes), int)
        fixed = np.zeros(len(nodes), bool)
        Kn = stiffness(nodes, tris).tocsr()
        well = {}  # vertex -> the conductance its ground vias add (K's)
        for k, r in enumerate(rings[:ns]):
            if r is None:
                continue
            if near[k]:
                fixed[r] = True
                continue
            g = _well(Kn, nodes, tris, r[0], self.shorts[k]['r'])
            if g is None:
                fixed[r[0]] = True
            else:
                well[r[0]] = well.get(r[0], 0.0) + g
        port_dof, nport, off = [], 0, []
        for k, r in enumerate(rings[ns:]):
            if r is None or fixed[r].any():
                port_dof.append(-1)  # (off the mesh, or merged with a short)
                off.append(r is None)
                continue
            off.append(False)
            if (dof[r] >= 0).any():  # (merged with another port's ring)
                port_dof.append(int(dof[r][dof[r] >= 0][0]))
                continue
            # (numbered apart from those off the mesh: numbered by their place
            # in the list, one off the mesh had left an unknown of no vertex)
            dof[r] = nport
            port_dof.append(nport)
            nport += 1
        # (ports first; then every free vertex)
        rest = np.flatnonzero((dof < 0) & ~fixed & used)
        dof[rest] = nport + np.arange(len(rest))
        n = nport + len(rest)
        keep = dof >= 0
        P = coo_matrix((np.ones(int(keep.sum())),
                        (np.flatnonzero(keep), dof[keep])),
                       shape=(len(nodes), n)).tocsr()
        if well:
            from scipy.sparse import diags
            add = np.zeros(len(nodes))
            for v, g in well.items():
                add[v] += g
            Kn = Kn + diags(add)
        self.K = (P.T @ Kn @ P).tocsc()
        self.M = (P.T @ (0.5 * (mass(nodes, tris, lumped=False)
                             + mass(nodes, tris, lumped=True))) @ P
                  ).tocsc() * 1e-6
        self.W = np.asarray(P.T @ mass(nodes, tris).diagonal() * 1e-6)
        self.area = float(self.W.sum())
        self.nodes, self.tris, self.dof, self.n = nodes, tris, dof, n
        self.port_dof = np.array(port_dof, int)
        # a port off the mesh (its disc in no triangle: a void there): its Z
        # is 0 as a short's, which it is not (segment leaves such a cavity out)
        self.port_off = np.array(off, bool)
        self.dmax = dmax
        self.mesh = True
        self.log(f"{self.name}: {self.geom.area:.0f} mm2, d {self.d:.3g} mm, "
                 f"er {self.er:.3g}; {len(self.shorts)} ground vias, "
                 f"{len(self.ports)} other vias through it; "
                 f"{len(nodes):,} vertices (cells to {dmax:.2f} mm)")
        return self

    # ---- the modes ------------------------------------------------------
    def modes(self, fmax, dmax=None, max_modes=MAX_MODES, sigma_cu=SIGMA_CU):
        """The cavity's modes below fmax (Hz): dict(f (Hz), Q, k2 (1/m2),
        V (unknowns x modes, each mode's RMS 1 over the cavity), pr (the
        participation ratio: 1 a mode over the whole cavity, small a local
        one), coupling (ports x modes: |V| at each port's disc, RMS units),
        more (True where max_modes cut the list short))."""
        from scipy.sparse.linalg import eigsh
        if self.mesh is None:
            self.build(fmax, dmax)
        k2max = (2 * math.pi * fmax) ** 2 * MU0 * EPS0 * self.er
        # Weyl's count of a 2D Neumann region's modes below k: area k^2 / 4pi
        # (and a margin)
        guess = int(1.3 * self.area * k2max / (4 * math.pi)) + 12
        nev = int(min(self.n - 2, max_modes, guess))
        if nev < 1:
            return self._empty()
        w, V = eigsh(self.K, k=nev, M=self.M, sigma=-1.0, which='LM')
        o = np.argsort(w)
        w, V = w[o], V[:, o]
        more = bool(nev == max_modes and w[-1] < k2max)
        keep = w <= k2max
        w, V = np.maximum(w[keep], 0.0), V[:, keep]
        # each mode's RMS 1 over the cavity: V^T M V = area
        norm = np.sqrt(np.einsum('im,im->m', V, self.M @ V) / self.area)
        V = V / norm
        f = C0 * np.sqrt(w) / (2 * math.pi * math.sqrt(self.er))
        # the static ones (a part with no ground via) apart
        static = int((f < STATIC).sum())
        f, w, V = f[f >= STATIC], w[f >= STATIC], V[:, f >= STATIC]
        delta = np.sqrt(2.0 / (2 * math.pi * np.maximum(f, 1.0) * MU0
                               * sigma_cu))
        Q = 1.0 / (self.tand + delta / (self.d * 1e-3))
        v2 = V ** 2
        pr = (self.W @ v2) ** 2 / (self.area * (self.W @ v2 ** 2))
        pd = self.port_dof
        cpl = np.zeros((len(pd), len(f)))
        cpl[pd >= 0] = np.abs(V[pd[pd >= 0]])
        # the cavity's impedance at each port at each mode's resonance: its
        # term j w mu0 d phi^2 / (k_m^2 - k^2) of the modal sum (phi^2 = V^2 /
        # area) at k^2 = k_m^2 (1 - j / Q): d V^2 Q / (area w_m eps0 er), ohm
        wm = 2 * math.pi * np.maximum(f, 1.0)
        zpk = (self.d * 1e-3 * cpl ** 2 * Q[None, :]
               / (self.area * wm[None, :] * EPS0 * self.er))
        return dict(f=f, Q=Q, k2=w, V=V, pr=pr, coupling=cpl, z_peak=zpk,
                    static=static, more=more)

    # ---- the impedance between its vias ----------------------------------
    def impedance(self, freqs, which=None, sigma_cu=SIGMA_CU, log=None):
        """The cavity's impedance matrix (nf, k, k) between its ports `which`
        (indices into self.ports; default all) at freqs (Hz): the voltage
        between the planes at port i per current through port j's via (from
        the top plane to the bottom: segment.py's via models' convention),
        the other ports open, the shorts (ground vias) at V = 0:
            Z = j w mu0 d (K - k^2 M)^-1 at the ports' discs,
            k^2 = w^2 mu0 eps0 er (1 - j tan d) (1 - j delta / d)
        (the modal sum of `modes` with every mode, and the losses its Q
        has). A port merged with a short (its disc on one) is 0. The mesh
        `build`'s, for max(freqs) where not built."""
        from scipy.sparse.linalg import splu
        freqs = np.atleast_1d(np.asarray(freqs, float))
        if self.mesh is None:
            self.build(float(freqs.max()))
        which = (np.arange(len(self.ports)) if which is None
                 else np.asarray(which, int))
        pd = self.port_dof[which]
        live = np.flatnonzero(pd >= 0)
        cols = np.unique(pd[live])
        at = np.searchsorted(cols, pd[live])
        E = np.zeros((self.n, len(cols)))
        E[cols, np.arange(len(cols))] = 1.0
        out = np.zeros((len(freqs), len(which), len(which)), complex)
        K, M = self.K.astype(complex), self.M.astype(complex)
        d = self.d * 1e-3
        for i, f in enumerate(freqs):
            w = 2 * math.pi * f
            delta = math.sqrt(2.0 / (w * MU0 * sigma_cu))
            k2 = (w * w * MU0 * EPS0 * self.er * (1 - 1j * self.tand)
                  * (1 - 1j * delta / d))
            X = splu((K - k2 * M).tocsc()).solve(E.astype(complex))
            Zc = 1j * w * MU0 * d * X[cols]  # (cols x cols)
            out[i][np.ix_(live, live)] = Zc[np.ix_(at, at)]
        if log:
            log(f"{self.name}: its impedance between {len(which)} vias at "
                f"{len(freqs)} frequencies ({self.n:,} unknowns)")
        return out

    def _empty(self):
        z = np.zeros(0)
        return dict(f=z, Q=z, k2=z, V=np.zeros((self.n, 0)), pr=z,
                    coupling=np.zeros((len(self.ports), 0)),
                    z_peak=np.zeros((len(self.ports), 0)), static=0,
                    more=False)

    def nearest_short(self):
        """Each port's distance (mm) to the cavity's nearest ground via
        (np.inf: none)."""
        if not self.shorts:
            return np.full(len(self.ports), np.inf)
        from scipy.spatial import cKDTree
        t = cKDTree([(s['x'], s['y']) for s in self.shorts])
        return t.query([(p['x'], p['y']) for p in self.ports])[0] \
            if self.ports else np.zeros(0)

    def vertex_field(self, V, m):
        """Mode m's voltage at every mesh vertex (0 on the shorts)."""
        out = np.zeros(len(self.nodes))
        ok = self.dof >= 0
        out[ok] = V[self.dof[ok], m]
        return out


# ---- a report -----------------------------------------------------------
def study(board, fmax, nets=None, frac=PLANE_FRAC, dmax=None, planes=None,
          suggest=False, log=print):
    """Every cavity's modes and the vias that drive them: [dict(cavity,
    layers, inside, d, er, area, shorts, ports, static, modes [dict(f, Q, pr,
    top [(port index, coupling, z_peak ohm)])], vias [dict(net, x, y,
    nearest_ground_via mm, peaks [(f Hz, z_peak ohm, coupling)] at least
    Z_FLAG, the largest first)])]. nets (fnmatch patterns): the vias reported
    (default every via that is no ground via). suggest: for each via with a
    peak, a ground via at the nearest place on both planes' copper
    (ground_spot), the cavity solved again with all of them: each one's
    'fix' dict(x, y, distance, z_peak, f) (KiCad mm, the peak then)."""
    from fnmatch import fnmatch
    want = (lambda n: any(fnmatch(n, p) for p in nets)) if nets else \
        (lambda n: True)
    out = []
    for cav in cavities(board, frac, planes=planes, log=log):
        res = cav.modes(fmax, dmax)
        near = cav.nearest_short()
        zp, cp = res['z_peak'], res['coupling']
        modes = []
        for m in range(len(res['f'])):
            top = [int(i) for i in np.argsort(-zp[:, m])[:5]
                   if zp[i, m] >= Z_FLAG and want(cav.ports[i]['net'])]
            modes.append(dict(f=float(res['f'][m]), Q=float(res['Q'][m]),
                              pr=float(res['pr'][m]),
                              top=[(i, float(cp[i, m]), float(zp[i, m]))
                                   for i in top]))
        vias = []
        for i, p in enumerate(cav.ports):
            if not want(p['net']):
                continue
            hit = sorted(((float(res['f'][m]), float(zp[i, m]),
                           float(cp[i, m])) for m in range(len(res['f']))
                          if zp[i, m] >= Z_FLAG), key=lambda t: -t[1])
            mx = int(np.argmax(zp[i])) if zp.shape[1] else -1
            vias.append(dict(net=p['net'], x=p['x'], y=-p['y'],
                             nearest_ground_via=float(near[i]), peaks=hit,
                             largest=((float(res['f'][mx]), float(zp[i, mx]))
                                      if mx >= 0 else None)))
        if suggest:
            _suggest(board, cav, vias, fmax, dmax, frac, planes, log)
        out.append(dict(cavity=cav.name, layers=list(cav.layers),
                        inside=cav.inside, d=cav.d, er=cav.er,
                        area=cav.geom.area, shorts=len(cav.shorts),
                        ports=len(cav.ports), static=res['static'],
                        more=res['more'], modes=modes, vias=vias, _cav=cav,
                        _res=res))
        log(f"{cav.name}: {len(modes)} modes below {fmax / 1e9:g} GHz"
            + (f" (the first {res['f'][0] / 1e9:.2f} GHz)" if modes else '')
            + (f", {res['static']} part(s) with no ground via" if
               res['static'] else '')
            + (" - more than MAX_MODES" if res['more'] else ''))
    return out


def _suggest(board, cav, vias, fmax, dmax, frac, planes, log):
    """study's suggest: a ground via beside each via with a peak, the cavity
    again with them, each via's peak then (its 'fix')."""
    hot = [v for v in vias if v['peaks']]
    if not hot:
        return
    tmpl = dict(z0=min(cav.zs) - 1e-3, z1=max(cav.zs) + 1e-3, r=0.15,
                net='GND')
    if cav.shorts:
        tmpl['r'] = cav.shorts[0]['r']
    added, spots = [], {}
    for v in hot:
        sp = ground_spot(cav, v['x'], -v['y'])
        if sp is None:
            continue
        spots[id(v)] = sp
        added.append(dict(tmpl, x=sp[0], y=sp[1]))
    if not added:
        return
    b2 = dict(board, vias=list(board.get('vias', [])) + added)
    two = [c for c in cavities(b2, frac, planes=list(cav.layers),
                               log=lambda *a: None)
           if c.layers == cav.layers]
    if not two:
        return
    c2 = two[0]
    r2 = c2.modes(fmax, dmax)
    at = {(round(p['x'], 4), round(p['y'], 4)): i
          for i, p in enumerate(c2.ports)}
    for v in hot:
        sp = spots.get(id(v))
        i = at.get((round(v['x'], 4), round(-v['y'], 4)))
        if sp is None or i is None:
            continue
        zp = r2['z_peak'][i] if r2['z_peak'].size else np.zeros(1)
        m = int(np.argmax(zp)) if len(zp) else -1
        v['fix'] = dict(x=sp[0], y=-sp[1], distance=sp[2],
                        z_peak=float(zp[m]) if m >= 0 else 0.0,
                        f=float(r2['f'][m]) if m >= 0 and len(r2['f'])
                        else None)
    log(f"{cav.name}: {len(added)} ground via(s) suggested, solved again")


def plot_mode(cav, res, m, path, title=None):
    """Mode m's |V| over the cavity (shorts black dots, ports red crosses),
    KiCad's y down."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.tri import Triangulation
    v = cav.vertex_field(res['V'], m)
    fig = Figure(figsize=(9, 6.5))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    tri = Triangulation(cav.nodes[:, 0], -cav.nodes[:, 1], cav.tris)
    pc = ax.tripcolor(tri, np.abs(v), shading='gouraud', cmap='magma')
    fig.colorbar(pc, ax=ax, shrink=0.8, label='|V| (RMS 1)')
    if cav.shorts:
        s = np.array([(q['x'], -q['y']) for q in cav.shorts])
        ax.plot(s[:, 0], s[:, 1], '.', color='0.6', ms=2, label='ground vias')
    if cav.ports:
        p = np.array([(q['x'], -q['y']) for q in cav.ports])
        ax.plot(p[:, 0], p[:, 1], 'x', color='tab:cyan', ms=3,
                label='other vias')
    ax.set_aspect('equal')
    # a local mode drawn where it is (|V| over a tenth of its peak, 8 mm
    # round), the strongest-coupled via named
    a = np.abs(v)
    at = a >= 0.1 * a.max() if a.max() > 0 else np.zeros(len(a), bool)
    if res['pr'][m] < 0.2 and at.any():
        x, y = cav.nodes[at, 0], -cav.nodes[at, 1]
        ax.set_xlim(x.min() - 8, x.max() + 8)
        ax.set_ylim(y.min() - 8, y.max() + 8)
    col = res['z_peak'][:, m] if res['z_peak'].size else np.zeros(0)
    if len(col) and col.max() > 0:
        i = int(np.argmax(col))
        q = cav.ports[i]
        x0, x1 = ax.get_xlim()
        left = q['x'] > (x0 + x1) / 2   # (the label towards the middle)
        ax.annotate(f"{q['net'].rsplit('/', 1)[-1]}: {col[i]:.0f} ohm",
                    (q['x'], -q['y']), xytext=(-12 if left else 12, 12),
                    textcoords='offset points', color='w', fontsize=8,
                    ha='right' if left else 'left',
                    bbox=dict(boxstyle='round,pad=0.25', fc='0.1', ec='none',
                              alpha=0.75),
                    arrowprops=dict(arrowstyle='-', color='w', lw=0.6))
    ax.invert_yaxis()
    ax.set_xlabel('x (mm)')
    ax.set_ylabel('y (mm)')
    ax.legend(loc='upper right', fontsize=7)
    ax.set_title(title or f"{cav.name}: {res['f'][m] / 1e9:.3f} GHz, "
                 f"Q {res['Q'][m]:.0f}")
    fig.savefig(path, dpi=130, bbox_inches='tight')
    return path


def _echo(rows, fmax, nets, echo):
    for r in rows:
        echo(f"\n{r['cavity']} (inside: {', '.join(r['inside']) or 'none'}; d "
             f"{r['d']:.3g} mm, er {r['er']:.3g}, {r['area']:.0f} mm2, "
             f"{r['shorts']} ground vias, {r['ports']} other vias): "
             f"{len(r['modes'])} modes below {fmax / 1e9:g} GHz"
             + (f", {r['static']} part(s) with no ground via (planes not "
                "tied there)" if r['static'] else ''))
        hot = sorted((v for v in r['vias'] if v['peaks']),
                     key=lambda v: -v['peaks'][0][1])
        if not hot:
            big = sorted((v for v in r['vias'] if v['largest']),
                         key=lambda v: -v['largest'][1])[:3]
            echo(f"  no via sees the cavity above {Z_FLAG:g} ohm at a "
                 "resonance in band" + ("; the largest: " + ", ".join(
                     f"{v['net'].rsplit('/', 1)[-1]} {v['largest'][1]:.2g} "
                     f"ohm at {v['largest'][0] / 1e9:.2f} GHz ("
                     f"{v['nearest_ground_via']:.1f} mm from a ground via)"
                     for v in big) if big else ''))
        for v in hot[:12]:
            f0, z0, c0 = v['peaks'][0]
            echo(f"  {v['net'][:38]:38s} at ({v['x']:.2f}, {v['y']:.2f}) mm: "
                 f"{z0:5.0f} ohm at {f0 / 1e9:.2f} GHz ({len(v['peaks'])} "
                 f"resonance(s) over {Z_FLAG:g} ohm), nearest ground via "
                 f"{v['nearest_ground_via']:.1f} mm")
            fx = v.get('fix')
            if fx:
                echo(f"      a ground via at ({fx['x']:.2f}, {fx['y']:.2f}) "
                     f"mm, {fx['distance']:.1f} mm from it: its largest peak "
                     f"then {fx['z_peak']:.0f} ohm"
                     + (f" at {fx['f'] / 1e9:.2f} GHz" if fx['f'] else ''))
        if len(hot) > 12:
            echo(f"  ... {len(hot) - 12} more vias")


def parser():
    import argparse
    p = argparse.ArgumentParser(
        prog='python -m kicad2ems.core.cavities',
        description=__doc__.strip().split('\n\n')[0])
    p.add_argument('board', help='.kicad_pcb, or a board.json')
    p.add_argument('--fmax', type=float, default=10.0, help='GHz')
    p.add_argument('--nets', default=None,
                   help='the vias reported: fnmatch patterns, comma-separated '
                   '(default: every via that is no ground via)')
    p.add_argument('--dmax', type=float, default=None,
                   help='the mesh\'s largest cell, mm (default: a twelfth of '
                   'the wavelength at fmax, at most 2)')
    p.add_argument('--plane-frac', type=float, default=PLANE_FRAC,
                   help='a reference plane: its ground copper this fraction '
                   'of the outline or more')
    p.add_argument('--planes', default=None,
                   help='the reference planes by name, comma-separated (in '
                   'place of --plane-frac)')
    p.add_argument('--plots', type=int, default=3,
                   help='each cavity\'s lowest modes with a reported via '
                   'strong in them drawn (PNG)')
    p.add_argument('--suggest', action='store_true',
                   help='for each via with a peak, a ground via at the '
                   'nearest place on both planes\' copper, and the peak then '
                   '(the cavity solved again with them). The two planes '
                   'alone are checked: a through via must clear every other '
                   'layer too')
    p.add_argument('--out', default='cavities')
    return p


def main(argv=None):
    import json
    a = parser().parse_args(argv)
    from kicad2ems.pcbqs._cli import load_board
    board = load_board(a.board)
    nets = [n for n in (a.nets or '').split(',') if n.strip()] or None
    fmax = a.fmax * 1e9
    planes = [l for l in (a.planes or '').split(',') if l.strip()] or None
    rows = study(board, fmax, nets, a.plane_frac, a.dmax, planes,
                 a.suggest)
    os.makedirs(a.out, exist_ok=True)
    _echo(rows, fmax, nets, print)
    for r in rows:
        cav, res = r.pop('_cav'), r.pop('_res')
        drawn = 0
        for m, mode in enumerate(r['modes']):
            if drawn >= a.plots or not mode['top']:
                continue
            safe = r['cavity'].replace('/', '_').replace('.', '')
            path = os.path.join(a.out, f"{safe}_{mode['f'] / 1e9:.2f}GHz.png")
            plot_mode(cav, res, m, path)
            mode['plot'] = os.path.basename(path)
            drawn += 1
    with open(os.path.join(a.out, 'cavities.json'), 'w') as fh:
        json.dump(dict(board=os.path.basename(a.board), fmax=fmax,
                       cavities=rows), fh, indent=1)
    print(f"\ncavities -> {os.path.join(a.out, 'cavities.json')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
