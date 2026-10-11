# 6. Meshing

[← The 3D model](model.md) · [Index](index.md) · [Next: Transmission-line model →](transmission-lines.md)

`mesh.auto_mesh(m, fmin, fmax, res, fine, air, ratio, bc, nz_min, curves, vias, via_snap)` builds a
rectilinear mesh (lists of x, y, z lines in mm). FDTD accuracy depends on resolving the copper edges and
the wavelength; the time step depends on the smallest cell, so every line costs run time.

## Cell sizes

- **Maximum cell:** λ / `res` at `fmax`, separately in the dielectric (λ / √εr,max) and in air.
- **Fine cell** `fine` at copper edges. Default: min(narrowest port trace width, 2 × thinnest
  dielectric) / 4, clipped to [0.01 mm, a quarter of the maximum dielectric cell].
- **Growth:** neighbouring cells differ by at most `ratio` (1.4).

## x / y lines

<a id="thirds-rule"></a>**Thirds rule.** For each Manhattan copper edge (along x or y, at least `fine` long), a pair of
lines across the edge: one a third of the pair's spacing inside the metal, one two thirds outside. This
puts the edge between two lines in the right proportion for the FDTD field singularity at the metal
edge. The pair spans `THIRDS_CELL · fine` = 2/3 `fine` (lines `2·fine/9` inside and `4·fine/9`
outside): with pairs a full `fine` apart, edges closer than about 1.5 `fine` (a trace end, the via pad
round it, the antipad) gave lines within the merge distance of each other and too few survived - see
the validation below. An edge counts as Manhattan if it drifts
by at most `fine/10` across its axis (`mesh.manhattan`): the 2 µm simplification in `Board` can merge a
tiny jog into a long, very slightly oblique edge, and without the tolerance such edges got no
thirds-rule lines (a 0.35 mm feed line ended up with one mesh line across it).

**Curves** (`curve_lines`): for non-Manhattan runs of edges (rounded corners, arcs, round track ends,
circular antipads, oblique edges) `extremes` (default) adds lines only at each run's end points and its
local x / y extremes; `vertices` adds one at every vertex.

**Vias** (`--vias`): `exact` adds lines at the centre and ± radius; `centre` one line per axis; `snap`
no lines of their own — the via moves onto the nearest existing line crossing if within `--via-snap`
(else it gets centre lines). Centre lines can be merged away with their neighbours; a via left off
every line would be dropped by openEMS ("Unused primitive"), so such vias snap to the nearest crossing
whatever the distance, and the log counts them. `auto`: exact for vias on the ports' nets, snap for the
rest (stitching, fences). The moved positions are in `mesh['vias']` and used for the openEMS model.

**Kept lines.** The board outline, port planes (`Port.planes()`) and both ends of every lumped-part
piece are kept exactly.

**Merging** (`_merge`): lines closer than `fine/2` are merged by priority — kept lines, then the
thirds-rule line inside the metal, then the one outside, then the rest. Clusters don't chain and lines
are never averaged, so the inside line of a narrow trace stays inside it.

**Filling** (`smooth_lines`): graded lines between the fixed ones up to the maximum cell, growing by at
most `ratio`, starting from the neighbouring cell sizes (capped at 3 × `fine`).

## z lines

Every interface (dielectric boundaries, copper heights), at least `nz` cells per dielectric (`--nz`),
graded air above and below (no air below when the bottom boundary is PEC on a solid plane). Copper
sheets must sit exactly on lines; `auto_mesh` asserts it.

## Boundaries and air

`boundaries.resolve_bc` picks one boundary per side. `auto`: **PEC directly on the bottom copper** when it
covers ≥ 85 % of the outline (exact for a ground plane, and no cells below it), MUR elsewhere (absorbing,
no extra cells). Otherwise `--bc` gives one value for all sides or six (`xmin,xmax,ymin,ymax,zmin,zmax`),
each `PEC`, `PMC`, `MUR` or `PML_n`; a `PML_n` side gets n extra cells beyond the air margin. A bottom PEC
without a solid bottom plane gets a note: the wall then sits below the board, after the air margin.
`cavity_check` warns when PEC side walls form a box resonating below `fmax` (spurious resonances, slow
decay): use them only when the real board sits in a shield can of that size.

<a id="edge-walls"></a>
**Edge walls** (`--edge-walls xmin,xmax,ymin,ymax|all`, `edges.py`) are PEC sheets standing in for a row
of stitching vias or edge plating along a board side. Without them the plane pairs of a test coupon are
open at the board edge and can resonate. On an SMA edge-launch coupon (30 × 14 mm, inner planes
1.07 mm apart, εr 4.6, the last via row 6.5 mm from the connector edge) the plane pair beyond that row,
shorted at the vias and open at the edge, rang as a quarter-wave resonator: S21 notched to −5.5 dB at
4.7 GHz and the run decayed slowly. With `--edge-walls all` the notch was gone, S11 followed the line
model, and the run reached −30 dB in 14 k steps instead of 96 k. Sides are named in KiCad coordinates
(`ymin` is the top edge as drawn). Each wall stands where the planes end (they are usually pulled back
from the outline, up to 2 mm), and at each point along the side it spans from the lowest to the highest
layer with ground copper at the edge (ground: any modelled net that is not a signal net). It is left
out where fewer than two layers have ground, and within `--edge-keepout` (default 0.5 mm) of signal
copper on any layer, so a launch pad or a trace running off the edge stays open. The preview draws the
walls in green on every layer they pass; the log and the report's mesh table list them per side.

