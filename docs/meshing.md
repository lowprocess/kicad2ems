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
covers ≥ 85 % of the outline (exact for a ground plane, and no cells below it), MUR elsewhere (absorbing,
no extra cells). Otherwise `--bc` gives one value for all sides or six (`xmin,xmax,ymin,ymax,zmin,zmax`),
each `PEC`, `PMC`, `MUR` or `PML_n`; a `PML_n` side gets n extra cells beyond the air margin. A bottom PEC
without a solid bottom plane gets a note: the wall then sits below the board, after the air margin.
`cavity_check` warns when PEC side walls form a box resonating below `fmax` (spurious resonances, slow
decay): use them only when the real board sits in a shield can of that size.

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

The default mesh is not always converged. On the transition demo (microstrip → blind via →
stripline, `examples/demo_transition.toml`) the levels compare like this (P1 excited, 1–8 GHz; delay
from the S21 phase slope; differences against the x2 mesh):

| Mesh | Cells | max \|S11\| | Delay | max \|ΔS11\| | max \|ΔS21\| |
|---|---|---|---|---|---|
| default (fine 0.0375 mm, nz 4) | 0.54 M | −25.2 dB | 138.8 ps | 0.046 | 0.197 |
| `--nz 6` | 0.69 M | −23.5 dB | 138.9 ps | 0.036 | 0.200 |
| `--res 30` | 0.53 M | −25.5 dB | 138.8 ps | 0.046 | 0.195 |
| `--vias exact` | 0.63 M | −25.1 dB | 138.8 ps | 0.046 | 0.196 |
| `--fine 0.025` | 0.68 M | −24.7 dB | 134.8 ps | 0.022 | 0.005 |
| x1.5 (fine 0.025, nz 6, res 30) | 0.86 M | −22.5 dB | 134.7 ps | 0.004 | 0.000 |
| x2 (fine 0.019, nz 8, res 40) | 1.42 M | −22.0 dB | 134.7 ps | – | – |
| line model (`--fast qucsator`) | – | −18.0 dB | 132.2 ps | 0.089 | 0.124 |

- The fine cell sets the delay and the dielectric cells (`--nz`) the return loss; the cells per
  wavelength (`--res`) and via snapping do not matter here. The example config therefore sets
  `fine = 0.025` and `nz = 6`.
- The error is not monotonic in `--fine`: an older mesh with fine 0.1 mm gave 135.5 ps. Single-line
  boards cut from the same stackup converge at the default mesh (microstrip: 122.6 / 122.7 / 122.8 ps
  at fine 0.0875 / 0.0375 / 0.025 mm; stripline: 141.4 ps at x1 and x1.5), so the sensitivity sits
  in the via region. Its cause is not found yet.
- |S21| agrees within 0.02 dB on every mesh; only the phase and the small reflections move. The
  line model's |S21| matches too (−0.33 vs −0.34 dB on average); its 2.5 ps shorter delay is most of
  its vector difference.

So: run `--converge 1,1.5` once on every new kind of structure, and treat return loss below about
−20 dB from an unconverged mesh as uncertain. The transmission-line model (`--fast`, see
[the transmission-line model](transmission-lines.md)) is a cheap independent check: here it sits
within 2 % of the converged delay and a few dB above its return loss.

---
[← The 3D model](model.md) · [Index](index.md) · [Next: Transmission-line model →](transmission-lines.md)
