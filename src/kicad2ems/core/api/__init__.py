"""
The boundary between kicad2ems's core and its other parts: what they may import
of it. The rest of kicad2ems.core is the core's own.

    kicad2ems.core.api.solver    for the solvers (kicad2ems.core.solvers: the
                                 FDTD solvers, mom25d, Palace, pcbqs)
    kicad2ems.core.api.analysis  for the analyses (kicad2ems.core.analyses:
                                 pcbqs's tools, the Elmer and Palace tools)
    kicad2ems.core.api.frontend  for programs that drive kicad2ems (the GUI, the
                                 KiCad plugin, dfsynth)

The modules load their names on first use (PEP 562), so importing one costs
nothing until a name is used: a solver that imports plot_antenna does not import
matplotlib until it draws.
"""
import importlib


def lazy(module_name, names):
    """(__getattr__, __dir__) for a module that re-exports `names` ({public
    name: 'module:attribute' or 'module'}) on first use. Module paths are
    relative to kicad2ems."""
    def __getattr__(name):
        spec = names.get(name)
        if spec is None:
            raise AttributeError(
                f"module {module_name!r} has no attribute {name!r}")
        mod, _, attr = spec.partition(':')
        obj = importlib.import_module('kicad2ems.core'
                                      + mod if mod.startswith('.') else mod)
        return getattr(obj, attr) if attr else obj

    def __dir__():
        return sorted(names)
    return __getattr__, __dir__
