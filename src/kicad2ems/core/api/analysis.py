"""
The API of an analysis plugin: the Analysis interface (kicad2ems.analyses explains it) and what an analysis needs to
write its maps where a front end's viewer reads a run's.

    from kicad2ems.api.analysis import Analysis

    class Thermal(Analysis):
        api = (2, 6)                # the kicad2ems.api version it is written against (API_VERSION)
        provides = ('thermal',)     # the questions it answers (kicad2ems.study)
        name, label, tool = 'thermal', 'Thermal', 'mypackage.thermal'
        result = 'thermal.json'
        ...

  the interface       Analysis, KINDS (the kinds of an option), RF (the S-parameter run's key: taken), API_VERSION
  questions           Question, QUESTIONS (kicad2ems.study: what an analysis `provides`; a plugin's own for what
                      kicad2ems has none for) (2.6)
  the registry        registry ({name: Analysis}), analysis_of (whose result a folder holds)
  maps for a viewer   Dump, write_info, write_npz (field dumps: <out>/<name>/exc_<what>/, as a solver's), save_mesh

An analysis runs as a command of its own (Analysis.command) and may be used without kicad2ems; its plugin class is
the only part that imports this module.
"""
from . import API_VERSION, lazy                                            # noqa: F401

NAMES = dict(
    Analysis='.analyses:Analysis', KINDS='.analyses:KINDS', RF='.analyses:RF',
    Question='.study:Question', QUESTIONS='.study:QUESTIONS',
    registry='.analyses:registry', analysis_of='.analyses:analysis_of',
    Dump='.fields.dumps:Dump', write_info='.fields.dumps:write_info', write_npz='.fields.frames:write_npz',
    save_mesh='.meshes:save',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
