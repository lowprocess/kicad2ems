# openEMS: simulation

[← openEMS meshing](meshing.md) · [Index](index.md) · [Next: mom25d →](mom.md)

`solvers/openems/simulation.py` turns the board and its mesh into an openEMS model and runs it.
`antenna.py` adds the far field in antenna mode. openEMS is imported only inside these functions, so the
rest of kicad2ems works without it.

## The model

| Part | openEMS / CSXCAD |
|---|---|
| dielectrics | materials extruded over the board outline |
| copper | perfect conductors, or conducting sheets with `--sigma` |
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
