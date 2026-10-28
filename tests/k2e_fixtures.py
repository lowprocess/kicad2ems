"""kicad2ems's test fixtures, shared with the solver packages' tests (kicad2ems-fdtd, mom25d: their
conftest imports this file from a kicad2ems checkout, KICAD2EMS_SRC): the generated example boards, a strict
stand-in for CSXCAD / openEMS, a machine without openEMS, and an in-process command-line runner.

    from k2e_fixtures import *              # noqa: F401,F403  (in a conftest.py)

A fixture's name must not start with '_': the star import leaves those out, and an autouse one (scratch_settings,
which keeps the tests out of the user's settings) would silently stop applying.
"""
import base64, importlib.util, os, subprocess, sys, types, zlib
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)
# the kicad2ems runs the tests start (python -m kicad2ems) import this checkout too
os.environ['PYTHONPATH'] = os.pathsep.join(x for x in (SRC, os.environ.get('PYTHONPATH')) if x)
try:                                    # GUI tests: shared OpenGL contexts, as kicad2ems_gui.application makes them
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
except ImportError:
    pass
GEN = os.path.join(ROOT, 'examples', 'make_demo_board.py')
ANT = os.path.join(ROOT, 'examples', 'make_antennas.py')
RF = os.path.join(ROOT, 'examples', 'make_rf_examples.py')


@pytest.fixture(autouse=True)
def scratch_settings(tmp_path_factory, monkeypatch):
    """The GUI's QSettings (recent files, the window's shape, the Results plots) in a scratch folder: a test
    never writes the user's own. The site settings (sitecfg) from that scratch user file alone: not the
    environment's kicad2ems-site.toml, where setup.sh records this machine's qucsator and openEMS."""
    if 'XDG_CONFIG_HOME' not in os.environ or not os.environ['XDG_CONFIG_HOME'].startswith(str(tmp_path_factory.getbasetemp())):
        monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path_factory.getbasetemp() / 'xdg'))
    from kicad2ems import sitecfg
    monkeypatch.setattr(sitecfg, 'paths', lambda: [sitecfg.user_file()])
    monkeypatch.setattr(sitecfg, '_cache', None)


@pytest.fixture(scope='session', autouse=True)
def rf_examples():
    """examples/rf_<kind>.kicad_pcb, which the configs and several tests read where they are: made by
    make_rf_examples.py (not in the repository) when any is missing."""
    sys.path.insert(0, os.path.dirname(RF))
    try:
        from make_rf_examples import KINDS
    finally:
        sys.path.remove(os.path.dirname(RF))
    if not all(os.path.exists(os.path.join(ROOT, 'examples', f'rf_{k}.kicad_pcb')) for k in KINDS):
        subprocess.check_call([sys.executable, RF, 'all'], stdout=subprocess.DEVNULL)


@pytest.fixture(scope='session')
def boards(tmp_path_factory):
    """Every generated example board, by name: demo (transition), diff, rlc, lowpass, highpass, bandpass, launch
    and the four antennas. Values are base paths (add .kicad_pcb / -top.pos)."""
    d = tmp_path_factory.mktemp('boards')
    out = {}
    for name, variant in (('demo', None), ('diff', 'diff'), ('rlc', 'rlc'), ('lowpass', 'lowpass'), ('highpass', 'highpass'),
                          ('bandpass', 'bandpass'), ('launch', 'launch')):
        subprocess.check_call([sys.executable, GEN, str(d / name)] + ([variant] if variant else []),
                              stdout=subprocess.DEVNULL)
        out[name] = str(d / name)
    for kind in ('patch', 'ifa', 'dipole', 'slot', 'vivaldi', 'vivaldi_antipodal'):
        subprocess.check_call([sys.executable, ANT, kind, str(d / kind)], stdout=subprocess.DEVNULL)
        out[kind] = str(d / kind)
    return out


@pytest.fixture(scope='session')
def demo_files(boards):
    """(transition base path, differential base path)."""
    return boards['demo'], boards['diff']


@pytest.fixture
def run_cli(capsys):
    """kicad2ems in this process: run_cli(*args) -> (exit code, stdout, stderr)."""
    from kicad2ems.cli import main

    def run(*args):
        code = main([str(a) for a in args])
        out, err = capsys.readouterr()
        return code, out, err
    return run


