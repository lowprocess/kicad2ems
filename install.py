#!/usr/bin/env python3
"""kicad2ems setup: kicad2ems and the parts you choose, where you choose, with a launcher entry for the GUI.
Linux, macOS and Windows; only the Python standard library (it runs before anything is installed).

  python install.py                    asks about each part (defaults in [brackets]; Enter takes them)
                                       (Windows: py install.py; Linux and macOS: ./setup.sh runs this)
  python install.py --yes              takes every default without asking
  python install.py --target user      where: checkout (.venv here, the default), user (a venv of your own, the
                                       commands on your PATH), system (every user: /opt/kicad2ems or
                                       Program Files; runs sudo, or needs an Administrator prompt on Windows) or
                                       python (into the Python itself, pip --user: no venv)
  python install.py --openems PATH     an openEMS installed elsewhere: another venv's python, or on Windows the
                                       unpacked openEMS folder (or: build, none)
  python install.py --qucsator PATH    the line model's circuit solver (or: none)
  python install.py --python python3.12 --venv /path/to/venv   another Python, another venv (checkout target)
  python install.py --uninstall --target user                  remove what an install of that target added
  python install.py --update [--target user]   only kicad2ems and its stack in an existing install: asks
                                       nothing, leaves openEMS, the other dependencies and the GPU wheel as they are

Asks about: the GUI, field dumps, the scikit-rf line model, mom25d and gfdtd (and their compiled cores), dfsynth,
the test tools, the CuPy wheel for your GPU (detected: NVIDIA CUDA 12 / 13, AMD ROCm 7 on Linux), openEMS (one
this Python has, one in another venv linked in, Windows' unpacked release, build one with tools/build_openems.sh
on Linux, or none), qucsator, and a launcher entry and desktop icon for the GUI (a .desktop file on Linux, a
Start-menu shortcut on Windows). Makes the example boards that are not in the repository
(examples/make_boards.py), next to their configs. Run it again to change anything: an existing venv is kept and
added to (or made anew if you say so)."""
import argparse, glob, json, os, re, shutil, subprocess, sys, sysconfig, tempfile

TRIANGLE_COMMIT = '595b43eb6682992a0b1012b9671bf860c0e6ae56'    # triangle 20250106, builds on Python 3.14
# triangle's exact-arithmetic predicates need every product rounded on its own: a fused multiply-add (GCC fuses them
# at -O2 where the target has FMA; clang on ARM always) breaks them, and the mesher then never finishes
TRIANGLE_CFLAGS = '-O2 -ffp-contract=off'
CUPY_WHEELS = ('cupy-rocm-7-0', 'cupy-cuda12x', 'cupy-cuda13x')
STACK = ('emsdev', 'kicad2ems-gui', 'kicad2ems-fdtd', 'mom25d', 'gfdtd', 'dfsynth')
REPO = os.path.dirname(os.path.abspath(__file__))
WINDOWS = sys.platform == 'win32'
LINUX = sys.platform.startswith('linux')
SYSROOT = os.environ.get('KICAD2EMS_SYSROOT', '')               # tests: the system target under a scratch root
YES = False


class Abort(Exception):
    """Stop with a message and an exit status."""
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


# ---- talking --------------------------------------------------------------------------------------------
def bold(text):
    print(f"\n\033[1m{text}\033[0m" if sys.stdout.isatty() else f"\n{text}", flush=True)


def note(text):
    print(f"  {text}", flush=True)


def _input(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ''


def ask(q, default):
    """A yes / no question; default 'y' or 'n'; --yes takes the default."""
    if YES:
        return default == 'y'
    while True:
        a = (_input(f"  {q} [{'Y/n' if default == 'y' else 'y/N'}] ").strip().lower() or default)
        if a in ('y', 'yes'):
            return True
        if a in ('n', 'no'):
            return False


def choose(q, default, options):
    """One of options (their text); default: its 1-based index."""
    if not YES:
        print(f"  {q}")
        for i, o in enumerate(options, 1):
            print(f"    {i}) {o}{'   (default)' if i == default else ''}")
    while True:
        a = str(default) if YES else (_input(f"  choice [{default}] ").strip() or str(default))
        if a.isdigit() and 1 <= int(a) <= len(options):
            return options[int(a) - 1]


def first_word(option):
    return option.split()[0]


def askpath(q, default=''):
    if YES:
        return default
    return _input(f"  {q} [{default or 'skip'}] ").strip() or default


# ---- the machine ---------------------------------------------------------------------------------------
def venv_bin(venv):
    """A venv's scripts folder: Scripts on Windows, bin elsewhere."""
    return os.path.join(venv, 'Scripts' if WINDOWS else 'bin')


def venv_python(venv):
    return os.path.join(venv_bin(venv), 'python.exe' if WINDOWS else 'python')


def script(folder, name):
    """A console script's file in a scripts folder (kicad2ems.exe on Windows)."""
    return os.path.join(folder, name + ('.exe' if WINDOWS else ''))


def which_python(py):
    """The path of a Python given by name or path, or None."""
    if os.path.sep in py or (WINDOWS and '/' in py):
        return os.path.abspath(py) if os.path.isfile(py) else None
    return shutil.which(py)


def neutral_dir():
    """A folder to run the target Python in, so it doesn't import this checkout's files by accident."""
    return tempfile.gettempdir()


def py_out(py, code, *args):
    """What a Python prints for a snippet ('' when it fails)."""
    try:
        r = subprocess.run([py, '-c', code, *args], capture_output=True, text=True, cwd=neutral_dir())
    except OSError:
        return ''
    return r.stdout.strip() if r.returncode == 0 else ''


def py_ok(py, code):
    try:
        return subprocess.run([py, '-c', code], capture_output=True, cwd=neutral_dir()).returncode == 0
    except OSError:
        return False


def run(cmd, check=False, **kw):
    """A command, its output to the terminal; True when it succeeded (raises with check)."""
    kw.setdefault('cwd', neutral_dir())
    r = subprocess.run(cmd, **kw)
    if check and r.returncode:
        raise subprocess.CalledProcessError(r.returncode, cmd)
    return r.returncode == 0


def is_admin():
    if WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return os.geteuid() == 0


def data_home():
    """Where per-user data goes: %LOCALAPPDATA% on Windows, $XDG_DATA_HOME or ~/.local/share elsewhere."""
    if WINDOWS:
        return os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    return os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')


def start_menu(system=False):
    """Windows: the Start menu's Programs folder, the user's or every user's."""
    if system:
        return os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Microsoft', 'Windows', 'Start Menu',
                            'Programs')
    return os.path.join(os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming'),
                        'Microsoft', 'Windows', 'Start Menu', 'Programs')


def powershell(command, env=None):
    """Run a PowerShell command (Windows); its output, or None when it failed."""
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True,
                           text=True, env={**os.environ, **(env or {})}, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def desktop_folder():
    """The user's desktop (it may be redirected: OneDrive on Windows, xdg-user-dir on Linux), or None."""
    if WINDOWS:
        d = powershell("[Environment]::GetFolderPath('Desktop')")
    elif shutil.which('xdg-user-dir'):
        d = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True).stdout.strip()
    else:
        d = os.path.join(os.path.expanduser('~'), 'Desktop')
    return d if d and os.path.isdir(d) and os.path.abspath(d) != os.path.expanduser('~') else None


