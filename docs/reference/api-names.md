# API names

Each name of the APIs between the packages ([APIs](../api.md)), and where it is defined.

## `kicad2ems.core.api.solver`

| Name | Defined as |
|---|---|
| `Solver` | {py:class}`kicad2ems.core.solvers.Solver` |
| `Result` | {py:class}`kicad2ems.core.solvers.Result` |
| `Deviation` | {py:class}`kicad2ems.core.solvers.Deviation` |
| `Option` | {py:class}`kicad2ems.core.options.Option` |
| `AUTO` | {py:data}`kicad2ems.core.solvers.AUTO` |
| `FEATURES` | {py:data}`kicad2ems.core.solvers.FEATURES` |
| `floatlist` | {py:func}`kicad2ems.core.options.floatlist` |
| `floats` | {py:func}`kicad2ems.core.options.floats` |
| `stop` | the module {py:mod}`kicad2ems.core.stop` |
| `mixed_mode` | {py:func}`kicad2ems.core.sparams.mixed_mode` |
| `fill_reciprocal` | {py:func}`kicad2ems.core.sparams.fill_reciprocal` |
| `plot_antenna` | {py:func}`kicad2ems.core.plots.plot_antenna` |
| `plot_pattern` | {py:func}`kicad2ems.core.plots.plot_pattern` |
| `plot_convergence` | {py:func}`kicad2ems.core.plots.plot_convergence` |
| `describe_antenna` | {py:func}`kicad2ems.core.fields.antenna.describe` |
| `match_summary` | {py:func}`kicad2ems.core.fields.antenna.match_summary` |
| `save_antenna` | {py:func}`kicad2ems.core.fields.antenna.save` |
| `DumpSource` | {py:class}`kicad2ems.core.fields.frames.DumpSource` |
| `save_fd_outputs` | {py:func}`kicad2ems.core.fields.frames.save_fd_outputs` |
| `write_npz` | {py:func}`kicad2ems.core.fields.frames.write_npz` |
| `Dump` | {py:class}`kicad2ems.core.fields.dumps.Dump` |
| `write_info` | {py:func}`kicad2ems.core.fields.dumps.write_info` |
| `save_mesh` | {py:func}`kicad2ems.core.meshes.save` |
| `phased` | the module {py:mod}`kicad2ems.core.phased` |
| `settings_drive` | {py:func}`kicad2ems.core.phased.settings_drive` |
| `sitecfg` | the module {py:mod}`kicad2ems.core.sitecfg` |
| `core_threads` | {py:func}`kicad2ems.emsdev.cpu.core_threads` |
| `ram_gb` | {py:func}`kicad2ems.core.pipeline.ram_gb` |
| `C0` | {py:data}`kicad2ems.core.constants.C0` |
| `EPS0` | {py:data}`kicad2ems.core.constants.EPS0` |
| `ETA0` | {py:data}`kicad2ems.core.constants.ETA0` |
| `MU0` | {py:data}`kicad2ems.core.constants.MU0` |
| `strlist` | {py:func}`kicad2ems.core.options.strlist` |
| `items` | {py:func}`kicad2ems.core.options.items` |
| `G_DUMP` | {py:data}`kicad2ems.core.fields.dumps.G_DUMP` |
| `registry` | {py:func}`kicad2ems.core.solvers.registry` |
| `split_holes` | {py:func}`kicad2ems.core.geometry.split_holes` |
| `describe_edges` | {py:func}`kicad2ems.core.edges.describe` |
| `snap_edges` | {py:func}`kicad2ems.core.edges.snap` |
| `parse_dump` | {py:func}`kicad2ems.core.fields.dumps.parse_dump` |
| `add_dumps` | {py:func}`kicad2ems.core.fields.dumps.add_dumps` |
| `dump_size_mb` | {py:func}`kicad2ems.core.fields.dumps.estimate_size_mb` |
| `write_norm` | {py:func}`kicad2ems.core.fields.dumps.write_norm` |
| `plot_fd_dumps` | {py:func}`kicad2ems.core.fields.dumps.plot_fd_dumps` |
| `save_animation` | {py:func}`kicad2ems.core.fields.frames.save_animation` |
| `gain_table` | {py:func}`kicad2ems.core.fields.antenna.gain_table` |
| `add_polarisation` | {py:func}`kicad2ems.core.fields.antenna.add_polarisation` |
| `pattern_gain` | {py:func}`kicad2ems.core.fields.antenna.pattern_gain` |
| `plot_preview` | {py:func}`kicad2ems.core.plots.preview` |
| `solve_lines` | {py:func}`kicad2ems.core.solvers.lines.fastsolve.solve` |
| `lines_backend` | {py:func}`kicad2ems.core.solvers.lines.fastsolve.resolve_backend` |
| `hammerstad` | {py:func}`kicad2ems.core.roughness.hammerstad` |
| `rough_sigma` | {py:func}`kicad2ems.core.roughness.rough_sigma` |
| `band_sigma` | {py:func}`kicad2ems.core.roughness.band_sigma` |
| `resolve_finish` | {py:func}`kicad2ems.core.finish.resolve` |
| `finish_factor` | {py:func}`kicad2ems.core.finish.factor` |
| `finish_weighted` | {py:func}`kicad2ems.core.finish.weighted` |
| `finish_band_sigma` | {py:func}`kicad2ems.core.finish.band_sigma` |
| `finish_face_z` | {py:func}`kicad2ems.core.finish.face_z` |

