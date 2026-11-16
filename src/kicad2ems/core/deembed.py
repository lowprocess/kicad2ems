"""
De-embedding (R-E1): a port's reference plane moved along its own trace, or a fixture removed, from any solver's S.

    --deembed P1=2.5            P1's reference 2.5 mm further in along its trace (the line model's sections from
                                the port: their Z0, eeff, the mode's tan d and, with --sigma, the copper's loss);
                                a negative length moves it out, the line continued as its first section
    --deembed-file P1=fix.s2p   a measured or simulated fixture removed at P1 (its port 1 at P1, its port 2 the
                                device's side)

Each is a 2-port F at the port; the device's S is S with F's inverse connected there (the inverse of a line is the
same line of minus its length). <name>_<solver>_deembedded.sNp is written beside the solver's own, which stands.
Single-ended ports only: a differential pair's legs are mixed into modes before this step.
"""
import math

import numpy as np

from .constants import C0


def line_abcd(z0, eeff, length_m, f, tand=0.0, r_per_m=None):
    """ABCD (nf, 2, 2) of a line of characteristic impedance z0 (ohm, lossless), eeff and loss: R per metre (an
    array over f, or None) and the mode's tan d."""
    w = 2 * np.pi * np.asarray(f, float)
    L = z0 * math.sqrt(eeff) / C0
    C = math.sqrt(eeff) / (C0 * z0)
    R = np.zeros_like(w) if r_per_m is None else np.asarray(r_per_m, float)
    Z, Y = R + 1j * w * L, w * C * tand + 1j * w * C
    zc, g = np.sqrt(Z / Y), np.sqrt(Z * Y)
    gl = g * length_m
    ch, sh = np.cosh(gl), np.sinh(gl)
    return np.stack([np.stack([ch, zc * sh], -1), np.stack([sh / zc, ch], -1)], -2)


def abcd_to_s(T, z0):
    """S (nf, 2, 2) of ABCD T in a reference z0 at both ports."""
    A, B, Cc, D = T[:, 0, 0], T[:, 0, 1], T[:, 1, 0], T[:, 1, 1]
    den = A + B / z0 + Cc * z0 + D
    return np.stack([np.stack([(A + B / z0 - Cc * z0 - D) / den, 2 * (A * D - B * Cc) / den], -1),
                     np.stack([2 / den, (-A + B / z0 - Cc * z0 + D) / den], -1)], -2)


def s_to_abcd(S, z0):
    s11, s12, s21, s22 = S[:, 0, 0], S[:, 0, 1], S[:, 1, 0], S[:, 1, 1]
    d = 2 * s21
    return np.stack([np.stack([((1 + s11) * (1 - s22) + s12 * s21) / d, z0 * ((1 + s11) * (1 + s22) - s12 * s21) / d], -1),
                     np.stack([((1 - s11) * (1 - s22) - s12 * s21) / (z0 * d), ((1 - s11) * (1 + s22) + s12 * s21) / d], -1)], -2)


def connect(S, k, G):
    """S (nf, n, n) with 2-port G (nf, 2, 2) connected at port k: G's port 2 to S's port k, G's port 1 the new
    port k (one reference impedance throughout)."""
    S = np.asarray(S, complex)
    g11, g12, g21, g22 = G[:, 0, 0], G[:, 0, 1], G[:, 1, 0], G[:, 1, 1]
    skk = S[:, k, k]
    den = 1 - g22 * skk
    col = S[:, :, k] / den[:, None]                                     # S_ik / (1 - g22 S_kk)
    out = S + col[:, :, None] * g22[:, None, None] * S[:, k, :][:, None, :]
    out[:, :, k] = col * g21[:, None]
    out[:, k, :] = g12[:, None] * S[:, k, :] / den[:, None]
    out[:, k, k] = g11 + g12 * skk * g21 / den
    return out


def path_abcd(lm, port, length_mm, f, sigma=None, sheet=True):
    """ABCD of the first `length_mm` of the port's trace in the line model, section by section (the last in part);
    a negative length: the first section continued outwards (its inverse). ValueError where the path is shorter."""
    from .solvers.lines.fastsolve import _line_params, conductor_r, mode_tand
    paths = lm.paths if lm is not None else []
    path = next((secs for p, q, secs, _ in paths if p == port), None)
    if path is None:                                                    # the far end of a path: walked backwards
        path = next((secs[::-1] for p, q, secs, _ in paths if q == port), None)
    if not path:
        raise ValueError(f"--deembed {port}: no line model from it (a port on a routed trace)")
    T = np.broadcast_to(np.eye(2, dtype=complex), (len(f), 2, 2)).copy()
    left = abs(float(length_mm))
    for e, _, _ in path:
        if left <= 1e-9:
            break
        if e.kind not in ('trace', 'pad') or e.group is not None:
            raise ValueError(f"--deembed {port}: {e.kind} '{e.label}' at {length_mm - left:g} mm: only a plain trace "
                             f"is removed (not a via, part or coupled section)")
        z0, ee = _line_params(e, sheet)
        if not z0 or not ee:
            raise ValueError(f"--deembed {port}: a section with no Z0 at {length_mm - left:g} mm")
        take = min(left, e.length) if length_mm > 0 else left
        r = conductor_r(e.w, e.t or 0.035, f, sigma) if sigma and e.w else None
        T = T @ line_abcd(z0, ee, take * 1e-3, f, mode_tand(e), r)
        left -= take
        if length_mm < 0:
            break
    if left > 1e-6:
        raise ValueError(f"--deembed {port}: its trace is {abs(length_mm) - left:.3g} mm before it reaches a "
                         f"junction or its end, not {abs(length_mm):g}")
    return T if length_mm > 0 else np.linalg.inv(T)


def apply(S, names, zref, f, lines=None, files=None, lm=None, sigma=None):
    """S with each port's reference moved (lines {port: mm}) and its fixture removed (files {port: Touchstone}).
    Returns (S, what was done: [str])."""
    from .touchstone import read_touchstone
    out, done = np.array(S, complex), []
    for port, mm in (lines or {}).items():
        if port not in names:
            raise ValueError(f"--deembed {port}: not a port of this result ({', '.join(names)})")
        k = names.index(port)
        out = connect(out, k, abcd_to_s(np.linalg.inv(path_abcd(lm, port, mm, f, sigma)), float(np.real(zref[k]))))
        done.append(f"{port}: its reference {mm:+g} mm along its trace")
    for port, path in (files or {}).items():
        if port not in names:
            raise ValueError(f"--deembed-file {port}: not a port of this result ({', '.join(names)})")
        k = names.index(port)
        ff, Fs = read_touchstone(path)
        if Fs.shape[1:] != (2, 2):
            raise ValueError(f"--deembed-file {port}: {path} is not a 2-port")
        if f[0] < ff[0] * (1 - 1e-9) or f[-1] > ff[-1] * (1 + 1e-9):
            raise ValueError(f"--deembed-file {port}: {path} covers {ff[0] / 1e9:g}-{ff[-1] / 1e9:g} GHz, not the run's band")
        F = np.stack([np.interp(f, ff, Fs[:, i, j].real) + 1j * np.interp(f, ff, Fs[:, i, j].imag)
                      for i in range(2) for j in range(2)], -1).reshape(len(f), 2, 2)
        z = float(np.real(zref[k]))
        out = connect(out, k, abcd_to_s(np.linalg.inv(s_to_abcd(F, z)), z))
        done.append(f"{port}: the fixture {path} removed")
    return out, done
