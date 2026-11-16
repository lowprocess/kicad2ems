"""
Touchstone files: v1 when all reference impedances are equal, v2.0 with
[Reference] otherwise. kicad2ems names the ports in a comment, `! ports: 1=P1,
2=P2d, ...` (port_names reads it back).
"""
import os, re
import numpy as np


def log_spaced(freq):
    """The frequencies are evenly spaced on a log scale (a run's sweep = log),
    not linearly."""
    f = np.asarray(freq, float)
    return bool(
        len(f) > 2 and f[0] > 0 and not np.allclose(np.diff(f), (f[-1] - f[0]) /
                                                    (len(f) - 1), rtol=1e-6)
        and np.allclose(f, np.geomspace(f[0], f[-1], len(f)), rtol=1e-9))


def band_unit(freq):
    """(Hz per unit, its name) to read a band's frequencies in: GHz from 1 GHz
    up, MHz from 1 MHz, else kHz."""
    top = float(np.max(freq))
    return ((1e9, 'GHz') if top >= 1e9 else (1e6, 'MHz') if top >= 1e6 else
            (1e3, 'kHz'))


def port_names(path):
    """The port names of the `! ports:` comment kicad2ems writes, or None
    (another program's file, or none)."""
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if not line.startswith('!'):
                    break
                m = re.match(r'!\s*ports:\s*(.*)', line)
                if m:
                    return [
                        p.split('=', 1)[1].strip()
                        for p in m.group(1).split(',')
                        if '=' in p
                    ]
    except OSError:
        pass
    return None


def write_touchstone(path, f, S, zref, comment='', names=None):
    """Touchstone v1 when all reference impedances are equal, v2.0 (per-port
    [Reference]) otherwise."""
    n = S.shape[1]
    zref = np.asarray(zref, float)
    v2 = not np.allclose(zref, zref[0])
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(f"! kicad2ems {comment}\n")
        if names:
            fh.write("! ports: "
                     + ", ".join(f"{i + 1}={nm}" for i, nm in enumerate(names))
                     + "\n")
        if v2:
            fh.write("[Version] 2.0\n")
        fh.write(f"# Hz S RI R {zref[0]:.6g}\n")
        if v2:
            fh.write(f"[Number of Ports] {n}\n")
            if n == 2:
                fh.write("[Two-Port Data Order] 21_12\n")
            fh.write(f"[Number of Frequencies] {len(f)}\n")
            fh.write("[Reference] " + " ".join(f"{z:.6g}" for z in zref)
                     + "\n[Network Data]\n")
        # 1-2 ports: one line; more: each matrix row on a new line, at most 4
        # values a line
        for fi, s in zip(f, S):
            rows = ([[s[0, 0], s[1, 0], s[0, 1], s[1, 1]]]
                    if n == 2 else [list(r) for r in s])
            first = True
            for row in rows:
                for k in range(0, len(row), 4):
                    pre = f"{fi:.9e}" if first else " " * 15
                    fh.write(pre + " " + " ".join(f"{v.real:.9e} {v.imag:.9e}"
                                                  for v in row[k:k + 4]) + "\n")
                    first = False
        if v2:
            fh.write("[End]\n")


