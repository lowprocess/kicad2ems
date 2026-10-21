"""
Microstrip line models on one dielectric layer over a ground plane, and the substrate presets.

Single line: Hammerstad & Jensen (1980), with strip-thickness correction; dispersion of eeff after
Kirschning & Jansen (1982). Coupled pair: Kirschning & Jansen (1984), quasi-static even / odd modes, each
mode given the single-line dispersion law. Open end: Kirschning, Jansen & Koster (1981). Series gap:
Garg & Bahl (1978). Dimensions are in mm, frequencies in Hz.

    sub = Substrate.preset('RO4003C-0.508')
    w50 = sub.width(50.0)
    z0, ee = sub.line(w50)
    ze, zo, eee, eeo = sub.coupled(w, s)
    w, s = sub.coupled_synth(ze, zo)
"""
import math

import numpy as np
from dataclasses import dataclass, replace

C0 = 299792458.0
ETA0 = 376.730313668
MU0 = 4e-7 * math.pi

# name: (h mm, er, tan d, material)  er / tan d are the design values near a few GHz
PRESETS = {
    'FR4': (1.6, 4.3, 0.02, 'FR4'),            # the usual 1.6 mm two-layer board (FR4-1.6)
    'FR4-0.8': (0.8, 4.3, 0.02, 'FR4'),
    'FR4-1.6': (1.6, 4.3, 0.02, 'FR4'),
    'FR4-0.2': (0.2, 4.2, 0.02, 'FR4'),
    'RO4003C-0.203': (0.203, 3.55, 0.0027, 'RO4003C'),
    'RO4003C-0.305': (0.305, 3.55, 0.0027, 'RO4003C'),
    'RO4003C-0.508': (0.508, 3.55, 0.0027, 'RO4003C'),
    'RO4003C-0.813': (0.813, 3.55, 0.0027, 'RO4003C'),
    'RO4350B-0.254': (0.254, 3.66, 0.0037, 'RO4350B'),
    'RO4350B-0.508': (0.508, 3.66, 0.0037, 'RO4350B'),
    'RO4350B-0.762': (0.762, 3.66, 0.0037, 'RO4350B'),
    'RT5880-0.254': (0.254, 2.20, 0.0009, 'RT5880'),
    'RT5880-0.508': (0.508, 2.20, 0.0009, 'RT5880'),
    'RT5880-0.787': (0.787, 2.20, 0.0009, 'RT5880'),
    'RO3010-0.635': (0.635, 11.2, 0.0022, 'RO3010'),
    'Alumina-0.635': (0.635, 9.8, 0.0001, 'Alumina'),
}


