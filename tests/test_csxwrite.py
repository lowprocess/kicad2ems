"""csxwrite: the CSX geometry file written without CSXCAD must be the file CSXCAD writes.

tests/data/csx_api_golden.xml was written by CSXCAD 0.7.0 / openEMS (2026) from the calls in api_calls()
in a fresh process (CSXCAD's colours come from rand(), so only the first structure of a process
compares); regenerate it with  python tests/test_csxwrite.py <out.xml>  in an openEMS environment.
"""
import os, subprocess, sys
import xml.etree.ElementTree as ET
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, 'tests', 'data', 'csx_api_golden.xml')


def api_calls(ContinuousStructure, openEMS, CSPropDebyeMaterial):
    """Every CSXCAD / openEMS call build_fdtd makes for a geometry file, with awkward values."""
    FDTD = openEMS(NrTS=1000, EndCriteria=1e-4)
    FDTD.SetGaussExcite(2e9, 1e9)
    FDTD.SetBoundaryCond(['MUR'] * 6)
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    g = CSX.GetGrid()
    g.SetDeltaUnit(1e-3)
    g.SetLines('x', np.array([-1.0, 0.0, 0.25, 1.5]))
    g.SetLines('y', np.array([0.0, 2.0]))
    g.SetLines('z', np.array([0.0, 0.2, 1.0]))
    m = CSX.AddMaterial('dielectric_1', epsilon=4.4, kappa=0.0121)
    m.AddLinPoly([np.array([0.0, 2.0, 2.0, 0.0]), np.array([0.0, 0.0, 1.0, 1.0])], 'z', 0.0, 0.2, priority=1)
    d = CSPropDebyeMaterial(CSX.GetParameterSet(), order=2, name='core_w', epsilon=4.1, kappa=0.001)
    CSX.AddProperty(d)
    d.SetDispersiveMaterialProperty(order=0, eps_delta=0.2, eps_relax=1e-10)
    d.SetDispersiveMaterialProperty(order=1, eps_delta=0.1, eps_relax=1e-11)
    d.AddLinPoly([np.array([0.0, 2.0, 2.0, 0.0]), np.array([0.0, 0.0, 1.0, 1.0])], 'z', 0.2, 0.3, priority=1)
    c = CSX.AddMetal('cu_F.Cu')
    c.AddPolygon([np.array([0.0, 1.0, 1.0]), np.array([0.0, 0.0, 0.5])], 'z', 0.2, priority=10)
    s = CSX.AddConductingSheet('cu_B.Cu', conductivity=5.8e7, thickness=3.5e-5)
    s.AddPolygon([np.array([0.0, 1.0, 1.0]), np.array([0.0, 0.0, 0.5])], 'z', 0.0, priority=10)
    CSX.AddMetal('vias').AddCylinder([0.5, 0.25, 0.0], [0.5, 0.25, 0.2], 0.1, priority=11)
    CSX.AddMetal('edge_walls').AddBox([0.0, 0.0, 0.0], [0.0, 1.0, 0.2], priority=11)
    CSX.AddLumpedElement('part_L1', ny=0, caps=True, L=8.2e-9, R=0.4, LEtype=1).AddBox(
        [0.1, 0.2, 0.2], [0.3, 0.4, 0.2], priority=12)
    CSX.AddLumpedElement('part_C2', ny=1, caps=True, C=6.8e-12).AddBox([0.1, 0.2, 0.2], [0.3, 0.4, 0.2], priority=12)
    CSX.AddMetal('part_R9').AddBox([0.1, 0.2, 0.2], [0.3, 0.4, 0.2], priority=12)
    FDTD.AddLumpedPort(1, 50.0, [0.5, 0.0, 0.0], [0.5, 0.35, 0.2], 'z', excite=1.0, priority=12)      # driven, up
    FDTD.AddLumpedPort(2, 73.0, [0.2, 0.1, 0.2], [0.6, 0.4, 0.2], 'x', excite=0.0, priority=12)       # gap port
    FDTD.AddLumpedPort(3, 50.0, [0.5, 1.0, 0.2], [0.5, 0.65, 0.0], 'z', excite=0.0, priority=12)      # downwards
    return FDTD, CSX                                                # FDTD owns CSX (CSXCAD): keep both


