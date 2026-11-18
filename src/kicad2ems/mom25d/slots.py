"""
mom25d.slots - openings in a PEC plane of the stack (slot planes)

A plane of the stack (LayerStack.pec) stays a perfect conductor, not meshed, and
each of its openings - a slot, a defected-ground cut, the aperture of an
aperture-coupled patch - is meshed on its own: its unknowns are the tangential
electric field in the opening, so the plane costs about its openings' area
instead of its own. The frill holes (frill.py) are the case of a round hole
round a via, with a few fixed field shapes; here the field is free over the
opening, in RWG functions.

Model ----- The field in an opening is E = z x M on its mesh, M a sum of RWG
functions (their normal component is zero at the opening's rim, so E along the
rim is: the PEC's). Closing the opening and putting E on both faces of the plane
(a magnetic current each side) leaves two problems, each with the plane as its
ground or cover, as for a frill (frill.py, with the same conventions: exp(+jwt),
the spectral network of multilayer.py, T_k = LayerStack.transfer the voltage at
interface k of the network driven by a unit voltage at the plane). A field
element E dA at the origin of the opening gives, on a metal interface k on
either side:

    its TM part, a potential   Psi_k = (E . rho^) P_k(rho),     P_k = -(1/2 pi)
                               int T_k^e J1(kr rho) dkr
    its TE part, a field       E_k = (U0_k I + U2_k R2(phi)) E,  U_n = (1/4 pi)
                               int T_k^h J_n(kr rho) kr dkr R2 = [[cos 2 phi,
                               sin 2 phi], [sin 2 phi, -cos 2 phi]]
    along a via in layer span  -int E_z dz less its potential part: (E . rho^)
                               Rem(rho), Rem = -(1/2 pi) int sum_j (T_j -
                               T_{j-1}) k_j^2 / (kr^2 - k_j^2) J1(kr rho) dkr

(a frill's shapes are these, superposed over its hole: tests/test_slots.py
checks it). The unknowns v_n (the amplitudes of E_n = z x f_n) take one equation
each, H continuous across the opening, tested with f_n; by reciprocity the
system stays symmetric, as frill.py's:

    [ Z    c ] [I]   [E]
    [ c^T -Y ] [v] = [0]

    c_mn = -<f_m, E_k of E_n> = -int div f_m Psi_k(E_n)
           - int f_m . E_k^TE(E_n) (edges), the chains' ends on pads by their
           fans' charge and the remainder (frill.py)
    Y_mn = int int f_m . f_n G_e + int int div f_m div f_n G_d
           (one plane's openings)
    G_e = (1/2 pi) int Ye J0 kr dkr,
    G_d = (1/2 pi) int (Yh - Ye) / kr^2 J0 kr dkr

Ye, Yh: the TM and TE admittances into both sides of the plane (Yu + Yd). Y is
the dual of the strips' Z (Vh I + (Ve - Vh) k^k^ there, Ye I + (Yh - Ye) k^k^
here), and is filled the same way: G_e / (jw) as its "A" and jw G_d as its "q",
their 1/rho parts eps0 (eps_below + eps_above) / (2 pi) and 1 / (pi mu0) taken
out and integrated analytically (base.singular_moments).

Between an opening's triangle and a strip's, the kernels are smooth (the plane
is no metal interface): one point each where they are far apart, quadrature
where they are near (the opening's triangles subdivided, the field peaking over
the plane's distance to the strip, each pair subdivided to the scale of its own
distance). An opening's field radiates (farfield.py).

A via ending on the plane outside the openings is not at zero potential there:
only a round hole's axisymmetric field leaves the plane at zero, while another
field's TM part has a potential on the plane, P_p = -1 / (2 pi rho) (T = 1),
whose gradient cancels its TE part's field on the metal. The via is charged it
at that end, as its fans are at their ends (+ at its top, - at its bottom).
Without it a port via's potential at its pad stood alone, and a slot's effect on
the line came out about doubled (S21 -12 dB at 8 GHz where the ground meshed
gives -5.1 dB; 2026- 11-15). frill.py's first-order shapes leave such a
potential too, and its chains see it since then.
"""
import numpy as np

from kicad2ems.emsdev.numerics.base import (MU0, EPS0, RWG, qform, combine,
                                            quad_pts, _BAR3, _W3, _BAR6, _W6)
from .base import singular_moments, moments7, quad_moments
from .frill import bessel_int
from .multilayer import GreenTableML
from .via import sommerfeld_contour, Table1D, _layers

