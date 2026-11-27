"""
The per-unit-length R(f), L(f), G(f) and C(f) of an N-conductor cross-section
(xsection.CrossSection): the series impedance from a 2D magnetoquasistatic
(eddy-current) solve with the copper meshed inside, so R and L have the skin
effect, the proximity effect between neighbours and the return current's own
crowding in the planes and the coplanar ground; G and C from xsection's
electrostatic solve.

    xs = X.coupled_microstrip(0.15, 0.15, 0.2, 4.3, t=0.035)
    r = rlgc(xs, [0, 1e6, 1e8, 1e9, 1e10])   # RLGC: f, R, L, G, C (per metre)
    r.R[-1]                                    # (N, N) ohm/m at 10 GHz
    r.L_inf                                    # mu0 eps0 C_air^-1, H/m

The field is A_z(x, z), the magnetic vector potential along the lines:

    -div(grad A / mu0) = J,   J = sigma (E_g - j w A) in conductor g

E_g, the field along conductor g (its voltage drop per metre), is one number
per conductor: each signal conductor's own, and one for all the ground copper
together (the planes, coplanar ground, other layers' copper: one net, joined at
the line's ends). Each conductor's total current is imposed, I_g = int sigma
(E_g - j w A) dS: the solve gives E for given I (the current-driven form), and
Z, the N x N series impedance with the ground as the return, is

    -dV_k/dl = E_k - E_ground = sum_j Z_kj I_j,  Z = R + j w L.

Bilinear rectangles (xsection's elements and hanging-node reduction) on a mesh
of its own: every face of the copper is a key line, and cells inside the
copper are halved across a face until they are SKIN_CELL skin depths thick at
it (at the highest frequency asked), growing by SKIN_GROW per unit of distance
from it, along the face as across it (a face's current changes along it on the
scale of the distance to the nearest edge); in the air, cells grow from the
copper's corners as xsection's do. One mesh for every frequency: K + j w M
factored at each (complex symmetric with a positive definite real part, so no
pivoting; the frequencies on THREADS threads), the conductors' constraint
closed in an (N+1) x (N+1) system. At DC the current is uniform in each
conductor: R from the areas, L from one magnetostatic solve.

rlgc()'s L is xsection's L_inf (mu0 eps0 C_air^-1, extrapolated from two meshes
as its C is) plus the internal inductance, this solve's L(f) less its own
perfect-conductor limit on the same mesh (pec_inductance): the two share their
air mesh's error, so the difference converges on a mesh that is coarse in the
air, and the lossless line's Z0 and delay are the field solver's.

The geometry: a plane of the CrossSection (a full-width sheet at a height,
its face to the lines) is copper `plane_t` mm thick behind that face, across
the box; a conductor of zero thickness (another layer's copper as a sheet, a
strip given as a sheet) gets `sheet_t` mm, grown away from the signal strips.
Copper beyond the nearest plane below and above the signal strips is left out
(the planes shield it from the lines at any frequency where their thickness is
a few skin depths; below that, its share of the return is that of its
resistance, and the planes' own resistance across the box's width is already
small). The box reaches REACH x the copper's size beyond it on every side, A =
0 on its walls; a plane's current spreads over the box's width at DC, so the
lowest frequencies' L (below where the return gathers under the line, about
R / (2 pi L) of the plane: kHz on 35 um copper) is that of a plane as wide as
the box. mu = mu0 everywhere (no magnetic material).

Roughness (rough, um RMS): the conductivity at each frequency is sigma / K(f)^2
(core.roughness: Hammerstad's factor on the skin-effect loss), so the internal
inductance grows with the loss as a causal rough-surface model has it.

Units: mm for the geometry, SI for the results (per metre).
"""
import math
from dataclasses import dataclass, field

import numpy as np

from ..constants import MU0
from . import xsection as X

__all__ = ['RLGC', 'rlgc', 'series_z', 'Copper', 'geometry', 'skin_depth']

SIGMA_CU = 5.8e7
# the cell at a copper face, in skin depths at the highest frequency
SKIN_CELL = 1 / 3
# cells in the copper grow by this x their distance from the nearest face
SKIN_GROW = 0.35
# the cell at a face, as a fraction of the copper's smallest size, where that
# is smaller than the skin's (low frequencies: the current fills the copper)
CU_FINE = 1 / 8
# air: the cell at a corner as a fraction of the feature there, and its growth
# with the distance from the corner (xsection's MESH_FINE, MESH_GROW)
AIR_FINE = 1 / 32
AIR_GROW = 0.4
# a cell larger than this x the size it may have is halved
SPLIT = 1.5
# the box: this many x the copper's size beyond it (xsection's)
REACH = X.REACH
# default copper thickness of planes and sheets, mm
COPPER_T = 0.035
# the threads a frequency sweep is solved on (None: one per performance core)
THREADS = None


