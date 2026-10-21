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
| lowpass | `radial` | the same with each stub a butterfly of radial stubs (`--stub-angle`, default 60 degrees): each fan's resonance puts a transmission zero in the stop band |
| highpass | `stub` | shunt short-circuited quarter-wave stubs: a wide band-pass from the cut-off to `--bw` above it (default up to 3x the cut-off), as any distributed high-pass is |
| bandpass | `coupled` | edge-coupled (parallel-coupled) half-wave resonators |
| bandpass | `hairpin` | the same, folded into alternating U resonators, coupled-line feeds |
| bandpass | `interdigital` | quarter-wave rods grounded at alternate ends, tapped feeds; wide gaps for the bandwidth (1.8 mm at 10 % on RO4003C-0.813, against 0.13 mm edge-coupled); tune it with `--validate gfdtd --em-tune 3` |
| bandpass | `endcoupled` | gap-coupled half-wave resonators; narrow bands only (the end gaps get small fast) |
| bandpass | `stub` | Matthaei short-circuited stub band-pass, wide bands (30 to 100 %) |
| bandstop | `stub` | open quarter-wave stubs a quarter wave apart; the connecting lines' impedances are optimised too |
| bandstop | `radial` | the same with radial stubs: these are low-impedance stubs, so only very wide stop bands (about 100 % and up); narrower ones are refused |

Responses: `--response chebyshev` (`--ripple` dB, default 0.1) or `butterworth`. The order comes from
`--order`, or from a stop-band point, `--atten 40@3.5G` (the lowest order with 40 dB at 3.5 GHz).
Ladders with equal terminations (lowpass, bandstop) need an odd Chebyshev order and get bumped to one.
The bandwidth is the ripple bandwidth for Chebyshev, the -3 dB bandwidth for Butterworth. Stub filters
(stub band-pass, high-pass, band-stop) map the prototype through Richards' transformation, so their bands
are arithmetically symmetric about f0 rather than geometrically.

Substrates: `dfsynth --list` shows the presets (FR4, RO4003C, RO4350B, RT5880, RO3010, alumina in
common thicknesses); `--h`, `--er`, `--tand`, `--copper` override them.

Fabrication limits: `--min-width` and `--min-gap` (default 0.1 mm trace and 0.1 mm space, about the best
PCB vendors do; raise them for a cheaper process) are enforced. A design with a narrower trace or gap is refused, with the dimensions at fault and the
nearest bandwidth that meets the limits; `--allow-fab-violations` writes it anyway. dfsynth never
silently changes a dimension to meet them, except that band-stop stubs too narrow to make are clamped to
the minimum width (and the result says the stop band comes out wider). With the defaults the edge-coupled
5 GHz / 500 MHz example on RO4003C-0.813 fits (0.134 mm end gaps) but a 1 GHz wide one is refused (0.070
mm: 661 MHz is the widest that fits), and so is the end-coupled 10 GHz / 300 MHz one on alumina (0.024 mm
gaps: 97 MHz).

## How a design is made

1. **Synthesis**: low-pass prototype g-values; then the topology's classic design equations
   (stepped-impedance with the inductive lines' shunt parasitics taken off the capacitors; J-inverters for
   coupled, hairpin and end-coupled resonators; Matthaei's equations for the stub band-pass; quarter-wave
   stub band-stop). The ideal TEM network of each synthesis is checked in the tests.
2. **Physical dimensions** on the substrate (and for the interdigital filter, design curves extracted from
   the multiconductor model below: the coupling of two rods against their gap from their split resonances,
   the external Q of a rod tapped through a narrow neck, a quarter of the feed's width, from its input
   susceptance's slope; a 50 ohm feed joined straight to the rod is as wide as the tap's distance from the
   short and spreads the tap over its width): Hammerstad-Jensen microstrip with strip thickness and
   Kirschning-Jansen dispersion; Kirschning-Jansen coupled lines; Kirschning-Jansen-Koster open ends;
   Garg-Bahl series gaps (whose shunt capacitance is taken off each resonator end); Edwards' width-step
   extension; Goldfarb-Pucel via inductance on shorted stubs.
