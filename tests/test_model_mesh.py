"""Geometry, ports, automatic mesh, boundaries and post-processing (no openEMS install needed)."""
import os, sys, subprocess
import numpy as np
import pytest
from shapely.geometry import box, Point

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from kicad2openems.geometry import split_holes
from kicad2openems.mesh import smooth_lines, auto_mesh
from kicad2openems.model import Model as OEModel
from kicad2openems.sparams import mixed_mode
from kicad2openems.touchstone import read_touchstone, write_touchstone
from kicad2openems.kicad import KiCadBoard


@pytest.fixture(scope='session')
def demo(demo_files):
    return demo_files[0]


def test_split_holes_preserves_area():
    p = box(0, 0, 10, 6).difference(Point(3, 3).buffer(1)).difference(Point(7, 3).buffer(1.2))
    parts = split_holes(p)
    assert all(not q.interiors for q in parts)
    assert sum(q.area for q in parts) == pytest.approx(p.area, rel=1e-9)


def test_smooth_lines_grading_and_limits():
    fixed = [0, 0.1, 0.2, 5, 5.05, 20, 20.3]
    x = smooth_lines(fixed, 1.0, ratio=1.4)
    d = np.diff(x)
    assert set(np.round(fixed, 9)) <= set(np.round(x, 9))          # fixed lines kept
    assert d.max() <= 1.0 + 1e-9
    assert d.min() >= 0.05 - 1e-12                                  # nothing smaller than the features
    r = d[1:] / d[:-1]
    assert np.max(np.maximum(r, 1 / r)) <= 1.4 * 1.25              # graded (middle cells split evenly)


def test_model_ports_and_mesh(demo):
    m = OEModel(KiCadBoard(demo + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=demo + '-top.pos', ground='B.Cu')
    p1, p2 = m.ports
    assert (p1.layer, p1.ref_layer, p2.layer, p2.ref_layer) == ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu')
    assert p1.w == pytest.approx(0.35, rel=1e-3) and p2.w == pytest.approx(0.15, rel=1e-3)
    assert len(m.vias) == 23
    mesh = auto_mesh(m, 1e9, 8e9, log=lambda *a: None)
    zset = set(mesh['z'].tolist())
    assert all(z in zset for z in m.cu_z.values())                  # every sheet exactly on a z line
    for k in ('x', 'y'):
        d = np.diff(mesh[k])
        assert d.min() > 0.3 * mesh['info']['fine_mm']                 # no slivers next to edge lines
        assert d.max() <= mesh['info']['maxres_air_mm'] + 1e-9
    for p in m.ports:                                               # port plane is a mesh line
        assert np.min(np.abs(mesh['xy'[p.axis]] - p.c[p.axis])) < 1e-9
    # trace edges: thirds rule -> a line 1/3 fine inside each edge of the 0.35 mm trace
    yc, w, f = p1.c[1], p1.w, mesh['info']['fine_mm']
    for e, s in ((yc - w / 2, 1), (yc + w / 2, -1)):
        assert np.min(np.abs(mesh['y'] - (e + s * f / 3))) < 1e-6


def test_mixed_mode_is_unitary_for_lossless():
    rng = np.random.default_rng(3)
    H = rng.standard_normal((4, 4)) + 1j * rng.standard_normal((4, 4))
    H = H + H.T                                                     # symmetric -> reciprocal
    from scipy.linalg import expm
    S = expm(1j * (H + H.conj().T) / 2)[None]                       # unitary
    class P:  # minimal stand-in for OEModel ports
        def __init__(s, n): s.name, s.z0 = n, 50.0
    ps = [P('P1+'), P('P1-'), P('P2+'), P('P2-')]
    m = type('M', (), {'ports': ps, 'user_ports': [('P1', ps[:2]), ('P2', ps[2:])]})()
    Smm, names, zref = mixed_mode(S, m)
    assert names == ['P1d', 'P1c', 'P2d', 'P2c'] and zref == [100, 25, 100, 25]
    assert np.allclose(Smm[0] @ Smm[0].conj().T, np.eye(4), atol=1e-12)


def test_touchstone_roundtrip(tmp_path):
    f = np.linspace(1e9, 5e9, 7)
    S = np.random.default_rng(0).standard_normal((7, 2, 2)) * (1 + 0.5j)
    p = str(tmp_path / 'x.s2p')
    write_touchstone(p, f, S, [50, 50])
    f2, S2 = read_touchstone(p)
    assert np.allclose(f2, f) and np.allclose(S2, S, atol=1e-8)
    p = str(tmp_path / 'y.s4p')
    S4 = np.random.default_rng(1).standard_normal((7, 4, 4)) + 0j
    write_touchstone(p, f, S4, [100, 25, 100, 25])
    assert np.allclose(read_touchstone(p)[1], S4, atol=1e-8)


def test_boundaries_and_air_margin(demo):
    from kicad2openems.boundaries import resolve_bc, cavity_check
    m = OEModel(KiCadBoard(demo + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=demo + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    assert bc == ['MUR'] * 4 + ['PEC', 'MUR']                       # solid ground -> PEC bottom
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, log=lambda *a: None)
    ob = m.outline.bounds
    assert mesh['z'][0] == 0.0                                      # no air below the PEC plane
    assert mesh['z'][-1] == pytest.approx(m.thickness + 5.0)        # 5 mm air above
    assert mesh['x'][0] == pytest.approx(ob[0] - 5.0) and mesh['y'][-1] == pytest.approx(ob[3] + 5.0)
    pml = auto_mesh(m, 1e9, 8e9, bc=['PML_8'] * 6, log=lambda *a: None)
    assert pml['x'][0] < mesh['x'][0] - 8 * 0.9 * pml['info']['maxres_air_mm'] / 2   # PML cells added outside
    assert resolve_bc('PEC', m) == ['PEC'] * 6
    msgs = []
    f = cavity_check(m, mesh, ['PEC'] * 6, 20e9, log=msgs.append)
    assert f is not None and f < 20e9 and msgs and 'cavity' in msgs[0]
    m2 = OEModel(KiCadBoard(demo + '.kicad_pcb'), nets=['RF'], pos_file=demo + '-top.pos', ground='B.Cu')
    m2.copper.pop('B.Cu')
    assert resolve_bc('auto', m2)[4] == 'MUR'                       # no solid bottom plane -> absorbing
