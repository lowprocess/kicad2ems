"""
The API of a program that drives kicad2ems: reads a board, shows its line model, starts runs and reads their
results (the GUI, the KiCad plugin, dfsynth's --validate).

    from kicad2ems.api.frontend import KiCadBoard, Board, build_lines, Run, registry

  boards              KiCadBoard (a .kicad_pcb, or KiCad's board text), Board (the canonical model), kind_value
                      (a footprint's lumped part), extra_port_fps (the footprints a region adds as ports)
  the line model      build_lines (Board -> LineModel), RefCopper (its reference copper, built once), overlay (the
                      module: the line model drawn on the board)
  settings            Settings, build_settings, read_toml, all_options, default_out, PATH_KEYS, sitecfg (the
                      module: the user's settings file), G_DUMP (the field-dump settings group)
  solvers             registry, get_solver, resolve_lines, AUTO, LINES
  runs                Run, EventTail, stop_runs, find_results, toml_value, write_toml
  results             read_touchstone; meshes (the module: the solvers' saved meshes); phased (the module: drives,
                      grids, tapers); frames (the module: field frames), find_dumps, write_frames; read_sphere,
                      sphere_cuts, sphere_gain (far fields)
  the machine         core_threads (one per fast physical core: worker processes of a parallel map)
  examples            examples ({name: config path}), prepare_example (an example's config and board, copied or
                      generated into a folder of the user's: open it from there), examples_folder (that folder)
"""
from . import API_VERSION, lazy                                            # noqa: F401

NAMES = dict(
    KiCadBoard='.importers.kicad:KiCadBoard', Board='.board:Board', kind_value='.parts:kind_value',
    extra_port_fps='.region:extra_port_fps',
    build_lines='.lines.builder:build', RefCopper='.lines.reference:RefCopper', overlay='.overlay',
    Settings='.config:Settings', build_settings='.config:build_settings', read_toml='.config:read_toml',
    all_options='.config:all_options', default_out='.config:default_out', PATH_KEYS='.config:PATH_KEYS',
    sitecfg='.sitecfg', G_DUMP='.fields.dumps:G_DUMP',
    examples='.examples:examples', prepare_example='.examples:prepare_example', examples_folder='.examples:user_folder',
    registry='.solvers:registry', get_solver='.solvers:get', resolve_lines='.solvers:resolve_lines',
    AUTO='.solvers:AUTO', LINES='.solvers:LINES',
    Run='.runs:Run', EventTail='.runs:EventTail', stop_runs='.runs:stop_runs', find_results='.runs:find_results',
    toml_value='.runs:toml_value', write_toml='.runs:write_toml',
    read_touchstone='.touchstone:read_touchstone', meshes='.meshes', phased='.phased', frames='.fields.frames',
    find_dumps='.fields.frames:find_dumps', write_frames='.fields.frames:write_frames',
    read_sphere='.fields.antenna:read_sphere', sphere_cuts='.fields.antenna:sphere_cuts',
    sphere_gain='.fields.antenna:sphere_gain',
    core_threads='emsdev.cpu:core_threads',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
