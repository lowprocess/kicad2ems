"""
The API of a solver plugin: the Solver interface (kicad2ems.solvers explains it) and what a solver needs to write
its results as kicad2ems's other solvers do.

    from kicad2ems.api.solver import Solver, Result, Option, mixed_mode

    class MySolver(Solver):
        api = (2, 0)                # the kicad2ems.api version it is written against (API_VERSION)
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
  the machine         sitecfg (where external programs are), core_threads, ram_gb
  for an FDTD layer (kicad2ems-fdtd's: the FDTD solvers' shared model, mesh and runs):
    constants         C0, EPS0; ETA0, MU0 (2.1)
    settings          strlist, items (Option value parsers), G_DUMP (the field-dump settings group), registry
    geometry          split_holes (a polygon with holes -> simple polygons), describe_edges, snap_edges (the
                      board-edge walls: their log line, snapped onto a grid)
    field dumps       parse_dump, add_dumps, dump_size_mb, write_norm, plot_fd_dumps, save_animation
    antennas          gain_table, add_polarisation, pattern_gain
    figures           plot_preview (the model and its mesh)
    the line model    solve_lines, lines_backend (its fast solve: a first look before a long run)
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
    sitecfg='.sitecfg', core_threads='emsdev.cpu:core_threads', ram_gb='.pipeline:ram_gb',
    C0='.constants:C0', EPS0='.constants:EPS0', ETA0='.constants:ETA0', MU0='.constants:MU0', strlist='.options:strlist', items='.options:items',
    G_DUMP='.fields.dumps:G_DUMP', registry='.solvers:registry', split_holes='.geometry:split_holes',
    describe_edges='.edges:describe', snap_edges='.edges:snap',
    parse_dump='.fields.dumps:parse_dump', add_dumps='.fields.dumps:add_dumps',
    dump_size_mb='.fields.dumps:estimate_size_mb', write_norm='.fields.dumps:write_norm',
    plot_fd_dumps='.fields.dumps:plot_fd_dumps', save_animation='.fields.frames:save_animation',
    gain_table='.fields.antenna:gain_table', add_polarisation='.fields.antenna:add_polarisation',
    pattern_gain='.fields.antenna:pattern_gain', plot_preview='.plots:preview',
    solve_lines='.solvers.lines.fastsolve:solve', lines_backend='.solvers.lines.fastsolve:resolve_backend',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
