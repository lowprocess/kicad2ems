"""The radiated EMC questions on mom25d (kicad2ems.core.radiated,
.susceptibility, .emission): a short top-loaded element over the ground (air
between, so its effective length is closed form) receives a plane wave as its
effective length says and as an independent plane-wave solve of the same MoM
system says; two of them 3 wavelengths apart couple as Friis says from their
solved gains; its field at a distance is eta k I 2h / 4 pi d; an antenna run's
array file (mom25d's Y form, an FDTD solver's S form) gives what the solve gives;
the far field's grid samples far_field_dirs; both tools end to end."""
import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

from k2e_fixtures import ROOT
from kicad2ems.core import radiated as R
from kicad2ems.core.constants import C0, ETA0

sys.path.insert(0, os.path.join(ROOT, 'examples'))


def element_board(path, h=1.0, a=2.0, xs=(0.0,), W=None):
    """a x a mm patches h mm over a ground (air between), each fed at its
    centre by a port: short top-loaded elements over the ground, xs mm apart
    along x."""
    from _pcb import Items

    class B(Items):
        def __init__(self):
            self.items, self.nets = [], ['', 'GND', 'S']
    span = max(xs) - min(xs)
    W = W or span + 20.0
    b = B()
    b.rect(0, 0, W, 20.0, layer='B.Cu', net=1)
    for i, x in enumerate(xs):
        xc = W / 2 + x
        b.rect(xc - a / 2, 10 - a / 2, xc + a / 2, 10 + a / 2, net=2)
        b.port_footprint(f'TP{i + 1}', f'P{i + 1}', xc, 10.0, 0, z0=50)
    b.write_board(str(path), W, 20.0, [h], 1.0, 0.0)
    return str(path)


def fields(path, fmax=6e9):
    from kicad2ems.core.config import build_settings
    s = build_settings(dict(board=path, solvers='mom25d', fmin=0.1, fmax=6,
                            out=os.path.dirname(path), mom_thickness='off'))
    return R.PortFields.solve(s, 1e8, fmax, log=lambda *a: None)


@pytest.fixture(scope='module')
def element(tmp_path_factory):
    return fields(element_board(tmp_path_factory.mktemp('el') / 'el.kicad_pcb'))


def _plane_wave(sim, f, th, ph):
    """MoM's excitation for a theta-polarised plane wave of 1 V/m arriving
    from (th, ph) over the PEC ground in air: the incident field and its image,
    tested on each RWG function (centroid rule) and integrated along each via
    chain."""
    k = 2 * np.pi * f / C0
    u = R.unit(th, ph)
    t, p = np.radians(th), np.radians(ph)
    pol = np.array([np.cos(t) * np.cos(p), np.cos(t) * np.sin(p), -np.sin(t)])

    def E(r):
        rm = r * np.array([1, 1, -1])
        return (pol[None] * np.exp(1j * k * r @ u)[:, None]
                + (R.REFLECT * pol)[None] * np.exp(1j * k * rm @ u)[:, None])
    rw = sim.rwg
    z = np.zeros(len(rw.area))
    for kk, idx in sim.tri_idx.items():
        z[idx] = sim.st.z[kk]
    cen = np.column_stack([rw.cent[:, :2], z])
    Ec = E(cen)
    out = np.zeros((sim.Ne + sim.Nv, 1), complex)
    for tri, sgn, v in ((rw.tp, 1, rw.vp), (rw.tm, -1, rw.vm)):
        fv = sgn * (rw.l / 2)[:, None] * (cen[tri, :2] - v[:, :2])
        out[:len(rw.l), 0] += (fv * Ec[tri, :2]).sum(1)
    for c, (via, a, b) in enumerate(sim.chains):
        zs = np.linspace(sim.st.z[a], sim.st.z[b], 41)
        pts = np.column_stack([np.full_like(zs, via.x),
                               np.full_like(zs, via.y), zs])
        out[sim.Ne + c, 0] = np.trapezoid(E(pts)[:, 2], zs)
    return out


