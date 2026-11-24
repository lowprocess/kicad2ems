"""Plane-pair cavities' resonances (kicad2ems.core.cavities): a rectangle's
modes against the closed form, a ground via's short, a port's coupling."""
import math

import numpy as np
import pytest

from kicad2ems.core import cavities as CV


def _cavity(a, b, vias=(), er=4.0, d=0.2, dmax=0.5):
    """A rectangular plane pair a x b mm (corner at the origin), GND both
    planes; vias [(x, y, net)]: GND ones on both planes' copper (shorts), the
    others in 0.4 mm antipads in both (ports)."""
    from shapely import STRtree
    from shapely.geometry import Point, box
    board = dict(vias=[dict(x=x, y=y, r=0.15, z0=0.0, z1=1.0, net=n)
                       for x, y, n in vias])
    g = box(0, 0, a, b)
    plane = g
    for x, y, n in vias:
        if n != 'GND':
            plane = plane.difference(Point(x, y).buffer(0.4))
    index = (STRtree([plane]), [(plane, 'GND')])
    cav = CV.Cavity(board, ('L2', 'L1'), (0.0, 1.0), g, d, er, 0.01,
                    (index, index), [], log=lambda *x: None)
    return cav.build(20e9, dmax)


def _closed(a, b, er, n):
    """The open-edged rectangle's first n mode frequencies (Hz), the static one
    (0) first."""
    fs = sorted(C * math.hypot(m / (a * 1e-3), k / (b * 1e-3))
                for m in range(12) for k in range(12)
                for C in [CV.C0 / (2 * math.sqrt(er))])
    return np.array(fs[:n])


def test_a_rectangle_s_modes_are_the_closed_form_s():
    """40 x 25 mm, er 4, open edges: the static mode counted apart (no ground
    via), the next eight within 0.5 %; Q from the loss tangent and the skin
    depth."""
    cav = _cavity(40.0, 25.0)
    res = cav.modes(8e9)
    want = _closed(40.0, 25.0, 4.0, 9)
    assert res['static'] == 1          # (no ground via: the planes untied)
    assert np.abs(res['f'][:8] / want[1:9] - 1).max() < 5e-3
    # (tan d 0.01 and the skin depth over 0.2 mm: 1 / Q = 0.01 + delta / d)
    assert np.all(res['Q'] < 1 / 0.01) and np.all(res['Q'] > 50)


def test_a_ground_via_shorts_the_modes_that_are_not_zero_there():
    """A ground via at the centre: the static mode gone, in its place the
    planes' capacitance against the via's inductance (an LC mode, below every
    rectangle mode: 0.96 GHz here, 177 pF); (1,0), (0,1), (1,1) (zero there)
    unchanged, (2,0) (its peak there) raised; a port at a corner sees (1,0)
    at sqrt(2) x its RMS."""
    a, b = 40.0, 25.0
    cav = _cavity(a, b, vias=[(a / 2, b / 2, 'GND'), (0.3, 0.3, 'SIG')])
    assert len(cav.shorts) == 1 and len(cav.ports) == 1
    res = cav.modes(8e9)
    f10, f01 = (CV.C0 / (2 * math.sqrt(4.0)) / (x * 1e-3) for x in (a, b))
    f11, f20 = math.hypot(f10, f01), 2 * f10
    f = res['f']
    assert 0.3e9 < f[0] < 0.8 * f10                 # (the LC mode)
    for want in (f10, f01, f11):
        assert np.abs(f / want - 1).min() < 5e-3
    assert np.abs(f / f20 - 1).min() > 0.02         # (2,0) moved
    m10 = int(np.argmin(np.abs(f - f10)))
    assert res['coupling'][0, m10] == pytest.approx(math.sqrt(2), rel=0.03)