## `kicad2ems.core.api.analysis`

| Name | Defined as |
|---|---|
| `Analysis` | {py:class}`kicad2ems.core.analyses.Analysis` |
| `KINDS` | {py:data}`kicad2ems.core.analyses.KINDS` |
| `RF` | {py:data}`kicad2ems.core.analyses.RF` |
| `Question` | {py:class}`kicad2ems.core.study.Question` |
| `QUESTIONS` | {py:data}`kicad2ems.core.study.QUESTIONS` |
| `registry` | {py:func}`kicad2ems.core.analyses.registry` |
| `analysis_of` | {py:func}`kicad2ems.core.analyses.analysis_of` |
| `Dump` | {py:class}`kicad2ems.core.fields.dumps.Dump` |
| `write_info` | {py:func}`kicad2ems.core.fields.dumps.write_info` |
| `write_npz` | {py:func}`kicad2ems.core.fields.frames.write_npz` |
| `save_mesh` | {py:func}`kicad2ems.core.meshes.save` |

## `kicad2ems.core.api.frontend`

| Name | Defined as |
|---|---|
| `KiCadBoard` | {py:class}`kicad2ems.core.importers.kicad.KiCadBoard` |
| `Board` | {py:class}`kicad2ems.core.board.Board` |
| `kind_value` | {py:func}`kicad2ems.core.parts.kind_value` |
| `extra_port_fps` | {py:func}`kicad2ems.core.region.extra_port_fps` |
| `board_layout` | {py:func}`kicad2ems.core.layout.board_layout` |
| `net_at` | {py:func}`kicad2ems.core.layout.net_at` |
| `part_at` | {py:func}`kicad2ems.core.layout.part_at` |
| `build_lines` | {py:func}`kicad2ems.core.lines.builder.build` |
| `RefCopper` | {py:class}`kicad2ems.core.lines.reference.RefCopper` |
| `overlay` | the module {py:mod}`kicad2ems.core.overlay` |
| `sections` | the module {py:mod}`kicad2ems.core.lines.sections` |
| `impedance` | the module {py:mod}`kicad2ems.core.lines.calc` |
| `Settings` | {py:class}`kicad2ems.core.config.Settings` |
| `build_settings` | {py:func}`kicad2ems.core.config.build_settings` |
| `read_toml` | {py:func}`kicad2ems.core.config.read_toml` |
| `all_options` | {py:func}`kicad2ems.core.config.all_options` |
| `default_out` | {py:func}`kicad2ems.core.config.default_out` |
| `PATH_KEYS` | {py:data}`kicad2ems.core.config.PATH_KEYS` |
| `sitecfg` | the module {py:mod}`kicad2ems.core.sitecfg` |
| `G_DUMP` | {py:data}`kicad2ems.core.fields.dumps.G_DUMP` |
| `examples` | {py:func}`kicad2ems.examples.examples` |
| `prepare_example` | {py:func}`kicad2ems.examples.prepare_example` |
| `examples_folder` | {py:func}`kicad2ems.examples.user_folder` |
| `example_index` | {py:func}`kicad2ems.examples.index` |
| `analyses` | {py:func}`kicad2ems.core.analyses.registry` |
| `analysis_of` | {py:func}`kicad2ems.core.analyses.analysis_of` |
| `read_analyses` | {py:func}`kicad2ems.core.analyses.read_config` |
| `read_analysis_sets` | {py:func}`kicad2ems.core.analyses.read_sets` |
| `study_tree` | {py:func}`kicad2ems.core.study.study_tree` |
| `questions` | {py:func}`kicad2ems.core.study.questions` |
| `engines` | {py:func}`kicad2ems.core.study.engines` |
| `runs_for` | {py:func}`kicad2ems.core.study.runs_for` |
| `mark_run` | {py:func}`kicad2ems.core.study.mark` |
| `run_mark` | {py:func}`kicad2ems.core.study.marked` |
| `Study` | {py:class}`kicad2ems.core.study.Study` |
| `read_study` | {py:func}`kicad2ems.core.study.read_config` |
| `write_study` | {py:func}`kicad2ems.core.study.write_config` |
| `study_plan` | {py:func}`kicad2ems.core.study.plan` |
| `engine_named` | {py:func}`kicad2ems.core.study.engine_named` |
| `latest_results` | {py:func}`kicad2ems.core.study.latest_results` |
| `default_fields` | {py:func}`kicad2ems.core.study.default_fields` |
| `compare_engines` | {py:func}`kicad2ems.core.study.compare` |
| `rf_needs` | {py:func}`kicad2ems.core.study.rf_needs` |
| `run_dir` | {py:func}`kicad2ems.core.runs.run_dir` |
| `preview_dir` | {py:func}`kicad2ems.core.runs.preview_dir` |
| `tool_dir` | {py:func}`kicad2ems.core.runs.tool_dir` |
| `tool_preview_dir` | {py:func}`kicad2ems.core.runs.tool_preview_dir` |
| `board_name` | {py:func}`kicad2ems.core.runs.board_name` |
| `registry` | {py:func}`kicad2ems.core.solvers.registry` |
| `get_solver` | {py:func}`kicad2ems.core.solvers.get` |
| `resolve_lines` | {py:func}`kicad2ems.core.solvers.resolve_lines` |
| `AUTO` | {py:data}`kicad2ems.core.solvers.AUTO` |
| `LINES` | {py:data}`kicad2ems.core.solvers.LINES` |
| `Run` | {py:class}`kicad2ems.core.runs.Run` |
| `EventTail` | {py:class}`kicad2ems.core.runs.EventTail` |
| `stop_runs` | {py:func}`kicad2ems.core.runs.stop_runs` |
| `find_results` | {py:func}`kicad2ems.core.runs.find_results` |
| `toml_value` | {py:func}`kicad2ems.core.runs.toml_value` |
| `write_toml` | {py:func}`kicad2ems.core.runs.write_toml` |
| `read_summary` | {py:func}`kicad2ems.core.runs.read_summary` |
| `read_antenna` | {py:func}`kicad2ems.core.fields.antenna.read` |
| `check_format` | {py:func}`kicad2ems.core.formats.check` |
| `read_touchstone` | {py:func}`kicad2ems.core.touchstone.read_touchstone` |
| `port_names` | {py:func}`kicad2ems.core.touchstone.port_names` |
| `meshes` | the module {py:mod}`kicad2ems.core.meshes` |
| `phased` | the module {py:mod}`kicad2ems.core.phased` |
| `frames` | the module {py:mod}`kicad2ems.core.fields.frames` |
| `find_dumps` | {py:func}`kicad2ems.core.fields.frames.find_dumps` |
| `write_frames` | {py:func}`kicad2ems.core.fields.frames.write_frames` |
| `read_sphere` | {py:func}`kicad2ems.core.fields.antenna.read_sphere` |
| `sphere_cuts` | {py:func}`kicad2ems.core.fields.antenna.sphere_cuts` |
| `sphere_gain` | {py:func}`kicad2ems.core.fields.antenna.sphere_gain` |
| `core_threads` | {py:func}`kicad2ems.emsdev.cpu.core_threads` |

## `kicad2ems.gui.api`

| Name | Defined as |
|---|---|
| `PlanView` | {py:class}`kicad2ems.gui.planview.PlanView` |
