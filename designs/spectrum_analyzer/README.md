# A spectrum analyzer from two LMX2582 and two SIM-83+: its simulation

A behavioural simulation of a 10 MHz to 5.5 GHz spectrum analyzer built from
two TI LMX2582 synthesizers, two Mini-Circuits SIM-83+ mixers and a 40 MSPS
ADC: what it reads for a given set of input signals, with its noise, its
images, its spurs, its distortion and its ADC in it.

```bash
pip install -e . scipy scikit-rf matplotlib      # from the repository root
python designs/spectrum_analyzer/run.py          # every case: 2 minutes
python designs/spectrum_analyzer/run.py budget cw --att 0
python -m pytest designs/spectrum_analyzer/test_sasim.py
```

`run.py` writes `out/report.md` and its figures. The report of the design as
it stands is kept in [results/report.md](results/report.md).

## The design

![Block diagram: a step attenuator and band switch; band A through the first
mixer to a 3.21 GHz IF; bands B1 and B2 through a preselector; all three into
the second mixer, a 10 MHz IF, a 40 MSPS ADC and an RP2350](block_diagram.svg)

`make_block_diagram.py` draws it.

| band | range | path |
|---|---|---|
| A | 10 MHz to 2.29 GHz | up-conversion in the first mixer (used backwards: in at its IF port) to a 3.21 GHz first IF, LO1 = f + IF1 (3.2 to 5.5 GHz); printed band-pass; second mixer down to 10 MHz |
| B1, B2 | 2.29 to 3.45, 3.45 to 5.49 GHz | straight into the second mixer, LO2 10 MHz from the signal |

Both LOs run integer-N from a 10 MHz phase detector (40 MHz / 4): the LO moves
in 10 MHz steps (5 MHz below 3.55 GHz) and the FFT does the rest, so there are
no fractional spurs to model. One capture is 4096 samples: 10 MHz of span, an
RBW of 19.6 kHz.

Neither path rejects the second mixer's image in hardware. The analyzer does
it in software: each step is captured twice, the second time with LO2 on the
other side of the signal, and the lower reading is kept (`image_reject`).

## What the simulation finds

The numbers are from [results/report.md](results/report.md).

- **Noise.** Band A has a 20 dB noise figure with the attenuator at 0 dB, and
  23 dB counting the second mixer's image noise: -151 dBm/Hz. Six of those dB
  are the two 3 dB pads either side of the first mixer (`--pads 0 0`: 17 dB
  with the image).
  Band B is 25 dB without the preamp and 10 dB with it.
- **Linearity.** IIP3 is +7.7 dBm at 0 dB attenuation in band A, set by the
  two mixers; the spur-free range is 106 dB in 1 Hz, 77 dB in this RBW. The
  ADC clips (-17.8 dBm at the input) before either mixer compresses.
- **The image** reads at full level, 20 MHz below every signal, with one
  capture per step. Two captures take it down to -73 dBc and no further:
  the IF is a quarter of the sampling rate, so the image in one capture and
  the signal's alias in the other land on the same frequency, and the
  rejection is the anti-alias filter's stop band.
- **A bursting signal** (on for 35 % of the captures) is shown over only a
  third of its band after eight sweeps of two captures, because a step shows
  it only when both captures caught it. A steady noise-like signal, and the noise
  floor, read 3 dB low for the same reason.
- **A signal on the first IF** reads 82 dB down at every tuning in band A.
- **With nothing connected** no response stands 15 dB over the floor.
- **Phase noise** is -106 dBc/Hz inside the loop at 1 GHz: -66 dBc in the
  RBW at 100 kHz from a carrier.
- **The printed filters** (EM): the hairpin IF filter passes again at 6.0 GHz
  (-7 dB), and the stub band-pass preselectors pass again above their band
  (B1 at 5.5 GHz, -2.5 dB). Each is followed by a printed low-pass for that.

## How it is modelled

| part of the chain | how | file |
|---|---|---|
| input to the second mixer's output | the signal as a list of tones; every stage adds the products its intercepts, isolations and spur table give (every `n LO +/- m f`, LO leakage, harmonics, two- and three-tone products), with compression | `sasim/rf.py` |
| noise | a density against frequency, taken through the same stages: thermal noise, noise figures, both sidebands and the LO's odd harmonics at each mixer, and the LOs' phase noise as a skirt on every strong product | `sasim/rf.py` |
| second mixer's output to the ADC | a waveform at 320 MS/s: the IF amplifiers' gain, noise and distortion (a cubic applied to the waveform), the anti-alias filter, aperture jitter, the ADC's noise, INL and clipping | `sasim/backend.py` |
| the instrument | tuning, capture, FFT, amplitude calibration, sweep, image rejection, the budget | `sasim/analyzer.py` |

`Analyzer.explain(signals, f)` names what makes the reading at a frequency.

## Where the numbers come from

`run.py` prints this for every part. In short:

| | source |
|---|---|
| printed filters (IF band-pass, two low-passes, two preselectors) | S-parameters from an EM simulation: synthesised by `dfsynth`, corrected against and solved by `mom25d`, 0.05 to 10-16 GHz (`make_filters.sh`; layouts in `filters/`, files in `data/`) |
| LMX2582 | TI datasheet SNAS680E: the open-loop VCO table, PLL noise floor and flicker, output power, harmonics, phase-detector spur. The model reproduces the datasheet's 900 MHz and 1.8 GHz rows to 0.6 dB from 10 kHz to 1 MHz |
| PE43711, PE42540 | pSemi datasheets: insertion loss and isolation by band, IIP3, compression |
| LMH6629, THS4541, ADC12040 | TI datasheets: noise, HD2 and HD3 at 10 MHz, SNR, INL, DNL, aperture jitter |
| SIM-83+ | conversion loss, isolations and IP3 are datasheet typical values, read from search results (the datasheet could not be fetched). **Its spur table, compression point and out-of-band port response are a generic double-balanced mixer's, not this part's** |
| LFCN-2250+, PMA3-83LN+ | datasheet figures read from search results; the LFCN-2250+ is its specification limits (30 dB at 3 to 5 GHz), not its typical curve |

No manufacturer's S-parameter file is used: minicircuits.com refuses scripted
downloads and pSemi's are behind a licence click-through. Put the files in
`vendor/` ([how](vendor/README.md)) and the simulation uses them.

## Limits

- Stages are cascaded by |S21| in 50 ohm. Mismatch between stages (two
  reflective filters in a row, a mixer port against a filter) is not modelled;
  it is what the pads are for.
- The spur levels of both mixers rest on the generic table. Measure the
  SIM-83+ and replace `Mixer.spur` before trusting a spur prediction to
  better than 10 dB.
- A noise-like signal makes no distortion products and loads nothing.
- No direct-sampling band under 10 MHz, no LO pulling between the two
  synthesizers, no coupling through the board or the supplies.
- The ADC is an ADC12040; the INL's shape is synthetic. Set `sasim.parts.Adc`
  to the converter you have.
