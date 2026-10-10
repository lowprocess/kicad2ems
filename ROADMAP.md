# Roadmap

Where kicad2openems stands and what is worth doing next. Items come from what the examples and benchmarks
showed, so each one names the evidence; details are in the linked docs. Ordered by priority within each
section. Last updated 2026-10-11.

## Where it stands

- **Three solvers from one converted board**: openEMS (FDTD), mom25d (2.5D MoM) and the
  transmission-line model (scikit-rf / qucsator), chosen with `--solvers` (or added as references with
  `--mom` / `--fast`), checked before a run starts, compared in the plots, the report and the GUI. MoM and
  the line model need no openEMS: without it, kicad2openems writes the CSX file itself (`csxwrite.py`).
  mom25d is its own repository ([lowprocess/mom25d](https://github.com/lowprocess/mom25d)), a git
  submodule in `mom25d/`; it reads the CSX file (`<name>.xml`) openEMS gets, plus a `<mom25d>` hints
  element.
- **GUI**: setup with config open / save that keeps the file's comments, live run progress, interactive
  S-parameter plots with a return-loss Smith chart and markers, field dumps (live, cuts, GIF / MP4),
  antenna far field.
- **Examples**: microstrip filters, lumped ladders, a via transition, an SMA launch, four antennas and six
  RF structures (branch-line, Wilkinson, stepped low-pass, stub notch, grounded CPW, coupler) with openEMS
  references ([examples](docs/examples.md)).

## 1. Open questions from the examples (correctness)

These are disagreements between the three solvers that are not yet explained. Settle them before trusting
the affected cases.

- **Wilkinson isolation** (`rf_wilkinson`): openEMS S32 −18.5 dB at 2.4 GHz; mom25d −38.9 dB, the line
  model −35.7 dB. The ideal-resistor models agree with each other, so look at openEMS's lumped resistor:
  the parasitics of the 1 mm pad gap, the element's box, the mesh in the gap. A sweep of the gap length
  and of `fine` around the resistor, and the same resistor in a simple two-port, would separate the
  causes ([examples: RF structures](docs/examples.md#rf-structures)).
- **Coupler coupling** (`rf_coupler`, 0.14 mm gap): openEMS −8.6 dB, mom25d −10.9 dB, line model −10.4 dB.
  The suspected cause is copper thickness (35 µm, modelled only by openEMS). Confirm with an openEMS run
  on zero-thickness copper; if that is it, add a thickness correction to the coupled-line formulas and
  consider mom25d's thick-conductor option (`Sheets` / `thicken`) for narrow gaps.
- **Grounded CPW return loss** (`rf_gcpw`): S11 −18.9 dB (openEMS) against −30.1 dB (mom25d, wave
  ports, de-embedded). Probably the lumped-port parasitics openEMS includes; check with mom25d forced to
  lumped ports (`mom_port = "lumped"`).
- **Filter pass-band loss** (`filter`): openEMS ~1.4 dB more loss than mom25d at 3.3 GHz, attributed to
  openEMS's conductivity κ being fitted at the band centre (`diel = fixed`). Re-run with
  `diel = "wideband"` to confirm ([mom](docs/mom.md#the-csx-file-and-the-hints)).

## 2. Line model

- **Stubs drawn as filled shapes overlapping a track**: the extractor took the overlap of a stub
  rectangle and the line for an 8 Ω section along the path (notch at 3.5 GHz instead of 2.4 GHz). Treat
  a shape crossing a track as a tee with a stub of the shape's length beyond the track edge. The
  `rf_stubnotch` generator can write the rectangle variant as a regression test.
- Coupled lines: copper-thickness correction (see the coupler above).

## 3. mom25d

- **Planes are expensive**: 7× (via transition) to 48× (SMA launch, 17k unknowns) slower than openEMS
  once inner planes, pours and via rows must be meshed; 5× faster on single-layer microstrip
  ([runtime](docs/mom.md#runtime)). Options, cheapest first: coarser plane meshing away from signal copper;
  modelling enclosed plane pairs as a parallel-plate stack instead of meshing them; a fast solver (the
  existing ACA / BLR paths) tuned for plane-heavy boards.
- **Adaptive sweep**: the convergence test now also checks each new sample's prediction (it had stopped
  early on the coupler). Add a regression test on a case that used to stop early, and log the sample
  frequencies in the run summary.
- **Meshers**: Triangle with offset rings stays the default; gmsh and DistMesh gave no gain per unknown
  ([mesher study](docs/mom.md#meshers)). gmsh's boundary layer fails where a via fan sits in the layer
  band; only worth more work if a case appears where Triangle fails.
- **Antenna mode** is not supported (infinite ground under the copper).
- Housekeeping: `build/` in mom25d's `.gitignore` (on its review branch).

## 4. openEMS pipeline

- The low-pass and stub examples stop at −24 to −28 dB energy (on the port criterion), like the other
  narrow-band filters. Check that the S-parameters are converged there, or give their configs an
  `end_db` / `end_on` that says so.
- Mesh defaults: the automatic fine cell followed the widest line (0.38 mm) on the coupler, whose gap is
  0.14 mm, so the RF configs set `fine` by hand. Deriving `fine` from the smallest copper gap as well as
  the narrowest trace would make the default safe.

## 5. Structure (from the October 2026 review)

- **mom25d's API.** The `review/multi-solver-ux` branch of mom25d adds `mom25d.api.solve_csx(path, freq)`
  returning a `Result` (S, names, port kinds, unknowns, notes) and fixes its CSX reader dropping the
  conductivity of conducting sheets. Once it is merged and the submodule bumped, `mom.solve` becomes one
  call to it instead of reaching into `Simulation` (`sim.Ne`, `sim.rwg.tris`, `hasattr(p, 's0')`).
- **A `solvers/` package.** The openEMS-only modules (mesh, boundaries, edges, converge, simulation,
  progress, dielectric, antenna, dumps, fields) and the line model (tlines, strips, coupled, fastsolve)
  could move under `solvers/openems/` and `solvers/lines/`, next to `solvers/mom.py`, behind the
  steps of [adding a solver](docs/maintenance.md#adding-a-solver). A move, not a rewrite: do it in one
  commit with nothing else.
- **One KiCad reader.** mom25d has its own, older `.kicad_pcb` reader (`mom25d/kicad.py`). Either it keeps
  only CSX and Gerber input (KiCad boards come through kicad2openems), or `kicad2openems/kicad/` becomes
  a small package both use.
- **Names.** The run summary is `<name>_openems.json` and the default output folder `openems_out` whichever
  solvers run; a solver-neutral `<name>_summary.json` (the GUI reading both for a while) and one default
  folder for the command line and the GUI (which uses `out` next to the board) would match what the tool
  is now.
- **MoM progress.** mom25d's adaptive sweep logs lines but sends no `status` events, so the GUI's Run tab
  shows no bar for it.

## 6. GUI

- An interactive comparison view: the difference between two sources (e.g. openEMS − mom25d in dB or
  |ΔS|) as its own plot kind, the numbers the report's comparison tables already compute.
- Mixed-mode (differential) traces in the trace list, named from the Touchstone port names.
- Show the run summary's notes and warnings (port fallbacks, mesh notes) in the Results tab.
- The HTML report's plots are static images; interactive plots there (same traces and markers) would
  match the GUI.

## 7. Examples and validation

- More structures where the solvers are expected to differ: a rat-race (closed loop, 1.5 λ), a radial
  stub, a via-fenced stripline, a differential pair through a via transition.
- A validation table generated by a script (like `tools/bench_mom_openems.py`) over every example, so the
  three-way comparison in [examples](docs/examples.md#rf-structures) is reproducible and can run before
  a release. Run the simulations one at a time when timings matter.

## Housekeeping

- Delete the merged remote branches `mom25d` and `gui-configs-plots-rf-examples`.
