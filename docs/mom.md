# mom25d

[← openEMS simulation](simulation.md) · [Index](index.md) · [Next: Transmission-line model →](transmission-lines.md)

mom25d is a 2.5D method-of-moments solver for circuit boards. It is its own package, in its own
repository ([mom25d](https://github.com/lowprocess/mom25d)): the `mom` extra (`pip install '.[mom]'`), or
`./setup.sh`, installs it. It doesn't need openEMS. Without it, `kicad2ems --check` lists mom25d (and the mixed
solver, which is built on it) as not installed, and `--solvers auto` leaves them out.

```bash
kicad2ems examples/rf_interdig.toml --solvers mom        # mom25d alone
kicad2ems examples/rf_interdig.toml --solvers mom,lines  # with the line model
kicad2ems examples/rf_interdig.toml --mom                # next to openEMS, as a reference
```

Its settings are the `--mom-*` options, or `dmax`, `port`, `solves`, `tiled`, `dump` and `near`
under `[solver.mom25d]` ([options](usage.md#options)). Build the compiled core for about a fivefold
speed-up: `python -m mom25d.core.build` (needs g++ with OpenMP and pybind11).

## What mom25d models

- Copper is zero-thickness sheets on the layer interfaces.
- The dielectric layers extend sideways to infinity, over an **infinite ground plane**. That plane is
  the board's `ground` layer, or the lowest layer whose copper covers half the board. Copper below it is
  left out.
- Vias are vertical wires between the layers they connect.
- Edge walls become rows of vias every 0.5 mm.
- A lumped part is one series R-L-C element across its gap.
- Radiation and surface waves are included. The board edge and openEMS's air box aren't, so there are
  no box resonances and no edge effects.
- Loss uses the stackup's tan δ, constant over frequency. `--sigma` adds copper loss.

Other copper layers, including inner planes and pours, are meshed. Large planes add many unknowns and
slow mom25d down (see [speed](#runtime)).

<a id="sweep"></a>
**The adaptive sweep.** mom25d solves only some frequencies. It fits one rational model through all of
S (AAA) and adds a solve where two successive models disagree most. It stops when the model changes by
less than the tolerance (5e-3 in |S|) and had predicted the newest solve as closely, twice in a row, or
after `--mom-solves` (60) solves. The GUI's Run panel plots the model change and the prediction error after
each solve, in dB of |S|, against the tolerance (−46 dB) as the sweep runs. A sweep that ends at the cap
without settling shows there. The run summary keeps the history (`solved.mom25d.afs`).

<a id="the-board"></a>
mom25d reads the board file every solver gets, `<name>_board.json`. It can also run on its own:

```python
import numpy as np
from mom25d.api import solve_board
res = solve_board('out/filter_board.json', np.linspace(1e9, 8e9, 401), dmax=0.3e-3)
res.S, res.names, res.zref, res.notes
```

## Ports

| `--mom-port` | Port |
|---|---|
| `auto` (default) | a de-embedded wave port where the straight feed line is long enough, otherwise lumped |
| `wave` | always a wave port (an error if the feed is too short) |
| `lumped` | a small via from the reference copper to the trace |

A wave port fits the incoming and outgoing waves along its feed line. The propagation constant comes from
a separate characterisation of the line (a long test line of the same cross-section), so the feed only
has to be long enough for the two amplitudes, about 0.1 rad at the lowest frequency. Fitting the
propagation constant from the feed too failed on short feeds with a strong reflection: on the radial
stub it came out 2–10× low and gave |S11| = 1.19. Switching between the two at a feed length made S jump,
which cost the via transition 42 solves instead of 21.

Lumped ports are closest to openEMS's ports, since both include the port's own parasitics. Wave ports
are closest to the line model, which has an ideal reference plane. Differential pairs are one mixed-mode
port (`P1d`, `P1c`). With `auto`, a pair whose straight coupled feed is too short for a differential wave
port (ports on pads or connector pins) is solved with its legs as single-ended ports, and the modes are
taken from them as for openEMS.

<a id="real-boards"></a>
### On real boards

mom25d suits planar circuits over one ground plane. On a multilayer board it has three limits, seen on
KiCad's CM5 demo (HDMI D0 pair, 6 layers):

- **The ground plane**: by default it's the lowest layer covering half the board (B.Cu there), and every
  plane above it is meshed: 128,000 unknowns, an estimated 35 hours. With `ground = "In1.Cu"` the region
  drops to 22,700 unknowns, but the copper below the ground is left out.
- **Ports below the ground**: the module's pads sit on B.Cu, under that ground, so they can't be ports. On
  that board every high-speed pair starts there.
- **Results**: with the ports moved to F.Cu, the GPU solver returned NaN (a solve that isn't finite is now
  redone on the CPU; there, `MOM25D_GPU=off`, it solves, about 3 minutes a frequency). The adaptive sweep did not
  converge in 15 solves, and a lumped port in the middle of a trace reflected almost everything (|S| 0.99, gfdtd
  0.29).

For such boards use openEMS or gfdtd, or mom25d's whole stack (below) where the board is small enough.

<a id="whole-stack"></a>
### The whole stack

`mom_stack = "whole"` (`--mom-stack whole`) models every copper layer instead of an infinite ground: the
lowest copper layer is the bottom of the stack, with air below as well as above, and the planes are meshed
like any other copper (three times the cell size, as for planes above the ground). That lifts the first two
limits: copper below the old ground is in the model, ports can sit on any layer, and each port is a lumped
port to the nearest plane under or over it (or its `ref_layer`). Wave ports need the infinite ground, so the
ports are lumped. `mixed` stays on the ground stack.

On `rf_msvia` (F.Cu microstrip through a via to B.Cu, two inner planes), which the ground stack cannot solve
(its B.Cu port is below every plane), the whole stack has 5,219 unknowns at `--mom-dmax 0.6` and takes 25 s:

| `rf_msvia` | S21 1 GHz | 3 GHz | 5 GHz | 7 GHz | 10 GHz | worst S11 |
|---|---|---|---|---|---|---|
| mom25d, whole stack | −0.09 dB | −0.27 | −0.38 | −0.60 | −0.77 | −18.3 dB |
| gfdtd | −0.07 dB | −0.24 | −0.38 | −0.55 | −1.02 | −12.9 dB |

They agree to 0.05 dB up to 7 GHz. Above it the planes' 1.8 mm cells are about eight per wavelength, and the
dielectric is still laterally infinite (FDTD's ends at the board edge).

The cost is the planes. On the CM5 region the whole stack is about 125,000 unknowns at 0.3 mm (the signal and
pour layers F.Cu, In3.Cu, B.Cu and In2.Cu are 77 % of them), which is why openEMS or gfdtd remain the tools for
boards of that size. Slot layers (meshing a solid plane's openings rather than its copper) would cut In1.Cu
and In4.Cu from 14,500 to 8,700 unknowns each there: not enough to change that (ROADMAP).

<a id="antenna-mode"></a>
## Antenna mode

With `mode = "antenna"`, mom25d computes the far field of the currents and writes the same results as
openEMS: match, directivity, efficiency, gain and patterns (`<name>_mom25d_antenna.json`, `_antenna.png`,
`_pattern.png`). The GUI's Results page plots them, and its Viewer draws the pattern in 3D.

On the ground stack the ground is infinite, so nothing radiates below the board and there's no board edge.
That suits patches and arrays on a ground larger than themselves. With the [whole stack](#whole-stack) the
board's own ground is meshed and the pattern covers the full sphere: behind the board too. The vias' vertical
currents radiate in both (a probe feed, a shorting via), through the stack's TM response. The dielectric is
still laterally infinite, so an antenna whose radiation comes from the board's edge (an inverted-F, a monopole
at the edge) is still openEMS's or gfdtd's.

| Patch (`antenna_patch.toml`) | Resonance | S11 | Efficiency | Directivity | Gain | Time |
|---|---|---|---|---|---|---|
| mom25d | 2.450 GHz | −31.4 dB | 36 % | 6.4 dBi | 2.0 dBi | 20 s |
| mom25d, whole stack (its 80 mm ground meshed) | 2.470 GHz | | 36 % | 7.3 dBi | 2.9 dBi | 47 s |
| openEMS | 2.438 GHz | −32.3 dB | 38 % | 7.0 dBi | 2.9 dBi | 81 s |

With the whole stack the back lobe at θ 180° is −17.5 dBi (gfdtd, which ends the substrate at the board's
edge: −13.1 dBi); on the ground stack there is none.

<a id="phased-drive"></a>
### Phased arrays

`drive` drives several ports at once, each as `PORT[=amplitude][@phase]` (the incident waves, phases in
degrees). Ports not named are terminated in their reference impedance.

```bash
kicad2ems examples/antenna_array2x2_phased.toml                     # drive = ["P1@0", "P2@60"]
kicad2ems examples/antenna_array2x2_phased.toml --drive P1@0,P2=0.5@-90
```

mom25d solves each port driven alone, then adds their far fields for the drive. The solve is linear, so
this is exact at every frequency and includes the coupling between the elements. The gain uses the power
accepted by all the driven ports. The log and the JSON give the beam's direction and each driven port's
*active reflection*: what it sees with its neighbours driven too, which changes as the beam is steered.

For a 2D array, `grid` says where each port sits and `steer` adds a phase step per column and per row:

```bash
kicad2ems examples/antenna_array4x4_phased.toml                     # grid = "4x4", steer = [30, 0]
kicad2ems examples/antenna_array4x4_phased.toml --steer 30,-45      # 30 deg per column, -45 deg per row
kicad2ems board.toml --grid "P1:0,0 P2:1,0 P3:0,1 P4:1,1" --steer 0,60
```

`grid` is `COLSxROWS` (the ports in order fill the rows, top row first: `4x4` for P1..P16) or
`PORT:COL,ROW` per port, separated by spaces (fractional places for an offset lattice). An element at
column *c*, row *r* gets *c*·X + *r*·Y more phase on top of its `drive` phase. Without a grid, `steer X`
adds X from each driven port to the next.

`aim` points the beam at an angle instead of a phase step: `--aim THETA,PHI` (degrees) gives each driven
port −k₀(x sin θ cos φ + y sin θ sin φ) more phase from its place (the port's centre), at the pattern
frequency, on top of `drive` and `steer`. It needs no grid, so any layout of feeds works: rows and columns, a
triangle, an offset lattice. The phases are those of phase shifters set at that frequency, so the beam squints
off it. A feed well off its element's centre still needs the drive's own phases to make up the difference.

```bash
kicad2ems examples/antenna_array2x2_phased.toml --drive P1,P2 --aim 20,90     # 70.5 deg between the rows
```

The peak lands where the array factor times the element's own pattern is largest, which on a few elements is
short of the target: the 2×2 example aimed at θ 20° peaks at 15°, where its two rows give a broad array factor
and the patch's pattern falls away from broadside. On isotropic elements it is on target within a degree.

`taper` multiplies an amplitude taper into the drive for lower sidelobes: `taylor`, `chebyshev` or
`cosine`, with `:SLL` for the level in dB (Taylor and Chebyshev; default 25), e.g. `--taper taylor:25`.
On a grid it is a column weight times a row weight. On the 4×4 example at 2.45 GHz, uniform drive gives
sidelobes of −14 dB (xz) and −13 dB (yz) at 14.3 dBi. With `taylor:25` they drop to −28 and −25 dB at
13.4 dBi. With `chebyshev:25` they reach −30 and −27 dB at 13.3 dBi. On only four elements a sampled
Taylor falls a dB or two short of its level, where Chebyshev holds it exactly.

Each port's far field is kept in `<name>_mom25d_array.npz`. The GUI's Results page uses it to steer the
beam without solving again ([Beam steering](gui.md#beam-steering)).

The example is a 2×2 patch array, 70 mm (0.57 λ) apart, with a feed per row. With P2 60° behind P1 the
beam tilts to θ 15° in the yz plane, with both ports matched (active S11 −29 and −23 dB); 45 s.

openEMS and gfdtd take `drive` too: one run per driven port, the others terminated, so each run is an incident
wave on its port alone. Each run's far field divided by that wave is the field per unit wave, and the drive is
their sum: exact at every recorded frequency, with the coupling between the elements, and steered afterwards in
the GUI like mom25d's. `aim`'s phases are applied at the pattern frequency after the runs. On the 2×2 example,
gfdtd (two runs, 46 s on the GPU) puts the beam at θ 10° (mom25d 15°: its infinite ground has no board edge),
and P1 driven alone this way gives what its single antenna run gives, to 0.01 dB.

<a id="current-dumps"></a>
## Current dumps

`--mom-dump 2.4,5` saves the surface current on each layer at those frequencies, driven from the port
named by `--dump-port` (default: the first). The adaptive sweep solves at those frequencies among its first
samples and keeps their currents, so a dump inside the band costs no solve of its own (one outside it still
does). The dumps are drawn as
|J| maps, animated with `--dump-animate`, and shown in the GUI's Viewer.

```bash
kicad2ems examples/rf_stubnotch.toml --solvers mom --mom-dump 2.4,1.2 --dump-animate gif
```

`--mom-near 0.5,2` also computes the E field on planes 0.5 and 2 mm above the board. Keep them at least
one mesh cell (`--mom-dmax`) above the copper.

<a id="meshers"></a>
## The mesh

mom25d meshes each copper layer into triangles (Triangle). The current peaks sharply at conductor edges,
so what matters most is thin cells along the edges: the mesher adds offset rings inside each edge.

A study on a coupled-line filter found that edge resolution sets the accuracy, not the cell size along
the copper. Thin edge cells gave 2–4 times smaller errors than evenly shaped cells, with a third of the
unknowns. The study is in `tools/mesher_study.py` in mom25d.

A gmsh mesher was tried and dropped. On six example boards it needed 5–15 % more unknowns than Triangle for
the same cell size and was never more accurate: as good on three, worse on the coupled-line filter and the
differential via pair (at 0.3 mm cells 0.26 from a fine reference in |S|, Triangle 0.015), and it could not
mesh the Wilkinson divider's via ports at all.

![mesher study](img/mesher_study.png)

<a id="zone"></a>
### Signal zone (experimental)

`--mom-zone on` keeps the mesh detail where the current crowds: within a zone around the signal copper
(every net except the ground nets). The zone reaches 0.5 mm from the signal copper or 1.5 times the
thickest dielectric, whichever is more, and 1 mm around each signal via, so its return vias are inside.
Outside the zone:

- copper edges get no edge rings, and cells grow to 5 times `--mom-dmax`;
- vias get no octagon, only their attachment point;
- small pads (stitching vias) become squares of the same area;
- the layers nearby are not refined under it.

Most of the unknowns this removes were stitching-via pads. Each round pad had edge rings, an octagon and a
fan: about 130 unknowns, against 4 now.

| Board | Unknowns | Difference from a fine reference at 2 / 5 / 8 GHz |
|---|---|---|
| Demo transition (cropped, via ports) | 8,334 → 2,781 | 0.015 / 0.018 / 0.009 (default mesh: 0.015 / 0.015 / 0.010) |
| Same, `--mom-dmax 0.2` and the zone | 4,611 | 0.001 / 0.002 / 0.002 |
| Grounded coplanar waveguide | 3,416 → 2,430 | 0.001 / 0.001 / 0.002 (default: 0.001 / 0.001 / 0.003) |
| Differential via | 7,404 → 5,079 | 0.002 / 0.007 / 0.024 (default: 0.004 / 0.007 / 0.009) |
| Demo transition, whole board | 13,543 → 3,623 | (the default mesh does not fit in 7 GB) |

Layers made only of signal copper are left as they are. The differential via shows the limit: at 8 GHz the
via transition excites the space between its ground planes, and the coarser mesh away from the signal costs
about 0.02 there. Studies: `tools/zone_study.py` in mom25d.

<a id="vias"></a>
### Via meshing

Signal vias (on any net but the ground nets) and stitching vias (ground nets: stitching, fences, edge walls)
are meshed separately:

| Option | Values |
|---|---|
| `--mom-signal-vias` | `round` (default): pads and antipads as drawn, an octagon of the via radius. `square` or a number of sides N: pads, antipads and the via barrel as N-gons |
| `--mom-stitching-vias` | `auto` (default): square outside `--mom-zone`, else round. `round`, `square` or N: pads and antipads as N-gons, the via a single vertex |

A pad on its own becomes a square of the same area, and a pad on a trace the square around the round pad, so the
joint is kept. A round antipad becomes a square hole of the same area.

Difference from a fine reference at 2 / 5 / 8 GHz, with `--mom-zone on`:

| Board | Stitching `auto` | Stitching `square` | Signal and stitching `square` |
|---|---|---|---|
| Demo transition (cropped) | 2,701: 0.016 / 0.018 / 0.010 | 2,695: 0.015 / 0.016 / 0.011 | 2,470: 0.010 / 0.050 / 0.040 |
| GCPW | 2,430: 0.001 / 0.001 / 0.002 | 1,533: 0.001 / 0.002 / 0.006 | 1,533: same |
| Differential via | 5,067: 0.003 / 0.010 / 0.024 | 5,070: same | 4,778: 0.013 / 0.041 / 0.057 |

Signal vias by number of sides (with the zone; difference from the fine reference at 2 / 5 / 8 GHz):

| Sides | Demo transition (cropped) | Differential via |
|---|---|---|
| round | 2,701: 0.016 / 0.018 / 0.010 | 5,067: 0.003 / 0.011 / 0.024 |
| 16 | 2,875: 0.019 / 0.025 / 0.018 | 6,060: 0.002 / 0.006 / 0.025 |
| 12 | 2,801: 0.015 / 0.016 / 0.008 | 5,675: 0.003 / 0.012 / 0.024 |
| 8 | 2,682: 0.013 / 0.019 / 0.012 | 5,291: 0.003 / 0.015 / 0.024 |
| 6 | 2,617: 0.014 / 0.022 / 0.021 | 5,043: 0.004 / 0.019 / 0.029 |
| 4 | 2,473: 0.015 / 0.046 / 0.052 | 4,823: 0.010 / 0.035 / 0.047 |
| 8, no barrel | 2,634: 0.012 / 0.021 / 0.018 | 5,195: 0.004 / 0.018 / 0.028 |

Eight sides or more match the round vias within the mesh's own scatter (about 0.01 on these boards); six start
to drift, and four lose 0.03 to 0.05 at 5 to 8 GHz. The n-gons save next to nothing, though: KiCad's round pads
are already coarse polygons, and a board has few signal vias. Keep `round`.

Square signal vias cost accuracy wherever the via is the structure (the transition, the differential pair):
the pad and antipad set its capacitance. Square stitching vias are close; on the GCPW its fence carries the
return current, and the error at 8 GHz grows from 0.002 to 0.006.

<a id="lines"></a>
### Line functions (experimental)

`--mom-lines on` finds straight runs of signal trace and, inside each, replaces the triangles' unknowns with a few
functions per column along the run (mom25d's `line.py`). Each one carries current along the line with a fixed shape
across it: peaked at both edges, as 1/√(distance to the edge), plus an odd shape that pushes the current to one
side (coupled lines need it). The runs stop short of bends, tees, pads, ports and vias, and the matrix is reduced
the same way as for quads. `even` keeps only the symmetric shape (isolated lines), `full` adds a flat one.

Difference from a fine reference at 2 / 5 / 8 GHz (columns 2 × `--mom-dmax` long):

| Board | Unknowns | With line functions | Without |
|---|---|---|---|
| rf_coupler | 5,864 → 4,027 | 0.006 / 0.013 / 0.018 | 0.004 / 0.009 / 0.015 |
| Demo transition (cropped, zone) | 2,701 → 2,307 | 0.017 / 0.019 / 0.008 | 0.016 / 0.018 / 0.010 |
| GCPW (zone) | 2,430 → 2,159 | 0.003 / 0.002 / 0.004 | 0.001 / 0.001 / 0.002 |
| Differential via (zone) | 5,067 → 3,860 | 0.003 / 0.012 / 0.024 | 0.003 / 0.011 / 0.024 |

Inside a run they replace the unknowns about 10 to 20 to one; what is left is the rest of the board. `even` alone
loses 0.04 on the coupler. Columns one cell long fail (the functions' kinks fall inside the triangles), so they are
at least two cells long.

<a id="mixed"></a>
## The mixed solver: long lines as transmission lines

`--solvers mixed` (`mom25d/kicad2ems/mixed.py` in mom25d's repository) is mom25d with the middle of each long, lone, straight run of
signal trace cut out of the board and put back as a transmission line. mom25d solves the rest with a de-embedded
wave port on each face of each cut. Each port's line is characterised on its own cross-section: the planes over
it and coplanar ground beside it. Each cut then becomes a line of that γ and Zc, and the lines are joined into
mom25d's network.

A stretch is cut only where no other signal copper (on any layer) comes within 3 widths or 4 heights of it, and
not through vias, parts or ports. Each face keeps the straight feed its wave port needs down to `fmin`, and a cut
is at least `--mixed-cut` mm long (default 3). It uses mom25d's mesh options (`--mom-dmax`, `--mom-zone`, ...).

The board's own ports are made from the uncut copper, with their wave-port feeds capped at what the fit needs
down to `fmin`. Otherwise a port fits over its whole straight feed and leaves nothing to cut, or, once the
line is cut short, it falls back to a lumped port.

On rf_stripline (with `--mom-zone on`), one 12.5 mm stretch is cut, taking 5,069 unknowns to 4,335. The result
matches mom25d's own: S21 within 0.5° up to 10 GHz, and a group delay of 160.1 ps against 160.2 ps.
The demo transition gets no cut: at 1 GHz each wave port needs about 5.5 mm of feed, and its traces are 10 mm.

**Current dumps** (`--mom-dump`) work. Each port's mom25d solution (1 V on its gap) is combined into the
joined network's state, using mom25d's own dump convention: 1 V on the driven gap, the board's other ports'
gaps shorted, and the cut ports' gap voltages set so their waves are those of the lines. On the cut stretch,
the trace current comes from the line's waves, with mom25d's own profile across the trace taken just outside
the faces. The planes over and under it carry the line's image as return current. On rf_stripline at 5 GHz,
the trace current matches mom25d's within 5% along the whole line. The planes under the stretch follow
mom25d's shape about 20% low, with small spikes at the cut faces.

<a id="quads"></a>
### Quad cells (experimental)

`--mom-quads on` merges pairs of triangles that together make a near-rectangle (the strips along straight
traces) into one quad cell. The current on a quad is kept at the same charge density on both triangles, so
the shared diagonal stops being an unknown. Quads have the unknowns of rooftop cells, one per side. The
matrix is still filled on the triangles and then reduced before the factorisation.

| Board | Unknowns | Change in S from quads |
|---|---|---|
| Stepped microstrip, tensor mesh | 795 → 511 | 0.014 from a fine reference, triangles 0.015 |
| Hairpin filter | 4,763 → 3,974 | 0.006; S21 within 0.1 dB through the passband |
| Demo transition (cropped, via ports) | 8,334 → 6,509 | 0.017 from a fine reference, triangles 0.015 |

`--mom-quads centre` merges only in the middle of signal lines: no triangle with a corner on the copper outline,
none off the signal copper. It merges fewer (rf_coupler: 5,864 → 5,173 unknowns, against 4,725 with `on`); both
stay as close to a fine reference as the triangles (0.015 / 0.014 / 0.015 at 8 GHz).

On the demo transition the factorisation went from 8.3 s to 4.1 s a frequency, plus 1.4 s to reduce the
matrix (2 cores). On the fine reference mesh itself, quads stay within 0.006 of the triangles. Quads work
with the direct solver on the CPU (double and mixed precision); the GPU and compressed solvers ignore them. Studies: `tools/quad_study.py` in mom25d.

<a id="gpu"></a>
## GPU

mom25d can factor its matrix on a GPU, in single precision, and then refine the answer to full precision on
the CPU. Install the CuPy package for your GPU stack:

| GPU | Package |
|---|---|
| AMD, ROCm 7 | `pip install cupy-rocm-7-0` |
| NVIDIA, CUDA 12 / 13 | `pip install cupy-cuda12x` / `cupy-cuda13x` |

PyTorch also works as a backend. `kicad2ems --check` shows the GPU mom25d will use, and so does the GUI's
Solvers window (Help > Solvers and hardware). `MOM25D_GPU=off` turns it off. Consumer AMD cards outside ROCm's support list may need
`HSA_OVERRIDE_GFX_VERSION=11.0.0` (RDNA3) or `10.3.0` (RDNA2).

mom25d chooses how to solve from the memory available. The log says which it chose (`memory: direct
solver needs ...`):

| Path | When | Memory for N unknowns |
|---|---|---|
| double precision on the CPU | up to 3,500 unknowns (or no compiled core), and it fits in half the free RAM | 18 N² bytes |
| mixed precision on the CPU | above 3,500 unknowns, no GPU: single-precision LU refined to double (about 1.3× faster) | 10 N² bytes |
| GPU LU | the single-precision matrix fits on the GPU | 24 N² bytes RAM, 8 N² bytes GPU |
| tiled | the GPU LU doesn't fit (`--mom-tiled`) | little RAM, 4 N² bytes GPU, the rest streamed from RAM |
| compressed (BLR / ACA) | nothing else fits | much less, but slower |

The **tiled** solver keeps only half the matrix on the GPU (it is symmetric) and builds it one block at
a time. It solves the matrix's symmetric part, which changes S by at most about 1e-3. Where the GPU LU
fits, that is faster, so `--mom-tiled auto` uses tiling only for larger boards. With it, the coupler at
0.1 mm cells (45,850 unknowns) solves in 62 s on a 16 GB card, where the CPU took over an hour.

<a id="runtime"></a>
## Speed against openEMS

Measured on every example, CPU and GPU: [benchmarks](benchmarks.md). A few of them:

| Board | openEMS | mom25d GPU | mom25d CPU |
|---|---|---|---|
| filter (single-layer microstrip) | 119 s | 18 s | 22 s |
| hairpin (single-layer microstrip) | 50 s | 9 s | 10 s |
| rf_coupler (0.14 mm gap) | 92 s | 16 s | 20 s |
| demo_transition (2 inner planes) | 55 s | 188 s | 436 s |
| sma_launch (3 planes, 185 vias) | 33 s | 171 s | 417 s |

- **mom25d is faster on single-layer microstrip**: filters, couplers and matching networks. It has no
  time-step or ring-down problem with high-Q circuits.
- **openEMS is faster once planes must be meshed.** mom25d's cost grows with the copper area, openEMS's
  with the volume.
- **Agreement**: where both resolve the structure, |S| agrees to about 0.05, and transmission to 0.2 dB
  and 1–3 ps. Larger dB differences appear only deep in stop bands and on steep filter skirts.
- **On the GPU**, mixed precision can fail to converge at the bottom of a band, where the matrix is worst
  conditioned. That frequency, and those up to 1.25 times it, are then solved in double precision on the
  CPU, and the GPU is tried again above that. On the SMA launch, whether that happens at a given frequency
  depends on rounding in the last digit of the matrix: relative noise of 1e-15 moved the fallbacks from
  run to run, and with them the run time, between about 195 and 410 s.

<a id="core"></a>
## The compiled core

The core (`core/mom25d_core.cpp` in mom25d, pybind11 and OpenMP) fills the matrix, evaluates the near-pair
corrections, finds the near pairs, sums the Green's function tables' Sommerfeld integrals, builds the via
fans' columns and prepares the GPU's complex64 copy of the matrix. NumPy versions of all of them remain as
the fallback. `mom_core` chooses:

| `mom_core` | Kernels |
|---|---|
| `auto` (default) | the core where it's built; boards up to 3,500 unknowns use NumPy's dense solver |
| `compiled` | the core for every board, small ones through its direct solver; stops if the core isn't built |
| `python` | NumPy only: slower, for checking the core |

Measured on the RX 7800 XT machine (whole runs, mom25d only):

| Board | Unknowns | NumPy paths before | Core with the new kernels | `compiled` |
|---|---|---|---|---|
| antenna_patch | 1,391 | 19.6 s | 17.9 s | 5.3 s |
| demo_lowpass | 1,875 | 21.5 s | 19.0 s | 2.7 s |
| rf_wilkinson | 4,657 | 7.2 s | 5.4 s | – |
| rf_stubnotch | 5,090 | 10.3 s | 8.8 s | – |
| rf_stripline | 15,563 | 135 s | 101 s | – |

On small boards, `compiled` gives |S| within about 0.005 of the dense solver's (the two split near and far
interactions differently), with the same resonances. A profile of the larger boards shows what a complete
C++ port could still gain: about 60 % of their time is already compiled (the core, the GPU's LU, BLAS in the
refinement), and most of the rest is NumPy on large arrays rather than interpreted Python.

## Inside mom25d

| Module | Contents |
|---|---|
| `api.py` | `solve_board`: a board file in, S-parameters out |
| `board.py` | the board file as a planar model |
| `model.py` | stack, metal, vias, ports, port characterisation |
| `sim.py` | `Simulation`: mesh, ports, parts, solver choice (`memory_plan`) |
| `multilayer.py` | layered-media Green's functions |
| `base.py` | RWG basis functions, the 1/R moment integrals, the port wave fits |
| `via.py` | via kernels |
| `geo.py` | the mesher (Triangle, with offset rings along the copper edges) |
| `fast.py` | the matrix fill and the solvers |
| `gpu.py`, `ldlt.py` | the GPU backends and the tiled solver |
| `blr.py` | the block low-rank solver |
| `afs.py`, `run.py` | the adaptive frequency sweep |
| `farfield.py` | far and near fields from the currents |
| `core/` | the compiled C++ kernels |

The formulation is a mixed-potential integral equation with RWG basis functions. Layered-media kernels
come from a spectral transmission-line network. The singular terms are integrated analytically.

mom25d's tests (in [its repository](https://github.com/lowprocess/mom25d)) check it against exact solutions and textbook formulas: layered
Green's functions to about 1e-5, stripline impedance to 0.6 %, coupled-line couplers to 0.1 dB, plane
cavity resonances to 0.7 %.

**Limits**: copper is zero-thickness, there is no surface-roughness model, and wave ports need a straight
feed line.

---
[← openEMS simulation](simulation.md) · [Index](index.md) · [Next: Transmission-line model →](transmission-lines.md)