def give_to_user(path):
    """A file made as root by sudo in the user's own folders: back to that user."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if not WINDOWS and uid and os.geteuid() == 0:
        try:
            os.chown(path, int(uid), int(gid or uid))
        except OSError:
            pass


# ---- the PATH on Windows ---------------------------------------------------------------------------------
def _env_key(scope, write=False):
    import winreg
    if scope == 'system':
        return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
                              0, winreg.KEY_READ | (winreg.KEY_WRITE if write else 0))
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ | (winreg.KEY_WRITE if write else 0))


def path_entries(value):
    return [p for p in (value or '').split(';') if p]


def path_with(value, folder, add=True):
    """A PATH value (;-separated) with folder added at its end, or removed (compared without case or a trailing
    separator)."""
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    kept = [p for p in path_entries(value) if norm(p) != norm(folder)]
    return ';'.join(kept + ([folder] if add else []))


def windows_path(folder, scope, add=True):
    """Add folder to (or remove it from) the user's or the machine's PATH in the registry, and tell running programs
    (new terminals see it). True when it changed."""
    import winreg
    try:
        with _env_key(scope) as k:
            try:
                value, kind = winreg.QueryValueEx(k, 'Path')
            except FileNotFoundError:
                value, kind = '', winreg.REG_EXPAND_SZ
        new = path_with(value, folder, add)
        if new == value:
            return False
        with _env_key(scope, write=True) as k:
            winreg.SetValueEx(k, 'Path', 0, kind, new)
    except OSError as e:
        note(f"could not change the {scope} PATH: {e}")
        return False
    try:
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 0x0002, 5000, None)
    except (AttributeError, OSError):
        pass
    return True


def on_path(folder):
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    return norm(folder) in {norm(p) for p in os.environ.get('PATH', '').split(os.pathsep) if p}


# ---- links, shims, launchers ---------------------------------------------------------------------------------
def link_dir(src, dst):
    """dst as a link to the folder src: a symbolic link, or on Windows a junction (no privileges needed)."""
    if os.path.lexists(dst):
        if os.path.isdir(dst) and not os.path.islink(dst) and not is_junction(dst):
            shutil.rmtree(dst)
        else:
            unlink(dst)
    if WINDOWS:
        run(['cmd', '/c', 'mklink', '/J', dst, src], check=True, stdout=subprocess.DEVNULL)
    else:
        os.symlink(src, dst)


def is_junction(p):
    j = getattr(os.path, 'isjunction', None)
    return bool(j and j(p))


def unlink(p):
    """Remove a file, link or junction (never what a link points to)."""
    if is_junction(p):
        os.rmdir(p)
    else:
        os.remove(p)


def shim_text(target):
    """Windows: a .cmd in a PATH folder that runs a venv's console script with the arguments it is given."""
    return f'@echo off\r\n"{target}" %*\r\n'


def write_shortcut(path, target, args='', icon='', workdir=''):
    """Windows: a .lnk shortcut (through PowerShell's WScript.Shell; the values pass in the environment, so no
    quoting can break them). True when it was made."""
    cmd = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:K2E_LNK); $s.TargetPath = $env:K2E_TARGET; "
           "$s.Arguments = $env:K2E_ARGS; $s.WorkingDirectory = $env:K2E_DIR; "
           "if ($env:K2E_ICON) { $s.IconLocation = $env:K2E_ICON }; "
           "$s.Description = 'EM simulation of KiCad boards'; $s.Save()")
    powershell(cmd, {'K2E_LNK': path, 'K2E_TARGET': target, 'K2E_ARGS': args, 'K2E_ICON': icon,
                     'K2E_DIR': workdir or os.path.expanduser('~')})
    return os.path.isfile(path)


def desktop_entry(gui_cmd, icon):
    return f"""[Desktop Entry]
Type=Application
Name=kicad2ems
GenericName=EM simulation of KiCad boards
Comment=S-parameters, fields and antennas of KiCad boards: openEMS, mom25d and the line model
Exec="{gui_cmd}" %f
Icon={icon}
Terminal=false
Categories=Science;Engineering;Electronics;
StartupWMClass=kicad2ems
"""


# ---- the target: where it all goes ----------------------------------------------------------------------
class Target:
    """The folders of an install target: the venv (None: into the Python itself), the PATH folder for the
    commands, the launcher's, openEMS's build prefix and the record of what was made."""

    def __init__(self, name, venv_arg=None):
        self.name, data = name, data_home()
        self.bin = self.apps = self.icons = None
        self.editable, self.user = True, False
        self.start = start_menu(name == 'system') if WINDOWS else None
        if not WINDOWS:
            self.apps = os.path.join(data, 'applications')
            self.icons = os.path.join(data, 'icons', 'hicolor', 'scalable', 'apps')
        if name == 'checkout':
            self.venv = venv_arg or os.path.join(REPO, '.venv')
            self.oems_prefix = os.path.join(REPO, '.openems')
            self.record = os.path.join(self.venv, 'kicad2ems-installed')
        elif name == 'user':
            self.venv = os.path.join(data, 'kicad2ems', 'venv')
            self.bin = os.path.join(data, 'kicad2ems', 'bin') if WINDOWS else \
                os.path.join(os.path.expanduser('~'), '.local', 'bin')
            self.oems_prefix = os.path.join(data, 'kicad2ems', 'openems')
            self.record = os.path.join(data, 'kicad2ems', 'installed')
        elif name == 'system':
            root = os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'kicad2ems') if WINDOWS else \
                SYSROOT + '/opt/kicad2ems'
            self.venv = os.path.join(root, 'venv')
            self.bin = os.path.join(root, 'bin') if WINDOWS else SYSROOT + '/usr/local/bin'
            self.oems_prefix = os.path.join(root, 'openems')
            self.record = os.path.join(root, 'installed')
            self.editable = False
            if not WINDOWS:
                self.apps = SYSROOT + '/usr/share/applications'
                self.icons = SYSROOT + '/usr/share/icons/hicolor/scalable/apps'
        elif name == 'python':
            self.venv, self.user = None, True
            self.oems_prefix = os.path.join(data, 'kicad2ems', 'openems')
            self.record = os.path.join(data, 'kicad2ems', 'installed-python')
        else:
            raise Abort(f"unknown --target {name}: checkout, user, system or python", 2)

    @property
    def path_scope(self):
        return 'system' if self.name == 'system' else 'user'

    def python(self, py):
        """The Python kicad2ems runs on: the venv's, or py itself."""
        return venv_python(self.venv) if self.venv else which_python(py)

    def scripts(self, vpy):
        """Where the target's console scripts are: the venv's, or the user scheme's of vpy."""
        if self.venv:
            return venv_bin(self.venv)
        return py_out(vpy, "import sysconfig; print(sysconfig.get_path('scripts', "
                           "sysconfig.get_preferred_scheme('user')))")

    def remember(self, entry):
        """A file this install made (or a PATH entry: '@path\\t<scope>\\t<folder>'), for --uninstall."""
        os.makedirs(os.path.dirname(self.record), exist_ok=True)
        with open(self.record, 'a', encoding='utf-8') as fh:
            fh.write(entry + '\n')


