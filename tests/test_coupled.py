"""Coupled lines (coupled.py), copper shapes as strips (strips.py) and the two example filters through
the line model: groups, discontinuities, qucsator elements and the pass band against openEMS."""
import os
import numpy as np
import pytest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from kicad2ems.coupled import coupled_microstrip, group_lc, mtl_s
from kicad2ems.strips import shape_strips, rectilinear
from kicad2ems.tlines import microstrip, build
from kicad2ems.kicad import KiCadBoard
from kicad2ems.model import Model
from kicad2ems.fastsolve import solve, qucs_netlist, find_qucsator, read_qucs_dataset

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, 'examples')
db = lambda x: 20 * np.log10(np.abs(x) + 1e-15)


def _qucsator():
    try:
        return find_qucsator()
    except ValueError:
        return None


def test_coupled_pair_bounds_the_single_line():
    ze, zo, ee, eo = coupled_microstrip(0.35, 0.3, 0.2, 4.4)
    z, e = microstrip(0.35, 0.2, 4.4)
    assert zo < z < ze and eo < e < ee
    ze2, zo2, _, _ = coupled_microstrip(0.35, 3.0, 0.2, 4.4)               # far apart: both -> the single line
    assert abs(ze2 - z) < 0.02 * z and abs(zo2 - z) < 0.02 * z


def test_mtl_single_line_and_block_properties():
    f = np.linspace(0.5e9, 6e9, 12)
    C, Ca = group_lc([0.35], [], 0.2, 4.4)
    S = mtl_s(C, Ca, 4.4, 0.0, 10.0, f)
    z, ee = microstrip(0.35, 0.2, 4.4)
    beta = 2 * np.pi * f * np.sqrt(ee) / 299792458.0 * 10e-3
    assert np.allclose(np.unwrap(np.angle(S[:, 1, 0])), -beta, atol=0.02)   # phase of the single line
    G = (z - 50) / (z + 50)
    assert np.max(np.abs(S[:, 0, 0])) == pytest.approx(2 * abs(G) / (1 + G * G), rel=0.02)
    C, Ca = group_lc([0.35] * 5, [0.58, 0.74, 0.74, 0.58], 0.2, 4.4)
    S = mtl_s(C, Ca, 4.4, 0.02, 12.0, f)
    assert np.allclose(S, np.transpose(S, (0, 2, 1)), atol=1e-9)            # reciprocal
    sv = np.linalg.svd(S, compute_uv=False)
    assert sv.max() <= 1 + 1e-9                                            # passive


@pytest.mark.skipif(_qucsator() is None, reason='qucsator not installed')
def test_pair_block_matches_mcoupled(tmp_path):
    f = np.linspace(0.2e9, 3e9, 15)
    net = tmp_path / 't.net'
    net.write_text('SUBST:S1 er="4.4" h="0.0002" t="1e-9" tand="0" rho="1e-15" D="0"\n'
                   'MCOUPLED:C1 n1 n2 n3 n4 Subst="S1" W="0.00035" S="0.00058" L="0.0123" Model="Kirschning" '
                   'DispModel="Kirschning"\n' +
                   "".join(f'Pac:P{k} n{k} gnd Num="{k}" Z="50 Ohm" P="0 dBm" f="1 GHz"\n' for k in (1, 2, 3, 4)) +
                   f'.SP:SP1 Type="lin" Start="{f[0]:g}" Stop="{f[-1]:g}" Points="{len(f)}" Noise="no"\n')
    import subprocess
    subprocess.run([_qucsator(), '-i', str(net), '-o', str(tmp_path / 't.dat')], capture_output=True, check=True)
    _, Sq = read_qucs_dataset((tmp_path / 't.dat').read_text(), 4)
    C, Ca = group_lc([0.35, 0.35], [0.58], 0.2, 4.4)
    S = mtl_s(C, Ca, 4.4, 0.0, 12.3, f)
    m = [0, 3, 1, 2]                                                       # MCOUPLED: 1-2 line A, 4-3 line B
    assert np.max(np.abs(S - Sq[:, m][:, :, m])) < 0.005


