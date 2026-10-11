"""Shared fixtures: the generated example boards, a strict stand-in for CSXCAD / openEMS, and an
in-process command-line runner."""
import os, subprocess, sys, types
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GEN = os.path.join(ROOT, 'examples', 'make_demo_board.py')
ANT = os.path.join(ROOT, 'examples', 'make_antennas.py')


@pytest.fixture(scope='session')
def boards(tmp_path_factory):
    """Every generated example board, by name: demo (transition), diff, rlc, lowpass, launch and the four antennas.
    Values are base paths (add .kicad_pcb / -top.pos)."""
    d = tmp_path_factory.mktemp('boards')
    out = {}
    for name, variant in (('demo', None), ('diff', 'diff'), ('rlc', 'rlc'), ('lowpass', 'lowpass'), ('launch', 'launch')):
        subprocess.check_call([sys.executable, GEN, str(d / name)] + ([variant] if variant else []),
                              stdout=subprocess.DEVNULL)
        out[name] = str(d / name)
    for kind in ('patch', 'ifa', 'dipole', 'slot'):
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
            log.append('port'); return object()

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
