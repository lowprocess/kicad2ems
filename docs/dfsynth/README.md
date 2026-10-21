# dfsynth

dfsynth synthesises distributed-element microstrip filters from a spec (type, response, frequency,
bandwidth, substrate) and writes the layout as a KiCad board. It can then run that board through
[kicad2ems](https://github.com/lowprocess/kicad2ems) to check it with a field solver, and correct the
design from the result.

```bash
pip install -e tools/dfsynth                      # from the kicad2ems checkout; numpy + matplotlib
pip install -e .                                  # kicad2ems itself, for --validate (scipy: lowpass refinement)

dfsynth --type bandpass --topology coupled --f0 5G --bw 500M --order 5 \
        --substrate RO4003C-0.813 -o out/bpf --validate mom,lines --em-tune 1
```

That writes:

| file | what |
|---|---|
| `out/bpf.kicad_pcb` | the layout: KiCad 8 format, 2 layers, opens in KiCad 8 and later |
| `out/bpf.toml` | a kicad2ems run configuration for it (`kicad2ems out/bpf.toml --solvers mom,lines`) |
| `out/bpf_model.s2p`, `_model.png` | dfsynth's own circuit model of the layout |
| `out/bpf_design.json` | spec, substrate, every dimension, the targets and the validation metrics |
| `out/bpf_em/`, `_validation.png` | with `--validate`: kicad2ems's results, overlaid on the model |
| `out/bpf_em_tune1/`, `_validation_tune1.png` | with `--em-tune N`: each corrected round |

## Filters

| type | topology | notes |
|---|---|---|
| lowpass | `stepped` | alternating high / low impedance sections, L first |
| lowpass | `stub` | high-impedance lines with shunt open stubs (alternating sides) |
| highpass | `stub` | shunt short-circuited quarter-wave stubs: a wide band-pass from the cut-off to `--bw` above it (default up to 3x the cut-off), as any distributed high-pass is |
| bandpass | `coupled` | edge-coupled (parallel-coupled) half-wave resonators |
| bandpass | `hairpin` | the same, folded into alternating U resonators, coupled-line feeds |
| bandpass | `endcoupled` | gap-coupled half-wave resonators; narrow bands only (the end gaps get small fast) |
| bandpass | `stub` | Matthaei short-circuited stub band-pass, wide bands (30 to 100 %) |
| bandstop | `stub` | open quarter-wave stubs a quarter wave apart |

Responses: `--response chebyshev` (`--ripple` dB, default 0.1) or `butterworth`. The order comes from
`--order`, or from a stop-band point, `--atten 40@3.5G` (the lowest order with 40 dB at 3.5 GHz).
Ladders with equal terminations (lowpass, bandstop) need an odd Chebyshev order and get bumped to one.
The bandwidth is the ripple bandwidth for Chebyshev, the -3 dB bandwidth for Butterworth.

Substrates: `dfsynth --list` shows the presets (FR4, RO4003C, RO4350B, RT5880, RO3010, alumina in
common thicknesses); `--h`, `--er`, `--tand`, `--copper` override them. `--min-width` and `--min-gap`
(default 0.15 mm) only raise warnings: dfsynth never silently changes a dimension to meet them, except
that band-stop stubs too narrow to make are clamped to the minimum width (and the result says the stop
band comes out wider).

## How a design is made

1. **Synthesis**: low-pass prototype g-values; then the topology's classic design equations
   (stepped-impedance with the inductive lines' shunt parasitics taken off the capacitors; J-inverters for
   coupled, hairpin and end-coupled resonators; Matthaei's equations for the stub band-pass; quarter-wave
   stub band-stop). The ideal TEM network of each synthesis is checked in the tests.
2. **Physical dimensions** on the substrate: Hammerstad-Jensen microstrip with strip thickness and
   Kirschning-Jansen dispersion; Kirschning-Jansen coupled lines; Kirschning-Jansen-Koster open ends;
   Garg-Bahl series gaps (whose shunt capacitance is taken off each resonator end); Edwards' width-step
   extension; Goldfarb-Pucel via inductance on shorted stubs.
3. **Calibration**: dfsynth builds a circuit model of the actual layout (dispersion, loss, open ends,
   steps, gaps, vias) and iterates two factors, `kf` (all lengths) and `kb` (the design bandwidth), until
   the model's -3 dB edges land on the spec's. This is what fixes the approximations of step 1 (the
   hairpin's bends and short arms, the band-stop's narrow-band formula, ...).
4. **Validation** (`--validate`): kicad2ems solves the written board with the solvers named (`mom` is
   the usual choice for these planar filters; `gfdtd` runs well on a CPU too; `lines` is kicad2ems's own
   line model). dfsynth measures each result's band edges (3 dB below the pass-band peak, so loss does
   not move them) and reports centre and bandwidth error against the spec, pass-band loss and match.
