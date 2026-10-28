# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-28.

## 1. Open questions (correctness)

- ~~**Rat-race isolation in openEMS**~~ Settled (2026-10-28): it wasn't converged. On gfdtd (the same FDTD
  model) the null moves from 2.18 GHz at the default `--curve-res` to 2.25 at 12 (the reference's), 2.28 at 24
  and 2.294 / 2.296 / 2.299 GHz at 36 / 48 / 64 (up to 22 M cells): 2.30 GHz, against EMerge's 2.31 and mom25d's
  2.33. No conformal option needed; the ring needs small cells along the curve (docs/examples.md).

## 2. Line model

- **`rf_diffvia` above 5 GHz** (worst Sdd11 −9.4 dB against openEMS's −15.4 dB). Not the via barrels, it turns
  out: they are 0.4 mm long (0.03 λ at 10 GHz), and modelling the two as a coupled pair in a shared antipad (two
  line charges and their images in a grounded cylinder; odd mode 46 Ω against each barrel's 42 Ω coax) moved the
  worst Sdd11 by 0.3 dB, the wrong way (tried 2026-10-28, not kept). The line model's reflection rises evenly from
  3 GHz on both sides of the board. Next suspects: the fan-out to the 1 mm via pitch (its diagonal legs aren't a
  coupled section) and the via pads.

## 3. mom25d

- **Boards with planes are slow**: 8–23 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 417 s → 171 s). The slot layers below were the plan for it;
  their count (step 2) says they would gain about 1.5x, so this stays as it is.
- **Multilayer boards** (KiCad's CM5 demo, an HDMI pair, [mom.md](docs/mom.md#on-real-boards)): one infinite
  ground at z = 0 leaves out the copper below it, so ports below it are impossible (every high-speed pair on that
  board starts on B.Cu), while every plane above it is meshed. Keysight's Momentum models the whole stack and
  meshes a solid plane as its openings only ("slot" layers: magnetic currents on the antipads). In order:
  1. ~~**The whole stack**~~ Done (2026-10-28): `LayerStack(bottom='open')` (a half-space below, metal on
     interface 0, `is_pec`), the via network and chain ends on it, `BoardModel(ground='none')` (every copper layer,
     the planes meshed; each port lumped to the nearest plane under or over it, `ViaPort(sign=)` for one above), and
     kicad2ems's `--mom-stack whole`. Checked: in a homogeneous stack the network is free space's to 1e-15 on every
     pair of interfaces; a meshed ground converges to the infinite one (S21 0.011 -> 0.0004 as it widens); the
     board upside down (B.Cu microstrip under its plane) gives the same S to 1e-4; `rf_msvia`, which the ground stack
     cannot solve (a B.Cu port), matches gfdtd's S21 to 0.05 dB up to 7 GHz (5,219 unknowns, 25 s). Antenna mode
     radiates over the full sphere: the patch over its meshed 80 mm ground has a back lobe (-17.5 dBi; gfdtd -13.1)
     and 2.9 dBi gain (gfdtd 3.1; the infinite ground 1.9). Wave ports still need the ground stack (they assume its
     line characterisation), and the dielectric is still laterally infinite.
  2. ~~**Count the unknowns** on CM5 with In1.Cu and In4.Cu as slot layers~~ Done (2026-10-28): **no-go** for steps
     3-5. The whole stack is 125,525 unknowns at 0.3 mm (planes at 0.9 mm), and 77 % of them are on layers that
     cannot be slot layers: F.Cu 30,382, In3.Cu 29,627, B.Cu 20,552, In2.Cu 15,966 (signal copper, split pours). In1.Cu
     and In4.Cu do qualify (one plane with 35 holes, their islands the pads of vias passing through), but their
     openings are narrow and need fine cells: 8,658 unknowns each as slot layers against 14,499 meshed, so 113,800
     in all, and 96,500 even if those two planes cost nothing. On `rf_msvia`, a board whose cost is its planes, slot
     layers save a third (3,864 -> 2,582). A factor of about 1.5 at best, against the order of magnitude CM5 needs.
  3. **Slot layers without vias through them**, 4. **vias through antipads**, 5. **assembly and solvers**: not
     pursued, on step 2's count. About two months with research risk (the electric-magnetic cross terms across thin
     prepreg, the via-through-antipad junction, an indefinite matrix for `ldlt.py` and `blr.py`) for 1.5x on the
     boards that need it, where gfdtd solves the CM5 HDMI pair in 47 s at `--fine 0.1`. What would change it: a
     board class dominated by solid planes and large enough to need it, or a cheaper mesh of the signal layers.
  Also open: the GPU solve returned NaN on the 22,700-unknown CM5 region (reported, not fixed; not reproduced on
  2026-10-28: which F.Cu pads the ports were moved to isn't recorded, and the saved region config has the original
  B.Cu ports), and the adaptive sweep didn't converge there in 15 solves.
- ~~**Current dumps** cost one extra solve per frequency.~~ Done (2026-10-28): the adaptive sweep solves at the
  dump frequencies among its first samples and keeps their currents (`solve_band(keep_at=...)`); a dump inside the
  band costs no solve of its own.
- ~~**Antenna mode** assumes an infinite ground, and via currents are left out of the far field.~~ Done
  (2026-10-28): with the whole stack (above) the board's own ground is meshed and the far field covers the full
  sphere (the lower half-space from the tangential field on interface 0), and the via chains' vertical currents
  radiate on either stack (E_u = -j k_rho S~ through the TM network, the via kernels' S~). Checked against free-space
  dipoles above and below the board, the via against a horizontal element in sign and phase, and a via over the
  ground with its image. Left: the laterally infinite dielectric, so an antenna whose radiation comes from the
  board's edge (an inverted-F, a monopole at the edge) is still openEMS's or gfdtd's.
- ~~**Beam steering to an angle**~~ Done (2026-10-28): `--aim THETA,PHI` gives each driven port
  −k₀(x sin θ cos φ + y sin θ sin φ) from its place at the pattern frequency, on top of `--drive` and `--steer`
  (any layout; tested on a 1×3 row and a triangle of feeds), and the GUI's Beam steering bar has *Aim at* with
  Animate sweeping θ or turning φ. On a few elements the peak falls short of the target where the element's
  pattern pulls it to broadside (the 2×2 example aimed at θ 20° peaks at 15°).
- ~~**Phased drive in openEMS**~~ Done (2026-10-28), for gfdtd too: one run per driven port, each run's far field
  per unit incident wave, summed with the drive's weights; P1 alone this way matches its single antenna run to
  0.01 dB. The GUI steers it like mom25d's.

## 4. openEMS

- **The references (`examples/reference/`) are partly stale.** `tools/compare_solvers.py` ran every board on
  today's openEMS (2026-10-28): branchline, stub notch, coupler, radial stub, stripline and diff-via match their
  references; four moved. The rat-race and the grounded CPW: their references were made with `--curve-res 12` and
  `--z-sheet 0.25`, which the configs don't set. The Wilkinson and the step low-pass moved with the newer mesh, and
  `--converge 1,1.5,2` says neither is converged on its default mesh (×1.5 to ×2: 1.8 and 0.9 dB at most). Their
  main figures hold (Wilkinson S21 −3.34 dB at every level); the deep ones move: S22 −20.2 / −20.7 dB and
  isolation −35.3 / −33.6 dB at ×1 / ×2 (reference −19.4, −33.2), the step low-pass −3 dB point 1.736 / 1.730 GHz
  (reference 1.757). To do: put the references' settings in the configs, regenerate the references on a converged
  mesh, and the examples.md table with `tools/compare_solvers.py`.

## 5. GUI

All done (2026-10-28): ~~a difference plot between two solvers~~ (Δ|S| and |ΔS| in dB against a solver chosen
on the plot), ~~mixed-mode traces in the trace list~~ (Sdd21, Scd21, Sds31), ~~the run's notes and warnings in
the results~~ (a fold on the Results page), ~~interactive plots in the HTML report~~ (inline SVG and script:
hover, a box per solver and S, |S| or phase, drag to zoom).

## 6. Examples and validation

- ~~A script that regenerates the solver comparison over every example before a release.~~ Done (2026-10-28):
  `tools/compare_solvers.py` runs openEMS, mom25d and the line model on the eleven boards, measures the table's
  figures and checks openEMS's against the references (exit 1 when one moved): 5–160 s a board on openEMS.

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
6. ~~**CI on GitHub Actions**~~ Done (2026-10-28): `.github/workflows/stack.yml` checks out every repository
   side by side and runs all their tests on every push, pull request, dispatch and nightly, and the KiCad plugin
   with its pinned requirements. Still to weigh, from the plan below: the Python matrix, the `docker` image with
   real openEMS and qucsator, a self-hosted GPU runner. The plan was: nothing runs the suites but `tools/test_all.sh` today. The cheapest
   useful job: check out kicad2ems and the three solver repositories side by side, `pip install -e` all four, run
   `tools/test_all.sh`. It needs no openEMS or qucsator (the tests use stand-ins), a Python matrix (3.10 to 3.14;
   triangle from source on 3.14), and runs on every push and nightly, so a change in any repository that breaks
   another shows within a day (or the solver repositories trigger it with `repository_dispatch`). Options to
   weigh: the container on the `docker` branch as the job's image (real openEMS and qucsator, a prebuilt image on
   GHCR), and a self-hosted runner with the AMD GPU for the GPU tests (hosted runners have none; never for pull
   requests from forks).

7. ~~**The window as its own package.**~~ Done (2026-10-27): [kicad2ems-gui](https://github.com/lowprocess/kicad2ems-gui)
   (`kicad2ems_gui`), with its tests on kicad2ems's stand-in solvers. Core keeps what starts and follows a run
   (`kicad2ems.runs`) and the modules the KiCad plugin also uses (`overlay`, `meshes`). Since 2026-10-28 the GUI,
   the KiCad plugin and dfsynth import only `kicad2ems.api.frontend`, and the solver plugins `kicad2ems.api.solver`
   (versioned: `API_VERSION`).

The line model stays in core. Its analysis (`src/kicad2ems/lines/`: sections, Z0, coupled lines) is not a
solver: every run builds it, and the report, the GUI's overlay, the KiCad plugin's impedance map and the
example generator use it. Its circuit solvers (`solvers/lines/`: qucsator, scikit-rf) could be split out like
the others, but they need nothing compiled, they are the one solver that runs with nothing else installed, and
openEMS's and gfdtd's run-time estimates use them.

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
