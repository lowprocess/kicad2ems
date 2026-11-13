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
    for other in (['--uninstall'], ['--openems', 'build'], ['--qucsator', 'none'], ['--elmer', 'build'],
                  ['--palace', 'none']):
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


def test_qs_can_be_selected_without_mom_or_fdtd(inst,monkeypatch):
    setup=inst.Installer.__new__(inst.Installer);setup.oems='none'
    monkeypatch.setattr(inst,'ask',lambda prompt,default:prompt.startswith(('pcbqs (','build the compiled cores')))
    monkeypatch.setattr(setup,'has_compiler',lambda:'g++')
    setup.choose_parts()
    assert setup.qs and setup.core and not setup.mom and not setup.gfdtd
    assert setup.extras==['qs','core'] and 'pcbqs' in inst.STACK


def test_qs_only_mesher_source_fallback(inst,monkeypatch):
    setup=inst.Installer.__new__(inst.Installer);setup.mom=False;setup.qs=True;setup.vpy='python';setup.pyver='3.14'
    calls=[]
    def pip(*args,**kwargs):
        calls.append((args,kwargs));return len(calls)>1
    monkeypatch.setattr(setup,'pip',pip);monkeypatch.setattr(inst,'py_ok',lambda *_:False);monkeypatch.setattr(inst,'WINDOWS',False)
    setup.ensure_triangle()
    assert calls[0][0]==('triangle>=20230923',)
    assert calls[1][0][1].endswith('@'+inst.TRIANGLE_COMMIT)
    assert calls[1][1]['env']=={'CFLAGS':'-O2 -ffp-contract=off'}


