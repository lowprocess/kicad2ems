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
6. **A hierarchical direct solver.** `solver='hodlr'` (`mom25d/hodlr.py`, 2026-10-29, experimental): a binary
   geometric tree; each node's two off-diagonal blocks low rank (ACA with a check of the rows holding significant
   near-field entries, where partial-pivoting ACA alone ended early and left blocks 4–7 % off; or the exact block
   by an adaptive randomized range finder, in row panels past 512 MB); Sherman–Morrison–Woodbury factorisation;
   optionally the preconditioner of GMRES on the exact operator, factored again 100× tighter if that stalls.
   Measured (CPU, 8 P-cores, one solve at 5 GHz):

   | | per solve | memory | \|ΔS\| from dense |
   |---|---|---|---|
   | hairpin, 4,763 unknowns: dense | 0.7 s | 0.35 GB | |
   | HODLR 1e-4 / 1e-6, compressing the filled matrix (build + factor) | 1.1 s / 7.6 s | 51 / 112 MB | ranks at the top 32 / 212 |
   | demo_transition (meshed), 13,543: dense | 13.7 s | 2.8 GB | |
   | HODLR 1e-4 | 24.5 s | 0.77 GB | 2.2e-3 |
   | HODLR 1e-6 | 160 s | 1.9 GB | 7.7e-6 |
   | HODLR 1e-4 + GMRES (11 iterations) | 26.5 s | 0.77 GB | the exact operator's, to 1e-6 |

   What it shows: the ranks are low (the hairpin's top block: 16 at 1e-4 and 73 at 1e-6 of 2,381), the
   memory falls 3–4×, but it is not faster at these sizes, for two reasons. (1) The entries: on boards with
   coarse planes the near-field correction reaches 5 cells (15 mm under 3 mm plane cells; 15 % of the matrix,
   ~2,100 entries a row), so most rows of a block hold significant near entries and every block is compressed
   from its exact entries: N² entries and ~N² r flops, about the dense LU's work at 13k. (2) Weak admissibility
   on a 2D board: the top blocks' ranks grow like √N (241 / 670 at 13.5k against 32 / 212 at 4.8k). So it is
   ~N^2.5, not N log N; it wins by memory first (a 55k board, 48 GB dense, fits), by time only well above
   ~30k. The 55k CM5 run (filled, whole stack, zone: 54,885 unknowns) was stopped by the system for memory
   before it finished: the near-field CSR and its scaled copy at 55k are the suspects, to be measured.

   To reach N log N, in order:
   - **the near field's reach**: cap it at a distance (not 5 × the cell: 15 mm under plane cells), so ACA can
     compress from O(N r) entries and most blocks are not "hot". Measured (2026-10-29): on demo_transition
     `near_fac` 5 gives 1,199 near triangle pairs a triangle (11.3 M, 2,100 entries a row, 75 % of them
     between layers: every layer is within reach through thin dielectrics); 3 gives 548 and moves S by
     ≤ 1.3e-3 from the converged reach (8), 5 by ≤ 2.3e-4; the hairpin the same. A candidate default once
     checked on the other benchmark boards: half the near field's cost and memory in every path;
   - **strong admissibility** (H-LU, or HODLR with a nested-dissection order) so ranks stay bounded;
   - **an FFT-based operator (pFFT / AIM)**: a planar board's layered Green's function depends on the lateral
     distance only, so its far field is a 2D convolution per pair of interfaces: O(N log N) products, O(N)
     memory, with the near correction mom25d already has and a loose HODLR (1e-3–1e-4) as GMRES's
     preconditioner (11 iterations at 1e-4 on demo_transition). This is the route to boards of 100k+;
   - **reuse across the sweep**: done (2026-10-29, `fast._solve_kept`, `_solve_reused`). One frequency's LU
     preconditions the band (the 5 GHz LU of demo_transition: 6–30 GMRES iterations from 1 to 10 GHz). The
     CPU keeps up to 4 complex64 LUs where memory allows, the dense double Z for GMRES's products; one LU and
     the matrix-free products where not; the GPU on the GPU; BLR and HODLR their factorisation.
     demo_transition's CPU sweep 176 s → 84 s (3 LUs for 17 solves), GPU 107 s → 76 s. Found on the way:
     ROCm's complex64 triangular solves lose ill-conditioned factors' accuracy (where the GPU's solves stall,
     the factor is now solved on the CPU), and the batched GMRES's Givens rotations were not
     conjugated, so on complex Hessenberg entries it stopped early (a true residual 1.5e-4 where it reported
     1e-6) and the refinement was judged failed: fixed. The near pairs' analytic parts are now kept across
     frequencies too (the near field into the dense Z 1.10 s → 0.29 s a frequency on demo_transition);
   - **loop–star (or loop–tree) basis**: the MPIE matrix's low-frequency conditioning sets both the GMRES
     iterations and how much a compressed factorisation's error is amplified (10³–10⁴ here);
   - the GPU for the compression's products and the leaves' factorisations; symmetry (Zᵀ = Z: half the
     compression, `symmetric=True`, to be checked).

Items 1–3 are implemented (`--mom-planes pec`, `--mom-stitching-pads drop`, the zone's grid; experimental, off by
default) and checked against the meshed model and openEMS (`tools/mom_planes_study.py`, 2026-10-29):

| | unknowns | time | max \|ΔS\| from meshed | openEMS: reflection / transmission (dB) |
|---|---|---|---|---|
| rf_stripline, meshed | 15,563 | 100 s | | −23.0 / 0.21 |
| rf_stripline, planes PEC + pads dropped | 1,337 | 3 s | 0.003 | −23.1 / 0.21 |
| rf_stripline, all (+ zone, quads) | 1,076 | 3 s | 0.003 | −23.1 / 0.21 |
| demo_transition, meshed | 13,670 | 107 s | | −30.6 / 0.15 |
| demo_transition, pads dropped | 4,043 | 14 s | 0.009 | −31.2 / 0.15 |
| demo_transition, all | 2,805 | 11 s | 0.012 | −31.1 / 0.15 |

Left: making them the default, docs/mom.md#planes, and a column in the benchmarks.