def skin_depth(f, sigma=SIGMA_CU):
    """The skin depth (m) at f (Hz) in copper of conductivity sigma."""
    return 1.0 / np.sqrt(np.pi * np.asarray(f, float) * MU0 * sigma)


@dataclass
class Copper:
    """The copper of an MQS solve: rectangles (n, 4: x0, x1, z0, z1, mm), the
    group of each (0 ground, k signal conductor k - 1, as the CrossSection
    numbers them), the box (xl, xr, zb, zt)."""
    rects: np.ndarray
    group: np.ndarray
    box: tuple
    notes: list = field(default_factory=list)

    @property
    def n(self):
        return int(self.group.max())


# ---- the geometry -----------------------------------------------------------
def _thick(spec, z):
    """A thickness (mm) from spec: a number, or {height: thickness} (the
    nearest height within 1 nm; else its 'default', or COPPER_T)."""
    if not isinstance(spec, dict):
        return float(spec)
    for k, v in spec.items():
        if k != 'default' and abs(float(k) - z) < 1e-6:
            return float(v)
    return float(spec.get('default', COPPER_T))


def geometry(xs, plane_t=COPPER_T, sheet_t=COPPER_T):
    """The Copper of a CrossSection: its signal and ground conductors, its
    planes as copper plane_t thick behind their faces, zero-thickness
    conductors sheet_t thick (grown away from the signal strips), clipped to
    the box; copper beyond the planes nearest the signal strips left out.
    plane_t, sheet_t: mm, or {height: mm} (each plane's or sheet's layer,
    by the height the CrossSection has it at; 'default' for the rest)."""
    sig = xs.signals
    if not sig:
        raise ValueError("a cross-section needs a signal conductor")
    if min(_thick(plane_t, p) for p in list(xs.planes) or [0.0]) <= 0 or \
            _thick(sheet_t, 0.0) <= 0:
        raise ValueError("the copper needs a thickness for its resistance")
    s_lo = min(min(c.z0, c.z1) for c in sig)
    s_hi = max(max(c.z0, c.z1) for c in sig)
    s_mid = 0.5 * (s_lo + s_hi)
    below = [p for p in xs.planes if p <= s_lo + 1e-12]
    above = [p for p in xs.planes if p >= s_hi - 1e-12]
    between = [p for p in xs.planes if s_lo + 1e-12 < p < s_hi - 1e-12]
    if between:
        raise ValueError(
            f"a plane at z = {between[0]:g} mm lies between the signal "
            "conductors: each side is a cross-section of its own")
    zlo = max(below) if below else -math.inf
    zhi = min(above) if above else math.inf
    notes = []
    rects, groups = [], []
    k = 0
    dropped = 0
    for c in xs.conductors:
        z0, z1 = c.z0, c.z1
        if not c.ground:
            k += 1
        if z1 - z0 < 1e-9:
            # a sheet: its copper grown away from the signal strips (a ground
            # piece), or away from the nearer plane (a strip)
            if c.ground:
                up = z0 >= s_mid
            else:
                up = z0 - zlo <= zhi - z0
            ts = _thick(sheet_t, z0)
            z0, z1 = (z0, z0 + ts) if up else (z0 - ts, z0)
        if c.ground and (z1 <= zlo + 1e-9 or z0 >= zhi - 1e-9):
            dropped += 1
            continue
        rects.append([c.x0, c.x1, z0, z1])
        groups.append(0 if c.ground else k)
    if dropped:
        notes.append(f"{dropped} piece(s) of ground copper beyond the planes "
                     "left out (shielded by them)")
    finite = [v for r in rects for v in r[:2] if math.isfinite(v)]
    zs = [v for r in rects for v in r[2:]]
    for p in (zlo, zhi):
        if math.isfinite(p):
            tp = _thick(plane_t, p)
            zs += [p - tp if p <= s_lo else p + tp, p]
    xa, xb = min(finite), max(finite)
    za, zb_ = min(zs), max(zs)
    H = max(zb_ - za, xb - xa, 1e-3)
    box = (xa - REACH * H, xb + REACH * H, za - REACH * H, zb_ + REACH * H)
    for p in (zlo, zhi):
        if math.isfinite(p):
            tp = _thick(plane_t, p)
            z0, z1 = (p - tp, p) if p <= s_lo else (p, p + tp)
            rects.append([box[0], box[1], z0, z1])
            groups.append(0)
    R = np.array(rects, float)
    R[:, 0] = np.maximum(R[:, 0], box[0])
    R[:, 1] = np.minimum(R[:, 1], box[1])
    cu = Copper(R, np.array(groups, int), box, notes)
    _check_apart(cu)
    return cu


