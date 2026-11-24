"""What happens at a cavity's open edges: fringing fields and radiation.

Fringing. Past an open edge the field bulges out, into air on an outer layer.
Over a wide plane that changes little; on a 1 mm sliver it is a large share
of the field, and the mode sits higher than a parallel-plate model gives (a
microstrip's effective permittivity is well below the substrate's). The
fringe is modelled as a thin border strip along each open edge, of width
`delta` and relative permittivity `er_ext`, chosen so a microstrip of the
reference width gets Hammerstad's capacitance and inductance per length
exactly:

    C = eps0 (er u + 2 cf(er)),  C_air = eps0 (u + 2 cf(1))       (u = w / h)
    delta = h cf(1),  er_ext = cf(er) / cf(1)

The strip is thin against the mode's variation, so it reduces to terms along
the mesh boundary: a 1D mass (its capacitance) and a 1D stiffness along the
edge (its share of the current). Buried pairs fringe into the dielectric on
both sides: er_ext = er.

Radiation. The voltage V along an open edge is a magnetic current (a slot
between the planes); its image in the plane under it doubles it. The power
radiated by all edge elements together is summed with their free-space
mutual coupling (exact for small elements, so short edges and the phase along
them are accounted for - the long-slot rule G = 1/(120 lambda0) per metre
overstates a 10 mm sliver's radiation about threefold):

    P = k0^2 / (4 pi eta0) sum_ij [ (m_i . m_j)(j0 - j1/x) + (m_i . r)(m_j . r)(3 j1/x - j0) ]

with m_i = V_i l_i t_i (edge element moment), x = k0 |r_i - r_j|, r the unit
vector between them. Q_rad = w W / P, W = (eps0 er / 2 d) integral V^2 dA.
Outer pairs radiate from every open edge; buried pairs only where the board
ends (their other edges couple into neighbouring cavities, not modelled).
"""
import math

import numpy as np

ETA0 = 376.730313668
C0 = 299_792_458.0
U_REF = 5.0            # w/h of the microstrip the fringe parameters are fitted at


def microstrip(u, er):
    """Hammerstad-Jensen zero-thickness microstrip: (Z0 in air, eps_eff)."""
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    z_air = ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))
    a = (1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49
         + math.log(1 + (u / 18.1) ** 3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    eeff = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    return z_air, eeff


def fringe(h, er, outer=True, u=U_REF):
    """(delta mm, er_ext) of the fringe strip for plate spacing h (mm)."""
    z_air, eeff = microstrip(u, er)
    c_air = ETA0 / z_air               # C_air / eps0 (= 1 / (c0 Z0_air eps0))
    cf1 = (c_air - u) / 2              # per edge, in units of eps0
    if not outer:                      # buried: the same dielectric beyond the edge
        return h * cf1, er
    cfr = (eeff * c_air - er * u) / 2
    return h * cf1, cfr / cf1


def boundary_edges(nodes, tris, fixed):
    """Edges on the mesh boundary that are open (not a ground via's rim):
    (n, 2) node pairs, ordered so the domain lies on their left."""
    P = nodes[tris]
    cw = ((P[:, 1, 0] - P[:, 0, 0]) * (P[:, 2, 1] - P[:, 0, 1])
          - (P[:, 1, 1] - P[:, 0, 1]) * (P[:, 2, 0] - P[:, 0, 0])) < 0
    tris = np.where(cw[:, None], tris[:, ::-1], tris)       # all counter-clockwise
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    b = e[cnt[inv.ravel()] == 1]
    return b[~(fixed[b[:, 0]] & fixed[b[:, 1]])]


def fringe_matrices(nodes_mm, edges, delta_mm, er_rel):
    """The fringe strip's 1D stiffness (dimensionless) and mass (m^2) along
    `edges`: width delta, permittivity er_rel times the cavity's."""
    import scipy.sparse as sp
    n = len(nodes_mm)
    if not len(edges) or delta_mm <= 0:
        return sp.csr_matrix((n, n)), sp.csr_matrix((n, n))
    p = nodes_mm[edges] * 1e-3
    L = np.linalg.norm(p[:, 1] - p[:, 0], axis=1)
    d = delta_mm * 1e-3
    ke = (d / L)[:, None, None] * np.array([[1, -1], [-1, 1]])
    me = (er_rel * d * L / 6)[:, None, None] * np.array([[2, 1], [1, 2]])
    r = np.repeat(edges, 2, axis=1).ravel()
    c = np.tile(edges, (1, 2)).ravel()
    K = sp.csr_matrix((ke.ravel(), (r, c)), shape=(n, n))
    M = sp.csr_matrix((me.ravel(), (r, c)), shape=(n, n))
    return K, M


def radiated_power(nodes_mm, edges, V, f):
    """Power (W) radiated by each mode (columns of V, peak volts) through the
    open `edges` (with the ground image). Edge elements are lumped into cells
    of lambda0/16 (their summed moment): exact while cells are small."""
    if not len(edges) or not V.shape[1]:
        return np.zeros(V.shape[1])
    p0, p1 = nodes_mm[edges[:, 0]] * 1e-3, nodes_mm[edges[:, 1]] * 1e-3
    mid, seg = (p0 + p1) / 2, p1 - p0                       # seg = l t
    out = np.zeros(V.shape[1])
    for m in range(V.shape[1]):
        k = 2 * math.pi * f[m] / C0
        ve = 0.5 * (V[edges[:, 0], m] + V[edges[:, 1], m])
        mom = seg * ve[:, None]
        big = np.abs(ve) > 1e-4 * max(np.abs(ve).max(), 1e-30)
        if not big.any():
            continue
        cell = (2 * math.pi / k) / 16
        key = np.floor(mid[big] / cell).astype(np.int64)
        _, grp = np.unique(key, axis=0, return_inverse=True)
        grp = grp.ravel()
        g = grp.max() + 1
        M = np.zeros((g, 2)); P = np.zeros((g, 2)); w = np.zeros(g)
        np.add.at(M, grp, mom[big])
        a = np.abs(ve[big]) * np.linalg.norm(seg[big], axis=1)
        np.add.at(P, grp, mid[big] * a[:, None]); np.add.at(w, grp, a)
        P /= np.maximum(w, 1e-30)[:, None]
        out[m] = _pair_sum(M, P, k)
    return out


def _pair_sum(M, P, k, block=2048):
    """k^2/(4 pi eta0) sum_ij of the elements' mutual radiation (see above)."""
    total = 0.0
    for s in range(0, len(M), block):
        Mi, Pi = M[s:s + block], P[s:s + block]
        r = Pi[:, None, :] - P[None, :, :]
        dist = np.linalg.norm(r, axis=2)
        x = k * dist
        small = x < 1e-6
        xs = np.where(small, 1.0, x)
        j0 = np.where(small, 1.0, np.sin(xs) / xs)
        j1x = np.where(small, 1 / 3, (np.sin(xs) / xs ** 2 - np.cos(xs) / xs) / xs)
        rh = r / np.where(dist > 0, dist, 1.0)[..., None]
        mm = Mi @ M.T
        mr = (np.einsum('id,ijd->ij', Mi, rh) * np.einsum('jd,ijd->ij', M, rh))
        total += np.sum(mm * (j0 - j1x) + mr * (3 * j1x - j0))
    return k * k / (4 * math.pi * ETA0) * total
