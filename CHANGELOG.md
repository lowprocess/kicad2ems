# Changelog

All notable changes to kicad2ems (formerly kicad2openems), and to the mom25d and gfdtd solvers while they
were bundled with it, by day. Releases are versioned `MAJOR.MINOR.PATCH` (semantic versioning) from 0.3.0
(2026-11-14), and tagged `vX.Y.Z`; before that they were named by datecode, `YYMMDD-rN` (for example
`261013-r1`, the first release on 13 October 2026).

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- **Decoupling chosen to a target impedance (`pcbqs.pdn --choose-decaps`, `kicad2ems.pcbqs.pdn.decoupling`,
  docs/pcbqs/decoupling.md, 2026-11-27).** The fewest capacitors (`--decap-mode cost`: the least relative cost)
  at a rail's decap sites that keep every load under its target over the band. The sites are its two-pad `C*`
  footprints from the tree to ground, fitted and unfitted (DNP: `Rail.sites`, `RailPDN(sites=)` as ports with
  nothing attached), or `--decap-sites`.
  - **Kept as fitted:** `--decap-keep`, a vendor file's part, a package with no MLCC in the library, the
    regulator.
  - **The library:** MLCCs 1 nF-100 µF (E3) by package, 0201-1210; ESR 12 mΩ (C / 1 µF)^-0.4; ESL by package; a
    relative cost; `--decap-library` CSV, `--decap-values` for a short BOM.
  - **DC-bias derating** (ROADMAP Power tools 5): 1 / (1 + (V / Vh)²), Vh = 2.4 V (Cmax / C)^¼, a quarter of the
    rating where higher, C0G none (`--derate-table` CSV replaces it). It applies to every MLCC while choosing,
    and alone with `--derate-bias`.
  - **The closed form:** the bare PDN between the loads and the sites comes from the impedance sweep's own
    factorisations (`RailPDN.sweep(between=)`), the fixed parts attached by Woodbury's identity. Each candidate
    part is a rank-one term, so no set is solved again.
  - **The search:** greedy on the excess (log |Z| / target over it, log-f weighted), then drop, two-for-one and
    move until nothing changes, from no part and from the fitted ones, the better kept. Frequencies where a
    load's own pads, or the floor (an ideal part at every site: mount and package ESL alone), are over the
    target are out of reach, left out and said.
  - **Results:** pdn.json's `decoupling` (each site's part and its capacitance at bias, a BOM, |Z| as fitted and
    as chosen, margins, the bands still over, out of reach), `decoupling.png`, three report sections and a
    curve set.
  - **The study:** a *Decoupling* question (Power, figures `parts` and `margin <load>`) answered by the
    power-integrity analysis. `Analysis.answers(values)` gives the questions a run of these settings answers,
    which the window's own Run marks: a rail run without `--choose-decaps` no longer claims the decaps.
  - **Checks:**
    - the closed form against a full solve with the chosen parts attached (`RailPDN.z`) at 16 frequencies and
      each load's worst: 1e-9 on the test board, 1.6e-8 and 7.7e-11 on the Jetson's rails;
    - the fitted set through the closure against the rail's own sweep (1e-7);
    - the rank-one terms against Woodbury's closure (1e-9);
    - the derating curve at its closed-form points (35 / 63 / 84 % at 3.3 V for the package's largest value,
      a decade and two below);
    - on the test board, 0.15 Ω to 20 MHz: the three fitted parts miss it (2.6×), the search meets it with two,
      and an exhaustive search over every one-part set finds none that does.
  - **Jetson AGX Thor carrier:**
    - +1V15 to U21 (115 mΩ): five parts against the thirteen fitted hold it to 43 MHz, out of reach above that
      (worst 0.976 against 0.967 of the target). The choice took 0.1 s of a 75 s run (DC 49 s).
    - +3V3 (J13, U21, J12, 110 mΩ): ten parts against 49 fitted, worst 0.978 (0.970 as fitted). The choice
      took 7.5 s beside a 22 s sweep.
  - **Speed, four passes** on +3V3's choice (49 sites, 3 loads, 160 frequencies; load average 5-9 from other
    runs), the result the same at each:
    1. the closure from the sweep's factorisations, and the check at 16 frequencies, not 160: 20.2 s to 0.8 s
       (the closure 12.3 s to 0.0, the check 7.8 s to 0.7);
    2. once the reach took the floor (more of the band left to search: 14.1 s, the search 13.2 s), trial moves
       on the loads' rows and the diagonal alone, O(f nl k) in place of a full rank-one update, O(f k²): 14.1 s
       to 5.6 s;
    3. the trials on |Z|² in the batch's own layout, and the worst alone where only feasibility is read: 5.6 s to
       4.2 s;
    4. the reciprocal at the smaller shape: 4.5 s, no gain, rejected.

    The peak memory of the choice is 72 MB (tracemalloc) throughout. `between=` costs the sweep nothing
    measurable (15.3 s against 15.7 s, 60 MB against 53 MB peak).
  - **Tests:** tests/pcbqs/test_pdn_decoupling.py.
