# Usage

[← Getting started](getting-started.md) · [Index](index.md) · [Next: GUI →](gui.md)

## Running kicad2ems

```bash
kicad2ems board.toml                         # everything from a config file
kicad2ems board.kicad_pcb --fmin 1 --fmax 6  # no config file
kicad2ems board.kicad_pcb --config run.toml  # the board on the command line, options from a file
kicad2ems board.toml --preview               # build the model and mesh, don't solve
kicad2ems --init board.kicad_pcb             # write a starter config with the ports found
kicad2ems --check                            # what this Python can run
```

`python -m kicad2ems` does the same with a particular Python.

The exit code is 0 when the run finished, 1 when a solver you named gave no result, and 2 when a setting
is invalid or a named solver cannot run. Settings and solvers are checked before any work starts.

<a id="solvers"></a>
## Choosing solvers

`--solvers` (or `solvers = [...]` in a config) picks the solvers. Each writes `<name>_<solver>.sNp`.

| Name | Solver | Needs |
|---|---|---|
| `openems` (or `fdtd`) | openEMS, 3D FDTD | openEMS |
| `gfdtd` | gfdtd, 3D FDTD on the GPU or the CPU: openEMS's model, its own engine ([details](gfdtd.md)) | CuPy for the GPU, or its compiled core |
| `mom25d` (or `mom`) | mom25d, 2.5D method of moments ([details](mom.md)) | nothing extra |
| `mixed` | mom25d with long straight runs as transmission lines ([details](mom.md#mixed)) | nothing extra |
| `qucs` | the [line model](transmission-lines.md), solved by qucsator | qucsator on PATH |
| `scikit` | the line model, solved by scikit-rf | scikit-rf |
| `lines` | `qucs` if qucsator is installed, otherwise `scikit` | |
| `auto` | the default: the full-wave solver expected to be fastest, plus the line model | |
| `all` | every solver | |

```bash
kicad2ems board.toml --solvers mom             # MoM only
kicad2ems board.toml --solvers openems,lines   # openEMS and the line model
kicad2ems board.toml --solvers all             # everything, compared in one plot
```

The first solver in the table order is the reference: its plot is `<name>_<solver>.png`, and the others
are drawn over it and compared with it in the report.

`--mom` and `--fast` add mom25d or the line model as an extra reference. If a reference can't run, you
get a warning and the run continues. If a solver named in `--solvers` can't run or can't model the
board, the run stops before starting.

Antenna mode (`mode = "antenna"`) needs openEMS, gfdtd or mom25d. mom25d models antennas over an infinite
ground plane, so it suits patches and arrays but not antennas that use the board edge.

<a id="which"></a>
### Which solver

gfdtd and mom25d are the working solvers, and the line model is for tuning. openEMS is the slower reference,
for what the other two lack and for checking gfdtd's engine.

Here is how close each solver came to openEMS, and how long it took. The difference is the largest |ΔS|
from openEMS over S11 and S21 (openEMS drove P1 only). The times are wall times from the
[benchmarks](benchmarks.md): 24 CPU threads, and an RX 7800 XT for gfdtd and mom25d. gfdtd ran on 8 of the
examples.

| Example | openEMS | gfdtd (GPU) | mom25d (GPU) | Line model |
|---|---|---|---|---|
| sma_launch | 21.5 s | 0.001, 2.8 s | 0.066, 108 s | 0.47, 0.0 s |
| hairpin | 45 s | 0.002, 5.5 s | 0.106, 7.0 s | 0.65, 0.3 s |
| rf_wilkinson | 33 s | 0.006, 4.2 s | 0.040, 4.5 s | 0.09, 0.3 s |
| rf_coupler | 26 s | 0.006, 2.9 s | 0.045, 10.1 s | 0.09, 0.1 s |
| filter | 118 s | 0.012, 10.2 s | 0.267, 14.7 s | 0.82, 0.4 s |
| rf_branchline | 8.2 s | 0.012, 0.7 s | 0.052, 23.0 s | 0.06, 0.2 s |
| antenna_patch | 25 s | 0.020, 2.6 s | 0.224, 9.3 s | – |
| demo_bandpass | 84 s | 0.126, 2.4 s | 0.093, 36.3 s | 0.47, 0.2 s |

**gfdtd** solves openEMS's model (the same mesh, ports, parts and end criteria) with its own engine.
- It was the fastest full-wave solver on every example: 3–35 times faster than openEMS on the GPU, and its
  CPU core was faster than openEMS on all 8 of them.
- It agrees with openEMS to 0.02 or better on 7 of the 8.
- It handles what FDTD handles: board edges, enclosures and cavities, antennas with a finite ground, and gap
  ports.
- Its copper is lossless, so `--sigma` is ignored.
- It drives one port at a time, so there is no phased drive (`--drive`), and it has no `--converge` levels.
- Its lumped parts are modelled differently from openEMS's. That is where demo_bandpass's 0.126 comes from.
- It shares openEMS's mesh and its simplifications: vias snapped to mesh lines, and copper as zero-thickness
  sheets. Agreeing with openEMS checks gfdtd's engine, not the model.

**mom25d** is a different method and doesn't use the FDTD mesh, so it is the independent cross-check.
- It has no air box and no enclosure resonances. Radiation and surface waves are in.
- It includes conductor loss (`--sigma`), and it de-embeds wave ports on straight feed lines.
- On thin trace filters it is quick: on the CPU it beat gfdtd's core 3.5 times on hairpin and 1.4 times on
  filter.
- Boards with large planes and many vias are slow and memory-hungry. Memory grows with the square of the
  unknowns and time with their cube: sma_launch took 108 s against gfdtd's 2.8 s.
- It ignores the board outline, because the dielectric extends sideways forever. Antennas that use the board
  edge or a finite ground come out wrong (antenna_ifa and antenna_slot are 1.4 from openEMS).
- It can't model gap ports (the dipole), and its loss tangent doesn't vary with frequency.
- On most boards it lands 0.04–0.3 from openEMS, and further on a few (rf_ratrace 0.62, rf_gcpw 0.51). Part of
  that is the different physics.

**openEMS** is the reference.
- It is the most widely used and does the most: conductor loss, phased drive and `--converge`.
- It runs on the CPU only, and it was the slowest full-wave solver on all but the smallest boards (8 s on
  rf_branchline and rf_ratrace).

**mixed** is mom25d with the middle of each long, lone, straight run put back as a transmission line.
- It needs fewer unknowns than mom25d on boards of long traces, and it has mom25d's limits.
- Coupled stretches are never cut, and it isn't part of `all`.

**The line model** is a circuit of line sections, solved in milliseconds. It's right for topology and tuning.
- It is close on plain distributed circuits: branchline 0.06, coupler and wilkinson 0.09.
- It misses coupling, radiation and parasitics, so it is 0.4–0.8 off on filters and transitions.
- It can't do antennas.

<a id="auto"></a>
### `auto`

`auto` drops the solvers that can't run here or can't model the board, estimates how long each remaining
full-wave solver would take (gfdtd, mom25d, openEMS), runs the fastest and adds the line model. gfdtd's estimate
is openEMS's mesh and timesteps at gfdtd's measured speed: about 8 times openEMS's on the GPU and 2 times on
its CPU core. With `--sigma`, `--converge` or `--drive`, auto leaves gfdtd out, because it would ignore them.
openEMS then runs where mom25d can't. The log says what it chose:

```
solvers auto: gfdtd + qucs (0.31 M cells, 1 run(s) of ~146,729 timesteps on the GPU, ~13 s); openems not chosen
(0.31 M cells, 1 run(s) of ~146,729 timesteps, ~89 s); mom25d not chosen (4,763 unknowns, ~16 EM solves, ~19 s);
force one with --solvers <name>
```

The estimates are rough. The mom25d estimate accounts for a GPU if there is one. With `--preview`, auto
only reports what it would run.

## Config files

A config is a TOML file. Every option can be a top-level key, with dashes or underscores (`end-db` or
`end_db`). A solver's own options can also go in a `[solver.<name>]` table under their short names:

```toml
board   = "board.kicad_pcb"
nets    = ["RF", "GND"]
fmax    = 6.0
solvers = ["openems", "mom25d"]

[solver.openems]
excite  = "P1"
dump    = ["E@F.Cu-0.1@2,6"]

[solver.mom25d]
dmax    = 0.4
```

- Lists can be TOML arrays or comma-separated strings (`nets = "RF,GND"`).
- Paths in a config are relative to the config file, so you can run it from any directory.
- Unknown keys are reported, with the option they are probably a typo of.
- Settings are applied in this order: defaults, then the config file, then the command line.
- An invalid value stops the run with a one-line error. Set `KICAD2EMS_TRACEBACK=1` for the traceback.

<a id="options"></a>
## Options

### Board

| Option | Default | Meaning |
|---|---|---|
| `board` | | the `.kicad_pcb`, or a `.toml` that names it |
| `--pos` | | a pick-and-place file (ports can be listed there instead of a Fab text) |
| `--nets` | all copper | the nets to include, or `auto`: the port nets and the ground |
| `--region x0,y0,x1,y1\|LAYER` | | crop to a rectangle (KiCad mm) or to the shapes on a drawing layer ([details](model.md#region)) |
| `--cut-ports` | off | add a port where a signal track crosses the region edge |
| `--port SPEC` | | an extra port: `P3=J1.1` (a pad) or `P3@x,y[,layer[,rotation]]` ([details](model.md#extra-ports)) |
| `--ground LAYER` | | treat this copper layer as a solid plane |
| `--mask` | off | include the solder mask |
| `--parts auto\|none` | auto | lumped R/L/C parts from two-pad footprints ([details](model.md#lumped-parts)) |

### Frequency and mesh (openEMS)

| Option | Default | Meaning |
|---|---|---|
| `--fmin`, `--fmax` | 0.5, 10 | the band, GHz |
| `--points` | 401 | frequency points |
| `--res` | 20 | cells per wavelength at fmax |
| `--fine` | automatic | cell size at copper edges, mm ([details](meshing.md#cell-sizes)) |
| `--air` | 5 | air between the board and the boundary, mm (antenna mode: at least λ/4) |
| `--ratio` | 1.4 | largest size ratio of neighbouring cells |
| `--nz` | 4 | fewest cells through each dielectric layer |
| `--curves extremes\|vertices` | extremes | mesh lines for curved copper |
| `--z-sheet` | 1 | first z cell next to signal copper, × the fine cell, graded into the dielectric; 0: uniform ([details](meshing.md#z-lines)) |
| `--curve-res` | 6 | over arcs and fans the largest cell is the dielectric's ÷ this; 1: off |
| `--vias auto\|exact\|centre\|snap` | auto | mesh lines for vias |
| `--via-snap` | 0.2 | how far a snapped via may move, mm |
| `--edge-walls SIDES` | off | metal walls at the board edge, standing in for stitching vias (`xmin,xmax,ymin,ymax` or `all`; [details](meshing.md#edge-walls)) |
| `--edge-keepout` | 0.5 | gap between edge walls and signal copper, mm |
| `--converge F1,F2,...` | | also run coarser meshes and check the result has converged ([details](meshing.md#convergence-levels)) |
| `--converge-tol DB,DEG` | 0.1,2 | the convergence tolerance |

### openEMS run

| Option | Default | Meaning |
|---|---|---|
| `--bc` | auto | boundaries: `PEC`, `PMC`, `MUR` or `PML_8`, one value or six; auto puts PEC under a solid bottom plane and MUR elsewhere |
| `--end-db` | −40 | stop when the signals have decayed by this much ([details](simulation.md#ending-a-run)) |
| `--end-on ports\|energy` | ports | what the decay is measured on |
| `--max-steps` | 1 000 000 | the timestep limit per run |
| `--sigma` | perfect conductor | copper conductivity, S/m |
| `--diel fixed\|wideband` | fixed | the dielectric loss model ([details](simulation.md#dielectrics)) |
| `--diel-fref` | 1 | the frequency at which the stackup's εr and tan δ apply, GHz |
| `--excite` | all ports | the ports to drive |
| `--threads` | 0 | openEMS threads per run (0: automatic) |
| `--jobs` | 1 | runs in parallel |

### Antenna mode

| Option | Default | Meaning |
|---|---|---|
| `--mode sparams\|antenna` | sparams | antenna: one driven port, absorbing boundaries, the far field ([details](postprocessing.md#antenna-mode)) |
| `--drive` | one port | drive several ports, `PORT[=amplitude][@phase]` each: a phased array (mom25d; [details](mom.md#phased-drive)) |
| `--grid` | a line | the ports' places in a 2D array: `COLSxROWS` (ports in order, row by row) or `PORT:COL,ROW ...` ([details](mom.md#phased-drive)) |
| `--steer` | none | phase steps added to `--drive`: `X[,Y]`, X per column and Y per row of `--grid` (no grid: X from port to port) |
| `--taper` | uniform | amplitude taper for lower sidelobes: `taylor`, `chebyshev` or `cosine`, `:SLL` in dB (e.g. `taylor:25`) |
| `--ff-freq` | the resonance | the frequencies of the radiation patterns, GHz |
| `--ff-step` | 2 | the angle step of the pattern cuts, degrees |

### Field dumps

| Option | Meaning |
|---|---|
| `--dump FIELD@REGION[@GHz,...]` | save E, H or J on a layer, a plane, the volume or a box; repeatable ([details](postprocessing.md#field-dumps)) |
| `--dump-port` | which driven port's run records the dumps (default: the first) |
| `--dump-animate gif\|mp4` | also animate each frequency-domain dump over one period (MP4 needs ffmpeg) |

### Solvers

| Option | Default | Meaning |
|---|---|---|
| `--solvers LIST` | `auto` | the solvers to run ([above](#solvers)) |
| `--fast auto\|skrf\|qucsator` | off | also solve the line model, as a reference |
| `--mom` | off | also run mom25d, as a reference |

### mom25d (`[solver.mom25d]`)

| Option (short name) | Default | Meaning |
|---|---|---|
| `--mom-dmax MM` (`dmax`) | 0.3 | largest mesh cell along the copper, mm (planes get 3× this) |
| `--mom-port auto\|wave\|lumped` (`port`) | auto | port type: de-embedded wave ports where the feed line is long enough, otherwise lumped |
| `--mom-solves N` (`solves`) | 60 | most frequencies the adaptive sweep may solve |
| `--mom-tiled auto\|on\|off` (`tiled`) | auto | the tiled GPU solver for boards too big for the dense one ([details](mom.md#gpu)) |
| `--mom-dump GHz,...` (`dump`) | | save the surface currents at these frequencies ([details](mom.md#current-dumps)) |
| `--mom-near MM,...` (`near`) | | with `--mom-dump`: also the E field this far above the board |

### Line model (`[solver.qucs]`)

| Option (short name) | Default | Meaning |
|---|---|---|
| `--qucsator PATH` (`path`) | found on PATH | the qucsator program |

### Output

| Option | Default | Meaning |
|---|---|---|
| `--out` | `out` next to the board | the output folder |
| `--compare FILE.sNp` | | draw another result on the S-parameter plot |
| `--preview` | off | build the model and mesh, don't solve |
| `--no-report` | off | skip the HTML report |
| `--openems-output` | off | show openEMS's own output instead of the progress line |
| `--events FILE` | | also write progress as JSON lines ([below](#events)) |

<a id="outputs"></a>
## Output files

| File | Contents |
|---|---|
| `<name>_<solver>.sNp` | each solver's S-parameters (terms that weren't computed are 0 and listed in the header) |
| `<name>_<solver>.png` | the S-parameter plot and Smith chart, the other solvers overlaid |
| `<name>_report.html` | everything on one page ([details](postprocessing.md#the-report)) |
| `<name>_run.json` | the run summary ([details](postprocessing.md#json-summary)) |
| `<name>_board.json` | the converted board every solver gets ([details](model.md)) |
| `<name>_mesh.png` / `_model.png` | the openEMS mesh, or the model alone without openEMS |
| `<name>_tlines.png` | the line sections ([details](transmission-lines.md#outputs)) |
| `<name>_lines.json` | the line sections' geometry and impedances, for the GUI's Viewer |
| `<name>.xml` | openEMS's geometry, for AppCSXCAD |
| `<name>_antenna.png`, `_pattern.png`, `_antenna.json` | antenna results |
| `<name>_converge.png` | the mesh convergence check |
| `<name>_mesh_<solver>.npz` | each solver's mesh for the GUI's 3D view: openEMS's grid lines, mom25d's surface triangles (written by a preview too) |
| `<name>_<port>_<dump>_<GHz>GHz.png` (`.gif`, `.mp4`) | field dump images and animations |
| `openems/exc_<port>/` | one openEMS run folder per driven port, with its log and dumps |
| `mom25d/exc_<port>/` | mom25d's current dumps |

## Progress

While openEMS runs, its output goes to `openems/exc_<port>/openems.log` and the terminal shows a progress
line:

```
P1 [##############------]  71%  step 23184  -28.4/-40 dB  305 MC/s  ETA 18s
```

The bar follows the signal decay towards `--end-db`, or the step count towards `--max-steps`, whichever
is further. `energy flat` means the field isn't decaying (see
[maintenance](maintenance.md#static-plateau)). With `--jobs` above 1, all runs share one line.

<a id="events"></a>
## Events for a GUI or script

`--events FILE` writes the run as JSON lines as it goes. `t` is seconds since the start.

```
{"t": 0.0, "kind": "stage", "name": "model"}
{"t": 0.4, "kind": "log", "text": "mesh: 188 x 115 x 25 = 0.54 M cells ..."}
{"t": 9.8, "kind": "status", "run": "P1", "step": 6118, "db": -3.2, "fraction": 0.08, "eta": 95.0}
{"t": 99.1, "kind": "file", "label": "S-parameters, openEMS (FDTD)", "path": "out/board_openems.s2p"}
{"t": 99.3, "kind": "done", "code": 0}
```

| kind | Fields |
|---|---|
| `stage` | `name`: model, mesh, lines, preview, solve, outputs |
| `log` | `text`: each message as printed |
| `status` | openEMS: `run` (the port), `step`, `db`, `speed`, `fraction`, `eta`; mom25d: `run` = `mom25d`, `solves`, `f_ghz`, `fraction` |
| `file` | `label` and `path` of each output file |
| `done` | `code`: the exit code |

---
[← Getting started](getting-started.md) · [Index](index.md) · [Next: GUI →](gui.md)
