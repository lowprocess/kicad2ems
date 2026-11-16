"""
Copper surface roughness (--rough, um RMS): Hammerstad and Jensen's factor of the conductor loss,

    K(f) = 1 + 2 / pi atan(1.4 (rms / delta)^2),   delta = 1 / sqrt(pi f mu0 sigma)

from 1 (smooth, rms << delta) to 2 (rough, rms >> delta). In the skin-effect regime, K times the surface resistance
is the loss of a conductivity sigma / K^2: rough_sigma. Each solver applies it as it can (its capabilities'
'roughness'): mom25d and gfdtd over the band, openEMS and the line model as that conductivity at the band centre
(band_sigma), as --diel fixed fits the dielectric's loss there.
"""
import math

MU0 = 4e-7 * math.pi


def hammerstad(f, rms_um, sigma):
    """K at f (Hz; a float or a numpy array) for rms_um um RMS on copper of conductivity sigma (S/m)."""
    import numpy as np
    delta = 1 / np.sqrt(np.pi * np.asarray(f, float) * MU0 * sigma)
    return 1 + 2 / np.pi * np.arctan(1.4 * (rms_um * 1e-6 / delta) ** 2)


def rough_sigma(sigma, rms_um, f):
    """The conductivity whose smooth skin-effect loss at f is the rough copper's: sigma / K(f)^2."""
    return float(sigma / hammerstad(f, rms_um, sigma) ** 2)


def band_sigma(s, fmin_hz, fmax_hz):
    """The one conductivity of a solver without a roughness model: --sigma, or with --rough the rough copper's at the
    band centre. None without --sigma."""
    if not s.sigma:
        return None
    rms = getattr(s, 'rough', None)
    return rough_sigma(s.sigma, rms, 0.5 * (fmin_hz + fmax_hz)) if rms else s.sigma
