"""
mom25d.near - the near triangle pairs of a mesh and the sparse matrix of their correction
(fast.py's, split out: R-F3)
"""
import numpy as np
from scipy import sparse

from . import _core


# near-field triplets per piece of the sum into the sparse matrix (_near_csr)
NEAR_CHUNK = 8_000_000


def _near_csr(ri, ci, vv, n, owner):
    """The n x n CSR of the core's near-field triplets, duplicates summed. The
    structure is built once and kept on `owner` (3.4 s on the SMA launch's 84 M
    triplets); each frequency then only sums its values into it, a piece at a
    time (0.6 s, against 3.1 s for SciPy's COO -> CSR every frequency)."""
    pat = getattr(owner, '_near_pat', None)
    if pat is None or pat[0] != (len(ri), n):
        # the CSR structure of the triplets (row < 0: unused), the same at every
        # frequency (a pair's nine slots are the edges of its two triangles):
        # the used slots in CSR order, and where each entry's run of them starts
        slot = np.flatnonzero(ri >= 0)
        key = ri[slot].astype(np.int64) * n + ci[slot]
        o = np.argsort(key)
        slot, key = slot[o].astype(np.int32), key[o]
        del o
        first = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
        key = key[first]
        indptr = np.r_[0, np.cumsum(np.bincount(key // n, minlength=n))].astype(
            np.int32)
        pat = owner._near_pat = ((len(ri), n),
                                 (indptr, (key % n).astype(np.int32), slot,
                                  first.astype(np.int32)))
    indptr, indices, slot, first = pat[1]
    data = np.empty(len(first), vv.dtype)
    a = 0
    while a < len(first):  # whole entries per piece: about NEAR_CHUNK slots
        b = min(len(first), int(np.searchsorted(first, first[a] + NEAR_CHUNK)))
        s1 = first[b] if b < len(first) else len(slot)
        data[a:b] = np.add.reduceat(vv[slot[first[a]:s1]],
                                    first[a:b] - first[a])
        a = b
    M = sparse.csr_matrix((data, indices, indptr), shape=(n, n))
    M.has_canonical_format = True
    return M


def near_pairs(sim, near_fac=None):
    """(nP, nQ): the near triangle pairs of sim's mesh (sorted, near if within
    either's radius), found once: the memory plan counts them before the
    FastOperator is made (policy.memory_plan's pairs), which takes the same."""
    near_fac = float(near_fac or sim.near_fac)
    kept = getattr(sim, '_near_found', None)
    if kept is not None and kept[0] == near_fac:
        return kept[1]
    r = sim.rwg
    size = getattr(sim, 'tri_size', None)
    if size is None:  # (a mesh_only simulation: Simulation's own rule)
        V = r.nodes[r.tris]
        size = np.linalg.norm(V - np.roll(V, 1, axis=1), axis=2).max(1)
    found = _core.near_pairs(
        np.ascontiguousarray(r.cent, float),
        np.ascontiguousarray(size, float),
        np.ascontiguousarray(sim.st.z[sim.tri_layer], float), near_fac)
    sim._near_found = (near_fac, found)
    return found