def _check_apart(cu):
    """Distinct conductors must not touch (a signal on ground: one conductor)."""
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union
    by = {}
    for r, g in zip(cu.rects, cu.group):
        by.setdefault(int(g), []).append(sbox(r[0], r[2], r[1], r[3]))
    u = {g: unary_union(v) for g, v in by.items()}
    keys = sorted(u)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if u[a].distance(u[b]) < 1e-9:
                what = lambda g: 'ground' if g == 0 else f"conductor {g}"
                raise ValueError(f"{what(a)} touches {what(b)}")


def _faces(cu):
    """(horizontal, vertical) faces of the copper's outline, each (n, 3): z, x0,
    x1 and x, z0, z1 (mm); and its corners (n, 2)."""
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union
    u = unary_union([sbox(r[0], r[2], r[1], r[3]) for r in cu.rects])
    polys = getattr(u, 'geoms', [u])
    hor, ver, corners = [], [], []
    for p in polys:
        for ring in [p.exterior] + list(p.interiors):
            c = np.asarray(ring.coords)
            for (x0, z0), (x1, z1) in zip(c[:-1], c[1:]):
                if abs(z1 - z0) < 1e-12 and abs(x1 - x0) > 1e-12:
                    hor.append((z0, min(x0, x1), max(x0, x1)))
                elif abs(x1 - x0) < 1e-12 and abs(z1 - z0) > 1e-12:
                    ver.append((x0, min(z0, z1), max(z0, z1)))
            corners += [tuple(q) for q in c[:-1]]
    return (np.array(hor, float).reshape(-1, 3),
            np.array(ver, float).reshape(-1, 3),
            np.unique(np.round(np.array(corners, float), 12), axis=0))


def _gap(c0, c1, a0, a1):
    """Distance between intervals [c0, c1] (arrays, n) and [a0, a1] (m): (n,
    m)."""
    return np.maximum(np.maximum(a0[None, :] - c1[:, None],
                                 c0[:, None] - a1[None, :]), 0.0)


def _face_size(cell, faces, along, h0, grow, chunk=4096):
    """The size each cell may have across faces of one orientation: h0 + grow x
    its distance from the nearest such face. along 'z': faces (z, x0, x1)
    limit the cells' height; 'x': faces (x, z0, z1) their width."""
    out = np.full(len(cell), np.inf)
    if not len(faces):
        return out
    for s in range(0, len(cell), chunk):
        c = cell[s:s + chunk]
        if along == 'z':
            dn = _gap(c[:, 2], c[:, 3], faces[:, 0], faces[:, 0])
            dt = _gap(c[:, 0], c[:, 1], faces[:, 1], faces[:, 2])
        else:
            dn = _gap(c[:, 0], c[:, 1], faces[:, 0], faces[:, 0])
            dt = _gap(c[:, 2], c[:, 3], faces[:, 1], faces[:, 2])
        out[s:s + chunk] = h0 + grow * np.hypot(dn, dt).min(1)
    return out


def _corner_size(cell, P, h0, grow, chunk=4096):
    """Isotropic size: min over corners of h0_i + grow x distance."""
    out = np.empty(len(cell))
    for s in range(0, len(cell), chunk):
        c = cell[s:s + chunk]
        dx = _gap(c[:, 0], c[:, 1], P[:, 0], P[:, 0])
        dz = _gap(c[:, 2], c[:, 3], P[:, 1], P[:, 1])
        out[s:s + chunk] = (h0[None, :] + grow * np.hypot(dx, dz)).min(1)
    return out


def _inside(cu, cx, cz):
    """The group of the copper at points cx, cz (-1: air)."""
    g = np.full(cx.shape, -1, int)
    for r, k in zip(cu.rects, cu.group):
        m = (cx > r[0]) & (cx < r[1]) & (cz > r[2]) & (cz < r[3])
        g[m] = k
    return g


