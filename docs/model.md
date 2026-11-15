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
used for `--sigma`, for the line model's impedances and, with `--thickness on`, by openEMS and gfdtd
([details](meshing.md#thickness)).

## Copper

On each layer, the copper of the selected nets is merged, clipped to the outline, and simplified to
2 µm. `--ground LAYER` replaces that layer with a solid plane over the whole board.

<a id="fill-voids"></a>
### Voids of the nets left out

A pour imported with only some nets selected keeps the clearance around every other net's pads, traces and vias:
holes with nothing in them. The CM5 Minima with one net and GND selected has 903 of them. Every solver models
them, and mom25d pays most: no plane is solid, so none can be a PEC plane of the stack, and each hole has its ring
of edges. `--fill-voids` (`fill_voids` in a config) fills them, for every solver alike:

| | Filled |
|---|---|
| `off` (default) | nothing: the copper as imported |
| `far` | the voids farther from the signal copper of any layer than 1 mm or 3 times the widest gap between copper layers, whichever is more; and every void under 0.5 mm² (thermal-relief gaps, a lone via's clearance) |
| `all` | every void |

A void is a hole of a ground net's copper as the whole board has it, so a clearance that crosses a region's edge
counts. One that holds copper or a via of a selected net that is not ground (an antipad, a trace's own clearance)
is the model, and is never filled. Ground copper that stood alone in a filled void joins the pour. The run's notes
say how many voids were filled on each layer and the area added, and `<name>_board.json` keeps it (`filled`).

On a crop of the CM5 Minima (a 9.3 mm stripline on In2.Cu, 0.1 to 10 GHz, `/CM5/PCIE_PI.nRST` and GND):

| | mom25d unknowns | gfdtd against `off` (it meshes every void) |
|---|---|---|
| `off` | 7,938 (every layer meshed) | |
| `far` | 4,651 (the planes solid: PEC) | \|S21\| within 0.1 dB; \|ΔS\| 0.02 median, 0.07 at most, at a resonance of the cut planes |
| `all` | 4,651 | \|ΔS\| 0.03 median; a resonance moved: \|S21\| at 10 GHz −1.6 → −4.0 dB |

With `all`, a channel on B.Cu 1.1 mm to the side of the trace was filled, which `far` keeps: a void near the signal
is part of the board. `far` is the one to use; `all` only where the planes are known not to matter. On larger
regions around the same trace mom25d's unknowns go from 24,073 to 22,125 (`far`) and 13,100 (`all`) for
14 × 16 mm, and from 42,689 to 33,088 and 25,662 for 14 × 32 mm: `far` keeps the voids on every layer within
its distance of the trace, also those behind a solid plane, where they no longer matter.

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
  trace's width, just inside its end. The trace may run at any angle: the port's sheet stands across the axis
  nearer the trace's direction, as wide as the trace's cut there. mom25d takes the angle exactly; the FDTD
  model's diagonal copper is a staircase on its mesh (a 20 mm line at 30° came out 3.4 % longer electrically than
  the same line along x, on its default mesh). A pad or point port (`P3=J1.1`, `P3@x,y`) takes its track's
  direction, snapped to x or y within a degree.
- **Gap**, when the footprint sits **between** two conductors on one layer, such as a dipole's feed or a
  feed across a slot. The port spans the gap in the copper plane.

The reference impedance is `port.z0` (50 Ω). Differential legs are two ports, combined into modes
afterwards. A lumped port's S carries its feed: fitted as a series L and a shunt C, about 0.07-0.1 nH (0.22 nH
on a grounded CPW) and the trace left behind the port plane, about 0.12 pF per mm of 50 Ω line on FR-4 (70 fF in
the examples, which matter more at 10 GHz).

<a id="line-ports"></a>
**Line ports** (the FDTD solvers, `--port-kind line` or `auto`): voltage and current probes on each port's
straight feed, behind its lumped port, which still drives and terminates the line. S comes from the waves
they read in every run, S = B A⁻¹ (a = (V + z0 I) / 2, b = (V − z0 I) / 2 at the probes' plane, the lumped
ports' own where a port has none), and each probed port's reference is moved back to its lumped port's plane
along the FDTD's own line, read there in the port's own run (Zc and εeff, logged beside the line model's). The
result is the board from the same planes as the lumped S, without the ports' feed: on a straight 50 Ω line
|S11| fell from −20 dB at 8-10 GHz to below −30 dB, and the thru's phase at 10 GHz came within 0.5° of
mom25d's wave ports (lumped: 18° off). A port takes probes where its trace runs straight at one width from the
port to the probes' last plane and max(2 h, w) further, the measurement plane max(3 h, 2 w, four cells, 3 mm)
along it, with no other signal copper within 3 h of it. Nearer than 3 mm the port's own field is in what the
probes read (rf_stripline: εeff 4.87 at 0.6 mm, 4.42 at 3 mm, 4.40 at 4.5 mm; S within 0.011 of 4.5 mm's at
3 mm). 53 of the examples' 88 ports qualify. Gap ports, differential legs,
oblique traces and thick copper stay lumped. Every port must be driven: the examples that drive P1 alone
(`excite`) need `--excite` with every port (`auto` says so and keeps them lumped). Default `lumped`. `--deembed P1=2.5` moves P1's reference plane 2.5 mm in along its own trace (negative: out), the line
model's sections from the port taken off (their Z0, εeff, the mode's tan δ and, with `--sigma`, the copper's
loss), and `--deembed-file P1=fixture.s2p` removes a fixture there (its port 1 at the port); either writes
`<name>_<solver>_deembedded.sNp` beside the solver's own (`kicad2ems.deembed`). Single-ended ports only.

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

### Parts as ports, closed after the solve

`--part-ports all` (or `C1,L2`) makes parts internal ports instead: each is a lumped gap port where its element
would be, named after it, and every one is excited. The solver's S over the external and part ports is then
closed by each part's impedance (its series R + jωL + 1/(jωC)), so `<name>_<solver>.sNp` is the board with
its parts, as a run without the option gives it, and `<name>_<solver>_ports.sMp` the full matrix. Then a
value costs no solve:

- `--part-value C2=3.3p,6.8p,10p` writes `<name>_<solver>_C2=3.3p.s2p` and so on, for every combination of the
  parts swept (at most 64);
- `--part-file C2=grm155.s2p` closes a part port by a vendor's model, measured in series through the part
  (Z = 2 z0 (1 − S21) / S21).

The solvers with gap ports take it: openEMS, gfdtd and Palace; mom25d and the line model are left out of such
a run, with the reason. On `demo_lowpass` (L1, C2, L3) on gfdtd, the closed result is the lumped run's within
0.011 in S21 and 0.054 in S11 (−8.20 dB at 1 GHz against −8.05, −43.7 dB at 2 GHz against −40.8), for one more
excitation a part.

## What isn't modelled

- Ports must point along x or y.
- Copper has no thickness in mom25d, nor in openEMS and gfdtd unless `--thickness on`.
- Only two-pad R/L/C parts are modelled. Connectors aren't; a port on the connector pad stands in for one.

---
[← KiCad import](kicad-import.md) · [Index](index.md) · [Next: Testing →](testing.md)
