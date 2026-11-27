# Radiated EMC: emission and susceptibility

[← Line parameters](rlgc.md) · [Index](index.md) · [Next: Whole-board studies →](segment-studies.md)

Two questions about the board's own radiation, both from each port's far field on mom25d:

- **Radiated emission** (`kicad2ems.core.emission`): the board's ports driven by a signal (a clock, data, a CW
  sweep, a spectrum file). It gives the field at a test site's antenna 3 or 10 m away, with the antenna's height
  scanned and the board turned on a turntable, compared with CISPR 32's class A or B limits.
- **Radiated susceptibility** (`kicad2ems.core.susceptibility`): the voltage a plane wave puts on each port's load.
  It takes the worst case over every direction and linear polarisation, at IEC 61000-4-3's levels (3 and 10 V/m,
  80 MHz–6 GHz).

Both are estimates for the **board alone**. What the board is connected to is left out, and cables most often
dominate a product's emission and set its immunity. Over mom25d's infinite ground nothing radiates below the board,
and the board has no edge (see [What is left out](#left-out)).

```sh
# a 25 MHz clock (3.3 V, 1 ns edges) on P1, class B at 3 m
python -m kicad2ems.core.emission examples/rf_serpentine.toml --drive P1 --f0 25 --out out/emi
# data: 1 Gb/s PRBS7 on a pair's legs, in opposite phase
python -m kicad2ems.core.emission board.toml --drive P1+@0,P1-@180 --signal pattern --rate 1 --out out/emi
# what 3 and 10 V/m put on each port, flagged above 100 mV
python -m kicad2ems.core.susceptibility examples/rf_serpentine.toml --threshold 100 --out out/sus
```

In a study these are *Radiated emission* and *Radiated susceptibility*, in the EMC group. Their engine is
mom25d. An antenna run's array file can stand in for the solve (`--array`, below).

## Each port's field

mom25d solves the board at each frequency with a 1 V source at every port, the other ports shorted: one factor
gives every port's currents (its antenna mode's excitation, with lumped ports). The port currents give Y. With
each port on its load Z (its reference impedance, or `--load PORT=OHMS`), a source V_s at port j behind its load is

    V = (1 + Z Y)^-1 V_s,     I_term = I (1 + Z Y)^-1

so each port's currents per unit source volt, with the others loaded, take one product. Their far field
(`mom25d.farfield.far_field_dirs`) is the field per volt. It is computed on a θ × φ grid every `--step` degrees
(emission 3, susceptibility 5), with extra steps toward the board's plane. Over a grounded dielectric the field
falls to zero within about a degree of grazing, which a plain grid misses: on 30 mm of microstrip at 4 GHz the
interpolation was off by 43 % of the largest field. With the extra steps it is within 1.1 %.

**Frequencies.** Up to `--points` (41). The first 15 are equally spaced in log f. Then each interval beside a sample
is halved where the sample lies more than 0.5 dB off its neighbours' line (dB over log f), either way: a peak
between two samples can leave the one between them below the line. The worst is taken first. A sample more than
30 dB below the curve's top is not refined. The samples gather at resonances, and a field rising 20 dB a decade
gets none beyond the first 15.

Against 241 frequencies (the emission's field per volt, 30 MHz–6 GHz), the largest shortfall of the samples'
line was:

| Board | 61 equally spaced | 41 adaptive |
|---|---|---|
| rf_serpentine, 2.8k unknowns | 2.23 dB | 0.67 dB (0.27 within 20 dB of the top) |
| rf_branchline, 2.9k unknowns | 2.26 dB | 0.25 dB |

So the adaptive frequencies are a third fewer solves and come closer.

**An antenna run's array file** (`--array <name>_<solver>_array.npz`, kicad2ems.core.phased) stands in for the
solve at its own frequencies and directions. This can be mom25d's file (per unit port voltage, with Y: any loads),
or an FDTD solver's (per unit incident wave, every port in its reference; every port must have been excited). A
resonance between its frequencies is not seen.

## Susceptibility

By Lorentz reciprocity between a port's gap source and a far dipole, a plane wave E0 p̂ arriving from (θ, φ) puts
on port j, on its load z_j with the others on theirs,

    V_j = 4π / (j k0 η0) · z_j · E0 · (p̂ · F_j(θ, φ))

where F_j is the r E of port j's terminated field per unit source volt (the ground's image and the other ports'
loads are in it). The largest |V_j| over the grid's directions and over every linear polarisation (the larger
eigenvalue of the 2 × 2 form in the θ and φ parts) is each port's worst at that frequency. The direction and the
polarisation angle (from θ̂) are kept.

