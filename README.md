# kicad2ems

kicad2ems simulates KiCad boards. It reads the stackup, copper, vias and outline from a `.kicad_pcb`,
takes ports from marker footprints and R/L/C parts from the components, and solves the board with one or
more electromagnetic solvers. Each run writes Touchstone S-parameters, plots, a JSON summary and an HTML
report that compares the solvers.

| Solver | Name | Best for | Needs |
|---|---|---|---|
| [openEMS](https://openems.de) (3D FDTD) | `openems` | anything: planes, board edges, connectors, enclosures, antennas | openEMS |
| [mom25d](docs/mom.md) (2.5D method of moments) | `mom` | planar circuits over a ground plane: filters, couplers, matching networks, patch antennas | cuPy for GPU acceleration |
| [gfdtd](docs/gfdtd.md) (3D FDTD on the GPU) | `gfdtd` | openEMS's model, 8–20x faster on a GPU, ~2x on the CPU: the same boards and antennas as openEMS, without conductor loss | CuPy and a GPU, or its CPU core (`python -m gfdtd.core.build`) |
| Transmission-line model | `lines` | a quick estimate: impedance steps, delays, layout mistakes | scikit-rf or qucsator |

By default kicad2ems picks the full-wave solver it expects to be fastest for your board and adds the
line model.

## Gallery

<p align="center">
  <img src="docs/img/readme/kicad_plugin.jpg" width="100%"
       alt="The KiCad plugin beside KiCad's PCB editor, set up to simulate an HDMI pair">
</p>

*The KiCad plugin beside KiCad 10's PCB editor with its CM5 Minima demo board: the HDMI D0 pair selected in KiCad,
and the region and ports the plugin set up around it.*

<p align="center">
  <img src="docs/img/readme/plugin_map.gif" width="32%" alt="The impedance map filling in">
  <img src="docs/img/readme/plugin_section.jpg" width="32%" alt="An HDMI section read on the map">
  <img src="docs/img/readme/plugin_results.png" width="32%" alt="The pair's S-parameters against the line model">
</p>

*Left: the impedance map of the board's 94 signal nets filling in, in real time. Middle: the HDMI pairs against their
100 ohm class; the section read out at the bottom is 117.8 ohm differential (+18 %). Right: the D0 pair's
mixed-mode S-parameters from gfdtd (solid) and the line model (dashed), with the largest differences.*

<p align="center">
  <img src="docs/img/readme/hdmi_field.gif" width="80%" alt="Differential E field of the HDMI pair">
</p>

*The same pair driven differentially from the module: its E field just inside the F.Cu and B.Cu dielectrics,
in dB re the peak (30 dB range). gfdtd field dumps of the two legs' runs, combined as (E+ - E-) / sqrt(2).*

<p align="center">
  <img src="docs/img/readme/patch_3d.gif" width="49%" alt="3D field of a patch antenna">
  <img src="docs/img/readme/stub_3d.gif" width="49%" alt="3D field of a stub notch filter">
</p>

*The E field of a 2.45 GHz patch antenna (openEMS, `--dump E@volume+12@2.45`) and of a stub notch filter at its
2.4 GHz notch, over one period: the GUI's 3D field view, translucent over the copper.*

<p align="center">
  <img src="docs/img/readme/gui_sparams.png" width="100%" alt="S-parameters of a hairpin filter from three solvers">
</p>

*A hairpin band-pass filter solved by openEMS (FDTD), mom25d (MoM) and the transmission-line model (qucsator),
overlaid in the GUI with a Smith chart of the return loss.*

<p align="center">
  <img src="docs/img/readme/stub_current.gif" width="49%" alt="Surface current standing wave">
  <img src="docs/img/readme/gui_antenna.png" width="49%" alt="Antenna panel">
</p>

*Left: mom25d's surface current on the stub notch at 2.4 GHz (`--mom-dump 2.4 --dump-animate gif`): the
quarter-wave stub holds the standing wave and nothing passes it. Right: the patch in antenna mode: match, gain and
efficiency over frequency, polar cuts and the 3D pattern.*

<p align="center">
  <img src="docs/img/readme/gui_mesh.png" width="49%" alt="openEMS model and mesh">
  <img src="docs/img/readme/hairpin_tlines.png" width="40%" alt="Transmission-line sections">
</p>

*The openEMS mesh preview (`--preview`) and the transmission-line sections the line model finds.*
`python tools/readme_media.py --openems-python <python with openEMS>` makes these again from the examples. The
plugin's media were recorded from KiCad 10 on a virtual display, the plugin driven by a script.

## Features

- **KiCad import** (KiCad 5 to 10): stackup, tracks, arcs, zones, graphics, pads, vias and outline.
  Gerber files work too.
- **Ports** from footprints with a Fab text `P1`, `P2`, …, including differential pairs (`P1+` / `P1-`)
  and gap ports for antennas.
- **Lumped parts** from two-pad R/L/C footprints.
- **Antenna mode**: gain, efficiency and radiation patterns.
- **Field dumps** of E, H or current density: images, animations, and a 3D view.
- **GUI** (`kicad2ems-gui`) for setup, preview, progress and results.
- **KiCad plugin** (KiCad 9+, IPC API): the open board's trace impedances in the PCB editor, and a region with
  ports to simulate ([KiCad plugin](docs/kicad-plugin.md)).
- **GPU acceleration** for mom25d (AMD ROCm or NVIDIA CUDA).
- **Solver plugins**: other solvers can be added as packages.

## Install

```bash
git clone https://github.com/lowprocess/kicad2ems.git && cd kicad2ems
./setup.sh
source .venv/bin/activate
```

`setup.sh` asks where to install (a venv in this checkout, the default; for your user, with the commands on
your PATH; system-wide; or into the Python itself) and which optional parts you want: the GUI, field dumps,
the line model, the gmsh mesher, mom25d's compiled core, the CuPy package for your GPU, openEMS (one you
have, one in another venv linked in, a local build, or none), qucsator, and a launcher entry and desktop
icon for the GUI. `./setup.sh --yes` takes the defaults; `./setup.sh --target user` installs for your user.
The options and installing by hand are in [Getting started](docs/getting-started.md).

openEMS is only needed for the FDTD solver. Everything else works without it. To see what your setup
can run:

```bash
kicad2ems --check
```

## Quick start

```bash
kicad2ems examples/filter.toml --preview            # model, mesh and line model only: seconds
kicad2ems examples/filter.toml --solvers mom,lines  # MoM and the line model: about a minute
kicad2ems examples/filter.toml --solvers all        # openEMS too, all three compared
```

Results go to `examples/out/`: `filter_<solver>.s2p` for each solver, plots, `filter_run.json` and
`filter_report.html`. To simulate your own board, see
[Getting started](docs/getting-started.md#your-own-board).

## Documentation

Start with [Getting started](docs/getting-started.md), then [Usage](docs/usage.md). The
[documentation index](docs/index.md) lists every page.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). kicad2ems uses openEMS (GPL-3.0-or-later) and CSXCAD
(LGPL-3.0-or-later) as Python packages and runs qucsator (GPL-2.0-or-later) as a separate program. No
third-party code is copied into the repository.
