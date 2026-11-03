"""install.py, the installer (Linux, macOS, Windows): its folders and files per platform, without installing
anything. The Windows paths are checked here on any system, with install.WINDOWS set."""
import importlib.util, os, subprocess, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def inst(monkeypatch):
    spec = importlib.util.spec_from_file_location('k2e_install', os.path.join(ROOT, 'install.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'YES', True)
    return mod


@pytest.fixture
def windows(inst, monkeypatch, tmp_path):
    """install.py as on Windows: its environment's folders under tmp_path."""
    monkeypatch.setattr(inst, 'WINDOWS', True)
    monkeypatch.setattr(inst, 'LINUX', False)
    for k, sub in (('LOCALAPPDATA', 'Local'), ('APPDATA', 'Roaming'), ('ProgramFiles', 'Program Files'),
                   ('ProgramData', 'ProgramData')):
        monkeypatch.setenv(k, str(tmp_path / sub))
    return inst


def test_venv_layout(inst, windows):
    assert windows.venv_python('V') == os.path.join('V', 'Scripts', 'python.exe')
    assert windows.script(os.path.join('V', 'Scripts'), 'kicad2ems').endswith('kicad2ems.exe')


def test_targets_on_windows(windows, tmp_path):
    """Windows' folders: the user's venv and commands in %LOCALAPPDATA%, the system's in Program Files, the
    shortcuts in the user's or every user's Start menu."""
    u = windows.Target('user')
    assert u.venv == str(tmp_path / 'Local' / 'kicad2ems' / 'venv') and u.bin == str(tmp_path / 'Local' / 'kicad2ems' / 'bin')
    assert u.start.startswith(str(tmp_path / 'Roaming')) and u.start.endswith('Programs') and u.apps is None
    s = windows.Target('system')
    assert s.venv == str(tmp_path / 'Program Files' / 'kicad2ems' / 'venv') and not s.editable
    assert s.start.startswith(str(tmp_path / 'ProgramData')) and s.path_scope == 'system' and u.path_scope == 'user'
    c = windows.Target('checkout')
    assert c.venv == os.path.join(windows.REPO, '.venv') and c.bin is None and c.editable
    with pytest.raises(windows.Abort):
        windows.Target('elsewhere')


@pytest.mark.skipif(sys.platform == 'win32', reason="Linux's folders: tested on Linux")
def test_targets_on_linux(inst, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, 'WINDOWS', False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'share'))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))                 # expanduser's home on Windows
    u = inst.Target('user')
    assert u.venv == str(tmp_path / 'share' / 'kicad2ems' / 'venv') and u.bin == str(tmp_path / '.local' / 'bin')
    assert u.apps == str(tmp_path / 'share' / 'applications') and u.start is None
    monkeypatch.setattr(inst, 'SYSROOT', str(tmp_path / 'root'))
    s = inst.Target('system')
    assert s.venv == str(tmp_path / 'root') + '/opt/kicad2ems/venv' and s.bin == str(tmp_path / 'root') + '/usr/local/bin'


def test_shim_and_path(inst):
    """Windows: the commands' .cmd shims, and the PATH value with a folder added once, or taken out."""
    assert inst.shim_text(r'C:\k\venv\Scripts\kicad2ems.exe') == '@echo off\r\n"C:\\k\\venv\\Scripts\\kicad2ems.exe" %*\r\n'
    v = r'C:\Windows;C:\tools'
    assert inst.path_with(v, r'C:\k\bin') == r'C:\Windows;C:\tools;C:\k\bin'
    assert inst.path_with(r'C:\Windows;C:\k\bin;', r'C:\k\bin', add=False) == r'C:\Windows'
    assert inst.path_with('', 'C:\\k\\bin') == 'C:\\k\\bin'


def test_openems_folder_wheels(windows, tmp_path):
    """Windows' unpacked openEMS: found by its DLL; its CSXCAD and openEMS wheels for the Python kicad2ems runs on."""
    d = tmp_path / 'openEMS'
    (d / 'python').mkdir(parents=True)
    (d / 'openEMS.dll').write_text('', encoding='utf-8')
    for w in ('CSXCAD-0.6.3-cp313-cp313-win_amd64.whl', 'openEMS-0.0.36-cp313-cp313-win_amd64.whl',
              'CSXCAD-0.6.3-cp312-cp312-win_amd64.whl'):
        (d / 'python' / w).write_text('', encoding='utf-8')
    os.environ['OPENEMS_INSTALL_PATH'] = str(d)
    try:
        i = windows.Installer(windows.parse([]))
        assert str(d) in i.windows_openems_dirs()
    finally:
        del os.environ['OPENEMS_INSTALL_PATH']
    i.oems_dir = str(d)
    assert [os.path.basename(w) for w in i.openems_wheels('3.13')] == [
        'CSXCAD-0.6.3-cp313-cp313-win_amd64.whl', 'openEMS-0.0.36-cp313-cp313-win_amd64.whl']
    assert i.openems_wheels('3.12') == [] and i.openems_wheels('3.14') == []        # no openEMS wheel for those


