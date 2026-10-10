# 11. Testing

[← Examples](examples.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)

## Running the tests

```bash
pip install '.[test]'        # pytest, scikit-rf, h5py
python -m pytest -q          # openEMS not needed
```

The suite (about 120 tests) runs in-process in about 20 s. It needs numpy, shapely, matplotlib and
pytest; tests that need scikit-rf or h5py are skipped without them. The qucsator test is skipped unless
`qucsator_rf` or `qucsator` is on PATH.

## What is covered

| File | Covers |
|---|---|
| `test_kicad.py` | S-expressions, stackup, copper items, vias, footprints, port footprints and their fields, nets, outline |
| `test_kicad_shapes.py` | outlines from any Edge.Cuts mix (mounting holes and cutouts ignored, KiCad 5 arcs, footprint edges), copper graphics on the board and in footprints, pad shapes (chamfered, trapezoid, custom), `from_text` |
| `test_model_mesh.py` | hole splitting, mesh grading, model building, port placement, thirds rule incl. near-Manhattan edges, merging, curves and via snapping, boundaries, region cropping, mixed mode, Touchstone round trip |
| `test_parts.py` | value parsing, part detection and skip notes, element pieces and topologies, mesh lines at part ends, the low-pass demo and its ideal circuit |
| `test_tlines.py` | the closed-form impedances against reference values; the sections of the example boards (pads, vias, parts, branches, T-junctions, reference changes, gap ports) |
| `test_fastsolve.py` | scikit-rf and qucsator solves, the qucsator netlist and dataset reader, part impedances |
| `test_simulation_setup.py` | `build_fdtd` against the strict CSXCAD / openEMS stand-ins (plain scalars), wideband (Debye) materials |
| `test_dielectric.py` | Djordjevic-Sarkar model, Debye fit accuracy, dropped short poles |
| `test_dumps.py` | dump specs, boxes, the HDF5 reader (both layouts), field maps, TOML lists |
| `test_fields.py` | VTR frames as openEMS writes them (zlib / plain), plane cuts in KiCad axes, dump discovery, outlines, GIF / MP4, the NF2FF sphere cuts, the Fields tab |
| `test_converge.py` | level parsing, monotonic refinement, comparison and verdict, `--preview --converge` |
| `test_antenna.py` | antenna defaults, match / gain tables, pattern scaling, gap ports, `--mode antenna --preview` for the four examples |
| `test_parallel.py` | `--jobs` (stubbed runs in real worker processes) matches the serial loop |
| `test_max_steps.py` | excitation length, steps read back from a run folder, the step-limit warning, option / TOML |
| `test_progress.py` | status-line parsing, ETA from decay / step limit, flat energy, the monitor (bar / raw), output redirection |
| `test_cli.py` | Settings (defaults ← TOML ← command line, abbreviated options), lists, path rules, one-line errors, `check` as command-line only, `--preview` end to end with the report and JSON |
| `test_pipeline.py` | the whole pipeline with canned solver results: S-parameters with `--fast skrf` and `--converge`, antenna mode; post-processing, plots, JSON summary and report |

## Fixtures and stand-ins

Shared fixtures live in `tests/conftest.py`:

- `boards`: every generated example board (demo transition, diff, rlc, lowpass and the four antennas),
  generated once per session; `demo_files` gives the transition and diff boards.
- `fake_openems`: replaces the `CSXCAD` and `openEMS` modules with strict stand-ins. Every scalar must
  be a plain Python number (as older CSXCAD requires), and every call is logged (`'linpoly'`, `'port'`,
  `('lumped', name, kwargs)`, `('debye', name, order)`, …) for assertions.
- `run_cli`: runs `kicad2openems` in the test process and returns (exit code, stdout, stderr).

`test_pipeline.py` replaces `simulation.run_excitations` with canned results (a plausible thru), so
everything after the solver runs for real. `test_parallel.fake_run_port` is a top-level stub so spawned
workers can import it.

## Checking against openEMS

The unit tests can't catch solver behaviour (stability, accuracy). Before a release, or after touching
`simulation.py`, `parts.py`, `antenna.py`, `mesh.py` or `model.py`, run a few real cases in the openEMS
environment and compare:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.toml --compare examples/reference/demo_transition_openems.s2p
python examples/make_antennas.py dipole examples/antenna_dipole
kicad2openems examples/antenna_dipole.toml       # 2.43 GHz, -20 dB, 2.1 dBi, 96 %; 54 s on a 2-vCPU container
python examples/make_demo_board.py examples/demo_lowpass lowpass
kicad2openems examples/demo_lowpass.toml         # overlays the ideal circuit
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
`examples/reference/` is regenerated with the current mesher ([examples](examples.md#reference-results)).

---
[← Examples](examples.md) · [Index](index.md) · [Next: Maintenance notes →](maintenance.md)
