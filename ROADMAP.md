# Roadmap

What is worth doing next, in priority order. Each item comes from something the examples or benchmarks
showed. Finished items leave this file; what was done is in CHANGELOG.md, and the decisions not to do
something are under its *Decided* headings. Last updated 2026-10-28.

1. **Regenerate the stale openEMS references (`examples/reference/`).** `tools/compare_solvers.py` ran every
   board on today's openEMS (2026-10-28): branchline, stub notch, coupler, radial stub, stripline and diff-via
   match their references; four moved. The rat-race and the grounded CPW: their references were made with
   `--curve-res 12` and `--z-sheet 0.25`, which the configs don't set. The Wilkinson and the step low-pass moved
   with the newer mesh, and `--converge 1,1.5,2` says neither is converged on its default mesh (×1.5 to ×2: 1.8
   and 0.9 dB at most). Their main figures hold (Wilkinson S21 −3.34 dB at every level); the deep ones move: S22
   −20.2 / −20.7 dB and isolation −35.3 / −33.6 dB at ×1 / ×2 (reference −19.4, −33.2), the step low-pass −3 dB
   point 1.736 / 1.730 GHz (reference 1.757). To do: put the references' settings in the configs, regenerate the
   references on a converged mesh, and the examples.md table with `tools/compare_solvers.py`. Until then the
   comparison flags changes that aren't real.
2. **A Python matrix in CI.** `.github/workflows/stack.yml` runs every repository's tests on Python 3.12 only:
   3.10 to 3.14 (triangle, mom25d's mesher, from source on 3.14).
3. **A NaN guard on mom25d's GPU solve.** The GPU solve returned NaN once on the 22,700-unknown CM5 region and
   hasn't reproduced (the ports it ran with weren't recorded). Rather than hunt it: check the solution, and fail
   loudly or solve again on the CPU.
4. **`rf_diffvia` above 5 GHz in the line model** (worst Sdd11 −9.4 dB against openEMS's −15.4 dB). Not the
   via barrels (a coupled via-pair model moved it the wrong way). The reflection rises evenly from 3 GHz on both
   sides of the board. One try at the fan-out to the 1 mm via pitch (its diagonal legs aren't a coupled
   section); if that isn't it, note the limit in docs/ and close this.
