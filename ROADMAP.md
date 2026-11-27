# Roadmap

What is left to do across kicad2ems and its parts (`kicad2ems.{core,gui,fdtd,gfdtd,mom25d,pcbqs,fem,ipc,dfsynth,
emsdev}`), most useful first within each section. Each item comes from something an example, a benchmark or a
validation showed. Finished work leaves this file: what was done, measured and set aside is in CHANGELOG.md
(pcbqs's history before the merge: docs/pcbqs/CHANGELOG.md). R numbers (R-E1, ...) are the stack review of
2026-11-14 (`REVIEW-2026-11-14.md` in kicad2ems-project), C numbers docs/pcbqs/power-tools-review.md, F numbers
the code review of 2026-11-25. Last updated 2026-11-27.

## Waiting on a decision

- **PyPI:** it refuses direct-URL dependencies (the user's call, 2026-11-14).
- **The adaptive sweep's last check** after a deep agreement: skipping it saves 6.8 % of mom25d's solves (604
  pieces measured, 2026-11-23); it is the guard against two models wrong alike.
- **The planes' internal inductance** (F87): `core.cavities` and pcbqs's plane pairs leave it out (k² (1 − j δ/d));
  the exact form (1 + (1 − j) δ/d) moves the rail-impedance tool (the planes' inductance +δ/d, ~2 % at 1 GHz on
  0.1 mm) and the resonances (−0.2 %).
- **Images on a page of its own** in the window, as Section has (asked, not decided).
- **Plane-pair cavities** (segment's `--cavities`) were set aside by the user on 2026-11-24: their items below
  wait on that.

## Release and CI

1. **Regression references** (R-C9, C10): gfdtd, mom25d and the line model regress against openEMS's
   demo_lowpass alone; a board with wave ports and one with vias would cover more. mom25d's README figures are
   tighter than its tests check. pcbqs's real-board regressions need `~/Desktop/wpthb`.
2. **Windows and macOS** (F80): CI runs Linux alone; a job on each that builds the cores and runs the core suite.
3. **The GPU report** (`tools/gpu_report.py`, run before a release): an NVIDIA run in it, and a mom25d board past
   the GPU's threshold (the report's boards factor on the CPU).
4. **The KiCad plugin's release:** its Plugin and Content Manager entry (`"VERSION"` in its metadata today) and
   a v0.1.0 tag.

## The study: questions and engines

1. **gfdtd is not offered for the antenna question:** `study.engines` reads a solver's `antenna` capability up
   to its first colon, and gfdtd's ("yes (a phased drive: ...)") is neither "yes" nor "approximated".
2. **A second engine where a question has one:** capacitance (Palace alone; pcbqs's electrostatic solver is not
   wired as one), coils (Elmer alone; pcbqs's quasi-static inductance), the circuit (ngspice alone; Xyce: it ran
   behavioural switches ngspice could not, 2-7× slower).
3. **New questions:** segment and the plane-pair cavities as study questions (they are command-line tools); a
   return-path check (traces over plane splits and voids, layer changes with no stitching via near); via stubs
   and backdrill (each via's stub resonance against the data rate); a radiated-emission estimate (mom25d's
   currents to the far field against CISPR 32); decoupling chosen to a target impedance; matching networks from a
   solved S11 with real part values (`core.embed`).
4. **S-parameters compared per engine in the window** (its *Engines compared* table asks the analyses alone), and
   a resonance's or a line's delay as figures.
5. **One setting for one thing:** pcbqs's rail tool takes `rail`, Elmer's `net`; asked both, the rail is typed
   twice. A question could say what it needs and each engine take it from there.
6. **Ports the solvers share** (R-E1): mom25d's wave window refuses the examples' 4-6 mm feeds where the FDTD's
   probes stand 3 mm in; the FDTD's line ports on pairs and oblique traces (FDTD 3).
7. **A variation's figures** in the window and the report (`<name>_vary.csv` and the log have them; *Compare with*
   overlays the curves alone).
8. **Solvers side by side** (R-D20): `--solver-jobs N` runs them as threads, but gfdtd's GPU stepping holds the
   interpreter lock; processes of their own would let them overlap.
9. **The KiCad plugin starts `kicad2ems study`** for a board whose config has one.

## Signal integrity

1. **The time-domain question, the rest** (`core.timedomain`, docs/timedomain.md): equalization (CTLE, FFE, DFE)
   and IBIS-AMI, jitter on the transmitted edges as such (the sampling instant's stands for it) and sinusoidal or
   bounded jitter's own shapes, a floating differential termination (split per leg to Vtt today), IBIS's [Driver
   Schedule] and [Submodel], a SPICE subcircuit of the macromodel for the circuit question, the passivity
   projection's slow rounds near DC (a fitted pair scaled 0.11 % after 20), and a check on a real channel with a
   vendor's IBIS file.
2. **Crosstalk and board coupling through real buffers:** their edge responses take `z0` at every end; a source
   impedance and a receiver's load (an unterminated receiver doubles what arrives).
3. **Differential pairs, the rest:** a pair's skew (its legs' delays) and the common-mode noise its neighbours
   put on it (in the modal S, not reported).
4. **Board coupling, the rest** (`core.coupling`): a net that forks on a layer, a port inside an antipad but off
   its via's ring (the question stops), the window's view of the matrices. On a large board mom25d does not fit
   (the CM5 Minima ran out of memory): segment is the way there.
5. **Broadside pairs** on adjacent layers are not coupled at all (the groups are of one layer).
6. **The time grid:** whether 1 / rise as fmax (the top 20 % faded) loses an edge's corner on short, fast traces,
   against a grid to 3 / rise.
7. **The coupled stretches drawn on the plan**, coloured by their share.
8. **Line parameters, the rest** (`core.rlgc`, docs/rlgc.md):
   - the W-element files checked in a simulator that reads them (HSPICE's tabular `sp` models are written from its
     manual, lower triangles, not run here); a SPICE subcircuit of the RLGC (ngspice has no W element) for the
     circuit and time-domain questions;
   - broadside groups (the line model's are of one layer) and a cut along a section's length (it is cut once);
   - the lowest frequencies' L is that of a plane as wide as the box (24 heights either side): a plane's own
     width from the board's outline;
   - speed, measured and set aside (2026-11-27, an 8-line bus of 19.2k nodes, 23 frequencies, the machine shared
     by four other runs): a Galerkin reduced model of K + jωσM from solves at adaptive shifts needed 13
     factorisations for Z within 6.5e-7 (5 for 3e-3) and took 5.3 s against 1.3 s direct (its basis grows by 9
     columns a shift); the fill-reducing ordering reused across frequencies (one pattern) saves 7 % of a
     factorisation (45.5 → 43.7 ms); one figure is about one section's solve (0.12 s): drawn for the 12 longest
     sections alone. Each concurrent frequency's LU holds ~37 MB on that bus (threads 1 / 4 / 8: 11.2 / 5.6 /
     3.8 s, 132 / 280 / 432 MB peak on rf_ddr's 20 sections).

## Segment: a board in pieces (`core.segment`)

1. **In the window:** a study question (the board, net patterns, band, pour, edges, windows), its overlay page
   and figures in the results.
2. **The extra reflection near 10 GHz:** S11 2-3 dB above the whole board's on rf_msvia and rf_diffvia; neither a
   larger via box, the cut further out nor `--calibrate` changes it much. The via model's, to find.
3. **More checks against a cut-out solved whole** (done for the CM5's PCIe clock: Sdd21 within 0.13-0.15 dB
   rms): the CM5's other interfaces, its Ethernet pairs' −2.6 dB match and USB-C's reflection, the Thor
   carrier's SFI_REFCLK at −3.5 dB at 1 GHz.
4. **The physics-based against the meshed via models:** 0.1-0.36 |ΔS| at 6 GHz where both are well posed, the
   box's truncation or the model's (a larger region solved whole); LimeRFE and ThunderScope's front end again
   with `pbm`.
5. **The layer pieces' ideal planes have no copper loss** (rf_msvia: 0.22 dB at 10 GHz in pieces against the
   whole board's 0.41): mom25d 2.
6. **Speed:**
   - straight coupled runs as analytic multi-line sections (an N-line cross-section, exact for TEM) with MoM
     at the breakouts, bends and vias alone: the largest gain on bus-heavy boards (the Thor's In7 bus). The
     section itself exists now (`core.rlgc`: a cut's RLGC with skin and proximity loss, `coupled.zy_s` its S over
     a length, 0.2-1 s a cross-section); what is left is segment's side: finding the straight runs, cutting the
     pieces at their ends and joining the sections' S as pieces;
   - a piece's file keeps whole any plane that reaches its neighbourhood (12.7 MB median, ~1 MB clipped);
   - the physics-based via models run one at a time last; in a thread they stalled the run (not found why): a
     process of their own;
   - gfdtd's via models (`--via-solver gfdtd`) are as accurate but not faster on the CM5: its fine cell came
     down to 19 µm (the cut circles' short edges, a part left in the box); the box's parts left out, its fine
     cell at the via's scale, a smaller air margin;
   - a row of vias split where its circles only touch;
   - the second GPU for the small pieces (the 980 Ti: bus-limited for an LU, not for a small piece).
7. **LimeRFE:** re-check its overlapping net pairs after the pad-rotation fix (F03 took 194 pairs to 0); 20
   series parts with no value leave their chains open; its outline is the panel's frame.
8. **DDR lane 1** (no port footprints: its ends).
9. **A test-isolation fault:** with tests/core/test_segment.py first in a session, the CLI's tests lack mom25d's
   dump options (the solver registry built in an order that leaves them out; CI runs the folders apart).
10. **Plane-pair cavities** (set aside by the user):
    - their cost on a large board (the Thor's five blocks 26 min of 56: a sparse factor of 85-94k unknowns at
      each frequency): each cavity's modes once and a sum over them would make it minutes; `--suggest` slow the
      same way;
    - `--suggest` checks the cavity's two planes alone (a through via must clear every layer);
    - the far-return rf_msvia's notch's upper flank (−1.9 dB at 8.5 GHz against −1.0);
    - the meshed via models are not joined to the cavities;
    - decoupling capacitors in the cavities (their ESL, ESR), the cavity edges' radiation in Q;
    - why the Thor run with `--cavities` solved every piece again (the pieces' hashes to compare).

## mom25d

### Accuracy

1. **`edge` thickness's copper loss** (F90): 2.9× the strip's share over the ideal ground, 2.4× the line's with
   the plane meshed (docs/mom.md#thickness-loss), and every whole-stack run with `sides` or `mixed` gets `edge`.
   Spreading the edge triangles' loss over their walls takes 0.229 to 0.157 dB. The 2D reference is measured now
   (`core.lines.mqs`, docs/rlgc.md#checks, 2026-11-27): that microstrip at 10 GHz has R = 64.9 Ω/m, 0.00589
   dB/mm at Z0 47.8 Ω, the strip's 0.00437 (0.741 of it) and the plane's 0.00152. Over 10.4 mm that is 0.061 dB,
   the strip's 0.046, against the table's references of 0.097 and 0.072 (Hammerstad and Jensen's formula, 1.6×
   the 2D solve; Wheeler's rule on the field solver gives 65.6 Ω/m). Against the 2D numbers, `sides` over the
   ideal ground (0.066 dB) is 1.45× the strip's, `grow` 2.0×, `off` 2.1× and `edge` 4.6×. With the plane meshed,
   `grow` (0.109) is 1.8× the line's 0.061, `off` 1.9× and `edge` 3.7×. The table and the test's
   `STRIP_DB_MM` (tests/mom25d/test_conductor_loss.py) are to move to these, then the rules fitted.
2. **The ideal ground's own loss:** over the infinite plane (stack `ground`, segment's layer pieces) its share
   of a line's conductor loss (0.26 on a 50 Ω microstrip) is missing: a surface impedance at the bottom of the
   layered Green's function, or Wheeler's share added per line.
3. **Copper thickness, the rest:** the sides with holes and openings of PEC planes and a via's vertical current,
   the copper's top face; the loss takes the port layer's thickness for every layer.
4. **Thickness cheaper than the sides:** `mixed` has 10 % fewer unknowns but fails the gates (antenna_patch +0.057
   against openEMS); `edge` is 0.047 against the sides' 0.036 on solid copper and unchecked with wave ports. A
   better rule for where the plates matter.

### Planes

1. **A plane a via runs through, as the default** (`--mom-planes holes`, docs/mom.md#plane-holes: within 0.005
   in |S| of the meshed plane for a third of the unknowns or fewer). Before it can be: the loop/tree basis with
   holes, a hole two vias share (a pair's antipad) and holes that are not round, the second azimuthal order where
   holes nearly touch, a plane on the top interface, the hole's field in the dumps and the near and far field.
2. **What `--fill-voids` leaves:** `far` keeps a void near the signal copper on every layer (14 × 32 mm of the
   CM5: 33k unknowns, 25.7k with `all`); on a crop `all` moved a resonance of the planes the region cut. It
   needs a board whose planes are not cut. The KiCad plugin's runs could default to `far`.
3. **Slot planes, the rest** (`--mom-planes slots`): the openings' own matrix in the compiled core, the loop/tree
   basis with openings, holes and openings in one stack, a via through an opening, their field in the dumps, and
   when `--mom-stack auto` should choose them (antenna_aperture 0.057 from openEMS against 0.19, antenna_slot
   0.257 against 0.058).

### Speed

1. **A pivoted symmetric factor** (Bunch-Kaufman blocks): the LDLᵀ without pivoting fails on via-chain-heavy
   pieces (LimeRFE's F_Cu_g34 at 0.8-1.6 GHz before its ports were merged).
2. **The GPU's products:** the float-float kernels run at ~0.5 of ~6 TFLOP/s (Z re-read for every column block):
   GEMM-like tiles in shared memory would be the next 2-3×; the half read once a number; an exact fused fma on
   RADV (an inline SPIR-V decoration would drop Dekker's 20 extra operations); `_products`' host share
   (SPLIT_MIN 16) now loses and wants measuring against the float-float rate with many ports.
3. **The block low-rank solver, the rest:** its near tiles in complex64 (the operator on a pour ~36 % of dense
   complex128, ~25 % then), the factor's tiles from the operator's, the factor on the GPU, a factor kept across
   frequencies, a first run at 45-100k unknowns, a strong-admissibility H-LU for 100k+.
4. **A large board at low frequency in compressed form:** the loop/tree basis does not compress by tiles; the
   current-and-charge form (the augmented EFIE) would, in tiles of one place. Also the S extraction's floor
   below ~10 Hz (S from the loop and tree amplitudes).
5. **AIM + GMRES** (a prototype: on a test strip 10-15 iterations, S within 2e-5): on the CM5's F.Cu piece (29k)
   its near list did not fit 24 GB. Next: the near part's sparse LU in a fill-reducing order (METIS, MMD_AT_PLUS_A)
   and a shorter reach; a complex-symmetric Krylov method (COCG, QMR) to drop GMRES's basis.
6. **Out of core:** a tiled complex64 LU on the NVMe, factored on the GPU, for problems that cannot be split; to
   measure on a case just over RAM (~60k unknowns) against the block low-rank path first.
7. **The GPU LU:** its GEMM (~11.7 of 18.7 TFLOP/s on the 7800 XT), the panel on the GPU (on the 980 Ti's slow
   bus the panel's copies are 13 of the 14k LU's 16 s), the copies overlapped with the trailing GEMM (one command
   buffer with buffer barriers, or a second queue).
8. **Smaller:** the near-field correction's smooth rest on fewer points where the pair is far enough (9 % on
   rf_ebg); interpolation over frequency where it does not apply (holes in PEC planes, wide bands); a sweep-wide
   "double from here" on a complex64 residual ~1 (two LimeRFE via models took 11 and 28 min); the charge block's
   `fan_q` and `near_q` host arrays; terminated ports in the loop/tree basis (their loads are lost in its
   transform); the GPU LDLᵀ's one-column solve (16 ms against the LU's 12.6 at 20k); a host LDLᵀ as fast as
   LAPACK's LU; the policy's host bytes for the LDLᵀ hybrid (24 N² counted, 20 used).
9. **Quad cells** (`--mom-quads`) no longer pay: remove them if nothing needs them.
10. **A quasi-static mode** (ranked low): the per-frequency build is 6-50 % of a sweep, less the larger the
    board; if built, on a static layered Green's function with the PEC ground.

### The code

1. **The numerics shared with pcbqs** (R-F4, `kicad2ems.emsdev.numerics`): mom25d's singular rule over the smaller
   triangle into pcbqs, with pcbqs's compiled static, compressed and layered kernels.

## FDTD (gfdtd and openEMS)

1. **rf_ebg is not converged at the default resolution** (F88): its stopbands at 3.2 and 7.2 GHz at `--res 60`,
   at 3.2-3.8 and 4.4-4.9 GHz at res 20 and 40, with any fine cell; res 60 is not settled either (0.057 between
   gradings). A converged reference (res 80 and up), then the example's `res`, or a check that says a resonant
   structure wants more than res 20.
2. **gfdtd taking a part's ESR in one series RLC** (an implicit update), so kicad2ems need not split such parts.
3. **Line ports, the rest** (R-E1): differential pairs (each mode's Zc and γ from the two runs), a stripline's
   port driving both planes, the probes on an oblique trace.
4. **Diagonal copper** is a staircase (a 50 Ω line at 30° 3.4 % long electrically): lines along such an edge, or
   a conformal edge (Dey-Mittra).
5. **Speed without new physics:** the exact time step on the backend's own kernels (Lanczos in NumPy today, so
   `auto` takes it only from 1 M cells); εr averaged inside a cell, so a thin layer needs no lines of its own.
6. **Boundaries:** first-order Mur by default (6 % reflection against 5e-5 for an 8-cell PML); CFS-CPML and
   thinner graded cells.
7. **Larger boards:** subgridding or local refinement, temporal blocking, conformal PEC for curved copper.
8. **Materials:** Lorentz, Drude, anisotropy; openEMS's Debye run has 0.5 dB more loss than gfdtd's on the same
   material (which is right is not checked).
9. **openEMS's interface, the rest** (`kicad2ems.gfdtd.compat`, 10 of 16 tutorials run): gfdtd's radiated power
   7-12 % above openEMS's (same directivity), the plane-wave excitation, the SAR calculation, the getters and
   setters, wires, polyhedra, `AddDiscMaterial`, cylindrical grids; the tutorials as a test; the magnetic
   materials on the GPU.

## Power tools (pcbqs)

1. **Converters:** a flyback and an inverting buck-boost are not found; a bridge's circuit (its tank or
   transformer); bias, enable and supply nets resolved by part metadata; the switch node's inductive E and
   output-inductor models; decap and via mounting inductance from the quasi-static solver.
2. **A load with no pad on ground, the rest:** a return less direct than one part, a low-side switch through a
   sense resistor, two parts in parallel as one return.
3. **Cross-tool flows, the rest** (docs/pcbqs/power-workflow.md): a controller with its two switches as separate
   parts, a rail's ripple at a converter's pin with the input loop's own inductance, the circuit naming the
   connector as the rail's source.
4. **Several rails, the rest:** their drops side by side in one report, each row's state in the questions' list, a
   rail's own board or output folder.
5. **What a PI user takes away, the rest** (R-E14): an impedance for a rail with no planes, DC-bias derating from
   a curve, a ground pour beside the rail on its own layer (Palace's PDN engine read the buck example's bare |Z|
   0.68 of pcbqs's, R-E22).
6. **Low-frequency S-parameters, the rest** (docs/pcbqs/sparams.md): gap ports; the two-face copper with the
   charge slow on several dielectrics (25-77 s a frequency); the window's Plots page draws a log sweep on a linear
   GHz axis, and the report's table reads "0.000 GHz" for a run in kHz.
7. **Accuracy:**
   - thick copper and AC resistance (C14): a trace close over a plane is 19-30 % low in R on one sheet, 12.5 % on
     two faces; the inductance 2-3 % low with face sheets; the compressed backend as `slab`'s default; one sheet
     or two in the circuit (6 % in wpthb's ripple at 2.6× the time);
   - capacitance in narrow gaps with the copper's thickness, and whether edge rings are the default;
   - a real converter loop converged (CM5 U702: within 4 % of Palace, both still converging);
   - measurements: the plan is ready (`validation/pcbqs/measurements.md`), waiting on a VNA; a validation board's
     structures listed (`validation/pcbqs/test-board.md`);
   - models: vendor MOSFETs, datasheet capacitor ESR and ESL, measured coil Q, switch capacitances as elements;
   - a complete PEEC model to ~λ/10 (proximity currents, via skin loss, mutuals between parts and copper);
   - pcbqs's Palace validations again on Palace 0.19 (made on 0.18.1).
8. **Losses and thermal:** a convection film that grows with the rise and radiation, air one part warms reaching
   the next; the heating from cold, a load step's transient; the iGSE's DC-bias and relaxation terms; the DC
   drop's copper at its temperature.
9. **Conducted emission, the rest** (`pcbqs.conducted`, docs/pcbqs/conducted.md):
   - radiated emission from a converter: its switch node's loop and its input cable, driven by the conducted
     run's currents (the radiated question's far field);
   - an AC mains source with its rectifier: a 10-20 ms period, so the lines are unresolved and the QP's
     slow-rate response matters (it reads 2.1 and 2.5 dB under the standard's at 2 and 1 Hz, cause not found);
   - spread-spectrum modulation (a period of its modulation);
   - `cispr16` from the standard's own schematic (it is realised from its impedance) and the networks checked
     against a measured one;
   - the plane's images near the board: centroid to centroid holds while the triangles are small against twice
     the height; a board 5-10 mm over its plane wants the exact moments;
   - the CISPR 25 tables checked against the standard (FM class 5 matches application notes, the rest is
     transcribed), and an example config (the buck's) in the window's list.

   Measured and set aside:
   - a periodic tolerance of 0.07 for the whole circuit instead of leaving the networks out of the test: 4.5 s,
     readings within 0.016 dB, but then the converter's own states settle only to 7 %;
   - receiver batches of 2^22 samples (2.06 s, 190 MB) and 2^20 (2.64 s, 50 MB), against 2^21 (1.89 s, 97 MB);
   - the averaged detector's start with one convergence test for a whole batch: 40 iterations and 2.38 s, because
     a few rows cycle at 2e-5 at a kink of the period map;
   - an in-place bisection: 1.08 s against 1.09.
10. **Speed:** the full bridge on ngspice (1 min 47 s with copper against the old tool's 6 s); two faces in the
   circuit by a local loop basis (iterations 217 at DC, 575 at 20 MHz); the LU of the dense solves compressed (90
   % of a two-face S-parameter solve); whole-board near field's mesh and crop convergence; the quasi-static
   solver's 10× target shown on real boards.

## The fem part (Elmer, Palace)

1. **Palace's own accuracy** (R-E20): rf_shielded's reference (Palace 4.65-4.66 GHz, mom25d 4.648, openEMS still
   climbing with its mesh); antenna efficiency over 100 % (the far field's integration over the box's faces);
   wave ports; copper as solids in the driven model (its sheets' loss holds only to ~10 MHz).
2. **A winding's own AC loss** (R-E21): skin and proximity loss in the wire (Elmer's stranded-coil
   homogenisation); Elmer's transient with a net's own current.
3. **The PDN in Palace** (R-E22): the Jetson U69 loop's 40 % (Palace 0.20 nH, pcbqs 0.34 nH at 12 MHz).
4. **Real boards** (R-B14): the Elmer builder's fragmenting, a gap per edge.

## The window

The window keeps its docks (the user's word, 2026-11-12): nothing below may fix a panel in place.

1. **The study as a model without Qt, the rest:** the panels hold the values between a load and a save (the
   document is filled on open and save, not on each edit), undo is the form's, the board as read is the window's.
   Then the KiCad plugin can drive a study through them.
2. **An analysis's few settings first:** Elmer's and Palace's tools do not name their `basic` options yet (the coil
   tool does).
3. **Picking on the board, the rest:** a pad (a port), a `REF=VALUE` list's part, a region dragged as a box, the
   pick in the 3D view; the Circuit page's *Load model…* and *Save model…* buttons go.
4. **The Start page:** a tile says nothing of what its example takes (the engine, the time).
5. **The schematic's format-1 layout code**, left from the removed bridge.

## The line model

1. **Nets reached only through the field:** a combline filter's inner rods are in no model (no pass band on
   rf_combline); copper between or beside coupled lines that is no ground net should be a line of its own.
2. **A coupled section's cut along its length:** cut once, across its middle; copper beside part of it counts for
   all of it or none. Cut at a few places and split where they differ.
3. **The conductor loss from the cross-section, the rest** (done for scikit-rf's trace and coupled sections: each
   distinct cross-section's eddy-current solve, `fastsolve.field_losses`): qucsator's microstrip models
   (MLIN, MCOUPLED, CLIN) keep their own Hammerstad-Jensen loss (1.6× the 2D solve's on a 50 Ω microstrip) and
   its RLCG sections the band centre's (an SPfile per section would take each frequency's); pads and
   de-embedding's line sections keep 2 Rs / w; above 48 distinct cross-sections (`FIELD_LOSS_MAX`) the formulas
   come back: the solves in worker processes, as the field solver's cuts are, would let that rise.
4. **Discontinuities, the rest** (R-E15): a tee's and a cross's reactances on the scikit-rf backend.
5. **One multiconductor model** (R-F5): dfsynth's and `coupled.mtl_s` are two; so are the Touchstone readers.
   `coupled.zy_s` (2026-11-27) takes any per-unit-length Z and Y matrices at each frequency (an RLGC's: full R,
   the internal inductance, G, C), and `mtl_s` and the line model's lossy groups go through it: dfsynth's
   `mtl.Array` could too, with `core.lines.mqs` for its loss.
6. **The mesh's key lines** run across the whole box; merging cells away from the corners needs constraints for
   sides that overlap without nesting.
7. **Boundary elements with a layered Green's function**, only if the mesh's 0.2 s becomes the limit.

## The KiCad plugin (`kicad2ems.ipc`)

1. The analyses beyond the DC drop and the temperature (the near field, the circuit, crosstalk, time domain) are
   the window's only (R-E26).
2. Its environment builds gfdtd without the compiled core (NumPy, ~15× slower): the core's build there, where a
   compiler is.
3. Starting a study (the study 9) and its release (Release 4).

## dfsynth

R-E18, in order: the tapped hairpin feed; combline; elliptic and cross-coupled designs.
