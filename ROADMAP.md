# Roadmap

What is worth doing next, in priority order within each section. Each item comes from something the
examples or benchmarks showed. Last updated 2026-10-12.

## 1. Open questions (correctness)

- **Wilkinson isolation** (`rf_wilkinson`): openEMS gives S32 −18.5 dB at 2.4 GHz (its null shallow, at
  2.6 GHz); mom25d −40 dB (at 0.3 and 0.15 mm cells) and the line model −35.7 dB. Ruled out: openEMS's
  resistor (a 100 Ω resistor in series on a 50 Ω line gives −6.3 to −6.5 dB in all three solvers, along x
  and y), openEMS's mesh (−17.5 dB at 2× finer), and its boundaries (−18.7 dB with PML and 15 mm of air).
  An ESL would need about 3 nH to explain it in mom25d (1 nH: −27.6 dB). Next: compare the even- and
  odd-mode responses of the two arms between the solvers, to find which mode differs.
- **Coupler and grounded CPW: openEMS needs finer meshes than their configs had.** Coupler S31 at 2.4 GHz:
  −8.56, −9.21 and −9.93 dB at 1×, 1.5× and 3×, towards mom25d's −10.9 dB. Grounded CPW S11 at 5 GHz: −18.9
  and −26.1 dB at 1× and 2×, towards −31.4 dB. Their configs and reference results now use the 3× and 2×
  meshes. Neither is fully converged, so the reference results are still uncertain at the 1 dB level.
  The cost is a few minutes per run; a converged reference would be worth one long run each.

Settled:
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

- The low-pass and stub examples stop at −24 to −28 dB on the port criterion. Check that their
  S-parameters have converged there.

## 5. GUI

- A difference plot between two solvers (dB or |ΔS|).
- Mixed-mode traces in the trace list.
- The run's notes and warnings in the results.
- Interactive plots in the HTML report.

## 6. Examples and validation

- Run the three solvers on the new structures (`rf_ratrace`, `rf_radialstub`, `rf_stripline`, `rf_diffvia`)
  and add them to the comparison table in docs/examples.md.
- A script that regenerates the solver comparison over every example before a release.

## Shelved

- An Elmer FEM solver plugin (in a git stash): second-order elements needed over 21 GB on a simple microstrip
  line, and first-order ones weren't accurate enough.
