"""
Touchstone files: v1 when all reference impedances are equal, v2.0 with [Reference] otherwise.
"""
import os
import numpy as np


def write_touchstone(path, f, S, zref, comment='', names=None):
    """Touchstone v1 when all reference impedances are equal, v2.0 (per-port [Reference]) otherwise."""
    n = S.shape[1]
    zref = np.asarray(zref, float)
    v2 = not np.allclose(zref, zref[0])
    with open(path, 'w') as fh:
        fh.write(f"! kicad2ems {comment}\n")
        if names:
            fh.write("! ports: " + ", ".join(f"{i + 1}={nm}" for i, nm in enumerate(names)) + "\n")
        if v2:
            fh.write("[Version] 2.0\n")
        fh.write(f"# Hz S RI R {zref[0]:.6g}\n")
        if v2:
            fh.write(f"[Number of Ports] {n}\n")
            if n == 2:
                fh.write("[Two-Port Data Order] 21_12\n")
            fh.write(f"[Number of Frequencies] {len(f)}\n")
            fh.write("[Reference] " + " ".join(f"{z:.6g}" for z in zref) + "\n[Network Data]\n")
        for fi, s in zip(f, S):                                      # 1-2 ports: one line; more: each matrix
            rows = [[s[0, 0], s[1, 0], s[0, 1], s[1, 1]]] if n == 2 else [list(r) for r in s]   # row on a new line,
            first = True                                             # at most 4 values a line
            for row in rows:
                for k in range(0, len(row), 4):
                    pre = f"{fi:.9e}" if first else " " * 15
                    fh.write(pre + " " + " ".join(f"{v.real:.9e} {v.imag:.9e}" for v in row[k:k + 4]) + "\n")
                    first = False
        if v2:
            fh.write("[End]\n")


def read_touchstone(path):
    """Minimal Touchstone reader (v1 / v2, RI / MA / DB). Returns f (Hz), S (nf, n, n)."""
    n = int(''.join(ch for ch in os.path.splitext(path)[1] if ch.isdigit()) or 2)
    fmt, funit, data = 'MA', 1e9, []
    mult = {'HZ': 1, 'KHZ': 1e3, 'MHZ': 1e6, 'GHZ': 1e9}
    order21 = n == 2
    for line in open(path):
        line = line.split('!')[0].strip()
        if not line:
            continue
        if line.startswith('#'):
            t = line[1:].upper().split()
            funit = next((mult[x] for x in t if x in mult), 1e9)
            fmt = next((x for x in t if x in ('RI', 'MA', 'DB')), 'MA')
            continue
        if line.startswith('['):
            if 'Two-Port Data Order' in line and '12_21' in line:
                order21 = False
            continue
        data += [float(v) for v in line.split()]
    per = 1 + 2 * n * n
    a = np.array(data).reshape(-1, per)
    f = a[:, 0] * funit
    v = a[:, 1::2] + 1j * a[:, 2::2] if fmt == 'RI' else (
        (a[:, 1::2] if fmt == 'MA' else 10 ** (a[:, 1::2] / 20)) * np.exp(1j * np.radians(a[:, 2::2])))
    S = v.reshape(-1, n, n)
    if order21:                                                     # v1 2-port order: S11 S21 S12 S22
        S = S.transpose(0, 2, 1)
    return f, S
