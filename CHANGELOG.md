# Changelog

All notable changes to kicad2ems (formerly kicad2openems), and to the mom25d and gfdtd solvers while they
were bundled with it, by day. Releases are versioned `MAJOR.MINOR.PATCH` (semantic versioning) from 0.3.0
(2026-11-14), and tagged `vX.Y.Z`; before that they were named by datecode, `YYMMDD-rN` (for example
`261013-r1`, the first release on 13 October 2026).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- **A variation's figures side by side (2026-11-20).** `--vary` (and `[vary]`) writes `<name>_vary.csv` and ends
  its log with a table, a line a combination and solver: each port's worst match and its dip, each transmission
  term at the band's centre and its lowest (`vary.figures`, also in `<name>_vary.json`).
- **mom25d and the line model against a stored reference in CI (R-C9).** `tests/mom25d/test_reference.py` and
  `tests/core/test_reference.py` solve demo_lowpass as the FDTD test does and compare with openEMS's
  S-parameters, now in `tests/data/` for all three: mom25d within 0.05 (0.024 measured, ~3 s), the line model's
  S21 within 0.08 (0.046) and its -3 dB corner within 50 MHz (its S11 has no model of the pads: not compared).
- **A board's coupling, a layer at a time (2026-11-20).** `python -m kicad2ems.core.coupling board --nets 'DQ*'`
  and the study's *Board coupling* question (Signal integrity; engines mom25d and the line model): the nets by name
  patterns, each copper layer solved with the ground copper alone, each net's stretch on it a port at each end
  (`si.trace_ends` takes a layer now: ended where it leaves it), breakouts under `--min-length` left out and said.
  Each layer's NEXT and FEXT matrices for an edge, each victim's worst aggressors and their sum (a differential
  pair's other leg not an aggressor), the worst pairs, a figure per layer, a limit on the worst victim. The
  example `si_ddr` (`make_rf_examples.py ddr`: two byte lanes, microstrip on F.Cu and stripline on In2.Cu through
  vias): the worst victim 14.7 % NEXT and 20.4 % FEXT on F.Cu, 8.1 % and 1.4 % on In2.Cu by mom25d (8.6 min), the
  line model within 3-15 % on NEXT in 18 s (docs/si.md#coupling).
- **A board end to end through the FDTD layer in CI (R-C6).** `tests/fdtd/test_board_end_to_end.py` generates
  demo_lowpass, runs its example config on gfdtd's CPU backend in a process of its own (~8 s) and checks it against
  openEMS's S-parameters of the same run, stored in `tests/fdtd/data/demo_lowpass_openems.s2p`: within 0.05 in
  complex S11 and S21 (0.029 measured, at 0.6 GHz), and a low-pass whatever the reference (its -3 dB at 0.76 GHz:
  the parts' ESL and pads move it from the ideal 1 GHz, in openEMS too).
- **`--mom-thickness mixed` (mom25d).** Plates of thick copper's sides only where copper faces copper within
  0.5 mm or is narrower than 1 mm, the other edges with `edge`'s correction: unknowns of the 44 boards 177.5k →
  160.7k (rf_bends 7.1k → 5.7k, sma_launch 15.1k → 13.3k), the suite 14.6 → 11.5 min on a loaded machine. Not the
  default: against gfdtd's solid copper the 17 boards' median 0.0379 (sides 0.0359), and against openEMS six boards
  further than 0.005 (antenna_patch +0.057). With wave ports it is `sides` (docs/mom.md#thickness).
- **The window's Runs page, in place of the toolbar's Results of.** A table of the board's runs: when, what each
  answered, the engines, how it ended, how long it took, its size, and *≠ settings* (which differ from the form's,
  in its tooltip); the preview last, *current* or *stale*. *This config* by default (a run's mark names the config
  it came from: another config's runs of the board no longer show), *This board*, *All*. A click shows a run; Ctrl
  and a click lays more over it in Plots and the second Viewer; several deleted at once. The toolbar's *Showing*,
  the steps bar and the Questions list (*last 14:02*) say what is shown and when each question was answered.
- **The KiCad plugin runs the DC drop and the temperature, and draws their maps on the board (R-E26).**
  kicad2ems-ipc f43f5ca: a power box (rail, voltage, loads or losses) runs pcbqs's pdn and thermal through
  kicad2ems's analysis registry; *Map on the board* draws a result's map as six bands of filled polygons, a band a
  user layer, with a legend, in one undoable commit.
- **A rail's PDN in Palace (R-E22).** kicad2ems-fem acffc2c: `palace_pdn`, the power-integrity question's 3D
  engine: the rail of pcbqs's PDN run, each part a lumped port at its copper's gap, the bare copper solved in Palace
  and closed by the run's parts after the solve; each load's |Z| and the bare matrix beside pcbqs's. On the buck
  example it found that a ground pour beside the rail on its own layer is not in pcbqs's PDN (Palace's bare |Z| 0.68
  of pcbqs's, the loaded inductance 0.45).
- **The PDN for a PI user (R-E14).** pcbqs 09cd9db: `--cap-file` (a capacitor's vendor model: a .s2p measured
  through it, shunt or series told by its |S21|, a .s1p / .z1p or a CSV), `--cap-derate` (an MLCC's C at its DC
  bias), `--vrm-file` (the regulator's output impedance in place of R + L), `--ports-touchstone` (the bare PDN
  between every part's port, the decap sites included: the parts attached there give the loaded rail to 1e-6) and
  `--spice` (it as a passive SPICE subcircuit, vector fitted). The near field's input capacitors take their own ESR
  and ESL (fields, else their package's), as the PDN does.
- **The GPUs' runs shared by speed (R-D9).** `--gfdtd-gpus` gives each run to the GPU that would finish it first,
  by each GPU's speed measured on earlier runs (kicad2ems-fdtd 28b75ea): a GPU too slow to help gets none, and one
  GPU alone runs them side by side. rf_coupler's four runs on an RX 7800 XT and a GTX 980 Ti: 22.6 s shared alike,
  11.8 s by speed. Excitations batched in the kernels were measured and left (under 5 % of the traffic to save).
- **Part ports on every solver, other settings varied, and the window's view (R-E5).** mom25d's part port is a
  delta gap across the part's strip (mom25d d6e51ed; its pads kept: a shunt part's ground pad and vias had gone as a
  lone stitching pad), the line model's a floating port in the part's place (a series tee on scikit-rf, a Pac across
  the gap on qucsator): closed by the parts' values, demo_lowpass is the parts' own to 1.5e-5 and 1e-15. `--vary
  KEY=V1,V2` (or a `[vary]` table) solves the whole job for every combination of other settings (at most 32), each in
  `<out>/vary/<tag>/`, listed in `<name>_vary.json` (`kicad2ems.vary`). The window shows a variation's first run with
  the others, and a run's part-value combinations, under *Compare with* (kicad2ems-gui dfc62b7).
- **Line ports for the FDTD solvers, and ports at any angle (R-E1).** `--port-kind line | auto` (default `lumped`):
  probes on each port's straight feed behind its lumped port, S = B A⁻¹ from every run's waves, each reference moved
  back to the port along the FDTD's own line (kicad2ems-fdtd 3eb00f7; docs/model.md#line-ports). Against mom25d's
  wave ports, every port driven: rf_stripline 0.348 → 0.053, rf_gcpw 0.499 → 0.067, rf_bends 0.351 → 0.108,
  rf_crosstalk 0.181 → 0.121; rf_msvia 0.055 → 0.256, where the probes are plane-independent (0.011) and the two
  solvers' via transitions differ (open). A vertical port's trace may run at any angle (its sheet across the
  dominant axis, as wide as the trace's cut; mom25d 6fe665d takes the angle exactly: a 20 mm line at 30° and 45°
  within 0.008 of the same line along x); a pad or point port takes its track's own direction. The lumped port's
  feed is documented (0.07-0.1 nH and the trace behind the plane, 0.12 pF/mm); where no straight feed is it stays,
  and mom25d's lumped ports keep their via (its wave ports are the de-embedded ones).
- **mom25d's via and hole blocks, the rest (R-D11).** The lifted Sommerfeld contour's J0, J1 and J2 of complex
  argument in the compiled core (mom25d 91c1a64; within 5e-12 of scipy's, 13 times faster): demo_transition's via
  sums 1.77 → 1.34 s, its sweep 7.3 → 6.7 s, S at fixed frequencies within 5e-14. Each frequency's tables stay (their
  Sommerfeld integrals depend on it). The next frequency's fill beside the GPU's factor was measured and left:
  sma_launch's 3 factorisations take 4 s of 43 (its other 10 solves reuse one), and the sweep picks each next
  frequency from the last solve. The reused LU's GMRES product (13 s there) is in the ROADMAP.
- **Failed and stopped runs stay listed (R-E24), and the window's desktop basics (R-E25).** A run that fails or
  is stopped keeps its row, with how it ended and its log when picked; *Delete run* removes one; a run's process's
  whole output is kept in stdout.log. A board, config or results folder dropped on the window opens; an exception
  in a slot is shown in a dialog; a crash, and a run that cannot start, are said (kicad2ems-gui 061e7b6).
- **The 3D view colours a frame when it is shown (R-D21),** its colour range from a subsample and the map from a
  lookup table; the height scale no longer recolours. A colour control's step on a 256-voxel frequency-domain
  volume: 12.1 s and 4.8 GB to 0.5 s and 0.7 GB (kicad2ems-gui 94aceb4).
- **The window's desktop basics, the rest (R-E25).** *Edit → Undo / Redo* through the settings' changes (a tick
  or a choice a step, a field's typing a step once it pauses; the menu names the field), *View → Plot colours*
  (as the desktop, light or dark: every plot restyled at once, the Smith grid and markers too), *Save…* (HTML with
  its pictures inside, or PDF) and *Print…* on an analysis's Report, and *Export Touchstone* on the Plots page
  (kicad2ems-gui 9f67717).
- **The line model's wideband dielectric (R-E4).** With `--diel wideband` each layer's εr and tan δ follow its
  Djordjevic-Sarkar curve in the line model too, as in mom25d and the FDTD layer (`kicad2ems.lines.dispersion`):
  the network solved with its materials frozen at 3-12 frequencies across the band and S interpolated between the
  solves to each frequency's own (within 4e-5 of solving each alone), at 3-4 times the line model's time. Its
  effect matches mom25d's: the thru's phase moves +5.5° against mom25d's +5.6° on rf_crosstalk, +12.6° against
  +13.1° on rf_meander. Against openEMS: rf_meander 0.31 → 0.26, rf_serpentine 0.61 → 0.57, rf_msvia 0.068 →
  0.055; rf_crosstalk 0.032 → 0.035 (its fixed εr had offset the formulas' own error).
- **Solvers side by side (R-D20).** `--solver-jobs N` (the window: Solvers at a time) runs a board's solvers N at
  a time, as threads; the log goes to the terminal through a copy of its descriptor, which openEMS's in-process
  redirection to its own log would otherwise have taken. Measured (docs/benchmarks.md#side-by-side): 1.14 times
  on rf_ebg, where gfdtd's 24 s hid under mom25d's 5 minutes; even on small boards; slower where two solvers share
  the CPU (openEMS beside mom25d: 26 → 52 s) or beside gfdtd's GPU stepping, which holds the GIL while it records
  each step's kernels (mom25d on hairpin 3.8 → 9.6 s). Default 1, one after another. mom25d ce06a82: its OpenMP
  cap is set in each thread (libgomp keeps it per thread: a solve in a second thread had run on every CPU).
- **A GPU report before each release (R-C7).** CI has no GPU, so the superproject's `tools/gpu_report.py` runs
  the GPU tests of gfdtd, mom25d, pcbqs, emsdev and kicad2ems-fdtd and two boards on each backend against the CPU on
  a machine with GPUs, and commits what it found in `gpu-report/`; `sync_pins.py` ends with whether the latest
  report is for the commits pinned now. The first (2026-11-15): 101 GPU tests pass on the RX 7800 XT (Vulkan and
  CuPy), gfdtd's GPU runs within 6e-7 of its CPU's, mom25d's within its sweep's 1e-3.
- **Causality of band-limited S-parameters (R-E2).** Each term against its Kramers-Kronig reconstruction from the
  band alone, with subtractions (generalised dispersion relations: `kicad2ems.causality`), the bound of what the
  data outside the band could add from |S| <= 1, and the quadrature's own error from causal test responses; the
  miss beyond them is in the run summary and above 0.1 warned of, for the solver's S and a de-embedded one. Causal
  solver outputs read 0-0.06, time-reversed ones 1.5-4 (an impulse response's energy before t = 0 could not tell
  them apart from 1 GHz: 53 % and 35 %).
- **gfdtd's time step from the operator's own limit (R-D6).** 2 / sqrt(lambda_max) of its curl-curl with the real
  coefficients (Lanczos, kept per model), 1.19-1.29 times the vacuum Courant step on the examples: stepping
  1.14-1.53 times faster, the S within 0.005. `--gfdtd-timestep auto` (the default: from 1 M cells or once
  measured), `exact`, `courant` (gfdtd 9ce728f, kicad2ems-fdtd 47bf714).
- **gfdtd takes the openEMS options it had ignored or refused, but cylindrical grids (R-B18).** Mue and magnetic
  loss (`Sigma`; on the NumPy backend, said: the compiled kernels' H update is geometry alone), E- and H-field probes
  (types 2, 3: one file, t Ex Ey Ez), time-domain NF2FF (`CreateNF2FFBox(frequency=None)`: the faces as openEMS's
  time-domain HDF5 dumps, any frequency at `CalcNF2FF`; the dipole as the frequency-domain box, 0.05 dB), a coaxial
  port's finite `Feed_R` (a resistive annulus at the feed plane; openEMS's port has none) and `SetAbort` (gfdtd
  626197c).
- **Slot planes in mom25d (`--mom-planes slots`, experimental).** Every copper layer as the whole stack has them,
  but the ground a perfect conductor of the stack, infinite, and only its openings meshed: their field the unknowns,
  coupled to the traces and vias on either side through the stack's networks (`mom25d/slots.py`; docs/mom.md#slot-planes).
  antenna_aperture comes to 0.057 from openEMS (the whole stack 0.19 at any cell size); on boards whose ground edge
  matters (antenna_slot, 0.257 against 0.058) the whole stack stays the choice. Checked by Babinet's principle, the
  frill holes' kernels and the ground meshed on a slot under a line (within 0.03). Its coupling to the traces is in
  the compiled core: rf_dgs 12 s (the whole stack 18 s), antenna_slot 35 s (149 s), antenna_aperture 15 s, rf_slotline
  73 s (55 s).
- **Parts as ports, closed after the solve (R-E5).** `--part-ports all` (or `C1,L2`) solves each part as an internal
  gap port and closes it by its value afterwards (`kicad2ems.embed`): the result is the board with its parts,
  `<name>_<solver>_ports.sMp` the full matrix, and `--part-value C2=3.3p,6.8p,10p` (every combination, at most 64)
  or `--part-file C2=vendor.s2p` (a series-measured model) costs no new solve. openEMS, gfdtd and Palace (the
  solvers with gap ports). demo_lowpass on gfdtd: within 0.011 of the lumped run in S21.
- **gfdtd on several GPUs (R-D9, the GPUs).** `--gfdtd-gpus all` (or `0,1`) shares a run's excitations out to the
  Vulkan GPUs, a process each taking the next as it comes free (threads cannot: two drove two GPUs at a fifth of
  their speed, the interpreter's lock between them). On a 1.1 M-cell board each GPU runs at its own speed, the
  S-parameters as one GPU's within 8e-7. Off by default: here the second GPU is 4.7x slower.
- **A converter's loop on Palace (R-E22, the loop).** kicad2ems-fem's `palace_loop` is a second engine of the near
  field's question: the loop of the board's newest pcbqs near-field run, solved again in 3D, its inductance at each
  harmonic beside pcbqs's (both name it `loop L at F MHz`). On the Jetson's U69 Palace reads 0.22-0.20 nH against
  pcbqs's 0.42-0.34 nH, where the two agree within 2 % on simple loops: which models this loop right is open.
- **Coils at their operating frequency, and a board heating from cold, on Elmer (R-E21).** kicad2ems-fem's coil
  tool (the study's *Coils and ferrite*) takes `--freq`: each coil in the time-harmonic field, its L there and its
  series resistance from the ferrite's loss (a plate's μ″), from a metal plate's eddy currents (`sigma=`, a surface
  impedance) and its winding's own (`r=`), and its Q. Checked against a coil over copper and its mirror image (L
  1.980 against 1.983 µH, R 22.9 against 23.5 mΩ). Its thermal tool takes `--transient S`: BDF2 on pcbqs's steps,
  each part's pads against time and its time to 63 %, within 0.2 % of a lumped strip's time constant. On wpthb it
  found pcbqs's transient counting an inner layer's copper as laminate too (pcbqs's CHANGELOG).
- **Palace's refinement within the memory, and a second-order boundary (R-E20, part).** kicad2ems-fem's
  `--palace-amr` stops at `--palace-amr-max` unknowns, by default what 70 % of the memory available holds, and
  refines the eigenmode run too; `--palace-abc 2` is the second-order absorbing boundary. The estimate's unknowns
  are Palace's own count (6.9 a tetrahedron, not 1.53). Measured: refining `rf_shielded` from the coarse mesh
  reaches 4.651 GHz in twice the default mesh's time, so the default mesh stays; the second-order boundary leaves
  the dipole's 100.4 % efficiency as it was.
- **Resonances, and a board in its can, on Palace (R-E19).** kicad2ems-fem's Palace solver closes its air box in
  metal (`--palace-box pec`, a can `--palace-margin` from the board) and, with `--palace-modes N`, follows the
  driven run with an eigenmode run on the same mesh: the N resonances above `fmin`, each one's frequency and loaded
  Q (the ports as their resistors) and each port's share. A solver's resonances (`Result.info['modes']`) are a
  table in its report section (plugin API 2.13). `rf_shielded` in its can: the resonator at 4.6574 GHz, Q 158 (the
  driven peak 4.657 GHz), the can's own mode at 7.115 GHz, 5.7 min for both runs. Palace's own mesh refinement
  settles there too (4.651 GHz at 547,000 unknowns), 0.8 % above EMerge's 4.621 GHz, while openEMS is still
  climbing with its mesh (4.626 GHz at 4x).
- **dfsynth matches its coupled-line band-pass filters (R-E18).** Only their band edges had been calibrated: the
  hairpin's pass-band return loss was 5.5 dB on dfsynth's own lossless model. The coupling gaps, and each hairpin
  U's bar, are now fitted for the pass band's match with the edges held: 11.0 dB on FR4 and on RO4003C, the 5 GHz
  edge-coupled filter 13.7 dB. Every refinement runs BLAS on one thread (the hairpin's fit 9.6 s -> 5.1 s).
- **Materials by name (R-E4).** The stackup's `material` is read; a dielectric whose εr or tan δ the file does not
  give takes its material's from a small library (`kicad2ems.materials`, with the frequency each maker quotes) or
  FR-4's, and a note says which; the board's log shows each layer's material. With `--diel wideband`, a named
  laminate's layer holds its values at its own reference frequency (RO4350B's 10 GHz) instead of `--diel-fref`,
  in mom25d and the FDTD layer; a generic FR4 keeps `--diel-fref`.
- **Coupled lines with a jog (R-E17).** Only whole straight elements were paired, so a trace with a jog (one
  polyline element) coupled with nothing: dfsynth's edge-coupled band-pass came out open on the line model. A
  polyline whose straight run lies beside another line closely enough is now cut at its corners and that run
  paired, the others left whole: the order-5 band-pass's 6 coupled sections, P1 to P2, its pass band at 2.34 GHz
  (dfsynth's own model 2.38, designed for 2.4).
- **Discontinuities on scikit-rf (R-E15).** The line model's backend without qucsator had none: open ends now
  carry Kirschning-Jansen-Koster's extension, width steps Edwards', and corners and bends Gupta-Garg-Bahl's
  capacitance (scaled by tan(θ/2) away from 90°) with their arms cut to the corner square as qucsator's MCORN
  takes them; qucsator gets the same for a bend of another angle. The formulas are dfsynth's (`lines/formulas.py`,
  checked against them). The hairpin's pass band on scikit-rf 3.25 to 3.30 GHz (qucsator 3.31, openEMS 3.37);
  an open stub equals its line lengthened by the end's extension.
- **De-embedding (R-E1, its first part).** `--deembed P1=2.5` moves a port's reference plane along its own trace,
  the line model's sections from the port taken off (their Z0, εeff, the mode's tan δ and, with `--sigma`, the
  copper's loss; a negative length moves it out), and `--deembed-file P1=fixture.s2p` removes a fixture there:
  `<name>_<solver>_deembedded.sNp` beside each solver's own S, from any solver (`kicad2ems.deembed`: the fixture's
  inverse connected at the port). Checked against exact cascades of matched and unmatched, lossy lines. Left: the
  FDTD layer's own line ports, mom25d's ports without their via, ports at an angle.
- **A run done again takes its earlier result (R-D20).** Each solver's result is kept beside the run's outputs
  (`<name>_<solver>.result.npz` and `.json`), and a later run in the same folder takes it in place of a solve when
  the board every solver gets, the frequencies, every setting but the output ones, the solver's version line and
  a digest of the stack's loaded code are the same (`kicad2ems.reuse`); `--no-reuse` solves again. A solver says
  whether its outputs need its own solve (`Solver.reusable(run)`, API 2.12): mom25d and the FDTD solvers take an
  earlier result unless the run asks for field or current dumps, the far field or `--converge`; a solver with no
  outputs of its own (the line model) always may. The solvers of a run still go one after another: each uses
  the machine's cores or its GPU already.

### Fixed
- **gfdtd's `--jobs` on the GPU capped by its memory (R-D7, 2026-11-20).** Each excitation's fields are its own;
  `--jobs N` on Vulkan now runs at most as many at once as the GPU's free memory holds (`engine.runs_that_fit`,
  gpu_bytes per run), and says so, where a run past it had failed with MemoryError.
- **A Palace run that hangs silently is stopped at its timeout (2026-11-20).** `fem.palace.run.run` read
  Palace's log as it came and only then waited with the timeout, so a Palace writing nothing was waited on for
  ever, and a timeout that fired escaped as `TimeoutExpired`. A watchdog now stops it (its process group, the MPI
  ranks with it) and says so (`tests/fem/test_palace_watchdog.py`, with a stand-in palace). The roadmap's other
  quiet failures were already fixed: Elmer's timeout (`fem.children.run`), Palace's non-convergence (read and
  written beside its log), both engines' versions in the results, Palace's ranks one a fast core.
- **mom25d's estimate and memory plan (2026-11-20).** The estimate (the window's, a run's `estimate:` line, and
  what `auto` picks solvers by) meshed the board without the cell growth (`--mom-coarsen`, the default since
  2026-11-18, `--mom-cells`), the zone and the stitching vias: rf_coupler 8,242 unknowns, ~49 s, against the run's
  2,740 (now 2,740, ~17 s). `policy.memory_plan` now counts the double Z on the GPU where `choose` puts it there too
  (20k unknowns on 16 GB: 10.0 GB on the GPU, not 3.6), the via blocks the sweep keeps to interpolate them
  (`anchor_bytes`: up to a double Z on the host) and the near field (`near_bytes`: each near triangle pair's
  kept analytic parts, charge correction and indices, and for the block low-rank solver the sparse correction's
  build): the near pairs are found before the plan now (`fast.near_pairs`, kept for the FastOperator). rf_ebg
  (15.0k unknowns, 9.3 M near pairs) had been planned at 1.9 GB compressed and peaked at 8.7 (now 10.7 planned),
  dense at 6.7 GB against a solve's 7.3 (now 7.5).
- **mom25d's mixed precision accepted a refinement that had stalled (2026-11-19).** GMRES on the complex64 LU was
  taken as converged at up to 100 times its tolerance (1e-4 against 1e-6): GMRES stops at the tolerance when it
  reaches it, so that slack only ever accepted a refinement that ran out of iterations, whose error is the matrix's
  condition times its residual. On the lowpass with the sides' loops and quad cells at 0.5 GHz (its complex64 LU
  borderline; the fill's OpenMP sums moving it from run to run) it stalled at 2e-6 to 9e-5 in some runs and the
  currents came out 1 to 73 % off, said nowhere: CI's `test_thickness_sides_loops_with_quad_cells` failed about one
  run in eight. Converged now means at the tolerance; short of it the solve falls back to double precision as it
  already did above 1e-4 (no fallback, and no change in time, on demo_lowpass, rf_coupler, hairpin, antenna_patch).
- **mom25d's block low-rank solver failed on every board with thick copper's sides.** The factor's cluster tree
  had no place for the sides' functions, so it had fewer rows than the matrix (`BLRFactor.solve: R must have N
  rows`): with no GPU past 20,000 unknowns, or with too little memory for the direct solver (rf_ebg, sma_launch's
  wave-port cross-sections), mom25d gave no result. A fold now sits at its plate's middle, a vertical at its corner.
- **A sweep of many ports, or of noisy solves, lost its model (`emsdev.afs`).** The sweep's rational model was
  fitted to 1e-10, through each solve's noise (a mixed-precision solve's S: 1e-5 to 1e-4), so it took a support
  at every sample and put poles between them; and with P ports' P² entries it had no rows left to fit until P²
  + 1 solves: a constant until then. A 16-port array's sweep stopped at its 60 solves 0.87 from its own solves
  between the samples (its S not causal, 0.144 from openEMS: 0.036 now, in 16 solves), rf_bends' 6 ports at 60
  solves 0.056 off (now 34, within 3e-4), 4 ports took 37. The model now stops at 3e-4 of each entry's scale
  (at most a fifth of the sweep's tolerance), keeps no more supports than the samples determine, leaves entries
  under 1e-3 of the largest out (straight lines between their samples) and, once two models agree, must
  predict one more solve in the middle of its widest gap. Against 201 solves across each band, twelve boards'
  sweeps took 221 solves where they had taken 351, every model within 1.3e-3 of the solves between its samples
  (docs/mom.md#sweep). pcbqs's S-parameters use the same sweep.
- **mom25d's sides with quad cells: their loops.** With `--mom-quads` the plates' verticals were not taken as
  loops (the complex64 LU a weaker preconditioner in mixed precision); they are, among the quad cells' unknowns.
- **mom25d's sides with wave ports (`WAVE_SIDES` on).** A fold's half on the sheet is an RWG half there, but
  it met the sheet's triangles, the vias and the holes through the plates' own rules (3 × 3 points) where the
  sheet's edges have the strips' (centroids, their near pairs): a loop of a vertical with its folds and the
  sheet's edges, no charge on any triangle, kept 3.5e-3 of a vertical's charge, at 1 GHz 4-80 times its
  inductive part. Along a short feed the charge between a wave port's cuts jumped cut to cut (demo_transition's
  P1 at 1 GHz: 6.4 times, as sheets 1.15) and the wave fit made |S11| up to 1.8. Those pairs are now the strips'
  operator's (`Sides.strip_part`): the loops keep 1e-14, the wave ports' thickness shift follows the lumped
  ports' on the 7 wave-port boards (RMS 0.006-0.022 apart; demo_transition 0.22 before), cond at 0.5 GHz 2.4e10
  → 1.9e8 (demo_transition; sheets 1.6e8), and the lumped table median 0.037 → 0.036. Wave ports keep the sides
  (`edge` still becomes sheets with them).
- **The line model's 45 deg bends, and its junctions on scikit-rf (R-E15).** A bend of another angle than 90 deg keeps
  its arms' centre lines to the vertex, its capacitance there: cut to a corner square's edges, as qucsator's MCORN is at
  90 deg, rf_coupler's eight 45 deg bends had moved it from 0.029 to 0.060 from openEMS (now 0.028 on qucsator, 0.027 on
  scikit-rf). On scikit-rf a tee's stub, a cross's narrower pair and the arms of a junction of five or more start at
  the widest straight run's edge (they had met at the centre lines): rf_branchline 0.135 -> 0.037, rf_radialstub 0.158
  -> 0.052, rf_interdig 0.065 -> 0.041, rf_wilkinson 0.086 -> 0.072 from openEMS; a five-arm junction on qucsator the
  same.
- **mom25d's via holes (`--mom-planes holes`): a via ending on the plane sees the hole's first-order field.** A
  first-order shape leaves a potential on the plane outside its hole (-e1(0) cos phi / rho), which a via ending there
  had not been charged: such vias did not excite the dipoles at all. Two via ports either side of a 0.6 mm hole: Z21
  0.287 ohm (as with no hole) against the finely meshed plane's 0.246, now 0.241; the return vias' currents on
  demo_transition 4-7 times closer at 2 and 6 GHz (mom25d 3842ae4; found with the slot planes).
- **mom25d says so where its sparse near field cannot fit**, instead of the system killing it: the block low-rank
  solver's near-field correction was not in the memory plan, and on a fine mesh of several layers it is the larger
  (antenna_aperture's whole stack, 78,458 unknowns: 37 M near pairs, ~15 GB, killed 7 s into a 16 GB scope without
  a word). It is estimated before it is built (400 bytes a near pair) and the run stops with what to change.
  antenna_aperture's config now sets mom25d's cell to 1 mm: 8,384 unknowns, 19 s, 0.19 from openEMS, as with any
  finer mesh (the Solver Matrix of 2026-11-15).
- **Part values in a library's form.** A value written `C_22u_16V_0603` or `R_45k3_0402` (the kind's letter, then
  the value) was not understood, and the part was left out: all 788 R, L and C of the Jetson baseboard. The value
  after the kind's letter is read now.
- **An option with choices and a number** (Palace's `--palace-abc 1|2`) was refused on the command line: its
  choices were checked against the typed string.
- **Crosstalk in time on a grid that holds the trace (R-E16).** The crosstalk question solved 401 linear points to
  1 / rise whatever the traces' length, so a long trace's edge response read low (an ideal 1 m coupler under a 25 ps
  edge: 1.92 % of its 2.5 % plateau). Its grid is now 16 samples a ripple of S for the longest trace (`si.time_grid`,
  401 to 16,001 points): 2.51 %.
- **The GPUs, as run (R-C7).** mom25d's LU and gfdtd ran on an NVIDIA GTX 980 Ti through Vulkan as well as the AMD
  RX 7800 XT (gfdtd on the demo transition within 0.008 of openEMS's stored answer; mom25d's S within 1.2e-6 of the
  CPU's), and CI runs mom25d's Vulkan LU on Mesa's lavapipe (emsdev 0.3.1: `EMSDEV_VULKAN_CPU=1`, and host buffers
  on devices whose memory is all host-visible). The docs name those GPUs rather than any GPU. gfdtd's energy kernel
  crashes llvmpipe, so its Vulkan path is not in CI yet.
- **CI tests more.** A real FDTD engine runs through kicad2ems-fdtd against openEMS's stored answer (R-C6); Elmer is
  built once and cached so kicad2ems-fem's Elmer runs are tested (R-C2); pcbqs's real-board regressions on the
  examples' boards run there (R-C10); mom25d checks a plane cavity, the 10 dB coupler against openEMS and stored
  S-parameters (R-C9); the window's tests wait in an event loop and test Stop's two presses (R-C8).
- **Which Elmer or Palace it was (R-B14).** kicad2ems-fem's tools (dc, thermal, mag, cap) and Palace's S-parameter
  solver record the program, its version and revision, and kicad2ems-fem's version in their results.
- **The window's leftovers (R-F2).** The removed bridge's schematic layout, unused functions and the unused
  `planned` flag are gone; `ResultsTab.show(files)`, which overrode `QWidget.show` (the kind of bug behind A1), is
  `show_files`; the study form, the Log page and the Plots page are modules of their own (app.py 3,033 to 1,700
  lines).
- **Board cut-outs are air (R-E6).** A closed Edge.Cuts shape inside the outline is a hole in the board's outline
  (it had been dropped: the dielectric ran through every slot and cut-out), so openEMS and gfdtd fill it with air,
  Palace and Elmer mesh around it and thermal cools its edge; the notes give the cut-outs' count and area. A region
  that splits the board keeps its largest piece, as before, and now says how much is left out.
- **KiCad import, the rest (R-A22).** KiCad 9+ padstacks give a pad or via its own shape on each copper layer
  (`front_inner_back` and `custom`; it had the front's on all). Pads and vias that remove their unused layers
  keep copper only where copper of their net touches them, at the layers KiCad lists as zone connections, and at
  their ends with `keep_end_layers` (vme-wren: 35,219 rings KiCad does not draw were inside the planes'
  clearances; the import 4.7 s to 5.5 s). Copper text is copper: outlined text (`render_cache`) exactly, stroke-font
  text from a stand-in font in its box (KiCad's own glyphs are not in the file: area within about 15 %, place
  within 0.15 mm of KiCad's, on a KiCad 10 board made with pcbnew in tests/data), knockout text as its box less the
  glyphs; the notes say which. It had all been read as nothing, with nothing said.

### Changed
- **mom25d's `fast.py` split (R-F3, 2026-11-20):** 1,457 lines to 1,048, its helpers in modules of their own:
  `dense` (Z scaled and bordered, the host's and the GPU's LU and solves, the sides' loops, batched GMRES), `near`
  (the near pairs and their sparse correction), `tiles` (the block low-rank tiles, the compiled factor) and
  `threads` (the BLAS's and OpenMP's threads). `fast` still has their names.
- **mom25d's Vulkan LU: U12 as a GEMM with the CPU's inverse of L11 (2026-11-20),** in place of the GPU's forward
  substitution (MAGMA's getrf does the same): at 14k unknowns 0.898 -> 0.883 s, at 20k 2.31 -> 2.30, and the
  factor's residual 2-3 times smaller (14k: 3.5e-2 -> 1.6e-2 on a random matrix). The inverse is the one the solves
  took, made earlier.
- **mom25d without a GPU: the block low-rank solver from 13k unknowns (2026-11-20).** `policy.BLR_CPU_MIN_N`
  (`MOM25D_BLR_CPU_MIN_N`) where the CPU factors, in place of the GPU's 20k: rf_ebg's mesh, three solves on 8
  P-cores, 10.5k dense 16.6 s against 19.5 s compressed, 13.3k 26.5 / 25.1, 14.7k 32.9 / 26.7, S within 4e-7 of
  each other; a sweep of 8 solves at 15.0k 82 s / 56 s and a peak of 15.8 / 8.7 GB.
- **mom25d's Vulkan LU: 96 x 96 GEMM tiles on AMD GPUs (2026-11-19).** `gpu_vk.tile` picks each GPU's tile
  (`MOM25D_VK_GT` overrides it): on an RX 7800 XT GT = 6 runs the update GEMM at 11.7 TFLOP/s against GT = 4's 10.9,
  the LU at 14k unknowns 0.935 -> 0.898 s and at 20k 2.44 -> 2.31 s (7k unchanged: its CPU panels set the pace); the
  factors are bit-for-bit the same. Other GPUs keep GT = 4 (a GTX 980 Ti was 7 % slower with 6). The factor's stats
  now hold each step's CPU panel and wait (`stats['steps']`). What the LU's time goes to, and what was tried and not
  kept: ROADMAP.md, mom25d speed item 1.
- **The window's config and runs in progress as models without Qt (the window, item 1).** `gui/document.py`'s
  `StudyDocument` holds a config as data (the RF settings, each analysis's tables with an analysis's several runs,
  the study, and what this machine cannot show: an analysis not installed, a question no engine here answers) and
  reads and writes it, comments kept; the form and the analyses' panels are filled from it and give it their values
  on save. `gui/runqueue.py`'s `RunQueue` is a Run's queue (the order, the run going, a run passed over when what it
  needs did not finish, Stop, each state). The window's behaviour is unchanged (the gui suite's 155 tests); 6 more
  test the two without a window.
- **One repository, one package (2026-11-16 to 19).** mom25d, gfdtd, pcbqs, kicad2ems-fdtd, kicad2ems-fem,
  kicad2ems-gui, kicad2ems-ipc, dfsynth and the shared numerics (emsdev) are merged into kicad2ems with their
  history: `kicad2ems.{core,gui,fdtd,gfdtd,mom25d,pcbqs,fem,ipc,dfsynth,emsdev}`, their tests, docs, tools and
  examples under `tests/<part>`, `docs/<part>` and so on, one wheel. `tools/test_all.py` runs each part's tests in
  its own pytest. The old extras (mom, qs, gfdtd, filter, dumps, openems) install nothing more; the compiled cores
  (mom25d, pcbqs, emsdev's mesher) are built by the installer, not shipped in the wheel. The superproject's pins
  and the separate repositories are no longer how the stack is put together.
- **The GPU report and the stack map moved here from kicad2ems-project (2026-11-19).** `tools/gpu_report.py` runs
  in this repository: the GPU tests from `tests/<part>`, the boards from `examples/`, and `--check` compares each GPU
  part's last commit (gfdtd, mom25d, pcbqs, emsdev, fdtd) with the report's, in place of the superproject's pins.
  Its first report here (`gpu-report/2026-11-19.md`): every GPU test passes, and each GPU run is within 5e-7 of its
  CPU run on demo_lowpass and rf_coupler. The stack map (`docs/img/stack-map.svg`, on docs/architecture.md) names
  the parts as subpackages and their interfaces as the modules they now are (no `kicad2ems.api` or its versions).
  `tools/bench_solvers.py` is the benchmark of today's stack (it runs; kicad2ems-project's benchmark-results stays
  an archive).
- **mom25d's refinement on the GPU (2026-11-18).** Where the GPU holds the double-precision matrix and two LUs
  beside it, the hybrid fills the matrix in the GPU's mapped host memory, copies it across (no staging copy, no
  complex64 copy on the host) and runs GMRES's products there in double precision: 15 ms for two ports at 14.7k
  unknowns, against 0.12–0.19 s on the CPU. A kept LU then serves frequencies further away, so fewer are made:
  rf_ebg's sweep 104 → 82 s (29 LUs → 25), rf_siw 27.6 → 20.2 s (13 → 5), antenna_vivaldi 43.4 → 36.7 s (16 →
  9), antenna_array4x4_phased 31.5 → 27.2 s, sma_launch 50.4 → 47.3 s, rf_bends 20.3 → 18.0 s; S as before, the
  45 boards' RMS against openEMS within 0.0005 (docs/mom.md#gpu-products). `MOM25D_GPU_DENSE=off` keeps the
  products on the host.
- **mom25d fills the via and side blocks at some of a sweep's frequencies (2026-11-19).** The rows and columns of
  the vias and of thick copper's sides were filled at every frequency: 22-38 % of the run on four of the five largest
  Solver Matrix boards. Every entry of them is smooth in f once its 1/(jω) and the permittivity are taken out, so a
  sweep keeps the blocks it fills (its first seven frequencies', then those the others cannot be had from) and takes
  the rest as the polynomial through the seven nearest kept ones, where that agrees with the one through six to 1e-6;
  the Green tables of such a frequency are the same polynomial through the kept ones' tables, so the strips' block and
  the via blocks stay one kernel's. The sides' part and the via chains' are judged apart (the chains' kernels are the
  rougher: where theirs cannot be had it is filled, the sides' still taken). The sweep solves its band's two ends
  first, and a linear sweep seven frequencies across its band. The far field's fill makes each triangle's kernel row
  once, not once for each of its edges (a fresh matrix filled: rf_ebg 0.73 → 0.43 s, sma_launch 0.57 → 0.22 s). The 45
  Solver Matrix boards, each after the other on the code before and on this: 8.9 → 8.2 min (rf_ebg 111 → 100 s, 15 of
  its 39 frequencies filled; rf_diffvia 15.3 → 12.6 s), their RMS against openEMS as it was (median 0.0632, mean
  0.0987, no board more than 0.0004 from before); the 17 boards' linear solves and the wave ports' within 1e-5 and
  7e-4 RMS of before; S within 8e-5 of every block filled at the same frequencies. `MOM25D_INTERP=off` fills every
  frequency (docs/mom.md#blocks).
- **mom25d's sides cost less a frequency, the sweep a solve less a board (2026-11-18).** The plates' blocks in
  the compiled core with the observers in colours (each observer's sums at once into its rows, not through a
  chunk's buffer) and the folds' far block a triangle at a time: a frequency's blocks rf_bends 0.29 → 0.085 s,
  sma_launch 0.70 → 0.32 s, the matrices the same to 5e-16 (the 17 boards' S and the wave ports' as they were).
  The adaptive sweep's solve in the widest gap comes after its first agreement, not its second: rf_bends 31 → 29
  solves, sma_launch 11 → 10, the 44 boards 683 → 635 (rf_ebg 39 → 40); their RMS against openEMS all within
  0.0003 of before (median 0.0628, mean 0.0962). pcbqs's sweep is the same function.
- **mom25d is 2.5 times faster on the Solver Matrix's boards: 45 in 9.8 min, 24.7 before (2026-11-18).** Each
  example's own config, lumped ports, RX 7800 XT, two runs each (9.9 and 9.7; 25.5 and 23.9); the RMS against
  openEMS median 0.0632 (0.0645), mean 0.0987
  (0.1009), 44 boards within 0.005 of what they were and the 16-port array 0.108 closer; against gfdtd's solid
  copper on the 17 boards median 0.0359 (0.0360), mean 0.0506 (0.0506). As sheets (`--mom-thickness off`, as the
  Solver Matrix of 15 November ran, openEMS's copper being sheets): 7.2 min, median 0.0511, mean 0.0957; then
  30.7 min for 44 boards (0.0493, 0.0946) and 11.2 min with the *fast* setting (0.0526, 0.0968). What changed:
  - *the sweep* (Fixed, above), *the plates as cells* and their far pairs: 25.5 → 15.2 min. A plate of thick
    copper's sides is one cell of one charge density, as a quad cell of the sheet, so its diagonal is no
    unknown: two functions a plate, not three (the 17 boards' S moved by an RMS of 0.0002–0.006). Plate pairs
    more than five sizes apart take one point each in the compiled core, as the strips' far field (S within
    1e-4);
  - *the cells grow to twice `--mom-dmax`* away from the features (`--mom-coarsen`, now 2 by default: the
    coarsening of the old *fast* setting), where that is no more than a wavelength / 15 (a plane's cells: / 8),
    and to a wavelength / `--mom-cells` (30) where that is larger, each layer by its own narrowest trace: the
    8–13 GHz boards, where a wavelength / 30 left the 0.3 mm cells nearly as they were, have 0.6 mm cells away
    from the features (rf_bends 11.8k unknowns → 7.1k, rf_siw 17.0k → 11.3k, rf_ebg 17.3k → 14.7k), no board
    more than 0.003 further from openEMS (docs/mom.md#cells). `--mom-coarsen 0`: as before;
  - *a solve's passes over its matrix*: the refinement's residual from the products GMRES made (one product
    of the double Z less a solve: 0.3 s of 5 at 17k unknowns); a kept LU given up after three iterations where
    its rate cannot reach the tolerance in its eight (rf_ebg: 5 of 15 had run all eight for nothing); the
    double Z equilibrated in its products and its complex64 copy, not in place; the border blocks copied a
    block of rows at a time (14 % on the twelve largest boards before the residual and the kept LUs). With the
    cells: 15.2 → 9.8 min.

  The quad cells of the old *fast* setting do not pay on today's mesh (docs/mom.md#quads): `--mom-quads on
  --mom-quad-tol 60` on 14 of the larger boards 6.2 min against 6.1, six of them further from openEMS; the old
  flags whole (`--mom-coarsen 2` too) on the 45 before these changes: 16.6 min against 25.5, eight further.
- **mom25d's copper thickness decided in one place (`mom25d.thickness.resolve`).** The request, the board's
  planes and stack and its ports give the mode (`sides` becomes `edge` with meshed planes or the whole stack,
  sheets with holed or slot planes; `edge` becomes sheets with wave ports, see Fixed), and its reason is the
  log's one note; BoardModel and the simulation both ask it (they had their own rules, and the mixed solver a
  third). `grow+sides` is gone (median 0.040 against `sides`' 0.037, mean 0.050 against 0.051).
- **pcbqs shares core's leaf helpers instead of copying them.** pcbqs may import `kicad2ems.core.units` (new:
  part values with SI prefixes), `constants`, `touchstone` and `fields.dumps`, which import nothing from kicad2ems;
  nothing else of core (`tests/pcbqs/test_standalone.py`). Removed its copies: `pcbqs/_artifacts.py` (the dump
  files), pdn.rail's `parse_value` (now `units.value`: the whole text, so `'33n 10%'` is still not a value; core's
  `parse_value` takes a Value field's first word), pdn.models' Touchstone reader and `_to_s`, sparams'
  `write_touchstone`, its constants. `core.touchstone` gains `read_matrix` and `to_s` (pcbqs reads a v1 Z / Y file
  in ohms, as vendors write them), a `program` for the header and one zref for all ports, and refuses a file that
  ends inside a frequency point; `write_npz` / `read_npz` moved to `core.fields.dumps`. `kicad2ems.core` imports
  its board model on first use, so importing a leaf brings nothing else.
- **No plugins: kicad2ems lists its own solvers and analyses.** The registries
  name every one by module and class (`kicad2ems.core.solvers.SOLVERS`,
  `kicad2ems.core.analyses.ANALYSES`) instead of reading entry points; the
  interfaces stay (`kicad2ems.core.api`). Gone with the entry points: the API
  versions (`API_VERSION`, `compatible()`, each class's `api`, gfdtd's engine
  API and `kicad2ems.gui.api.API_VERSION`), the `broken` lists, the "not
  installed" hints (`missing()`, `EXTRAS`, `kicad2ems.core.extras`) and the six
  empty extras (`dumps`, `mom`, `qs`, `gfdtd`, `openems`, `filter`). A solver
  or an analysis whose program or package is missing says so in its `check()`,
  as before; one that fails to import is a bug. Modules renamed for what they
  are: `core/solvers/lines/plugin.py`, `fdtd/plugin.py`,
  `fdtd/openems/plugin.py` and `fem/palace/plugin.py` are `solver.py`, and
  `kicad2ems.mom25d.kicad2ems` is `kicad2ems.mom25d.solvers`; a study's
  `Engine.plugin` is `Engine.impl`.
- **mom25d's cells grow to the wavelength's size, and the mesher's interior is equilateral (`--mom-cells`, 30).**
  mom25d 3b86e68, pcbqs daaf1ec. The points inside the copper are seeded on a hexagonal lattice and smoothed (2-10 %
  fewer unknowns than Triangle at the same S; pcbqs's FastHenry cases as before with a quarter fewer triangles), and
  by default the cells keep `--mom-dmax` at corners, line ends, vias and ports and grow to a wavelength / 30 away
  from them (docs/mom.md#cells): 38 % of the unknowns and a quarter of the time on 16 boards, the median RMS from
  openEMS 0.059 against 0.057. `--mom-cells 0` is the old sizing. Tried and not taken (mom25d's `mesher-trial`
  branch): the wavelength's size everywhere, fewer edge rings, other layers' outlines imprinted on planes in place
  of their fine regions (two boards lost 0.03-0.04), via fans as polygonal cells (exact, up to 12 % fewer unknowns,
  no time gained). With long runs cheap in mom25d the mixed solver's cut saves little.
- **mom25d and pcbqs mesh with CDT, not Triangle.** Their compiled cores triangulate (CDT 2.0.1, MPL-2.0, vendored
  in each core's `core/CDT`; `core/cdt_mesh.hpp` and `cdtmesh.py`, the same files in both): the `triangle` package
  is gone, with Triangle's non-free terms and its missing wheels (none for Python 3.14). The compiled cores of
  mom25d and pcbqs are needed now (they hold the mesher; their kernels keep their NumPy fallbacks): the installer
  builds them without asking, CI builds pcbqs's on Windows too. CDT keeps triangles near their area limits, where
  Triangle's refinement left them at about half, and on the 45 Solver Matrix boards mom25d's answers hold: the
  default setting 43 boards within 0.005 of Triangle's RMS from openEMS (median 0.0558 against 0.0551) for 6 % more
  unknowns; the fast setting 44 within 0.005 (median 0.0524 against 0.0525) for 4.6 % more. Under `--mom-coarsen`
  a meshed plane is now coarsened outside its fine regions only (under the copper of the layers near it): Triangle
  had in effect left those uncoarsened, and coarsened they cost 0.03-0.08 at 6-10 GHz (rf_msvia, sma_launch,
  demo_transition). pcbqs's real-board answers moved 0.01-3 % (its CHANGELOG); its validation against FastHenry
  reads as before on the strip loop, 1 % higher L and 2 % lower R on the trace over a plane. Studies:
  ~/pcbqs-bench/cdt-trial-2026-11-15 (the quad-cell share first: a mesher of our own buys at most 2.6 % of the fast
  setting's unknowns by pairing alone).

- **mom25d's retired solver paths (R-F3).** The tiled GPU solver (`--mom-tiled`; the block low-rank solver took its
  boards), the HODLR solver (never chosen automatically) and the Python block low-rank factor (reached only with an
  older core: such a core falls to ACA, the compiled factor named otherwise) are gone, with their modules (`ldlt.py`,
  `hodlr.py`), the core's unused `static_pairs`, `biot.h_grid` and three tools importing the removed `mom25d.mqs` and
  `mom25d.pdn`. mom25d's stored board files written again (they predated the layers' `material` and `fref`).
- **Mesh convergence on every solver (R-E3).** mom25d's `--mom-converge 1,1.5` solves with cells of `--mom-dmax / f`
  for each factor, gives the finest and says how far the second-finest is (worst term, dB and degrees, against
  0.1 dB / 2 deg, as the FDTD layer's `--converge`; run.json's `converge`). Palace's adaptive mesh refinement is
  `--palace-amr N` (its Model.Refinement, to `--palace-amr-tol`). The demo transition at 0.3 against 0.2 mm cells:
  2.8 dB and 30 deg on its small S11, not converged there.
- **A passive copy of S (R-E2).** `--passive` (the window's *Write a passive copy of S*) writes
  `<name>_<solver>_passive.sNp` where a solver's S is not passive: each frequency's singular values above 1 clipped
  to 1, the least change that makes it passive, the solver's own file kept. Causality is not checked: tried on the
  impulse response, it cannot tell causal from not on a band from 1 GHz (the roadmap has the figures).
- **The window waits on nothing it can do in a thread (R-D22).** An example is made in a thread and loaded when it
  is ready; dfsynth's description for the Filter page is asked in a thread, the page's form made when it comes (the
  window's start had waited on `dfsynth --describe`).
- **The line model's coupled pairs from a tree (R-D17).** The pairing tried every pair of straight sections (480-odd
  on CM5); a tree of the sections, each grown by the farthest it couples, proposes the near ones and the same tests
  decide: the same 295 groups, CM5's formula build 5.25 s to 4.69 s. The builder's quadratic loops are all indexed
  now; what is left is each sample's geometry (a per-sample width test was tried: no faster).
- **gfdtd's excitations share their set-up (R-D4).** The operator's core (grid, materials, metal, sheets, lumped
  parts, coefficients, boundaries) and its compressed GPU form are made once for a model and kept for its other
  excitations, which differ only in their excitation, probes and dumps; kicad2ems-fdtd keeps the worker threads
  between solves, and with them each thread's GPU device. On the coupler a later excitation's set-up is 0.04 s for
  0.3-0.65 s; a second solve in the same process (a `--converge` level, a study's next run) 0.02 s for 0.46 s a
  run (demo_lowpass: 1.74 s a run for 2.21). S is unchanged (8e-8). Excitations that start together still make a
  device each.
- **emsdev is pinned (R-C5).** Every package that needs it names its release tag, `v0.3.0`, the same URL in
  all five pyprojects (it was main's archive), and `sync_pins.py` checks that and says when main is past the tag.
- **pcbqs's switches are vdmos by default (R-A18).** A board's circuit, the KiCad import and the examples switch
  with ngspice's VDMOS and a generic MOSFET's capacitances (Cgs 1 nF, Cgd 50 pF, Cds 500 pF) unless the part's are
  given, so a switching loss is a device's, not the drive ramp's; pcbqs's CHANGELOG has the figures.

### Removed
- **The GUI's copies of KiCad (2026-11-18, wave 3).** The Circuit page's
  *KiCad source sheets* and *KiCad component data* views (KiCad shows both);
  importing a schematic reads its netlist only. The System panel's advice to
  install CuPy, and `emsdev.gpu.cupy_device` (no solver uses CuPy now:
  `gpu.probe()` lists the Vulkan GPUs). The old names `app.run_notes`,
  `app.LOWER`/`BESIDE` and `traces.port_names` (`runtab`, `windocks`,
  `core.touchstone`). The two plots (S-parameters, an analysis's curves)
  share their buttons and marker table (`gui/markers.py`); the curves'
  table is as tall as its rows, as the S-parameters' was. The Circuit
  page's *Choose PCB…* (its settings' Board field has a …); *Save model…*
  writes as the study's save does (the board relative when beside it).
- **gfdtd's `GFDTD_VK_BLOCK` and `GFDTD_VK_IDX`.** Tuning variables nothing
  set or documented: the Vulkan kernels' workgroup is 64 and their index
  `int` (2026-11-18). The documented settings that no test had set now have
  tests: `--part-file`, `--deembed-file`, `--port-kind`, `--mesh-zone`,
  `--nz-mask` (now in the [usage](docs/usage.md) table) and
  `lines.reference.FIELD_CPW`.
- **`kicad2ems.core.api` and `kicad2ems.gui.api`, the re-export layer.** A part imports the module that
  defines a name, by its own name (`kicad2ems.core.study.read_config`, not `read_study`); each such module's
  `__all__` is its API, listed on [APIs](docs/api.md). A test checks that importing the core's solver and
  analysis modules loads no PySide6, matplotlib or h5py.
- **Superseded code, tools and examples (2026-11-18).** pcbqs: the PEEC PDN trial
  (`pdn/peec_trial.py`, its tools and the `pdn-peec-trial` and `peec-memory`
  examples; the compressed backend stays, `docs/pcbqs/peec-memory.md`), the skin
  demo and `peec-skin` example, `cm5_converter_fields.py` (`pcbqs.mqs
  --converter` does it), the CuPy fill (`static_cupy*.py`, `--fill-device`;
  OpenMP took 0.9 s where it took 39.6 s), the thermal mesh solver (`--method
  mesh|both`, `tri_locate`: the grid is the one solver) and `side_charge`.
  fem: Elmer's capacitance tool (`elmer_cap`, `kicad2ems-fem-cap-elmer`) and the
  DC drop's `--mesh tet` (and `--h-fine`). core: the line model's tensor-grid
  solve (`xsection.MESH = False`; the grid stays for `potential()` and the
  finish's loss share). gfdtd: the CuPy backend (Vulkan, the core and numpy
  stay). The old names `kicad2openems`, `kicad2openems-gui`, `from kicad2ems
  import <core name>` and `parts._kind_value`. Tools: `perf/{cli_prof,dcfix,
  dcmesh,dcsolve}.py`, `mom25d/hodlr_study.py`, power-workflow's 05, the
  Windows workflow and `ci_deps.py`. `pcbqs.wpt` is `pcbqs.power_spice`'s.
- **mom25d's retired paths (2026-11-18, wave 2).** The mixed solver
  (`--solvers mixed`, `--mixed-cut`: with long runs cheap in mom25d its cuts
  saved little). Line basis functions (`--mom-lines`, experimental: worse than
  quad cells). The GPU's streamed fill (the rows filled straight into the
  Vulkan factor: antenna_cp_patch 46 s and 5.8 GB a solve, block low-rank 22.5
  s and 2.4 GB). mom25d's CuPy backend (`MOM25D_GPU=cupy`): Vulkan is the GPU
  path; with gfdtd's and pcbqs's gone no solver uses CuPy, so the `gpu-cuda12`,
  `gpu-cuda13` and `gpu-rocm7` extras and the installer's CuPy question go too
  (it offers to remove a CuPy wheel left in the venv).
  The NumPy kernels as solver paths: the dense solver (`solver='dense'`),
  ACA / H-matrix (`fast.HMatrix`, `aca`), `--mom-core` (`fast.use_core`,
  `MOM25D_NO_CORE`) and the kernels' NumPy fallbacks: the compiled core is
  required, as the mesher already needed it (`auto` chose none of them with
  the core built; ACA took more than 30 minutes a frequency where block
  low-rank takes 22.5 s). The tests keep the NumPy references they check the
  core against (`tests/mom25d/dense_reference.py`). The old names
  `mom25d.afs` and `mom25d.blr` (emsdev's `afs`, `fast.cluster_tiles`).
  `--mom-signal-vias` (square or N-sided signal vias: 0.03-0.05 off at 5-8
  GHz, nothing saved; signal vias are round, `--mom-stitching-vias` stays).

### Done before the merge, recorded in the ROADMAP until now (2026-11-15)
- **gfdtd's memory (R-D7).** The build's host peak 443 to 229 B a node (the geometry as views of the grid's
  spacings, compress's arrays let go once used), what the core keeps 152 to 86; the GPU backends check the model
  against the free VRAM, `auto` running one that does not fit on the CPU.
- **Palace on real boards (R-B21).** `feature_size` takes every piece's nearest gap (a tree, not the first 60 a
  layer), the copper's outlines are straightened (to a twentieth of the feature, at most 10 µm), and `estimate()`
  builds the mesh and scales it by the examples' runs (demo_lowpass at the default feature: 16 min and 13.5 GB
  estimated, 30 min and 19.3 GB as run).
- **`elmer_dc` on the layered wedges (R-D23).** wpthb VHB in 0.8 s and 120 MB, within 0.2 % of 1.1 M tetrahedra's
  drop.

### Decided
- **Wave-port set-up (R-D10).** A port's line is characterised as a 3D model of its own, kept on disk by
  cross-section: on `tests/mom25d/boards/launch.json` 6.8 s of the first run's 31 s, 0.5 s cached. A shorter line
  is no cheaper way (18 mm against 8 mm: Zc 6 ohm and beta 1 % off; 10 mm: 36 ohm); a 2D cut of the feed would need
  a full-wave solve of its own and would not take a coplanar ground stitched by vias. Left until a board needs it.
- **No one shared gfdtd device (R-D4.2).** A worker's device and pipelines cost about 1 s once and nothing on its
  later runs; the threads make theirs at the same time, and slangpy gives a device one queue, so one device would
  save little wall time and stop the runs overlapping.
- **gfdtd's energy check syncs every 100 steps (R-D8).** Every 1,600 gains 1.7 % of the steps a second on 1.7 M
  nodes and 3.6 % on 0.22 M (RX 7800 XT): not three backends' work.
- **The fem part's `mag` keeps its n(n+1)/2 energy solves** (three for a coil pair, where flux linkage would take
  two and each coil's flux integral).

## 2026-11-14 (0.3.0)

### Changed
- **Semantic versions.** Every package of the stack is 0.3.0 (the KiCad plugin keeps 0.1.0), in place of
  the four schemes before (`261112.postN`, `261102-r1`, `0.2.6`, `0.1.0`); the floors between them are
  `>=0.3.0`, and each repository is tagged `v0.3.0` (R-C13). gfdtd's features are named by its engine API
  (1.1 lossy sheets, 1.2 roughness, 1.3 finish), not by version. The superproject's README has the rules.
- **Authors and licences.** `authors` in every pyproject is Claude and lowprocess (it was the assistant's
  address alone, and pcbqs and dfsynth had none). The superproject has a LICENSE (GPL-3.0), and the
  READMEs of kicad2ems, mom25d, pcbqs and the superproject say that Triangle, which the `triangle` mesher
  compiles in, keeps its own terms: not for commercial products without its author's licence (R-C14).

### Removed
- **pcbqs's piecewise-linear circuit engine.** The circuit run is for its parts' losses (thermal) and its
  copper's currents (fields), and on the wpthb bridge's copper the piecewise-linear engine was either no faster
  than ngspice and wrong on the switches' losses, or right and hours a run. ngspice is the one engine; the
  window's Circuit settings lose *Engine*.
- **The full-bridge circuit tool (pcbqs, kicad2ems-gui).** `pcbqs.power_circuit` was a second circuit tool beside
  `pcbqs.circuit`, for the wpthb bridge alone. The bridge is a general circuit model now (pcbqs's
  `data/wpthb_bridge.json`, under Examples > Circuits > Bridges), with the same answers on its board (93.11 % for
  93.13 %); its receiver is fixed rather than tuned at each frequency. The window's Circuit page and schematic
  editor are the general model's alone, and a format 1 model is refused with where its bridge went.
  kicad2ems-fem's *Use these coils in the circuit* sets the model's coils (`--set PRIMARY.value=...`).

### Changed
- **Speed, from the review of 2026-11-14 (R-D):**
  - pcbqs's circuit knob runs at a quarter of the periods once one has settled (wpthb 25.1 s for 40.9, the same
    answer);
  - the PDN's loaded and bare impedance from one factorisation a frequency (3.7 times faster on its test board);
  - one cell through the solder mask in FDTD by default (4 had made the hairpin 17 times longer);
  - the line model's junction split as one indexed pass (the CM5's 94 nets: 8.3 s for 11.6);
  - the parts' gap search through a spatial index (a 105-footprint board's `Board`: 0.27 s for 0.95);
  - the near field can choose its compressed solve (`--backend hierarchical`);
  - the crosstalk's layout estimates solve each cut once and the wider gaps for the three worst stretches only,
    which the report shows (a synthetic 12-trace bus: 1.8 s, where widening every pair took 34 s); a stretch whose
    cut cannot be solved is said in the warnings (it had been dropped silently);
  - the window reads the run folders once while they are unchanged (kicad2ems-gui): what each analysis still
    needs had listed and read every run once per analysis and per row of its table at each settings change (20 ms
    a time at 200 runs);
  - pcbqs's S-parameters sweep where a rational model is least sure (emsdev.afs, mom25d's sweep), at most 31
    solves, where 31 log-spaced points had been interpolated linearly across a filter's resonance; its via matrix
    from arrays (2,500 segments: 0.35 s for 19.5) and its loop tree breadth first (loops a third shorter);
  - mom25d keeps its wave ports' line characterisations on disk by cross-section (`MOM25D_CACHE`; `off` keeps
    none): the launch board's set-up run again 0.5 s for 14.5;
  - mom25d's via blocks: the chains' kernel on the triangles in its compiled core, the fans' charge product sparse
    and the pairs' geometry made once (317 vias on the launch board: 1.36 s a solve for 3.43, S within 4e-7).
- **The Viewer's views are Plan, 2D, 3D, Images (kicad2ems-gui).** In that order, the board flat first; *Cut*
  is named *2D* (a field dump in a cut), and *Section* left for a page of its own. A setting's **Pick** button
  is capitalised.
- **The window is docks again, with the settings on the left (kicad2ems-gui).** The fixed three-column layout
  (the settings in a column on the right, a drawer under the board, nothing movable) is taken back, on the
  user's word: the selected question's settings are a dock on the left under the study's, the Viewer, the
  editors, Results, Run and Filter Synthesis are docks of the workspace, and each moves, tabs, floats and
  closes as before; a new window has the board over the Results and Run pages and opens over the whole screen,
  later ones as the last was left. What came with that layout and is not about it stays: the few settings
  first, "Still needed", Run's wording, what a result proposes.

### Added
- **Passivity** (the review of 2026-11-14, R-E2, its first part): each solver's S-parameters are checked for passivity
  (their largest singular value against 1 over the band): a warning where they give out more power than goes in,
  and run.json's `passivity` (largest, where, at how many frequencies). Nothing had checked it; enforcing it is
  left.
- **The window begins on a Start page (kicad2ems-gui).** A window with no board showed an empty Viewer over
  empty Results. It opens on *Start*: *Open a KiCad board…*, *Open a config…*, *Open a results folder…*, the
  configs opened lately, and every example as a tile by group with its board drawn from above (drawn in a
  thread the first time and kept in the cache folder; *Find an example* filters by word). The page is a dock,
  tabbed with the Viewer, and has the workspace to itself: the result pages step aside while there is no board
  and nothing to show, and come back where they were when a board is chosen or a result opened. *View > Start*
  brings it back.
- **A result's pages are docks of their own (kicad2ems-gui).** The Results page held three modes behind
  buttons (Plots, Report, Tables) and the Run page the log. They are four pages, *Plots*, *Report*, *Tables*
  and *Log*, each a dock that moves, tabs, floats and closes: Plots beside the board where the workspace has
  1,400 px (else the first tab under it), the others tabbed under the board. A run's end brings the Report to
  the front (the Plots for S-parameters), so a report and its curves, or a table and the board, are seen
  together.
- **Section is a page under the board (kicad2ems-gui).** The field across a line-model section was a view of
  the Viewer, so opening it hid the plan the section is picked on. It is a page of its own, tabbed with the
  Report under the board: pick a section on the plan or in the list and its field follows. It is there while
  the folder shown has a line model, and solves only while it is in view.
- **The steps of a study, as a bar (kicad2ems-gui).** Under the toolbar: Board, Questions, Set up, Run,
  Results, each with where it stands in a line (the board's layers and nets, what is asked, what is still
  needed, the estimate or the run's state, what the result is), those done ticked and the next one marked; a
  click goes to where the step is done. The toolbar stays what acts. The bar is a toolbar of its own: it drags
  to the bottom of the window and *View > Steps* hides it, both kept with the layout.
- **The study as a model with no window (kicad2ems-gui: `study.StudyModel`).** What is asked of a board, who
  answers each question, the Fields ticks, what cannot run here and why, each run's state and the runs a Run
  takes were the Study dock's list's own, inside a Qt widget. They are an object that imports no Qt, with
  listeners for its changes; the list (`studytree.py`) draws it and passes on the user's hand. Nothing in the
  window changes; a program that drives a study without the window (kicad2ems-ipc, a test) uses the same
  object. The settings' panels still hold their own values (the roadmap's next step).
- **The mixed solver cuts two traces side by side as a coupled line.** It cut only lone traces: anything with
  other signal copper within 3 widths or 4 heights stayed in mom25d, so a differential pair or two signals
  routed together kept all their unknowns. Two parallel traces of one width with nothing else near are now cut
  together: a mixed-mode wave port on each face (mom25d's `DiffPort`), the stretch put back as the pair's
  differential and common mode lines, each with its own γ and impedance. Two 0.38 mm traces 0.3 mm apart over
  40 mm, 2 to 10 GHz: 3,926 unknowns to 1,892 and every term within 0.006 of mom25d's, the far-end crosstalk
  (the two modes' different speeds) within 4 % at the band's low end; the current dumps draw both traces from
  the two modes. A pair's faces keep 0.4 rad of feed at the lowest frequency (a lone line's 0.1), so a pair
  that is short for its band is left to mom25d (rf_crosstalk, 40 mm from 0.5 GHz). docs/mom.md has why.
- **mom25d: a trace over a plane's hole (`--mom-planes holes`).** A plane that only vias run through is a
  perfect conductor with each hole a few unknowns; the hole's field was taken as axisymmetric, so a trace
  crossing its rim saw the plane closed under it (0.065 in |S| from the meshed plane at 10 GHz on a microstrip
  to stripline transition, 0.08 on demo_transition). The hole now also carries its first azimuthal order: two
  shapes that go as cos φ and sin φ about the via, each a part along k (TM, a potential as before) and a part
  across it (TE, tested on the edges as a field), with the holes of one plane coupled. Against the plane meshed
  finely enough to have settled: 0.004 on the transition (1,661 unknowns for 11,256, 0.3 s a frequency for 16 s),
  0.005 on two transitions 1.2 mm apart and on demo_transition (1,508 unknowns for 14,220; 3,910 with the
  plane's usual cells, which are 0.01 from the fine mesh themselves). demo_transition's S11 agrees with openEMS
  to −31.3 dB, as the meshed plane's does (−25.9 dB before). The hole's kernels are also five times faster (the real Bessel functions on
  the real part of the contour). docs/mom.md#plane-holes has the table and what keeps it from being the default.
- **A setting is picked on the board (kicad2ems-gui; `kicad2ems.layout`, API 2.11).** The Viewer's plan shows
  the board as drawn as soon as one is chosen, before any preview or run: every net's copper and the parts'
  outlines, read off the event loop (`board_layout`: CM5's in 3.7 s). A setting that names a net or a part has
  a **pick** button beside it: with it down, a click on the plan puts the trace's net or the part under it into
  the setting (one that takes several adds it, and takes it out when clicked again), and what the setting holds
  is drawn in blue. A rail's table has one button for the selected cell, which picks a net or a part by its
  column. While a run's result is in the plan, the pick is on the board as drawn (a run's board has only the
  nets it simulated and no parts), and the result is back when the button is let go. What may be picked is the
  option's own word (`pick`: `'net'`, `'nets'`, `'part'`, `'parts'`), so the window knows no setting by name:
  the crosstalk question's traces, aggressor and near parts; pcbqs's rail, ground, source, converter, inductor
  and capacitors; kicad2ems-fem's nets and source. `net_at` and `part_at` say what is under a point.
- **The joined band's crossover goes where the two solvers agree best** (`join.best_crossover`), at or below the
  frequency pcbqs says it holds to, which is a rule of thumb and often generous: `demo_transition` from 10 MHz to
  6 GHz was joined at 714 MHz with the two 0.043 apart, and is now joined at 17 MHz, 0.0012 apart (mom25d's low
  end holds since its loop/tree basis). The estimate stands where they agree as well everywhere; the summary's
  `joined` has both (`crossover_Hz`, `estimate_Hz`).
- **A signal-integrity question: the crosstalk between traces (`kicad2ems.si`; docs/si.md).** Asked by the user.
  The traces are named by their nets; each gets a port at its two ends (found along its tracks: on a pad, or
  just in from a free end), the S-parameters between them are solved by the line model (its coupled sections
  from the 2D field solver; a full-wave solver beside it on request), and the answer is NEXT and FEXT over
  frequency and for an edge of a given rise time (percent of the swing), one aggressor at a time and every
  victim with all its aggressors (the worst case in time, the power sum in frequency), against a limit where one
  is given (`mode`: next, fext or both). The layout's part: every stretch where two of the traces run side by
  side, with its length, gap, Kc and Kl, the estimates they give ((Kc + Kl) / 4 saturated or by length;
  (Kc − Kl) / 2 × Td / ramp), which case each is in, and what a gap 1.5, 2 and 3 times as wide would give.
  *Find traces side by side* proposes the board's close pairs (a differential pair's own legs left out). It is
  kicad2ems's first own analysis (`analyses.BUILTIN`: in the registry without an entry point), a new group
  *Signal integrity* in the study, and needs no window code. `examples/si_bus.toml` (three traces 0.2 mm apart
  for 30 mm, 100 ps): NEXT 6.0 %, FEXT −6.5 % by the line model in 2 s; mom25d 5.9 % and −7.1 % in 30 s.
- **Coils with ferrite as a question (*Coils and ferrite*, kicad2ems-fem's `elmer_mag`).** The user asked how a
  wireless-power coil with its ferrite gets into the software: it was a command of kicad2ems-fem alone, its L and
  k copied into a circuit by hand. It is now a question under *Power* that needs no board: each coil and each
  ferrite plate a line of the settings, Elmer's static field (two coils and a plate: 2 minutes), a report with
  the inductances, the coupling and the same coils in air, and a proposal on the result, *Use these coils in the
  circuit*: the full bridge's primary and secondary inductance and their coupling (pcbqs's new `--primary-l` and
  `--secondary-l`, bound to the model in the bridge's page). The README example: 6.45 and 6.80 µH, k 0.567 (in air
  3.74, 5.77, 0.474).
- **mom25d's block low-rank solver is compiled, and the large boards' path.** The compressed solvers were Python
  (an SVD of every tile, the tile LU in Python loops): more than 30 minutes a frequency on antenna_cp_patch
  (40.8k unknowns), so the GPU paths had to hold the whole matrix. The core now has the block low-rank LU
  (`BLRFactor`): tiles of a geometric cluster tree, the separated pairs compressed (adaptive cross approximation,
  QR and SVD), a right-looking tile LU in single precision with the low-rank updates recompressed, on OpenMP's
  threads with SciPy's BLAS and LAPACK (nothing to link); the factor preconditions GMRES on the exact operator,
  the ports' columns together. mom25d takes it from 20,000 unknowns wherever the GPU's hybrid does not hold the
  matrix: rf_siw's sweep (28.7k) 272 s against 520 s tiled on the GPU, antenna_cp_patch 22.5 s a solve in 2.4 GB
  against 30.3 s on the GPU LU, antenna_vivaldi (21.9k) without a GPU 202 s against 243 s, in 1.2 GB against 11.5.
  Its tolerance is 1e-5 (1e-3 left antenna_cp_patch unsolved); a factor that stalls GMRES is made again at a
  hundredth of it. docs/mom.md#blr.
- **mom25d solves low frequencies in a loop/tree basis (pcbqs's).** Below a few hundred MHz the MPIE's charge part
  (1/jω) swamps its current part (jω) in the matrix mom25d formed, and the loops' currents were lost to rounding:
  the single-precision LU stopped preconditioning (demo_transition below ~100 MHz: each solve again in double)
  and double precision itself drifted below ~10 kHz (S11 3 % off at 10 kHz, its sign wrong at 1 kHz). Below
  (k h)² = 10⁻⁵ the direct solver now works in the loop/tree basis pcbqs uses (`mom25d/looptree.py`): the mesh as a
  graph, a node per triangle and one for the ground, a branch per edge and per via chain (its fan's charge moved
  onto one triangle), a breadth-first tree that carries the charge, and loops that carry none. The current's part
  is filled with the tables' charge side zeroed, the charge's as potentials between triangles added on the tree
  alone (the compiled core's new `near_q`, `tree_cols`, `tree_rows` and `charge_into`), so nothing cancels.
  demo_transition from 100 kHz to 100 MHz: 37 s to 7 s, the same S to 1e-6; with copper loss at 10 Hz S11 is the
  copper's DC resistance (4.806e-4; the RWG basis 5.34e-4, pcbqs 4.9e-4); at 1 and 6 GHz the two bases agree to
  1e-12. Each basis keeps its own LUs across a sweep. `sim.loop_tree` (or `MOM25D_LOOP_TREE`) chooses; the
  compressed and tiled solvers, quad cells, line functions and `--mom-planes holes` stay in the RWG basis.
- **One band from pcbqs and a full-wave solver (`<name>_joined.sNp`).** A run with both wrote their Touchstone files
  side by side and left the user to know which to believe where. It now also joins them (`join.py`): pcbqs
  below the frequency it says it holds to (its `valid_to_Hz`), mom25d (else the other full-wave solver) above,
  a raised-cosine blend in log f across a fifth of a decade each side, and how far the two disagree there in the
  log (a warning above 0.1 in |S|) and in the summary (`joined`). The window lists it as its own source, first,
  and the run's plot overlays it. `demo_transition` from 10 MHz to 6 GHz: crossover 714 MHz, the two within
  0.043; mom25d's own run there fell back to double precision at 10 MHz (its mixed precision did not converge:
  the low-frequency breakdown the roadmap's loop/tree basis is for).
- **Several rails in the window, as a table (kicad2ems-gui; API 2.10's `several_keys`).** The user chose the table of
  the two mock-ups. An analysis that says what differs between its runs (`several_keys`: pcbqs's rail tool, the
  rail, its volts, source, ground source, loads and load spectra) has those as a table at the top of its
  settings, a row a run, *Add a rail* and *Remove the rail* under it, and the rest of its settings below, shared.
  Run makes each row's run in turn, a folder each, as one job of the study (the panel's `ended` signal, after
  the last row); "Still needed" names the row; a config's `[[analysis.pdn]]` tables open as the rows and are
  saved as them. On CM5 Minima: +5V and M2_3V3 in one Run, 13.4 and 26.2 mV of drop.
- **The installer offers the finite-element tools (kicad2ems-fem) with Elmer and Palace.** It was the one package
  of the stack `install.py` never mentioned. Asked for by default only where Elmer or Palace is found. Elmer: one
  found, a folder given (`--elmer PATH`), or built from source on Linux and macOS (`--elmer build`,
  `tools/build_elmer.sh`: serial with OpenMP and UMFPACK, no GUI; two minutes on the development machine at -j8).
  Palace: one found (an executable, or an unpacked image run in podman), one given (`--palace PATH`), or built
  with Spack (`--palace spack`, `tools/build_palace.sh`: never the default, several hours): Palace's newest
  release (0.19.0), from Palace's own Spack packages with its patches to Spack's, Spack's packages at the commit
  Palace's CI names and its requirements (OpenMPI 5, OpenBLAS, MFEM with exceptions), since Spack's own package
  list stops at 0.16.0. What was chosen goes into `kicad2ems-site.toml` (`elmer`, `palace`, `palace_rootfs`), which
  kicad2ems-fem now reads after the `K2E_*` variables; `--update` brings kicad2ems-fem up to date with the rest.
- **Several rails in one study (API 2.10).** A config's `[analysis.pdn]` was one rail, so a board's rails took
  as many configs. An analysis may now be given several tables, as an array (`[[analysis.pdn]]`), and
  `kicad2ems study` runs it once for each, into a folder of its own named by the run's tag (the rail), with
  its own lines in the plan (`2 runs: +5V, +3V3`) and in the study's summary; one that fails or cannot start
  does not stop the others. `analyses.read_sets` (`read_analysis_sets` for a front end) gives every table;
  `read_config` gives the first, as before. A plugin says what a study may hold several of it by with
  `several` (`'rail'` for pcbqs's power integrity, `'converter'` for its near field). The window shows and runs
  the first, says how many more the config has, and keeps them when it saves (`docs/usage.md#several`).
- **What a result proposes goes on to the next question, asked (kicad2ems-gui; pcbqs).** A finished question's
  proposals were buttons that filled another's settings; they now tick that question too, so that Run includes
  it, and bring its page to the front. An analysis with an editor of its own takes a proposal as it takes its
  config table, so a model the proposal names is opened there, as the study's own (a config writes it beside
  itself, not into the result it came from). With pcbqs's new proposals that is a chain through the power
  questions: a converter's near field offers the converter as a circuit (its capacitors and inductor from the
  board), and the circuit's result offers its supply rail with what is drawn from it to the power integrity
  (the ripple the current's harmonics make on the rail) and its input current to the near field (in place of
  the assumed pulse). On CM5 Minima's U702: 94 % at a duty of 0.72, 140 mV peak to peak on +5V at the
  regulator (pcbqs's CHANGELOG and `docs/power-workflow.md`; `docs/gui.md#proposals`).
- **The window in one fixed layout, with the few settings first and what a run still needs (kicad2ems-gui; API
  2.9).** Steps 3 to 5 of its redesign (`kicad2ems-gui/docs/redesign.md`), as the user chose them from a
  prototype. *Layout*: the board and the questions on the left, the board itself always in the middle (the
  Viewer; the circuit's schematic a tab beside it while Circuit is asked) with the Results and Run pages in a
  drawer under it, the selected question's settings on the right. The columns and the drawer fold from View and
  stay folded; nothing docks or floats, and a dock layout an older window saved is not read; Filter Synthesis
  is a window of its own. *Settings by level*: an option has a `level`, and a solver names the options a run is
  set up with (`basic`): the frequency range and points and each chosen solver's main cell size are in view,
  the rest under "More settings" in their sections, with a box that finds a setting by a word of its name, key
  or section; the board's file and extra ports first, the rest under "More about the board". *Still needed*:
  under the settings, what a run of the selected question lacks, each line leading to where it is set, and the
  Run button says what it will run ("Run 2") or the first thing missing ("Choose the board") and goes there
  instead of starting. `rf_needs(values)` is kicad2ems's own check (no board, a KiCad board without a port, a
  band the wrong way round: the footprints are read, no model made); an analysis says its own with
  `missing(values, board, results)`, by default what its command raises, and `uses` names the analyses whose
  result it can start from, so that what the circuit will give the temperature in the same run is not missing.
  pcbqs's rail tool says there a load with no pad on ground whose return the board does not show; where it
  shows it (a half bridge's low-side switch) the rail takes it, and the drop has the ground's share (pcbqs's
  CHANGELOG).
- **mom25d: a plane a via runs through as a perfect conductor (`--mom-planes holes`, experimental).** A plane
  with an antipad had to be meshed, and was most of the unknowns of a via transition. `LayerStack(pec=)` puts PEC
  planes inside the stack (the network shorted there: the copper above and below in one model, decoupled), and
  `mom25d/frill.py` joins the two sides at a via's hole: the via's current runs through, and the voltage across
  the hole is an unknown with one equation, the magnetic field continuous across it, whose row is the transpose
  of the column the hole's field makes in the other equations (symmetric, in the via rows' border). A board's
  planes qualify when their only holes are those of vias through them (`board.plane_holes`). Checked against
  the plane meshed: two pads joined through a 0.45 mm hole, 202.0 fF meshed, 203.2 fF as a hole, 280.6 fF with
  the hole closed; a trace-to-trace transition through it, 1,656 unknowns for 4,978 and nine times faster,
  within 0.03 in |S| to 5 GHz and 0.065 at 10 GHz; demo_transition 1,504 for 3,910, |S21| within 0.02 dB and S
  within 0.08. What is left of that difference is the trace over the hole, which the axisymmetric field does not
  have (roadmap), so it is an option, not the default.
- **Coupled sections on the board's cut.** A coupled group was solved over ideal planes, without the copper
  beside it. It is now cut as a single section is: one cut across the group's middle, out to 5 reference heights
  beyond its outer lines, its lines as the strips and the copper found on every layer as ground
  (`reference.group_cut`, `_solve_group`; similar cuts and mirror images share a solve, many go to the worker
  processes). `--lines-field auto` (the default) solves a group on its cut where the cut holds what ideal planes
  leave out, by the single sections' rules with the group as one strip (`reference.group_beyond`; the lines'
  notes say what), `all` every group, `off` none. The report's coupled-sections table says which each was solved
  on, and the section view draws the cut. On the CM5 Minima (295 groups, 780 mm of pairs), against every group
  on its cut: over ideal planes the odd mode is over 3 % off on 154 mm (up to 29 % high where an inner
  layer's ground pour fills in between a bus's traces, 24 % low where a plane ends under a pair) and the even
  mode on 176 mm; with `auto` (86 groups on their cuts) the odd mode is within 2.1 % everywhere and the even
  within 3.1 %, in the same 20 s. Copper between two lines of a group (a guard trace, a pour) is in its cut
  and always has it solved there, and a pair solved on its cut is the multiconductor block in qucsator too (its
  MCOUPLED knows nothing of the copper around the lines): `rf_crosstalk_guard`'s far-end crosstalk is −21.7 dB
  at worst instead of −17.3 dB (gfdtd −20.7 dB), 1.0 dB rms from gfdtd instead of 3.6 dB. The other examples'
  groups hold nothing but their planes and are unchanged (their cuts solve within 0.02 Ω), but for
  `rf_combline`, whose middle rod is not in the line model and is now ground between the outer two (no pass
  band either way).
- **The Circuit page is the model's editor; its settings are generated (kicad2ems-gui).** The page keeps the
  schematic and the model's JSON. Its settings are a panel in the Study dock made from the circuit plugin's
  options, as every analysis's are (the two tools' own tables; the options of the other kind of model hidden;
  an option with choices is a drop-down), with a *Model* section for a general model's parameters and sweep.
  The full bridge's operating points and switch model show the model's values, and what is typed goes into the
  model; the switch model is the model's own instead of the first in a list, and the behavioural `switch` is
  warned of (U5). Overrides are by group (the tank, each rail's capacitors, the filter) and are the run's, so
  the model keeps each part's value (U2); nothing names wpthb's parts (U1). The page's own Results, waveform
  and Log tabs are gone: a circuit's report, tables and waveforms are on the Results page (the full bridge's
  too, which the registry did not know), its fields in the Viewer, its log on the Run page. A run is the
  plugin's command on the model as edited.
- **The window asks questions (kicad2ems-gui).** The Study's list is the questions that can be asked of the
  board, by group (RF, Power, Thermal), instead of one row per analysis plugin and one for all of S-parameters.
  A row has a tick (it is asked), who answers it (click the cell: a menu of its engines with *auto* first;
  several ticked all run and are compared), a Fields tick where an engine can write field dumps, and its state
  per engine. The solvers are chosen on the S-parameters row (the Solvers section's tick boxes are gone, the
  preview's estimates are in the menu); *Antenna pattern* is a row of its own, the run's antenna mode; *Trace
  impedances* is the line model. Fields ticked with nothing set fills a default into the settings (an FDTD
  solver: E on the first port's layer at the band's centre; mom25d: its currents there; a circuit: its field
  maps); unticked, the run leaves the dumps out and the settings stay. A window without a config starts with
  the solvers at *auto*, as the command line does. A config is read and saved with its `[study]` tables, and
  one that only has `[analysis] run` opens as before.
- **Two engines' answers side by side (API 2.8).** An analysis gives the figures of its answer by name
  (`figures(folder)`), and engines that answer one question name the same quantity alike (`Question.figures`
  says which): a rail's own copper drop per load, the hottest point and each part's temperature, the capacitance
  between two nets. `compare_engines` lays them side by side, and the window's Results page adds them to the
  report as a section and table, *Engines compared*, whenever another engine has answered the same question on
  the board. pcbqs's rail tool, its thermal tool and kicad2ems-fem's four give theirs. On pcbqs's buck example:
  hottest 77.4 °C (pcbqs) and 79.1 °C (Elmer), parts within 0.7 K; a rail's own copper 0.0579 and 0.0573 mV,
  with pcbqs's drop including the ground return (1.08 mV) beside it as its own figure. `default_fields` is what
  an engine is asked when its field dumps are wanted and none is set.
- **A study in a config, and `kicad2ems study` (API 2.7).** A config's `[study]` tables say which questions are
  asked of the board and who answers each: `run = ["sparams", "dc", "thermal"]`, `[study.thermal] engine =
  ["pcbqs", "Elmer"]` (one engine, or several to compare; by name or as a list shows it), `fields = true` for an
  engine's field dumps. `kicad2ems study board.toml` runs them in order, which only the window could do: the
  solvers together as kicad2ems's own run, then each analysis as the command its plugin gives, with its
  settings from `[analysis.<name>]`, each into a folder of its own that says who made it; `--list`, `--only`,
  `--preview`. An engine that answers two questions answers them in one run; one that fails or cannot start
  says why and the rest go on. `[analysis] run` is read as before. `api.frontend` has the study (`Study`,
  `read_study`, `write_study`, `study_plan`) and the run folders' names (`run_dir`, `tool_dir`, ...), which
  were the window's. Checked on pcbqs's buck example, both engines from one study: the temperature by pcbqs
  and by Elmer (hottest 77.4 and 79.1 °C, the parts within 0.7 K) and a rail's own copper drop (0.0579 and
  0.0573 mV). docs/usage.md#study.
- **A low-frequency solver: `--solvers pcbqs`.** pcbqs's quasi-static solve of the copper as an S-parameter
  solver (`qs` extra; also `qs`, `quasistatic`), for the low end of a band, from DC to about a tenth of a
  wavelength: the copper's resistance, the loop inductance with the return current where it flows, and the
  copper's capacitance where the band reaches it. It is run only when named (`auto` and `all` leave it out),
  says to what frequency it holds on the board, reports two ports whose return is not joined, and writes each
  layer's current for the Viewer. `--sweep log` (`sweep = "log"`) spaces a run's frequencies evenly on a log
  scale, for such a band of several decades: every solver takes them, and the result plot's axis is then
  logarithmic, in MHz or kHz below 1 GHz. On `demo_transition` and `rf_diffvia` from 0.05 to 1 GHz it is
  within 0.008 rms |ΔS| of gfdtd up to 200 MHz and 0.03 up to 1 GHz, in 33 s; with `rf_diffvia`'s return vias
  taken out it gives the common mode's −11.0 dB at 50 MHz (gfdtd −11.1 dB), which the line model does not see.
  See docs/usage.md#solvers and pcbqs's docs/sparams.md.
- **Questions and engines (API 2.6).** A study asks questions of a board and engines answer them
  (`kicad2ems.study`): the ports' S-parameters, an antenna's pattern, the traces' impedances, a rail's drop, its
  impedance, a converter's near field, a circuit, capacitance, the temperature. An engine is a solver plugin or
  an analysis plugin; an analysis says which questions it `provides` from one run, under which name a list shows
  it (`engine`), how its settings change when only some are asked (`for_questions`), and whether a folder is its
  result (`owns`, for two engines that write a file of the same name). `api.frontend.study_tree()` is what a
  front end lists: each question by group with the engines installed that answer it, or how to get one;
  `runs_for` the runs a set of questions takes; `mark_run` the mark a run's folder bears. `kicad2ems --check`
  lists the questions with their engines. pcbqs's rail tool answers the drop and the impedance; kicad2ems-fem
  (the new `fem` extra) adds Elmer for the drop and the temperature and Elmer and Palace for capacitance, so
  those have two engines each. Nothing in a front end changes yet: docs/api.md#study-api.
- **Analysis plugins (API 2.5).** Tools that work on a run's board but give something other than S-parameters
  register as `kicad2ems.analyses` entry points, as solvers do as `kicad2ems.solvers`: an `Analysis`
  (`kicad2ems.api.analysis`) declares its options, the command that runs it, the file a finished run leaves, and
  what a result holds to read (a report of text and tables) and to plot (curve sets). pcbqs's power integrity, near
  field, circuit and thermal analyses are the first; `kicad2ems --check` lists them, and a config keeps a study's
  under `[analysis]` (`run = [...]`, a table per analysis), which kicad2ems itself leaves alone
  (`api.frontend.read_analyses`). The GUI builds its Study from the registry and has no page per analysis.
- **The line model solves the sections its formulas get wrong, by default.** `--lines-field` is now `auto`
  (default), `all` (the option alone, as before; `lines_field = true`) or `off` (`false`). With `auto`, a single
  section is solved by the field solver on the board's cut across it when the cut holds what its formula leaves
  out: a reference plane that ends near the trace or under it, copper on a layer in between, ground beside a
  stripline, a grounded CPW whose gaps are not the gap classified (`reference._beyond_formula`; the section's
  notes say which). On the CM5 Minima's 1,230 mm of single sections, 324 mm were more than 3 % from their cuts
  and 43 mm more than 10 %; now 0.7 mm and none, with 361 of 2,309 sections solved. The examples' models are
  unchanged but for the demo transition's two sections at its via's antipads. `build(field=)` takes the three
  values too (docs/transmission-lines.md#lines-field).
- **The line model's cross-sections are solved together, in worker processes.** A build classifies every section,
  then solves the distinct cross-sections (cuts and grounded CPW) in one go: in worker processes, one per fast core,
  when there are 48 or more (`reference.solve_deferred`, `reference.PARALLEL`), one after another otherwise or
  where processes cannot start. The workers are commands (`python -m kicad2ems.lines.cutworker`), not
  multiprocessing's: a program that builds a line model without guarding its main module is not run again. The CM5 Minima's 94 nets on eight cores: 20 s by default (46 s one after another;
  17 s before the cuts above), 23 s with `--lines-field all` (was 74 s). The numbers are the same.
- **`--fill-voids off | far | all`: the ground copper's voids left by the nets not selected, filled.** A pour
  imported with a few nets selected keeps the clearance of every other net (the CM5 Minima with one net and GND:
  903 holes), so no plane is solid and mom25d meshes them all. `far` fills the voids away from the signal copper
  of any layer and the small ones, `all` every one; a void that holds a selected net's copper or via is never
  filled, and a clearance that crosses a region's edge counts as one. Every solver gets the same copper
  (`Board(fill_voids=)`, `filled` in `<name>_board.json`, a note per run). On a CM5 Minima crop mom25d goes from
  7,938 to 4,651 unknowns (its planes become PEC) and gfdtd, which meshes the voids, moves by 0.1 dB in |S21| with
  `far`; `all` moved a plane resonance there, so `far` is the one to use (docs/model.md#fill-voids). Off by default.
  What it was built on (2026-10-28, mom25d's mesh at 0.3 mm, boards not in the repository). A 4-layer synthesizer
  carrier, 55 × 50 mm, a pair and GND kept (each layer 86–97 % pour, 17–59 voids a layer, 761 vias):

  | | unknowns |
  |---|---|
  | as imported | 157,459 (a dense matrix: ~400 GB) |
  | lone pads dropped | 147,370 |
  | the 154 voids with no selected copper or via in them filled | 142,070 |
  | filled + planes PEC + pads dropped (In2.Cu and B.Cu solid) | 44,071 (all on F.Cu's pour) |
  | as imported + PEC + pads dropped + `--mom-zone on` | 30,948 |
  | filled + PEC + pads dropped + zone (+ quads) | 8,952 (8,534) |
  | filled + PEC + pads dropped, F.Cu's pour cut 3 mm / 1.5 mm from the signal copper, quads | 6,572 / 4,489 |

  A 6-layer CM5 carrier's PCIe RX pair (0.14 mm traces, ~55 mm on F.Cu, two vias through four planes to B.Cu; a
  30 × 29 mm region, the whole stack meshed since a port is on B.Cu), where filling is not enough:

  | | unknowns |
  |---|---|
  | as imported | 167,849 |
  | lone pads dropped | 152,331 |
  | voids filled (far from the signal) | 119,644 |
  | filled + zone (+ quads) | 54,885 (55,168): each inner plane still ~7.5k, F.Cu 15k |
  | filled, GND copper cut 1.5 mm from the pair on every layer, zone | 58,645: cutting does not help |
  | F.Cu alone over In1.Cu, filled, zone + quads | 10,593 |
  | the same with no F.Cu pour at all: the pair alone | 7,112 |
- **A stack-up impedance calculator.** `kicad2ems-z0` (`python -m kicad2ems.lines.calc`, `api.frontend.impedance`):
  the 2D field solver without a board. A line's width, a pair's gap, the copper and any stack of dielectrics (typed,
  or a board's own around a copper layer: `--board`, `--layer`) give Z0, Zdiff, Zcommon, εeff, the delay and the
  dielectric's loss, with the closed form beside it where one applies; `--target` finds the width or gap of an
  impedance by a root search around the solver (0.1 %, about a second; not snapped to the line model's 1 % steps);
  `--plot` draws the field. See docs/transmission-lines.md#calculator.
- **Examples in groups, and from plugins.** `api.frontend.example_index()` gives every example with its group and
  title (Antennas, RF structures, Lumped-element demos, with subgroups); an installed analysis ships its own, which
  `examples()` and `prepare_example()` now cover (pcbqs's circuits, each a config beside its model and board).
- **Dumps of steady quantities.** A dump at 0 Hz alone is labelled static, and one whose `dump_info.json` entry says
  `"range": "data"` (a temperature) is coloured over its own values instead of from zero
  (`DumpSource.static`, `.spans`; `frames.levels(span=True)`).

### Decided
- mom25d's near-field reach stays 5 cells (`near_fac`); 3 was a candidate default for half the near field's cost.
  Checked on eleven examples, CPU, whole sweeps (2026-11-12): eight move by 0.5e-3 to 7.5e-3 in |S| at most, but
  rf_interdig by 0.038 and rf_diffvia by 0.18. Against a reach of 8, rf_diffvia at 1.0 GHz is 0.146 off with 3,
  0.047 with 4 and 0.060 with 5; rf_interdig at its band edge 0.024, 0.012 and 0.040. So 3 is no worse on the
  filter and 2.4 times worse on the pair's vias, for about 5 % of these boards' time. What made those sweeps move
  so much was not the reach: the pair's modes fitted their gamma on the feed (Fixed, below), and two sweeps
  sample different frequencies. Solved at fixed frequencies with that fixed, against a reach of 12: rf_diffvia
  at 1 GHz 0.0085 with 3 and 0.0046 with 5; rf_interdig's band edge 0.013 and 0.0024. 5 stays.
- mom25d's GMRES products on the GPU (the roadmap's "Z·V on the GPU in the reuse path"): not done. It was
  written when a reused solve made its products matrix-free on the host; they are dense double products now, and
  the card is no faster at those: at the via transition's 13.9k unknowns (its planes meshed) a complex128 Z·V
  takes 94 ms on the RX 7800 XT through ROCm, whatever the number of columns, and 46 to 114 ms on the host
  (1 to 8 columns). A double-precision LU there takes 13.7 s against 1.3 s in single, so that is no way round
  the low-frequency solves either. Where that sweep's 70 s go (15 solves): 25 s filling Z on the host (the far
  field 1.0 s, the near field 0.3 s and the vias 0.3 s a frequency), 8.5 s in six LUs on the card, 28 s refining
  (7 s of it the one solve at 1 GHz whose factor has to be solved on the host), the rest ports and tables.

### Fixed
- **The stack review of 2026-11-14, Fix first** (kicad2ems-project's REVIEW-2026-11-14.md, its R numbers):
  - **The window's Run and Preview** (R-A1). The Start page (2026-11-13) took the name of `MainWindow.start`, so
    neither started kicad2ems's run and the window stayed busy until closed. A start that raises says why on its
    row, its traceback in the log, and the queue goes on; a test runs the window's own run against a stand-in.
  - **The line model's ground vias** (R-A4). A ground via inside a trace's copper shorts it there (a row across a
    stub's end in parallel, their mutual inductance left out); only the line's own vias were taken, so a shorted
    stub was open. dfsynth's stub band-pass was a band-stop (-80 dB at 3 GHz on qucsator) and is a band-pass;
    its interdigital peaks at 4.906 GHz for its own model's 4.891 (-47 dB before).
  - **End gaps** (R-A11) join their ports: the gap's two sides were taken as unconnected (S21 NaN although qucsator
    had its MGAP), and scikit-rf has a gap too (Garg and Bahl's capacitances). dfsynth's end-coupled filter passes
    at 9.96 GHz for 10.0.
  - **Crosstalk not computed reads as not judged** (R-A8): a pair the line model puts in no coupled section had
    its S terms written as 0, read back as 0 % and "limit met"; it is "not modelled" and the limit not judged.
    The pairs the board proposes leave out nets with a zone (R-A9: looked for as dicts among tuples, none was).
  - **Blind and micro vias** (R-A7) span every copper layer between their ends (their rings at their ends): only
    through vias were spread, so `--fill-voids` filled a blind via's antipad on the layers between.
  - **KiCad import** (R-A22): KiCad 5's zones (their fills have no layer: the zone's), a pad's drill offset, NPTH
    pads (a hole, not copper of no net on every layer), a plated slot (a via of its perimeter, not of its long
    side), and a zone never filled (left out as before, now said in the board's notes).
  - **mom25d's log sweep** (R-A6): its adaptive sweep seeded and checked a log sweep on a linear grid and the
    result was interpolated linearly onto the log points (|dS| up to 1.8 on a 100 kHz pole over 10 Hz to 1 GHz, one
    solve below 1 MHz). It takes the frequencies asked for: first samples and gaps in log f, the model given there;
    a sweep that stops at its solve cap says so. `OPENBLAS_NUM_THREADS` no longer switches off its OpenMP cap
    (R-B1: 24 threads on the i7-13700K, `MOM25D_OMP_THREADS` ignored).
  - **Stop** (R-A5, R-A19). A page's tool was killed at once, so what it had started ran on (pcbqs's ngspice runs,
    kicad2ems-fem's Elmer and Palace, 8 ranks for half an hour) and a table's next row started. The tool is asked
    to end (SIGTERM, killed 5 s later if not), the queue is cleared; kicad2ems-fem's solvers run in process groups
    of their own, stopped with the tool and on the run's STOP file. Closing the window asks before it stops
    kicad2ems's own run (the docs had said a run was never lost), and waits for the solve threads.
  - **Python 3.10 and 3.11** (R-C3): the line model's solver and emsdev's build used 3.12's f-strings and did not
    compile there, while every package says `>=3.10`. The stack's CI compiles every package on 3.10.
  - **The stack's CI** (R-C1, C2, C4) installs ngspice (pcbqs's 84 circuit tests had skipped), kicad2ems-fem (not
    installed at all: its meshes, configs, readers and registry; Elmer's and Palace's runs skip there) and builds
    pcbqs's compiled core (its kernels had run as their NumPy twins only).
  - **An unstable FDTD run** (R-A12, B17): gfdtd took only an infinite energy as unstable and kicad2ems-fdtd never
    read why a run ended, so a run that blew up read as "port not excited" and one that grew within bounds as a
    result. Energy over 10 times its driven peak once the excitation ends is unstable, and either engine's run that
    is (or whose port voltages are not finite) says so. A lumped inductance too small for its edge and the time step
    (dt²/(LC) of 2 or more: 0.5 mm cells, 1.89 held and 2.52 diverged) and a series R and C without L are refused.
  - **`--converge` on gfdtd** (R-E3, its first part): the mesh convergence check is the FDTD layer's, not the openEMS
    subclass's, so gfdtd runs it (it said "not supported yet - ignored", and auto gave such runs to openEMS).
  - **kicad2ems-fem's coils** (R-A13) propose each coil for the circuit's inductor of its name and each pair for the
    coupling of those two (pcbqs's `couplings.A+B.k`), not wpthb's PRIMARY, SECONDARY and 130 nF; Elmer's thermal
    takes a coupled winding's own loss, as pcbqs's does.
  - **The PDN** (pcbqs, R-A14, A15, B10, B11): a mount's resistance; a load on a domain no source reaches; a rail
    with no plane pair keeps its DC drop; a failed pad-to-via solve said. **Two-face copper** (R-A16):
    `conductor_faces=1` is the one-sheet slab again, and `MQS.share_kernels` takes every kernel.
  - **Touchstone** (R-A20): the reader (`--compare`, the report, the window) took any file as S, ignored its `R`,
    read the port count from the extension and failed on a noise block. Y, Z, H and G are converted to S at the
    file's reference, a v1 noise block ends the data, v2's [Number of Ports], [Reference] and a lower or upper
    [Matrix Format] are read, and `with_zref=True` gives the references. pcbqs writes v2.0 with [Reference] where
    its ports' references differ (one in a v1 header had been right for the first port only).
  - **`install.py --update`** (R-A21) installs a stack package at the commit this checkout pins: it had re-installed
    the revision recorded at the last install, keeping the stack at its old commits.
  - **The mixed solver** (mom25d, R-B2) models the board with mom25d's options (`--mom-planes`, stitching pads,
    `--diel`, coarsen, quad tolerance): it had taken mom25d's defaults, so `--solvers mom,mixed` compared two models.
  - **A Gerber import says its lumped parts are left out** (the placement file's parts have no pads; it had said
    nothing, and the docs called it the same board).
  - **The plugins' API versions** (R-C15): pcbqs and kicad2ems-fem declare 2.11 (they use `several` and `pick`),
    kicad2ems-fdtd and mom25d 2.9 (`basic`); kicad2ems-fem has its LICENSE. **Docs** (R-G): the window's README,
    `api.md`'s version, the README's results, `testing.md`, the Gerber import, mom.md's mixed, HODLR and GPUs,
    pcbqs's circuit keys, kicad2ems-fem's five engines, gfdtd's backends, the superproject's table.
  - **Errors that said nothing** (R-B3, B4, B15, B16, and pcbqs's B8, B9, B12):
    - a solver's set-up or outputs that raises is a warning as its solve's is: the others' results, run.json and the
      report are written (an exception there had ended the run with nothing written);
    - a table kicad2ems does not read is said (a misspelt `[solver.openems]` had been ignored), `sigma` at or below 0
      and over 100,000 `points` are refused, a study question's unknown key is an error (`engines` for `engine` had
      run the default engine), and the config's notes are in the run's log (only the command line had printed them:
      the window and a study's runs never saw them);
    - a port's reference is the nearest ground copper under it, a nearer signal trace passed over (it had been taken
      without a word), and said when it is not ground;
    - a placement file's reference P1, P2 alone is a plug, not a port (every connector of a full .pos had been one).
  - **The window** (R-B5, B6, B7): a field that is not a number is marked and listed as still needed, and Run says
    why (it had raised from a timer: the list and Run froze); a Model JSON edit is a change (a save prompt) and an
    undo step (the schematic's history had been wiped by each); another board chosen lists its own runs, and the last
    board's run leaves the pages.
  - **mom25d** (R-B13): an unconverged ACA or HODLR solve is an error, as the block low-rank solve's (it had been
    returned with a warning); its input checks are ValueErrors (asserts: gone under `python -O`). Its fast cores are
    emsdev's (R-B20).
  - **gfdtd** (R-B18): openEMS's `TimeStepFactor` and `TimeStep` are honoured, a cylindrical grid refused, field
    probes and `CellConstantMaterial` noted (all dropped silently before).
  - **emsdev** (R-B19, B20): an Intel hybrid's P-cores from the kernel's list, else the clocks within 0.85 of the
    top (0.95 had split an i9-13900K's P-cores and an X3D's dies), for every solver; `auto` does not take a lone
    integrated GPU (its memory is the system's).
  - **kicad2ems-fem** (R-B14): Palace's unconverged solves are warnings and kept with its changeset; its MPI ranks
    one per fast core (8 had been fixed).
  - **The line model's losses** (R-A17): `--sigma` reaches every section (a stripline, a field-solved or a coupled
    one had been lossless whatever it said): microstrip's by its formula, the others' as 2 Rs / w per metre, the
    strip's and its return's. Dielectric loss is the mode's own tan δ: the field solver's where it solved a section,
    the filling factor elsewhere, a stack's layers weighted as the field crosses them (the largest had been taken:
    15 % high on a plain microstrip). A stripline between dielectrics of different er is solved on its cut (its
    formula on the averaged er had Z0 4 % low and the delay 5.6 % long).
  - **Coupled lines under solder mask** (R-A10): embedded microstrip forms coupled sections, its field solve with
    the mask over the copper (with `--mask` a hairpin had no coupled section and no path between its ports).
- **What a review of the full-bridge removal found (pcbqs, kicad2ems-gui, kicad2ems-fem).** pcbqs: thermal takes the
  bridge's per-pin and null `board.parts`; a boost's rail stays its source's node; the bridge's `bus_ripple_V` is
  the settled ripple again (a measure's `samples`); `<ref>.cap_V` for every capacitor. The window: a study whose
  circuit model will not load (the old tool's format) keeps naming it, and saving writes nothing over it; a
  proposal with no model of its own (kicad2ems-fem's coils) sets the one on the page. kicad2ems-fem's coils say
  which receiver capacitor would be resonant with the new secondary, since the bridge's is not retuned.
- **mom25d's block low-rank solver gave wrong S-parameters at low frequency.** A large board (20,000 unknowns
  up, where the GPU's hybrid does not hold the matrix) at a frequency below (k h)² = 10⁻⁵ went through a
  single-precision low-rank factor of the RWG matrix, which cannot hold the loops' currents at any compression:
  antenna_vivaldi (21.9k) at 50 MHz made the factor three times, GMRES stayed at a residual of 0.95, and the run
  wrote S11 = 1.000 with a warning in the log (it is 0.978 − 0.193j). Such a frequency now goes to the loop/tree
  basis's dense solve where that fits (29 s), else to a low-rank factor in double precision compressed to a
  hundredth of (k h)² (`BLRFactor128`: the same S11 in 50 s, nearly dense), and a block low-rank solve whose
  GMRES ends above a residual of 0.01 raises an error instead of returning its currents.
- **mom25d read crosstalk 20 dB low through wave ports.** On `rf_crosstalk` (two lines 3 mm apart, ports at their
  ends) `--mom-port auto` took de-embedded wave ports, whose fit of one line's two waves takes out what the
  neighbour couples in along the feed: NEXT −52 dB at 1 GHz and FEXT −34 dB at 6 GHz, against −31.7 and −17.5 dB
  with lumped ports (the line model −31.0 and −17.3 dB; Palace 0.19 −31.0 and −17.2 dB). An automatic port is
  now lumped where other signal copper runs beside its feed within 8 heights, with a note; `--mom-port wave`
  keeps the wave port. Found by running the example through Palace 0.19.
- **Palace's Spack build finished with a launcher that found neither its binary nor its MPI.**
  `tools/build_palace.sh` linked `bin/palace` to the view's launcher, which looks for `palace-x86_64.bin` beside
  itself and for `mpirun` on the PATH; it is now a wrapper that puts the view's `bin` first. The build itself was
  run for the first time: Palace 0.19.0, 72 packages, about an hour at 8 jobs, 2.3 GB, and kicad2ems-fem's plugin
  solves `rf_crosstalk` with it in 6 minutes (the figures above).
- **The window's tests no longer crash the process after passing (kicad2ems-gui).** CI's stack jobs were red on
  Linux (exit 139) and Windows (127) with every test passed: PySide 6.12 tearing down the QApplication at exit
  walked every widget the tests left (110k) and crashed in a pyqtgraph scene's items, whose C++ destructors call
  their Python overrides after the wrappers are gone (gdb). Reproduced here on Qt 6.12, two runs in three; the
  tests' session now ends with pytest's own status once it has reported. On the way: a removed plot of the
  Results grid is hidden and deleted with the event loop, not made a window of its own.
- **The example boards are made in a checkout again, and every CI workflow passes.** `examples/make_boards.py`
  stopped at its first line in a checkout since the analyses' examples were added (a relative import that only
  works installed), so `install.py` made no example board and its CI's demo run found none. The stack's CI
  installs and tests pcbqs, which the window's tests use; a dock test and the OpenGL start test hold on Qt 6.12
  and on a machine without a GL driver; the study tests write Windows paths as TOML literal strings.
- **The documentation site builds again.** It had failed on every push since the morning of 2026-11-12 (82
  warnings, the build run with `-W`): the API modules re-export (`kicad2ems.api.analysis`: `Analysis`, `Question`,
  `Dump` ...), and viewcode, following those names to their source, filed whole modules' source pages under the
  re-exporting name, so every link back from `_modules/kicad2ems/study.html`, `fields/frames.html`,
  `fields/dumps.html` and `analyses.html` missed (`viewcode_follow_imported_members = False`); and the pages'
  `#study` anchors had become ambiguous once the window grew a module of that name (`kicad2ems_gui.study`):
  `docs/api.md#study-api`, `docs/gui.md#the-study`.
- **mom25d spent 30 iterations on a GPU factor that could not be solved with.** ROCm's complex64 triangular
  solves lose an ill-conditioned factor (the lowest frequencies of a fine mesh), and the refinement then stays at
  a residual of 1 until its cap before the factor is taken to the host. It is given up after 4 iterations above
  0.1 (`fast.FRESH_STALL`): 0.6 s for 3.8 s at 1 GHz on the via transition with its planes meshed (13.9k
  unknowns).
- **mom25d fitted a differential pair's propagation constants on its feed.** A single wave port takes its
  gamma from the line's characterisation; a pair's two modes did not, though the characterisation has them, and
  fitted on a feed that spans a quarter of a radian they were unreliable. On rf_diffvia at 1 GHz, the low end
  of its band: |Sdd11| 0.19 where it is 0.01, |Sdd21| 1.21 one way and 0.79 the other (largest singular value
  1.30), S moving by 0.06 with the near field's reach, and the adaptive sweep chasing it for 54 solves. The
  modes now use the characterised constants: 27 solves, 0.993, and the reach moves S by 0.005 there. This was
  what the roadmap had as the near field's reach not being converged: with lumped ports, or away from the low
  end, 5 cells against 12 move |S| by 0.0025 at most (rf_diffvia, rf_interdig).
- **mom25d's matrix was not symmetric where cells of two sizes are close.** The near-singular part of a pair
  of triangles is integrated analytically over one and by a 6-point rule over the other; the rule ran over the
  observation triangle, so a pair had two values, and with the rule over a 0.9 mm plane cell above a 0.3 mm
  trace cell the worse one was 1.3 % off. Transmission through a via differed by 0.4 % between its two
  directions with lumped ports. The rule now runs over the smaller triangle whichever is the observation
  (`sing.rule_over`, the compiled core's too): reciprocal to 1e-6, and the multi-layer examples moved onto
  openEMS's figures (rf_stripline's worst S11 −19.9 to −17.3 dB, openEMS −17.5; rf_diffvia's worst Sdd11 −14.7
  to −15.4 dB, openEMS −15.4); the single-layer ones did not move. The core's `offplane_pairs` could not be
  called from Python (it built its result without the interpreter's lock).
- **Transmission between mom25d's wave ports depended on the direction.** A wave port's waves carry the error
  of its characterised impedance, 1–2 %: |Sdd21| of rf_diffvia's pair, on a microstrip at one end and a
  stripline at the other, was 0.962 one way and 0.985 the other at every frequency. Reflections and the
  product of the two directions do not depend on those scales, so they are fitted and taken out
  (`sim.balance`; lumped ports are held exact, ports coupled by less than −20 dB keep theirs, and a run says
  when they were more than 2 % apart).
- **mom25d kept a differential pair's wave ports below the band their feed supports.** With `--mom-port auto` a
  single port falls back to a lumped one when its feed's cuts span under 0.1 rad at the lowest frequency; a pair
  did not, and on rf_diffvia's 10 mm feeds from 0.05 to 1 GHz every term was wrong (|Sdd21| −48 dB at 50 MHz,
  0.59 rms |ΔS| from gfdtd). A pair is now refused the same way and its legs are solved as lumped ports, the
  modes taken from them: 0.004 to 0.009 from gfdtd. Found by the check of the new low-frequency solver.
- **`palace` was not known as the fem extra's solver**: named without kicad2ems-fem installed, it was "no
  solver 'palace'" instead of how to install it.
- **A run of four or more solvers ended at its result plot.** The third solver drawn over the reference has a
  dash pattern for its line, a tuple that was passed as matplotlib's format: the Touchstone files were written,
  the plot, the summary and the report were not. Seen once pcbqs made a fourth.
- **The qs, gui and filter extras installed stale packages.** They pin pcbqs and kicad2ems-gui by revision, and
  the pins had stayed at 7 November while both moved on (pcbqs by 68 commits: no circuit engine, no thermal). They
  name the current revisions again; the kicad2ems-project checkout's `sync_pins.py` checks them against its
  submodules and rewrites them.

### Changed
- **Independent PCB quasistatics package.** PDN/DC, MQS/PEEC and operational
  circuits move to `pcbqs`, with privately maintained numerical copies and its
  own optional native backend. MoM shared numerics/native sources and emsdev
  are unchanged. Install `kicad2ems[qs]`; GUI pages use the new namespace.
  Old QS/PDN commands are removed without compatibility shims. This release's
  GUI, MoM and QS extras pin the coordinated source revisions.

### Added
- **Operational switching circuits (companion pcbqs / GUI).** A new Circuit page and
  `python -m pcbqs.power_circuit` support ngspice transient simulation, selectable switch/NMOS/VDMOS/vendor
  subcircuits, per-part ESR/ESL/DCR and coil loss/SRF models, the wpthb input filter, and an explicitly
  matched coupled receiver. A passive broadband copper-terminal equivalent feeds the circuit, with
  reported fit errors; periodic terminal-current spectra drive actual MQS copper fields and time-domain
  Viewer frames. Power targeting, cycle repeatability and adaptive-grid terminal power checks are recorded.
  Receiver hardware and unmeasured parasitics remain assumptions; external-coil fields and full startup
  field inference are not included. See mom25d `docs/power-circuit.md`.
- **Quasi-static mesh preview and layout context.** The companion MQS command's `--preview`
  exports the actual copper-layer mesh without filling or solving. The GUI offers Preview mesh,
  a layer-selectable layout/mesh view, field overlays with opacity and component controls, and
  display-only package silhouettes in 2D/3D. Simulated copper and selected parts are highlighted;
  PNG/animation exports retain the overlay settings. Board/rail context can be handed to Power
  Integrity for separately specified DC source/voltage/loads. A validation guide covers independent
  static references and convergence where low-frequency full-wave runs are impractical.
- **Local quasi-static workflow (2026-11-07, companion mom25d and GUI checkouts).**
  `python -m pcbqs.mqs` reads KiCad or board JSON, describes its options as JSON, discovers
  buck input-loop candidates and supports explicit converter/net/capacitor selection. Operating
  spectrum, mesh, crop, backend and scan controls produce R/L, dipole/peak-H summaries, figures
  and standard plane/volume H dumps. The GUI Near field page runs the command and opens the
  results in the Viewer. This is an input-loop magnetic model with explicit waveform/parasitic
  assumptions; it does not model switch-node E or inductor leakage.
- **Local GPU static-fill optimization.** A fused CUDA/HIP pair kernel replaces CuPy operation
  chains. On RX 7800 XT, fine CM5 In3.Cu filling drops from 3.06 to 1.55 s; eight-worker OpenMP
  remains faster at 0.75 s. Device tests cover all three quadrature levels and scalar/magnetic
  fills, plus MQS, PEEC and electrostatic solver regressions.
- **Surface finish (`--finish`).** ENIG, ENEPIG, HASL (lead-free and SnPb), immersion silver and tin, or none / OSP;
  `auto` (the default) reads the KiCad stackup's copper finish. It covers the outer copper's outer face and sides where
  no mask is (left out, with a note, under `--mask`). Each finish layer loads the copper face as a line section; a
  line's share of the loss on those faces comes from the 2D field solver (45 % of the strip's on hairpin's line).
  mom25d weights its surface impedance per layer at each frequency, gfdtd fits it in its sheets (engine API 1.3), and
  with `--thickness on` the outer face is a sheet of its own; openEMS and the line model take the conductivity of the
  same loss at the band centre. ENIG cost hairpin 2.5–3.4 dB at its peak. API 2.4: `resolve_finish` and the finish
  helpers, the `finish` row. `--sigma` has a GUI field (Copper conductivity), so roughness and finish are usable there.
- **`--nz-mask`**: the solder mask's own number of z cells (default: `--nz`); `--mask` is in the GUI.
- **mom25d: the wideband dielectric.** `--diel wideband` (`diel = "wideband"`, as twelve examples set) now applies to
  mom25d too: the Djordjevic-Sarkar εr and tan δ, evaluated exactly at each frequency it solves, at no extra cost.
  Before, it kept the stackup's constant values, so comparisons with the FDTD solvers on these boards were mostly a
  dielectric difference. rms |ΔS| against gfdtd, constant to wideband: rf_bends 0.107 to 0.035, rf_spiral 0.129 to
  0.043, rf_ms2cpw 0.136 to 0.078, rf_meander 0.180 to 0.124, rf_dgs 0.121 to 0.088, rf_msvia 0.063 to 0.024
  (rf_ebg 0.295 to 0.319, rf_slotline unchanged). mixed and the line model keep the constant values.
- **mom25d: `--mom-stack auto`, the new default.** The whole stack where the ground layer has apertures under the
  signal copper (a slot, a defected or partial ground), which the infinite ground plane would fill in, or where a port
  is below the ground (rf_msvia, which failed before), else the ground stack as before. The log says which, and why. `--mom-stack ground` on such a board warns and lists the
  apertures as deviations. `mixed` declines them. Against gfdtd: rf_dgs rms |ΔS| 0.74 to 0.12, rf_slotline 0.93 to
  0.14, antenna_slot 0.95 to 0.06; the other 44 examples are unchanged.
- **mom25d: rings along narrow gaps.** Copper edges facing other copper across a gap of under about a quarter
  of the narrowest trace's width get edge rings at 1/4, 1/8 and 1/16 of the gap. rf_coupler's lossless S31 −10.64 to
  −10.35 dB (refined FDTD −10.24 to −10.29), +22 % unknowns; boards without such gaps mesh as before.
- **Conductor loss on gfdtd.** gfdtd 261104 models conducting sheets as openEMS does (its engine API 1.1), so
  `--sigma` is conductor loss on it too and `--solvers auto` no longer gives `--sigma` runs to openEMS
  (kicad2ems-fdtd decides from the installed gfdtd). Against openEMS on a 40 mm copper microstrip, |ΔS21| ≤ 0.009
  over 0.5–20 GHz.
- **`--thickness on`** (openEMS and gfdtd; docs/meshing.md#thickness): copper with its stackup thickness, the stack
  moved up to make room for it; with `--sigma` its faces carry the loss. rf_coupler's coupling rises 0.50 dB on
  both engines (the field solver: 0.65 dB).
- **`--rough`** (µm RMS; docs/simulation.md#roughness): copper roughness as Hammerstad's factor of the conductor
  loss, at each frequency in mom25d, over the band in gfdtd (a wideband fit of its sheets), as the rough copper's
  conductivity at the band centre in openEMS and the line model. The capabilities table's `roughness` row; API 2.3
  (`hammerstad`, `rough_sigma`, `band_sigma`).
- **Windows.** The stack installs and runs on Windows (docs/getting-started.md): `install.py`, the installer, in
  Python (standard library only) for Linux, macOS and Windows, with the options setup.sh had; `setup.sh` (and
  `setup.cmd` on Windows) runs it. On Windows: venvs with `Scripts\`, `.cmd` commands on the user's or the
  machine's PATH, a Start-menu shortcut that runs the GUI without a console, the CUDA CuPy wheels (CuPy has no ROCm
  wheel for Windows), openEMS's unpacked Windows release (its wheels installed, its DLL folder recorded as
  `openems_dir`), qucsator found in Qucs-S's install folder, and the compiled cores built with MSVC or MinGW-w64
  (emsdev). The user's `site.toml` is in `%APPDATA%\kicad2ems` there.
- **The mesh a run solved on**, in the GUI's 3D and Plan views: a run of mom25d writes it as
  `<name>_mesh_mom25d-run.npz` beside the preview's mesh, with the diagonals it merged into quad cells
  (`hidden`, left out when drawn); the views' *solved mesh* tick box draws it in pink over the preview's.
- `tools/test_all.py`, every suite on any system (`tools/test_all.sh` runs it).
- CI on Windows (`.github/workflows/windows.yml`): the stack's suites with MSVC-built cores, and the installer end
  to end on Windows and Linux.
- The line model's 2D field solver, `kicad2ems.lines.xsection` (docs/transmission-lines.md#field-solver): the
  capacitance matrices, Z0, εeff, even / odd modes and dielectric loss of any cross-section of rectangular
  conductors over planes in layered dielectrics (solder mask, coplanar ground beside striplines, copper
  thickness on coupled lines), within 0.5 % of the exact and closed-form results.
- The GUI's **Section** view (docs/gui.md#section): the field across any line-model section, solved when it is
  picked: strength or potential, equipotentials, arrows, the copper, the dielectrics, the mesh, a pair's odd and even
  modes, and its impedance beside the line model's. `kicad2ems.api.frontend.sections`, `xsection.field`,
  `CrossSection.to_dict`; each section of `<name>_lines.json` says where its Z0 came from (`source`, `z0_formula`,
  in the viewer's list and in its description) and holds its cross-sections (`section`). API 2.2.
- `build_lines(..., field=True)`, the KiCad plugin's *Every section by the field solver*.
- `tools/lines_crop_check.py`: a cropped trace's total C and L, openEMS against the line model.
- `tools/xsection_figures.py`: the field solver at work (a pair's mesh and fields, the cases the formulas leave
  out, convergence, the cache's bins, a five-line section's coupling, a cut across a real board).
  `tools/xsection_study.py`: its accuracy and time, on references and on every cross-section of a board.
  `tools/lines_vs_openems.py`: the line model against openEMS, by scikit-rf and qucsator, in each mode.
  `tools/cm5_line_model.py`: build times and the sections that move most on a real board.
  `tools/xsection_fullwave.py`: copper thickness on a pair by 3D FDTD, against the field solver.
- `xsection.potential`: the potential on the solver's grid, to draw the field.
- `--lines-field`: the line model on the field solver (docs/transmission-lines.md#lines-field): every section's
  cross-section from the board's copper cut across it (plane edges beside a trace, partial references, ground on
  one side, coplanar ground beside striplines). The examples with openEMS references are unchanged; on the CM5
  Minima demo board's HDMI pairs, the single-ended Z0 moves by at most 2.7 %.
- `setup.sh --update`: only kicad2ems and its stack in an existing install, without asking; openEMS, qucsator, the GPU
  wheel and other dependencies stay as they are (docs/getting-started.md).
- `setup.sh` makes every generated example board (rf_*, antenna_*, demo_*: 49 boards, a few seconds) in
  `examples/` on each run, so `kicad2ems examples/<name>.toml` works straight after it; `python
  examples/make_boards.py [NAME ...]` makes them again (`kicad2ems.examples.make_boards`).
- mom25d: `--mom-planes pec|mesh` and `--mom-stitching-pads drop|keep` (both the first by default: below).
- mom25d: `solver='hodlr'`, a hierarchical direct solver (experimental, `mom25d/hodlr.py`): a binary geometric
  tree, each node's two off-diagonal blocks low rank (ACA with a check of the rows holding significant near-field
  entries, or the exact block by an adaptive randomized range finder), a Sherman–Morrison–Woodbury factorisation,
  optionally the preconditioner of GMRES on the exact operator. 3–4× less memory than the dense LU at the same
  accuracy, not faster below ~30k unknowns. Measured (CPU, 8 P-cores, one solve at 5 GHz):

  | | per solve | memory | \|ΔS\| from dense |
  |---|---|---|---|
  | hairpin, 4,763 unknowns: dense | 0.7 s | 0.35 GB | |
  | HODLR 1e-4 / 1e-6 | 1.1 s / 7.6 s | 51 / 112 MB | ranks at the top 32 / 212 |
  | demo_transition (meshed), 13,543: dense | 13.7 s | 2.8 GB | |
  | HODLR 1e-4 | 24.5 s | 0.77 GB | 2.2e-3 |
  | HODLR 1e-6 | 160 s | 1.9 GB | 7.7e-6 |
  | HODLR 1e-4 + GMRES (11 iterations) | 26.5 s | 0.77 GB | the exact operator's, to 1e-6 |

  The ranks are low and the memory falls, but it is about N^2.5, not N log N, for two reasons. On boards with
  coarse planes the near-field correction reaches 5 cells (15 mm under 3 mm plane cells: 15 % of the matrix), so
  most rows of a block hold near entries and every block is compressed from its exact entries. And admissibility
  is weak on a 2D board: the top blocks' ranks grow like √N (241 / 670 at 13.5k against 32 / 212 at 4.8k). A 55k
  board (the CM5 pair, 48 GB dense) was stopped for memory before it finished; the near-field CSR and its scaled
  copy are the suspects. What would make it N log N is in ROADMAP.md (mom25d: boards past 30k unknowns).

### Fixed
- `--thickness on` with `--rough` on gfdtd: the faces' roughness took the skin depth of the face sheets' σ / 4, twice
  the copper's; gfdtd 1.3 knows them as faces.
- A one-cell layer slightly thicker than the first graded z cell left a 0.25 µm sliver line (411 z lines instead of 25).
- The GUI page's `#fields` anchor, which also named Python objects (gfdtd's backends' `fields`) and failed the docs
  build: `#cut-view`.
- `tools/xsection_figures.py --board` followed the line model's old internals (`reference._field`'s arguments) and
  stopped with a TypeError; it reads the cut where the line model makes it now (`reference._cut_args`).
- mom25d's preview mesh is the one the run meshes: it ignored `--mom-quads` (the structured runs),
  `--mom-coarsen`, `--mom-zone` and the via options, so the views showed a different mesh from the run's.
- triangle (mom25d's mesher) built from source without fused multiply-adds (`-ffp-contract=off`, install.py and CI):
  where the compiler fused them, its exact arithmetic broke and meshing a board never finished (CI's Python 3.14
  stack job, out of time in mom25d's suite since 1 November).
- The docs' `#coupled` anchor, which since dfsynth's `Substrate.coupled` also named two Python objects and failed the
  docs build: `#coupled-sections`.
- The field solver's cache moved copper too far: bins grew without limit away from a strip, so an edge a few heights
  off snapped hundreds of mm (a third of the CM5 Minima's cuts), and on wide traces reversed a conductor, the section
  falling back to the formula (27 of them). No bin is wider than one reference height now (`reference.BIN_MAX`).
- `formulas.cpwg` on wide strips: past w / h = 24 tanh rounded to 1 and Z0 came out 1e-16 ohm (a 3 mm trace on 0.1 mm:
  now 5.5 ohm), the CM5 Minima's wide traces among them. Narrower strips are unchanged.

### Changed
- **Faster solvers: the examples' suite 1.40× faster on gfdtd and mom25d together** (64.0 to 45.6 min, every board
  both versions solve; i7-13700K, RX 7800 XT through Vulkan). gfdtd 1.50× (19.6 to 13.1 min: the antennas 1.69×, the
  circuits unchanged); mom25d 1.37× (44.4 to 32.5 min; antenna_slot 2.8×, antenna_cp_patch 2.2×, antenna_monopole
  2.0×, antenna_vivaldi 1.8×), and rf_siw, the one board it slowed, 383 s to 296 s with the hybrid limit below.
  Results unchanged: S within the adaptive sweep's own tolerance, the far fields to 1e-13.
  - gfdtd's far field (NF2FF): each face's normal axis as a phase and its two tangential axes in one matrix product,
    the exponentials once per distinct direction component: antenna_lpda's sphere 27 s to 3.7 s. Its operator's
    node codes sorted through one 64-bit key: 6.4 s to 0.4 s at 5.6 M cells.
  - mom25d's compiled core (the matrix fill) on the fast physical cores, as its BLAS already was: 1.5–3.1 s a
    frequency to 0.8 s on a hybrid CPU's 24 threads. `MOM25D_OMP_THREADS` sets it.
  - mom25d on the GPU: each backend's own memory estimate decides what fits (antenna_cp_patch's 40.8k unknowns
    factor on the GPU in 19 s, where the CPU took minutes); the GPU LU path keeps its factor for the next
    frequencies (antenna_slot 15 LUs to 1); the hybrid path up to 25,000 unknowns (`MOM25D_HYBRID_MAX_N`); the
    matrix sent up at 16 GB/s, not 6.5; a streamed path that fills the GPU's LU a block of rows at a time where RAM
    is short (5.8 GB for cp_patch, where the compressed fallback took over 30 minutes a frequency).
  - mom25d's antenna mode solves the far field's frequencies within the adaptive sweep, and every port's far field
    from one set of phases.
  - emsdev: the free memory is the process's own cgroup's (a container, or `systemd-run --scope -p MemoryMax=`), not
    only the root's: under a 4 GB scope it said 11.2 GB.
- The docs are shorter, with pictures where words were: a gallery of every example board (docs/examples.md), each
  solver's distance from openEMS against its time (docs/usage.md#which), the field solver's figures
  (docs/transmission-lines.md), the GUI's pages, and the mesh in docs/meshing.md (`tools/doc_figures.py` draws the
  first two). The study tables of mom25d's experimental mesh options, the Vivaldi variants and the benchmarks keep
  their findings; how a default or a figure used to be is left to this changelog. Fixed on the way: the module map's
  `fdtd/` (in kicad2ems-fdtd since 2.0), the test table's files that moved with it, `KICAD2EMS_FDTD_SRC`, gfdtd's
  plugin (kicad2ems-fdtd's, not gfdtd's) and the KiCad plugin's interface (`api.frontend` and `kicad2ems_gui.api`).
- The example generators share the board file around their items (`_pcb.Items.write_board`, `port_footprint`,
  `segment`): the antenna boards list the F.Mask layer and write the outline to 0.1 µm, the same boards otherwise.
- `tools/bench_mom_openems.py` is gone: `tools/bench_solvers.py` measures every solver, and its `--mesher` option was
  the dropped gmsh one's.
- Text files are read and written as UTF-8 everywhere (boards, configs, reports, Touchstone): Windows' default
  code page broke non-ASCII net names and labels.
- The line model's coupled sections come from the field solver (docs/transmission-lines.md#coupled-sections): every line
  coupled to every other (rf_interdig's |S21| against openEMS: 51.2 → 8.5 dB rms, its stop bands), and the pairs'
  even / odd impedances with the copper's thickness (`ze`, `zo`; `ze_sheet`, `zo_sheet` as sheets). The CM5
  Minima demo board's HDMI pairs: 117.8 → 104.8 Ω differential (100 Ω class); by 3D FDTD, thickness takes 7–14 % off
  `rf_diffvia`'s pairs against the solver's 8–16 %. `topology.FIELD_GROUPS = False` keeps the formulas. Building the
  line model of every net of a large board takes longer (the CM5 Minima's 94 nets: 6.4 → 13 s, with grounded CPW
  below). A board whose pairs were designed with zero-thickness formulas now reads low: `rf_diffvia`'s 100 Ω pair
  is 92 and 84 Ω, which is what a map against a net class's target shows.
- The field solver works on a mesh of rectangles (docs/transmission-lines.md#field-solver), mom25d's methods in 2D:
  cells growing from the copper's corners, hanging nodes reduced out. On all 2,200 cross-sections of the CM5 Minima's
  94 nets: 4x faster than the tensor grid it replaces, and closer to a converged solve (worst 0.54 → 0.31 %, median
  0.22 → 0.04 %). The grid stays as a check (`xsection.MESH = False`).
- Grounded CPW sections come from the field solver (`reference.FIELD_CPW`): the formula has no copper thickness
  (35 µm beside 0.15 mm gaps: 10–12 % lower Z0). Field-solved microstrip and CPW sections reach the circuit
  solvers as lines of their own Z0 where it is more than 2 % from the formula's (`Element.field`); scikit-rf's and
  qucsator's own models, with dispersion, otherwise. With `--lines-field`, the CM5 Minima's planes with via
  clearances now reach the S-parameters, not only the reported impedances.
- Similar cross-sections share a field solve (docs/transmission-lines.md#similar): mirror images are one, widths
  and gaps snap to 1 % steps, and a cut's copper to bins that widen away from the strip (each worth 0.1–0.2 % of
  Z0, none wider than one height). The CM5 Minima's HDMI nets with `--lines-field`: 254 → 158 solves, within 0.33 %
  of solving each.
- mom25d by default models solid planes (one piece over the board, no holes, ground nets) as PEC, the ground or a
  cover over the stack, not meshed, and leaves out lone stitching-via pads (a ground via's pad with nothing else
  on its layer; not a part's or a port's), the via kept (docs/mom.md#planes). Of the 52 examples 7 change, all
  checked against the fully meshed model and openEMS (`tools/mom_planes_study.py`): rf_stripline 15,563 → 1,337
  unknowns (|ΔS| 0.003), demo_transition 13,670 → 4,043 (0.009), rf_marchand 14,789 → 6,603 (0.085), rf_diffvia
  7,688 → 5,925 (0.030), the same agreement with openEMS; the benchmarks' transition 72 → 12 s and stripline
  85 → 3 s with the GPU. `--mom-planes mesh --mom-stitching-pads keep` meshes everything as before.
- mom25d keeps factorisations across a sweep and solves each new frequency by GMRES preconditioned with the
  nearest kept one (up to four complex64 LUs on the CPU, memory allowing; BLR's and HODLR's too), factoring
  anew only where that would cost more (docs/mom.md#reuse). demo_transition on the CPU: 176 s → 84 s.
- mom25d with a GPU: the GPU factors, and where its single-precision triangular solves lose the factor's
  accuracy (ROCm, at the bottom of a band) the factor is solved on the CPU instead of the frequency being solved
  again in double precision; the LUs are kept on the GPU for the next frequencies. demo_transition 107 s → 76 s.
- mom25d's compiled core keeps the near triangle pairs' analytic parts (geometry only) across frequencies: the
  near field into the dense matrix 1.10 s → 0.29 s a frequency on demo_transition (96 bytes a pair, only where
  that is under a tenth of the free memory).

### Fixed
- mom25d's lone stitching-via pads: a shunt part's ground pad (a pad with a via to the ground, a part landing on
  it) counted as lone, since only one corner of the part was checked, and was dropped, cutting the part off
  (demo_bandpass: |ΔS| 1.98).
- mom25d's `--mom-zone`: a shunt part's ground via, outside the zone, was squared like a stitching via and its
  pad no longer reached the part (demo_bandpass: |ΔS| 1.98; now 0.006). Such a via is meshed as a signal via.
- mom25d's batched GMRES (the mixed-precision refinement) used unconjugated Givens rotations, so on complex
  matrices it reported convergence early (a true residual of 1.5e-4 at a reported 1e-6) and the refinement was
  judged failed and redone in double precision.

## 2026-10-28 (261028-r1)

Released together: kicad2ems, kicad2ems-fdtd (first release), mom25d, gfdtd, kicad2ems-gui, kicad2ems-ipc, dfsynth
and emsdev, each 261028-r1 (the KiCad plugin keeps its own version for the plugin manager, 0.1.0).

### Added
- The APIs documented: `docs/api.md`, every interface between the packages (the Python APIs, the solver plugin
  contract, the files and the event stream with their format versions, gfdtd's engine, `kicad2ems_gui.api`,
  `dfsynth --describe`), checked by `tests/test_api.py`; every public API object has a docstring.
- A documentation site (Sphinx, for Read the Docs: `.readthedocs.yaml`): the pages, and a code reference of the
  whole stack from its docstrings, the compiled cores through Doxygen (`docs/maintenance.md#documentation-site`).
- mom25d's whole stack (`--mom-stack whole`): every copper layer meshed instead of an infinite ground, the lowest
  on the bottom interface with air below as well as above, so copper and ports on any layer (each port lumped to
  the nearest plane under or over it) and, in antenna mode, radiation over the full sphere from the board's own
  ground. `rf_msvia`, whose B.Cu port the ground stack cannot take, matches gfdtd's S21 to 0.05 dB up to 7 GHz.
- mom25d's far field includes the vias' vertical currents (a probe feed, a shorting via), on either stack.
- `--aim THETA,PHI`: points a phased array's beam at an angle, each driven port's phase from its place
  (−k₀(x sin θ cos φ + y sin θ sin φ) at the pattern frequency) on top of `--drive` and `--steer`: any layout of
  feeds, no grid. The array file keeps the ports' places (`pos`), and the GUI's Beam steering bar has *Aim at*
  θ, φ, with Animate sweeping θ or turning φ.
- A phased drive on openEMS and gfdtd (`--drive`): one run per driven port, the others terminated, each run's far
  field per unit incident wave and their sum for the drive (`fdtd.antenna.run_phased`, `combine_phased`), exact
  at every recorded frequency. The same antenna results and array file as mom25d's (`phased.py`'s incident-wave
  basis: S and the fields per unit wave), so the GUI steers it. Auto no longer leaves gfdtd out for `--drive`.
- The report's S-parameters as an interactive plot (inline SVG and script, nothing loaded from the network):
  every solver's Touchstone file, hover for the values, a box per solver and S, |S| or phase, drag to zoom.
- The GUI (kicad2ems-gui): difference plots between two solvers (Δ|S| and |ΔS| in dB, against a solver chosen
  on the plot), traces of differential pairs named by mode (Sdd21, Scd21, Sds31), and the run's warnings and
  notes in a fold on the Results page.
- mom25d's current dumps (`--mom-dump`) cost no solve of their own inside the band: the adaptive sweep solves at
  the dump frequencies among its first samples and keeps their currents.
- `tools/compare_solvers.py`: the solver comparison of docs/examples.md regenerated from runs on every RF
  example with an openEMS reference, its figures measured from the Touchstone files, and the openEMS ones
  checked against `examples/reference/` (exit 1 when one moved beyond `--tol`).
- `kicad2ems.api`: the public API, `kicad2ems.api.solver` for solver plugins and `kicad2ems.api.frontend` for
  programs that drive kicad2ems (the GUI, the KiCad plugin, dfsynth); everything else is private. Its names load
  on first use. `API_VERSION` is 1.0; a solver declares the version it was written for (`api = (1, 0)`) and the
  registry refuses one of another major version or a newer minor one (`--check` lists it). mom25d, gfdtd,
  kicad2ems-openems, kicad2ems-gui, kicad2ems-ipc and dfsynth import only from it. `parts.kind_value` (was
  `_kind_value`, kept as an alias) is public.
- `Solver.resources()`: the GPUs a solver would run on and why there are none. `sysinfo` asks each solver
  (`resources` in its JSON; `gpu` stays mom25d's GPU, else any solver's) and no longer imports mom25d.
- [emsdev](https://github.com/lowprocess/emsdev), a dependency: the GPUs (CuPy, PyTorch), the CPU's thread count
  (one per fast physical core), the memory available and the compiled cores' build, shared by mom25d and gfdtd
  (`mom25d.gpu`, `gfdtd.engine`, both `core/build.py`). `sysinfo.core_threads` is the KiCad plugin's map workers
  (it imported gfdtd's engine for them). `setup.sh` reports the GPU with `python -m emsdev`.
- CI: `.github/workflows/stack.yml` installs every repository of the stack from its main branch next to this one
  and runs all their tests (the golden summaries, parity, mom25d's and gfdtd's plugins, the GUI, the KiCad plugin,
  dfsynth's line-model cross-check), on every push, pull request, a dispatch from another repository and nightly;
  and the KiCad plugin with the commits its requirements pin. `tools/ci_deps.py` lists the stack's third-party
  requirements from the checkouts.

### Changed
- **What crosses the repositories is versioned and read once.** kicad2ems.api 2.1 (`261028-r1`; kicad2ems-gui
  and kicad2ems-fdtd require it). The run summary, the event stream, sysinfo's JSON and the antenna results carry
  `format` and `format_version` (`formats.py`), read through `api.frontend`'s `read_summary`, `read_antenna`,
  `EventTail` and `check_format` by kicad2ems-gui, the KiCad plugin and dfsynth (whose own copy of the events
  reader went). Touchstone port names: `api.frontend.port_names`, in place of the GUI's and `compare_solvers`'
  copies. The GUI finds an antenna's sphere by its `sphere_file`, not by openEMS's run folder; its Filter page
  tells a dfsynth too old for `--describe` from a missing one.
- One definition of each physical constant per package: the core's `ETA0` (three) and `MU0` in `constants`;
  kicad2ems-fdtd's through `api.solver`; mom25d's `base.C0` where it was written out; gfdtd's in a new
  `constants.py` (its `ports.py` had 4e-7π for `MU0`, the rest CODATA 2018: now CODATA 2018 throughout, a
  5e-10 change); dfsynth's `ETA0`. mom25d's antenna pattern through `pattern_gain`; the report's air margin from
  the FDTD mesh summary (`air_text`), not a copy of its formatting.
- Direct dependencies declared: kicad2ems-gui numpy, shapely, matplotlib and Pillow; kicad2ems-fdtd emsdev;
  mom25d matplotlib. mom25d's tools import through `api.frontend`.
- Tests without copies: mom25d's six board tests run once (on its frozen boards), and a new check holds
  `tests/boards/*.json` to what kicad2ems writes (the suite 313 -> 266 s); the field-dump helpers and the parity
  helper (`prepared_job`) are in `k2e_fixtures.py`; kicad2ems-fdtd's canned runs in its conftest. The board
  generators share `examples/_pcb.py` (uuid, filled rectangles and polygons, vias): every generated board is
  byte-identical to before.
- kicad2ems-gui's tests delete their standalone widgets (`FilterTab`, `PlanView`): left to the interpreter's exit,
  their plots crashed it after the tests had passed (CI's exit code 139, about one run in three).
- gfdtd's engine interface is versioned: `gfdtd.API_VERSION` (1.0: `ContinuousStructure`, `openEMS(backend=)`,
  `engine.gpu_info`, `core_info`, `resolve_backend`, `concurrent_runs`), and kicad2ems-fdtd's gfdtd solver
  checks it (`ENGINE_API`): a gfdtd of another major version, or an older minor one, is not usable and
  `kicad2ems --check` says which package to update.
- The examples install with kicad2ems, as the package `kicad2ems.examples` (the configs, the board generators,
  the three boards in the repository; not `reference/`). `api.frontend` adds `examples()`, `prepare_example(name,
  folder)` (the config copied, the board copied or generated) and `examples_folder()`. The GUI's *File →
  Examples* opens them from `~/.local/share/kicad2ems/examples/`, so it works from a pip or setup.sh copy install,
  not only a checkout.
- The GUI's Filter page runs dfsynth only as a process: its types, topologies, presets and options from
  `dfsynth --describe --json` (new), not dfsynth's modules and a copy of its options; an option the page has no
  field for appears under *More options*.
- `kicad2ems_gui.api`: kicad2ems-gui's public surface (PlanView, and what a subclass may use), which the KiCad
  plugin imports instead of `kicad2ems_gui.planview`.
- **The FDTD layer moved out of kicad2ems into kicad2ems-fdtd** (the repository kicad2ems-openems was): the mesh,
  model, runs, readback and the `FDTD` plugin class, with both solvers on it, openEMS's (from kicad2ems-openems)
  and gfdtd's (from `gfdtd.kicad2ems`). gfdtd is now an engine alone, with no kicad2ems code. The engines are the
  package's own (`engines.ENGINES`): the `kicad2ems.engines` entry-point group is gone. The `openems` and
  `gfdtd` extras install `kicad2ems-fdtd[openems]` and `kicad2ems-fdtd[gfdtd]`.
- **kicad2ems.api 2.0**: `api.solver` drops `FDTD`, `MESH`, `RUN`, `G_MESH`, `run_excitations` and `auto_mesh`,
  and adds what an FDTD layer uses of the core (`C0`, `EPS0`, `strlist`, `items`, `G_DUMP`, `registry`,
  `split_holes`, `describe_edges`, `snap_edges`, the field-dump and antenna helpers, `plot_preview`, `ram_gb`,
  `solve_lines`, `lines_backend`). `kicad2ems` no longer exports `resolve_bc`, `cavity_check`, `auto_mesh` or
  `smooth_lines`. Plugins declare `api = (2, 0)`; mom25d's does. `G_DUMP` is the core's (`fields.dumps`).
- kicad2ems's tests run on FDTD-shaped stand-ins without the FDTD class; the FDTD tests (mesh, model, runs,
  dielectrics, progress, parity, the pipeline with canned runs, the preview, antenna runs) moved to kicad2ems-fdtd.
- `tools/compare_solvers.py` runs mom25d with lumped ports (`--mom-port lumped`), the FDTD solvers' kind, and
  docs/examples.md's mom25d column is from those runs (stripline worst S11 −34.3 -> −19.9 dB, openEMS −17.5; GCPW
  S11 −31.5 -> −40.1 dB; diffvia worst Sdd11 −15.7 -> −14.7 dB).
- `tools/compare_solvers.py` also writes an agreement table, each solver against openEMS's reference over the
  whole band (input reflection: rms complex difference; transmission: rms |S| difference), in docs/examples.md.
  GCPW's row gives its worst S11 (a 7.7 GHz resonance mom25d damps: −22.5 dB, openEMS −14.8) instead of S11 at
  5 GHz, a ripple point.
- The line model: a trace's last stretch over the antipad of the via it ends at keeps the line's cross-section
  instead of the next plane down's (up to 3 reference heights; `reference.VIA_ANTIPAD`). `rf_diffvia`'s worst Sdd11
  -9.4 -> -11.9 dB (openEMS -15.4), the demo transition's S11 7.4 -> 3.0 dB rms from openEMS; no other example
  with an openEMS reference changes.
- `examples/reference/rf_steplpf_openems.s2p` from a converged mesh (`--converge 2,3,4`, x4): -3 dB at 1.729 GHz
  (was 1.757). `rf_ratrace.toml` and `rf_gcpw.toml` set the mesh their references were made with
  (`curve_res = 12`, `z_sheet = 0.25`). `examples/reference/rf_wilkinson_openems.s3p` from today's default mesh
  (S21 / S22 / S32 at 2.4 GHz -3.34 / -20.2 / -35.3 dB): the converged figures (x4 and x6 agree within 0.1 dB:
  -3.35 / -21.2 / -32.2 dB) are noted in its config, and a reference on that mesh would fail every default run.
- CI runs on Python 3.13 and 3.14 (was 3.12); on 3.14 it builds triangle from setup.sh's pinned commit, as PyPI
  has no wheel for it.
- The comparison table in docs/examples.md regenerated with `tools/compare_solvers.py` (every openEMS figure
  within its reference's tolerance). mom25d's worst stripline S11 is now −34.3 dB (was −24.8; stable with
  more adaptive-sweep solves), the line model's rf_diffvia worst Sdd11 −11.9 dB (was −9.4).
- `tools/compare_solvers.py` runs gfdtd too: a gfdtd column in docs/examples.md's comparison.
- The README's diagram of the repositories (`docs/img/architecture.svg`, from `architecture.dot`).
- `lines/tlines.py` (1,350 lines) is split by job: `formulas` (the closed-form cross-sections), `reference` (the
  reference copper and the cross-section it gives), `builder` (`build`: board to sections), `topology` (paths,
  coupled lines, discontinuities) and `elements` (`Element`, `LineModel`). `tlines` re-exports them; module settings
  (`TILE`, `ADAPTIVE`, `COUPLE_H`) are set on the module that defines them (`reference.TILE`).
- The window is its own package, [kicad2ems-gui](https://github.com/lowprocess/kicad2ems-gui) (the `gui` extra
  installs it; `filter` installs it with dfsynth): `kicad2ems.gui` is now `kicad2ems_gui`, with `volume3d` (the 3D
  view's arrays), the `kicad2ems-gui` and `kicad2openems-gui` commands and the GUI's tests. kicad2ems has no Qt
  code left. What starts and follows a run stays here as `kicad2ems.runs` (`Run`, `EventTail`, `stop_runs`,
  `find_results`, `write_toml`, `toml_value`), which the KiCad plugin and `--init` use too. `--check` reports the
  GUI package. `setup.sh` takes the launcher's icon from the installed GUI package (its old path predated `src/`).
  Version 261027-r2.
- Every full-wave solver is a package of its own, found through the `kicad2ems.solvers` entry points (ROADMAP
  7.2-7.3); only the line solvers are built in. openEMS's plugin is
  [kicad2ems-openems](https://github.com/lowprocess/kicad2ems-openems) (the new `openems` extra: its engine and
  import check, `openems_venv`, `--converge`); mom25d's and mixed's are in mom25d (`mom25d.kicad2ems`), gfdtd's in
  gfdtd (`gfdtd.kicad2ems`). Installing kicad2ems alone gives the line model; `pip install '.[all]'` everything as
  before. A solver named but not installed says which extra brings it; `--check` lists the missing ones and exits 0
  when any full-wave solver is ready (it was: openEMS). FDTD engines register under `kicad2ems.engines` (each its
  `api()`, `LABEL` and optionally `IN_THREADS`), so `fdtd/engines.py` names none; openEMS's engine gives a CSXCAD
  without `AddDebyeMaterial` the method. A solver's `order` places it in lists (openEMS, mom25d, mixed, gfdtd, then
  the line solvers, as before).
- No solver names in core code (ROADMAP 7.4): the GUI's sections come from the solvers' option groups (the FDTD
  settings openEMS and gfdtd share are the "FDTD (openEMS, gfdtd)" section, `FDTD/Mesh` and so on, formerly
  `openEMS/...`), the sections auto enables are those of the solvers auto may pick, the antenna settings those of
  the solvers with an antenna mode, and the Filter page's solver choices are the full-wave solvers installed.
  The KiCad plugin's solver menu and dfsynth's validation do the same (their own repositories). Stop asks every
  FDTD run folder to abort, whichever engine wrote it.
- The tests of each plugin moved to its package (ROADMAP 7.5): `test_converge`, `test_openems_venv`, the openEMS
  import check and the `--converge` settings and run to kicad2ems-openems; `test_mom`, `test_mom25d_board` and
  mom25d's halves of the parity and mesh-file tests to mom25d (`test_kicad2ems*.py`); `test_gfdtd` to gfdtd. They use
  kicad2ems's fixtures from a checkout (`tests/k2e_fixtures.py`, formerly `conftest.py`; `KICAD2EMS_SRC`, default
  `../kicad2ems`). kicad2ems's own tests run on stand-in solvers (`tests/standins.py`: the FDTD plugin on a stand-in
  engine, a planar solver with a canned answer) and need no solver package; `test_conformance.py` runs the real
  ones, each in a process of its own. `tools/test_all.sh` runs all four suites, isolated from your settings.
- Version 261027-r1 (kicad2ems, mom25d, gfdtd, kicad2ems-openems); the plugins require kicad2ems 261027 or later.
- The FDTD code openEMS and gfdtd share left `solvers/openems/` (ROADMAP 7.1): `kicad2ems.fdtd` holds the
  boundaries, mesh, model (`build_fdtd`), engines, runs, progress, antenna runs and `FDTD`, the plugin class both
  solvers subclass; `kicad2ems.fields` the field results any solver writes (dumps, frames, far-field
  analysis). gfdtd no longer subclasses openEMS's plugin and lists the shared mesh and run settings itself (the
  same settings, under `[solver.gfdtd]` too). `solvers/openems/` keeps openEMS's engine and import check and
  `--converge`. Code importing `kicad2ems.solvers.openems.<module>` needs the new paths.
- gfdtd is its own package, in its own repository (https://github.com/lowprocess/gfdtd), with its history,
  tests, benchmark and README. kicad2ems takes it as an optional dependency (the `gfdtd` extra) and its solver
  plugin stays here; without it installed, gfdtd is listed as not installed. The `gfdtd` command comes with
  the gfdtd package.
- mom25d is back in its own repository (https://github.com/lowprocess/mom25d), with its history since the import and its tests,
  which now read saved board files instead of building them with kicad2ems. It is optional, as gfdtd is:
  the `mom` extra (`all` for every extra but a GPU wheel), and `setup.sh` asks for each. Without it,
  `--check` lists mom25d and the mixed solver as not installed, auto leaves them out, and the tests that need
  it are skipped (marker `mom25d`). The board-file contract test (tests/test_mom25d_board.py) stays here.
- dfsynth is its own repository (https://github.com/lowprocess/dfsynth), with its history: the `filter` extra
  installs it for the GUI's Filter page, which no longer falls back to a checkout's tools/dfsynth.
- The package moved to `src/kicad2ems` (the src layout, as mom25d's): a checkout no longer imports by accident
  from its root. Run from a checkout without installing with `PYTHONPATH=src`; `setup.sh`'s editable install
  points there.
- The KiCad plugin is its own repository, kicad2ems-ipc (https://github.com/lowprocess/kicad2ems-ipc), with its
  history, and installs from KiCad's Plugin and Content Manager: KiCad builds it a Python environment with
  kicad2ems in it. The `kicad` extra and the `kicad2ems-kicad` command are gone; docs/kicad-plugin.md lists
  what the plugin uses from kicad2ems.
- The line model's analysis (tlines, strips, coupled) moved from `kicad2ems/solvers/lines/` to
  `kicad2ems/lines/`: it is used in every run, not only by the line solvers, which stay in `solvers/lines/`
  (fastsolve, the qucs and scikit plugins). Code that imported `kicad2ems.solvers.lines.tlines` imports
  `kicad2ems.lines.tlines`.
- The rf_<name>.kicad_pcb example boards are no longer in the repository: `python examples/make_rf_examples.py
  all` writes them (their configs stay), and the tests make them when they are missing. Only the boards no
  script makes are kept: rf_interdig, hairpin and sma_launch.
- examples/make_rf_examples.py sizes the differential via's stripline pair with the line model's
  `coupled_stripline` (Cohn) instead of mom25d's copy of the same formula.

### Fixed
- mom25d: a solve whose currents aren't finite is solved again on the CPU, and raises FloatingPointError if it
  still isn't, instead of returning NaN S-parameters (the GPU solve once gave NaN on the CM5 region).
- `test_radial_stub_in_qucsator_is_mrstub` failed where qucsator isn't installed (CI) instead of skipping.
- The tests read the environment's `kicad2ems-site.toml` (setup.sh's record of this machine's qucsator), so four
  of them failed where qucsator was installed. They now see only a scratch user file.
- Since the shared fixtures moved to `tests/k2e_fixtures.py`, the fixture that keeps the tests out of the user's
  settings was not applied (a `_` name, which `import *` leaves out): the GUI tests wrote
  `~/.config/kicad2ems/gui.conf`. Renamed `scratch_settings`.
- gfdtd: a current probe normal to y ran its loop the wrong way round, so a lumped port along y measured its
  current negated (|S11| near 95). Ports along x and z were right, so no example had shown it.
- `bc = auto` put a PEC wall on a bottom plane with holes in it: a slot, a defected ground or an antipad there
  was shorted (`rf_dgs` came out a plain line). The bottom copper must now be one piece, bare only within 1 mm
  of the board edge.
- The Filter Synthesis page kept showing an earlier run's validation rounds when a later run had fewer (or
  none): it now shows only the rounds the run's design JSON lists, and clears the old `_em*` folders.
- dfsynth's hairpin came out 11-14 % high in frequency on the field solvers. Its line model now has every arm
  in one multiconductor array, a U's own arms coupled too, and a U's square corners shorter than its centre line
  (0.17 h + 0.33 w each, fitted to gfdtd and openEMS on single hairpins). Round 0 on gfdtd: centre -0.7 % on
  FR4-1.6, -0.2 % on RO4003C-0.813.
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

### Decided
- mom25d slot layers (Momentum-style magnetic currents on a plane's openings): not pursued. On CM5 the whole
  stack is 125,525 unknowns and 77 % of them are on layers that can't be slot layers; the two planes that can
  only drop from 14,499 to 8,658 each. About 1.5x at best for some two months' work with research risk; gfdtd
  solves the CM5 HDMI pair in 47 s. Worth reopening only for a large board dominated by solid planes.
- The rat-race's isolation null in openEMS was mesh convergence, not a missing conformal option: on gfdtd it moves
  from 2.18 GHz to 2.30 GHz (EMerge 2.31) as `--curve-res` rises. No conformal option added.
- `rf_diffvia`: modelling the two via barrels as a coupled pair in a shared antipad moved the worst Sdd11 by
  0.3 dB the wrong way. Not kept. After the antipad rule the line model is -11.9 dB worst against openEMS's -15.4,
  5.2 dB rms: the error is where the two transitions' reflections combine (the ripple's peaks), not a missing
  element. A pad capacitance, a return-path inductance and the two together (0-0.08 pF, 0-0.3 nH) only made the
  rms worse. Left as a limit of the line model.
- The line model stays in core: every run builds it, the report, overlay, KiCad plugin and example generator use
  it, and its circuit solvers need nothing compiled.
- No Elmer FEM solver plugin: second-order elements needed over 21 GB on a simple microstrip line, and
  first-order ones weren't accurate enough.
- mom25d stays slow on boards with planes (8–23x openEMS on the CPU): with slot layers ruled out there is no
  cheap fix, and gfdtd covers those boards. Its adaptive sweep not converging on the CM5 region is left for the
  same reason.
- The whole stack keeps lumped ports only (they match gfdtd; wave ports would need its own line
  characterisation) and a laterally infinite dielectric (built into the layered Green's function; a finite board
  edge is research-scale). Edge-radiating antennas stay with openEMS and gfdtd.
- CI stays on hosted runners with stand-in solvers: no openEMS/qucsator image unless the stand-ins start missing
  real breakage, and no self-hosted GPU runner (upkeep and exposure; `tools/test_all.sh` runs the GPU tests
  locally).
- No exported conformance suite (`kicad2ems.testing`) for now: `tests/test_conformance.py` is one thru board,
  and `tools/compare_solvers.py` with the parity tests check the solvers harder. Worth doing when a third-party
  solver appears.

### Added
- gfdtd: openEMS's other ports, with their `Add...Port` methods: microstrip (MSL), stripline, CPW, coaxial,
  rectangular and circular waveguide, any waveguide mode (functions or a mode file), and curve ports. Behind
  them: weighted, hard, H-field and delayed excitations; mode-matching probes; cylindrical shells and curves;
  current probes snapped to the dual mesh as openEMS snaps them. Against openEMS on small models: within 0.03 in
  |S| (`docs/gfdtd.md`, Ports).
- Antenna results give the polarisation at the gain maximum: axial ratio, hand (RHCP / LHCP or linear) and the
  gain in each hand, for openEMS, gfdtd and mom25d.
- `port.width`: a vertical port of that width centred on its footprint, for a probe feed inside a patch.
- RF examples (`make_rf_examples.py`): SIW, microstrip-to-microstrip through-via, microstrip to grounded CPW,
  microstrip-slotline transitions, broadside Marchand balun, bends, serpentine, crosstalk with and without a
  guard trace, parallel-coupled and combline band-pass, open-loop resonators, defected ground, mushroom EBG,
  interdigital capacitor, meander and spiral inductors.
- Antenna examples (`make_antennas.py`): circularly polarised patch, aperture-coupled patch, meander monopole on
  a finite ground, printed Yagi, log-periodic, bowtie, UWB disc monopole and full-wave loop, run on gfdtd.
- dfsynth: `--hairpin-gap` (the gap between a hairpin's own arms; its default is now about half what it was, so
  the 2.4 GHz FR4-1.6 filter is 66 mm long instead of 85) and `--refine-time` (the final refinement's time
  limit, 10 s: it had run for minutes on changes too small to matter). Both are on the Filter Synthesis page.

- `--air` takes six values (xmin,xmax,ymin,ymax,zmin,zmax) as well as one, like `--bc`: more air on one side,
  in front of an endfire antenna for instance. The GUI has it: *Air margin* under Mesh.
- A gfdtd preview (without openEMS) draws the mesh, `<name>_mesh.png`, and writes the grid for the Viewer.
- The GUI's openEMS section is *openEMS + gfdtd (FDTD)*, and gfdtd's own section says its mesh, run and dump
  settings are there.
- `make_antennas.py vivaldi` takes the board's size (`W=`, `H=`); the feed keeps its place beside the slot.
- An antipodal Vivaldi example (`make_antennas.py vivaldi_antipodal`, `antenna_vivaldi_antipodal.toml`): matched
  over all of 3–10 GHz, 0.4–1.2 dB more gain than the slot Vivaldi.
  Its options `cd` (edge slots), `dext`/`dl` (a director past the aperture) and `fr` add up to 2.3 dB more:
  `cd=5 dext=15 dl=24` gives 6.0 / 8.6 / 8.8 dBi at 4 / 6 / 8 GHz, front-to-back 9–15 dB.
  Also `sym` (the symmetric, curved form), `vias` (corner via walls), `nd` (striped directors), and
  `vivaldi_antipodal_array`: N elements from one port through a corporate feed (4 elements: 10.8 dBi at 8 GHz).
  Both take the substrate (`er`, `tand`, `h`); on RO4003C the array reaches 13.4 dBi at 8 GHz. The array's
  feed can use 2- or 3-section Chebyshev transformers (`ns`, over `fl`..`fh`): on RO4003C, `ns=3` takes its
  worst match from −5.1 to −8.7 dB.

### Changed
- The interdigital filter example is `rf_interdig` (`examples/rf_interdig.toml`, `.kicad_pcb`, and its reference
  results), formerly `filter`.
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
