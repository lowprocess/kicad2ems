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
35 µm outer copper, in a 100 Ω class) are 117.8 Ω differential with the zero-thickness formula and 104.9 Ω with
the copper's thickness; with the board's 10 µm solder mask (εr 3.8), 101.3 Ω. Its USB and PCIe pairs (90 Ω
class) are 108.3 Ω with the formula and 95.3 Ω with the copper.

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
beyond the nearest neighbour, and layers of different εr. The line model uses it with
[`--lines-field`](#lines-field).

```python
from kicad2ems.lines import xsection as X
X.microstrip(0.35, 0.2, 4.3, t=0.035, mask=0.02, er_mask=3.8).solve().single()   # (Z0, eeff)
X.coupled_microstrip(0.15, 0.15, 0.2, 4.4).solve().pair()                         # (Ze, Zo, eeff_e, eeff_o)
X.stripline(0.15, 0.2, 0.2, 4.3, s=0.15).solve().single()                         # coplanar ground beside it
r = X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3).solve(); r.C, r.Ca               # F/m: coupled.mtl_s takes them
```

It solves Laplace's equation by finite volumes on a grid graded towards every conductor edge, inside a grounded
box 12 structure heights beyond the copper. The cells at an edge are 1/16 of the smallest feature near the
signal copper, and at least a tenth of their distance from it (`xsection.FAR`): a tensor grid carries each
edge's fine cells across the whole cross-section, and a real board's cut, with pour and the plane between via
clearances on several layers, has 11–33 conductors (the CM5 Minima's worst cuts: 171,000 nodes and 8 s, now
17,700 nodes and 0.35 s, Z0 within 0.07 %). Planes are sheets at the heights given, and conductors are rectangles.
The charge on each conductor is the net flux out of its nodes. The edges' field singularity makes the grid's
error first order in the cell size, so each cross-section is solved on the grid and on the grid with every cell
halved, and the two are extrapolated.

| Against | Case | Difference |
|---|---|---|
| Cohn (exact) | stripline, w 0.15, b 0.4 mm | +0.25 % |
| Cohn (exact) | coupled stripline, w 0.1, s 0.1, b 0.4 mm: Ze, Zo | +0.16 %, +0.34 % |
| Hammerstad & Jensen | microstrip 0.35 / 0.2 mm, 3.0 / 1.6 mm, 0.4 / 0.21 mm with 35 µm | +0.08 to +0.13 % |
| Wheeler | stripline with 35 µm copper | +0.19 % |
| Ghione & Naldi | grounded CPW 0.5 / 0.5 / 1.6 mm, 0.3 / 0.15 / 0.2 mm | +0.32 %, −0.29 % |
| Kirschning & Jansen | coupled microstrip 0.15 / 0.15 / 0.2 mm: Ze, Zo | +0.02 %, +0.59 % |

The offset stripline formula above (two centred striplines combined) comes out 3 % (48.7 against 47.3 Ω) above the solver for a
strip 0.1 mm over one plane and 0.3 mm under the other. The formula is an approximation, and the solver has
no approximation there beyond its grid's.

A cross-section takes 0.02–0.1 s (more with copper thickness, which needs finer cells at the corners, and with
more lines or copper around it: 0.1 s for a pair as a sheet and with its copper, 0.35 s for a real board's cut of
33 conductors).
It's quasi-static: no dispersion and no conductor loss. `xsection.REFINE = False` skips the extrapolation:
5–10 times faster, Z0 about 2 % low.

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
  a whole plane, 54.7 Ω where the plane ends 0.125 mm past its edge, and 67.9 Ω where the plane ends under its
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
| `rf_interdig`: five coupled fingers | −23.0 dB / 51.21 dB | −24.7 dB / 8.48 dB | −24.7 dB / 8.48 dB |
| `rf_stripline`, `rf_diffvia`, `rf_coupler`, `rf_wilkinson`, `demo_transition`, `rf_shielded`, `hairpin` | | within 0.1 dB / 0.06 dB of the formulas | within 0.3 dB / 0.1 dB |
| `rf_branchline`, `rf_steplpf`, `rf_stubnotch`, `rf_gcpw`, `rf_ratrace`, `rf_radialstub`, `sma_launch` | | the same | the same |

A run takes 1.4–8 s either way, and 2–47 s with `--lines-field`.

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
| 0.2 mm line, its plane 0.099 mm under it | 48.2 Ω | 47.5 Ω | 43.2 Ω |
| 0.13 mm line, plane 0.099 mm under it | 61.5 Ω | 60.5 Ω | 53.6 Ω |
| 0.2 mm line, the plane under it full of via clearances: classified over the next plane down, 0.73 mm | 79.4 Ω | 58.3 Ω | 52.3 Ω |
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
times wider than one at half a height, and snapping to a bin's middle moves Z0 by 0.1–0.2 % wherever it is. On the
CM5 Minima's HDMI nets this solves 158 cuts instead of 254, the impedances within 0.33 % (0.11 % rms) of
solving each. Coupled sections gain less (206 solves instead of 228 on the board's 94 nets): their gaps set
the coupling and keep their 1 % steps. Each solve takes 0.05–1 s, so the time still grows with the number of
distinct cross-sections: `rf_radialstub`'s fan, sliced into widening sections, takes 47 s with `--lines-field`;
the CM5 Minima's 94 signal nets take 60 s to build by default (146 grounded-CPW and 206 coupled-section solves),
456 s with `--lines-field` (1,763 cuts), against 10 s with the formulas.

`python tools/xsection_figures.py` draws the solver at work: a pair's grid and its odd- and even-mode fields,
the cases the formulas leave out, the grid's convergence and the extrapolation, the cache's bins, a five-line
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