# ---- strict CSXCAD / openEMS stand-ins -------------------------------------------------------------
def _scalar(v, where):
    assert type(v) in (int, float, bool, str), f"{where}: {type(v).__name__} {v!r} is not a plain Python scalar"


def _coords(v, where):
    if isinstance(v, np.ndarray):
        return
    for x in v:
        _scalar(x, where)


class Prim:
    def __init__(self, log, name): self.log, self.name = log, name
    def AddLinPoly(self, pts, nd, elev, length, priority=0):
        _scalar(elev, 'AddLinPoly elevation'); _scalar(length, 'AddLinPoly length'); self.log.append('linpoly')
    def AddPolygon(self, pts, nd, elev, priority=0):
        _scalar(elev, 'AddPolygon elevation'); self.log.append('polygon')
    def AddBox(self, a, b, priority=0):
        _coords(a, 'box start'); _coords(b, 'box stop'); self.log.append('box')
    def AddCylinder(self, a, b, r, priority=0):
        _coords(a, 'cyl start'); _coords(b, 'cyl stop'); _scalar(r, 'cyl radius'); self.log.append('cyl')


@pytest.fixture
def fake_openems(monkeypatch):
    """CSXCAD / openEMS modules that check every scalar handed over is a plain Python number (older
    CSXCAD releases test type(v) in (int, float) and reject numpy scalars). Returns the call log."""
    log = []

    class Grid:
        def SetDeltaUnit(self, u): _scalar(u, 'delta unit')
        def SetLines(self, d, v): assert np.asarray(v).dtype == float

    class CSX:
        def GetGrid(self): return Grid()
        def AddMaterial(self, name, **kw):
            for k, v in kw.items(): _scalar(v, f'AddMaterial {k}')
            return Prim(log, name)
        def AddConductingSheet(self, name, **kw):
            for k, v in kw.items(): _scalar(v, f'AddConductingSheet {k}')
            return Prim(log, name)
        def AddMetal(self, name): return Prim(log, name)
        def AddLumpedElement(self, name, ny, caps=True, **kw):
            _scalar(ny, 'lumped ny')
            for k, v in kw.items(): _scalar(v, f'AddLumpedElement {k}')
            log.append(('lumped', name, kw)); return Prim(log, name)

    class FDTD:
        def __init__(self, NrTS, EndCriteria): _scalar(NrTS, 'NrTS'); _scalar(EndCriteria, 'EndCriteria')
        def SetGaussExcite(self, f0, fc): _scalar(f0, 'f0'); _scalar(fc, 'fc')
        def SetBoundaryCond(self, bc): [_scalar(b, 'bc') for b in bc]
        def SetCSX(self, c): pass
        def AddLumpedPort(self, nr, R, start, stop, d, excite=0, priority=0):
            _scalar(R, 'port R'); _coords(start, 'port start'); _coords(stop, 'port stop'); _scalar(excite, 'excite')
            log.append('port'); log.append(('port_box', tuple(start), tuple(stop))); return object()

    class Debye(Prim):
        def __init__(self, pset, order, name, **kw):
            _scalar(order, 'debye order')
            for k, v in kw.items(): _scalar(v, f'Debye {k}')
            Prim.__init__(self, log, name); log.append(('debye', name, order))
        def SetDispersiveMaterialProperty(self, order, **kw):
            for k, v in kw.items(): _scalar(v, f'Debye pole {k}')
    CSX.GetParameterSet = lambda self: None
    CSX.AddProperty = lambda self, p: None
    props = types.SimpleNamespace(CSPropDebyeMaterial=Debye)
    monkeypatch.setitem(sys.modules, 'CSXCAD', types.SimpleNamespace(ContinuousStructure=CSX, CSProperties=props))
    monkeypatch.setitem(sys.modules, 'CSXCAD.CSProperties', props)
    monkeypatch.setitem(sys.modules, 'openEMS', types.SimpleNamespace(openEMS=FDTD))
    return log


# ---- a machine without openEMS ------------------------------------------------------------------------
@pytest.fixture
def no_csxcad(monkeypatch):
    """CSXCAD and openEMS cannot be imported, as on a machine without openEMS."""
    import builtins
    real = builtins.__import__

    def imp(name, *a, **kw):
        if name.split('.')[0] in ('CSXCAD', 'openEMS'):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real(name, *a, **kw)
    for k in [k for k in sys.modules if k.split('.')[0] in ('CSXCAD', 'openEMS')]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.setattr(builtins, '__import__', imp)


