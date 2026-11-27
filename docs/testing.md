# Testing

[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
pip install '.[test]'           # pytest, scikit-rf, h5py
python -m pytest -q tests/core  # the core's suite: no solver or openEMS needed
tools/test_all.sh               # every part's suite, each in its own pytest
```

The core's tests run on stand-in solvers (`tests/standins.py`), whatever is installed: they need none of the
solvers and give the same result on every machine. Tests that need an optional package are skipped without it:
scikit-rf, h5py, qucsator, PySide6 for the GUI, PyOpenGL for the 3D view.

`tools/test_all.sh` (`tools/test_all.py`) runs one pytest for each part's folder under `tests/` (core, gui, fdtd,
gfdtd, mom25d, pcbqs, fem, ipc, dfsynth, emsdev), since their conftests set up their own paths and fixtures. It
uses `$PYTHON`, the active venv or this checkout's `.venv`, and keeps your settings out (a scratch
`XDG_CONFIG_HOME`, the GUI offscreen). Parts named run alone (`tools/test_all.sh core mom25d`), `--openems` adds
openEMS to the conformance test, and arguments after `--` go to every pytest (`tools/test_all.sh -- -x -k
antenna`).

## What is covered

| Area | Files |
|---|---|
| KiCad and Gerber import | `test_kicad.py`, `test_kicad_shapes.py`, `test_importers.py` |
| the board model: region, parts, edge walls | `test_board.py`, `test_region.py`, `test_parts.py`, `test_edges.py` |
| the line model, its field solver and circuit solvers | `test_tlines.py`, `test_coupled.py`, `test_xsection.py`, `test_fastsolve.py` |
| fields, antennas, phased drives | `test_fields.py`, `test_dumps.py`, `test_antenna.py`, `test_phased.py` |
| settings, the command line, solver choice, the registries, the APIs and files | `test_cli.py`, `test_solvers.py`, `test_solver_registry.py`, `test_registry_lists.py`, `test_api.py`, `test_formats.py`, `test_sitecfg.py` |
| runs started by another program, the report, meshes, the installer | `test_runs.py`, `test_report.py`, `test_meshes.py`, `test_install.py` |
| boards with a known answer, on the real solvers | `test_conformance.py` |

The table is `tests/core`'s. The solvers' tests are in their parts' folders: the FDTD layer (mesh, model, runs,
dielectrics, antenna runs, progress, parity, the pipeline with canned runs, the preview, openEMS's import check,
`openems_venv`, `--converge`, gfdtd's connection) in `tests/fdtd`; the mom25d solver, its parity and
mesh file in `tests/mom25d` (`test_kicad2ems*.py`); gfdtd's engine in `tests/gfdtd`; the window (the form, the
pages offscreen, the 3D view's arrays) in `tests/gui`, on the same stand-in solvers.

`test_conformance.py` is the one test that runs the real solver packages, each run `python -m kicad2ems` in a
process of its own; a solver that is not installed or not usable is skipped. It runs openEMS only with
`KICAD2EMS_CONFORMANCE_OPENEMS=1` (`tools/test_all.sh --openems`).

## The window's flows, recorded

`tools/gui/flows.py` drives the window offscreen through eight flows as a user would (the Start page and an
example opened, S-parameters by two solvers, crosstalk, board coupling, a circuit, settings typed, undone and
saved, Filter Synthesis, the Runs page), with real runs, settings and examples of its own. Each flow becomes
an animated GIF in `build/gui-flows/`, beside `flows.md`: each flow's steps, its time and its errors (an
exception in a slot, a Qt warning, a run that failed or timed out, a Run-page log not following its run).
Run it after a change to the window, and look at the GIFs: a page that is empty raises no error.

## The 3D view without a display

The window's 3D views (the Draw page, the Viewer's 3D) use OpenGL, which Qt's `offscreen` platform does not
give: offscreen they draw nothing, and their tests check the numbers (cameras, rays, picking) alone. To see them
drawn on a machine with no display, run a private Wayland compositor on a virtual framebuffer and the window in it
on Mesa's software OpenGL (llvmpipe). KWin has the virtual backend; its own D-Bus session keeps it apart from a
desktop running beside it:

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)
LIBGL_ALWAYS_SOFTWARE=1 KWIN_COMPOSE=Q dbus-run-session -- \
    kwin_wayland --virtual --width 1700 --height 1100 --socket k2e-gl --no-lockscreen &
WAYLAND_DISPLAY=k2e-gl QT_QPA_PLATFORM=wayland LIBGL_ALWAYS_SOFTWARE=1 python your_script.py
```

In it `widget.grab()` includes the GL views (`llvmpipe ... 4.6 (Compatibility Profile)` on Fedora 44's Mesa
26.2). Stop the compositor by its own PID when done. Any Wayland compositor with a headless backend will do
(`weston --backend=headless`, `sway` with `WLR_BACKENDS=headless`), as will `xvfb-run` where Xvfb is installed.
A script that makes more than one GL view (two Draw pages, say) sets `Qt.AA_ShareOpenGLContexts` before its
`QApplication`, as the window does (`kicad2ems.gui`): without it the second fails in `glUseProgram` (`GLError`).

## The GPUs, before a release

CI has no GPU: its runners have Mesa's CPU Vulkan (lavapipe), on which mom25d's Vulkan LU and gfdtd's GPU tests run
(`EMSDEV_VULKAN_CPU=1`; locally `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json` keeps a real GPU
out), which shows the kernels compile and agree, not their speed. On a machine with GPUs, before a release,
`tools/gpu_report.py` runs what CI cannot and writes
`gpu-report/<date>.md` and `gpu-report/latest.json`, to commit with the release:

```bash
python tools/gpu_report.py           # the GPU tests, and two boards on each GPU backend against the CPU
python tools/gpu_report.py --check   # is the latest report for the GPU parts' code as it is now?
```

It records the GPUs and drivers, the repository's commit and each GPU part's last commit (gfdtd, mom25d, pcbqs,
emsdev, fdtd), the GPU tests (passed, failed, and skipped with why) and, on demo_lowpass and rf_coupler, the largest
|ΔS| of each GPU run against its solver's CPU run, with the wall times. `--check` reads the report as out of date
once a later commit touches one of those parts.

## Fixtures

`tests/core/k2e_fixtures.py` holds the fixtures, shared with the solver packages' tests (their `conftest.py` imports it
from this checkout):

- `boards`: every generated example board, made once per session.
- `fake_openems`: strict stand-ins for CSXCAD and openEMS. They reject numpy scalars, as older CSXCAD does, and log
  every call so tests can check them. `no_csxcad`: a machine without openEMS.
- `run_cli`: runs `kicad2ems` in the test process and returns the exit code and output.
- `scratch_settings` (automatic): the GUI's settings and the site settings in a scratch folder, never yours.

A fixture's name must not start with `_`: `import *` leaves those out, so the solver packages would not get it.

`tests/standins.py` holds the stand-in solvers, which `tests/core/conftest.py` puts
in place of the installed ones before anything reads the registry: `openems` and
`gfdtd` (kicad2ems-fdtd's real solver class on a stand-in engine;
`standins.engine_check` is their import check), and `mom25d`, which refuses gap
ports and boards without a ground plane, as mom25d does, and solves to a canned
thru (`standins.solve` and `standins.status` can be replaced in a test). They
keep the real solvers' names, labels, order and the settings the tests use.

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
from kicad2ems.core.touchstone import read_touchstone
import numpy as np
_, a = read_touchstone('new.s2p'); _, b = read_touchstone('old.s2p')
print(np.abs(a - b).max())
```

---
[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)