def test_a_via_s_peak_impedance_is_the_cavity_s():
    """The cavity's impedance at a port at a resonance (z_peak: the mode's
    term of the modal sum) against the closed-form rectangle's (pcbqs.pdn
    rect_cavity_z: every mode, the same losses) at that mode's frequency:
    within 10 % at (1,0) and (0,1), a port near a corner."""
    from kicad2ems.pcbqs.pdn.cavity import rect_cavity_z
    a, b = 40.0, 25.0
    cav = _cavity(a, b, vias=[(1.0, 1.0, 'SIG')], dmax=0.4)
    res = cav.modes(5e9)
    for want in (CV.C0 / (2 * math.sqrt(4.0)) / (x * 1e-3) for x in (a, b)):
        m = int(np.argmin(np.abs(res['f'] - want)))
        f = res['f'][m]
        z = rect_cavity_z(a, b, 0.2, 4.0, 0.01, [(1.0, 1.0, 0.3)], [f])
        assert res['z_peak'][0, m] == pytest.approx(abs(z[0, 0, 0]), rel=0.1)


def test_a_ground_via_s_modes_are_the_zeros_of_the_open_cavity_s_impedance():
    """A via shorting the planes at (7, 5) mm: the cavity's modes are where
    the open rectangle's impedance at that point (rect_cavity_z, lossless, a
    square port of uniform current whose log radius, 0.447 w, is the via's)
    passes through zero. The first six within 1 % (0.3 % at most measured,
    the lowest, the planes' capacitance against the via's inductance, the
    least close; before the discs' grading, 2026-11-24, it had been 4 %
    off: the via's own field meshed in a few rings)."""
    from kicad2ems.pcbqs.pdn.cavity import rect_cavity_z
    a, b, x, y = 40.0, 25.0, 7.0, 5.0
    res = _cavity(a, b, vias=[(x, y, 'GND')], dmax=0.4).modes(6e9)
    f = np.linspace(0.2e9, 6e9, 3001)
    im = rect_cavity_z(a, b, 0.2, 4.0, 0.0, [(x, y, 0.15 / 0.4470)], f,
                       modes=400, t=1e3, sigma=1e30)[:, 0, 0].imag
    i = np.flatnonzero((im[:-1] < 0) & (im[1:] >= 0))
    zeros = f[i] - im[i] * (f[i + 1] - f[i]) / (im[i + 1] - im[i])
    assert len(zeros) >= 6
    assert np.abs(res["f"][:6] / zeros[:6] - 1).max() < 0.01


def test_a_ground_via_beside_a_driven_one_takes_its_resonance_away():
    """ground_spot: the nearest place at least 0.5 mm from a via where a
    ground via's disc lies on both planes (not in the via's 0.4 mm antipad);
    a ground via there takes the cavity's peak at the signal via, from the
    rectangle's (1,0) mode, down by more than seven times (8.6 here; 7.7 at
    0.12 mm cells: the old mesh's 12, its cells round the ground via too
    coarse, came down to 8.9 there)."""
    a, b, x, y = 40.0, 25.0, 3.0, 12.5
    cav = _cavity(a, b, vias=[(x, y, 'SIG')])
    res = cav.modes(6e9)
    before = res['z_peak'][0].max()
    sx, sy, dist = CV.ground_spot(cav, x, y)
    assert 0.5 <= dist <= 1.0 and math.hypot(sx - x, sy - y) >= 0.7
    fixed = _cavity(a, b, vias=[(x, y, 'SIG'), (sx, sy, 'GND')])
    assert len(fixed.shorts) == 1
    after = fixed.modes(6e9)['z_peak'][0].max()
    assert before > 50 and after < before / 7


def test_the_impedance_between_vias_is_the_closed_form_rectangle_s():
    """Cavity.impedance on the 40 x 25 mm rectangle (three vias in 0.4 mm
    antipads; d 0.2 mm, er 4, tan d 0.01) from 0.2 to 8 GHz, the mesh at
    lambda / 12 there: every self and transfer term within 4 % of the closed
    form's largest (rect_cavity_z, every mode to 700 x 700: a square port of
    uniform current, whose log radius 0.447 w is the barrel's; measured
    0.8-3.1 %), and symmetric."""
    from kicad2ems.pcbqs.pdn.cavity import rect_cavity_z
    a, b = 40.0, 25.0
    vias = [(1.0, 1.0, 'SIG'), (20.0, 12.0, 'SIG'), (35.0, 20.0, 'SIG')]
    f = np.linspace(0.2e9, 8e9, 79)
    lam = CV.C0 / (8e9 * 2.0) * 1e3
    Z = _cavity(a, b, vias=vias, dmax=lam / 12).impedance(f)
    R = rect_cavity_z(a, b, 0.2, 4.0, 0.01,
                      [(x, y, 0.15 / 0.4470) for x, y, _ in vias], f,
                      modes=700)
    for i, j in [(0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2)]:
        assert (np.abs(Z[:, i, j] - R[:, i, j]).max()
                < 0.04 * np.abs(R[:, i, j]).max())
    assert np.abs(Z - Z.transpose(0, 2, 1)).max() < 1e-9 * np.abs(Z).max()


