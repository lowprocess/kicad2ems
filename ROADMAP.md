# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-12.

## 1. Open questions (correctness)

Disagreements between the solvers that aren't explained yet. Settle them before trusting the affected
cases.

- **Wilkinson isolation** (`rf_wilkinson`): openEMS gives S32 −18.5 dB at 2.4 GHz; mom25d −38.9 dB and the
  line model −35.7 dB. The two that treat the resistor as ideal agree, so look at openEMS's resistor: the
  1 mm pad gap and the mesh in it. Sweeping the gap and `fine`, and testing the resistor alone in a two-port,
  would separate the causes.
- **Coupler coupling** (`rf_coupler`, 0.14 mm gap): openEMS −8.6 dB, mom25d −10.9 dB, line model −10.4 dB.
  It isn't copper thickness or the MoM mesh (−10.91, −10.89 and −10.88 dB at 0.3, 0.15 and 0.1 mm cells).
  Next: openEMS with a fine cell below 0.05 mm across the gap, and its port reference planes on the short
  4.3 mm feeds.
- **Grounded CPW return loss** (`rf_gcpw`): openEMS −18.9 dB, mom25d −30.1 dB with wave ports. Probably the
  lumped-port parasitics in openEMS; check with mom25d's lumped ports.
- **Filter pass-band loss** (`filter`): openEMS shows about 1.4 dB more loss at 3.3 GHz. Probably its loss
  model, fitted at the band centre; re-run with `diel = "wideband"` to confirm.

## 2. Line model

- **Stubs drawn as filled shapes over a track**: the overlap is taken for an 8 Ω section, putting the notch
  at 3.5 GHz instead of 2.4 GHz. Treat a shape crossing a track as a tee with a stub.

## 3. mom25d

- **Boards with planes are slow**: 7–25 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 448 s → 159 s). Left: modelling enclosed plane pairs as a
  parallel-plate stack instead of meshing them, which is research-level work.
- **Current dumps** cost one extra solve per frequency; the sweep's own solves rarely land on the dump
  frequencies.
- **Antenna mode** assumes an infinite ground, so antennas that use the board edge are out of reach, and
  via currents are left out of the far field.

## 4. openEMS

- The low-pass and stub examples stop at −24 to −28 dB on the port criterion. Check that their
  S-parameters have converged there.
- The automatic fine cell follows the narrowest trace, not the smallest copper gap, so the coupler's config
  sets `fine` by hand. Deriving it from the gaps too would make the default safe.

## 5. GUI

- A difference plot between two solvers (dB or |ΔS|).
- Mixed-mode traces in the trace list.
- The run's notes and warnings in the results.
- Interactive plots in the HTML report.

## 6. Examples and validation

- More structures where the solvers should differ: a rat-race, a radial stub, a via-fenced stripline, a
  differential pair through a via transition.
- A script that regenerates the solver comparison over every example before a release.

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
