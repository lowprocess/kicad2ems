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
python examples/make_demo_board.py examples/demo_rlc rlc     # microstrip with an 0402 series capacitor
kicad2openems examples/demo_rlc.kicad_pcb --pos examples/demo_rlc-top.pos --nets RF1,RF2,GND --fmax 6 --preview
```

**Lumped-element low-pass filter.** This demo is a 3rd-order Butterworth design (1 GHz, 50 Ω) built
from 0402 parts on microstrip. It is a T network: L1 8.2 nH in series, C2 6.8 pF to a GND pad with
two vias, then L3 8.2 nH in series. The parts carry KiCad fields for their parasitics: `sim.esr`
0.4 Ω on the inductors; `sim.esr` 0.1 Ω and `sim.esl` 0.5 nH on the capacitor. The capacitor's ESL
makes it self-resonate near 2.7 GHz, which appears as a notch in S21. The generator also writes the
S-parameters of the ideal lumped circuit (no lines or pads, −3 dB at 0.87 GHz). The configuration
overlays them, so the plot shows what the layout adds:

```bash
python examples/make_demo_board.py examples/demo_lowpass lowpass
kicad2openems examples/demo_lowpass.kicad_pcb --config examples/demo_lowpass.toml
```

Two variants come from the same generator, each with its own config: `highpass` (C 3.3 pF – L 3.9 nH
to GND – C 3.3 pF) and `bandpass` (series L 27 nH + C 1 pF, then shunt L 2.2 nH ∥ C 12 pF, at
1 GHz). The simulated low-pass rolls off earlier than the ideal circuit (−3 dB at 0.77 GHz instead
of 0.87 GHz), and C2's notch moves from 2.7 GHz down to 2.1 GHz. That shift implies about 0.4 nH
more in the shunt branch than C2's ESL, which is roughly what the stub to the pad, the GND pad and
its vias add. The band-pass shows the same effect more strongly: its passband peak lands at 0.74 GHz
instead of 0.88 GHz. Adding 0.5 nH to each shunt branch of the ideal circuit reproduces that. The
2.2 nH shunt inductor is small enough that the via and pad inductance detunes the filter by 25 %.

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
  tan δ (loss applied at the band centre; `--diel wideband` for a model valid over the whole band, see
  below); copper layers as zero-thickness sheets at their heights,
  PEC by default or conducting sheets with `--sigma 5.8e7` (thickness from the stackup). Solder
  mask only with `--mask` (its 10–20 µm layer forces a much smaller time step).
* **Copper** of the nets in `--nets` (all nets if omitted): tracks, arcs, filled zones as filled
  (antipads included), pads, graphic polygons. Polygons with holes are split into hole-free pieces
  for CSXCAD. `--region x0,y0,x1,y1` crops (KiCad mm).
* **Vias and plated holes** as metal cylinders (drill diameter) between the copper layers they join.
* **Board outline** from Edge.Cuts: the dielectric is finite, as on the real board.
* `--ground LAYER` models that copper layer as a solid plane over the outline (for boards where the
  reference plane is implied or partly drawn).
* **Lumped R / L / C parts** (`--parts auto`, the default; `--parts none` to leave them out): every
  two-pad footprint whose pads are both on modelled copper becomes an openEMS lumped element in the
  copper plane, spanning the gap between the inner pad edges (both ends on mesh lines). Kind and
  value come from KiCad's simulation fields (`Sim.Device` R / L / C, `Sim.Params` `c=1.5p`) or else
  from the reference prefix (R / L / C) and Value (`4p7`, `10k`, `1.2nH`, `0R5`, `100n 50V`).
  Optional fields `sim.esr` / `sim.esl` make it a series R-L-C; 0 Ω (or 0 H) is a metal strap.
  DNP, `exclude_from_sim` and `Sim.Enable = 0` footprints are skipped, as are parts whose pads are
  not in line along x / y or not on the selected nets (a note says why). The pads must be on nets
  in `--nets`.

### Wideband dielectric (`--diel wideband`)

By default the loss tangent becomes a conductivity matched at the band centre, so tan δ falls as
1/f: too lossy below the centre, not lossy enough above. `--diel wideband` uses the
Djordjevic-Sarkar model instead (εr and tan δ from the stackup hold at `--diel-fref`, default
1 GHz; εr then falls slowly and tan δ stays almost constant over frequency, as for real laminates),
fitted by Debye poles (1.5 per decade of the band, typically 4) plus a conductivity, with fit
errors below 0.2 % in εr and 1 % in tan δ. The run prints εr / tan δ at both band edges. The
poles are expensive: on the filter example (1-8 GHz) a run took 4× as long as with `fixed`
(5 poles then, 4 now). On that filter the insertion loss at the 3.1 GHz passband came out at
6.0 dB instead of 7.4 dB, because the default fit overstates the loss below the band centre.

## Antenna mode (`--mode antenna`)

For antennas, one port is driven (the first, or `--excite`) and the field leaves through PML
absorbers on all six sides (`--bc auto` becomes `PML_8`). The default air margin is a quarter
wavelength at the band centre. A near-field to far-field (NF2FF) box records 21 frequencies across
the band, plus any given with `--ff-freq`. The Gaussian excitation reaches 30 % of the band beyond
`--fmax`; near the top edge of a Gaussian's band the far field came out wrong. The run reports and
writes:

| File | Contents |
|---|---|
| `<name>_antenna.png` | \|S11\| with the −10 dB band, input impedance, directivity / gain / realized gain and radiation efficiency over frequency |
| `<name>_pattern.png` | gain (dBi) in the xz (φ = 0), yz (φ = 90°) and xy (θ = 90°) planes and a 3D pattern at the resonance (or `--ff-freq`), with a summary |
| `<name>_antenna.json` | resonance, S11, Zin, bandwidth, the gain table and the cuts |

Angles: θ from +z (the normal of the board's top side), φ from +x (KiCad x); y points up in the
KiCad view. Efficiency is Prad / accepted power (dielectric and copper loss). Realized gain also
counts the mismatch, Prad / incident power. `--ff-step` sets the angle step of the cuts (default 2°).

**Gap ports.** A port footprint placed *between* two conductors on the same layer, not on copper,
becomes an in-plane lumped port across the gap: centre-fed dipoles, loops, or a feed across a slot.
The footprint's rotation gives the axis across the gap, the width is the narrower conductor, and
`port.z0` sets the reference impedance (e.g. 73 Ω for a dipole). Setting `port.type = z` keeps the
usual vertical port.

Four examples, all for 2.45 GHz on FR-4 (`examples/make_antennas.py`, one config each):

```bash
python examples/make_antennas.py dipole examples/antenna_dipole        # also: patch, ifa, slot
kicad2openems examples/antenna_dipole.kicad_pcb --config examples/antenna_dipole.toml
```

| Example | Geometry | Feed |
|---|---|---|
| `patch` | inset-fed patch, 37.2 × 28.8 mm, 1.6 mm FR-4, B.Cu ground | 50 Ω microstrip from the board edge |
| `ifa` | inverted-F (arm 17.5 mm, 5 mm high) above the top edge of a 40 × 48 mm ground plane, shorting leg via to ground | at the ground edge, 3 mm from the short |
| `dipole` | printed half-wave dipole, 2 × 23.3 mm arms, 0.8 mm FR-4, no ground | gap port, 73 Ω |
| `slot` | 52 × 2 mm slot in the B.Cu ground of a 60 × 60 mm board | microstrip crossing 16 mm off-centre, λ/4 open stub |

The dimensions are parameters of the generator (`make_antennas.py ifa out arm=19 s=2.5`). The
defaults were tuned with kicad2openems:

| Example | Resonance | S11 | −10 dB band | Directivity | Efficiency | Gain |
|---|---|---|---|---|---|---|
| `patch` | 2.438 GHz | −19.4 dB | 2.2 % | 7.0 dBi | 39 % | 2.9 dBi |
| `ifa` | 2.451 GHz | −33.5 dB | 12.0 % | 2.9 dBi | 91 % | 2.5 dBi |
| `dipole` | 2.430 GHz | −20.3 dB | 15.6 % | 2.1 dBi | 96 % | 1.9 dBi |
| `slot` | 2.434 GHz | −26.9 dB | 20.6 % | 4.4 dBi | 87 % | 3.7 dBi |

These are the expected values: the dipole's 2.1 dBi matches the ideal half-wave dipole (2.15 dBi),
with its doughnut pattern and nulls along its axis. The patch radiates broadside (+z), and its
39 % efficiency is what FR-4 loss (tan δ 0.02) does to a narrowband patch. The IFA is nearly
omnidirectional. Tuning took a few sweeps of about 10 s per run. An IFA fed through a 3 mm
microstrip could not be matched, because the feed width kept the feed point too far from the
short; feeding at the ground edge fixed it.

## Ports

Lumped ports: a sheet across the trace at the footprint (just inside the trace end), from the
trace to the nearest copper underneath (above it for bottom-side ports, and the other side as a
fallback, e.g. striplines). Reference impedance `port.z0`. They are not de-embedded: the vertical
port current adds a small series inductance (~0.1 nH on 0.2 mm dielectric), noticeable only on
thick substrates at high frequency.

Differential pairs are simulated as two single-ended ports and converted to mixed mode:
`P1d` (Z = 2·Z0) and `P1c` (Z = Z0/2), written as Touchstone 2.0 with per-port `[Reference]`.

One FDTD run per excited port (`--excite P1,P2`; default all). `--jobs N` runs N of them at the
same time in separate processes (openEMS output is then suppressed). Without `--threads`, each run
gets half the logical CPUs divided by N. FDTD is limited by memory bandwidth, so more threads are
not always faster: on a 16-core / 24-thread desktop, one run with 6 threads beat one with all 24
threads, and two parallel runs with 12 threads each were 4× slower. Try `--threads` 4-8 first.
Results agree between thread counts to within the `--end-db` truncation, because openEMS checks
the end criterion on a wall-clock interval. Columns of ports that were not
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

### Mesh convergence (`--converge 1,1.5,2`)

Runs the model on several mesh levels: level *f* uses `--res` × *f*, `--fine` / *f* and
⌈`--nz` × *f*⌉ cells per dielectric. `--preview --converge ...` lists the cells, time step, memory
and relative run time of every level without running anything. A full run solves the coarser levels
first (into `<out>/converge/L<f>/`, without field dumps), then the finest level, which gives the
normal outputs. It prints each level's largest deviation from the finest level (|S| in dB and phase
in degrees, over points above −40 dB), writes `<name>_converge.png`, and stores the table in
`<name>_openems.json`. The result counts as **converged** when the second-finest level is within
`--converge-tol` (default `0.1,2`: 0.1 dB / 2°). On narrowband circuits a small shift of the
passband between levels shows up as tens of dB on the steep skirts. The microstrip filter example
is one: from x1 to x1.5 its passband moves up by about 0.15 GHz. The plot shows whether the
deviation is such a shift (resonators and coupling gaps under-resolved: refine further) or a level
difference.

## Boundaries

`--bc auto` (default): **PEC directly on the bottom copper** when it is a solid plane (≥ 85 % of the
outline) — exact, and there is no air below the board — and **MUR** (absorbing, no extra cells) on
the other five sides. Also accepted: `MUR`, `PML_8` (8 extra cells per side), `PEC`, or six
values `xmin,xmax,ymin,ymax,zmin,zmax`. PEC side walls close the model into a metal box; the tool
warns when its first cavity resonance is below fmax (spurious resonances, slow decay) — only use
them when the real board sits in a shield can of that size.

## Run length (`--end-db`, `--max-steps`)

A run stops when the field energy has fallen by `--end-db` (default 40 dB), or after `--max-steps`
timesteps (default 1,000,000), whichever comes first. `--preview` estimates the length of the
excitation pulse in timesteps and how many pulse lengths `--max-steps` allows, and says when that
is too few. Low frequencies make the pulse long (its length grows as 1 / bandwidth), and high-Q
circuits ring for a long time after it, so both need more steps:

```bash
kicad2openems board.kicad_pcb --config board.toml --fmin 0.05 --fmax 0.5 --max-steps 5000000
```

(or `max-steps = 5000000` in the TOML). After each run the number of timesteps taken is printed,
with a warning when a run stopped at the limit instead of at `--end-db`: its fields may still have
been ringing. The step count is read from the run folder (openEMS's own time step comes from the
excitation file `et`).

## Field dumps

`--dump FIELD@REGION[@GHz,...]` (repeatable; or `dump = [...]` under `[openems]` in the TOML):

| Part | Values |
|---|---|
| FIELD | `E`, `H`, `J` (total current density, shows the copper currents) |
| REGION | `F.Cu` (plane at a copper layer), `F.Cu-0.1` / `In1.Cu+0.05` (offset in mm, e.g. inside the dielectric under a trace), `z=0.3`, `x=12`, `y=7` (KiCad mm), `volume` (whole board), `box=x0,y0,z0,x1,y1,z1` |
| FREQS | GHz list → frequency-domain dump (HDF5) **plus PNG field maps** with copper outlines; omit → time-domain VTK series (animate in ParaView) |

```bash
kicad2openems board.kicad_pcb --config board.toml --excite P1 \
    --dump E@F.Cu-0.1@2,6 --dump J@In2.Cu@4 --dump E@y=7
