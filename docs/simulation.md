# openEMS: simulation

[← openEMS meshing](meshing.md) · [Index](index.md) · [Next: mom25d →](mom.md)

In the [kicad2ems-fdtd](https://github.com/lowprocess/kicad2ems-fdtd) package, `model.py` turns the board and its
mesh into an openEMS model and `run.py` runs it, on openEMS or on gfdtd, which has openEMS's Python interface
(`engines.py`); `antenna.py` adds the far field. openEMS is imported only by its engine (`openems/engine.py`).
What follows holds for gfdtd too, except that it fits `--rough` over the band ([gfdtd](gfdtd.md)).

## The model

| Part | openEMS / CSXCAD |
|---|---|
| dielectrics | materials extruded over the board outline |
| copper | perfect conductors, or conducting sheets with `--sigma` (`--rough`: roughness); with `--thickness on` solid, with lossy faces |
| vias | metal cylinders |
| edge walls | thin metal boxes where the planes end |
| lumped parts | lumped R/L/C elements across their gaps |
| ports | lumped ports; only the driven port is excited |
| field dumps | dump boxes |

Every number passed to CSXCAD is converted to a plain Python float, because older CSXCAD builds reject
numpy values.

<a id="dielectrics"></a>
## Dielectrics

- **`--diel fixed`** (the default) uses the stackup's εr, with the loss as a conductivity set at the
  band centre. That makes the loss too high below the centre and too low above it.
- **`--diel wideband`** uses the Djordjevic-Sarkar model, which matches real laminates: εr falls slowly
  with frequency and tan δ stays nearly flat. The stackup values apply at `--diel-fref` (1 GHz). It is
  built from a sum of Debye poles. Each pole costs run time: on the microstrip filter the run took about
  four times as long, and its pass band then matched mom25d's within 0.05 dB (the fixed model had 1.3 dB
  more loss). Use it for narrowband filters and wherever the loss matters.

<a id="roughness"></a>
## Copper roughness

`--rough` (µm RMS, with `--sigma`) multiplies the copper's surface resistance by Hammerstad and Jensen's factor
1 + 2/π atan(1.4 (rms / δ)²): 1 where the skin depth δ is much larger than the roughness, 2 where it is much
smaller. With 1 µm on copper that is 1.20 at 1 GHz, 1.65 at 5 GHz and 1.81 at 10 GHz. mom25d applies it at each
frequency and gfdtd over the band (a wideband fit of its conducting sheets). openEMS and the line model take one
conductivity, the rough copper's at the band centre (σ / K²), as `--diel fixed` does with the dielectric's loss: the
loss is right there and off by K(f) / K(centre) elsewhere. On a 40 mm microstrip with 1 µm, gfdtd's conductor loss
rose by 1.43× at 2.5 GHz and 1.84× at 18.5 GHz (Hammerstad: 1.43, 1.89).

<a id="finish"></a>
## Surface finish

