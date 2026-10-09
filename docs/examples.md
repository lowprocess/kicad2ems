# 9. Examples

[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)

All example boards except `filter.kicad_pcb` are **generated** (no KiCad needed) and git-ignored; each has
a config in `examples/` whose paths are relative to it. Run from the repository root:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.toml
```

Results land in `examples/out/`. Reference results for the transition and the microstrip filter are in
`examples/reference/` (use with `--compare`).

## Generators

| Generator | Variant (2nd argument) | Board |
|---|---|---|
| `make_demo_board.py out` | (none) | 4-layer: F.Cu microstrip → blind via → In2.Cu stripline, GND plane with antipad, stitching vias |
| `make_demo_board.py out diff` | `diff` | edge-coupled differential microstrip pair with a 45° jog (ports `P1±`, `P2±`) |
| `make_demo_board.py out rlc` | `rlc` | microstrip with a gap bridged by an 0402 1.5 pF capacitor, plus a DNP part and an unreadable value (skip notes) |
| `make_demo_board.py out lowpass` | `lowpass` / `highpass` / `bandpass` | lumped ladder filters (0402 parts with ESR / ESL fields) and `<out>_ideal.s2p`, the ideal circuit |
| `make_antennas.py KIND out [param=mm …]` | `patch` / `ifa` / `dipole` / `slot` | 2.45 GHz antennas on FR-4; dimensions are keyword parameters of each function |

The filter ladders are defined in `LADDERS` in `make_demo_board.py` (stages `('series', part)` or
`('shunt', [parts])`), so new topologies are a few lines; `ladder_ideal` computes the ideal circuit by
ABCD matrices.

## Verified results

Runs with openEMS 0.37 (see the commit history for the details of each check).

**Lumped filters** (`demo_*.toml`): the simulation differs from the ideal circuit by the layout's
parasitics, which is the point of simulating them:

| Filter | Ideal circuit | Simulated | Cause |
|---|---|---|---|
| low-pass, fc 1 GHz | −3 dB at 0.87 GHz, C2 notch 2.73 GHz | 0.77 GHz, notch 2.07 GHz | ~0.4 nH of pad / via inductance in the shunt branch |
| high-pass, fc 1 GHz | — | cutoff ~15 % lower | via path in series with the shunt L |
| band-pass, 1 GHz | peak 0.88 GHz | 0.74 GHz | +0.5 nH per shunt branch reproduces it in the ideal circuit |

**Antennas** (`antenna_*.toml`):

| Antenna | Resonance | S11 | −10 dB band | Directivity | Efficiency |
|---|---|---|---|---|---|
| patch | 2.438 GHz | −19.4 dB | 2.2 % | 7.0 dBi | 39 % |
| IFA | 2.451 GHz | −33.5 dB | 12.0 % | 2.9 dBi | 91 % |
| dipole | 2.430 GHz | −20.3 dB | 15.6 % | 2.1 dBi | 96 % |
| slot | 2.434 GHz | −26.9 dB | 20.6 % | 4.4 dBi | 87 % |

The dipole's 2.1 dBi matches the ideal half-wave dipole (2.15 dBi). Tuning notes: the IFA is fed at the
ground edge (a 3 mm feed microstrip kept the feed point too far from the short to match); the slot is
fed 16 mm off-centre (at the centre its impedance is far above 50 Ω).

---
[← Post-processing](postprocessing.md) · [Index](index.md) · [Next: Testing →](testing.md)
