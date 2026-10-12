# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-12.

## 1. Open questions (correctness)

- **Coupler and grounded CPW: openEMS needs finer meshes than their configs had.** Coupler S31 at 2.4 GHz:
  −8.56, −9.21 and −9.93 dB at 1×, 1.5× and 3×, towards mom25d's −10.9 dB. Grounded CPW S11 at 5 GHz: −18.9
  and −26.1 dB at 1× and 2×, towards −31.4 dB. Their configs and reference results now use the 3× and 2×
  meshes. Neither is fully converged, so the reference results are still uncertain at the 1 dB level.
  The cost is a few minutes per run; a converged reference would be worth one long run each.

Settled:
- **Wilkinson isolation** (`rf_wilkinson`): openEMS −18.5 dB was the resistor overlapping track copper that
  reached into its gap, which openEMS's metal shortened (a 100 Ω resistor fed from the side measured 67 Ω).
  The element now spans the copper-free gap: openEMS −33.2 dB, mom25d −38.7, the line model −35.7.
- **Stopping on a static plateau** (low-pass, stub notch): the port criterion is converged there (stub
  notch: |ΔS| < 1e-4 against 3M steps). The lumped filters' looser `end-db = -27` left 0.03 in |S| and is gone.
- **Filter pass-band loss** (`filter`): it was openEMS's fixed dielectric model, whose loss is set at the
  band centre. With `--diel wideband`, openEMS's pass-band peak is −5.28 dB against mom25d's −5.23 dB
  (−6.52 dB with the fixed model), at four times the run time.

## 2. Line model

- Coupled sections are microstrip only: the stripline half of `rf_diffvia` runs as two uncoupled lines.

## 3. mom25d

- **Boards with planes are slow**: 7–25 times slower than openEMS on the CPU once inner planes and via rows
  must be meshed. The GPU solvers help (SMA launch 448 s → 159 s). Left: modelling enclosed plane pairs as a
  parallel-plate stack instead of meshing them, which is research-level work.
- **Current dumps** cost one extra solve per frequency; the sweep's own solves rarely land on the dump
  frequencies.
- **Antenna mode** assumes an infinite ground, so antennas that use the board edge are out of reach, and
  via currents are left out of the far field.

## 4. openEMS

- Several examples need finer meshes than the defaults give (the coupler, the grounded CPW, the radial
  stub, the rat-race's isolation). The automatic fine cell now follows narrow gaps and thin curved copper,
  but a per-structure check (`--converge`) is still needed. Worth making cheaper: a mesh refinement only
  around gaps and curves, rather than everywhere.

## 5. GUI

- A difference plot between two solvers (dB or |ΔS|).
- Mixed-mode traces in the trace list.
- The run's notes and warnings in the results.
- Interactive plots in the HTML report.

## 6. Examples and validation

- Reference results (`examples/reference/`) for the four new structures, on converged meshes.
- A script that regenerates the solver comparison over every example before a release.

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
