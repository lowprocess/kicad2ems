# 10. Testing

[← Examples](examples.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
python -m pytest -q          # any Python with numpy, shapely, matplotlib, pytest; openEMS not needed
```

The suite runs in a few seconds. Three field-dump tests skip without `h5py`.

## What is covered

| File | Covers |
|---|---|
| `test_kicad.py` | stackup, copper items, vias, footprints, port footprints, nets |
| `test_model_mesh.py` | hole splitting, mesh grading, model building, port placement, thirds rule incl. near-Manhattan edges, merging, boundaries, mixed mode, Touchstone round trip |
| `test_parts.py` | value parsing, part detection and skip notes, element pieces and topologies, mesh lines at part ends, the low-pass demo and its ideal circuit |
| `test_simulation_setup.py` | `build_fdtd` against strict CSXCAD / openEMS stand-ins (plain scalars), wideband (Debye) materials |
| `test_dielectric.py` | Djordjevic-Sarkar model, Debye fit accuracy, dropped short poles |
| `test_dumps.py` | dump specs, boxes, the HDF5 reader, field maps |
| `test_converge.py` | level parsing, monotonic refinement, comparison and verdict, `--preview --converge` |
| `test_antenna.py` | antenna defaults, match / gain tables, pattern scaling, gap ports, `--mode antenna --preview` for the four examples |
| `test_parallel.py` | `--jobs` (stubbed runs in real worker processes) matches the serial loop |
| `test_max_steps.py` | excitation length, steps read back from a run folder, the step-limit warning, option / TOML |
| `test_progress.py` | status-line parsing, ETA from decay / step limit, flat energy, the monitor (bar / raw), output redirection |
| `test_cli.py` | TOML handling and its path rules, the `.toml`-only invocation, `--preview` end to end, the report, `--check` |

## Fixtures and stand-ins

- `conftest.demo_files` generates the demo boards (`make_demo_board.py`) once per session.
- `test_simulation_setup.fake_openems` replaces the `CSXCAD` and `openEMS` modules with strict stand-ins:
  every scalar must be a plain Python number (as older CSXCAD requires), and every call is logged
  (`'linpoly'`, `'port'`, `('lumped', name, kwargs)`, `('debye', name, order)`, …) for assertions. Other
  test files import the fixture from there.
- `test_parallel.fake_run_port` is a top-level stub so spawned workers can import it.

## Checking against openEMS

The unit tests can't catch solver behaviour (stability, accuracy). Before a release, or after touching
`simulation.py`, `parts.py`, `antenna.py`, `mesh.py` or `model.py`, run a few real cases in the openEMS
environment and compare:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.toml --compare examples/reference/demo_transition_openems.s2p
python examples/make_antennas.py dipole examples/antenna_dipole
kicad2openems examples/antenna_dipole.toml                  # ~10 s: 2.43 GHz, -20 dB, 2.1 dBi, 96 %
python examples/make_demo_board.py examples/demo_lowpass lowpass
kicad2openems examples/demo_lowpass.toml                    # ~15 s; overlays the ideal circuit
```

To compare two result files numerically:

```python
from kicad2openems.touchstone import read_touchstone
import numpy as np
_, a = read_touchstone('new.s2p'); _, b = read_touchstone('old.s2p')
print(np.abs(a - b).max())        # same code and mesh: ~1e-3 (end-criterion timing)
```

The same model run twice with a different thread count, or by old and new code, should agree to about
1e-3; larger differences mean the model or mesh changed (check the mesh statistics in the report).
`examples/reference/` was produced with an earlier mesh for the demo transition, so it differs more.

---
[← Examples](examples.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)
