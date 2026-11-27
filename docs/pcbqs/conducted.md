# Conducted emission

`kicad2ems.pcbqs.conducted` estimates a switching circuit's conducted emission the way a CISPR test
measures it. It takes a circuit model (the [circuit tool's](circuit.md) JSON, run by ngspice, on its
board's copper when asked) and puts a line impedance stabilisation network (LISN, an artificial network)
in each supply line. It then takes the voltage at the networks' EUT terminals over a steady period to its
spectrum, reads it with a CISPR 16-1-1 receiver (peak, quasi-peak and average detectors), and sets it
against CISPR 32's or CISPR 25's limits. The study asks it as **Conducted emission** (group *EMC*).

```sh
python -m kicad2ems.pcbqs.conducted buck.json --out emc                  # CISPR 32 class B
python -m kicad2ems.pcbqs.conducted buck.json --standard cispr25 --cispr-class 5 \
    --cm-cap sw=15p --copper --out emc25
```

It is pre-compliance: it shows which lines are over a limit, by how much, and what is behind them
(differential or common mode). It is not a compliance result. Its answer is only as good as the
circuit model's edges and parasitics. Those are the switch model's capacitances, the diode's
junction capacitance, each part's ESL and the copper's inductance with `--copper`.

## What it does

1. **The circuit alone** runs to its steady state first. Only its end state is kept, not a reported
   run, and it runs without the copper, which moves the operating point little.
2. **The networks go in.** The model's DC supply (its one `dc_V` source, or `--source`) moves behind a
   network in its + line. Its minus becomes the reference, the ground plane the networks stand on,
   and the return line gets a network too. On a board, the networks' EUT ends take the source's own
   footprint pads (`board.parts`), so the copper between the connector and the parts is in the path.
3. **The circuit with the networks** starts where the circuit alone settled. The networks' inductors
   carry its input current and their capacitors sit at its DC voltage. It is run until it repeats,
   then once more at the strict tolerance with steps fine enough for the band: 8 samples a cycle of
   its top frequency, a power of two (1024 a period for a 250 kHz converter to 30 MHz).
4. **Each line's voltage** at its EUT terminal against the reference is read over the last period as
   a line spectrum, together with the differential mode (V+ - V-) / 2 and the common mode (V+ + V-) / 2.
5. **The receiver** reads each spectrum, and the worse of the two lines is set against the limits.

## The networks

| `--network` | Standard | Impedance at the EUT terminal | Realised as | Against the standard's |
|---|---|---|---|---|
| `cispr16` (CISPR 32's) | CISPR 16-1-2's 50 Ω / 50 µH + 5 Ω V-network, 9 kHz to 30 MHz | 50 ∥ (5 + jω 50 µH) | 50 µH from the EUT terminal; 5 Ω + 8 µF to the reference; 250 µH from the supply; the receiver's 50 Ω behind 1 µF | within 0.9 % from 150 kHz to 30 MHz, 15 % at 9 kHz (the standard allows 20 %), phase within 7° |
| `cispr25` (CISPR 25's) | the 5 µH / 50 Ω artificial network, 0.15 to 108 MHz | 50 ∥ jω 5 µH | 5 µH from the supply to the EUT terminal, 1 µF to the reference on the supply side, 50 Ω and 1 kΩ behind 0.1 µF | within 5 % |

The voltage reported is the EUT terminal's against the reference. That is the receiver's reading
corrected by the network's voltage-division factor, as the standards have it.

## The common mode

A converter's common-mode current flows from its fast nodes to the reference plane and back through
both networks. Two kinds of capacitance give it that path:

- **The board's copper.** Each model node's copper has a capacitance to a plane `--height-mm` below the
  board: 50 mm for CISPR 25 (the EUT on its support), 400 mm for CISPR 32 (its distance to the vertical
  reference plane). It comes from pcbqs's electrostatic kernel: the nets' copper is meshed and solved
  together for their charges, with the plane as their image. The board's dielectric is left out,
  because it changes a capacitance to a plane that far away very little. `--no-cm-board` leaves these
  capacitances out.
- **Your own.** `--cm-cap NODE=VALUE` adds a capacitance from a node to the reference: a heatsink on the
  switch node (10 to 30 pF is typical for a TO-220 tab on a chassis), or a chassis-mounted part.

The kernel is checked on a 20 mm square plate alone: Hwang and Mascagni's 0.3667874 × 4πε₀a within
0.6 % at a 1 mm mesh (1.1 % at 2 mm, 0.28 % at 0.5 mm). The plane's images are taken centroid to
centroid. That holds while the triangles are small against twice the height: millimetres against
100 mm or more.

## The receiver

| Band | Frequencies | RBW (6 dB) | QP charge / discharge | Meter |
|---|---|---|---|---|
| A | 9 to 150 kHz | 200 Hz | 45 ms / 500 ms | 160 ms |
| B | 0.15 to 30 MHz | 9 kHz | 1 ms / 160 ms | 160 ms |
| C | 30 MHz to 1 GHz | 120 kHz | 1 ms / 550 ms | 100 ms |

The steady state is periodic, so the voltage is a line spectrum at multiples of 1 / period. Tuned to f,
the IF filter passes Σ a_k H(f_k − f) e^{j2πf_k t}, which is exact in the steady state. H is a Gaussian
whose 6 dB width is the RBW.

The three detectors read that filter output's envelope over one period. Each indicates the RMS of a
sine (/√2), as a receiver does:

- **Peak:** the envelope's largest value.
- **Average:** its mean.
- **Quasi-peak:** the detector charges while the envelope is above it and discharges always, with
  CISPR 16-1-1's time constants. It is run to its own periodic state and read by a critically damped
  meter, scaled so a constant envelope reads itself.

Lines further apart than twice the RBW are read one at a time, and all three detectors agree. That is
the usual case: a converter's lines are its switching frequency apart. Closer lines beat in the filter
and the detectors part. This happens when the model's period covers many switching cycles, as in a
burst or a modulation. The receiver is tuned at every line, and every quarter RBW where lines are closer
than 2 RBW.

Checked against CISPR 16-1-1's pulse calibration, impulses that must read 66 dBµV quasi-peak ±1.5 dB:

| Band | Pulses | QP | Peak | Average |
|---|---|---|---|---|
| A | 13.5 µVs at 25 Hz | 65.6 dBµV | 72.2 (2A × the filter's impulse bandwidth, exactly) | 53.6 (2A × the rate, exactly) |
| B | 0.316 µVs at 100 Hz | 66.6 dBµV | 72.6 | 33.0 |
| C | 0.044 µVs at 100 Hz | 65.9 dBµV | 78.0 | 15.9 |

The QP's response to the repetition rate in band B, against the standard's, both relative to 100 Hz:

| Rate | Standard | Here |
|---|---|---|
| 1000 Hz | +4.5 ± 1.0 dB | +4.6 |
| 20 Hz | −6.5 ± 1.0 | −7.5 |
| 10 Hz | −10 ± 1.5 | −12.0 |
| 2 Hz | −20.5 ± 2 | −23.2 |
| 1 Hz | −22.5 ± 2 | −25.5 |

Counted in absolute terms, against the pulses that read 66 dBµV at 100 Hz as the standard's
reference is: at 10 Hz the reading is 1.4 dB under the standard's value, inside its ±1.5 dB. At 2 Hz
and 1 Hz it reads 2.1 and 2.5 dB under, just outside ±2 dB. The cause is not found; the meter is
modelled as the standard's critically damped one. Repetition that slow does not come up in a
converter's steady state.

What the receiver leaves out:

- The IF filter's shape beyond its 6 dB width. CISPR's mask allows a range; a Gaussian is taken.
- Its settling. The input is periodic, so none is needed.
- Anything not periodic in the circuit's period, such as spread-spectrum modulation or noise.

## Limits

| `--standard` | `--cispr-class` | Detectors | Bands |
|---|---|---|---|
| `cispr32` | A, B | QP, average | 0.15–0.5 MHz (B: 66→56 and 56→46 dBµV, log-linear), 0.5–5, 5–30 MHz |
| `cispr25` | 1 to 5 | peak, QP, average | LW 0.15–0.3, MW 0.53–1.8, SW 5.9–6.2, CB 26–28, VHF 30–54 and 68–87, FM 76–108 MHz |

These values are pcbqs's own transcription of the standards. Check them against the edition your test
plan names. CISPR 25 class 5's FM band (38 dBµV peak, 18 average) matches published application
notes, and each class above it is 6 to 10 dB higher, band by band. `--limits FILE` takes limits of your
own instead, one line per segment: `detector,f1_Hz,f2_Hz,dBuV1,dBuV2`, log-linear between f1 and f2.

A band's margin is its limit minus the reading at its worst frequency, per detector, for the worse of
L+ and L−. Where two bands meet, each is judged against its own limit, so the lower one governs.

## Options

| Option | Default | What it sets |
|---|---|---|
| `--standard` | `cispr32` | the limits: `cispr32` or `cispr25` |
| `--cispr-class` | `B` | A or B (CISPR 32); 1 to 5 (CISPR 25) |
| `--limits FILE` | | your own limits instead |
| `--network` | the standard's | `cispr16` or `cispr25` |
| `--source REF` | its one `dc_V` source | the supply the networks go behind |
| `--lines` | `+,-` | `+` alone leaves the return's network out (no common mode) |
| `--cm-cap NODE=VALUE` | | a capacitance from a node to the reference (repeat) |
| `--no-cm-board` | | the board's own capacitances to the reference left out |
| `--height-mm` | 50 (CISPR 25), 400 (CISPR 32) | the reference plane below the board |
| `--fmin`, `--fmax` | the limits' | the range read, Hz |
| `--copper` | | with the board's copper between the parts' pads |
| `--set PATH=VALUE` | | a model value set before running |

It writes these files:

- `<out>/conducted.json`: the standard, the network, the capacitances put in, and for each case its
  margins and worst frequencies.
- `<out>/circuit/<case>/conducted.csv`: the readings (frequency, band, detector, L+, L−, DM, CM, limit).
- `<out>/conducted.png`: the first case's readings, with peak as stems, QP and average as points and
  the limit lines.
- `<out>/circuit`: the circuit run with the networks; `<out>/alone` holds the run without them.

## An example: the buck converter

The pcbqs buck example (`circuit/examples/04_buck.json`): 12 V to 5 V at 2 A, 250 kHz, 22 µF at its
input and no input filter. It is run against CISPR 32 class B, with a 15 pF heatsink on its switch node:

| | CISPR 32 class B | with `--copper` |
|---|---|---|
| worst | −32.6 dB, average at 250 kHz (84.4 dBµV against 51.8) | −34.2 dB, average at 4.5 MHz |
| 0.5–5 MHz | −28.0 dB average at 750 kHz (74.0 dBµV) | |
| 5–30 MHz | −25.9 dB average at 5 MHz (71.9 dBµV) | |
| largest common-mode line | 73.1 dBµV at 2 MHz | |
| run time | 5.5 s | 5.7 s |

Its fundamental is differential mode (82.3 dBµV), and its input capacitor alone does not hold it
back. Above a few MHz the heatsink's common mode sets the lines. Against CISPR 25 class 5 (the 5 µH
network, to 108 MHz) it is 51 dB over at 76.75 MHz: 69.4 dBµV against the FM band's 18. That is the
common mode through the heatsink. The run takes 7.8 s.

## Checks

These are in `tests/pcbqs/test_conducted.py`:

- **A square wave into the networks.** A 1 V, 100 kHz square wave with 5 ns edges, behind 100 Ω between
  the board's supply and return. Each odd line at the EUT terminals is its Fourier series times
  Z / (R + 2Z), where Z is the network's impedance. The result is within 1.5 % to 30 MHz (cispr16) and
  within 0.8 % to 108 MHz (cispr25), with no common mode (below 2e-8 of the differential).
- **The common mode's path.** A 10 V square wave on a node with 100 pF to the reference, its current
  through the two networks in parallel back to the board. It matches the closed form within 2.2 %.
- **The networks' impedances** against the standards' (above).
- **The receiver.** A lone line reads as its RMS; CISPR 16-1-1's pulse calibration and rate response
  are as above.
- **The plate's capacitance** against the unit square's (above).

## What is left

See the *Power tools* section of ROADMAP.md: radiated emission from the switch node's loop and the
cables, a mains (AC) source with its rectifier, the networks' exact schematics (`cispr16` is realised
from its impedance), the plane's images near the board, and spread-spectrum modulation.
