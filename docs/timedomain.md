# Time domain: TDR, step response and eye

[← Signal integrity](si.md) · [Index](index.md) · [Next: Whole-board studies →](segment-studies.md)

The time-domain question takes a channel's S-parameters into time: its impedance along its length (TDR), its step
response at the far end (delay, rise, overshoot), its eye for a bit pattern at a data rate (height, width) and,
with jitter and noise, its BER contours and bathtub, driven and received by ideal or IBIS buffers. The code is `kicad2ems.core.timedomain`, on two parts of its own:
`kicad2ems.core.macromodel` (the S-parameters' passive rational model and its transient) and `kicad2ems.core.ibis`
(IBIS buffers).

```sh
# the board's S on the line model (seconds), from fmax / points to fmax
python -m kicad2ems.core.timedomain examples/rf_msvia.toml --near P1 --far P2 --rate 5 --out out/td
# a Touchstone file (a segment result, mom25d's, a measurement): a pair, IBIS buffers
python -m kicad2ems.core.timedomain --sparams ch.s4p --near P1 --far P2 --near-n P3 --far-n P4 \
    --rate 8 --driver ibis --ibis part.ibs --ibis-model DQ_34 --receiver ibis --ibis-rx IN_DQ --out out/td
```

In a study the question is *Time domain* (Signal integrity); it starts from the study's S-parameter run when
there is one (`--from` that folder), else it solves the board itself (its engines: the line model, mom25d).

## Where the S-parameters come from

| Option | |
|---|---|
| `--sparams FILE` | a Touchstone file; its ports by kicad2ems's `! ports:` names, else 1, 2, ... |
| `--from FOLDER` | a run's folder: the Touchstone of `--solvers` there, else the first it records |
| the board (`.kicad_pcb` or a config) | solved here on `--solvers` (`lines` by default), `--fmax` GHz (20) down to `fmax / --points` (1001) |

The band should reach low: the response settles to S at DC, which no sample in a band starting at 1 GHz fixes.
Samples starting under 5 % of fmax give the model a DC point; above that the run warns. An edge faster than 0.7 /
fmax is past what the band holds (warned too).

## The macromodel

Relaxed vector fitting, every entry of S on the same poles: the fewest of 8, 12, 16, ... poles within 2e-3 RMS (of
the largest |S|), the poles stable (a causal model). The DC point: Re S is even in ω and Im S odd, so S(0) is the
constant of Re S fitted as a + b ω² on the lowest samples. Passivity (the largest singular value at most 1 at every
frequency, not only the samples': a non-passive model's transient can grow without bound) is checked on a dense
grid to 3 × fmax and at each pole; where it fails, the model's samples there are projected onto passivity (each
singular value above 1 − 1e-3 taken down to it) and the residues fitted to them again, until passive. A lossless
channel's singular values are 1 across the band, so its model loses 1e-3 (0.009 dB).

In time, each pole's state is stepped exactly for an input linear over the step (recursive convolution), and each
port's termination (a source behind its resistance, a load, an IBIS buffer) is solved with the channel's waves at
every step (Newton where it is not linear).

## The buffers

