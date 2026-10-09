# 11. Maintenance notes

[← Testing](testing.md) · [Index](index.md)

## openEMS behaviours and their workarounds

Each of these cost real debugging time. Check here before changing the related code.

<a id="series-rlc"></a>
### Series-topology lumped elements are unstable without a C

openEMS 0.37's lumped RLC extension discretises the **differentiated** branch equation for the series
topology (`LEtype=1`). Without a capacitor in the branch it keeps an undamped DC mode: a series
8.2 nH + 0.4 Ω element made the energy grow by ~5 dB per 9000 steps until it overflowed (`Energy: ~inf`),
and a lone series L drifted upwards slowly. The parallel topology with only L (a plain inductor
integrating the voltage) and anything containing a C are stable.

**Workaround** (`parts.Part.pieces`): a resistance in series with L / C becomes its own resistive
piece on half the gap; an inductor without C uses the parallel topology. openEMS adds a small
parallel capacitance to inductor elements (self-resonance ≈ 4.5 × fmax, `LUMPED_RLC_Z_FACT = 20`),
under 0.5 % error well below fmax. If a future openEMS fixes the series update, `pieces()` can go back
to one series element. A stability probe: run 30 000 steps with `end-db -200` and watch the energy.

<a id="static-plateau"></a>
### Static plateau with sweeps starting near 0 Hz

The Gaussian excitation of a band starting near DC has a DC component. It leaves a static field that
never decays, so the energy levels off (e.g. at −28 dB) and the run goes to `--max-steps`. The
S-parameters are not affected (a run stopped at the plateau differs by < 0.01 from a 1M-step run).
Use an `--end-db` just above the plateau (the low-pass example uses −27), or start the band higher. The
progress line says `energy flat` when this happens.

<a id="excitation-edge"></a>
### The far field is wrong near the top of the excitation band

In the top ~15 % of the Gaussian's band (its −20 dB edge), the NF2FF far field came out wrong (a
dipole showed 6.3 dBi and 107 % efficiency at fmax) while S11 looked fine. It is not truncation or the
mesh. Antenna runs therefore extend the excitation to fmax + 30 % of the band (`antenna.exc_fmax`).

### Other quirks

- **numpy scalars.** Older CSXCAD builds type-check `int` / `float` and reject numpy scalars: pass every
  number through `simulation._f` / `_fl`. The test stand-ins enforce it.
- **Working directory.** `FDTD.Run` changes the working directory; `simulation.execute` restores it.
- **End criterion timing.** openEMS checks the energy only at its status updates (wall-clock interval),
  so the stopping step varies between runs and machines; results differ by ~1e-3.
- **Time step.** openEMS's step (Rennings method) is ~10 % smaller than the mesher's CFL estimate
  (`info['dt_ps']`). The real step is in the run folder's `et` file (one sample per step).
- **Threads.** FDTD is memory-bound. On a 16-core / 24-thread desktop 6 threads beat 24, and two runs
  with 12 threads each were 4× slower. Defaults: all cores for one run, half the logical CPUs shared by
  `--jobs`.
- **Debye materials.** No `AddDebyeMaterial` helper: create `CSPropDebyeMaterial(CSX.GetParameterSet(),
  order=…, name=…, epsilon=…, kappa=…)`, `CSX.AddProperty(mat)`, then `SetDispersiveMaterialProperty`.
  Poles with τ < 2 Δt are ignored by openEMS.
- **NF2FF frequencies** must be known before the run (frequency-domain recording), so patterns are at
  the recorded frequency nearest the resonance unless `--ff-freq` is given.
- **openEMS output** is C++ `std::cout`: redirecting `sys.stdout` doesn't catch it; `progress.redirect_output`
  redirects file descriptors 1 and 2 and calls `fflush` before restoring them.

### KiCad-side gotchas

- Zones are read from their **filled** polygons: refill zones in KiCad before simulating.
- `Model` simplifies copper with a 2 µm tolerance, which can merge tiny jogs into long, slightly oblique
  edges; the mesher's Manhattan tolerance (`fine/10`) exists for this.
- Port footprints must point along x or y.

## Adding a command-line option

1. `cli.parser()`: add it to the right group, with a help text that says the default.
2. If it takes a list, add its name to `config.LIST_KEYS` (or `KEEP_LIST` when items contain commas);
   if it is a path, to `config.PATH_KEYS`.
3. Use it in the relevant `Job` stage (`pipeline.py`), passing plain values down (lower-level functions
   take keyword arguments, not the `args` object).
4. If it changes the results, put it in `Job.info` (JSON) and, if useful, in the report.
5. Document it in `docs/usage.md` (and the README if it is common), test parsing / TOML in `test_cli.py`
   and its effect where it applies.

## Conventions

- Units: mm and Hz inside, GHz on the command line, KiCad coordinates in messages.
- openEMS / CSXCAD imported lazily inside functions.
- Messages through `log` (in a `Job`: `self.log`); warnings contain `WARNING` so the report lists them;
  model-level remarks go to `Model.notes`.
- Code style: comments explain *why* (often a measured number); keep the existing density.
- Generated example boards are git-ignored; commit generators and configs, not outputs (except
  `examples/reference/`).

## Before committing

1. `python -m pytest -q`
2. `kicad2openems examples/<config>.toml --preview` for the examples your change touches.
3. A real run of an affected example ([Testing](testing.md#checking-against-openems)) when the solver
   setup, model or mesh changed, compared with the previous result.
4. Update these pages and the README.

## Ideas not done yet

Local mesh refinement at part gaps and ports regardless of `--fine`; frequency-dependent `nz`; detecting a
static plateau and stopping; a DC-free excitation; de-embedded (MSL) ports; exact-frequency patterns via
a first pass; polarisation cuts; multi-port antennas; automatic thread count; resuming unchanged runs;
thick copper; surface roughness; TDR; SPICE export; reporting the series-RLC instability upstream.

---
[← Testing](testing.md) · [Index](index.md)
