# kicad2openems documentation

kicad2openems turns a KiCad board (`.kicad_pcb`) into an [openEMS](https://openems.de) 3D FDTD model:
the real stackup, copper, vias and outline, ports marked by footprints, lumped R/L/C parts, an automatic
mesh. It runs openEMS and writes S-parameters (Touchstone), antenna results (gain, efficiency, patterns),
plots and an HTML report.

These pages are for **users who want the details** and for **maintainers** who need to change the
code. The [README](../README.md) is the short tour.

## Pages

| # | Page | What it covers |
|---|---|---|
| 1 | [Getting started](getting-started.md) | Installing openEMS, `--check`, a first run, the examples |
| 2 | [Usage and configuration](usage.md) | Command line, TOML files and their path rules, every option, outputs, progress, report |
| 3 | [Architecture](architecture.md) | The run pipeline, module map, the data passed between stages |
| 4 | [KiCad import](kicad-import.md) | Reading `.kicad_pcb`: stackup, copper, vias, footprints, port and part detection |
| 5 | [The 3D model](model.md) | `Model`: stack heights, copper, vias, ports (vertical and gap), lumped parts |
| 6 | [Meshing](meshing.md) | The automatic mesh rules, time step, convergence levels |
| 7 | [Simulation](simulation.md) | openEMS setup: materials, excitation, boundaries, ports, lumped elements, runs, progress |
| 8 | [Post-processing and outputs](postprocessing.md) | S-parameters, mixed mode, convergence, antenna far field, field dumps, report |
| 9 | [Examples](examples.md) | The example boards and generators, and their verified results |
| 10 | [Testing](testing.md) | Test layout, the openEMS stand-ins, real-solver checks |
| 11 | [Maintenance notes](maintenance.md) | openEMS quirks and their workarounds, adding an option, conventions, checklist |

## Where to start

- **Using the tool:** read 1 → 2, then 9 for examples.
- **Changing the tool:** read 3 for the overall shape, then the page for the stage you're touching (4–8),
  then 10 and 11 before committing. [Maintenance notes](maintenance.md) lists the openEMS behaviours
  that cost the most time to find; read it before changing anything in `simulation.py`, `parts.py`
  or `antenna.py`.

## Conventions used in these pages

- **Units.** Geometry is in mm, frequencies in Hz inside the code and GHz on the command line.
- **Coordinates.** KiCad's y axis points down; the model's y axis points up (`y_model = -y_kicad`).
  Messages and the report give positions in KiCad coordinates so they can be found in the PCB editor.
- **z.** z = 0 is the bottom copper layer; the stack grows upwards.

---
[Getting started →](getting-started.md)
