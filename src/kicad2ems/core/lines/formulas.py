"""
Closed-form quasi-static line impedances (Z0, effective permittivity):
microstrip (Hammerstad & Jensen, with their thickness correction), embedded
microstrip (Bahl / Wadell's cover correction), stripline (Wheeler) and offset
stripline, grounded CPW (Ghione & Naldi), coax and a via barrel's inductance.
Plain math, no board: the line model (tlines), coupled lines (coupled) and the
examples use them. Units: mm, Ohm.
"""
import math

# (formulas.ETA0, MU0: tlines, dfsynth's tests)
from ..constants import ETA0, MU0  # noqa: F401


# ---- closed-form line impedances
# -------------------------------------------------------------------
def ellipk(k, kp=None):
    """Complete elliptic integral of the first kind K(k), modulus k in [0, 1)
    (arithmetic-geometric mean); kp: the complementary modulus, sqrt(1 - k^2),
    where it is known better than k is (k rounding to 1)."""
    a, b = 1.0, math.sqrt(max(1.0 - k * k, 0.0)) if kp is None else kp
    for _ in range(60):
        if abs(a - b) <= 1e-15 * a:
            break
        a, b = 0.5 * (a + b), math.sqrt(a * b)
    return math.pi / (2 * a)


def _hj_z01(u):
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _hj_eeff(u, er):
    a = (1 + math.log((u**4 + (u / 52)**2) /
                      (u**4 + 0.432)) / 49 + math.log(1 + (u / 18.1)**3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def microstrip(w, h, er, t=0.0):
    """(Z0, eeff) of a microstrip line, Hammerstad & Jensen, quasi-static, strip
    thickness t."""
    u = w / h
    du1 = dur = 0.0
    if t > 0:
        th = t / h
        du1 = (th / math.pi
               * math.log(1 + 4 * math.e /
                          (th / math.tanh(math.sqrt(6.517 * u))**2)))
        dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(max(er - 1, 0.0)))) * du1
    u1, ur = u + du1, u + dur
    ee = _hj_eeff(ur, er)
    return _hj_z01(ur) / math.sqrt(ee), ee * (_hj_z01(u1) / _hj_z01(ur)) ** 2


def embedded_microstrip(w, h, er, t, cover, er_cover):
    """Microstrip under a dielectric cover of thickness `cover` (mm): Bahl's
    correction of eeff."""
    z0, ee = microstrip(w, h, er, t)
    x = math.exp(-2 * cover / h)
    ee2 = ee * x + er_cover * (1 - x)
    return z0 * math.sqrt(ee / ee2), ee2


def stripline(w, b, er, t=0.0):
    """Z0 of a centred stripline (planes b apart, strip thickness t), Wheeler
    (1978); eeff = er."""
    x = t / b
    wb = w / (b - t)
    if x > 0:
        n = 2 / (1 + 2 * x / (3 * (1 - x)))
        dw = (x / (math.pi * (1 - x)) * (1 - 0.5 * math.log(
            (x / (2 - x))**2 + (0.0796 * x / (w / b + 1.1 * x))**n)))
        wb += dw
    m = 8 / (math.pi * wb)
    return (30 / math.sqrt(er)
            * math.log(1 + 4 / (math.pi * wb) * (m + math.sqrt(m * m + 6.27))),
            er)


def offset_stripline(w, h1, h2, er, t=0.0):
    """Z0 of a stripline h1 above one plane and h2 below the other: 2 Z1 Z2 /
    (Z1 + Z2) with Zi the centred stripline of spacing 2 hi + t."""
    z1, _ = stripline(w, 2 * h1 + t, er, t)
    z2, _ = stripline(w, 2 * h2 + t, er, t)
    return 2 * z1 * z2 / (z1 + z2), er


def cpwg(w, s, h, er):
    """(Z0, eeff) of a grounded (conductor-backed) coplanar waveguide: strip w,
    gaps s, substrate h."""
    k = w / (w + 2 * s)
    kp = math.sqrt(1 - k * k)
    # past w / h = 24 tanh rounds to 1: k3p from sech^2 = 1 - tanh^2
    a = min(math.pi * w / (4 * h), 300.0)
    c = a + math.pi * s / (2 * h)
    sech2 = lambda u: 0.0 if u > 300 else 1 / math.cosh(u) ** 2
    k3, k3p = (math.tanh(a) / math.tanh(c),
               math.sqrt(sech2(a) - sech2(c)) / math.tanh(c))
    K3 = ellipk(k3, k3p)
    q = ellipk(kp) * K3 / (ellipk(k) * ellipk(k3p))
    ee = (1 + er * q) / (1 + q)
    return (60 * math.pi / math.sqrt(ee) /
            (ellipk(k) / ellipk(kp) + K3 / ellipk(k3p)), ee)


