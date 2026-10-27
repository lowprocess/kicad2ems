# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-27.

## 1. Open questions (correctness)

- **Rat-race isolation in openEMS** converges to a null at 2.24 GHz, against mom25d's 2.33, EMerge's 2.31 and
  the line model's 2.40 (the design): the staircased ring is electrically long, and refining doesn't remove it. Worth
  trying: openEMS's conformal or diagonal-edge options for curved copper, if they apply to sheets.

## 2. Line model

- **Vias** are the weak part of `rf_diffvia` above 5 GHz (worst Sdd11 −9.4 dB against openEMS's −15.4 dB):
  a coupled via pair in its antipads.

## 3. mom25d

- **Boards with planes are slow**: 8–23 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 417 s → 171 s). Left: the slot layers below, which
  replace the earlier idea of modelling enclosed plane pairs as a parallel-plate stack.
- **Multilayer boards** (KiCad's CM5 demo, an HDMI pair, [mom.md](docs/mom.md#on-real-boards)): one infinite
  ground at z = 0 leaves out the copper below it, so ports below it are impossible (every high-speed pair on that
  board starts on B.Cu), while every plane above it is meshed (128,000 unknowns with B.Cu as the ground;
  22,700 with In1.Cu, without B.Cu). Keysight's Momentum gets round both by modelling the whole stack (air,
  ground or cover top and bottom, metal on any interface) and by meshing a solid plane as its openings only
  ("slot" layers: magnetic currents on the antipads). The two belong together: the whole stack alone means
  meshing every plane, which makes the CM5 case worse; slot layers are what make it affordable. In order:
  1. **The whole stack** (about 2 weeks, low risk). The ground is assumed in a handful of places: the short
     at the bottom of `LayerStack._network`, the copy of the network in `via.py` and its via-chain ends
     (`k == 0`), `board.py`'s ground choice (z = 0, copper below dropped), the ports' default reference
     (`kref = 0`) and their characterisation, the singular terms at the bottom interface (`eps_sum`,
     `nearest_discontinuity`) and `farfield.py`'s upper half-space. Checks: a dielectric slab in air against
     exact results, a meshed finite ground converging to the infinite one, B.Cu microstrip against its F.Cu
     mirror image. Worth doing on its own: bottom copper and ports, and antennas on a finite ground
     (an inverted-F, a monopole at the board edge), today sent to openEMS (see Antenna mode below).
  2. **Count the unknowns** on CM5 with In1.Cu and In4.Cu as slot layers (about a day): go or no-go for the
     rest.
  3. **Slot layers without vias through them** (2–3 weeks): a solid plane is a perfect conductor at its
     interface, splitting the stack in two halves coupled through magnetic currents on its openings. New
     kernels: the magnetic-current counterparts of the present ones (the same network with a series source),
     and the cross terms between electric and magnetic currents, nearly singular where a signal layer sits
     close to a plane (0.1 mm prepreg on CM5 against a 0.2–0.3 mm mesh). Only a layer that is one solid plane
     with holes qualifies: copper islands in its openings would be shorted to it, so layers with signal copper
     stay meshed. Checks: an aperture-coupled patch (textbook), and the same board with the plane meshed
     (step 1) and as a slot layer.
  4. **Vias through antipads** (2–3 weeks; the hardest part): the via ends on the closed plane from both
     sides with its current carried through, and new via kernels against the antipad's magnetic current
     (`via.py`). Vias ending on the plane get easier. Check: a stripline-to-microstrip via transition against
     gfdtd and openEMS.
  5. **Assembly and solvers** (1–2 weeks): the new blocks in `fast.py`, the compiled core and the GPU path.
     Mixed electric and magnetic unknowns make the matrix indefinite, which `ldlt.py` and `blr.py` don't
     expect: a sign convention, and perhaps pivoting.

  Roughly 2–2.5 months in all, with research risk in steps 3–5 (cross terms across thin layers, the
  via-through-antipad junction, low-frequency stability with mixed unknowns), any of which could add weeks.
  Even then mom25d stays a planar solver without board edges. Against it: gfdtd already solves the CM5 HDMI
  pair in 47 s at `--fine 0.1` (3.6 M cells), and in about 31 minutes for all six legs on the default mesh.
  Plan: step 1, then decide on the rest after step 2. Also open: the GPU solve returned NaN on the 22,700-
  unknown CM5 region (reported, not fixed), and the adaptive sweep didn't converge there in 15 solves.
- **Current dumps** cost one extra solve per frequency; the sweep's own solves rarely land on the dump
  frequencies.
- **Antenna mode** assumes an infinite ground, so antennas that use the board edge are out of reach (step 1 of
  multilayer boards, above, opens them), and via currents are left out of the far field.
