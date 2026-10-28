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
4. **Pours from real boards: fill the voids of nets that are not simulated.** A board imported from KiCad with
   only a few nets selected keeps every clearance void its pours have around the other nets' pads, traces and
   vias. Then no plane is solid (item 1 never applies), and each void has edge rings. Measured 2026-10-28 on a
   real 4-layer board (a synthesizer carrier, not in the repository; 55 × 50 mm, only `/RF_A_P`, `/RF_A_N` and
   GND kept: each layer 86–97 % GND pour, 17–59 voids per layer, 761 vias), with mom25d's mesh at 0.3 mm:

   | | unknowns |
   |---|---|
   | as imported | 157,459 (a dense matrix: ~400 GB) |
   | lone pads dropped (item 2) | 147,370 |
   | the 154 voids with no selected copper or via in them filled | 142,070 |
   | filled + planes PEC + pads dropped (In2.Cu and B.Cu now solid: ground In1.Cu) | 44,071 (all on F.Cu's pour) |
   | as imported + PEC + pads dropped + `--mom-zone on` | 30,948 |
   | filled + PEC + pads dropped + zone (+ quads) | 8,952 (8,534) |
   | filled + PEC + pads dropped, F.Cu's pour cut 3 mm / 1.5 mm from the signal copper, quads | 6,572 / 4,489 |

   Without filling, this board needs item 6 (31k–157k unknowns). With it, a dense GPU LU is enough. To do:
   - the rule: fill a void unless it holds copper or a via of a selected non-ground net (an antipad), or lies
     within the zone distance of the signal copper projected from any layer (a void under the trace changes
     its return path: keep it). Small voids anywhere (thermal-relief gaps, under ~0.5 mm²) are filled unless
     they are an antipad;
   - where: at import, in kicad2ems (`Board`), as an option every solver gets (`--fill-voids off | far | all`),
     so the solvers still compare on the same copper; declared like `--ground` (area added per layer). The
     KiCad plugin's selected-net runs could default to `far`;
   - optionally, a ground pour on a signal layer cut to a distance from the signal copper (F.Cu above): the
     stitching vias hold it at ground, and beyond a few dielectric heights it carries next to no current;
   - check against openEMS on a cropped section of a real board, filled and not (openEMS meshes both).
   A long diff pair through vias is the harder case. Measured on a second real board (a 6-layer CM5 carrier,
   not in the repository: the PCIe RX pair, 0.14 mm traces 0.15 mm apart, ~55 mm on F.Cu, then two vias through
   all four inner planes to B.Cu; a 30 × 29 mm region; `--mom-stack whole`, since a port is on B.Cu):

   | | unknowns |
   |---|---|
   | as imported | 167,849 |
   | lone pads dropped | 152,331 |
   | voids filled (far from the signal) | 119,644 |
   | filled + zone (+ quads) | 54,885 (55,168): each inner plane still ~7.5k, F.Cu 15k |
   | filled, GND copper cut 1.5 mm from the pair on every layer, zone | 58,645: cutting does not help |
   | F.Cu alone over In1.Cu (what item 5 would leave of the top), filled, zone + quads | 10,593 |
   | the same with no F.Cu pour at all: the pair alone | 7,112 |

   The planes cannot be PEC here (the pair's vias pass through them all), so filling alone leaves ~55k: item 5
   is what brings this board to ~12k (F.Cu ~10.6k plus B.Cu's pads and pour), which a dense GPU LU still
   handles (complex64: ~1.2 GB). What remains scales with the pair's length (the pair alone ~7k for 55 mm).
   That is the job of the mixed solver (long runs as lines), which today cuts only lone traces (a pair's legs
   are each other's neighbours) and needs wave ports (the whole stack has only lumped ones). Extending it to
   coupled runs (4-port coupled lines, `line_char_pair` exists) would cut the pair's straight runs: 9, 5–7, 5
   and 3.5 mm here, ~40 % of its length.
5. **Planes with antipads: an internal PEC plane with magnetic frills.** A plane a signal via passes through
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
6. **A hierarchical direct solver.** The compressed solvers are not N log N: `blr` fills every tile densely and
   factors them in a flat tile LU (O(N²) fill, O(N³) with a fixed tile), and `aca` is an H-matrix under GMRES
   whose iteration count the MPIE's conditioning leaves unbounded. An H-LU or HODLR factorisation built with
   ACA would make large boards N log² N. After items 1–5 the plane-heavy examples are 1–4k unknowns, where the
   dense GPU LU takes well under a second: there, the fill and the Green's tables dominate a solve, not the LU.
   Do this once a real board (a large array, a whole-board run) stays above ~15k unknowns after 1–5 (unfilled: 31k on the synthesizer carrier; without item 5: 55k on the CM5 carrier), and
   measure first the off-diagonal ranks of Z on it (plane-plane blocks, trace-plane blocks) at the top
   frequency.

Items 1–3 are small and stack; each needs the accuracy check the quad cells had (a fine reference, and openEMS
on rf_stripline and demo_transition), and a column in the benchmarks.
