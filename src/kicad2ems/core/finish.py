"""
Copper surface finish (--finish): the metal plated on the outer copper where no solder mask covers it (ENIG,
HASL, immersion silver or tin, or none: OSP and bare copper). A finish only covers the copper's outer face and its
sides: the face against the dielectric, where a microstrip carries most of its current, stays bare copper, and
inner layers and copper under the mask have none.

The finished face's impedance: each finish layer (conductivity sigma_i, thickness t_i, outermost first) a section
of line of Z_i = gamma_i / sigma_i, gamma_i = (1 + j) / delta_i, loading the one beneath it, down to the copper
face, gamma / sigma coth(gamma t) (one face of the copper's thickness t):

    Z_in = Z_i (Z_load + Z_i tanh(gamma_i t_i)) / (Z_i + Z_load tanh(gamma_i t_i))

5 um of ENIG's nickel (electroless Ni-P, mid-phosphorus: about 1.45 MS/m and, as plated, non-magnetic) puts a
face at 2.2x bare copper's resistance at 1 GHz and 4-5x from 3 to 30 GHz; lead-free HASL 2.3-2.9x; immersion
silver none. How much of that a line sees depends on the share of its loss on the finished faces:
loss_share() takes it from the 2D field solver (the line's current goes as its charge with no dielectric, TEM).

Each solver applies it as it can (its capabilities' 'finish'): FDTD with --thickness on as the outer faces' own
sheets (gfdtd over the band, openEMS as their conductivity at the band centre); mom25d per layer, weighted by the
share at each frequency; the line model and zero-thickness FDTD sheets weighted by the share at the band centre.
Values are typical fab figures (IPC-4552 / 4553 / 4554 thicknesses): a fab's own are the better input.
"""
import math

import numpy as np

MU0 = 4e-7 * math.pi
SIGMA_CU = 5.8e7

# name: (description, layers outermost first as (metal, sigma S/m, thickness m))
FINISHES = {
    'none': ("bare copper", []),
    'osp': ("OSP: an organic film on bare copper", []),
    'enig': ("ENIG: 5 um electroless Ni-P under 0.08 um immersion Au",
             [('Au', 4.1e7, 0.08e-6), ('Ni-P', 1.45e6, 5e-6)]),
    'enepig': ("ENEPIG: 5 um Ni-P, 0.1 um Pd, 0.05 um Au",
               [('Au', 4.1e7, 0.05e-6), ('Pd', 9.3e6, 0.1e-6), ('Ni-P', 1.45e6, 5e-6)]),
    'hasl': ("HASL, lead-free: about 5 um SnCu (levelled HASL ranges 1-25 um)", [('SnCu', 7.5e6, 5e-6)]),
    'hasl-snpb': ("HASL, tin-lead: about 5 um Sn63Pb37", [('SnPb', 6.9e6, 5e-6)]),
    'imag': ("immersion silver: 0.2 um Ag", [('Ag', 6.3e7, 0.2e-6)]),
    'imsn': ("immersion tin: 1 um Sn", [('Sn', 8.7e6, 1e-6)]),
}
CHOICES = ('auto',) + tuple(FINISHES)

# KiCad's stackup copper_finish names (Board Setup > Physical Stackup), lower case
KICAD = {'none': 'none', 'osp': 'osp', 'ht_osp': 'osp', 'immersion gold': 'enig', 'enig': 'enig', 'enepig': 'enepig',
         'hal lead-free': 'hasl', 'hasl lead-free': 'hasl', 'hal snpb': 'hasl-snpb', 'hasl snpb': 'hasl-snpb',
         'hasl': 'hasl-snpb', 'immersion silver': 'imag', 'immersion tin': 'imsn'}


def kicad_name(text):
    """(finish, note): the --finish name of KiCad's copper_finish text; unknown ones (hard gold, immersion nickel,
    user defined) as none, with a note."""
    key = (text or 'none').strip().lower()
    if key in KICAD:
        return KICAD[key], None
    return 'none', f"finish: the stackup's {text!r} is not modelled (--finish names the ones that are): bare copper"


def layers(name):
    """The finish's metal layers, outermost first: [(metal, sigma, t m), ...] ([] for none / osp)."""
    return list(FINISHES[name][1])