# an opening's triangle and a strip's are near within NEAR x (the larger's size
# + the height)
NEAR = 4.0
# the opening's triangles subdivided at most 4^SUB_MAX times for a near strip
SUB_MAX = 3


class Slot:
    """An opening in the PEC plane on interface k: poly, a shapely Polygon in
    metres (holes allowed: an island inside the opening is plane too, not
    floating metal: say so with a hole only where it is joined to the plane,
    else it is left out). name: for the log."""
    def __init__(self, k, poly, name=''):
        self.k, self.poly, self.name = int(k), poly, name
        self.rwg = None  # mesh(): the opening's RWG functions

    @property
    def count(self):
        return 0 if self.rwg is None else self.rwg.Ne

    def mesh(self, dmax, ncross=6, grade=1.0, fine=None, dmax_fine=None,
             w_max=None):
        """The opening's triangles: edge rings at its rim (where its field
        peaks), cells up to dmax, and up to dmax_fine inside `fine` (where
        copper on the other layers is near: as a meshed plane is refined).
        w_max: the edge rings' reference width at most (default 4 dmax)."""
        from .geo import mesh_polygons
        # its width, about: (twice area / perimeter)
        w = 2 * self.poly.area / max(self.poly.length, 1e-12)
        nodes, tris, _ = mesh_polygons(self.poly, dmax,
                                       min(w, w_max or 4 * dmax), ncross, grade,
                                       fine=fine, dmax_fine=dmax_fine)
        self.rwg = r = RWG(nodes, tris)
        self.P3, self.W3 = quad_pts(r, _BAR3), r.area[:, None] * _W3[None, :]
        self.P6, self.W6 = quad_pts(r, _BAR6), r.area[:, None] * _W6[None, :]
        V = r.nodes[r.tris]
        self.size = np.linalg.norm(V - np.roll(V, 1, axis=1), axis=2).max(1)
        return r


class SlotKernels:
    """
    Per-frequency kernels of the openings `slots` (Slot) of a stack's PEC
    planes:
      table[p]      : SlotTable of plane p (its openings' own Y)
      P[(p, k)]     : Table1D of P_k(rho), the TM potential's kernel on metal
                    interface k
      U[(p, k)]     : Table1D of (U0, U2), the TE field's
      rem(p, span)  : Table1D of Rem(rho) for a chain through `span` (k_bot,
                    k_top)
    """
    def __init__(self, f, stack, slots, interfaces, rho_max, nrho=260):
        self.w = w = 2 * np.pi * f
        self.st = st = stack.at(f)
        self.kr, self.dk, _ = kr, dk, _ = sommerfeld_contour(f, st, rho_max)
        self.rho_max = rho_max
        self.rho = rho = rho_max * np.linspace(0, 1, nrho) ** 2
        planes = sorted({s.k for s in slots})
        self.table = {p: SlotTable(f, st, p, rho_max) for p in planes}
        self.P, self.U, self._rem = {}, {}, {}
        keys = [(p, k)
                for p in planes
                for k in interfaces
                if st.reaches(p, k) and k != p]
        if keys:
            Te = np.array([st.transfer(kr, w, p, k, 'e') for p, k in keys])
            Th = np.array([st.transfer(kr, w, p, k, 'h') for p, k in keys])
            P = bessel_int(1, -Te / (2 * np.pi), kr, dk, rho)
            U0 = bessel_int(0, Th * kr / (4 * np.pi), kr, dk, rho)
            U2 = bessel_int(2, Th * kr / (4 * np.pi), kr, dk, rho)
            for i, key in enumerate(keys):
                self.P[key] = Table1D(rho, P[i], rho_max)
                self.U[key] = Table1D(rho, np.stack([U0[i], U2[i]], 1), rho_max)

    def rem(self, p, span):
        """Rem(rho) of a chain through `span`'s layers (on one side of plane
        p)."""
        if (p, span) not in self._rem:
            kr, k0 = self.kr, self.w / 299792458.0
            spec = 0.0
            for j in _layers(span):
                dT = (self.st.transfer(kr, self.w, p, j, 'e')
                      - self.st.transfer(kr, self.w, p, j - 1, 'e'))
                if np.any(dT):
                    k2 = k0 ** 2 * self.st.eps(j)
                    spec = spec + dT * k2 / (kr ** 2 - k2)
            vals = (np.zeros_like(self.rho, dtype=complex) if np.isscalar(spec)
                    else bessel_int(1, -spec /
                                    (2 * np.pi), kr, self.dk, self.rho))
            self._rem[(p, span)] = Table1D(self.rho, vals, self.rho_max)
        return self._rem[(p, span)]


