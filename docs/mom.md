# 14. MoM reference (mom25d)

[← GUI](gui.md) · [Index](index.md)

`--mom` solves the board a second time with **mom25d**, a 2.5D (planar) Method-of-Moments solver, from the
very file openEMS gets: `<name>.xml`, CSXCAD's geometry format (the one AppCSXCAD shows). FDTD, MoM and
the transmission-line model then all start from one converted board, and the three results are compared
in the plots, the report and the GUI.

```bash
kicad2openems examples/filter.toml --preview --mom      # line model + MoM, no openEMS run: ~20 s
kicad2openems examples/filter.toml --mom                # openEMS too, all three compared
mom25d out/filter.xml --fmin 1 --fmax 8                 # mom25d on its own, on any CSX file
```

| Output | |
|---|---|
| `<name>_mom.sNp` | MoM S-parameters, same ports, names, mixed-mode pairs and reference impedances as openEMS |
| `<name>_mom.png` | with `--preview`: MoM, the line model dotted |
| `<name>_openems.png` | MoM dash-dotted over openEMS |
| report, *MoM reference* | unknowns, solves, time, ports; MoM against openEMS and against the line model (largest \|ΔS\|, transmission dB, delay) |

## What MoM sees

mom25d models metal as zero-thickness sheets on the interfaces of a **laterally infinite** dielectric stack
over an **infinite ground plane**; vias are vertical wires. Radiation and surface waves are included; the
board edge, the air box and its boundaries are not, so there are no box resonances (the ripple in an
openEMS stop band) and no edge fringing. The ground is the `ground` layer (or the lowest solid plane);
copper below it is left out. Antenna mode is not supported (no ground plane under the copper).

Planes on other layers are meshed (3 × `mom_dmax`, refined near the copper of nearby layers); a board
with large inner planes has many unknowns ([runtime](#runtime)).

## The CSX file and the hints

`mom25d.csx.CSXModel` reads any CSX file: Material slabs (εr, κ), planar Metal / ConductingSheet
polygons and boxes, via cylinders, vertical metal sheets (edge walls: rows of vias every 0.5 mm), lumped
elements (series / parallel R-L-C across a cut, bridged by a metal strip) and openEMS lumped ports
(`port_resist_<n>`). What the file cannot say, kicad2openems adds as an element the CSXCAD reader ignores:

```xml
<mom25d version="1" ground="B.Cu" f_loss="4.5e9">
  <Layer name="F.Cu" z="0.2"/>
  <Dielectric name="dielectric 1" z0="0" z1="0.2" er="4.4" tand="0.011"/>
  <Port number="1" name="P1" layer="F.Cu" ref_layer="B.Cu" x="1.09" y="-13.9" dx="1" dy="0" width="0.35" z0="50"/>
</mom25d>
```

- **Loss tangents:** the CSX file holds a conductivity κ that openEMS fits at the band centre; the hint
  gives MoM the stackup's real tan δ (constant over frequency). Without hints tan δ = κ / (2π f ε0 εr)
  at `f_loss`. This is one real difference between the two results: in a narrow-band filter openEMS's
  loss is higher below the band centre (filter example: ~1 dB at 3.3 GHz with the centre at 4.5 GHz).
- **Ports:** name, layer, reference layer, feed direction (into the circuit) and width. Without hints:
  `P<n>`, the direction from the side of the lumped-port sheet that has copper, the reference from the
  copper at the sheet's ends.

Every openEMS run writes the hints into `<name>.xml` too, so `mom25d <name>.xml` works on any earlier run.

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

<a id="meshers"></a>
## Meshers

mom25d meshes each copper layer into triangles (RWG basis). The surface current is singular across a
conductor edge (~1/√d) and smooth along it, so what matters is **thin cells along the edges**. Four
meshers, same contract (port cuts and via fans conformal), selectable in mom25d (`Simulation(mesher=)`,
`mom25d --mesher`; kicad2openems `--mom-mesher triangle|gmsh`):

| mesher | edge refinement | |
|---|---|---|
| `polygon` (Triangle, default; `tensor` for Manhattan layers without vias) | inward offset rings (cosine-graded, `ncross`), constrained Delaunay | |
| `gmsh` / `gmsh-rings` | gmsh boundary layer; falls back to the same offset rings embedded by an OpenCASCADE fragment | `pip install gmsh` |
| `gmsh-graded` | isotropic: size from w/ncross at the edge to dmax (Distance + Threshold fields) | `pip install gmsh` |
| `distmesh` / `distmesh-rings` | DistMesh node relaxation (Persson & Strang) with an isotropic size function, or inside the offset rings; constrained Delaunay of the nodes | numpy / scipy |

**Study** (`mom25d/benchmarks/mesher_study.py`, results in `mom25d/benchmarks/results/`): the coupled-line
filter, lumped ports, 6 frequencies over 2–5 GHz, against a reference of 23k unknowns (Triangle, 0.1 mm,
ncross 10):

![accuracy per unknown](../mom25d/docs/mesher_study.png)

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
  equals Triangle for narrow traces (no room to relax) and only differs on wide copper (planes).
- The reference is a Triangle mesh, which flatters Triangle at ncross 10 slightly.
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
