# 14. MoM solver (mom25d)

[← GUI](gui.md) · [Index](index.md)

`--solvers mom` solves the board with **mom25d**, a 2.5D (planar) Method-of-Moments solver, from the
board every solver gets: `<name>_board.json`, which mom25d reads itself (`mom25d.board.BoardModel`,
`mom25d.api.solve_board`). FDTD, MoM and the transmission-line model all start from one converted board,
and their results are compared in the plots, the report and the GUI. `--mom` adds MoM next to openEMS as
a reference (a failure is then a warning, not an error: [solvers](usage.md#solvers)). mom25d's settings
are `--mom-*` on the command line, or under `[solver.mom25d]` in a config by their short names (`dmax`,
`port`, `mesher`, `solves`).

```bash
kicad2ems examples/filter.toml --solvers mom,lines  # MoM and the line model, no openEMS: ~1 min
kicad2ems examples/filter.toml --solvers all        # openEMS too, all three compared
```

```python
from mom25d.api import solve_board                  # mom25d on its own, on a board file of an earlier run
res = solve_board('out/filter_board.json', np.linspace(1e9, 8e9, 401), dmax=0.3e-3)
```

**No openEMS needed.** MoM reads the board, not openEMS's geometry: a run with mom25d and the line model
builds no openEMS mesh and imports nothing of openEMS. It also does not inherit openEMS's changes: vias
stay where they are (openEMS snaps most of them to its mesh), a part with ESR / ESL is one element (openEMS
0.37 needs two halves), edge walls sit where the planes end.

| Output | |
|---|---|
| `<name>_mom25d.sNp` | MoM S-parameters, same ports, names, mixed-mode pairs and reference impedances as openEMS |
| `<name>_mom25d.png` | when MoM is the first solver of the run (no openEMS): MoM, the line model dotted |
| `<name>_openems.png` | MoM dash-dotted over openEMS |
| report, *mom25d (MoM)* | unknowns, solves, time, ports; MoM against the first solver of the run (largest \|ΔS\|, transmission dB, delay) |
| `--events` | `status` events after each EM solve of the adaptive sweep (`run` = `mom25d`): the GUI's progress bar |
| `<name>_mom25d_<port>_dump_J_<layer>_fd_<GHz>GHz.png` | `--mom-dump`: \|J\| on each signal layer ([below](#current-dumps)); `.gif` / `.mp4` with `--dump-animate` |

## What MoM sees

mom25d models metal as zero-thickness sheets on the interfaces of a **laterally infinite** dielectric stack
over an **infinite ground plane**; vias are vertical wires. Radiation and surface waves are included; the
board edge, the air box and its boundaries are not, so there are no box resonances (the ripple in an
openEMS stop band) and no edge fringing. The ground is the `ground` layer (or the lowest solid plane);
copper below it is left out. Antenna mode is not supported (no ground plane under the copper).

Planes on other layers are meshed (3 × `mom_dmax`, refined near the copper of nearby layers); a board
with large inner planes has many unknowns ([runtime](#runtime)).

<a id="the-board"></a>
## The board, not the CSX file

MoM reads the board every solver gets (`<name>_board.json`, through `mom25d.board.BoardModel`): the stack
above the ground, copper per interface, vias over the interfaces with metal at them, edge walls as rows of
vias every 0.5 mm, a part as one series R-L-C element across its gap (bridged by a metal strip), the ports
with their layers, reference layers, feed directions and widths, and the stackup's own tan δ.

- **Copper loss:** `--sigma` makes openEMS's copper conducting sheets of that conductivity; MoM gets the
  same conductivity (with the copper thickness of the ports' layer) as its surface impedance. Without
  `--sigma` both are lossless.
- **Loss tangents:** MoM uses the stackup's tan δ, constant over frequency; openEMS (`diel = "fixed"`) a
  conductivity fitted at the band centre. In a narrow-band filter openEMS's loss is then higher below the
  band centre (filter example: ~1 dB at 3.3 GHz with the centre at 4.5 GHz).

Until October 2026 MoM read the CSX file openEMS gets (`<name>.xml` with a `<mom25d>` hints element), and so
inherited openEMS's own changes. Both paths, compared on every example (coarse settings: `mom_dmax` 0.6 mm,
the same mesh on both sides; S at five frequencies across the band, no adaptive sweep):

| Boards | What MoM gets now instead | max \|ΔS\| |
|---|---|---|
| filter, rf_steplpf, rf_stubnotch, rf_branchline, rf_coupler, rf_wilkinson, hairpin, demo_diff | the same model | ≤ 1.5e-4 (the CSX file's 8 digits) |
| rf_gcpw | 22 of 38 vias where they are, not snapped to the FDTD mesh (moved up to 0.27 mm) | 6.5e-4 (wave ports, away from the vias) |
| demo_transition | 22 of 23 vias where they are (moved up to 0.064 mm) | 2.0e-2 |
| demo_lowpass / highpass / bandpass | each part with ESR / ESL one element, not openEMS 0.37's two halves (3 elements instead of 6), 12–16 fewer unknowns; two vias by 0.014 mm | 4.3e-2 / 1.0e-2 / 4.4e-2 |
| sma_launch | 173 of 185 vias where they are (moved up to 0.30 mm); 7,714 unknowns instead of 8,671 (the mesh rings around the vias) | 5.4e-2 for the whole run (S21 within 0.36 dB) |

A whole run with the adaptive sweep capped at 12 solves can differ much more (|ΔS| up to 1.4 on the
stub notch): the sweep had not converged on either side, and an unconverged rational fit magnifies the
smallest difference in its first samples (the filter at the default settings, up to 60 solves, converged
after 20, its last model change 1.5e-4). The check in the
parity tests (`tests/test_parity.py`) keeps the two in step from here on: everything MoM changes from the
board is declared, and anything else fails a test.

## Ports

| `mom_port` | MoM port | Reference plane |
|---|---|---|
| `auto` (default) | a de-embedded **wave port** where the straight feed spans ≥ 0.4 rad at fmin, else **lumped** | the port position |
| `wave` | wave port (error if the feed is too short) | the port position (de-embedded) |
| `lumped` | a via from the reference copper to the trace with the port in it (radius w/4) | the port position (the via's inductance included, like openEMS's lumped port) |

Below ~0.4 rad of feed the wave fit fails (|S11| > 1 at the low end): `auto` falls back to lumped then.
Lumped MoM ports are the closest to openEMS's (both include the port's own parasitics); wave ports are
the closest to the line model (an ideal reference plane). Differential pairs: wave ports use mom25d's
coupled-mode fit (`P1d` / `P1c`); lumped legs are converted like openEMS's.

<a id="antenna-mode"></a>
## Antenna mode

`mode = "antenna"` with `--solvers mom` gives openEMS's antenna results from MoM: the input match, then the
far field of the driven port's currents at 11 frequencies over the band and the pattern frequency
(`--ff-freq`, else the resonance): directivity, efficiency, gain and realized gain over frequency, the
xz / yz / xy cuts and a sphere, as `<name>_mom25d_antenna.json`, `_mom25d_antenna.png`,
`_mom25d_pattern.png` and `_mom25d_sphere.npz` (the GUI's Antenna tab reads them). Ports are lumped unless
`--mom-port wave`: the accepted power is the 1 V gap source's.

`mom25d.farfield` takes each layer's current, Fourier transformed at k_rho = k0 sin(theta), through the
stack's TM / TE transmission-line voltages to the top surface (E_u = -V^e J_u, E_v = -V^h J_v) and from
there to the far field (r E_theta = j k0 / 2 pi E_u, r E_phi = j k0 cos(theta) / 2 pi E_v). Checked
against a dipole over a ground plane (shape and size to 1e-9), and on a lossless patch: efficiency 88.8 %,
the rest the surface wave a 1.6 mm FR4 substrate guides away.

The model is the board over an **infinite** ground: nothing radiates below it, there is no board edge.
That suits patches and arrays on a ground larger than they are; an inverted-F, a monopole at the board
edge or anything whose radiation comes from the finite ground is not this model's antenna (openEMS is).
Via currents are left out of the far field.

| `antenna_patch.toml` | resonance | S11 | Zin | -10 dB band | efficiency | directivity | gain | time |
|---|---|---|---|---|---|---|---|---|
| mom25d (2 mm cells) | 2.450 GHz | -31.4 dB | 48.2 + j1.9 ohm | 2.3 % | 36.1 % | 6.39 dBi | 1.97 dBi | 20 s |
| openEMS | 2.4375 GHz | -32.3 dB | 47.9 + j1.2 ohm | 2.6 % | 38.3 % | 7.04 dBi | 2.87 dBi | 81 s |

openEMS sees the board's 80 mm ground; MoM an infinite one.

<a id="current-dumps"></a>
## Current dumps

`--mom-dump 2.4,5` solves the currents at those frequencies after the sweep (one EM solve each) and writes
the surface current density J (A/m) of each meshed layer, driven by mom25d's 1 V gap source at the port
(`--dump-port`, default the first). J is sampled onto a grid a third of `--mom-dmax` apart (the value of
the triangle each point falls in; 0 off the copper) and stored like an openEMS frequency-domain plane dump
(`<out>/mom25d/exc_<port>/dump_J_<layer>_fd.npz` and `dump_info.json`), so the same code draws it: a peak
\|J\| map per frequency, with `--dump-animate gif|mp4` one period of Re(J·e^jφ), the standing waves
([post-processing](postprocessing.md#animations)), and the GUI's Fields tab lists it next to openEMS's
dumps.

```bash
kicad2ems examples/rf_stubnotch.toml --solvers mom --mom-dump 2.4,1.2 --dump-animate gif   # ~22 s
```

At the notch (2.4 GHz) the first quarter-wave stub stands: its current is largest at the tee and zero
at the open end, and little reaches P2; at 1.2 GHz the wave runs through. MoM has currents on the copper
only, not fields in space (those are openEMS's `--dump E@...`).

<a id="meshers"></a>
## Meshers

mom25d meshes each copper layer into triangles (RWG basis). The surface current is singular across a
conductor edge (~1/√d) and smooth along it, so what matters is **thin cells along the edges**. Four
meshers, same contract (port cuts and via fans conformal), selectable in mom25d (`Simulation(mesher=)`,
`mom25d --mesher`; kicad2ems `--mom-mesher triangle|gmsh`):

| mesher | edge refinement | |
|---|---|---|
| `polygon` (Triangle, default; `tensor` for Manhattan layers without vias) | inward offset rings (cosine-graded, `ncross`), constrained Delaunay | |
| `gmsh` / `gmsh-rings` | gmsh boundary layer; falls back to the same offset rings embedded by an OpenCASCADE fragment | `pip install gmsh` |
| `gmsh-graded` | isotropic: size from w/ncross at the edge to dmax (Distance + Threshold fields) | `pip install gmsh` |

**Study** (`tools/mom25d/mesher_study.py`, results in `tools/mom25d/results/`): the coupled-line
filter, lumped ports, 6 frequencies over 2–5 GHz, against a reference of 23k unknowns (Triangle, 0.1 mm,
ncross 10):

![accuracy per unknown](https://github.com/lowprocess/mom25d/raw/main/docs/mesher_study.png)

| mesher | unknowns | max \|ΔS\| | \|S21\| error (dB, where > −30 dB) |
|---|---|---|---|
| Triangle 0.3 mm, ncross 4 / 6 / 8 / 10 | 2.7k / 4.4k / 6.3k / 8.0k | 0.157 / 0.070 / 0.034 / 0.014 | 2.4 / 0.60 / 0.16 / 0.16 |
| Triangle ncross 6, dmax 0.4 / 0.2 / 0.15 mm | 3.5k / 6.2k / 9.4k | 0.074 / 0.063 / 0.062 | 0.57 / 0.58 / 0.57 |
| gmsh-rings 0.3 mm, ncross 4 / 6 / 8 / 10 | 3.6k / 5.0k / 7.3k / 8.3k | 0.156 / 0.070 / 0.033 / 0.151 | 2.4 / 0.61 / 0.15 / 3.3 |
| gmsh-graded (best of 4) | 10.3k | 0.28 | 5.4 |
| distmesh, ncross 4 / 6 | 7.2k / 13.5k | 0.33 / 0.16 | 7.2 / 2.9 |
| distmesh-rings 0.3 mm, ncross 6 | 4.4k | 0.070 | 0.59 |

- **The edge resolution (`ncross`) sets the accuracy**, not the cell size along the copper: halving dmax
  barely changes the error, each step of ncross halves it. Anisotropic edge cells beat isotropic
  refinement by 2-4x in error at a third of the unknowns (isotropic cells pay the small edge size along
  the edge too).
- **gmsh** gives nicer interior triangles and cleaner via fans but no gain per unknown; its 2D boundary
  layer cannot recover edges where a via fan sits in the layer band (lumped ports on pads), hence the
  fallback to embedded offset rings, and at fine settings the fragment produced occasional bad meshes
  (ncross 10). It costs 0.1–1 s per layer. Worth keeping as an option for awkward geometry; not as default.
- **distmesh**: isotropic node relaxation, so the same verdict as gmsh-graded; inside the offset rings it
  equals Triangle for narrow traces (no room to relax) and only differs on wide copper (planes). With no
  gain anywhere, the DistMesh mesher was removed after this study.
- The reference is a Triangle mesh, which flatters Triangle at ncross 10 slightly.

**What the meshers produce** (`tools/mom25d/mesher_gallery.py`; dmax 0.3 mm, ncross 6, the cases run
one after the other so the times compare): the feed tee of the filter, |J| at 3.25 GHz, the triangle
quality, |J| across two traces and |S21|:

![mesher gallery](https://github.com/lowprocess/mom25d/raw/main/docs/mesher_gallery.png)

- **Triangle / gmsh-rings / distmesh-rings** put a band of thin cells (aspect ratio 5–30) along every copper
  edge and leave a few large cells in the middle of the trace, where the current is flat. gmsh's interior
  is a symmetric diamond pattern, Triangle's a strip of long cells; the solution is the same (4.4–5.0k
  unknowns, |S21| within 0.02 dB of each other, -20 dB skirt 5 MHz from the reference).
- **gmsh-graded / DistMesh** make near-equilateral cells everywhere (median minimum angle 48° against 15°),
  which is what a FEM mesher aims for, but here the cells must be small along the edge as well as across it:
  2.9× the unknowns (25 triangles along 0.8 mm of trace edge against 6), and still the edge cells reach
  60–70 µm into the 0.35 mm trace against 23 µm for the ring meshes (reference 8.5 µm), so the edge peak
  of the current is averaged away (0.28–0.33 of the reference maximum, against 0.43 for the ring meshes and
  0.68 for the reference). The missing edge current shows up as a lower peak (−5.04 dB vs −4.93 dB) and the
  upper skirt 10–15 MHz low.
- Cost: 13k unknowns take ~15× the solve time of 4.4k (O(N³) LU), so the isotropic meshes are both slower and
  less accurate here: 158–182 s against 10–18 s for the sweep.
- The study found two solver issues, now fixed in mom25d: with lumped ports only, the edge-grading width
  was a quarter of the layer's extent (3.2 mm for 0.35 mm traces: much too coarse) - now the narrowest
  conductor's width (2·area/perimeter); and the mixed-precision direct solver could return garbage at low
  frequency on fine meshes (GMRES refinement unconverged, silently) - it now checks the residual and
  re-solves in double precision.

<a id="runtime"></a>
## Runtime against openEMS

`tools/bench_mom_openems.py` runs every example board once with `--mom` (one converted geometry for both
solvers) and tabulates both solvers' wall time and their agreement. 24 logical CPUs, 31 GB RAM, openEMS
0.37 (rc1) with its default threads, mom25d with the compiled core, Triangle mesher, `mom_dmax` 0.3 mm:

| board | FDTD cells | openEMS | MoM unknowns | MoM (EM solves) | MoM / openEMS | max \|ΔS\| | Δ dB (S21) | Δ delay |
|---|---|---|---|---|---|---|---|---|
| filter (coupled-line band-pass, microstrip) | 0.40 M | 126 s | 4,400 | 22 s (24) | **0.17×** | 0.27 | 8.3 | – |
| hairpin (band-pass, microstrip) | 0.27 M | 41 s | 4,763 | 8 s (13) | **0.20×** | 0.10 | 4.1 | – |
| demo_lowpass (L-C-L parts, microstrip) | 0.19 M | 12 s | 1,896 | 15 s (11) | 1.2× | 0.039 | 4.6 | – |
| demo_transition (microstrip → via → stripline, 2 inner planes) | 0.52 M | 33 s | 13,539 | 239 s (16) | 7× | 0.049 | 0.25 | 0.8 ps |
| sma_launch (3 plane layers, 185 vias, edge walls) | 0.77 M | 18 s | 17,073 | 871 s (16) | 48× | 0.12 | 0.20 | 3.1 ps |

(MoM times include the mesh, the port characterisation and the adaptive sweep; openEMS one excitation.)

- **MoM wins on single-layer microstrip** (filters, couplers, matching networks): a few thousand unknowns,
  a dozen or two solves, 5× faster than FDTD, and no time-step / ring-down issue for high-Q structures
  (openEMS needed 243k steps for the filter).
- **FDTD wins as soon as planes have to be meshed**: MoM's cost grows with the copper area (unknowns,
  O(N²) memory, O(N³) LU per frequency), FDTD's with the volume; inner planes, ground pours and hundreds of
  vias (edge walls become via rows; each via chain costs a kernel evaluation per frequency) make MoM 7-50×
  slower. An infinite-ground model of the outer plane is free; every other plane is not.
- **Agreement**: where both resolve the structure the results agree to ~0.05 in |S| and 0.2 dB / 1-3 ps in
  the transmissions (transition, SMA launch). The large dB columns come from the skirts and stop bands:
  the largest differences sit at -30 to -40 dB (filter 8.3 dB at 3.45 GHz on its steep upper skirt,
  hairpin 4.1 dB at 3.61 GHz, low-pass 4.6 dB at 1.98 GHz in its stop band), where a few tens of MHz of
  pass-band shift, or openEMS's box / edge floor, move |S21| by several dB. In the -3 dB pass bands:
  filter within 2.1 dB (peak openEMS -6.5 dB, MoM -4.9 dB: openEMS's κ loss model is ~1.4x MoM's tan δ at
  the pass band, see above), hairpin 1.2 dB, low-pass 0.23 dB.
- The benchmark first ran with mixed precision by default; a stalled refinement on the SMA launch took
  2530 s (now 871 s): mom25d now uses double precision whenever it fits.

---
[← GUI](gui.md) · [Index](index.md)
