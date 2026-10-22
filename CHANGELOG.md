# Changelog

All notable changes to kicad2ems (formerly kicad2openems) and its bundled
mom25d solver, by day. Releases are named by datecode, `YYMMDD-rN` (for
example `261013-r1`, the first release on 13 October 2026).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Fixed
- gfdtd: the end criterion counts the energy in lumped parts (L I²/2 in an inductor, q²/2C in a series
  capacitor) as well as the field's. A filter of lumped parts rings in them, and its runs had ended early:
  demo_bandpass after 53k timesteps instead of 67k, demo_lowpass 21.8k instead of 26.9k (now within 0.03 of
  openEMS).
- The `mixed` solver put a line's delay 27 % short between the adaptive sweep's samples. Its cut network has
  entries that are zero but for the solver's noise, and the sweep's rational fit let them decide the poles of
  every entry. Entries below 1e-4 of the largest are now fitted apart; the thru converges in 12 solves to 0.35 %.
- mom25d's port characterisation meshed reference planes at their full width: on the SMA launch it was bigger
  than the board (22.7k unknowns against 15.7k). The planes now reach 3 stack heights past the line (the same
  Zc and gamma), and the near-field correction is built in pieces: the SMA launch on the CPU in 6.6 GB instead
  of 10.7 GB and 270 s instead of 313 s, on the GPU in 9.7 GB instead of 13.2 GB, with the same result.

### Added
- `--air` takes six values (xmin,xmax,ymin,ymax,zmin,zmax) as well as one, like `--bc`: more air on one side,
  in front of an endfire antenna for instance.
- `make_antennas.py vivaldi` takes the board's size (`W=`, `H=`); the feed keeps its place beside the slot.
- An antipodal Vivaldi example (`make_antennas.py vivaldi_antipodal`, `antenna_vivaldi_antipodal.toml`): matched
  over all of 3–10 GHz, 0.4–1.2 dB more gain than the slot Vivaldi.

### Changed
- `--solvers auto` considers gfdtd. Its estimate is openEMS's mesh and timesteps at gfdtd's speed: about 8x
  openEMS's on the GPU, 2x on the CPU core. So auto now picks gfdtd or mom25d, and openEMS only where neither
  applies. With `--sigma`, `--converge` or `--drive`, gfdtd is left out of auto, because it would ignore them.
  docs/usage.md#which compares the solvers' strengths and weaknesses.
- mom25d's near-field correction keeps its sparse structure from one frequency to the next: a CPU solve on the
  SMA launch in 13.7 s instead of 16.2 s, the run 246 s instead of 270 s.
- dfsynth computes its line models over the whole sweep at once, and the interdigital design curves in one
  batched scan: an interdigital design in 0.3 s instead of 3 s, the same designs bit for bit.
- The test suite runs in about two thirds of the time (314 s to 218 s), every check kept.

### Removed
- EMerge (`--solvers emerge`) and the GUI's `--experimental` switch, which only it used. An old config's
  EMerge settings are ignored with a note.
- The gmsh mesher (`--mom-mesher`, the `gmsh` extra). On six example boards it needed 5–15 % more unknowns
  than Triangle and was never more accurate, and it could not mesh boards with via ports.
- mom25d's first two solvers (the single-layer `PlanarMoM` and `MultiLayerMoM`), their demos in
  examples/mom25d and `mom25d.viz`: `sim.Simulation` has long been the solver.

## 2026-10-20

Release `261020-r1`.

### Added
- Phased arrays in two dimensions. `grid` places each port in a 2D array: `4x4` puts the ports in order row
  by row (P1–P4 the top row), or `PORT:COL,ROW` per port (fractional places for an offset lattice).
  `steer = [X, Y]` (`--steer X,Y`) adds X degrees per column and Y per row on top of `drive`'s phases; without
  a grid, `steer X` adds X from each driven port to the next. mom25d applies it, and its antenna JSON records
  the grid.
- GUI Beam steering in 2D, on the Results page and in the 3D view's *Far field* section: a *Grid* field (the
  run's to start with), an X step per column and a Y step per row, and a phase map of the elements, each
  coloured by its phase with its active reflection. Animate sweeps X, Y, or both round a circle (a conical
  scan). For more than four driven ports the readout gives the active reflections' range and the worst port.
