# openEMS: meshing

[← Results](postprocessing.md) · [Index](index.md) · [Next: openEMS simulation →](simulation.md)

openEMS needs a rectilinear mesh: lists of x, y and z lines. `mesh.auto_mesh` builds it. Accuracy depends
on resolving the copper edges and the wavelength. The time step depends on the smallest cell, so every
extra line costs run time.

<a id="cell-sizes"></a>
## Cell sizes

- **Largest cell**: a wavelength at `fmax` divided by `--res` (20), in the dielectric and in air separately.
- **Fine cell** at copper edges, `--fine`. By default it is a quarter of the smaller of the narrowest port
  trace and twice the thinnest dielectric.
- **Growth**: neighbouring cells differ by at most `--ratio` (1.4).

## x and y lines

<a id="thirds-rule"></a>
- **Straight copper edges** get the "thirds rule": a pair of lines across the edge, one third inside the
  metal and two thirds outside. This places the edge correctly for the field singularity there. An edge
  counts as straight if it stays within `fine/10` of an axis.
- **Curves** (arcs, round ends, oblique edges) get lines at their ends and their x/y extremes
  (`--curves extremes`, the default), or at every vertex (`vertices`).
- **Vias** on the port nets get lines at their centre and edges. Other vias, such as stitching and
  fences, move onto the nearest existing line within `--via-snap` (0.2 mm). Every move is logged.
- The board outline, the port planes and the ends of each lumped part are kept exactly.
- Lines closer than `fine/2` are merged, keeping the most important one.
- The gaps are then filled with graded lines up to the largest cell.

## z lines

There's a line at every interface (dielectric boundaries and copper heights), at least `--nz` (4) cells
through each dielectric, and graded air above and below.

<a id="boundaries-and-air"></a>
## Boundaries and air

The default boundary (`--bc auto`) puts a perfect conductor (PEC) directly on the bottom copper when it
covers at least 85 % of the board, and absorbing MUR boundaries elsewhere. You can instead give one value
for all sides or six values (`xmin,xmax,ymin,ymax,zmin,zmax`), each `PEC`, `PMC`, `MUR` or `PML_n`.

PEC side walls form a closed box. kicad2ems warns when that box resonates below `fmax`, since that adds
false resonances. Use them only when the real board sits in a shield can of that size.

There's `--air` (5 mm) of air on every side. Antenna mode uses at least a quarter wavelength, with `PML_8`.

<a id="edge-walls"></a>
## Edge walls

`--edge-walls SIDES` (`xmin,xmax,ymin,ymax` or `all`) puts metal walls where the ground planes end at the
board edge. They stand in for edge plating or a row of stitching vias. Without them, plane pairs are open
at the edge and can resonate.

On the SMA launch example, the planes beyond the last via row rang as a quarter-wave resonator at
4.7 GHz, notching S21 to −5.5 dB. With `--edge-walls all` the notch disappeared and the run needed 14k
timesteps instead of 96k.

A wall spans the layers that have ground copper at the edge. It stops `--edge-keepout` (0.5 mm) short of
signal copper, so a launch pad or a trace running off the edge stays open. The preview draws the walls
in green.

## Time step

The time step is limited by the smallest cell. kicad2ems estimates it from the mesh. openEMS's own value
is usually about 10 % smaller. Thin layers such as prepregs or solder mask are often what limits it.

## Warnings

- Fewer than two mesh lines across a port trace: use a smaller `--fine`.
- A lumped part's gap narrower than half a fine cell: openEMS would drop the part.

<a id="convergence-levels"></a>
## Mesh convergence

`--converge 1,1.5,2` builds a mesh for each factor: the resolution × f, the fine cell ÷ f and the
dielectric cells × f. The finest level gives the result. The coarser ones run first and are compared with
it (the largest change in |S| in dB and in phase, where |S| is above −40 dB). The result counts as
converged when the second-finest level is within `--converge-tol` (0.1 dB, 2°).

`--preview --converge 1,1.5,2` shows the cells, time step and relative run time of each level without
running. Convergence checks aren't available in antenna mode.

A narrowband filter whose pass band shifts between levels shows large dB differences on its skirts. The
plot shows whether it's a shift (refine further) or a level change.

<a id="validation-the-transition-demo"></a>
## Validation: the transition demo

On the via transition demo, the default mesh used to give a delay 4 ps (3 %) too long. Around the via,
the trace end, the via pad and the antipad sit within about 1.5 fine cells of each other. Their
thirds-rule pairs were merged away, so the end of the microstrip landed a cell off. The pairs are now
narrower (2/3 of a fine cell), and the default mesh is within 0.5 ps of the converged delay with fewer
cells.

| Mesh | Cells | Worst S11 | Delay |
|---|---|---|---|
| default | 0.40 M | −24.1 dB | 135.1 ps |
| `--nz 6` (the example's setting) | 0.52 M | −21.9 dB | 135.1 ps |
| 1.5× finer | 0.71 M | −22.5 dB | 134.6 ps |
| line model | – | −18.0 dB | 132.2 ps |

Return loss still wants more cells through the dielectric, which is why the example sets `nz = 6`. Run
`--converge 1,1.5` once on each new kind of structure, and treat return loss below about −20 dB from an
unconverged mesh as uncertain.

---
[← Results](postprocessing.md) · [Index](index.md) · [Next: openEMS simulation →](simulation.md)
