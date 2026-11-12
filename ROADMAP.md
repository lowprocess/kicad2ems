# Roadmap

What is worth doing next across the stack (kicad2ems, its window, mom25d and pcbqs), in priority order within each
part. Each item comes from something the examples, the benchmarks or a validation showed. Finished items leave
this file: what was done, measured and decided against is in a CHANGELOG.md (this repository's for kicad2ems, its
window and mom25d; pcbqs's for the power tools). C, I and U numbers are findings of pcbqs's
`docs/power-tools-review.md`; estimates are working days. Last updated 2026-11-12.

## The study: questions and engines

The registry is there (API 2.6, `kicad2ems.study`, docs/api.md#study): a study asks questions, engines answer
them, and a rail's drop, the temperature and capacitance each have two (pcbqs; Elmer or Palace through
kicad2ems-fem). A config holds a study (`[study]`) and `kicad2ems study` runs it (API 2.7, docs/usage.md#study).
The window still lists one row per analysis plugin and one for all of S-parameters. Left:

1. **The window (1.5 days).** A grouped tree, one row per question, the engine a choice on the row ("auto", one, or
   several to compare). The antenna is a row of its own; field dumps are an output ticked on the rows whose
   engine can write them. The Solvers section goes into the S-parameters row; Results compares engines for any
   question. The window reads and writes `[study]` and takes its run order from `study_plan`.
2. **Two engines' answers side by side.** A rail's drop is the rail's own copper for Elmer and the rail with its
   ground return for pcbqs (1.08 mV against 0.057 mV on the buck example, where like for like is 0.0579 against
   0.0573): each engine's report is right, but nothing says which of their numbers are the same quantity. An
   engine could give its answer's figures by name (`drop: C2, rail`, `hottest`, `part: D1`), per question, for
   a table of engines against figures.
3. **The KiCad plugin** (kicad2ems-ipc) starts `kicad2ems study` for a board whose config has one.

## The window, from a clean slate

kicad2ems-gui's `docs/redesign.md` (not started; about three weeks; it needs step 2 above). One fixed layout
around the board: the questions on the left, the board in the middle, the selected question's few settings on the
right, plots and reports in a drawer under it. The steps Board, Questions, Set up, Run, Results in the header with
one primary action; settings split into the few that matter and the rest; a list of what is still missing instead
of disabled buttons; a start page with the examples as a gallery; tools that are not about a board under Tools
(the impedance calculator is there already).

## The line model

1. **Coupled sections on the board's cut.** A coupled group is solved over ideal planes, without the copper beside
   it. In a trial on the CM5 Minima, stripline pairs moved by 3–10 % over 134 of their 258 mm; microstrip pairs
   did not (520 of 522 mm within 3 %). It needs one cut per group: `reference.cut` at the group's middle, out to
   the reach beyond both outer legs, snapped from the group's outer edges, its legs as the strips. `_couple` has
   to be given the reference copper for it. The legs' own cuts cannot be put together: each is snapped from its
   own leg's edges, so copper far from it lands on the other legs.
2. **The mesh's key lines.** Every copper edge and mask step runs across the whole box as a key line, so a thin
   column of cells runs the box's full height (it keeps the sides nested). Merging cells away from the corners
   needs constraints for sides that overlap without nesting.
3. **Boundary elements with a layered Green's function**, for the largest cuts (300–2,000 unknowns, no box), only
   if the mesh's 0.2 s at worst becomes the limit. `mom25d/multilayer.py` has the stack but a dynamic kernel; the
   static 2D one needs deriving.

## mom25d on multi-layer boards

mom25d is slowest where planes have to be meshed. Solid planes are PEC, lone stitching pads are left out and a
board imported with a few nets can have its pours' voids filled (`--fill-voids`). The model items below are worth
an order of magnitude more than the solver's: taking planes out brought rf_stripline from 100 s to 3 s.

1. **Z·V on the GPU in the reuse path** (small; medium confidence). After the LU reuse, demo_transition's CPU sweep
   is 83 s: 14 reused solves of 4.8 s each, 3.4 s of that GMRES products bound by the host's memory bandwidth.
   On the card the GPU column would go from about 72 s to 40–45 s, while Z fits there (~25k unknowns); uploading
   Z each frequency eats into it. It matters for real boards past ~10k unknowns.
2. **Planes a signal via passes through: an internal PEC plane with magnetic frills** (about 500 lines in
   `multilayer.py`, `via.py` and `sim.py`, and validation against openEMS: a via through a plane,
   demo_transition, rf_msvia).
   - The stack's TL network gets internal PEC interfaces. Every existing kernel then decouples the copper above
     from the copper below: no new horizontal kernels, and two blocks coupled only through the vias.
   - Each round antipad gets one unknown, the voltage across it: a magnetic frill, M_φ = V / (ρ ln(b/a)),
     radiating TM only (in the TM network a series voltage source at the shorted interface, its radial profile
     (J0(k_r a) − J0(k_r b)) / ln(b/a)). The via through the hole stays one chain. New kernels: frill to
     horizontal currents (order-1 Hankel), to via chains and to frills (order 0; the self term's coaxial part
     analytic). One more equation per frill: H_φ continuous across the annulus, tested with the frill's profile.
   - A plane qualifies when every hole is one via's antipad (a hole that is not round: the annulus of its area);
     slots and cutouts stay meshed.
   - Gain: demo_transition's 3,910 unknowns, 2,408 of them the plane, become about 1,500 (~18× less LU work;
     with the zone and quads 2,701 to ~1,100); a CM5 carrier's PCIe pair, 54,885 after filling its voids, about
     12k.
3. **Coupled runs in the mixed solver.** It cuts only lone traces into lines, and needs wave ports. 4-port coupled
   lines (`line_char_pair` exists) would take a pair's straight runs: about 40 % of that PCIe pair's length, whose
   unknowns scale with it (the pair alone: ~7k for 55 mm).
4. **What `--fill-voids` leaves.** `far` keeps a void near the signal copper on every layer, also behind a solid
   plane (14 × 32 mm of the CM5 Minima: 33,088 unknowns, 25,662 with `all`, 42,689 as imported). Whether those
   can go is not settled: on a crop, `all` moved a resonance of the planes the region cut (gfdtd's |S21| at
   10 GHz from −1.6 to −4.0 dB). It needs a board whose planes are not cut. The KiCad plugin's selected-net runs
   could default to `far`. Optionally, a ground pour on a signal layer cut a few heights from the signal copper.
5. **Boards past ~30k unknowns**, only if they remain after 2–4. `solver='hodlr'` is about N^2.5 and wins by
   memory first (its measurements: CHANGELOG.md). Towards N log N, in order:
   - **the near field's reach.** 5 cells is not converged everywhere: against 8, rf_diffvia moves by 0.060 in |S|
     at 1.0 GHz and rf_interdig by 0.040 at 3.36 GHz, not monotonically in the reach. Find what is sensitive
     there (the low-frequency conditioning is the suspect for rf_diffvia), then cap the reach at a distance, so
     ACA compresses from O(N r) entries;
   - **a loop–star (or loop–tree) basis:** the low-frequency conditioning sets GMRES's iterations and amplifies a
     compressed factorisation's error (10³–10⁴); perhaps 10–30 % on today's boards; a sizeable change;
   - **strong admissibility** (H-LU, or HODLR in a nested-dissection order), so the ranks stay bounded: 50k+
     fits, perhaps N^1.5–2 in time;
   - **an FFT-based operator (pFFT / AIM):** a planar board's Green's function depends on the lateral distance
     only, so its far field is a 2D convolution per pair of interfaces, with the near correction mom25d has and a
     loose HODLR as GMRES's preconditioner (11 iterations at 1e-4 on demo_transition). The route to 100k+: a
     large effort, and still short of a cheap preconditioner;
   - HODLR's symmetry (Zᵀ = Z: half the compression, untested); the GPU for its products and its leaves.

## Power tools (pcbqs)

`pcbqs.pdn` (a rail's DC drop and impedance), `pcbqs.mqs` (a converter's near field), `pcbqs.circuit` and
`pcbqs.power_circuit` (switching circuits on the board's copper), `pcbqs.thermal` and `pcbqs.sparams` (the
ports' S-parameters at the low end, a solver of kicad2ems). What exists and how it was
validated: pcbqs's docs and CHANGELOG.md, and the project's `NOTES-pi-nearfield.md`.

### Correctness and workflow

1. **Settling that sees slow filter modes (C8; 1 day).** Track the cycle-mean drift of the supply current; average
   the input power over whole slow-mode periods, or extrapolate the decaying mode (vector extrapolation of the
   per-cycle state) instead of simulating it out.
2. **The bridge model without wpthb in the code (the rest of C7, U1; 0.5 day).** The precharge goes by what the
   parts join, but wpthb's own branches and resistances are still in `power_bridge`: move them into the
   template's data, or take the start from a DC operating point of the actual input network.
3. **The Circuit page from `--describe` (I4, U2, U5; 1.5 days).** Generate its form from the tool, as the other
   analyses' are; per-group parasitic overrides; default to the template's switch model and warn on `switch`
   with ngspice. Its own result tabs repeat the Results page, and a bridge run's `power-circuit.json` is not yet
   an analysis's result.
4. **Cross-tool flows (U4, I3; 2.5 days).** A near-field converter as a circuit template (its parts, its loop's
   copper); a circuit's input-current spectrum as PDN loads; `pcbqs.mqs`'s assumed-pulse fields against the
   circuit's `--fields`.
5. **Several rails in one power-integrity run** (a config's `[analysis.pdn]` is one rail).
6. **Converters.** A broader buck input-loop discovery; ambiguous bias, enable and supply nets resolved by part
   metadata; the switch node's E in the converter workflow (the Coulomb E of the surface charges is there, the
   inductive E is not) and output-inductor models; decap and via mounting inductance from the quasi-static solver
   instead of the formula.
7. **Low-frequency S-parameters, the rest.** `--solvers pcbqs` is there (pcbqs's `docs/sparams.md`). Left: the
   run's lumped parts between their pads (the quasi-static solver takes elements already, so a decoupled rail or
   a lumped filter would have its low end too; today they are left out and listed as deviations); gap ports; the
   two-face copper with the charge is slow on a stack of several dielectrics (25 s a frequency at
   15,000 triangles on demo_transition, 77 s at 9,000 on pcbqs's return-path example), and a narrow trace's
   capacitance as one sheet is what keeps a matched line's S11 a few dB high at the top of the band. In the
   window, the Results plot draws a log sweep (`sweep = "log"`) on a linear axis in GHz: a log axis and the
   band's own unit, as the report's plot has.

### Speed

A 30 W wpthb case is about 56 s: two strict transients of a 369-equation circuit, 81 % of ngspice's time in
refactorisation (pcbqs's `docs/power-circuit.md`).

| Option | Gain | Effort | Notes |
|---|---|---|---|
| Slow-mode extrapolation (item 1 above) | 1.5–3× on lightly damped filters | 1 day | as-built wpthb settles slowest |
| A piecewise-linear switched state-space engine | 10–100× for ideal-switch studies | 1–2 weeks | the copper model and passives are linear: exact matrix exponentials per switch state; device effects need ngspice |
| Xyce backend | robustness, not speed | 2 days | it ran behavioural switches ngspice could not; 2–7× slower on the benchmark converter; harmonic balance could suit resonant tanks |
| Two faces in the circuit: a local loop basis | fewer iterations, no dense setup | | iterations grow with frequency (217 at DC, 575 at 20 MHz); the dense common-mode setup is 19 s |
| Whole-board near field | | 3–5 days, only if a board needs it | a full ground plane is 50k+ triangles (a dense L: ~20 GB): compression and an iterative solve in the loop basis. A crop around the converter is enough for DC-DC loops |

The quasi-static solver's 10× target is not yet shown on real boards, and an FFT field grid's convergence has to
be checked for each scan.

### Accuracy and validation

In order:

1. **Thick copper and AC resistance (C14).** `conductor_loss='slab'` is two face sheets; stacked sheets with the
   thick kernel are the reference. Open:
   - a trace close over a plane, the largest error: one sheet is 19–30 % low in R, two faces were 12.5 % low when
     last compared, the reference is within 5 %. The validation board's second structure decides;
   - the 2–3 % drop in inductance with face sheets;
   - side charge in a layered dielectric (it is homogeneous only), and the tree preconditioner with face sheets;
   - the compressed backend as the default for `slab`, once the CM5 loop agrees;
   - one sheet or two in the circuit: two move wpthb's settled ripple by 6 % at 2.6 times the time, which the
     powered ripple cannot resolve until the parts are vendor models. It needs a measurement that isolates the
     copper (item 4's jumper variant, or the validation board).
2. **Capacitance in narrow gaps (0.5 day).** Two traces 0.1 mm apart are −4 % self and −11 % mutual as sheets,
   −5.5..−7.5 % and −10..−13 % with thickness: the edge charge's resolution within memory (the compressed backend,
   or graded edge rings).
3. **A real converter loop, converged.** CM5 U702 (`validation/mqs_cm5.py`): one sheet is within 4 % of Palace's
   PEC sheets at the finest meshes, both still converging; two meshes differ by up to 3.8 % in L. The Jetson's
   eight bucks run, no more. Mesh and crop convergence per board, with measured local refinement before any
   whole-board compression.
4. **Measurements (the plan is ready; waiting on a VNA).** `validation/measurements.md`: shunt-through at TP7 / TP6
   unpowered (as built, and with one 1206 jumper for the ceramics), VHB ripple at the regression operating point,
   the near field at 1–3 mm; predictions in `validation/wpthb_vhb_port.py`. A purpose-made validation board:
   `validation/test-board.md` lists its structures (no layout or predictions yet).
5. **Models.** Vendor or fitted MOSFET models (MCG8D2N06YL), datasheet capacitor ESR and ESL, measured coil Q;
   switch internal capacitances as explicit elements, so a warm start can initialise them; package and gate
   geometry, the receiver's and the leads' field geometry, capacitance to the environment, the fields at startup.
6. **A complete PEEC model up to about λ/10:** resolved proximity currents, via skin loss, mutuals between parts
   and copper, material dispersion.
