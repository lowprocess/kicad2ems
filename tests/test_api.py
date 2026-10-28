"""kicad2ems.api: every public name resolves, importing a surface loads nothing, the registry refuses a plugin
written for another API version, and sysinfo asks the solvers what they run on (Solver.resources)."""
import os
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
    assert compatible((major, 0)) is None and compatible(API_VERSION) is None
    assert 'update the plugin' in compatible(None)                  # unversioned: 1.0, from before 2.0
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


def test_packages_listed():
    """pyproject.toml lists the packages (for the examples' mapping): every one under src/, and the examples."""
    import os, tomllib
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    listed = set(tomllib.load(open(os.path.join(root, 'pyproject.toml'), 'rb'))['tool']['setuptools']['packages'])
    found = {os.path.relpath(d, os.path.join(root, 'src')).replace(os.sep, '.')
             for d, _, files in os.walk(os.path.join(root, 'src', 'kicad2ems')) if '__init__.py' in files}
    assert listed == found | {'kicad2ems.examples'}


def test_examples_prepared(tmp_path):
    """api.frontend's examples: every config, each copied into a folder with its board, made by its generator or
    copied with its project and placement files."""
    import kicad2ems.examples as ex
    names = frontend.examples()
    assert len(names) > 40 and {'rf_gcpw', 'sma_launch', 'demo_lowpass', 'antenna_patch'} <= set(names)
    for p in names.values():                                        # each board: made, or one of those shipped
        stem = ex.board_of(p)[:-len('.kicad_pcb')]
        assert ex.generator(stem) is not None or os.path.exists(os.path.join(ex.HERE, stem + '.kicad_pcb'))
    import tomllib
    data = tomllib.load(open(os.path.join(os.path.dirname(ex.HERE), 'pyproject.toml'), 'rb'))
    shipped = data['tool']['setuptools']['package-data']['kicad2ems.examples']
    assert {s[:-len('.kicad_pcb')] for s in shipped if s.endswith('.kicad_pcb')} == set(ex.BOARDS)
    for n in ('rf_gcpw', 'sma_launch', 'demo_lowpass'):
        cfg = frontend.prepare_example(n, str(tmp_path))
        assert cfg == str(tmp_path / f'{n}.toml') and (tmp_path / ex.board_of(cfg)).exists()
    assert (tmp_path / 'sma_launch-all.pos').exists() and (tmp_path / 'demo_lowpass-top.pos').exists()
    s = frontend.build_settings({}, str(tmp_path / 'rf_gcpw.toml'))
    assert os.path.dirname(s.board) == str(tmp_path)
