# 10. Examples

[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)

All example boards except `filter.kicad_pcb` are **generated** (no KiCad needed) and git-ignored. Most
have a config in `examples/` whose paths are relative to it. Run from the repository root:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.toml
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

```bash
python examples/make_demo_board.py examples/diff diff
kicad2openems examples/diff.kicad_pcb --ground B.Cu --fmin 1 --fmax 8 --preview
python examples/make_demo_board.py examples/demo_rlc rlc
kicad2openems examples/demo_rlc.kicad_pcb --fmax 6 --preview
```

The filter ladders are defined in `LADDERS` in `make_demo_board.py` (stages `('series', part)` or
`('shunt', [parts])`), so new topologies are a few lines; `ladder_ideal` computes the ideal circuit by
ABCD matrices.

`make_antennas.py KIND OUT [param=mm …]` writes the antennas `patch`, `ifa`, `dipole` and `slot` (one
config each, `antenna_<kind>.toml`). Their dimensions are keyword parameters of each function, also on
the command line (`make_antennas.py ifa out arm=19 s=2.5`).

<a id="the-microstrip-filter"></a>
## The microstrip filter

`filter.kicad_pcb` is a KiCad 10 board: an interdigital microstrip filter on 0.2 mm FR-4 (εr 4.4, tan δ
0.011), its resonators drawn as copper rectangles, fed by 0.35 mm microstrip; `filter.toml` runs it from
1 to 8 GHz with a 0.05 mm fine cell. Its coupled resonators are outside the
[line model](transmission-lines.md#limits): the line sections cover only the feed lines.

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
kicad2openems examples/filter.toml --compare examples/reference/filter_openems.s2p
```

---
[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)
