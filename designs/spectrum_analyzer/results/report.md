# Spectrum analyzer simulation

LO1 LMX2582, LO2 LMX2582, two SIM-83+, 40 MSPS ADC12040, 4096-point captures.

## Budget

Input attenuator 10 dB, IF attenuator 6 dB, preamp off. The first IF is 3.210 GHz (this filter's centre); band A ends at 2.29 GHz.

| f, GHz | band | gain to ADC, dB | NF (Friis), dB | NF (effective), dB | noise floor, dBm/Hz | IIP3, dBm | P1dB in, dBm | ADC full scale, dBm | SFDR, dB (1 Hz) |
|---|---|---|---|---|---|---|---|---|---|
| 0.10 | A | 24.3 | 29.1 | 32.5 | -141.5 | +17.2 | -4.7 | -8.3 | 106 |
| 0.50 | A | 24.2 | 29.3 | 32.5 | -141.5 | +17.3 | -4.6 | -8.2 | 106 |
| 1.00 | A | 23.8 | 29.7 | 32.9 | -141.1 | +17.7 | -4.2 | -7.8 | 106 |
| 1.50 | A | 23.5 | 30.0 | 33.3 | -140.7 | +18.0 | -3.9 | -7.5 | 106 |
| 2.00 | A | 22.7 | 30.8 | 34.1 | -139.9 | +18.8 | -3.1 | -6.7 | 106 |
| 2.29 | A | 21.8 | 31.7 | 35.0 | -139.0 | +19.7 | -2.2 | -5.8 | 106 |
| 2.50 | B1 | 14.7 | 31.1 | 34.8 | -139.2 | +27.2 | +4.9 | +1.3 | 111 |
| 3.00 | B1 | 14.5 | 31.4 | 35.1 | -138.9 | +27.4 | +5.1 | +1.5 | 111 |
| 3.40 | B1 | 14.1 | 31.8 | 35.5 | -138.5 | +27.8 | +5.5 | +1.9 | 111 |
| 4.00 | B2 | 14.2 | 31.7 | 35.4 | -138.6 | +27.7 | +5.4 | +1.8 | 111 |
| 5.00 | B2 | 13.4 | 32.4 | 36.2 | -137.8 | +28.5 | +6.2 | +2.6 | 111 |
| 5.49 | B2 | 11.8 | 34.1 | 37.8 | -136.2 | +30.1 | +7.8 | +4.2 | 111 |

With the input attenuator at 0 dB (the most sensitive setting), and with the band B preamp:

| f, GHz | band | preamp | NF (effective), dB | noise floor, dBm/Hz | IIP3, dBm | ADC full scale, dBm | SFDR, dB (1 Hz) |
|---|---|---|---|---|---|---|---|
| 1.0 | A | off | 22.9 | -151.1 | +7.7 | -17.8 | 106 |
| 2.5 | B1 | off | 24.8 | -149.2 | +17.2 | -8.7 | 111 |
| 2.5 | B1 | on | 9.5 | -164.5 | -5.6 | -31.5 | 106 |
| 4.0 | B2 | off | 25.4 | -148.6 | +17.7 | -8.2 | 111 |
| 4.0 | B2 | on | 10.1 | -163.9 | -4.6 | -30.5 | 106 |
| 5.4 | B2 | on | 11.9 | -162.1 | -2.8 | -28.7 | 106 |

The effective noise figure is what the noise pass finds: it has the second mixer's image, the LOs' noise and the ADC's, which the Friis column leaves out. SFDR is 2/3 (IIP3 - noise floor) in 1 Hz; take 2/3 of 10 log(RBW) off for a wider one.

### Stage by stage at 1.0 GHz (band A)

| stage | at, MHz | gain, dB | NF, dB | IIP3, dBm | gain so far | NF so far | IIP3 so far |
|---|---|---|---|---|---|---|---|
| DC block + limiter | 1000.0 | -0.3 | 0.3 | - | -0.3 | 0.3 | +300.0 |
| input PE43711 (10 dB) | 1000.0 | -11.4 | 11.4 | +57 | -11.7 | 11.7 | +57.3 |
| SW1 (band select) | 1000.0 | -0.7 | 0.7 | +58 | -12.5 | 12.5 | +57.1 |
| LFCN-2250+ | 1000.0 | -0.4 | 0.4 | - | -12.9 | 12.9 | +57.1 |
| LFCN-2250+ | 1000.0 | -0.4 | 0.4 | - | -13.3 | 13.3 | +57.1 |
| 3 dB pad (mixer 1 IF port) | 1000.0 | -3.0 | 3.0 | - | -16.3 | 16.3 | +57.1 |
| SIM-83+ (LO1, up) | 1000.0 | -6.0 | 6.0 | +12 | -22.3 | 22.3 | +28.3 |
| 3 dB pad (mixer 1 RF port) | 3210.0 | -3.0 | 3.0 | - | -25.3 | 25.3 | +28.3 |
| IF1 band-pass (hairpin N=5) | 3210.0 | -2.1 | 2.1 | - | -27.4 | 27.4 | +28.3 |
| IF1 amp PMA3-83LN+ | 3210.0 | +20.9 | 1.5 | +14 | -6.4 | 28.9 | +28.1 |
| IF1 low-pass (radial stub N=7) | 3210.0 | -0.2 | 0.2 | - | -6.6 | 28.9 | +28.1 |
| SW2 (mixer 2 select) | 3210.0 | -0.8 | 0.8 | +58 | -7.4 | 28.9 | +28.1 |
| SIM-83+ (LO2, down) | 3210.0 | -6.0 | 6.0 | +12 | -13.4 | 29.0 | +18.9 |
| IF2 diplexer (LP arm, 30 MHz) | 10.0 | -0.3 | 0.3 | - | -13.7 | 29.0 | +18.9 |
| LMH6629 #1 | 10.0 | +19.9 | 8.0 | +26 | +6.2 | 29.6 | +18.8 |
| IF PE43711 (6 dB) | 10.0 | -7.3 | 7.3 | +57 | -1.1 | 29.6 | +18.8 |
| anti-alias LPF (elliptic N=7, 16.5 MHz) | 10.0 | -1.1 | 1.1 | - | -2.1 | 29.6 | +18.8 |
| LMH6629 #2 | 10.0 | +19.9 | 8.0 | +26 | +17.8 | 29.7 | +18.3 |
| THS4541 ADC driver | 10.0 | +6.0 | 20.0 | +44 | +23.8 | 29.7 | +17.7 |

### Stage by stage at 4.0 GHz (band B2)

| stage | at, MHz | gain, dB | NF, dB | IIP3, dBm | gain so far | NF so far | IIP3 so far |
|---|---|---|---|---|---|---|---|
| DC block + limiter | 4000.0 | -0.3 | 0.3 | - | -0.3 | 0.3 | +300.0 |
| input PE43711 (10 dB) | 4000.0 | -12.1 | 12.1 | +57 | -12.4 | 12.4 | +57.3 |
| SW1 (band select) | 4000.0 | -0.9 | 0.9 | +58 | -13.3 | 13.3 | +57.1 |
| band B2 preselector (stub N=5) | 4000.0 | -0.3 | 0.3 | - | -13.6 | 13.6 | +57.1 |
| band B2 low-pass (radial stub N=7) | 4000.0 | -0.1 | 0.1 | - | -13.7 | 13.7 | +57.1 |
| SW3 (B1/B2) | 4000.0 | -0.9 | 0.9 | +58 | -14.6 | 14.6 | +56.9 |
| preamp bypass (two switch passes) | 4000.0 | -1.6 | 1.6 | - | -16.2 | 16.2 | +56.9 |
| SW2 (mixer 2 select) | 4000.0 | -0.9 | 0.9 | +58 | -17.1 | 17.1 | +56.9 |
| SIM-83+ (LO2, down) | 4000.0 | -6.0 | 6.0 | +12 | -23.1 | 23.1 | +29.1 |
| IF2 diplexer (LP arm, 30 MHz) | 10.0 | -0.3 | 0.3 | - | -23.4 | 23.4 | +29.1 |
| LMH6629 #1 | 10.0 | +19.9 | 8.0 | +26 | -3.4 | 31.4 | +29.0 |
| IF PE43711 (6 dB) | 10.0 | -7.3 | 7.3 | +57 | -10.7 | 31.4 | +29.0 |
| anti-alias LPF (elliptic N=7, 16.5 MHz) | 10.0 | -1.1 | 1.1 | - | -11.8 | 31.4 | +29.0 |
| LMH6629 #2 | 10.0 | +19.9 | 8.0 | +26 | +8.2 | 31.7 | +28.5 |
| THS4541 ADC driver | 10.0 | +6.0 | 20.0 | +44 | +14.2 | 31.7 | +27.7 |

## A carrier at 1 GHz, -30 dBm

| reading, dBm (RBW 19.6 kHz) | one capture per step | two captures, the lower kept |
|---|---|---|
| carrier | -30.0 | -30.0 |
| image, 20 MHz below | -30.0 | -92.7 |
| noise floor (median) | -99.6 | -102.6 |
| skirt at +100 kHz (mean) | -97.8 | -100.1 |
| skirt at +300 kHz (mean) | -102.6 | -103.2 |

At 100 kHz the two LOs' phase noise is -110.7 and -113.1 dBc/Hz (LMX2582 datasheet profile at 4.21 and 3.20 GHz), which in this RBW is -65.8 dBc: the skirt above.

The lower of two readings of noise is 3 dB under either: with two captures the floor, and any noise-like signal, reads 3 dB low.

![A carrier at 1 GHz](cw.png)

Close in (a 262144-sample capture, RBW 306 Hz), the reading follows the two LOs' datasheet profiles until the analyzer's noise floor takes over:

![Phase noise at 1 GHz](cw_phase_noise.png)

## Two tones, 1 MHz apart, -26 dBm each

| input attenuator, dB | tone reads, dBm | IM3 reads, dBm | IM3, dBc | from the budget's IIP3, dBc | each tone at the ADC, dBFS | floor, dBm |
|---|---|---|---|---|---|---|
| 0 | -26.2 | -95.8 | -69.6 | -67.4 | -8.2 | -126.4 |
| 10 | -26.2 | -110.2 | -84.1 | -87.4 | -18.2 | -117.2 |
| 20 | -26.2 | -99.8 | -73.6 | -107.4 | -28.2 | -107.6 |

With no attenuation the product is the chain's (the two mixers, mostly), within 2 dB of the budget, whose cascade adds every stage's product in phase. Each 10 dB of attenuation should take it down 20 dB; what is left when it does not is the ADC's own INL, which stays near -100 dBFS whatever the level.

![Two-tone intermodulation](im3.png)

## Reading against input level (1 GHz, attenuator 10 dB)

The ADC's full scale is -7.8 dBm at the input (budget); the first clipped sample appears at -6 dBm, and the reading is 1 dB low from -4 dBm. The first stage to compress (the budget's cascade) does so at -4.2 dBm at the input.

