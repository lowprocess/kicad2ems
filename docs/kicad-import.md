# KiCad import

[← APIs](api.md) · [Index](index.md) · [Next: The board model →](model.md)

`importers/kicad/` reads a `.kicad_pcb` (KiCad 5 to 10) into plain data, in KiCad coordinates (mm, y down).
It makes no modelling decisions; `Board` does that ([next page](model.md)). Boards can also come from
Gerber files ([below](#gerber)). Both importers produce the same raw form, so every solver sees the same
board whichever file it came from.

## Stackup

The stackup comes from *Board Setup → Physical Stackup*: copper thicknesses, and for each dielectric its
thickness, εr and loss tangent. Dielectric sub-layers become separate layers. Missing values default to
0.1 mm, εr 4.5 and tan δ 0.02. A board without a stackup can't be modelled.

## Copper

| KiCad item | Read as |
|---|---|
| tracks and arcs | the track's shape, plus the track itself for the line model |
| vias | a pad on each layer it connects, and a via |
| zones | the **filled** copper as KiCad last filled it. Refill zones before simulating |
| graphics on copper layers | lines, arcs, curves, rectangles, circles and polygons |
| footprint pads and graphics | placed and rotated; graphics take the net of the pad they touch |

All pad shapes are supported, including custom pads. Plated through-holes become vias. Zones defined
inside footprints aren't read.

## Board outline

The outline is built from every Edge.Cuts item, on the board and inside footprints. The largest closed
shape is the board. Cutouts inside it aren't removed from the dielectric. A board without an outline
uses the copper's bounding box plus 1 mm.

<a id="ports"></a>
## Ports

A port is a footprint with a Fab-layer text `P<n>`. `P<n>+` / `P<n>-` (or `P<n>P` / `P<n>N`) make a
differential pair. Ports can instead be listed in a pick-and-place file, by Value or Reference.

The footprint's position is the port, and its rotation points into the circuit. Optional footprint
fields:

| Field | Default | Meaning |
|---|---|---|
| `port.layer` | F.Cu or B.Cu, by footprint side | the copper layer of the port, e.g. `In2.Cu` for a stripline |
| `port.z0` | 50 | the reference impedance, Ω |
| `port.type` | | `z` forces a vertical port where a gap port would otherwise be detected |
| `port.width` | the trace's width | mm: a vertical port this wide, centred on the footprint, e.g. a probe feed inside a patch |

How a port becomes a solver port is on [the next page](model.md#ports).

## Nets

`--nets` names are matched exactly first, then by the last part of hierarchical names (`/sheet/RF`
matches `RF`), then ignoring case. An unknown name is an error that lists the nets with copper.

<a id="gerber"></a>
## Gerber input

```bash
kicad2ems fab/board-job.gbrjob --pos fab/board-top.pos
```

kicad2ems can read a board from its fabrication files: the KiCad job file (`.gbrjob`), the Gerber X2 files
and Excellon drill files it lists, and a pick-and-place file for the ports.

- The job file gives the stackup.
- The copper Gerbers give the copper, and their net attributes give the nets, so `--nets` works.
- The plated drill files give the vias, including blind and buried spans.
- The profile Gerber gives the outline.

Gerber files have no footprint fields, so a port on an inner layer, or with a different rotation or
impedance, must be given as a setting: `--port P2@x,y,In2.Cu,rotation`. Importing the demo board from its
own Gerber export gives the same board as the KiCad import (`tests/test_importers.py`).

---
[← APIs](api.md) · [Index](index.md) · [Next: The board model →](model.md)