- Amplitude tapers for phased arrays: `taper = "taylor:25"` (`--taper`; also `chebyshev:SLL` and `cosine`),
  a column weight times a row weight on a grid, otherwise across the driven ports. In the GUI, a *Taper*
  choice and a sidelobe level sit in the Beam steering bar, and the phase map shows each element's amplitude.
  A run records its base drive, taper and steps, so the GUI's Reset brings back exactly those instead of
  tapering twice. On the 4×4 example, Taylor 25 dB takes the sidelobes from −13 dB to −25 dB for 0.9 dB of gain.
- Example `array4x4_phased` (`make_antennas.py`, `antenna_array4x4_phased.toml`): sixteen patches 65 mm
  apart, each with its own feed line and port.

## 2026-10-19

Releases `261019-r1` (everything up to the KiCad plugin's net runs, with 2026-10-18 and before),
`261019-r2` (adds the FDTD mesh zone, the port end-criterion window and the mom25d fixes below) and
`261019-r3` (adds the mixed solver's current dumps and wave-port fix, below) and `261019-r4` (the plugin's
map redraws, below, and the README's KiCad plugin gallery).

### Fixed (261019-r4)
- KiCad plugin: the impedance map redraws at most four times a second while it fills in, instead of once per
  group of nets. On KiCad's CM5 demo the window no longer freezes for seconds at a time, and the map is done
  in 3 s instead of 11 s.

### Fixed (261019-r3)
- The `mixed` solver: the board's own wave ports are made from the uncut copper, their feeds capped at what
  the fit needs down to fmin. On rf_stripline its result was 0.38 in |S| (25 degrees of S21) from mom25d's;
  now within 0.007 (0.4 degrees). Current dumps (`--mom-dump`) work for it, from the joined solution.

### Added
- KiCad plugin: simulate a net, a differential pair or a set of nets. Pick them on the map or take them from
  KiCad's selection, then **Set up region and ports**. The region is the box around their copper plus a margin
  (2 mm by default). R / L / C parts to ground stay in as shunt parts; parts in series also bring in the net on
  their far side. Every other component on the nets gets a port on its pad, and so does a track end that reaches
  nothing. Where a component or a pair of open ends sits on both legs of a pair, that is one differential port.
  The run also solves the line model (`fast = auto`). The window's new S-parameters tab plots each full-wave
  solver against the line model (|S| in dB; differential terms of pairs) and lists the largest differences.
- openEMS and gfdtd: `--mesh-zone` (default on): ground copper's edges get mesh lines only within a zone
  around the signal copper, so planes' pours, antipads and pads far from the signal no longer set the mesh.
  KiCad's CM5 demo, an HDMI pair: 75 M cells to 17 M at the default fine cell (9.5 M to 3.6 M at
  `--fine 0.1`, S within 0.024); the examples change by at most 0.0033. Never in antenna mode.

### Changed
- The port end criterion (`--end-on ports`) judges the port signals over two periods of the board's lowest
  cavity mode where that is shorter than two periods at fmin (no lumped parts on the signal nets): a band
  from 0.1 GHz no longer keeps a 6-layer board's run going for 20 ns of simulated time.

### Fixed
- mom25d, `--mom-port auto`: a differential pair whose coupled feed is too short for a wave port (ports
  on pads) is solved with single-ended legs instead of failing. An EM solve that returns non-finite values
  (mom25d's GPU solver on a 22,700-unknown board) is reported as such, not as an SVD error in the sweep.
- Differential ports given as specs (`ports = ["P1+=U1.5", "P1-=U1.6"]`): the second leg overwrote the
  first, so the run failed (`KeyError: '+'`), and `nets = auto` missed one leg's net.

## 2026-10-18

### Added
- A KiCad plugin (KiCad 9+, through its IPC API): a button in the PCB editor opens the open board's impedance
  map (every signal trace coloured by its line-model Z0; hover to read, click to select the track in KiCad),
  with a region (drawn, or around KiCad's selection) and ports (clicked on traces and pads, or where tracks cross
  the region edge) to run kicad2ems on, or to open in kicad2ems's GUI. `kicad2ems-kicad install` (the `kicad`
  extra); docs/kicad-plugin.md. The map filters by net class and judges each trace against its class's target
  (50R_SE, 100R_DIFF, 90ohm ...: Z0, or Zdiff = 2 Zodd for a pair); #PWR nets are never mapped. The map is
  built a net or pair at a time over worker processes and kept in a cache per board (only edited nets are mapped
  again): KiCad's video demo board in 2.4 s (was 87 s), 1 s from the cache.
- The line model finds the pad or via a trace ends on through spatial indexes (a second faster on a board of
  4000 trace ends).
- Boards build much faster on large designs: the net of each copper polygon is weighed only where nets meet (the
  video demo board: 41 s to 3 s, with parts off).
- gfdtd (`--solvers gfdtd`, or its tick box in the GUI): a 3D FDTD solver in the manner of openEMS that runs on the GPU
  (CuPy: ROCm or CUDA) or on the CPU (a compiled OpenMP core, `python -m gfdtd.core.build`; numpy without
  it). It gets openEMS's model, mesh and settings through its own copy of CSXCAD's and openEMS's Python
  interface, and it reads and writes openEMS's XML (`gfdtd model.xml`). On the examples it agrees with openEMS
  to 0.004–0.05 in |S|; an RX 7800 XT runs them 8–20 times faster than openEMS on 24 threads, and the CPU core
  about twice as fast on the same CPU (five times with PML and wideband dielectrics). It has PML boundaries (CPML), Debye dielectrics (`--diel wideband`), field
  dumps (`--dump`, in openEMS's .vtr / HDF5 files, so the field maps and the GUI's Fields tab work) and
  antenna mode (openEMS's NF2FF box and result file: gain, efficiency, patterns, the GUI's antenna page). Not
  yet: conductor loss, `--converge`, phased drives (docs/gfdtd.md). On the GPU a timestep is three launches
  (PML, Debye poles, Mur, excitation and lumped parts folded in), small boards' excitations run side by side, and
  dump boxes are one kernel each. `tools/bench_gfdtd.py` times its engine.

## 2026-10-14

### Added
- mom25d: quad cells on straight runs (`--mom-quads on`, experimental): about 20% fewer unknowns and half
  the LU time at the same accuracy (docs/mom.md#quads).
- mom25d: signal zone (`--mom-zone on`, experimental): mesh detail only around the signal copper and
  vias; stitching-via pads as squares, coarser cells elsewhere. A third to a quarter of the unknowns on
  boards with via fences (docs/mom.md#zone).

- mom25d: `--mom-signal-vias round|square|N` and `--mom-stitching-vias auto|round|square|N`: pads and
  antipads as drawn or as N-gons, per kind of via (docs/mom.md#vias).
- mom25d: `--mom-quads centre`: quad cells only in the middle of signal lines.

- mom25d: line functions (`--mom-lines on|even|full`, experimental): straight runs of trace as a few functions
  per column with the edge-peaked current profile, in place of the triangles' unknowns (docs/mom.md#lines).
- The `mixed` solver (`--solvers mixed`): mom25d with long, lone straight runs cut out and put back as
  transmission lines (docs/mom.md#mixed), with current dumps of the joined solution (`--mom-dump`).

### Fixed
- `kicad2ems --help` failed on a `%` in the `--mom-quads` help text.

### Changed
- mom25d: mixed precision on the CPU by default above 3,500 unknowns (about 1.3x faster); a stalled
  refinement gives up after 30 iterations instead of 300.

## 2026-10-13

### Added
- Phased arrays: `--drive` several ports at once (mom25d), with beam steering
  and its animation on the Results page.
- 3D view: beam steering in the Far field section, linked to the Results page.
- 3D view: Save GIF / MP4 steps through a time-domain dump's time steps;
  colour-tagged MP4.
- 3D view: one colour range for every frame (switchable); a layer dump drawn
  on top of what its layer shows, openEMS dumps shown the same way as mom25d's.
- GUI: Viewer, Run and Results pages; the line model drawn over the board;
  plots you can add and remove.
- GUI: line model as Z0-height slabs, lumped parts as chips at true size,
  memory meters and the run's estimate.
- GUI: each run in its own folder, a fresh preview on opening, frame slider,
  pages that persist.
- GUI: settings grouped by solver (openEMS, mom25d, EMerge sections, greyed
  unless ticked); qucsator in Solvers and hardware.
- `setup.sh`: install for the checkout, the user, the system or a given
  Python; external openEMS and qucsator; a launcher.

### Changed
- GUI: experimental solvers (EMerge) hidden unless `kicad2ems-gui --experimental`.
- Line model: a radial stub is now qucsator's `MRSTUB` instead of widening
  sections.

### Fixed
- GUI: clear the previous folder's selection and the previous board's
  estimates; no sideways scroll in the settings.

## 2026-10-12

### Added
- EMerge (3D FEM) as an optional, experimental solver: `--solvers emerge`;
  installed from git into kicad2ems's own venv, patched for Python 3.14,
  NumPy 2.4 and gmsh 4.15.
- Example `rf_shielded`: a resonator filter in a metal can (the case for EMerge).
- mom25d antenna mode: far field, gain and efficiency over an infinite ground;
  a 2 × 2 patch array example.
- mom25d: current dumps from the RWG basis functions, E near the board from
  the currents, one-period animations of frequency-domain dumps.
- mom25d: LU on the GPU with double refinement and a tiled L·Lᵀ; via-via
  kernels from a table; GPU retry above a failed frequency.
- mom25d's adaptive sweep shown in the Convergence panel and live in the Run
  panel's plot; sampled frequencies in the run summary.
- Benchmarks of every solver on every example.
- Line model: coupled differential pairs, coupled striplines (Cohn); example
  `rf_diffvia` with two nets; four new RF examples.
- GUI: toolbar, collapsible settings, one results workspace of docking
  panels; a System panel with a preview's estimates.
- 3D field view: volume dumps as a translucent voxel cloud over shaded
  copper slabs; the board shown with openEMS's grid or mom25d's / EMerge's
  mesh; 2D and 3D views in one panel; plane dumps and far field in 3D.
- README gallery made from the examples.
- `setup.sh`: a `.venv` in the checkout with selectable parts (GUI, dumps,
  line model, gmsh, compiled core, CuPy, openEMS).

### Changed
- mom25d wave ports: a test line long enough to characterise; the
  characterised gamma used at every frequency; port lines characterised at
  the board's cell size.
- openEMS mesh: z cells graded at signal copper, finer cells over curved
  copper, curve lines keep thin curved tracks connected.
- openEMS: flat ports and part ends snapped onto their mesh lines; an
  unexcited port is now an error.
- Preview no longer runs a full-wave solver.
- GUI Stop ends every solver (via a STOP file the run checks).
- Docs rewritten in plain sentences at about half the length; `mom25d.md`
  merged into `mom.md`.
- `setup.sh` installs kicad2ems editable in compat mode so a copy in
  `~/.local` can't shadow the checkout.

### Fixed
- Lumped parts span the copper-free gap: the Wilkinson's poor isolation was
  openEMS shortening its resistor.
- Stubs drawn as shapes are tees; a shape across a track (e.g. a radial stub)
  is a tee and a stub; the fine cell follows the signal gaps.
- Line model: qucsator's missing terms are zero rather than undefined.
- EMerge: no polygon fragmenting (zero-length edges on the filters).
- mom25d: duplicated wall vias removed.

### Removed
- Dead code: unused functions, imports and attributes; settled roadmap items.

## 2026-10-11

### Added
- **Renamed to kicad2ems**; `kicad2openems` remains as an alias.
- Solver plugins: a registry, option tables and one pipeline for every
  solver; example configs under `[solver.openems]`.
- `--solvers openems,mom,lines` to choose a run's solvers; `--solvers auto`
  (now the default) picks the full-wave solver estimated cheapest plus the
  line model.
- Parity: solvers declare what they change; readbacks; a "What each solver
  saw" report section.
- Conformance test: a microstrip thru with a known answer, run on every solver.
- The canonical Board (replaces Model), written as `<name>_board.json`;
  mom25d reads the board directly, with no CSX file in between.
- Gerber import in kicad2ems, checked against the KiCad import.
- `kicad2ems --init board.kicad_pcb`: a commented starter config.
- MoM without openEMS: CSX geometry written without CSXCAD.
- `mom25d.api.solve_csx`: one call for programs that use mom25d.
- Config: suggests the option an unknown key is probably a typo of.
- Touchstone: names the terms that were not computed.
- GUI: solver tick boxes, one default output folder, full tracebacks.
- `MOM25D_NO_CORE` to choose the pure-Python solver; a message when the
  compiled core is missing.

### Changed
- One tree: mom25d moved back into the repository as a package next to
  kicad2ems, with one `pyproject.toml` (replacing the submodule).
- Importers and solvers split into packages.
- One default output folder: `out` next to the board.
- Command line options take precedence over `--config`; paths relative to it.
- `--sigma` passed to mom25d as conductor loss.
- Docs: multi-solver front end, Gerber input, the Board, adding options and
  solvers, MoM through the board vs. the CSX path on every example.

### Fixed
- CSX reader: a conducting sheet's conductivity is read from the element.
- AFS: converge only when the model also predicted the newest sample.
- Without openEMS installed, `auto` previews openEMS without importing it; a
  test skips without scikit-rf.

### Removed
- DistMesh mesher; duplicate Touchstone writer; dead code.

## 2026-10-10

### Added
- Transmission-line sections and a fast line-model solver (scikit-rf /
  qucsator), including other-net references, ground nets, region clipping,
  drawn tapers, split connector pads, ports on pads, pour openings, qucsator
  `CLIN`, coupled lines and discontinuities for filters.
- Region and ports engine: polygon / drawing-layer regions, cut ports, pad
  and point ports, `nets = auto`.
- Edge walls: PEC sheets where the ground planes end at chosen board sides.
- `--end-on ports` (default): end runs when port signals have decayed, once
  the field energy has stalled.
- GUI, first version: setup, preview, run with progress and Stop, results;
  live field view with x / y / z cuts and GIF / MP4; antenna far-field tab;
  File menu (open / save / save as / recent / examples) preserving config
  comments; interactive S-parameter plots with a Smith chart.
- mom25d imported with its history; installable package with a `mom25d`
  command; reads AppCSXCAD / openEMS `.xml` geometry; `PlanarModel` base;
  `solve_band`; gmsh mesher with a study and a mesher gallery.
- `--mom`: solve the CSX geometry with mom25d as a second full-wave
  reference, with report section, GUI, docs, tests and an openEMS-vs-MoM
  benchmark.
- KiCad import: outline from every Edge.Cuts item, copper graphics, more pad
  shapes.
- Settings object, one run path, run summary and events.
- Examples: SMA edge launch (Molex 73251-1151) with edge walls; six RF
  structures (branch-line, Wilkinson, stepped LPF, stub notch, GCPW,
  coupler) with openEMS references.
- `ROADMAP.md`.

### Changed
- Licence: GPL-3.0-or-later; packaging extras and version.
- Mesh: thirds-rule pair spans 2/3 fine; region cuts closed with edge walls;
  no via or edge wall left off the grid.
- Model: region cropping keeps ports inside; shared port helpers; clip
  before union.
- Shorter README; transmission-line page; mesh validation docs.
- Example references regenerated with the new mesh rule.

### Fixed
- Filter example pass band corrected.
- The via-region error (thirds-rule spans).
- Review fixes across the pipeline and line model.

## 2026-10-09

### Added
- Lumped R / L / C parts; `--jobs`; `--diel wideband`; `--converge`.
- Antenna mode with patch / IFA / dipole / slot examples; gap ports; lumped
  filter examples.
- `--max-steps` timestep limit per run, with a warning and the excitation
  length in `--preview`.
- Progress / ETA for openEMS runs; `--openems-output` for raw solver output.
- HTML report `<name>_report.html` for every run (also `--preview`).
- Documentation in `docs/` (11 linked pages) for users and maintainers.
- Microstrip filter test.

### Changed
- Config: paths relative to the TOML file, board named in the TOML, the
  `.toml` as the only argument.
- Run pipeline moved into `pipeline.py`; `RunResult` instead of function
  attributes.
- Reference results in `examples/reference`.
- Mesh: thirds rule on near-Manhattan edges.

### Fixed
- Filter reference rerun with the corrected mesh (6 lines across each feed
  instead of 1).

## 2026-10-08

### Added
- **kicad2openems**: KiCad board → openEMS 3D FDTD with automatic meshing.
- Field dumps: `--dump FIELD@REGION[@GHz]` for E / H / J planes or volumes.
- `--check`: reports the Python in use, the real import errors and what to do.
- Preview draws mesh lines over the copper.
- Smith chart and new plot type.
- mom25d (initial history): 2.5D planar MoM solver; polygons, vias, lumped
  elements and ports, conductor thickness and loss; H-matrix / ACA + GMRES
  fast solver; adaptive sweep; KiCad and Gerber import; CLI; port impedance
  characterisation; compiled C++/OpenMP core with mixed precision and BLR
  preconditioner; differential (mixed-mode) ports; Touchstone v2; GPU LU
  (ROCm / CUDA via CuPy or PyTorch).

### Changed
- Direct solver: in-place LU (half the peak memory), mixed precision by
  default, memory check before every direct run.
- KiCad import: hierarchical net names, grounded-CPW port cross-sections,
  clearer port errors.

### Fixed
- Mixed-mode transform: entries for unexcited ports stay local instead of
  turning every result into NaN.
- Plain Python numbers passed to CSXCAD / openEMS (older CSXCAD rejects
  NumPy scalars).
- Mesh: simplified curves and vias; line merging no longer collapses narrow
  traces.
