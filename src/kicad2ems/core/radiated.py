"""
kicad2ems.core.radiated - what the radiated EMC questions share (the
susceptibility, kicad2ems.core.susceptibility; the emission,
kicad2ems.core.emission): each port's far field on the board, the spectra a
port is driven with, CISPR 32's radiated limits and the test site's geometry.

The ports' fields. On mom25d (PortFields.solve) the board is solved at each
frequency for a 1 V source at each port with the others shorted (one factor,
every port's currents: mom25d's excitation), and the port currents Y. With
loads Z (each port's reference impedance, or --load), a source V_s at port j
behind its load and the others loaded is

    V = (1 + Z Y)^-1 V_s,   I_term = I (1 + Z Y)^-1          (columns: ports)

so each port's terminated currents per unit source volt are one product, and
their far field r E (mom25d.farfield.far_field_dirs) the field per volt. An
antenna run's array file (<name>_<solver>_array.npz: kicad2ems.core.phased;
mom25d's per unit port voltage with Y, an FDTD solver's per unit incident wave
with its ports in their references) gives the same at its own frequencies and
directions (PortFields.load).

Reciprocity (susceptibility). A plane wave E0 p arriving from the direction
(theta, phi) puts on port j, its load z_j and the other ports loaded as above,

    V_j = 4 pi / (j k0 eta0) z_j E0 (p . F_j(theta, phi))

F_j: r E of port j's terminated field per unit source volt (Lorentz
reciprocity between the gap source and a far dipole; the image of the ground and
the other ports' loads are in F_j). A short element h above the ground with its
image: V = E0 2h sin(theta) z / (z + Zin), its open-circuit voltage E0 h_e sin
theta (h_e = 2h) through its load; per incident wave a (matched loads) the
received wave is |b_j| = 2 pi E0 |F_j^a| / (k0 eta0), which is P_r = S A_e with
A_e = lambda^2 G_r / 4 pi (G_r the realized gain): Friis.

The test site (emission). The board at h_eut over the floor, the antenna at
distance d and height h; a turntable turns the board about the vertical; the
board stands in an orientation (its top up: 'flat'; on an edge; upside down).
Below 1 GHz the floor reflects (a semi-anechoic room or an open site, CISPR
16-1-4): the field at the antenna is the direct ray's and the floor's image's,

    E = F(u_d) e^{-j k L_d} / L_d + R F(u_r) e^{-j k L_r} / L_r,
    R = diag(-1, -1, 1)

u_r the ray to the floor's reflection point (the image of a PEC floor: its
horizontal field reversed); above 1 GHz the floor is covered (FSOATS) and only
the direct ray arrives. The antenna takes the vertical (E_z) and the horizontal
(E across the line of sight) polarisation; the largest over the turntable, the
heights and the polarisations is held, as a receiver's max hold.

The board alone radiates: what it is connected to (cables, which most often
dominate a product's emission and its immunity) is left out, and over mom25d's
infinite ground nothing radiates below it, with no board edge.
"""
import math
import os

import numpy as np

from .constants import C0, ETA0

# CISPR 16-1-1's resolution bandwidths, Hz: 120 kHz in bands C and D (30 MHz to
# 1 GHz), 1 MHz above, 9 kHz in band B
def rbw(f):
    f = np.asarray(f, float)
    return np.where(f > 1e9, 1e6, np.where(f >= 30e6, 120e3, 9e3))


# ---- the signals a port is driven with -------------------------------------

def trapezoid(f0, amp, rise, duty=0.5, fmin=0.0, fmax=6e9):
    """A clock's lines (f Hz, RMS volts) in [fmin, fmax]: from 0 to amp at f0,
    its pulse duty x the period at half swing, edges rise seconds 10-90 % (a
    linear ramp of rise / 0.8), the falling as the rising:

        c_n = 2 amp d |sinc(n d)| |sinc(n f0 t_r)|      (peak; RMS c_n / sqrt 2)

    np.sinc(x) = sin(pi x) / (pi x)."""
    if f0 <= 0 or amp <= 0:
        raise ValueError("a clock needs f0 > 0 and amp > 0")
    if not 0 < duty < 1:
        raise ValueError("duty is between 0 and 1")
    tr = max(float(rise), 0.0) / 0.8
    n = np.arange(max(1, math.ceil(fmin / f0 - 1e-9)),
                  math.floor(fmax / f0 + 1e-9) + 1)
    n = n[n >= 1]
    c = 2 * amp * duty * np.abs(np.sinc(n * duty)) * np.abs(np.sinc(n * f0 * tr))
    f = n * f0
    # (sinc's zeros: 50 %'s even harmonics are 4e-17, not 0)
    keep = c > 1e-12 * (c.max() if len(c) else 0)
    return f[keep], c[keep] / np.sqrt(2)