A differential pair's legs (P1+ / P1−, its ports in the board's config) also give the pair's differential
voltage: each leg's complex voltage for the same wave, subtracted.

| Option | |
|---|---|
| `--field 3,10` | V/m RMS, the levels (IEC 61000-4-3 levels 2 and 3) |
| `--threshold MV` | mV RMS: a port above it is flagged |
| `--ports P1,P2` | the ports reported (default all) |
| `--fmin 0.08 --fmax 6` | GHz |

Each level's table gives each port's largest voltage over the band (RMS of the unmodulated carrier), in mV and
dBµV. With the standard's 80 % AM at 1 kHz the peak is 1.8 √2 times it. The table also gives the frequency,
direction and polarisation. The study's figures are the worst at each level and each port's at the highest.

## Emission

**The drive.** `--drive` takes kicad2ems.core.phased's `PORT[=amplitude][@phase]` items (default: the first port).
Each scales the signal at its port: the source's open-circuit voltage, behind the port's load. Every other port is
on its load.

**The signal** (`--signal`):

| Kind | Options | Spectrum |
|---|---|---|
| `clock` | `--f0` MHz, `--amp` V (0 to it), `--rise` ns (10–90 %), `--duty` | the trapezoid's lines: c_n = 2 A d \|sinc(n d)\| \|sinc(n f0 t_r)\|, t_r = rise / 0.8 (RMS c_n / √2) |
| `pattern` | `--rate` Gb/s, `--amp`, `--rise`, `--prbs 7..31` or `random` | a PRBS of L = 2^n − 1 bits: lines at k rate / L. An m-sequence's spectrum is flat, so each is amp² (L + 1) / (2 L²) sinc²(f / rate) sinc²(f t_r). Where they are closer than the receiver's bandwidth, random data's PSD amp² T / 2 sinc²(f T) sinc²(f t_r) is read in 120 kHz (below 1 GHz) or 1 MHz |
| `flat` | `--amp` V RMS | the field per `--amp` volts at every frequency solved: a CW sweep |
| `file` | `--spectrum FILE` | lines: f (Hz), volts RMS a row |

The field per volt is solved at the lines themselves when there are no more than `--points` of them. Otherwise it
is solved on the adaptive frequencies and interpolated between them in dB over log f (said in the result). A line
is a CW tone: CISPR's quasi-peak, average and peak detectors all read it alike. Data's continuous part is its RMS
in the bandwidth, which the peak detector reads several dB above (said).

**The site** (`kicad2ems.core.radiated.site`):

- The board is at `--h-eut` (0.8 m) over the floor and the antenna at `--distance` (3 or 10 m).
- The antenna's height is scanned over `--heights 1,4,0.1` m, in both polarisations.
- The turntable turns the board every `--turn-step` (5°).
- The board stands as `--orientation` says: `flat` (top up), `upright` (on its +y edge), or `all` (the worst of flat,
  flipped and each edge up), or one of those six by name.
- Below 1 GHz the floor reflects (a semi-anechoic room or an open site). The field is the direct ray's plus the
  image's:

        E = F(u_d) e^{-jk L_d} / L_d + R F(u_r) e^{-jk L_r} / L_r,     R = diag(−1, −1, 1)

  u_r is the ray to the floor's reflection point (a PEC floor's image reverses the horizontal field).
- Above 1 GHz the floor is covered (FSOATS, as CISPR 16-1-4 has it) and only the direct ray arrives (`--floor on|off`
  forces either).
- The antenna takes E_z (vertical) and the field across the line of sight (horizontal). The largest over heights,
  turntable, polarisations and orientations is held.

**The limits** (CISPR 32, dBµV/m):

| | 30–230 MHz QP | 230–1000 MHz QP | 1–3 GHz AV / PK | 3–6 GHz AV / PK |
|---|---|---|---|---|
| class B, 10 m | 30 | 37 | | |
| class B, 3 m | 40 | 47 | 50 / 70 | 54 / 74 |
| class A, 10 m | 40 | 47 | | |
| class A, 3 m | 50 | 57 | 56 / 76 | 60 / 80 |

