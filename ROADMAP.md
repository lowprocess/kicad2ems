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

## The Draw page: a 3D editor (`gui.drawtab`, `core.shapes`)

The next focus for extension and the window's UI (the user's word, 2026-11-27). The page (2026-11-20, on main
since 2026-11-27) draws rectangles, circles, polygons, traces and ports as copper or cuts on a layer, and metal,
dielectric and air solids between two heights. It has plan and 3D views, selection of several shapes, the
clipboard, locking and hiding, exact edits and arrays, snapping, measuring, camera presets, a clip plane and a
gizmo in 3D, a properties form, undo, and writes the `shapes`, `region` and `ports` settings; ferrites and
magnets too (2026-11-27), so a circulator is built and run from it. What it lacks, most useful first:

**What the drawing does**

1. **Solids in the solvers, the rest** (the FDTD's model and mesh, Palace's volumes and mom25d's refusal; drawn
   metal's own loss, small posts meshed as vias, antenna mode and `--thickness on` with solids: done 2026-11-27,
   docs/meshing.md#drawn-solids):
   - curved and oblique metal sides stay PEC with `--sigma` (their staircase edges as sheets would give them loss);
   - a drawn metal's own σ and steel's μr (copper's now; a magnet NdFeB's);
   - the faces cost what they cover: rf_shielded_can's lid and walls are 54 k of its 85 k sheet edges, 4 % a step,
     for 0.002 dB; a solid marked lossless would save that;
   - metal thinner than its cell in plan is said and left out;
   - Palace meshes the air outside a drawn can that closes round the board (125,000 tetrahedra against its PEC
     box's 118,000, 161 s against 145 s); the FDTD leaves it out; Palace has not run the DRA;
   - mom25d could take a dielectric solid over the whole board as a stack layer (a cover) instead of refusing it;
   - a port on a solid's face and a coax port: `board.Port` as a box between two metal faces (`kind='box'`: a
     lumped port in the FDTD, a rectangle in Palace, lines at its faces); a coax template's end face with its TEM
     mode, E ∝ 1/r, as a weighted excitation and a mode-matched probe (the machinery of `lineport.py`), Z0 =
     60/√εr ln(b/a); checked on a drawn 50 Ω coax line (S11 under −30 dB) and the probe-fed cp_patch;
   - a high-εr solid's staircase: rf_dr_bandpass's puck (εr 38) is 0.35 % apart between gfdtd and openEMS on the
     default mesh, 0.03 % on 3.2× the cells: a curve refinement for dielectric solids, like `--curve-res` for
     copper;
   - the loss in a lossy high-εr solid: the puck's TE01δ unloaded Q is 4,420 on Palace, 3,740 on gfdtd (3,370
     finer) and 2,410 on openEMS (2,940 finer): how each engine puts a solid's loss at its faces, checked by a
     lossless run against Palace's eigenmode;
   - more examples: a cavity combline (metal rods in a drawn can, tapped by a pin and bar: rods need `res` ≥ 80,
     the tap bar loads the end rods, the loaded-rod closed form gives 4.8 GHz for 4 × 10 mm rods with a 0.7 mm
     gap);
   - set aside: a box-shaped closed can as the PEC boundary at its inner faces. The PEC-boundary run is that model:
     0.41 M cells and 33 s against the drawn can's 0.45 M and 35 s (−6 %), under the 10 % a pass must gain.
