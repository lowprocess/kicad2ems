# The board model

[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Testing →](testing.md)

`board.Board` turns an imported board into the one board every solver gets. It holds the physical
design only: nothing snapped to a mesh or split for a particular solver. Solvers make those changes
themselves and declare them. Every run saves it as `<name>_board.json`, which mom25d reads and which can
be the input of a later run.

Coordinates are mm, with **y up** (`y = -y_kicad`) and z = 0 at the bottom copper.

## Stack

Each dielectric (and the solder mask with `--mask`) becomes a slab with its height, εr and tan δ. Each
copper layer gets a height. Copper is a **zero-thickness sheet** at that height. Its real thickness is
used only for `--sigma` and for the line model's impedances.

## Copper

On each layer, the copper of the selected nets is merged, clipped to the outline, and simplified to
2 µm. `--ground LAYER` replaces that layer with a solid plane over the whole board.

<a id="region"></a>
## Region

`--region` crops the board to:

- a rectangle: `--region x0,y0,x1,y1` (KiCad mm);
- a polygon: `region = [[x, y], ...]` in a config;
- the shapes on a drawing layer: `--region User.1`. Draw the area to simulate on a user layer in KiCad.

Copper, vias, parts and ports outside it are left out.

**Cut ports.** `--cut-ports` adds a port wherever a signal track crosses the region edge, so the cut line
ends in its port impedance instead of an open end. The cut sides are closed with
[edge walls](meshing.md#edge-walls) that keep 2 mm clear of signal copper. Without them, a plane pair cut
open next to the port radiates. On a test stripline, cropping with cut ports changed |S21| by at most
0.06 dB.

**`--nets auto`** takes the nets under the ports plus the ground nets: nets named like ground, or with
zones covering a fifth of the board.

<a id="extra-ports"></a>
**Ports in the settings.** `--port` adds ports without a footprint:

- `P3=J1.1`: on pad 1 of J1, pointing along the track that leaves it;
- `P4@x,y[,layer[,rotation]]`: at a point on a track (KiCad mm). `P3+` / `P3-` make a differential pair.

## Vias

Each via and plated hole on a selected net becomes a metal cylinder of the **drill** diameter, between the
lowest and highest copper layer it joins.

<a id="ports"></a>
## Ports

Each port is one of two kinds:

- **Vertical** (the default), for a feed line over a reference plane. The footprint must sit on the trace.
  The port runs from the nearest copper below (above for bottom-side ports) up to the trace, across the
  trace's width, just inside its end. It must point along x or y.
- **Gap**, when the footprint sits **between** two conductors on one layer, such as a dipole's feed or a
  feed across a slot. The port spans the gap in the copper plane.

The reference impedance is `port.z0` (50 Ω). Differential legs are two ports, combined into modes
afterwards. Lumped ports aren't de-embedded: on a 0.2 mm dielectric the vertical port adds about 0.1 nH.

<a id="lumped-parts"></a>
## Lumped parts

With `--parts auto` (the default), two-pad footprints become lumped elements.

**Which footprints.** KiCad's simulation fields come first: `Sim.Device` R, L or C with the value from
`Sim.Params` or Value. Otherwise the reference prefix (`R12`, `L3`, `C7`) and the Value are used. A part
is skipped, with a note, if it is DNP or excluded from simulation, its value doesn't parse, it doesn't
have exactly two pads on modelled copper, or its pads aren't in line along x or y.

**Values** like `4p7`, `10k`, `1.2nH`, `0R5` and `100n 50V` are understood. The fields `sim.esr` and
`sim.esl` add series resistance and inductance. 0 Ω is a metal strap.

**Geometry.** The element fills the copper-free gap between the two pads, as wide as the narrower pad. A
track drawn to a pad's centre reaches into the gap with its copper, so the element starts where that copper
ends. Overlapping copper shortened the element in openEMS: a 100 Ω resistor fed from the side measured
67 Ω.

**In openEMS** a series lumped element is unstable without a capacitor in it (see
[maintenance](maintenance.md#series-rlc)). So an inductor is a parallel element with only L, and a part
with ESR is split into a resistor on one half of the gap and the L or L-C on the other. mom25d uses one
series element.

## What isn't modelled

- Ports must point along x or y.
- Copper has no thickness in the field solvers.
- Only two-pad R/L/C parts are modelled. Connectors aren't; a port on the connector pad stands in for one.

---
[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Testing →](testing.md)