```

Dumps are recorded in the run of the first excited port (`--dump-port P2` to choose). Planes are
clipped to the board (+1 mm) and the stack (+1 mm of air). The PNG maps are scaled to a **1 V
incident wave** at the driven port (checked: |Ez| under a 0.2 mm-high microstrip is 5.0 kV/m).
`--preview` validates the specs and prints the number of points per dump; the run prints a rough
disk estimate. Files land in `<out>/openems/exc_<port>/dump_*.h5|.vtr`;
`kicad2openems.dumps.read_fd(path)` reads an HDF5 dump into numpy (old and new openEMS layouts).

## Configuration file

Every option can go in a TOML file (`--config`); options typed on the command line win.
openEMS-specific options may sit under `[openems]`, and keys this tool does not know are reported
and ignored, so one file can be shared with other tools (see `examples/demo_transition.toml`).

## Outputs (`--out`, default `openems_out/`)

| File | Contents |
|---|---|
| `<name>_openems.sNp` | S-parameters (mixed-mode for differential ports) |
| `<name>_openems.png` | reflection and transmission plots (`--compare file.sNp` overlays another result, labelled with its file name) |
| `<name>_mesh.png` | per-layer copper, vias, ports and mesh lines; side view of the stack and z mesh |
| `<name>_openems.json` | port names, reference impedances, mesh statistics, lumped parts, run time (+ the convergence table) |
| `<name>_converge.png` | `--converge`: \|S\| on every mesh level and the deviation from the finest |
| `<name>.xml` | CSX geometry (open with AppCSXCAD) |
| `openems/exc_<port>/` | raw openEMS runs |

Reference results for the two example boards (demo transition and microstrip filter) are kept in
`examples/reference/`, e.g. `--compare examples/reference/filter_openems.s2p` after a change.

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
| `parts.py` | lumped R / L / C parts from two-pad footprints, value parsing |
| `dielectric.py` | Djordjevic-Sarkar permittivity and its Debye-pole fit |
| `converge.py` | mesh levels and their comparison for `--converge` |
| `antenna.py` | antenna mode: NF2FF box, match, gain / efficiency, patterns |
| `simulation.py` | CSXCAD / openEMS setup and runs |
| `sparams.py` | mixed-mode conversion, reciprocity fill |
| `dumps.py` | field-dump specs, CSX dump boxes, HDF5 reader, field-map plots |
| `touchstone.py` | Touchstone read / write |
| `plots.py` | mesh preview and S-parameter plots |
| `config.py` | TOML configuration |
| `cli.py` | command line (`kicad2openems`, `python -m kicad2openems`) |

Tests (`pytest`) cover the KiCad import, model, ports, mesh rules, boundaries, mixed-mode
conversion, Touchstone round trip, configuration, lumped parts, the dielectric fit, parallel runs,
convergence levels and an end-to-end `--preview`; none of them needs openEMS (the CSXCAD / openEMS
calls are checked against strict stand-ins).

## Limits

* Ports must point along x or y; lumped ports only (no de-embedded waveguide/MSL ports yet).
* Copper is zero-thickness; dielectric loss is fitted at one frequency (the band centre) unless
  `--diel wideband`.
* Of the components, only two-pad R / L / C parts are modelled (ideal, plus optional ESR / ESL fields).
  Connectors are not modelled; a lumped port on the connector pad stands in for one.
