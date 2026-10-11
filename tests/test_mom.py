"""mom25d as a reference solver: the package, the <mom25d> hints of a CSX file, --mom end to end
(needs CSXCAD / openEMS to write the CSX file and mom25d's mesher, triangle)."""
import json, os
import numpy as np
import xml.etree.ElementTree as ET
import pytest
from kicad2openems.mom import add_hints


def test_import():
    import mom25d
    assert hasattr(mom25d, '__version__')
    from mom25d.csx import CSXModel                                 # noqa: F401


@pytest.fixture(scope='module')
def model(demo_files):
    from kicad2openems.kicad import KiCadBoard
    from kicad2openems.model import Model
    base, _ = demo_files
    return Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
                 log=lambda *a: None)


def test_hints(tmp_path, model):
    p = tmp_path / 'g.xml'
    ET.ElementTree(ET.Element('ContinuousStructure')).write(p)
    add_hints(str(p), model, 1e9, 9e9)
    add_hints(str(p), model, 1e9, 9e9)                             # replaced, not duplicated
    root = ET.parse(p).getroot()
    h = root.findall('mom25d')
    assert len(h) == 1 and h[0].get('ground') == 'B.Cu' and float(h[0].get('f_loss')) == 5e9
    layers = {e.get('name'): float(e.get('z')) for e in h[0].findall('Layer')}
    assert layers == pytest.approx(model.cu_z)
    ports = h[0].findall('Port')
    assert [e.get('name') for e in ports] == [q.name for q in model.ports]
    for e, q in zip(ports, model.ports):
        assert e.get('layer') == q.layer and e.get('ref_layer') == q.ref_layer
        assert float(e.get('width')) == pytest.approx(q.w)
        assert abs(float(e.get('dx'))) + abs(float(e.get('dy'))) == 1
    diel = h[0].findall('Dielectric')
    assert [float(e.get('tand')) for e in diel] == [l['tand'] for l in model.layers]


def test_preview_with_mom(tmp_path, run_cli):
    pytest.importorskip('CSXCAD')
    pytest.importorskip('openEMS')
    pytest.importorskip('triangle')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = tmp_path / 'out'
    code, so, se = run_cli(os.path.join(root, 'examples', 'filter.toml'), '--preview', '--mom', '--mom-dmax', '0.8',
                           '--mom-solves', '8', '--points', '41', '--out', str(out))
    assert code == 0, so + se
    assert (out / 'filter.xml').exists() and (out / 'filter_mom.s2p').exists() and (out / 'filter_mom.png').exists()
    assert ET.parse(out / 'filter.xml').getroot().find('mom25d') is not None
    sm = json.load(open(out / 'filter_openems.json'))
    mo = sm['mom']
    assert mo['solves'] <= 8 and mo['unknowns'] > 100 and {p['kind'] for p in mo['ports']} == {'lumped'}
    assert mo['compare_fast'] and mo['table'][0]['param'] == 'S(P1,P1)'
    assert 'mom25d' in so


FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'filter.toml')


def test_mom_without_openems(run_cli, tmp_path, no_csxcad):
    """MoM needs no openEMS: the geometry file comes from csxwrite, and the run is the same."""
    pytest.importorskip('triangle')
    pytest.importorskip('skrf')
    from kicad2openems import simulation
    assert not simulation.have_csxcad()
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--preview', '--mom', '--fast', 'skrf', '--mom-dmax', '0.8', '--mom-solves', '8',
                            '--points', '41', '--out', out)
    assert code == 0, so + err
    assert 'written by kicad2openems' in so
    import xml.etree.ElementTree as ET
    root = ET.parse(out / 'filter.xml').getroot()
    assert root.tag == 'ContinuousStructure' and root.find('mom25d') is not None
    sm = json.loads((out / 'filter_openems.json').read_text())
    assert sm['mom']['solves'] <= 8 and sm['mom']['compare_fast']
    for f in ('filter_mom.s2p', 'filter_mom.png', 'filter_fast.s2p', 'filter_report.html'):
        assert (out / f).exists(), f




def test_sigma_reaches_mom(demo_files):
    """--sigma is conductor loss in openEMS's conducting sheets; MoM gets the same (it is not in the CSX file
    for mom25d), with the copper thickness of the ports' layer."""
    from kicad2openems.mom import _metal
    from kicad2openems.kicad import KiCadBoard
    from kicad2openems.model import Model
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    assert _metal(m, None) == (None, None)
    metal, t = _metal(m, 5.8e7)
    assert metal.sigma == 5.8e7 and t == pytest.approx(m.cu_t[m.ports[0].layer]) and metal.t == pytest.approx(t * 1e-3)
    zs = metal.zs(np.array([1e9]))
    assert zs.real[0] > 0                                           # a lossy surface impedance