def coax(d, D, er):
    """Z0 of a coaxial line (inner diameter d, outer D)."""
    return ETA0 / (2 * math.pi * math.sqrt(er)) * math.log(D / d)


def gap_capacitance(w, s, h, er):
    """(Cp, Cg) in F of an end gap s (mm) in a microstrip of width w (mm) over h
    (mm) of er: the shunt capacitance at each side and the series one across
    (Garg and Bahl, fitted for 0.5 <= w/h <= 2 and 0.1 <= s/w <= 1; clamped to
    that range outside it). As dfsynth's microstrip.gap."""
    r = min(max(w / h, 0.5), 2.0)
    sw = min(max(s / w, 0.1), 1.0)
    mo = r * (0.619 * math.log10(r) - 0.3853)
    ko = 4.26 - 1.453 * math.log10(r)
    me, ke = ((0.8675, 2.043 * r**0.12) if sw <= 0.3 else
              (1.565 / r**0.16 - 1, 1.97 - 0.03 / r))
    co = sw ** mo * math.exp(ko) * (er / 9.6) ** 0.8 * w * 1e-15
    ce = 12 * sw ** me * math.exp(ke) * (er / 9.6) ** 0.9 * w * 1e-15
    return 0.5 * ce, 0.5 * co - 0.25 * ce


def open_end_extension(w, h, er, eeff):
    """An open end's length extension (mm) of a microstrip of width w over h of
    er, its eeff (Kirschning, Jansen and Koster 1981). As dfsynth's
    microstrip.open_end."""
    u = w / h
    x1 = (0.434907 * (eeff**0.81 + 0.26) / (eeff**0.81 - 0.189) *
          (u**0.8544 + 0.236) / (u**0.8544 + 0.87))
    x2 = 1 + u ** 0.371 / (2.358 * er + 1)
    x3 = 1 + 0.5274 * math.atan(0.084 * u ** (1.9413 / x2)) / eeff ** 0.9236
    x4 = (1 + 0.0377 * math.atan(0.067 * u**1.456) *
          (6 - 5 * math.exp(0.036 * (1 - er))))
    x5 = 1 - 0.218 * math.exp(-7.5 * u)
    return h * x1 * x3 * x5 / x4


def step_extension(w1, w2, h, er, eeff_wide):
    """A width step's length extension of its wider strip (mm): the wider
    strip's open end scaled by the part of its width the narrower does not
    cover, 1 - wn / ww (Edwards). As dfsynth's microstrip.step."""
    wn, ww = sorted((w1, w2))
    return open_end_extension(ww, h, er, eeff_wide) * (1 - wn / ww)


def bend_capacitance(w, h, er, angle=90.0):
    """The excess capacitance (F) of an unmitred bend of a microstrip of width w
    over h of er (mm): Gupta, Garg and Bahl's 90 deg corner, scaled for another
    angle by its corner's area, tan(angle / 2) (0 for a straight line, the
    corner's own at 90 deg; an estimate away from 90)."""
    u = w / h
    if u < 1:
        cw = (((14 * er + 12.5) * u -
               (1.83 * er - 2.25)) / math.sqrt(u) + 0.02 * er / u)
    else:
        cw = (9.5 * er + 1.25) * u + 5.2 * er + 7.0
    scale = math.tan(math.radians(min(max(angle, 0.0), 135.0)) / 2)
    return max(cw, 0.0) * 1e-12 * w * 1e-3 * scale


def via_inductance(h, r):
    """Partial self-inductance (H) of a via barrel of length h and radius r
    (mm), Goldfarb & Pucel."""
    h, r = h * 1e-3, r * 1e-3
    s = math.hypot(r, h)
    return MU0 / (2 * math.pi) * (h * math.log((h + s) / r) + 1.5 * (r - s))
