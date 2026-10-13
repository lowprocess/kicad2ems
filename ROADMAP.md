# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-13.

## 1. Open questions (correctness)

- **Rat-race isolation in openEMS** converges to a null at 2.24 GHz, against mom25d's 2.33, EMerge's 2.31 and
  the line model's 2.40 (the design): the staircased ring is electrically long, and refining doesn't remove it. Worth
  trying: openEMS's conformal or diagonal-edge options for curved copper, if they apply to sheets.

## 2. Line model

- **Vias** are the weak part of `rf_diffvia` above 5 GHz (worst Sdd11 −9.4 dB against openEMS's −15.4 dB):
  a coupled via pair in its antipads.

## 3. mom25d

- **Boards with planes are slow**: 8–23 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 417 s → 171 s). Left: modelling enclosed plane pairs as a
  parallel-plate stack instead of meshing them, which is research-level work.
- **Current dumps** cost one extra solve per frequency; the sweep's own solves rarely land on the dump
  frequencies.
- **Antenna mode** assumes an infinite ground, so antennas that use the board edge are out of reach, and
  via currents are left out of the far field.
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

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
