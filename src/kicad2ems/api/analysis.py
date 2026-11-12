"""
The API of an analysis plugin: the Analysis interface (kicad2ems.analyses explains it) and what an analysis needs to
write its maps where a front end's viewer reads a run's.

    from kicad2ems.api.analysis import Analysis

    class Thermal(Analysis):
        api = (2, 5)                # the kicad2ems.api version it is written against (API_VERSION)
        name, label, tool = 'thermal', 'Thermal', 'mypackage.thermal'
        result = 'thermal.json'
        ...

  the interface       Analysis, KINDS (the kinds of an option), RF (the S-parameter run's key: taken), API_VERSION
  the registry        registry ({name: Analysis}), analysis_of (whose result a folder holds)
  maps for a viewer   Dump, write_info, write_npz (field dumps: <out>/<name>/exc_<what>/, as a solver's), save_mesh

An analysis runs as a command of its own (Analysis.command) and may be used without kicad2ems; its plugin class is
the only part that imports this module.
"""
from . import API_VERSION, lazy                                            # noqa: F401

NAMES = dict(
    Analysis='.analyses:Analysis', KINDS='.analyses:KINDS', RF='.analyses:RF',
    registry='.analyses:registry', analysis_of='.analyses:analysis_of',
    Dump='.fields.dumps:Dump', write_info='.fields.dumps:write_info', write_npz='.fields.frames:write_npz',
    save_mesh='.meshes:save',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