![Compression](compression.png)

## A 0 dBm signal on the first IF (3.21 GHz), seen from band A

With one capture per step it reads up to -82.2 dBm (82 dB under the signal), at every tuning: here at 698.70 MHz, made by `1LO2-1[1x[IF1 signal] through SIM-83+]`. With two captures, -90.9 dBm. The rejection is the two LFCN-2250+ (their datasheet limit, 30 dB each at this frequency), the mixer's own isolation, and the leak around the band switches.

![IF feedthrough](feedthrough.png)

## Band A with nothing connected

Noise floor -99.3 dBm (median). Responses 15 dB or more over their step's floor:

none.

![Residual responses](residuals.png)

## Band B: a carrier at 4 GHz, and a bursting 18 MHz wide signal at 2.442 GHz

| reading, dBm | one capture per step | two captures, the lower kept |
|---|---|---|
| carrier, 4 GHz | -40.0 | -40.0 |
| image, 20 MHz below | -40.0 | -89.3 |
| noise floor | -96.6 | -99.6 |

![Band B carrier](bandb_cw.png)

A noise-like signal 18 MHz wide, -40 dBm in total, is -69.6 dBm in this RBW. Steady, and then on for 35 % of the captures (each capture is 0.1 ms), max hold over eight sweeps:

|  | steady: reads, dBm | bursting: reads (median), dBm | bursting: share of its band shown | 20 MHz below: reads, dBm |
|---|---|---|---|---|
| one capture per step | -69.0 | -68.0 | 100 % | -70.6 |
| two captures, the lower kept | -72.4 | -91.0 | 32 % | -93.7 |

