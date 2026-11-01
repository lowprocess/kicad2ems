# Transmission-line model

[← mom25d](mom.md) · [Index](index.md) · [Next: Architecture →](architecture.md)

Every run, including previews, breaks the port nets into uniform line sections. Each gets an impedance
from closed-form formulas. This takes milliseconds and needs no openEMS. It's a quick estimate that shows
where a routed line departs from the port impedance. The sections can also be solved as a circuit
([below](#fast)).

## Sections

The model starts from the port nets and follows series parts into the next net, but never into a ground
net.

| Section | What it is |
|---|---|
| trace | tracks of one width on one layer. A trace is split at junctions, and where its surroundings change: the reference planes, the gap to coplanar ground, or extra copper widening it (a taper or teardrop) |
| pad | a component pad. A trace ending in a pad is shortened by the part inside it |
| strip | copper drawn as a shape: rectangular shapes (resonators, hairpins, tapped feeds, stubs drawn as rectangles), and other shapes that sit across a track as a stub (a radial stub). A strip is sliced into sections that follow its width, so a fan becomes a stub that widens step by step; with qucsator, a fan that is a circular sector is one radial stub (MRSTUB) instead |
| via | a via barrel: a coaxial line through the planes it passes, or an inductance where there's no plane |
| part | a lumped R/L/C part, in series or to ground ([details](model.md#lumped-parts)) |

<a id="coupled"></a>
**Coupled sections.** Straight, parallel sections of one cross-section on one layer within 5 substrate
heights of each other become a coupled section: hairpin and interdigital filter fingers, differential
pairs. The [field solver](#field-solver) gives each coupled section its capacitance matrix, every line coupled to
every other, and each neighbouring pair its even- and odd-mode impedances with the copper's thickness (`ze`,
`zo`; as sheets, `ze_sheet`, `zo_sheet`). `topology.FIELD_GROUPS = False` uses the formulas instead: Kirschning
& Jansen for microstrip, Cohn's exact zero-thickness formula for stripline (an offset stripline combines the two
halves as for a single line), zero thickness, and no coupling beyond the nearest neighbour. With qucsator, a
coupled microstrip pair is still qucsator's own MCOUPLED.

Copper thickness matters most here. The CM5 Minima demo board's HDMI pairs (KiCad's `demos/cm5_minima`, the
board of the [KiCad plugin](kicad-plugin.md)'s pictures: 0.13 mm lines 0.25 mm apart, 0.099 mm over In4.Cu,
35 µm outer copper, in a 100 Ω class) are 117.8 Ω differential with the zero-thickness formula and 104.8 Ω with
the copper's thickness; with the board's 10 µm solder mask (εr 3.8), 101.2 Ω. Its USB and PCIe pairs (90 Ω
class) are 108.3 Ω with the formula and 95.2 Ω with the copper.

**Discontinuities** are listed where sections meet: open ends, corners, bends, steps, tees, crosses and
end gaps.

## Impedances

Each section is classified by the copper around it. Only copper of other nets counts as a reference.

| Cross-section | When | Formula |
|---|---|---|
| microstrip | one plane below, nothing above | Hammerstad & Jensen |
| embedded microstrip | dielectric over the strip | microstrip with a cover correction |
| grounded CPW | ground copper beside the strip on both sides | conformal mapping (Ghione & Naldi) |
| stripline | planes on both sides | Wheeler; offset stripline for unequal heights |
| coax | a via inside antipads | the coaxial formula |
| lumped | a via without a plane, or a part | an inductance or the part's value |

A trace is split where its reference planes change: a plane's edge, a split, a slot it crosses. One
exception: where a trace ends at a via, the last stretch over that via's own antipad (up to 3 reference
heights long) keeps the line's cross-section. It is part of the via transition, with the plane's edge as
close all round as the plane under the line, not a line over the next plane down. Giving it the next
plane's impedance (92 Ω instead of 55 Ω on `rf_diffvia`) put the worst Sdd11 at −9.4 dB against openEMS's
−15.4; the rule gives −11.9, and the demo transition's S11 moved from 7.4 dB rms off openEMS to 3.0.

Each section gets two impedances: **Z0** with the stackup's copper thickness (the design value), and
**Z0 sheet** with zero thickness, which is how openEMS and mom25d model copper. For example, the 0.35 mm
microstrip of the demo transition is 50.7 Ω with 35 µm copper and 53.3 Ω as a sheet.

**Accuracy.** The formulas match scikit-rf's reference values within 0.3 %, and the stripline matches
Cohn's exact result within 0.2 %. On the two example filters, the qucsator line model finds the pass band
within 2 % of openEMS in a fraction of a second.

<a id="field-solver"></a>
## Field solver

`kicad2ems.lines.xsection` solves a cross-section numerically: the per-unit-length capacitance matrix of N
conductors over planes, in any stack of dielectrics, with and without them, and from these L, Z0, εeff, the even
and odd modes of a pair and the dielectric loss. It covers what the formulas above can't: coplanar ground under
solder mask or between two planes, mask that follows the copper, coupled lines with copper thickness, coupling
beyond the nearest neighbour, and layers of different εr. The line model uses it [by default](#lines-field) for coupled
sections and grounded CPW, and with [`--lines-field`](#lines-field) for every section.

```python
from kicad2ems.lines import xsection as X
X.microstrip(0.35, 0.2, 4.3, t=0.035, mask=0.02, er_mask=3.8).solve().single()   # (Z0, eeff)
X.coupled_microstrip(0.15, 0.15, 0.2, 4.4).solve().pair()                         # (Ze, Zo, eeff_e, eeff_o)
X.stripline(0.15, 0.2, 0.2, 4.3, s=0.15).solve().single()                         # coplanar ground beside it
r = X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3).solve(); r.C, r.Ca               # F/m: coupled.mtl_s takes them
```

It solves Laplace's equation with bilinear finite elements on a mesh of rectangles, inside a grounded box 24 structure
heights beyond the copper. The mesh takes its methods from [mom25d's](mom.md#coarsen):

- **Cells grow from the corners.** They start on the key lines (copper edges, layers, planes) and are halved in x or z
  only where they are larger than 0.4 × their distance from the nearest corner of copper (`xsection.MESH_GROW`), so a
  corner's fine cells stay around it and long thin cells fill the layers. A tensor grid carries each edge's fine cells
  across the whole cross-section; here halving the cells at a corner costs about 100 nodes.
- **Each corner's own scale.** The cell at a corner is 1/128 of the feature there, the nearest other corner or layer
  (`MESH_FINE`), and at least 1 % of its distance from the signal copper (`MESH_FAR`).
- **Hanging nodes tied, the matrix reduced.** Where a cell meets two smaller ones, the node between them is tied to the
  ends of the longest side it lies on, and the matrix reduced to the nodes left, T^T K T, as mom25d reduces its matrix
  to quads. Every side comes from halving the key lines' intervals, so sides on a line are nested and their nodes
  exactly equal: no tolerances, and a linear potential solves it exactly at machine precision (the patch test,
  `tests/test_xsection.py`).

The charge on each conductor is the net flux out of its nodes. Each cross-section is solved on this mesh and on one with
`MESH_GROW` and `MESH_FINE` doubled, and the two extrapolated, X + 0.5 (X − X_coarse) (`MESH_MIX`): the corners' error is
first order in their cells and wants a weight of 1, the grading's second order in `MESH_GROW` and wants ⅓. One
dielectric throughout (a stripline) needs one factorisation fewer: C_air is C / εr. `MESH = False` uses the tensor grid
the solver began on, kept as the independent check of the mesh and to draw the field on.

| Against | Case | Mesh | Tensor grid | Nodes, mesh / grid |
|---|---|---|---|---|
| Exact (Cohn, with η0 / 4) | stripline, w 0.15, b 0.4 mm | −0.10 % | +0.37 % | 885 / 6,105 |
| Hammerstad & Jensen | microstrip 0.35 / 0.2 mm, 3.0 / 1.6 mm | −0.02 % | +0.23 % | 921 / 14,181 |
| Hammerstad & Jensen | microstrip 0.4 / 0.21 mm with 35 µm | +0.12 % | +0.21 % | 2,140 / 28,427 |
| Wheeler | stripline with 35 µm copper | +0.02 % | +0.24 % | 2,058 / 15,631 |
| Ghione & Naldi | grounded CPW 0.5 / 0.5 / 1.6 mm | −0.18 % | +0.39 % | 1,833 / 19,109 |
| Ghione & Naldi | grounded CPW 0.3 / 0.15 / 0.2 mm | −0.58 % | −0.23 % | 1,848 / 19,065 |
| Kirschning & Jansen | coupled microstrip 0.15 / 0.15 / 0.2 mm: Ze, Zo | −0.15 %, +0.14 % | +0.11 %, +0.65 % | 1,796 / 18,655 |
| Cohn | coupled stripline, w 0.1, s 0.1, b 0.4 mm: Ze, Zo | −0.15 %, −0.31 % | +0.19 %, +0.38 % | 1,341 / 9,405 |

Closed forms are not exact: on the second CPW the mesh is within 0.02 % of a converged solve, and the formula 0.56 %
above it. The usual "30π" of the stripline formulas is itself 0.07 % high (c = 3 × 10⁸), which the coupled stripline's
row includes. As the cells are refined, the mesh converges on the exact stripline to within 0.003 %.

On a real board (`tools/xsection_study.py --board`), every one of the 2,200 cross-sections the CM5 Minima's 94 nets ask
for, up to 75 conductors, against the same one converged:

| | Error: worst | 99th percentile | median | Nodes, up to |
|---|---|---|---|---|
| Mesh | 0.31 % | 0.11 % | 0.04 % | 30,800 |
| Tensor grid | 0.54 % | 0.38 % | 0.22 % | 603,000 |

The solver's time on those nets, on one core, was 6 s by default and 49 s with `--lines-field`; on the grid, with its box
half the size (277,000 nodes at most), 25 s and 222 s.

The error is the largest of each cross-section's Z0, εeff and pairs' modes. A cross-section takes about 20 ms (median; up
to 0.2 s for a real board's largest cuts). It's quasi-static: no dispersion and no conductor loss. `xsection.REFINE =
False` skips the coarser solve: 30 % faster, Z0 up to 0.7 % low. `tools/xsection_study.py` makes these tables.

**Copper thickness on a pair, by full-wave simulation.** Both solvers are Laplace solvers, so the thickness effect was
also checked in 3D FDTD (`tools/xsection_fullwave.py`, openEMS): `rf_diffvia`'s two pairs, odd mode, as sheets and with
35 µm copper. Thickness takes 7.1 % off the microstrip pair's Zdiff and 14.2 % off the stripline pair's; the field
solver gives 8.4 % and 15.7 %. With the copper, FDTD's Zdiff is 91.9 and 83.4 Ω against the solver's 91.6 and 84.2 Ω.

The offset stripline formula above (two centred striplines combined) comes out 3 % (48.7 against 47.1 Ω) above the solver
for a strip 0.1 mm over one plane and 0.3 mm under the other. The formula is an approximation, and the solver has no
approximation there beyond its mesh's.

To see one, pick its section in the GUI: its [Section view](gui.md#section) draws the field on the board's cut, and the
line model's sections each carry what that takes (`LineModel.drawing`, `lines/sections.py`). A cut costs nothing
beside the build, about 1 s for a board's 2,500; only solving it does.

The formulas miss what lies beside a stripline. On the CM5 Minima, `PCIE_PI.nRST` runs 10.7 mm on In2.Cu with ground 0.2 mm
beside it: 71.9 Ω by the offset-stripline formula, 58.8 Ω by its cut. Cropped and solved by openEMS (0.05 mm cells), its
total C is 1.07 pF, against 1.05 pF for the cut and 0.89 pF for the formula (`tools/lines_crop_check.py`).

<a id="lines-field"></a>
### The line model on the field solver

Coupled sections and grounded CPW come from the field solver by default ([above](#coupled), and
[below](#cpw-thickness)). `--lines-field` (`lines_field = true`) takes every section's cross-section from it, from
the board:

- **Every section's cross-section, from the board.** At the point where a section is classified, the board's
  reference copper is cut across the strip, out to 5 reference heights either side, on every layer up to the
  nearest plane that spans the whole cut. The pieces the cut crosses become the solver's conductors, and the
  stackup its dielectrics. A plane that ends beside the trace, a reference under part of it, ground on one side
  or ground beside a stripline are solved as they are. A microstrip 0.35 mm wide on 0.2 mm FR-4 is 53.3 Ω over
  a whole plane, 54.6 Ω where the plane ends 0.125 mm past its edge, and 67.7 Ω where the plane ends under its
  centre. Copper thickness grows away from the plane under the strip, and the stack beyond it moves up by it (the
  dielectrics' thicknesses are between the copper), as the formulas assume. The section's cross-section name
  and its formula Z0 stay in the report, with the field solver's Z0 in its notes.

In the circuit solvers, a microstrip or grounded CPW section keeps scikit-rf's and qucsator's own models (with
their dispersion) unless the field solver's zero-thickness Z0 is more than 2 % from the formula's
(`reference.FIELD_DIFFERS`): then it is a line of the solver's Z0 and εeff (`Element.field`), quasi-static. Used
for every field-solved section, the lost dispersion moved `rf_steplpf`'s |S21| from 2.35 to 2.60 dB rms off
openEMS.

On the examples with openEMS references (scikit-rf; agreement as in [Examples](examples.md): the rms of the
complex difference of S11, and the rms difference of |S21|):

| Board | Formulas throughout | Coupled sections by the field solver (default) | and `--lines-field` |
|---|---|---|---|
| `rf_interdig`: five coupled fingers | −23.0 dB / 51.21 dB | −24.7 dB / 8.47 dB | −24.7 dB / 8.47 dB |
| `rf_stripline`, `rf_diffvia`, `rf_coupler`, `rf_wilkinson`, `demo_transition`, `rf_shielded`, `hairpin` | | within 0.1 dB / 0.06 dB of the formulas | within 0.3 dB / 0.1 dB |
| `rf_branchline`, `rf_steplpf`, `rf_stubnotch`, `rf_gcpw`, `rf_ratrace`, `rf_radialstub`, `sma_launch` | | the same | the same |

`tools/lines_vs_openems.py` makes this comparison, with qucsator too: it gives the same picture (`rf_interdig` 50.91 →
8.94 dB; the others within 0.01 dB of the formulas). Its coupled microstrip pairs stay qucsator's own MCOUPLED, so
the thickness in a pair's reported `ze` and `zo` doesn't reach its S-parameters, and neither reaches scikit-rf's, which
uses the sheets as openEMS does. A run takes seconds in any of these modes (`rf_radialstub`'s fan, its many widening
sections with `--lines-field`: 1.8 s).

The interdigital filter's stop bands were 40–60 dB too deep with the formulas: they are set by the coupling
between fingers that aren't neighbours, which `group_lc` leaves out. With the field solver's matrix cut back to
the nearest neighbours, the 51 dB comes back. On the other boards the sections are the idealised
cross-sections the formulas were made for: the remaining differences there come from what the line model leaves
out (discontinuities with scikit-rf, vias, radiation, the can). `sma_launch`'s grounded CPW with 35 µm copper
is 50.3 Ω against the formula's 54.7 Ω, which has no thickness, but the solvers use the sheet values (54.4 Ω
against 54.7 Ω).

On a real board, the CM5 Minima's eight HDMI pairs (119 mm of microstrip over solid planes), `--lines-field`
moves the single-ended Z0 by at most 2.7 %: 1–2.7 % on 8 % of the length, where a plane under B.Cu doesn't reach
2 heights past the trace or ground copper lies to one side. The differential impedance is what the copper's
thickness moves ([coupled sections](#coupled)).

<a id="cpw-thickness"></a>
Its other nets show what the cut is for. The HDMI side-band lines (DDC SDA and SCL, CEC, HOTPLUG) and the
board's other control lines run as grounded CPW, pour 0.15 mm from the trace, and the field solver differs from
the formulas in two ways:

| Where | Formula | Field solver, as a sheet | with 35 µm copper |
|---|---|---|---|
| 0.2 mm line, its plane 0.099 mm under it | 48.1 Ω | 47.3 Ω | 43.1 Ω |
| 0.13 mm line, plane 0.099 mm under it | 61.5 Ω | 60.3 Ω | 53.6 Ω |
| 0.2 mm line, the plane under it full of via clearances: classified over the next plane down, 0.73 mm | 79.4 Ω | 58.0 Ω | 52.1 Ω |
| 0.15 mm line, the same, 0.65 mm | 104.9 Ω | 60.5 Ω | 55.1 Ω |

- **Copper thickness in grounded CPW**: the formula (Ghione & Naldi) has none, so its "Z0 with the copper's
  thickness" is the sheet value. 35 µm of copper beside a 0.15 mm gap takes 10–12 % off. Gupta's thickness
  correction (wider strip, narrower gaps) finds a third to a half of it (0.2 mm line: 46.8 Ω). So grounded CPW
  sections come from the field solver by default, as classified (strip, gaps, plane: `reference.FIELD_CPW`).
- **A plane with holes**: where the plane under a trace doesn't cover the points `classify` tests, it takes the
  next plane down as the reference, and Z0 comes out 30–75 % too high. The copper left between the holes still
  carries most of the return: the cut solves it as it is (the board-cut figure below).

<a id="similar"></a>
**Similar cross-sections share a solve.** Each cross-section is cached, and a cross-section and its mirror image
are one. Widths and gaps are snapped to steps of 1 % (`xsection.SIMILAR`, `xsection.snap`), and the copper a cut
finds to bins that widen away from the strip (`reference._distance_bins`): ground beside a microstrip moves its Z0
by 12 % at half a reference height, by 0.9 % at 2 and by less than 0.1 % at 4, so a bin 2 heights out is 30
times wider than one at half a height, and snapping to a bin's middle moves Z0 by 0.1–0.2 % wherever it is. No bin is
wider than one height (`reference.BIN_MAX`): the rule is for ground beside a strip, and a plane's edge under a wide strip
counts wherever it is. On the
CM5 Minima's HDMI nets this solves 158 cuts instead of 254, the impedances within 0.33 % (0.11 % rms) of
solving each. Coupled sections gain less (206 solves instead of 228 on the board's 94 nets): their gaps set
the coupling and keep their 1 % steps. The time still grows with the number of distinct cross-sections: the CM5
Minima's 94 signal nets take 13 s to build by default (146 grounded-CPW and 206 coupled-section solves), 71 s with
`--lines-field` (1,848 cuts), against 6.4 s with the formulas (`tools/cm5_line_model.py`, on one core).

`python tools/xsection_figures.py` draws the solver at work: a pair's mesh and its odd- and even-mode fields,
the cases the formulas leave out, the mesh's convergence against the grid's, the cache's bins, a five-line
section's coupling, and (`--board`, the CM5 Minima) a cut across a real board.

## Paths

For each pair of ports the model finds the shortest path through the sections. Branches off the path,
such as stubs and shunt parts, are listed with their position, length and what they end in.

<a id="outputs"></a>
## Outputs

| Where | Contents |
|---|---|
| the log | one line per path: sections, length, impedance range, branches |
| `<name>_tlines.png` | the sections coloured by impedance, and an impedance profile along each path |
| `<name>_lines.json` | every section's geometry, Z0, εeff, length and delay: the GUI's [line-model overlay](gui.md#line-model) |
| the report | a table per path: layer, cross-section, width, length, Z0, εeff, delay. Impedances more than 10 % or 25 % off the port impedance are shaded |
| `<name>_run.json` | the same tables, under `lines` |

<a id="fast"></a>
## Solving the line model

The sections become a circuit, solved in milliseconds by one of two programs:

| | `scikit` | `qucs` |
|---|---|---|
| needs | scikit-rf (`pip install scikit-rf`) | qucsator from Qucs-S, on PATH or `--qucsator PATH` |
| microstrip | `MLine` | `MLIN` |
| grounded CPW | `CPW` | `CLIN` |
| coupled lines | a multiconductor line block | `MCOUPLED` for a microstrip pair, otherwise the same block |
| discontinuities | not modelled | open ends, corners, steps, tees, crosses and gaps |

`--solvers lines` uses qucsator when it's installed, otherwise scikit-rf. `--fast` adds the line model
next to the other solvers as a reference. The copper is zero-thickness, as in the other solvers, and
`--sigma` adds copper loss.

The result is `<name>_qucs.sNp` or `<name>_scikit.sNp`. In the plot and report it is compared with the
first solver: the largest difference in |S|, in dB for transmissions, and in delay. Large differences
point to something one model leaves out, such as coupling, radiation, plane resonances or the lumped
ports, or to a mesh problem.

## Limits

- Impedances are quasi-static. qucsator's microstrip models add dispersion.
- Coupled sections must be straight, parallel microstrip or stripline. Coupling across layers isn't modelled.
- Shapes other than rectangles become strips only as stubs across a track; tapers and teardrops widen the
  track instead.
- **Radial stubs**: with qucsator, a fan that is a circular sector (its point on the track, or cut off at
  the track's edge) is qucsator's radial stub (MRSTUB, March's model with Chew and Kong's effective size).
  Its radial line is taken to start half the track's width beyond the track's edge: at the edge itself,
  the notches came out 17–20 % low. That offset is fitted against mom25d on 60°, 90° and 40° fans on
  0.8 mm FR-4, where the notches then agree within 0 to −2.4 %. scikit-rf has no radial stub: there the
  fan is sliced into widening sections, which ignores its radial field (notches 5–8 % high with qucsator's
  step models).
- qucsator has no stripline model, so single striplines are RLCG lines whose dielectric loss is set at the
  band centre (too high below it, too low above). Coupled sections' loss grows with frequency, as it should.
- No radiation, no plane resonances, no return-path breaks.
- Via transitions: the barrel is a coaxial line and the pads aren't modelled. On `rf_diffvia` the worst Sdd11 is
  −11.9 dB against openEMS's −15.4 (5.2 dB rms): the ripple's peaks fall at other frequencies. Adding a pad
  capacitance or a return-path inductance didn't bring it closer.
- Solder mask counts only with `--mask`.

---
[← mom25d](mom.md) · [Index](index.md) · [Next: Architecture →](architecture.md)
