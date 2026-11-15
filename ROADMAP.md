# Roadmap

What is worth doing next across the stack (kicad2ems, its window, mom25d and pcbqs), in priority order within each
part. Each item comes from something the examples, the benchmarks or a validation showed. Finished items leave
this file: what was done, measured and decided against is in a CHANGELOG.md (this repository's for kicad2ems, its
window and mom25d; pcbqs's for the power tools). C, I and U numbers are findings of pcbqs's
`docs/power-tools-review.md`; R numbers (R-A1, R-D12, ...) are the whole stack's review of 2026-11-14,
`REVIEW-2026-11-14.md` in kicad2ems-project, which has each one's evidence; estimates are working days. Last updated
2026-11-14.

## Fix first (the review of 2026-11-14)

Answers that are wrong with nothing to say so, and a workflow that does not run: all done (2026-11-14). The
review's items are tracked, in the order they are worked through, in the review tracker sheet.

The errors that said nothing (R-B) are done (2026-11-14) but for Palace's cost estimate before a run (B21).

## CI and release

What the CI leaves out is where most of the list above came from.

1. **The runs CI skips, the rest** (R-C6, C8, and Elmer for C2). CI installs ngspice, kicad2ems-fem and pcbqs's
   compiled core since 2026-11-14. Left: Elmer built once and cached (about 2 min), so kicad2ems-fem's Elmer runs
   are tested too; a real FDTD engine through kicad2ems-fdtd (a small board on `gfdtd:cpu`); the window's run
   against a stand-in is tested since 2026-11-14 (`test_the_window_s_own_run_starts_and_ends`).
2. **Pins, the rest** (R-C5: emsdev is pinned by its release tag in all five pyprojects since 2026-11-14, and
   `sync_pins.py` checks it). kicad2ems-fdtd, gfdtd and dfsynth have no commit in the extras that name them; the KiCad
   plugin's `requirements.txt` is behind and checked by nothing.
3. **The GPUs** (R-C7). No GPU path runs in CI (Vulkan on lavapipe could), and no NVIDIA run is recorded, though the
   docs name any GPU.
4. **References** (R-C9, C10):
   - no stored S-parameters to regress against, and mom25d's README figures are tighter than its tests;
   - pcbqs's real-board regressions need `~/Desktop/wpthb`;
   - the edge-ring validation scripts live outside the repository.
5. **A release** (R-C12, C16, C17, M). Semantic versions (0.3.0, tagged), the authors, the LICENSEs and Triangle's
   notice are done (2026-11-14). Left:
   - PyPI, which refuses direct-URL dependencies (not yet: the user's call, 2026-11-14);
   - the KiCad plugin's PCM entry and its v0.1.0 tag;
   - a benchmark that runs today's stack (benchmark-results is from 2026-11-08 and imports modules that are gone).

## The study: questions and engines

The registry is there (API 2.6, `kicad2ems.study`, docs/api.md#study-api): a study asks questions, engines answer
them, and a rail's drop, the temperature and capacitance each have two (pcbqs; Elmer or Palace through
kicad2ems-fem). A config holds a study (`[study]`) and `kicad2ems study` runs it (API 2.7, docs/usage.md#study);
the window lists the questions, with the engine chosen on each row, and compares two engines' named figures
(API 2.8, docs/gui.md#the-study). Left:

1. **S-parameters compared per engine in the same way.** Two solvers' S-parameters are overlaid on the Results
   page as before (and scored in the report); the *Engines compared* table is for the analyses' named figures.
   A solver could give figures too (|S21| at the band's centre, a resonance, a line's delay).
2. **Engines with different settings for one thing.** pcbqs's rail tool takes `rail`, Elmer's `net`: asked both,
   the same rail is typed twice. A question could say what it needs (the net, the source, the loads) and each
   engine take it from there.
3. **The KiCad plugin** (kicad2ems-ipc) starts `kicad2ems study` for a board whose config has one.
4. **Ports and parts the solvers share** (R-E1, R-E5).
   - De-embedding and a reference plane: lumped ports carry their feed (about 0.1 nH, docs/model.md), the FDTD
     model has lumped ports only though gfdtd has line ports, and mom25d's lumped ports carry their via.
   - Ports and parts at angles other than x and y.
   - Internal ports at the parts' pads, with the parts embedded after the solve: value sweeps and vendor
     S-parameter parts without a new solve, then a `[sweep]` table. There is no sweep or batch driver today.
5. **Results the solvers give, the rest** (R-E2). Passivity is checked and warned of, `--passive` writes a passive
   copy (singular values clipped to 1), and mom25d's sweep says when it stopped at its solve cap. Left: causality. An
   impulse response's energy before t = 0 does not tell it on band-limited data (from 1 GHz): causal openEMS answers
   read up to 53 % there (the rat-race's S11), time-reversed ones as little as 35 %; it needs a band-limited
   Kramers-Kronig check.
7. **Materials** (R-E4). KiCad's stackup `material` names are not read and a missing value defaults without a note: a
   small library (RO4350B, FR4 grades) with each one's dispersion and the frequency its εr is given at.
8. **A run done again** (R-D20): no result is reused (a hash of board.json, the frequencies and the solver's
   options), and the solvers of one run go one after another.
9. **Board cutouts** (R-E6) stay dielectric, and a region that splits the board keeps its largest piece without a
   note.

## Signal integrity

The crosstalk question (`kicad2ems.si`, docs/si.md) is the first. Left, in the order a board would want them:

1. **Differential pairs as pairs.** Crosstalk between pairs in their differential and common modes (mixed-mode
   S of the pairs' four ends), a pair's own skew and mode conversion; the proposals then name pairs, not legs.
2. **Drivers and receivers.** A source impedance and a receiver's load in place of `z0` at every end (an
   unterminated receiver doubles what arrives; NEXT reflected at the driver arrives at the far end), and an eye
   for a bit pattern (the edge response is there: superposed).
3. **A trace's own quality:** its impedance along its length (the line model has it: *Trace impedances*), the
   reflections of its discontinuities for the edge (TDR from S11), its loss at the data rate.
4. **The coupled stretches drawn on the plan**, coloured by their share (the traces are picked on the board
   since API 2.11).
5. **The time grid** (R-E16). The edge response comes from 401 points at fmax/400 steps, linearly interpolated: a
   5 % coupler reads 3.3 % at 1 m and 25 ps. Its step from the longest trace's delay, H interpolated with the delay
   taken out, fmax 2-3 times 1/rise, S11 to DC (for TDR), before items 2 and 3 build on it.
6. **Broadside pairs** on adjacent layers: the groups are of one layer, so such a pair is not coupled at all (it
   reads "not modelled" once Fix first's item 3 is done).

## The window, from a clean slate

kicad2ems-gui's `docs/redesign.md`. The few settings first and the list of what a run still needs are built.
Its fixed layout was built and taken back (2026-11-12, the user's word): the settings belong on the left, under
the questions, and the window keeps its docks, as a studio application's. What follows is to be read with
that: nothing in it may fix a panel in place. Left:

1. **The study as a model without Qt, the rest** (its step 1). `study.StudyModel` has the questions' side
   since 2026-11-13 (what is asked, who answers, the Fields ticks, availability, the runs' states; the Study
   dock's list is its view, and it imports no Qt). Left: the settings (each analysis's panel and the solvers'
   still hold their values), the board and the runs in progress, which are `app.py`'s 2,800 lines. Nothing
   visible changes; it is what makes the rest cheap, and what kicad2ems-ipc could drive.
2. **An analysis's panel with its few settings first**, as the solvers' are (an options row may say `level`;
   pcbqs's tables do not yet).
3. **Picking on the board: what is left.** Nets and parts are picked (an option's `pick`, API 2.11). Not yet:
   a pad (a port: `REF.PAD`, or a point), a `REF=VALUE` list's part (a rail's loads, a part's loss), a region
   dragged as a box, and the pick in the 3D view. The Circuit page's own file buttons go.
4. **The window's pages, the rest.** Built 2026-11-13, as docks (the user's choice among three mock-ups): the
   Start page with the examples as tiles, Plots, Report, Tables and Log as pages of their own, Section as one
   too (the Viewer's buttons are Plan, 2D, 3D, Images), and the steps as a bar under the toolbar that moves to
   the bottom and hides. Left: *Images* may belong on a page of its own as Section does (asked, not decided);
   a tile says nothing of what its example takes (the engine, the time: the index has neither). (*Reset layout*
   with a floated dock aborted only under Qt's offscreen plugin on Windows; on the native platform it works,
   and the CI checks it there.)
5. **What the review found** (R-D21, E24, E25).
   - Failed and stopped runs should stay listed with their log (it lives only in the page today); a run can be
     deleted.
   - The 3D view recolours all 24 phases on the GUI thread at each step of a control (3.2 s and 1.4 GB at 256).
   - Desktop basics:
     - a dropped board, config or results folder;
     - a dialog for an exception in a slot;
     - the run's whole log;
     - its exit signal named;
     - the settings' undo;
     - dark plots;
     - the Report saved or printed;
     - a Touchstone export.
   - Left from the removed bridge: the schematic's format-1 layout code.
   - The Circuit page imports pcbqs into the window, against `toolprocess.py`'s rule.
   - `ResultsTab.show(files)` overrides `QWidget.show`, the same kind of clash as the Start page's.

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
5. **Coupled lines with a jog** (R-E17). The legs are paired per element, so a polyline's sub-segments are not, and
   dfsynth's edge-coupled band-pass is open on the line model.
6. **Discontinuities** (R-E15). The scikit-rf backend (qucsator's absence: often Windows) has none, so open stubs are
   short by their end's extension; qucsator has nothing for a 45° bend or a junction of five arms. dfsynth has
   Kirschning-Jansen-Koster open ends, Garg-Bahl gaps and Edwards steps to port.
7. **Dispersion and the losses' next step**: a Djordjevic-Sarkar εr(f) for each layer before the solve; the
   conductor loss from the cross-section (Wheeler's rule: two solves with the copper set back by half a skin
   depth) in place of 2 Rs / w, and each harmonic's in qucsator (its RLCG holds R and G at the band centre).
9. **One multiconductor model** (R-F5). dfsynth's (vectorised, with conductor loss) and `coupled.mtl_s` (looped,
   lossless) are two; so are the Touchstone readers.

## mom25d on multi-layer boards

mom25d is slowest where planes have to be meshed. Solid planes are PEC, lone stitching pads are left out and a
board imported with a few nets can have its pours' voids filled (`--fill-voids`). The model items below are worth
an order of magnitude more than the solver's: taking planes out brought rf_stripline from 100 s to 3 s. The
solver's own items (large boards, the low end) are in the next section.

1. **A plane a via runs through, as the default.** `--mom-planes holes` has the plane as PEC and each hole a
   few unknowns (`mom25d/frill.py`; docs/mom.md#plane-holes): its axisymmetric field and, since 2026-11-13, its
   first azimuthal order, which a trace over the hole drives. It is within 0.005 in |S| of the plane meshed
   finely on the transitions tried (closer than the plane meshed with kicad2ems's usual cells is), and as close
   to openEMS as the meshed plane on demo_transition, for a third of the unknowns or fewer (3,910 to 1,508; a
   transition alone 11,256 to 1,661; two side by side 11,972 to 2,938; a CM5 carrier's PCIe pair, 54,885 after
   filling its voids, about 12k). Before it can be the default: the loop/tree
   basis with the holes (the low end is the RWG basis's on such a board today); a hole two vias share (a
   differential pair's antipad: most pairs on real boards) and holes that are not round (their area's circle
   today); the second azimuthal order where holes nearly touch (0.012 at 10 GHz with 0.1 mm between them); a
   plane on the top interface; the hole's field in the dumps, the near field and the far field.
2. **Coupled runs in the mixed solver, the rest.** Two traces of one width side by side are cut as a coupled
   line since 2026-11-13 (their differential and common modes, a mixed-mode wave port on each face:
   docs/mom.md): 3,926 unknowns to 1,892 on a 40 mm pair, within 0.006 of mom25d. Left: a pair's faces need
   0.4 rad of feed at the lowest frequency, so a short pair or a low band leaves nothing to cut (rf_crosstalk,
   40 mm from 0.5 GHz); lumped ports on the faces in place of wave ports would lift that, as they
   would the lone lines' (the solver needs wave ports there today); three or more traces together (a bus: one
   line a mode, from the field solver's modes) and pairs of unequal widths; a pair that bends (the cut stops at
   each bend, and a serpentine has many).
3. **What `--fill-voids` leaves.** `far` keeps a void near the signal copper on every layer, also behind a solid
   plane (14 × 32 mm of the CM5 Minima: 33,088 unknowns, 25,662 with `all`, 42,689 as imported). Whether those
   can go is not settled: on a crop, `all` moved a resonance of the planes the region cut (gfdtd's |S21| at
   10 GHz from −1.6 to −4.0 dB). It needs a board whose planes are not cut. The KiCad plugin's selected-net runs
   could default to `far`. Optionally, a ground pour on a signal layer cut a few heights from the signal copper.

## mom25d: low frequency, large boards (the user's plan, 2026-11-13)

Against Momentum, what mom25d lacks is not a quasi-static mode but a well-conditioned low end and a direct solver
that does not need N² memory. In this order:

1. **pcbqs's loop/tree basis, ported.** Done (CHANGELOG.md; docs/mom.md#loop-tree): below (k h)² = 10⁻⁵ the
   direct solver's loop/tree basis, which the block low-rank path hands these frequencies to where its dense
   matrix fits, with a double-precision factor where it does not. Left: **a large board at low frequency in
   compressed form.** The loop/tree basis's paths are not local, so its matrix does not compress by tiles; the
   double factor is right but nearly dense. The way is the current-and-charge form (the augmented EFIE:
   unknowns I and q, blocks jωA, DᵀP, D, jω: each compresses at an ordinary tolerance and none cancels), its
   tiles holding the currents and charges of one place. Also left: the tiled solver, quad cells and line
   functions, holes of PEC planes; the S extraction's floor below ~10 Hz (currents a million times the ports':
   S from the loop and tree amplitudes, not from the summed currents).
2. **A compressed direct solver in the compiled core.** Done as a block low-rank LU (CHANGELOG.md;
   docs/mom.md#blr): compiled, OpenMP, SciPy's BLAS and LAPACK, the GMRES preconditioner of the exact operator;
   from 20,000 unknowns wherever the GPU's hybrid does not hold the matrix (rf_siw's sweep 272 s against 520 s,
   antenna_cp_patch 22.5 s a solve in 2.4 GB). Left: GMRES's matrix-free product (its far field one kernel pass
   over every triangle pair), for which a compressed operator at a tight tolerance was looked at on 2026-11-13
   and not built: with a factor made at the frequency GMRES takes 3 to 7 exact products, so the product is not
   what a fresh solve costs; it would pay only where a factor is kept across frequencies and the iterations
   grow, which the path does not do yet (that reuse first, then the operator). The near-field correction's
   sparse build (3-4 s at 22-41k); pivoting only inside the diagonal tiles; a
   strong-admissibility H-LU for 100k+ (BLR's dense near band grows as N), the near field's reach capped (ACA from
   O(N r) entries), and an FFT operator (pFFT / AIM) past that.
3. **The band divided between the two solvers.** Done (`<name>_joined.sNp`: pcbqs below the crossover, mom25d
   above, the crossover where they agree best at or below pcbqs's `valid_to_Hz`; CHANGELOG.md).
4. **A quasi-static mode in mom25d, ranked low.** Building the matrix once saves time only if building it is
   what costs. Profiled 2026-11-13 (kicad2ems's defaults, GPU): the per-frequency build (Green tables, fill, near
   correction, via kernels) is 39-50 % of a sweep on the mid-size examples (4-9k unknowns: demo_transition 3.9 s
   of 10.3, rf_diffvia 10.5 of 20.8), 36 % on antenna_vivaldi (21.9k: 66 s of 184; LU and refinement 92 s) and
   6 % on rf_siw (28.7k: the tiled factor 417 s of 520). The larger the board, the more the factorisation
   dominates: the compressed direct solver (item 2) is the bigger gain. If a quasi-static mode is built, it belongs
   in mom25d, on a static version of its layered Green's function with the PEC ground (`GreenTableML`): that, not
   a mode, is what lets Momentum's RF mode go past λ/10 without meshing the planes.

## mom25d: what the review found (2026-11-14)

1. **Wave-port set-up, the first run** (R-D10). A port's line is a 3D model of its own: on
   `tests/boards/launch.json` 14.4 s of a 14.6 s set-up. It is kept on disk by cross-section since 2026-11-14 (a run
   again: 0.5 s); the first run's cost is left, for a 2D cut of the feed (Zc and gamma) to take.
2. **The via blocks, what is left** (R-D11): since 2026-11-14 the chains' kernel is in the compiled core, the fans'
   product sparse and the pairs' geometry made once (317 vias: 1.36 s a solve for 3.43); since 2026-11-15 the
   holes' kernels are the core's J0/J1/J2 contour sums (the demo with `planes='holes'`: 0.78 s to 0.52 s of its
   hole blocks). Left: the holes' tables are made again at every frequency (their Sommerfeld integrals; 87 ms a
   hole's frequency on the demo), and the fill is not overlapped with the GPU's factor, which would hold two
   dense Z at once (the memory plan has one).
3. **Copper thickness**, ignored (sheets) while the other solvers model it; the loss takes the port layer's
   thickness for every layer. Per interface first; two sheets with side currents later (`sing.py` names the
   kernels).
4. **Fewer solver paths** (R-F3). About twelve, each with its own failure flag: HODLR is never chosen, the Python
   BLR only with an old core, the tiled path lost to BLR. Retire those and split `fast.py`; dead tools and
   `biot.h_grid` (pcbqs's API) go.
5. **The copies shared with pcbqs** (R-F4) drift both ways: mom25d's singular-rule fix (the moment up to 1.3 % off)
   is not in pcbqs, pcbqs's mesh-quality cap is not in mom25d. A shared numerics package beside emsdev, or a CI
   report of the drift.
## Power tools (pcbqs)

`pcbqs.pdn` (a rail's DC drop and impedance), `pcbqs.mqs` (a converter's near field), `pcbqs.circuit` (switching
circuits on the board's copper), `pcbqs.thermal` and `pcbqs.sparams` (the
ports' S-parameters at the low end, a solver of kicad2ems). What exists and how it was
validated: pcbqs's docs and CHANGELOG.md (the project's `NOTES-pi-nearfield.md` is its history, stale since).

### Correctness and workflow

1. **A load with no pad on ground, the rest.** The rail takes the return of a load that is on the rail and not
   on ground (a bridge's high-side switch: wpthb's Q1 and Q2, flagged by the user on 2026-11-12) through the
   one part of its kind on its other net and on ground, and says so; where the board shows none, the report's
   headline says the return was left out and the window asks for it before the run (pcbqs's CHANGELOG). Left:
   a return that is not that direct, a low-side switch on ground through a sense resistor (the search stops at
   the switch's own pads, so such a bridge is asked about), and two parts in parallel as one return.
2. **Cross-tool flows, the rest.** A near field's converter opens as a circuit, and a circuit's supply
   current goes to its rail (the ripple it makes) and to the near field, each a button on the result (pcbqs's
   `docs/power-workflow.md`); the full bridge's runs say what their high-side switches draw too, so wpthb's
   bridge current reaches its rail. Left: a controller with its two switches as separate parts is not made into
   a circuit (the regulator's own footprint is taken for both); a rail's ripple at a converter's pin needs the
   input loop's own inductance, which the near field solves and the rail's planes do not have (the two could
   share it). The circuit examples walk the steps now (the buck: its near field, *Open Q1 as a circuit*, its
   rail), and a converter's circuit has its supply and load on the board's connectors; what is left of the
   example is the circuit's proposal naming the connector as the rail's source, so that nothing is typed between
   the runs (drafted with a regulator's board of its own, not applied).
3. **Several rails, the rest.** A config holds them, `kicad2ems study` runs them, and the window has them as a
   table (the user's choice, 2026-11-13). Left: their drops side by side in one report (each run's is its own
   today), each row's state in the questions' list (the study's is the last run's), and a rail's own board or
   output folder (the table's rows share the panel's).
4. **The full bridge as a general circuit.** Done 2026-11-14: `pcbqs.power_circuit` and `power_bridge` are gone, the
   wpthb bridge is a circuit model (pcbqs's `data/wpthb_bridge.json`: leg B's phase a target for the load's power,
   the receiver fixed at its 90 kHz values; the per-frequency tuning was dropped on the user's word). On the board
   with copper it gives the old tool's answer (93.11 % for 93.13 %, phase 55.66° for 55.77°). Left: its speed on
   ngspice (40 s alone, 1 min 47 s with copper, for the old tool's 6 s and its precharged, warm-started runs).
5. **Converters.** The near field finds a regulator IC and, since 2026-11-13, a discrete switching cell (a
   buck's, a boost's, a bridge's legs: pcbqs's CHANGELOG). Left: a flyback (its loop runs through the
   transformer) and an inverting buck-boost (through both capacitors), which are not found; a circuit made of a
   bridge found that way (a buck and, since 2026-11-13, a boost are made into one; a bridge's leg has no one
   inductor on its node: its tank or its transformer is the model's to draw); ambiguous bias, enable and supply nets
   resolved by part metadata; the switch node's E in the converter workflow (the Coulomb E of the surface
   charges is there, the inductive E is not) and output-inductor models; decap and via mounting inductance from
   the quasi-static solver instead of the formula.
6. **Low-frequency S-parameters, the rest.** `--solvers pcbqs` is there (pcbqs's `docs/sparams.md`), with the
   run's lumped parts between their pads since 2026-11-13 (a lumped filter has its low end). Left: gap ports; the
   two-face copper with the charge is slow on a stack of several dielectrics (25 s a frequency at
   15,000 triangles on demo_transition, 77 s at 9,000 on pcbqs's return-path example). (A matched line's S11
   was high for want of the edge charge, which is solved on graded edge cells since 2026-11-13.) In the
   window, the Results plot draws a log sweep (`sweep = "log"`) on a linear axis in GHz: a log axis and the
   band's own unit; the report's interactive plot is linear in GHz too (its PNG is right), and its table reads
   "0.000 GHz" for a run in kHz (R-G).
7. **What a PI user takes away** (R-E14):
   - the port matrix (loads and decap sites) as Touchstone or a SPICE subcircuit;
   - the droop of a load step in time;
   - vendor decap models and MLCC DC-bias derating;
   - a VRM beyond R + L;
   - the near field's input capacitors from their own fields, as the PDN reads them (one `--esr`/`--esl` for all
     today);
   - an impedance for a rail with no planes (the PEEC route is the trial's only: one source, one ground, no beads).

### Losses and thermal

The circuit is there for its parts' losses; what thermal makes of them. Done: junction temperatures and limits
(E7), the copper's loss into thermal (E8), the fields' reuse of the circuit's copper (D3), and on 2026-11-15 the
electro-thermal runs (`--warm`, E9), an inductor's AC and iGSE core losses (E10), a map per sweep point (E11),
vendor models with any pins and KiCad's `Sim.*` fields (E12), and the surroundings: a part's own path to the air,
held footprints, moving air and the heating from cold (E13). Left of them:

- a film that grows with the rise (natural convection and radiation), and air warmed by one part reaching the next;
- the heating from cold on the mesh, and a transient of changing losses (a load step);
- the iGSE's DC-bias and relaxation terms, and minor loops;
- the DC drop's copper at its temperature (the circuit's is, with `--warm`).

### Speed

The wpthb bridge (`data/wpthb_bridge.json`) at 90 kHz and 10 W: 25 s on ngspice alone (40 s before the knob runs were
shortened, 2026-11-14), 1 min 47 s with its copper before that.
The circuit run is there for its parts' losses (thermal) and its copper's currents (fields); ngspice is its one
engine.

| Option | Gain | Effort | Notes |
|---|---|---|---|
| A piecewise-linear switched state-space engine | — | | Built 2026-11-13 and removed 2026-11-14: 3–30× faster than ngspice on ideal switches, but on the wpthb bridge's copper either no faster and wrong on the switches' losses (46 mW for 96: no gate charge, no square law, constant Cds), or right and hours a run with the gates and the junctions as states. The run is for losses and switching nodes: ngspice's job |
| Xyce backend | robustness, not speed | 2 days | it ran behavioural switches ngspice could not; 2–7× slower on the benchmark converter; harmonic balance could suit resonant tanks |
| Two faces in the circuit: a local loop basis | fewer iterations, no dense setup | | iterations grow with frequency (217 at DC, 575 at 20 MHz); the dense common-mode setup is 19 s |
| The dense solves, after their assembly | the LU is what is left: 90 % of a two-face S-parameter solve (147 s at 17,700 unknowns) | | The assembly, the thermal maps' sampling and the potential's solve are in the compiled core or on Cholesky since 2026-11-13 (pcbqs's CHANGELOG; `tools/perf/README.md` has where each tool's time goes). Left, none of it pcbqs's own Python: a compressed or block low-rank LU as mom25d has; ngspice's text output read by `np.loadtxt` (a binary raw file); the compression of field dumps; shapely's unions in the near field's mesh |
| Whole-board near field | | convergence | `--backend hierarchical` (since 2026-11-14) is GMRES in the loop basis (47,548 triangles in 13 s, docs/quasistatic.md). Left: mesh and crop convergence on a whole board. A crop around the converter is enough for DC-DC loops |
| The pencil, with `dc` loss (R-D13, the rest) | one factor a sweep | M | `Rl + jωLl` is a pencil: one eigendecomposition and Woodbury for the parts. The single-precision LU with refinement is done (2026-11-14) |

The quasi-static solver's 10× target is not yet shown on real boards, and an FFT field grid's convergence has to
be checked for each scan.

### Accuracy and validation

In order:

1. **Thick copper and AC resistance (C14).** `conductor_loss='slab'` is two face sheets; stacked sheets with the
   thick kernel are the reference. Graded cells at the copper's edges are most of the error away from a plane
   (two faces 1.6 mm over one: −9 % at 10 MHz against FastHenry, from −26 %) and a third of it close over one
   (0.1 mm: −14 %, from −18 %); `pcbqs.sparams --thickness` has them since 2026-11-13, the circuit's copper and
   the near field not yet (their meshes are the pads' and the loop's: to be measured there first). Open:
   - a trace close over a plane, the largest error: one sheet is 19–30 % low in R, two faces were 12.5 % low when
     last compared, the reference is within 5 %. The validation board's second structure decides;
   - the 2–3 % drop in inductance with face sheets;
   - side charge in a layered dielectric (it is homogeneous only), and the tree preconditioner with face sheets;
   - the compressed backend as the default for `slab`, once the CM5 loop agrees;
   - one sheet or two in the circuit: two move wpthb's settled ripple by 6 % at 2.6 times the time, which the
     powered ripple cannot resolve until the parts are vendor models. It needs a measurement that isolates the
     copper (item 4's jumper variant, or the validation board).
2. **Capacitance in narrow gaps, with thickness.** Graded edge rings resolve the edge charge (2026-11-13:
   two sheets 0.1 mm apart −2.5 % self and −3.6 % mutual against FasterCap, from −10 % and −23 %, on a third of
   the triangles; `pcbqs.sparams` uses them with the charge). Left: the same with the copper's thickness (face
   sheets and side charge were −5.5..−7.5 % and −10..−13 % against FasterCap's boxes, on plain cells), and
   whether the rings should be `MQS(capacitance=True)`'s default.
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
7. **Palace 0.19, the rest.** It is built with Spack here, kicad2ems-fem's plugin runs with it (CHANGELOG.md),
   and kicad2ems-fem's own Palace runs pass on it (2026-11-13: the capacitance tool's microstrip against
   Hammerstad-Jensen, a grounded box, the driven runs with a series part). Left: pcbqs's Palace validations
   (`validation/`), which were made on 0.18.1.

## FDTD (gfdtd, kicad2ems-fdtd)

The roadmap had no FDTD section; the review's, in order (R-A12, B17, B18, E3, D4-D9, C6, C7):

1. **The guards, the rest.** An unstable run is caught (energy over 10 times its driven peak) and said by both
   engines' runs, and lumped parts gfdtd's update cannot hold are refused (2026-11-14). Left: gfdtd taking a part's
   ESR in one series RLC (an implicit update for R and C without L), so kicad2ems need not split such parts for it.
2. **Speed without new physics.**
   - The operator's core and its compressed form are made once a model, and the worker threads (with their GPU
     devices and pipelines) kept, since 2026-11-14. *Decided* (2026-11-15, R-D4.2): no one shared device. A
     worker's device and pipelines cost about 1 s once (0.2-0.5 s and 0.5-0.9 s, four threads at once on the
     7800 XT) and nothing on its later runs; the threads make theirs at the same time, so one device would save
     little of the wall time, and slangpy gives a device one queue, so the runs would no longer overlap.
   - A node-local time step: gfdtd's is up to 15 % shorter than openEMS's on the same mesh (hairpin 105.9 fs
     against 124.9).
   - εr averaged inside a cell, so a thin layer needs no lines of its own (`nz_mask` is 1 since 2026-11-14: 4 cells
     through a 10 µm mask had made hairpin 17× longer for "within 1 MHz").
   - The energy read a check late, the ports' decay judged in the engine, dumps written by a thread.
3. **Memory.** The build peaks at ~470 B a node on the host (float64 arrays kept through the run), 16× what the GPU
   needs, and nothing checks free VRAM before a run or caps the concurrent runs by it.
4. **Line ports** (MSL, stripline, CPW: gfdtd has them) with a de-embedded plane where a straight trace meets the
   port.
5. **Excitations batched** in one launch, and every GPU emsdev finds used.
6. **Tests**: a small board on `gfdtd:cpu` end to end, openEMS's S-parameters stored as references; Vulkan on lavapipe
   in CI; one NVIDIA run before the docs name CUDA.
7. **Boundaries**: first-order Mur is the default on five sides (6 % reflection against 5e-5 for an 8-cell PML), and
   the PML is CPML with κ = 1, α = 0, at λ/res cells: CFS-CPML, and thinner graded cells.
8. **Larger boards**: every copper edge is a line across the box. Subgridding or local refinement, temporal
   blocking, conformal PEC for curved copper.
9. **Materials**: Lorentz and Drude, anisotropy; openEMS's Debye run has 0.5 dB more loss than gfdtd's on the same
    material (which is right is not checked).
10. **openEMS's interface** (R-B18): options gfdtd does not have are dropped without a word (`TimeStepFactor`,
    `TimeStep`, `CoordSystem`, field probes, magnetic σ); Gaussian is the only excitation; time-domain NF2FF,
    `MaxTime`, `SetAbort` and a coax `Feed_R` are not there.

## kicad2ems-fem

1. **Palace's eigenmode** (R-E19): the resonances of a can or a high-Q filter, the one use the notes give Palace;
   `rf_shielded`'s reference is EMerge's today.
2. **Palace's own accuracy** (R-E20):
   - its adaptive refinement from `feature = 1`;
   - a second-order absorbing boundary or a PML for antennas (efficiency reads up to 100.5 %);
   - wave ports;
   - copper as solids in the driven model (as the cap tool's `--thick`): its sheets' loss holds only to ~10 MHz.
3. **Elmer's harmonic and transient solvers** (R-E21): a coil's AC resistance, its ferrite's loss and Q at the
   operating frequency; a transient temperature.
4. **pcbqs's Palace validations as engines** (R-E22): `pdn_palace.py` and `mqs_palace.py` as `palace_pdn` and
   `palace_loop`, a second engine for the two questions whose accuracy is open; Palace stays out of pcbqs's
   runtime.
5. **Real boards** (R-B21, B14, D23). Palace's `feature_size` looks at 60 polygons a layer and nothing is simplified:
   the Elmer builder's fragmenting, a gap per edge, and an `estimate()` (demo_lowpass at the default feature: 19 GB,
   30 min). `elmer_dc` in tetrahedra where the layered wedges solve the same drop (1.1 M tetrahedra against 40 s and
   1.3 GB for the whole wpthb board); `tag_terminals` rewrites the mesh file in Python; `mag` runs n(n+1)/2 solves.
   Elmer's timeout is not caught, Palace's non-convergence not read, the versions not recorded, the MPI ranks fixed
   at 8.

## The KiCad plugin (kicad2ems-ipc)

Besides the study (the study's item 3), R-E26 and C16:
- only S-parameters, though the analyses and the picking are there;
- nothing goes back into KiCad (a map on a user layer, the region and the ports as items, markers at the worst
  places);
- an edited board needs Reload;
- its environment builds gfdtd without its core or a GPU (~15× slower) while the README quotes a GPU's time;
- the Plugin and Content Manager route its README gives does not exist yet (no entry, `"VERSION"` in its metadata, no
  v0.1.0 tag).

## dfsynth

R-E18:
- The calibration looks at the band's edges only. On dfsynth's own lossless model the hairpin's worst return loss is
  5.5 dB (FR4) and the interdigital's 9.0 dB, where the README still gives 16.4. The interdigital's match-aware
  refinement should go to the hairpin, coupled and band-stop designs, with a return-loss test for each.
- Then, in order:
  - a `.kicad_mod` to place in one's own board;
  - the tapped hairpin feed;
  - combline;
  - elliptic and cross-coupled designs.

## Docs that do not say what the code does

The review's section G, corrected on 2026-11-14 but for these:
- the line model's `conductor_loss = yes` (with Fix first's item on its losses);
- the superproject's stack map (`img/kicad2ems-stack-map.svg`), which has neither pcbqs nor kicad2ems-fem;
- mom25d's README validation figures, tighter than its tests check (CI and release, item 4).
