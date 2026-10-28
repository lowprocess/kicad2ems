"""
Closed-form quasi-static line impedances (Z0, effective permittivity): microstrip (Hammerstad & Jensen,
with their thickness correction), embedded microstrip (Bahl / Wadell's cover correction), stripline
(Wheeler) and offset stripline, grounded CPW (Ghione & Naldi), coax and a via barrel's inductance.
Plain math, no board: the line model (tlines), coupled lines (coupled) and the examples use them.
Units: mm, Ohm.
"""
import math


ETA0 = 376.730313668
MU0 = 4e-7 * math.pi


# ---- closed-form line impedances -------------------------------------------------------------------
def ellipk(k):
    """Complete elliptic integral of the first kind K(k), modulus k in [0, 1) (arithmetic-geometric mean)."""
    a, b = 1.0, math.sqrt(max(1.0 - k * k, 0.0))
    for _ in range(60):
        if abs(a - b) <= 1e-15 * a:
            break
        a, b = 0.5 * (a + b), math.sqrt(a * b)
    return math.pi / (2 * a)


def _hj_z01(u):
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    return ETA0 / (2 * math.pi) * math.log(f / u + math.sqrt(1 + 4 / u ** 2))


def _hj_eeff(u, er):
    a = 1 + math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) / 49 + math.log(1 + (u / 18.1) ** 3) / 18.7
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    return (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)


def microstrip(w, h, er, t=0.0):
    """(Z0, eeff) of a microstrip line, Hammerstad & Jensen, quasi-static, strip thickness t."""
    u = w / h
    du1 = dur = 0.0
    if t > 0:
        th = t / h
        du1 = th / math.pi * math.log(1 + 4 * math.e / (th / math.tanh(math.sqrt(6.517 * u)) ** 2))
        dur = 0.5 * (1 + 1 / math.cosh(math.sqrt(max(er - 1, 0.0)))) * du1
    u1, ur = u + du1, u + dur
    ee = _hj_eeff(ur, er)
    return _hj_z01(ur) / math.sqrt(ee), ee * (_hj_z01(u1) / _hj_z01(ur)) ** 2


def embedded_microstrip(w, h, er, t, cover, er_cover):
    """Microstrip under a dielectric cover of thickness `cover` (mm): Bahl's correction of eeff."""
    z0, ee = microstrip(w, h, er, t)
    x = math.exp(-2 * cover / h)
    ee2 = ee * x + er_cover * (1 - x)
    return z0 * math.sqrt(ee / ee2), ee2


def stripline(w, b, er, t=0.0):
    """Z0 of a centred stripline (planes b apart, strip thickness t), Wheeler (1978); eeff = er."""
    x = t / b
    wb = w / (b - t)
    if x > 0:
        n = 2 / (1 + 2 * x / (3 * (1 - x)))
        dw = x / (math.pi * (1 - x)) * (1 - 0.5 * math.log((x / (2 - x)) ** 2 + (0.0796 * x / (w / b + 1.1 * x)) ** n))
        wb += dw
    m = 8 / (math.pi * wb)
    return 30 / math.sqrt(er) * math.log(1 + 4 / (math.pi * wb) * (m + math.sqrt(m * m + 6.27))), er


def offset_stripline(w, h1, h2, er, t=0.0):
    """Z0 of a stripline h1 above one plane and h2 below the other: 2 Z1 Z2 / (Z1 + Z2) with Zi the
    centred stripline of spacing 2 hi + t."""
    z1, _ = stripline(w, 2 * h1 + t, er, t)
    z2, _ = stripline(w, 2 * h2 + t, er, t)
    return 2 * z1 * z2 / (z1 + z2), er


def cpwg(w, s, h, er):
    """(Z0, eeff) of a grounded (conductor-backed) coplanar waveguide: strip w, gaps s, substrate h."""
    k = w / (w + 2 * s)
    k3 = math.tanh(math.pi * w / (4 * h)) / math.tanh(math.pi * (w + 2 * s) / (4 * h))
    kp, k3p = math.sqrt(1 - k * k), math.sqrt(1 - k3 * k3)
    q = ellipk(kp) * ellipk(k3) / (ellipk(k) * ellipk(k3p))
    ee = (1 + er * q) / (1 + q)
    return 60 * math.pi / math.sqrt(ee) / (ellipk(k) / ellipk(kp) + ellipk(k3) / ellipk(k3p)), ee


def coax(d, D, er):
    """Z0 of a coaxial line (inner diameter d, outer D)."""
    return ETA0 / (2 * math.pi * math.sqrt(er)) * math.log(D / d)


def via_inductance(h, r):
    """Partial self-inductance (H) of a via barrel of length h and radius r (mm), Goldfarb & Pucel."""
    h, r = h * 1e-3, r * 1e-3
    s = math.hypot(r, h)
    return MU0 / (2 * math.pi) * (h * math.log((h + s) / r) + 1.5 * (r - s))