def pattern_psd(f, rate, amp, rise):
    """One-sided PSD (V^2/Hz) of random NRZ data from 0 to amp at rate (b/s),
    edges rise seconds 10-90 %: amp^2 T / 2 sinc^2(f T) sinc^2(f t_r) (its
    mean, the DC line amp / 2, aside)."""
    T, tr = 1.0 / rate, max(float(rise), 0.0) / 0.8
    f = np.asarray(f, float)
    return amp ** 2 * T / 2 * np.sinc(f * T) ** 2 * np.sinc(f * tr) ** 2


def prbs_lines(rate, amp, rise, order, fmin, fmax):
    """A PRBS of 2^order - 1 bits repeated: its lines (f Hz, RMS volts) at k
    rate / L in [fmin, fmax]. An m-sequence's spectrum is flat, |sum_i b_i
    e^{-j 2 pi k i / L}|^2 = (L + 1) / 4 for k not a multiple of L, so each line
    is

        RMS^2 = amp^2 (L + 1) / (2 L^2) sinc^2(f / rate) sinc^2(f t_r)

    (k a multiple of L: the bit rate's harmonics, where sinc(f / rate) = 0)."""
    L = 2 ** int(order) - 1
    df = rate / L
    k = np.arange(max(1, math.ceil(fmin / df - 1e-9)),
                  math.floor(fmax / df + 1e-9) + 1)
    k = k[(k >= 1) & (k % L != 0)]
    f = k * df
    tr = max(float(rise), 0.0) / 0.8
    p = (amp ** 2 * (L + 1) / (2 * L ** 2) * np.sinc(f / rate) ** 2
         * np.sinc(f * tr) ** 2)
    return f, np.sqrt(p)


def read_spectrum(path):
    """A spectrum file's lines: (f Hz, RMS volts), one a row (CSV or spaces,
    '#' comments; a header row is skipped)."""
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.split('#')[0].strip()
            if not line:
                continue
            cells = [c for c in line.replace(',', ' ').replace(';', ' ').split()]
            try:
                rows.append((float(cells[0]), float(cells[1])))
            except (ValueError, IndexError):
                if rows:
                    raise ValueError(f"{path}: not a number: {line!r}")
    if not rows:
        raise ValueError(f"{path}: no lines (f Hz, volts RMS)")
    a = np.array(sorted(rows))
    return a[:, 0], a[:, 1]


class Signal:
    """What drives the board: lines (f, RMS volts) or a continuous spectrum
    (its PSD, read in each receiver bandwidth), or a flat amplitude at every
    frequency solved (a CW sweep: the board's field per volt)."""

    def __init__(self, kind='clock', f0=None, amp=1.0, rise=1e-9, duty=0.5,
                 rate=None, prbs=7, path=None):
        self.kind, self.f0, self.amp, self.rise = kind, f0, amp, rise
        self.duty, self.rate, self.prbs, self.path = duty, rate, prbs, path
        if kind not in ('clock', 'pattern', 'flat', 'file'):
            raise ValueError(
                f"signal: clock, pattern, flat or file, not {kind!r}")
        if kind == 'clock' and not f0:
            raise ValueError("signal clock: give its frequency (--f0, MHz)")
        if kind == 'pattern' and not rate:
            raise ValueError("signal pattern: give its rate (--rate, Gb/s)")
        if kind == 'file' and not path:
            raise ValueError("signal file: give the file (--spectrum)")

    def describe(self):
        if self.kind == 'clock':
            return (f"a clock: {self.f0 / 1e6:g} MHz, 0-{self.amp:g} V, rise "
                    f"{self.rise * 1e9:g} ns, duty {self.duty:g}")
        if self.kind == 'pattern':
            return (f"data: {self.rate / 1e9:g} Gb/s "
                    + (f"PRBS{self.prbs}" if self.prbs else "random")
                    + f", 0-{self.amp:g} V, rise {self.rise * 1e9:g} ns")
        if self.kind == 'flat':
            return f"a CW sweep: {self.amp:g} V RMS at every frequency"
        return f"the lines of {os.path.basename(self.path)}"

    def lines(self, fmin, fmax):
        """(f, RMS volts) of its lines in the band, or None (continuous or
        flat: read at the frequencies solved)."""
        if self.kind == 'clock':
            return trapezoid(self.f0, self.amp, self.rise, self.duty, fmin,
                             fmax)
        if self.kind == 'file':
            f, v = read_spectrum(self.path)
            k = (f >= fmin) & (f <= fmax)
            return f[k], v[k]
        if self.kind == 'pattern' and self.prbs:
            # lines while their spacing is at least the receiver's bandwidth
            df = self.rate / (2 ** int(self.prbs) - 1)
            if df >= rbw(fmin) and df >= rbw(fmax):
                return prbs_lines(self.rate, self.amp, self.rise, self.prbs,
                                  fmin, fmax)
        return None

    def reading(self, f):
        """The RMS volts a receiver reads at f of a continuous or flat signal:
        flat its amplitude, data its PSD in the receiver's bandwidth."""
        f = np.asarray(f, float)
        if self.kind == 'flat':
            return np.full(f.shape, float(self.amp))
        return np.sqrt(pattern_psd(f, self.rate, self.amp, self.rise) * rbw(f))


