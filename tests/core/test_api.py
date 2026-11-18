"""The modules' public names (each its __all__) resolve and are documented,
importing the solvers and analyses loads no GUI or plotting stack, and sysinfo
asks the solvers what they run on (Solver.resources)."""
import importlib
import os
import subprocess
import sys

import pytest
from kicad2ems.core import solvers, sysinfo

from test_solver_registry import _Fake

# the modules other parts import from (docs/api.md lists them)
PUBLIC = [
    'kicad2ems.core.solvers', 'kicad2ems.core.options',
    'kicad2ems.core.sparams', 'kicad2ems.core.stop', 'kicad2ems.core.plots',
    'kicad2ems.core.fields.antenna', 'kicad2ems.core.fields.dumps',
    'kicad2ems.core.fields.frames', 'kicad2ems.core.meshes',
    'kicad2ems.core.phased', 'kicad2ems.core.sitecfg',
    'kicad2ems.core.pipeline', 'kicad2ems.core.constants',
    'kicad2ems.core.geometry', 'kicad2ems.core.edges',
    'kicad2ems.core.solvers.lines.fastsolve', 'kicad2ems.core.roughness',
    'kicad2ems.core.finish', 'kicad2ems.core.analyses',
    'kicad2ems.core.importers.kicad', 'kicad2ems.core.board',
    'kicad2ems.core.parts', 'kicad2ems.core.region', 'kicad2ems.core.layout',
    'kicad2ems.core.lines.builder', 'kicad2ems.core.lines.reference',
    'kicad2ems.core.overlay', 'kicad2ems.core.lines.sections',
    'kicad2ems.core.lines.calc', 'kicad2ems.core.runs',
    'kicad2ems.core.formats', 'kicad2ems.core.touchstone',
    'kicad2ems.emsdev.cpu', 'kicad2ems.examples'
]
# what a solver or analysis imports: none of it may load these
LIGHT = [
    'kicad2ems.core.solvers', 'kicad2ems.core.analyses',
    'kicad2ems.core.study', 'kicad2ems.core.config', 'kicad2ems.core.runs',
    'kicad2ems.core.fields.dumps', 'kicad2ems.core.fields.frames',
    'kicad2ems.core.fields.antenna', 'kicad2ems.mom25d',
    'kicad2ems.mom25d.solvers.mom', 'kicad2ems.pcbqs',
    'kicad2ems.pcbqs.analyses', 'kicad2ems.fdtd.solver',
    'kicad2ems.fem.analyses', 'kicad2ems.examples'
]
HEAVY = ('PySide6', 'matplotlib', 'h5py')


@pytest.mark.parametrize('name', PUBLIC)
def test_every_public_name_resolves(name):
    mod = importlib.import_module(name)
    missing = [n for n in mod.__all__ if not hasattr(mod, n)]
    assert mod.__all__ and missing == []


def test_solvers_and_analyses_import_light():
    """Importing the core's solver and analysis modules, and filling both
    registries, loads no PySide6, matplotlib or h5py: each is imported inside
    the function that needs it."""
    code = ('import importlib, sys\n'
            f'for m in {LIGHT!r}: importlib.import_module(m)\n'
            'from kicad2ems.core import solvers, analyses\n'
            'solvers.registry(); analyses.registry()\n'
            f'print(sorted(set({HEAVY!r}) & set(sys.modules)))')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ['[]']


def test_sysinfo_asks_the_solvers(monkeypatch):
    gpu = dict(device='Fake GPU', backend='cupy', api='ROCm/HIP', free=8.0,
               total=16.0)
    reg = dict(
        a=type('A', (_Fake, ),
               dict(resources=lambda self: dict(gpus=[], why='no CuPy')))(),
        b=type('B', (_Fake, ), dict(resources=lambda self: None))(),
        mom25d=type('M', (_Fake, ),
                    dict(resources=lambda self: dict(gpus=[gpu], why='')))())
    monkeypatch.setattr(solvers, 'registry', lambda: reg)
    res = sysinfo.resources()
    assert set(res) == {'a', 'mom25d'}  # b: the CPU only
    assert sysinfo.gpu(res) == (gpu, '')
    del reg['mom25d']
    assert sysinfo.gpu() == (None, 'a: no CuPy')
    info = sysinfo.info()
    assert (info['ram']['available'] > 0 and info['threads'] >= 1
            and info['resources'] == {'a': dict(gpus=[], why='no CuPy')})


def test_sysinfo_imports_no_solver():
    """The core reports the machine without importing a solver package (mom25d,
    gfdtd) itself."""
    src = open(sysinfo.__file__, encoding='utf-8').read()
    assert 'mom25d' not in src.replace("mom25d's", '') and 'gfdtd' not in src


