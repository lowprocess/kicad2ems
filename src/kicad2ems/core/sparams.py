"""
S-parameter post-processing: mixed-mode conversion for differential ports.
"""
import numpy as np

__all__ = ['mixed_mode', 'fill_reciprocal', 'figures', 'compared']


def mixed_mode(S, m):
    """Single-ended S (ports in m.ports order) -> user ports: diff pairs become
    (d, c) modes. Returns S_mm, names, reference impedances."""
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
    # unknown entries (NaN: ports not excited) must not spread: transform with
    # zeros, then mark only the results that actually depend on an unknown entry
    unknown = np.isnan(S)
    # (Mx S Mx^T as products: a three-operand einsum ran over every (i, j, k,
    # l) at once, 31 s of a 92-port Thor window's 107)
    Smm = Mx @ np.where(unknown, 0, S) @ Mx.T
    dep = (np.abs(Mx) @ unknown.astype(float) @ np.abs(Mx).T) > 0
    Smm[dep] = np.nan
    return Smm, names, zref


def fill_reciprocal(S, excite):
    """Columns of ports that were not excited, from reciprocity (S_ik = S_ki).
    Their own reflection and the terms between two unexcited ports stay unknown
    (NaN). Returns the list of those ports."""
    n = S.shape[1]
    missing = [k for k in range(n) if k not in excite]
    for k in missing:
        for i in excite:
            S[:, i, k] = S[:, k, i]
    return missing


def figures(path, most=4):
    """A Touchstone file's figures, to set combinations side by side: the worst
    match (S11's highest, dB) and its dip (S11's lowest, dB, and where, GHz) at
    each port, and each transmission term's value at the band's centre and its
    lowest (dB); ports past `most` left out. {} for a file that cannot be
    read."""
    from .touchstone import read_touchstone
    try:
        f, S = read_touchstone(path)
    except (OSError, ValueError):
        return {}
    db = lambda h: 20 * np.log10(np.maximum(np.abs(h), 1e-15))
    n, out, mid = min(S.shape[1], most), {}, len(f) // 2
    for i in range(n):
        d = db(S[:, i, i])
        if np.isnan(d).all():
            continue
        k = int(np.nanargmin(d))
        out[f"S{i + 1}{i + 1} max dB"] = round(float(np.nanmax(d)), 3)
        out[f"S{i + 1}{i + 1} dip dB"] = round(float(d[k]), 3)
        out[f"S{i + 1}{i + 1} dip GHz"] = round(float(f[k] / 1e9), 4)
    for i in range(n):
        for j in range(i):
            d = db(S[:, i, j])
            if np.isnan(d).all():
                continue
            out[f"S{i + 1}{j + 1} mid dB"] = round(float(d[mid]), 3)
            out[f"S{i + 1}{j + 1} min dB"] = round(float(np.nanmin(d)), 3)
    return out


def compared(paths, most=4):
    """Several Touchstone files' figures side by side: dict(names (the
    figures, in the first file's order and then the others' own), values
    {name: [a number or None per file]}, spread {name: the largest difference
    between the files that give it, or None}) from {label: path}."""
    got = {k: figures(p, most) for k, p in paths.items()}
    names = list(dict.fromkeys(n for f in got.values() for n in f))
    values = {n: [got[k].get(n) for k in paths] for n in names}
    spread = {}
    for n, vals in values.items():
        have = [v for v in vals if v is not None]
        spread[n] = (max(have) - min(have)) if len(have) > 1 else None
    return dict(labels=list(paths), names=names, values=values,
                spread=spread)
