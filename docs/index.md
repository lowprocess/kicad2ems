# kicad2ems documentation

kicad2ems converts a KiCad board into a model, solves it with openEMS (3D FDTD), mom25d (2.5D method of
moments) or a transmission-line model, and writes S-parameters, antenna results, field images and a report.

## Using kicad2ems

| Page | Contents |
|---|---|
| [Getting started](getting-started.md) | Installing, checking the install, a first run, preparing your own board |
| [Usage](usage.md) | The command line, config files, choosing solvers, every option, the output files |
| [GUI](gui.md) | The graphical interface: settings, running, results, field views |
| [KiCad plugin](kicad-plugin.md) | In KiCad's PCB editor: the impedance map of the open board, a region and ports, a simulation |
| [Examples](examples.md) | The example boards and how the solvers compare on them |
| [Results](postprocessing.md) | S-parameters, antenna results, field dumps, the run summary and report |
| [Benchmarks](benchmarks.md) | Each solver's run time and memory on each example, CPU and GPU |

## The solvers

| Page | Contents |
|---|---|
| [openEMS: meshing](meshing.md) | How the FDTD mesh, boundaries and time step are chosen |
| [openEMS: simulation](simulation.md) | Materials, excitation, when a run stops, parallel and antenna runs |
| [mom25d](mom.md) | The method-of-moments solver: what it models, ports, antennas, speed, GPU |
| [gfdtd](gfdtd.md) | The GPU FDTD solver: openEMS's model on a GPU, CSXCAD / openEMS compatibility, what it models |
| [Which solver](usage.md#which) | The solvers' strengths and weaknesses, how close each comes to openEMS and how fast |
| [Transmission-line model](transmission-lines.md) | Line sections, impedance formulas, the circuit solvers |

## Working on kicad2ems

| Page | Contents |
|---|---|
| [Architecture](architecture.md) | The run pipeline, solver plugins, the module map |
| [KiCad import](kicad-import.md) | What is read from a `.kicad_pcb` |
| [The board model](model.md) | The board every solver gets: stack, copper, vias, ports, parts |
| [Testing](testing.md) | Running and extending the tests |
| [Maintenance notes](maintenance.md) | openEMS quirks, adding an option or a solver, conventions |

## Conventions

- Lengths are in mm. Frequencies are in GHz on the command line and in Hz in the code.
- KiCad's y axis points down; the model's points up (`y_model = -y_kicad`). Messages give positions in
  KiCad coordinates, so you can find them in the PCB editor.
- z = 0 is the bottom copper layer.