# ---- CISPR 32's radiated limits --------------------------------------------

# dBuV/m: below 1 GHz quasi-peak at 10 m, 1-6 GHz average and peak at 3 m
# (CISPR 32 tables A.4, A.5; class B A.2, A.3)
CISPR32 = {
    'A': dict(qp=((30e6, 230e6, 40.0), (230e6, 1e9, 47.0)),
              av=((1e9, 3e9, 56.0), (3e9, 6e9, 60.0)),
              pk=((1e9, 3e9, 76.0), (3e9, 6e9, 80.0))),
    'B': dict(qp=((30e6, 230e6, 30.0), (230e6, 1e9, 37.0)),
              av=((1e9, 3e9, 50.0), (3e9, 6e9, 54.0)),
              pk=((1e9, 3e9, 70.0), (3e9, 6e9, 74.0))),
}
# the quasi-peak limits at 3 m, as CISPR 32 gives them (10 m + 10 dB)
QP_3M = 10.0


def limits(cls='B', distance=3.0):
    """{detector: [(f0, f1, dBuV/m)]} of CISPR 32 class cls at distance (m):
    below 1 GHz its table at 10 m or 3 m, elsewhere 20 log10(10 / d) from 10 m;
    above 1 GHz from its 3 m (20 log10(3 / d))."""
    t = CISPR32[str(cls).upper()]
    d = float(distance)
    if abs(d - 10) < 1e-9:
        qp_add = 0.0
    elif abs(d - 3) < 1e-9:
        qp_add = QP_3M
    else:
        qp_add = 20 * math.log10(10.0 / d)
    hi_add = 20 * math.log10(3.0 / d)
    return dict(qp=[(a, b, v + qp_add) for a, b, v in t['qp']],
                av=[(a, b, v + hi_add) for a, b, v in t['av']],
                pk=[(a, b, v + hi_add) for a, b, v in t['pk']])


def limit_at(f, segs):
    """The limit (dBuV/m) at each f of [(f0, f1, value)] (a step at a shared
    edge takes the lower), nan outside them."""
    f = np.asarray(f, float)
    out = np.full(f.shape, np.nan)
    for a, b, v in segs:
        k = (f >= a) & (f <= b)
        out[k] = np.where(np.isnan(out[k]), v, np.minimum(out[k], v))
    return out


def dbuv(v):
    """dBuV (or dBuV/m) of RMS volts (V/m)."""
    return 20 * np.log10(np.maximum(np.asarray(v, float), 1e-30) / 1e-6)


# ---- directions and fields -------------------------------------------------

def unit(theta, phi):
    """Unit vectors (.., 3) of directions theta, phi (degrees)."""
    t, p = np.radians(theta), np.radians(phi)
    return np.stack([np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)],
                    -1)


def angles(u):
    """(theta, phi) degrees of unit vectors (.., 3); phi in [0, 360)."""
    u = np.asarray(u, float)
    t = np.degrees(np.arccos(np.clip(u[..., 2], -1, 1)))
    p = np.degrees(np.arctan2(u[..., 1], u[..., 0])) % 360.0
    return t, p


def cartesian(Et, Ep, theta, phi):
    """r E as a vector (.., 3) from its theta and phi components."""
    t, p = np.radians(theta), np.radians(phi)
    th = np.stack([np.cos(t) * np.cos(p), np.cos(t) * np.sin(p), -np.sin(t)],
                  -1)
    ph = np.stack([-np.sin(p), np.cos(p), np.zeros_like(p)], -1)
    return Et[..., None] * th + Ep[..., None] * ph


class Grid:
    """A far field (r E, Cartesian: (k, ntheta, nphi, 3)) on a theta x phi grid
    (degrees: theta ascending from 0, its steps as grid_angles makes them; phi
    over 360 by its step), interpolated bilinearly in its Cartesian components
    (smooth at the poles, where theta and phi's unit vectors are not), zero past
    its last theta (the lower half over an infinite ground)."""

    def __init__(self, theta, phi, E):
        self.theta, self.phi = np.asarray(theta, float), np.asarray(phi, float)
        self.E = np.asarray(E)
        self.dt = float(np.max(np.diff(self.theta)))
        self.dp = float(self.phi[1] - self.phi[0])

    def at(self, u):
        """r E (k, m, 3) in the directions u (m, 3)."""
        t, p = angles(u)
        th, n_p = self.theta, len(self.phi)
        nt = len(th)
        out = t > th[-1] + 1e-9
        i = np.clip(np.searchsorted(th, t, side='right') - 1, 0, nt - 2)
        a = np.clip((t - th[i]) / (th[i + 1] - th[i]), 0.0, 1.0)
        y = ((p - self.phi[0]) / self.dp) % n_p
        j = np.floor(y).astype(int) % n_p
        b = y - np.floor(y)
        j1 = (j + 1) % n_p
        E = self.E
        v = ((1 - a)[:, None] * ((1 - b)[:, None] * E[:, i, j] +
                                 b[:, None] * E[:, i, j1]) +
             a[:, None] * ((1 - b)[:, None] * E[:, i + 1, j] +
                           b[:, None] * E[:, i + 1, j1]))
        v[:, out] = 0
        return v