def test_writer_matches_csxcad_byte_for_byte(tmp_path):
    from kicad2openems import csxwrite
    out = tmp_path / 'mine.xml'
    FDTD, CSX = api_calls(csxwrite.ContinuousStructure, csxwrite.openEMS, csxwrite.CSPropDebyeMaterial)
    CSX.Write2XML(str(out))
    assert out.read_bytes() == open(GOLDEN, 'rb').read()


def test_rand_is_glibc():
    from kicad2openems.csxwrite import _Rand
    r = _Rand()
    assert [r() for _ in range(5)] == [1804289383, 846930886, 1681692777, 1714636915, 1957747793]


def test_mom25d_reads_the_written_file(tmp_path):
    """What the file is for: mom25d's reader takes the stack, metal, vias, parts and ports from it."""
    from kicad2openems import csxwrite
    from mom25d.csx import CSXModel
    p = tmp_path / 'g.xml'
    CSX = csxwrite.ContinuousStructure()
    FDTD = csxwrite.openEMS()
    FDTD.SetCSX(CSX)
    CSX.GetGrid().SetDeltaUnit(1e-3)
    CSX.AddMaterial('core', epsilon=4.4, kappa=0.0).AddLinPoly([[0, 20, 20, 0], [0, 0, 10, 10]], 'z', 0.0, 0.2, priority=1)
    CSX.AddMetal('cu_B.Cu').AddPolygon([[0, 20, 20, 0], [0, 0, 10, 10]], 'z', 0.0, priority=10)
    CSX.AddMetal('cu_F.Cu').AddPolygon([[1, 19, 19, 1], [4.8, 4.8, 5.2, 5.2]], 'z', 0.2, priority=10)
    for k, x in enumerate((1.0, 19.0)):
        FDTD.AddLumpedPort(k + 1, 50.0, [x, 4.8, 0.0], [x, 5.2, 0.2], 'z', excite=1.0 if k == 0 else 0.0, priority=12)
    CSX.Write2XML(str(p))
    m = CSXModel(str(p), log=lambda *a: None)
    assert m.ground == 'B.Cu' and sorted(m.port_z0) == ['P1', 'P2']
    assert m.stack.layers[0].er == pytest.approx(4.4) and m.stack.layers[0].h == pytest.approx(0.2e-3)


@pytest.fixture(scope='module')
def csxcad():
    pytest.importorskip('CSXCAD')
    pytest.importorskip('openEMS')


_SCRIPT = r"""
import sys
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.boundaries import resolve_bc
from kicad2openems.mesh import auto_mesh
from kicad2openems.simulation import build_fdtd
base, backend, out, kind = sys.argv[1:5]
kw = dict(demo=dict(nets=['RF', 'GND'], ground='B.Cu', pos_file=base + '-top.pos'), diff=dict(ground='B.Cu'))
kw = kw.get(kind, {})
m = Model(KiCadBoard(base + '.kicad_pcb'), log=lambda *a: None, **kw)
bc = resolve_bc('auto', m)
mesh = auto_mesh(m, 1e9, 6e9, bc=bc, log=lambda *a: None)
extra = dict(diel='wideband', sigma=5.8e7) if kind == 'demo' else {}
FDTD, CSX, _ = build_fdtd(m, mesh, 1e9, 6e9, 0, bc=bc, backend=backend, **extra)
CSX.Write2XML(out)
"""


@pytest.mark.parametrize('board', ['demo', 'lowpass', 'diff', 'dipole'])
def test_build_fdtd_file_matches_csxcad(board, boards, tmp_path, csxcad):
    """The geometry of real boards (parts, Debye materials, conducting sheets, gap and differential ports),
    each backend in a fresh process (CSXCAD's colours depend on what the process created before)."""
    files = {}
    for backend in ('csxcad', 'xml'):
        files[backend] = tmp_path / f"{backend}.xml"
        subprocess.check_call([sys.executable, '-c', _SCRIPT, boards[board], backend, str(files[backend]),
                               board if board in ('demo', 'diff') else 'plain'], cwd=ROOT)
    assert files['xml'].read_bytes() == files['csxcad'].read_bytes()
    assert ET.parse(files['xml']).getroot().tag == 'ContinuousStructure'


if __name__ == '__main__':                                          # regenerate the golden file (needs openEMS)
    from CSXCAD import ContinuousStructure
    from CSXCAD.CSProperties import CSPropDebyeMaterial
    from openEMS import openEMS
    FDTD, CSX = api_calls(ContinuousStructure, openEMS, CSPropDebyeMaterial)
    CSX.Write2XML(sys.argv[1])
