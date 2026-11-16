# pcbqs

> Part of kicad2ems since 0.4: the package `kicad2ems.pcbqs`, installed with kicad2ems (`pip install .` in this
> repository; the [README](../../README.md#install) has the installer). Its history up to then is in this
> repository's, and in its own repository. Install lines below that name `.` mean this repository.

PCB DC/power-integrity analysis, magneto-quasistatic and PEEC extraction, magnetic
near fields, low-frequency S-parameters, and ngspice operational circuits with
passive copper feedback.

```sh
pip install .
python -m kicad2ems.pcbqs.pdn --describe --json
python -m kicad2ems.pcbqs.mqs --describe --json
python -m kicad2ems.pcbqs.sparams board.kicad_pcb --fmin 1e3 --fmax 1e8 --out lf
python -m kicad2ems.pcbqs.circuit --template boost > boost.json
python -m kicad2ems.pcbqs.circuit boost.json --out result
python -m kicad2ems.pcbqs.circuit src/pcbqs/data/wpthb_bridge.json --out wpthb               # the wpthb full bridge
```

JSON-based solving, layout/mesh export and field reconstruction work without
`mom25d` or `kicad2ems`. Install `.[kicad2ems]` to import `.kicad_pcb` files.

With kicad2ems installed the four tools are its analyses (`pcbqs/analyses.py`, the
`kicad2ems.core.analyses` entry points) and `kicad2ems.pcbqs.sparams` one of its S-parameter solvers
(`kicad2ems.core.solvers`: `--solvers pcbqs`): `kicad2ems --check` lists them, a config keeps a
study's under `[analysis]`, and kicad2ems-gui builds each one's settings from its
options table, runs it and shows what its result folder holds: the maps as field
dumps in its Viewer, the report and the curves (`pcbqs/reports.py`) on its Results
page. The circuit examples are configs beside their models
(`circuit/examples/*.toml`), under its File > Examples > Circuits.
Install ngspice separately for operational circuits. Component models and
parasitics are explicit assumptions; footprints do not imply calibrated models.

The compiled core is needed: it meshes (the constrained Delaunay triangulation of
[CDT](https://github.com/artem-ogre/CDT), vendored in `src/pcbqs/core/CDT`) and holds the
CPU kernels. Build it with `pip install '.[core]'` followed by `python -m kicad2ems.pcbqs.core.build`
(a C++17 compiler; the kicad2ems installer does this). The kernels keep their NumPy/threaded
fallbacks (`PCBQS_NO_CORE`); the mesher has none. CuPy GPU
fill is optional: select a matching CUDA/ROCm extra and `--fill-backend cupy`.
Execution controls use `PCBQS_FILL_BACKEND`, `PCBQS_FILL_THREADS`,
`PCBQS_OMP_THREADS`, `PCBQS_NO_CORE` and `PCBQS_ARCH`; general OpenMP settings
remain supported. Static-fill plugins use the `kicad2ems.pcbqs.static_backends` entry group.

- [Quasistatic guide](quasistatic.md)
- [Low-frequency S-parameters](sparams.md): a board's ports from DC to a tenth of a wavelength, with the
  return current where it flows; `--solvers pcbqs` in a kicad2ems run. Its example,
  `examples/return-path/return_path.py`: what a ground via's place does to a layer change
- [Circuits](circuit.md): any circuit of parts, nodes and drives, by ngspice: its parts' losses for the
  thermal analysis, its copper's currents for the fields; ten examples, each with a board whose copper can be put in, and the wpthb full bridge; KiCad boards,
  schematics and netlists read into a model
- [Temperature](thermal.md): a trial of a lightweight thermal solver, on a grid and on the triangle mesh
- [Validation and limits](quasistatic-validation.md)
- [Migration](migration.md)
- [Copied-code provenance](PROVENANCE.json)
- [PEEC and skin-effect test PCB](../../examples/pcbqs/peec-skin/README.md): two coupons,
  model comparisons, independent references and a reproducible KiCad layout.
- [Experimental PEEC PDN comparison](../../examples/pcbqs/pdn-peec-trial/README.md):
  loaded rails, independent DC/AC references, and mesh sensitivity.
- [Jetson AGX Thor PDN check](../../examples/pcbqs/jetson-pdn/REPORT.md): a real-board DC
  cross-check, cavity limitations, and a bounded local PEEC/skin comparison.
- [PEEC memory reduction](../../examples/pcbqs/peec-memory/REPORT.md) and the
  [experimental compressed backend](peec-memory.md): graded meshes,
  hierarchical current/charge operators and iterative solving.

Private `_numerics` functions are independently maintained snapshots, not a
shared library or a runtime link to MoM. `emsdev` supplies only hardware and
native-build services and the adaptive sweep's fit (`kicad2ems.emsdev.afs`). RF/full-wave
solvers remain in `mom25d`.

## Licence

GPL-3.0-or-later (see [LICENSE](../../LICENSE)). The mesher's triangulation is CDT 2.0.1 by
Artem Amirkhanov, vendored unmodified in `src/pcbqs/core/CDT` under its MPL-2.0 (its
`predicates.h` BSD-3-Clause, `portable_nth_element.hpp` Apache-2.0 with LLVM exceptions;
`src/pcbqs/core/CDT/README.md`), all compatible with the GPL.
