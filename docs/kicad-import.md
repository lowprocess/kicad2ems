# 4. KiCad import

[← Architecture](architecture.md) · [Index](index.md) · [Next: The 3D model →](model.md)

`kicad/board.py` reads a `.kicad_pcb` (KiCad 5 to 10) into plain data. It makes no modelling decisions:
everything stays in KiCad coordinates (mm, y down) as shapely geometry, and `Model` decides what to use.
`KiCadBoard(path)` reads a file; `KiCadBoard.from_text(text)` reads the text of one (KiCad's API returns
the open board as text, for a KiCad plugin). Anything that is not a `kicad_pcb` is rejected.

## S-expressions

`kicad/sexpr.py` parses the file into nested Python lists (`['footprint', 'Lib:Name', ['layer', 'F.Cu'],
…]`). Accessors: `items(node, name)` (all children with that head), `item(node, name)` (the first),
`val(node, name, default, cast)`, `xy(node, name)`, `pts(node)` (`xy` points, with KiCad 7+ `arc`
entries expanded into points), `width(node)` (`stroke` or plain `width`), `filled(node)`, `layers(node)`
(both `layer` and `layers`). Quoted strings lose their quotes, so `'F.Cu'` and `F.Cu` compare equal.

## Stackup

From `(setup (stackup …))`: copper layers (thickness), dielectrics (`core` / `prepreg` / anything named
"dielectric"; thickness, `epsilon_r`, `loss_tangent`), solder masks. Dielectric **sub-layers**
(`addsublayer`) become separate entries with the same name. Missing values default to 0.1 mm, εr 4.5,
tan δ 0.02. Without a stackup the model can't be built (`Model` raises with a hint).

## Copper

Collected into `board.items` as `(layer, net, geometry)`:

| KiCad item | Geometry |
|---|---|
| `segment` | the track buffered by half its width (round ends); also a `board.tracks` entry |
| `arc` | the arc polyline (`shapes.arc_points`) buffered by half its width; also a `board.tracks` entry |
| `via` | a circle of its pad size on each layer it connects; also a `board.vias` entry |
| `zone` → `filled_polygon` | the **filled** copper as KiCad last filled it (refill zones before simulating) |
| `gr_line` / `gr_arc` / `gr_curve` on a copper layer | the stroke, buffered by half its width (with its net if it has one) |
| `gr_rect` / `gr_circle` / `gr_poly` on a copper layer | the shape, filled or outline-only according to its fill flag |
| footprint `fp_*` graphics on a copper layer | the same shapes, placed and rotated; they take the net of the pad they touch on that layer |
| footprint `pad` | the pad outline (`shapes.pad_shape`), placed and rotated |

Pad shapes: rect, circle, oval, roundrect (also chamfered), trapezoid (`rect_delta`), custom (rectangular
or circular anchor plus `gr_poly`, `gr_line`, `gr_arc`, `gr_rect`, `gr_circle`, `gr_curve` primitives).
Arcs are KiCad 6+ start-mid-end or KiCad 5 centre-end-angle; curves are cubic Béziers.

Through-hole pads with a drill also become `board.vias` entries (`pth=True`), so plated holes become
metal cylinders like vias. Through vias that list only their two outer layers are expanded to every
copper layer between them. `*.Cu` and `F&B.Cu` layer lists are expanded.

**Not read:** zones defined inside footprints.

## Board outline

`kicad/outline.py` builds the outline from every Edge.Cuts item (lines, arcs, rectangles, polygons
including arc segments, circles, curves), on the board and inside footprints. The pieces are snapped to
a 1 nm grid, joined and polygonized; the largest closed shape is the board. Shapes inside it (mounting
holes, slots) do not replace it, and the model uses its outer edge only: cutouts are not removed from
the dielectric. Without an outline, `Model` uses the copper's bounding box plus 1 mm and notes it.

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

It returns `{n: footprint}` and `{n: DiffLegs}` for differential ports (both legs required). `Model`
uses the footprint's position, rotation and side, and these optional fields (each also accepted as
`mom.<field>`):

| Field | Default | Meaning |
|---|---|---|
| `port.layer` | F.Cu / B.Cu by footprint side | copper layer of the port, e.g. `In2.Cu` for a stripline |
| `port.z0` | 50 | reference impedance (Ω) |
| `port.type` | — | `z` forces a vertical port where a gap port would otherwise be detected |

How a port becomes a lumped port is in [The 3D model](model.md#ports).

## Nets

`kicad/nets.resolve_nets` matches `--nets` names to the board's nets: exact first, then the last part of
hierarchical names (`/sheet/RF` matches `RF`), then case-insensitively. An unknown name is an error that
lists the nets with copper.

## Lumped parts

Part detection lives in `parts.py` (it needs the model's copper), see [The 3D model](model.md#lumped-parts).
The board side only supplies footprints, pads, values and the KiCad simulation fields.

---
[← Architecture](architecture.md) · [Index](index.md) · [Next: The 3D model →](model.md)
