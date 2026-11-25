"""
Parts as internal ports, embedded after the solve (R-E5): a value changed, swept
or replaced by a vendor's measured model without solving the board again.

    --part-ports all         every two-pad R / L / C part a port across the gap
                             between its pads (or: C1,L2)
    --part-value C1=1p,2.2p  a value sweep of one part (repeat for others: every
                             combination, at most SWEEP_MAX)
    --part-file C1=grm.s2p   a part's vendor model, measured in series (port 1
                             to port 2 through the part)

The solvers solve the board with each such part as a lumped port across its gap
(a 'gap' port where the part's sheet would be, the part's reference its name),
every part port excited. Its S over the external and part ports is then closed
with each part's impedance: with Gamma = diag((Z_p - z_p) / (Z_p + z_p)) on the
part ports,

    S_ext = S_ee + S_ep Gamma (I - S_pp Gamma)^-1 S_pe

which is the board with its parts (the external ports' columns those that were
excited). <name>_<solver>.sNp is the embedded result as a run without
--part-ports writes it; <name>_<solver>_ports.sMp the full one; a sweep writes
<name>_<solver>_<REF>=<value>[_...].sNp for each combination. A part's impedance
is its series R + jwL + 1/(jwC) (its value and sim.esr / sim.esl); a vendor
file's is 2 z0 (1 - S21) / S21, its series-through reading.
"""
import itertools

import numpy as np

SWEEP_MAX = 64                  # combinations a sweep writes, at most


def part_port(part, z0=50.0, name=None):
    """The 'gap' Port across a part's gap (where its lumped sheet would be):
    named after the part."""
    from .board import Port
    a, b = int(part.axis), 1 - int(part.axis)
    c = 0.5 * (part.s[:2] + part.e[:2])
    w = float(part.e[b] - part.s[b])
    return Port(name or part.ref, part.layer, part.z, part.z, c, a, 1, w,
                float(z0), ref_layer=part.layer, kind='gap',
                gap=(float(part.s[a]), float(part.e[a])),
                source=f"part {part.ref}", internal=True)


OPEN_OHM = 1e15  # an open's impedance (a swept 0 F)


def impedance(R=None, L=None, C=None, f=None):
    """A series R-L-C's impedance at f (Hz, array); None for each element
    absent."""
    w = 2 * np.pi * np.asarray(f, float)
    z = np.zeros_like(w, dtype=complex)
    if R:
        z += R
    if L:
        z += 1j * w * L
    if C:
        z += 1 / (1j * w * C)
    elif C is not None:
        # 0 F in series: an open, as parts.py has it (`if C:` had made it a
        # short); finite, so that the S of it is 0 and 1, not inf / inf
        z += OPEN_OHM
    return z


def file_impedance(path, f, z0=None):
    """A two-port measured in series (a vendor's .s2p of a capacitor or
    inductor, port 1 to port 2 through it) as the impedance across the part: 2
    z0 (1 - S21) / S21, interpolated to f. ValueError outside its band."""
    from .touchstone import read_touchstone
    ff, S = read_touchstone(path)
    if S.shape[1:] != (2, 2):
        raise ValueError(f"{path}: not a 2-port")
    f = np.asarray(f, float)
    if f[0] < ff[0] * (1 - 1e-9) or f[-1] > ff[-1] * (1 + 1e-9):
        raise ValueError(
            f"{path} covers {ff[0] / 1e9:g}-{ff[-1] / 1e9:g} GHz, not the "
            "run's band"
        )
    s21 = (np.interp(f, ff, S[:, 1, 0].real)
           + 1j * np.interp(f, ff, S[:, 1, 0].imag))
    zr = float(z0 or _reference(path))
    return 2 * zr * (1 - s21) / s21


