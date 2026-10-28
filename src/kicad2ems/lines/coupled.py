"""
Coupled lines for the line model: parallel microstrip or stripline sections close enough to couple.

- coupled_microstrip(w, s, h, er): even / odd mode impedances and effective permittivities of a
  symmetric edge-coupled pair, Kirschning & Jansen (1984), quasi-static, zero thickness (the model
  qucsator's MCOUPLED uses by default, without its dispersion and thickness terms).
- coupled_stripline(w, s, b, er): the same for a pair centred between planes b apart, Cohn (1955), exact
  for zero thickness; coupled_offset_stripline(w, s, h1, h2, er) for a pair off centre.
- coupled(xs, w, s, h, er) / single(xs, w, h, er): either, by the line model's cross-section name.
- group_lc(widths, gaps, h, er, xs): per-unit-length Maxwell capacitance (with and without dielectric) of
  N side-by-side lines, built from the pairs of neighbours (coupling beyond the nearest neighbour is
  left out).
- mtl_s(...): S-parameters of a uniform N-line section (2N ports: near ends 1..N, far ends N+1..2N),
  lossless conductors, dielectric loss as a shunt conductance.

    Ze, Zo, ee, eo = coupled_microstrip(0.35, 0.58, 0.2, 4.4)
    C, Ca = group_lc([0.35] * 5, [0.58] * 4, 0.2, 4.4)
    S = mtl_s(C, Ca, 4.4, 0.011, 12.3, freq)                    # (nf, 10, 10), 50 ohm
"""
import math
import numpy as np
from ..constants import C0, ETA0
from .formulas import microstrip, stripline, offset_stripline, ellipk, _hj_eeff, _hj_z01


def coupled_microstrip(w, s, h, er):
    """(Ze, Zo, eeff_even, eeff_odd) of two coupled microstrip lines of width w, gap s, height h (mm),
    Kirschning & Jansen (IEEE MTT-32, 1984), quasi-static, zero strip thickness."""
    u, g = w / h, max(s, 1e-6) / h
    ee1 = _hj_eeff(u, er)                                      # the single line
    z1 = _hj_z01(u) / math.sqrt(ee1)
    v = u * (20 + g * g) / (10 + g * g) + g * math.exp(-g)
    eee = _hj_eeff(v, er)                                      # even mode
    d = 0.593 + 0.694 * math.exp(-0.562 * u)
    bo = 0.747 * er / (0.15 + er)
    co = bo - (bo - 0.207) * math.exp(-0.414 * u)
    ao = 0.7287 * (ee1 - (er + 1) / 2) * (1 - math.exp(-0.179 * u))
    eeo = ((er + 1) / 2 + ao - ee1) * math.exp(-co * g ** d) + ee1      # odd mode
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


def coupled_stripline(w, s, b, er):
    """(Ze, Zo, er, er) of two coupled strips of width w, gap s, centred between planes b apart (mm), Cohn
    (IRE MTT-3, 1955), exact for zero strip thickness. The medium is homogeneous: both modes travel at er."""
    a, c = math.tanh(math.pi * w / (2 * b)), math.tanh(math.pi * (w + max(s, 1e-6)) / (2 * b))
    z = []
    for k in (a * c, a / c):                                   # even, odd
        z.append(30 * math.pi / math.sqrt(er) * ellipk(math.sqrt(1 - k * k)) / ellipk(k))
    return z[0], z[1], er, er


def coupled_offset_stripline(w, s, h1, h2, er):
    """The pair h1 above one plane and h2 below the other: per mode 2 Z1 Z2 / (Z1 + Z2), Zi the centred
    pair of spacing 2 hi (as offset_stripline does for one strip)."""
    a, b = coupled_stripline(w, s, 2 * h1, er), coupled_stripline(w, s, 2 * h2, er)
    return 2 * a[0] * b[0] / (a[0] + b[0]), 2 * a[1] * b[1] / (a[1] + b[1]), er, er


