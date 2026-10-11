# 10. Examples

[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)

All example boards except `filter.kicad_pcb`, `hairpin.kicad_pcb`, `sma_launch.kicad_pcb` and the
[RF structures](#rf-structures) (`rf_*.kicad_pcb`, generated but kept in the repository) are
**generated** (no KiCad needed) and git-ignored. Most
have a config in `examples/` whose paths are relative to it. Run from the repository root:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2ems examples/demo_transition.toml
```

Results land in `examples/out/`.

## Generators

`make_demo_board.py OUT [VARIANT]` writes `OUT.kicad_pcb` and `OUT-top.pos`; an unknown variant is
rejected.

| Variant | Board | Config |
|---|---|---|
| `transition` (default) | 4-layer: F.Cu microstrip → blind via → In2.Cu stripline, In1.Cu GND plane with an antipad, stitching vias | `demo_transition.toml` |
| `diff` | 2-layer edge-coupled differential microstrip pair with a 45° jog (ports `P1±`, `P2±`); no ground copper of its own | none: run with `--ground B.Cu` |
| `rlc` | microstrip with a gap bridged by an 0402 1.5 pF capacitor, plus a DNP part and an unreadable value (skip notes) | none |
| `lowpass` / `highpass` / `bandpass` | lumped ladder filters (0402 parts with ESR / ESL fields), plus `OUT_ideal.s2p`, the ideal circuit | `demo_lowpass.toml`, … |
| `launch` | 4-layer edge launch: grounded-CPW feed, a taper drawn as copper in the connector footprint, the F.Cu pour opening out at 45°, inner planes cut away under a connector pad drawn as two pads numbered 1, P2 on the pad at the board edge | none: `--nets RF,GND --fmin 1 --fmax 6` |

```bash
python examples/make_demo_board.py examples/diff diff
kicad2ems examples/diff.kicad_pcb --ground B.Cu --fmin 1 --fmax 8 --preview
python examples/make_demo_board.py examples/demo_rlc rlc
kicad2ems examples/demo_rlc.kicad_pcb --fmax 6 --preview
```

The filter ladders are defined in `LADDERS` in `make_demo_board.py` (stages `('series', part)` or
`('shunt', [parts])`), so new topologies are a few lines; `ladder_ideal` computes the ideal circuit by
ABCD matrices.

`make_antennas.py KIND OUT [param=mm …]` writes the antennas `patch`, `ifa`, `dipole` and `slot` (one
config each, `antenna_<kind>.toml`). Their dimensions are keyword parameters of each function, also on
the command line (`make_antennas.py ifa out arm=19 s=2.5`).

<a id="rf-structures"></a>
## RF structures

`make_rf_examples.py {branchline,wilkinson,steplpf,stubnotch,gcpw,coupler,all} [OUT]` writes classic
structures on 0.8 mm FR-4 (εr 4.4, tan δ 0.02) over a B.Cu ground, sized with the package's own line
formulas (`tlines.microstrip`, `tlines.cpwg`, `coupled.coupled_microstrip`). The boards and their configs
(`rf_<name>.toml`, each with a `fine` mesh for its narrowest feature and the qucsator line model) are in
`examples/`; `examples/reference/rf_<name>_openems.s<n>p` are their openEMS results. All of them open from
the GUI's *File* → *Examples*.

| Board | What it tests | openEMS | mom25d | line model |
|---|---|---|---|---|
| `rf_branchline` — 3 dB 90° hybrid, 2.4 GHz, 4 ports | a closed loop of lines, tees, 35 / 50 Ω arms | S21 / S31 −3.77 / −3.34 dB, −139° / 133° | −3.57 / −3.39 dB | −3.46 / −3.38 dB |
| `rf_wilkinson` — divider, 70.7 Ω arms, 100 Ω 0805 | a lumped resistor between outputs; P1 and P2 driven | S21 −3.35 dB, S22 −18.6 dB, S32 −18.5 dB | −3.31, −20.0, −38.9 dB | −3.30, −18.4, −35.7 dB |
| `rf_steplpf` — 5-pole stepped-impedance low-pass, 2 GHz | 100 Ω / 20 Ω steps (0.35 / 5.55 mm) | −3 dB at 1.765 GHz | 1.765 GHz | 1.806 GHz |
| `rf_stubnotch` — two open λ/4 stubs, 2.4 GHz | open ends, tees | notch 2.384 GHz | 2.411 GHz | 2.494 GHz |
| `rf_gcpw` — grounded CPW, via fences | coplanar ground pours, 38 vias | S11 −18.9 dB, S21 −0.44 dB at 5 GHz | −30.1, −0.32 dB (wave ports) | −21.0, −0.35 dB |
| `rf_coupler` — 10 dB edge-coupled λ/4 coupler, 2.4 GHz | a 0.14 mm coupled gap, 45° bends | S31 −8.6 dB, S21 −1.05 dB | −10.9, −0.67 dB | −10.4, −0.72 dB |

openEMS runs take 4–34 s each (0.06–0.28 M cells); the full runs with `--mom` under a minute. Where they
disagree, the examples are doing their job:

- **Wilkinson isolation**: openEMS −18.5 dB against −36 to −39 dB from the two others, which treat the
  resistor as an ideal 100 Ω. openEMS's lumped resistor fills the 1 mm gap between the pads, so the pads
  and the gap add parasitics; not yet checked whether that explains all of the difference (an open
  question for this example).
- **Coupler**: openEMS couples ~2 dB more strongly. The likely cause: the gap is 0.14 mm and the copper
  35 µm thick, which openEMS models, while mom25d and the coupled-line formulas use zero-thickness strips
  and so miss the sidewall capacitance across the gap (lower odd-mode impedance, stronger coupling).
- **Stubs drawn as rectangles**: the stubs are tracks. Drawn as filled rectangles overlapping the line, the
  line model took the overlap for an 8 Ω section along the path and put the notch at 3.5 GHz (MoM and
  openEMS: 2.4 GHz) - a known limitation of the line extraction.

<a id="the-hairpin-filter"></a>
## The hairpin filter

`hairpin.kicad_pcb` is a 3.3–3.4 GHz band-pass filter: three hairpin resonators and two tapped feeds
drawn as copper polygons on 0.21 mm FR-4 (εr 4.4, tan δ 0.02), 15 × 15 mm, solid ground on B.Cu.
`hairpin.toml` previews it with the line model and qucsator (corners, tees, open ends and the two
coupled pairs between the hairpins); `examples/reference/hairpin_openems.s2p` is its openEMS result.

<a id="the-sma-launch"></a>
## The SMA launch

`sma_launch.kicad_pcb` is a KiCad 10 board: a Molex 73251-1151 end-launch SMA jack (J7) onto a
50 Ω line (net `REF_SMA_N`) on a 4-layer board, with a taper drawn as copper polygons in the connector
footprint and two ports (`TP1` at the far end, `TP2` on the connector pad). The ground planes end at the
board edges with no stitching there, so `sma_launch.toml` closes them with
[edge walls](meshing.md#edge-walls) (`edge_walls = "all"`): without them the plane pair beyond the last
via row resonates near 4.7 GHz. It runs from 1 to 6 GHz to −30 dB, about a minute of openEMS
(16 k steps). The embedded 3D models were removed from the board file.

| 1–6 GHz | worst S11 | S21 at 6 GHz |
|---|---|---|
| openEMS (`reference/sma_launch_openems.s2p`) | −10.2 dB at 5.6 GHz | −1.02 dB |
| line model, qucsator (`reference/sma_launch_fast.s2p`) | −13.2 dB at 5.56 GHz | −0.68 dB |

The line model takes the taper and the pad steps but not the connector body, hence the shallower
match at the top of the band.

<a id="the-microstrip-filter"></a>
## The microstrip filter

`filter.kicad_pcb` is a KiCad 10 board: an interdigital microstrip filter on 0.2 mm FR-4 (εr 4.4, tan δ
0.011), its resonators drawn as copper rectangles, fed by 0.35 mm microstrip; `filter.toml` runs it from
1 to 8 GHz with a 0.05 mm fine cell. Its five fingers become strips grounded by their vias, side by
side in a five-line [coupled section](transmission-lines.md#coupled); `fast = "qucsator"` solves them.

## Verified results

Runs with openEMS 0.37.

**Lumped filters** (`demo_*.toml`). The low-pass is a 3rd-order Butterworth design (1 GHz, 50 Ω) of
0402 parts on microstrip, a T network: L1 8.2 nH in series, C2 6.8 pF to a GND pad with two vias, then
L3 8.2 nH in series. The parts carry KiCad fields for their parasitics: `sim.esr` 0.4 Ω on the
inductors; `sim.esr` 0.1 Ω and `sim.esl` 0.5 nH on the capacitors. The high-pass is C 3.3 pF – L 3.9 nH
to GND – C 3.3 pF; the band-pass is series L 27 nH + C 1 pF, then shunt L 2.2 nH ∥ C 12 pF. Each config
overlays the ideal circuit (`compare`), so the plot shows what the layout adds:

| Filter | Ideal circuit | Simulated | Cause |
|---|---|---|---|
| low-pass, fc 1 GHz | −3 dB at 0.87 GHz, C2 notch 2.73 GHz (its ESL) | 0.77 GHz, notch 2.07 GHz | ~0.4 nH more in the shunt branch: the stub to the pad, the GND pad and its vias |
| high-pass, fc 1 GHz | — | cutoff ~15 % lower | via path in series with the shunt L |
| band-pass, 1 GHz | peak 0.88 GHz | 0.74 GHz | +0.5 nH per shunt branch reproduces it in the ideal circuit: the 2.2 nH shunt inductor is small enough that pad and via inductance detune the filter by 25 % |

**Antennas** (`antenna_*.toml`), all for 2.45 GHz on FR-4 (εr 4.4, tan δ 0.02):

| Antenna | Geometry | Feed |
|---|---|---|
| `patch` | inset-fed patch, 37.2 × 28.8 mm, 1.6 mm FR-4, B.Cu ground | 50 Ω microstrip from the board edge |
| `ifa` | inverted-F (arm 17.5 mm, 5 mm high) above the top edge of a 40 × 48 mm ground plane, shorting leg via to ground | at the ground edge, 3 mm from the short |
| `dipole` | printed half-wave dipole, 2 × 23.3 mm arms, 0.8 mm FR-4, no ground | gap port, 73 Ω |
| `slot` | 52 × 2 mm slot in the B.Cu ground of a 60 × 60 mm board | microstrip crossing 16 mm off-centre, λ/4 open stub |

| Antenna | Resonance | S11 | −10 dB band | Directivity | Efficiency | Gain |
|---|---|---|---|---|---|---|
| patch | 2.438 GHz | −19.4 dB | 2.2 % | 7.0 dBi | 39 % | 2.9 dBi |
| IFA | 2.451 GHz | −33.5 dB | 12.0 % | 2.9 dBi | 91 % | 2.5 dBi |
| dipole | 2.430 GHz | −20.3 dB | 15.6 % | 2.1 dBi | 96 % | 1.9 dBi |
| slot | 2.434 GHz | −26.9 dB | 20.6 % | 4.4 dBi | 87 % | 3.7 dBi |

The dipole's 2.1 dBi matches the ideal half-wave dipole (2.15 dBi), with its doughnut pattern and nulls
along its axis. The patch radiates broadside (+z); its 39 % efficiency is what FR-4 loss does to a
narrowband patch. The IFA is nearly omnidirectional. Tuning notes: the IFA is fed at the ground edge (a
3 mm feed microstrip kept the feed point too far from the short to match); the slot is fed 16 mm
off-centre (at the centre its impedance is far above 50 Ω).

## Reference results

`examples/reference/` holds the outputs of the demo transition and the microstrip filter (Touchstone,
plots, JSON summary, CSX geometry), regenerated with this version. The transition runs with the
converged mesh settings in its config (fine 0.025 mm, nz 6; [why](meshing.md#validation-the-transition-demo))
and also holds its line model (`demo_transition_fast.s2p`, `demo_transition_tlines.png`). Compare after
a change:

```bash
kicad2ems examples/filter.toml --compare examples/reference/filter_openems.s2p
```

---
[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)
