"""
S-parameter post-processing: mixed-mode conversion for differential ports.
"""
import numpy as np


def mixed_mode(S, m):
    """Single-ended S (ports in m.ports order) -> user ports: diff pairs become (d, c) modes.
    Returns S_mm, names, reference impedances."""
    n = len(m.ports)
    rows, names, zref = [], [], []
    idx = {p.name: i for i, p in enumerate(m.ports)}
    for nm, legs in m.user_ports:
        if len(legs) == 1:
            r = np.zeros(n); r[idx[legs[0].name]] = 1
            rows.append(r); names.append(nm); zref.append(legs[0].z0)
        else:
            ip, im = idx[legs[0].name], idx[legs[1].name]
            d = np.zeros(n); d[ip], d[im] = 1 / np.sqrt(2), -1 / np.sqrt(2)
            c = np.zeros(n); c[ip], c[im] = 1 / np.sqrt(2), 1 / np.sqrt(2)
            rows += [d, c]; names += [nm + 'd', nm + 'c']
            z = legs[0].z0
            zref += [2 * z, z / 2]
    Mx = np.array(rows)
    # unknown entries (NaN: ports not excited) must not spread: transform with zeros, then mark
    # only the results that actually depend on an unknown entry
    unknown = np.isnan(S)
    Smm = np.einsum('ij,fjk,lk->fil', Mx, np.where(unknown, 0, S), Mx)
    dep = np.einsum('ij,fjk,lk->fil', np.abs(Mx), unknown.astype(float), np.abs(Mx)) > 0
    Smm[dep] = np.nan
    return Smm, names, zref


def fill_reciprocal(S, excite):
    """Columns of ports that were not excited, from reciprocity (S_ik = S_ki). Their own reflection
    and the terms between two unexcited ports stay unknown (NaN). Returns the list of those ports."""
    n = S.shape[1]
    missing = [k for k in range(n) if k not in excite]
    for k in missing:
        for i in excite:
            S[:, i, k] = S[:, k, i]
    return missing
