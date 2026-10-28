# Roadmap

What is worth doing next, in priority order. Each item comes from something the examples or benchmarks
showed. Finished items leave this file; what was done is in CHANGELOG.md, and the decisions not to do
something are under its *Decided* headings. Last updated 2026-10-28.

## mom25d on multi-layer boards

mom25d is slowest where planes have to be meshed (rf_stripline 15,458 unknowns, demo_transition 13,543; the
filters are 4–6k). Most of those unknowns are not the traces: on rf_stripline the signal layer's 12 mm² of
copper carries 8,483 of them, mostly 38 isolated stitching-via pads, and its two solid planes 6,859.

1. **Solid planes as PEC, not meshed.** A plane that covers the board with no holes, and only stitching vias
   through it, can be the solver's ground (the plane under the signal copper: `BoardModel(ground=)` already
   leaves the copper below it out) or a PEC cover over it (`LayerStack(top='pec')` exists; `BoardModel` never
   uses it). rf_stripline: 15,458 → 8,561 unknowns, about 5× less LU work. To do: choose both automatically
   in `BoardModel` (solid, no antipads, nothing meshed beyond it); check against openEMS on rf_stripline.
2. **Stitching-via pads on signal layers.** Each round pad is meshed with its own edge rings. Square stitching
   vias alone take rf_stripline to 8,823 unknowns, with item 1 to 3,393 (4.6× fewer, ~95× less LU work). A
   pad whose via connects to nothing else on that layer could be dropped, the via kept as its barrel. Check
   accuracy (square stitching vias cost rf_gcpw 0.002 → 0.006–0.010 at 9 GHz).
3. **`--mom-zone` with quads.** Zone and quads together give more unknowns than zone alone (rf_stripline 5,647
   against 4,985, demo_transition 3,788 against 3,563): the structured runs' grid ignores the zone's coarse
   cells outside it. The grid should stay inside the zone. Zone is the largest single cut on these boards (3–4×).
4. **Planes with antipads: an internal PEC plane with magnetic frills.** A plane a signal via passes through
   (demo_transition's In1.Cu) cannot be a plain PEC cover or ground. Plan (2026-10-28, not started):
   - the stack's TL network (`multilayer.LayerStack._network`, `via.TMNetwork`) gets internal PEC interfaces:
     Yd / Yu restart shorted there, the voltage is zero across it. Every existing kernel then decouples the
     copper above from the copper below by itself: no new horizontal kernels, and the matrix splits into two
     blocks coupled only through the vias that pass the plane;
   - each antipad (a round hole around one via) gets one unknown, the voltage across it: a magnetic frill,
     M_phi = V / (rho ln(b/a)), the annulus' TEM-like field (antipads are far below a wavelength). It radiates TM
     only: in the TM network a series voltage source at the shorted interface, its radial profile
     (J0(kr a) - J0(kr b)) / ln(b/a). The via through the hole stays one chain (current continuous); with V = 0
     it would be shorted to the plane, which is the limit of a zero antipad;
   - new kernels: frill to horizontal currents (order-1 Hankel, E_rho), frill to via chains (order 0), frill
     to frill, self and mutual (order 0; the self term's coaxial part 1 / (rho ln(b/a)) analytically). The
     extra equation per frill: H_phi continuous across the annulus, tested with the frill's profile;
   - a plane qualifies when every hole is one via's antipad (an equivalent annulus of the same area for a
     non-round one). Slots and cutouts stay meshed.
   Gain: demo_transition with lone pads dropped (item 2) has 3,910 unknowns, 2,408 of them the plane: about
   1,500 with frills (~18x less LU work; with the zone and quads 2,701 to ~1,100). It applies to every board
   whose signal vias pass through a plane. Effort: ~500 lines in `multilayer.py`, `via.py` and `sim.py`, and
   validation (a via through a plane against openEMS, demo_transition, rf_msvia).
5. **A hierarchical direct solver.** The compressed solvers are not N log N: `blr` fills every tile densely and
   factors them in a flat tile LU (O(N²) fill, O(N³) with a fixed tile), and `aca` is an H-matrix under GMRES
   whose iteration count the MPIE's conditioning leaves unbounded. An H-LU or HODLR factorisation built with
   ACA would make large boards N log² N. After items 1–4 the plane-heavy examples are 1–4k unknowns, where the
   dense GPU LU takes well under a second: there, the fill and the Green's tables dominate a solve, not the LU.
   Do this once a real board (a large array, a whole-board run) stays above ~15k unknowns after 1–4, and
   measure first the off-diagonal ranks of Z on it (plane-plane blocks, trace-plane blocks) at the top
   frequency.

Items 1–3 are small and stack; each needs the accuracy check the quad cells had (a fine reference, and openEMS
on rf_stripline and demo_transition), and a column in the benchmarks.
