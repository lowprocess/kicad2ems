"""
FDTD engines: anything with openEMS's Python interface (a ContinuousStructure class and an openEMS class) runs
the model that model.build_fdtd makes. An engine is named by a string, so runs in worker processes can find it:
'<name>' or '<name>:<option>' (gfdtd:cupy). Its module's api(option) returns (ContinuousStructure, openEMS).
"""
import importlib

ENGINES = {'openems': ('kicad2ems.solvers.openems.engine', 'openEMS'),     # name: (module, name in the log)
           'gfdtd': ('kicad2ems.solvers.gfdtd', 'gfdtd')}
DEFAULT = 'openems'


def engine_api(engine):
    """(ContinuousStructure, openEMS) of an engine ('gfdtd:cupy'; None: the default)."""
    name, _, option = (engine or DEFAULT).partition(':')
    if name not in ENGINES:
        raise ValueError(f"unknown FDTD engine {engine!r}")
    return importlib.import_module(ENGINES[name][0]).api(option)


def engine_name(engine):
    """The engine's folder under the output folder and its name in the log: ('openems', 'openEMS')."""
    name = (engine or DEFAULT).partition(':')[0]
    return name, ENGINES.get(name, (None, name))[1]
