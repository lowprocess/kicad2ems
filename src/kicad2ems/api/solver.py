"""
The API of a solver plugin: the Solver interface (kicad2ems.solvers explains it) and what a solver needs to write
its results as kicad2ems's other solvers do.

    from kicad2ems.api.solver import Solver, Result, Option, mixed_mode

    class MySolver(Solver):
        api = (1, 0)                # the kicad2ems.api version it is written against (API_VERSION)
        name, label = 'mine', 'my solver'
        ...

  the interface       Solver, Result, Deviation, Option, AUTO, FEATURES, API_VERSION
  settings            floatlist, floats: Option value parsers
  stopping            stop: stop.check() between steps raises stop.Stopped when the GUI's Stop was pressed
  S-parameters        mixed_mode, fill_reciprocal
  figures             plot_antenna, plot_pattern, plot_convergence
  antennas, fields    describe_antenna, match_summary, save_antenna; DumpSource, save_fd_outputs, write_npz;
                      Dump, write_info; save_mesh (the GUI's 3D mesh view)
  phased arrays       phased (the module: parse_grid, settings_drive, combine, waves, format_drive, save), settings_drive
  FDTD solvers        FDTD, MESH, RUN, G_MESH (the shared FDTD plugin and settings), run_excitations, auto_mesh
  the machine         sitecfg (where external programs are), core_threads
"""
from . import API_VERSION, lazy                                            # noqa: F401

NAMES = dict(
    Solver='.solvers:Solver', Result='.solvers:Result', Deviation='.solvers:Deviation', Option='.options:Option',
    AUTO='.solvers:AUTO', FEATURES='.solvers:FEATURES',
    floatlist='.options:floatlist', floats='.options:floats',
    stop='.stop',
    mixed_mode='.sparams:mixed_mode', fill_reciprocal='.sparams:fill_reciprocal',
    plot_antenna='.plots:plot_antenna', plot_pattern='.plots:plot_pattern', plot_convergence='.plots:plot_convergence',
    describe_antenna='.fields.antenna:describe', match_summary='.fields.antenna:match_summary',
    save_antenna='.fields.antenna:save',
    DumpSource='.fields.frames:DumpSource', save_fd_outputs='.fields.frames:save_fd_outputs',
    write_npz='.fields.frames:write_npz', Dump='.fields.dumps:Dump', write_info='.fields.dumps:write_info',
    save_mesh='.meshes:save',
    phased='.phased', settings_drive='.phased:settings_drive',
    FDTD='.fdtd.plugin:FDTD', MESH='.fdtd.plugin:MESH', RUN='.fdtd.plugin:RUN', G_MESH='.fdtd.plugin:G_MESH',
    run_excitations='.fdtd.run:run_excitations', auto_mesh='.fdtd.mesh:auto_mesh',
    sitecfg='.sitecfg', core_threads='emsdev.cpu:core_threads',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
