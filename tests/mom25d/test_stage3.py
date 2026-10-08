"""
Stage-3 checks (fast): vias, off-plane integrals, mesher robustness, lumped elements, AAA,
KiCad / Gerber import, fast operator vs dense.     python -m pytest tests -q
"""
import os
import sys
import subprocess

import numpy as np
import pytest
from scipy.integrate import quad

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from mom25d import C0, MU0, EPS0                                          # noqa: E402
from mom25d_ml import Layer, LayerStack, line_char_ml                     # noqa: E402
from mom25d_via import ViaKernels                                         # noqa: E402
from mom25d_sing import offplane_pairs                                    # noqa: E402
from mom25d_geo import mesh_polygons, LinePort                            # noqa: E402
from mom25d_sim import Simulation, SeriesLumped, Via, ViaPort, rlc        # noqa: E402
from mom25d_afs import aaa                                                # noqa: E402


def test_via_kernels_exact_in_air():
    """Via-via reaction (self and mutual) and the via->strip correction D against images."""
    h, a, f = 0.5e-3, 0.15e-3, 10e9
    w = 2 * np.pi * f; k = w / C0
    vk = ViaKernels(f, LayerStack([Layer(h, 1.0)]), [(0, 1)], [1], 20e-3)
    assert np.max(np.abs(vk.D[(1, (0, 1))](np.array([1e-4, 1e-3])))) < 1e-10      # D == 0 in air
    for d in (a, 1e-3):
        tot = vk.R((0, 1), (0, 1), d)[0] + vk.homog((0, 1), (0, 1), d)[0]
        Rp = np.hypot(d, 2 * h)
        phi = (np.exp(-1j * k * d) / d - np.exp(-1j * k * Rp) / Rp) / (1j * w * 4 * np.pi * EPS0)
        g = lambda z, zp: np.exp(-1j * k * np.hypot(d, z - zp)) / np.hypot(d, z - zp)
        I = sum((1j ** p) * quad(lambda z: quad(lambda zp: (g(z, zp).real, g(z, zp).imag)[p], -h, h,
                                                points=[z], limit=200)[0], 0, h, limit=200)[0] for p in (0, 1))
        ref = 1j * w * MU0 / (4 * np.pi) * I + phi
        assert abs(tot + phi - ref) / abs(ref) < 1e-4


def test_offplane_reduces_to_inplane_and_converges():
    from mom25d import make_mesh, RWG, singular_moments
    nodes, tris = make_mesh([(0, 0, 0.3e-3, 0.06e-3)], 0.15e-3, 0.03e-3)
    M0, _, _ = singular_moments(RWG(nodes, tris))
    m0, *_ = offplane_pairs(nodes, tris, [0, 0], [0, 3], 0.0)
    assert np.allclose(m0, [M0[0, 0], M0[0, 3]], rtol=1e-12)
    a, *_ = offplane_pairs(nodes, tris, [0], [3], 1e-3)          # far: ~ A_p A_q / R
    V = nodes[tris]
    ar = 0.5 * np.abs((V[:, 1, 0] - V[:, 0, 0]) * (V[:, 2, 1] - V[:, 0, 1]) - (V[:, 1, 1] - V[:, 0, 1]) * (V[:, 2, 0] - V[:, 0, 0]))
    R = np.sqrt(np.sum((V[0].mean(0) - V[3].mean(0)) ** 2) + 1e-6)
    assert abs(a[0] / (ar[0] * ar[3] / R) - 1) < 0.02


def test_mesher_t_junction_port_lines():
    """Port cut lines ending on a polygon edge must split the edge exactly (no sliver cascade)."""
    from shapely.geometry import box
    w = 0.15e-3
    g = box(14e-3, -5.075e-3, 22.075e-3, -4.925e-3)
    lp = LinePort(1, (21.85e-3, -5e-3), (-1, 0), w, 0.0)
    lines = [lp.line(s) for s in np.arange(0, 6e-3, 0.3e-3)]
    nodes, tris, _ = mesh_polygons(g, 0.3e-3, w, 4, lines=lines)
    near = (np.abs(nodes[:, 0] - 17.95e-3) < 0.02e-3) & (np.abs(nodes[:, 1] + 5.075e-3) < 1e-6)
    assert near.sum() <= 2


def test_series_resistor_matches_circuit_theory():
    w, h = 0.35e-3, 0.2e-3
    st = LayerStack([Layer(h, 4.3)])
    g, zc = line_char_ml(st, 1, w, [5e9]); z = zc[0].real
    L = 12e-3
    p1 = LinePort(1, (-L / 2 + 0.5e-3, 0), (1, 0), w, 4e-3, s0=1.5e-3, step=0.7e-3, zc=z)
    p2 = LinePort(1, (L / 2 - 0.5e-3, 0), (-1, 0), w, 4e-3, s0=1.5e-3, step=0.7e-3, zc=z)
    sim = Simulation(st, {1: [(-L / 2, -w / 2, L / 2, w / 2)]}, [p1, p2],
                     lumped=[SeriesLumped(1, (0, 0), (1, 0), w, 50.0)], dmax=0.25e-3, w_ref=w, verbose=False)
    S = sim.sparams(5e9)
    assert abs(abs(S[1, 0]) - 2 * z / (2 * z + 50)) < 0.01


