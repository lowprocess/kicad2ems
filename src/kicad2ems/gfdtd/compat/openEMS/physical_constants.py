"""openEMS.physical_constants as upstream defines them (MUE0 = 4e-7 pi, the
pre-2019 value: scripts compute what they would on openEMS)."""
import numpy as np

C0 = 299792458  # m/s
MUE0 = 4e-7 * np.pi  # N/A^2
EPS0 = 1 / (MUE0 * C0 ** 2)  # F/m
Z0 = np.sqrt(MUE0 / EPS0)  # Ohm
