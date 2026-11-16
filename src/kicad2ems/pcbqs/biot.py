"""
pcbqs.biot - an MQS model's open-boundary magnetic field on uniform grids
(h_grid): its sheet currents deposited on the grid and convolved with the
Biot-Savart kernel by FFT, its filaments (vias, parts) exact. The Biot-Savart
pieces (the currents of the RWG functions, the field of sheets and filaments)
are emsdev.numerics.biot's.
"""
from types import SimpleNamespace

import numpy as np

from kicad2ems.emsdev.numerics.biot import current_deposit, h_segments


def h_grid(model, res, fi, x, y, heights, refine=1):
    """Open-boundary H on uniform x/y grids (m), at absolute z `heights` (m).

    Sheet currents are conservatively deposited then convolved with the
    magnetostatic Biot-Savart kernel by FFT. Linear convolution avoids periodic
    images; the explicitly meshed copper supplies every return path. The
    source grid expands to contain all copper, even for a small observation
    window. Filaments are evaluated analytically. For reliable sheet fields
    use grid spacing well below distance to copper (and check convergence).
    Shape (nx, ny, nh, 3), with an extra last axis for a list of fi.
    """
    from scipy.fft import fft2, ifft2, next_fast_len

    x, y, heights = [
        np.asarray(v, float) for v in (x, y, np.atleast_1d(heights))
    ]
    for axis in (x, y):
        if (axis.ndim != 1 or len(axis) < 2 or not np.isfinite(axis).all()
                or np.diff(axis).min() <= 0 or not np.allclose(
                    np.diff(axis), axis[1] - axis[0], rtol=1e-8, atol=1e-15)):
            raise ValueError('x and y must be increasing uniform grids (m)')
    if heights.ndim != 1 or not np.isfinite(heights).all():
        raise ValueError('heights must be finite absolute z coordinates (m)')
    if np.any(
            np.isclose(heights[:, None],
                       np.unique(model.z_of)[None], rtol=0, atol=1e-15)):
        raise ValueError('FFT H must be evaluated away from copper planes')
    if not isinstance(refine, (int, np.integer)) or refine < 1:
        raise ValueError('refine must be a positive integer')
    dx, dy = (x[1] - x[0]) / refine, (y[1] - y[0]) / refine
    grids, offsets = [], []
    for dim, axis, step in ((0, x, dx), (1, y, dy)):
        lo = min(
            0,
            int(np.floor((model.nodes[:, dim].min() - axis[0]) / step)) - 2)
        hi = max(refine * (len(axis) - 1),
                 int(np.ceil((model.nodes[:, dim].max() - axis[0]) / step)) + 2)
        grids.append(axis[0] + np.arange(lo, hi + 1) * step)
        offsets.append(-lo)
    gx, gy = grids
    nx, ny = len(gx), len(gy)
    shape = (next_fast_len(3*nx - 2), next_fast_len(3*ny - 2))
    rx = np.arange(1 - nx, nx)[:, None] * dx
    ry = np.arange(1 - ny, ny)[None, :] * dy
    multi = np.ndim(fi) > 0
    fis = list(np.atleast_1d(fi))
    out = np.zeros((len(x), len(y), len(heights), 3, len(fis)), complex)
    crop = (slice(nx - 1 + offsets[0], nx - 1 + offsets[0] + refine * len(x),
                  refine),
            slice(ny - 1 + offsets[1], ny - 1 + offsets[1] + refine * len(y),
                  refine))
    sim = SimpleNamespace(rwg=model.rwg, tri_idx=model.layer_tris)
    for lay, idx in model.layer_tris.items():
        # Bound spectral working memory when refinement or harmonic count grows.
        block_size = max(1, (64 * 1024**2) // (np.prod(shape) * 16 * 6))
        for first in range(0, len(fis), block_size):
            spectra = [[], []]
            for k in fis[first:first + block_size]:
                jx, jy = current_deposit(sim, res.i[k, :model.ne] / model.rwg.l,
                                         gx, gy, lay)
                spectra[0].append(fft2(jx, s=shape))
                spectra[1].append(fft2(jy, s=shape))
            jx, jy = [np.stack(s, axis=-1) for s in spectra]
            for h, height in enumerate(heights):
                dz = height - model.z_of[idx[0]]
                kernel = dx*dy / (4*np.pi * (rx*rx + ry*ry + dz*dz)**1.5)
                kx, ky, kz = [
                    fft2(v * kernel, s=shape)[..., None] for v in (rx, ry, dz)
                ]
                for c, spectrum in enumerate((jy*kz, -jx*kz, jx*ky - jy*kx)):
                    out[:, :, h, c, first:first + block_size] += (ifft2(
                        spectrum, axes=(0, 1))[crop])
    X, Y, Z = np.meshgrid(x, y, heights, indexing='ij')
    pts = np.stack([X, Y, Z], axis=-1).reshape(-1, 3)
    p0, p1, currents = [], [], []
    for k, (vx, vy, rr, za, zb) in enumerate(model.vseg):
        p0.append([vx, vy, za]); p1.append([vx, vy, zb])
        currents.append(res.i[fis, model.ne + k])
    a, b = model._part_ends()
    p0, p1 = p0 + a, p1 + b
    currents += [
        res.i[fis, model.ne + model.nv + k] for k in range(len(model.parts))
    ]
    if p0:
        out += h_segments(p0, p1, np.asarray(currents), pts).reshape(out.shape)
    return out if multi else out[..., 0]