3. **Calibration**: dfsynth builds a circuit model of the actual layout (dispersion, loss, open ends,
   steps, gaps, vias; radial stubs as radial lines, from half a line width beyond the line's edge as
   kicad2ems fits qucsator's; arrays of coupled strips as a multiconductor line whose capacitance matrix
   is a quasi-static moment-method solution of the cross-section, every strip coupled to every other, with
   the copper thickness and dispersion from the closed forms, inductances from the same strips in air,
   solved as a nodal network) and iterates two factors, `kf` (all lengths) and `kb` (the design bandwidth), until
   the model's -3 dB edges land on the spec's. This is what fixes the approximations of step 1 (the
   hairpin's bends and short arms, the band-stop's narrow-band formula, ...). Two topologies are refined
   further on the lossless model before a final calibration: the low-pass sections' lengths are trimmed
   to follow the lumped prototype, and the band-stop's stub and connecting-line impedances are optimised
   (within what the substrate can make) to follow the Chebyshev response in Richards' variable, which the
   narrow-band formula's 50 ohm spacers miss by more as the band widens. On RO4003C-0.508 at 2.4 GHz that
   takes the pass-band return loss from 5.4 to 8.2 dB (1 GHz stop band) and from 3.8 to 11.4 dB (1.5 GHz);
   narrower stop bands whose stubs are already at the minimum width gain nothing. The interdigital's gaps,
   taps and rod lengths are trimmed for the prototype's match over the band with the edges on target (5
   GHz / 500 MHz on RO4003C-0.813: 2.2 to 12.2 dB return loss on the lossless model).
4. **Validation** (`--validate`): kicad2ems solves the written board with the solvers named (`mom` is
   the usual choice for these planar filters; `gfdtd` runs well on a CPU too; `lines` is kicad2ems's own
   line model). dfsynth measures each result's band edges (3 dB below the pass-band peak, so loss does
   not move them) and reports centre and bandwidth error against the spec, pass-band loss and match.
5. **EM tuning** (`--em-tune N`): the full-wave result's centre / bandwidth error is folded back into `kf` /
   `kb`, the board is rewritten and solved again. The interdigital is tuned by space mapping instead: the
   trims (and the port reference planes) with which the zero-thickness model reproduces the full-wave
   run's complex S21 and S11 are extracted, and the board's trims move by their difference from the
   model's own. Each rod, gap and tap can then be corrected on its own, which a centre / bandwidth factor
   cannot do; fitting the complex response tells a gap's error from a tap's, which |S| alone does not.

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
- The kicad2ems configs dfsynth writes set PML boundaries (`bc = "PML_8"`) for openEMS and gfdtd.
  kicad2ems's default first-order Mur boundary, 5 mm from the board, absorbs a resonant filter's near
  field: on the interdigital it lost up to 60 % of the power at the lower band edge in both FDTD codes and
  read as a 17-27 % narrow band; with PML the same board is -0.6 % (and unchanged with 12 mm of air).

## Validation status (2026-10-21, mom25d on GPU, one `--em-tune` round)

"Predicted" is mom25d's error over the zero-thickness line model applied to the model with 35 um copper:
the error expected of the real board against the spec. RL is mom25d's worst return loss over the
response's own pass band (`own band` in the output: between its measured edges, a tenth of the band
inside them); the spec-band figure also printed counts a mistuned filter's skirt as mismatch. The
lowpass and band-stop rows were run with conductor loss (`sigma` in the written .toml), the others
without.

| case | substrate | round 0 predicted (centre / bw) | after 1 tune round | mom25d RL (round 0 / 1) | notes |
|---|---|---|---|---|---|
| lowpass stepped, 2 GHz, N5 | RO4003C-0.508 | -2.0 % | +0.0 % | 10.7 / 11.7 dB | model 13.6 dB |
| lowpass stub, 2 GHz, N5 | RO4003C-0.508 | -1.6 % | +0.0 % | 11.7 / 12.0 dB | model 16.5 dB |
| bandstop stub, 2.4 GHz, 1 GHz | RO4003C-0.508 | +1.9 % / +5.2 % | +0.1 % / +0.6 % | 9.2 / 9.5 dB | 5.5 dB before the impedance refinement; 37 dB rejection |
| bandpass coupled, 5 GHz, 500 MHz | RO4003C-0.813 | +0.4 % / -6.7 % | -0.0 % / -1.5 % | 12.0 / 12.3 dB | |
| bandpass hairpin, 2.4 GHz, 300 MHz | FR4-1.6 | +13.3 % / -18.2 % | -1.2 % / -6.2 % | 16.4 / 16.4 dB | the line model is off, see below; gfdtd agrees with mom25d |
| bandpass endcoupled, 10 GHz, 300 MHz, N3 | Alumina-0.635 | +0.5 % / -57 % | +0.0 % / -57 % | 2.5 / 2.5 dB | not validated: see below |
| bandpass stub, 3 GHz, 1.5 GHz | RO4003C-0.508 | -3.8 % / -5.7 % | -0.2 % / +2.9 % | 12.7 / 15.1 dB | |
| highpass stub, 2 GHz | RO4003C-0.508 | +4.5 % / +5.8 % | +1.5 % / -4.1 % | 12.1 / 12.9 dB | not yet tried with `--em-tune 2` |

