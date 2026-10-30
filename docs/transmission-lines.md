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
pairs. Each neighbouring pair gets even- and odd-mode impedances: Kirschning & Jansen for microstrip,
Cohn's exact zero-thickness formula for stripline (an offset stripline combines the two halves as for a
single line). Lines further apart than the nearest neighbour aren't coupled.

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
beyond the nearest neighbour, and layers of different εr. The line model doesn't use it yet.

```python
from kicad2ems.lines import xsection as X
X.microstrip(0.35, 0.2, 4.3, t=0.035, mask=0.02, er_mask=3.8).solve().single()   # (Z0, eeff)
X.coupled_microstrip(0.15, 0.15, 0.2, 4.4).solve().pair()                         # (Ze, Zo, eeff_e, eeff_o)
X.stripline(0.15, 0.2, 0.2, 4.3, s=0.15).solve().single()                         # coplanar ground beside it
r = X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3).solve(); r.C, r.Ca               # F/m: coupled.mtl_s takes them
```

It solves Laplace's equation by finite volumes on a grid graded towards every conductor edge, inside a grounded
box 25 structure heights beyond the copper. Planes are sheets at the heights given, and conductors are rectangles.
The charge on each conductor is the net flux out of its nodes. The edges' field singularity makes the grid's
error first order in the cell size, so each cross-section is solved on the grid and on the grid with every cell
halved, and the two are extrapolated.

| Against | Case | Difference |
|---|---|---|
| Cohn (exact) | stripline, w 0.15, b 0.4 mm | +0.15 % |
| Cohn (exact) | coupled stripline, w 0.1, s 0.1, b 0.4 mm: Ze, Zo | +0.1 %, +0.2 % |
| Hammerstad & Jensen | microstrip 0.35 / 0.2 mm, 3.0 / 1.6 mm, 0.4 / 0.21 mm with 35 µm | +0.1 to +0.15 % |
| Wheeler | stripline with 35 µm copper | +0.1 % |
| Ghione & Naldi | grounded CPW 0.5 / 0.5 / 1.6 mm, 0.3 / 0.15 / 0.2 mm | +0.2 %, −0.4 % |
| Kirschning & Jansen | coupled microstrip 0.15 / 0.15 / 0.2 mm: Ze, Zo | +0.0 %, +0.5 % |

The offset stripline formula above (two centred striplines combined) comes out 3 % above the solver for a
strip 0.1 mm over one plane and 0.3 mm under the other. The formula is an approximation, and the solver has
no approximation there beyond its grid's.

A cross-section takes 0.1–0.5 s (about 1 s with copper thickness, which needs finer cells at the corners).
It's quasi-static: no dispersion and no conductor loss. `xsection.REFINE = False` skips the extrapolation:
5–10 times faster, Z0 about 2 % low.

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