def _hj_z01(u):
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _hj_eeff(u, er):
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def _bisect(fn, target, lo, hi, n=100):
    """x in [lo, hi] with fn(x) = target (fn monotonic), geometric bisection for positive ranges."""
    flo = fn(lo) - target
    for _ in range(n):
        mid = math.sqrt(lo * hi)
        fm = fn(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return math.sqrt(lo * hi)


@dataclass
class Substrate:
    h: float                 # mm dielectric height
    er: float
    tand: float = 0.0
    t: float = 0.035         # mm copper thickness
    sigma: float = 5.8e7     # S/m conductor
    name: str = 'custom'
    material: str = 'custom'
    min_width: float = 0.1   # mm fabrication limits (the CLI refuses designs below them)
    min_gap: float = 0.1

    @classmethod
    def preset(cls, name, **kw):
        key = {k.lower(): k for k in PRESETS}.get(name.lower())
        if key is None:
            raise ValueError(f"unknown substrate {name!r}; presets: {', '.join(PRESETS)}")
        h, er, tand, mat = PRESETS[key]
        return cls(h=h, er=er, tand=tand, name=key, material=mat, **kw)

    def with_(self, **kw):
        return replace(self, **kw)

    # ---------------------------------------------------------------- single line
    def line(self, w):
        """(Z0, eeff) quasi-static of a strip of width w (mm), thickness self.t."""
        h, er = self.h, self.er
        u = w / h
        du1 = dur = 0.0
        if self.t > 0:
            th = self.t / h
            du1 = th / math.pi * math.log(1 + 4 * math.e / (th / math.tanh(math.sqrt(6.517 * u)) ** 2))
            dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(max(er - 1, 0.0)))) * du1
        u1, ur = u + du1, u + dur
        ee = _hj_eeff(ur, er)
        return _hj_z01(ur) / math.sqrt(ee), ee * (_hj_z01(u1) / _hj_z01(ur)) ** 2

    def disperse(self, ee0, w, f):
        """eeff at f (Hz, scalar or array) of a mode with quasi-static eeff ee0 on a strip of width w
        (Kirschning-Jansen)."""
        er, u = self.er, w / self.h
        fn = f * 1e-9 * self.h                                 # GHz mm
        p1 = 0.27488 + (0.6315 + 0.525 / (1 + 0.0157 * fn) ** 20) * u - 0.065683 * math.exp(-8.7513 * u)
        p2 = 0.33622 * (1 - math.exp(-0.03442 * er))
        p3 = 0.0363 * math.exp(-4.6 * u) * (1 - np.exp(-(fn / 38.7) ** 4.97))
        p4 = 1 + 2.751 * (1 - math.exp(-(er / 15.916) ** 8))
        p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
        return er - (er - ee0) / (1 + p)

    def eeff(self, w, f):
        return self.disperse(self.line(w)[1], w, f)

    def width(self, z0):
        """Strip width (mm) for impedance z0."""
        return _bisect(lambda w: -self.line(w)[0], -z0, 1e-3 * self.h, 60 * self.h)

    def guided_wavelength(self, w, f):
        return C0 / (f * math.sqrt(self.eeff(w, f))) * 1e3

    # ---------------------------------------------------------------- coupled pair
    def coupled(self, w, s):
        """(Ze, Zo, eeff_even, eeff_odd) of a symmetric edge-coupled pair (Kirschning-Jansen 1984)."""
        er = self.er
        u, g = w / self.h, max(s, 1e-6) / self.h
        ee1 = _hj_eeff(u, er)
        z1 = _hj_z01(u) / math.sqrt(ee1)
        v = u * (20 + g * g) / (10 + g * g) + g * math.exp(-g)
        eee = _hj_eeff(v, er)
        d = 0.593 + 0.694 * math.exp(-0.562 * u)
        bo = 0.747 * er / (0.15 + er)
        co = bo - (bo - 0.207) * math.exp(-0.414 * u)
        ao = 0.7287 * (ee1 - (er + 1) / 2) * (1 - math.exp(-0.179 * u))
        eeo = ((er + 1) / 2 + ao - ee1) * math.exp(-co * g ** d) + ee1
        q1 = 0.8695 * u ** 0.194
        q2 = 1 + 0.7519 * g + 0.189 * g ** 2.31
        q3 = 0.1975 + (16.6 + (8.4 / g) ** 6) ** -0.387 + math.log(g ** 10 / (1 + (g / 3.4) ** 10)) / 241
        q4 = q1 / q2 * 2 / (math.exp(-g) * u ** q3 + (2 - math.exp(-g)) * u ** -q3)
        ze = math.sqrt(ee1 / eee) * z1 / (1 - z1 * math.sqrt(ee1) * q4 / ETA0)
        q5 = 1.794 + 1.14 * math.log(1 + 0.638 / (g + 0.517 * g ** 2.43))
        q6 = 0.2305 + math.log(g ** 10 / (1 + (g / 5.8) ** 10)) / 281.3 + math.log(1 + 0.598 * g ** 1.154) / 5.1
        q7 = (10 + 190 * g * g) / (1 + 82.3 * g ** 3)
        q8 = math.exp(-6.5 - 0.95 * math.log(g) - (g / 0.15) ** 5)
        q9 = math.log(q7) * (q8 + 1 / 16.5)
        q10 = (q2 * q4 - q5 * math.exp(math.log(u) * q6 * u ** -q9)) / q2
        zo = math.sqrt(ee1 / eeo) * z1 / (1 - z1 * math.sqrt(ee1) * q10 / ETA0)
        return ze, zo, eee, eeo

    def coupled_synth(self, ze, zo):
        """(w, s) of the pair with even / odd impedances ze > zo. Newton on (log w, log s)."""
        if ze <= zo:
            raise ValueError("Ze must exceed Zo")
        lw = math.log(self.width(math.sqrt(ze * zo)))
        k = (ze - zo) / (ze + zo)
        ls = math.log(self.h * max(0.05, min(3.0, 0.8 * math.exp(-12 * k) + 0.05)))
        tgt = (math.log(ze), math.log(zo))

        def res(a, b):
            e, o, _, _ = self.coupled(math.exp(a), math.exp(b))
            return math.log(e) - tgt[0], math.log(o) - tgt[1]

        for _ in range(60):
            r = res(lw, ls)
            if abs(r[0]) + abs(r[1]) < 1e-10:
                break
            d = 1e-6
            ra, rb = res(lw + d, ls), res(lw, ls + d)
            j = ((ra[0] - r[0]) / d, (rb[0] - r[0]) / d, (ra[1] - r[1]) / d, (rb[1] - r[1]) / d)
            det = j[0] * j[3] - j[1] * j[2]
            dw = (-r[0] * j[3] + r[1] * j[1]) / det
            ds = (-j[0] * r[1] + j[2] * r[0]) / det
            stp = max(abs(dw), abs(ds))
            if stp > 0.5:
                dw, ds = dw * 0.5 / stp, ds * 0.5 / stp
            lw, ls = lw + dw, ls + ds
        w, s = math.exp(lw), math.exp(ls)
        e, o, _, _ = self.coupled(w, s)
        if abs(e / ze - 1) > 1e-3 or abs(o / zo - 1) > 1e-3:
            raise ValueError(f"no coupled pair found for Ze={ze:.1f}, Zo={zo:.1f} ohm on {self.name}")
        return w, s

    # ---------------------------------------------------------------- discontinuities
    def open_end(self, w):
        """Open-end length extension (mm), Kirschning, Jansen & Koster (1981)."""
        er, u = self.er, w / self.h
        ee = self.line(w)[1]
        x1 = 0.434907 * (ee ** 0.81 + 0.26) / (ee ** 0.81 - 0.189) * (u ** 0.8544 + 0.236) / (u ** 0.8544 + 0.87)
        x2 = 1 + u ** 0.371 / (2.358 * er + 1)
        x3 = 1 + 0.5274 * math.atan(0.084 * u ** (1.9413 / x2)) / ee ** 0.9236
        x4 = 1 + 0.0377 * math.atan(0.067 * u ** 1.456) * (6 - 5 * math.exp(0.036 * (1 - er)))
        x5 = 1 - 0.218 * math.exp(-7.5 * u)
        return self.h * x1 * x3 * x5 / x4

    def step(self, w1, w2):
        """Width step w1 -> w2: the wider strip's length extension (mm) at the step (Edwards: the open
        end of the wider strip, scaled by the part of its width not covered, 1 - wn / ww)."""
        wn, ww = sorted((w1, w2))
        return self.open_end(ww) * (1 - wn / ww)

    def gap(self, w, s):
        """(Cp, Cg) in F of a series gap s in a strip of width w: shunt capacitance at each side and the
        series capacitance across (Garg & Bahl; fitted for 0.5 <= w/h <= 2, 0.1 <= s/w <= 1)."""
        r, sw = w / self.h, s / w
        mo = r * (0.619 * math.log10(r) - 0.3853)
        ko = 4.26 - 1.453 * math.log10(r)
        if sw <= 0.3:
            me, ke = 0.8675, 2.043 * r ** 0.12
        else:
            me, ke = 1.565 / r ** 0.16 - 1, 1.97 - 0.03 / r
        wm = w * 1e-3
        co = sw ** mo * math.exp(ko) * (self.er / 9.6) ** 0.8 * wm * 1e-12
        ce = 12 * sw ** me * math.exp(ke) * (self.er / 9.6) ** 0.9 * wm * 1e-12
        return 0.5 * ce, 0.5 * co - 0.25 * ce

    def gap_for(self, w, cg):
        """Gap s (mm) giving series capacitance cg (F) in a strip of width w (ValueError if even a gap of
        0.02 w is too wide)."""
        smin = 0.02 * w
        if cg > self.gap(w, smin)[1]:
            raise ValueError(f"a gap in a {w:.2f} mm strip gives at most {self.gap(w, smin)[1] * 1e12:.3f} pF "
                             f"({cg * 1e12:.3f} pF needed)")
        return _bisect(lambda s: -self.gap(w, s)[1], -cg, smin, 3.0 * w)

    def via_inductance(self, d):
        """Inductance (H) of a via of drill d (mm) through the substrate."""
        h = self.h * 1e-3
        r = d * 1e-3 / 2
        return MU0 / (2 * math.pi) * (h * math.log((h + math.sqrt(r * r + h * h)) / r)
                                      + 1.5 * (r - math.sqrt(r * r + h * h)))

    # ---------------------------------------------------------------- loss
    def alpha(self, w, z0, ee, f):
        """Attenuation (Np/m) of a mode of impedance z0, eeff ee on a strip of width w: dielectric plus a
        simple conductor term (strip resistance Rs / w, ground plane neglected, roughness ignored)."""
        k0 = 2 * math.pi * f / C0
        er = self.er
        ad = k0 * er * (ee - 1) * self.tand / (2 * math.sqrt(ee) * (er - 1)) if er > 1 else 0.0
        ac = 0.0
        if self.sigma and math.isfinite(self.sigma):
            rs = math.sqrt(math.pi * f * MU0 / self.sigma)
            ac = rs / (z0 * w * 1e-3)
        return ad + ac

    def lossless(self):
        return replace(self, tand=0.0, sigma=0.0)