- **Radiated emission and susceptibility (`kicad2ems.core.emission`, `kicad2ems.core.susceptibility` on
  `kicad2ems.core.radiated`; docs/emc.md; 2026-11-27).** Two study questions in the EMC group, both from each
  port's far field on mom25d. Each frequency is one factor with every port's source; with each port on its load
  (its reference or `--load`), V = (1 + Z Y)^-1 V_s, so each port's terminated currents per volt are one product.
  An antenna run's array file (`--array`: mom25d's Y form, or an FDTD solver's S form with every port excited)
  stands in at its own frequencies.
  - *Susceptibility:* by reciprocity, V_j = 4π / (j k0 η0) z_j E0 (p · F_j). It takes the worst over every
    direction and linear polarisation, at IEC 61000-4-3's 3 and 10 V/m (80 MHz–6 GHz), with the 80 % AM peak
    and a pair's differential voltage, and flags ports over `--threshold` mV.
  - *Emission:* `--drive` ports driven by a clock (trapezoid lines), data (a PRBS's lines, which are exact for an
    m-sequence, or random data's PSD in the receiver's 120 kHz / 1 MHz), a CW sweep or a spectrum file. The site
    is 3 or 10 m with the floor's image below 1 GHz (direct only above), heights 1–4 m, the turntable round and
    the board in six orientations, maximum held. Each line gets a margin against CISPR 32 class A / B
    (quasi-peak, average, peak).
  - Both say that cables, the board edge and anything below mom25d's infinite ground are left out.
  - *Checks:* a short top-loaded element 1 mm over the ground (air between, its effective length 2h) has a far
    field η k I 2h / 4π to 1e-6, D 3.000 and efficiency 1.0000. It receives E0 2h sin θ z / (z + Zin) to 3e-4.
    An independent plane-wave solve of the same MoM system (the incident field and its image tested on every RWG
    and via) agrees to 1e-6. Two of them 3λ apart couple as Friis says from their solved gains (|S21| = √(G1 G2)
    λ / 8πR along the ground) to 0.014 dB (0.0017 dB at 10λ). Its field at 3 m matches to 1e-6. The site
    matches a dipole and its image to 1e-12. A clock's and a PRBS's lines match their waveforms' Fourier series
    (1e-4, 2e-3). An array file gives the solve's result to single precision.
  - *Found and fixed on the way:* the far field's 3° grid had missed its fall to 0 within a degree of grazing over
    the grounded dielectric, by 43 % of the largest on 30 mm of microstrip at 4 GHz. The grid now takes 14 steps
    toward 90° (within 1.1 %).
  - *On rf_serpentine* (examples/emc_serpentine.toml, 2.8k unknowns): a 25 MHz 3.3 V clock is +5.8 dB under
    class B at 3 m (225 MHz), in 27 s. 10 V/m puts up to 36 mV on a port at 6 GHz, where the line radiates 8 % of
    what it carries, in 24 s.
  - *Speed and memory* (rf_serpentine's emission, 4 threads, machine load 10-15 from other runs):
    - 35.9 s and 918 MB before, 27.7 s and 814 MB after.
    - Frequencies: adaptive, 15 in log f then halving where a sample lies 0.5 dB off its neighbours' line, within
      30 dB of the top. Against 241, the largest shortfall is 0.67 dB with 41 (2.23 dB with 61 equally spaced) on
      rf_serpentine, and 0.25 dB against 2.26 on rf_branchline: a third fewer solves, and closer.
    - `mom25d.farfield`'s phases are made in blocks of 1M numbers (8M peaked at 896 MB against 749) on threads
      (NumPy's exp holds one core): the far field per frequency 0.132 s → 0.062 s, the same to round-off.
    - `far_field_dirs` takes paired directions; far_field is its grid.
    - What is left is mom25d's own per-frequency fill and LU, 86-88 % of either run.

  Tests: tests/core/test_radiated.py, tests/mom25d/test_radiated.py.
- **Line parameters: R, L, G, C per metre over frequency with the skin and proximity effect (`kicad2ems.core.rlgc`,
  `kicad2ems.core.lines.mqs`, docs/rlgc.md, 2026-11-27).**
  - *The solver.* A 2D eddy-current cross-section solve beside xsection's electrostatic one: A_z with the copper
    meshed inside, xsection's bilinear rectangles and hanging nodes, cells a third of a skin depth at every copper
    face (at the band's top), growing inward and along it. Each conductor's total current is imposed (the ground
    copper one net). Z = R + jωL comes per frequency from one complex symmetric factorisation and an (N+1) ×
    (N+1) closure; at DC the currents are uniform. The planes are copper of their layers' thickness behind their
    faces, and the return crowds under the lines as the frequency rises. L is xsection's extrapolated L_inf plus
    the internal inductance (the solve's L less its own perfect-conductor limit on the same mesh), so Z0 and the
    delay stay the field solver's; G and C are xsection's, and `--diel wideband` solves them at each frequency's
    materials. `--rough` uses σ / K(f)² at each frequency.
  - *Checks.* A coax as staircases against the Bessel forms of the wire and the tube, DC to a / δ = 5: R within
    0.55 %, L within 0.2 %. A strip's DC resistance exactly. (L − L_inf) ω / R 0.985–1.005 at 1–10 GHz on a 50 Ω
    microstrip. Wheeler's incremental inductance on the field solver: 65.6 against 64.9 Ω/m, the strip's share
    0.747 against 0.741. A centred stripline's conductor loss against Pozar's closed form (Wheeler's rule) within
    0.6–1.6 % at w 0.15–0.6 mm. A pair's R and L symmetric and positive definite, R(10 GHz) / R(2.5 GHz) 2.02.
    The losses add up to R to 1e-9.
  - *F90's reference.* mom25d's thickness-loss microstrip (w 0.383 mm on 0.2 mm, εr 4.4, 35 µm) has R = 64.9 Ω/m
    at 10 GHz: 0.00589 dB/mm, the strip's 0.00437, against the 0.00935 of Hammerstad and Jensen's formula, which
    is 1.6× high. The ROADMAP's mom25d Accuracy 1 has the comparison with each thickness rule.
  - *The question.* *Line parameters* (Signal integrity) on a board's nets by name patterns: each distinct cross-
    section of each coupled group and single trace, cut as the line model cuts the board, with lengths added and
    mirror images solved once. It writes each section's W-element RLGC file (HSPICE `RLGCfile=`, Rs and Gd fitted),
    its tabular model (`MODELTYPE=TABLE`, `sp` models), its S-parameters over its length (`coupled.zy_s`: any
    RLGC), a figure, and `rlgc.json`. Also `--xs microstrip|stripline|file.json` for a cross-section by its sizes.
    On rf_diffvia (D_*, 6 sections, DC + 22 frequencies to 20 GHz) it runs in 3.1 s; on rf_ddr (DQ*, 20 sections)
    in 4.2 s, the machine shared with four other runs (load 7–30).
  - *Optimisation passes*, under load averages of 7–30 from four other runs:
    1. The frequencies on threads (SuperLU lets go of the interpreter). `rlgc()` over DC + 22 points went from
       0.294 / 0.433 / 0.497 / 1.393 s to 0.204 / 0.305 / 0.353 / 0.800 s (microstrip, pair, stripline pair, 8-line
       bus), results bit-identical. Peak memory went from 25 / 27 / 30 / 45 to 37 / 47 / 57 / 107 MB (tracemalloc).
       On rf_ddr, 1 / 4 / 8 threads take 11.2 / 5.6 / 3.8 s at 132 / 280 / 432 MB peak RSS.
    2. Fixed figure margins in place of `tight_layout` (0.24 s a figure): rf_diffvia 3.92 → 3.11 s wall.
    3. A cross-section and its mirror image as one solve (R the same to 3e-10): rf_diffvia's solves 2.50 →
       2.17 s, min of 3.
    4. Figures for the 12 longest sections alone (a figure takes about as long as a section's solve): rf_ddr
       5.76 → 4.16 s, min of 3 (spread 4.2–7.1 s).

    Measured and set aside (ROADMAP Signal integrity 8): a reduced model over frequency, the ordering reused, and
    a figure's minor grid. Tests: tests/core/test_rlgc.py.
- **Conducted emission (`kicad2ems.pcbqs.conducted`, docs/pcbqs/conducted.md, 2026-11-27; the user's ask, item 5's
  first half).** A circuit model (pcbqs.circuit, ngspice, on its board's copper with `--copper`) with a CISPR
  network in each supply line: `cispr16`, CISPR 16-1-2's 50 Ω / 50 µH + 5 Ω V-network (CISPR 32's), realised from
  its impedance 50 ∥ (5 + jω 50 µH) within 0.9 % from 150 kHz to 30 MHz (15 % at 9 kHz, the standard allows 20);
  `cispr25`, the 5 µH / 50 Ω network, within 5 % to 108 MHz. The source's minus is the reference plane, the return
  line has its network too; on a board the networks' EUT ends take the source's footprint pads. Each line's voltage
  at its EUT terminal (the receiver's reading corrected by the voltage-division factor) over the last period as a
  line spectrum, with the differential (V+ − V−)/2 and common (V+ + V−)/2 modes; read by a CISPR 16-1-1 receiver
  (bands A, B, C: RBW 200 Hz / 9 kHz / 120 kHz, a Gaussian IF filter, the envelope of a period exact in the steady
  state; peak, average, and quasi-peak as the detector's charge and discharge run to their periodic state and read
  by the critically damped meter); against CISPR 32 class A/B (QP, average) or CISPR 25 class 1-5 (peak, QP,
  average in LW, MW, SW, CB, VHF, FM), or `--limits FILE`: each band's margin, worst frequency, `conducted.csv`,
  `conducted.png`. The common mode's path: each model node's copper to a plane 50 mm (CISPR 25) or 400 mm (CISPR
  32) below the board from pcbqs's 1/R moments with the plane as images (`capacitance_to_plane`; a 20 mm plate
  alone: Hwang and Mascagni's unit square within 0.6 % at a 1 mm mesh), and `--cm-cap NODE=VALUE` (a heatsink). A
  study question (*Conducted emission*, group *EMC*), its figures margin, at, margin peak / QP / average.
  Checked: a 1 V square wave with 5 ns edges behind 100 Ω into the networks against its Fourier series times
  Z / (R + 2Z): within 1.5 % to 30 MHz (cispr16), 0.8 % to 108 MHz (cispr25), common mode below 2e-8 of the
  differential; a square wave through 100 pF to the reference against the closed form of the two networks in
  parallel: 2.2 %; CISPR 16-1-1's quasi-peak calibration (impulses of 13.5 / 0.316 / 0.044 µVs at 25 / 100 / 100
  Hz read 66 ± 1.5 dBµV): 65.6 / 66.6 / 65.9; peak and average exactly the Gaussian filter's 2A × impulse bandwidth
  and 2A × rate; band B's rate response 70.5 ± 1 / 59.5 ± 1 / 56 ± 1.5 dBµV at 1000 / 20 / 10 Hz: 71.2 / 59.1 /
  54.6 (2 and 1 Hz read 2.1 and 2.5 dB under the standard's, just outside its ±2 dB: not found why). On the buck
  example (12 V to 5 V, 2 A, 250 kHz, no input filter) with a 15 pF heatsink on its switch node, CISPR 32 class B:
  −32.6 dB, average at 250 kHz (84.4 dBµV, the differential mode's 82.3); −25.9 dB at 5 MHz, where the common mode
  is up to 73.1 dBµV (2 MHz); with `--copper` −34.2 dB at 4.5 MHz; CISPR 25 class 5: −51.4 dB at 76.75 MHz.
  Speed (four passes, the buck with its heatsink, the machine shared with four other forks' tests, load average
  6-11): 21.2 s, 1018 MB Python peak (1.66 GB RSS), 4550 periods → 11.3 s, 549 MB (the circuit alone run first and
  the networks started where it settled: their inductors at its input current, their capacitors at its DC) → 7.4
  s, 301 MB (the networks' and common-mode capacitors' states not judged for repeating: their 2-4 kHz modes are
  below the band, a return-line capacitor's swing near nothing) → 7.0 s (no strict reported run of the circuit
  alone) → 5.5 s, 145 MB (285 MB RSS), 200 periods (nor their stored energy); the readings within 0.007 dB of the
  DC start's on L+ and L−, 0.003 on CM, 0.08 on DM's weakest lines. CISPR 25 to 108 MHz: 7.8 s. The receiver on
  an unresolved spectrum (lines every 1 kHz to 30 MHz, 39.8k tuned frequencies): 5.10 s → 1.89 s (the quasi-peak's
  envelope at 4 × its lines in the filter, not 8; batches of 2^21 samples: 97 MB) → 1.46 s (each row's fixed point
  by a secant from the averaged detector's state, settled rows left out, to 1e-4) → 1.09 s (16 bisection steps
  for that start); QP within 0.024 dB of the first's, the calibration unchanged to 0.01 dB. Also, in the circuit
  engine: each case's `end_state` in its row, `run(..., starts=)` to begin a case there, `analysis.final_always`
  (the reported run made even when the search ended at the strict tolerance: the buck's spectrum had come from the
  search's 256 samples a period, its 5-30 MHz lines 26 dB low), `analysis.ignore` counting a named part's stored
  energy out of the settling test too, and a circuit that stores nothing no longer fails on an empty sum. Test:
  tests/pcbqs/test_conducted.py.
- **The DC drop at temperature, and the board's temperature over time (`kicad2ems.pcbqs.electrothermal`,
  `pcbqs.thermal.transient`, docs/pcbqs/electrothermal.md, 2026-11-27).** A rail's DC drop (pdn's: the tree and its
  return, the vias' barrels) puts its loss, each triangle's at its edge midpoints and each barrel's at its via, into
  the thermal grid beside the parts' (`--heat REF=W`, `--loads-heat`: each load's V x I; the series parts' I^2 R on
  their pads), and the temperature sets each triangle's and barrel's resistivity, rho_20 (1 + 0.00393 (T - 20 C)),
  for the next DC solve, until no triangle moves by more than 0.05 K (the thermal system factored once). It
  answers a new study question (*Drop at temperature*, Thermal) and is a second engine of *Rail drop* (`pcbqs (at
  temperature)`), so a study sets the drop hot and at 20 C side by side; figures drop / drop with return / drop
  cold per load, hottest, loss with return. `--transient S` with `--current-profile SHAPE` (the copper's and the
  series parts' loss by its square) and `--profile REF=SHAPE`: the temperature over time, the copper's loss
  following each point's temperature about the hot current pattern (linear in T: A - alpha diag(W') in the
  stepping matrix). The thermal tool's transient is now TR-BDF2 (second order, L-stable) with each part's loss by
  a profile (`step[:A,B][@T]`, `off@T`, `pulse:ON,OFF[,LOW,HIGH][@T]`, a file of t, W), from cold or from the
  steady state before t = 0 (`--start steady`), the board's longest time constants (C v = tau A v), each part's
  peak; no step crosses a profile's break, step lengths on a 4x ladder so a pulse train reuses its factors.
  Checked: a 60 x 20 mm sheet heated evenly by its own 25 A (the coupled closed form T - Ta = P0 (1 + a (Ta - 20))
  / (H A - a P0)): 70.326 C against 70.329, the drop within 5e-5, five passes each 0.15 of the last (the loop's
  gain); a 1 mm trace at 2.4 A across a 60 mm plane-less board against the two-region fin: 8.80 K against 8.81
  (IPC-2221's external-trace fit 10.1 K, a sanity bound); `--alpha 0` the DC tool's drop bit for bit and the
  thermal tool's map to 1e-9 K; the transient's frozen pattern ends 2-30 mK from the passes'; a lumped RC within
  5e-4 of its closed form (a self-heating one 1e-3), a plate's single mode through a pulse train, a cooling and a
  load step within 1 %, its time constant to 1e-6 and its 63 % time to 1e-3 (backward Euler's: 2 % late). On the
  synchronous buck's board (05_buck_synchronous, `out` at 5 A, Q1, Q2, L1 at 0.25, 0.2, 0.3 W): copper at
  80.0 C, the drop at J2 6.65 mV against 5.52 at 20 C (+20.5 %), the copper's loss 33.3 mW against 27.6; three
  passes, 0.3 s. Also: `pdn.dc.rail_networks` / `solve_networks` (solve_rail in two), `Network.conductivity`
  (a factor per triangle and barrel) and each triangle's loss (`Sheet.P`); `pdn.cli.parse_loads` / `read_rail`
  (the rail setup the two tools share); the thermal grid built once for any heats (`thermal.grid.build`,
  `Grid.place` for heat at points, `Problem.heat_points`). Tests: tests/pcbqs/test_electrothermal.py,
  tests/pcbqs/test_thermal_transient.py.
  Speed (three passes on the Jetson carrier's SYS_VIN_HV at 6 A, --dmax 3 --size 1: 1.14 M DC unknowns, a 179,000-
  unknown 10-layer grid; the machine shared with four other runs, load 2-21): (1) the copper's loss points joined
  once (the barrels' had been appended one by one, copying 6.6 M points for each of 1456 vias: 154 s) and the vias'
  temperatures a layer at a time: the run 235 -> 138 s, the coupling 135 -> 34 s, results to 5e-12; (2) the
  viewers' JSON by the C encoder (json.dumps; json.dump to a file takes the pure-Python one): their four files
  10.7 -> 3.2 s, the same bytes; (3) the transient error-controlled (Hosea and Shampine's estimate, a ladder of
  powers of two, factors kept within a memory budget) and, above 40,000 unknowns, run as a reduced model (a
  rational block Krylov basis, six moments at the steady state and at two shifts, PRIMA's congruence,
  diagonalised): with --transient 1200 and 60 s bursts every 300 s, the transient 244 -> 20 s, the run 357 -> 118 s
  and its peak memory 18.9 -> 4.7 GB (it had kept 20 factors of 0.8 GB), within 0.1 mK of the grid stepped itself
  at every break, the time constants 135.4, 78.1, 41.9 s; on the test boards the error control takes 100 -> 46
  steps for the heating from cold and a pulse train 1898 -> 260 with its error 0.13 -> 0.001 K.
- **Jitter, noise and BER contours in the time-domain question (2026-11-25; docs/timedomain.md#ber).** The
  statistical eye of a lone bit's response at the far end (`timedomain.stat_eye`): every pattern of the other bits
  (each cursor's two-point distribution convolved on a grid, its mass split between neighbouring levels so the
  tails hold to 1e-15), Gaussian noise at the receiver (`--noise`, mV RMS), and jitter on the sampling instant
  (`--rj` ps RMS about `--dj`'s dual-Dirac, ps peak-to-peak): BER contours (1e-3 to 1e-15), the eye's height and
  width at `--ber` (1e-12), the bathtub, TJ = DJ + 2 Q RJ; `<name>_ber.png`; the report's section and the study's
  figures (eye height and width at BER, TJ). Exact for linear buffers, an IBIS pair's linearization about the lone
  bit (said). Checked against closed forms: noise alone A − 2σ Q⁻¹(2 BER), random jitter UI − 2σ Q⁻¹(2 BER), with
  a dual-Dirac UI − DJ − 2σ Q⁻¹(4 BER); with neither, the worst pattern's height. On rf_diffvia's pair at 25 Gb/s
  (0.8 V, the pattern's eye 110 mV by 35 ps): 104 / 44 / 95 / 41 mV by 36 / 23 / 24 / 17 ps at 1e-12 with none, 5
  mV noise, 0.6 ps RJ and 4 ps DJ, both. Also: the macromodel's fit, passivity and stepper hold BLAS to one
  thread (OpenBLAS's 16 had spun on their small QRs: a 4-port's fit 5.7 s and 110 s of CPU, now 0.55 s; a
  time-domain run 9.5 s to 2.2 s). Test: tests/core/test_timedomain.py.
- **The time-domain question: a channel's TDR, step response and eye (`kicad2ems.core.timedomain`,
  docs/timedomain.md, 2026-11-25; the user's ask, with a macromodel and IBIS drivers).** A channel's S-parameters
  (a Touchstone file, a run's folder, the study's S-parameter run, or the board solved here on the line model or
  mom25d down to fmax / points) through `core.macromodel`'s passive model, driven and received by ideal buffers (a
  source behind its resistance; z0, R to Vtt beside C, open) or IBIS ones (`core.ibis`), a pair by complementary
  drivers: the TDR (the impedance along the channel), the step at the far end (delay, rise, overshoot), the eye
  for a PRBS at a data rate (inner height at the best phase, width at the threshold, levels) and every other
  port's peak during it. A study question (*Time domain*, Signal integrity; engines: the line model, mom25d), its
  figures eye height and width, TDR min and max, delay and rise; `runs.latest_results` gives the study's
  S-parameter run as `sparams`. On rf_msvia (the line model from 20 MHz, 0.7 s): TDR 46.7-50.3 Ω (the via's dip),
  delay 144 ps, eyes of 495 / 485 / 262 mV by 200 / 100 / 47 ps at 5 / 10 / 20 Gb/s. Checked: a 50-75-50 Ω line's
  TDR at 75 Ω (2 %), a pair at 100 Ω; the PRBS7 eye of a lossy line at 8 Gb/s the peak-distortion analysis' of
  its own step response (2 % of the swing); IBIS buffers through a pair end to end; a board solved on the line
  model. Left (ROADMAP, Signal integrity 1): jitter and noise, equalization and IBIS-AMI, a floating differential
  termination. Test: tests/core/test_timedomain.py.
- **IBIS buffers as the channel's terminations (`kicad2ems.core.ibis`, 2026-11-25; the time-domain question's
  second part).** `read`/`parse` take an IBIS file's [Model]s (Model_type, Polarity, C_comp, the references and
  [Voltage Range], [Pulldown], [Pullup], [GND Clamp], [POWER Clamp], [Ramp], [Rising/Falling Waveform] with
  their fixtures), its [Pin] table's R/L/C and [Package], typ/min/max with NA as typ's and the scale letters.
  `Driver(model, bits, ui)` is the buffer at its die (Ku(t) Ipu(Vcc − V) + Kd(t) Ipd(V) + the clamps + C_comp)
  behind its package (R and L in series, C at the pin), its switching from two V-T tables an edge (both
  coefficients) or one or [Ramp] (Kd = 1 − Ku), each new edge started where its Ku is the present one;
  `Receiver(model)` its clamps and C_comp; both terminations for `core.macromodel.simulate`. Checked: a
  resistive buffer from [Ramp] through a 50 mm line as a Thevenin source of Ku(t) Vcc behind its resistance (to
  1e-6), its edge into R_load the [Ramp]'s slope (2 %); Ku and Kd made not complementary found again from two
  V-T tables (1e-3); a 2 pF input's rise 2.2 RC (10 %); a package's resistance in the DC level, an inverting
  buffer. Not read: [Driver Schedule], [Submodel], EBD, [Model Spec]. Test: tests/core/test_ibis.py.
- **A passive rational macromodel of S-parameters, and its transient (`kicad2ems.core.macromodel`, 2026-11-25;
  the time-domain SI question's first part).** `fit(f, S)` is relaxed vector fitting with every entry on common
  poles (the fewest of 8, 12, 16 ... within 2e-3 RMS of the largest |S|), stable poles (a causal model), and a DC
  point where the samples reach low enough (Re S even in ω: the constant of a + b ω² on the lowest samples).
  `Model.passive()` checks the largest singular value on a dense grid to 3 × fmax and at each pole;
  `Model.enforce()` projects the model's samples there onto passivity (each singular value above 1 − 1e-3 taken
  down to it) and fits the residues and D to them again, a small leftover scaled away. `simulate(model, terms, t)`
  steps it exactly for an input linear over each step (each pole's state: recursive convolution, b = G a + h)
  with a termination at each port (`Thevenin`, `Load`, or a nonlinear one: Newton), every port's voltage and
  current. Checked: a known 10-pole model found again to 1e-8; a lossy 100 mm line in 64 poles (RMS 8e-4,
  passive as fitted, S21(0) 0.998); a lossless line made passive within 1e-3 of its samples; its step into a
  matched, an open and a 5 Ω end at 0.4995, 0.999 and 0.0908 (0.5, 1.0, 0.0909), the far end's half at the delay
  and half the edge, to a 2 ps step; the lossy line's far end within 0.5 % of the swing of its closed form's FFT.
  Test: tests/core/test_macromodel.py.
- **segment `--cavities`: the board's plane-pair cavities in the join (2026-11-24).** The physics-based via models'
  planes were infinite; now each leaves each cavity its vias cross to the join (`viamodel.build(cavities=)`: each
  barrel segment in it a port, + at its upper node, the box's ground vias there not modelled), and each cavity is
  one block between every via model's ports in it, Z = W Zc W^T: Zc the board's cavity between those vias at each
  frequency (`Cavity.impedance`: jw mu0 d (K - k^2 M)^-1 at their discs, every ground via on the board a short,
  k^2 with the dielectric's and the planes' loss), W each segment's share of the cavity's height. The via model
  now gives S with every port terminated (2 Z_L / z0 - I): a stripline pad between two cavity ports has no other
  path, and open-circuit Z none (the same S as before to 4e-14 without cavities). Checked: leaving a cavity to
  the join and joining the infinite planes' coupling back is the model to 1e-15 (rf_msvia, rf_diffvia); on
  rf_msvia with its ground vias 4.5 mm out (the cavity 100 ohm at the via at 7.9 GHz) the whole board's S21 notch
  (mom25d -7.1 dB at 7.95 GHz, gfdtd -7.0 at 7.90), which the infinite planes miss (-1.2 dB at most), is -7.4 dB
  at 7.95 GHz with the cavity, S21 0.24 dB rms from mom25d's whole board over 1-10 GHz (0.85 without); rf_msvia
  itself (ground vias at 1 mm) unchanged (0.117 / 0.120 dB rms from gfdtd's). On the Thor carrier's pairs: 56 min
  against 25 (the five cavities' blocks 26 min: a sparse factor of 85-94k unknowns at each of 201 frequencies);
  83 of 93 channels within 0.1 dB of the infinite planes' (median 0.005 dB), the worst NEXT 0.835 -> 0.837 %; the
  ten that move have vias 1-2.5 mm from a ground via (`USBC4_D` 3.1 dB at 9.85 GHz; `PCIe_{C2x1}.CLK` -6.27 ->
  -3.78 dB at 10 GHz, the power the infinite planes had taken as radiated away coming back). Left: the
  cavities' cost on a large board, the notch's upper flank (-1.9 dB at 8.5 GHz against the whole board's -1.0),
  the meshed via models (their boxes kept).
- **Plane-pair cavities and their resonances (`kicad2ems.core.cavities`, 2026-11-23; the user's question: resonances
  from signal and ground via placement).** Each pair of adjacent reference planes a 2D cavity (the voltage between
  them, -div grad V = k^2 V over the area both cover, open edges; the PDN tool's mesher and matrices, the consistent
  mass matrix); a via touching both planes' copper shorts it (V = 0 on its disc), any other through it drives it;
  the modes below fmax by shift-invert Lanczos, each one's Q (tan d, skin depth) and extent, and for each signal via
  the cavity's impedance at it at each resonance (the mode's term of the modal sum) and its nearest ground via.
  `python -m kicad2ems.core.cavities board --fmax 10 --nets ... --out cav`: a report, `cavities.json`, mode maps.
  Against closed forms: a rectangle's modes within 0.5 %, a shorting via's modes (the zeros of the open
  rectangle's impedance there) within 2 % (its LC mode 4 %), a peak impedance within 10 %. On the reference boards
  in 17-71 s: ThunderScope's and LibreVNA's signal vias each 0.6-1.0 mm from a ground via, no mode in band reaching
  them; the Thor carrier's fast lanes at most 22 ohm above 8 GHz (In4.Cu/In6.Cu round the In5.Cu striplines), its
  slow USB 2 and DP AUX lines up to 203 ohm at 7.8 GHz in a pocket by the USB hub with few ground vias. `--suggest`:
  for each flagged via a ground via at the nearest place on both planes' copper and the peak then (the cavity solved
  again with them): the Thor carrier's 13 flagged vias each 0.7 mm from one, every peak then 0-16 ohm.
  Against Palace's 3D eigenmodes on rf_msvia (a signal via through In1.Cu/In2.Cu inside four ground vias): the six
  cavity modes Palace finds (Q 52, the dielectric's) within 2.5 % of the 2D model's, in 34 min against under 1 s.
- **segment: a piece's file carries the primitives near it, not the whole board's (2026-11-23).** Each piece's
  board file had held every primitive of the board (a Thor window's 34.6 of its 35 MB: the planes of nine other
  layers, 1,206 footprints), read by its run and written and hashed by the parent; now those within 3 mm of its own
  copper and ports (`_near_prims`; a via model's: its box), what its run reads (its ports' nets and references, the
  line model's strips). The Thor carrier's piece files 34.7 -> 12.7 MB (median), its meshed pieces' processes 34.6 ->
  25.2 min, its via models 3.2 -> 1.6, the run 28:14 -> 24:33, every channel's figures as before (one,
  `/SoM_IO2/USB2.D`, moves 0.01-0.04 dB from run to run alike).
- **mom25d: the loop/tree transform's steps read a row's own part where it is (2026-11-23).** `lt_half_step`
  copied each row it takes whole into its group's buffer (step 2: the tree's 23k rows of LimeRFE's 33k-unknown
  piece, ~12 GB of copying); only the part before the row's block (its mirror's, strided) is gathered now, the
  rest read in place (each step writes only pairs no other row reads in it). Bit for bit the same; the three
  steps on that piece's tree 2.31 -> 1.72 s (step 2 1.26 -> 0.96 s), on an 8.9k piece's 183 -> 149 ms.
- **A run's own work around the solve, 6x less on a segment piece (2026-11-23).** A Thor bus window (92 ports, 8k
  unknowns) took 107 s, of which its 10 field solves were 9.3: the causality check 46 s, the mixed-mode conversion
  31.5, the board file written again 10.6, the board as data four more times 3.7. Now 16.6 s, its S the same to
  1.2e-8 (the GMRES tolerance's); on the Thor carrier the pieces' processes had been 44.6 min for 20.9 of solving.
  - `causality.check` with every term at once: a term's reconstruction is `R1 h + R2 conj(h)`, its matrices and
    bound the band's alone (`_operator`), so all terms are a few products a subtraction count (a term at a time it
    had been 29,967 calls, each computing Gauss-Legendre's nodes again); the same figures to 1.5e-15, a 36-port S
    6.35 -> 0.02 s.
  - `sparams.mixed_mode` as products (`Mx @ S @ Mx.T`; a three-operand einsum ran over every (i, j, k, l)): a
    92-port S 30.1 -> 0.04 s, the same to 1e-15.
  - a lean run of a board file in its own output folder (a segment piece's) does not write it again; mom25d takes
    the board without its primitives (`Board.to_dict(prims=False)`: it reads none, and a piece carries the whole
    board's, 34.6 of its 35 MB).
- **segment: the pieces whose every channel is open end to end are not solved (2026-11-23).** `unrouted` already
  found, before any solve, the channels with no path through the pieces; a piece whose every net is such a
  channel's would give only their own open S and their coupling to one another (pieces apart do not couple), so
  `open_pieces` leaves it out (said, `--solve-open` to solve it anyway); a kept piece's cut to one left out ends
  open, as a leg with no path on already did. LimeRFE's run of the night before: 74 of its 317 pieces, 18.5 of
  its 91 min of solving, five via models of 25-29k unknowns among them (the largest pieces, and so the peak
  memory, are live ones: unchanged). No such piece on the Thor carrier, LibreVNA or ThunderScope. Asked whether a
  line-model pass should find the open traces first: their openness is the connectivity's, known for free; the
  channels that read open only after the solve (LimeRFE's 145 and 220 MHz filters' inputs, chains through
  parts the model does not have) are joined copper whose coupling is wanted, and no cheaper model knows them.
- **mom25d (GPU): the refinement's products in float-float (2026-11-23).** The double half on the GPU is made hi +
  lo (two floats a number, ~48 bits) in place after its upload, the vectors split on the host, and its products
  run on the single-precision units with error-free products and sums (Dekker, Knuth; FMA where `ff_probe` finds
  the driver's float fma fused, Veltkamp's split where not: RADV's is not). Both drivers folded the error terms
  away (RADV even z - double(float(z))), with `precise` and slang's precise mode alike: each step's result passes
  through an opaque XOR with a run-time zero. Within 1.4e-13 of double on both GPUs; RX 7800 XT 1.37-1.43x the
  double kernels from 8 to 106 columns (20k unknowns, 106 columns 0.757 -> 0.545 s), GTX 980 Ti 1.2-1.5x (tiles 2
  x 8); used from 8 columns (`MOM25D_VK_FF_MIN`; below it the products are bound by memory, the double's no
  slower), `MOM25D_VK_FF=off` keeps them double. With it the policy keeps Z on the GPU with one factor beside it
  rather than several factors without it (`GPU_DENSE_FIRST`): a GMRES iteration (the refinement's, a reused
  factor's) tens of ms there against ~0.45 s on the host at 29k.
- **mom25d: a solve's matrix built faster and in less memory (2026-11-23).** Measured on a LimeRFE 6-layer via model
  (29,363 unknowns, the whole stack) and its 33k-unknown F.Cu piece:
  - the GPU's LDL^T from the double half itself (`gpu_ldl.upload_c64`: converted a range of rows at a time into
    the staging buffers): no complex64 half on the host, the hybrid half's host 8 N^2 rather than 12
    (`HALF_STREAM`); the 33k piece had peaked at 15.5 GB with the copy;
  - the LU of an LDL^T without a factor from the double half set whole on the GPU (`factor_from_half`): its
    complex64 half and whole copy had been 12 N^2 more on the host, the 33k piece OOM-killed at 21.9 GB there;
  - the Green tables' Hankel transforms one call a frequency (`GreenTableML.batch`: every pair's contour and rho
    the same): 21 tables 3.6 -> 0.65 s at 0.1 GHz;
  - the stack's transmission-line network kept (it depends on no interface: every pair's voltage had made it
    again): the via kernels 2.3 -> 0.65 s a frequency;
  - the half scaled in one pass of the core (`half_scale`, real or complex), not two of NumPy's; the loop/tree
    transform's passes reading only the rows they take.
- **mom25d: the block low-rank operator by its tiles on and above the diagonal (2026-11-23).** Z, its near field
  and (checked) its extras are symmetric, so the GMRES operator of the block low-rank solve (double, to 1e-8) now
  keeps only the tiles on and above the diagonal and applies the others as their mirrors' transposes (`matvec`
  a pass by tile rows, then one by tile columns, each thread its own block; D^T, or V^T U^T): a LimeRFE piece of
  9,341 unknowns at 2 GHz 0.43 GB against 0.82 (31 % of dense complex128 against 59 %), assembled in 0.9 s
  against 1.6, its products the same (0.030 s against 0.026 for 4 columns) and the full operator's to 1.2e-14.
  `MOM25D_BLR_OP_SYM=off` keeps every tile.
- **mom25d: the loop/tree basis and the sides' loops on the matrix's half itself (2026-11-23).** The loop/tree
  basis (low frequency) had filled and transformed the whole double Z, 16 N^2, before halving it; now its far and
  near fill go straight into the half (`fill_far_half`, `near_into_half`), the vias' fan paths are a border
  transform (`Half.loop_transform`: T = I + W with W in some columns, only their rows and columns change), and
  K^T Z K is three passes over the half (`looptree.transform_half`, the core's `lt_half_step`): with K = I + P,
  M_tt = Z_tt, M_tc = Z_tc + Z_tt P, M_cc = Z_cc + Z_ct P + P^T M_tc, each pass reading one region while it
  writes another, rows in parallel; the charge block into the pairs the half keeps (`charge_into_half`). Thick
  copper's sides' loops likewise (`Half.loop_transform`). Peak 8 N^2 (and the verticals' columns), host 12 N^2
  with the factor's copy, not 16: a LimeRFE piece of 9,341 unknowns at 0.1 GHz 1.41 GB over its baseline
  against 2.06, its system built in 1.5 s against 1.7 (the transform 0.33 s against 0.32; on NumPy panels it had
  been 3x slower); at 27k the basis needs 9.6 GB rather than 12.6, so the LimeRFE's 27k-unknown piece's 16.4 GB
  share holds it beside the small pieces. Its matrix the whole path's block by block to 1e-13 (at 0.1 GHz the
  blocks span ~17 decades: a first version's error in the loops' block, 0.6, was invisible in the whole's
  maximum; the test compares each block). `MOM25D_LT_HALF=off`, `MOM25D_LOOPS_HALF=off` restore the whole
  matrix's transforms.
- **segment: surface-current maps of a finished run (2026-11-22).** `tools/segment_studies/current_maps.py GHZ --
  <the run's arguments>`: the run's network rebuilt from its folder (no piece solved again), every chain driven at
  once from its start (the end whose net reads as an input, else its first port; `--starts` by hand; 1 W each, in
  phase, the other ends matched: `--mode power` each alone, their powers summed) and the waves at every cut solved, each layer piece solved once at that frequency from its board
  file and run settings (its S against the run's: 1e-6 to 4e-5) and weighted by its ports' voltages; |J| a layer
  in dB over the tracks (PNG, the triangles' values, the starts chosen); `--animate 36`: one cycle at the
  frequency, the instantaneous current |Re(J e^(j phase))| on a linear scale (MP4 and GIF a layer). A LimeRFE section of 11 nets
  at 2.4 GHz: 55 s. Also `select_nets.py netclass BOARD CLASS,...` (the project's own impedance classes) and
  LibreVNA and ThunderScope Rev 5.3 in `fetch_boards.sh` (the next whole-board studies).
- **mom25d: terminated ports, a few driven (2026-11-21).** `--mom-excite P1,P3` (`solve_board(excite=...)`,
  `sim.excite`): every port terminated in its reference (as a lumped element on its gap or via chain) and only
  those driven, one solve a column of S; their rows by reciprocity, 0 between two undriven ports. Lumped ports
  only; the RWG basis throughout (the loads were lost in the loop/tree basis's transform). On a LimeRFE piece
  the driven columns are the full solve's to 1.7e-7, and the sweep converged in 7 solves against 28. segment's
  windows drive their core ports alone (`K2E_SEG_EXCITE_CORE`): the Thor's In7 bus's 8 windows 1,103 s to 796 s
  (11,920 s as one piece), the same agreement with the whole group (through paths within 0.047 dB).
- **`--lean`**: no line-model preview and no figures where no solver needs them; segment's pieces use it (a small
  piece 13.3 s to 6.8 s, the same S).
- **segment: small pieces in parallel.** Pieces of at most 8 ports and 30 mm2 of signal copper are solved
  `K2E_SEG_JOBS` at a time on the CPU (the GPU off, the cores shared) beside the big ones on the GPU.
- **segment `--pour keep`: an RF board's coplanar ground (2026-11-21).** A layer piece keeps its own layer's ground
  within 3 mm of its copper (`K2E_SEG_POUR_REACH`; the band's edge straight and simplified), the voids that do
  not border its copper filled, the ground vias through it to its planes kept, solved with mom25d's signal zone.
  On a LimeRFE's 11 grounded-coplanar RX nets (0.1-6 GHz), against the pour to 6 mm on a fine mesh (33,659
  unknowns, 32 min): 14,378 unknowns and 57 s, |dS| 0.006 (return loss within 0.05 dB at the median); without
  the pour 5,937 unknowns and 17 s but |dS| 0.137 (return loss up to 10 dB off: the line is 57 ohm without its
  coplanar ground).
- **segment: where along a victim its crosstalk comes from (2026-11-21).** `<name>_coupling_profiles.png`
  (`coupling_profile`, `plot_coupling_profiles`): for the 4 worst pairs, the local backward-coupling coefficient
  Kb (the two lines' cross-section at the gap to the aggressor's nearest copper, the line model's field solve)
  against the distance along the victim's route from its near end (through its vias and series parts), the
  layer shaded, with a map of where it peaks and the pair's full-wave NEXT/FEXT. On the Thor the worst pair's
  coupling is the last 5 mm at the USB-C connector, not its 160 mm on In7.
- **segment: the worst couplings on the whole board (2026-11-21).** `<name>_couplings.png` (`plot_couplings`): the
  12 strongest channel pairs (both directions merged, by the larger of NEXT and FEXT) in colour over the faint
  high-speed routing, the stretch where each pair runs closest marked and numbered, with its NEXT, FEXT, gap and
  layer.
- **segment: big groups in overlapping windows (2026-11-21).** A layer group of more than 32
  nets (`K2E_SEG_WINDOW`) is solved as windows: cores of up to 6 nets (pairs whole, in breadth-first order across
  the bus) each with every net within the group's spacing, assembled into the group's S (`assemble`: a core
  port's row from its own window, an entry two windows have their mean). The Thor's In7 bus: 44 nets in 8
  windows of at most 18 (9.8k unknowns): 1,103 s against 11,920 s as one piece (106 ports), through paths
  within 0.05 dB and return loss within 0.04 dB median at 10 GHz, couplings above -30 dB within 0.42 dB (90 %);
  of those above -50 dB, 382 of 1,051 left out, between nets 0.86 mm apart or more (median 4.7 mm; the
  strongest, -32 dB at 5 mm, at DP0 AUX ports whose reference is floating copper).
- **segment's overlay (2026-11-21).** `<name>_overlay.json` (each channel's tracks with its figures, the via models,
  the cuts) and `<name>_overlay.png` (the channels coloured by their worst NEXT/FEXT and by their loss at the
  top frequency over the board's outline); segment.json records the via models and the layer pieces.
  `<name>_overlay.html`: the overlay as one self-contained page to explore (a figure and frequency to colour by,
  the channels ranked, each one's insertion/return-loss and mode-conversion curves and its worst aggressors);
  the report carries each channel's curves (60 frequencies).
- **segment: each channel's figures (2026-11-21).** A pair one channel in its differential mode: insertion and return
  loss at 1, 5 and 10 GHz, its far end's conversion to common mode, every victim's NEXT and FEXT for a 100 ps edge
  (Board coupling's `matrix`), in segment.json's `report`. The CM5 Minima (36 nets, 18 channels, 78 s with
  physics-based vias): D-PHY, HDMI, PCIe 1.1-2.4 dB loss at 10 GHz, the worst victims HDMI's lanes at 0.8 % NEXT,
  0.6 % FEXT; Ethernet at 125 MHz -0.02 dB / -41 dB, USB 2 and USB-C at 240 MHz -0.03 dB / -28 to -31 dB.
- **A physics-based via model (2026-11-20, prototype: `kicad2ems.core.viamodel`, `segment --via-solver pbm`).**
  No mesh: the vias as chains of nodes with their capacitances to the planes (an axisymmetric electrostatic
  solve), the cavities between planes as parallel-plate Green's functions coupling every via, ground vias
  shorting them. rf_diffvia Sdd21 0.073 dB rms from the whole board on mom25d (the meshed via model 0.193);
  the CM5 Minima's 12 via models in 18 s, its 36 nets end to end in 28 s with the layer pieces solved before.
- **mom25d: a coarse edge mesh (2026-11-20, `--mom-edges coarse`).** Neither the edge rings nor the gap rings, as
  Momentum without its edge mesh. A CM5 carrier's 18-net layer piece: 28,964 unknowns to 3,941, 49 min and 19.6 GB
  to 36 s and 1.4 GB, its through paths within 0.07 dB of the fine mesh at the median, its coupling terms |dS|
  0.006 (median); the 10 dB coupler's S31 within 0.46 dB of gfdtd (the fine mesh 0.64), its isolation 3.4 dB off
  at -25 dB (docs/mom.md#edges). `kicad2ems.core.segment --edges coarse` passes it to every piece.
- **segment: the layer pieces on the line model (2026-11-20, `--layer-solver lines`).** The via models stay on
  mom25d. rf_msvia S21 within 0.15 dB of the whole board, rf_diffvia Sdd21 0.11 dB rms; the CM5 Minima's 13 layer
  pieces in 2 min, against mom25d on its largest (49 min alone) 0.5 dB median, a stub's notch misplaced. A layer
  piece's tracks are its own nets', cut at the via circles (uncut, the line model saw an open stub past each cut
  port); a via model leaves a signal layer's pour out.
- **A board in pieces, vias as ports (2026-11-20, in progress: `kicad2ems.core.segment`).** Each signal layer's
  traces over ideal planes, each signal via (or a pair's) a small model with its antipads and the ground vias round
  it, the pieces joined as one network at cuts round the vias (docs/si.md). Against the whole board on mom25d:
  rf_msvia S21 within 0.18 dB (0.04 rms), rf_diffvia Sdd21 0.16 dB rms, as close as gfdtd's whole board is; the
  reflection near 10 GHz 2-3 dB higher (open). The CM5 Minima's 36 high-speed nets: 73 ports at their ends, 95
  cuts, two layer pieces (88 and 80 ports) and 12 via models.
- **mom25d's near-field correction built a piece at a time (2026-11-20).** The sparse correction's triplets came
  from the core for every near pair at once (~625 bytes a pair at the peak: a CM5 layer's 74 M pairs, 46 GB): it
  is built a million pairs at a time, its structure found once and each frequency's values summed straight in.
  5.5 M pairs: 289 bytes a pair at the peak (with the 112 the old build held too), the first frequency 3.5 s
  against 2.8 (np.unique's hashing had made it 10 s: a sort in place), the later ones unchanged; the same CSR.
  The memory plan counts it so. On the CM5's F.Cu the run got past the near field at 12.6 GB.
- **Board coupling in groups (2026-11-20).** A layer's nets further apart than `--apart` (default 20 heights over
  its plane) are solved as separate groups, the coupling between them taken as none; for mom25d each group's
  board keeps its layer's ground within 15 heights of its nets (a coarse outline) and the stitching vias in it,
  meshed in detail only near the traces (`--mom-zone on`). The CM5's F.Cu: two groups (16, 8 nets), B.Cu four.
- **The window's flows, recorded (2026-11-20).** `tools/gui/flows.py` drives the window offscreen through
  eight flows with real runs and records each as an animated GIF, with its steps and the errors met
  (docs/testing.md). Its first runs found the four bugs fixed below.
- **Solvers compared by figures (2026-11-20).** A run with two solvers or more has a *Solvers compared* section in
  its report: each solver's figures (each port's worst match and its dip, each transmission at the band's centre
  and its lowest: `core.sparams.figures`, `compared`) and their spread; `study.compare` takes solver engines too.
  On demo_lowpass it shows at a glance the line model's stop band 14 dB shallower than mom25d's at mid-band.
- **Parts at an angle (R-E5, 2026-11-20).** A two-pad part whose pads are not in line along x or y had been skipped
  (an open circuit where it stood); its element now runs along the dominant axis, from where the line between its
  pads leaves the first, with a copper strap across to the second pad in the board's copper (every solver has
  it). A 100 Ω 0805 at 45° between 50 Ω lines: |S21| 0.499 at 0.1 GHz (0.5 in theory), within 0.007 of the
  part at 0° to 3 GHz by mom25d and by gfdtd.
- **The crosstalk question takes a differential pair as a pair (2026-11-20).** Its legs (by name) are one trace in
  its differential mode (`si.as_channels`, board coupling's channels); `--legs` (the `legs` option) as before.
  On the DDR example, DQ1 beside the DQS0 pair: 4.96 % NEXT at the pair's receiver (6.0 % on its near leg alone),
  2.48 % on DQ1 from the pair (each leg half the differential swing).
- **gfdtd: primitive transforms and rotated polygons, as CSXCAD's (2026-11-20).** `AddTransform` / `GetTransform`
  (`CSTransform`: Translate, RotateAxis, RotateOrigin, Scale, Matrix; post-multiplied, degrees by default; in the
  XML as CSXCAD's `Transformation`) on every primitive, and `AddRotPoly` (a polygon turned about an axis, CSXCAD's
  `IsInside` ported); both agree with CSXCAD's `IsInside` on every one of 3,000-4,000 random points per case.
- **openEMS scripts run unchanged on gfdtd: `kicad2ems.gfdtd.compat` (2026-11-20).** CSXCAD and openEMS packages over
  gfdtd (put first on PYTHONPATH: `python -m kicad2ems.gfdtd.compat` prints the folder), with CSXCAD's own
  `SmoothMeshLines` (vendored, LGPL-3.0: the lines openEMS gets), the grid's `GetLine`, `Snap2LineNumber`
  (CSXCAD's `(index, inside)`), `GetSimArea`, `IsValid`, `Clear`, `openEMS.AddEdges2Grid` (openEMS's automesh
  box hints), `openEMS.physical_constants` as upstream, and spheres (`AddSphere`, `AddSphericalShell`, in the XML
  too), transforms, `AddRotPoly`, openEMS's `utilities` and `sar_utils` (vendored, GPL-3.0). 10 of openEMS's 16
  Python tutorials run unchanged, against 1 before; the horns' and the helix's directivity and aperture
  efficiency agree with openEMS's to the printed digit, the patch's resonance to the frequency point
  (docs/gfdtd/README.md#compat).
- **Board coupling takes a differential pair as a pair (2026-11-20).** Its legs (by name: `D0_P` / `D0_N`) are one
  channel, driving and received in its differential mode (`coupling.channels`, `modal`: mixed-mode S of every
  channel's ends, the voltage share between modes of other impedances), and each pair reports its own conversion
  to common mode; `--legs` (the `legs` option) keeps them as two nets. On si_ddr the DQS pairs are the worst
  victims: 12.1 % NEXT and 14.0 % FEXT on F.Cu, 8.3 % and 1.5 % on In2.Cu (mom25d), converting 0.02-0.06 %.
- **dfsynth writes the filter as a footprint (R-E18, 2026-11-20):** `<out>.kicad_mod` beside the board, to place
  in one's own: the copper as polygons on F.Cu, pads 1 and 2 at the ports (the feed's width), the ground vias as
  plated pads 3, the origin at the filter's centre, its substrate in the description.
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
- **The window's EMC plots and the decoupling summary** (seen in screenshots of the five new tools, 2026-11-27):
  - radiated emission's and susceptibility's curves (dBµV/m, dBµV) were drawn on a log y axis, the window's
    default for a curve set that does not say: `logy=False` in both, as conducted emission had;
  - the decoupling summary said "the target met to 2 GHz" (the band's top) on the Jetson's +1V15, where it is out
    of reach from 43.1 MHz (U21's own pads and the sites' mounts over 115 mΩ): it now reads "met where in reach
    (out of reach from 43.1 MHz at U21)", the report's text and `decoupling.png`'s title the same
    (`reports._reach`, from each load's `out_of_reach` bands).
- **CI's `ipc-as-installed` job tests its own commit (2026-11-25; F92).** It installed kicad2ems from the
  plugin's requirements as KiCad does, an archive of `main`, so a run tested its commit's `tests/ipc` against
  whichever commit `main` served then (pushes minutes apart, or an archive cached): code older than its tests.
  The job now installs the same requirements with the archive of the run's own commit.
- **The KiCad plugin's window closed with its map running (2026-11-25; found from CI's red `ipc-as-installed`
  runs, F91).** The board's read ending after the window closed (its signal queued) started the impedance map,
  and closing waited 2 s for a map's current group (a group of nets in the line model, often longer); the map's
  thread then ran on to the process's exit, where a QThread destroyed while running aborts the process: the
  plugin's when its window closes, and the test suite's after its last test (all 28 passed, the step failed:
  13 of 15 local runs aborted). Closed, the window starts no map (`_closing`), stops its map timer and waits for
  its loader (a map stops at its group's end). The suite: 12 of 12 runs clean. Test: tests/ipc/test_plugin.py.
- **Elmer's via barrels each their own plating (2026-11-25; the review, F57).** The layered mesh drew every barrel
  as its drill's cylinder and gave all of them one copper fraction, the mean of their plated fractions, so on a
  board with drills of several sizes each barrel's conductance (and its heat path) was the mean's: 25 µm plating is
  44 % of a 0.2 mm drill and 16 % of a 0.6 mm one, and a net with one of each through 1.6 mm came out 35 % below
  its closed-form resistance. `layered_mesh(plating=)` now draws each barrel as a solid copper cylinder of its
  plating's cross-section (radius √(r² − (r − p)²)), at copper's own conductivity, so each via's resistance and
  axial heat conduction are its own: the pair 4 % above the closed form (the pads' share). The DC drop and the
  thermal solve pass their `--plating`; a run whose nets have one drill is unchanged. Test:
  tests/fem/test_elmer_dc.py.
- **dfsynth's gap model across s/w = 0.3 (2026-11-25; the review, F56).** Garg and Bahl's two fits of a series
  gap's even-mode capacitance (s/w up to 0.3, and from 0.3) meet by a jump: on 0.2 mm FR-4 with a 0.38 mm strip
  Cp 3.61 → 4.92 fF and Cg 6.02 → 5.26 fF between s/w 0.30 and 0.31, so an end-coupled filter's gaps near it were
  sized on a step. The two are blended over s/w 0.25-0.35 (`Substrate.GAP_BLEND`, smoothly in log s/w and log
  Ce), the fits as published outside; Cg still falls with the gap (`gap_for`'s bisection). Cp and Cg change by
  16 % at most, inside the blend. Test: tests/dfsynth/test_line_models.py.
- **segment `--cavities`: the planes the join and the via model agree on, and a port off the cavity's mesh
  (2026-11-25; the review, F20 F21).** The board's cavities are pairs of planes covering 40 % of its outline
  (`core.cavities`), a via model's planes those covering half its box (`viamodel.planes_of`); where the two
  differed (a plane's void at the via, a pour that is a plane in the box alone) the cavity named for the join
  matched none of the model's, the via kept its infinite planes there with no word, and the run called it a
  meshed box. Now a cavity left to the join gives the model its planes (`viamodel._planes`: its two layers planes,
  a layer between them not one), said in the model's notes; the run says which models are not joined and which of
  those are meshed boxes. A port whose disc is on no triangle of the cavity's mesh (inside the outline, outside it
  once simplified) had Z 0, a short across that barrel's segment in the join, with no word: `cavities_for` now
  builds the meshes before the pieces solve (`Cavity.port_off`) and leaves such a cavity out of that via model's
  join, saying so (the late check in `cavity_blocks`, F22, is the same as `cavities_for`'s and cannot fire). Tests:
  tests/core/test_viamodel.py (rf_msvia's In2.Cu cut to a ring: a third of the box), tests/core/test_cavities.py.
- **Copper loss on mom25d's thick-copper sides (2026-11-25; found with the review's F17, F86).** With
  `--mom-thickness sides` (the default) and `mixed`, every copper edge is a plate carrying part of the current,
  but the copper's surface impedance was added to the sheet's functions alone (`gram_matrix`: the edges' block),
  so the plates' current was lossless, and a trace's edge current, which they carry, too. A 50 Ω microstrip over
  the ideal ground at 10 GHz lost 0.033 dB in 10.4 mm where the strip's share of Hammerstad and Jensen's 0.097 is
  0.072 (Wheeler's rule: 0.74). The plates' functions now take it (`Sides.gram`: their overlap with themselves and
  with the edges on a fold's sheet triangle; each its plate's interface's Zs, scaled symmetrically): 0.066 dB
  (0.92×). It reaches every solver path (the extras' via rows, F89). Measured on each thickness (docs/mom.md#
  thickness-loss): `off` 1.34× the strip's share, `grow` 1.25×; on a whole stack, where `sides` becomes `edge`,
  `edge` gives 2.4× the line's loss (the review's tracker F90, to fix), `grow` 1.12×, `off` 1.19×; segment's
  rf_msvia whole board lost 0.813 dB at 10 GHz on `edge` where the line model loses 0.349. Over the ideal ground
  that plane's own loss (0.26 of the line's here) is not modelled (ROADMAP). Tests:
  tests/mom25d/test_conductor_loss.py (the Gram against the halves integrated directly; the microstrip within 20 %
  of the strip's share, 0.90).
- **Terminated via ports on mom25d's block low-rank path (2026-11-25; found with the review's F86, F89).** With
  `excite` (every port terminated, the ones named driven: segment's windows, `--mom-excite`) a port on a via chain
  has its load in the sparse extras' via rows and columns. The block low-rank solve assembled its factor and its
  GMRES operator from the extras' edge block (`dZx`) and the vias' border (B, C) alone, and the exact matrix-free
  product added the edge block alone too, so those loads were not in the system solved: on a strip between two
  via ports, |S21| 1.95 against the direct solver's 0.98 (|ΔS| 1.09; with thick copper's sides 1.18). The border
  it is assembled from now has them (`_border_with`), and the exact product adds the extras whole: the direct
  solver's S to 6e-7 (2e-7 with sides). The direct and mixed-precision paths had them already (1e-7). Gap ports
  (the segment pieces' cuts) were not affected. Test: tests/mom25d/test_blr.py.
- **The FDTD mesh graded across its kept lines (2026-11-25; the review, F42).** `smooth_lines` graded each gap
  between kept lines from the widths of the gaps beside it: where one of those was split into small cells, its
  neighbour started from the gap's own width (0.21 mm beside 0.031, 6.8×), and 32 of the 52 examples' default
  meshes had a cell beside one 2× or more its size (rf_ratrace 8.9×), against the `--ratio` (1.4) promised. Each
  kept line now has the cell size it may have, carried along the axis (growing ratio − 1 times the distance, as
  cells growing by the ratio do); each gap is graded from its two ends, its middle split into the count that
  keeps both graded sides within the ratio, and a jump left at a kept line is mended by grading again. The cell
  round a copper edge (its thirds-rule pair, `_edge_lines`' edges) is kept whole: mending a cluster of fine-scale
  gaps (on demo_transition 0.042 mm between 0.025 and 0.033, 1.6× whole or halved) had halved them in turn into
  the pair round its stripline's edge, a line 0.004 mm off it and S21 8.6° late at 8 GHz. On the 52 examples: 3
  past 1.4 (rf_ratrace 2.3×: a thirds pair beside cells its curved ring's largest cell holds small;
  demo_transition 1.5× among its kept lines), cells +6.9 % in all (median +7.6 %; −47 % on rf_pcoupled, whose air
  the old grading had filled with 0.13 mm cells, +84 % on rf_ebg), the time step the same on 49 (9-13 % shorter on
  demo_transition, rf_interdig and rf_wilkinson). On gfdtd (the 7800 XT), against a reference of twice the
  resolution (res 40, fine / 2, the two gradings' runs averaged), 13 of 19 examples are closer (rms |ΔS| rf_siw
  0.92 → 0.38, rf_pcoupled 0.021 → 0.016, demo_transition 0.0066 → 0.0053, sma_launch 0.0061 → 0.0042) and 5
  farther by 0.003 at most (rf_msvia 0.0061 → 0.0092, rf_openloop 0.0086 → 0.0110, demo_lowpass, rf_marchand,
  rf_ratrace); rf_ebg is converged on neither (its stopbands at 3.2 and 7.2 GHz at res 60 and fine / 3, at 3.2-3.8
  and 4.4-4.9 GHz on the default mesh and at res 40, with any fine cell: its patches want finer cells; the
  review's tracker, F88). demo_transition's delay 134.8 ps (134.9 before; 134.4 converged). Tests:
  tests/fdtd/test_model_mesh.py (the 6.8× case, the edge's cell kept whole, random lines within the ratio).
- **segment's pieces with the config's copper and sweep (2026-11-25; the review, F17).** Each piece ran with the
  band and the mom25d options alone: a config's `sigma`, `rough`, `finish` and `sweep = log` never reached it, so
  `segment` solved lossless copper on a linear sweep where a whole-board run of the same config is lossy, and
  nothing said so. Every piece's run now takes them (`piece_settings`; only those set, so a lossless linear
  study's pieces and their `piece.sha256` keys are as before), the physics-based via model is solved on the
  sweep's own grid (`band_freqs`: it had built a linear one) with the planes' copper in each cavity
  (`viamodel.solve(sigma=, rough=)`: k^2 (1 + (1 - j) delta / d), the planes' surface impedance), and
  `--cavities`' blocks take the config's conductivity (with `rough`, Hammerstad's factor at each frequency) in
  place of `cavities.SIGMA_CU`. The run says which settings its pieces have. On rf_msvia with `sigma = 5.8e7`,
  `rough = 1` and a log sweep over 1-10 GHz (`--thickness grow`, as the studies): the copper takes 0.219 dB from
  S21 at 10 GHz in pieces, 0.407 dB on the whole board on mom25d, 0.349 dB on the line model; S21 0.145 dB rms
  from the whole board's, as lossless. The pieces' share is low because a layer piece's ideal planes have no loss
  of their own. With mom25d's default `sides` thickness the same run gave 0.117 dB against the whole board's
  0.813: the pieces' side plates carried no surface impedance (F86, fixed below) and the whole board's `edge`
  thickness overstates the loss (F90). Tests:
  tests/core/test_segment.py (the run in pieces against the whole board), tests/core/test_viamodel.py.
- **A part-value sweep of an inductor with its ESR, and of 0 F (2026-11-25; the review, F23).** `--part-value`
  took a part's kind from which of R, L and C it has, an L with no R: an inductor with an ESR field was a
  resistor, and the sweep's values replaced its ESR. It is Part.kind's rule now (an inductance: an inductor).
  `impedance` read `if C:`, so a swept 0 F was a short; it is an open (`OPEN_OHM`), as parts.py has it. Test:
  tests/core/test_embed.py.
- **A hung Palace stopped (2026-11-25; the review, F60).** `palace.run` had a timeout watchdog, but no caller
  (the capacitance, loop, PDN tools and the S-parameter solver) gave it one: a hung MPI job, which is silent, ran for
  ever from the command line. A Palace that writes nothing for `K2E_PALACE_SILENT_S` seconds (default 7200, 0
  none) is stopped now and said; one that keeps writing runs as long as it needs. Test:
  tests/fem/test_palace_watchdog.py; docs/fem/README.md.
- **--warm names the switches it cannot warm (2026-11-25; the review, F59).** Only a switch's `Ron_ohm` moves
  with its temperature, and a vendor's VDMOS `.model` or a MOSFET given `Kp_A_V2` conducts by its own
  parameters: such a switch was reported at 110 C with its 25 C conduction loss, without a word. `--warm` now
  names each at its start (`temperature.unmoved`). Test: tests/pcbqs/test_circuit_warm.py.
- **A circuit's part that is not fitted (2026-11-25; the review, F53).** The circuit model's checks named a
  probe's or a coupling's part but did not ask whether it is fitted (`enabled: false`, which the KiCad import gives
  a DNP part, and a sweep may set). A coupling to such an inductor wrote a K naming an element the deck does not
  have (ngspice failed the point three times over, with a cryptic log); it couples to nothing now. A current probe
  on one raised KeyError, which the engine's per-point catch let through: the whole sweep ended; it is a
  ValueError for that point now, saying the part is not fitted. Test: tests/pcbqs/test_circuit_model.py.
- **dfsynth at the edges of what is asked, and its validation's stale results (2026-11-25; the review, F52 F54
  F55).** A validation read every `<base>_*.s2p` in its `_em` folder, so the last round's files (another design,
  a solver not asked now) were read, plotted and reported with this round's, and `--em-tune` could correct
  kf/kb from a stale response; they are removed before each run now. A band-stop's `--atten` at f0 itself
  divided by zero (a traceback); any order meets it there. An interdigital filter of order 1 failed on
  `max()` of no couplings; it is made of order 2, said as the stub band-pass's minimum is. `--order 0` had
  become 5 (`a.order or 5`); the spec refuses an order below 1. Tests: tests/dfsynth/test_synthesis.py.
- **mom25d's last rung, the double LDL^T, checked (2026-11-25; the review, F15).** Where every complex64 factor had
  failed and the whole matrix did not fit for the pivoted LU, the half's own LDL^T in double, without pivoting,
  gave the answer with no check: the rung is reached on near-singular matrices, where such a factor can be far
  from its matrix (the code's own comment: |S| from 1.4 to 539 run to run). The factor replaces Z in place, so the
  operator's products with two vectors are taken before it and compared with the factor's after it
  (`LDL_CHECK`, 1e-8 relative; a stable factor is below 1e-12): a factor that is not its matrix's is said, a
  WARNING that its S is not to be trusted. Test: tests/mom25d/test_gpu.py (the 980 Ti and the CPU).
- **The KiCad plugin's Reload (2026-11-25; the review, F12 F13).** With KiCad's API not up when the window opened,
  Reload did nothing and the window had to be opened again; it connects now. With another board opened in the
  same editor, Reload read it but kept the last board's stem, folder and session: that board's region and ports
  were applied to the new one, and its session, config and runs written over the last one's; the window takes the
  new board as its own now (its own folder and session). `Live.changed()` asked the last board's handle, so
  another board open was no change to reload for; it asks KiCad for the board open now. Tests:
  tests/ipc/test_plugin.py.
- **The window's Save with a field that is not a number, and paths on another drive (2026-11-25; the review, F62
  F63).** Save wrote `values()`, which leaves out a field that is not a number (`2,5` with a comma, a half-typed
  `1e`): the config lost that key and the window said "saved". Save now refuses it and says which field, as Run
  does. On Windows, an analysis's board on another drive than the config (or on a UNC share) has no relative path:
  `os.path.relpath` raised in `config_values`, the study document's tables and the circuit page, after the main
  config was written, and the save stopped halfway; `runner.relative` keeps such a path as it is. Tests:
  tests/gui/test_study.py.
- **gfdtd's GPU choice given back, and an antenna's estimate (2026-11-25; the review, F44 F45).** With
  `--gfdtd-gpus`, when the measured speeds left one GPU, `jobs()` set `EMSDEV_VULKAN_DEVICE` for the whole process
  and never put it back: the window's later solves and mom25d's Vulkan LU took that GPU. gfdtd's solve now gives
  the variable back at its end, a failed one too (solvers run side by side by `--solver-jobs` still see it
  while it runs). The FDTD estimate meshed an antenna with `air`, not `antenna_air` (lambda / 4 at the band's
  centre), and its pulse to fmax, not `exc_fmax` (30 % past it), as prepare does: its cells, time and memory
  came out low, and `--solvers auto` chose by them. Tests: tests/fdtd/test_gfdtd_solver.py.
- **A config saved with an escaped quote in a string (2026-11-25; the review, F25).** `update_toml` (the window's
  save) found a line's comment and brackets with `_scan`, which ended a basic string at `\"`: a bracket after
  it opened a value that ran on to the next line, and that key was deleted on the rewrite. A backslash escapes the
  next character in a basic string now (a literal one, '...', has none). Test: tests/core/test_runs.py.
- **A row of vias to ground for qucsator (2026-11-25; the review, F24).** The line model's via element of n barrels
  in parallel had L / n for scikit-rf but was written to qucsator as one `MVIA` of one drill: n times the
  inductance on the qucs backend. It is n MVIAs side by side now (`Element.n`). Test: tests/core/test_fastsolve.py.
- **A dielectric without er, and a cut's open ends, in the raw stack's readers (2026-11-25; the review, F18
  F19).** KiCad's "Not specified" is `er = None` in KiCadBoard's `stack_items`, which only Board filled (the
  material's, else FR-4's, with a note); si's and coupling's time grid (`max()` of the dielectrics' er), segment's
  cross sections (`_xs_of`) and the impedance calculator (`lines/calc`, the mask's too) raised TypeError on it.
  `materials.er_of(it, key)` gives Board's value for any of them. si's wider-gap figures (`_moved`) compared a
  conductor's end that the cut does not reach (a pour's: None, infinite) as a number: TypeError outside the try,
  after every solve, and `crosstalk.json` never written; open ends are infinite now and stay open when moved.
  Tests: tests/core/test_materials.py, tests/core/test_si.py.
- **A band from 0 Hz refused (2026-11-25; the review, F16).** `fmin <= 0` was refused for a log sweep only: a
  linear one from DC (or below) went on to a ZeroDivisionError in the cavity model, NaN in the via model and
  in mom25d's Sommerfeld contour ("cannot convert float NaN to integer"), and an FDTD end check that never
  fired. The settings now say fmin must be above 0. segment's `--fmin 0` and `--points 0` had been passed over
  for the config's (`a.fmin or s.fmin`) without a word; a value given is taken as given, and a band that is
  not one is refused. Tests: tests/core/test_cli.py.
- **mom25d's Gram matrix sparse from the start (2026-11-25; the review, F14).** A surface impedance's term
  (`--sigma`, `fast.py`'s extra blocks, the block low-rank path too) took `gram_matrix`, which built four dense
  Nt x Nt diagonals and summed seven dense Ne x Ne products before the caller made it sparse: about 5 x 8 Ne^2
  bytes on the first frequency (the review measured 836 MB at 4,485 unknowns; ~65 GB at 40k), none of it in
  the memory plan, and on the very path chosen because memory is short. Its triangle moments are diagonal (a
  basis function meets another only on the triangles they share), so it is the same sum with sparse diagonals
  now, CSR throughout: the same numbers (difference 0 on a 3,605-unknown grid), peak 1.5 MB against 541 MB
  there. Test: tests/mom25d/test_gram.py; tests/mom25d (CPU) and test_gpu.py (the 980 Ti) pass.
- **Stopping a run ends what it started (2026-11-25; the review, F06 F11 F71).** The window's second Stop, its
  grace kill after a first Stop and closing the window all killed the run's process alone (QProcess.kill):
  fem's Palace and Elmer ranks run in sessions of their own and their guard stops them only at exit or on
  SIGTERM, which SIGKILL skips, so closing the window during a Palace analysis left its 8 MPI ranks running; a
  tool that ended on the first Stop's SIGTERM left its children to init. `core.runs.kill_tree(pid, also)` now
  kills the process and every descendant by parent pid (Windows: `taskkill /T /F`), never by name; the first
  Stop records the tree (`tree_of`: each pid with its start time, so a pid used again is not touched) and the
  kill reaches what the tool left. Closing the window asks first for an analysis's run too, as
  docs/gui/README.md said (it had asked for kicad2ems's own only). The KiCad plugin's window kept its finished
  QProcess, so the board was never read again on activation after a first Run (R-E26) and a run that failed to
  start left Run disabled; it lets the process go when it ends or fails to start, and closing it during a run
  asks, stops the run and then ends its tree. Tests: tests/core/test_runs.py and tests/gui/test_runprocess.py
  (a tool whose child runs in a session of its own: the window's close, Stop twice, Stop and its grace).
- **The installer's exit status, and the folder its target Python runs in (2026-11-25; the review, F07-F09).**
  A step that failed (a compiled core, the openEMS, Elmer or Palace build, `kicad2ems --check`) was a note
  and the installer said "Done" and exited 0, so a `--yes` or CI install of a mom25d that cannot mesh passed;
  now each is said as a WARNING, listed under "Done ..., with N failure(s)" and the exit status is 1. The
  optional slangpy wheel was installed with `check=True`: a platform or Python without one aborted right after
  kicad2ems itself, before the cores, the site file, the commands and the launcher; now it is one of those
  failures and the install goes on without the GPU. The target Python's `-c` and `-m` ran in the shared temp
  folder, which they put first on `sys.path`, so another user's `pip/` or `numpy/` there could be imported, as
  root under sudo; they run in an empty folder of the installer's own now (`mkdtemp`, removed at exit).
  Tests: tests/core/test_install.py.
- **The physics-based via model with two barrels at one place (2026-11-25; the review, F05 F31).** Two vias at
  one place (a via drawn twice, one in a PTH pad's plated hole, which the importer takes for a via, a ground via
  drawn twice) were 0 apart, so the cavity's H0^(2)(0) was infinite and the whole S NaN, with no word; `pbm` is
  the automatic choice for an unmeshable box and the one `--cavities` joins, so one NaN block made the joined S
  NaN (the twin of 2e7dd1e in mom25d). Each signal via took the nearest record of its net, so a stack of
  microvias at one place had the first record's span twice. `viamodel.build` now makes a net's records at one
  place (a centre inside the other's barrel) one barrel over all their spans, ground vias there one, and leaves
  out a ground via or another net's via in a signal via's barrel (a short in the file); `model['notes']` says
  each, and segment prints them. Test: `test_barrels_at_one_place_are_one` (rf_msvia's piece, each case: S as
  without them, to 1e-12).
- **FDTD S-parameters between ports of different z0 (2026-11-25; the review, F04).** openEMS's and gfdtd's runs
  gave S as the voltage waves' ratio uf_ref_p / uf_inc_k, which is S only where every port has one z0; the
  reciprocity fill (`fill_reciprocal`), the mixed modes and the comparison with mom25d (power waves) all take it
  for power-wave S. A port given its own `z0` (a footprint's field, a config's port) had a matched 50-to-75 ohm
  thru at |S21| 1.22 and |S12| 0.82. `run.s_column` now scales each term by sqrt(z0_k / z0_p); with one z0 the
  factor is 1 and nothing changes. Tests: tests/fdtd/test_s_column.py.
- **The KiCad plugin's map bands in their colours (2026-11-25; the review, F10).** `_polygon` set a band's colour
  from a generator and 1.0 unpacked into four channels, two values for four: it always raised, the colour's
  `try` (meant for a kipy without colours) hid it, and every band went to KiCad in its layer's colour since the
  maps were drawn there (R-E26). `test_a_band_is_a_filled_polygon_in_kicad` now checks the colour.
- **The GPU report's adapters, and a retired option out of the docs (2026-11-25; the review, F49 F78).**
  `tools/gpu_report.py` imported `emsdev`, the package before the merge: it worked only beside a stale
  emsdev 0.3.1 in the venv, and a fresh install's report would have said "no Vulkan adapters"; it is
  `kicad2ems.emsdev` now. docs/usage.md no longer lists `--mom-tiled`, retired with the tiled solver on 2026-11-15.
- **A rotated footprint's pads in a KiCad 4/5 file (2026-11-25; the review, F03).** The importer added the
  footprint's rotation to each pad's angle in a `(module ...)`, but a board file's pad angle already holds it,
  in a module as in a `(footprint ...)`: every pad of a footprint not at 0 or 180 degrees was turned a quarter
  turn. KiCad's qa `issue4139` (5.99): R1's pads 0.70 x 1.30 mm instead of 1.30 x 0.70. The LimeRFE (KiCad 4):
  194 pairs of pads of two nets overlapping in 30 footprints, none now; hackrf-one and LibreVNA (`footprint`)
  unchanged. Some of what segment said of the LimeRFE's file (its overlapping net pairs) came from this. Test:
  `test_a_rotated_footprint_s_pads_keep_their_board_angle`, a module and a footprint alike.
- **Part values as libraries and BOMs write them (2026-11-25; the whole-repo review, F01 F02 F50 F51).** Three
  parsers read a part's value, and each had its own gaps, silently. `core.units.value` took no space before the
  prefix and matched the unit in one case, and both fallbacks took the first word that read as a number, which
  is often the package: pcbqs's `part_value` read `0.1 uF` as 0.1 F, `100 nF` as 100 F, `4.7 uH` as 4.7 H and
  `2 mOhm` as 2 ohm (the PDN's decaps, the rail tree's series parts, the converter's ngspice model); core's
  `kind_value` read `10 kOhm` as 10 ohm, `2.2 MOhm` as 2.2 ohm, `C_0402_100nF` as 402 F and `R_0603_10k` as
  603 ohm (openEMS's lumped parts, segment's series blocks); `field_value` gave None for `5mOhm`, `5 mOhm` and
  `2.2 nH` (an ESR, ESL or DCR field ignored, the package's default used); `engineering_value` read `10f` as 10.
  Now `value` takes a space before the prefix or unit, the unit (ohm, F, H, R) in any case and SPICE's `Meg`;
  the new `units.part_value` is the first word, else the first word that is a value and not a package code
  (`units.PACKAGES`), after the kind's letter when one is given; pcbqs's `part_value` and `field_value` and
  core's `kind_value` use it, and a ferrite's `600OHM` reads too. Tests: each spelling above, in
  tests/core/test_parts.py, tests/pcbqs/test_pdn_rail.py and tests/pcbqs/test_schematic.py.
- **CI green again (2026-11-24; the stack workflow red since 11-20, the docs build since 11-23).**
  - mom25d refuses a port whose via chain shares its place and interfaces with another chain
    (`Simulation._refuse_ports_at_one_place`, the ports named): their columns of the matrix are the same, so it is
    singular in either basis and the port has no S of its own (its gap driven while the other chain there is held
    at 0 V). 2026-11-22's test of it had read |S21| 1.18 and S11 != S22 on this machine, finite only by a
    rounding-level pivot, and not finite on CI (an exact zero pivot). segment already makes such ports one
    (`_one_port_a_place`). Two plain vias at one place (a via drawn twice) are one plated hole: the second's chain
    is left out, said (`_one_chain_a_place`; two with a part each are refused). Kept, their equal columns had
    solved on this machine (S within 4e-7 of one via's) and given |S11| 1.14 on CI: a right-hand side consistent
    with a singular matrix is no guarantee its factors hold.
  - `test_the_tables_in_one_batch_are_each_alone` compares `total(r)` to its largest value, as it does the
    table: elementwise, far out where the values are small, the batch's and the single build's rounding
    differ by ~1e-12 of their own (1.2e-12 here, over on CI); to the largest value 1.2e-16.
  - docs/segment-studies.md in the site's navigation (`conf.py` NAV).
- **KiCad import: a fill over another net's via is a short to it again (2026-11-23).** 2026-11-21's stale-fill cut
  (a LimeRFE GND pour saved over its 30 MHz RX line) cut every fill back from other nets' tracks, pads and vias;
  the examples draw their grounding vias inside the ground fill (rf_interdig's resonators, the filters' line
  models), so those were open from then: seven tests red (rf_interdig's golden summary, test_fastsolve's ground
  via, test_xsection's two, test_coupled's three, the last put down to something older in the ROADMAP). The cut
  now spares vias (a via in a fill shorts it, as drawn), tracks and pads still cut. And the cut found the fills
  by their positions in the import's lists, which `_drop_unused` rebuilds before it on a board whose unused rings
  are dropped (the Thor carrier's 2207): it finds them as themselves now (no run so far met both: the cut fired
  on LimeRFE alone, which drops none). A test of both (test_kicad).
- **Two tests red since earlier this week (2026-11-23):** `parts.not_fitted` documented in docs/api.md, and the
  configs' stored meanings (tests/core/data/config_compat.json) given the settings added since (`mom_edges`,
  `lean`, `mom_excite`, each at its default: no config's meaning changed).
- **segment: a via model that is no meshed box is the physics-based model's (2026-11-23).** Two faults made meshed
  via models read open or wrong, found by solving LimeRFE's via models with `pbm` beside their meshed results:
  - a port over signal copper: a series part's pad near the via shrinks its cut circle (the keep-out) inside the
    antipad, or the port is a through-hole pad's over its own barrel; no plane is under the port, so its
    reference is the nearest copper, its own net's on the board's far side, and the via shorts it (|S11| 0 dB,
    |S21| -30 to -150 dB: LimeRFE's `VCOM`, `Net-(C6-Pad1)`, `Net-(C508-Pad2)`, `/Cell/VIA_U35_U39` and others,
    ThunderScope's four BNC inputs, which docs/segment-studies.md had put down to relays the model lacks);
  - fewer than two ground vias in its box: the planes, clipped to the box, tied by none (the return current at
    low frequency only their few pF: -12 to -18 dB at 0.1 GHz, 0 dB above 1 GHz) or by one (a shorted patch:
    LimeRFE's J18 via -16 dB at 3.88 GHz, `pbm` -0.8 dB at most).
  With no `--via-solver` given, `_unmeshable` sends such a model to `pbm` (said; `--via-solver mom25d`, or
  `K2E_SEG_VIA_AUTO=off`, meshes every box): 14 of LimeRFE's 20 via models, 1 of LibreVNA's 24, ThunderScope's
  front end's 4, none of the examples. Where both are well posed (3-15 ground vias) they agree to |dS| 0.10-0.36
  at 6 GHz; on rf_diffvia `pbm` is the closer to the whole board already (0.073 dB rms against 0.193).
- **mom25d: a kept factor that did not precondition is freed when the new one needs its room (2026-11-23).** In the
  dense path `solve` still held the kept factor it had tried (its local and the preconditioner's closure), so when
  `_factor_half` dropped it for the new factor's room the GPU's memory was not given back, and the new LDL^T ran
  on an over-full card: on LimeRFE's 33k-unknown F.Cu piece (Z's double half 8.7 GB on the GPU, a factor 4.4 GB)
  every factor after a failed reuse took 10.0 s, 4.9 s with room (3.05 GHz 20.2 -> 14.6 s, S within 3e-11); its
  run had four. Pieces under ~28k unknowns had room for both and were not slowed.
- **segment: a channel whose end port lies on another net's copper is left out, said (2026-11-23).** A port's net
  is the copper under its point (`Board.net_at`, the first net there); where two nets' copper overlap in the board
  file, the port joined the other net's piece and read its signal. LimeRFE's one 37.7 % far-end figure was that:
  `Net-(C120-Pad1)`'s end port at U11 pad 4 (`Net-(C120-Pad2)`) sat on `/30MHz_TX`'s track in the file, so it
  read that line's signal, and the 50 ohm port took half its power (`/30MHz_TX` -3.5 dB at 0.1 GHz). `placed`
  now leaves such channels out before building the pieces (`_off_its_net`, all at once): ten on LimeRFE, seven of
  them pads under a stale ground pour's fill (their channels had read open), none on the other boards.
- **segment: the pieces share the memory (2026-11-22).** The LimeRFE rerun and LibreVNA were OOM-killed at a 23 GB
  cap: a 27k-unknown piece planned its hybrid solve (24 n^2 with thick copper's sides, 17.5 GB) against what was
  free when it started, and the six small pieces beside it on the CPU grew into the same cap. Now each piece runs
  in a share (`EMSDEV_MEM_BUDGET`, new: the bytes a process may hold in all, `emsdev.memory.available()` then at
  most that less what it holds): a small one up to 3 GB (`K2E_SEG_SMALL_GB`; 95 % of 461 LimeRFE, Thor and
  ThunderScope pieces planned 2.1 GB or less), a big one the rest less 1 GB a CPU worker. A piece whose direct
  solver does not fit its share exits before its fill (`MOM25D_NO_ROOM_EXIT`, code 75) and runs again at the end,
  alone, with all of it; a small one starts only with room for it, and below a low mark the newest one is stopped
  and run again at the end the same way.
- **segment, mom25d, mesh: three failures of the LimeRFE study (2026-11-23).** (1) Ports that come to one place in
  a piece are one (`segment._one_port_a_place`): two series parts' pads 1.1 mm apart, each port centred on the
  same 4 mm of copper, had made two via chains at one place; the other's links are its, `nodes` joining them.
  (2) The double rung on the half pivots on the matrix whole where it fits beside the half (`DOUBLE_WHOLE`): it
  is reached where every complex64 factor failed, a near singular matrix, and there the half's LDL^T without
  pivoting had returned |S| of 1.4 to 539 run to run, without an error. (3) A mesh refinement round's
  circumcentre that falls on its split midpoint (a right triangle's) is dropped: added twice it was a duplicate
  point the triangulator refuses; a via model had failed in every run, unseen because a study raises only its
  first failed piece.
- **segment: one cut where one conductor crosses (2026-11-23).** Two crossings of one net on one layer within half
  the narrower track's width, alike in direction, are one cut (they were within 1 um): a KiCad 4 file's track
  drawn twice 3 um off gave two ports 0.03 um apart (centred on its 2.5 mm copper), two via chains at one place,
  the matrix near singular in either basis: two LimeRFE via models (25-29k unknowns) had every complex64 factor
  fail to precondition (GMRES residual ~1) and solved in double precision, 11 and 28 minutes. (Its ports
  renumbered after the cuts dropped: a LimeRFE run solves its pieces again once.)
- **mom25d: a broken factor no longer runs a piece out of memory (2026-11-22).** The LimeRFE rerun's 27k-unknown
  piece ran at 4.6 GB in its share for 15 minutes, then grew to 20 GB within three and was OOM-killed: GMRES had
  gone on to its cap on NaN (a complex64 factor without pivoting broken down; `den` NaN in the Givens rotation),
  and the rung after it, the double-precision LU, had no memory check. Now GMRES stops at the first non-finite
  step (the steps before kept, the fallbacks at once), and the double rung checks its 16 n^2 first:
  `MemoryError`, or with `MOM25D_NO_ROOM_EXIT` its share given back. In core.segment a piece killed by a signal
  runs again at the end alone too, and each piece's process raises its own `oom_score_adj` (800) so the kernel
  kills a piece rather than the study (with systemd's `OOMPolicy=continue`: its default had stopped the whole
  scope).
- **segment/coupling: a board turned over turned the next back (2026-11-22).** `_over_its_plane` mirrored the
  stack's dielectrics in place, and every piece of a board shares them (`to_dict`): every second B.Cu piece
  turned over had them turned back, its ground off its dielectric interface ("port layer B.Cu is not a metal
  interface above the ground In4.Cu", a ThunderScope piece). A mirror-symmetric stack (LimeRFE's, Thor's) gave the
  same heights either way. Now each turned piece mirrors its own copy.
- **segment: two ports at one place (2026-11-22).** A track drawn twice (as a KiCad 4 file has them) crossed a via
  group's circle twice at one spot: two cut ports there, and two series parts whose pads coincide gave the same.
  Each lumped port is a via chain to the plane, so in the loop/tree basis one was a loop through the other, of no
  impedance and no charge: the matrix singular (four LimeRFE pieces, "the direct solve at 0.1 GHz is not
  finite"). Now one cut a crossing (`cuts`: same net, layer, place to 1 um and direction), one port where two parts'
  pads meet, joined to both through an ideal junction (`node_block`, `nodes`), and mom25d keeps the loop/tree basis
  off where two via chains coincide (the RWG basis solves them; `FastOperator._coincident_chains`). A run frees
  each piece's geometry once it is solved or read back (most of the 7.6 GB a LimeRFE run's process held beside a
  27k-unknown piece's 15 GB: the run had met its 24 GB cap, OOM-killed at 36 min).
- **Part values written '<value>_<package>_<part number>' (2026-11-22).** '1000pF_0402_GRM1555C1H102FA01D',
  '0R_0402_RC0402JR-070RL': the leading value; 'NF', '0RNF', 'DNP', 'DNF' not fitted (no element, and segment
  chains no nets through it); a ferrite bead ('FB_600R_...') as its rated impedance. And a part's element no
  longer skipped as "bridged" by another net's copper in its gap, nor at 45 degrees by its own pads' overlap
  along the element's axis. A LimeRFE's 15 unmodelled series parts on its RF chains (DC blocks, its filters' 1, 27,
  91 pF, a fitted 0 ohm) checked against its BOM and now modelled; its 'NF' 0 ohm links (R90, R93, R374, R375:
  not assembled per the BOM) open; 2 left (a 100 uH and a 2.2 uH on supply nets, their values not written).
- **segment: a piece's reference planes where its traces are (2026-11-21).** The nearest layer whose ground covers
  half the area within 1 mm of the piece's copper, not the board-wide half (a LimeRFE's bottom RF lines over its
  PWR layer's ground, 35 % of the board, had been referenced to the layer beyond).
- **segment with `--pour keep`: nets grouped 1.5 mm apart** (`K2E_SEG_POUR_APART`; a LimeRFE receive piece's GCPW
  nets 1.5 mm and more apart coupled at -68.6 dB median: the stitched ground between them) instead of 20
  heights (10 mm on 20 mil RO4350B: 322 nets in one group), and the pour band 1.5 mm (`K2E_SEG_POUR_REACH`): that
  piece 67 s to 27 s, |dS| 0.006 to 0.007 against the pour to 6 mm on a fine mesh. `placed()`: pieces() leaving
  out a channel it cannot place.
- **Zone fills saved over other nets' copper (2026-11-21).** A fill made before a track was routed (the file saved
  without a refill) is cut back from other nets' tracks, pads and vias by the zone's clearance, as KiCad's
  refill would: a LimeRFE's GND pour lay over its 30 MHz RX line, which merged into the pour (12 fills there;
  none on the Thor or CM5 boards).
- **`nets = ['auto', ...]`**: the nets under the ports and the ground nets and those named. segment passes each
  channel's nets: a filter's middle nets (no port of their own) and their parts had been left out of the model.
- **segment: a channel left out, not the run**, where a port finds no reference copper (a bottom-side SAW
  duplexer over a void in the PWR layer) or a cut lands on copper the model gives another net (two nets' copper
  overlapping in the board file); each said.
- **A board's outline from a panel's Edge.Cuts (2026-11-21).** Loose ends of the outline within 0.1 mm are joined
  (a KiCad 4 board's hand-drawn edges met within 0.01-0.06 mm), and the face the parts are on is the board (a
  LimeRFE panel's outline had been a 91 x 3 mm strip between its frame and the board).
- **KiCad 4/5 boards with their copper layers renamed (2026-11-21).** A layer named as the user did ('T', 'GND',
  'C1F': KiCad 6 on keep 'F.Cu' and add the name after) is F.Cu / In<n>.Cu / B.Cu by its number, in the layer
  table and every layer field (a net of a layer's name keeps its own). A LimeRFE and a HackRF Opera Cake had
  come in with no tracks; now 10,728 and 1,091.
- **segment: open lanes (2026-11-21).** On the Thor carrier 29 of 92 channels came out open end to end. (1) A via in
  a connector's pad was skipped as "a port at its barrel" although its net's track left it on another layer: it
  is kept where the net's copper there is more than its ring. (2) A connector's group of via circles closed round
  a hole, and its edge (and a notch's tip touching a track) made cuts with the inside on both sides: the holes are
  filled and a crossing counts only where the track leaves the circle; a track vertex on the circle no longer
  makes two cuts (the camera lanes' four-port stars). (3) A cut port is nudged clear of the circle. (4) A pad port
  on several layers takes the layer its net's track meets it on (the longer run's). (5) A via's circle stops short
  of a series part's pads. A check before solving (`unrouted`) names the channels with no path through the
  pieces: 2 of 184 legs left on the Thor (a via in an AC capacitor's own pad: cuts follow tracks, not pads).
- **segment: a lane through its AC-coupling capacitor is one channel (2026-11-21).** Each net had been a channel of
  its own, its ends at the capacitor's pads: a 50 ohm port each side of it split the lane's power (the Thor
  carrier's USB-C TX halves at -5 to -7 dB) and read its through signal as crosstalk (DP2.TX0 "38 % FEXT from"
  DP2.TX0_C). Nets joined by a two-pad series part (C, R, L, FB) are now one channel, its ports at the chain's
  ends, named by the net at its best end; the part stays in the model, and such nets are kept in one piece
  (the Thor's 158 nets: 100 parts, 88 of them capacitors).
- **Big networks: the join and the modal S (2026-11-21).** segment's join held every frequency's S of all the
  pieces' ports side by side (the Thor carrier's ~3,500: 39 GB, killed at 24); it now goes a frequency at a time
  with the blocks as a sparse matrix and a sparse LU (the same S to 4e-15). coupling.modal's change to modes was
  a three-operand einsum, one loop of m^2 n^2 nf (4 minutes at 160 ports, never at 568): two products now (568
  ports' figures in 9 s). Board coupling's matrix goes through it too.
- **segment's stripline pieces: a parallel-plate mode (2026-11-21).** A layer piece between two planes kept them as
  whole sheets with nothing between: every port excited the plates' mode and every port picked it up, a floor of
  -40 to -46 dB of coupling at any distance (the Thor carrier's In2 camera lanes 1.5 mm and more apart). The piece
  now keeps the board's ground vias through both planes within 1 mm of its copper (`K2E_SEG_STITCH`): the floor
  falls to -67 dB median, the close couplings unchanged (-32 dB at 0.12 mm), the unknowns barely (124 vias).
- **segment: a stripline group past 24 nets** (`K2E_SEG_GROUP_MAX`) is grouped again closer, down to 2 plane
  spacings, and says so (the Thor's In7 bus, 64 nets chained at 3 spacings, 32.9k unknowns: five groups at 0.65 mm,
  the largest 23.8k); `--layer-solver auto` puts a piece past 20k unknowns (`K2E_SEG_MOM_MAX`) on the line model
  (its loss agrees within 0.1-0.5 dB, but it couples only traces within 5 heights: lanes' crosstalk is lost);
  segment.json records each piece's solver.
- **mom25d on the GPU: a lost device on a 158-port piece (2026-11-21).** The double-precision Z went up to the GPU
  beside four kept factors with no check of the room left; the driver spilled it to host memory, and a product
  that took 0.6 s took 2 s, then ran past the driver's 10 s watchdog (VK_ERROR_DEVICE_LOST). It now drops kept
  factors until Z fits, as a new factor does (and solves on the host where it still does not), and the GPU's
  products and solves go 32 columns a submission.
- **Figures of big networks.** The S-parameter figure past 12 ports draws the reflections and the 24 strongest
  transfers (a 158-port piece's 12,561 curves took 4 minutes); the line-model figure draws the 12 longest paths'
  profiles (871 made it 2,600 inches tall: 100 s, now 5).
- **segment on a 10-layer board (2026-11-21, the Jetson Thor carrier).** (1) A layer piece kept every layer's ground
  copper: a split or perforated plane is not solid to mom25d, which then meshed every plane above a stripline
  (the carrier's In7 bus: 625,819 unknowns); a piece keeps the planes next to its layer alone, each a whole
  sheet (28,083). (2) A stripline layer's nets are grouped at 3 plane spacings (its crosstalk falls as
  exp(-pi s / b)), not microstrip's 20 heights (In7's bus one group of 54 nets; now its largest is 20). (3) A port
  on a through-hole pin found no plane under it in its piece (the pin's ring had filled the antipad): the hole
  under it is closed with the plane's copper.
- **segment's net ends and cuts (2026-11-21).** (1) A port went on every pad a net's stretch ended on, so an ESD
  diode's stub was a 50 ohm load (USB's D701, D101), and an end on a short piece of track apart from the net's
  longest on its layer was missed (USB-C's D_N at the module): the two pads a net's own tracks and vias reach
  that are furthest apart, connectors and modules first, the rest left open. (2) A layer piece cut every net at
  every via circle: a pair's leg passing through its twin's circle lost its copper there with nothing joining it
  (now each net at its own vias' circles only). (3) A via near a port is a transition still: its circle takes
  the port in and the via model has it (a via skipped had opened HDMI's clock: -126 dB).
- **segment's mom25d via models were solved as sheets (2026-11-20).** They asked for `--mom-planes holes` with the
  whole stack, which mom25d does not take there, and the request turned the copper's thickness off.
- **segment.json's `port_nets` was a cut's two sides (2026-11-20)**, not the external ports' nets: the loop over the
  cuts reused the name of the ports' list.
- **mom25d's block low-rank solve ran the machine out of memory instead of stopping (2026-11-20).** Its plan did
  not count the via and copper-side blocks it fills whole (B and C, complex128: a CM5 layer's 37k edges and 10k
  sides, 7.6 GB), and on that path nothing compared the total with the memory available; the kernel killed the
  run. Both are counted now, and a solve over the memory available stops at once saying what would fit
  (`--mom-thickness grow`, a coarser `--mom-dmax`, smaller pieces). Below the frequency where its factor goes to
  double precision at a tight tolerance (nearly dense: a CM5 layer piece from 0.1 GHz), a factor that cannot fit
  stops too, naming the `--fmin` from which it is not needed.
- **Board coupling on a real board (2026-11-20).** The CM5 Minima's 36 high-speed nets (PCIe, HDMI, Ethernet,
  MIPI D-PHY, USB) found four faults. (1) The line model cut a trace in two where two of its pieces met at a
  point written two ways (0.1 um apart: two node names): Ethernet TRD0_N and TRD1_P passed nothing (12.7 %
  "mode conversion", now 0.03-0.25 %); a trace end takes the node already named within 0.1 um on its layer and
  net. (2) A port at its net's own via took that via's ring as its reference; with the via left out of the
  layer's board its antipad was a hole and mom25d stopped ("ends where there is no metal"): the hole under the
  port is closed with the plane's copper. (3) mom25d's automatic stack meshed every plane of the board (1.6 TiB on
  the first layer): a layer is solved over its own reference plane (`--mom-stack ground`). (4) A layer under its
  plane (B.Cu under In4.Cu) could not be posed to mom25d (its ground is below its copper): its board is turned
  over; a mirrored pair couples exactly as its top-side original (1.88 / 3.95 %).
- **Board coupling's line-model engine was never offered (2026-11-20).** Its check asked the solver
  registry for `lines`, a run's alias with no solver of that name, so the variant always read as unavailable
  and a choice of it fell back to mom25d: si_ddr's coupling took 9 min instead of 18 s.
- **An analysis's log on the Run page stood at its first lines (2026-11-20).** It was a second view of the
  page's document, which Qt never told of the text added (a scroll range of 0); the page's own log is shown
  now, and every log there follows its run while at its end, also behind another page.
- **The Report page was blank** before any report and after an S-parameter run (whose report is a page of its
  own); it says where a report comes from, and links that run's report. **A stopped run** read "ended with
  code 1 - see the log" and brought the Run page forward; it reads *stopped*. **OpenGL that cannot make a
  context** left the Viewer on an empty 3D view; the 3D view is disabled and the Plan shown (2026-11-20).
- **gfdtd's Vulkan kernels run on lavapipe, and CI runs them there (R-C7.2, 2026-11-20).** The energy kernel's
  loops over the lumped parts crashed Mesa's CPU Vulkan on a model with none: their arrays were null addresses,
  which lavapipe read through although the loops never ran. Absent arrays now point at a small zeroed buffer; the
  GPU tests pass on lavapipe (6, 3 s), and the stack workflow runs them after mom25d's.
- **gfdtd's `AddLumpedPort` took `edges2grid` as openEMS's (2026-11-20):** grid lines at the port's start and stop in
  those directions. Ignored, it had left the port between lines: openEMS's Simple_Patch_Antenna showed no
  resonance on gfdtd (S11 0 dB); now 2.435 GHz at -26.8 dB against openEMS's 2.435 GHz at -26.6 dB.
- **gfdtd read a polygon normal to y with its coordinates swapped (2026-11-20).** CSXCAD's X1, X2 run along the
  axes after the normal, cyclic (normal y: X1 is z, X2 is x); gfdtd had taken the two others in order (x, z), so a
  y-normal Polygon or LinPoly of an openEMS script sat transposed. kicad2ems's own models (z-normal) were not
  affected. Checked against CSXCAD's own bounds.
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
- **The line model's conductor loss from each section's cross-section (2026-11-27; ROADMAP line model 3).** With
  `--sigma`, scikit-rf's trace and coupled sections take R(f) and the internal inductance from their own cross-
  sections (`fastsolve.field_losses`: `core.lines.mqs` on the board's cut, or the section as classified). Each
  distinct one is solved at 3 frequencies a decade, interpolated as log-log and cached. A coupled group gets its
  full R matrix. This replaces Hammerstad and Jensen's microstrip loss (1.6× the 2D solve's) and 2 Rs / w on
  every other section (3× a stripline's). Up to 48 distinct cross-sections; above that the formulas stay, and the
  log says which.
  - S21's loss at 10 GHz: rf_stripline 0.566 → 0.189 dB, rf_msvia 0.197 → 0.128, rf_coupler 0.117 → 0.080,
    hairpin 1.02 → 0.69. It costs 0.2–1.4 s uncached on the examples (2–8 cross-sections), a few ms cached.
  - qucsator's RLCG sections take the cross-section's R at the band centre; its MLIN, MCOUPLED and CLIN keep
    their own loss.
- **cavities: the field round each via resolved, and the mesh's dispersion cancelled (2026-11-24).** A via's
  disc fanned out to far vertices: its loop inductance to a ground via 0.5-5 mm away was 36-71 % of the two-wire
  formula's (the linear elements miss the log field round a small disc); the ground vias shorted too much round
  them (a ground via 0.7 mm from a via had taken its peak down 12x, 7.7-8.6x converged). Now the mesh's smallest
  angle 30 degrees (cells graded round the discs) and each disc meshed at e^-0.15 of its radius (the linear
  elements' field round a disc is that of one 1.16x wider): the loop inductance within 2 % of the two-wire
  formula's over radii 0.1-0.25 mm, spacings 0.5-10 mm and cells to 2 mm. The mass matrix the consistent and the
  lumped averaged, whose dispersion errors cancel: a rectangle's 60 modes at lambda / 10 within 0.03 % (1.1 % with
  either alone). rf_msvia's six modes against Palace's 3D eigenmodes at the default cells: within 2.7 % (2.741 /
  2.741 GHz for the first; the old mesh there up to 4 % high). The graded discs only where they matter: every via
  that is no ground via's, and past 400 ground vias in a cavity those within 3 mm of one; each other ground via a
  vertex alone tied to the planes as a well (Peaceman: the inductance between the radius a vertex alone stands for
  and the barrel's), and the outline simplified to 0.02 mm. ThunderScope's five cavities (3,211 ground vias) then
  52-83k unknowns where every disc graded took 268k, their modes within 0.2-0.7 % of those (median; the old mesh's
  4-6 %), 60 s (163 graded, 46 before). Held at V = 0 alone such a vertex had moved the modes tens of %, and
  opening and closing the outline (0.1 mm) had lost the modes of its narrow strips. Two faults of the unknowns'
  numbering fixed on the way: a port off the mesh left an unknown of no vertex, and so did the refinement's stray
  vertices (both K + M singular). The reference boards again (docs/si.md): the Thor carrier's vias flagged in all
  five cavities now (`DP1.AUX_C+` 214 ohm at 8.84 GHz; before, 13 in In4.Cu/In6.Cu alone), 578 s with `--suggest`
  (50 ground vias suggested: their peaks then 3-4 ohm at the median). `--suggest` checks the cavity's two planes
  alone: a through via must clear every other layer too (said in its help and the docs).
- **mom25d: the block low-rank solver 3-11x faster in a fifth to a tenth of its factor's memory (2026-11-22).**
  GMRES's products were the exact matrix-free operator (the far field's kernels recomputed each product: 215 s of
  a 221 s solve on the Thor's In7 group, 24.9k unknowns, 106 ports, 5 GHz). Now its operator is the matrix
  itself, assembled as the factor's tiles are, in double to `BLR_OP_TOL` 1e-8 and not factored (`BLRFactor128(...,
  factor_it=False)`, `matvec`); where it would outgrow the matrix's dense half (its assembly stops early, a byte
  budget) the dense half, where neither fits the exact products, and at low frequency the exact ones (the loops'
  part of an entry is below 1e-8). The factor compresses every off-diagonal tile (weak admissibility:
  `sim.blr_eta` -1, was 0.3). At 5 / 6 GHz: the In7 group 221 -> 19.3 s, factor 1,076 -> 185 MB (23 -> 4 % of
  dense complex64), operator 1.7 GB (18 % of dense complex128); a LimeRFE piece with its ground pour (27.1k, 17
  ports) 59 -> 19.0 s, factor 4.7 GB -> 429 MB (84 -> 8 %), the dense half as the operator; S within 7.5e-7 and
  1.3e-7 of the direct solve's (12.5 and 8.3 s there). The operator at 1e-10 / 1e-9 / 1e-7 / 1e-6: 73 / 43 / 7 / 4 %
  of dense complex128, |dS| 2e-7 / 6e-7 / 1.1e-5 / 2e-4. **Tried and not taken: tiles by connected copper**
  (`tiles.cluster_groups`, `sim.blr_tiles = 'copper'`: no tile spanning two traces): on the In7 group the factor 7 %
  against 4 %, 3.6 GMRES iterations against 5.3, the operator 27 % against 18 %, |dS| 6.4e-6 against 7.5e-7.
- **mom25d: the loop/tree basis and the sides' loops by the half too (2026-11-22).** Their transforms need the
  whole matrix: it is filled in an anonymous mapping of its own (`half.zeros_mapped`, huge pages), transformed,
  then compacted into its half in place and the rest given back to the system (`Half.compact`, madvise); the
  products, the GPU's copy and the factor then the half's (kept whole where the sparse extras have an
  antisymmetric part: transformed it would be dense). The loop/tree basis's charge matrix is built by blocks of
  2,048 triangles straight into Z (`FastOperator.CHARGE_COLS`, the core's `charge_into` by column blocks), no
  longer Nt x Nt (5.2 GB at 27k unknowns). A LimeRFE piece (27.1k unknowns, 17 ports) at 1 / 2 GHz: peak 19.7 GB
  -> 16.5 GB whole (the charge matrix by blocks) -> 15.1 GB by the half, 13.1 / 10.7 s (whole 13.4 / 10.8),
  S within 6.5e-9; the loop/tree basis's need 16 N^2 now (24 N^2 and the charge matrix before), so ~35k pieces
  take it on this machine.
- **mom25d: the dense double Z by its lower half (2026-11-22; `MOM25D_HALF=off` keeps it whole).** Where the
  factor is the LDL^T and nothing needs whole rows (the RWG basis without the sides' loops or quad cells), Z is
  filled straight into `half.Half`'s layout (the compiled core's `fill_far_half`, `near_into_half`; the vias'
  border and the sparse extras' symmetric part into it, their antisymmetric rest in the products), its products
  by blocks (`Half.matvec`; on the GPU `zhalf_rows` and `zhalf_cols`, the mirror in chunks of 8 blocks), the
  factor's complex64 half made from it (`half_c64`, on the GPU `to_c64_hh`); the host's factor the LDL^T too
  (`half.ldl_factor`), the LU of a whole complex64 copy where it fails, the double fallback the half's own LDL^T
  in place (refined on the factor's products where the extras have an antisymmetric rest; where that fails the
  half is given up for the run). 12 N^2 bytes on the host for the LU's 24, 8 N^2 on the GPU for the double Z.
  **With it:** the refinement's products split between the GPU's copy and the host's from 16 columns
  (`policy.SPLIT_MIN`: with many ports both are bound by double arithmetic, about equal on an RX 7800 XT and 8
  P-cores; 25k unknowns, 106 columns: 1.17 s GPU, 1.3 s host, 0.7 s split), and the GPU's large buffers pooled
  for the next frequency (`VulkanBackend._pool`: slangpy releases a freed buffer late and the driver counts it
  used till then; after a 24.9k solve 7.4 GB of the 16 GB looked taken and the next plan left Z on the host).
  The Thor carrier's In7 group (24.9k unknowns, 106 ports): 12.6 / 8.2 s at 5 / 10 GHz, peak 7.7 GB, against
  14.8 / 10.9 s and 13.9 GB whole with the LDL^T and 22.1 / 14.6 s and 16.3 GB with this morning's LU; S within
  5.7e-8 of the whole Z's. A LimeRFE piece (11.8k, CPU) 9.0 -> 6.1 s at 6 GHz (3.3e-6). The near pairs of a
  triangle with itself take one moment for both sides (the rule's and the analytic integral's had been ~1e-5
  apart, all of Z's asymmetry): Z symmetric to 6e-16, S12 = S21 to 1e-15 on the sides' regression board (its
  S moved 1.9e-9). Checked: `tests/mom25d` (RX 7800 XT, GTX 980 Ti, GPU off; new: the half's path, fallbacks,
  extras and GPU kernels), `tools/mom25d_ldl_compare.py --what half`. **The hybrid by its half past 25k
  unknowns** (`policy.HALF_HYBRID_MAX_N`, 45,000; the whole Z's `HYBRID_MAX_N` stays 25,000): a LimeRFE piece
  (27.1k unknowns, 17 ports) 5.9 s at 6 GHz against 17.5 s matrix-free, S within 8.7e-8; `auto` takes it rather
  than the block low-rank solver there (the direct path was 15x faster than it on the In7 group).
- **mom25d: the GPU's factor is a complex-symmetric LDL^T (2026-11-22; `MOM25D_LDL=off` for the LU).** MoM's Z is
  complex symmetric, so the GPU factors its lower half alone without pivoting (`mom25d/gpu_ldl.py`, the layout
  `mom25d/half.py`: block columns of 256): the CPU factors each 256 x 256 diagonal block in double (the compiled
  core's `ldl_diag`, 0.5 ms), the GPU forms the panel's W = A21 L11^-T and L21 and updates each later block
  column from its own start (`syrk_half`), with look-ahead; the solves run on the GPU (the forward update a
  thread a row, the backward right-looking, 64 columns a workgroup through shared memory). Half an LU's work
  and GPU memory; the LU, pivoted, where it has no factor or does not precondition (that frequency and up to
  1.25 x it; a matrix-free plan's frequency filled again). The factor on an RX 7800 XT: 7k unknowns 0.108 s
  against the LU's 0.175, 14k 0.485 / 0.883, 20k 1.23 / 2.29; its solves at 20k 30 ms against 60 for 32
  right-hand sides, 17 / 21 for 8, 16 / 12.6 for one. The Thor carrier's In7 group (24.9k unknowns, 106 ports,
  fixed frequencies): 22.1 -> 15.1 s at 5 GHz, 14.6 -> 10.1 s at 10 GHz, peak 16.3 -> 13.9 GB, S within 5.9e-7
  of the LU's; antenna_patch (14k) 1.6 -> 1.2 s a solve (1.4e-8), a LimeRFE piece (11.8k) 3.2 -> 1.6 s at 6 GHz
  (5.3e-6), demo_transition 2.9e-8 (`tools/mom25d_ldl_compare.py`: the two factors at fixed frequencies).
  The matrix-free path's complex64 Z goes into the half in its own memory (`Half.compact`: no second matrix),
  the hybrid's double Z into a complex64 half (4 N^2 bytes, not the LU's 8 N^2 copy). Checked: `tests/mom25d`
  (343, on the RX 7800 XT, the GTX 980 Ti and with the GPU off; 33 new: the LDL^T's path and fallbacks on the
  emulated backend, the Vulkan factor against `half.ldl_factor` over many buffers, `half.py`, `Half.compact`;
  the Vulkan tests and `tools/mom25d/gpu_selfcheck.py`, which checks the LDL^T now, on lavapipe too). On the 980 Ti
  (NVIDIA's driver 580) the trailing update first hung: a column's pointer formed below its buffer and indexed
  from there, which RADV took.
- **mom25d: the loop/tree basis to (k h)^2 = 1e-3 (2026-11-22; was 1e-5; `MOM25D_LOOP_TREE_BELOW`).** The RWG
  basis's badly conditioned band (about 1-2 GHz on fine meshes) now goes to the loop/tree basis where its dense
  matrices fit. The Thor carrier's In7 group (24.9k unknowns, 106 ports): 16 / 15 / 16 / 13.5 s at 1 / 2 / 5 /
  10 GHz against 208 / 238 / 20 / 13.6 s, S within 1.4e-5; a LimeRFE piece (27k, 17 ports) 17 / 14 s at 1 / 2
  GHz against 129 / 130 s; GMRES 1-2 iterations a port. Checked: `tests/mom25d` (314, GPU on and off; eight tests
  of the RWG basis's fallbacks pinned to it by a fixture, `rwg_at_4ghz`), the examples demo_bandpass,
  demo_transition, hairpin, antenna_patch and demo_lowpass swept both ways (max |dS| 0 to 2.9e-6, demo_transition
  2.6e-3 from the sweep's own sample points: at fixed frequencies within 4e-8; `tools/mom25d_threshold_compare.sh`). Peak memory as before (it is
  taken only where it fits: ~20 GB at 25k).
- **`tools/mom25d_ldl_trial.py --lt-below`**: the loop/tree threshold for the trial. With it at 1e-3 the
  complex64 LDL^T without pivoting preconditioned as LU does at every frequency on three pieces (LimeRFE
  F_Cu_g50 11.8k, F_Cu_g24 14k, a Thor In7 window 7.4k; 0.3-10 GHz): 2 GMRES iterations a port in the loop/tree
  basis against LU's 1-2, 4-17 in the RWG basis against 3-16, half the factor's bytes (ROADMAP: the GPU LDL^T
  next).
- **segment: a channel's ends on two parts (2026-11-21).** A USB-C connector's two D+ pins (A6, B6: the plug either
  way up) had been a USB 2 lane's two ends, the hub it runs to left out (the Thor's USBC4_D -31 dB at 1 GHz).
- **segment: each net cut by its own vias' circles (2026-11-21).** A net's copper was cut by the whole via
  group's circle: a track passing other nets' vias in the group (the Thor's DP1 lanes along the SoM connector's
  28-via group) was cut in and out of it, each stretch a stub to the via model's barrel (DP1.TX2 -39 dB at
  10 GHz). Now each net is cut and clipped by its own vias' circles; that group's model went from 92 ports to 56.
- **segment: a series part's pad inside a via's circle (2026-11-21).** Its port is the via model's (the part a joint
  between the via model and the layer piece); it had been left in the mesh with its pad cut off into the via
  model, which holds no parts: the Thor's PCIe C2x1 clock and DP0 AUX lanes open end to end (0 of 184 legs open
  now; the four pads' via models pass -0.03 to -0.19 dB at 1 GHz).
- **segment: series parts as joints, microstrip groups capped (2026-11-21).** A series part (an AC-coupling
  capacitor, a resistor) is a joint between pieces: a port on each pad, joined through its own series R, L, C
  (`series_block`), the part out of the meshes; its nets are no longer forced into one piece (a part with a pad in
  a via's circle stays in the mesh, its nets together). Microstrip groups past 24 nets are grouped closer too,
  down to 10 heights. The Thor carrier's largest F.Cu piece: 17k unknowns and 237 ports (90 nets chained by
  breakouts across the board, 62 min) to 11.2k and 166.
- **An analysis's panel opens on its few settings (2026-11-20).** Options of level `basic` are a first section,
  open, as the solvers' form has them; the rest stay in their groups, closed. pcbqs names its tools': the rail's
  rail, ground, voltage and loads; the near field's converter, input net, ground, frequency and current; the
  temperature's losses, circuit and ambient.
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
