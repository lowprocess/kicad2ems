"""
Where this install's external programs are: ~/.config/kicad2ems/site.toml (the GUI's Solvers and hardware
window writes it), then kicad2ems-site.toml in the environment (sys.prefix: the venv, or the user's site for a
--user install), as setup.sh recorded them. A setting given on the command line or in a config still comes
first.

    qucsator = "/usr/bin/qucsator_rf"           # the line model's circuit solver
    openems_venv = "/opt/openems/venv"          # a venv with openEMS (kicad2ems-openems: venv.py)

    sitecfg.get('qucsator') -> the path, or None
    sitecfg.set_user('qucsator', path)          # into the user's file (None / '': removed)
"""
import os, site, sys

_cache = None


def paths():
    """The site files read, the first that sets a key wins: the user's (what the GUI set), then the
    environment's (what setup.sh recorded)."""
    out = [user_file(), os.path.join(sys.prefix, 'kicad2ems-site.toml')]
    try:
        out.append(os.path.join(site.getuserbase(), 'kicad2ems-site.toml'))     # a pip install --user
    except Exception:
        pass
    return out


def user_file():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'kicad2ems', 'site.toml')


def set_user(key, value):
    """A setting into the user's file (value None or '': the key removed), the others kept."""
    global _cache
    import json
    p = user_file()
    kept = {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(p, 'rb') as fh:
            kept = tomllib.load(fh)
    except (OSError, ValueError):
        pass
    if value:
        kept[key] = str(value)
    else:
        kept.pop(key, None)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as fh:
        fh.write("# kicad2ems: where its external programs are (src/kicad2ems/sitecfg.py)\n")
        for k, v in kept.items():
            fh.write(f"{k} = {json.dumps(str(v))}\n")
    _cache = None


def get(key):
    global _cache
    if _cache is None:
        _cache = {}
        try:
            import tomllib
        except ImportError:                                         # Python 3.10
            import tomli as tomllib
        for p in reversed(paths()):
            try:
                with open(p, 'rb') as fh:
                    _cache.update(tomllib.load(fh))
            except (OSError, ValueError):
                continue
    v = _cache.get(key)
    return v or None
