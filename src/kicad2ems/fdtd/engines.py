"""
FDTD engines: anything with openEMS's Python interface (a ContinuousStructure class and an openEMS class) runs
the model that model.build_fdtd makes. An engine is a module installed under the entry-point group
'kicad2ems.engines' (kicad2ems-openems: openems, gfdtd: gfdtd), giving api(option) -> (ContinuousStructure,
openEMS), LABEL, its name in the log, and optionally IN_THREADS (True: the runs of one solve share this process,
as threads, gfdtd's on the GPU). It is named by a string, so runs in worker processes can find it:
'<name>' or '<name>:<option>' (gfdtd:cupy).
"""
GROUP = 'kicad2ems.engines'
DEFAULT = 'openems'


def _entry_points():
    from importlib.metadata import entry_points
    return list(entry_points(group=GROUP))


def engine_module(engine):
    """The module of an engine ('gfdtd:cupy'; None: the default), or None when none is installed under its name."""
    name = (engine or DEFAULT).partition(':')[0]
    ep = next((ep for ep in _entry_points() if ep.name == name), None)
    return None if ep is None else ep.load()


def engine_api(engine):
    """(ContinuousStructure, openEMS) of an engine ('gfdtd:cupy'; None: the default)."""
    mod = engine_module(engine)
    if mod is None:
        raise ValueError(f"unknown FDTD engine {engine!r} (none installed under that name)")
    return mod.api((engine or DEFAULT).partition(':')[2])


def engine_name(engine):
    """The engine's folder under the output folder and its name in the log: ('openems', 'openEMS')."""
    name = (engine or DEFAULT).partition(':')[0]
    return name, getattr(engine_module(engine), 'LABEL', name)