5. **EM tuning** (`--em-tune N`): the full-wave result's centre / bandwidth error is folded back into `kf` /
   `kb`, the board is rewritten and solved again.

kicad2ems's solvers treat copper as infinitely thin; dfsynth designs for the copper thickness you give it.
On narrow lines that is a few percent of frequency, so dfsynth also models the layout with zero
thickness (`model (t=0)` in the plots and metrics), and the EM tuning only corrects the difference between
the full-wave result and that model: the board stays designed for real copper.

## Known limits

- Microstrip on one dielectric over a ground plane only (no stripline, no cover, no enclosure).
- Edge-coupled and hairpin filters with more than ~15 % bandwidth on thin substrates need gaps under
  0.1 mm in their first and last sections; a tapped feed would avoid that and is not implemented.
- End-coupled filters need very small end gaps for anything but narrow bands on thick, high-er substrates;
  dfsynth refuses designs whose end gap would have to be under 2 % of the strip width.
- The shorted stubs' vias are GND vias inside RF copper: KiCad's DRC flags them as shorts; they are meant
  to be (a net tie would silence it).
- T-junctions are modelled only as the main line's half width; the EM tuning takes care of the rest.

## Validation status (261021-r1, mom25d on CPU, one `--em-tune` round)

"Predicted" is mom25d's error over the zero-thickness line model applied to the model with 35 um copper:
the error expected of the real board against the spec.

| case | substrate | round 0 predicted (centre / bw) | after 1 tune round | notes |
|---|---|---|---|---|
| lowpass stepped, 2 GHz, N5 | RO4003C-0.508 | -0.7 % (mom vs t=0 model) | | run before the trim refinement; RL ~9 dB then, 13 dB model now |
| lowpass stub, 2 GHz, N5 | RO4003C-0.508 | -0.6 % | -1.0 % | after the stub/junction overlap fix (was +6.3 %) |
| bandstop stub, 2.4 GHz, 1 GHz | RO4003C-0.508 | +2.0 % / +2.6 % | -0.1 % / +2.1 % | pass-band RL ~10 dB |
| bandpass coupled, 5 GHz, 500 MHz | RO4003C-0.813 | invalid | | the runs had a layout short (feed and width steps across the gaps), fixed on this branch; **re-run** |
| bandpass hairpin, 2.4 GHz, 300 MHz | FR4-1.6 | pending | | |
| bandpass endcoupled, 10 GHz, 300 MHz, N3 | Alumina-0.635 | pending | | end gaps ~0.03 mm |
| bandpass stub, 3 GHz, 1.5 GHz | RO4003C-0.508 | pending | | |
| highpass stub, 2 GHz | RO4003C-0.508 | pending | | |

Cross-check: on the stepped lowpass gfdtd (CPU) and mom25d agree within 1 % on the cut-off, and the
zero-thickness line model within 0.4 % of mom25d.

To do next:

- re-run the pending cases (`tools/dfsynth/validate_all.sh` runs them all with mom25d);
- a gfdtd fine-mesh (`--fine 0.04`) cross-check of the coupled / hairpin filters: too slow on 2 CPU cores,
  fine on a GPU;
- stepped / stub lowpass pass-band match: the trim refinement brings the stub lowpass to ~16 dB RL on the
  model; confirm on mom25d;
- band-stop: an optimum (Hong) synthesis instead of the narrow-band formula plus calibration would improve
  the pass-band match.