# ---- field dumps as openEMS writes them (kicad2ems's field tests, the GUI's field view) ----------------------
def _b64_block(raw, compress):
    if not compress:
        return base64.b64encode(np.uint32(len(raw)).tobytes() + raw).decode()
    c = zlib.compress(raw)
    head = np.array([1, 32768, len(raw), len(c)], '<u4').tobytes()
    return base64.b64encode(head).decode() + base64.b64encode(c).decode()   # encoded separately, as VTK does


def write_vtr(path, x, y, z, field, compress=True):
    """A VTK rectilinear grid like openEMS's dumps: coordinates in m, point data x fastest."""
    hdr = ' compressor="vtkZLibDataCompressor"' if compress else ''
    data = np.transpose(field.astype('<f4'), (2, 1, 0, 3)).tobytes()
    coords = ''.join(f'<DataArray type="Float64" format="binary">{_b64_block((np.asarray(c, "<f8") * 1e-3).tobytes(), compress)}</DataArray>'
                     for c in (x, y, z))
    ext = f"0 {len(x) - 1} 0 {len(y) - 1} 0 {len(z) - 1}"
    with open(path, 'w') as fh:
        fh.write(f'<?xml version="1.0"?><VTKFile type="RectilinearGrid" version="0.1" byte_order="LittleEndian" '
                 f'header_type="UInt32"{hdr}><RectilinearGrid WholeExtent="{ext}"><Piece Extent="{ext}"><PointData>'
                 f'<DataArray type="Float32" Name="E-Field" NumberOfComponents="3" format="binary">'
                 f'{_b64_block(data, compress)}</DataArray></PointData><CellData></CellData><Coordinates>{coords}'
                 f'</Coordinates></Piece></RectilinearGrid></VTKFile>')


@pytest.fixture(scope='module')
def run_folder(demo_files, tmp_path_factory):
    """A run folder with a time-domain plane dump (VTR frames), a frequency-domain one (HDF5) and their info."""
    h5py = pytest.importorskip('h5py')
    from kicad2ems.fields.dumps import write_info, write_norm, parse_dump
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    out = tmp_path_factory.mktemp('out')
    run = out / 'openems' / 'exc_P1'
    td, fd = parse_dump('E@F.Cu-0.1', m), parse_dump('J@F.Cu@3', m)
    write_info(str(run), [td, fd], m, dt_ps=0.5)
    ob = m.outline.bounds
    x, y = np.linspace(ob[0], ob[2], 30), np.linspace(ob[1], ob[3], 12)
    for k in range(5):
        f = np.zeros((30, 12, 1, 3), np.float32)
        f[:, :, 0, 2] = (k + 1) * np.sin(x / 3)[:, None]
        write_vtr(run / f"{td.name}_{k * 60:010d}.vtr", x, y, [td.start[2]], f)
    with h5py.File(run / f"{fd.name}.h5", 'w') as h:
        for ax, v in zip('xyz', (x, y, [fd.start[2]])):
            h[f'Mesh/{ax}'] = np.asarray(v) * 1e-3
        g = h.create_group('FieldData/FD')
        ds = g.create_dataset('f0', data=np.ones((3, 30, 12, 1)) * (1 + 1j))
        ds.attrs['d_order'], ds.attrs['frequency'] = 'NXYZ', 3e9
    write_norm(str(run), {3e9: 0.5j})
    return str(out), m


# ---- a prepared solver, for the parity checks (kicad2ems-fdtd's, mom25d's) ------------------------------------
def prepared_job(tmp_path, base, solver, *args):
    """A job with its board and line model, and `solver`'s model prepared, as a preview does (the mesh alone)."""
    from kicad2ems.cli import settings_from_argv
    from kicad2ems.pipeline import Job
    from kicad2ems.solvers import registry
    s, _ = settings_from_argv([base + '.kicad_pcb', '--pos', base + '-top.pos', '--preview', '--solvers', solver,
                               '--out', str(tmp_path), *args])
    j = Job(s, echo=lambda *a: None)
    os.makedirs(s.out, exist_ok=True)
    j.model()
    j.lines()
    return j, registry()[solver].prepare(j)
