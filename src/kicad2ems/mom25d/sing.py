"""
mom25d.sing - the analytic integrals of 1/R between triangles with an
out-of-plane offset (emsdev.numerics.sing), with the 6-point rule over the
smaller triangle of a pair, and their moments

The rule runs over the smaller triangle of a pair and the analytic integral over
the larger (the lower index's rule where their areas are equal): a pair's
moments are then one value whichever of the two is the observation, so Z is
symmetric, and they are the accurate ones. With the rule over a 0.9 mm plane
cell and a 0.3 mm trace cell 0.2 mm under it, the moment was up to 1.3 % off;
the other way 0.003 %.
"""
import numpy as np

from kicad2ems.emsdev.numerics import sing


def offplane_pairs(nodes, tris, p_idx, q_idx, d, chunk=4000, outer_levels=0,
                   _geometry=None, scalar_only=False):
    """emsdev.numerics.sing.offplane_pairs (m0, mro, mrs, mrr of pairs (p, q) at
    offsets d; scalar_only: m0) with the rule over the smaller triangle of each
    pair (rule_over): such a pair computed as (q, p), handed back as (p, q).
    outer_levels subdivides that triangle into four per level."""
    g = (sing._offplane_geometry(nodes, tris, outer_levels)
         if _geometry is None else _geometry)
    p_idx, q_idx = np.asarray(p_idx), np.asarray(q_idx)
    swap = ~rule_over(g[4], p_idx, q_idx)                  # the rule over q
    m = sing.offplane_pairs(nodes, tris, np.where(swap, q_idx, p_idx),
                            np.where(swap, p_idx, q_idx), d, chunk, _geometry=g,
                            scalar_only=scalar_only)
    if scalar_only:
        return m
    m0, mro, mrs, mrr = m
    # the moments weighted by p's and by q's coordinates
    mro[swap], mrs[swap] = mrs[swap], mro[swap]
    return m0, mro, mrs, mrr


# triangles whose areas differ by less than this (relative) count as equal
RULE_TIE = 1e-9


def rule_over(area, p, q):
    """True where a pair's 6-point rule runs over p (and the analytic integral
    over q): p is the smaller triangle, or the lower index of two equal ones.
    The same answer for (p, q) and (q, p) read the other way round."""
    p, q = np.asarray(p), np.asarray(q)
    ap, aq = area[p], area[q]
    tie = np.abs(ap - aq) <= RULE_TIE * np.maximum(ap, aq)
    return np.where(tie, p <= q, ap < aq)


def offplane_moments(cA, cq, m):
    """The seven moments (base.moments7's order) of the extracted cA / R, cq / R
    from offplane_pairs' result m."""
    m0, mro, mrs, mrr = m
    return [
        cA * m0, cA * mro[:, 0], cA * mro[:, 1], cA * mrs[:, 0], cA * mrs[:, 1],
        cA * mrr, cq * m0
    ]
