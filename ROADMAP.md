# Roadmap

Where kicad2ems stands and what is worth doing next. Items come from what the examples and benchmarks
showed, so each one names the evidence; details are in the linked docs. Ordered by priority within each
section. Last updated 2026-10-12.

## Where it stands

- **One board, solver plugins**: the KiCad or Gerber import gives one board (`<name>_board.json`), which
  every solver gets: openEMS (FDTD), mom25d (2.5D MoM) and the transmission-line model (qucs / scikit), each a
  plugin with its own settings (`[solver.<name>]`), chosen with `--solvers` (or added as references with
  `--mom` / `--fast`), checked before a run starts, compared in the plots, the report and the GUI. Others
  install as packages ([architecture](docs/architecture.md#solver-plugins)). Each solver declares what it
  changes on the way (vias snapped, parts split, walls as via rows); the parity tests read each solver's
  model back and fail on anything undeclared. mom25d lives in `mom25d/` and reads the board file itself;
  MoM and the line model need no openEMS.
- **GUI**: a toolbar (open, save, preview, run), settings in collapsible sections, config save that keeps
  the file's comments, live run progress, and one results workspace of docking panels (S-parameters with a
  return-loss Smith chart and markers, model and mesh, line model, run, fields, antenna, convergence) that
  open as a run writes them; other results folders can be overlaid ([gui](docs/gui.md)).
- **Examples**: microstrip filters, lumped ladders, a via transition, an SMA launch, five antennas (one a
  2 × 2 patch array) and six
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
  Not copper thickness: openEMS models the copper as zero-thickness sheets too. Not mom25d's mesh either:
  S31 at 2.4 GHz is −10.91 dB at `dmax` 0.3 mm and −10.89 dB at 0.15 mm (20k unknowns, 197 s, 10 GB;
  0.1 mm needs over 2 h on the CPU and was not finished). Next: openEMS with a finer cell across the gap
  (`fine` below 0.05 mm) and a check of its port reference planes on the short 4.3 mm feeds.
- **Grounded CPW return loss** (`rf_gcpw`): S11 −18.9 dB (openEMS) against −30.1 dB (mom25d, wave
  ports, de-embedded). Probably the lumped-port parasitics openEMS includes; check with mom25d forced to
  lumped ports (`mom_port = "lumped"`).
- **Filter pass-band loss** (`filter`): openEMS ~1.4 dB more loss than mom25d at 3.3 GHz, attributed to
  openEMS's conductivity κ being fitted at the band centre (`diel = fixed`). Re-run with
  `diel = "wideband"` to confirm ([mom](docs/mom.md#the-board)).

## 2. Line model

- **Stubs drawn as filled shapes overlapping a track**: the extractor took the overlap of a stub
  rectangle and the line for an 8 Ω section along the path (notch at 3.5 GHz instead of 2.4 GHz). Treat
  a shape crossing a track as a tee with a stub of the shape's length beyond the track edge. The
  `rf_stubnotch` generator can write the rectangle variant as a regression test.

## 3. mom25d

- **Planes are expensive**: 7× (via transition) to 48× (SMA launch, 17k unknowns) slower than openEMS
  once inner planes, pours and via rows must be meshed; 5× faster on single-layer microstrip
  ([runtime](docs/mom.md#runtime)). Done: the LU in single precision on the GPU with a double refinement
  (via transition 14.9 → 6.2 s per solve; the SMA launch does not converge in mixed precision and falls
  back to double) and via-via kernels from a table (SMA via blocks 26.8 → 2.5 s). Tried and dropped: LU
  recycling across frequencies, coarser or signal-only plane refinement (|S21| > 1 on the SMA launch), BLR
  (4.6× slower than dense at 13.5k unknowns). Left: modelling enclosed plane pairs as a parallel-plate
  stack instead of meshing them (the planes carry the return currents; research-grade).
- **Current dumps** (`--mom-dump`) take one extra solve per frequency; taking them from the sweep's own
  solves would need the sweep to land on the dump frequencies.
- **Meshers**: Triangle with offset rings stays the default; gmsh and DistMesh (now removed) gave no gain per unknown
  ([mesher study](docs/mom.md#meshers)). gmsh's boundary layer fails where a via fan sits in the layer
  band; only worth more work if a case appears where Triangle fails.
- **Antenna mode** is approximated over an infinite ground (patch: 2.450 GHz, 36 % efficiency, 6.4 dBi
  against openEMS 2.4375 GHz, 38 %, 7.0 dBi). Boards whose radiation depends on a finite ground (an
  inverted-F, an edge monopole) are out of reach; vias are left out of the far field.
- Housekeeping: `build/` in mom25d's `.gitignore` (on its review branch).

## 4. openEMS pipeline

- The low-pass and stub examples stop at −24 to −28 dB energy (on the port criterion), like the other
  narrow-band filters. Check that the S-parameters are converged there, or give their configs an
  `end_db` / `end_on` that says so.
- Mesh defaults: the automatic fine cell followed the widest line (0.38 mm) on the coupler, whose gap is
  0.14 mm, so the RF configs set `fine` by hand. Deriving `fine` from the smallest copper gap as well as
  the narrowest trace would make the default safe.

## 5. Structure

Done on the `kicad2ems` branch: mom25d in this repository, reading the board (no CSX file, no KiCad reader
of its own), the `solvers/` package with a plugin per solver, solver-neutral names (`<name>_run.json`,
`out/` next to the board), MoM progress events, `--solvers auto`, conformance boards
(`tests/test_conformance.py`) and `kicad2ems --init`.

- An Elmer FEM solver plugin was built and shelved (stash "Elmer FEM solver plugin"): second-order prisms
  needed over 21 GB on the microstrip thru; first-order elements were not accurate enough.

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
