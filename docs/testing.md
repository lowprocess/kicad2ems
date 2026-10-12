# Testing

[← The board model](model.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
pip install '.[test]'      # pytest, scikit-rf, h5py
python -m pytest -q        # about 280 tests, a few minutes; openEMS not needed
```

Tests that need an optional package are skipped without it: scikit-rf, h5py, qucsator, PySide6 for the
GUI, PyOpenGL for the 3D view, and mom25d's compiled core for the GPU tests.

## What is covered

| Area | Files |
|---|---|
| KiCad and Gerber import | `test_kicad.py`, `test_kicad_shapes.py`, `test_importers.py` |
| the board model, mesh and parts | `test_model_mesh.py`, `test_parts.py` |
| the line model and its circuit solvers | `test_tlines.py`, `test_fastsolve.py` |
| openEMS setup, against strict stand-ins | `test_simulation_setup.py`, `test_dielectric.py`, `test_dumps.py` |
| field reading, animations, the 3D view | `test_fields.py`, `test_volume3d.py` |
| convergence, antennas, parallel runs, progress | `test_converge.py`, `test_antenna.py`, `test_parallel.py`, `test_progress.py`, `test_max_steps.py` |
| settings, the command line, solver choice, plugins | `test_cli.py`, `test_solvers.py`, `test_plugins.py` |
| what each solver saw against the board | `test_parity.py` |
| boards with a known answer | `test_conformance.py` |
| the whole pipeline, with canned solver results | `test_pipeline.py` |
| mom25d | `test_mom.py`, `test_mom25d_*.py` (including the GPU paths, against a fake CuPy) |
| the GUI | `test_gui.py` |

`test_conformance.py` runs openEMS only when `KICAD2EMS_CONFORMANCE_OPENEMS=1` is set.

## Fixtures

`tests/conftest.py` provides:

- `boards`: every generated example board, made once per session.
- `fake_openems`: strict stand-ins for CSXCAD and openEMS. They reject numpy scalars, as older CSXCAD
  does, and log every call so tests can check them.
- `run_cli`: runs `kicad2ems` in the test process and returns the exit code and output.

`test_pipeline.py` replaces the openEMS runs with canned results, so everything after them runs for real.

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