One capture per step shows the signal and its image side by side. Keeping the lower of two captures removes the image, but a step shows a bursting signal only when both of its captures caught it: the signal is shown in pieces, and low.

![Band B burst](bandb_burst.png)

## 10 MHz to 5.49 GHz

| signal, MHz | level, dBm | reads, dBm |
|---|---|---|
| 100.3 | -35.0 | -35.2 |
| 915.2 | -25.0 | -25.0 |
| 1575.4 | -60.0 | -59.9 |
| 2440.1 | -30.0 | -30.2 |
| 5200.3 | -45.0 | -45.2 |

Readings 15 dB or more over the local floor that are not the five signals: 5 with one capture per step, 0 with two.

| at, MHz | one capture, dBm | two captures, dBm | made by |
|---|---|---|---|
| 904.80 | -25.0 | -91.6 | `1LO2-1[1LO1-1[915.2 MHz]]` |
| 2419.90 | -30.2 | -90.5 | `1LO2-1[2440.1 MHz]` |
| 79.70 | -35.2 | -100.2 | `1LO2-1[1LO1-1[100.3 MHz]]` |
| 5179.70 | -45.2 | -89.2 | `1LO2-1[5200.3 MHz]` |
| 1564.60 | -59.9 | -97.3 | `1LO2-1[1LO1-1[1575.4 MHz]]` |

