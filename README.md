# kicad2ems

kicad2ems turns a KiCad board into electromagnetic simulations. It reads the real stackup, copper,
vias and board outline, takes ports from footprints and lumped R/L/C parts from the components, and
solves the converted board with the solvers you choose, writing S-parameters (Touchstone) with plots, a
JSON run summary and an HTML report that compares them:

| Solver | `--solvers` | Good for | Needs |
|---|---|---|---|
| [openEMS](https://openems.de), 3D FDTD | `openems` (default) | anything: planes, edges, connectors, box effects, antennas (gain, efficiency, patterns) | openEMS's Python packages |
| [mom25d](docs/mom25d.md), 2.5D Method of Moments | `mom` | planar circuits over a ground plane: filters, couplers, matching networks (often 5× faster than FDTD, no ring-down) | included (`mom25d/`); no openEMS |
| transmission-line model as a circuit (scikit-rf or qucsator) | `lines` | a ballpark in milliseconds: impedance steps, delays, where a layout departs from its design | scikit-rf, or Qucs-S's qucsator |

Every run also breaks the port nets into transmission-line sections with closed-form impedances.

## Features

- **KiCad import** (KiCad 5 to 10): stackup with εr / tan δ, tracks, arcs, filled zones, copper
  graphics, all pad shapes, vias and plated holes, outline from any Edge.Cuts mix.
- **Ports** from footprints with a Fab text `P1`, `P2`, …: vertical lumped ports on feed lines,
  differential pairs (`P1+` / `P1-`, mixed-mode results), gap ports for dipoles and slots.
- **Lumped parts** from two-pad R / L / C footprints, with KiCad simulation fields and optional ESR / ESL.
- **Automatic mesh** (thirds rule, curves, vias, graded cells) with a mesh-convergence check.
- **S-parameters** of all ports, or **antenna mode** (PML, near-to-far field: gain, efficiency,
  patterns).
- **Transmission-line sections** (microstrip, stripline, grounded CPW, vias, pads, parts, resonators
  drawn as copper shapes, coupled lines) with Z0, εeff and delay; `--fast qucsator` solves them as a
  circuit with qucsator's corners, tees, steps, open ends and coupled lines (`--fast skrf` too).
- **Edge walls** (`--edge-walls`): PEC where the planes end at the board edge, for stitching.
- **Field dumps** (E, H, J) as PNG maps, in the GUI (live while the run goes, any x / y / z cut of a
  volume, saved as GIF / MP4) or for ParaView; wideband dielectric model; parallel runs.
- **GUI** (`kicad2ems-gui`): setup, preview, run with progress, S-parameters, field view, antenna
  far field (polar cuts and 3D pattern at any recorded frequency).
- **MoM** (`--solvers mom`, or `--mom` next to openEMS): the same converted board (`<name>_board.json`)
  solved by [mom25d](docs/mom25d.md), the 2.5D Method-of-Moments solver in `mom25d/`, which reads that
  file itself: MoM needs no openEMS.
- **Solver plugins**: each solver is a plugin with its own settings (`[solver.<name>]` in a config); others
  install as packages ([architecture](docs/architecture.md#solver-plugins)). `--solvers auto` picks the
  full-wave solver estimated cheapest for the board; each solver declares what it changes, and the report
  shows what each one saw.
- **TOML configs** (typos named), `--preview`, `--check` for every solver, an HTML report, JSON-lines
  events for a GUI or script.

## Install

The quick way: `./setup.sh` makes a virtual environment in `.venv/` and asks about each optional part: the
GUI, field dumps, scikit-rf, gmsh, mom25d's compiled core, the CuPy wheel for your GPU (it detects CUDA 12 / 13 or
ROCm 7), and openEMS (a local build into `.openems/` with `tools/build_openems.sh`, an existing install, or
none). `./setup.sh --yes` takes the defaults (no openEMS build). Then `source .venv/bin/activate`.

By hand:

```bash
git clone https://github.com/lowprocess/kicad2ems.git && cd kicad2ems
pip install .                 # numpy, scipy, shapely, matplotlib, triangle (the MoM solver's mesher)
pip install '.[fast]'         # + scikit-rf: the line model as a circuit
pip install '.[dumps]'        # + h5py, for frequency-domain field dumps
python -m mom25d.core.build   # optional: the MoM solver's compiled core, ~5x faster (g++ with OpenMP, pybind11)
pip install cupy-rocm-7-0     # optional, one of: the MoM solver on an AMD GPU (ROCm 7) ...
pip install cupy-cuda12x      # ... or NVIDIA (CUDA 12; cupy-cuda13x for CUDA 13)
```

The same CuPy wheels as extras of this package: `pip install '.[gpu-rocm7]'`, `'.[gpu-cuda12]'`,
`'.[gpu-cuda13]'`.

openEMS and its Python bindings are not on PyPI. Install them from your distribution, or build them with
`tools/build_openems.sh [prefix]`, and install kicad2ems into the same Python environment. Then
check which solvers this Python can run:

```bash
kicad2ems --check
```

Without openEMS, everything except the FDTD run works: the model, mesh, line sections, `--preview`, the
report, the MoM solver and the line model (`--solvers mom,lines`). Details and troubleshooting:
[Getting started](docs/getting-started.md).

## Quick start

The microstrip filter example comes with a config (`examples/filter.toml`) that names the board:

```bash
kicad2ems examples/filter.toml --preview              # model, mesh, line sections, report: seconds
kicad2ems examples/filter.toml --solvers mom,lines    # MoM and the line model: ~1 min, no openEMS
kicad2ems examples/filter.toml                        # the openEMS run (and the line model)
kicad2ems examples/filter.toml --solvers all          # all three, compared
```

Outputs land in `examples/out/`: one Touchstone file per solver (`filter_openems.s2p`, `filter_mom25d.s2p`,
`filter_qucs.s2p` or `filter_scikit.s2p`), the S-parameter plot, `filter_mesh.png`, `filter_tlines.png`,
`filter_board.json` (the board every solver gets), `filter_run.json` (the run summary) and
`filter_report.html`, which collects everything on one page. The config also
solves the line sections as a circuit (`fast = "auto"`: qucsator if installed, five coupled fingers,
else scikit-rf) in under a second and compares them with the other solvers in the plot and the report
([Transmission-line model](docs/transmission-lines.md#coupled)). For your own board, mark the ports
in KiCad and write a config: see [Getting started](docs/getting-started.md#your-own-board).

## Documentation

[docs/index.md](docs/index.md) is the table of contents.

1. [Getting started](docs/getting-started.md): installing openEMS, `--check`, a first run, your own board.
2. [Usage and configuration](docs/usage.md): choosing solvers, every option, TOML configs, outputs,
   progress, events.
3. [Architecture](docs/architecture.md): the run pipeline, the run summary, the module map.
4. [KiCad import](docs/kicad-import.md): what is read from the `.kicad_pcb`, port footprint fields.
5. [The 3D model](docs/model.md): stack, copper, region, vias, ports, lumped parts, what is not modelled.
6. [Meshing](docs/meshing.md): mesh rules, boundaries, time step, convergence levels, a validation.
7. [Transmission-line model](docs/transmission-lines.md): line sections, Z0 formulas, `--fast`.
8. [Simulation](docs/simulation.md): openEMS setup, dielectrics, run length, parallel and antenna runs.
9. [Post-processing and outputs](docs/postprocessing.md): S-parameters, antenna results, field dumps,
   JSON summary, report.
10. [Examples](docs/examples.md): the example boards and their verified results.
11. [Testing](docs/testing.md): the test suite and checks against openEMS.
12. [Maintenance notes](docs/maintenance.md): openEMS quirks, adding an option, conventions.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). kicad2ems imports openEMS's Python bindings
(GPL-3.0-or-later) and CSXCAD (LGPL-3.0-or-later) in the same process; qucsator (GPL-2.0-or-later) runs
as a separate program; numpy, shapely, matplotlib and scikit-rf (BSD / PSF-style licenses) are
GPL-compatible. No third-party code is copied into the repository: the line-impedance formulas are
implemented from the published papers cited in `kicad2ems/tlines.py`.
