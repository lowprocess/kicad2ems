# APIs

[← Architecture](architecture.md) · [Index](index.md) · [Next: KiCad import →](kicad-import.md)

Everything one package of the kicad2ems stack uses of another goes through one of the interfaces on this
page; the rest of each package is private and may change in any release. Each interface has a version, so a
mismatch is reported instead of misread. The [code reference](reference/index.md) (in the documentation site)
lists every module, class and function.

| Interface | Provided by | Used by | Version |
|---|---|---|---|
| [`kicad2ems.api.solver`](#kicad2emsapisolver) | kicad2ems | kicad2ems-fdtd, mom25d | `kicad2ems.api.API_VERSION` (2, 6) |
| [`kicad2ems.api.analysis`](#kicad2emsapianalysis) | kicad2ems | pcbqs | the same |
| [`kicad2ems.api.frontend`](#kicad2emsapifrontend) | kicad2ems | kicad2ems-gui, kicad2ems-ipc, dfsynth | the same |
| [The `kicad2ems.solvers` entry point](#solver-plugins) | each solver package | kicad2ems | the plugin's `api` |
| [The `kicad2ems.analyses` entry point](#analysis-plugins) | each analysis package | kicad2ems, kicad2ems-gui | the plugin's `api` |
| [The command line and its files](#files) | kicad2ems | kicad2ems-gui, kicad2ems-ipc, dfsynth | `format_version` in each file |
| [gfdtd's engine](#gfdtd-engine) | gfdtd | kicad2ems-fdtd | `gfdtd.API_VERSION` (1, 0) |
| [`kicad2ems_gui.api`](#kicad2ems_guiapi) | kicad2ems-gui | kicad2ems-ipc | `kicad2ems_gui.api.API_VERSION` (1, 0) |
| [`dfsynth --describe --json`](#dfsynth) | dfsynth | kicad2ems-gui's Filter page | `format` 1 |

<a id="versions"></a>
## Versions

A version is (major, minor). A minor release adds to an interface; a major one removes or changes something
in it. A user takes a provider of the same major version and at least the minor it was written for.

- **Solver plugins** declare the `kicad2ems.api` version they were written for on their class (`api = (2, 1)`).
  The registry leaves out a plugin of another major version, or of a newer minor one than this kicad2ems has,
  and `kicad2ems --check` lists it as broken with what to update (`kicad2ems.api.compatible(api)` gives the
  reason). A plugin that declares none counts as 1.0.
- **Analysis plugins** declare it the same way (`api = (2, 6)` on their `Analysis`), and are left out and listed
  as broken on the same terms.
- **Front ends** need a kicad2ems of the minor version they use; their `pyproject.toml` asks for the release
  (kicad2ems-gui: `kicad2ems>=261101.post1`, API 2.2).
- 2.0 took the FDTD layer out of `api.solver` (it is in kicad2ems-fdtd). 2.1 added the examples and the checked
  file readers to `api.frontend`, and `ETA0`, `MU0` to `api.solver`. 2.3 added the copper roughness (`--rough`):
  `hammerstad`, `rough_sigma`, `band_sigma`, and the `roughness` row of `FEATURES`. 2.4 the surface finish (`--finish`): `resolve_finish`,
  `finish_factor`, `finish_weighted`, `finish_band_sigma`, `finish_face_z`, and the `finish` row. 2.5 the analysis
  plugins: `kicad2ems.api.analysis`, and `analyses`, `analysis_of`, `read_analyses`, `example_index`, `impedance` in `api.frontend`.
  2.6 the study's questions and engines: `Question`, `QUESTIONS` and an analysis's `provides`, `engine`, `questions`, `fields`,
  `for_questions`, `owns`; `study_tree`, `questions`, `engines`, `runs_for`, `mark_run`, `run_mark` in `api.frontend`.
  [CHANGELOG.md](https://github.com/lowprocess/kicad2ems/blob/main/CHANGELOG.md) has each release's.

Both API modules load their names on first use: importing one costs nothing until a name is used.

<a id="kicad2emsapisolver"></a>
## `kicad2ems.api.solver`

For solver plugins: the solver interface and what a solver needs to write its results as the others do.

| Group | Names |
|---|---|
| the interface | `Solver`, `Result`, `Deviation`, `Option`, `AUTO`, `FEATURES`, `API_VERSION` |
| settings | `floatlist`, `floats`, `strlist`, `items` (Option value parsers), `G_DUMP` (the field-dump settings group), `registry` |
| stopping | `stop` (the module): `stop.check()` between steps raises `stop.Stopped` when the GUI's Stop was pressed |
| S-parameters | `mixed_mode`, `fill_reciprocal` |
| figures | `plot_antenna`, `plot_pattern`, `plot_convergence`, `plot_preview` (the model and its mesh) |
| antennas | `describe_antenna`, `match_summary`, `save_antenna`, `gain_table`, `add_polarisation`, `pattern_gain` |
| field dumps | `Dump`, `write_info`, `DumpSource`, `save_fd_outputs`, `write_npz`, `parse_dump`, `add_dumps`, `dump_size_mb`, `write_norm`, `plot_fd_dumps`, `save_animation` |
| meshes | `save_mesh` (the GUI's 3D mesh view) |
| phased arrays | `phased` (the module: `parse_grid`, `settings_drive`, `combine`, `waves`, `format_drive`, `save`), `settings_drive` |
| geometry | `split_holes` (a polygon with holes as simple polygons), `describe_edges`, `snap_edges` (the board-edge walls) |
| the line model | `solve_lines`, `lines_backend` (its fast solve: a first look before a long run) |
| surface finish | `resolve_finish(s, m)` (a run's `Finish`: its metal layers, the outer layers it covers and each one's share of the loss; None without `--sigma` or a finish), `finish_factor(f, finish)` (the finished face's impedance over bare copper's), `finish_weighted(f, finish, share)`, `finish_band_sigma(sigma, finish, share, fmin, fmax)` (the one conductivity of the same loss at the band centre), `finish_face_z(f, sigma, t, finish)` |
| copper roughness | `hammerstad(f, rms_um, sigma)` (Hammerstad's loss factor), `rough_sigma(sigma, rms_um, f)` (the conductivity of the same loss at f), `band_sigma(s, fmin, fmax)` (a solver's one conductivity: `--sigma`, or with `--rough` that at the band centre) |
| constants | `C0`, `EPS0`, `ETA0`, `MU0` |
| the machine | `sitecfg` (the module: where external programs are), `core_threads`, `ram_gb` |

<a id="solver-plugins"></a>
### Solver plugins

A solver is a subclass of `Solver`, registered under the entry-point group `kicad2ems.solvers`:

```toml
[project.entry-points."kicad2ems.solvers"]
mysolver = "mypackage.plugin:MySolver"
```

It declares, as class attributes:

| Attribute | Meaning |
|---|---|
| `api` | the `kicad2ems.api` version it is written for: `(2, 1)` |
| `name`, `aliases`, `label` | `'mom25d'`, `('mom',)`, `'mom25d (MoM)'`; `name` is also its config table `[solver.mom25d]` |
| `consumes` | `'board'` (the geometry) or `'network'` (the line model) |
| `in_auto`, `in_all` | whether `--solvers auto` may pick it, and whether `all` includes it (default True; False for a variant of another solver or one for part of a band: mixed, pcbqs) |
| `previewed` | `--preview` prepares it without solving |
| `rank`, `order` | rough cost (cheap solvers run first); its place in lists |
| `capabilities` | `{feature: 'yes' \| 'no' \| 'ignored' \| 'approximated: how'}` over `FEATURES` |
| `options` | its settings, `[Option]`: the command line, config keys, GUI fields and settings are made from them |

and implements, in the order the pipeline calls them:

| Method | Returns |
|---|---|
| `check(s=None)` | `(usable, one line)`: its version and origin, or what is missing and how to get it |
| `validate(s)` | a message when its settings cannot be used together, or `None` |
| `assess(run)` | why it cannot model this board, a list (`[]`: it can) |
| `estimate(run)` | `dict(seconds, gb, what)`, its rough cost, for `--solvers auto`; or `None` |
| `prepare(run)` | its own form of the board (openEMS: the boundaries and mesh); a preview stops here |
| `solve(run, prep)` | a `Result`: S-parameters, port names, reference impedances, facts for the summary |
| `deviations(run, prep)` | `[Deviation]`: what it models differently from the board |
| `readback(run, prep)` | what its own model holds, in the board's terms, for the parity checks; or `None` |
| `outputs(run, prep, result)` | its own extra outputs (optional) |
| `resources()` | the GPUs it would run on here and why there are none, or `None`: the CPU only |

`run` is the pipeline's `Job`: a solver may use `run.m` (the board), `run.s` (the settings, its options among
them), `run.freq` (Hz), `run.lm` (the line model), `run.name`, `run.log()`, `run.status(name, dict)` (progress
events), `run.path(suffix)`, `run.wrote(label, path)`, `run.figure(fig, suffix, key, label)` and `run.summary`.
[Architecture](architecture.md#solver-plugins) has the rest: parity, and settings as data.

<a id="kicad2emsapianalysis"></a>
## `kicad2ems.api.analysis`

For analysis plugins: tools that work on the board a run is made of but give something other than S-parameters
(pcbqs's power integrity, near field, circuit and thermal analyses).

| Group | Names |
|---|---|
| the interface | `Analysis`, `KINDS` (the kinds of an option), `RF` (the S-parameter run's key in a front end's lists: a plugin may not take it), `API_VERSION` |
| the registry | `registry` (`{name: Analysis}`), `analysis_of(folder)` (`(name, preview)` of the analysis whose result a folder holds, or `None`) |
| questions | `Question` (name, label, group, about, shares, answer, order), `QUESTIONS` (kicad2ems's own: what an analysis may `provides`) |
| maps for a viewer | `Dump`, `write_info`, `write_npz` (field dumps, as a solver's), `save_mesh` |

<a id="analysis-plugins"></a>
### Analysis plugins

An analysis is a subclass of `Analysis`, registered under the entry-point group `kicad2ems.analyses`:

```toml
[project.entry-points."kicad2ems.analyses"]
thermal = "mypackage.analyses:Thermal"
```

It runs as a command of its own, so the tool behind it works without kicad2ems; the plugin class is the only
part that imports `kicad2ems.api.analysis`. It declares, as class attributes:

| Attribute | Meaning |
|---|---|
| `api` | the `kicad2ems.api` version it is written for: `(2, 6)` |
| `name`, `label`, `tool` | `'pdn'`, `'Power integrity'`, `'pcbqs.pdn'` (what runs it, as lists show it); `name` is also its config table `[analysis.pdn]` and the tag of its run folders |
| `about` | one line: what it gives |
| `order` | its place in lists, and in a run of several |
| `previewed` | it has a preview (the model and its mesh, no solve) |
| `needs` | the analyses it cannot run without the results of |
| `result`, `preview_result` | the file a finished run (a preview) leaves in its folder (`'pdn.json'`): how a folder is known as its result |
| `paths` | the options whose values are paths: relative to the config file they are read from |
| `page` | `True`: the GUI has an editor of its own for it (the circuit's schematic) |
| `finds` | what the board can propose settings for (`'converters'`: the GUI's "Find converters"), with `discover` and `choices` below; `''`: nothing |
| `env` | environment variables its command runs with |
| `provides` | the [questions](#study) it answers, from one run: `('dc', 'pdn')`. None: one question of its own name |
| `engine` | the program's name as a list shows it beside a question: `'pcbqs'`, `'Elmer'` |
| `questions` | `Question` objects of its own, for what kicad2ems has no question for |
| `fields` | the bool option that makes it write field dumps too (`'fields'`), or `''` |

and implements:

| Method | Returns |
|---|---|
| `check()` | `(usable, one line)`: its version and origin, or what is missing and how to get it |
| `options()` | its settings, `[dict(key, flag, kind, default, help, group, choices)]`; `kind` one of `KINDS`: `'str'`, `'int'`, `'float'`, `'bool'` (a switch), `'list'` (repeatable) |
| `tag(values)` | a short name for a run of these settings (the rail, the converter): its folder's |
| `command(values, board, out, preview=False, results=None)` | the command line that runs it into folder `out`; `results`: `{analysis: the folder of its latest result on this board}`, for one that can start from another's. `ValueError` says what is missing |
| `status(line)` | the progress a line of its output states, or `None` |
| `discover(values, board)`, `choices(text)` | a command that proposes settings from the board, and its output as `[(label, values)]` to pick from; `None` when it has none |
| `report(folder)` | what a result holds, to read: `dict(title, summary, sections=[dict(title, text, table=dict(columns, rows, formats), notes)], notes, images, footer)`, or `None`. A cell is a number or a string. `show=dict(field, run)` and `mesh` say where a viewer starts: on the dump of that field with the largest values, on that mesh |
| `curves(folder)` | what a result holds, to plot: `[dict(title, xlabel, ylabel, logx, logy, info, curves=[dict(name, x, y, role, unit)])]`; `role` is `'data'`, `'reference'` or `'limit'` |
| `suggest(folder)` | settings a result proposes for other analyses: `[dict(to, label, values, note, need)]`; `need`: the options it has no value for, to set before a run |
| `examples()` | the examples it ships: `[dict(name, config, group, title, files)]` |
| `for_questions(values, asked)` | its settings for a run asked only some of its questions (a rail's drop without its impedance) |
| `owns(folder, preview=False)` | whether the folder holds its result: by default, that its result file is there. An engine whose file another also writes looks inside it |

Its maps need no method: an analysis writes them as field dumps under `<out>/<name>/exc_<what>/` with its board
as `<out>/<name>_board.json`, and a viewer of a run's dumps shows them. A dump's entry in `dump_info.json` may give
its `unit`, and `"range": "data"` for a quantity whose colours should span its own values (a temperature) instead
of starting at zero.

A config holds the analyses' settings under `[analysis]`, which kicad2ems itself leaves alone:

```toml
[analysis]
run = ["circuit", "thermal"]        # the analyses a study of this config runs ("rf": S-parameters)
[analysis.circuit]
model = "buck.json"                 # an analysis's options by key; a path (its `paths`) relative to the file
```

<a id="study"></a>
### Questions and engines

A study asks questions of a board; engines answer them (`kicad2ems.study`). A question is something to find out
with a known kind of answer; an engine is a solver plugin or an analysis plugin. Several engines may answer one
question, and one engine several questions from one run.

| Group | Question | What it is | Answered by |
|---|---|---|---|
| RF | `sparams` | the ports' S-parameters | every solver plugin |
| RF | `antenna` | a driven port's far field | the solvers whose `antenna` capability is yes or approximated |
| RF | `lines` | every trace's impedance and delay | the line model's solvers |
| Power | `dc` | a rail's DC drop, current density and loss | an analysis that `provides` it |
| Power | `pdn` | the impedance a rail's loads see (shares `dc`'s run) | |
| Power | `nearfield` | a converter's input loop and its magnetic field | |
| Power | `circuit` | a switching circuit on the board's copper | |
| Power | `capacitance` | the capacitance between nets | |
| Thermal | `thermal` | the board's steady temperature | |

`study_tree()` is what a front end lists: `[dict(group, questions=[dict(question, engines, missing)])]`, every
question with the engines installed that answer it (`Engine`: `name`, `label`, `kind` `'solver'` or `'analysis'`,
`provides`, `plugin`, `fields`, `previewed`, `check()`), and how to get one where there is none. `runs_for({question:
[engine names]})` gives the runs that takes, `{engine: the questions asked of it}`: one run for the questions an
engine answers together. A front end passes an analysis engine's settings through `for_questions` before
`command`, runs the solver engines as one kicad2ems run, and leaves a mark in each run's folder (`mark_run(folder,
engine, asked, preview)`; `run_mark(folder)` reads it), by which `analysis_of` knows a result's engine even where
two engines write a file of the same name. `kicad2ems --check` lists the questions with their engines.

<a id="kicad2emsapifrontend"></a>
## `kicad2ems.api.frontend`

For programs that drive kicad2ems: read a board, show its line model, start runs and read their results.

| Group | Names |
|---|---|
| boards | `KiCadBoard` (a `.kicad_pcb`, or KiCad's board text), `Board` (the canonical model), `kind_value` (a footprint's lumped part), `extra_port_fps` (the footprints a region adds as ports) |
| the line model | `build_lines` (Board → LineModel; `field=True`: every section by the field solver), `RefCopper` (its reference copper), `overlay` (the module: the line model drawn on the board), `sections` (the module: one section's field: `modes`, `solve`), `impedance` (the module: a stack-up's impedance without a board, `kicad2ems.lines.calc`: `Line`, `calculate`, `fit`, `element`, `describe`, `report`; 2.5) |
| settings | `Settings`, `build_settings`, `read_toml`, `all_options`, `default_out`, `PATH_KEYS`, `sitecfg` (the module), `G_DUMP` |
| solvers | `registry`, `get_solver`, `resolve_lines`, `AUTO`, `LINES` |
| analyses | `analyses` (`{name: Analysis}`: the analysis plugins installed), `analysis_of(folder)`, `read_analyses(config)` (`(run, {name: {key: value}})` of a config's `[analysis]` tables) |
| the study | `study_tree()`, `questions()` (`{name: Question}`), `engines()` (`{name: Engine}`), `runs_for(asked)`, `mark_run(folder, engine, asked, preview)`, `run_mark(folder)` ([Questions and engines](#study)) |
| runs | `Run`, `EventTail`, `stop_runs`, `find_results`, `toml_value`, `write_toml` |
| files kicad2ems writes | `read_summary`, `read_antenna`, `check_format`, `read_touchstone`, `port_names` |
| results | `meshes`, `phased`, `frames` (modules), `find_dumps`, `write_frames`, `read_sphere`, `sphere_cuts`, `sphere_gain` |
| examples | `examples` (`{name: config path}`), `example_index` (every example as a menu's entry: `[dict(name, config, group, title, source)]`, the installed analyses' own after kicad2ems's), `prepare_example` (an example copied, its board made, into a folder of the user's), `examples_folder` |
| the machine | `core_threads` |

<a id="files"></a>
## The command line and its files

A front end runs kicad2ems as a separate process (`kicad2ems.api.frontend.Run` makes the command line:
`python -m kicad2ems <settings>.toml --events <file> [--preview]`; dfsynth runs `python -m kicad2ems.cli`) and
reads what it writes. Each file another program reads names its format and version, `format` and
`format_version` ([`kicad2ems.formats`](reference/index.md)); the readers in `api.frontend` take a version up to
their own and say to update kicad2ems for a newer one. A version goes up only when a key is removed or changes
meaning; a new key needs none. A file without them was written before they were added: version 1.

| File | Format | Reader |
|---|---|---|
| `<name>_run.json`, the run summary | `kicad2ems-run` 1 | `read_summary(path)` |
| the event stream (`--events FILE`, JSON lines; its first line names the format) | `kicad2ems-events` 1 | `EventTail(path).read()` |
| `python -m kicad2ems.sysinfo`'s JSON on stdout: solvers, GPUs, memory, threads | `kicad2ems-sysinfo` 1 | `check_format(d, 'kicad2ems-sysinfo')` |
| `<name>[_<solver>]_antenna.json`, an antenna run's results (`sphere_file`: its far field) | `kicad2ems-antenna` 1 | `read_antenna(path)` |
| `<name>_board.json`, the board every solver gets | `kicad2ems-board`, `version` 1 | `Board.load(path)` |
| `<name>_<solver>.sNp`, Touchstone, the ports named in a `! ports: 1=P1, 2=P2d, ...` comment | Touchstone 1 / 2 | `read_touchstone(path)`, `port_names(path)` |

The events are `format` (first), `stage` (`name`), `log` (`text`), `status` (a solver's progress: `run`,
`fraction`, `eta` and the solver's own numbers), `file` (`label`, `path`) and `done` (`code`); a reader passes
over a kind it does not know. [Usage](usage.md#events) shows a stream. A run is stopped from outside with
`stop_runs(out_dir)`.

<a id="gfdtd-engine"></a>
## gfdtd's engine

gfdtd runs openEMS's models: its `ContinuousStructure` and `openEMS` classes stand in for CSXCAD's and openEMS's
own. kicad2ems-fdtd connects it (`kicad2ems_fdtd.gfdtd`) and is written against `gfdtd.API_VERSION`
(`ENGINE_API = (1, 0)`); a gfdtd of another major version, or an older minor one, is not used and
`kicad2ems --check` says which package to update. What it may rely on:

| Name | What |
|---|---|
| `ContinuousStructure`, `openEMS(..., backend=)` | openEMS's and CSXCAD's Python interface, as openEMS scripts use it; `backend`: `'auto'`, `'gpu'`, `'cupy'`, `'vulkan'`, `'core'`, `'numpy'` |
| `engine.gpu_info()`, `engine.core_info()` | `(usable, one line)` of the GPU and the compiled CPU core |
| `engine.resolve_backend(name)` | `'cupy'`, `'vulkan'`, `'core'` or `'numpy'` for `'auto'`, `'gpu'`, `'cpu'`, ... |
| `engine.concurrent_runs(cells, runs)` | how many runs to keep on the GPU at once |
| `__version__` | the release |

openEMS itself is the other engine, imported only by `kicad2ems_fdtd.openems.engine`.

<a id="kicad2ems_guiapi"></a>
## `kicad2ems_gui.api`

The KiCad plugin's map is the GUI's plan view: `PlanView`, the board seen from above. A subclass may use, beside
its public methods (`load`, `set_board`, `set_lines_visible`, `current_layer`, `z_of`, `redraw`, `fit`,
`select`) and its `selected` signal: `plot` (the pyqtgraph `PlotWidget` it draws in), `mesh` (the mesh selector),
and override `redraw()` and the mouse hooks `_moved(scene_pos)` and `_clicked(event)`, calling the base's for
what it does not handle itself.

<a id="dfsynth"></a>
## `dfsynth --describe --json`

kicad2ems-gui's Filter page never imports dfsynth: it builds its form from this description and runs dfsynth as a
process.

| Key | What |
|---|---|
| `format` | `1`; the page reads format 1 and says which package to update otherwise |
| `version` | dfsynth's release |
| `kinds` | `{type: [topologies]}`: `lowpass`, `highpass`, `bandpass`, `bandstop` |
| `presets` | `{name: {h, er, tand, material}}`: the substrate presets |
| `options` | `[{key, flag, kind, default, choices, help, group}]`, every option, `kind` one of `str`, `float`, `int`, `flag` |

[← Architecture](architecture.md) · [Index](index.md) · [Next: KiCad import →](kicad-import.md)
