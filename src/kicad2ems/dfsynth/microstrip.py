"""
Microstrip line models on one dielectric layer over a ground plane, and the
substrate presets.

Single line: Hammerstad & Jensen (1980), with strip-thickness correction;
dispersion of eeff after Kirschning & Jansen (1982). Coupled pair: Kirschning &
Jansen (1984), quasi-static even / odd modes, each mode given the single-line
dispersion law. Open end: Kirschning, Jansen & Koster (1981). Series gap:
Garg & Bahl (1978). Dimensions are in mm, frequencies in Hz. The line, the pair
and the ends are kicad2ems's own closed forms (kicad2ems.core.lines), so the
synthesis and the line model that checks it agree.

    sub = Substrate.preset('RO4003C-0.508')
    w50 = sub.width(50.0)
    z0, ee = sub.line(w50)
    ze, zo, eee, eeo = sub.coupled(w, s)
    w, s = sub.coupled_synth(ze, zo)
"""
import math

import numpy as np
from dataclasses import dataclass, replace

from ..core import constants
from ..core.lines import formulas
from ..core.lines.coupled import coupled_microstrip

# (ETA0: circuit.py)
C0, ETA0, MU0 = constants.C0, constants.ETA0, constants.MU0
# (the tests compare them with kicad2ems's)
_hj_z01, _hj_eeff = formulas._hj_z01, formulas._hj_eeff

# name: (h mm, er, tan d, material)  er / tan d are the design values near a few
# GHz
PRESETS = {
    # the usual 1.6 mm two-layer board (FR4-1.6)
    'FR4': (1.6, 4.3, 0.02, 'FR4'),
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


def _bisect(fn, target, lo, hi, n=100):
    """x in [lo, hi] with fn(x) = target (fn monotonic), geometric bisection for
    positive ranges."""
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
    # mm fabrication limits (the CLI refuses designs below them)
    min_width: float = 0.1
    min_gap: float = 0.1

    @classmethod
    def preset(cls, name, **kw):
        key = {k.lower(): k for k in PRESETS}.get(name.lower())
        if key is None:
            raise ValueError(
                f"unknown substrate {name!r}; presets: {', '.join(PRESETS)}")
        h, er, tand, mat = PRESETS[key]
        return cls(h=h, er=er, tand=tand, name=key, material=mat, **kw)

    def with_(self, **kw):
        return replace(self, **kw)

    # ---------------------------------------------------------------- single
    # line
    def line(self, w):
        """(Z0, eeff) quasi-static of a strip of width w (mm), thickness
        self.t."""
        return formulas.microstrip(w, self.h, self.er, self.t)

    def disperse(self, ee0, w, f):
        """eeff at f (Hz, scalar or array) of a mode with quasi-static eeff ee0
        on a strip of width w (Kirschning-Jansen)."""
        er, u = self.er, w / self.h
        fn = f * 1e-9 * self.h                                 # GHz mm
        p1 = (0.27488 +
              (0.6315 + 0.525 /
               (1 + 0.0157 * fn)**20) * u - 0.065683 * math.exp(-8.7513 * u))
        p2 = 0.33622 * (1 - math.exp(-0.03442 * er))
        p3 = 0.0363 * math.exp(-4.6 * u) * (1 - np.exp(-(fn / 38.7) ** 4.97))
        p4 = 1 + 2.751 * (1 - math.exp(-(er / 15.916) ** 8))
        p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
        return er - (er - ee0) / (1 + p)

    def eeff(self, w, f):
        return self.disperse(self.line(w)[1], w, f)

    def width(self, z0):
        """Strip width (mm) for impedance z0."""
        return _bisect(lambda w: -self.line(w)[0], -z0, 1e-3 * self.h,
                       60 * self.h)

    # ---------------------------------------------------------------- coupled
    # pair
    def coupled(self, w, s):
        """(Ze, Zo, eeff_even, eeff_odd) of a symmetric edge-coupled pair
        (Kirschning-Jansen 1984)."""
        return coupled_microstrip(w, s, self.h, self.er)

    def coupled_synth(self, ze, zo):
        """(w, s) of the pair with even / odd impedances ze > zo. Newton on (log
        w, log s)."""
        if ze <= zo:
            raise ValueError("Ze must exceed Zo")
        lw = math.log(self.width(math.sqrt(ze * zo)))
        k = (ze - zo) / (ze + zo)
        ls = math.log(
            self.h * max(0.05, min(3.0, 0.8 * math.exp(-12 * k) + 0.05)))
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
            j = ((ra[0] - r[0]) / d, (rb[0] - r[0]) / d, (ra[1] - r[1]) / d,
                 (rb[1] - r[1]) / d)
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
            raise ValueError(
                f"no coupled pair found for Ze={ze:.1f}, Zo={zo:.1f} ohm on "
                f"{self.name}"
            )
        return w, s

    # ----------------------------------------------------------------
    # discontinuities
    def open_end(self, w):
        """Open-end length extension (mm), Kirschning, Jansen & Koster
        (1981)."""
        return formulas.open_end_extension(w, self.h, self.er, self.line(w)[1])

    def step(self, w1, w2):
        """Width step w1 -> w2: the wider strip's length extension (mm) at the
        step (Edwards: the open end of the wider strip, scaled by the part of
        its width not covered, 1 - wn / ww)."""
        return formulas.step_extension(w1, w2, self.h, self.er,
                                       self.line(max(w1, w2))[1])

    def gap(self, w, s):
        """(Cp, Cg) in F of a series gap s in a strip of width w: shunt
        capacitance at each side and the series capacitance across (Garg & Bahl;
        fitted for 0.5 <= w/h <= 2, 0.1 <= s/w <= 1)."""
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
        """Gap s (mm) giving series capacitance cg (F) in a strip of width w
        (ValueError if even a gap of 0.02 w is too wide)."""
        smin = 0.02 * w
        if cg > self.gap(w, smin)[1]:
            raise ValueError(
                f"a gap in a {w:.2f} mm strip gives at most "
                f"{self.gap(w, smin)[1] * 1e12:.3f} pF ({cg * 1e12:.3f} pF "
                "needed)")
        return _bisect(lambda s: -self.gap(w, s)[1], -cg, smin, 3.0 * w)

    def via_inductance(self, d):
        """Inductance (H) of a via of drill d (mm) through the substrate."""
        h = self.h * 1e-3
        r = d * 1e-3 / 2
        return (MU0 / (2 * math.pi) * (h * math.log(
            (h + math.sqrt(r * r + h * h)) / r) + 1.5 *
                                       (r - math.sqrt(r * r + h * h))))

    # ---------------------------------------------------------------- loss
    def alpha(self, w, z0, ee, f):
        """Attenuation (Np/m, f scalar or array) of a mode of impedance z0, eeff
        ee on a strip of width w: dielectric plus a simple conductor term (strip
        resistance Rs / w, ground plane neglected, roughness ignored)."""
        k0 = 2 * math.pi * f / C0
        er = self.er
        ad = (k0 * er * (ee - 1) * self.tand / (2 * np.sqrt(ee) *
                                                (er - 1)) if er > 1 else 0.0)
        ac = 0.0
        if self.sigma and math.isfinite(self.sigma):
            rs = np.sqrt(math.pi * f * MU0 / self.sigma)
            ac = rs / (z0 * w * 1e-3)
        return ad + ac

    def lossless(self):
        return replace(self, tand=0.0, sigma=0.0)
