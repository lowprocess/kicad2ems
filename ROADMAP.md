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
4. **Planes with antipads: an aperture formulation.** A plane a signal via passes through (demo_transition's
   inner plane) cannot be a plain PEC. Keep it PEC and give only its holes unknowns (magnetic currents in the
   antipads): the plane then costs its hole area, not its whole area.
5. **A hierarchical direct solver.** The compressed solvers are not N log N: `blr` fills every tile densely and
   factors them in a flat tile LU (O(N²) fill, O(N³) with a fixed tile), and `aca` is an H-matrix under GMRES
   whose iteration count the MPIE's conditioning leaves unbounded. Plane–plane interactions are strongly low
   rank; an H-LU or HODLR factorisation built with ACA would help most on exactly these boards, and on every
   large one.

Items 1–3 are small and stack; each needs the accuracy check the quad cells had (a fine reference, and openEMS
on rf_stripline and demo_transition), and a column in the benchmarks.
