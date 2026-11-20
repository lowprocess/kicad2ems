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


# near pairs per piece of near_sparse's build: a piece's triplets, their keys,
# sort and sums at once, ~0.5 GB (4 M pieces: their own peak was the larger)
NEAR_PAIRS = 1_000_000
# a near pair's bytes near_sparse holds at its peak beyond the pairs' own
# (policy.NEAR_HELD): each used slot's place in its piece's sort (int32, nine),
# per entry of the CSR (about four a pair) its value (complex128), column and
# each piece's first slot and place in it (int32), and the merge of the pieces'
# entries. Measured on 5.5 M pairs: 289 B a pair at the peak with the 112 held
# (625 all at once), 230 kept after
NEAR_KEPT = 180.0
# a piece's own peak, a pair: its nine triplets (rows, columns int32, values
# complex128), their keys and sort (int64), its used slots (int32) and its
# values in sort order (complex128)
NEAR_PIECE = 9 * (4 + 4 + 16 + 8 + 8 + 4 + 16)


def near_sparse(op, pairs=None):
    """The n x n CSR of the core's exact near-field correction for operator op
    (fast.FastOperator: its core, mesh, tables, quadrature, near pairs), and
    its per-pair via-fan correction, built a piece of `pairs` near pairs at a
    time (NEAR_PAIRS). All the triplets at once had peaked at ~625 bytes a pair
    (a CM5 carrier's F.Cu, 74 M pairs: more than this machine's RAM, where the
    CSR is a fraction of it). The structure is found on the first frequency (a
    pass over the pieces for their entries alone, merged) and kept on op: each
    piece's sort of its slots and where its entries go. Then, and at every
    later frequency, each piece's values are summed straight into the CSR."""
    pairs = int(pairs or NEAR_PAIRS)
    nP, nQ, q, n = op.nP32, op.nQ32, op.q, op.sim.Ne
    total = len(nP)
    stat = q.get('stat')
    pieces = [(a, min(total, a + pairs)) for a in range(0, total, pairs)]
    tag = ('pieces', total, n, pairs)

    def coo(a, b):
        """A piece's triplets (the core's analytic parts written on the first
        pass, read after)."""
        qa = q if stat is None else dict(
            q, stat=stat[a:b], stat_ready=q.get('stat_ready', False)
            or done[0])
        return op.core.near_coo(op.g, op.tb, qa, nP[a:b], nQ[a:b], op.w)
    done = [False]
    qcorr = np.empty(total, complex)
    pat = getattr(op, '_near_pat', None)
    if pat is None or pat[0] != tag:
        # the structure: each piece's used slots in key order, its entries
        found = []
        for a, b in pieces:
            ri, ci, vv, qcorr[a:b] = coo(a, b)
            del vv
            slot = np.flatnonzero(ri >= 0).astype(np.int32)
            key = ri[slot].astype(np.int64) * n + ci[slot]
            del ri, ci
            o = np.argsort(key)
            order, key = slot[o], key[o]
            del o, slot
            first = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
            found.append((order, first.astype(np.int32), key[first]))
            del key, first
        done[0] = True
        # merged (a sort in place and its repeats dropped: np.unique hashed
        # them, 7 s of 10 on 5.5 M pairs)
        keys = np.concatenate([k for _, _, k in found])
        keys.sort()
        keys = keys[np.r_[True, keys[1:] != keys[:-1]]]
        kept = []
        for order, first, k in found:
            kept.append((order, first, np.searchsorted(keys, k).astype(
                np.int32)))
        del found
        indptr = np.r_[0, np.cumsum(np.bincount(keys // n, minlength=n))
                       ].astype(np.int32)
        indices = (keys % n).astype(np.int32)
        del keys
        pat = op._near_pat = (tag, kept, indptr, indices)
    data = np.zeros(len(pat[3]), complex)
    for (a, b), (order, first, pos) in zip(pieces, pat[1]):
        _, _, vv, qcorr[a:b] = coo(a, b)
        if len(first):
            data[pos] += np.add.reduceat(vv[order], first)
        del vv
    M = sparse.csr_matrix((data, pat[3], pat[2]), shape=(n, n))
    M.has_canonical_format = True
    return M, qcorr


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