# theta's extra steps toward the board's plane (90 deg): 90 - 2 step /
# 2^(m / 2), m = 1 .. HORIZON. Over a grounded dielectric the field at grazing
# falls to 0 within a degree or so (TM's reflection -1 there): 30 mm of
# microstrip on 0.2 mm FR4 at 4 GHz 0.0113 at 88 deg, 0.0097 at 89, 4e-5 at
# 90; on a 3 degree grid the interpolation had missed it by 43 % of the
# largest, by 4.3 % with 6 steps halving from 87 deg, 2.0 % with 12 by 2^(1/2)
# from 87, 1.1 % with these (from 84)
HORIZON = 14


def grid_angles(step, lower=True):
    """(theta, phi) of the grid at step degrees: theta 0..180 (lower: the whole
    sphere) or 0..90, with HORIZON steps halving toward 90 from either side;
    phi 0..360 - step."""
    nt = int(round((180.0 if lower else 90.0) / step))
    th = np.linspace(0, 180.0 if lower else 90.0, nt + 1)
    near = 90.0 - 2 * step / 2.0 ** (np.arange(1, HORIZON + 1) / 2)
    th = np.union1d(th, near)
    if lower:
        th = np.union1d(th, 180.0 - near)
    return th, np.arange(0, 360.0 - 1e-9, step)