def mesh(cu, fmax, sigma=SIGMA_CU, skin_cell=None, skin_grow=None,
         air_fine=None, air_grow=None):
    """The cells (n, 4: x0, x1, z0, z1, mm) of an MQS solve to fmax (Hz; 0:
    DC alone) and the group of each (-1 air)."""
    skin_cell = SKIN_CELL if skin_cell is None else skin_cell
    skin_grow = SKIN_GROW if skin_grow is None else skin_grow
    air_fine = AIR_FINE if air_fine is None else air_fine
    air_grow = AIR_GROW if air_grow is None else air_grow
    xl, xr, zb, zt = cu.box
    R = cu.rects
    hor, ver, P = _faces(cu)
    xk = np.unique(np.round(np.r_[xl, xr, R[:, 0], R[:, 1]], 9))
    zk = np.unique(np.round(np.r_[zb, zt, R[:, 2], R[:, 3]], 9))
    # the copper's smallest size, and the skin's cell
    small = float(min((R[:, 1] - R[:, 0]).min(), (R[:, 3] - R[:, 2]).min()))
    hs = CU_FINE * small
    if fmax and fmax > 0:
        hs = min(hs, skin_cell * float(skin_depth(fmax, sigma)) * 1e3)
    # air: each corner's own feature, its nearest other corner or face
    D = np.maximum(np.abs(P[:, None, 0] - P[None, :, 0]),
                   np.abs(P[:, None, 1] - P[None, :, 1]))
    D[D < 1e-9] = np.inf
    feat = D.min(1)
    h0a = np.maximum(air_fine * feat, hs)
    X0, Z0 = np.meshgrid(xk[:-1], zk[:-1], indexing='ij')
    X1, Z1 = np.meshgrid(xk[1:], zk[1:], indexing='ij')
    cell = np.stack([X0.ravel(), X1.ravel(), Z0.ravel(), Z1.ravel()], 1)
    done = []
    while len(cell):
        cx, cz = cell[:, :2].mean(1), cell[:, 2:].mean(1)
        cu_cell = _inside(cu, cx, cz) >= 0
        hx = np.empty(len(cell))
        hz = np.empty(len(cell))
        a = ~cu_cell
        if a.any():
            hx[a] = hz[a] = _corner_size(cell[a], P, h0a, air_grow)
        if cu_cell.any():
            c = cell[cu_cell]
            hz[cu_cell] = _face_size(c, hor, 'z', hs, skin_grow)
            hx[cu_cell] = _face_size(c, ver, 'x', hs, skin_grow)
        sx = cell[:, 1] - cell[:, 0] > SPLIT * hx
        sz = cell[:, 3] - cell[:, 2] > SPLIT * hz
        done.append(cell[~(sx | sz)])
        cell = np.concatenate([
            X._halved(cell[sx & ~sz], 0),
            X._halved(cell[sz & ~sx], 2),
            X._halved(X._halved(cell[sx & sz], 0), 2)
        ])
    cell = np.concatenate(done)
    return cell, _inside(cu, cell[:, :2].mean(1), cell[:, 2:].mean(1))


# a bilinear rectangle's mass matrix / its area, corners (x0, z0), (x1, z0),
# (x1, z1), (x0, z1)
_M = np.array([[4, 2, 1, 2], [2, 4, 2, 1], [1, 2, 4, 2], [2, 1, 2, 4]]) / 36.0


@dataclass
class System:
    """The assembled MQS system on the independent nodes: K (stiffness / mu0),
    M (sigma-weighted mass, per S/m of sigma), B (n, G + 1: each group's
    int N_i dS, m^2), area (G + 1, m^2), the free nodes (not on the box)."""
    K: object
    M: object
    B: np.ndarray
    area: np.ndarray
    free: np.ndarray
    nodes: int
    cells: int
    Mg: list = field(default_factory=list)   # M per group (M = their sum)
    # each free node's group: the copper it lies in or on (-1 air)
    on: np.ndarray = None


def assemble(cu, cell, grp):
    """The System of the cells (mesh())."""
    from scipy.sparse import coo_matrix
    x, ix = np.unique(cell[:, :2], return_inverse=True)
    z, iz = np.unique(cell[:, 2:], return_inverse=True)
    T, kept, nodes = X._hanging(x, z, ix.reshape(-1, 2), iz.reshape(-1, 2))
    n = T.shape[0]
    w = (cell[:, 1] - cell[:, 0]) * 1e-3
    t = (cell[:, 3] - cell[:, 2]) * 1e-3
    rows = np.repeat(nodes, 4, axis=1).ravel()
    cols = np.tile(nodes, (1, 4)).ravel()
    Ke = (t / w)[:, None, None] * X._K_X + (w / t)[:, None, None] * X._K_Z
    K = coo_matrix((Ke.ravel() / MU0, (rows, cols)), shape=(n, n)).tocsr()
    c = np.flatnonzero(grp >= 0)
    area = w[c] * t[c]
    G = int(grp.max()) + 1

    def mass(sel):
        Me = (w[sel] * t[sel])[:, None, None] * _M
        return coo_matrix((Me.ravel(),
                           (np.repeat(nodes[sel], 4, axis=1).ravel(),
                            np.tile(nodes[sel], (1, 4)).ravel())),
                          shape=(n, n)).tocsr()
    Mg = [mass(np.flatnonzero(grp == g)) for g in range(G)]
    M = sum(Mg[1:], Mg[0])
    B = np.zeros((n, G))
    np.add.at(B, (nodes[c].ravel(), np.repeat(grp[c], 4)),
              np.repeat(area / 4, 4))
    A = np.bincount(grp[c], weights=area, minlength=G)
    red = lambda A: (T.T @ A @ T).tocsr()
    K, M, Mg = red(K), red(M), [red(m) for m in Mg]
    B = np.asarray(T.T @ B)
    px, pz = np.zeros(n), np.zeros(n)
    px[nodes.ravel()] = cell[:, [0, 1, 1, 0]].ravel()
    pz[nodes.ravel()] = cell[:, [2, 2, 3, 3]].ravel()
    px, pz = px[kept], pz[kept]
    xl, xr, zb, zt = cu.box
    wall = ((np.abs(px - xl) < 1e-12) | (np.abs(px - xr) < 1e-12)
            | (np.abs(pz - zb) < 1e-12) | (np.abs(pz - zt) < 1e-12))
    free = np.flatnonzero(~wall)
    on = np.full(len(px), -1, int)
    tol = 1e-9
    for r, g in zip(cu.rects, cu.group):
        on[(px >= r[0] - tol) & (px <= r[1] + tol) & (pz >= r[2] - tol)
           & (pz <= r[3] + tol)] = g
    sub = lambda A: A[free][:, free].tocsc()
    return System(sub(K), sub(M), B[free], A, free, len(free), len(cell),
                  [sub(m) for m in Mg], on[free])


