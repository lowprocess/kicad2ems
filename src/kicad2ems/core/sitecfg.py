"""
Where this install's external programs are: the user's site.toml (the GUI's
Solvers and hardware window writes it:
~/.config/kicad2ems/site.toml, %APPDATA%\\kicad2ems\\site.toml on Windows), then
kicad2ems-site.toml in the environment (sys.prefix: the venv, or the user's site
for a --user install), as the installer (install.py) recorded them. A setting
given on the command line or in a config still comes first.

    qucsator = "/usr/bin/qucsator_rf"   # the line model's circuit solver
    openems_venv = "/opt/openems/venv"  # a venv with openEMS (kicad2ems-fdtd:
                                        # openems/venv.py)
    openems_dir = "C:/openEMS"          # Windows: the folder of openEMS's DLLs
                                        # (kicad2ems-fdtd)
    elmer = "/usr/local/bin"            # kicad2ems-fem: the folder of
                                        # ElmerSolver and ElmerGrid
    palace = "~/opt/palace/bin/palace"  # kicad2ems-fem: Palace's launcher (or
                                        # palace_rootfs: an image)

    sitecfg.get('qucsator') -> the path, or None
    sitecfg.set_user('qucsator', path)  # into the user's file (None / '':
                                        # removed)
"""
import os, site, sys

from .runs import load_toml

_cache = None


def paths():
    """The site files read, the first that sets a key wins: the user's (what the
    GUI set), then the environment's (what the installer recorded)."""
    out = [user_file(), os.path.join(sys.prefix, 'kicad2ems-site.toml')]
    try:
        # a pip install --user
        out.append(os.path.join(site.getuserbase(), 'kicad2ems-site.toml'))
    except Exception:
        pass
    return out


def user_file():
    """The user's site settings file: $XDG_CONFIG_HOME/kicad2ems/site.toml, else
    %APPDATA%\\kicad2ems\\site.toml on Windows and ~/.config/kicad2ems/site.toml
    elsewhere."""
    base = os.environ.get('XDG_CONFIG_HOME')
    if not base and sys.platform == 'win32':
        base = os.environ.get('APPDATA')
    base = base or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'kicad2ems', 'site.toml')


def set_user(key, value):
    """A setting into the user's file (value None or '': the key removed), the
    others kept."""
    global _cache
    import json
    p = user_file()
    try:
        kept = load_toml(p)
    except (OSError, ValueError):
        kept = {}
    if value:
        kept[key] = str(value)
    else:
        kept.pop(key, None)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(
            "# kicad2ems: where its external programs are "
            "(src/kicad2ems/sitecfg.py)\n"
        )
        for k, v in kept.items():
            fh.write(f"{k} = {json.dumps(str(v))}\n")
    _cache = None


def get(key):
    """A site setting (openems_venv, qucsator, ...), from the first of paths()
    that sets it, or None. Read once."""
    global _cache
    if _cache is None:
        _cache = {}
        for p in reversed(paths()):
            try:
                _cache.update(load_toml(p))
            except (OSError, ValueError):
                continue
    return _cache.get(key) or None
