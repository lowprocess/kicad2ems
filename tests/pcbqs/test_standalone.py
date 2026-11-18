"""Reference parity, and pcbqs apart from the RF solvers and the application:
it imports only the shared leaf modules of core (SHARED)."""
import ast,json,subprocess,sys
from pathlib import Path
import numpy as np
from shapely.geometry import box
from kicad2ems.pcbqs.mqs import MQS
from kicad2ems.pcbqs.mqs_ports import CopperPorts
from kicad2ems.pcbqs.mqs_cli import parser,run
from test_mqs import trace_board
from test_mqs_cli import board

# what pcbqs may import from kicad2ems.core: leaf helpers that import nothing
# from kicad2ems (test_shared_modules_are_leaves), and their packages
SHARED = ('kicad2ems.core.units', 'kicad2ems.core.constants',
          'kicad2ems.core.touchstone', 'kicad2ems.core.fields.dumps')
PACKAGES = ('kicad2ems', 'kicad2ems.core', 'kicad2ems.core.fields')
SRC = Path(__file__).resolve().parents[2] / 'src'
# a meta path finder that fails on the rest of kicad2ems (RAISE: the error)
BLOCK = f'''import sys,importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if (fullname.startswith(('kicad2ems.mom25d','kicad2ems.core',
                                 'kicad2ems.gui','kicad2ems.fdtd',
                                 'kicad2ems.fem'))
                and fullname not in {SHARED + PACKAGES!r}):
            raise RAISE('forbidden runtime dependency: '+fullname)
sys.meta_path.insert(0,Block())
'''


# the pre-split pipeline's impedances on this board (Triangle's mesh,
# 2026-11-07): today's mesh within 1 %
TRIANGLE_ERA_Z = np.array(
    [0.0269162, 0.0281909 + 0.0084952j, 0.0288185 + 0.015794j])


def test_copper_snapshot_parity():
    # Parity with the frozen run on its unsimplified mesh2d mesh
    # (fixtures/copper-reference.npz, 2026-11-17; CDT's within 1.2e-5), and the
    # physics with the pre-split reference pipeline's (Triangle's mesh)
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
    code = BLOCK.replace('RAISE', 'AssertionError') + '''
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


def _imports(file):
    """(module, names) of each import in a file, relative ones made
    absolute."""
    package = '.'.join(file.relative_to(SRC).with_suffix('').parts[:-1])
    for node in ast.walk(ast.parse(file.read_text())):
        if isinstance(node, ast.ImportFrom):
            base = package.rsplit('.', node.level - 1)[0] if node.level else ''
            module = '.'.join(x for x in (base, node.module) if x)
            yield module, [n.name for n in node.names]
        elif isinstance(node, ast.Import):
            for n in node.names:
                yield n.name, []


def test_no_rf_or_application_dependency_in_core():
    # pcbqs imports its own modules, emsdev (the machine) and SHARED; the rest
    # of kicad2ems in two places only: _cli.py (reading a KiCad board) and
    # analyses.py (the analyses kicad2ems loads). Nothing in pcbqs imports
    # analyses.py, so the tools run without them
    own = ('kicad2ems.pcbqs', 'kicad2ems.emsdev')
    for file in (SRC / 'kicad2ems/pcbqs').rglob('*.py'):
        for module, names in _imports(file):
            assert not module.startswith('kicad2ems.mom25d'), file
            if not module.startswith('kicad2ems') or module.startswith(own):
                assert 'analyses' not in [module.split('.')[-1]] + names, file
                continue
            used = ([f'{module}.{n}' for n in names] if module in PACKAGES
                    else [module])
            assert (all(m in SHARED for m in used)
                    or file.name in ('_cli.py', 'analyses.py')), (file, module)


def test_shared_modules_are_leaves():
    # SHARED import nothing from kicad2ems: pcbqs gets no more of core
    for name in SHARED:
        file = SRC / (name.replace('.', '/') + '.py')
        assert not [m for m, _ in _imports(file)
                    if m.startswith('kicad2ems')], name


def test_automatic_fallback_without_native_core(tmp_path):
    file=tmp_path/'board.json';file.write_text(json.dumps(trace_board()))
    code = BLOCK.replace('RAISE', 'ImportError') + '''
import json,numpy as np
from shapely.geometry import box
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
    # (the core triangulates: mesh2d)
    import os
    subprocess.run([
        sys.executable, '-c', code,
        str(file),
        str(Path(__file__).parent / 'fixtures/copper-reference.npz')
    ], check=True, capture_output=True, text=True,
                   env=dict(os.environ, PCBQS_NO_CORE='1'))