Other distances scale by 20 log10(d_ref / d), from 10 m below 1 GHz and from 3 m above. The margin at each line is
the least of the limits less the field (negative: over). The study's figures are the worst margin, its frequency,
the largest field and the number of lines over the limit.

## Checks

On mom25d (tests/mom25d/test_radiated.py):

- **A short top-loaded element** (a 2 mm patch 1 mm over the ground, air between, fed at its centre): over the
  ground with its image it is a short dipole of effective length 2h.
  - Its far field is η k I 2h sin θ / 4π to 1e-6, its directivity 3.000 and its efficiency 1.0000.
  - The received voltage from a θ-polarised wave is E0 2h sin θ z / (z + Zin): 3e-5 at 0.3 GHz, 3e-4 at 1 GHz
    (Zin from the solve, a 0.12 pF capacitor with 0.0176 Ω of radiation resistance at 1 GHz).
  - An independent plane-wave solve of the same MoM system agrees to 1e-6: the incident field and its image tested
    on each RWG function and along each via, the port's short-circuit current through its load.
- **Two of them** 3 wavelengths apart at 5 GHz: their solved S21 against Friis from their solved realized gains.
  Along the ground the incident wave and its reflection are one, so |S21| = √(G1 G2) λ / 8πR. They agree within
  0.014 dB (0.0017 dB at 10 wavelengths).
- **Its field at 3 m**, the board flat and the antenna level with it, with no floor: η k |I| 2h / 4π d, within
  1e-6.
- **An antenna run's array file**: mom25d's Y form and an FDTD solver's S form give what the solve gives, to the
  file's single precision.
- **The far field's grid** against far_field_dirs in 300 random directions, on a microstrip at 4 GHz: within 1.1 %
  of the largest.

Closed forms (tests/core/test_radiated.py):

- a clock's lines against its sampled trapezoid's Fourier series (1e-4);
- a PRBS7's lines against its waveform's (2e-3), and their density as L grows against random data's PSD;
- CISPR 32's limits;
- the site against a dipole at h_eut and its image at −h_eut, every polarisation of the dipole, to 1e-12;
- the turntable and the orientations;
- the worst linear polarisation against a sweep of angles;
- a Q 100 resonance found within 0.5 dB by the adaptive frequencies, where 61 equally spaced miss it by 4.9 dB.

**On rf_serpentine** (`kicad2ems study examples/emc_serpentine.toml`; 2.8k unknowns, 41 frequencies, 4 threads on a
machine loaded by other runs):

- **Emission:** a 25 MHz, 3.3 V clock with 1 ns edges on P1, class B at 3 m. Worst margin +5.8 dB at 225 MHz
  (34.2 dBuV/m against 40, quasi-peak), seen with the board on its +y edge, the antenna at 1.4 m, horizontally
  polarised. 27 s, about 0.8 GB.
- **Susceptibility:** at 10 V/m, up to 36 mV (91.0 dBuV) on either port at 6 GHz. There the line radiates 8 % of
  what it carries (0.17 % at 4 GHz), and the emission's field per volt rises by the same 16 dB. 24 s.
- In both, the per-frequency solve (mom25d's fill and LU) is 86-88 % of the time.

<a id="left-out"></a>
## What is left out

- **Cables and what the board connects to.** A cable's common-mode current, driven by the board's ground noise and
  its connector's transfer impedance, most often dominates a product's radiated emission and its immunity. It needs
  the cable's own model (its RLGC) and the field-to-line coupling (Agrawal / Taylor).
- **The board edge and its ground's lower side.** Over mom25d's infinite ground nothing radiates below the board, and
  no edge radiates. `--mom-stack whole` meshes the planes: the field above and below, but a laterally infinite
  dielectric.
- **An enclosure**, a heatsink, the parts' bodies.
- **The receiver's detectors on a modulated or noise-like signal:** quasi-peak's weighting of pulsed emission. A line
  reads the same on every detector; data's continuous part is given as its RMS in the bandwidth.
- **A resonance narrower than the samples' spacing.** The adaptive frequencies gather where the curve bends, but a
  peak the first 15 do not touch is not found.
