# kicad2openems documentation

kicad2openems turns a KiCad board (`.kicad_pcb`) into an [openEMS](https://openems.de) 3D FDTD model:
the real stackup, copper, vias and outline, ports marked by footprints, lumped R/L/C parts, an automatic
mesh. It runs openEMS and writes S-parameters (Touchstone), antenna results (gain, efficiency, patterns),
plots, a JSON run summary and an HTML report. Every run also breaks the port nets into transmission-line
sections with closed-form impedances, and can solve them as a circuit in milliseconds.

These pages are for **users who want the details** and for **maintainers** who need to change the
code. The [README](../README.md) is the landing page: install, a quick start and links to these pages.

## Pages

| # | Page | What it covers |
|---|---|---|
| 1 | [Getting started](getting-started.md) | Installing kicad2openems and openEMS, `--check`, a first run, your own board |
| 2 | [Usage and configuration](usage.md) | Command line, Settings and TOML files, every option, outputs, progress, `--events` |
| 3 | [Architecture](architecture.md) | The run pipeline, the run summary, module map, the data passed between stages |
| 4 | [KiCad import](kicad-import.md) | Reading `.kicad_pcb`: stackup, copper, graphics, pads, vias, outline, footprints, port fields |
| 5 | [The 3D model](model.md) | `Model`: stack, copper, region cropping, vias, ports (vertical and gap), lumped parts, limits |
| 6 | [Meshing](meshing.md) | The automatic mesh rules, boundaries and air, time step, convergence levels, a validation on the transition demo |
| 7 | [Transmission-line model](transmission-lines.md) | Line sections, cross-sections and Z0 formulas, paths, `--fast` S-parameters with scikit-rf or qucsator |
| 8 | [Simulation](simulation.md) | openEMS setup: materials, excitation, run length, runs, `--jobs`, antenna runs |
| 9 | [Post-processing and outputs](postprocessing.md) | S-parameters, mixed mode, convergence, antenna far field, field dumps, JSON summary, report |
| 10 | [Examples](examples.md) | The example boards and generators, and their verified results |
| 11 | [Testing](testing.md) | Test layout, fixtures and openEMS stand-ins, real-solver checks |
| 12 | [Maintenance notes](maintenance.md) | openEMS quirks and their workarounds, adding an option, conventions, checklist, license |
| 13 | [GUI](gui.md) | `kicad2openems-gui`: setup, preview, run with progress and Stop, results, live field view with GIF / MP4, antenna far field |
| 14 | [MoM reference (mom25d)](mom.md) | `--mom`: the CSX geometry solved by mom25d, the CSX reader, ports, meshers (triangle / gmsh / distmesh), runtime against openEMS |

## Where to start

- **Using the tool:** read 1 → 2, then 10 for examples; 7 for the line model.
- **Changing the tool:** read 3 for the overall shape, then the page for the stage you're touching (4–9),
  then 11 and 12 before committing. [Maintenance notes](maintenance.md) lists the openEMS behaviours
  that cost the most time to find; read it before changing anything in `simulation.py`, `parts.py`
  or `antenna.py`.

## Conventions used in these pages

- **Units.** Geometry is in mm, frequencies in Hz inside the code and GHz on the command line.
- **Coordinates.** KiCad's y axis points down; the model's y axis points up (`y_model = -y_kicad`).
  Messages and the report give positions in KiCad coordinates so they can be found in the PCB editor.
- **z.** z = 0 is the bottom copper layer; the stack grows upwards.

---
[Getting started →](getting-started.md)