def pec_inductance(sysm):
    """The N x N inductance (H/m) of the System's copper as perfect
    conductors (A one value over each, the field in the air alone), on its
    mesh: the high-frequency limit its eddy-current solve tends to, whose own
    discretisation error in the air the internal inductance L(f) - this
    leaves out."""
    from scipy.sparse import csr_matrix
    G = sysm.B.shape[1]
    air = np.flatnonzero(sysm.on < 0)
    cuN = np.flatnonzero(sysm.on >= 0)
    Tg = csr_matrix((np.ones(len(cuN)), (np.arange(len(cuN)), sysm.on[cuN])),
                    shape=(len(cuN), G))
    K = sysm.K.tocsr()
    Kaa = K[air][:, air].tocsc()
    Kac = (K[air][:, cuN] @ Tg).toarray()
    Kcc = (Tg.T @ K[cuN][:, cuN] @ Tg).toarray()
    P = Kcc - Kac.T @ _factor(Kaa).solve(Kac)      # the conductors' currents per A
    return _reference(np.linalg.inv(0.5 * (P + P.T)))


def _reference(Zf):
    """The N x N impedance with the ground (group 0) as the return, from the
    (G, G) map of currents to fields Zf: Z_kj = Zf_kj - Zf_k0 - Zf_0j +
    Zf_00."""
    return (Zf[1:, 1:] - Zf[1:, :1] - Zf[:1, 1:] + Zf[0, 0])


def _factor(A):
    """SuperLU of a complex symmetric (or real symmetric) matrix with a
    positive definite real part: no pivoting, a symmetric ordering."""
    from scipy.sparse.linalg import splu
    return splu(A, permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0,
                options=dict(SymmetricMode=True))


def threads(n):
    """The threads a sweep of n frequencies is solved on: THREADS, else one
    per performance core (emsdev.cpu.core_threads), at most n."""
    from ...emsdev.cpu import core_threads
    return max(1, min(n, THREADS or core_threads()))


def series_z(sysm, f, sigma=SIGMA_CU, rough=None, losses=False):
    """Z (nf, N, N), ohm/m, of the assembled System at frequencies f (Hz; 0:
    DC: its real part the DC resistance, its imaginary part the DC inductance,
    H/m). losses: also P (nf, N, G), the loss per metre in each group (ground
    first) with 1 A in signal conductor j and its return in the ground (W/m,
    peak phasors: sum_g P[:, j, g] = R_jj). Each frequency is a factorisation
    of its own, on threads (threads(): SuperLU lets go of the interpreter)."""
    from concurrent.futures import ThreadPoolExecutor
    from ..roughness import hammerstad
    f = np.atleast_1d(np.asarray(f, float))
    G = sysm.B.shape[1]
    out = np.zeros((len(f), G - 1, G - 1), complex)
    P = np.zeros((len(f), G - 1, G))
    # each signal's excitation: +1 A in it, -1 A in the ground
    I = np.zeros((G, G - 1))
    I[0, :] = -1.0
    I[1:, :] = np.eye(G - 1)

    def one(i):
        fi = f[i]
        s = sigma
        if rough and fi > 0:
            s = sigma / float(hammerstad(fi, rough, sigma)) ** 2
        S = s * sysm.area
        B = s * sysm.B
        w = 2 * math.pi * fi
        if fi <= 0:
            W = _factor(sysm.K).solve(B)
            Si = 1 / S
            Lf = Si[:, None] * (B.T @ W) * Si[None, :]
            Zf = np.diag(Si)
            out[i] = _reference(Zf) + 1j * _reference(Lf)
        else:
            A0 = (sysm.K + (1j * w * s) * sysm.M).tocsc()
            W = _factor(A0).solve(B.astype(complex))
            Y = np.diag(S).astype(complex) - 1j * w * (B.T @ W)
            Zf = np.linalg.inv(Y)
            out[i] = _reference(Zf)
        if losses:
            E = Zf @ I                              # (G, N): each group's field
            if fi <= 0:
                P[i] = (S[:, None] * np.abs(E) ** 2).T
                return
            A = W @ E                               # (n, N)
            bA = sysm.B.T @ A                       # (G, N): int_g A dS
            for g, Mg in enumerate(sysm.Mg):
                AMA = np.einsum('ij,ij->j', A.conj(), Mg @ A).real
                P[i, :, g] = s * (sysm.area[g] * np.abs(E[g]) ** 2
                                  - 2 * (E[g].conj() * 1j * w * bA[g]).real
                                  + w * w * AMA)
    nt = threads(len(f))
    if nt == 1:
        for i in range(len(f)):
            one(i)
    else:
        with ThreadPoolExecutor(nt) as ex:
            list(ex.map(one, range(len(f))))
    return (out, P) if losses else out


