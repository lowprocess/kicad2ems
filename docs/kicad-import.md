# 4. KiCad import

[← Architecture](architecture.md) · [Index](index.md) · [Next: The 3D model →](model.md)

`kicad/board.py` reads a `.kicad_pcb` (KiCad 5 to 10) into plain data. It makes no modelling decisions:
everything stays in KiCad coordinates (mm, y down) as shapely geometry, and `Model` decides what to use.

## S-expressions

`kicad/sexpr.py` parses the file into nested Python lists (`['footprint', 'Lib:Name', ['layer', 'F.Cu'],
…]`). Accessors: `items(node, name)` (all children with that head), `item(node, name)` (the first),
`val(node, name, default, cast)`, `xy(node, name)`, `layers(node)` (both `layer` and `layers`). Quoted
strings lose their quotes, so `'F.Cu'` and `F.Cu` compare equal.

## Stackup

From `(setup (stackup …))`: copper layers (thickness), dielectrics (`core` / `prepreg` / anything named
"dielectric"; thickness, `epsilon_r`, `loss_tangent`), solder masks. Dielectric **sub-layers**
(`addsublayer`) become separate entries with the same name. Missing values default to 0.1 mm, εr 4.5,
tan δ 0.02. Without a stackup the model can't be built (`Model` raises with a hint).

## Copper

Collected into `board.items` as `(layer, net, geometry)`:

| KiCad item | Geometry |
|---|---|
| `segment` | the track buffered by half its width (round ends) |
| `arc` | the arc polyline (`shapes.arc_points`) buffered by half its width |
| `via` | a circle of its pad size on each layer it connects; also a `board.vias` entry |
| `zone` → `filled_polygon` | the **filled** copper as KiCad last filled it (refill zones before simulating) |
| `gr_poly` / `gr_rect` / `gr_circle` on a copper layer | the shape (with its net if it has one) |
| footprint `pad` | the pad outline (`shapes.pad_shape`: rect, roundrect, circle, oval, custom) placed and rotated |

Through-hole pads with a drill also become `board.vias` entries (`pth=True`), so plated holes become
metal cylinders like vias. Through vias that list only their two outer layers are expanded to every
copper layer between them. `*.Cu` and `F&B.Cu` layer lists are expanded.

`gr_line` / `gr_arc` on copper layers are **not** read (only filled shapes are copper).

## Board outline

`kicad/outline.py`: an Edge.Cuts `gr_rect`, `gr_poly` or `gr_circle` is used directly; otherwise lines and
arcs are merged and polygonized and the largest loop wins. Without an outline, `Model` uses the copper's
bounding box plus 1 mm and notes it.

## Footprints

`board.footprints` keeps, per footprint: reference, value, all properties (`props`, KiCad 7+
`(property …)`), Fab-layer texts (`fab`), position, rotation, side, pads (name, centre, layers, net,
geometry), and the `dnp` / `exclude_from_sim` flags (from `(attr …)` or their own items). KiCad 5
`module` footprints are read too (pad angles there are relative to the footprint).

## Ports

`KiCadBoard.port_footprints(pos_refs)` finds the port footprints:

1. a Fab-layer text matching `P<n>`, `P<n>+` / `P<n>-` or `P<n>P` / `P<n>N` (differential legs), or
2. if a pick-and-place file is given, footprints listed there whose Value or Reference matches, or whose
   package contains "PORT" (`kicad/pnp.py`).

It returns `{n: footprint}` and `{n: DiffLegs}` for differential ports (both legs required). The
footprint's position, rotation and side, plus the optional fields `port.layer`, `port.z0` and
`port.type` (`mom.layer` / `mom.z0` are accepted too) are all `Model` uses; how a port becomes a lumped
port is in [The 3D model](model.md#ports).

## Nets

`kicad/nets.resolve_nets` matches `--nets` names to the board's nets: exact first, then the last part of
hierarchical names (`/sheet/RF` matches `RF`), then case-insensitively. An unknown name is an error that
lists the nets with copper.

## Lumped parts

Part detection lives in `parts.py` (it needs the model's copper), see [The 3D model](model.md#lumped-parts).
The board side only supplies footprints, pads, values and the KiCad simulation fields.

---
[← Architecture](architecture.md) · [Index](index.md) · [Next: The 3D model →](model.md)