class PortFields:
    """Each port's far field per unit source volt, its load and the others'
    on theirs: from mom25d (solve) or an antenna run's array file (load).

        pf = PortFields.solve(settings, fmin, fmax)    # mom25d on the board
        F = pf.grid(f, step=5)       # Grid: (n ports, ntheta, nphi, 3)
        F = pf.grid(f, step=3, drive=w)  # the drive w (source volts per port)
    """

    def __init__(self, names, loads, lower, engine, freq=None):
        self.names, self.loads = list(names), np.asarray(loads, float)
        self.lower, self.engine = bool(lower), engine
        self.freq = None if freq is None else np.asarray(freq, float)
        self.seconds = dict(build=0.0, solve=0.0, field=0.0)

    # mom25d ------------------------------------------------------------
    @classmethod
    def solve(cls, s, fmin, fmax, loads=None, log=print):
        """The board of settings s on mom25d with lumped ports (its sources
        the ports' gaps, as its antenna mode has them), between fmin and fmax
        (Hz, the mesh's range)."""
        import time
        from .pipeline import board_from_settings
        from kicad2ems.mom25d.solvers import mom as M
        from kicad2ems.mom25d.api import build_board
        t0 = time.time()
        m = board_from_settings(s, log=lambda *a: None)
        if not m.ports:
            raise ValueError("the board has no ports (port footprints, or "
                             "ports = [...] in its config)")
        mesh = M._mesh_kw(s, m)
        pairs, kw = M._board_kw(m, s.mom_dmax, 'lumped', **mesh)
        metal, _ = M._metal(m, getattr(s, 'sigma', None),
                            getattr(s, 'rough', None))
        bm, sim = build_board(m.to_dict(prims=False), fmin, fmax, pairs=pairs,
                              metal=metal, log=lambda *a: None, **kw)
        names = [p.name for p in sim.ports]
        z = np.array([float(bm.port_z0.get(n, 50.0)) for n in names])
        for k, v in (loads or {}).items():
            if k not in names:
                raise ValueError(f"--load {k}: no such port "
                                 f"({', '.join(names)})")
            z[names.index(k)] = float(v)
        self = cls(names, z, sim.st.bottom != 'pec', 'mom25d')
        self.sim, self.model, self.board = sim, bm, m
        self.stack = mesh.get('stack', 'ground')
        self.unknowns = int(sim.Ne + sim.Nv)
        self.pairs = [(n, [p.name for p in legs])
                      for n, legs in m.user_ports if len(legs) == 2]
        self._held = (None, None)
        self.seconds['build'] = time.time() - t0
        log(f"{cls.__name__}: mom25d: {self.unknowns:,} unknowns, ports "
            f"{', '.join(f'{n} ({zz:g} ohm)' for n, zz in zip(names, z))}; "
            f"stack {self.stack}"
            + (" (radiates above and below)" if self.lower else
               " (over an infinite ground: nothing below it)"))
        return self

    def terminated(self, f):
        """(I_term (N, n): the currents per unit source volt at each port, the
        others on their loads; Y (n, n)) at f (mom25d)."""
        import time
        if self._held[0] == f:
            return self._held[1]
        t0 = time.time()
        sim = self.sim
        I = sim.currents(f, sim.excitation())
        n = len(self.names)
        Y = np.array([np.atleast_1d(sim.port_current(I, j)) for j in range(n)])
        M = np.linalg.inv(np.eye(n) + self.loads[:, None] * Y)
        out = (I @ M, Y)
        self._held = (f, out)
        self.seconds['solve'] += time.time() - t0
        return out

    def grid(self, f, step, drive=None):
        """Grid of each port's r E per unit source volt (drive None: (n, ...))
        or the drive's (source volts per port: (1, ...)) at f."""
        import time
        if self.engine != 'mom25d':
            return self._grid_file(f, drive)
        from kicad2ems.mom25d.farfield import far_field_dirs
        I, _ = self.terminated(f)
        if drive is not None:
            I = I @ np.asarray(drive, complex)[:, None]
        t0 = time.time()
        th, ph = grid_angles(step, self.lower)
        T, P = np.meshgrid(th, ph, indexing='ij')
        Et, Ep = far_field_dirs(self.sim, I, f, T.ravel(), P.ravel())
        E = cartesian(Et, Ep, T.ravel()[None], P.ravel()[None])
        self.seconds['field'] += time.time() - t0
        return Grid(th, ph, E.reshape((I.shape[1],) + T.shape + (3,)))

    # an antenna run's array file ----------------------------------------
    @classmethod
    def load(cls, path, loads=None):
        """An antenna run's array file (kicad2ems.core.phased: mom25d's or an
        FDTD solver's): its frequencies and directions, the ports in their
        references (an FDTD run's) or any loads (mom25d's Y)."""
        from . import phased
        d = phased.load(path)
        names = list(d['ports'])
        z = np.asarray(d['zc'], float).copy()
        for k, v in (loads or {}).items():
            if k not in names:
                raise ValueError(f"--load {k}: no such port in {path}")
            z[names.index(k)] = float(v)
        if 'Y' not in d and loads:
            raise ValueError(
                f"{os.path.basename(path)}: an FDTD run's fields are per "
                "incident wave with every port in its reference: --load "
                "needs mom25d's (Y)")
        if 'excited' in d and not np.all(d['excited']):
            run = [n for n, e in zip(names, d['excited']) if e]
            raise ValueError(
                f"{os.path.basename(path)}: only {', '.join(run)} were "
                "excited: every port's field is needed (drive them all)")
        th = np.asarray(d['theta'], float)
        ph = np.asarray(d['phi'], float)
        if ph[-1] - ph[0] >= 360 - 1e-6:  # 360 is 0 again
            ph, cut = ph[:-1], True
        else:
            cut = False
        self = cls(names, z, th[-1] > 90.0 + 1e-6, f"{os.path.basename(path)}",
                   freq=d['freq'])
        self._file = d
        self._ph_cut = cut
        self._th, self._ph = th, ph
        self.pairs = []
        self.unknowns = None
        return self

    def _grid_file(self, f, drive):
        d = self._file
        i = int(np.argmin(np.abs(self.freq - f)))
        Et, Ep = d['Eth'][i], d['Eph'][i]   # (n, nphi, ntheta)
        if self._ph_cut:
            Et, Ep = Et[:, :-1], Ep[:, :-1]
        n = len(self.names)
        if 'Y' in d:
            Y = d['Y'][i]
            M = np.linalg.inv(np.eye(n) + self.loads[:, None] * Y)
            Et = np.tensordot(M.T, Et, 1)
            Ep = np.tensordot(M.T, Ep, 1)
        else:  # per unit incident wave: per source volt / (2 sqrt z)
            s = 1 / (2 * np.sqrt(self.loads))
            Et, Ep = Et * s[:, None, None], Ep * s[:, None, None]
        if drive is not None:
            w = np.asarray(drive, complex)
            Et = np.tensordot(w, Et, 1)[None]
            Ep = np.tensordot(w, Ep, 1)[None]
        T, P = np.meshgrid(self._th, self._ph, indexing='ij')
        E = cartesian(np.transpose(Et, (0, 2, 1)), np.transpose(Ep, (0, 2, 1)),
                      T[None], P[None])
        return Grid(self._th, self._ph, E)


# ---- the test site ---------------------------------------------------------

