# 2. Usage and configuration

[← Getting started](getting-started.md) · [Index](index.md) · [Next: Architecture →](architecture.md)

## Invocation

```bash
kicad2openems board.toml                         # everything from the config (board = "...")
kicad2openems board.kicad_pcb --config run.toml  # board on the command line, options from the file
kicad2openems board.kicad_pcb --fmin 1 --fmax 6  # no config file
python -m kicad2openems ...                      # same, with a specific Python
kicad2openems --check                            # can this Python run openEMS?
```

## Settings

All options end up in one typed `config.Settings`, built in this order: defaults, then the TOML file,
then the command line. The command line wins, also with abbreviated options (`--max-st 5000` beats
`max-steps` in the file).

A value that cannot be used stops the run with one line and exit code 2:

```
kicad2openems: error: fmax = 'abc': could not convert string to float: 'abc'
```

Set `KICAD2OPENEMS_TRACEBACK=1` to get the Python traceback instead. Checked up front: the choices of
each option, whole numbers, list lengths, fmin < fmax, `points` ≥ 2, at least two positive `converge`
factors (an empty list turns it off), no `converge` in antenna mode.

## Config files (TOML)

Every option except `check` and `config` can be a key, with dashes or underscores (`end-db` or
`end_db`): also `preview`, `no_report`, `openems_output`, `mask` (or `no_mask = true`), `events`, `fast`, `mom`
and `qucsator`. Options may also sit under an `[openems]` table, so one file can hold settings for other
tools; keys the tool doesn't use (a `check` key included) are reported and ignored:

```
config: ignoring keys not used here: check
```

**Lists** can be TOML arrays or comma-separated strings: `nets`, `excite`, `dump_port`, `region` (4
numbers; or an array of [x, y] points, or a drawing layer name), `ports` (an array of port specs), `converge`, `converge_tol` (2 numbers), `ff_freq` and `bc` (1 or 6 values). `dump` takes an
array or a single string.

```toml
board  = "board.kicad_pcb"
nets   = ["RF", "GND"]       # or "RF,GND"
fmax   = 6.0
region = [10, 5, 40, 30]     # or "10,5,40,30"
[openems]
excite = "P1"
dump   = ["E@F.Cu-0.1@2,6"]
```

**Paths.** `board`, `pos`, `compare`, `out`, `events` and `qucsator` in a config file are relative to **the config
file's folder**, so `kicad2openems path/to/board.toml` works from any directory. Paths typed on the
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
| `--dump-port` | the runs that record dumps (default: the first excited port) |

### Transmission-line model

| Option | Default | Meaning |
|---|---|---|
| `--fast skrf\|qucsator` | off | also solve the line sections as a circuit ([transmission lines](transmission-lines.md#fast)) |
| `--mom` | off | also solve the CSX geometry with mom25d, the 2.5D MoM solver ([MoM reference](mom.md)); works with `--preview` |
| `--mom-dmax MM` | 0.3 | largest MoM cell along the copper (planes 3×, refined near the copper of other layers) |
| `--mom-port auto\|wave\|lumped` | auto | MoM ports: de-embedded wave ports where the feed is long enough down to fmin, else lumped |
| `--mom-mesher triangle\|gmsh` | triangle | MoM surface mesher ([MoM reference](mom.md#meshers)) |
| `--mom-solves N` | 60 | most EM solves of the MoM adaptive sweep |
| `--qucsator PATH` | `qucsator_rf` or `qucsator` on PATH | the qucsator executable |

### Output

| Option | Default | Meaning |
|---|---|---|
| `--out` | `openems_out` | output folder |
| `--compare FILE.sNp` | — | overlay another result on the S-parameter plot |
| `--preview` | off | model, mesh, line sections, JSON and report; no openEMS run |
| `--no-report` | off | skip `<name>_report.html` |
| `--openems-output` | off | show openEMS's own output instead of the progress line |
| `--events FILE` | — | also write the run as JSON lines ([below](#events)) |

## Outputs

| File | Written by |
|---|---|
| `<name>_mesh.png` | every run: copper per layer with mesh lines, side view with the z mesh |
| `<name>_tlines.png` | every run with routed port nets: [line sections](transmission-lines.md#outputs) |
| `<name>_openems.json` | every run, also `--preview`: the [run summary](postprocessing.md#json-summary) |
| `<name>_report.html` | every run (unless `--no-report`): [report](postprocessing.md#the-report) |
| `<name>_openems.sNp` | S-parameters (mixed-mode for differential ports) |
| `<name>_openems.png` | \|S\| plot and Smith chart (`--compare` dashed, `--fast` dotted) |
| `<name>_fast.sNp`, `<name>_fast.png` | `--fast` (the PNG with `--preview` only) |
| `<name>_mom.sNp`, `<name>_mom.png` | `--mom` (the PNG with `--preview` only; the openEMS plot shows MoM dash-dotted) |
| `<name>.xml` | CSX geometry (AppCSXCAD), with the `<mom25d>` hints; written by every run (and by `--mom`) |
| `<name>.xml` | the CSX geometry (AppCSXCAD) |
| `<name>_antenna.png`, `_pattern.png`, `_antenna.json` | antenna mode |
| `<name>_converge.png` | `--converge` |
| `<name>_<port>_<dump>_<GHz>GHz.png` | frequency-domain plane dumps |
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
{"t": 99.1, "kind": "file", "label": "S-parameters (Touchstone)", "path": "out/board_openems.s2p"}
{"t": 99.3, "kind": "done", "code": 0}
```

| kind | fields |
|---|---|
| `stage` | `name`: model, mesh, lines, preview, solve, outputs |
| `log` | `text`: every message, as printed |
| `status` | `run` (port), `run_time` (openEMS's clock, s), `step`, `db` (energy), `speed` (MC/s), `fraction` (progress 0–1), `eta` (s or null) |
| `file` | `label`, `path` of each output file |
| `done` | `code`: the exit code |

---
[← Getting started](getting-started.md) · [Index](index.md) · [Next: Architecture →](architecture.md)
