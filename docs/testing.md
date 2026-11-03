# Testing

[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
pip install '.[test]'      # pytest, scikit-rf, h5py
python -m pytest -q        # about 360 tests, under a minute: no solver package or openEMS needed
tools/test_all.sh          # this suite, then kicad2ems-gui's, kicad2ems-fdtd's, mom25d's, gfdtd's (checkouts beside it)
```

kicad2ems's own tests run on stand-in solvers (`tests/standins.py`), whatever is installed: they need none of the
solver packages and give the same result on every machine. Tests that need an optional package are skipped without
it: scikit-rf, h5py, qucsator, PySide6 for the GUI, PyOpenGL for the 3D view.

`tools/test_all.sh` runs each package's suite from its checkout (`../kicad2ems-gui`, `../kicad2ems-fdtd`, `../mom25d`,
`../gfdtd`, or `KICAD2EMS_GUI_SRC`, `KICAD2EMS_FDTD_SRC`, `MOM25D_SRC`, `GFDTD_SRC`), so the GUI and the real plugins
are tested against this checkout. It uses `$PYTHON`, the active venv or this checkout's `.venv`, warns when that
Python imports a package from somewhere other than its checkout, and keeps your settings out (a scratch
`XDG_CONFIG_HOME`, the GUI offscreen). `--core` runs kicad2ems's suite alone, `--openems` adds openEMS to the
conformance test, and arguments after `--` go to every pytest (`tools/test_all.sh -- -x -k antenna`). kicad2ems-ipc,
dfsynth and emsdev run their own (`python -m pytest` in each).

## What is covered

| Area | Files |
|---|---|
| KiCad and Gerber import | `test_kicad.py`, `test_kicad_shapes.py`, `test_importers.py` |
| the board model: region, parts, edge walls | `test_board.py`, `test_region.py`, `test_parts.py`, `test_edges.py` |
| the line model, its field solver and circuit solvers | `test_tlines.py`, `test_coupled.py`, `test_xsection.py`, `test_fastsolve.py` |
| fields, antennas, phased drives | `test_fields.py`, `test_dumps.py`, `test_antenna.py`, `test_phased.py` |
| settings, the command line, solver choice, plugins, the APIs and files | `test_cli.py`, `test_solvers.py`, `test_plugins.py`, `test_api.py`, `test_formats.py`, `test_sitecfg.py` |
| runs started by another program, the report, meshes, the installer | `test_runs.py`, `test_report.py`, `test_meshes.py`, `test_install.py` |
| boards with a known answer, on the real solvers | `test_conformance.py` |

The solver plugins' tests are in their packages: the FDTD layer (mesh, model, runs, dielectrics, antenna runs,
progress, parity, the pipeline with canned runs, the preview, openEMS's import check, `openems_venv`, `--converge`,
gfdtd's connection) in kicad2ems-fdtd; the mom25d and mixed plugins, their parity and mesh file in mom25d
(`test_kicad2ems*.py`); gfdtd's engine in gfdtd; the window (the form, the pages offscreen, the 3D view's arrays) in
kicad2ems-gui, on the same stand-in solvers.

`test_conformance.py` is the one test that runs the real solver packages, each run `python -m kicad2ems` in a
process of its own; a solver that is not installed or not usable is skipped. It runs openEMS only with
`KICAD2EMS_CONFORMANCE_OPENEMS=1` (`tools/test_all.sh --openems`).

## Fixtures

`tests/k2e_fixtures.py` holds the fixtures, shared with the solver packages' tests (their `conftest.py` imports it
from this checkout):

- `boards`: every generated example board, made once per session.
- `fake_openems`: strict stand-ins for CSXCAD and openEMS. They reject numpy scalars, as older CSXCAD does, and log
  every call so tests can check them. `no_csxcad`: a machine without openEMS.
- `run_cli`: runs `kicad2ems` in the test process and returns the exit code and output.
- `scratch_settings` (automatic): the GUI's settings and the site settings in a scratch folder, never yours.

A fixture's name must not start with `_`: `import *` leaves those out, so the solver packages would not get it.

`tests/standins.py` holds the stand-in solvers, which `tests/conftest.py` puts in place of the installed ones before
anything reads the registry: `openems` and `gfdtd` (kicad2ems-fdtd's real plugin class on a stand-in engine;
`standins.engine_check` is their import check), and `mom25d`, which refuses gap ports and boards without a ground
plane, as mom25d does, and solves to a canned thru (`standins.solve` and `standins.status` can be replaced in a
test). They keep the real solvers' names, labels, order and the settings the tests use.

## Checking against openEMS

Unit tests can't catch solver behaviour such as stability or accuracy. After changing the FDTD model, parts,
antenna, mesh or model code, run a few real cases and compare them with the reference results:

```bash
kicad2ems examples/demo_transition.toml --compare examples/reference/demo_transition_openems.s2p
kicad2ems examples/antenna_dipole.toml       # expect 2.43 GHz, −20 dB, 2.1 dBi, 96 %
```

The same model run twice agrees to about 1e-3, because openEMS checks its end criterion on a wall-clock interval;
a larger difference means the model or mesh changed:

```python
from kicad2ems.touchstone import read_touchstone
import numpy as np
_, a = read_touchstone('new.s2p'); _, b = read_touchstone('old.s2p')
print(np.abs(a - b).max())
```

---
[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)