def test_strips_of_a_hairpin_and_a_tee():
    u = unary_union([box(0, 0, 0.6, 10), box(2.4, 0, 3.0, 10), box(0, 0, 3.0, 0.6)])     # U, arms up
    assert rectilinear(u) and not rectilinear(Polygon([(0, 0), (1, 0), (0.5, 1)]))
    st = shape_strips(u, 'F.Cu', 'X')
    assert len(st) == 3
    pts = sorted(tuple(map(tuple, np.round(s['pts'], 6))) for s in st)
    assert ((0.3, 0.3), (0.3, 10.0)) in pts and ((2.7, 0.3), (2.7, 10.0)) in pts      # arms from the corner centres
    assert ((0.3, 0.3), (2.7, 0.3)) in pts                                            # the bar between them
    t = unary_union([box(0, 4, 5, 4.4), box(5, 0, 5.6, 10)])                          # a feed into a side
    st = shape_strips(t, 'F.Cu', 'X')
    feed = next(s for s in st if s['width'] == pytest.approx(0.4))
    assert tuple(np.round(feed['pts'][-1], 6)) == (5.3, 4.2)                           # runs on to the centreline


@pytest.fixture(scope='module')
def filters():
    out = {}
    m = Model(KiCadBoard(os.path.join(EX, 'filter.kicad_pcb')), nets=['RF', 'GND'],
              pos_file=os.path.join(EX, 'filter-all.pos'), log=lambda *a: None)
    out['interdigital'] = (m, build(m))
    m = Model(KiCadBoard(os.path.join(EX, 'hairpin.kicad_pcb')), pos_file=os.path.join(EX, 'hairpin-top.pos'),
              log=lambda *a: None)
    out['hairpin'] = (m, build(m))
    return out


def test_filter_line_models(filters):
    from collections import Counter
    m, lm = filters['interdigital']
    assert [len(g['members']) for g in lm.groups] == [5, 5]                # five fingers side by side
    assert [round(x, 3) for x in lm.groups[0]['gaps']] == [0.584, 0.74, 0.74, 0.584]
    assert sum(1 for e in lm.elements if e.kind == 'via' and e.b == 'gnd') == 5   # fingers grounded by vias
    assert Counter(j['kind'] for j in lm.junctions)['tee'] == 2            # the tapped feeds
    assert lm.paths and lm.paths[0][1] == 'P2'                              # P1 reaches P2 through the coupling
    m, lm = filters['hairpin']
    assert [len(g['members']) for g in lm.groups] == [2, 2]
    k = Counter(j['kind'] for j in lm.junctions)
    assert k['corner'] == 6 and k['tee'] == 2 and k['open'] >= 6
    text, _ = qucs_netlist(lm, m.ports, np.linspace(3e9, 3.6e9, 5))
    for el in ('MCOUPLED:', 'MCORN:', 'MTEE:', 'MOPEN:', 'MLIN:'):
        assert el in text, el


def _peak(f, S):
    return f[np.argmax(np.abs(S[:, 1, 0]))]


def _reference(name):
    from kicad2ems.touchstone import read_touchstone
    return read_touchstone(os.path.join(EX, 'reference', name))


@pytest.mark.parametrize('backend', ['skrf', 'qucsator'])
def test_filters_against_openems(filters, backend):
    """The pass band of both example filters within a few per cent of openEMS (ballpark)."""
    if backend == 'skrf':
        pytest.importorskip('skrf')
    elif _qucsator() is None:
        pytest.skip('qucsator not installed')
    for name, ref, tol in (('hairpin', 'hairpin_openems.s2p', 0.04), ('interdigital', 'filter_openems.s2p', 0.02)):
        m, lm = filters[name]
        fr, Sr = _reference(ref)
        band = (fr > 2.8e9) & (fr < 3.9e9)
        f = fr[band]
        S, _ = solve(lm, m.ports, f, backend)
        assert np.isfinite(S[:, 1, 0]).all()
        assert abs(_peak(f, S) / _peak(f, Sr[band]) - 1) < tol, name
        assert db(S[:, 1, 0]).max() > -10                                  # a pass band, not a stop band
