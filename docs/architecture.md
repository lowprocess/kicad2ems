# Architecture

[← Transmission-line model](transmission-lines.md) · [Index](index.md) · [Next: KiCad import →](kicad-import.md)

## The run pipeline

`cli.main` reads the command line and the config into one `config.Settings`. `pipeline.Job` then runs the
stages in order. Everything solver-specific goes through the solver's plugin.

```mermaid
flowchart LR
    P["preflight<br/>can the solvers run?"] --> A[".kicad_pcb / Gerber<br/>/ board.json"]
    A --> B["importers/"]
    B --> C["Board<br/>&rarr; &lt;name&gt;_board.json"]
    C --> L["lines<br/>the line model"]
    L --> S["assess<br/>can each solver model it?"]
    S --> E["prepare<br/>(openEMS: mesh)"]
    E --> F["solve"]
    F --> G["outputs<br/>.sNp, plots, comparisons"]
    G --> R["report<br/>_run.json, _report.html"]
```

| Stage | What it does |
|---|---|
| preflight | resolves which solvers run and checks each can. A named solver that can't stops the run (exit 2); a reference is dropped with a warning |
| model | builds the `Board` and writes `<name>_board.json` |
| lines | builds the line model, `<name>_tlines.png` and `<name>_lines.json` (its sections, for the GUI) |
| assess | asks each solver whether it can model this board (mom25d can't model gap ports) |
| prepare | each solver builds its own form of the board (openEMS: the mesh). With `--preview`, openEMS stops here |
| solve | runs each solver, cheapest first. A failure is a warning (exit 1 for a named solver) |
| outputs | writes each solver's Touchstone file, the comparison plot, and each solver's extra outputs |
| report | writes `<name>_run.json` and `<name>_report.html` |

Each stage adds its results to `Job.summary`, a JSON-friendly dict ([keys](postprocessing.md#json-summary)).
The report is rendered from that dict alone, so it can be rebuilt from the JSON file.

All messages go through `Job.log`. It prints them, keeps them for the report, and sends them to
`--events`. Lines containing `WARNING` go into the report's warning list. Lower-level functions take a
`log` callable, so they can be used without a `Job`.

Plots are matplotlib `Figure` objects without pyplot, so the GUI can embed them.

<a id="solver-plugins"></a>
## Solver plugins

A solver is a subclass of `solvers.Solver`. Only the line solvers are built in (`solvers.BUILTIN`); the
full-wave ones are packages that add theirs through an entry point: openEMS's is
[kicad2ems-openems](https://github.com/lowprocess/kicad2ems-openems) (the `openems` extra), mom25d's and mixed's are
in mom25d (`mom25d.kicad2ems`, the `mom` extra), gfdtd's in gfdtd (`gfdtd.kicad2ems`, the `gfdtd` extra). Any
other package works the same way:

```toml
# the plugin package's pyproject.toml
[project.entry-points."kicad2ems.solvers"]
mysolver = "mypackage.plugin:MySolver"
```

It then appears in `--solvers`, `--check`, the config (`[solver.mysolver]`), the GUI (a section per option
group, greyed until one of its solvers is ticked), the Filter page and the KiCad plugin's menu. Its `order`
places it in those lists. A plugin that fails to load is left out and listed by `--check`; a solver kicad2ems has
an extra for (`solvers.EXTRAS`) but finds no package for says how to install it.

An FDTD solver subclasses `fdtd.plugin.FDTD`, which builds the mesh and the model, runs the excitations and
writes the dumps and antenna results. It names its engine (`engine(s)`: `'<name>'` or `'<name>:<option>'`, a
module registered under the entry-point group `kicad2ems.engines`, whose `api(option)` returns its
`ContinuousStructure` and `openEMS` classes and whose `LABEL` names it in the log) and lists the shared settings
(`MESH + RUN`, the GUI's FDTD section) before its own. Solvers share a setting by listing the same `Option` object; a plugin that defines
another setting of the same name is refused.

A solver declares:

| Attribute | Meaning |
|---|---|
| `name`, `aliases`, `label` | e.g. `'mom25d'`, `('mom',)`, `'mom25d (MoM)'` |
| `consumes` | `'board'` (the geometry) or `'network'` (the line model) |
| `previewed` | `--preview` prepares it without solving |
| `rank` | rough cost; cheap solvers run first |
| `capabilities` | what it models, per feature (`yes`, `no`, `ignored`, or `approximated: how`) |
| `options` | its settings |

and implements:

| Method | Returns |
|---|---|
| `check()` | whether it can run here, and a one-line reason |
| `validate(s)` | a message if its settings can't be used together |
| `assess(job)` | why it can't model this board (empty: it can) |
| `prepare(job)` | its own form of the board |
| `solve(job, prep)` | a `Result`: S-parameters, port names, reference impedances |
| `outputs(job, prep, result)` | extra outputs (optional) |
| `deviations(job, prep)` | what it models differently from the board |
| `readback(job, prep)` | what its own model holds, in board terms |
| `estimate(job)` | its expected time and memory on this board |

**Parity.** A solver may approximate the board, but it must say so. `parity.check` compares the board
with each solver's readback and lists any difference the solver didn't declare. `tests/test_parity.py`
requires none on the examples. The declarations also produce the report's *What each solver saw* table.

**Settings as data.** Every setting is declared once, as an `options.Option`: the run's own in
`config.CORE`, each solver's in its plugin. The settings class, the command-line options, the config
reader and the GUI form are all built from these declarations.

<a id="choosing-solvers"></a>
## Choosing solvers (`auto`)

`Job.choose()` replaces `auto` once the board and line model exist:

1. Drop the solvers that can't run or can't model the board.
2. Estimate the rest. openEMS: mesh cells × timesteps × runs. The timesteps come from the pulse length
   and the line model's longest delay. mom25d: the unknowns of its mesh, the matrix work for that size on
   the CPU or GPU, and the via count. A solver that needs more than 80 % of the RAM is dropped.
3. Run the faster one, plus the line model.
4. Log the reason.

The estimates are calibrated on the examples. On 10 of 13 they picked the faster solver, and in the
other three the choice was at most 1.6 times slower.

## Module map

```
src/kicad2ems/
  cli.py          the command line
  config.py       the run's options, Settings, config files
  options.py      Option and value converters
  pipeline.py     Job: the stages above
  events.py       --events
  sysinfo.py      what this machine can run (the GUI's Solvers window, as `python -m kicad2ems.sysinfo`)
  importers/      KiCad (kicad/) and Gerber readers
  board.py        Board: the converted board, board.json
  region.py       cropping, nets = auto, extra and cut ports
  parts.py        lumped R/L/C parts
  edges.py        edge walls
  geometry.py     coordinate and polygon helpers
  fdtd/           shared by the FDTD solvers: boundaries, mesh, the CSX model, engines, runs, antenna runs,
                  and FDTD, their common plugin class with the shared settings
  fields/         field results of any solver: dump specs and readers, frames and animations, far-field analysis
  solvers/        the Solver base class and registry (the full-wave solvers: their own packages)
    lines/          the line solvers: the line model as a circuit (qucsator, scikit-rf)
  lines/          the transmission-line model: sections, Z0, coupled lines (every run, the KiCad plugin's map)
  sparams.py      reciprocity and mixed-mode conversion
  touchstone.py   .sNp files
  plots.py        figures
  report.py       the HTML report
  overlay.py      the line model over the board (<name>_lines.json): colours by Z0, picking a section
  meshes.py       each solver's mesh as a file, for the 3D view
  runs.py         a run started by another program (the GUI, the KiCad plugin): TOML, command line, events, stop
```

The window is [kicad2ems-gui](https://github.com/lowprocess/kicad2ems-gui), a package of its own (the `gui`
extra): its settings form is made from the option tables and the solver registry, and it starts each run as a
`kicad2ems` process (`runs.py`), so it needs nothing of a solver's. The full-wave solvers are packages too
([Solver plugins](#solver-plugins)).

openEMS, CSXCAD, scikit-rf, h5py and mom25d are imported **inside the functions that need them**. A plugin
imports nothing heavy at module level, because the registry loads every plugin to build the settings.
Keep it that way: the tests rely on it, and so do runs without openEMS.

**One board for every solver.** No solver reads KiCad files. Each gets the `Board`, and mom25d reads
`<name>_board.json` itself. Changes a solver makes, such as openEMS snapping vias to its mesh, stay in
its plugin.

## Main data structures

- **`Settings`**: one typed field per option.
- **`KiCadBoard`** (KiCad coordinates, y down): the stackup, copper items, tracks, vias, footprints and
  outline as read.
- **`Board`** (model coordinates, y up, mm): dielectric layers, copper heights and polygons per layer,
  outline, vias `(x, y, r, z0, z1)`, ports, parts, edge walls. `to_dict()` is board.json.
- **The mesh** (`auto_mesh`): x, y and z line positions, the vias as placed, and mesh statistics.
- **`LineModel`**: sections, port nodes, paths, coupled groups and discontinuities.
- **`RunResult`**: openEMS's S-matrix (NaN for ports not driven), dump files, and per-run times.

## Processes and threads

- A run is one process. `--jobs N` starts N worker processes, one per driven port.
- openEMS uses its own threads (`--threads`).
- A monitor thread follows the `openems.log` files for the progress line and `--events`.
- The GUI runs kicad2ems as a separate process, in the output folder, and reads its `--events` file.

---
[← Transmission-line model](transmission-lines.md) · [Index](index.md) · [Next: KiCad import →](kicad-import.md)