def recorded(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return [line.rstrip('\n') for line in fh if line.strip()]
    except OSError:
        return []


# ---- update -----------------------------------------------------------------------------------------------
INSTALLED_FROM = r'''
import importlib.metadata as md, json, urllib.parse, urllib.request
for name in %r:
    try:
        text = md.distribution(name).read_text('direct_url.json')
    except md.PackageNotFoundError:
        continue
    d = json.loads(text) if text else {}
    url = d.get('url', '')
    if d.get('dir_info', {}).get('editable'):
        print('editable', name, urllib.request.url2pathname(urllib.parse.urlparse(url).path), sep='\t')
    elif 'vcs_info' in d:
        rev = d['vcs_info'].get('requested_revision')
        print('url', name, f"{d['vcs_info']['vcs']}+{url}" + (f"@{rev}" if rev else ''), sep='\t')
    elif url:
        print('url', name, url, sep='\t')
    else:
        print('index', name, '', sep='\t')
''' % (STACK,)


def core_dir(vpy, pkg):
    """The folder of an installed package with a compiled core ('' when it isn't installed)."""
    return py_out(vpy, f"import importlib.util as u, os; s = u.find_spec('{pkg}'); "
                       f"print(os.path.dirname(s.origin) if s else '')")


def core_file(vpy, folder):
    return os.path.join(folder, '_core' + py_out(vpy, "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"))


def core_stale(folder, built):
    """The compiled core is missing, or a source of it (core/*.cpp, *.h) is newer."""
    if not os.path.isfile(built):
        return True
    t = os.path.getmtime(built)
    return any(os.path.getmtime(f) > t for f in glob.glob(os.path.join(folder, 'core', '*.cpp')) +
               glob.glob(os.path.join(folder, 'core', '*.h')))


def find_install(py):
    """--update without --target: the install there is, in the order --target lists them."""
    for name in ('checkout', 'user', 'system', 'python'):
        t = Target(name)
        vpy = t.python(py)
        if vpy and os.path.isfile(vpy) and py_ok(vpy, 'import importlib.metadata as m; m.distribution("kicad2ems")'):
            return t
    return None


class Installer:
    def __init__(self, a):
        self.a = a
        self.py = which_python(a.python)
        self.failed = False

    def pip(self, *args, check=True, quiet=False, env=None):
        cmd = [self.vpy, '-m', 'pip', 'install', *(['--user'] if self.t.user else []), *(['-q'] if quiet else []), *args]
        ok = run(cmd, env={**os.environ, **env} if env else None)
        if check and not ok:
            raise Abort(f"pip failed: {' '.join(cmd)}")
        return ok

    def pip_uninstall(self, *names):
        run([self.vpy, '-m', 'pip', 'uninstall', '-y', '-q', *names], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def has_dist(self, *names):
        return any(py_ok(self.vpy, f'import importlib.metadata as m; m.distribution("{n}")') for n in names)

    # ---- the target ----
    def pick_target(self):
        a = self.a
        if a.target:
            return Target(a.target, a.venv)
        if a.venv:
            return Target('checkout', a.venv)
        if a.update:
            t = find_install(a.python)
            if not t:
                raise Abort("no kicad2ems install to update: run the installer (python install.py)")
            return t
        if a.uninstall:
            raise Abort("--uninstall needs --target (checkout, user, system or python)", 2)
        bold("Where to install")
        d, py = data_home(), a.python
        if WINDOWS:
            opts = ["checkout  a venv in this checkout (.venv); run it from here or the Start-menu entry",
                    f"user      for you: a venv in {os.path.join(d, 'kicad2ems')}, kicad2ems and kicad2ems-gui on "
                    f"your PATH",
                    "system    for every user: a venv in Program Files, the commands on the PATH (an Administrator "
                    "prompt)",
                    f"python    into {py} itself (pip install --user, no venv)"]
        else:
            opts = ["checkout  a venv in this checkout (.venv); run it from here or the launcher entry",
                    f"user      for you: a venv in {d}/kicad2ems, kicad2ems and kicad2ems-gui on your PATH (~/.local/bin)",
                    "system    for every user: a venv in /opt/kicad2ems, commands in /usr/local/bin (runs sudo)",
                    f"python    into {py} itself (pip install --user, no venv)"]
        return Target(first_word(choose("kicad2ems goes:", 1, opts)), a.venv)

    def need_privileges(self):
        """The system target needs root (Linux, macOS: the installer runs itself again under sudo) or an
        Administrator prompt (Windows)."""
        if self.t.name != 'system' or SYSROOT or is_admin():
            return
        if WINDOWS:
            raise Abort("the system target writes to Program Files: run this from an Administrator prompt "
                        "(or choose --target user)")
        if not shutil.which('sudo'):
            raise Abort("the system target needs root: run this as root (or choose --target user)")
        note("the system target needs root: running the installer again under sudo")
        args = [a for a in sys.argv[1:]]
        if '--target' not in args and not any(x.startswith('--target=') for x in args):
            args += ['--target', 'system']
        if YES and '--yes' not in args and '-y' not in args:
            args.append('--yes')
        os.execvp('sudo', ['sudo', '--preserve-env=HOME,PATH,XDG_DATA_HOME,XDG_CONFIG_HOME,PYTHON',
                           sys.executable, os.path.abspath(__file__), *args])

    # ---- the steps ----
    def main(self):
        a = self.a
        if a.update and (a.uninstall or a.openems or a.qucsator):
            raise Abort("--update changes neither openEMS nor qucsator, and doesn't go with --uninstall: "
                        "--target and --venv only", 2)
        self.t = self.pick_target()
        if a.update:
            return self.update()
        if a.uninstall:
            return self.uninstall()
        self.need_privileges()
        self.check_python()
        self.choose_openems()
        self.make_venv()
        self.choose_parts()
        self.choose_gpu()
        self.choose_qucsator()
        self.install()
        self.link_openems()
        self.build_openems()
        self.write_site()
        self.commands()
        self.launcher()
        self.make_examples()
        self.check()
        self.done()
        return 0

    def check_python(self):
        bold("Python")
        if not self.py:
            raise Abort(f"  {self.a.python} not found (--python to name another)")
        if not py_ok(self.py, 'import sys; sys.exit(sys.version_info < (3, 10))'):
            raise Abort(f"  kicad2ems needs Python 3.10 or newer; {self.py} is "
                        f"{py_out(self.py, 'import sys; print(sys.version.split()[0])') or '?'}")
        self.pyver = py_out(self.py, 'import sys; print("%d.%d" % sys.version_info[:2])')
        note(f"Python {py_out(self.py, 'import sys; print(sys.version.split()[0])')} ({self.py})")
        if self.t.name == 'python' and py_ok(self.py, 'import sys, sysconfig, os; sys.exit(not os.path.exists('
                                                      'os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED")))'):
            raise Abort(f"  {self.py} is managed by your distribution (EXTERNALLY-MANAGED): pip may not install into "
                        f"it.\n  Choose --target user instead (a venv of its own, the commands on your PATH).")

    # openEMS first: one this Python has is only seen from a venv with --system-site-packages; one in another venv
    # is linked in (its CSXCAD and openEMS packages), which needs the same Python version; on Windows the unpacked
    # release brings wheels for some Python versions and its DLLs (the openems_dir setting)
    def openems_candidates(self):
        home = os.path.expanduser('~')
        if WINDOWS:
            return [os.path.join(home, 'venvs', 'openems', 'Scripts', 'python.exe'),
                    os.path.join(REPO, '.venv', 'Scripts', 'python.exe')]
        return ['/opt/openems/venv/bin/python', f'{home}/opt/openEMS/venv/bin/python', f'{home}/venvs/openems/bin/python',
                os.path.join(REPO, '.venv', 'bin', 'python')]

    def windows_openems_dirs(self):
        """Unpacked openEMS releases for Windows (openEMS.dll there or in bin)."""
        out = []
        for d in (os.environ.get('OPENEMS_INSTALL_PATH'), r'C:\opt\openEMS', r'C:\openEMS',
                  os.path.join(os.path.expanduser('~'), 'openEMS')):
            if d and (os.path.isfile(os.path.join(d, 'openEMS.dll')) or os.path.isfile(os.path.join(d, 'bin', 'openEMS.dll'))):
                out.append(d)
        return list(dict.fromkeys(out))

    def choose_openems(self):
        bold("openEMS (3D FDTD; not on PyPI)")
        self.oems, self.ext_py, self.oems_dir = 'none', None, None
        sys_oems = py_ok(self.py, 'import CSXCAD, openEMS')
        mine = os.path.realpath(self.py)
        found = next((c for c in self.openems_candidates() if os.path.isfile(c) and os.path.realpath(c) != mine
                      and py_ok(c, 'import CSXCAD, openEMS')), None)
        win_dirs = self.windows_openems_dirs() if WINDOWS else []
        arg = self.a.openems
        if arg in ('build', 'none'):
            if arg == 'build' and WINDOWS:
                raise Abort("--openems build: openEMS builds on Linux and macOS here; on Windows unpack its release "
                            "(docs.openems.de) and give its folder: --openems C:\\opt\\openEMS", 2)
            self.oems = arg
        elif arg:
            if WINDOWS and os.path.isdir(arg) and not os.path.isfile(venv_python(arg)):
                self.oems, self.oems_dir = 'folder', os.path.abspath(arg)
            else:
                self.oems, self.ext_py = 'elsewhere', arg
        else:
            opts = []
            if sys_oems:
                where = py_out(self.py, 'import openEMS, os; print(os.path.dirname(openEMS.__file__))')
                opts.append(f"this     {self.py} has it: {where}")
            if WINDOWS:
                opts.append("folder   the unpacked openEMS for Windows: its Python wheels and DLLs"
                            + (f" (found: {win_dirs[0]})" if win_dirs else ''))
            opts.append("elsewhere  another venv's openEMS, linked in" + (f" (found: {found})" if found else ''))
            if not WINDOWS:
                opts.append(f"build    a copy built with tools/build_openems.sh into {self.t.oems_prefix} (10-20 min)")
            opts.append("none     mom25d and the line model only")
            default = len(opts)
            if sys_oems or found or win_dirs:
                default = 1
                if not sys_oems and WINDOWS and found and not win_dirs:
                    default = 2
            self.oems = first_word(choose("openEMS for kicad2ems:", default, opts))
            if self.oems == 'folder':
                self.oems_dir = askpath("the openEMS folder (with openEMS.dll):", win_dirs[0] if win_dirs else '')
        if self.oems == 'folder':
            self.oems_dir = os.path.abspath(self.oems_dir) if self.oems_dir else None
            if not self.oems_dir or not os.path.isdir(self.oems_dir):
                note(f"no folder {self.oems_dir or '(none given)'}: going on without openEMS")
                self.oems = 'none'
            elif not self.openems_wheels(self.pyver):
                note(f"{self.oems_dir} has no openEMS wheels for Python {self.pyver} (python\\*cp{self.pyver.replace('.', '')}*"
                     f".whl): kicad2ems would run on {self.pyver}. Run the installer again with --python naming a "
                     f"Python it has wheels for, or install CSXCAD and openEMS into that Python yourself.")
                self.oems = 'none'
        if self.oems == 'elsewhere':
            if not self.ext_py:
                self.ext_py = askpath("the Python of the venv openEMS is installed in:", found or '')
            if self.ext_py and os.path.isdir(self.ext_py):
                self.ext_py = venv_python(self.ext_py)                                    # a venv's folder
            if not self.ext_py or not py_ok(self.ext_py, 'import CSXCAD, openEMS'):
                note(f"no openEMS in {self.ext_py or '(none given)'} (import CSXCAD, openEMS failed): going on without")
                self.oems = 'none'
            else:
                ext_ver = py_out(self.ext_py, 'import sys; print("%d.%d" % sys.version_info[:2])')
                if ext_ver != self.pyver:
                    note(f"that openEMS is built for Python {ext_ver}, kicad2ems would run on {self.pyver}: its compiled")
                    note(f"modules can't be shared. Run the installer again with --python naming Python {ext_ver}, or "
                         f"build openEMS for {self.pyver}.")
                    self.oems = 'none'
                else:
                    note(f"linking the openEMS of {self.ext_py} "
                         f"({py_out(self.ext_py, 'import openEMS, os; print(os.path.dirname(openEMS.__file__))')})")
        if self.oems == 'this' and not sys_oems:
            self.oems = 'none'

    def openems_wheels(self, pyver):
        """The CSXCAD and openEMS wheels for Python pyver in an unpacked openEMS for Windows (its python folder)."""
        tag = 'cp' + pyver.replace('.', '')
        out = []
        for pkg in ('CSXCAD', 'openEMS'):
            hits = sorted(glob.glob(os.path.join(self.oems_dir, 'python', f'{pkg}-*-{tag}-*.whl')) +
                          glob.glob(os.path.join(self.oems_dir, 'python', '*', f'{pkg}-*-{tag}-*.whl')))
            if not hits:
                return []
            out.append(hits[-1])
        return out

    def make_venv(self):
        t = self.t
        if t.venv:
            bold(f"Virtual environment: {t.venv}")
            vpy = venv_python(t.venv)
            if os.path.isfile(vpy) and ask("it exists: make it anew (otherwise it is kept and added to)?", 'n'):
                shutil.rmtree(t.venv)
            if not os.path.isfile(vpy):
                os.makedirs(os.path.dirname(os.path.abspath(t.venv)), exist_ok=True)
                site = ['--system-site-packages'] if self.oems == 'this' else []
                run([self.py, '-m', 'venv', *site, t.venv], check=True)
                note(f"made with Python {py_out(vpy, 'import sys; print(sys.version.split()[0])')}"
                     + (", seeing the system packages (openEMS)" if site else ''))
            self.vpy = vpy
        else:
            self.vpy = self.py
        if t.venv:
            self.pip('--upgrade', 'pip', 'setuptools', 'wheel', quiet=True)

    def has_compiler(self):
        """A C++ compiler for the cores: g++ / c++; on Windows Visual Studio's C++ tools (emsdev.build finds them),
        cl on the PATH, or MinGW-w64's g++."""
        if WINDOWS:
            base = os.environ.get('ProgramFiles(x86)') or os.environ.get('ProgramFiles') or ''
            vswhere = os.path.join(base, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
            if os.path.isfile(vswhere):
                out = subprocess.run([vswhere, '-products', '*', '-requires',
                                      'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property',
                                      'installationPath'], capture_output=True, text=True).stdout
                if out.strip():
                    return 'Visual Studio'
            if shutil.which('cl'):
                return 'cl'
        for c in ('g++', 'c++'):
            if shutil.which(c):
                return c
        return None

    def choose_parts(self):
        bold("Optional parts")
        self.extras = []
        if ask("GUI (kicad2ems-gui, from github.com/lowprocess/kicad2ems-gui: PySide6, pyqtgraph, PyOpenGL)?", 'y'):
            self.extras.append('gui')
        if ask("frequency-domain field dumps and far fields (h5py)?", 'y'):
            self.extras.append('dumps')
        if ask("line model with scikit-rf (--fast skrf; qucsator is a separate program)?", 'y'):
            self.extras.append('fast')
        self.mom = ask("mom25d (2.5D MoM: planar circuits and antennas, no openEMS needed; from "
                       "github.com/lowprocess/mom25d)?", 'y')
        if self.mom:
            self.extras.append('mom')
        self.gfdtd = ask("gfdtd (3D FDTD on the GPU or a CPU core, openEMS's model; from github.com/lowprocess/gfdtd)?", 'y')
        if self.gfdtd:
            self.extras.append('gfdtd')
        if ask("dfsynth (filter synthesis: the GUI's Filter page; from github.com/lowprocess/dfsynth)?", 'y'):
            self.extras.append('filter')
        self.core = False
        if self.mom or self.gfdtd:
            cc = self.has_compiler()
            if cc:
                self.core = ask(f"build the compiled cores (C++ / OpenMP with {cc}: mom25d ~5x, gfdtd ~15x faster on "
                                f"the CPU)?", 'y')
                if self.core:
                    self.extras.append('core')
            else:
                note("no C++ compiler (" + ("Visual Studio 2022's C++ tools, or MinGW-w64's g++" if WINDOWS else 'g++')
                     + "): the compiled cores are skipped (NumPy kernels: mom25d ~5x, gfdtd ~15x slower)")
        if ask("test tools (pytest)?", 'n'):
            self.extras.append('test')
        if self.oems != 'none':
            self.extras.append('openems')        # openEMS's solver (kicad2ems-fdtd[openems]; it brings h5py)

    # ---- the GPU: CuPy for mom25d's direct solver and gfdtd -----------------------------------------------
    def detect_gpu(self):
        """(what was found, the default option index, the ROCm major version or None, an AMD card's name)."""
        what, default, rocm, amd = 'no GPU stack found', 0, None, None
        if shutil.which('nvidia-smi'):
            r = subprocess.run(['nvidia-smi'], capture_output=True, text=True)
            if r.returncode == 0:
                m = re.search(r'CUDA Version:\s*(\d+)\.', r.stdout)
                cuda = m.group(1) if m else None
                name = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], capture_output=True,
                                      text=True).stdout.strip().splitlines()
                what = f"NVIDIA: {name[0] if name else 'a CUDA GPU'}, driver CUDA {cuda or '?'}"
                default = {'12': 2, '13': 3}.get(cuda, 0)
        if WINDOWS:
            cards = powershell("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }") or ''
            amd = next((c.strip() for c in cards.splitlines() if re.search(r'\b(AMD|Radeon)\b', c)), None)
            return what, default, None, amd
        if os.path.isfile('/opt/rocm/.info/version'):
            rocm = open('/opt/rocm/.info/version', encoding='utf-8').read().split('.')[0].strip()
        elif shutil.which('rpm') and run(['rpm', '-q', 'rocm-hip'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
            rocm = subprocess.run(['rpm', '-q', '--qf', '%{VERSION}', 'rocm-hip'], capture_output=True,
                                  text=True).stdout.split('.')[0]
        elif shutil.which('dpkg-query') and run(['dpkg-query', '-W', '-f=${Version}', 'rocm-hip-runtime'],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
            rocm = subprocess.run(['dpkg-query', '-W', '-f=${Version}', 'rocm-hip-runtime'], capture_output=True,
                                  text=True).stdout.split('.')[0]
        elif shutil.which('hipconfig'):
            rocm = subprocess.run(['hipconfig', '--version'], capture_output=True, text=True).stdout.split('.')[0]
        rocm = (rocm or '').strip() or None
        if rocm:
            card = None
            if shutil.which('rocminfo'):        # it lists the CPU as an agent too: the first GPU's marketing name
                name = None
                for line in subprocess.run(['rocminfo'], capture_output=True, text=True).stdout.splitlines():
                    k, _, v = line.partition(':')
                    if k.strip() == 'Marketing Name':
                        name = v.strip()
                    elif k.strip() == 'Device Type' and 'GPU' in v:
                        card = name
                        break
            what = f"AMD: {card or 'a ROCm GPU'}, ROCm {rocm}"
            if rocm == '7':
                default = 1
        return what, default, rocm, amd

    def choose_gpu(self):
        bold("GPU (CuPy: mom25d's LU in complex64, gfdtd's engine)")
        what, default, rocm, amd = self.detect_gpu()
        note(f"found: {what}")
        if rocm and rocm != '7':
            note("CuPy 14 has a ROCm wheel for ROCm 7 only (cupy-rocm-7-0); older ROCm: none here")
        if amd:
            note(f"{amd}: CuPy has no ROCm wheel for Windows, so an AMD card is not used here (Linux with ROCm 7 is)")
        opts = ["cupy-rocm-7-0   AMD, ROCm 7", "cupy-cuda12x    NVIDIA, CUDA 12", "cupy-cuda13x    NVIDIA, CUDA 13",
                "none            mom25d on the CPU"]
        if WINDOWS:
            opts, default = opts[1:], max(default - 1, 0)
        self.cupy = first_word(choose("CuPy wheel:", default or len(opts), opts))
        self.cupy = None if self.cupy == 'none' else self.cupy
        self.drop_cupy = False
        if not self.cupy and self.t.venv and os.path.isfile(self.vpy) and self.has_dist(*CUPY_WHEELS):
            self.drop_cupy = ask("the venv has a CuPy wheel already: remove it (mom25d on the CPU)?", 'n')

    # ---- qucsator: the line model's circuit solver, a separate program --------------------------------------
    def find_qucsator(self):
        exe = shutil.which('qucsator_rf') or shutil.which('qucsator')
        if exe or not WINDOWS:
            return exe
        for base in dict.fromkeys(filter(None, (os.environ.get('ProgramFiles'), os.environ.get('ProgramW6432'),
                                                os.environ.get('ProgramFiles(x86)')))):
            for name in ('qucsator_rf.exe', 'qucsator.exe'):
                hits = sorted(glob.glob(os.path.join(base, 'Qucs*', 'bin', name)), reverse=True)
                if hits:
                    return hits[0]
        return None

    def choose_qucsator(self):
        bold("qucsator (the line model's circuit solver; scikit-rf stands in without it)")
        arg, self.qucs = self.a.qucsator, None
        if arg == 'none':
            return
        if arg:
            self.qucs = arg
        else:
            self.qucs = self.find_qucsator()
            if self.qucs:
                note(f"found: {self.qucs}")
            else:
                deps = None
                if shutil.which('dnf'):
                    deps = ['sudo', 'dnf', 'install', 'qucs-s']
                elif shutil.which('apt-get'):
                    deps = ['sudo', 'apt', 'install', 'qucs-s']
                if deps and is_admin():
                    deps = deps[1:]
                opts = ["path     give the path of a qucsator_rf / qucsator you have"]
                if deps:
                    opts.append(f"install  from your distribution: {' '.join(deps)}")
                elif WINDOWS:
                    note("Qucs-S for Windows (ra3xdh.github.io) brings qucsator_rf.exe in its bin folder")
                opts.append("none     scikit-rf solves the line model instead")
                pick = first_word(choose("qucsator:", len(opts), opts))
                if pick == 'path':
                    self.qucs = askpath("qucsator's path:") or None
                elif pick == 'install':
                    run(deps)
                    self.qucs = self.find_qucsator()
        if self.qucs and not (os.path.isfile(self.qucs) and os.access(self.qucs, os.X_OK)):
            note(f"{self.qucs} is not an executable: going on without qucsator")
            self.qucs = None

    # ---- install ----
    def install(self):
        t = self.t
        spec = REPO + (f"[{','.join(self.extras)}]" if self.extras else '')
        how = " (editable: this checkout's code is what runs)" if t.editable else ' (a copy of this checkout)'
        bold(f"Installing kicad2ems{' [' + ','.join(self.extras) + ']' if self.extras else ''}{how}")
        # mom25d's mesher: PyPI has no triangle >= 20230923 for every Python (none for 3.14): build it from its
        # repository
        if self.mom and not py_ok(self.vpy, 'import triangle') and not self.pip('triangle>=20230923', check=False,
                                                                            quiet=True):
            note(f"no triangle wheel for Python {self.pyver}: building it from github.com/drufat/triangle (needs a C "
                 f"compiler)")
            self.pip('--no-cache-dir', f"triangle @ git+https://github.com/drufat/triangle.git@{TRIANGLE_COMMIT}",
                     env=None if WINDOWS else {'CFLAGS': TRIANGLE_CFLAGS})    # MSVC: /fp:precise doesn't fuse
        if self.oems == 'folder':
            bold(f"openEMS's Python packages from {self.oems_dir}")
            self.pip(*self.openems_wheels(self.pyver))
        if t.editable:
            # compat: the checkout's path in a .pth inside the environment, ahead of the user site on sys.path. The
            # default editable finder runs after the path search, so a kicad2ems installed in the user site (visible
            # through --system-site-packages) was imported instead of this checkout
            self.pip('-e', spec, '--config-settings', 'editable_mode=compat')
        else:
            self.pip(spec)
        where = py_out(self.vpy, 'import kicad2ems, os; print(os.path.dirname(os.path.dirname(kicad2ems.__file__)))')
        if t.editable and os.path.normcase(os.path.realpath(where or '.')) != os.path.normcase(os.path.realpath(os.path.join(REPO, 'src'))):
            note(f"WARNING: kicad2ems is imported from {where}, not this checkout: remove that copy")
            note(f"(e.g. {self.py} -m pip uninstall kicad2ems, outside the venv) and run the installer again")
        if t.user:
            self.sitedir = py_out(self.vpy, 'import site; print(site.getusersitepackages())')
            self.sitecfg = py_out(self.vpy, 'import site, os; print(os.path.join(site.getuserbase(), "kicad2ems-site.toml"))')
        else:
            self.sitedir = py_out(self.vpy, 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
            self.sitecfg = py_out(self.vpy, 'import sys, os; print(os.path.join(sys.prefix, "kicad2ems-site.toml"))')
        if self.cupy:
            bold(f"Installing {self.cupy}")
            others = [w for w in CUPY_WHEELS if w != self.cupy]  # one CuPy wheel at a time: another would shadow it
            self.pip_uninstall(*others)
            self.pip(f"{self.cupy}>=14")
        elif self.drop_cupy:
            self.pip_uninstall(*CUPY_WHEELS)
        for pkg, on in (('mom25d', self.mom), ('gfdtd', self.gfdtd)):
            if self.core and on:
                bold(f"Building {pkg}'s compiled core")
                if not run([self.vpy, '-m', f'{pkg}.core.build']):
                    note(f"the core did not build: {pkg} runs on NumPy (see the error above)")

    # ---- openEMS: linked in from another venv, or built from source ---------------------------------------
    def link_openems(self):
        if self.oems != 'elsewhere':
            return
        bold(f"Linking openEMS from {self.ext_py}")
        link = os.path.join(self.t.venv, 'kicad2ems-openems') if self.t.venv else \
            os.path.join(data_home(), 'kicad2ems', 'openems-link')
        os.makedirs(link, exist_ok=True)
        for pkg in ('CSXCAD', 'openEMS'):
            src = py_out(self.ext_py, f"import {pkg}, os; print(os.path.dirname({pkg}.__file__))")
            link_dir(src, os.path.join(link, pkg))
        os.makedirs(self.sitedir, exist_ok=True)
        pth = os.path.join(self.sitedir, 'kicad2ems-openems.pth')
        with open(pth, 'w', encoding='utf-8') as fh:
            fh.write(link + '\n')
        self.t.remember(pth)
        if py_ok(self.vpy, 'import CSXCAD, openEMS'):
            note(f"openEMS imports in kicad2ems's Python ({link})")
        else:
            note(f"WARNING: linked, but import CSXCAD, openEMS fails in {self.vpy} (see: {self.vpy} -c 'import openEMS')")

    def build_openems(self):
        if self.oems != 'build':
            return
        prefix = self.t.oems_prefix
        bold(f"Building openEMS into {prefix}")
        missing = [x for x in ('cmake', 'git', 'make', 'g++') if not shutil.which(x)]
        deps = None
        if shutil.which('dnf'):
            deps = ['dnf', 'install', 'gcc-c++', 'cmake', 'git', 'hdf5-devel', 'vtk-devel', 'CGAL-devel',
                    'tinyxml-devel', 'boost-devel', 'python3-devel']
        elif shutil.which('apt-get'):
            deps = ['apt', 'install', 'build-essential', 'cmake', 'git', 'libhdf5-dev', 'libvtk9-dev', 'libcgal-dev',
                    'libtinyxml-dev', 'libboost-all-dev', 'python3-dev']
        if missing:
            note(f"missing: {' '.join(missing)}")
        if deps:
            deps = deps if is_admin() else ['sudo', *deps]
            note(f"build dependencies: {' '.join(deps)}")
            if ask("install them now" + ('' if is_admin() else ' (runs sudo)') + "?", 'n'):
                run(deps)
        else:
            note("install a C++ compiler, cmake, git and the HDF5, VTK, CGAL, TinyXML and Boost headers first")
        self.pip('cython', 'numpy', 'h5py', 'setuptools', quiet=True)
        if not ask(f"build openEMS now (sources in {prefix}/src)?", 'y'):
            return
        env = {**os.environ, 'SRC': os.path.join(prefix, 'src')}
        if self.t.venv:
            env.update(VIRTUAL_ENV=self.t.venv, PATH=venv_bin(self.t.venv) + os.pathsep + env.get('PATH', ''))
        if run(['bash', os.path.join(REPO, 'tools', 'build_openems.sh'), prefix], env=env):
            # the venv's activate script finds the libraries; the GUI and kicad2ems started from it too
            act = os.path.join(venv_bin(self.t.venv), 'activate') if self.t.venv else None
            if act and 'kicad2ems: openEMS libraries' not in open(act, encoding='utf-8').read():
                with open(act, 'a', encoding='utf-8') as fh:
                    fh.write(f'\n# kicad2ems: openEMS libraries (install.py)\nexport LD_LIBRARY_PATH="{prefix}/lib'
                             '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n')
            os.environ['LD_LIBRARY_PATH'] = os.pathsep.join(filter(None, (os.path.join(prefix, 'lib'),
                                                                          os.environ.get('LD_LIBRARY_PATH'))))
            self.oems = 'built'
        else:
            note("the openEMS build failed (see above): kicad2ems is installed without it; fix and run the installer "
                 "again")

    # ---- where the external programs are: kicad2ems-site.toml (src/kicad2ems/sitecfg.py) ----------------------
    def write_site(self):
        keys = {}
        if self.qucs:
            keys['qucsator'] = self.qucs
        if self.oems == 'folder':
            keys['openems_dir'] = self.oems_dir
        if not keys:
            return
        os.makedirs(os.path.dirname(self.sitecfg), exist_ok=True)
        with open(self.sitecfg, 'w', encoding='utf-8') as fh:
            fh.write("# written by install.py: where kicad2ems finds its external programs (src/kicad2ems/sitecfg.py)\n")
            for k, v in keys.items():
                fh.write(f"{k} = {json.dumps(v)}\n")                # a TOML basic string (backslashes escaped)
        for k, v in keys.items():
            note(f"{k}: {v} (in {self.sitecfg})")
        self.t.remember(self.sitecfg)

    # ---- commands on the PATH, the launcher entry, a desktop icon -------------------------------------------
    def commands(self):
        t = self.t
        if not t.bin:
            return
        bold(f"Commands in {t.bin}")
        os.makedirs(t.bin, exist_ok=True)
        for cmd in ('kicad2ems', 'kicad2ems-gui'):
            src = script(venv_bin(t.venv), cmd)
            if not os.path.isfile(src):
                continue
            if WINDOWS:                                 # a .cmd that runs the venv's own launcher: no activation
                dst = os.path.join(t.bin, cmd + '.cmd')
                with open(dst, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(shim_text(src))
            else:
                dst = os.path.join(t.bin, cmd)
                if os.path.lexists(dst):
                    os.remove(dst)
                os.symlink(src, dst)                    # the venv's own script: no activation needed
            t.remember(dst)
            note(f"{dst} -> {src}")
        if on_path(t.bin):
            return
        if WINDOWS:
            if ask(f"put {t.bin} on {'every user' if t.path_scope == 'system' else 'your'}'s PATH?", 'y') and \
                    windows_path(t.bin, t.path_scope):
                t.remember(f"@path\t{t.path_scope}\t{t.bin}")
                note(f"{t.bin} is on the PATH from the next terminal you open")
            else:
                note(f"{t.bin} is not on your PATH: add it to run kicad2ems from any folder")
        else:
            note(f'{t.bin} is not on your PATH: add it (export PATH="{t.bin}:$PATH")')

    def gui_icon(self, ext):
        return py_out(self.vpy, f"import kicad2ems_gui, os; print(os.path.join(os.path.dirname(kicad2ems_gui.__file__),"
                                f" 'kicad2ems.{ext}'))")

    def launcher(self):
        t = self.t
        if 'gui' not in self.extras:
            return
        scripts = t.scripts(self.vpy)
        gui_cmd = script(scripts, 'kicad2ems-gui') if scripts else ''
        if not os.path.isfile(gui_cmd):
            return
        if WINDOWS:
            return self.start_menu_entry()
        if not LINUX:
            note(f"the GUI: {gui_cmd} (no launcher entry is made on this system)")
            return
        bold("The GUI in the application launcher")
        if not ask(f"a launcher entry for kicad2ems-gui (runs {gui_cmd})?", 'y'):
            return
        os.makedirs(t.apps, exist_ok=True)
        os.makedirs(t.icons, exist_ok=True)
        icon = os.path.join(t.icons, 'kicad2ems.svg')
        shutil.copyfile(self.gui_icon('svg'), icon)                 # the icon kicad2ems-gui installs
        t.remember(icon)
        entry = desktop_entry(gui_cmd, icon)
        desktop_file = os.path.join(t.apps, 'kicad2ems.desktop')
        with open(desktop_file, 'w', encoding='utf-8') as fh:
            fh.write(entry)
        t.remember(desktop_file)
        if shutil.which('update-desktop-database'):
            run(['update-desktop-database', '-q', t.apps], stderr=subprocess.DEVNULL)
        note(desktop_file)
        desk = desktop_folder()
        if desk and ask(f"an icon on the desktop too ({desk})?", 'n'):
            p = os.path.join(desk, 'kicad2ems.desktop')
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write(entry)
            os.chmod(p, 0o755)
            give_to_user(p)
            if shutil.which('gio'):
                run(['gio', 'set', p, 'metadata::trusted', 'true'], stderr=subprocess.DEVNULL)
            t.remember(p)
            note(p)

    def start_menu_entry(self):
        """Windows: a Start-menu shortcut (and a desktop one) that runs the GUI with pythonw, so no console window
        opens with it."""
        t = self.t
        bold("The GUI in the Start menu")
        pyw = os.path.join(os.path.dirname(self.vpy), 'pythonw.exe')
        target, args = (pyw, '-m kicad2ems_gui') if os.path.isfile(pyw) else \
            (script(t.scripts(self.vpy), 'kicad2ems-gui'), '')
        if not ask(f"a Start-menu entry for kicad2ems-gui (runs {target} {args})?", 'y'):
            return
        icon = self.gui_icon('ico')
        icon = icon if icon and os.path.isfile(icon) else ''
        os.makedirs(t.start, exist_ok=True)
        lnk = os.path.join(t.start, 'kicad2ems.lnk')
        if write_shortcut(lnk, target, args, icon):
            t.remember(lnk)
            note(lnk)
        else:
            note("the shortcut could not be made (PowerShell failed): start the GUI with kicad2ems-gui")
            return
        desk = desktop_folder()
        if desk and ask(f"a shortcut on the desktop too ({desk})?", 'n'):
            p = os.path.join(desk, 'kicad2ems.lnk')
            if write_shortcut(p, target, args, icon):
                t.remember(p)
                note(p)

    # ---- the example boards, the check, the end --------------------------------------------------------------
    def make_examples(self):
        # most examples' boards are not in the repository (rf_*, antenna_*, demo_*: .gitignore) but made by their
        # generators (examples/make_*.py, which import kicad2ems): made here, next to their configs, and again on
        # each run (a generator may have changed), so `kicad2ems examples/<name>.toml` works in this checkout
        bold("Example boards (examples/make_boards.py)")
        if not run([self.vpy, os.path.join('examples', 'make_boards.py'), '--quiet'], cwd=REPO):
            note("WARNING: some example boards could not be made (above): python examples/make_boards.py tries again")

    def k2e(self):
        return script(self.t.scripts(self.vpy) or os.path.dirname(self.vpy), 'kicad2ems')

    def check(self):
        bold("Check")
        env = dict(os.environ)
        if self.t.venv:
            env.update(VIRTUAL_ENV=self.t.venv, PATH=venv_bin(self.t.venv) + os.pathsep + env.get('PATH', ''))
        run([self.k2e(), '--check'], env=env)

    def done(self):
        if self.cupy:
            run([self.vpy, '-m', 'emsdev'])
        t = self.t
        print(f"\nDone ({t.name} install). Use it with:")
        if t.name == 'checkout':
            rel = os.path.relpath(t.venv, REPO) if os.path.normcase(t.venv).startswith(os.path.normcase(REPO)) else t.venv
            print(f"  {os.path.join(rel, 'Scripts', 'activate')}" if WINDOWS else f"  source {rel}/bin/activate")
            print("  kicad2ems examples/demo_transition.toml        # or: kicad2ems-gui")
        elif t.name in ('user', 'system'):
            where = 'the Start menu' if WINDOWS else 'the launcher'
            print(f"  kicad2ems examples/demo_transition.toml        # or: kicad2ems-gui (also in {where})")
        else:
            print(f"  {self.k2e()} examples/demo_transition.toml")
        print(f"Remove it again with: python install.py --uninstall --target {t.name}")

    # ---- update: only the code, in an install that exists ---------------------------------------------------
    def update(self):
        t = self.t
        self.vpy = t.python(self.a.python)
        if not self.vpy or not os.path.isfile(self.vpy) or not py_ok(
                self.vpy, 'import importlib.metadata as m; m.distribution("kicad2ems")'):
            raise Abort(f"no kicad2ems in the {t.name} install ({self.vpy}): run the installer (python install.py)")
        self.need_privileges()
        bold(f"Updating the {t.name} install ({self.vpy})")
        had_core = []
        for pkg in ('mom25d', 'gfdtd'):
            d = core_dir(self.vpy, pkg)
            if d and os.path.isfile(core_file(self.vpy, d)):
                had_core.append(pkg)
        # no deps: a requirement that is new shows in pip check below. Editable packages build without isolation:
        # nothing to download, and it doesn't need the network
        ok = []
        if t.editable:
            note("kicad2ems: this checkout (editable)")
            ok.append(self.pip('-q', '--no-deps', '--no-build-isolation', '-e', REPO, '--config-settings',
                               'editable_mode=compat', check=False))
        else:
            note("kicad2ems: a copy of this checkout")
            ok.append(self.pip('-q', '--no-deps', '--force-reinstall', REPO, check=False))
        # the others as they were installed: a checkout stays that checkout, a URL is fetched again
        for line in py_out(self.vpy, INSTALLED_FROM).splitlines():
            kind, name, spec = (line.split('\t') + ['', ''])[:3]
            if kind == 'editable':
                note(f"{name}: its checkout {spec} (editable)")
                ok.append(self.pip('-q', '--no-deps', '--no-build-isolation', '-e', spec, '--config-settings',
                                   'editable_mode=compat', check=False))
            elif kind == 'url':
                note(f"{name}: {spec}")
                ok.append(self.pip('-q', '--no-deps', '--force-reinstall', f"{name} @ {spec}", check=False))
            elif kind == 'index':
                note(f"{name}: the package index")
                ok.append(self.pip('-q', '--no-deps', '--upgrade', name, check=False))
        # a compiled core that was built is built again where its source is newer (a package installed anew is)
        for pkg in had_core:
            d = core_dir(self.vpy, pkg)
            if core_stale(d, core_file(self.vpy, d)):
                bold(f"Building {pkg}'s compiled core")
                if not run([self.vpy, '-m', f'{pkg}.core.build']):
                    note(f"WARNING: the core did not build: {pkg} runs on NumPy")
                    ok.append(False)
        bold("Requirements")
        if not run([self.vpy, '-m', 'pip', 'check']):
            note("above: what is missing or at the wrong version: the installer (python install.py) installs it")
        self.make_examples()
        lib = os.path.join(t.oems_prefix, 'lib')
        if not WINDOWS and os.path.isdir(lib):
            os.environ['LD_LIBRARY_PATH'] = os.pathsep.join(filter(None, (lib, os.environ.get('LD_LIBRARY_PATH'))))
        self.check()
        if all(ok):
            print("\nUpdated.")
            return 0
        print("\nUpdated, with the failures above.")
        return 1

    # ---- uninstall ----------------------------------------------------------------------------------------
    def uninstall(self):
        t = self.t
        self.need_privileges()
        bold(f"Removing the {t.name} install")
        entries = recorded(t.record)
        if not entries:
            note(f"nothing recorded for the {t.name} target ({t.record})")
        for e in entries:
            if e.startswith('@path\t'):
                _, scope, folder = e.split('\t', 2)
                if WINDOWS and windows_path(folder, scope, add=False):
                    note(f"removed {folder} from the {scope} PATH")
            elif os.path.lexists(e):
                try:
                    unlink(e)
                    note(f"removed {e}")
                except OSError as err:
                    note(f"could not remove {e}: {err}")
        if os.path.isfile(t.record):
            os.remove(t.record)
        if t.name == 'python':
            py = which_python(self.a.python)
            if py and run([py, '-m', 'pip', 'uninstall', '-y', 'kicad2ems']):
                note(f"kicad2ems uninstalled from {py}'s user site")
        elif t.venv and os.path.isdir(t.venv) and ask(f"remove the venv {t.venv} too?", 'y'):
            shutil.rmtree(t.venv)
            note(f"removed {t.venv}")
        if t.apps and shutil.which('update-desktop-database'):
            run(['update-desktop-database', '-q', t.apps], stderr=subprocess.DEVNULL)
        return 0


def parse(argv):
    p = argparse.ArgumentParser(prog='install.py', description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)
    p.add_argument('-y', '--yes', action='store_true')
    p.add_argument('--python', default=os.environ.get('PYTHON') or sys.executable)
    p.add_argument('--venv')
    p.add_argument('--target')
    p.add_argument('--openems')
    p.add_argument('--qucsator')
    p.add_argument('--uninstall', action='store_true')
    p.add_argument('--update', action='store_true')
    p.add_argument('-h', '--help', action='store_true')
    a, rest = p.parse_known_args(argv)
    if rest:
        raise Abort(f"unknown option: {rest[0]} (--help)", 2)
    if a.venv:
        a.venv = os.path.abspath(os.path.expanduser(a.venv))
    return a


def main(argv=None):
    global YES
    if WINDOWS:
        os.system('')                                   # the console's escape sequences (bold) on
    try:
        a = parse(sys.argv[1:] if argv is None else argv)
        if a.help:
            print(__doc__)
            return 0
        YES = a.yes
        return Installer(a).main()
    except Abort as e:
        print(e)
        return e.code
    except KeyboardInterrupt:
        print("\nstopped")
        return 130


if __name__ == '__main__':
    sys.exit(main())
