# Testing

[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
pip install '.[test]'      # pytest, scikit-rf, h5py
python -m pytest -q        # about 320 tests, about a minute: no solver package or openEMS needed
tools/test_all.sh          # this suite, then kicad2ems-gui's, kicad2ems-fdtd's, mom25d's, gfdtd's (checkouts beside it)
```

kicad2ems's own tests run on stand-in solvers (`tests/standins.py`), whatever is installed: they need none of
the solver packages and give the same result on every machine. Tests that need an optional package are skipped
without it: scikit-rf, h5py, qucsator, PySide6 for the GUI, PyOpenGL for the 3D view.

`tools/test_all.sh` runs every suite, each package's from its checkout (`../kicad2ems-gui`, `../kicad2ems-fdtd`,
`../mom25d`, `../gfdtd`, or `KICAD2EMS_GUI_SRC`, `KICAD2EMS_OPENEMS_SRC`, `MOM25D_SRC`, `GFDTD_SRC`). Those test the GUI
and the real plugins against this checkout. It uses `$PYTHON`, the active venv or this checkout's `.venv`, and warns when that Python imports
a package from somewhere other than its checkout. The runs never see your settings: a scratch
`XDG_CONFIG_HOME`, the GUI offscreen. `--core` runs kicad2ems's suite alone, `--openems` adds openEMS to the
conformance test, and arguments after `--` go to every pytest (`tools/test_all.sh -- -x -k antenna`).

## What is covered

| Area | Files |
|---|---|
| KiCad and Gerber import | `test_kicad.py`, `test_kicad_shapes.py`, `test_importers.py` |
| the board model, mesh and parts | `test_model_mesh.py`, `test_parts.py` |
| the line model and its circuit solvers | `test_tlines.py`, `test_fastsolve.py` |
| the FDTD model, against strict stand-ins | `test_simulation_setup.py`, `test_dielectric.py`, `test_dumps.py` |
| field reading and animations | `test_fields.py` |
| antennas, parallel runs, progress | `test_antenna.py`, `test_parallel.py`, `test_progress.py`, `test_max_steps.py` |
| settings, the command line, solver choice, plugins | `test_cli.py`, `test_solvers.py`, `test_plugins.py` |
| what the FDTD model saw against the board | `test_parity.py` |
| boards with a known answer, on the real solvers | `test_conformance.py` |
| starting a run from another program | `test_runs.py` |

The solver plugins' own tests are in their packages: the FDTD layer (mesh, model, runs, dielectrics, antenna
runs, progress, the parity checks, the whole pipeline with canned runs, the preview), openEMS's import check,
`openems_venv` and `--converge`, and gfdtd's connection in kicad2ems-fdtd; the mom25d and mixed plugins, mom25d's
parity and mesh file in mom25d (`test_kicad2ems*.py`); gfdtd's engine in gfdtd. The window's tests (the form, the panels offscreen, the 3D view's arrays) are
in kicad2ems-gui, on the same stand-in solvers.

`test_conformance.py` is the one test that runs the real solver packages. Each run is `python -m kicad2ems` in a
process of its own, and a solver that is not installed or not usable is skipped. It runs openEMS only when
`KICAD2EMS_CONFORMANCE_OPENEMS=1` is set (`tools/test_all.sh --openems`).

## Fixtures

`tests/k2e_fixtures.py` holds the fixtures, shared with the solver packages' tests (their `conftest.py` imports it
from this checkout):

- `boards`: every generated example board, made once per session.
- `fake_openems`: strict stand-ins for CSXCAD and openEMS. They reject numpy scalars, as older CSXCAD
  does, and log every call so tests can check them. `no_csxcad`: a machine without openEMS.
- `run_cli`: runs `kicad2ems` in the test process and returns the exit code and output.
- `scratch_settings` (automatic): the GUI's settings and the site settings in a scratch folder, never yours.

A fixture's name must not start with `_`: `import *` leaves those out, so the solver packages would not get it.

`tests/standins.py` holds the stand-in solvers, which `tests/conftest.py` puts in place of the installed ones
before anything reads the registry:

- `openems` and `gfdtd`: kicad2ems's FDTD plugin class (the real code) on a stand-in engine that imports CSXCAD
  and openEMS (the real ones, or `fake_openems`'s). `standins.engine_check` is their import check.
- `mom25d`: refuses gap ports and boards without a ground plane, as mom25d does, and solves to a canned thru.
  `standins.solve` and `standins.status` can be replaced in a test.

They keep the real solvers' names, labels, order and the settings the tests use. `test_pipeline.py` also
replaces the FDTD runs with canned results, so everything after them runs for real.

## Checking against openEMS

Unit tests can't catch solver behaviour such as stability or accuracy. After changing the openEMS
simulation, parts, antenna, mesh or model code, run a few real cases and compare them with the reference
results:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2ems examples/demo_transition.toml --compare examples/reference/demo_transition_openems.s2p
python examples/make_antennas.py dipole examples/antenna_dipole
kicad2ems examples/antenna_dipole.toml       # expect 2.43 GHz, −20 dB, 2.1 dBi, 96 %
```

The same model run twice should agree to about 1e-3, because the end criterion is checked on a
wall-clock interval. A larger difference means the model or mesh changed:

```python
from kicad2ems.touchstone import read_touchstone
import numpy as np
_, a = read_touchstone('new.s2p'); _, b = read_touchstone('old.s2p')
print(np.abs(a - b).max())
```

---
[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)