def face_z(f, sigma=SIGMA_CU, t=35e-6, finish=()):
    """Complex impedance (ohm per square) of one copper face, thickness t (m), under the finish layers."""
    w = 2 * np.pi * np.asarray(f, float)
    g = lambda s: (1 + 1j) * np.sqrt(w * MU0 * s / 2)
    z = g(sigma) / sigma / np.tanh(g(sigma) * t)
    for _, s, tl in reversed(list(finish)):
        zc, th = g(s) / s, np.tanh(g(s) * tl)
        z = zc * (z + zc * th) / (zc + z * th)
    return z


def factor(f, finish, sigma=SIGMA_CU, t=35e-6):
    """The finished face's impedance over the bare face's (complex; 1 without a finish)."""
    if not finish:
        return np.ones_like(np.asarray(f, float)) + 0j
    return face_z(f, sigma, t, finish) / face_z(f, sigma, t)


def weighted(f, finish, share, sigma=SIGMA_CU, t=35e-6):
    """A conductor's impedance over its bare copper's when `share` of its loss is on finished faces:
    1 + share (factor - 1)."""
    return 1 + share * (factor(f, finish, sigma, t) - 1)


def resistance_ratio(f, finish, share, sigma=SIGMA_CU, t=35e-6):
    """The finished conductor's resistance over its bare copper's: Re(z weighted()) / Re(z), z the bare face's (the
    finished face's impedance has another phase, so this is not the real part of weighted())."""
    z = face_z(f, sigma, t)
    return (z * weighted(f, finish, share, sigma, t)).real / z.real


def band_sigma(sigma, finish, share, fmin_hz, fmax_hz, t=35e-6):
    """The one conductivity whose skin-effect loss at the band centre is the finished conductor's: sigma / K^2,
    K = resistance_ratio() there."""
    if not finish or not share:
        return sigma
    k = float(resistance_ratio(0.5 * (fmin_hz + fmax_hz), finish, share, sigma, t))
    return sigma / k ** 2


def loss_share(w, h, t):
    """(strip, line): the share of a microstrip's conductor loss on its finished faces (the outer face and both
    sides) - of the strip's own loss, and of the line's (with the plane's). w, h, t mm: the strip, its height over
    the plane, its thickness. TEM: the surface current goes as the charge with no dielectric, from the 2D field
    solver's grid (each node's charge q over its share of the surface ds: loss ~ sum q^2 / ds). The ground plane's
    current is on its face towards the strip (a plane's outer face, finished or not, carries next to none)."""
    from .lines import xsection as X
    t = max(t, 1e-4)
    xs = X.CrossSection(dielectrics=[], planes=[0.0])
    grid = X.FINE, X.GROW
    X.FINE, X.GROW = 1 / 64, 1.15           # the corners' current converges slowly: 1/16 0.425, 1/64 0.455, 1/128 0.465
    try:                                    # (hairpin's line); etched corners are round, which a sharp limit overstates
        return _share(X, xs, w, h, t)
    finally:
        X.FINE, X.GROW = grid