The air margin is `--air` (default 5 mm) on every side except a PEC bottom plane. Antenna mode uses a
quarter wavelength at the band centre, at least 5 mm, and `PML_8` when `--bc` is `auto`.

## Time step

`info['dt_ps']` is the 3D CFL limit for the smallest cells: Δt = 1 / (c · √(1/Δx² + 1/Δy² + 1/Δz²)).
openEMS uses its own method (Rennings) and typically ends up ~10 % smaller, so treat it as an estimate.
Thin layers (prepregs, solder mask) with `nz` cells are often what limits it; `info['min_cell_mm']` shows
which axis.

## Checks and warnings

- fewer than 2 lines across a port trace → `WARNING … use a smaller --fine`
- a lumped part's gap narrower than half the fine cell (its ends would merge and openEMS would drop the
  element) → `WARNING`
- `info['lines_across_ports']` is in the JSON and the report.

## Convergence levels

`converge.mesh_levels` builds one mesh per factor f: `res × f`, `fine / f` (the level-1 fine cell as
the base) and `ceil(nz × f)` cells per dielectric. The finest level gives the result; each coarser
level is compared with it (`converge.compare_levels`: max |Δ|S|| in dB and max phase difference over
points where the finest |S| > −40 dB). Converged means the second-finest level is within
`--converge-tol`. A narrowband circuit whose passband shifts between levels shows tens of dB on its
skirts: the plot shows whether it is a shift (resonators and coupling gaps under-resolved: refine
further) or a level difference.

`--preview --converge 1,1.5,2` lists the cells, time step, memory and relative run time of every level
without running anything. A full run solves the coarser levels first (into `<out>/converge/L<f>/`,
without field dumps), then the finest level, which gives the normal outputs. Each level's largest
deviation goes to the log, `<name>_converge.png`, the JSON summary and the report; a `WARNING` says when
the mesh is not converged. Not available in antenna mode.

## Validation: the transition demo

On the transition demo (microstrip → blind via → stripline, `examples/demo_transition.toml`; P1
excited, 1–8 GHz; delay from the S21 phase slope) the mesh levels compare like this:

| Mesh | Cells | max \|S11\| | Delay |
|---|---|---|---|
| **default** (fine 0.0375 mm, nz 4) | 0.40 M | −24.1 dB | 135.1 ps |
| `--nz 6` (the example config) | 0.52 M | −21.9 dB | 135.1 ps |
| x1.5 (fine 0.025, nz 6, res 30) | 0.71 M | −22.5 dB | 134.6 ps |
| before the change below: default | 0.54 M | −25.2 dB | 138.8 ps |
| before: x1.5 / x2 | 0.86 / 1.42 M | −22.5 / −22.0 dB | 134.7 / 134.7 ps |
| line model (`--fast qucsator`) | – | −18.0 dB | 132.2 ps |

**What was wrong.** The default mesh used to be 4 ps (3 %) long on this board, while boards with only
the microstrip or only the stripline were converged at the same settings. Of everything tried, only
the `fine` cell moved the delay; extra lines did not help (refining the via region, the stripline or
the microstrip with 0.025 mm lines gave 139.5–140.2 ps). Splitting what `fine` controls (`_edge_lines`
offsets, the smoothing start cell, the merge distance) found it: with the thirds-rule offsets of
fine 0.025 and everything else at 0.0375 the delay was 135.1 ps; with the merge distance shrunk
instead, 134.9 ps; the smoothing made no difference. Around the via, the trace end, the round via pad
and the antipad put edges within about 1.5 `fine` of each other; their thirds-rule pairs, a full `fine`
wide, fell within the merge distance (`fine/2`) of each other, and the merge kept one line per
cluster, so the end of the microstrip and the edge of the via pad were placed a cell off. The pair now
spans 2/3 `fine` (`mesh.THIRDS_CELL`): the default mesh is within 0.5 ps of the converged delay, with
fewer cells than before. The same error had put the interdigital filter example's pass band 4.5 % low
(3.118 GHz; now 3.275 GHz, and its x1.5 mesh agrees); the hairpin example did not move.

The return loss still wants more cells through the dielectric (`--nz 6`: −21.9 dB, against −22.0 to
−22.5 dB at x1.5 / x2), so the example config sets `nz = 6`. So: run
`--converge 1,1.5` once on every new kind of structure, and treat return loss below about −20 dB from
an unconverged mesh as uncertain. The transmission-line model is a cheap independent check: here it
sits within 2 % of the converged delay and a few dB above its return loss.

---
[← The 3D model](model.md) · [Index](index.md) · [Next: Transmission-line model →](transmission-lines.md)
