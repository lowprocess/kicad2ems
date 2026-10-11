# 5. The 3D model

[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Meshing →](meshing.md)

`model.Model(board, nets, pos_file, include_mask, region, ground, parts)` turns a `KiCadBoard` into the
geometry openEMS needs. Coordinates: mm, **y up** (`y = -y_kicad`), z = 0 at the bottom copper.

## Stack

The stackup is walked from the bottom up. Each dielectric (and solder mask with `--mask`) becomes a slab
`(z0, z1, er, tand)` in `Model.layers`; each copper layer gets a height in `Model.cu_z`. Copper is a
**zero-thickness sheet** at that height (its thickness `cu_t` is only used for `--sigma` and for the
line model's Z0). Heights are rounded to 1e-6 mm so sheets land exactly on z mesh lines.

## Copper

Per copper layer, the selected nets' items are merged (`unary_union`), flipped to y up, clipped to the
outline and **simplified with a 2 µm tolerance** (this keeps polygon counts down; see the
near-Manhattan note in [Meshing](meshing.md#thirds-rule)). Only items whose bounding box meets the
outline are merged, so a small region of a large board stays fast. Polygons are oriented
counter-clockwise (metal on the left of every edge), which the mesher relies on. `--ground LAYER`
replaces that layer with the whole outline. CSXCAD can't take polygons with holes;
`geometry.split_holes` cuts them into hole-free pieces when the openEMS model is built.

<a id="region"></a>
## Region

`region` crops the board (`region.region_polygon`): the outline becomes its intersection with

- a rectangle, `--region x0,y0,x1,y1` (KiCad mm);
- a polygon, `region = [[x, y], ...]` in a config;
- the shapes drawn on a drawing layer, `--region User.1` (any non-copper layer except Edge.Cuts): draw
  the area to simulate in KiCad on a user layer; the largest closed face of its shapes is the region.

Copper, vias and parts outside are left out. Port footprints outside the region are left out with a
note; it is an error when no port remains, or when the region misses the board. Parts outside are
skipped silently.

**Cut ports** (`--cut-ports`): wherever a track of a signal net crosses the region edge, a port is
placed just inside the edge, pointing into the region, so the cut line ends in the port impedance
instead of an open end (`region.cut_port_fps`; numbered after the other ports, source `cut (x, y)`).
Signal nets are the selected nets except the ground nets (or the nets under the ports when every net is
selected). A track crossing the edge at an angle gets no port (lumped ports run along x or y), with a
note. The sides where the region cuts the board (`Model.cut_sides`) are closed with
[edge walls](meshing.md#edge-walls) that keep 2 mm clear of the signal copper
(`board.CUT_KEEPOUT`), unless `--edge-walls` names sides itself: a cut through a plane pair leaves it
open right at the port, and the open edge radiates (on the stripline below: 0.22 dB off without the
walls). An absorbing boundary on the cut face was tried and is worse (the port sits against it).

**Nets = auto** (`--nets auto`): the nets under the ports plus the ground nets (named like ground, or
owning zones over a fifth of the board).

<a id="extra-ports"></a>
**Ports in the settings** (`--port SPEC`, repeatable; `ports = [...]` in a config), next to the port
footprints:

- `P3=J1.1` — on pad 1 of J1 (pads sharing the number: the first): at the pad centre, across the pad,
  pointing along the track that leaves it (else along the pad's longer side);
- `P4@x,y[,layer[,rotation]]` — at a point (KiCad mm) on a track of `layer` (default F.Cu), pointing
  toward the track's far end unless a rotation (degrees, the direction into the circuit) is given.
  `P3+` / `P3-` make a differential pair.

**Validation.** A 10 mm microstrip (F.Cu over In1) and stripline (In2 between In1 and B.Cu) on the
transition demo's stackup, 24 × 10 mm board with stitched planes: the line running on to x = 22 and
cropped at x = 12 with a cut port, against the uncropped board whose line ends at x = 12 with a port
footprint there (1–8 GHz, default mesh):

| Line | max \|ΔS21\| | max ∠ΔS21 | max \|ΔS11\| |
|---|---|---|---|
| microstrip | 0.020 dB | 1.8° | 0.033 |
| stripline | 0.056 dB | 2.0° | 0.022 |
| stripline, cut left open (no walls) | 0.22 dB | 0.9° | 0.059 |

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
(top-side ports; above for bottom-side ports, with the other side as fallback, e.g. striplines) up to
the trace. Rotation must be a multiple of 90° (the port must be along x or y).

**Gap (`kind='gap'`)** — when the footprint sits **between** two conductors on the same layer (not on
copper), e.g. the feed gap of a dipole, a loop or a feed across a slot: copper is searched within 5 mm on
both sides along the port direction. The port sheet lies in the copper plane, from one copper edge to
the other, as wide as the narrower conductor. The footprint field `port.type = z` (or `mom.type`)
forces a vertical port where a gap port would otherwise be detected (and its error message when there
is no copper under it).

`Port.box()` gives the sheet's corners, `Port.direction` the openEMS excitation direction (`z`, or
`x` / `y` for gap ports), `Port.planes()` the coordinates that must be mesh lines. Reference impedance:
`port.z0` (default 50 Ω). Differential legs (`P1+` / `P1-`) are two single-ended ports; they are
combined in post-processing.

Lumped ports are not de-embedded: the vertical port current adds a small series inductance (about
0.1 nH on 0.2 mm dielectric), noticeable only on thick substrates at high frequency.

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
inductance. 0 Ω (or 0 H) is a metal strap.

**Geometry.** A sheet in the copper plane from the inner edge of one pad to the inner edge of the other
(the gap), as wide as the narrower pad, at the pads' copper height (`Part.s`, `Part.e`). Both ends are
kept as mesh lines.

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

## What is not modelled

- Lumped ports only (no de-embedded waveguide or microstrip ports); ports along x or y.
- Copper is zero-thickness (`--sigma` makes it a conducting sheet, still zero-thickness in the mesh).
- Of the components, only two-pad R / L / C parts (ideal, plus optional ESR / ESL fields). Connectors
  are not modelled; a lumped port on the connector pad stands in for one.

---
[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Meshing →](meshing.md)
