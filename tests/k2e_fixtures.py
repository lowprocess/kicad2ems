"""kicad2ems's test fixtures, shared with the solver packages' tests (kicad2ems-fdtd, mom25d: their
conftest imports this file from a kicad2ems checkout, KICAD2EMS_SRC): the generated example boards, a strict
stand-in for CSXCAD / openEMS, a machine without openEMS, and an in-process command-line runner.

    from k2e_fixtures import *              # noqa: F401,F403  (in a conftest.py)

A fixture's name must not start with '_': the star import leaves those out, and an autouse one (scratch_settings,
which keeps the tests out of the user's settings) would silently stop applying.
"""
import importlib.util, os, subprocess, sys, types
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