class SlotTable(GreenTableML):
    """The kernels of an opening's own equation on plane p, a GreenTableML of
    that interface (its 'A' and 'q', what base.combine and the singular moments
    take): A = G_e / (jw), q = jw G_d (module doc), their 1/rho parts cA / rho,
    cq / rho extracted, and the nearest image (a dielectric step or metal close
    to the plane) where there is one within IMAGE_REACH."""
    IMAGE_REACH = 0.6e-3

    def __init__(self, f, stack, p, rho_max, nrho=260, nfine=8192):
        w = 2 * np.pi * f
        self.p, self.f, self.d, self.o, self.s = p, f, 0.0, p, p
        self.cA = EPS0 * stack.eps_sum(p) / (2 * np.pi)
        self.cq = 1.0 / (np.pi * MU0)
        kr, dk, K = sommerfeld_contour(f, stack, rho_max)
        GA, Gq = self.spectral(stack, kr, w, p)
        self.dimg, self.cAi, self.cqi = 0.0, 0.0, 0.0
        near = (stack.nearest_discontinuity(p) if hasattr(
            stack, 'nearest_discontinuity') else np.inf)
        if near < self.IMAGE_REACH:
            self.dimg = 2 * near
            K1 = 18.0 / self.dimg
            ga, gq = self.spectral(stack, np.array([K1 + 0j]), 2 * np.pi, p)
            fac = K1 * np.exp(K1 * self.dimg) / (2 * np.pi)
            self.cAi = complex((ga[0] - 2 * np.pi * self.cA / K1) * fac)
            self.cqi = complex((gq[0] - 2 * np.pi * self.cq / K1) * fac)
        self._tabulate(GA, Gq, kr, dk, rho_max, nrho, nfine)

    @staticmethod
    def spectral(stack, kr, w, p):
        _, _, _, Yd, Yu = stack._network(kr, w, 'e')
        Ye = Yu[p] + Yd[p]
        _, _, _, Yd, Yu = stack._network(kr, w, 'h')
        Yh = Yu[p] + Yd[p]
        return Ye / (1j * w), 1j * w * (Yh - Ye) / kr ** 2


def self_block(slots, table, w, near_fac=5.0, cache=None):
    """Y (n x n) of the openings `slots` of one plane, with `table` (SlotTable),
    as Simulation.impedance fills Z: jw (static A + A) + (static q + q) / (jw),
    the triangle pairs near each other by quadrature."""
    from .sing import offplane_pairs, offplane_moments
    # the openings of one plane as one mesh (their triangles and functions in
    # turn)
    nodes, tris, off = [], [], 0
    for s in slots:
        nodes.append(s.rwg.nodes)
        tris.append(s.rwg.tris + off)
        off += len(s.rwg.nodes)
    key = tuple(id(s) for s in slots)
    cache = {} if cache is None else cache
    if key not in cache:
        r = RWG(np.vstack(nodes), np.vstack(tris))
        M0, Mr, Mrr = singular_moments(r)
        V = r.nodes[r.tris]
        size = np.linalg.norm(V - np.roll(V, 1, axis=1), axis=2).max(1)
        # the order of the functions: for the edges of the slots' own meshes in
        # turn (Slot.rwg), their index among the joint mesh's edges, matched by
        # their end points (the joint mesh has each opening's nodes and
        # triangles, in order). The sign of a function (its T+ and T-) is the
        # same in both: it follows the triangles' order, which the joint mesh
        # keeps
        look = {tuple(np.round(r.ends[e].ravel(), 12)): e for e in range(r.Ne)}
        order = [
            look[tuple(np.round(s.rwg.ends[e].ravel(), 12))]
            for s in slots
            for e in range(s.rwg.Ne)
        ]
        cache[key] = dict(r=r, M0=M0, Mr=Mr, Mrr=Mrr, size=size,
                          P3=quad_pts(r,
                                      _BAR3), W3=r.area[:, None] * _W3[None, :],
                          img={}, order=np.array(order, int))
    c = cache[key]
    r = c['r']
    sym = lambda M: 0.5 * (M + M.T)
    rho = np.sqrt(((r.cent[:, None, :] - r.cent[None, :, :]) ** 2).sum(-1))
    GA, GQ = table.AQ(rho)
    wgt = r.area[:, None] * r.area[None, :]
    out = moments7(GA * wgt, GQ * wgt, r.cent[:, None], r.cent[None])
    size = np.maximum(c['size'][:, None], c['size'][None, :])
    p, q = np.nonzero(rho < near_fac * size)
    for M, v in zip(out, quad_moments(c['P3'], c['W3'], p, q, table)):
        M[p, q] = v
    if table.dimg:  # the nearest image, analytically for every pair
        if table.dimg not in c['img']:
            pp, qq = (np.repeat(np.arange(len(r.tris)), len(r.tris)),
                      np.tile(np.arange(len(r.tris)), len(r.tris)))
            c['img'][table.dimg] = (pp, qq,
                                    offplane_pairs(r.nodes, r.tris, pp, qq,
                                                   table.dimg))
        pp, qq, m = c['img'][table.dimg]
        for M, v in zip(out, offplane_moments(table.cAi, table.cqi, m)):
            M[pp, qq] += v
    A0, Axo, Ayo, _, _, Arr, Q0 = out
    if 'SA1' not in c:  # the 1/R parts' combination, once (scaled each time)
        c['SA1'] = combine(r, c['Mrr'], c['Mr'][..., 0], c['Mr'][..., 1],
                           c['M0'])
        c['SQ1'] = qform(r.D, c['M0'], r.D)
    Y = (1j * w *
         (table.cA * c['SA1'] + combine(r, sym(Arr), Axo, Ayo, sym(A0))) +
         (table.cq * c['SQ1'] + qform(r.D, sym(Q0), r.D)) / (1j * w))
    Y = 0.5 * (Y + Y.T)
    o = c['order']
    return Y[np.ix_(o, o)]