Cross-checks: on the stepped lowpass gfdtd (CPU) and mom25d agree within 1 % on the cut-off. On the tuned
hairpin gfdtd (GPU, 5.3 M cells, 4 min) and mom25d agree within 0.1 % on the centre (-1.55 % / -1.43 %).

- **Hairpin**: dfsynth's line model puts the hairpin 11-13 % low (wide arms on FR4-1.6). It couples only
  adjacent arms and runs the bars along their centrelines; kicad2ems's own line model of the same layout
  (all 12 arms as one multi-line coupled section, 12 corners) is within 4 % of the field solvers. One tune
  round brings the centre to -1.2 %, the bandwidth to -6.2 % (`--em-tune 2` not yet tried).
- **End-coupled**: the 0.03 mm end gaps make the response depend on the copper thickness (the zero-
  thickness model of the same layout has 1.2 dB RL against 16 dB with 35 um), and kicad2ems's solvers
  treat copper as infinitely thin, so they cannot validate this design. Thicker gaps (lower er, wider
  band) or a solver with thick copper are needed.
- **Interdigital** (5 GHz / 500 MHz, N5, RO4003C-0.813, tapped through 0.44 mm necks, vias inside the
  rods' ends; gfdtd and openEMS with PML boundaries). The synthesis puts the band where the field solvers
  do: centre +0.3 % (gfdtd) / -0.4 % (openEMS), bandwidth -0.6 % on both. Its match does not survive:
  3 dB return loss against the model's 11 dB. The rod-pair couplings (within 2-5 % of gfdtd at 1.0-2.4
  mm gaps) and the taps' external Q (within 4-14 %) are right; the tapped outer rods resonate 2-3 % high
  against the inner ones, which leaves the skirts in place and wrecks the match. Space mapping fixes that:
  `--validate gfdtd --em-tune 3` extracts the model's trims from each run to 0.034 rms in |dS|, converges
  to within 1-3 % of the target trims, and the board it predicts (the extracted model with 35 um copper)
  goes from 3.4 to 10.7 dB return loss over the spec band (the target design's being 11.2 dB), centre
  -0.3 %, bandwidth -3.7 %. The solvers' own readings stay worse (6-9 dB): they solve zero-thickness
  copper, which detunes this filter's outer rods by itself (the zero-thickness model of the target has 5
  dB). A board tuned this way on gfdtd (an earlier run, against its zero-thickness readings) measures
  the same on openEMS: 11.6 dB return loss over the spec band (gfdtd 11.9), centre -0.5 %, bandwidth -4.8 %. mom25d (lumped ports: the 5 mm feeds are too short for its wave ports) disagrees with both FDTD
  codes on this filter (+4 % centre) and is not used for it. Earlier figures here (-17 to -27 % bandwidth)
  were kicad2ems's default Mur boundary, not the filter; see Known limits.
- **Radial stubs**: the stub model puts kicad2ems's `rf_radialstub` notch at 2.10 GHz (openEMS 2.13,
  mom25d 2.18). The radial low-pass and band-stop are not validated on a field solver yet; the 100 %
  band-stop's pass-band match is only 5 dB on the model (its outer fans need ~45 ohm, at the 15 degree
  limit).
- **kicad2ems's `lines` (qucs)** gives an open circuit on the edge-coupled filter: its extractor groups
  only the first and last coupled sections, as the middle resonators' width steps put a jog in the traces.

To do next:

- a multi-line coupled model for the hairpin (each arm coupled to its own resonator's other arm and to the
  next-but-one arms) and corner models, so round 0 lands within a few percent;
- the kicad2ems line extractor: coupled sections across width-step jogs (or dfsynth drawing the steps
  without them);
- band-stop: the impedance refinement is limited by the narrowest strip on thin substrates (the outer
  stubs want 200+ ohm for wide bands on the ideal network); a thicker substrate gets closer to the 16 dB of
  a 0.1 dB ripple;
- gfdtd ignores `--sigma` (PEC copper), so its pass-band loss is the dielectric's only;
- interdigital: the space mapping's tap correction oscillates between rounds (0.97 / 0.96 / 1.03 of the
  target): a damped or Broyden step would converge in fewer runs; and why the zero-thickness copper
  detunes its outer rods this much is not understood yet;
- openEMS with PML is several times slower than with Mur (873 s against ~130 s on the interdigital);
  gfdtd is the practical solver for these runs.
