"""The line model's discontinuities in the scikit-rf backend (R-E15): an open end's, a step's and a bend's excess
capacitance, the formulas against dfsynth's, and an open stub against a line lengthened by its end's extension."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from kicad2ems.constants import C0
from kicad2ems.lines import formulas as FM
from kicad2ems.lines.elements import Element, LineModel


def test_the_formulas_are_dfsynths():
    ms = pytest.importorskip('dfsynth.microstrip')
    sub = next(v for k, v in vars(ms).items() if isinstance(v, type) and hasattr(v, 'open_end'))
    try:
        s = sub(h=0.8, er=4.3, name='t')
    except TypeError:
        s = sub(0.8, 4.3)
    w = 1.5
    assert FM.open_end_extension(w, 0.8, 4.3, s.line(w)[1]) == pytest.approx(s.open_end(w), rel=1e-12)
    assert FM.step_extension(0.5, w, 0.8, 4.3, s.line(w)[1]) == pytest.approx(s.step(0.5, w), rel=1e-12)


def test_a_bend_is_the_corners_scaled_by_its_area():
    c90 = FM.bend_capacitance(1.5, 0.8, 4.3)
    assert c90 == pytest.approx(((9.5 * 4.3 + 1.25) * 1.5 / 0.8 + 5.2 * 4.3 + 7.0) * 1e-12 * 1.5e-3)
    assert FM.bend_capacitance(1.5, 0.8, 4.3, 0.0) == 0.0 and 0 < FM.bend_capacitance(1.5, 0.8, 4.3, 45.0) < c90


def test_an_open_stub_is_its_line_lengthened_by_the_end():
    pytest.importorskip('skrf')
    from kicad2ems.solvers.lines.fastsolve import solve
    z0, ee, w, h, er, L = 50.0, 3.2, 1.5, 0.8, 4.3, 20.0
    e = Element('trace', 'p', 'n1', L, w=w, label='stub')
    e.xs, e.z0, e.eeff, e.er, e.h, e.field = 'microstrip', z0, ee, er, h, True          # its own Z0 and eeff
    lm = LineModel()
    lm.elements, lm.ports, lm.port_z0 = [e], {'P1': 'p'}, {'P1': 50.0}
    lm.junctions = [dict(node='n1', arms=[(e, 1)], widths=[w], xs='microstrip', kind='open', layer='F.Cu')]
    f = np.linspace(0.2e9, 3e9, 15)
    S, names = solve(lm, [SimpleNamespace(name='P1', z0=50.0)], f, backend='skrf')
    dl = FM.open_end_extension(w, h, er, ee)
    beta = 2 * np.pi * f * math.sqrt(ee) / C0
    zin = -1j * z0 / np.tan(beta * (L + dl) * 1e-3)
    assert S[:, 0, 0] == pytest.approx((zin - 50) / (zin + 50), abs=2e-3)       # (its C at the node: dl's line)
    lm.junctions = []
    S0, _ = solve(lm, [SimpleNamespace(name='P1', z0=50.0)], f, backend='skrf')
    assert np.abs(S0[:, 0, 0] - (zin - 50) / (zin + 50)).max() > 10 * np.abs(S[:, 0, 0] - (zin - 50) / (zin + 50)).max()
