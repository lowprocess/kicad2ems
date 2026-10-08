"""build_fdtd against strict stand-ins for CSXCAD / openEMS: every scalar handed over must be a plain
Python number (older CSXCAD releases test type(v) in (int, float) and reject numpy scalars)."""
import sys, types
import numpy as np
import pytest
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.boundaries import resolve_bc
from kicad2openems.mesh import auto_mesh


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
    def AddCylinder(self, a, b, r, priority=0):
        _coords(a, 'cyl start'); _coords(b, 'cyl stop'); _scalar(r, 'cyl radius'); self.log.append('cyl')


@pytest.fixture
def fake_openems(monkeypatch):
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
    class FDTD:
        def __init__(self, NrTS, EndCriteria): _scalar(NrTS, 'NrTS'); _scalar(EndCriteria, 'EndCriteria')
        def SetGaussExcite(self, f0, fc): _scalar(f0, 'f0'); _scalar(fc, 'fc')
        def SetBoundaryCond(self, bc): [_scalar(b, 'bc') for b in bc]
        def SetCSX(self, c): pass
        def AddLumpedPort(self, nr, R, start, stop, d, excite=0, priority=0):
            _scalar(R, 'port R'); _coords(start, 'port start'); _coords(stop, 'port stop'); _scalar(excite, 'excite')
            log.append('port'); return object()
    monkeypatch.setitem(sys.modules, 'CSXCAD', types.SimpleNamespace(ContinuousStructure=CSX))
    monkeypatch.setitem(sys.modules, 'openEMS', types.SimpleNamespace(openEMS=FDTD))
    return log


@pytest.mark.parametrize('sigma', [None, 5.8e7])
def test_build_fdtd_passes_plain_scalars(demo_files, fake_openems, sigma):
    from kicad2openems.simulation import build_fdtd
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, log=lambda *a: None)
    freq = np.linspace(1e9, 8e9, 11)                                # numpy scalars, as in a real run
    build_fdtd(m, mesh, freq[0], freq[-1], 0, bc=bc, sigma=np.float64(sigma) if sigma else None)
    assert fake_openems.count('linpoly') == len(m.layers) and fake_openems.count('port') == 2
    assert fake_openems.count('cyl') == len(m.vias)
