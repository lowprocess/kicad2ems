"""
The API of a program that drives kicad2ems: reads a board, shows its line model, starts runs and reads their
results (the GUI, the KiCad plugin, dfsynth's --validate).

    from kicad2ems.api.frontend import KiCadBoard, Board, build_lines, Run, registry

  boards              KiCadBoard (a .kicad_pcb, or KiCad's board text), Board (the canonical model), kind_value
                      (a footprint's lumped part), extra_port_fps (the footprints a region adds as ports)
  the line model      build_lines (Board -> LineModel), RefCopper (its reference copper, built once), overlay (the
                      module: the line model drawn on the board), sections (the module: one section's field),
                      impedance (the module: a stack-up's Z0 without a board: Line, calculate, fit, element) (2.5)
  settings            Settings, build_settings, read_toml, all_options, default_out, PATH_KEYS, sitecfg (the
                      module: the user's settings file), G_DUMP (the field-dump settings group)
  solvers             registry, get_solver, resolve_lines, AUTO, LINES
  analyses            analyses ({name: Analysis}: the analysis plugins installed, kicad2ems.analyses), analysis_of
                      (whose result a folder holds), read_analyses (a config's [analysis] tables) (2.5),
                      read_analysis_sets (every table of each: an analysis given several, a run each) (2.10)
  the study           study_tree (what to list: by group, each question with the engines that answer it),
                      questions, engines ({name: Engine}: solvers and analyses alike), runs_for ({engine: the
                      questions asked of it}), mark_run and run_mark (a run folder's mark: who made it) (2.6);
                      a config's study: Study, read_study, write_study (its [study] tables), study_plan (the runs
                      it takes, in order: [Step]), engine_named (an engine by what a config calls it),
                      latest_results ({analysis: its newest result's folder}) (2.7); default_fields (what an
                      engine is asked when its field dumps are wanted and none is set), compare_engines (two
                      engines' named figures side by side: Analysis.figures) (2.8)
  runs                Run, EventTail, stop_runs, find_results, toml_value, write_toml; where a front end's runs go
                      (a folder each under the output folder): run_dir, preview_dir, tool_dir, tool_preview_dir,
                      board_name (2.7)
  files it writes     read_summary (<name>_run.json), read_antenna (<name>_antenna.json), check_format (a JSON of
                      kicad2ems's, e.g. python -m kicad2ems.sysinfo's: None, or why this kicad2ems cannot read it)
  results             read_touchstone, port_names (a kicad2ems Touchstone file's port names); meshes (the module: the solvers' saved meshes); phased (the module: drives,
                      grids, tapers); frames (the module: field frames), find_dumps, write_frames; read_sphere,
                      sphere_cuts, sphere_gain (far fields)
  the machine         core_threads (one per fast physical core: worker processes of a parallel map)
  examples            examples ({name: config path}), prepare_example (an example's config and board, copied or
                      generated into a folder of the user's: open it from there), examples_folder (that folder),
                      example_index (every example with its group and title, the analyses' own among them) (2.5)
"""
from . import API_VERSION, lazy                                            # noqa: F401

NAMES = dict(
    KiCadBoard='.importers.kicad:KiCadBoard', Board='.board:Board', kind_value='.parts:kind_value',
    extra_port_fps='.region:extra_port_fps',
    build_lines='.lines.builder:build', RefCopper='.lines.reference:RefCopper', overlay='.overlay',
    sections='.lines.sections', impedance='.lines.calc',
    Settings='.config:Settings', build_settings='.config:build_settings', read_toml='.config:read_toml',
    all_options='.config:all_options', default_out='.config:default_out', PATH_KEYS='.config:PATH_KEYS',
    sitecfg='.sitecfg', G_DUMP='.fields.dumps:G_DUMP',
    examples='.examples:examples', prepare_example='.examples:prepare_example', examples_folder='.examples:user_folder',
    example_index='.examples:index',
    analyses='.analyses:registry', analysis_of='.analyses:analysis_of', read_analyses='.analyses:read_config',
    read_analysis_sets='.analyses:read_sets',
    study_tree='.study:study_tree', questions='.study:questions', engines='.study:engines', runs_for='.study:runs_for',
    mark_run='.study:mark', run_mark='.study:marked',
    Study='.study:Study', read_study='.study:read_config', write_study='.study:write_config',
    study_plan='.study:plan', engine_named='.study:engine_named', latest_results='.study:latest_results',
    default_fields='.study:default_fields', compare_engines='.study:compare', rf_needs='.study:rf_needs',
    run_dir='.runs:run_dir', preview_dir='.runs:preview_dir', tool_dir='.runs:tool_dir',
    tool_preview_dir='.runs:tool_preview_dir', board_name='.runs:board_name',
    registry='.solvers:registry', get_solver='.solvers:get', resolve_lines='.solvers:resolve_lines',
    AUTO='.solvers:AUTO', LINES='.solvers:LINES',
    Run='.runs:Run', EventTail='.runs:EventTail', stop_runs='.runs:stop_runs', find_results='.runs:find_results',
    toml_value='.runs:toml_value', write_toml='.runs:write_toml',
    read_summary='.runs:read_summary', read_antenna='.fields.antenna:read', check_format='.formats:check',
    read_touchstone='.touchstone:read_touchstone', port_names='.touchstone:port_names', meshes='.meshes', phased='.phased', frames='.fields.frames',
    find_dumps='.fields.frames:find_dumps', write_frames='.fields.frames:write_frames',
    read_sphere='.fields.antenna:read_sphere', sphere_cuts='.fields.antenna:sphere_cuts',
    sphere_gain='.fields.antenna:sphere_gain',
    core_threads='emsdev.cpu:core_threads',
)
__all__ = ['API_VERSION', *NAMES]
__getattr__, __dir__ = lazy(__name__, NAMES)