# ---- the RLGC of a cross-section --------------------------------------------
@dataclass
class RLGC:
    """Per-unit-length matrices of N lines over frequency f (nf,): R, L, G, C
    (nf, N, N; ohm/m, H/m, S/m, F/m); L_inf = mu0 eps0 C_air^-1 (H/m, the
    external inductance the field solve gives); C_air; the MQS mesh's nodes."""
    f: np.ndarray
    R: np.ndarray
    L: np.ndarray
    G: np.ndarray
    C: np.ndarray
    L_inf: np.ndarray
    C_air: np.ndarray
    nodes: int = 0
    notes: list = field(default_factory=list)

    @property
    def n(self):
        return self.R.shape[-1]

    def z(self):
        """Z = R + j w L (nf, N, N)."""
        return self.R + 2j * np.pi * self.f[:, None, None] * self.L

    def y(self):
        """Y = G + j w C (nf, N, N)."""
        return self.G + 2j * np.pi * self.f[:, None, None] * self.C

    def at(self, f):
        """The RLGC interpolated to frequencies f: R and the internal
        inductance L - L_inf as log-log in f above the lowest nonzero
        frequency (R ~ sqrt(f) in the skin regime is a straight line there),
        linear below it; G linear in f, C linear (C(f) is constant unless a
        wideband dielectric made it otherwise)."""
        f = np.atleast_1d(np.asarray(f, float))
        n = self.n
        out = {}
        for key in ('R', 'L', 'G', 'C'):
            v = getattr(self, key)
            if key == 'L':
                v = v - self.L_inf[None]
            flat = v.reshape(len(self.f), -1)
            out[key] = np.stack([_interp(f, self.f, flat[:, j],
                                         log=key in ('R', 'L'))
                                 for j in range(n * n)], 1).reshape(-1, n, n)
        out['L'] = out['L'] + self.L_inf[None]
        return RLGC(f, out['R'], out['L'], out['G'], out['C'], self.L_inf,
                    self.C_air, self.nodes, list(self.notes))

    def to_dict(self):
        lst = lambda a: np.asarray(a).tolist()
        return dict(f=lst(self.f), R=lst(self.R), L=lst(self.L),
                    G=lst(self.G), C=lst(self.C), L_inf=lst(self.L_inf),
                    C_air=lst(self.C_air), nodes=self.nodes, notes=self.notes)

    @classmethod
    def from_dict(cls, d):
        a = lambda k: np.asarray(d[k], float)
        return cls(a('f'), a('R'), a('L'), a('G'), a('C'), a('L_inf'),
                   a('C_air'), int(d.get('nodes', 0)), list(d.get('notes', [])))


def _interp(f, f0, v0, log):
    """v at f from samples v0 at f0 (sorted): in log f (and log |v| where log
    and the samples keep their sign) between nonzero frequencies, linear down
    to 0 Hz; held beyond the ends."""
    f0 = np.asarray(f0, float)
    v0 = np.asarray(v0, float)
    out = np.interp(f, f0, v0)
    nz = f0 > 0
    if nz.sum() >= 2:
        lf, lf0 = np.log(np.maximum(f, 1e-300)), np.log(f0[nz])
        m = f >= f0[nz][0]
        same = np.all(v0[nz] > 0) or np.all(v0[nz] < 0)
        if log and same:
            sg = np.sign(v0[nz][0])
            out[m] = sg * np.exp(np.interp(lf[m], lf0, np.log(np.abs(v0[nz]))))
        else:
            out[m] = np.interp(lf[m], lf0, v0[nz])
    return out