def coupled(xs, w, s, h, er):
    """(Ze, Zo, eeff_even, eeff_odd) of a coupled pair of cross-section xs ('microstrip', 'stripline' or
    'offset stripline'; h: the height, or (h1, h2) to the planes below and above for striplines)."""
    if xs == 'microstrip':
        return coupled_microstrip(w, s, h, er)
    if xs == 'stripline':
        return coupled_stripline(w, s, h[0] + h[1], er)
    return coupled_offset_stripline(w, s, h[0], h[1], er)


def single(xs, w, h, er):
    """(Z0, eeff) of one zero-thickness line of cross-section xs (as coupled)."""
    if xs == 'microstrip':
        return microstrip(w, h, er)
    if xs == 'stripline':
        return stripline(w, h[0] + h[1], er)
    return offset_stripline(w, h[0], h[1], er)


def _pair_c(w, s, h, er, xs='microstrip'):
    """(C11, C12) per metre of a symmetric pair (Maxwell form, C12 < 0)."""
    ze, zo, ee, eo = coupled(xs, w, s, h, er)
    ce, cc = math.sqrt(ee) / (C0 * ze), math.sqrt(eo) / (C0 * zo)
    return 0.5 * (ce + cc), 0.5 * (ce - cc)


def _single_c(w, h, er, xs='microstrip'):
    z, ee = single(xs, w, h, er)
    return math.sqrt(ee) / (C0 * z)


def group_lc(widths, gaps, h, er, xs='microstrip'):
    """(C, C_air) per metre, N x N Maxwell matrices of N side-by-side lines of cross-section xs (widths[i],
    gap gaps[i] between line i and i+1). Each neighbour pair (at its mean width) sets the mutual term and
    adds its coupling to both self terms; lines further apart are uncoupled."""
    out = []
    for e in (er, 1.0):
        C = np.diag([_single_c(w, h, e, xs) for w in widths])
        for i, s in enumerate(gaps):
            wm = 0.5 * (widths[i] + widths[i + 1])
            c11, c12 = _pair_c(wm, s, h, e, xs)
            extra = c11 - _single_c(wm, h, e, xs)              # what the neighbour adds to the self term
            C[i, i] += extra
            C[i + 1, i + 1] += extra
            C[i, i + 1] = C[i + 1, i] = c12
        out.append(C)
    return out[0], out[1]


def mtl_s(C, Ca, er, tand, length_mm, freq, z0=50.0):
    """S-parameters (nf, 2N, 2N) of a uniform N-line section of length length_mm: ports 1..N at the
    near ends, N+1..2N at the far ends (line i: port i -> port N + i), reference z0."""
    C, Ca = np.asarray(C, float), np.asarray(Ca, float)
    n = len(C)
    L = np.linalg.inv(Ca) / C0 ** 2                            # mu0 eps0 Ca^-1
    Cd = (C - Ca) * (er / (er - 1) if er > 1 else 0.0)         # the part of C held by the dielectric
    ell = length_mm * 1e-3
    S = np.zeros((len(freq), 2 * n, 2 * n), complex)
    eye = np.eye(2 * n)
    for k, f in enumerate(np.asarray(freq, float)):
        w = 2 * np.pi * f
        Z = 1j * w * L
        Y = 1j * w * C + w * tand * Cd
        g2, T = np.linalg.eig(Z @ Y)
        g = np.sqrt(g2)
        g = np.where(g.real < 0, -g, g)
        Ti = np.linalg.inv(T)
        Zi = np.linalg.inv(Z)
        coth = 1 / np.tanh(g * ell)
        csch = 1 / np.sinh(g * ell)
        Y11 = Zi @ T @ np.diag(g * coth) @ Ti
        Y12 = -Zi @ T @ np.diag(g * csch) @ Ti
        Yp = np.block([[Y11, Y12], [Y12, Y11]])
        S[k] = (eye - z0 * Yp) @ np.linalg.inv(eye + z0 * Yp)
    return S
