# Maintenance notes

[← Testing](testing.md) · [Index](index.md)

## openEMS quirks

Each of these took real debugging time to find. Check here before changing the related code.

<a id="series-rlc"></a>
### Series lumped elements are unstable without a capacitor

openEMS 0.37's series lumped element keeps an undamped DC mode when there's no capacitor in the branch.
A series 8.2 nH + 0.4 Ω element made the energy grow until it overflowed. The parallel topology with
only L, and anything containing a C, are stable.

So `parts.Part.pieces` puts a series resistance on its own half of the gap, and uses the parallel
topology for an inductor without C. To test stability, run 30,000 steps with `--end-db -200` and watch
the energy.

<a id="static-plateau"></a>
### Bands starting near 0 Hz leave a static field

The excitation of a band that starts near DC has a DC component. It leaves a static field that never
decays, so the energy levels off. The default stop (`--end-on ports`) handles it: the run ends once the
port signals have decayed and the energy has stopped falling. On the low-pass example that is after 200k
steps, with S within 1e-4 of a 300k-step run. Don't loosen `--end-db` to the plateau instead: stopping at
−27 dB left errors of 0.03 in |S|. The progress line shows `energy flat` when this happens.

<a id="excitation-edge"></a>
### The far field is wrong near the top of the excitation band

In the top 15 % or so of the pulse's band, the far field came out wrong (a dipole showed 107 %
efficiency), while S11 looked fine. Antenna runs therefore extend the excitation above `fmax` by 30 % of the band.

<a id="other-quirks"></a>
### Other quirks

- **numpy scalars.** Older CSXCAD rejects them. Pass every number through `simulation._f` / `_fl`.
- **Working directory.** `FDTD.Run` changes it; `simulation.execute` restores it.
- **When a run stops.** openEMS checks the energy on a wall-clock interval, so the stopping step varies
  slightly between runs.
- **Time step.** openEMS's is about 10 % smaller than kicad2ems's estimate. The real one is in the run
  folder's `et` file.
- **Threads.** FDTD is limited by memory bandwidth, so more threads aren't always faster. On a 24-thread
  desktop, one run with 6 threads beat one with 24. Try `--threads` 4 to 8.
- **Debye materials** have no helper in openEMS's Python API. Create a `CSPropDebyeMaterial`, add it with
  `CSX.AddProperty`, then call `SetDispersiveMaterialProperty`. Poles faster than 2 time steps are ignored.
- **Far-field frequencies** must be chosen before the run.
- **openEMS's output** is C++ `std::cout`, so redirecting `sys.stdout` doesn't catch it.
  `progress.redirect_output` redirects the file descriptors instead.

### KiCad gotchas

- Zones are read from their **filled** polygons, so refill zones in KiCad before simulating.
- Copper is simplified to 2 µm, which can turn tiny jogs into long, slightly slanted edges. The mesher's
  straight-edge tolerance exists for this.
- Port footprints must point along x or y.

## Adding an option

Every setting is declared once, as an `options.Option`: the run's own in `config.CORE`, each solver's in
its plugin ([architecture](architecture.md#solver-plugins)).

1. Add the `Option` to the right list, with its default, help text, type, and any of: choices, a
   converter, `path=True` for paths, a short name for the solver table, a GUI label and a group. The
   settings field, command-line option, config key and GUI field all follow from it.
2. Use it as `s.<key>` where it's needed. Pass plain values to lower-level functions, not the `Settings`.
   Settings that can't be combined go in the plugin's `validate`.
3. Put anything it produces into `Job.summary` and, if useful, the report.
4. Document it in `docs/usage.md` and test it.

## Adding a solver

A solver is a plugin ([architecture](architecture.md#solver-plugins)). It gets the `Board`, or the line
model with `consumes = 'network'`, and returns a `solvers.Result` with S-parameters for the board's ports.

1. Declare `name`, `aliases`, `label`, `consumes`, `rank`, `capabilities` and `options`. Implement
   `check()`, which should say how to fix a problem, and `solve()`. Implement `validate()`, `assess()`,
   `prepare()`, `outputs()` and `estimate()` where they apply.
2. Declare what it changes from the board in `deviations()`, read its model back in `readback()`, and add
   it to `tests/test_parity.py`.
3. Register it in `solvers.BUILTIN`, or as an entry point in its own package. Nothing else needs to
   change; the pipeline, report and GUI pick it up.
4. Add it to the solver table in `docs/usage.md`.

## Conventions

- Lengths in mm, frequencies in Hz inside the code and GHz on the command line. Messages give KiCad
  coordinates.
- Import openEMS, CSXCAD, scikit-rf, h5py and other heavy packages inside the functions that use them.
- Send messages through `log`. Warnings contain `WARNING`, so the report lists them.
- Plots return matplotlib `Figure` objects without pyplot. The report reads only `Job.summary`.
- The version lives only in `src/kicad2ems/__init__.py`.
- Comments explain *why*, often with a measured number.
- Generated example boards aren't committed; their generators and configs are.

## Before committing

1. `tools/test_all.sh` (or `python -m pytest -q` for kicad2ems alone; the solver packages' suites test their
   plugins against this checkout)
2. `kicad2ems examples/<config>.toml --preview` for the examples your change touches.
3. A real run of an affected example when the solver setup, model or mesh changed
   ([testing](testing.md#checking-against-openems)).
4. Update the docs. A new API name goes on [APIs](api.md) (`tests/test_api.py` checks every name is there and
   has a docstring).

<a id="documentation-site"></a>
## The documentation site

The pages here are also a Sphinx site (Read the Docs: `.readthedocs.yaml`), with a code reference of the whole
stack made from the installed packages' docstrings and, through Doxygen, the compiled cores:

```bash
python -m pip install -r docs/requirements.txt         # Sphinx, MyST, Furo, Breathe (and the stack's packages)
sphinx-build -b html docs docs/_build/html             # doxygen on PATH; about 20 s, warnings are errors to fix
```

`docs/conf.py` explains the build: the pages stay plain Markdown for GitHub, docstrings are shown as written,
and every link between the built pages is checked. CI builds it on every push and pull request (`.github/workflows/docs.yml`, a
warning fails it) and publishes it to GitHub Pages from main; nightly too, as the code reference follows the other
repositories' main branches.

Planned work is in [ROADMAP.md](https://github.com/lowprocess/kicad2ems/blob/main/ROADMAP.md).

## License

GPL-3.0-or-later, because openEMS's Python bindings (GPL-3.0-or-later) run in the same process. New
dependencies must be GPL-compatible. No third-party code is copied into the repository; the impedance
formulas are implemented from the papers cited in `src/kicad2ems/lines/tlines.py`.

---
[← Testing](testing.md) · [Index](index.md)