def rlgc(xs, f, sigma=SIGMA_CU, plane_t=COPPER_T, sheet_t=COPPER_T, rough=None,
         wideband=None, mesh_f=None, es=None):
    """The RLGC of a CrossSection at frequencies f (Hz, 0 for DC). sigma: the
    copper's conductivity (S/m); plane_t, sheet_t: the planes' and sheets'
    copper (mm); rough: um RMS (core.roughness); wideband: fref (Hz) for each
    dielectric's Djordjevic-Sarkar curve through its er and tan d there (C and
    G solved at each frequency's materials), None: constant er and tan d;
    mesh_f: the frequency the mesh resolves the skin at (default: the highest
    asked); es: xsection's Result of xs, where already solved.

    L is xsection's L_inf = mu0 eps0 C_air^-1 (extrapolated from two meshes,
    as its C is: the lossless line's Z0 and delay are the field solver's) plus
    the internal inductance, the eddy-current solve's L(f) less its own
    perfect-conductor limit on the same mesh (pec_inductance): their air
    meshes' error cancels, so it needs no fine air mesh."""
    f = np.atleast_1d(np.asarray(f, float))
    cu = geometry(xs, plane_t, sheet_t)
    cell, grp = mesh(cu, mesh_f if mesh_f is not None else f.max(), sigma)
    sysm = assemble(cu, cell, grp)
    Z = series_z(sysm, f, sigma, rough)
    L_pec = pec_inductance(sysm)
    w = 2 * np.pi * f
    R = Z.real.copy()
    L = np.empty_like(R)
    dc = w <= 0
    L[dc] = Z[dc].imag
    L[~dc] = Z[~dc].imag / w[~dc, None, None]
    sym = lambda a: 0.5 * (a + np.swapaxes(a, -1, -2))
    es = es or X.solve(xs)
    R, L = sym(R), sym(L - L_pec[None] + es.L[None])
    n = R.shape[-1]
    if wideband:
        C = np.empty((len(f), n, n))
        G = np.empty((len(f), n, n))
        for i, fi in enumerate(f):
            r = (X.solve(_materials_at(xs, wideband, fi)) if fi > 0 else es)
            C[i] = r.C
            G[i] = w[i] * r.G_w
    else:
        C = np.broadcast_to(es.C, (len(f), n, n)).copy()
        G = w[:, None, None] * es.G_w[None]
    return RLGC(f, R, L, G, C, es.L, es.Ca, sysm.nodes, list(cu.notes))


def mirrored(xs):
    """xs mirrored in x (each conductor kept in its place in the list, so a
    line is the same line): its RLGC is xs's, exactly (the mesh mirrors too)."""
    neg = lambda v: -v
    d = xs.to_dict()
    flip = lambda a, b: (None if b is None else neg(b),
                         None if a is None else neg(a))
    d['dielectrics'] = [[z0, z1, er, td, *flip(x0, x1)]
                        for z0, z1, er, td, x0, x1 in d['dielectrics']]
    d['conductors'] = [[*flip(x0, x1), z0, z1, g]
                       for x0, x1, z0, z1, g in d['conductors']]
    return X.CrossSection.from_dict(d)


def section_key(xs, *extra):
    """A key for an RLGC solve of xs (and its settings, extra) that it and
    its mirror image share."""
    import json
    a = json.dumps(xs.to_dict())
    b = json.dumps(mirrored(xs).to_dict())
    return (min(a, b),) + tuple(extra)


def section_of(e):
    """(CrossSection, copper thickness mm) of a line model's section (an
    Element): its board's cut where the line model took one, the grounded CPW
    it was classified as, else its classification over ideal planes; None
    where it has none (no reference copper)."""
    t = float(e.t or 0.0)
    if getattr(e, 'cut', None):
        from .reference import _cut_section
        return _cut_section(*e.cut), float(e.cut[2]) or COPPER_T
    if getattr(e, 'classified', None) is not None:
        return e.classified, t or COPPER_T
    if e.xs in ('microstrip', 'embedded microstrip') and e.h and e.er and e.w:
        return X.microstrip(e.w, float(e.h), e.er, t, e.tand or 0.0,
                            mask=e.cover or 0.0,
                            er_mask=e.er_cover or 1.0), t or COPPER_T
    if e.xs == 'cpwg' and e.h and e.er and e.s and e.w:
        return (X.microstrip(e.w, float(e.h), e.er, t, e.tand or 0.0, s=e.s),
                t or COPPER_T)
    if e.xs in ('stripline', 'offset stripline') and e.er and e.h and e.w:
        h1, h2 = e.h if np.ndim(e.h) else (e.h, e.h)
        return X.stripline(e.w, h1, h2, e.er, t, e.tand or 0.0), t or COPPER_T
    return None


def impedance(cu, f, sigma=SIGMA_CU, rough=None, mesh_f=None, losses=False):
    """(Z (nf, N, N) ohm/m, the mesh's free nodes) of Copper cu at frequencies
    f (Hz; at 0 its imaginary part is the DC inductance, H/m), on a mesh that
    resolves the skin at mesh_f (default: the highest of f); losses: ((Z, P),
    nodes), P each group's loss (series_z)."""
    f = np.atleast_1d(np.asarray(f, float))
    cell, grp = mesh(cu, mesh_f if mesh_f is not None else f.max(), sigma)
    sysm = assemble(cu, cell, grp)
    return series_z(sysm, f, sigma, rough, losses), sysm.nodes


