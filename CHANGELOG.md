# Changelog

All notable changes to kicad2ems (formerly kicad2openems), and to the mom25d and gfdtd solvers while they
were bundled with it, by day. Releases are named by datecode, `YYMMDD-rN` (for
example `261013-r1`, the first release on 13 October 2026).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
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