@pytest.mark.parametrize('s', [0.5, 1.0, 5.0])
def test_a_via_s_loop_through_a_ground_via_is_the_two_wire_inductance(s):
    """The field round a via's disc: a via s mm from a ground via (barrels
    0.15 mm) in a 60 mm square, at 10 MHz: its loop inductance mu0 d / 2 pi
    ln(s^2 / a^2) within 3 % (0.98-1.005 measured: the cells graded round
    the discs (QUALITY), the discs meshed at RING x the radius; 36-71 % low
    without them)."""
    A = 60.0
    cav = _cavity(A, A, vias=[(A / 2, A / 2, 'SIG'),
                              (A / 2 + s, A / 2, 'GND')])
    z = cav.impedance([10e6])[0, 0, 0]
    L = z.imag / (2 * math.pi * 10e6)
    want = CV.MU0 * 0.2e-3 / (2 * math.pi) * math.log(s ** 2 / 0.15 ** 2)
    assert L == pytest.approx(want, rel=0.03)


@pytest.mark.parametrize('s', [5.0, 8.0])
def test_a_far_ground_via_as_a_well_is_its_barrel(monkeypatch, s):
    """A ground via meshed as a vertex alone (past DISCS, further than NEAR
    from every via that is no ground via) and tied to the planes as a well
    (Peaceman: the inductance between the radius the vertex stands for and
    the barrel's): the loop through it, s mm from a via, within 3 % of the
    two-wire formula at 1 mm cells (0.1-0.9 % measured; 38 % off held at V = 0
    alone)."""
    monkeypatch.setattr(CV, 'DISCS', 0)
    A = 60.0
    cav = _cavity(A, A, vias=[(A / 2, A / 2, 'SIG'),
                              (A / 2 + s, A / 2, 'GND')], dmax=1.0)
    z = cav.impedance([10e6])[0, 0, 0]
    assert cav.wells == 1
    L = z.imag / (2 * math.pi * 10e6)
    want = CV.MU0 * 0.2e-3 / (2 * math.pi) * math.log(s ** 2 / 0.15 ** 2)
    assert L == pytest.approx(want, rel=0.03)


def test_a_port_on_a_ground_via_leaves_no_unknown_without_a_vertex():
    """A via whose disc lies on a ground via's (merged with it: no port of its
    own, its impedance 0) before another: the other port's unknown the first,
    K + M with no empty row (numbered by their place in the list, the first
    port had left an unknown of no vertex: K + M singular), its impedance
    finite."""
    from shapely import STRtree
    from shapely.geometry import box
    g = box(0, 0, 40.0, 25.0)
    via = lambda x, y, z1, n: dict(x=x, y=y, r=0.15, z0=0.0, z1=z1, net=n)
    # (the signal vias end half way: ports; the ground via through both)
    board = dict(vias=[via(10.0, 10.0, 1.0, 'GND'), via(10.05, 10.0, 0.5, 'S1'),
                       via(30.0, 15.0, 0.5, 'S2')])
    index = (STRtree([g]), [(g, 'GND')])
    cav = CV.Cavity(board, ('L2', 'L1'), (0.0, 1.0), g, 0.2, 4.0, 0.01,
                    (index, index), [], log=lambda *x: None).build(8e9, 0.5)
    assert len(cav.shorts) == 1 and len(cav.ports) == 2
    assert list(cav.port_dof) == [-1, 0]
    d = (cav.K + cav.M).diagonal()
    assert (np.abs(d) > 0).all()
    Z = cav.impedance([1e9, 3e9])
    assert np.isfinite(Z).all() and (Z[:, 0, :] == 0).all()
    assert (Z[:, 1, 1].imag != 0).all()
