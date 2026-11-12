# Roadmap

What is worth doing next across the stack (kicad2ems, its window, mom25d and pcbqs), in priority order within each
part. Each item comes from something the examples, the benchmarks or a validation showed. Finished items leave
this file: what was done, measured and decided against is in a CHANGELOG.md (this repository's for kicad2ems, its
window and mom25d; pcbqs's for the power tools). C, I and U numbers are findings of pcbqs's
`docs/power-tools-review.md`; estimates are working days. Last updated 2026-11-12.

## The study: questions and engines

The registry is there (API 2.6, `kicad2ems.study`, docs/api.md#study): a study asks questions, engines answer
them, and a rail's drop, the temperature and capacitance each have two (pcbqs; Elmer or Palace through
kicad2ems-fem). A config holds a study (`[study]`) and `kicad2ems study` runs it (API 2.7, docs/usage.md#study);
the window lists the questions, with the engine chosen on each row, and compares two engines' named figures
(API 2.8, docs/gui.md#study). Left:

1. **S-parameters compared per engine in the same way.** Two solvers' S-parameters are overlaid on the Results
   page as before (and scored in the report); the *Engines compared* table is for the analyses' named figures.
   A solver could give figures too (|S21| at the band's centre, a resonance, a line's delay).
2. **Engines with different settings for one thing.** pcbqs's rail tool takes `rail`, Elmer's `net`: asked both,
   the same rail is typed twice. A question could say what it needs (the net, the source, the loads) and each
   engine take it from there.
3. **The KiCad plugin** (kicad2ems-ipc) starts `kicad2ems study` for a board whose config has one.

## The window, from a clean slate

kicad2ems-gui's `docs/redesign.md` (not started; about three weeks; it needs step 2 above). One fixed layout
around the board: the questions on the left, the board in the middle, the selected question's few settings on the
right, plots and reports in a drawer under it. The steps Board, Questions, Set up, Run, Results in the header with
one primary action; settings split into the few that matter and the rest; a list of what is still missing instead
of disabled buttons; a start page with the examples as a gallery; tools that are not about a board under Tools
(the impedance calculator is there already).

## The line model

1. **Nets reached only through the field.** A net is in the model when a port sits on it or a series part leads
   to it. A combline filter's inner rods are neither: the line model has no pass band on `rf_combline`, and a
   coupled section's cut takes such copper as ground. Copper that lies between or beside coupled lines and is
   not a ground net should join the model as a line of its own (its ends: vias and parts to ground, or open).
2. **A coupled section's cut along its length.** It is cut once, across its middle. Copper beside it for part
   of its length (a via's pad, a pour that ends) counts for all of it or not at all: cut at a few places and
   split the section where the cuts differ, as single sections are split where their reference changes.
3. **The mesh's key lines.** Every copper edge and mask step runs across the whole box as a key line, so a thin
   column of cells runs the box's full height (it keeps the sides nested). Merging cells away from the corners
   needs constraints for sides that overlap without nesting.
4. **Boundary elements with a layered Green's function**, for the largest cuts (300–2,000 unknowns, no box), only
   if the mesh's 0.2 s at worst becomes the limit. `mom25d/multilayer.py` has the stack but a dynamic kernel; the
   static 2D one needs deriving.

## mom25d on multi-layer boards

mom25d is slowest where planes have to be meshed. Solid planes are PEC, lone stitching pads are left out and a
board imported with a few nets can have its pours' voids filled (`--fill-voids`). The model items below are worth
an order of magnitude more than the solver's: taking planes out brought rf_stripline from 100 s to 3 s.

1. **A plane a via runs through: the trace over the hole.** `--mom-planes holes` has the plane as PEC and the
   hole's voltage as an unknown (`mom25d/frill.py`; docs/mom.md#plane-holes), with an axisymmetric field in the
   hole: exact for the pads (their capacitance within 0.6 % of the meshed plane's), but a trace crossing the
   hole's rim sees the plane closed under it (0.03 in |S| at 5 GHz, 0.065 at 10 GHz for a 0.45 mm hole and 0.3 mm
   pads; demo_transition's S11 against openEMS −27.7 dB for −32.3). What it needs is the hole's first azimuthal
   order: field shapes in cos φ and sin φ, which are TE as well as TM (the network's other mode, tested on the
   edges as a field, not a potential). Then it can be the default, and holes that are not round (their area's
   circle today) and a plane on the top interface can follow. Gain as measured: demo_transition 3,910 unknowns to
   1,504, a transition alone 4,978 to 1,656 (nine times faster); a CM5 carrier's PCIe pair, 54,885 after filling
   its voids, about 12k.
2. **Coupled runs in the mixed solver.** It cuts only lone traces into lines, and needs wave ports. 4-port coupled
   lines (`line_char_pair` exists) would take a pair's straight runs: about 40 % of that PCIe pair's length, whose
   unknowns scale with it (the pair alone: ~7k for 55 mm).
3. **What `--fill-voids` leaves.** `far` keeps a void near the signal copper on every layer, also behind a solid
   plane (14 × 32 mm of the CM5 Minima: 33,088 unknowns, 25,662 with `all`, 42,689 as imported). Whether those
   can go is not settled: on a crop, `all` moved a resonance of the planes the region cut (gfdtd's |S21| at
   10 GHz from −1.6 to −4.0 dB). It needs a board whose planes are not cut. The KiCad plugin's selected-net runs
   could default to `far`. Optionally, a ground pour on a signal layer cut a few heights from the signal copper.
4. **Boards past ~30k unknowns**, only if they remain after 1–3. `solver='hodlr'` is about N^2.5 and wins by
   memory first (its measurements: CHANGELOG.md). Towards N log N, in order:
   - **the near field's reach, capped at a distance.** The correction's pattern reaches 5 cells, 15 mm under
     3 mm plane cells, but beyond about 3 mm it is under 5e-4 of its largest: capped there, ACA would compress
     from O(N r) entries (the reach itself is converged: 5 cells against 12 move |S| by 0.0025 at most on
     rf_diffvia and rf_interdig);
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

1. **A load with no pad on ground ("Q1: no ground pad").** A rail's load that sits on the rail and not on ground
   (a bridge's high-side switch: wpthb's Q1 and Q2 on VIN) has its return left out: the rail drop is then the
   power side alone (`ground_rise` 0: 49.7 mV for Q1 where the ground's share is missing) and only a note in
   the result says so, with the way round it (`--load Q1=0.67@Q4`, the part its current returns through). The
   user met it on wpthb (2026-11-12) and asked for it to be kept in view. To do: find the return by itself (from
   the load's other pads through the parts on those nets to one with a pad on ground: the low-side switch of
   its half bridge, a sense resistor), say in the report's headline when a return was left out, and have the
   window list it under "Still needed" for the Rail drop question ("Q1 has no pad on ground: name the part its
   current returns through") instead of running without it.
2. **Cross-tool flows (U4, I3; 2.5 days).** A near-field converter as a circuit template (its parts, its loop's
   copper); a circuit's input-current spectrum as PDN loads; `pcbqs.mqs`'s assumed-pulse fields against the
   circuit's `--fields`.
3. **Several rails in one power-integrity run** (a config's `[analysis.pdn]` is one rail).
4. **Converters.** A broader buck input-loop discovery; ambiguous bias, enable and supply nets resolved by part
   metadata; the switch node's E in the converter workflow (the Coulomb E of the surface charges is there, the
   inductive E is not) and output-inductor models; decap and via mounting inductance from the quasi-static solver
   instead of the formula.
5. **Low-frequency S-parameters, the rest.** `--solvers pcbqs` is there (pcbqs's `docs/sparams.md`). Left: the
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
