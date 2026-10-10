# kicad2openems

kicad2openems turns a KiCad board into an [openEMS](https://openems.de) 3D FDTD simulation. It reads the
real stackup, copper, vias and board outline, takes ports from footprints and lumped R/L/C parts from
the components, builds the mesh automatically, runs openEMS and writes S-parameters (Touchstone) or
antenna gain, efficiency and patterns, with plots, a JSON run summary and an HTML report. Every run also
breaks the port nets into transmission-line sections with closed-form impedances, a quick ballpark that
can be solved as a circuit in milliseconds.

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
- **GUI** (`kicad2openems-gui`): setup, preview, run with progress, S-parameters, field view, antenna
  far field (polar cuts and 3D pattern at any recorded frequency).
- **MoM reference** (`--mom`): the same converted geometry (the CSX file openEMS gets) solved by
  [mom25d](mom25d/README.md), a 2.5D Method-of-Moments solver, its own repository
  ([lowprocess/mom25d](https://github.com/lowprocess/mom25d)) included as a git submodule in `mom25d/`.
- **TOML configs**, `--preview` without openEMS, an HTML report, JSON-lines events for a GUI or script.

## Install

```bash
git clone --recurse-submodules git@github.com:lowprocess/kicad2openems.git   # mom25d/ is a submodule
git submodule update --init   # in an existing clone without it (only needed for --mom)
pip install .                 # numpy, shapely, matplotlib
pip install '.[fast]'         # + scikit-rf, for --fast skrf
pip install '.[dumps]'        # + h5py, for frequency-domain field dumps
pip install ./mom25d          # optional, for --mom (otherwise used from the submodule checkout)
```

openEMS and its Python bindings are not on PyPI. Install them from your distribution, or build them with
`tools/build_openems.sh [prefix]`, and install kicad2openems into the same Python environment. Then
check that this Python can run openEMS:

```bash
kicad2openems --check
```

Everything except the FDTD run (model, mesh, line sections, `--preview`, the report) works without
openEMS. Details and troubleshooting: [Getting started](docs/getting-started.md).

## Quick start

The microstrip filter example comes with a config (`examples/filter.toml`) that names the board:

```bash
kicad2openems examples/filter.toml --preview      # model, mesh, line sections, report: seconds
kicad2openems examples/filter.toml                # the openEMS run
```

Outputs land in `examples/out/`: `filter_openems.s2p`, the S-parameter plot, `filter_mesh.png`,
`filter_tlines.png`, `filter_openems.json` and `filter_report.html`, which collects everything on one
page. The config also solves the line sections with qucsator (`fast = "qucsator"`, five coupled
fingers) in under a second and compares them with openEMS in the plot and the report
([Transmission-line model](docs/transmission-lines.md#coupled)). For your own board, mark the ports
in KiCad and write a config: see [Getting started](docs/getting-started.md#your-own-board).

## Documentation

[docs/index.md](docs/index.md) is the table of contents.

1. [Getting started](docs/getting-started.md): installing openEMS, `--check`, a first run, your own board.
2. [Usage and configuration](docs/usage.md): every option, TOML configs, outputs, progress, events.
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

GPL-3.0-or-later (see [LICENSE](LICENSE)). kicad2openems imports openEMS's Python bindings
(GPL-3.0-or-later) and CSXCAD (LGPL-3.0-or-later) in the same process; qucsator (GPL-2.0-or-later) runs
as a separate program; numpy, shapely, matplotlib and scikit-rf (BSD / PSF-style licenses) are
GPL-compatible. No third-party code is copied into the repository: the line-impedance formulas are
implemented from the published papers cited in `kicad2openems/tlines.py`.
