# 12. Maintenance notes

[← Testing](testing.md) · [Index](index.md) · [Next: GUI →](gui.md)

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
- **Threads.** FDTD is memory-bound, so more threads are not always faster. On a 16-core / 24-thread
  desktop one run with 6 threads beat one with all 24, and two parallel runs with 12 threads each were
  4× slower. Try `--threads` 4–8 first. Defaults: all cores for one run, half the logical CPUs shared by
  the runs with `--jobs`. Results agree between thread counts to within the `--end-db` truncation.
- **Debye materials.** No `AddDebyeMaterial` helper: create `CSPropDebyeMaterial(CSX.GetParameterSet(),
  order=…, name=…, epsilon=…, kappa=…)`, `CSX.AddProperty(mat)`, then `SetDispersiveMaterialProperty`.
  Poles with τ < 2 Δt are ignored by openEMS.
- **NF2FF frequencies** must be known before the run (frequency-domain recording), so patterns are at
  the recorded frequency nearest the resonance unless `--ff-freq` is given.
- **openEMS output** is C++ `std::cout`: redirecting `sys.stdout` doesn't catch it; `progress.redirect_output`
  redirects file descriptors 1 and 2 and calls `fflush` before restoring them.

### KiCad-side gotchas

- Zones are read from their **filled** polygons: refill zones in KiCad before simulating.
- `Board` simplifies copper with a 2 µm tolerance, which can merge tiny jogs into long, slightly oblique
  edges; the mesher's Manhattan tolerance (`fine/10`) exists for this.
- Port footprints must point along x or y.

## Adding an option

Every setting is declared once, as an `options.Option`: the settings of every run in `config.CORE`, a
solver's own in its plugin's `options` list ([architecture](architecture.md#solver-plugins)).

1. Add an `Option(key, default, help, type, ...)` to the right list: `help` may quote the default as
   `{default}`; `choices` for fixed choices, `conv` for a converter (lists, regions: `options.strlist`,
   `floats(n)`, ...), `path=True` for a path (relative to the TOML file), `name` for its short name under
   `[solver.<name>]`, `gui` for a label in the GUI's form, `group` for the command-line and form group, `cli`
   for argparse extras (`flags`, `dest`, `action='append'`, `metavar`). The `Settings` field, the
   command-line option, the config keys and the form field follow from it.
2. Use it as `s.<key>` in the stage or plugin that needs it, passing plain values down (lower-level
   functions take keyword arguments, not the `Settings` object). Settings that cannot be used together go
   to the plugin's `validate(s)`.
3. If it changes the results, it is already in the summary's `settings`; put what it produces into
   `Job.summary` and, if useful, into the report.
4. Document it in `docs/usage.md`, test parsing / TOML in `test_cli.py` and its effect where it
   applies.

## Adding a solver

A solver is a plugin: a `solvers.Solver` subclass ([architecture](architecture.md#solver-plugins)). It gets
the `Board` (or, with `consumes = 'network'`, the line model) and returns a `solvers.Result`: S-parameters
over `Job.freq` for the board's ports as reported (differential pairs as their modes:
`sparams.mixed_mode`).

1. Declare `name`, `aliases`, `label`, `consumes`, `rank`, `capabilities` (every key of
   `solvers.FEATURES`) and `options`; implement `check()` (with the fix in its message), `solve()` and, where
   they apply, `validate()`, `assess()` (why it cannot model a board), `prepare()`, `outputs()`.
2. Declare what it changes in `deviations()` and read its native model back in `readback()`; add it to
   `tests/test_parity.py`.
3. Register it: in `solvers.BUILTIN` (in this repository), or with an entry point in the group
   `kicad2ems.solvers` (a package of its own). Nothing else in the pipeline, the report or the GUI needs a
   change: its Touchstone file is `<name>_<solver>.sNp`, it has its own report section and tick box. Give it
   a line style in `gui/traces.py` (`SOURCES`, `BY_SUFFIX`) if it should not be drawn solid.
4. Document it in `docs/usage.md` (the solvers table).

## Conventions

- Units: mm and Hz inside, GHz on the command line, KiCad coordinates in messages.
- openEMS / CSXCAD, scikit-rf and h5py imported lazily inside functions.
- Messages through `log` (in a `Job`: `self.log`); warnings contain `WARNING` so the report lists them;
  board-level remarks go to `Board.notes`.
- Plots return matplotlib `Figure` objects (no pyplot); `plots.save` writes them. The report reads only
  `Job.summary`: everything it shows must be in there.
- The version (currently 0.2.0) lives only in `kicad2ems/__init__.py` (`__version__`);
  `pyproject.toml` reads it, and the report and JSON summary show it.
- Code style: comments explain *why* (often a measured number); keep the existing density.
- Generated example boards are git-ignored; commit generators and configs, not outputs (except
  `examples/reference/`).

## Before committing

1. `python -m pytest -q`
2. `kicad2ems examples/<config>.toml --preview` for the examples your change touches.
3. A real run of an affected example ([Testing](testing.md#checking-against-openems)) when the solver
   setup, model or mesh changed, compared with the previous result.
4. Update these pages. The README only links to them: change it when a page or an install step is
   added.

## Ideas not done yet

Local mesh refinement at part gaps and ports regardless of `--fine`; frequency-dependent `nz`; detecting a
static plateau and stopping; a DC-free excitation; de-embedded (MSL) ports; automatic ports where
`--region` cuts a trace; coupling between line sections (coupled-line filters in the line model);
exact-frequency patterns via a first pass; polarisation cuts; multi-port antennas; automatic thread
count; resuming unchanged runs; thick copper; surface roughness; TDR; SPICE export; a KiCad plugin
(`KiCadBoard.from_text` and `--events` are its hooks); reporting the series-RLC instability upstream.

## License

GPL-3.0-or-later (`LICENSE`, and `license` in `pyproject.toml`), because openEMS's Python bindings
(GPL-3.0-or-later) run in the same process; the full reasoning is in the README's
[License](../README.md#license) section. A new dependency must be GPL-compatible. No third-party code is
copied into the repository (the line-impedance formulas are implemented from the papers cited in
`tlines.py`): keep it that way, or check the license of anything you copy in.

---
[← Testing](testing.md) · [Index](index.md) · [Next: GUI →](gui.md)