1647 captures of 4096 samples, 24 s of simulation.

![Full sweep](full.png)

## One strong signal: 0 dBm at 915.2 MHz, attenuator 20 dB

The mixer sees -25 dBm. The signal reads -0.0 dBm. Other readings 15 dB or more over the local floor: 5 with one capture per step, 1 with two.

| at, MHz | one capture, dBm | made by | two captures, dBm | and in the second capture by |
|---|---|---|---|---|
| 904.80 | -0.0 | `1LO2-1[1LO1-1[915.2 MHz]]` | -73.1 | `alias of [1LO2-1[1LO1-1[915.2 MHz]]] at an IF of 25.2 MHz, through the anti-alias filter` |
| 899.60 | -66.0 | harmonic 2 of [1LO2-1[1LO1-1[915.2 MHz]]] in the IF amplifiers and ADC | -87.2 |  |
| 919.60 | -67.5 | harmonic 2 of [1LO2-1[1LO1-1[915.2 MHz]]] in the IF amplifiers and ADC | -85.6 |  |
| 885.07 | -71.9 | the skirt of `1LO2-1[1LO1-1[915.2 MHz]]` | -82.8 |  |
| 875.20 | -73.4 | `alias of [1LO2-1[1LO1-1[915.2 MHz]]] at an IF of 25.2 MHz, through the anti-alias filter` | -87.7 |  |

`1LO2-1[1LO1-1[x]]` is the plain conversion of x: where it is not at x it is the second mixer's image. What survives two captures does so because the IF is a quarter of the sampling rate: the image in the first capture and the alias (the signal at an IF of 25 MHz, folded) in the second land on the same frequency, so the image rejection is no better than the anti-alias filter's stop band.