- **Beam steering to an angle** for phased arrays (`--drive`): today the phase step is added from each
  driven port to the next in the order the ports are listed, which only steers cleanly for a line of equally
  spaced elements numbered along it. A target θ, φ instead would give each port −k₀(xₖ sin θ cos φ +
  yₖ sin θ sin φ) from its position, on top of the drive's phases: any number of feeds in any layout
  (rows and columns, a triangle), and Animate sweeping the angle. Test it on a 1×3 row of patches. Feed
  points well off their element's centre still need the drive's phases to make up the difference.
- **Phased drive in openEMS**: one run per port, then the near-to-far-field results combined with the
  drive's weights (exact at every frequency). It drives the first port alone today.

## 4. openEMS

- Other structures may still need a `--converge` check: the mesh now grades z cells at signal copper and
  refines over curves, which settled the coupler, the grounded CPW and the radial stub, but only those were
  studied. The other references (`examples/reference/`) were made before these mesh changes.

## 5. GUI

- A difference plot between two solvers (dB or |ΔS|).
- Mixed-mode traces in the trace list.
- The run's notes and warnings in the results.
- Interactive plots in the HTML report.

## 6. Examples and validation

- A script that regenerates the solver comparison over every example before a release.

## 7. Solvers as separate packages

The aim is full mix and match: kicad2ems as the board model, the line model and the results, with each
full-wave solver a package that is installed or not. In order:

1. ~~**Shared FDTD and results code out of `solvers/openems/`.**~~ Done (2026-10-26): `src/kicad2ems/fdtd/`
   (boundaries, mesh, dielectric, model, engines, run, progress, antenna runs, readback, and `FDTD`, the plugin
   class with the shared settings that openEMS and gfdtd both subclass) and `src/kicad2ems/fields/` (dumps,
   frames, far-field analysis).
2. ~~**The openEMS adapter as its own package.**~~ Done (2026-10-27):
   [kicad2ems-openems](https://github.com/lowprocess/kicad2ems-openems) (the `openems` extra), registered through
   the `kicad2ems.solvers` and `kicad2ems.engines` entry points. Its engine gives a CSXCAD without
   `AddDebyeMaterial` the method, so `build_fdtd` no longer imports CSXCAD.
3. ~~**Each adapter goes to its solver's package.**~~ Done (2026-10-27): `mom25d.kicad2ems` (mom25d and mixed,
   built on mom25d's `refactor/concise` branch) and `gfdtd.kicad2ems` (the solver and the engine). `mixed.py`'s use
   of mom25d's internals stays inside mom25d; the contract is now kicad2ems's Solver API, which the plugins pin
   (`kicad2ems>=261027`). `BUILTIN` holds only the line solvers.
4. ~~**No solver names in core code.**~~ Done (2026-10-27) for what decides behaviour: the GUI's sections (from
   the option groups), auto's and the antenna's solver sets, the Filter page, the KiCad plugin's menu, dfsynth's
   full-wave result, `--check`, the pipeline's checks, Stop, the engines' threading. Left, as display defaults
   with a fallback for any other solver: the trace styles and file suffixes (`gui/traces.py`), the mesh labels
   (`meshes.py`), `--mom` (adds mom25d; warns when it is not installed), `solvers.EXTRAS` (the install hints),
   the `[openems]` config table of old files and the `kicad2openems` command.
5. ~~**Tests move with their plugins.**~~ Done (2026-10-27): each plugin's tests are in its package, on kicad2ems's
   fixtures (`tests/k2e_fixtures.py`) from a checkout; kicad2ems's own run on stand-in solvers
   (`tests/standins.py`) and pass with no solver package installed. `test_conformance.py` runs the real ones, each
   in a process of its own. `tools/test_all.sh` runs all four suites locally.
6. **CI on GitHub Actions** (to consider). Nothing runs the suites but `tools/test_all.sh` today. The cheapest
   useful job: check out kicad2ems and the three solver repositories side by side, `pip install -e` all four, run
   `tools/test_all.sh`. It needs no openEMS or qucsator (the tests use stand-ins), a Python matrix (3.10 to 3.14;
   triangle from source on 3.14), and runs on every push and nightly, so a change in any repository that breaks
   another shows within a day (or the solver repositories trigger it with `repository_dispatch`). Options to
   weigh: the container on the `docker` branch as the job's image (real openEMS and qucsator, a prebuilt image on
   GHCR), and a self-hosted runner with the AMD GPU for the GPU tests (hosted runners have none; never for pull
   requests from forks).

The line model stays in core. Its analysis (`src/kicad2ems/lines/`: sections, Z0, coupled lines) is not a
solver: every run builds it, and the report, the GUI's overlay, the KiCad plugin's impedance map and the
example generator use it. Its circuit solvers (`solvers/lines/`: qucsator, scikit-rf) could be split out like
the others, but they need nothing compiled, they are the one solver that runs with nothing else installed, and
openEMS's and gfdtd's run-time estimates use them.

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
