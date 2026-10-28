"""kicad2ems.api: every public name resolves, importing a surface loads nothing, the registry refuses a plugin
written for another API version, and sysinfo asks the solvers what they run on (Solver.resources)."""
import subprocess
import sys

import pytest
from kicad2ems import solvers, sysinfo
from kicad2ems.api import API_VERSION, compatible, solver as solver_api, frontend

from test_plugins import _EP, _Fake


@pytest.mark.parametrize('surface', [solver_api, frontend])
def test_every_name_resolves(surface):
    for name in surface.NAMES:
        assert getattr(surface, name) is not None, name
    assert set(dir(surface)) == set(surface.NAMES)
    with pytest.raises(AttributeError):
        getattr(surface, 'no_such_name')


def test_a_surface_loads_on_first_use():
    """Importing kicad2ems.api.solver (as a plugin's module does when the registry loads it) imports neither
    matplotlib nor the field code; a name imports its module when it is used."""
    code = ("import sys; import kicad2ems.api.solver as a; before = {'matplotlib', 'kicad2ems.plots'} & set(sys.modules); "
            "a.plot_antenna; print(sorted(before), 'kicad2ems.plots' in sys.modules)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ['[]', 'True']


def test_compatible():
    major, minor = API_VERSION
    assert compatible(None) is None and compatible((major, 0)) is None and compatible(API_VERSION) is None
    assert 'update kicad2ems' in compatible((major, minor + 1))
    assert 'update the plugin' in compatible((major - 1, 0)) and 'update kicad2ems' in compatible((major + 1, 0))


def test_registry_refuses_another_api(monkeypatch):
    class Newer(_Fake):
        name, aliases, options = 'newer', (), []
        api = (API_VERSION[0] + 1, 0)

    class Current(_Fake):
        name, aliases, options = 'current', (), []
        api = API_VERSION
    monkeypatch.setattr(solvers, '_registry', None)
    monkeypatch.setattr(solvers, 'broken', [])
    installed = solvers._entry_points()
    monkeypatch.setattr(solvers, '_entry_points', lambda: installed + [_EP('newer', Newer), _EP('current', Current)])
    reg = solvers.registry()
    assert 'current' in reg and 'newer' not in reg
    assert [b[0].split(' =')[0] for b in solvers.broken] == ['newer'] and 'kicad2ems API' in solvers.broken[0][1]
    monkeypatch.setattr(solvers, '_registry', None)


def test_sysinfo_asks_the_solvers(monkeypatch):
    gpu = dict(device='Fake GPU', backend='cupy', api='ROCm/HIP', free=8.0, total=16.0)
    reg = dict(a=type('A', (_Fake,), dict(resources=lambda self: dict(gpus=[], why='no CuPy')))(),
               b=type('B', (_Fake,), dict(resources=lambda self: None))(),
               mom25d=type('M', (_Fake,), dict(resources=lambda self: dict(gpus=[gpu], why='')))())
    monkeypatch.setattr(solvers, 'registry', lambda: reg)
    res = sysinfo.resources()
    assert set(res) == {'a', 'mom25d'}                              # b: the CPU only
    assert sysinfo.gpu(res) == (gpu, '')
    del reg['mom25d']
    assert sysinfo.gpu() == (None, 'a: no CuPy')
    info = sysinfo.info()
    assert info['ram']['available'] > 0 and info['threads'] >= 1 and info['resources'] == {'a': dict(gpus=[], why='no CuPy')}


def test_sysinfo_imports_no_solver():
    """The core reports the machine without importing a solver package (mom25d, gfdtd) itself."""
    src = open(sysinfo.__file__).read()
    assert 'mom25d' not in src.replace("mom25d's", '') and 'gfdtd' not in src
