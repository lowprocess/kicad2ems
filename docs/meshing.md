# 6. Meshing

[← The 3D model](model.md) · [Index](index.md) · [Next: Simulation →](simulation.md)

`mesh.auto_mesh(m, fmin, fmax, res, fine, air, ratio, bc, nz_min, curves, vias, via_snap)` builds a
rectilinear mesh (lists of x, y, z lines in mm). FDTD accuracy depends on resolving the copper edges and
the wavelength; the time step depends on the smallest cell, so every line costs run time.

## Cell sizes

- **Maximum cell:** λ / `res` at `fmax`, separately in the dielectric (λ / √εr,max) and in air.
- **Fine cell** `fine` at copper edges. Default: min(port widths, 2 × thinnest dielectric) / 4, clipped
  to [0.01 mm, max dielectric cell / 4].
- **Growth:** neighbouring cells differ by at most `ratio` (1.4).

## x / y lines

<a id="thirds-rule"></a>**Thirds rule.** For each Manhattan copper edge (along x or y, at least `fine` long), a line `fine/3`
inside the metal and one `2·fine/3` outside. This puts the edge between two lines in the right
proportion for the FDTD field singularity at the metal edge. An edge counts as Manhattan if it drifts
by at most `fine/10` across its axis (`mesh.manhattan`): the 2 µm simplification in `Model` can merge a
tiny jog into a long, very slightly oblique edge, and without the tolerance such edges got no
thirds-rule lines (a 0.35 mm feed line ended up with one mesh line across it).

**Curves** (`curve_lines`): for non-Manhattan runs of edges (rounded corners, arcs, round track ends,
circular antipads, oblique edges) `extremes` (default) adds lines only at each run's end points and its
local x / y extremes; `vertices` adds one at every vertex.

**Vias** (`--vias`): `exact` adds lines at the centre and ± radius; `centre` one line per axis; `snap`
no lines of their own — the via moves onto the nearest existing line crossing if within `--via-snap`
(else it gets centre lines). `auto`: exact for vias on the ports' nets, snap for the rest (stitching,
fences). The moved positions are in `mesh['vias']` and used for the openEMS model.

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
covers ≥ 85 % of the outline (exact for a ground plane, and no cells below it), MUR elsewhere. PML sides
get 8 extra cells. `cavity_check` warns when PEC side walls form a box resonating below `fmax`.

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
skirts: the plot shows whether it is a shift or a level difference.

---
[← The 3D model](model.md) · [Index](index.md) · [Next: Simulation →](simulation.md)