def test_via_ports_on_a_line_are_reciprocal_and_lossless():
    from shapely.geometry import box, Point
    w, h, L = 0.35e-3, 0.2e-3, 10e-3
    st = LayerStack([Layer(h, 4.3)])
    v1, v2 = Via(-L / 2, 0, 0, 1, 0.1e-3), Via(L / 2, 0, 0, 1, 0.1e-3)
    geo = [box(-L / 2, -w / 2, L / 2, w / 2), Point(-L / 2, 0).buffer(0.25e-3), Point(L / 2, 0).buffer(0.25e-3)]
    sim = Simulation(st, {1: geo}, [ViaPort(v1, 54.0), ViaPort(v2, 54.0)], dmax=0.3e-3, w_ref=w, verbose=False)
    S = sim.sparams(2e9)
    assert abs(S[0, 1] - S[1, 0]) < 1e-8
    assert abs(np.sum(np.abs(S[:, 0]) ** 2) - 1) < 2e-3


def test_aaa_interpolates_resonances():
    def F(f):
        g = f / 1e9
        return np.array([sum(1 / (1j * (g - p) + 0.01) for p in (2.4, 3.6))])
    fs = np.linspace(1e9, 6e9, 25)
    r = aaa(fs / 1e9, np.array([F(f) for f in fs]))
    fg = np.linspace(1e9, 6e9, 301)
    assert np.max(np.abs(r(fg / 1e9)[:, 0] - np.array([F(f)[0] for f in fg]))) < 1e-6


@pytest.fixture(scope='module')
def demo_board(tmp_path_factory):
    d = tmp_path_factory.mktemp('board')
    base = str(d / 'demo')
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'make_demo_board.py'), base])
    return base


def test_kicad_import(demo_board):
    from mom25d_kicad import KiCadBoard
    b = KiCadBoard(demo_board + '.kicad_pcb')
    m = b.build(nets=['RF', 'GND'], ground='B.Cu', pos_file=demo_board + '-top.pos', include_mask=False)
    assert m.iface == {'In2.Cu': 1, 'In1.Cu': 2, 'F.Cu': 3}
    assert [l.h for l in m.stack.layers] == pytest.approx([0.2e-3] * 3)
    assert sorted(m.port_fps) == [1, 2]
    ps = m.ports(dmax=0.3e-3)
    assert [p.layer for p in ps] == [3, 1]
    assert ps[0].w == pytest.approx(0.35e-3, rel=1e-6) and ps[1].w == pytest.approx(0.15e-3, rel=1e-6)
    assert len(m.vias) == 23
    signal = [v for v in m.vias if v.name.startswith('RF')][0]
    assert (signal.k_bot, signal.k_top) == (1, 3)


def test_gerber_roundtrip_matches_native(demo_board, tmp_path):
    from shapely.ops import unary_union
    from mom25d_kicad import KiCadBoard
    from mom25d_gerber import GerberBoard
    subprocess.check_call([sys.executable, os.path.join(ROOT, 'examples', 'export_demo_gerbers.py'),
                           demo_board + '.kicad_pcb', str(tmp_path)])
    k = KiCadBoard(demo_board + '.kicad_pcb')
    g = GerberBoard(str(tmp_path / 'demo-job.gbrjob'))
    for lay in ('In1.Cu', 'In2.Cu', 'B.Cu'):
        a = unary_union([x for l, n, x in k.items if l == lay])
        b = unary_union([x for l, n, x in g.items if l == lay])
        assert a.symmetric_difference(b).area < 0.05                    # mm^2
    assert len(g.vias) == len(k.vias)


def test_fast_operators_match_dense():
    """Direct (centroid far field + exact near correction) and ACA/GMRES against the dense reference
    on a two-port line between via ports."""
    from shapely.geometry import box, Point
    w, h, L = 0.35e-3, 0.2e-3, 10e-3
    st = LayerStack([Layer(h, 4.3, 0.02)])
    v1, v2 = Via(-L / 2, 0, 0, 1, 0.1e-3), Via(L / 2, 0, 0, 1, 0.1e-3)
    geo = [box(-L / 2, -w / 2, L / 2, w / 2), Point(-L / 2, 0).buffer(0.25e-3), Point(L / 2, 0).buffer(0.25e-3)]
    out = {}
    for solver in ('dense', 'direct', 'aca'):
        sim = Simulation(st, {1: geo}, [ViaPort(v1, 54.0), ViaPort(v2, 54.0)], dmax=0.3e-3, w_ref=w,
                         solver=solver, verbose=False)
        out[solver] = sim.sparams(4e9)
    assert np.max(np.abs(out['direct'] - out['dense'])) < 0.01
    assert np.max(np.abs(out['aca'] - out['direct'])) < 1e-3
