# 5. The 3D model

[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Meshing →](meshing.md)

`model.Model(board, nets, pos_file, include_mask, region, ground, parts)` turns a `KiCadBoard` into the
geometry openEMS needs. Coordinates: mm, **y up** (`y = -y_kicad`), z = 0 at the bottom copper.

## Stack

The stackup is walked from the bottom up. Each dielectric (and solder mask with `--mask`) becomes a slab
`(z0, z1, er, tand)` in `Model.layers`; each copper layer gets a height in `Model.cu_z`. Copper is a
**zero-thickness sheet** at that height (its thickness `cu_t` is only used for `--sigma`). Heights are
rounded to 1e-6 mm so sheets land exactly on z mesh lines.

## Copper

Per copper layer, the selected nets' items are merged (`unary_union`), flipped to y up, clipped to the
outline and **simplified with a 2 µm tolerance** (this keeps polygon counts down; see the
near-Manhattan note in [Meshing](meshing.md#thirds-rule)). Polygons are oriented counter-clockwise
(metal on the left of every edge), which the mesher relies on. `--ground LAYER` replaces that layer
with the whole outline. CSXCAD can't take polygons with holes; `geometry.split_holes` cuts them into
hole-free pieces when the openEMS model is built.

## Vias

Each via (and plated hole) on a selected net becomes a metal cylinder of the **drill** diameter between
the lowest and highest copper layer it joins, if its centre is inside the outline. `Model.via_signal`
marks vias on the ports' nets; the mesher treats those more carefully.

## Ports

Each port footprint becomes a lumped port (`model.Port`). Two kinds:

**Vertical (`kind='z'`)** — the default, for a feed line over a reference plane. The footprint must
sit on copper of the port layer (`port.layer`, default F.Cu / B.Cu by side). The port sheet lies across
the trace, just inside its end (`min(0.25 w, 0.1 mm)` in from the footprint position along the port
direction), centred on the trace, as wide as the trace there. It runs from the nearest copper **below**
(top-side ports; above for bottom-side ports, with the other side as fallback) up to the trace.
Rotation must be a multiple of 90° (the port must be along x or y).

**Gap (`kind='gap'`)** — when the footprint sits **between** two conductors on the same layer (not on
copper), e.g. the feed gap of a dipole: copper is searched within 5 mm on both sides along the port
direction. The port sheet lies in the copper plane, from one copper edge to the other, as wide as the
narrower conductor. `port.type = z` forces a vertical port (and its error message) instead.

`Port.box()` gives the sheet's corners, `Port.direction` the openEMS excitation direction (`z`, or
`x` / `y` for gap ports), `Port.planes()` the coordinates that must be mesh lines. Reference impedance:
`port.z0` (default 50 Ω). Differential legs (`P1+` / `P1-`) are two single-ended ports; they are
combined in post-processing.

## Lumped parts

`parts.find_parts(board, model)` (with `--parts auto`, the default) turns two-pad footprints into
lumped elements.

**Which footprints.** KiCad's simulation fields first: `Sim.Device` R / L / C with the value from
`Sim.Params` (`c=1.5p`) or Value. Otherwise the reference prefix (`R12`, `L3`, `C7`) and the Value.
Skipped, with a note in `Model.notes`: DNP / `exclude_from_sim` / `Sim.Enable = 0`, port footprints,
values that don't parse (`tbd`), not exactly two copper pads, pads not on modelled copper (their nets
must be in `--nets`), pads not in line along x or y, C = 0 (open).

**Values.** `parts.parse_value` reads `4p7`, `10k`, `1.2nH`, `0R5`, `100n 50V`, `2.2 nH`, `1,5p`, … (SI
prefixes, R/k/M notation, optional unit). Fields `sim.esr` / `sim.esl` add a series resistance /
inductance.

**Geometry.** A sheet in the copper plane from the inner edge of one pad to the inner edge of the other
(the gap), as wide as the narrower pad, at the pads' copper height (`Part.s`, `Part.e`).

**openEMS elements** (`Part.pieces()`): openEMS's series-topology lumped element is unstable without a
capacitor in the branch (see [maintenance](maintenance.md#series-rlc)), so:

| Part | openEMS elements |
|---|---|
| R, C, or 0 Ω / 0 H (metal strap) | one element (parallel topology = plain R or C) / a metal box |
| L | one element, **parallel** topology with only L (a plain inductor) |
| L + C (ESL) | one **series** L-C element |
| R in series with L and/or C (ESR) | two pieces along the gap: a resistive sheet on the first half, the L / L-C on the second |

`Part.series` says whether the part is a series combination (for display); the topology handed to
openEMS is the third item of each piece.

## Pickling

`Model.__getstate__` drops the parsed board and the log callable, so a model pickles small for the
`--jobs` worker processes.

---
[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Meshing →](meshing.md)
