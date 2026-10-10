# 7. Transmission-line model

[← Meshing](meshing.md) · [Index](index.md) · [Next: Simulation →](simulation.md)

`tlines.py` breaks the ports' nets into uniform sections and gives each one a cross-section and a
quasi-static impedance from closed-form formulas. It runs in every run, also with `--preview`, takes
milliseconds and needs no openEMS. It is a ballpark next to the FDTD result: it shows where a routed line
departs from the port impedance. With `--fast` the sections are also solved as a circuit.

## Sections

`tlines.build(model)` starts from the nets the ports sit on and follows series parts into the next net,
but never into a ground net: one named like ground (GND, AGND, VSS, ...) or owning zones that cover at
least a fifth of the board on some layer (`tlines.ground_nets`). Traces are clipped to the board outline,
so with `--region` the sections stop at the crop.

| Section | What it is |
|---|---|
| trace | KiCad tracks of one width on one layer. Split at T-junctions (where another track ends on it) and, sampling every 0.25 mm, where the reference planes above or below change (over an antipad, past a plane edge), where the coplanar gap changes by a factor 1.5 (a pour that opens out), and where copper of the trace's own net drawn as a shape or zone widens it by a factor 1.2 (a taper, a teardrop, copper drawn in a footprint): such a piece gets the full copper width across the trace |
| pad | a component pad at its own width. A trace that ends in a pad is shortened by the part inside the pad: KiCad's track lengths hide this discontinuity. Touching pads with the same number and net are one pad (a connector's signal pad drawn as an oval plus a rectangle). A port on a pad ends the pad section: it runs from where the trace meets the pad to the port |
| via | a via barrel between the layers that tracks attach to: a coaxial line from the drill to the antipad in the planes it passes, or a lumped inductance (Goldfarb & Pucel) when no plane surrounds it. Barrel beyond the used layers is an open stub |
| part | a lumped R / L / C part ([lumped parts](model.md#lumped-parts)). A series part continues the path into the next net; a part whose other pad is on a ground net, or a net outside the line model, is a shunt to ground |

The path follows routed tracks, pads, vias and parts. Copper drawn as shapes or zones only widens the
tracks it overlaps; a shape that no track runs through is not a section. Gap ports (dipoles) have no
line model. The legs of a differential pair are single-ended lines: the
coupling between them (odd / even mode impedance) is not modelled, and a note says so.

## Cross-sections and impedances

Each trace and pad section is classified at its middle. Only copper of other nets can be a reference
(`tlines.RefCopper`): a trace never references its own net (a via pad, its own pour) or the other leg of
its pair. The nearest such copper below and above counts as a plane when it covers the strip and
w/2 + 2h on either side; partial cover gives a note. A `ground` layer is a plane everywhere.

| Cross-section | When | Z0 and εeff |
|---|---|---|
| microstrip | one plane, nothing over the strip | Hammerstad & Jensen (1980), with their strip-thickness correction |
| embedded microstrip | one plane, dielectric over the strip (an inner layer, or solder mask with `--mask`) | microstrip with the Bahl / Wadell cover correction of εeff |
| grounded CPW | microstrip without cover, other copper on the same layer within 2h on both sides | conformal mapping (Ghione & Naldi), zero thickness |
| stripline | planes on both sides, heights within 5 % | Wheeler (1978), thickness included; εeff = εr |
| offset stripline | planes on both sides, unequal heights | 2 Z1 Z2 / (Z1 + Z2), Zi the centred stripline of spacing 2 hi + t |
| coax | via barrel inside antipads | η0 / (2π √εr) · ln(D / d); D is twice the median antipad radius |
| lumped | via without a surrounding plane, or a part | inductance / part value, no Z0 |

εr is the thickness-weighted mean of the dielectrics between the strip and its planes; tan δ the largest
of them. Ground copper within 2h on one side only keeps the microstrip, with the note "Z0 somewhat
lower". A strip with no copper above or below gets no Z0.

**Two impedances per section.** *Z0* uses the stackup's copper thickness: the design value. *Z0 sheet*
uses zero thickness, which is how openEMS models copper. Example: the 0.35 mm microstrip of the demo
transition on 0.2 mm FR-4 (εr 4.3) has Z0 50.7 Ω with 35 µm copper and 53.3 Ω as a sheet.

**Accuracy.** In the tests the formulas agree with scikit-rf's reference values within 0.2 %
(microstrip) and 0.3 % (grounded CPW), and the stripline with Cohn's exact zero-thickness result within
0.2 %. Against converged openEMS runs: a 20 mm microstrip line matches in delay (120.4 vs 122.6 ps) and
peak return loss (−18.9 vs −18.4 dB); the transition demo within 2 % in delay and 0.02 dB in |S21|,
with return loss a few dB too high ([validation](meshing.md#validation-the-transition-demo)).

## Paths

For each pair of ports, the shortest path (by physical length) through the sections. A port that
reaches no other port gets the path to the farthest point it reaches, with a note. Branches that leave
the path (stubs, shunt parts) are listed under it with their position, length, first Z0 and what they
end in. Trace overhangs shorter than 0.25 mm behind a port or past a pad are not listed.

## Outputs

| Where | Contents |
|---|---|
| log | one line per path: sections, length, Z0 range, branches |
| `<name>_tlines.png` | map of the sections coloured by Z0 and numbered along each path; an impedance profile per path (Z0 against the distance from the first port) with arrows at the branches |
| report, *Transmission-line sections* | one table per path: section, layer, cross-section, width, length, Z0, Z0 sheet, εeff, delay, start position, notes. Z0 cells are shaded when more than 10 % / 25 % from the port impedance; branches are listed under the path |
| `<name>_openems.json`, `lines` | the same tables ([JSON summary](postprocessing.md#json-summary)) |

<a id="fast"></a>
## Fast S-parameters (`--fast`)

`fastsolve.py` turns the sections into a circuit: lines, via barrels, lumped parts, ports at the port
nodes, open ends left open. It solves in milliseconds.

| | `--fast skrf` | `--fast qucsator` |
|---|---|---|
| needs | scikit-rf (`pip install scikit-rf`, or the `fast` extra) | `qucsator_rf` (Qucs-S) or `qucsator` on PATH, or `--qucsator PATH` |
| runs | in-process | as a separate program (netlist in, dataset out) |
| microstrip | `MLine` (Hammerstad-Jensen, Kirschning-Jansen dispersion) | `MLIN` |
| grounded CPW | `CPW` with metal backside | `CLIN` (metal backside) |
| other sections | `DefinedAEpTandZ0`: a line of the section's Z0 and εeff | `RLCG` line of the section's Z0 and εeff, dielectric loss matched at the band centre |
| vias without plane, parts | series impedances | series R-L-C chains |

The copper is zero-thickness (Z0 sheet), as in openEMS, so the two compare like with like. Copper loss
comes from `--sigma` (else lossless). A failing fast solve (package missing, qucsator error) is a
warning, not an error.

Outputs:

- `<name>_fast.sNp` (mixed-mode for differential ports, like the openEMS result);
- with `--preview`: `<name>_fast.png`;
- after an openEMS run: the fast result drawn dotted in `<name>_openems.png` (for the S-parameters
  openEMS computed), and a report section *Fast line model* with, per S-parameter, the openEMS and
  line-model maxima (dB) and the largest vector difference |ΔS|; for transmissions also the largest
  |Δ| in dB (where openEMS is above −40 dB) and the delay difference (line model − openEMS, from the
  phase slope). A reflection's dB difference is left out: it mostly says where the nulls fall.

Large differences point at something one of the two models leaves out (coupling, radiation, plane
resonances, the lumped ports) or at a mesh problem.

## Limits

- Quasi-static impedances (with `--fast`, the solvers' own microstrip models add dispersion).
- No coupling between sections: coupled-line filters (hairpin, or the interdigital
  [`filter` example](examples.md#the-microstrip-filter)) are out of scope.
- No radiation, no plane resonances, no return-path breaks (a gap in a plane only changes the
  cross-section above it).
- Solder mask only when it is modelled (`--mask`).

---
[← Meshing](meshing.md) · [Index](index.md) · [Next: Simulation →](simulation.md)
