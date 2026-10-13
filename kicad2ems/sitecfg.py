"""
Where this install's external programs are, as setup.sh recorded them: kicad2ems-site.toml in the environment
(sys.prefix: the venv, or the user's site for a --user install), then ~/.config/kicad2ems/site.toml. A setting
given on the command line or in a config still comes first.

    qucsator = "/usr/bin/qucsator_rf"           # the line model's circuit solver
    emerge_python = "/opt/emerge/bin/python"    # a Python with EMerge (--emerge-python)

    sitecfg.get('qucsator') -> the path, or None
"""
import os, site, sys

_cache = None


def paths():
    """The site files read, the first that sets a key wins: the environment's, then the user's."""
    out = [os.path.join(sys.prefix, 'kicad2ems-site.toml')]
    try:
        out.append(os.path.join(site.getuserbase(), 'kicad2ems-site.toml'))     # a pip install --user
    except Exception:
        pass
    out.append(os.path.join(os.path.expanduser('~'), '.config', 'kicad2ems', 'site.toml'))
    return out


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
