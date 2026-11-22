# Roadmap

What is worth doing next across kicad2ems and its parts (`kicad2ems.{core,gui,fdtd,gfdtd,mom25d,pcbqs,fem,ipc,
dfsynth,emsdev}`), in priority order within each section. Each item comes from something the examples, the
benchmarks or a validation showed. Finished items leave this file: what was done, measured and decided against is
in CHANGELOG.md (pcbqs's history before the merge: docs/pcbqs/CHANGELOG.md). C, I and U numbers are findings of
docs/pcbqs/power-tools-review.md; R numbers (R-A1, R-D12, ...) are the whole stack's review of 2026-11-14,
`REVIEW-2026-11-14.md` in kicad2ems-project (the superproject before the merge), which has each one's evidence;
estimates are working days. Last updated 2026-11-19.

## CI and release

1. **References** (R-C9, C10):
   - gfdtd, mom25d and the line model regress against openEMS's demo_lowpass (`tests/data/`); a board with
     wave ports and one with vias would cover more, and mom25d's README figures are tighter than its tests;
   - pcbqs's real-board regressions need `~/Desktop/wpthb`.
2. **The GPUs** (R-C7): run before a release instead of in CI (`tools/gpu_report.py`, docs/testing.md); CI runs
   mom25d's Vulkan LU and gfdtd's Vulkan kernels on lavapipe. Left: an NVIDIA run in the report (the GTX 980 Ti is
   listed; Vulkan's runs take the AMD card, CuPy runs on it too); the report's mom25d boards are small enough to
   factor on the CPU, so a board past the GPU's threshold.
3. **A release** (R-C12, C16, C17, M). Left:
   - PyPI, which refuses direct-URL dependencies (not yet: the user's call, 2026-11-14);
   - the KiCad plugin's PCM entry and its v0.1.0 tag.

## The study: questions and engines

The registry is there (`kicad2ems.core.study`, docs/api.md#study-api): a study asks questions, engines answer them,
and a rail's drop, the temperature and capacitance each have two (pcbqs; Elmer or Palace through the fem part). A
config holds a study (`[study]`) and `kicad2ems study` runs it (docs/usage.md#study); the window lists the
questions, with the engine chosen on each row, and compares two engines' named figures (docs/gui.md#the-study).
Left:

1. **S-parameters compared per engine, the rest.** The run's report has *Solvers compared* (each solver's figures:
   each port's worst match and its dip, each transmission at the band's centre and its lowest, with their spread)
   and `study.compare` takes solvers too (2026-11-20). Left: the window's *Engines compared* table for the
   S-parameters question (it asks the analyses alone), and a resonance's or a line's delay as figures.
2. **Engines with different settings for one thing.** pcbqs's rail tool takes `rail`, Elmer's `net`: asked both,
   the same rail is typed twice. A question could say what it needs (the net, the source, the loads) and each
   engine take it from there.
3. **The KiCad plugin** starts `kicad2ems study` for a board whose config has one.
4. **Ports the solvers share** (R-E1): mom25d's wave window (6 cells + max(2 cells, 4 w, 3 h, 1 mm)) refuses the
   examples' 4-6 mm feeds, where the FDTD's probes stood 3 mm in; the FDTD's line ports on pairs and oblique traces
   (FDTD, item 3).
6. **A variation's figures in the window and the report**: `<name>_vary.csv` and the log set them side by side
   (2026-11-20); the window's *Compare with* still overlays the curves alone.
7. **Solvers side by side, overlapping** (R-D20): `--solver-jobs N` runs them as threads, but gfdtd's GPU stepping
   records its kernels from Python and holds the GIL, so a solver beside it waits; processes of their own, or
   gfdtd recording many steps for each Python call, would let them overlap.

## Signal integrity

The crosstalk question (`kicad2ems.core.si`, docs/si.md) is the first. Left, in the order a board would want them:

1. **Differential pairs as pairs, the rest.** Board coupling and the crosstalk question take a pair as one trace in
   its differential mode (2026-11-20; board coupling with its own mode conversion). Left: a pair's skew (its legs'
   delays), and the common-mode noise its neighbours put on it (in the modal S, not yet reported).
2. **A board's coupling, the rest** (`kicad2ems.core.coupling`, docs/si.md#coupling, 2026-11-20). On a real
   board (the CM5 Minima's 36 high-speed nets, 18 pairs; 2026-11-20) the line model answers in 4 min; mom25d does
   not fit yet: each layer's group still carries its share of the signal layer's ground pour and stitching vias,
   and the last run was stopped by the machine running out of memory with nothing logged (its plan does not count
   the signal zone with the pour cut near the group: to check by a size-only preview per group). **Being replaced
   by the segmented solve below.** Also left: what crosses between layers (vias, a plane's gaps, broadside
   traces), a net that forks on a layer (its longest path there is the stretch), a port inside an antipad but off
   its via's ring (no reference copper under it on the full board: the question stops), and the window's view of
   the matrices.
3. **A board solved in pieces (the user's plan, 2026-11-20): vias as ports.** Each signal layer's traces alone
   over ideal planes (mom25d's Green's function: the planes not meshed), a port wherever a trace meets a via or a
   pad; each kind of signal via (its pads, antipads, stub and the ground vias around it) a small model of its own,
   cached by its geometry; the pieces joined as one network: each net's S end to end across its layer changes,
   and the crosstalk through the vias. Validated against whole-board full-wave runs on `rf_diffvia` and the DDR
   example's lane 1, then the CM5 Minima. Its limits to report: a plane's cavity between sparse stitching vias,
   apertures under a trace (the ideal plane has none), broadside coupling between layers. **Started 2026-11-20**
   (`kicad2ems.core.segment`, docs/si.md): rf_msvia and rf_diffvia within the whole board's spread of solvers.
   Left: the extra reflection near 10 GHz (2-3 dB in S11: the via model's, to find), DDR lane 1 (no port
   footprints: its ends), crosstalk figures from the joined S (as Board coupling's), the window. **On the CM5
   Minima with mom25d's direct factor it is not practical** (2026-11-20, set aside by the user): its 18-net F.Cu
   piece is 29k unknowns, 49 min and 19.6 GB (the block low-rank factor did not converge at 1-1.5 GHz), the board
   hours. **With `--edges coarse` (2026-11-20) the whole board runs on mom25d: 6 min 25 s, 3.8 GB peak**, 13
   layer pieces (4-43 s each) and 12 via models (12-47 s; the 28-port one had not fitted 24 GB on the fine
   mesh): the 36 nets' 73-port S end to end. Next: its figures in each pair's differential mode (as Board
   coupling's matrix; single-ended at 50 ohm the 100 ohm pairs read -13 dB between HDMI lanes), the Ethernet
   pairs' -2.6 dB match and USB-C's reflection looked into (parts left out: ESD diodes, connectors), and a check
   of one interface against a cut-out solved whole (done for PCIe's clock: Sdd21 within 0.13-0.15 dB rms of the
   cut-out, either via model; the board's other interfaces to check the same way). **Via models without a mesh:**
   the physics-based model (`--via-solver pbm`, viamodel.py) is in; mom25d's plane holes on a whole stack were
   studied (2026-11-20): lifting the `holes` gate for whole stacks (board.py:532) and adding the cavity's
   transfer admittance between holes of adjacent planes (frill.py:376 returns 0 there) solved rf_msvia's
   through via on PEC planes in 983 unknowns against 6,597 (S21 within 0.06 dB, the static two-plane
   capacitance within 0.8 % of meshed planes); about half the unknowns on the CM5's via models (their outer
   layers stay meshed). Left there: the RF part above 5 GHz, power planes, and a pair's shared or oval antipads
   (a via through a slot opening, 1-2 weeks). **Tried on the CM5's via1 (2026-11-21): no plane qualifies**, so
   it meshes them all as before (6,301 unknowns): In1.Cu and In4.Cu hold each pair's two vias in one antipad, and
   In2.Cu / In3.Cu are split planes (GND and +5V: 3 and 10 pieces in the box). On a real board's high-speed vias
   the quick version does not apply; it needs the via-through-an-opening work and split planes. The
   physics-based via model covers these vias now (a shared antipad and split planes are only capacitances and
   cavities to it). Measured and set aside: edge cells only near the signal
   copper (the via models' plane edges: 10 % fewer unknowns on the fine mesh, 1 % on the coarse), and an
   aggressor's copper fine with its victims coarse (rf_coupler: between the two meshes in unknowns and in S31,
   which all three give within 0.2 dB of gfdtd; and a run per aggressor). **With `--layer-solver lines`** its layer pieces take 2 min and the via models are the
   cost: 9-33k unknowns each (the whole stack's planes in their boxes, D-PHY's rows of 8 vias one model), 33 s
   to 23 min, the largest over 24 GB. On gfdtd (`--via-solver gfdtd`) as accurate on the test boards (rf_msvia
   S21 within 0.14 dB of the whole board, rf_diffvia Sdd21 0.078 dB rms against mom25d's via model's 0.110) and
   light (2.5 GB), but not faster on the CM5 (via1 983 s against 1358, via3 179 s against 44; via12's 28 ports
   ~2.4 h): its fine cell came down to 19 um (the cut circles' short edges, a part, C705, left in the via box), a
   0.018 ps time step, 87k steps a port, a 5 mm air margin. To try: the via box's parts left out, its fine cell
   at the via's own scale, a smaller margin. Next: identical via geometries solved once (most are a pair's two vias of
   one size), the planes as PEC with their holes where the via stays on one side of them, a row of vias split
   where its circles only touch. A purpose-built trace mesher was measured first and set aside: the CM5 piece's
   unknowns are 80 % edge rings at 65 a mm of trace, and neither fewer rings (more triangles: the area limit
   splits the wider strips), coarsening nor quads lowers them; an outline simplified at 5 um saves 18 %.
   **The Jetson AGX Thor carrier (2026-11-21, the user's scale target: 10 layers, 22k tracks, 3.3k vias)**: its 158
   high-speed nets (79 channels, 568 ports) in 78 layer pieces and 118 via models, on this machine. On the way:
   the layer pieces had meshed every split plane above a stripline (626k unknowns; now the two adjacent planes
   as sheets), stripline groups at 3 plane spacings with a cap of 24 nets (the In7 bus chained 64), the stripline
   pieces' parallel-plate mode (-45 dB coupling at any distance until the board's ground vias went in), a lost GPU
   device at 158 ports (the double Z spilled out of VRAM), and figures of 158 ports that took 6 minutes. The line
   model was measured as a stand-in for the biggest stripline piece and set aside: its loss within 0.1-0.5 dB of
   mom25d, but none of the lane-to-lane coupling (it couples within 5 heights). **The run itself (2026-11-21):
   4 h 19 min and 20.9 GB with the big groups whole (the In7 bus 3 h 21 min: 106 ports), 1 h 02 min and 12.5 GB
   with them in windows** (In7: 8 windows of at most 9.8k unknowns), the 91 connected channels' insertion loss
   within 0.04 dB of the whole groups' and each victim's worst crosstalk within 0.008 points. **With every
   connectivity fix in (2026-11-21): 59 min, 12.7 GB, all 92 channels connected end to end** (median loss
   -0.33 / -1.05 / -2.41 dB at 1 / 5 / 10 GHz, the worst crosstalk 0.83 % NEXT), the overlay page, the worst
   couplings and where along the victims they come from (the breakouts at connectors and ICs, not the long
   striplines). Left: SFI_REFCLK's -3.5 dB at 1 GHz (to look at), the USB 2 lanes' stubs at 10 GHz (out of
   their band), a check of one interface against a cut-out solved whole as on the CM5.
   The In7 bus piece (24k unknowns, 66 ports): 60 EM solves of ~70 s (the sweep at its cap, not converged).
   **Tried and set aside (2026-11-21): each entry's delay taken out before the sweep's fit** (replayed on that
   piece's S, 16 ports): 72 solves to converge without, the 80-solve cap unconverged with (2.7e-2 off; the
   through paths' alone 8.6e-2): the set-valued AAA's poles are common to all entries, and one delay per entry
   breaks that (and an entry is several paths' sum). The solves are the long lines' resonances. Left to try on
   the solve's cost: the stages timed at 24k (the double Z and the factor no longer both on the GPU?), a
   complex-symmetric LDL^T (half the memory: both would fit), single-precision products on the GPU; and the
   windows (being validated). The fit itself is ~4 min of the 73 (the SVD of a 60-sample, 4356-entry fit:
   13 s; its rows' build vectorised gained nothing).
   **Speeding up large boards further (2026-11-21; the quick wins are in: windows' core ports alone driven,
   `--lean` pieces, small pieces in parallel).** In order of payoff: (a) straight coupled runs as analytic
   multi-line sections (an N-line 2-D cross-section of all the lines together, exact for TEM) with MoM only at the
   discontinuities (breakouts, bends, vias): the Thor's In7 bus is mostly straight parallel stripline, and the
   coupling profiles put the crosstalk at the breakouts; the line model's coupling (only within 5 heights) is
   what to widen. (b) A complex-symmetric LDL^T factor on the GPU: half the memory and work, so at ~24k unknowns
   the double Z and the factor fit together (the In7 piece's ~N^4.2 is that fall-back). (c) The block low-rank
   factor's convergence below 1.5 GHz (it is off in segment's pieces): a ground plane's smooth kernel compresses
   well. (d) Identical pieces solved once (a geometry hash up to a shift or rotation): set aside for now, the via
   models it would mostly dedupe take a second each and no two layer pieces matched on these boards. (e) The
   second GPU for small pieces (bus-limited for LUs). (f) Terminated ports in the loop/tree basis (their loads are
   lost in its transform: excite runs use the RWG basis). **(b) prepared (2026-11-22):** `mom25d/ldl.py` (a blocked
   complex-symmetric LDL^T without pivoting, NumPy: the reference) and `tools/mom25d_ldl_trial.py` (a piece's
   equilibrated matrix at fixed frequencies, captured where the direct solve factors it, factored in complex64 as
   LU / LAPACK's Bunch-Kaufman / LDL^T and each used as GMRES's preconditioner). On a 2.7k LimeRFE piece: at 0.3 GHz
   (the loop/tree basis) and 6 GHz both LDL^T and LU converge in 1 and 5 iterations; at 1 GHz (the RWG basis just
   above the loop/tree's threshold, ill-conditioned) LU takes 48 and both symmetric factors (pivoted too) fail in
   300: that band would fall back to a double factor. To trial on 10-15k pieces (the band's width), then the GPU
   kernel: the CPU's panel (its L11 D1 and the panel below), the GPU's trailing update on the lower block columns
   alone (half the GEMMs), the factor held by block columns (half the memory). **(c) measured (2026-11-22):** the block
   low-rank solver against the direct at fixed frequencies (`--thickness grow`, GPU off against GPU): a LimeRFE
   piece (27k, 17 ports, pour kept) 128 / 60 / 55 s at 1 / 3 / 6 GHz against 275 / 72 / 22 s, 14.5 GB against
   24.1; the Thor's In7 group (24.9k, 106 ports, coarse edges) failed at 1 GHz (residual 0.83 at a 1e-9
   compression), 250 / 239 s at 5 / 10 GHz against 1,017 / 275 / 221 s, 9.7 GB against 19.2; S within 5.5e-7
   where both converged. (Its old CM5 failure was the copper sides' dense blocks: with `grow` that piece converged.)
   **The common bottleneck is the RWG basis's conditioning below ~1-2 GHz on these meshes:** the direct path's
   GMRES runs to its cap there (1,017 s of the In7 group's three), the compressed factor does not precondition,
   and a complex64 symmetric factor fails (the LDL^T trial). The loop/tree basis is taken only below (k h)^2 =
   1e-5 and only where its dense matrix fits. Next: that band first: the loop/tree threshold raised where it fits
   (a cheap test), then the current-and-charge (augmented EFIE) form in compressed tiles (item 1 below), which
   would make all three paths behave at the low end. The LimeRFE (1.5-2 h) is set by two pieces of ~27k
   unknowns where several RF paths meet: (b) and (c) are what would bring it to an hour.
   **LimeRFE left over:** its KiCad 4 file's nets are out of step with its tracks (84 net pairs touching the RF
   nets overlap): re-deriving the nets from the copper's connectivity would bring back the channels the run
   leaves out; series parts with no value (20) leave their chains open; the outline is the panel's frame (the
   board's own edge is open at the tabs). **Segment in the window**: a study for it (the board, net patterns,
   band, pour/edges/windows), its overlay page and figures in the results (kicad2ems-add-analysis).
4. **mom25d iterative: AIM + GMRES (being prototyped, 2026-11-20).** The far (centroid) product by a
   precorrected FFT on a grid per layer pair (the layered tables sampled on it: no multipole expansion of the
   layered kernel needed), mom25d's exact near correction as it is, GMRES preconditioned by the near part's
   sparse LU. Measured on the test strip (4.2k unknowns): the product within 1e-5 of the exact one (grid one
   median cell), 10-15 iterations from 1 to 10 GHz, S within 2e-5 of the direct solve; SciPy's incomplete LU
   does not precondition this complex matrix (the exact sparse LU does). On the CM5 F.Cu piece (29k unknowns,
   22 M near pairs) the prototype's structures on the whole near list did not fit 24 GB: the grid's correction
   and the preconditioner on smaller reaches (a few grid cells), the near-pair correction in the compiled core.
5. **mom25d out of core** (asked 2026-11-20, after the near-field build in pieces): a tiled complex64 LU with its
   tiles on the NVMe, factored on the GPU with VRAM as its working set (the mixed path's refinement to double), for
   the problems that cannot be split. To prototype and measure on a case just over RAM (~60,000 unknowns) against
   the block low-rank path before it earns a place.
6. **The time grid, the rest** (R-E16). The grid holds the longest trace (`si.time_grid`, 16 samples a ripple:
   CHANGELOG 2026-11-14), and `coupling` uses it too. Left: S to DC for a step's settled value and TDR (item 8),
   and whether 1 / rise as fmax (the top 20 % faded) loses an edge's corner on short, fast traces: to measure
   against a grid to 3 / rise before changing it.
7. **Drivers and receivers.** A source impedance and a receiver's load in place of `z0` at every end (an
   unterminated receiver doubles what arrives; NEXT reflected at the driver arrives at the far end), and an eye
   for a bit pattern (the edge response is there: superposed).
8. **A trace's own quality:** its impedance along its length (the line model has it: *Trace impedances*), the
   reflections of its discontinuities for the edge (TDR from S11), its loss at the data rate.
9. **The coupled stretches drawn on the plan**, coloured by their share (the traces are picked on the board
   already).
10. **Broadside pairs** on adjacent layers: the groups are of one layer, so such a pair is not coupled at all (it
   is listed as not modelled, and the limit not judged).

## The window

The window keeps its docks, as a studio application's (the user's word, 2026-11-12: a fixed layout was built and
taken back); nothing below may fix a panel in place. Left:

1. **The study as a model without Qt, the rest.** `study.StudyModel` has the questions' side, `document.StudyDocument`
   the config (the RF settings, the analyses' tables, the study, read and written back) and `runqueue.RunQueue` the
   runs in progress (2026-11-19). Left: the panels still hold the values between a load and a save (the document is
   filled when the config opens and when it is saved, not on each edit), and undo is the form's; the board as read
   (its layout, for picking) is the window's. Then the KiCad plugin can drive a study through these.
2. **An analysis's panel with its few settings first, the rest.** The panel puts options of level `basic` first and
   open (2026-11-20), and pcbqs's rail, near-field and thermal tools name theirs; Elmer's and Palace's tools do not
   yet (the coil tool does).
3. **Picking on the board: what is left.** Nets and parts are picked (an option's `pick`). Not yet: a pad (a port:
   `REF.PAD`, or a point), a `REF=VALUE` list's part (a rail's loads, a part's loss), a region dragged as a box,
   and the pick in the 3D view. The Circuit page's *Load model…* and *Save model…* buttons go (*Choose PCB…* went).
4. **The pages, the rest.** *Images* may belong on a page of its own as Section does (asked, not decided); a Start
   page tile says nothing of what its example takes (the engine, the time: the index has neither).
5. **Left from the removed bridge:** the schematic's format-1 layout code.

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
4. **The losses' next step**: the conductor loss from the cross-section (Wheeler's rule: two solves with the
   copper set back by half a skin depth) in place of 2 Rs / w, and each frequency's in qucsator (its RLCG holds R
   and G at the band centre; the wideband dielectric's solves at the materials' frequencies keep it there, as G
   held at each would spread over the band too far to interpolate between a few).
5. **Discontinuities, the rest** (R-E15): a tee's and a cross's own reactances on the scikit-rf backend
   (Hammerstad's MTEE, MCROSS: it has their geometry, not their L and C).
6. **One multiconductor model** (R-F5). dfsynth's (vectorised, with conductor loss) and `coupled.mtl_s` (looped,
   lossless) are two; so are the Touchstone readers.
7. **Boundary elements with a layered Green's function**, for the largest cuts (300–2,000 unknowns, no box), only
   if the mesh's 0.2 s at worst becomes the limit. `mom25d/multilayer.py` has the stack but a dynamic kernel; the
   static 2D one needs deriving.

## mom25d: speed (2026-11-19)

The Solver Matrix's 45 boards went from 24.7 to under 9 min on 2026-11-18/19 (CHANGELOG.md: the sweep's fit, the
plates as cells, `--mom-coarsen 2`, the sides' blocks, the via and side blocks interpolated over frequency, the
GPU's double products). Measured and left, roughly in order of what they would save:

1. **The GPU LU, measured (2026-11-19, RX 7800 XT; the factor's `stats['steps']`).** At 14k unknowns 0.90 s
   (8.2 TFLOP/s): the first ~60 % of the steps are the GPU's (the update GEMM ~11.7 TFLOP/s at k = 256, and per
   step ~4-6 ms besides it: the panel down and up ~1 ms each, the triangular solves 1.6 ms, the interchanges), the
   rest the CPU's panel (1-4 ms a step, the GPU's share under 1 ms). At 7k the CPU's panels set the pace from the
   start (0.18 s, 5.2 TFLOP/s). Tried and not kept: wider or narrower panels (128: same, 384 and 512: slower), the
   first panel factored on the host during the upload or the GPU's conversion (no faster: its copy down is short
   at these sizes), the diagonal blocks' inverses in a thread (the CPU's panels twice as slow: OpenBLAS's threads
   shared), Gauss's three-product complex GEMM (+7 % on the GEMM, not on the LU, a larger error). Left, in order:
   - the GEMM itself (its ceiling with this kernel's layout ~11.7 TFLOP/s, 18.7 the card's FP32 without dual
     issue): more of k per stage, or a layout the compiler dual-issues;
   - the panel on the GPU, which would end the copies and the CPU's tail; on a slow bus it is the whole cost: the
     GTX 980 Ti here reads back at 0.07-0.18 GB/s (its slot; slangpy's own copies too) and its LU at 14k is 16 s,
     13 of them waiting on the panel's copies;
   - the copies down and up overlapped with the trailing GEMM. Tried 2026-11-20: the panel's copy down last in
     the CPU's submission with no barrier after it, the trailing GEMM first in the next: slower (14k 0.885 ->
     0.907 s), RADV does not run two submissions' work together, and the interchanges and U12 move onto the CPU's
     wait. It needs both in one command buffer with a barrier on the buffers alone (slangpy's encoder has the
     global barrier only), or a second queue.
2. **The near-field correction** (9 % on rf_ebg and sma_launch) is its pairs' integrals, not their scatter
   (2026-11-20, rf_ebg, 2 frequencies, 8 threads: 2.25 s; with no scatter at all 2.04 s; row locks in its place
   2.97 s, 2.55 s with a thread taking a triangle's pairs whole: the atomics stay). What would pay: the smooth
   rest's 3- or 6-point rules where the pair is far enough for fewer, or the rest tabulated per pair once like
   the analytic parts (`stat`), as long as the stack is the same.
3. **Interpolation where it does not apply.** Boards with holes in PEC planes fill every frequency (their
   estimate always fails); slot planes interpolate a quarter; wide bands (rf_bends, 1-12 GHz) fill 23 of 30.
4. **Thickness cheaper than the sides.** `--mom-thickness mixed` (plates only on narrow copper and gaps) has
   10 % fewer unknowns but fails the gates (antenna_patch +0.057 against openEMS); `edge` is 0.047 against the
   sides' 0.036 on solid copper and unvalidated with wave ports. A better rule for where the plates matter.
5. **The sweep.** A greedy oracle needs 169 solves on 12 dense boards against the sweep's 206: the rest are
   confirmations. Uncoupled ports fitted apart helped rf_bends (32 -> 29) but fitted noise on small blocks.
6. **Quad cells** (`--mom-quads`) no longer pay with the hexagonal mesh (same time, six boards further from
   openEMS): kept experimental; remove if nothing needs them.

## mom25d: multi-layer boards

mom25d is slowest where planes have to be meshed. Solid planes are PEC, lone stitching pads are left out and a
board imported with a few nets can have its pours' voids filled (`--fill-voids`). The model items below are worth
an order of magnitude more than the solver's: taking planes out brought rf_stripline from 100 s to 3 s.

1. **A plane a via runs through, as the default.** `--mom-planes holes` has the plane as PEC and each hole a
   few unknowns (`mom25d/frill.py`; docs/mom.md#plane-holes): its axisymmetric field and its first azimuthal
   order, which a trace over the hole drives. It is within 0.005 in |S| of the plane meshed finely on the
   transitions tried, and as close to openEMS as the meshed plane on demo_transition, for a third of the unknowns
   or fewer (3,910 to 1,508; a transition alone 11,256 to 1,661; two side by side 11,972 to 2,938; a CM5 carrier's
   PCIe pair, 54,885 after filling its voids, about 12k). Before it can be the default:
   - the loop/tree basis with the holes (the low end is the RWG basis's on such a board today);
   - a hole two vias share (a differential pair's antipad: most pairs on real boards) and holes that are not
     round (their area's circle today);
   - the second azimuthal order where holes nearly touch (0.012 at 10 GHz with 0.1 mm between them), and maybe
     for a hole's own residual: with the via-foot term a hole's effect on Z21 between via ports is 7-12 % high and
     on Z11 13 % low, the pattern a missing even order leaves (a slot-plane solve restricted to orders <= 1 tells
     it without new kernels);
   - a plane on the top interface; the hole's field in the dumps, the near field and the far field.
2. **What `--fill-voids` leaves.** `far` keeps a void near the signal copper on every layer, also behind a solid
   plane (14 × 32 mm of the CM5 Minima: 33,088 unknowns, 25,662 with `all`, 42,689 as imported). Whether those
   can go is not settled: on a crop, `all` moved a resonance of the planes the region cut (gfdtd's |S21| at
   10 GHz from −1.6 to −4.0 dB). It needs a board whose planes are not cut. The KiCad plugin's selected-net runs
   could default to `far`. Optionally, a ground pour on a signal layer cut a few heights from the signal copper.
3. **Slot planes, what is left** (`--mom-planes slots`; docs/mom.md#slot-planes): the openings' own matrix
   (slots.self_block, ~1 s a solve on rf_slotline) in the compiled core, as the coupling to the traces is; the
   loop/tree basis with openings; frill holes and openings in one stack; a via through an opening; the openings'
   field in the dumps and the near field; and when `--mom-stack auto` should choose them (on antenna_aperture they
   are 0.057 from openEMS against 0.19, on antenna_slot 0.257 against 0.058: a ground that is large against the
   wavelength, or a radiator above the plane rather than in it).
4. **Copper thickness, the rest.** The sides are the default (docs/mom.md#thickness, `mom25d.thickness`, wave
   ports too). Left: the sides with holes and openings of PEC planes and a via's vertical current, the copper's
   top face; the loss takes the port layer's thickness for every layer.

## mom25d: low frequency, large boards (the user's plan, 2026-11-13)

Against Momentum, what mom25d lacks is not a quasi-static mode but a well-conditioned low end and a direct solver
that does not need N² memory. The loop/tree basis, the block low-rank LU and the band joined with pcbqs are done
(CHANGELOG.md; docs/mom.md#loop-tree, #blr). In this order:

1. **A large board at low frequency in compressed form.** The loop/tree basis's paths are not local, so its matrix
   does not compress by tiles; the double factor is right but nearly dense. The way is the current-and-charge
   form (the augmented EFIE: unknowns I and q, blocks jωA, DᵀP, D, jω: each compresses at an ordinary tolerance
   and none cancels), its tiles holding the currents and charges of one place. Also: the tiled solver, quad cells
   and line functions, holes of PEC planes with the loop/tree basis; the S extraction's floor below ~10 Hz
   (currents a million times the ports': S from the loop and tree amplitudes, not from the summed currents).
2. **The block low-rank solver, the rest.** A factor kept across frequencies (then GMRES's matrix-free product,
   which pays only where the iterations grow: with a factor made at the frequency GMRES takes 3 to 7 exact
   products); the near-field correction's sparse build (3-4 s at 22-41k); pivoting only inside the diagonal
   tiles; a strong-admissibility H-LU for 100k+ (BLR's dense near band grows as N), the near field's reach capped
   (ACA from O(N r) entries), and an FFT operator (pFFT / AIM) past that.
3. **A quasi-static mode, ranked low.** Building the matrix once saves time only if building it is what costs.
   Profiled 2026-11-13 (GPU): the per-frequency build is 39-50 % of a sweep on the mid-size examples (4-9k
   unknowns), 36 % on antenna_vivaldi (21.9k) and 6 % on rf_siw (28.7k): the larger the board, the more the
   factorisation dominates. If one is built, it belongs on a static version of the layered Green's function with
   the PEC ground (`GreenTableML`): that, not a mode, is what lets Momentum's RF mode go past λ/10 without meshing
   the planes.

## mom25d: the code

1. **The numerics shared with pcbqs** (R-F4): one copy in `kicad2ems.emsdev.numerics`. Left to port: mom25d's
   singular rule over the smaller triangle into pcbqs (`mom25d.sing` wraps the shared `offplane_pairs` with it),
   with pcbqs's compiled static, compressed and layered kernels (they integrate over p too; ported in Python alone,
   fourteen of pcbqs's tests held them apart).

## Power tools (pcbqs)

`kicad2ems.pcbqs.pdn` (a rail's DC drop and impedance), `.mqs` (a converter's near field), `.circuit` (switching
circuits on the board's copper), `.thermal` and `.sparams` (the ports' S-parameters at the low end). What exists
and how it was validated: docs/pcbqs/ and its CHANGELOG.md.

### Correctness and workflow

1. **A load with no pad on ground, the rest.** The rail takes the return of a high-side switch through the one
   part of its kind on its other net and on ground, and says so, or asks for it. Left: a return that is not that
   direct, a low-side switch on ground through a sense resistor (the search stops at the switch's own pads, so
   such a bridge is asked about), and two parts in parallel as one return.
2. **Cross-tool flows, the rest** (docs/pcbqs/power-workflow.md). Left: a controller with its two switches as
   separate parts is not made into a circuit (the regulator's own footprint is taken for both); a rail's ripple
   at a converter's pin needs the input loop's own inductance, which the near field solves and the rail's planes
   do not have (the two could share it); the circuit's proposal naming the connector as the rail's source, so that
   nothing is typed between the runs (drafted with a regulator's board of its own, not applied).
3. **Several rails, the rest.** A config holds them, `kicad2ems study` runs them, and the window has them as a
   table. Left: their drops side by side in one report, each row's state in the questions' list (the study's is
   the last run's), and a rail's own board or output folder (the table's rows share the panel's).
4. **The full bridge's speed on ngspice** (`pcbqs/data/wpthb_bridge.json`): 25 s alone, 1 min 47 s with copper,
   for the old tool's 6 s and its precharged, warm-started runs.
5. **Converters.** Left: a flyback (its loop runs through the transformer) and an inverting buck-boost (through
   both capacitors), which are not found; a circuit made of a bridge found that way (a bridge's leg has no one
   inductor on its node: its tank or its transformer is the model's to draw); ambiguous bias, enable and supply
   nets resolved by part metadata; the switch node's inductive E in the converter workflow (the Coulomb E is
   there) and output-inductor models; decap and via mounting inductance from the quasi-static solver instead of
   the formula.
6. **Low-frequency S-parameters, the rest** (docs/pcbqs/sparams.md). Left: gap ports; the two-face copper with
   the charge is slow on a stack of several dielectrics (25 s a frequency at 15,000 triangles on demo_transition,
   77 s at 9,000 on pcbqs's return-path example). In the window, the Plots page draws a log sweep (`sweep = "log"`)
   on a linear axis in GHz: a log axis and the band's own unit; the report's interactive plot is linear in GHz too
   (its PNG is right), and its table reads "0.000 GHz" for a run in kHz (R-G).
7. **What a PI user takes away, the rest** (R-E14). Left: an impedance for a rail with no planes (the PEEC route
   is the trial's only: one source, one ground, no beads); DC-bias derating from a curve by dielectric and voltage
   (a vendor's file at the bias, or a share, today); a ground pour beside the rail on its own layer, which the
   plane pairs leave out: Palace's PDN engine read the buck example's bare |Z| 0.68 of pcbqs's and its loaded
   inductance 0.45 (2026-11-15, R-E22).

### Losses and thermal

- a film that grows with the rise (natural convection and radiation), and air warmed by one part reaching the next;
- the heating from cold on the mesh, and a transient of changing losses (a load step);
- the iGSE's DC-bias and relaxation terms, and minor loops;
- the DC drop's copper at its temperature (the circuit's is, with `--warm`).

### Speed

The circuit run is there for its parts' losses (thermal) and its copper's currents (fields); ngspice is its one
engine. (A piecewise-linear switched state-space engine was built and removed: CHANGELOG.md, 2026-11-14.)

| Option | Gain | Effort | Notes |
|---|---|---|---|
| Xyce backend | robustness, not speed | 2 days | it ran behavioural switches ngspice could not; 2–7× slower on the benchmark converter; harmonic balance could suit resonant tanks |
| Two faces in the circuit: a local loop basis | fewer iterations, no dense setup | | iterations grow with frequency (217 at DC, 575 at 20 MHz); the dense common-mode setup is 19 s |
| The dense solves, after their assembly | the LU is 90 % of a two-face S-parameter solve (147 s at 17,700 unknowns) | | a compressed or block low-rank LU as mom25d has; ngspice's text output read by `np.loadtxt` (a binary raw file); the compression of field dumps; shapely's unions in the near field's mesh (`tools/pcbqs/perf/README.md` has where each tool's time goes) |
| Whole-board near field | | convergence | `--backend hierarchical` is GMRES in the loop basis (47,548 triangles in 13 s, docs/pcbqs/quasistatic.md). Left: mesh and crop convergence on a whole board. A crop around the converter is enough for DC-DC loops |

The quasi-static solver's 10× target is not yet shown on real boards, and an FFT field grid's convergence has to
be checked for each scan.

### Accuracy and validation

1. **Thick copper and AC resistance (C14).** `conductor_loss='slab'` is two face sheets; stacked sheets with the
   thick kernel are the reference. Graded cells at the copper's edges are in `kicad2ems.pcbqs.sparams
   --thickness`; the circuit's copper and the near field not yet (their meshes are the pads' and the loop's: to be
   measured there first). Open:
   - a trace close over a plane, the largest error: one sheet is 19–30 % low in R, two faces were 12.5 % low when
     last compared, the reference is within 5 %. The validation board's second structure decides;
   - the 2–3 % drop in inductance with face sheets;
   - the tree preconditioner with face sheets;
   - the compressed backend as the default for `slab`, once the CM5 loop agrees;
   - one sheet or two in the circuit: two move wpthb's settled ripple by 6 % at 2.6 times the time, which the
     powered ripple cannot resolve until the parts are vendor models. It needs a measurement that isolates the
     copper (item 4's jumper variant, or the validation board).
2. **Capacitance in narrow gaps, with thickness.** Graded edge rings resolve the edge charge on sheets
   (validation/pcbqs/edge_rings_*.py). Left: the same with the copper's thickness (face sheets and side charge,
   since removed, were −5.5..−7.5 % and −10..−13 % against FasterCap's boxes, on plain cells), and whether the
   rings should be `MQS(capacitance=True)`'s default.
3. **A real converter loop, converged.** CM5 U702 (`validation/pcbqs/mqs_cm5.py`): one sheet is within 4 % of
   Palace's PEC sheets at the finest meshes, both still converging; two meshes differ by up to 3.8 % in L. The
   Jetson's eight bucks run, no more. Mesh and crop convergence per board, with measured local refinement before
   any whole-board compression.
4. **Measurements (the plan is ready; waiting on a VNA).** `validation/pcbqs/measurements.md`: shunt-through at
   TP7 / TP6 unpowered (as built, and with one 1206 jumper for the ceramics), VHB ripple at the regression
   operating point, the near field at 1–3 mm; predictions in `validation/pcbqs/wpthb_vhb_port.py`. A purpose-made
   validation board: `validation/pcbqs/test-board.md` lists its structures (no layout or predictions yet).
5. **Models.** Vendor or fitted MOSFET models (MCG8D2N06YL), datasheet capacitor ESR and ESL, measured coil Q;
   switch internal capacitances as explicit elements, so a warm start can initialise them; package and gate
   geometry, the receiver's and the leads' field geometry, capacitance to the environment, the fields at startup.
6. **A complete PEEC model up to about λ/10:** resolved proximity currents, via skin loss, mutuals between parts
   and copper, material dispersion.
7. **Palace 0.19, the rest:** pcbqs's Palace validations (`validation/pcbqs/`), which were made on 0.18.1.

## FDTD (gfdtd and the FDTD layer)

1. **The guards, the rest.** gfdtd taking a part's ESR in one series RLC (an implicit update for R and C without
   L), so kicad2ems need not split such parts for it.
2. **Speed without new physics.**
   - The exact time step on the GPU: `--gfdtd-timestep exact` measures the operator's limit by Lanczos in NumPy
     (0.6-2 s once a model, kept), so `auto` takes it only from 1 M cells; the curl-curl products on the backend's
     own kernels would make it free for every run (R-D6's rest).
   - εr averaged inside a cell, so a thin layer needs no lines of its own (`nz_mask` is 1 since 2026-11-14: 4 cells
     through a 10 µm mask had made hairpin 17× longer for "within 1 MHz").
3. **Line ports, the rest** (R-E1): differential pairs (the legs' B A^-1 is exact; the shift back needs each
   mode's Zc and gamma from the two runs), a stripline's port driving both of its planes (one plane drives the
   wave between them: the probes need 3 mm to settle), and the probes on an oblique trace.
4. **Diagonal copper** is a staircase on the mesh: the mesher puts lines at a polygon's vertices, so a diagonal
   edge between them sits on the coarse cells (a 20 mm, 50 ohm line at 30 deg came out 3.4 % longer electrically
   than along x, at 45 deg 1.7 %; a fine cell of 0.09 mm in place of 0.18 helped 0.6 %). Lines along such an
   edge, or a conformal edge (Dey-Mittra), would follow it.
5. **Boundaries**: first-order Mur is the default on five sides (6 % reflection against 5e-5 for an 8-cell PML),
   and the PML is CPML with κ = 1, α = 0, at λ/res cells: CFS-CPML, and thinner graded cells.
6. **Larger boards**: every copper edge is a line across the box. Subgridding or local refinement, temporal
   blocking, conformal PEC for curved copper.
7. **Materials**: Lorentz and Drude, anisotropy; openEMS's Debye run has 0.5 dB more loss than gfdtd's on the same
   material (which is right is not checked).
8. **openEMS's interface, the rest.** Through `kicad2ems.gfdtd.compat` (docs/gfdtd/README.md#compat) 10 of
   openEMS's 16 Python tutorials run unchanged (2026-11-20; 1 on 2026-11-18), the 7 compared agreeing with openEMS
   (the horns' and the helix's directivity to the printed digit). Left, in order:
   - the radiated power: gfdtd's Prad 7-12 % above openEMS's on the helix and the conical horn, with the same
     directivity (the normalisation of the incident power?);
   - the plane-wave excitation (type 10, a total-field / scattered-field box: RCS_Sphere);
   - the SAR calculation (`openEMS.sar_calculation`, compiled in openEMS: Dipole_SAR, MRI_Loop_Coil) and the
     phantom files `get_resource_path` finds in an openEMS tree;
   - the getters and setters scripts read back, wires, polyhedra, Lorentz and Drude materials, `AddDiscMaterial`;
   - cylindrical grids (Bent_Patch_Antenna, Circ_Waveguide, CylindricalWave_CC), the one option gfdtd refuses;
   - the tutorials as a test: gfdtd against openEMS where openEMS is installed, gfdtd alone where not.
   The magnetic materials run on the NumPy backend: the compiled kernels' H update with a per-edge μ and loss
   would put them on the GPU.

Tests (lavapipe, NVIDIA) are under CI and release.

## The fem part (Elmer, Palace)

1. **Palace's own accuracy** (R-E20):
   - `rf_shielded`'s reference: Palace in its can and its own refinement settle near 4.65-4.66 GHz, mom25d without
     the can 4.648, openEMS still climbing with its mesh (4.555, 4.588, 4.626 at 1x, 2x, 4x), EMerge's
     "converged" 4.621: which is right is open (EMerge's model to be checked: copper thickness, the can's floor);
   - the antennas' efficiency over 100 % (100.4 % at 2.9 GHz on the dipole) is not the boundary (`--palace-abc 2`
     leaves it); the far field's integration over the box's faces is next to look at;
   - wave ports;
   - copper as solids in the driven model (as the cap tool's `--thick`): its sheets' loss holds only to ~10 MHz.
2. **A winding's own AC loss** (the rest of R-E21): the coil tool spreads its turns' current over the section, so
   skin and proximity loss in the wire is not solved; Elmer's stranded-coil homogenisation (its
   `circuits_harmonic_*_homogenization` cases) would give it. Elmer's transient with a net's own current.
3. **The PDN in Palace, the rest** (R-E22): the loop's 40 % on the Jetson's U69 (Palace 0.20 nH, pcbqs 0.34 nH at
   12 MHz) to explain: zero-thickness PEC against 35 µm copper, the capacitors as straps against filaments, one
   port against all the converter's pads.
4. **Real boards, the rest** (R-B14): the Elmer builder's fragmenting, a gap per edge.

## The KiCad plugin (`kicad2ems.ipc`)

Besides the study (The study, item 3), R-E26 and C16:
- the analyses beyond the DC drop and the temperature (the near field, the circuit, crosstalk) are the window's
  only;
- its environment builds gfdtd without its compiled core (the GPU is offered: *Use the GPU for gfdtd*; without it
  NumPy, ~15× slower): the core's build in the plugin's environment, where a compiler is there;
- the Plugin and Content Manager route its README gives does not exist yet (no entry, `"VERSION"` in its metadata,
  no v0.1.0 tag; also under CI and release).

## dfsynth

R-E18, in order:
1. the tapped hairpin feed;
2. combline;
3. elliptic and cross-coupled designs.

## Docs that do not say what the code does

The review's section G, corrected on 2026-11-14 but for these:
- mom25d's README validation figures, tighter than its tests check (CI and release, item 1).