def test_a_short_element_receives_by_its_effective_length(element):
    """The voltage on its 50 ohm load from a theta-polarised wave: E0 2h
    sin(theta) z / (z + Zin) (its open-circuit voltage through its load; 2h
    with its image), and a plane-wave solve of the same system (its
    short-circuit current through the load) agrees to its rounding."""
    pf = element
    for f in (0.3e9, 1e9):
        G = pf.grid(f, 5.0)
        Vt, Vp = R.receive_grid(G, pf.loads, f)
        _, Y = pf.terminated(f)
        z, zin = pf.loads[0], 1 / Y[0, 0]
        want = 2e-3 * np.sin(np.radians(G.theta)) * abs(z / (z + zin))
        got = np.abs(Vt[0, :, 0])
        assert np.abs(got - want)[1:].max() < 5e-4 * want.max()
        assert np.abs(Vp).max() < 1e-2 * want.max()
        for th, ph in ((30.0, 0.0), (60.0, 40.0), (85.0, 120.0)):
            I = pf.sim.currents(f, _plane_wave(pf.sim, f, th, ph))
            isc = np.atleast_1d(pf.sim.port_current(I, 0))[0]
            v = abs(isc * z / (1 + z * Y[0, 0]))
            i = int(np.argmin(np.abs(G.theta - th)))
            assert np.isclose(v, abs(Vt[0, i, int(ph / 5)]), rtol=1e-5)


def test_two_elements_couple_as_friis_says(tmp_path):
    """Two elements 3 wavelengths apart at 5 GHz: their solved S21 against
    Friis from their solved realized gains toward each other, over the ground
    along it (the incident wave and its reflection one: |S21| = sqrt(G1 G2)
    lambda / 8 pi R). Measured: 0.014 dB (0.0017 dB at 10 wavelengths)."""
    f = 5e9
    lam = C0 / f
    Rm = 3 * lam * 1e3
    pf = fields(element_board(tmp_path / 'two.kicad_pcb',
                              xs=(-Rm / 2, Rm / 2)))
    _, Y = pf.terminated(f)
    z = pf.loads
    S = (np.eye(2) - z[:, None] * Y) @ np.linalg.inv(np.eye(2) + z[:, None] * Y)
    G = pf.grid(f, 5.0)

    def gain(j, phi):
        F = G.at(R.unit(np.array([90.0]), np.array([phi])))[j, 0]
        return 4 * np.pi * np.sum(np.abs(F * 2 * np.sqrt(z[j])) ** 2) / ETA0
    friis = math.sqrt(gain(0, 0.0) * gain(1, 180.0)) * lam / (
        8 * np.pi * Rm * 1e-3)
    assert abs(20 * math.log10(abs(S[1, 0]) / friis)) < 0.05


def test_its_field_at_a_distance(element):
    """emission's site, the board flat, no floor, the antenna level with it:
    eta k |I| 2h / (4 pi d), I = 1 / (z + Zin) for 1 V behind the load."""
    pf = element
    for f in (0.1e9, 1e9):
        G = pf.grid(f, 3.0, drive=[1.0])
        s = R.site(lambda u: G.at(u)[0], f, 3.0, 0.8, [0.8], [0.0],
                   ('flat',), False)
        _, Y = pf.terminated(f)
        I = 1 / (pf.loads[0] + 1 / Y[0, 0])
        k = 2 * np.pi * f / C0
        assert np.isclose(s['E'], ETA0 * k * abs(I) * 2e-3 / (4 * np.pi * 3),
                          rtol=1e-5)
        assert s['pol'] == 'vertical'


def test_an_array_file_gives_what_the_solve_gives(element, tmp_path):
    """The element's fields as mom25d's antenna mode writes them (per unit port
    voltage with Y) and as an FDTD solver's (per unit incident wave, S): the
    same received voltage as the solve, to the file's single precision."""
    from kicad2ems.core import phased
    pf = element
    f = np.array([0.5e9, 1e9])
    th, ph = np.arange(0, 91, 5.0), np.arange(0, 361, 5.0)
    from kicad2ems.mom25d.farfield import far_field
    Eth, Eph, Ys, Ss = [], [], [], []
    for x in f:
        I = pf.sim.currents(x, pf.sim.excitation())
        Y = np.array([[pf.sim.port_current(I, 0)[0]]])
        et, ep = far_field(pf.sim, I, x, th, ph)
        Eth.append(et), Eph.append(ep), Ys.append(Y)
        z = pf.loads
        Ss.append((1 - z * Y) / (1 + z * Y))
    kw = dict(theta=th, phi=ph, theta_sphere=th, freq=f, ports=['P1'],
              zc=[50.0], drive=[1.0], f_pattern=f[0])
    phased.save(tmp_path / 'y_array.npz', Eth=Eth, Eph=Eph, Y=Ys, **kw)
    # per incident wave: the terminated field per source volt x 2 sqrt(z)
    Ea = [np.asarray(e) * (2 * np.sqrt(50.0)) / (1 + 50.0 * Y[0, 0])
          for e, Y in zip(Eth, Ys)]
    Eb = [np.asarray(e) * (2 * np.sqrt(50.0)) / (1 + 50.0 * Y[0, 0])
          for e, Y in zip(Eph, Ys)]
    phased.save(tmp_path / 's_array.npz', Eth=Ea, Eph=Eb, S=Ss,
                excited=[True], **kw)
    for name in ('y_array.npz', 's_array.npz'):
        pa = R.PortFields.load(str(tmp_path / name))
        for x in f:
            Ga = pa.grid(x, 5.0)
            Vt, _ = R.receive_grid(Ga, pa.loads, x)
            # the solve's grid at the file's directions (its nodes are
            # among the solve's)
            T, P = np.meshgrid(Ga.theta, Ga.phi, indexing='ij')
            Es = pf.grid(x, 5.0).at(R.unit(T, P).reshape(-1, 3))
            Wt, _ = R.receive_grid(
                R.Grid(Ga.theta, Ga.phi, Es.reshape((1,) + T.shape + (3,))),
                pf.loads, x)
            assert np.allclose(np.abs(Vt), np.abs(Wt), rtol=1e-5,
                               atol=1e-6 * np.abs(Wt).max())


