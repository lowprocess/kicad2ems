# EMerge

[← Index](index.md)

[EMerge](https://github.com/FennisRobert/EMerge) is a 3D finite-element (FEM) solver written in Python. kicad2ems
can use it as an optional third full-wave solver, `--solvers emerge`. It's **experimental**: it works on small
boards, but memory limits how fine a mesh it can use.

## Installing

EMerge 2.5 and later need Python 3.13 or older. kicad2ems runs EMerge as a separate process, so it can live in
a Python of its own:

```
python3.13 -m venv ~/emerge-venv
~/emerge-venv/bin/pip install emerge
kicad2ems board.toml --solvers emerge --emerge-python ~/emerge-venv/bin/python
```

`$KICAD2EMS_EMERGE_PYTHON` does the same as `--emerge-python`. If kicad2ems itself runs on Python 3.13,
`pip install kicad2ems[emerge]` puts EMerge next to it. `kicad2ems --check` shows whether it's found.

## How the board is modelled

EMerge reads the same board as the other solvers (`<name>_board.json`):

- each dielectric is a box over the outline's bounding box;
- copper is zero-thickness PEC sheets, holes cut out, and vias are PEC cylinders;
- each port is a lumped port from the reference copper to the trace, across its width, as in openEMS;
- each part is a lumped element (a resistive sheet). It spans pad to pad, with any track copper reaching into
  the gap cut away. With the copper-free span alone (what openEMS gets), the Wilkinson's tracks touched the
  resistor along half its width and its 100 Ω acted as about 200 Ω;
- the air box reaches `--emerge-margin` (3 mm) past the board on every side, with a first-order absorbing
  boundary (`--emerge-bc pec` for a closed metal box).

EMerge solves `--emerge-points` (11) frequencies over the band, and its rational fit gives the run's points.

| Setting | Default | |
|---|---|---|
| `--emerge-python` | this Python | the Python EMerge is installed in |
| `--emerge-points` | 11 | frequencies solved |
| `--emerge-cell` | half the narrowest port trace | mesh size along copper edges and on ports, mm |
| `--emerge-res` | 0.2 | largest element as a fraction of the wavelength |
| `--emerge-margin` | 3 | mm of air on every side |
| `--emerge-bc` | abc | `abc` (absorbing) or `pec` |
| `--emerge-workers` | 1 | frequencies solved at once; each needs the memory of a whole solve |

Not modelled: gap ports, edge walls, antenna mode, copper loss.

## How it compares

On one machine (24 threads, 31 GB), the default settings:

| Board | EMerge | openEMS | mom25d | EMerge time, RAM |
|---|---|---|---|---|
| microstrip thru: \|S21\| min, \|S11\| max, delay | 0.995, 0.074, +2.0 % | 0.98, 0.07, +2.0 % | 0.999, 0.036, ref. | 70 s, 4 GB |
| `rf_wilkinson` at 2.4 GHz: S21, S22, S32 | −3.26, −21.3, −32.6 dB | −3.35, −19.4, −33.2 dB | −3.31, −20.1, −38.7 dB | 18 s, 3 GB |
| `rf_gcpw` at 5 GHz: S21 | −0.45 dB | −0.39 dB | −0.31 dB | 30 s, 4 GB |
| `rf_coupler` at 2.4 GHz: S31 | −7.8 dB | −10.5 dB | −10.9 dB | 18 s, 3 GB |
| `rf_ratrace`: isolation null | 2.31 GHz | 2.24 GHz | 2.33 GHz | 49 s, 5 GB |
| `demo_lowpass`: S21 at 1 / 2 GHz | −7.9 / −40.7 dB | −7.9 / −40.7 dB | −8.4 / −46.2 dB | 59 s, 7 GB |
| `demo_bandpass`: S21 at 0.8 / 1.2 GHz | −3.4 / −24.8 dB | −4.4 / −26.9 dB | −3.5 / −26.4 dB | 65 s, 8 GB |

Solve times against the other two (mom25d on the GPU, openEMS on its default mesh):

| Board | EMerge | openEMS | mom25d |
|---|---|---|---|
| `rf_wilkinson` | 18 s | 26 s | 8 s |
| `rf_stubnotch` | 17 s | 35 s | 11 s |
| `rf_gcpw` | 30 s | 21 s | 30 s |
| `rf_ratrace` | 49 s | 15 s | 34 s |
| `demo_lowpass` | 59 s | 69 s | 19 s |
| `demo_bandpass` | 65 s | 81 s | 38 s |

EMerge solves each frequency directly, so a high-Q circuit costs it no more than a line: on the lumped filters
it's a little faster than openEMS, which needs about 200k timesteps to let them ring down. Its tetrahedra follow
curves, so the rat-race's ring isn't staircased as it is in openEMS. mom25d is faster still on all these
planar boards. EMerge's place is boards mom25d can't model (a finite ground, board edges, an enclosure) where
openEMS needs a fine mesh, as in the next example.

<a id="shielded"></a>
## Where EMerge is the best choice: a filter in a can

`rf_shielded` is a half-wave resonator side-coupled to two 50 Ω lines through 1 mm gaps, on 0.508 mm RO4003C,
in a closed metal can 4 mm round the board and above it: a band-pass about 30 MHz wide at 4.62 GHz.

| Solver | Peak | S21 at 4.62 GHz | Time |
|---|---|---|---|
| EMerge, default mesh (19k tetrahedra) | 4.621 GHz, −3.5 dB | −3.5 dB | 25 s, 3 GB |
| EMerge, cells along copper halved | 4.626 GHz, −3.6 dB | −3.6 dB | 64 s, 6 GB |
| openEMS, default mesh (0.04 M cells) | 4.555 GHz, −4.0 dB | −16.7 dB | 12 s |
| openEMS, 2× | 4.588 GHz, −3.9 dB | −11.7 dB | 40 s |
| openEMS, 4× (0.39 M cells, 690k timesteps) | 4.626 GHz, −4.0 dB | −4.0 dB | 175 s |
| mom25d (no can: it can't model one) | 4.647 GHz, −4.0 dB | −7.9 dB | 13 s |
| line model | 4.878 GHz, −3.0 dB | −26.0 dB | 1 s |

- **EMerge** is converged on its default mesh: halving its cells moves the peak by 0.1 %. Its walls are exact
  metal, and its mesh follows the strips.
- **openEMS** agrees once its mesh is 4× finer, but that takes 7 times as long. On its default mesh the peak
  is 66 MHz (1.4 %) low, two bandwidths, so it misses the pass band.
- **mom25d** can't model the can: its layers are laterally infinite. It gives the board without the can,
  25 MHz higher. `--solvers auto` still picks it here, so name the solver.
- **The line model** leaves out the open-end and coupling effects that set a half-wave resonator's
  frequency, so it's 5 % high.

EMerge on the same board *without* the can (`--emerge-bc abc`) is less sure: its first-order absorbing
boundary 4 mm out gives 4.596 GHz where mom25d gives 4.647 GHz. Closed problems suit it best.

The coupler shows the limit. Its 0.14 mm gap needs a fine mesh, but a 0.14 mm cell along all the copper gave
150k elements, and the direct solver ran out of memory (over 18 GB). A mesh refined only around gaps would fix
that. Memory is EMerge's main constraint in general: when it runs out, kicad2ems says so and suggests a coarser
`--emerge-cell` or `--emerge-res`.

---
[← Index](index.md)