# the board's orientations on the turntable: room = R board (z up); the
# turntable turns it about the room's vertical
ORIENTATIONS = {
    'flat': np.eye(3),                                   # its top up
    'flipped': np.diag([1.0, -1.0, -1.0]),               # its bottom up
    'edge-x': np.array([[0, 0, -1.0], [0, 1, 0], [1, 0, 0]]),   # its +x up
    'edge-x-down': np.array([[0, 0, 1.0], [0, 1, 0], [-1, 0, 0]]),
    'edge-y': np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]]),   # its +y up
    'edge-y-down': np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]]),
}
ORIENTATION_SETS = {'flat': ('flat',), 'upright': ('edge-y',),
                    'all': tuple(ORIENTATIONS)}
REFLECT = np.array([-1.0, -1.0, 1.0])  # a PEC floor's image: horizontal E


def _rz(b):
    c, s = math.cos(b), math.sin(b)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def site(field, f, distance=3.0, h_eut=0.8, heights=None, turns=None,
         orientations=('flat',), floor=True):
    """The field at the antenna (V/m per the field's source volt), the largest
    over the orientations, the turntable's angles (deg), the heights (m) and the
    two polarisations: dict(E, orientation, turn, height, pol, E_by_orientation).
    field(u) gives r E (m, 3) in the board's directions u (m, 3) (a Grid's
    at, the first column). floor: its reflection (else free space)."""
    heights = np.arange(1.0, 4.0 + 1e-9, 0.1) if heights is None else \
        np.asarray(heights, float)
    turns = np.arange(0.0, 360.0, 5.0) if turns is None else \
        np.asarray(turns, float)
    k = 2 * np.pi * f / C0
    d = float(distance)
    # rays (room): direct and to the floor's reflection point, per height
    rd = np.stack([np.full_like(heights, d), np.zeros_like(heights),
                   heights - h_eut], -1)
    rr = np.stack([np.full_like(heights, d), np.zeros_like(heights),
                   -heights - h_eut], -1)
    Ld, Lr = np.linalg.norm(rd, axis=1), np.linalg.norm(rr, axis=1)
    ud, ur = rd / Ld[:, None], rr / Lr[:, None]
    best, by = None, {}
    for name in orientations:
        R0 = ORIENTATIONS[name]
        Rs = np.array([_rz(math.radians(b)) @ R0 for b in turns])  # (nt,3,3)
        # board-frame directions: R^T u (nt, nh, 3)
        bd = np.einsum('tji,hj->thi', Rs, ud)
        Fd = np.asarray(field(bd.reshape(-1, 3))).reshape(bd.shape)
        Fd = np.einsum('tij,thj->thi', Rs, Fd)     # back in the room
        E = Fd * (np.exp(-1j * k * Ld) / Ld)[None, :, None]
        if floor:
            br = np.einsum('tji,hj->thi', Rs, ur)
            Fr = np.asarray(field(br.reshape(-1, 3))).reshape(br.shape)
            Fr = np.einsum('tij,thj->thi', Rs, Fr) * REFLECT
            E = E + Fr * (np.exp(-1j * k * Lr) / Lr)[None, :, None]
        pol = np.stack([np.abs(E[..., 2]), np.abs(E[..., 1])], -1)  # V, H
        i = np.unravel_index(int(np.argmax(pol)), pol.shape)
        by[name] = float(pol[i])
        if best is None or pol[i] > best['E']:
            best = dict(E=float(pol[i]), orientation=name,
                        turn=float(turns[i[0]]), height=float(heights[i[1]]),
                        pol='vertical' if i[2] == 0 else 'horizontal')
    best['E_by_orientation'] = by
    return best


def two_ray(F_dir, F_ref, f, distance, h_eut, h):
    """The field at height h of the direct and reflected rays of a source whose
    room-frame r E toward them are F_dir, F_ref (3,): site's sum, for a check."""
    k = 2 * np.pi * f / C0
    Ld = math.hypot(distance, h - h_eut)
    Lr = math.hypot(distance, h + h_eut)
    return (np.asarray(F_dir) * np.exp(-1j * k * Ld) / Ld
            + REFLECT * np.asarray(F_ref) * np.exp(-1j * k * Lr) / Lr)


# ---- reciprocity -----------------------------------------------------------

def receive_grid(G, loads, f, E0=1.0):
    """(V_theta, V_phi) on Grid G's directions: each port's voltage on its
    load (V, complex; (n, ntheta, nphi)) for a plane wave of E0 V/m in theta
    or phi polarisation arriving from there, from G's r E per unit source volt
    of each port (n, ...): V = 4 pi / (j k0 eta0) z E0 (p . F)."""
    k0 = 2 * np.pi * f / C0
    T, P = np.meshgrid(G.theta, G.phi, indexing='ij')
    t, p = np.radians(T), np.radians(P)
    th = np.stack([np.cos(t) * np.cos(p), np.cos(t) * np.sin(p), -np.sin(t)],
                  -1)
    ph = np.stack([-np.sin(p), np.cos(p), np.zeros_like(p)], -1)
    c = 4 * np.pi / (1j * k0 * ETA0) * E0 * np.asarray(loads, float)
    Vt = c[:, None, None] * np.einsum('kabi,abi->kab', G.E, th)
    Vp = c[:, None, None] * np.einsum('kabi,abi->kab', G.E, ph)
    return Vt, Vp


