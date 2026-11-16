"""Reference parity and independence from both application and RF packages."""
import ast,json,subprocess,sys
from pathlib import Path
import numpy as np
from shapely.geometry import box
from kicad2ems.pcbqs.mqs import MQS
from kicad2ems.pcbqs.mqs_ports import CopperPorts
from kicad2ems.pcbqs._artifacts import Dump,write_npz,write_info
from kicad2ems.pcbqs.mqs_cli import parser,run
from test_mqs import trace_board
from test_mqs_cli import board


# the pre-split pipeline's impedances on this board (Triangle's mesh,
# 2026-11-07): today's mesh within 1 %
TRIANGLE_ERA_Z = np.array(
    [0.0269162, 0.0281909 + 0.0084952j, 0.0288185 + 0.015794j])


def test_copper_snapshot_parity():
    # Parity with the frozen run on its unsimplified CDT mesh
    # (fixtures/copper-reference.npz, 2026-11-15), and the physics with the
    # pre-split reference pipeline's (Triangle's mesh)
    q = MQS(
        trace_board(), 'N', terminals={
            's': [('F.Cu', box(2, 9.6, 2.6, 10.4))],
            'g': [('F.Cu', box(2, 7.7, 2.6, 8.5))]
        }, dmax=2, log=lambda *_: None, ideal_terminals=False,
        drop_lone_pads=False, pad_sides=None, via_sides=None)
    q.source('p','s','g',part_h=0);r=CopperPorts(q).extract([0,1e5,2e5])
    with np.load(Path(__file__).parent
                 / 'fixtures/copper-reference.npz') as expected:
        np.testing.assert_array_equal(q.nodes, expected['nodes'])
        np.testing.assert_array_equal(q.tris, expected['tris'])
        np.testing.assert_allclose(r.impedance, expected['impedance'],
                                   rtol=1e-11, atol=1e-14)
        # (atol 1e-11 of currents up to 0.07: the free loops are eliminated by
        # LU, the reference's by a symmetric indefinite factorization, and at DC
        # one branch's current differs by 1.7e-12)
        np.testing.assert_allclose(r.currents, expected['currents'], rtol=1e-10,
                                   atol=1e-11)
        np.testing.assert_allclose(
            q.h_field(r.drive(np.ones((3, 1))), 1,
                      np.array([[.015, .010, .002]])), expected['H'],
            rtol=1e-10)
    # (0.8 % at 200 kHz, dmax 2 mm)
    np.testing.assert_allclose(np.ravel(r.impedance),TRIANGLE_ERA_Z,rtol=1e-2)


def test_json_fields_without_optional_packages(tmp_path):
    b=tmp_path/'board.json';b.write_text(json.dumps(board()));out=tmp_path/'out'
    code = '''import sys,importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.startswith(('kicad2ems.mom25d','kicad2ems.core',
                                'kicad2ems.gui','kicad2ems.fdtd',
                                'kicad2ems.fem')):
            raise AssertionError('forbidden runtime dependency: '+fullname)
sys.meta_path.insert(0,Block())
from kicad2ems.pcbqs.mqs_cli import main
main()
'''
    subprocess.run([
        sys.executable, '-c', code,
        str(b), '--out',
        str(out), '--harmonics', '1,2', '--heights', '1,2', '--scan-points',
        '5', '--dmax', '1', '--fine', '1', '--fill-backend', 'numpy'
    ], check=True, capture_output=True, text=True)
    info=json.loads(next(out.glob('mqs/exc_*/dump_info.json')).read_text())
    assert len(info['dumps'])==3 and info['components']
    for file in out.glob('mqs/exc_*/*.npz'):
        with np.load(file) as data:
            assert {'x','y','z','data','freqs'}<=set(data.files)
            assert data['data'].shape[0]==2 and np.isfinite(data['data']).all()


def test_no_rf_or_application_dependency_in_core():
    # the rest of kicad2ems (its core, mom25d, the window) is imported in two
    # places only: _cli.py (reading a KiCad board) and analyses.py (the analyses
    # that kicad2ems itself loads). Nothing in pcbqs imports analyses.py, so the
    # tools run without them; pcbqs's own modules and emsdev (the machine) are
    # kicad2ems.pcbqs, kicad2ems.emsdev
    root=Path(__file__).resolve().parents[2]/'src/kicad2ems/pcbqs'
    other = lambda m: m.startswith('kicad2ems') and not m.startswith(
        ('kicad2ems.pcbqs', 'kicad2ems.emsdev'))
    for file in root.rglob('*.py'):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node,ast.ImportFrom):
                module=node.module or ''
                assert not module.startswith('kicad2ems.mom25d'),file
                if (other(module) and node.level == 0):
                    assert (file.name in ('_cli.py', 'analyses.py')), file
                names=[module.split('.')[-1]]+[n.name for n in node.names]
                # the analyses: kicad2ems's to load
                assert 'analyses' not in names or other(module),file
            elif isinstance(node,ast.Import):
                assert (not any(
                    n.name.startswith('kicad2ems.mom25d')
                    for n in node.names)), file
                assert (not any(
                    n.name.split('.')[-1] == 'analyses'
                    for n in node.names)), file


def test_automatic_fallback_without_native_core(tmp_path):
    file=tmp_path/'board.json';file.write_text(json.dumps(trace_board()))
    code = '''import sys,importlib.abc,json,numpy as np
from shapely.geometry import box
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.startswith(('kicad2ems.mom25d','kicad2ems.core',
                                'kicad2ems.gui','kicad2ems.fdtd',
                                'kicad2ems.fem')):
            raise ImportError('unavailable: '+fullname)
sys.meta_path.insert(0,Block())
from kicad2ems.pcbqs.mqs import MQS
from kicad2ems.pcbqs.mqs_ports import CopperPorts
q=MQS(json.load(open(sys.argv[1])),'N',
      terminals={'s':[('F.Cu',box(2,9.6,2.6,10.4))],
                 'g':[('F.Cu',box(2,7.7,2.6,8.5))]},
      dmax=2,fill_workers=2,log=lambda *_:None,drop_lone_pads=False,
      pad_sides=None,via_sides=None,ideal_terminals=False)
q.source('p','s','g',part_h=0)
r=CopperPorts(q).extract([0,1e5,2e5])
with np.load(sys.argv[2]) as ref:
    np.testing.assert_allclose(r.impedance,ref['impedance'],rtol=1e-8,
                               atol=1e-13)
assert q.fill_info['magnetic']['backend']=='threaded'
'''
    # PCBQS_NO_CORE: the kernels on NumPy's threads. The mesher has no fallback
    # (the core triangulates: CDT)
    import os
    subprocess.run([
        sys.executable, '-c', code,
        str(file),
        str(Path(__file__).parent / 'fixtures/copper-reference.npz')
    ], check=True, capture_output=True, text=True,
                   env=dict(os.environ, PCBQS_NO_CORE='1'))