def _share(X, xs, w, h, t):
    xs.conductor(-w / 2, w / 2, h, h + t)
    x, z = X._grid(xs)
    er, _, own = X._grid_cells(xs, x, z)
    A = X._laplacian(x, z, np.ones_like(er))
    phi = np.zeros(own.size)
    phi[own == 1] = 1.0
    free, fixed = np.flatnonzero(own < 0), np.flatnonzero(own >= 0)
    from scipy.sparse.linalg import spsolve
    phi[free] = spsolve(A[free][:, free].tocsc(), -(A[free][:, fixed] @ phi[fixed]))
    q = A @ phi
    X2, Z2 = np.meshgrid(x, z, indexing='ij')
    X2, Z2 = X2.ravel(), Z2.ravel()

    def half_widths(v):
        """Each grid line's share of the length around it (half the cells either side)."""
        d = np.diff(v)
        return 0.5 * (np.r_[d, 0.0] + np.r_[0.0, d])
    hx = dict(zip(np.round(x, 9), half_widths(x)))
    hz = dict(zip(np.round(z, 9), half_widths(z)))
    eps = 1e-9
    on_strip = own == 1
    top = on_strip & (np.abs(Z2 - (h + t)) < eps)
    bottom = on_strip & (np.abs(Z2 - h) < eps)
    side = on_strip & (np.abs(np.abs(X2) - w / 2) < eps) & ~top & ~bottom
    surf = top | bottom | side
    ds = np.array([hx[round(a, 9)] if (tp or bt) else hz[round(b, 9)]
                   for a, b, tp, bt in zip(X2[surf], Z2[surf], top[surf], bottom[surf])])
    loss = np.zeros(own.size)
    loss[surf] = q[surf] ** 2 / np.maximum(ds, 1e-12)
    plane = (own == 0) & (np.abs(Z2) < eps) & (np.abs(X2) < 50 * (w + h))
    lp = np.array([q[i] ** 2 / max(hx[round(X2[i], 9)], 1e-12) for i in np.flatnonzero(plane)])
    fin = loss[top].sum() + loss[side].sum()               # the face away from the plane, and the sides
    strip = loss[surf].sum()
    return float(fin / strip), float(fin / (strip + lp.sum()))


class Finish:
    """A run's finish (resolve): name, description, metal layers (outermost first), and per finished copper layer
    (the outer two) its copper thickness (m) and loss shares (strip, line; loss_share of its narrowest port trace,
    0 on a layer without ports: a plane, whose outer face carries next to no current)."""
    def __init__(self, name, layers, faces, notes):
        self.name, self.layers, self.faces, self.notes = name, layers, faces, notes
        self.description = FINISHES[name][0]

    def __bool__(self):
        return bool(self.layers and self.faces)

    def share(self, layer, line=False):
        f = self.faces.get(layer)
        return 0.0 if f is None else f[2 if line else 1]

    def thickness(self, layer, default=35e-6):
        f = self.faces.get(layer)
        return default if f is None else f[0]


def resolve(s, m):
    """resolve_once, kept on the board for its settings (every solver of a run asks)."""
    key = tuple(getattr(s, k, None) for k in ('finish', 'sigma', 'mask'))
    memo = m.__dict__.setdefault('_finish', {})
    if key not in memo:
        memo[key] = resolve_once(s, m)
    return memo[key]


def resolve_once(s, m):
    """The run's Finish from --finish (auto: the board's stackup) for board m, or None: no --sigma, bare copper, or
    solder mask over the board (--mask: copper under mask has no finish; its openings are not modelled). Notes say
    what was taken."""
    choice = (getattr(s, 'finish', None) or 'auto').lower()
    notes = []
    if choice == 'auto':
        stack = getattr(m, 'copper_finish', None)
        name, note = kicad_name(stack)
        notes += [note] if note else []
    else:
        name = choice
    if name not in FINISHES:
        raise ValueError(f"--finish {choice!r}: one of {', '.join(CHOICES)}")
    if not FINISHES[name][1] or not getattr(s, 'sigma', None):
        return None
    if getattr(s, 'mask', False):
        notes.append(f"finish: {name} not applied - with --mask the outer copper is under solder mask (its openings "
                     f"are not modelled)")
        return Finish(name, layers(name), {}, notes)
    order = list(m.cu_order)
    faces = {}
    for lay in {order[0], order[-1]}:
        ws = sorted((p.w, abs(p.z_sig - p.z_ref)) for p in m.ports
                    if getattr(p, 'layer', None) == lay and getattr(p, 'w', 0) and p.z_sig != p.z_ref)
        t = m.cu_t.get(lay, 0.035)
        if not ws:
            faces[lay] = (t * 1e-3, 0.0, 0.0)
            continue
        strip, line = loss_share(ws[0][0], ws[0][1], t)        # the narrowest port trace, over its reference
        faces[lay] = (t * 1e-3, strip, line)
    desc = ', '.join(f"{lay} {faces[lay][1]:.0%} of the strip's loss" for lay in sorted(faces) if faces[lay][1])
    notes.append(f"finish: {FINISHES[name][0]}, on the outer copper's outer face and sides ({desc or 'no port traces'})")
    return Finish(name, layers(name), faces, notes)
