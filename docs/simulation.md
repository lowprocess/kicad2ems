# 8. Simulation

[← Transmission-line model](transmission-lines.md) · [Index](index.md) · [Next: Post-processing and outputs →](postprocessing.md)

`simulation.py` turns a `Model` and a mesh into an openEMS simulation and runs it; `antenna.py` adds the
far field for antenna mode. openEMS / CSXCAD are imported only inside these functions.

## Building the model: `build_fdtd`

`build_fdtd(m, mesh, fmin, fmax, excite, bc, end_db, sigma, max_steps, dumps, diel, diel_fref, exc_fmax)`
returns `(FDTD, CSX, ports)`:

| Part | CSXCAD / openEMS |
|---|---|
| grid | `SetDeltaUnit(1e-3)` (mm), the mesh lines |
| dielectrics | `AddMaterial` (or a Debye material, below) extruded over the outline (`AddLinPoly`), priority 1 |
| copper | `AddMetal` (PEC) or `AddConductingSheet` (`--sigma`, with the copper thickness), polygons at the copper heights, priority 10 |
| vias | `AddMetal` cylinders, priority 11 |
| edge walls | `AddMetal` zero-thickness boxes where the planes end at the `--edge-walls` sides (`mesh['walls']`), priority 11 |
| lumped parts | `AddLumpedElement` boxes (R / L / C, topology per piece) or a metal box for 0 Ω, priority 12 |
| ports | `AddLumpedPort` (direction `z`, or `x` / `y` for gap ports), priority 12; only port `excite` is driven |
| field dumps | `dumps.add_dumps` |

All numbers passed to CSXCAD go through `_f` / `_fl` (plain Python floats): older CSXCAD builds reject
numpy scalars. The test stand-ins enforce this.

<a id="dielectrics"></a>
## Dielectrics

- `--diel fixed` (default): εr from the stackup, the loss as a conductivity κ = 2π f0 ε0 εr tan δ at the
  band centre f0. tan δ then falls as 1/f across the band: too lossy below the centre, not lossy enough
  above.
- `--diel wideband`: the Djordjevic-Sarkar model. εr and tan δ from the stackup hold at `--diel-fref`
  (default 1 GHz); εr then falls slowly and tan δ stays nearly flat, as for real laminates. The run
  prints εr / tan δ of each dielectric at both band edges.

