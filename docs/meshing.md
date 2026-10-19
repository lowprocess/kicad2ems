# openEMS: meshing

[← Results](postprocessing.md) · [Index](index.md) · [Next: openEMS simulation →](simulation.md)

openEMS needs a rectilinear mesh: lists of x, y and z lines. `mesh.auto_mesh` builds it. Accuracy depends
on resolving the copper edges and the wavelength. The time step depends on the smallest cell, so every
extra line costs run time.

<a id="cell-sizes"></a>
## Cell sizes

- **Largest cell**: a wavelength at `fmax` divided by `--res` (20), in the dielectric and in air separately.
- **Fine cell** at copper edges, `--fine`. By default it is a quarter of the smallest of: the narrowest port
  trace, twice the thinnest dielectric, and the narrowest gap next to the port copper (coupled lines, a
  coplanar slot). Narrow gaps can still need finer meshes: check with `--converge`.
- **Growth**: neighbouring cells differ by at most `--ratio` (1.4).

## x and y lines

<a id="thirds-rule"></a>
- **Straight copper edges** get the "thirds rule": a pair of lines across the edge, one third inside the
  metal and two thirds outside. This places the edge correctly for the field singularity there. An edge
  counts as straight if it stays within `fine/10` of an axis.
- **Curves** (arcs, round ends, oblique edges) get lines at their ends and their x/y extremes, plus one
  wherever the curve has run on by the copper's own width (`--curves extremes`, the default), or a line at
  every vertex (`vertices`). The width rule keeps a thin curved track connected: without it a rat-race
  ring broke apart on the coarse mesh.
- **Over curved copper** (arcs, rings and fans longer than twice the copper's width, not round track ends)
  the largest cell is the dielectric's divided by `--curve-res` (6). A radial stub's fan had sat on 1.8 mm
  cells, and its notch came out at 2.03 GHz instead of 2.13.
- **Vias** on the port nets get lines at their centre and edges. Other vias, such as stitching and
  fences, move onto the nearest existing line within `--via-snap` (0.2 mm). Every move is logged.
- The board outline, the port planes and the ends of each lumped part are kept exactly.
- Lines closer than `fine/2` are merged, keeping the most important one.
- The gaps are then filled with graded lines up to the largest cell.

<a id="signal-zone"></a>
### The signal zone

Every mesh line runs across the whole model, so on a real board the ground planes' pours, antipads and
pads would set the mesh everywhere, not just near the signal. `--mesh-zone on` (the default) gives ground
copper's edges lines only within a zone around the signal copper (every net but the ground nets, on any
layer, and the signal vias). The zone reaches 1 mm or 3 times the thickest dielectric, whichever is more;
`--mesh-zone 2.5` sets the reach in mm and `--mesh-zone off` meshes every copper edge. Outside the zone
the planes are left to the graded mesh. A small ground ring (a pad, an antipad or a via, up to 2 mm across)
that touches the zone keeps all its lines: the return vias next to a signal via matter, and on
`rf_diffvia` cutting half of their lines moved the common mode by 0.14. Antenna mode never uses the zone,
since there the ground copper is often the radiator.

| Board | Cells, zone off → on | Largest change in S |
|---|---|---|
| KiCad's CM5 demo, HDMI D0 pair, default fine cell | 75 M → 17 M | not runnable before |
| the same, `--fine 0.1` | 9.5 M → 3.6 M (142 s → 47 s) | 0.024 |
| `sma_launch` | 1.14 M → 0.82 M | 0.0001 |
| `filter` | 0.40 M → 0.34 M | 0.0033 |
| `demo_transition` | 0.53 M → 0.51 M | 0.0003 |

The other examples lie within their zones and mesh as before.

<a id="z-lines"></a>
## z lines

There's a line at every interface (dielectric boundaries and copper heights), at least `--nz` (4) cells
through each dielectric, and graded air above and below.

Next to a copper layer with signal edges (a port's layer, or any layer that isn't a plane), the first cell
on either side is `--z-sheet` (1) × the fine cell, growing by `--ratio` into the dielectric and the air.
The field at a strip's edge needs resolving vertically as much as sideways. On 0.8 mm FR-4, uniform cells
gave a 10 dB coupler's coupling and a grounded CPW's match far off, and refining the edge cells in x and y
didn't help:

| Mesh | Coupler S31 at 2.4 GHz | Grounded CPW S11 at 5 GHz |
|---|---|---|
| uniform, `--nz 4` | −8.6 dB, 0.32 M cells | −19.3 dB, 0.09 M cells |
| uniform, fine cell ÷ 3 (÷ 2 for the CPW) | −8.4 dB, 0.75 M cells | −18.1 dB, 0.18 M cells |
| uniform, `--nz 36` (32 for the CPW) | −10.5 dB, 1.21 M cells | −33.1 dB, 0.34 M cells |
| graded from the fine cell (the default) | −10.4 dB, 0.57 M cells | −31.5 dB, 0.14 M cells |

The graded mesh took 1.3× the uniform one's run time. `--z-sheet 0.5` gets closer (−10.5 dB, −32.6 dB)
for about twice the time, and `--z-sheet 0` turns grading off. Grading only starts where the fine cell
is smaller than the uniform cell, so wide lines on thin dielectrics are unchanged.

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