def _fem(inst, monkeypatch, tmp_path, *args, answers=None):
    """An installer at its finite-element step, in a target whose folders are under tmp_path; nothing found unless a
    test puts it there."""
    setup = inst.Installer.__new__(inst.Installer)
    setup.a, setup.extras = inst.parse(list(args)), []
    setup.t = inst.Target('checkout', str(tmp_path / 'venv'))
    setup.t.fem_prefix = str(tmp_path / 'fem')
    for key in ('K2E_ELMER', 'K2E_PALACE', 'K2E_PALACE_ROOTFS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(inst.os.path, 'expanduser', lambda p: p.replace('~', str(tmp_path / 'home')))
    monkeypatch.setattr(inst.shutil, 'which', lambda name: None)
    monkeypatch.setattr(inst, 'WINDOWS', False)
    mine = inst.Installer.elmer_dir                                 # (this machine's own Elmer: not seen)
    monkeypatch.setattr(inst.Installer, 'elmer_dir',
                        staticmethod(lambda f: mine(f) if f and str(f).startswith(str(tmp_path)) else None))
    if answers is not None:
        monkeypatch.setattr(inst, 'ask', lambda q, default: answers.get(q.split(' ')[0], default == 'y'))
    return setup


def _elmer(folder):
    folder.mkdir(parents=True)
    for name in ('ElmerSolver', 'ElmerGrid'):
        (folder / name).write_text('')
    return folder


def test_finite_elements_are_offered_and_nothing_built_by_default(inst, monkeypatch, tmp_path):
    """kicad2ems-fem: asked about (not by default when neither program is found); Elmer and Palace each one there
    is, one given, built, or none; Palace's Spack build is never the default."""
    setup = _fem(inst, monkeypatch, tmp_path)
    monkeypatch.setattr(inst, 'ask', lambda q, default: default == 'y')
    setup.choose_fem()                                              # neither found: not by default
    assert not setup.fem and setup.extras == []
    setup = _fem(inst, monkeypatch, tmp_path, answers={'kicad2ems-fem': True})
    setup.choose_fem()                                              # asked for: both 'none' by default
    assert setup.fem and setup.extras == ['fem'] and setup.elmer is None and setup.palace is None
    assert not setup.build_elmer and not setup.build_palace
    found = _elmer(tmp_path / 'opt-elmer' / 'bin')
    monkeypatch.setenv('K2E_ELMER', str(found))
    setup = _fem(inst, monkeypatch, tmp_path)
    monkeypatch.setenv('K2E_ELMER', str(found))
    image = tmp_path / 'home' / 'opt' / 'palace' / 'rootfs' / 'opt' / 'views' / 'view' / 'bin'
    image.mkdir(parents=True)
    monkeypatch.setattr(inst.shutil, 'which', lambda name: '/usr/bin/podman' if name == 'podman' else None)
    monkeypatch.setattr(inst, 'ask', lambda q, default: default == 'y')
    setup.choose_fem()                                              # found: asked for by default, each one taken
    assert setup.fem and setup.elmer == str(found)
    assert setup.palace == ('image', str(tmp_path / 'home' / 'opt' / 'palace' / 'rootfs'))
    setup.sitecfg = str(tmp_path / 'site.toml')
    setup.qucs, setup.oems = None, 'none'
    setup.write_site()
    text = open(setup.sitecfg).read()
    import json
    assert f'elmer = {json.dumps(str(found))}' in text and 'palace_rootfs = ' in text and 'palace = ' not in text


def test_finite_elements_from_the_command_line(inst, monkeypatch, tmp_path):
    exe = tmp_path / 'view' / 'bin' / 'palace'
    exe.parent.mkdir(parents=True)
    exe.write_text('')
    elmer = _elmer(tmp_path / 'elmer' / 'bin')
    setup = _fem(inst, monkeypatch, tmp_path, '--elmer', str(elmer.parent), '--palace', str(exe))
    setup.choose_fem()                                              # a folder or its bin; the executable
    assert setup.fem and setup.elmer == str(elmer) and setup.palace == ('exe', str(exe))
    setup = _fem(inst, monkeypatch, tmp_path, '--elmer', 'build', '--palace', 'spack')
    setup.choose_fem()
    assert setup.build_elmer and setup.build_palace and setup.elmer is None and setup.palace is None
    setup = _fem(inst, monkeypatch, tmp_path, '--elmer', str(tmp_path / 'nothing'), '--palace', 'none')
    setup.choose_fem()                                              # given, not there: said, and none
    assert setup.fem and setup.elmer is None and setup.palace is None
    setup = _fem(inst, monkeypatch, tmp_path, '--palace', 'spack')
    monkeypatch.setattr(inst, 'WINDOWS', True)
    with pytest.raises(inst.Abort, match='Palace builds on Linux and macOS'):
        setup.choose_fem()


def test_the_builds_run_their_scripts_and_record_what_they_made(inst, monkeypatch, tmp_path):
    setup = _fem(inst, monkeypatch, tmp_path, '--elmer', 'build', '--palace', 'spack')
    setup.choose_fem()
    ran = []

    def fake(cmd, **kw):
        ran.append(cmd[1:])
        if cmd[0] == 'bash' and cmd[1].endswith('build_elmer.sh'):
            _elmer(tmp_path / 'fem' / 'elmer' / 'bin')
        elif cmd[0] == 'bash':
            (tmp_path / 'fem' / 'palace' / 'bin').mkdir(parents=True)
            (tmp_path / 'fem' / 'palace' / 'bin' / 'palace').write_text('')
        return True
    monkeypatch.setattr(inst, 'run', fake)
    monkeypatch.setattr(inst, 'ask', lambda q, default: q.startswith('build'))     # not the distribution's packages
    setup.build_fem()
    assert ran == [[os.path.join(inst.REPO, 'tools', 'build_elmer.sh'), str(tmp_path / 'fem' / 'elmer')],
                   [os.path.join(inst.REPO, 'tools', 'build_palace.sh'), str(tmp_path / 'fem' / 'palace')]]
    assert setup.elmer == str(tmp_path / 'fem' / 'elmer' / 'bin')
    assert setup.palace == ('exe', str(tmp_path / 'fem' / 'palace' / 'bin' / 'palace'))
    assert 'kicad2ems-fem' in inst.STACK


@pytest.mark.skipif(sys.platform == 'win32', reason='the builds are for Linux and macOS')
def test_the_build_scripts_are_shell_that_parses():
    for name in ('build_elmer.sh', 'build_palace.sh'):
        r = subprocess.run(['bash', '-n', os.path.join(ROOT, 'tools', name)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