![One strong signal](strong.png)

## Where each part's numbers come from

| part | source |
|---|---|
| DC block + limiter | assumed 0.3 dB (no part chosen) |
| input PE43711 (10 dB) | pSemi datasheet DOC-84940 table 3 (typical insertion loss by band; past 6 GHz assumed) |
| SW1 (band select) | pSemi PE42540 datasheet (typical insertion loss; past 8 GHz assumed) |
| LFCN-2250+ | datasheet specification points (LFCN-2250D+ table, read from a search result); past 5 GHz assumed |
| 3 dB pad (mixer 1 IF port) | resistive pad (exact) |
| SIM-83+ | datasheet typ. (CL, isolations, IP3, via search results); spur table, port roll-off and P1dB assumed |
| 3 dB pad (mixer 1 RF port) | resistive pad (exact) |
| IF1 band-pass (hairpin N=5) | EM simulation (kicad2ems mom25d) of dfsynth's layout: data/if1_bpf_3g2_hairpin.s2p, 3.2 GHz, 180 MHz, RO4003C 0.508 mm |
| IF1 amp PMA3-83LN+ | datasheet figures at 2 GHz (via search results); frequency slopes assumed |
| IF1 low-pass (radial stub N=7) | EM simulation (kicad2ems mom25d) of dfsynth's layout: data/if1_lpf_4g0_radial.s2p, 4.0 GHz, RO4003C 0.508 mm |
| SW2 (mixer 2 select) | pSemi PE42540 datasheet (typical insertion loss; past 8 GHz assumed) |
| SW1 (off path) | pSemi PE42540 datasheet (typical RFC-RFx isolation; past 8 GHz assumed) |
| band B2 preselector (stub N=5) | EM simulation (kicad2ems mom25d) of dfsynth's layout: data/b2_bpf_3g4_5g5_stub.s2p, 3.4-5.5 GHz, RO4003C 0.508 mm |
| band B2 low-pass (radial stub N=7) | EM simulation (kicad2ems mom25d) of dfsynth's layout: data/b2_lpf_6g4_radial.s2p, 6.4 GHz, RO4003C 0.508 mm |
| SW3 (B1/B2) | pSemi PE42540 datasheet (typical insertion loss; past 8 GHz assumed) |
| preamp PMA3-83LN+ (unpowered) | assumed 20 dB through an unpowered LNA |
| SW2 (off path) | pSemi PE42540 datasheet (typical RFC-RFx isolation; past 8 GHz assumed) |
| band B1 preselector (stub N=5) | EM simulation (kicad2ems mom25d) of dfsynth's layout: data/b1_bpf_2g3_3g5_stub.s2p, 2.3-3.5 GHz, RO4003C 0.508 mm |
| preamp bypass (two switch passes) | resistive pad (exact) |
| IF2 diplexer (LP arm, 30 MHz) | ideal 3rd-order Butterworth, 0.3 dB loss assumed |
| LMH6629 #1 | TI datasheet SNOSB18 (NF, HD2 and HD3 at 10 MHz, swing); bandwidth at Av=10 assumed |
| IF PE43711 (6 dB) | pSemi datasheet DOC-84940 table 3 (typical insertion loss by band; past 6 GHz assumed) |
| anti-alias LPF (elliptic N=7, 16.5 MHz) | ideal elliptic prototype, 1 dB loss assumed |
| LMH6629 #2 | TI datasheet SNOSB18 (NF, HD2 and HD3 at 10 MHz, swing); bandwidth at Av=10 assumed |
| THS4541 ADC driver | TI datasheet SLOS375 (HD2, HD3 at 10 MHz); noise figure estimated |
| LMX2582 | TI datasheet SNAS680E table 6.5 (VCO and PLL noise, output power, harmonics, PFD spur); loop bandwidth chosen |
| ADC12040 | TI datasheet SNAS122 (SNR, THD, INL, DNL, jitter); INL shape synthetic |