def worst_linear(Vt, Vp):
    """(|V| largest over linear polarisations, its angle from theta's unit
    vector, deg): max over alpha of |Vt cos alpha + Vp sin alpha|, the larger
    eigenvalue of [[|Vt|^2, c], [c, |Vp|^2]], c = Re(Vt conj Vp)."""
    a, b = np.abs(Vt) ** 2, np.abs(Vp) ** 2
    c = np.real(Vt * np.conj(Vp))
    lam = (a + b) / 2 + np.sqrt(((a - b) / 2) ** 2 + c ** 2)
    return np.sqrt(lam), np.degrees(0.5 * np.arctan2(2 * c, a - b))


def log_grid(fmin, fmax, points):
    """points frequencies from fmin to fmax, equally spaced in log f."""
    if points < 2 or fmax <= fmin:
        return np.array([float(fmin)])
    return np.geomspace(fmin, fmax, int(points))


# adaptive's: dB a point may sit off its neighbours' line in log f before the
# intervals beside it are halved; the narrowest interval it halves (ratio);
# how far below a curve's top a point may lie and still be refined (dB); the
# frequencies it starts with, equally spaced in log f. The emission's field per
# volt against 241 frequencies (30 MHz - 6 GHz; the shortfall of the samples'
# line in dB over log f, largest / within 20 dB of the top): rf_serpentine (2.8k
# unknowns) 61 equally spaced 2.23 / 0.21 dB, these with 41 0.67 / 0.27;
# rf_branchline (2.9k) 2.26 / 2.26 against 0.25 / 0.25. Refining only above
# the line (peaks) had left a peak between two samples (branchline 4.14 dB); no
# window spent the samples on notches (2.92 dB with 41), 20 dB stopped short
# (serpentine 1.16 dB in 28)
ADAPT_DB, ADAPT_MIN, ADAPT_WINDOW, ADAPT_START = 0.5, 1.003, 30.0, 15


