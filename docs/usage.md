# 2. Usage and configuration

[← Getting started](getting-started.md) · [Index](index.md) · [Next: Architecture →](architecture.md)

## Invocation

```bash
kicad2ems board.toml                         # everything from the config (board = "...")
kicad2ems board.kicad_pcb --config run.toml  # board on the command line, options from the file
kicad2ems board.kicad_pcb --fmin 1 --fmax 6  # no config file
kicad2ems board.toml --solvers mom,lines     # other solvers than openEMS (below)
python -m kicad2ems ...                      # same, with a specific Python
kicad2ems --check                            # which solvers can this Python run?
kicad2ems --init board.kicad_pcb --fmax 6    # a commented starter config, board.toml, with the ports found
```

Exit codes: 0 done; 1 a solver named in `--solvers` gave no result (the warning says why; the report is
still written); 2 a setting that cannot be used, or a named solver that cannot run (checked before any
work).

<a id="solvers"></a>
## Solvers

One converted board, several solvers. Each solver is a plugin (`kicad2ems/solvers/`, [architecture](architecture.md#solver-plugins));
`--solvers` (or `solvers = [...]` in a config) names the ones to run. The default is `auto`: the full-wave
solver estimated cheapest for the board, plus the line model ([below](#auto)). The `kicad2openems` command
keeps openEMS as its default, so older scripts and configs give the same results.

| Name (aliases) | Solver | Output | Needs |
|---|---|---|---|
| `openems` (`fdtd`) | openEMS, 3D FDTD: the whole board in its air box, antennas | `<name>_openems.sNp` | openEMS's Python packages ([getting started](getting-started.md)) |
| `mom25d` (`mom`) | mom25d, 2.5D MoM: copper sheets over an infinite ground ([MoM](mom.md)) | `<name>_mom25d.sNp` | nothing more (the `mom25d` package comes with kicad2ems; `python -m mom25d.core.build` makes it ~5x faster) |
| `qucs` (`qucsator`) | the [line model](transmission-lines.md) as a circuit, solved by qucsator | `<name>_qucs.sNp` | Qucs-S's `qucsator_rf` (or `qucsator`) on PATH, or `--qucsator PATH` |
| `scikit` (`skrf`) | the same line model, solved by scikit-rf | `<name>_scikit.sNp` | scikit-rf |
| `lines` | `qucs` when qucsator is installed, else `scikit` | | |
| `auto` | the full-wave solver estimated cheapest for this board, plus the line model ([below](#auto)) | | |
| `all` | every solver, the line model once (`lines`) | | |

```bash
kicad2ems board.toml --solvers mom              # MoM alone: no openEMS needed
kicad2ems board.toml --solvers openems,lines    # = kicad2ems board.toml --fast auto
kicad2ems board.toml --solvers qucs,scikit      # the two circuit solvers on the same line model
kicad2ems board.toml --solvers all              # openEMS, MoM and the line model, compared in one plot and the report
kicad2ems --check                               # which solvers this Python can run, and what each one models
```

- **Named or added.** `--mom` and `--fast` add their solver next to the others as a reference: if it
  cannot run, or cannot model the board, that is a warning and the run goes on. A solver named in
  `--solvers` (and openEMS when nothing is named) is what the run is for: if it cannot run (openEMS or
  mom25d does not import, no scikit-rf or qucsator) or cannot model the board (mom25d and a gap port, or no
  ground plane), the run stops before any solver starts, with exit code 2 and the reason; if it fails during
  the run, the run ends with exit code 1. `--fast skrf` / `--fast qucsator` also says which solver `lines`
  means.
- **Checked first.** Every run starts with the solvers it uses (`solvers: openEMS (FDTD), mom25d (MoM)`)
  and checks they can run before it reads the board, so a missing openEMS is reported in a second, not
  after the MoM solve.
- **Compared with the first.** The first solver of the run (in the order of the table) gives the
  S-parameter plot `<name>_<solver>.png`; the others are overlaid on it and compared with it in the
  report, each in a section of its own.
- **`--preview`** runs no openEMS: the model, openEMS's mesh, the line sections and the report, plus the
  other solvers of the run.
- **Without openEMS** nothing of openEMS is built: no mesh (the model alone is drawn in
  `<name>_model.png`), no run-length estimate, no closed-box resonance check.
- `mode = "antenna"` needs openEMS; mom25d and the line model cannot be named in antenna mode.

<a id="auto"></a>
**`--solvers auto`** leaves out the solvers that cannot run here or cannot model the board (gap ports or
antenna mode: openEMS only; no ground plane: no mom25d; no routed path: no line model), estimates the time
of each full-wave solver left (openEMS from its mesh and the excitation length, mom25d from the unknowns
of its mesh), runs the cheaper one and adds the line model. It says why:

```
solvers auto: mom25d + qucs (4,400 unknowns, ~16 EM solves, ~21 s); openems not chosen (0.40 M cells, 1 run(s)
of ~502,493 timesteps, ~7 min); force one with --solvers <name>
```

The estimates are rough ([architecture](architecture.md#choosing-solvers)). With `--preview`, `auto` solves
no full-wave solver: it says which one would run, and the preview has openEMS's mesh and the line model.

## Settings

All options end up in one typed `config.Settings`, built in this order: defaults, then the TOML file,
then the command line. The command line wins, also with abbreviated options (`--max-st 5000` beats
`max-steps` in the file).

A value that cannot be used stops the run with one line and exit code 2:

```
kicad2ems: error: fmax = 'abc': could not convert string to float: 'abc'
```

Set `KICAD2EMS_TRACEBACK=1` to get the Python traceback instead. Checked up front: the choices of
each option, whole numbers, list lengths, fmin < fmax, `points` ≥ 2, at least two positive `converge`
factors (an empty list turns it off), no `converge` in antenna mode.

## Config files (TOML)

Every option except `check` and `config` can be a key at the top level, with dashes or underscores
(`end-db` or `end_db`): also `preview`, `no_report`, `openems_output`, `mask` (or `no_mask = true`),
`events`, `solvers`, `fast`, `mom` and `qucsator`. A solver's own options may also sit under its table,
`[solver.<name>]` (any alias works: `[solver.mom]`), by their short names: mom25d's `mom_dmax` is `dmax`
there, qucs's `qucsator` is `path`. An `[openems]` table, the older form, is read like the top level, with a
note. Other tables belong to other tools and are left alone; keys the tool doesn't use (a `check` key
included) are reported and ignored, with the option a key is probably a typo of:

```
config: ignoring keys not used here: check, fmx (did you mean fmax?), solver.mom25d.dmx (did you mean dmax?)
```

**Lists** can be TOML arrays or comma-separated strings: `nets`, `excite`, `dump_port`, `region` (4
numbers; or an array of [x, y] points, or a drawing layer name), `ports` (an array of port specs), `converge`, `converge_tol` (2 numbers), `ff_freq` and `bc` (1 or 6 values). `dump` takes an
array or a single string.

```toml
board   = "board.kicad_pcb"
nets    = ["RF", "GND"]       # or "RF,GND"
fmax    = 6.0
region  = [10, 5, 40, 30]     # or "10,5,40,30"
solvers = ["openems", "mom25d"]
[solver.openems]
excite  = "P1"
dump    = ["E@F.Cu-0.1@2,6"]
[solver.mom25d]
dmax    = 0.4
```

**Paths.** `board`, `pos`, `compare`, `out`, `events` and `qucsator` in a config file are relative to **the config
file's folder**, so `kicad2ems path/to/board.toml` works from any directory. Paths typed on the
command line are relative to the working directory. Older configs written for the repository root
(`pos = "examples/..."`) still work: when a path only exists relative to the working directory, it is
used and a `config:` note says so (`config.resolve_path`).

## Options

### Board

| Option | Default | Meaning |
|---|---|---|
| `board` | — | the `.kicad_pcb`, or a `.toml` config naming it (positional argument or config key) |
| `--pos` | — | pick-and-place file (`.pos` or `.csv`); ports may be listed there instead of a Fab text |
| `--nets` | all copper | nets to include (hierarchical names match their last part), or `auto`: the nets under the ports plus the ground nets |
| `--region x0,y0,x1,y1\|LAYER` | — | crop to a rectangle (KiCad mm) or the shapes on a drawing layer (`User.1`); a config also takes a list of [x, y] points ([the model](model.md#region)) |
| `--cut-ports` | off | a port wherever a signal track crosses the region edge |
| `--port SPEC` | — | an extra port: `P3=J1.1` (pad) or `P3@x,y[,layer[,rotation]]` (point); repeatable ([ports](model.md#extra-ports)) |
| `--ground LAYER` | — | model that copper layer as a solid plane over the outline |
| `--mask` | off | include solder mask layers (their thinness shrinks the time step) |
| `--parts auto\|none` | auto | lumped R/L/C parts from two-pad footprints ([model](model.md#lumped-parts)) |

### Frequency and mesh

| Option | Default | Meaning |
|---|---|---|
| `--fmin`, `--fmax` | 0.5, 10 | band in GHz |
| `--points` | 401 | frequency points |
| `--res` | 20 | cells per wavelength at fmax (in each medium) |
| `--fine` | see [meshing](meshing.md#cell-sizes) | mm, cell size at copper edges; default min(narrowest port trace, 2 × thinnest dielectric) / 4 |
| `--air` | 5 (antenna mode: λ/4 at the band centre, at least 5) | mm of air between the board and the boundary |
| `--ratio` | 1.4 | maximum growth between neighbouring cells |
| `--nz` | 4 | minimum cells through each dielectric |
| `--curves extremes\|vertices` | extremes | mesh lines for curved copper |
| `--vias auto\|exact\|centre\|snap` | auto | via mesh lines |
| `--via-snap` | 0.2 | mm a snapped via may move |
| `--edge-walls SIDES` | off | PEC walls where the ground planes end at these sides (`xmin,xmax,ymin,ymax` in KiCad coordinates, or `all`), standing in for edge stitching ([edge walls](meshing.md#edge-walls)) |
| `--edge-keepout` | 0.5 | mm between edge walls and signal copper |
| `--converge F1,F2,...` | — | run several mesh levels ([meshing](meshing.md#convergence-levels)) |
| `--converge-tol DB,DEG` | 0.1,2 | convergence tolerance |

### Solver

| Option | Default | Meaning |
|---|---|---|
| `--bc` | auto | boundaries: `auto` (PEC on a solid bottom plane, MUR elsewhere), or one value for all sides, or six (`xmin,xmax,ymin,ymax,zmin,zmax`), each `PEC`, `PMC`, `MUR` or `PML_n` (any n, e.g. `PML_8`) |
| `--end-db` | −40 | stop when the port signals or the field energy have fallen by this much ([simulation](simulation.md#ending-a-run)) |
| `--end-on ports\|energy` | ports | `energy`: only openEMS's field-energy criterion |
| `--max-steps` | 1 000 000 | timestep limit per run |
| `--sigma` | PEC | copper conductivity (S/m) |
| `--diel fixed\|wideband` | fixed | dielectric loss model ([simulation](simulation.md#dielectrics)) |
| `--diel-fref` | 1 | GHz at which the stackup εr / tan δ hold (wideband) |
| `--excite` | all ports | ports to drive (`P1`, or `P1,P2`) |
| `--threads` | 0 | openEMS threads per run; 0 = all cores with one job, half the logical CPUs shared by the runs with several jobs |
| `--jobs` | 1 | excitations run in parallel (separate processes) |

### Antenna mode

| Option | Default | Meaning |
|---|---|---|
| `--mode sparams\|antenna` | sparams | antenna: one driven port, PML on all sides, far field ([post-processing](postprocessing.md#antenna-mode)) |
| `--ff-freq` | resonance | GHz for the patterns (also recorded) |
| `--ff-step` | 2 | angle step of the pattern cuts (degrees) |

### Field dumps

| Option | Meaning |
|---|---|
| `--dump FIELD@REGION[@GHz,...]` | repeatable; `E`/`H`/`J` at a layer, a plane, the volume or a box ([post-processing](postprocessing.md#field-dumps)) |
| `--dump-port` | the runs that record dumps (default: the first excited port); mom25d's current dumps too |
| `--dump-animate gif\|mp4` | frequency-domain dumps (openEMS and `--mom-dump`): also one period of Re(F·e^jφ) at each frequency, the standing waves ([post-processing](postprocessing.md#animations)); MP4 needs ffmpeg |

### Solvers

| Option | Default | Meaning |
|---|---|---|
| `--solvers LIST` | `auto` (`kicad2openems`: `openems`) | the solvers to run ([above](#solvers)): `openems`, `mom25d`, `qucs`, `scikit`, `lines` or `all` |
| `--fast auto\|skrf\|qucsator` | off | also solve the line sections as a circuit, as a reference ([transmission lines](transmission-lines.md#fast)); `auto`: qucsator if installed, else scikit-rf |
| `--mom` | off | also run mom25d, as a reference ([MoM](mom.md)); works with `--preview` |

### mom25d (`[solver.mom25d]`)

| Option (short name) | Default | Meaning |
|---|---|---|
| `--mom-dmax MM` (`dmax`) | 0.3 | largest MoM cell along the copper (planes 3×, refined near the copper of other layers) |
| `--mom-port auto\|wave\|lumped` (`port`) | auto | MoM ports: de-embedded wave ports where the feed is long enough down to fmin, else lumped |
| `--mom-mesher triangle\|gmsh` (`mesher`) | triangle | MoM surface mesher ([MoM](mom.md#meshers)) |
| `--mom-solves N` (`solves`) | 60 | most EM solves of the MoM adaptive sweep |
| `--mom-dump GHz,...` (`dump`) | — | surface-current dumps at these frequencies: a \|J\| map per signal layer, with `--dump-animate` one period of Re(J·e^jφ) ([MoM](mom.md#current-dumps)) |

### Line model (`[solver.qucs]`)

| Option (short name) | Default | Meaning |
|---|---|---|
| `--qucsator PATH` (`path`) | `qucsator_rf` or `qucsator` on PATH | the qucsator executable |

### Output

| Option | Default | Meaning |
|---|---|---|
| `--out` | `out` next to the board | output folder (for the `<name>_board.json` of an earlier run: its folder) |
| `--compare FILE.sNp` | — | overlay another result on the S-parameter plot |
| `--preview` | off | model, openEMS's mesh, line sections, the other solvers, JSON and report; no openEMS run |
| `--no-report` | off | skip `<name>_report.html` |
| `--openems-output` | off | show openEMS's own output instead of the progress line |
| `--events FILE` | — | also write the run as JSON lines ([below](#events)) |

## Outputs

| File | Written by |
|---|---|
| `<name>_board.json` | every run: the [board](model.md) every solver gets (also accepted as the board of a later run) |
| `<name>_mesh.png` | runs with openEMS (and `--preview`): copper per layer with mesh lines, side view with the z mesh |
| `<name>_model.png` | runs without openEMS: copper per layer, side view |
| `<name>_tlines.png` | every run with routed port nets: [line sections](transmission-lines.md#outputs) |
| `<name>_run.json` | every run, also `--preview`: the [run summary](postprocessing.md#json-summary) (older runs: `<name>_openems.json`) |
| `<name>_report.html` | every run (unless `--no-report`): [report](postprocessing.md#the-report) |
| `<name>_<solver>.sNp` | each solver's S-parameters: `openems`, `mom25d`, `qucs`, `scikit` (mixed-mode for differential ports); terms not computed (ports not driven, no path in the line model) are written as 0 and named in the header comment |
| `<name>_<solver>.png` | the first solver's \|S\| plot and Smith chart, the others overlaid (dotted, dash-dotted, ...), `--compare` dashed |
| `<name>.xml` | CSX geometry (AppCSXCAD), written by every openEMS run |
| `<name>_antenna.png`, `_pattern.png`, `_antenna.json` | antenna mode |
| `<name>_converge.png` | `--converge` |
| `<name>_<port>_<dump>_<GHz>GHz.png` | frequency-domain plane dumps (`.gif` / `.mp4` with `--dump-animate`) |
| `<name>_mom25d_<port>_<dump>_<GHz>GHz.png` | mom25d current dumps (`--mom-dump`; `.gif` / `.mp4` with `--dump-animate`) |
| `mom25d/exc_<port>/` | mom25d's current dumps as grids (`dump_J_<layer>_fd.npz`, `dump_info.json`), for the GUI's Fields tab |
| `openems/exc_<port>/` | one openEMS run folder per driven port, with `openems.log` (`P1+` / `P1-` → `exc_P1p` / `exc_P1n`) |
| `converge/L<f>/` | the coarser convergence levels |

## Progress and the openEMS log

During a run, openEMS's own output goes to `openems/exc_<port>/openems.log` and the terminal shows a
progress line per run:

```
P1 [##############------]  71%  step 23184  -28.4/-40 dB  305 MC/s  ETA 18s
```

The percentage is the further of the energy decay (towards `--end-db`) and the step count (towards
`--max-steps`). The ETA follows the energy trend; `<= … (step limit)` means the energy is not
falling fast enough and the step limit will end the run first; `energy flat` points at a static
field (see [maintenance](maintenance.md#static-plateau)). In a terminal the line is redrawn in place;
in a log file it appears every 30 s. `--openems-output` shows openEMS's own lines instead.

With `--jobs` above 1, one monitor shows all runs on one line (`P1 [...] | P2 [...]`; with
`--openems-output`, the raw lines prefixed with the port); openEMS's output still goes to each run's
`openems.log`. After each run the number of timesteps is printed, with a warning when the run stopped at
`--max-steps` before the energy fell by `--end-db`.

<a id="events"></a>
## Events for a GUI or a script

`--events FILE` writes the run as JSON lines, flushed as they are written (`events.py`); its folder is
created if needed. `t` is seconds since the start; NaN and infinite numbers are written as `null`:

```
{"t": 0.0, "kind": "stage", "name": "model"}
{"t": 0.4, "kind": "log", "text": "mesh: 188 x 115 x 25 = 0.54 M cells ..."}
{"t": 9.8, "kind": "status", "run": "P1", "run_time": 8.0, "step": 6118, "db": -3.2, "speed": 138.0, "fraction": 0.08, "eta": 95.0}
{"t": 99.1, "kind": "file", "label": "S-parameters, openEMS (FDTD)", "path": "out/board_openems.s2p"}
{"t": 99.3, "kind": "done", "code": 0}
```

| kind | fields |
|---|---|
| `stage` | `name`: model, mesh, lines, preview, solve, outputs |
| `log` | `text`: every message, as printed |
| `status` | openEMS: `run` (port), `run_time` (openEMS's clock, s), `step`, `db` (energy), `speed` (MC/s), `fraction` (progress 0–1), `eta` (s or null); mom25d: `run` = `mom25d`, `solves` (EM solves so far), `f_ghz` (the last one), `fraction` |
| `file` | `label`, `path` of each output file |
| `done` | `code`: the exit code |

---
[← Getting started](getting-started.md) · [Index](index.md) · [Next: Architecture →](architecture.md)