def _materials_at(xs, fref, f):
    """A copy of xs with each dielectric's er and tan d at f on its
    Djordjevic-Sarkar curve through them at fref."""
    from copy import deepcopy
    from .dispersion import layer_at
    out = deepcopy(xs)
    for d in out.dielectrics:
        d.er, d.tand = layer_at(d.er, d.tand, fref, f)
    return out


# ---- closed forms (the checks) ----------------------------------------------
def wire_internal_z(f, a, sigma=SIGMA_CU):
    """The internal impedance per metre (ohm/m) of a round wire of radius a
    (m) at f (Hz): k I0(k a) / (2 pi a sigma I1(k a)), k = (1 + j) / delta
    (Schelkunoff); its DC limit 1 / (pi a^2 sigma)."""
    from scipy.special import ive
    f = np.atleast_1d(np.asarray(f, float))
    out = np.empty(len(f), complex)
    for i, fi in enumerate(f):
        if fi <= 0:
            out[i] = 1 / (math.pi * a * a * sigma)
            continue
        k = (1 + 1j) / float(skin_depth(fi, sigma))
        out[i] = k * ive(0, k * a) / (2 * math.pi * a * sigma * ive(1, k * a))
    return out


def tube_internal_z(f, b, c, sigma=SIGMA_CU):
    """The internal impedance per metre of a coax's outer conductor, a tube of
    inner radius b and outer c (m), carrying the return with the field inside
    it: (k / (2 pi b sigma)) (I0(kb) K1(kc) + K0(kb) I1(kc)) / (I1(kc) K1(kb) -
    I1(kb) K1(kc)); its DC limit 1 / (pi (c^2 - b^2) sigma)."""
    from scipy.special import ive, kve
    f = np.atleast_1d(np.asarray(f, float))
    out = np.empty(len(f), complex)
    for i, fi in enumerate(f):
        if fi <= 0:
            out[i] = 1 / (math.pi * (c * c - b * b) * sigma)
            continue
        k = (1 + 1j) / float(skin_depth(fi, sigma))
        kb, kc = k * b, k * c
        # scaled: I_n(x) = ive e^Re(x), K_n(x) = kve e^-x; every term divided
        # by e^(Re(kc) - kb), the others' factor is q
        q = np.exp((kb.real + kb) - (kc.real + kc))
        num = ive(0, kb) * kve(1, kc) * q + kve(0, kb) * ive(1, kc)
        den = ive(1, kc) * kve(1, kb) - ive(1, kb) * kve(1, kc) * q
        out[i] = k / (2 * math.pi * b * sigma) * num / den
    return out


def _chord(r, z0, z1):
    """The half-width of the rectangle between heights z0 and z1 (relative to
    a circle's centre) that holds the circle of radius r's area there."""
    def area(z):            # the circle's area below z, from -r
        z = min(max(z, -r), r)
        return z * math.sqrt(r * r - z * z) + r * r * math.asin(z / r)
    return (area(z1) - area(z0)) / (2 * (z1 - z0))


def staircase(r_in, r_out, h):
    """A round conductor (r_in 0) or a tube between radii r_in and r_out (mm),
    centred at the origin, as rectangles in slabs h high (r_out a multiple of
    h, so that shapes of one h share their slabs' lines), each as much copper
    as the shape has in its slab: [(x0, x1, z0, z1)] (the MQS solve's Copper
    takes touching rectangles of one group; xsection's electrostatic solve
    does not)."""
    n = int(round(2 * r_out / h))
    out = []
    for i in range(n):
        z0, z1 = -r_out + i * h, -r_out + (i + 1) * h
        wo = _chord(r_out, z0, z1)
        wi = (_chord(r_in, max(z0, -r_in), min(z1, r_in))
              * (min(z1, r_in) - max(z0, -r_in)) / h
              if r_in > 0 and z1 > -r_in and z0 < r_in else 0.0)
        if wi <= 0:
            out.append((-wo, wo, z0, z1))
        else:
            out += [(-wo, -wi, z0, z1), (wi, wo, z0, z1)]
    return out


def coax_copper(a, b, c, n=16, reach=0.25):
    """The Copper of a coax (mm: the inner conductor's radius a, the outer's
    inner and outer radii b and c; c a multiple of a / n) as staircases of
    slabs a / n high, in a box reach x c beyond the tube (no field reaches
    it)."""
    h = a / n
    rects = [(*r, 1) for r in staircase(0.0, a, h)]
    rects += [(*r, 0) for r in staircase(b, c, h)]
    R = np.array([r[:4] for r in rects], float)
    g = np.array([r[4] for r in rects], int)
    m = c * (1 + reach)
    return Copper(R, g, (-m, m, -m, m))
