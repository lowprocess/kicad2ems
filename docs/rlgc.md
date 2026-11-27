# Line parameters: R, L, G, C over frequency

[← Time domain](timedomain.md) · [Index](index.md) · [Next: Whole-board studies →](segment-studies.md)

The line-parameters question gives the per-unit-length matrices of transmission lines: R(f) and L(f) with the
skin effect, the proximity effect between neighbours and the return current's own crowding in the planes, and
G(f) and C(f), for one line or N coupled lines. It does this for a cross-section you give by its sizes, or for
every distinct cross-section of a board's nets as the line model cuts the board. Each section gets W-element
models for a circuit simulator and S-parameters over its length. The code is `kicad2ems.core.rlgc`, on
`kicad2ems.core.lines.mqs`, the 2D eddy-current solver.

```sh
# a board's nets: each coupled group and each single trace, on the board's cut across it
python -m kicad2ems.core.rlgc examples/rf_ddr.kicad_pcb --nets 'DQ*' --fmax 20 --out out/rlgc
# a cross-section by its sizes: a microstrip pair, a stripline, or an xsection.CrossSection JSON file
python -m kicad2ems.core.rlgc --xs microstrip --w 0.15 --lines 2 --gap 0.15 --h 0.2 --er 4.3 --t 0.035 --out o
python -m kicad2ems.core.rlgc --xs stripline --w 0.1 --h 0.15 --h2 0.15 --er 3.7 --t 0.018 --out o
```

In a study the question is *Line parameters* (Signal integrity), answered by the field solver.

## Options

| Option | |
|---|---|
| `--nets` | the board's nets, name patterns (`DQ*`, `CLK_[PN]`), comma-separated |
| `--layers` | only sections on these copper layers |
| `--min-length` | a board's section shorter than this (a breakout) is left out, mm (1) |
| `--xs` | `microstrip`, `stripline` or a JSON file, with `--w --lines --gap --h --h2 --er --tand --t --mask --s` |
| `--fmin`, `--fmax` | the band, GHz (0.001 to 20), DC as well |
| `--per-decade` | frequencies a decade (5) |
| `--sigma` | copper conductivity, S/m (5.8e7) |
| `--rough` | copper roughness, µm RMS: the conductivity at each frequency is σ / K(f)², Hammerstad's K |
| `--diel` | `fixed` (er and tan δ as given) or `wideband` (each layer's Djordjevic-Sarkar curve through them at `--diel-fref` GHz: C and G solved at each frequency's materials) |
| `--length`, `--z0` | the Touchstone's length (a board section's own; 10 mm for a given cross-section) and reference |

## What each section gets

| File | |
|---|---|
| `<name>.rlgc` | the W-element's RLGC file (HSPICE `RLGCfile=`): Lo the external inductance, Co, Ro the DC resistance, Go 0, Rs and Gd of R(f) = Ro + √f (1 + j) Rs and G(f) = f Gd (Rs fitted where the skin is thinner than the copper) |
| `<name>_w.inc` | the W-element's tabular model (`MODELTYPE=TABLE`): `sp` models of L, C, R and G at every frequency, DC first; a W element takes `TABLEMODEL=<name>` |
| `<name>.s<2N>p` | S over its length: ports 1..N at the near ends, N+1..2N at the far ends |
| `rlgc_<name>.png` | R and L of each line over frequency (the 12 longest sections) |