def _reference(path):
    """A Touchstone file's reference resistance (its option line's R; 50 when it
    gives none)."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.lstrip().startswith('#'):
                w = line.split()
                if 'R' in [x.upper() for x in w]:
                    return float(w[[x.upper() for x in w].index('R') + 1])
                return 50.0
    return 50.0


def close(S, names, zref, loads):
    """S (nf, n, n) over `names` with the ports in `loads` ({name: Z (nf,)})
    closed by those impedances: (S over the other ports, their names, their
    zref). A NaN column (a port not excited) stays NaN."""
    S = np.asarray(S, complex)
    p = [names.index(k) for k in loads]
    e = [i for i in range(len(names)) if i not in p]
    if not p:
        return S, list(names), list(zref)
    zr = np.asarray(zref, float)
    out = np.full((S.shape[0], len(e), len(e)), np.nan + 0j)
    for k in range(S.shape[0]):
        g = np.diag([(loads[names[i]][k] - zr[i]) / (loads[names[i]][k] + zr[i])
                     for i in p])
        See, Sep, Spe, Spp = (S[k][np.ix_(e, e)], S[k][np.ix_(e, p)],
                              S[k][np.ix_(p, e)], S[k][np.ix_(p, p)])
        if not np.isfinite(Spp).all() or not np.isfinite(Sep).all():
            continue
        out[k] = See + Sep @ g @ np.linalg.solve(np.eye(len(p)) - Spp @ g, Spe)
    return out, [names[i] for i in e], [zref[i] for i in e]


def pairs(items):
    """'REF=a,b' items as {REF: 'a,b'}."""
    out = {}
    for x in items or []:
        if '=' not in str(x):
            raise ValueError(f"{x!r}: REF=VALUE[,VALUE...]")
        k, v = str(x).split('=', 1)
        out[k.strip()] = v.strip()
    return out


def sweep(spec, parts):
    """The combinations of --part-value ({REF: 'v1,v2'}) as [{REF: (R, L, C)}],
    at most SWEEP_MAX; ValueError for a part that is not a part port or a value
    that is not one. A value replaces the part's own element of its kind (a
    capacitor's C), its parasitics kept."""
    from .units import parse_value
    axes = []
    for ref, vals in spec.items():
        if ref not in parts:
            raise ValueError(
                f"--part-value {ref}: not a part port "
                f"({', '.join(parts) or 'none'}; --part-ports)"
            )
        pt = parts[ref]
        # (Part.kind's rule: an inductor with its ESR is an inductor; it had
        # been taken for a resistor, and the sweep's values replaced its ESR)
        kind = ('C' if pt.C is not None else
                'L' if pt.L is not None else 'R')
        row = []
        for v in str(vals).split(','):
            x = parse_value(v.strip())
            if x is None:
                raise ValueError(f"--part-value {ref}={v.strip()}: not a value")
            row.append((ref, v.strip(), kind, x))
        axes.append(row)
    combos = list(itertools.product(*axes)) if axes else []
    if len(combos) > SWEEP_MAX:
        raise ValueError(
            f"--part-value: {len(combos)} combinations, at most {SWEEP_MAX}")
    return combos


def loads_of(parts, f, files=None, combo=()):
    """{ref: Z (nf,)} of the part ports: their own values, a vendor file's where
    one is given, a sweep's value."""
    files = files or {}
    out = {}
    over = {ref: (kind, x) for ref, _, kind, x in combo}
    for ref, pt in parts.items():
        if ref in files:
            out[ref] = file_impedance(files[ref], f)
            continue
        R, L, C = pt.R, pt.L, pt.C
        if ref in over:
            kind, x = over[ref]
            R, L, C = ((x, L, C) if kind == 'R' else
                       (R, x, C) if kind == 'L' else (R, L, x))
        out[ref] = impedance(R, L, C, f)
    return out


def tag(combo):
    """A file-name tag of a sweep's combination: C1=2.2p_L2=3n."""
    return '_'.join(f"{ref}={v}" for ref, v, _, _ in combo).replace('/', '-')
