# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Finished items leave this file; what was done is in CHANGELOG.md, and the
decisions not to do something are under its *Decided* headings. Last updated 2026-10-28.

## 1. Line model

- **`rf_diffvia` above 5 GHz** (worst Sdd11 −9.4 dB against openEMS's −15.4 dB). Not the via barrels (a coupled
  via-pair model was tried and moved it the wrong way). The line model's reflection rises evenly from 3 GHz on
  both sides of the board. Next suspects: the fan-out to the 1 mm via pitch (its diagonal legs aren't a coupled
  section) and the via pads.

## 2. mom25d

- **Boards with planes are slow**: 8–23 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 417 s → 171 s).
- **The CM5 region**: the GPU solve returned NaN on the 22,700-unknown CM5 region (reported, not reproduced on
  2026-10-28: which F.Cu pads the ports were moved to isn't recorded, and the saved region config has the
  original B.Cu ports), and the adaptive sweep didn't converge there in 15 solves.
- **The whole stack's limits**: wave ports still need the ground stack (they assume its line characterisation),
  and the dielectric is laterally infinite, so an antenna whose radiation comes from the board's edge (an
  inverted-F, a monopole at the edge) is still openEMS's or gfdtd's.

## 3. openEMS

- **The references (`examples/reference/`) are partly stale.** `tools/compare_solvers.py` ran every board on
  today's openEMS (2026-10-28): branchline, stub notch, coupler, radial stub, stripline and diff-via match their
  references; four moved. The rat-race and the grounded CPW: their references were made with `--curve-res 12` and
  `--z-sheet 0.25`, which the configs don't set. The Wilkinson and the step low-pass moved with the newer mesh, and
  `--converge 1,1.5,2` says neither is converged on its default mesh (×1.5 to ×2: 1.8 and 0.9 dB at most). Their
  main figures hold (Wilkinson S21 −3.34 dB at every level); the deep ones move: S22 −20.2 / −20.7 dB and
  isolation −35.3 / −33.6 dB at ×1 / ×2 (reference −19.4, −33.2), the step low-pass −3 dB point 1.736 / 1.730 GHz
  (reference 1.757). To do: put the references' settings in the configs, regenerate the references on a converged
  mesh, and the examples.md table with `tools/compare_solvers.py`.

## 4. CI

- `.github/workflows/stack.yml` runs every repository's tests on Python 3.12 only. Still to weigh:
  a Python matrix (3.10 to 3.14; triangle from source on 3.14), the container on the `docker` branch as the job's
  image (real openEMS and qucsator, prebuilt on GHCR), and a self-hosted runner with the AMD GPU for the GPU tests
  (hosted runners have none; never for pull requests from forks).
