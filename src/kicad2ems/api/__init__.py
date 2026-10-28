"""
kicad2ems's public API: what other packages may import. Everything else in kicad2ems is private to it and may
change in any release.

    kicad2ems.api.solver      for solver plugins (the 'kicad2ems.solvers' entry points: mom25d, gfdtd, openEMS)
    kicad2ems.api.frontend    for programs that drive kicad2ems (the GUI, the KiCad plugin, dfsynth)

API_VERSION is (major, minor). A minor release adds names; a major one removes or changes one. A solver plugin
says which version it was written against with a class attribute `api = (1, 0)` on its Solver; the registry
leaves out (kicad2ems --check lists it as broken) a plugin of another major version, or of a newer minor one than
this kicad2ems has. A plugin that declares nothing was written before the API had a version: it counts as 1.0.

Both modules load their names on first use (PEP 562), so importing one costs nothing until a name is used: a
plugin that imports plot_antenna does not import matplotlib until it draws.
"""
import importlib

API_VERSION = (1, 0)
UNVERSIONED = (1, 0)                # what a plugin that declares no `api` is taken to be


def compatible(api):
    """None when a plugin written against `api` (a (major, minor) pair, or None) runs on this kicad2ems, else why
    not."""
    major, minor = tuple(api or UNVERSIONED)[:2]
    if major == API_VERSION[0] and minor <= API_VERSION[1]:
        return None
    have = '.'.join(map(str, API_VERSION))
    if major == API_VERSION[0]:
        return f"written for kicad2ems API {major}.{minor}, this kicad2ems has {have}: update kicad2ems"
    return (f"written for kicad2ems API {major}.{minor}, this kicad2ems has {have}: "
            f"{'update the plugin' if major < API_VERSION[0] else 'update kicad2ems'}")


def lazy(module_name, names):
    """(__getattr__, __dir__) for a module that re-exports `names` ({public name: 'module:attribute' or 'module'})
    on first use. Module paths are relative to kicad2ems."""
    def __getattr__(name):
        spec = names.get(name)
        if spec is None:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        mod, _, attr = spec.partition(':')
        obj = importlib.import_module('kicad2ems' + mod if mod.startswith('.') else mod)
        return getattr(obj, attr) if attr else obj

    def __dir__():
        return sorted(names)
    return __getattr__, __dir__
