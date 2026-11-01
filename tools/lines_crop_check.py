#!/usr/bin/env python3
"""
A straight trace of a real board, cropped, against the line model: the total shunt C (F) and series L (H) of two-port
results at low frequency. C = Im(sum Y) / w doesn't depend on the ports' series inductance, so it is what separates
line models; L includes the ports' lumped paths in openEMS's result. The CM5 Minima's PCIE_PI.nRST, 10.7 mm on In2.Cu:

    # region = "105.0,28.65,114.4,34.65", nets = ["/CM5/PCIE_PI.nRST", "GND"], cut_ports = true, fmin = 0.1, fmax = 10
    kicad2ems crop.toml --solvers openems,lines --out ems
    kicad2ems crop.toml --solvers lines --lines-field --out field
    python tools/lines_crop_check.py ems/*_openems.s2p ems/*_qucs.s2p field/*_qucs.s2p
"""
import sys
import numpy as np
import skrf

for path in sys.argv[1:]:
    n = skrf.Network(path)
    w = 2 * np.pi * n.f
    i = np.argmin(abs(n.f - 0.3e9))
    C = n.y[i].sum().imag / w[i]
    L = (1 / n.y[i, 0, 0]).imag / w[i]
    print(f"{path}: C {C * 1e12:.3f} pF, L {L * 1e9:.2f} nH at {n.f[i] / 1e9:.2f} GHz")