def _tm_moments(dx, dy, wgt, ru, P):
    """The far / near pair moments at observation - source offsets (dx, dy) (any
    shape, summed over the last axes by the caller), weights wgt and the source
    points ru (x, y) (same shape): the TM potential's (M1, M2, M3: int g_x, int
    g_y, int g_y x' - g_x y', g = P rho^)."""
    rho = np.hypot(dx, dy)
    inv = np.divide(1.0, rho, out=np.zeros_like(rho), where=rho > 0)
    p = P(rho) * wgt
    gx, gy = p * dx * inv, p * dy * inv
    return gx, gy, gy * ru[0] - gx * ru[1]


# strip triangles x opening triangles a pass of coupling's moments (11 complex
# each: 0.7 GB)
CHUNK = 4_000_000


def _moments(sim, idx, s, dz, P, U):
    """(M1, M2, M3, T0, Ro, Ws, RW) of the strip triangles idx against the
    opening s's triangles (module doc): the compiled core's slot_coupling."""
    from . import _core
    r, rs = sim.rwg, s.rwg
    m = _core.slot_coupling(
        np.ascontiguousarray(r.nodes[r.tris[idx]], float),
        r.area[idx], sim.tri_size[idx],
        np.ascontiguousarray(rs.nodes[rs.tris], float), rs.area, s.size,
        float(dz), np.ascontiguousarray(P.v, complex),
        np.ascontiguousarray(U.v, complex), float(P._dr), float(NEAR),
        int(SUB_MAX))
    return (m[0], m[1], m[2], (m[3], m[4], m[5]), (m[6], m[7]), (m[8], m[9]),
            m[10])


def _plane_potential(rho):
    """P on the plane itself: -(1/2 pi) int J1(kr rho) dkr = -1 / (2 pi rho)."""
    return -1.0 / (2 * np.pi * np.maximum(rho, 1e-12))