`--finish` (with `--sigma`; default `auto`, the stackup's copper finish in KiCad's Board Setup) puts the plating of
the outer copper on its outer face and sides, where no solder mask covers it. The face against the dielectric stays
bare copper, and so do the inner layers and copper under mask (with `--mask` the finish is left out, with a note: mask
openings aren't modelled). Each finish layer loads the copper face as a section of line, Z = γ / σ:

| `--finish` | layers (typical; a fab's own figures are better) | face resistance / bare copper's, 1 / 3 / 10 GHz |
|---|---|---|
| `enig` | 5 µm electroless Ni-P (1.45 MS/m, non-magnetic as plated), 0.08 µm Au | 2.2 / 4.4 / 5.3 |
| `enepig` | 5 µm Ni-P, 0.1 µm Pd, 0.05 µm Au | 2.2 / 4.4 / 5.5 |
| `hasl` | 5 µm lead-free SnCu (HASL varies 1–25 µm) | 2.3 / 2.9 / 2.8 |
| `hasl-snpb` | 5 µm Sn63Pb37 | 2.3 / 3.0 / 2.9 |
| `imsn` | 1 µm immersion tin | 1.07 / 1.2 / 1.7 |
| `imag` | 0.2 µm immersion silver | 1.0 |
| `none`, `osp` | bare copper | 1 |

What a line sees is that times the share of its loss on the finished faces: from the 2D field solver for the narrowest
port trace on each outer layer, 45 % of the strip's own loss on hairpin's 0.37 mm line (20 % on the outer face, 25 %
on the sides, where the edge current runs), less of the line's with the plane's. mom25d weights its surface impedance
by that share at each frequency, gfdtd's zero-thickness sheets over the band, and the line model and openEMS take the
conductivity of the same loss at the band centre. With `--thickness on` the outer face is a sheet of its own (gfdtd:
the finished face's wideband fit; openEMS: its conductivity at the band centre), but the slab's sides are lossless
there, so that model leaves out the sides' share, finished or bare. On hairpin with ENIG (`--sigma 5.8e7`) the peak
S21 fell by 3.4 dB in mom25d, 2.6 dB in gfdtd's sheets, 2.5 dB in the line model, 1.5 dB with `--thickness on` (the
outer face only), and the pass band moved down 8–24 MHz with the nickel's internal inductance.

## Excitation

openEMS is driven with a Gaussian pulse covering the band. A narrow band means a long pulse, so low
frequencies and narrow bands need more timesteps. Antenna runs extend the pulse above `fmax` by 30 % of the band,
because the far field is wrong near the top edge of the excitation (see
[maintenance](maintenance.md#excitation-edge)).

<a id="ending-a-run"></a>
## When a run stops

openEMS stops when the field energy has decayed by `--end-db` (−40 dB) or after `--max-steps` timesteps.

Sometimes the energy stalls above `--end-db` because it is trapped where the ports can't see it. On the
SMA launch it levelled off near −36.5 dB while the port signals had fallen 74 dB. So by default
(`--end-on ports`), a run also stops when **both**:

- the port signals have decayed by `--end-db` from their peak, and
- the energy has stopped falling (less than 1.5 dB over the last quarter of the run).

Both are needed. A high-Q filter leaks its stored energy slowly through the ports while their signals are
already low, and stopping on the ports alone smoothed its pass band. `--end-on energy` uses openEMS's own
criterion only.

The port signals are judged over a window of two periods of the slowest ringing they can carry: two
periods at `fmin`. A board without lumped parts on its signal nets can't ring below its lowest cavity mode,
c0 / (2 D √εr) over the outline's diagonal D, so there the window is at most two periods of that mode (at
least 1 ns): a band from 0.1 GHz would otherwise wait 20 ns before the criterion could fire.

`--end-db -30` is usually enough for S-parameters, and faster. You can also stop a run by hand: writing
a file called `ABORT` into its run folder makes openEMS finish cleanly. The GUI's *Stop* does this.

`--preview` estimates the pulse length and warns when `--max-steps` allows fewer than 20 pulse lengths.
For low frequencies, raise it:

```bash
kicad2ems board.toml --fmin 0.05 --fmax 0.5 --max-steps 5000000
```

A run that used at least 97 % of `--max-steps` gets a warning, since its fields may still have been
ringing.

## Runs

There's one openEMS run per driven port (`--excite`, by default every port), each in
`<out>/openems/exc_<port>/`. Each run gives one column of the S-matrix.

`--jobs N` runs N ports at once in separate processes. Each then gets a share of the CPU threads, since
FDTD is limited by memory bandwidth (see [maintenance](maintenance.md#other-quirks)). kicad2ems warns when
the runs would need more than 80 % of the RAM.

openEMS's output goes to each run's `openems.log`, and a progress line is shown instead.

## Antenna runs

Antenna mode uses one driven port, absorbing PML boundaries, more air, and a near-to-far-field box that
records 21 frequencies across the band plus the pattern frequencies. The results are described in
[Results](postprocessing.md#antenna-mode).

---
[← openEMS meshing](meshing.md) · [Index](index.md) · [Next: mom25d →](mom.md)