def adaptive(evaluate, fmin, fmax, budget, start=None, tol_db=ADAPT_DB,
             window_db=ADAPT_WINDOW, echo=None):
    """(f, values): evaluate(f) (an array of values above 0, each a curve in
    dB over log f) at `start` frequencies equally spaced in log f (default a
    third of the budget), then at the midpoint in log f of each interval beside
    a point its two neighbours' straight line (dB over log f) misses by more
    than tol_db, either way (a peak between two samples can leave the one
    between them below its neighbours' line), the worst first, until none does
    or the budget is spent. A point more than window_db below the largest
    sample of every curve (a notch, a quiet band) is not refined. Where the
    curves bend (a resonance) the samples gather; where they are straight (a
    field rising 20 dB a decade) none is added."""
    start = start or max(5, int(budget) // 3)
    f = list(log_grid(fmin, fmax, min(start, budget)))
    v = [np.atleast_1d(np.asarray(evaluate(x), float)) for x in f]
    while len(f) < budget:
        lf = np.log(f)
        d = 20 * np.log10(np.maximum(np.array(v), 1e-300))   # (n, k)
        top = d.max(0)
        miss = np.zeros(len(f))
        for i in range(1, len(f) - 1):
            a = (lf[i] - lf[i - 1]) / (lf[i + 1] - lf[i - 1])
            off = np.abs(d[i] - ((1 - a) * d[i - 1] + a * d[i + 1]))
            # only the curves near their top here count
            near = d[i - 1:i + 2].max(0) >= top - window_db
            miss[i] = np.max(np.where(near, off, 0.0))
        cand = {}
        for i in np.argsort(miss)[::-1]:
            if miss[i] <= tol_db:
                break
            for j in (i - 1, i):       # the intervals beside point i
                if f[j + 1] / f[j] > ADAPT_MIN:
                    cand.setdefault(j, miss[i])
        if not cand:
            break
        new = []
        for j in sorted(cand, key=cand.get, reverse=True):
            if len(f) + len(new) >= budget:
                break
            new.append(math.sqrt(f[j] * f[j + 1]))
        for x in new:
            k = int(np.searchsorted(f, x))
            f.insert(k, x)
            v.insert(k, np.atleast_1d(np.asarray(evaluate(x), float)))
        if echo:
            echo(f"{len(new)} more where the curves bend ({len(f)} of "
                 f"{budget}, the worst {max(cand.values()):.1f} dB off its "
                 "neighbours' line)")
    return np.array(f), np.array(v)


def parse_loads(items):
    """{port: ohms} of PORT=OHMS items."""
    out = {}
    for it in items or []:
        name, _, v = str(it).partition('=')
        try:
            out[name.strip()] = float(v)
        except ValueError:
            raise ValueError(f"--load {it}: write PORT=OHMS")
        if out[name.strip()] <= 0:
            raise ValueError(f"--load {it}: a load above 0 ohm")
    return out


# ---- what both tools take ---------------------------------------------------

def add_board_args(p, fmin, fmax, points, step):
    """The board, its solve and the ports' loads: the arguments both tools
    take."""
    p.add_argument('board', nargs='?', default=None,
                   help=".kicad_pcb or a config (.toml) to solve on mom25d "
                   "(its ports: the board's port footprints, or the config's)")
    p.add_argument('--array', default=None,
                   help="an antenna run's array file (<name>_<solver>_array"
                   ".npz: each port's far field, mom25d's or an FDTD solver's "
                   "with every port excited) in place of a solve: its "
                   "frequencies and directions")
    p.add_argument('--fmin', type=float, default=fmin,
                   help=f"GHz (default {fmin:g})")
    p.add_argument('--fmax', type=float, default=fmax,
                   help=f"GHz (default {fmax:g})")
    p.add_argument('--points', type=int, default=points,
                   help=f"frequencies solved, equally spaced in log f "
                   f"(default {points})")
    p.add_argument('--step', type=float, default=step,
                   help=f"degrees between the far field's directions "
                   f"(default {step:g})")
    p.add_argument('--load', action='append', default=[],
                   help="PORT=OHMS: a port's load (and source resistance) in "
                   "place of its reference impedance (repeat)")
    p.add_argument('--mom-dmax', type=float, default=None,
                   help="mm, mom25d's largest cell (default: its own)")
    p.add_argument('--mom-stack', default=None,
                   choices=('auto', 'ground', 'whole'),
                   help="mom25d's stack (default: its own; whole for a board "
                   "with no solid ground)")
    p.add_argument('--out', default=None, help="output folder")


def settings_of(a, out):
    """kicad2ems's settings for the board of a's arguments (its config's when
    a config), mom25d its solver."""
    from .config import build_settings
    if not a.board:
        raise ValueError("give the board (or a config), or --array")
    cli = dict(solvers='mom25d', out=os.path.join(out, 'solve'),
               no_report=True, lean=True, fmin=a.fmin, fmax=a.fmax)
    for k in ('mom_dmax', 'mom_stack'):
        if getattr(a, k, None) is not None:
            cli[k] = getattr(a, k)
    if a.board.lower().endswith('.toml'):
        return build_settings(cli, config=a.board)
    return build_settings(dict(cli, board=a.board))


def port_fields(a, out, log):
    """PortFields of a's arguments: the array file's, or the board on
    mom25d."""
    loads = parse_loads(a.load)
    if a.array:
        return PortFields.load(a.array, loads)
    s = settings_of(a, out)
    return PortFields.solve(s, a.fmin * 1e9, a.fmax * 1e9, loads, log)


def frequencies(pf, a, given=None):
    """The frequencies solved (Hz): given (a signal's lines), the array file's
    in the band, or a.points equally spaced in log f."""
    lo, hi = a.fmin * 1e9, a.fmax * 1e9
    if pf.freq is not None:
        f = pf.freq[(pf.freq >= lo * (1 - 1e-9)) & (pf.freq <= hi * (1 + 1e-9))]
        if not len(f):
            raise ValueError(
                f"{pf.engine}: no frequency between {a.fmin:g} and "
                f"{a.fmax:g} GHz (it has {pf.freq.min() / 1e9:g}-"
                f"{pf.freq.max() / 1e9:g} GHz)")
        return f
    if given is not None:
        return np.asarray(given, float)
    if hi <= lo:
        raise ValueError("--fmax is not above --fmin")
    return log_grid(lo, hi, a.points)


def sweep(pf, a, one, value, given=None, echo=None):
    """(f, [one(f)]) in order of f: at given frequencies (a signal's lines) or
    the array file's, else adaptive's (a.points at most, ADAPT_START equally
    spaced in log f first) on value(one(f)), the curves whose peaks the samples
    follow."""
    held = {}

    def ev(f):
        if f not in held:
            held[f] = one(f)
        return value(held[f])
    if pf.freq is not None or given is not None:
        for f in frequencies(pf, a, given):
            ev(float(f))
    else:
        lo, hi = a.fmin * 1e9, a.fmax * 1e9
        if hi <= lo:
            raise ValueError("--fmax is not above --fmin")
        adaptive(ev, lo, hi, a.points, start=min(ADAPT_START, a.points),
                 echo=echo)
    f = sorted(held)
    return np.array(f), [held[x] for x in f]


def name_of(a):
    """The result files' stem: the board's name."""
    src = a.array or a.board or 'board'
    base = os.path.basename(src)
    for suf in ('_array.npz', '.kicad_pcb', '.toml', '.json', '.npz'):
        if base.endswith(suf):
            return base[:-len(suf)]
    return os.path.splitext(base)[0]