def read_touchstone(path, with_zref=False):
    """Touchstone v1 / v2 (RI / MA / DB; S, Y, Z, H or G) as S-parameters at the
    file's own reference: f (Hz), S (nf, n, n), and with with_zref its reference
    impedances (n,). Y and Z (and a two-port's H and G) are converted to S at
    `R` (v1) or [Reference] (v2); a noise block (v1: a frequency not above the
    last; v2: [Noise Data]) ends the network data; v2's [Number of Ports] and
    [Matrix Format] Lower / Upper are honoured. (It had taken any file as S,
    ignored R, read the port count from the extension only, and failed on a
    noise block.)"""
    n = int(''.join(ch for ch in os.path.splitext(path)[1] if ch.isdigit())
            or 2)
    fmt, funit, param, r_ref, data = 'MA', 1e9, 'S', 50.0, []
    mult = {'HZ': 1, 'KHZ': 1e3, 'MHZ': 1e6, 'GHZ': 1e9}
    order21, matrix, refs, v2, reading_ref, last_f = (None, 'FULL', None, False,
                                                      False, -np.inf)
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    pending = []  # the numbers of the frequency being read
    for raw in lines:
        line = raw.split('!')[0].strip()
        if not line:
            continue
        if line.startswith('#'):
            tok = line[1:].upper().split()
            funit = next((mult[x] for x in tok if x in mult), 1e9)
            fmt = next((x for x in tok if x in ('RI', 'MA', 'DB')), 'MA')
            param = next((x for x in tok if x in ('S', 'Y', 'Z', 'H', 'G')),
                         'S')
            if 'R' in tok and tok.index('R') + 1 < len(tok):
                r_ref = float(tok[tok.index('R') + 1])
            continue
        if line.startswith('['):
            key, _, rest = line[1:].partition(']')
            key, rest = key.strip().upper(), rest.strip()
            reading_ref = key == 'REFERENCE'
            if key == 'VERSION':
                v2 = True
            elif key == 'NUMBER OF PORTS':
                n = int(rest)
            elif key == 'TWO-PORT DATA ORDER':
                order21 = '21_12' in rest
            elif key == 'MATRIX FORMAT':
                matrix = rest.upper()
            elif key == 'REFERENCE':
                refs = [float(x) for x in rest.split()]
            elif key in ('NOISE DATA', 'END'):
                break
            continue
        # [Reference] may run onto the next lines
        if reading_ref and refs is not None and len(refs) < n:
            refs += [float(x) for x in line.split()]
            continue
        nums = [float(x) for x in line.split()]
        if not pending and not v2 and n == 2 and data and nums[0] <= last_f:
            break  # v1's noise block: the frequency starts over
        pending += nums
        per = (1 + 2 *
               (n * (n + 1) // 2 if matrix in ('LOWER', 'UPPER') else n * n))
        while len(pending) >= per:
            data.append(pending[:per])
            last_f, pending = pending[0], pending[per:]
    a = np.array(data, float).reshape(
        -1, 1 + 2 * (n *
                     (n + 1) // 2 if matrix in ('LOWER', 'UPPER') else n * n))
    f = a[:, 0] * funit
    v = (a[:, 1::2] + 1j * a[:, 2::2] if fmt == 'RI' else
         ((a[:, 1::2] if fmt == 'MA' else 10**(a[:, 1::2] / 20))
          * np.exp(1j * np.radians(a[:, 2::2]))))
    # one triangle, row by row: the other its mirror
    if matrix in ('LOWER', 'UPPER'):
        X = np.zeros((len(f), n, n), complex)
        idx = [(i, j)
               for i in range(n)
               for j in (range(i + 1) if matrix == 'LOWER' else range(i, n))]
        for k, (i, j) in enumerate(idx):
            X[:, i, j] = X[:, j, i] = v[:, k]
    else:
        X = v.reshape(-1, n, n)
        # S11 S21 S12 S22
        if (order21 if order21 is not None else n == 2 and not v2) and n == 2:
            X = X.transpose(0, 2, 1)
    zref = np.array(refs[:n] if refs else [r_ref] * n, float)
    S = _to_s(X, param, zref, normalised=not v2)
    return (f, S, zref) if with_zref else (f, S)


def _to_s(X, param, zref, normalised=False):
    """S at the reference impedances zref from S, Y, Z (any number of ports) or
    a two-port's H or G: normalised to the reference (a v1 file's, to R) or in
    ohms and siemens (v2)."""
    if param == 'S':
        return X
    n = X.shape[1]
    I = np.eye(n)
    if param in ('H', 'G'):
        if n != 2:
            raise ValueError(f"Touchstone {param}-parameters: two-ports only")
        if param == 'G':
            X = np.linalg.inv(X)  # G is H's inverse
        h11, h12, h21, h22 = X[:, 0, 0], X[:, 0, 1], X[:, 1, 0], X[:, 1, 1]
        det = h11 * h22 - h12 * h21
        Z = np.stack([
            np.stack([det / h22, h12 / h22], -1),
            np.stack([-h21 / h22, 1 / h22], -1)
        ], -2)
        param, X = 'Z', Z
    sq = np.ones(n) if normalised else np.sqrt(zref)
    if param == 'Z':
        Zn = X / np.outer(sq, sq)  # normalised: z_ij / sqrt(r_i r_j)
        return np.linalg.solve((Zn + I).transpose(0, 2, 1),
                               (Zn - I).transpose(0, 2, 1)).transpose(0, 2, 1)
    Yn = X * np.outer(sq, sq)  # Y: y_ij sqrt(r_i r_j)
    return np.linalg.solve((I + Yn).transpose(0, 2, 1),
                           (I - Yn).transpose(0, 2, 1)).transpose(0, 2, 1)
