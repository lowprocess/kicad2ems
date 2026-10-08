# kicad2openems

Turn a KiCad board into an [openEMS](https://openems.de) 3D FDTD simulation: real stackup, copper,
vias and board outline, ports from footprints, an automatic mesh, and S-parameters out as Touchstone.

```bash
pip install .                                   # numpy, shapely, matplotlib (+ openEMS, see below)
kicad2openems board.kicad_pcb --pos board-top.pos --nets RF,GND --fmin 1 --fmax 6 --preview
kicad2openems board.kicad_pcb --pos board-top.pos --nets RF,GND --fmin 1 --fmax 6 --excite P1
```

`--preview` builds the model and mesh, writes `<name>_mesh.png` and prints cell count, time step
and memory, without running openEMS (it does not need openEMS installed). Try it on the demo board:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.kicad_pcb --config examples/demo_transition.toml --preview
```

## Marking ports in KiCad

Place a footprint (no copper needed) at the end of each feed line with a **Fab-layer text `P1`,
`P2`, …**. Its position is the port location and its rotation the direction *into* the circuit
(0° = +x; ports must point along x or y). Alternatively list the components in the
pick-and-place file (`--pos`) with Value or Reference `P<n>`, or a package name containing
"PORT". **Differential ports:** one footprint per leg, texts `P1+` / `P1-` (or `P1P` / `P1N`).

Optional footprint fields: `port.layer` (copper layer of the port, e.g. `In2.Cu` for a stripline;
default F.Cu / B.Cu by footprint side) and `port.z0` (default 50 Ω). `mom.layer` / `mom.z0` are
accepted too.

## What the model contains

* **Stackup** from *Board Setup → Physical Stackup*: each dielectric with its thickness, εr and
  tan δ (loss applied at the band centre); copper layers as zero-thickness sheets at their heights,
  PEC by default or conducting sheets with `--sigma 5.8e7` (thickness from the stackup). Solder
  mask only with `--mask` (its 10–20 µm layer forces a much smaller time step).
* **Copper** of the nets in `--nets` (all nets if omitted): tracks, arcs, filled zones as filled
  (antipads included), pads, graphic polygons. Polygons with holes are split into hole-free pieces
  for CSXCAD. `--region x0,y0,x1,y1` crops (KiCad mm).
* **Vias and plated holes** as metal cylinders (drill diameter) between the copper layers they join.
* **Board outline** from Edge.Cuts: the dielectric is finite, as on the real board.
* `--ground LAYER` models that copper layer as a solid plane over the outline (for boards where the
  reference plane is implied or partly drawn).

## Ports

Lumped ports: a sheet across the trace at the footprint (just inside the trace end), from the
trace to the nearest copper underneath (above it for bottom-side ports, and the other side as a
fallback, e.g. striplines). Reference impedance `port.z0`. They are not de-embedded: the vertical
port current adds a small series inductance (~0.1 nH on 0.2 mm dielectric), noticeable only on
thick substrates at high frequency.

Differential pairs are simulated as two single-ended ports and converted to mixed mode:
`P1d` (Z = 2·Z0) and `P1c` (Z = Z0/2), written as Touchstone 2.0 with per-port `[Reference]`.

One FDTD run per excited port (`--excite P1,P2`; default all). Columns of ports that were not
excited are filled by reciprocity; their own reflection stays unknown (written as 0, with a note).

## Mesh

* **x / y:** thirds rule at Manhattan copper edges (lines 1/3 of the fine cell inside and 2/3
  outside the metal); port planes and the board outline exactly on lines; graded filling with
  ≤ `--ratio` (1.4) growth up to λ/`--res` (λ/20) in the dielectric and in air.
* **Curves** (`--curves`): rounded pad corners, round track ends, arcs, circular antipads and
  oblique edges add lines only at the ends and the x / y extremes of each curved run (`extremes`,
  default: a round antipad gives 2 lines per axis); `vertices` adds a line at every vertex.
* **Vias** (`--vias`): `auto` (default) resolves vias on the port (signal) nets exactly (centre and
  ± radius) and *snaps* all others (ground stitching / fences) onto the nearest existing line
  crossing if it is within `--via-snap` mm (0.2), otherwise gives them a single centre line.
  `exact`, `centre` and `snap` apply one rule to every via.
* **Merging:** lines closer than half the fine cell are merged without chaining or averaging; the
  line just inside a copper edge has priority, so every trace keeps its lines (the mesh report and
  `_openems.json` list the lines across each port trace; fewer than 2 gives a warning).
* **z:** every interface, at least `--nz` (4) cells per dielectric, copper sheets exactly on lines.
* **Fine cell** (`--fine`): default min(port widths, 2 × thinnest dielectric) / 4.
* **Air:** `--air` mm (default 5) between the board and the boundary.

Effect of the curve and via rules (same accuracy: S21 within 0.04 dB / 7° of the full-detail mesh
on the demo board up to 8 GHz):

| Board | every vertex, exact vias | defaults |
|---|---|---|
| demo transition (microstrip → via → stripline) | 1.37 M cells, 272 s | 0.54 M cells, 111 s |
| 4-layer SMA edge launch with via fences | 0.99 M cells | 0.57 M cells |

## Boundaries

`--bc auto` (default): **PEC directly on the bottom copper** when it is a solid plane (≥ 85 % of the
outline) — exact, and there is no air below the board — and **MUR** (absorbing, no extra cells) on
the other five sides. Also accepted: `MUR`, `PML_8` (8 extra cells per side), `PEC`, or six
values `xmin,xmax,ymin,ymax,zmin,zmax`. PEC side walls close the model into a metal box; the tool
warns when its first cavity resonance is below fmax (spurious resonances, slow decay) — only use
them when the real board sits in a shield can of that size.

## Configuration file

Every option can go in a TOML file (`--config`); options typed on the command line win.
openEMS-specific options may sit under `[openems]`, and keys this tool does not know are reported
and ignored, so one file can be shared with other tools (see `examples/demo_transition.toml`).

## Outputs (`--out`, default `openems_out/`)

| File | Contents |
|---|---|
| `<name>_openems.sNp` | S-parameters (mixed-mode for differential ports) |
| `<name>_openems.png` | reflection and transmission plots (`--compare file.sNp` overlays another result) |
| `<name>_mesh.png` | per-layer copper, vias, ports and mesh lines; side view of the stack and z mesh |
| `<name>_openems.json` | port names, reference impedances, mesh statistics, run time |
| `<name>.xml` | CSX geometry (open with AppCSXCAD) |
| `openems/exc_<port>/` | raw openEMS runs |

## Installing openEMS

openEMS is not on PyPI. Check your distribution first (`dnf search openems`, `apt search openems`);
otherwise build it from source without the GUI: `tools/build_openems.sh [prefix]` (lists the build
dependencies for Debian/Ubuntu and Fedora, builds openEMS-Project and pip-installs the `CSXCAD`
and `openEMS` Python packages). If `import openEMS` cannot find its libraries, add `<prefix>/lib`
to `LD_LIBRARY_PATH`.

### "openEMS cannot be imported"

Run `kicad2openems --check` (or `python -m kicad2openems --check`) with the Python you use for the
simulations. It prints which Python is running, the real import error for numpy, h5py, CSXCAD and
openEMS, and what to do:

* **not installed for this Python** — the `kicad2openems` command was installed by a different
  Python than your openEMS venv (`--check` also shows which Python the command on PATH runs).
  Install kicad2openems inside the venv (`python -m pip install .`) or run `python -m kicad2openems`.
* **openEMS from your distribution (dnf / apt) but a venv** — a plain venv does not see system
  packages: recreate it with `python3 -m venv --system-site-packages <dir>`.
* **missing dependency** (often `h5py`) — `python -m pip install h5py`.
* **shared library not found** — add the openEMS install's `lib` folder to `LD_LIBRARY_PATH`.

## Code layout

| Module | Contents |
|---|---|
| `kicad/sexpr.py` | S-expression parser and node accessors |
| `kicad/shapes.py` | arcs, rotation, pad outlines (incl. custom pads) |
| `kicad/board.py` | `KiCadBoard`: stackup, copper items, vias, footprints, port footprints |
| `kicad/nets.py` | net-name matching (hierarchical `/sheet/NET`), differential-leg container |
| `kicad/pnp.py` | pick-and-place files, port components |
| `kicad/outline.py` | board outline from Edge.Cuts |
| `geometry.py` | y flip, polygon lists, hole splitting |
| `model.py` | `Model`: 3D stack, copper per layer, vias, lumped `Port`s |
| `boundaries.py` | per-side boundary selection, cavity-resonance check |
| `mesh.py` | automatic mesh (`auto_mesh`, `smooth_lines`) |
| `simulation.py` | CSXCAD / openEMS setup and runs |
| `sparams.py` | mixed-mode conversion, reciprocity fill |
| `touchstone.py` | Touchstone read / write |
| `plots.py` | mesh preview and S-parameter plots |
| `config.py` | TOML configuration |
| `cli.py` | command line (`kicad2openems`, `python -m kicad2openems`) |

Tests (`pytest`) cover the KiCad import, model, ports, mesh rules, boundaries, mixed-mode
conversion, Touchstone round trip, configuration and an end-to-end `--preview`; none of them
needs openEMS.

## Limits

* Ports must point along x or y; lumped ports only (no de-embedded waveguide/MSL ports yet).
* Copper is zero-thickness; dielectric loss is fitted at one frequency (the band centre).
* Connectors and components are not modelled; a lumped port on the connector pad stands in for it.