def coupling(sim, slots, kern, B, C, col0):
    """The rows and columns of the openings' unknowns: c into B (the strips'
    edges) and into C's chain rows (their fans and remainder), from column col0
    on, in the order of `slots` (each Slot.count). module doc."""
    r, st, Nc = sim.rwg, kern.st, len(sim.chains)
    F = np.asarray(sim.F, int)
    pos_F = {int(t): i for i, t in enumerate(F)}
    col = col0
    for s in slots:
        rs, n = s.rwg, s.count
        cols = slice(col, col + n)
        Aal_s, Bx_s, By_s = rs.Aal, rs.Bx, rs.By
        vs = lambda M1, M2, M3: np.asarray(M3 @ Aal_s + M1 @ By_s - M2 @ Bx_s)
        for k in sim.layers:
            if (s.k, k) not in kern.P:
                continue
            idx_all = sim.tri_idx[k]
            if not len(idx_all):
                continue
            dz = abs(st.z[k] - st.z[s.k])
            P, U = kern.P[(s.k, k)], kern.U[(s.k, k)]
            # strip triangles a pass (11 moments each)
            per = max(1, CHUNK // max(len(rs.tris), 1))
            for c0 in range(0, len(idx_all), per):
                idx = idx_all[c0:c0 + per]
                M1, M2, M3, T0, Ro, Ws, RW = _moments(sim, idx, s, dz, P, U)
                # TM: the potential over each strip triangle, tested with the
                # divergence (and the fans' charge)
                psi = vs(M1, M2, M3)  # (len(idx), n)
                Dk = r.D[idx]
                B[:, cols] -= np.asarray(Dk.T @ psi)
                if len(F) and Nc:
                    here = [(pos_F[int(t)], j)
                            for j, t in enumerate(idx)
                            if int(t) in pos_F]
                    if here:
                        fi, li = np.array(here).T
                        C[:Nc, cols] -= np.asarray(sim.dF[fi, :Nc].T @ psi[li])
                # TE: the field on the edges themselves: -<f, T E>, f = Aal r -
                # (Bx, By), E = Aal' w' + (By', -Bx')
                (txx, txy, tyy), (rox, roy), (wsx, wsy) = T0, Ro, Ws
                Aal_k, Bx_k, By_k = r.Aal[idx], r.Bx[idx], r.By[idx]
                te = Aal_k.T @ (RW @ Aal_s + rox @ By_s - roy @ Bx_s)
                te = te - Bx_k.T @ (wsx @ Aal_s) - By_k.T @ (wsy @ Aal_s)
                te = (te - Bx_k.T @ (txx @ By_s - txy @ Bx_s)
                      - By_k.T @ (txy @ By_s - tyy @ Bx_s))
                B[:, cols] -= np.asarray(te)
        # the chains' remainder (their part of int E_z dz that is not the
        # potential's), by span
        if Nc:
            geo = sim._via_geometry()
            for span, cs in geo['by_span'].items():
                # (a chain does not run through the plane: a via through it is
                # split there, its parts ending on the plane; Simulation refuses
                # one through an opening)
                if not any(st.reaches(s.k, j) or j == s.k for j in span):
                    continue
                dx = geo['x'][cs, None, None] - s.P6[None, :, :, 0]
                dy = geo['y'][cs, None, None] - s.P6[None, :, :, 1]
                ru = (s.P6[None, :, :, 0] + 0 * dx,
                      s.P6[None, :, :, 1] + 0 * dy)
                R = kern.rem(s.k, span)
                if np.any(R.v):
                    n1, n2, n3 = _tm_moments(dx, dy, s.W6[None] + 0 * dx,
                                             ru, R)
                    C[cs, cols] += vs(n1.sum(-1), n2.sum(-1), n3.sum(-1))
                if s.k in span:
                    # its end on the plane: the opening's potential there, P_p =
                    # -1 / (2 pi rho) (T = 1 on the plane). Not zero outside an
                    # opening: only a round hole's axisymmetric field leaves the
                    # plane at zero; another's TM part has a potential there
                    # whose gradient its TE part cancels. With the fans' sign
                    # (dF: +1 at a chain's bottom, -1 at its top), the plane at
                    # its bottom: -Psi_p
                    n1, n2, n3 = _tm_moments(dx, dy, s.W6[None] + 0 * dx,
                                             ru, _plane_potential)
                    sign = -1.0 if span[0] == s.k else 1.0
                    C[cs, cols] += sign * vs(n1.sum(-1), n2.sum(-1), n3.sum(-1))
        col += n
    return col


def spectrum(slots, v, kx, ky):
    """(Ex, Ey) (nk[, nexc]): the openings' field transformed at the plane-wave
    directions (kx, ky), sum of int E e^{j k.rho} (far field, farfield.py), with
    v their unknowns in the order of `slots`."""
    from scipy.sparse import diags
    Ex = Ey = 0
    col = 0
    for s in slots:
        n, r = s.count, s.rwg
        # the integral of E_n = z x f_n = (-f_y, f_x) over each of the opening's
        # triangles: f = Aal r - (Bx, By) on a triangle, its integral A (Aal c -
        # B)
        fx, fy = (diags(r.cent[:, 0]) @ r.Aal - r.Bx,
                  diags(r.cent[:, 1]) @ r.Aal - r.By)
        ix, iy = (diags(r.area) @ (-fy)).tocsr(), (diags(r.area) @ fx).tocsr()
        vv = v[col:col + n]
        ph = np.exp(
            1j *
            (np.outer(kx, s.rwg.cent[:, 0]) + np.outer(ky, s.rwg.cent[:, 1])))
        Ex, Ey = Ex + ph @ (ix @ vv), Ey + ph @ (iy @ vv)
        col += n
    return Ex, Ey