def test_the_grid_samples_the_far_field(tmp_path):
    """A microstrip's terminated far field on mom25d: the 3 degree grid's
    interpolation against far_field_dirs in random directions, to 1.5 % of
    the largest (1.1 % measured, where the field falls to 0 at grazing over
    the grounded dielectric); far_field_dirs in small blocks of directions the
    same as in one."""
    from kicad2ems.mom25d import farfield
    board = os.path.join(ROOT, 'tests', 'mom25d', 'boards', 'thru.json')
    pf = fields(board)
    f = 4e9
    G = pf.grid(f, 3.0, drive=[1.0, 0.0])
    rng = np.random.default_rng(2)
    q = rng.normal(size=(300, 3))
    q[:, 2] = np.abs(q[:, 2])
    q /= np.linalg.norm(q, axis=1)[:, None]
    t, p = R.angles(q)
    I, _ = pf.terminated(f)
    Et, Ep = farfield.far_field_dirs(pf.sim, I[:, :1], f, t, p)
    want = R.cartesian(Et[0], Ep[0], t, p)
    got = G.at(q)[0]
    assert (np.linalg.norm(got - want, axis=1).max()
            < 0.015 * np.linalg.norm(want, axis=1).max())
    old = farfield.PHASE_BLOCK
    try:
        farfield.PHASE_BLOCK = 1000
        Et2, Ep2 = farfield.far_field_dirs(pf.sim, I[:, :1], f, t, p)
    finally:
        farfield.PHASE_BLOCK = old
    assert np.allclose(Et2, Et, rtol=1e-12) and np.allclose(Ep2, Ep,
                                                            rtol=1e-12)


def test_the_tools_end_to_end(tmp_path):
    """Both tools from the command line on a 30 mm microstrip over the
    ground (two ports)."""
    board = os.path.join(ROOT, 'tests', 'mom25d', 'boards', 'thru.json')
    env = dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.susceptibility', board,
         '--out', str(tmp_path / 's'), '--points', '4', '--fmax', '3',
         '--threshold', '0.5'], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr[-2000:]
    d = json.load(open(tmp_path / 's' / 'susceptibility.json'))
    assert d['engine'] == 'mom25d' and d['ports'] == ['P1', 'P2']
    v = [t['mv'] for t in d['table'] if t['field'] == 10]
    # a 0.2 mm high line: about E0 h at 10 V/m, a millivolt or two
    assert 0.1 < max(v) < 10 and d['flagged']
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.emission', board, '--out',
         str(tmp_path / 'e'), '--f0', '100', '--fmax', '1', '--drive', 'P1',
         '--heights', '1,4,0.5', '--turn-step', '15'],
        capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr[-2000:]
    d = json.load(open(tmp_path / 'e' / 'emission.json'))
    assert d['exact'] and len(d['emission']['freq']) == 5   # 100 .. 900 MHz
    assert d['worst']['margin_db'] > 0 and os.path.isfile(
        tmp_path / 'e' / 'thru_emission.png')
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.emission', board, '--out',
         str(tmp_path / 'x'), '--signal', 'clock'], capture_output=True,
        text=True, env=env)
    assert p.returncode == 2 and 'frequency' in p.stderr