**The Debye fit** (`dielectric.fit_debye`): Debye poles log-spaced over [fmin / 2, 2 fmax] (fmin at
least fmax / 1000), 1.5 per decade (rounded up) plus one, at least 3, plus a conductivity, fitted by non-negative least
squares; fit errors < 0.2 % in εr and < 1 % in tan δ. openEMS ignores poles with a relaxation time below
2 time steps, so poles faster than 2.5 time steps are dropped. The material is a
`CSPropDebyeMaterial` (`simulation._debye_material`; how to build one: [maintenance](maintenance.md#other-quirks)).

Each pole costs run time: on the microstrip filter example (1–8 GHz) a wideband run took about 4× as long
as with `fixed`. There, the insertion loss in the 3.1 GHz passband came out at 6.0 dB instead of 7.4 dB
with `fixed`, whose conductivity overstates the loss below the band centre.

## Excitation

A Gaussian pulse: `SetGaussExcite(f0, fc)` with f0 = (fmin + fe)/2 and fc = 1.05·(fe − fmin)/2, where
fe = fmax (or `exc_fmax`). openEMS's pulse lasts 2·9/(2π·fc), so narrow bands mean long pulses
(`simulation.pulse_steps`, printed in the preview). Antenna runs use fe = fmax + 0.3·(fmax − fmin)
(`antenna.exc_fmax`), see [maintenance](maintenance.md#excitation-edge).

## Ending a run

openEMS stops when the field energy has fallen by `--end-db` (`EndCriteria = 10^(end_db/10)`) or after
`--max-steps` (`NrTS`). It checks the energy at its periodic status updates (on a wall-clock interval),
so runs with different speeds stop at slightly different steps; results agree to the truncation
level (≈ 0.002 in S at −40 dB).

The total field energy can stall above `--end-db` when some of it sits where the ports do not see it:
on the SMA launch example it levelled off near −36.5 dB while the port voltages had fallen 74 dB from
their peak, and the run would have gone on to `--max-steps`. So by default (`--end-on ports`) a run
also ends when the port signals have decayed: `simulation.PortWatch` reads the port voltage and current
probes every 2 s (openEMS flushes them as it runs) and, once past the excitation pulse, compares the
largest u² + (Z i)² of any port over the last 2 / fmin (at least 1 ns) with its peak. At `--end-db` it
writes `ABORT` into the run folder; openEMS stops and finishes normally, and `run_end.json` records
the level and time. The log says so, and the report's run table has an *ended on* column.
`--end-on energy` keeps openEMS's own criterion only. `--end-db -30` is usually enough for S-parameters
and faster still. Writing `ABORT` into a run folder by hand stops a run the same way.

`--preview` estimates the pulse length in timesteps and how many pulse lengths `--max-steps` allows,
with a warning below 20. Low frequencies make the pulse long (its length grows as 1 / bandwidth), and
high-Q circuits ring long after it, so both need more steps:

```bash
kicad2openems board.toml --fmin 0.05 --fmax 0.5 --max-steps 5000000
```

**Steps taken** (`steps_taken`): the last sample time of the port voltage probe divided by openEMS's own
time step, read from the excitation file `et` (one sample per step). `step_limit_warning` flags runs
that used ≥ 97 % of `--max-steps`: their fields may still have been ringing.

## Running: `execute`

`execute(FDTD, path, threads, verbose, progress)` calls `FDTD.Run(path, cleanup=True, …)` and restores
the working directory (openEMS changes it). With a `progress` setting (the pipeline always passes one):

1. `progress.redirect_output` points file descriptors 1 and 2 at `<path>/openems.log` (C++ output
   included; `fflush` before restoring).
2. A `progress.Monitor` thread tails the log and writes to a duplicate of the original stdout: a
   progress line (`Tracker.status`) or, with `--openems-output`, the raw lines. Each status line is also
   handed to `on_status` (the `--events` status events).

## One run per driven port: `run_excitations`

For each port in `--excite` (default all): `_run_port` builds the model with that port driven, runs it
in `<out>/openems/exc_<port>` (`P1+` / `P1-` → `exc_P1p` / `exc_P1n`), computes every port's incident /
reflected waves (`CalcPort`) and returns the S column. Dumps are recorded in the runs listed in
`--dump-port` (default the first). Returns a `RunResult`.

With `--jobs N`, `_run_port` runs in N `spawn` worker processes. The workers only redirect their output
to their logs (`progress={'mode': 'file'}`); one monitor in the main process shows all runs. Without
`--threads`, each worker gets half the logical CPUs / N (FDTD is memory-bound, see
[maintenance](maintenance.md#other-quirks)). The pipeline warns when N runs need more than 80 % of the
installed RAM.

`run_excitations` takes two optional hooks that every run calls: `setup(FDTD, CSX)` before the run (its
return value is the run's token) and `analyse(path, ports, token, k, col)` after it; what `analyse`
returns lands in `RunResult.extra[port]`. With `--jobs` the hooks must pickle (top-level functions or
`functools.partial`).

## Antenna runs: `antenna.run_antenna`

Antenna mode uses the same run path: `run_excitations` with one driven port (the first of `--excite`),
an NF2FF box as `setup` (`FDTD.CreateNF2FFBox(frequency=…)`, frequency-domain recording at 21 band
frequencies plus `--ff-freq`) and the far-field analysis as `analyse`. `Job.mesh` sets PML boundaries
and a larger air margin ([meshing](meshing.md#boundaries-and-air)). Post-processing is in
[Post-processing](postprocessing.md#antenna-mode).

## `--check`

`check_openems()` imports numpy, h5py, CSXCAD and openEMS and turns failures into hints (other Python,
venv without system site packages, missing module, shared library not found, the `kicad2openems` on
PATH running a different Python). The pipeline runs it before `solve()`. For users:
[Getting started](getting-started.md#checking-the-installation).

---
[← Transmission-line model](transmission-lines.md) · [Index](index.md) · [Next: Post-processing and outputs →](postprocessing.md)
