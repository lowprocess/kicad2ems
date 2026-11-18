# kicad2ems-fdtd

> Part of kicad2ems since 0.4: the package `kicad2ems.fdtd`, installed with kicad2ems (`pip install .` in this
> repository; the [README](../../README.md#install) has the installer). Its history up to then is in this
> repository's, and in its own repository. Install lines below that name `.` mean this repository.

The FDTD solvers of [kicad2ems](https://github.com/lowprocess/kicad2ems):
[openEMS](https://openems.de) and [gfdtd](../gfdtd/README.md), on one shared
FDTD model. This package turns kicad2ems's board into a rectilinear mesh and an
openEMS-style model (the boundaries, the ports and parts, the dielectrics, the
field dumps, the antenna's far-field box), runs one excitation per port with its
end criteria, and reads the results back. Only the engine differs between the
two solvers. kicad2ems lists them in `kicad2ems.core.solvers.SOLVERS`, and the
package reaches kicad2ems only through `kicad2ems.core.api.solver`.

- `solver.py`: `FDTD`, the solvers' common class, and their shared settings
  (`MESH`, `RUN`: the GUI's FDTD section)
- `mesh.py`, `boundaries.py`, `dielectric.py`, `model.py`: the mesh, the six boundaries, dielectric loss, the
  CSX model (`build_fdtd`)
- `run.py`, `progress.py`: the runs, their end criteria (port watch) and the progress line
- `antenna.py`: antenna-mode runs; `readback.py`: the model as an engine gets it, for the parity checks
- `engines.py`: the engines by name (`'openems'`, `'gfdtd:gpu'`): their `ContinuousStructure` / `openEMS`
  classes. They ship here, with the FDTD layer that drives them
- `openems/`: the solver `openems` (alias `fdtd`): openEMS's engine and its import check (`kicad2ems --check`),
  an openEMS in another environment (`openems_venv`); `--converge` (`converge.py`) is the FDTD layer's, on either
  engine
- `gfdtd.py`: the solver `gfdtd` (alias `gpu-fdtd`) on gfdtd's engine: its backends, its speed estimate, its
  runs at once on the GPU

## Install

    python -m pip install kicad2ems                     # kicad2ems, with this part and gfdtd
    # openEMS itself is not on PyPI: your distribution's, or tools/build_openems.sh

openEMS and CSXCAD are not on PyPI: install them from your distribution or build them from source (see
kicad2ems's README). Without them the `openems` solver still loads and `kicad2ems --check` says what is
missing; without gfdtd, `gfdtd` does the same.

On Windows, openEMS's release for Windows brings the DLLs and its Python packages: install the packages into
kicad2ems's Python (or name the Python that has them in `openems_venv`) and set `openems_dir` to the unpacked folder,
which puts its DLLs on the DLL search (the job `LD_LIBRARY_PATH` does on Linux).

## Tests

    python -m pytest

They need kicad2ems's checkout for its example boards and fixtures (`KICAD2EMS_SRC`, default `../kicad2ems`),
and not openEMS: CSXCAD and openEMS are replaced by strict stand-ins (`fake_openems`), and the runs by canned
results. They cover the mesh, the model, the dielectrics, the parts and walls on the grid, the runs' end
criteria and progress, antenna mode, the parity checks, the whole pipeline and `--preview`, `--converge`,
`openems_venv`, and gfdtd's connection (with gfdtd installed).

## Licence

GPL-3.0-or-later, as kicad2ems.