2. **Ferrites and magnets, the rest** (done 2026-11-27: the `ferrite` and `magnet` materials, `core.magnets`'s bias
   as a full vector with the ferrite's magnetisation self-consistent, gfdtd's `FerriteMaterial` along each cell's
   own direction, a partly magnetised ferrite (Green and Sandy), the grid's magnetostatic modes gone (the faces'
   scheme); `rf_circulator`, `rf_circulator_stripline`, `rf_isolator`: docs/gfdtd/README.md#how-it-works,
   docs/examples.md):
   - steel: pole pieces, a return path, a steel case shape the bias as much as the magnet does; a magnetostatic
     solve with B-H curves (Elmer's `WhitneyAVSolver`, which the coils study already drives) would give the bias
     where the face-charge sum cannot;
   - the partly magnetised ferrite's low-field loss and Green and Sandy's μz; χ as a material parameter;
   - the voxel staircase at curved rims: partly magnetised rim voxels scatter about ±0.1 Ms;
   - the faces' scheme costs 3–4× the old update on the GPU (memory-bound, 88 / 140 B a cell, 36 an edge): fuse
     or compress it;
   - the end criterion counts the field's energy, not the magnetisation's (−40 dB was enough on the circulator:
     0.6 dB from −60);
   - the linewidth as Gilbert damping, ΔH ∝ f (from the datasheet's at 9.4 GHz); temperature (4πMs and Br at
     25 °C);
   - Palace: its μ is a real symmetric tensor; a ferrite's is Hermitian (its operator code, not ours);
   - a circulator study question: isolation, insertion loss and the 20 dB band, the magnet's height and the
     disc's radius optimised (the template is done); an isolator's termination template (a thin-film load or a
     radial-stub return: a resistor to a via reflects −8.3 dB at 9.2 GHz and caps the isolation at 10 dB);
   - the page: the *Bias* readout still uses the z model (the self-consistent one takes 0.4–0.9 s: in a worker);
     an H0 slice in 3D and the magnet's field lines (the H0 map in plan is done); the readout's 20 ms is three
     quarters `core.magnets._omega_up` (numpy over every face triangle).
3. **Templates, the rest** (done 2026-11-27, `core.templates`: a shield can with posts, a dielectric resonator
   on a ported line, rf_circulator's Y junction, an edge-launch SMA as a square coax, an absorber sheet, a via
   fence along traces): templates are one-shot, so a group that keeps its template and numbers, reopened by a
   double click and made again (the step to item 4); a can with openings where traces pass, its posts kept off
   them, and "a can over the selection"; an enclosure with a lid; a vertical coax or probe feed; a waveguide; a
   port on the SMA's coax face (item 5).
4. **Dimensions as variables:** a shape's numbers as named variables (w, gap, h) in the drawing, which the study's
   *Vary* sweeps, and later an optimiser (the study 10's optimisation).
5. **Ports, the rest:** a port's kind (lumped, wave), impedance and direction in the form; a port on a solid's face
   for the FDTD; lumped R, L and C between two points.
6. **Import:** DXF outlines as polygons; STEP or STL of an enclosure or a connector as a solid (its triangles, for
   the FDTD and Palace).
7. **Booleans and curves, the rest** (done 2026-11-27, `core.shapeops`: union, subtract, intersect into polygons,
   holes kept by a cut of no width; fillets and chamfers of every corner and of a trace's bends): per-corner
   fillets; true arcs kept as arcs, not facets; growing and shrinking a shape; booleans of solids of different
   heights (3D CSG).

**The page (UI)** (selection, exact edits, snapping, measuring, the 3D view's tools and the speed with 300 shapes
were done on 2026-11-27; then its menus, icons, grouped form, scrolled tabs, the drawn solids in the Viewer and the
mesh preview, an orthographic camera, arrowheads, a 3D box selection, select alike and the arrow keys: CHANGELOG)

1. **The page itself, the rest:** the list as a tree of groups (a template's shapes one group, selected, moved,
   locked or hidden at a click); a command palette (Ctrl+K) and an undo history list; the side-by-side views
   remembered.
2. **The 3D view, the rest:** the 3D box adding to the selection (it replaces it now); a plane handle on the gizmo;
   the board's copper capped at the clip (its thin slabs are cut open).
3. **Selecting and editing, the rest:** a point typed while drawing (a corner at x, y); snapping to a pad's corners
   and edges and along a track's centre line and to intersections; snapping while dragging in 3D (the grid alone
   there); the measure tool in 3D; dimensions shown on the shapes and edited there.
4. **Speed, the rest** (tools/gui/draw_bench.py, a 300-via fence over sma_launch, offscreen):
   - a first 3D draw (31–32 ms: every shape's items made, as on opening a drawing) is now mostly pyqtgraph's
     GLMeshItem construction, and each shape is a draw call every frame (the GPU's time is not measured
     offscreen): items batched by look would cut both, at the cost of splitting a batch when a shape in it is
     selected or dragged;
   - the plan's paint is 6 ms for 300 items, and a move of the whole fence is that paint; on a large board
     (LibreVNA) the board's own copper is most of a move's 11.6 ms.
   - Tried and rejected (the same fence, load 11 from other runs, so the times are ~2× a quiet machine's):
     - the plan's items cached as pixmaps (DeviceCoordinateCache): a repaint with nothing moved 12.2 → 6.2 ms,
       but panned 14.4 → 12.7 (−12 %) and zoomed 15.9 → 18.8 (+18 %), with a pixmap an item; a repaint with
       nothing moved is the rare one;
     - one path item for every unselected shape of a look: painted slower (15.3 ms against 12.2: no culling of
       what is off the view) and made again (3.2 ms) at every change to any of them;
     - the shapes' 1,500 snap points in a k-d tree: a query 0.014 ms against numpy's 0.015, and 0.15 ms to
       build at each change (no gain below ~10⁴ points).

## The study: questions and engines

1. **gfdtd is not offered for the antenna question:** `study.engines` reads a solver's `antenna` capability up
   to its first colon, and gfdtd's ("yes (a phased drive: ...)") is neither "yes" nor "approximated".
2. **A second engine where a question has one:** capacitance (Palace alone; pcbqs's electrostatic solver is not
   wired as one), coils (Elmer alone; pcbqs's quasi-static inductance), the circuit (ngspice alone; Xyce: it ran
   behavioural switches ngspice could not, 2-7× slower).
3. **New questions:** segment and the plane-pair cavities as study questions (they are command-line tools); a
   return-path check (traces over plane splits and voids, layer changes with no stitching via near); via stubs
   and backdrill (each via's stub resonance against the data rate); matching networks from a solved S11 with
   real part values (`core.embed`).
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
10. **Candidate solvers and questions** (talked over with the user on 2026-11-27, not started; most useful first):
    - a 2D full-wave port-mode solver (a cross-section's modes, Zc and dispersion, higher modes, CPW at mmWave):
      true wave ports for every engine, which closes item 6; from `core.lines.xsection`'s mesh;
    - a layout rule check, no solver (item 3's return-path check with the rest: fast nets near the board edge,
      decaps far from their pins, unreferenced traces);
    - high-voltage field stress (peak E at sharp copper, Palace or Elmer electrostatics) and IEC 60664 / 62368
      creepage and clearance;
    - a passive layout's noise figure (its noise from its S-parameters) and an amplifier's K and μ: no new solve;
    - cable common-mode radiation (`core.rlgc` and field-to-line coupling: the EMC section's first item);
    - a power tree: source → regulators (LDOs are not found today) → rails → loads, a budget with efficiencies,
      each rail's drop and impedance from it, every loss in one thermal map;
    - tolerance and yield: Monte Carlo over part tolerances through `core.embed` (no new solve), geometry and εr
      by the line model, sensitivities or polynomial chaos; a spec mask and the yield;
    - optimisation: part values first (item 3's matching networks, E-series values), then dfsynth's designs with
      EM in the loop (the line model as the coarse model);
    - channel compliance (COM, the PCIe, USB and DDR masks) once the time domain has equalization (Signal
      integrity 1); PDN-induced jitter (the rail's impedance in the channel);
    - ESD, surge and EFT (IEC 61000-4-2, -4-5, -4-4) through the protection parts on the macromodel or ngspice;
    - reliability from the thermal map: plated-hole fatigue (IPC-TR-579), solder-joint fatigue (Engelmaier),
      warpage from the copper balance;
    - airflow film coefficients from correlations (no CFD); planar magnetics' winding and core loss (Elmer, R-E21);
    - lower: characteristic modes (mom25d's Z is complex-symmetric; the substrate's surface waves land in R),
      periodic eigenmodes (Palace with Floquet boundaries; F88's rf_ebg reference), a 3D surface MoM for
      connectors and cans, DGTD, a board's vibration modes. Not planned: full-wave PEEC (in a layered stack it is
      mom25d), hybrid MoM-cavity enclosures (an enclosure in Palace's or the FDTD's model instead).

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

## EMC: radiated emission and susceptibility (`core.emission`, `core.susceptibility`)

1. **Cables:** a cable's common-mode current is most often what radiates and what picks up, and it is left out.
   It needs:
   - the cable's own model, its RLGC (the multiconductor cross-section);
   - the field-to-line coupling (Agrawal / Taylor) from the board's own near field, or from the plane wave;
   - its common-mode source: the board's ground noise across the connector, and its transfer impedance.
2. **The board edge and its ground:** over mom25d's infinite ground nothing radiates below and no edge radiates
   (`--mom-stack whole` meshes the planes, in a laterally infinite dielectric). Two fixes:
   - gfdtd's per-port far fields (an antenna run with every port excited, the array file `--array` reads) at the
     questions' frequencies;
   - an enclosure.
3. **One solve for both questions:** a study asking both solves the board's every port twice, at each one's
   frequencies. The per-frequency solve (mom25d's fill and LU) is 86-88 % of either run.
4. **Several independent signals** in one emission run, each with its own spectrum and its own phase.
5. **The detectors on modulated and noise-like emission:** quasi-peak's weighting of pulsed signals, and data's
   peak (its RMS in the bandwidth is given).
6. **A single-port antenna run's array file:** mom25d writes `_array.npz` only with several ports.
7. **Measured and set aside** (rf_serpentine's emission, 2.8k unknowns; rf_branchline's, 2.9k; against 241
   frequencies):
   - refining only above the neighbours' line (peaks) left a peak between two samples: 4.14 dB short on
     rf_branchline;
   - refining everywhere spent the 41 samples on notches: 2.92 dB short there;
   - refining within 20 dB of the top stopped at 28 samples, 1.16 dB short on rf_serpentine;
   - the far field's phases by cos / sin into the real and imaginary parts instead of a complex exp: 7 % of a
     block (0.151 s against 0.165);
   - complex64 phases: no faster.

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

- **Ground-plane loss (found 2026-11-27, drawn metal's loss):** a zero-thickness ground plane is openEMS's
  two-sided sheet and gets half its loss (`--thickness on` gets it right); one on a PEC bottom boundary or in a Mur
  cell is lossless with `--sigma` (`rf_shielded.toml` among them). rf_shielded's res-80 mesh is not converged in x:
  dense x lines move its peak 37 MHz up, to 4.6625 GHz (EMerge 4.621).
- **A cylinder of no length is left out** (gfdtd's `inside()`): a flat disc drawn as `AddCylinder` with start = stop
  z is silently absent; openEMS takes it as a disc. Seen in the ferrite tests (their disc is a polygon now).
- **Weakly coupled high-Q runs** stop when the probes' signals have decayed while the cavity still rings: they need
  `end_on = "energy"` (tools/fdtd/can_q.py does); the end criterion could watch the energy when the ports' coupling
  is weak.

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
5. **What a PI user takes away, the rest** (R-E14): an impedance for a rail with no planes, a vendor's DC-bias
   curve by part number (`--derate-bias` takes a typical one by package and value, `--derate-table` a file), a
   ground pour beside the rail on its own layer (Palace's PDN engine read the buck example's bare |Z| 0.68 of
   pcbqs's, R-E22).
6. **Decoupling, the rest** (`pcbqs.pdn --choose-decaps`, docs/pcbqs/decoupling.md):
   - sites it could add (a footprint where none is) and packages a footprint does not take: neither is proposed;
   - a rail with no plane pair has no PDN model, so no choice;
   - the droop after a load step with the chosen parts (pdn.json's `droop` is the fitted parts'), and a target in
     time as well as in frequency;
   - a second engine: Palace's bare port matrix (`fem.palace.pdn`) closed by the same search;
   - the search is greedy with local moves (drop, two for one, move), not proven optimal: an exhaustive check
     beyond one-part sets on a real rail;
   - speed (measured, load average 5-9 from other runs): on the Jetson's +3V3 (49 sites, 3 loads, 160
     frequencies) the search is 3.4-5.4 s, two thirds of it the two-for-one move over every pair, and few of its
     15 calls succeed (one in the result kept): first-improvement there would save little. Rejected: the
     reciprocal at (f, site, option) and a product in place of the batch's division (4.22 s against 4.46 s,
     within the noise).
7. **Low-frequency S-parameters, the rest** (docs/pcbqs/sparams.md): gap ports; the two-face copper with the
   charge slow on several dielectrics (25-77 s a frequency); the window's Plots page draws a log sweep on a linear
   GHz axis, and the report's table reads "0.000 GHz" for a run in kHz.
8. **Accuracy:**
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
9. **Losses and thermal:** a convection film that grows with the rise and radiation, air one part warms reaching
   the next; the iGSE's DC-bias and relaxation terms; the parts' bodies' heat capacity (a part's first seconds
   read quick). The drop at temperature (`pcbqs.electrothermal`), the rest: its transient keeps the hot steady
   state's current pattern (the pattern's shift as the copper warms is the passes' alone); a series part's and a
   load's own resistance against temperature; a circuit run's losses (`--circuit`, as the thermal tool takes
   them) and its copper's AC loss; several rails at once (their copper heats one board). Its speed on the
   Jetson's SYS_VIN_HV (118 s with a transient, 2026-11-27): pcbqs.pdn's mesh of the rail and its ground 40 s; the
   viewers 16 s (each writes the board's copper twice, `*_board.json` and again in `dump_info.json`); the figures
   10 s (the thermal picture's outline, 31,000 `plot` calls: one LineCollection); the DC re-solve of each pass by
   conjugate gradients on the first pass's factor, 0.8 s against spsolve's 15.0 s on its 1.14 M ground unknowns,
   left for now as 8 % of a two-pass run (more with more passes). Measured and set aside: SuperLU in a symmetric
   ordering for the DC solve (6.62 s against spsolve's 6.67); the error-controlled stepping with every step
   length factored, within a 4 GB budget, on the Jetson grid (786 s: 122 factorisations as the budget evicted them,
   against 244 s and 18.9 GB keeping 20; the reduced model, 20 s, is the answer).
10. **Conducted emission, the rest** (`pcbqs.conducted`, docs/pcbqs/conducted.md):
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
11. **Speed:** the full bridge on ngspice (1 min 47 s with copper against the old tool's 6 s); two faces in the
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
