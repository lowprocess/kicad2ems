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

Options typed on the command line win over the config file.

## Config files (TOML)

Every option can be a key in a TOML file, with dashes or underscores (`end-db` or `end_db`). openEMS
options may also sit under an `[openems]` table, so one file can hold settings for other tools too;
keys the tool doesn't know are reported and ignored. Lists are allowed where the option takes a
comma-separated list (`nets`, `excite`, `region`, `dump_port`, `converge`, `converge_tol`) and for `dump`.
`no_mask = true` is accepted for "no solder mask".

**Paths.** `board`, `pos`, `compare` and `out` in a config file are relative to **the config file's
folder**, so `kicad2openems path/to/board.toml` works from any directory. Paths typed on the command
line are relative to the working directory. Older configs written for the repository root
(`pos = "examples/..."`) still work: when a path only exists relative to the working directory, it is
used and a `config:` note says so. (Implementation: `config.resolve_path`.)

## Options

### Board

| Option | Default | Meaning |
|---|---|---|
| `board` | — | the `.kicad_pcb` (positional argument or config key) |
| `--pos` | — | pick-and-place file (`.pos` or `.csv`); ports may be listed there instead of a Fab text |
| `--nets` | all copper | nets to include; hierarchical names match their last part |
| `--region x0,y0,x1,y1` | — | crop (KiCad mm) |
| `--ground LAYER` | — | model that copper layer as a solid plane over the outline |
| `--mask` | off | include solder mask layers (their thinness shrinks the time step) |
| `--parts auto\|none` | auto | lumped R/L/C parts from two-pad footprints ([model](model.md#lumped-parts)) |

### Frequency and mesh

| Option | Default | Meaning |
|---|---|---|
| `--fmin`, `--fmax` | 0.5, 10 | band in GHz |
| `--points` | 401 | frequency points |
| `--res` | 20 | cells per wavelength at fmax (in each medium) |
| `--fine` | min(port width, 2 × thinnest dielectric) / 4 | mm, cell size at copper edges |
| `--air` | 5 (antenna: λ/4) | mm of air between the board and the boundary |
| `--ratio` | 1.4 | maximum growth between neighbouring cells |
| `--nz` | 4 | minimum cells through each dielectric |
| `--curves extremes\|vertices` | extremes | mesh lines for curved copper |
| `--vias auto\|exact\|centre\|snap` | auto | via mesh lines |
| `--via-snap` | 0.2 | mm a snapped via may move |
| `--converge F1,F2,...` | — | run several mesh levels ([meshing](meshing.md#convergence-levels)) |
| `--converge-tol DB,DEG` | 0.1,2 | convergence tolerance |

### Solver

| Option | Default | Meaning |
|---|---|---|
| `--bc` | auto | boundaries: `auto` (PEC on a solid bottom plane, MUR elsewhere), one value, or six |
| `--end-db` | −40 | stop when the field energy has fallen by this much |
| `--max-steps` | 1 000 000 | timestep limit per run |
| `--sigma` | PEC | copper conductivity (S/m) |
| `--diel fixed\|wideband` | fixed | dielectric loss model ([simulation](simulation.md#dielectrics)) |
| `--diel-fref` | 1 | GHz at which the stackup εr / tan δ hold (wideband) |
| `--excite` | all ports | ports to drive (`P1`, or `P1,P2`) |
| `--threads` | 0 = all cores | openEMS threads per run |
| `--jobs` | 1 | runs in parallel (separate processes) |

### Antenna mode

| Option | Default | Meaning |
|---|---|---|
| `--mode sparams\|antenna` | sparams | antenna: one driven port, PML on all sides, far field |
| `--ff-freq` | resonance | GHz for the patterns (also recorded) |
| `--ff-step` | 2 | angle step of the pattern cuts (degrees) |

### Field dumps

| Option | Meaning |
|---|---|
| `--dump FIELD@REGION[@GHz,...]` | repeatable; `E`/`H`/`J` at a layer (`F.Cu`, `In1.Cu-0.1`), `x=`/`y=`/`z=` plane, `volume`, `box=x0,y0,z0,x1,y1,z1`; with frequencies: HDF5 + PNG maps, without: time-domain VTK |
| `--dump-port` | the runs that record dumps (default: the first excited port) |

### Output

| Option | Default | Meaning |
|---|---|---|
| `--out` | `openems_out` | output folder |
| `--compare FILE.sNp` | — | overlay another result on the S-parameter plot |
| `--preview` | off | model, mesh, report; no openEMS run |
| `--no-report` | off | skip `<name>_report.html` |
| `--openems-output` | off | show openEMS's own output instead of the progress line |

## Outputs

| File | Written by |
|---|---|
| `<name>_mesh.png` | every run: copper per layer with mesh lines, side view with the z mesh |
| `<name>_report.html` | every run (unless `--no-report`): [report](postprocessing.md#the-report) |
| `<name>_openems.sNp` | S-parameters (mixed-mode for differential ports) |
| `<name>_openems.png` | |S| plot and Smith chart (`--compare` overlaid) |
| `<name>_openems.json` | ports, impedances, mesh statistics, parts, runs (time, steps); convergence / antenna summaries |
| `<name>.xml` | the CSX geometry (AppCSXCAD) |
| `<name>_antenna.png`, `_pattern.png`, `_antenna.json` | antenna mode |
| `<name>_converge.png` | `--converge` |
| `<name>_<port>_<dump>_<GHz>GHz.png` | frequency-domain plane dumps |
| `openems/exc_<port>/` | one openEMS run folder per driven port, with `openems.log` |
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

After each run the number of timesteps is printed, with a warning when the run stopped at
`--max-steps` before the energy fell by `--end-db`.

---
[← Getting started](getting-started.md) · [Index](index.md) · [Next: Architecture →](architecture.md)
