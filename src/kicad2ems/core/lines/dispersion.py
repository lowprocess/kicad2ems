"""
The line model with the wideband dielectric (R-E4, `--diel wideband`): each
layer's er and tan d at each frequency from its Djordjevic-Sarkar curve
(materials.djordjevic_sarkar, matched at the layer's fref, else --diel-fref), as
mom25d and the FDTD layer have them.

The line model's sections hold one er (scikit-rf's media take a
frequency-invariant one; qucsator's substrates are constant), so the network is
solved with its materials frozen at a few frequencies f_k across the band, each
solve over every frequency, and S at f is interpolated between the solves to the
materials at f itself: at each f, S of the materials' frequency u is smooth (the
curves change er a few per cent per decade), a polynomial in log u through the K
solves, read at u = f. K follows the phase the change can move along the longest
path (a Chebyshev interpolation of exp(j theta x) on K nodes misses by 2 (theta
/ 2)^K / K!).

A frozen copy: each section's er and tan d from its own layers (Element.diel,
the averages lines.reference takes), its effective er by its filling factor q =
(eeff - 1) / (er - 1) (Hammerstad's form: q does not depend on er), Z0 with the
air capacitance kept (Z0 sqrt(eeff) fixed), a coupled group's dielectric
capacitance (C - Ca) by the same factor. qucsator's RLCG sections (the
field-solved ones) keep their G and R at the band centre, as without the
wideband model: held at f_k, G (proportional to f_k) would spread over the band
as no few solves interpolate.

    plan = nodes(lm, freq, fref)        # None: nothing to do (no loss, so no
                                        # dispersion), else the f_k
    copy = frozen(lm, fk, fref)         # the line model with its materials at
                                        # fk
    S = interpolate(freq, plan, [S_k])  # S at each f from the K solves
"""
import copy
import math
import numpy as np
from ..constants import C0
from ..materials import djordjevic_sarkar
from .reference import mix

TOL = 1e-4                  # the interpolation's target miss, |dS|
K_MIN, K_MAX = 3, 12


def layer_at(er, tand, fref, f):
    """(er, tan d) of one layer at f: its Djordjevic-Sarkar curve through er (1
    - j tand) at fref."""
    if not tand:
        return er, 0.0
    e = complex(djordjevic_sarkar(er, tand, fref, f))
    return e.real, -e.imag / e.real


def section_at(e, f, fref):
    """(er, tan d) of an element at f: its layers' curves (e.diel; a layer
    without fref at fref), averaged as built; without layers its own er and tan
    d as one material."""
    diel = getattr(e, 'diel', None)
    if diel:
        return mix([(t, *layer_at(er, td, fr or fref, f))
                    for t, er, td, fr in diel])
    return layer_at(e.er, e.tand or 0.0, fref, f)


def _rescale(e, er1, tand1):
    """The element with er1, tand1 in place of its er and tan d: eeff by its
    filling factor, Z0 with its air capacitance kept, the mode's tan d with the
    material's."""
    er0, tand0 = e.er, e.tand or 0.0
    for z, ee in (('z0', 'eeff'), ('z0_sheet', 'eeff_sheet')):
        e0 = getattr(e, ee)
        if e0 is None or er0 is None:
            continue
        q = (e0 - 1) / (er0 - 1) if er0 > 1 + 1e-9 else 1.0
        e1 = 1 + q * (er1 - 1) if er0 > 1 + 1e-9 else e0 * er1 / er0
        setattr(e, ee, e1)
        if getattr(e, z):
            setattr(e, z, getattr(e, z) * math.sqrt(e0 / e1))
    if e.tand_eff and tand0:
        e.tand_eff = e.tand_eff * tand1 / tand0
    e.er, e.tand = er1, tand1


def frozen(lm, fk, fref):
    """A copy of the line model with its materials at fk (Hz). The copper's
    records (outlines, cuts, parts) are shared, not copied."""
    keep = {}
    for e in lm.elements:
        for x in (e.geom, e.pts, e.region, e.radial, e.cut, e.classified,
                  e.coupled, e.part):
            if x is not None:
                keep[id(x)] = x
    out = copy.deepcopy(lm, keep)
    old = {id(a): b for a, b in zip(out.elements, lm.elements)}
    for e in out.elements:
        if e.er:
            _rescale(e, *section_at(e, fk, fref))
    for g in out.groups:
        e0 = g['members'][0][0]
        er0 = old[id(e0)].er if id(e0) in old else g['er']
        if g.get('C') is not None and er0 and er0 > 1 + 1e-9:
            C, Ca = np.asarray(g['C'], float), np.asarray(g['Ca'], float)
            g['C'] = Ca + (C - Ca) * (e0.er - 1) / (er0 - 1)
        g['er'], g['tand'] = e0.er, e0.tand
    return out


def nodes(lm, freq, fref):
    """The materials' frequencies f_k for the band of freq (Hz), or None when
    nothing disperses (no section has loss: a lossless curve is flat). Chebyshev
    nodes in log f; K from the phase the er change can move."""
    f = np.asarray(freq, float)
    fa, fb = float(f.min()), float(f.max())
    els = [e for e in lm.elements if e.er]
    if (not els or not any(
        (e.tand or 0) > 0 or any(td
                                 for _, _, td, _ in (getattr(e, 'diel', None) or
                                                     ()))
            for e in els)):
        return None
    if fb <= fa * (1 + 1e-9):
        return np.array([fa])
    lo = [section_at(e, fa, fref)[0] for e in els]
    hi = [section_at(e, fb, fref)[0] for e in els]
    # the largest relative change of er
    d = max(abs(a - b) / max(a, b) for a, b in zip(lo, hi))
    # (m sqrt(eeff))
    longest = max((sum(e.length * 1e-3 * math.sqrt(e.eeff or e.er or 1.0)
                       for e, _, _ in secs)
                   for _, _, secs, _ in lm.paths), default=0.0)
    # half the phase range the change moves, at fmax
    theta = math.pi * fb * longest / C0 * d / 2
    theta = max(theta, 0.05)
    K = K_MIN
    while K < K_MAX and 2 * (theta / 2) ** K / math.factorial(K) > TOL:
        K += 1
    k = np.arange(K)
    # Chebyshev nodes on (-1, 1), ascending
    x = np.cos(np.pi * (k + 0.5) / K)[::-1]
    return np.exp(0.5 * (np.log(fa) + np.log(fb))
                  + 0.5 * (np.log(fb) - np.log(fa)) * x)


def interpolate(freq, fk, Sk):
    """S at each f of freq from the solves Sk (K, nf, n, n) with the materials
    at fk: the polynomial in log f through them, read at f (barycentric
    Lagrange)."""
    Sk = np.asarray(Sk)
    if len(fk) == 1:
        return Sk[0]
    u, uk = np.log(np.asarray(freq, float)), np.log(np.asarray(fk, float))
    w = np.array([
        1.0 / np.prod([uk[i] - uk[j]
                       for j in range(len(uk))
                       if j != i])
        for i in range(len(uk))
    ])
    D = u[:, None] - uk[None, :]                                    # (nf, K)
    hit = np.isclose(D, 0.0, atol=1e-12)
    D = np.where(hit, 1.0, D)
    L = w[None, :] / D
    L = np.where(hit.any(1, keepdims=True), hit.astype(float), L)
    L = L / L.sum(1, keepdims=True)
    return np.einsum('fk,kfij->fij', L, Sk)