def test_packages_found():
    """setup.py takes every package under src/ (find_packages) and the examples:
    so every folder of modules there has to be a package (an __init__.py), down
    from kicad2ems, or the installed stack would miss it."""
    import os
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = os.path.join(root, 'src', 'kicad2ems')
    missed = []
    for d, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        rel = os.path.relpath(d, src)
        if any(f.endswith('.py') for f in files) and '__init__.py' not in files:
            missed.append(rel)
    # ipc/plugin is the folder KiCad loads as it is (package data, its files
    # copied by kicad2ems.ipc.install)
    assert missed == [os.path.join('ipc', 'plugin')], missed
    setup = open(os.path.join(root, 'setup.py'), encoding='utf-8').read()
    assert "find_packages('src')" in setup and "'kicad2ems.examples'" in setup


def test_examples_prepared(tmp_path):
    """The examples: every config, each copied into a folder with its
    board, made by its generator or copied with its project and placement
    files."""
    import kicad2ems.examples as ex
    names = ex.own()  # kicad2ems's own (an analysis may ship more)
    assert set(names) <= set(ex.examples())
    assert (len(names) > 40
            and {'rf_gcpw', 'sma_launch', 'demo_lowpass', 'antenna_patch'
                 } <= set(names))
    for p in names.values():  # each board: made, or one of those shipped
        stem = ex.board_of(p)[:-len('.kicad_pcb')]
        assert (ex.generator(stem) is not None
                or os.path.exists(os.path.join(ex.HERE, stem + '.kicad_pcb')))
    import tomllib
    data = tomllib.load(
        open(os.path.join(os.path.dirname(ex.HERE), 'pyproject.toml'), 'rb'))
    shipped = data['tool']['setuptools']['package-data']['kicad2ems.examples']
    assert ({
        s[:-len('.kicad_pcb')]
        for s in shipped
        if s.endswith('.kicad_pcb')
    } == set(ex.BOARDS))
    for n in ('rf_gcpw', 'sma_launch', 'demo_lowpass'):
        cfg = ex.prepare_example(n, str(tmp_path))
        assert (cfg == str(tmp_path / f'{n}.toml')
                and (tmp_path / ex.board_of(cfg)).exists())
    assert ((tmp_path / 'sma_launch-all.pos').exists()
            and (tmp_path / 'demo_lowpass-top.pos').exists())
    from kicad2ems.core.config import build_settings
    s = build_settings({}, str(tmp_path / 'rf_gcpw.toml'))
    assert os.path.dirname(s.board) == str(tmp_path)


def test_example_boards_made_in_a_folder(tmp_path, monkeypatch):
    """make_boards (install.py, through examples/make_boards.py): each generated
    board made in the folder, those in the repository left alone; a generator
    that fails is reported, the others still made."""
    import kicad2ems.examples as ex
    assert (ex.make_boards(str(tmp_path),
                           names=['demo_lowpass', 'rf_gcpw',
                                  'hairpin'], log=None) == {})
    assert ({p.name
             for p in tmp_path.glob('*.kicad_pcb')
             } == {'demo_lowpass.kicad_pcb', 'rf_gcpw.kicad_pcb'})
    real = ex.generator
    monkeypatch.setattr(
        ex, 'generator',
        lambda stem: (('no_such_generator.py', [], []) if stem == 'rf_gcpw'
                      else real(stem)))
    failed = ex.make_boards(str(tmp_path), names=['rf_gcpw', 'antenna_patch'],
                            log=None)
    assert (list(failed) == ['rf_gcpw.kicad_pcb']
            and (tmp_path / 'antenna_patch.kicad_pcb').exists())


def test_setup_update_needs_an_install(tmp_path):
    """install.py --update: nothing to update says so, before anything is
    installed; it takes no solver choices."""
    import sys
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run = lambda *a: subprocess.run([
        sys.executable, os.path.join(root, 'install.py'), *a
    ], capture_output=True, text=True)
    r = run('--update', '--venv', str(tmp_path / 'none'))
    assert (r.returncode == 1 and 'run the installer' in r.stdout
            and not (tmp_path / 'none').exists())
    for other in (['--uninstall'], ['--openems',
                                    'build'], ['--qucsator', 'none']):
        r = run('--update', '--target', 'user', *other)
        assert r.returncode == 2 and '--update' in r.stdout


def test_every_public_name_is_documented():
    """docs/api.md names every module of PUBLIC and each name of its __all__,
    and each class and function has a docstring: a name added to an __all__
    without its documentation fails here."""
    import inspect, re
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    page = open(os.path.join(root, 'docs', 'api.md'), encoding='utf-8').read()
    for name in PUBLIC:
        assert f'`{name}`' in page, f"{name} is not in docs/api.md"
        mod = importlib.import_module(name)
        for n in mod.__all__:
            assert re.search(rf'`{re.escape(n)}[`(]',
                             page), f"{name}.{n} is not in docs/api.md"
            obj = getattr(mod, n)
            if inspect.isclass(obj) or inspect.isfunction(obj):
                assert inspect.getdoc(obj), f"{name}.{n} has no docstring"