`rlgc.json` has every section: its nets, layer, length, cross-section, the matrices at every frequency, its
lossless impedance (a pair's differential and common) and each mode's attenuation at the top frequency. On a
board, sections of one net (or one group of nets) on one layer with the same cross-section are one, with their
lengths added. A cross-section and its mirror image are solved once (a pair's two breakouts). The reference copper
is every net that is not a routed signal, as the KiCad plugin's impedance map has it. The planes and the other
layers' copper get their own layers' thickness.

## How R and L are solved

The field is A_z(x, z), the magnetic vector potential along the lines: −∇·(∇A / µ0) = J, with J = σ(E_g − jωA) in
conductor g. E_g, the field along a conductor (its voltage drop per metre), is one number per signal conductor, and
one for all the ground copper together: the planes, coplanar ground and other layers' copper are one net, joined
at the line's ends. Each conductor's total current is imposed, and the solve gives E for it. Z, the N × N series
impedance with the ground as the return, is then −dV_k/dl = E_k − E_ground = Σ Z_kj I_j.

The elements are xsection's bilinear rectangles with its hanging-node reduction, on a mesh of their own. Every
copper face is a key line. Cells inside the copper are halved across a face until they are a third of a skin depth
thick there (at the highest frequency), growing by 0.35 of their distance from it, along the face as across it. In
the air, cells grow from the copper's corners as xsection's do. K + jωσM is factored once at each frequency (complex
symmetric with a positive definite real part, so no pivoting), on threads. At DC the current is uniform in each
conductor.

L is xsection's L_inf = µ0ε0 C_air⁻¹ (extrapolated from two meshes, as its C is) plus the internal inductance. The
internal inductance is the eddy-current solve's L(f) less its own perfect-conductor limit on the same mesh, so the
two air meshes' errors cancel. The lossless line's Z0 and delay are therefore the field solver's.

A plane is copper of its layer's thickness behind its face, across the box. Copper beyond the planes nearest the
lines is left out (shielded). At DC a plane's current spreads over the box's width (24 heights either side), so
the lowest frequencies' L, below a few hundred kHz, is that of a plane that wide.

## Checks

| Check | Result |
|---|---|
| a coax (a 1 mm, b 2 mm, c 2.5 mm, staircases a / 16 high) against Bessel's internal impedances of the wire and the tube, DC to a / δ = 5 | R within 0.55 %, L within 0.2 % (the staircase's own error) |
| a strip's DC resistance | 1 / (σ w t) and the plane's across the box, exactly |
| a 50 Ω microstrip's high-frequency limit | (L − L_inf) ω / R = 0.985 to 1.005 at 1 to 10 GHz; L → L_inf from above |
| Wheeler's incremental inductance on the electrostatic solver (each surface receded 2 µm) | R(10 GHz) 64.9 against 65.6 Ω/m; the strip's share 0.741 against 0.747 |
| a centred stripline's conductor loss against the closed form from Wheeler's rule (Pozar 3.198), w 0.15, 0.3, 0.6 mm | within 0.6, 1.5 and 1.6 % |
| a microstrip pair | R and L symmetric and positive definite, R(10 GHz) / R(2.5 GHz) 2.02 (√4 = 2; the corners crowd a little more as δ shrinks) |
| the losses | each conductor's loss summed is R, to 1e-9 |

The 50 Ω microstrip of [mom25d's thickness-loss table](mom.md#thickness-loss) (w 0.383 mm on 0.2 mm, εr 4.4,
35 µm, 5.8e7 S/m) at 10 GHz has R = 64.9 Ω/m: the strip's 48.1 and the plane's 16.8. That is 0.00589 dB/mm at
Z0 47.8 Ω (the strip's 0.00437), or 0.061 dB over 10.4 mm (the strip's 0.046). Hammerstad and Jensen's formula, as
scikit-rf's MLine has it (Rs / (Z0 w) · e^(−1.2 (Z0/η0)^0.7), the line model's microstrip loss until now), gives
0.00935 dB/mm, 1.6× this. Pucel's formula gives 0.0050.

<a id="line-model"></a>
## Where the line model uses it

With `--sigma`, the line model's trace sections and coupled sections take their conductor loss (R(f) and the
internal inductance) from their cross-sections. That is the board's cut where the line model took one, else the
section as classified over ideal planes. Each distinct one is solved at 3 frequencies a decade across the band and
interpolated (R as log-log), when the model has at most 48 of them (`fastsolve.FIELD_LOSS_MAX`). With more, or
`fastsolve.FIELD_LOSS = False`, the formulas apply. The run's log says which. On the examples this adds 0.2–1.4 s
uncached (2–8 cross-sections) and a few ms on a repeat. The loss at 10 GHz falls from the formulas' to the cross
section's: rf_stripline 0.566 to 0.189 dB (2 Rs / w had been 3× a stripline's), rf_msvia 0.197 to 0.128, hairpin
1.02 to 0.69. qucsator keeps its own microstrip models' loss (MLIN, MCOUPLED, CLIN); its other sections take the
cross-section's R at the band centre.

## What it leaves out

- Dielectric dispersion unless `--diel wideband`; no magnetic materials (µ = µ0).
- Radiation and the planes' resonances: the lines are quasi-TEM, per unit length.
- A cross-section's copper beyond its nearest planes. The coplanar ground runs to the box.
- The line model's pads keep the formula's loss (they are short), and so do de-embedding's line sections.

Tests: tests/core/test_rlgc.py.