- **Ideal** (`--driver ideal`): a source of `--swing` V behind `--rs` (the port's reference), raised-cosine edges
  of `--rise` (10-90 %). **Receivers**: `z0` (each port's reference), `rc` (`--rt` to `--vtt` beside `--ct` pF; a
  pair's `--rt` across, split per leg to Vtt), `open` (`--ct` alone).
- **IBIS** (`--driver ibis --ibis FILE --ibis-model NAME` or `--ibis-pin`; `--receiver ibis --ibis-rx NAME`;
  `--corner typ|min|max`): the buffer at its die, Ku(t) Ipu(Vcc − V) + Kd(t) Ipd(V) + the clamps + C_comp, behind
  its package (the pin's [Pin] R/L/C, else [Package]'s: R and L in series, C at the pin). The switching Ku, Kd from
  the edge's two V-T tables (both found; one table or [Ramp] alone takes Kd = 1 − Ku); a new edge before the last
  has settled starts where its own Ku is the present one (a UI shorter than the model's edge table is said).
  Not read: [Driver Schedule], [Submodel], EBD, [Model Spec]; IBIS-AMI.

A pair (`--near-n`, `--far-n`) is driven by two complementary drivers and received across its legs.

## What it gives

| | |
|---|---|
| TDR | a step of the edge behind each near port's reference (a pair: odd), Z = z0 V / (E − V) against the round trip; its lowest and highest over the channel |
| Step | one edge of the driver: the far end's delay (50 % to 50 % from the near end), 10-90 % rise, overshoot, levels |
| Eye | a PRBS (7 to 31; `--bits`, default twice its period, at most 4000) after the first bit has settled the channel, folded at the step's latency: the inner height at the best phase, the width at the threshold (the levels' middle), the levels at that phase |
| Crosstalk | every other port's peak during the pattern, against the near end's swing |

`timedomain.json` has all of them (the curves thinned to 2000 points) and the macromodel's record; the figures are
`<name>_timedomain.png` (TDR, step, eye) and `<name>_ber.png` (BER contours and the bathtub).

<a id="ber"></a>
## Jitter, noise and BER

The pattern's eye above is one run's: the patterns PRBS-n holds, no noise, no jitter. The statistical eye takes a
lone bit's response at the far end (the same buffers, one more short run) and every pattern of the other bits:
at each sampling phase the received voltage's distribution is each cursor's two-point distribution (its share of
the pulse, or none, at 1/2 each) convolved, then the noise's Gaussian; the BER at a phase and a threshold is the
1s read below it and the 0s above, half each. Jitter moves the sampling instant: the BER at a phase is the mean
over its shifted instants, random jitter (Gaussian) about the dual-Dirac's two deterministic ones.

| Option | |
|---|---|
| `--rj` | ps RMS, random jitter (Gaussian) |
| `--dj` | ps peak-to-peak, deterministic jitter (dual-Dirac: ±DJ/2) |
| `--noise` | mV RMS, Gaussian noise at the receiver |
| `--ber` | the BER the eye's height and width are taken at (1e-12) |

It gives the BER contours (1e-3 to 1e-15), the eye's height and width at `--ber` (the opening round the levels'
middle at the best phase, the bathtub's at that threshold), the bathtub, and TJ = DJ + 2 Q(BER) RJ (the
dual-Dirac rule; Q(1e-12) = 7.03). Each distribution is built on a grid with each cursor's mass split between its
two nearest levels (never negative: the tails hold to 1e-15) and the error tails summed from their own ends. It is
exact for linear buffers; with IBIS ones (not linear) it is their linearization about the lone bit, said in the
report.

rf_diffvia's pair at 25 Gb/s (above's model; 0.8 V swing; 2.2 s a run, the pattern's eye 110 mV by 35 ps):

| Jitter and noise | Height at 1e-12 | Width at 1e-12 |
|---|---|---|
| none | 104 mV (the worst pattern's 107) | 36 ps |
| 5 mV noise | 44 mV | 23 ps |
| 0.6 ps RJ, 4 ps DJ (TJ 12.4 ps) | 95 mV | 24 ps |
| both | 41 mV | 17 ps |

## An example

rf_msvia (a 50 Ω microstrip on F.Cu through a via to B.Cu), its S on the line model from 20 MHz to 20 GHz (1000
points), the ideal driver (1 V behind 50 Ω, a 50 ps edge), 50 Ω at the far end; 0.7 s a run:

| | |
|---|---|
| macromodel | 16 poles, RMS error 8.4e-4, passive as fitted |
| TDR | 46.7-50.3 Ω (the via's dip, 46.7 Ω at 0.29 ns round trip) |
| step | delay 144 ps, rise 55 ps, settled 0.500 V |
| eye, 5 Gb/s | 495 mV (99 % of its levels) by 200 ps (1.00 UI) |
| eye, 10 Gb/s | 485 mV (98 %) by 100 ps |
| eye, 20 Gb/s | 262 mV (69 %) by 47 ps (0.94 UI): the 50 ps edge is a UI |

## How it is checked

tests/core/test_macromodel.py, test_ibis.py, test_timedomain.py:

- the fit finds a known 10-pole model again (1e-8); a lossy 100 mm line in 64 poles (RMS 8e-4), its S21(0) 0.998;
- a line's step into a matched, an open and a 5 Ω end: 0.4995, 0.999, 0.0908 V (0.5, 1.0, 0.0909), the far end's
  half at the delay and half the edge to a step; a lossy line's far end within 0.5 % of the swing of its closed
  form's FFT;
- a 50-75-50 Ω stepped line's TDR: 75 Ω within 2 %; a pair of 50 Ω lines: 100 Ω;
- the PRBS7 eye of a lossy 150 mm line at 8 Gb/s: the peak-distortion analysis' height of the same step response
  (cursor − Σ|ISI|), within 2 % of the swing;
- a resistive IBIS buffer from [Ramp] as a source of Ku(t) Vcc behind its resistance (to 1e-6), its edge the
  ramp's slope (2 %); Ku and Kd that are not complementary found again from two V-T tables (1e-3); a 2 pF input's
  rise 2.2 RC (10 %); a package's resistance in the DC level;
- the statistical eye with no ISI: noise alone closes it to A − 2σ Q⁻¹(2 BER), random jitter to a width of
  UI − 2σ Q⁻¹(2 BER), with a dual-Dirac UI − DJ − 2σ Q⁻¹(4 BER) (two voltage steps, two phase bins); with no
  noise and no jitter on the lossy line, its height at 1e-12 the worst pattern's.

## What it leaves out

Equalization (CTLE, FFE, DFE) and IBIS-AMI; jitter on the transmitted edges as such (the sampling instant's
stands for it: a jittered edge's ISI is not modelled), sinusoidal or bounded uncorrelated jitter's own shapes (the
dual-Dirac stands for them); a floating differential termination (split per leg to Vtt); the driver's supply noise
(SSO).

---
[← Signal integrity](si.md) · [Index](index.md) · [Next: Whole-board studies →](segment-studies.md)