def test_uninstall_takes_back_the_path(windows, tmp_path, monkeypatch):
    """--uninstall removes the recorded files and the PATH entry it added; the venv on request."""
    t = windows.Target('user')
    os.makedirs(t.venv)
    shim = tmp_path / 'Local' / 'kicad2ems' / 'bin' / 'kicad2ems.cmd'
    shim.parent.mkdir(parents=True)
    shim.write_text('x', encoding='utf-8')
    t.remember(str(shim))
    t.remember(f"@path\tuser\t{shim.parent}")
    changes = []
    monkeypatch.setattr(windows, 'windows_path', lambda folder, scope, add=True: changes.append((folder, scope, add)) or True)
    a = windows.parse(['--uninstall', '--target', 'user'])
    assert windows.Installer(a).main() == 0
    assert not shim.exists() and changes == [(str(shim.parent), 'user', False)] and not os.path.exists(t.venv)
    assert not os.path.exists(t.record)


def test_options(inst, tmp_path):
    """The command line: --update takes no solver choices; an unknown option is refused."""
    run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, 'install.py'), *a], capture_output=True,
                                    text=True)
    for other in (['--uninstall'], ['--openems', 'build'], ['--qucsator', 'none']):
        r = run('--update', '--target', 'user', *other)
        assert r.returncode == 2 and '--update' in r.stdout
    r = run('--frobnicate')
    assert r.returncode == 2 and 'unknown option' in r.stdout
    assert run('--help').stdout.startswith('kicad2ems setup')
    a = inst.parse(['--venv', '~/v', '-y'])
    assert a.yes and a.venv == os.path.abspath(os.path.expanduser('~/v')) and a.python


def test_choose_and_ask(inst, monkeypatch):
    """--yes takes each default; otherwise the answer typed, Enter the default, end of input the default too."""
    assert inst.choose('q', 2, ['a x', 'b y']) == 'b y' and inst.ask('q', 'n') is False
    monkeypatch.setattr(inst, 'YES', False)
    answers = iter(['', '1', 'maybe', 'y'])
    monkeypatch.setattr('builtins.input', lambda prompt='': next(answers))
    assert inst.choose('q', 2, ['a x', 'b y']) == 'b y' and inst.choose('q', 2, ['a x', 'b y']) == 'a x'
    assert inst.ask('q', 'n') is True                                # 'maybe' asked again
    monkeypatch.setattr('builtins.input', lambda prompt='': (_ for _ in ()).throw(EOFError))
    assert inst.ask('q', 'y') is True and inst.askpath('p', 'd') == 'd'


def test_core_stale(inst, tmp_path):
    (tmp_path / 'core').mkdir()
    src = tmp_path / 'core' / 'x.cpp'
    src.write_text('', encoding='utf-8')
    built = tmp_path / '_core.so'
    assert inst.core_stale(str(tmp_path), str(built))
    built.write_text('', encoding='utf-8')
    os.utime(src, (1, 1))
    assert not inst.core_stale(str(tmp_path), str(built))
    os.utime(src, None)
    os.utime(built, (2, 2))
    assert inst.core_stale(str(tmp_path), str(built))


def test_stdlib_only():
    """The installer runs before anything is installed: it imports nothing outside the standard library."""
    import ast
    tree = ast.parse(open(os.path.join(ROOT, 'install.py'), encoding='utf-8').read())
    names = {n.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
    names |= {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


def test_visual_studio_without_vswhere(windows, tmp_path, monkeypatch):
    """Windows: Build Tools found by their vcvars64.bat where vswhere is missing."""
    b = tmp_path / 'Program Files' / 'Microsoft Visual Studio' / '18' / 'BuildTools' / 'VC' / 'Auxiliary' / 'Build'
    b.mkdir(parents=True)
    (b / 'vcvars64.bat').write_text('', encoding='utf-8')
    monkeypatch.setenv('ProgramFiles(x86)', str(tmp_path / 'none'))
    monkeypatch.setattr(windows.shutil, 'which', lambda name: None)
    assert windows.Installer(windows.parse([])).has_compiler() == 'Visual Studio'
