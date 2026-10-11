# 1. Getting started

[← Index](index.md) · [Next: Usage and configuration →](usage.md)

## Install

kicad2ems needs Python 3.10+, numpy, scipy, shapely, matplotlib and triangle (installed with the package,
which includes the MoM solver mom25d). The other solvers need their own part ([solvers](usage.md#solvers)):
openEMS with its Python bindings (`CSXCAD`, `openEMS`) for FDTD runs, scikit-rf (or Qucs-S's qucsator) for
the line model. Everything except the FDTD run works without openEMS: model building, meshing, line
sections, `--preview`, the report, and the MoM and line-model solvers.

```bash
pip install .                       # from the repository: kicad2ems and mom25d
pip install '.[fast]'               # + scikit-rf: the line model as a circuit (--solvers scikit)
pip install '.[dumps]'              # + h5py, for frequency-domain field dumps
python -m mom25d.core.build         # optional: mom25d's compiled core (~5x faster)
```

mom25d runs on NumPy; its compiled core (`python -m mom25d.core.build`, needs g++ with OpenMP and
pybind11) makes it about five times faster, and `kicad2ems --check` says whether it is built.

openEMS is not on PyPI. Check your distribution first (`dnf search openems`, `apt search openems`).
Otherwise build it from source without the GUI:

```bash
tools/build_openems.sh [prefix]     # default prefix ~/opt/openEMS, sources in ~/src/openEMS-Project ($SRC)
```

The script lists the build dependencies for Debian / Ubuntu and Fedora in its header. It clones
openEMS-Project (or updates an existing checkout), builds it, and pip-installs the `CSXCAD` and
`openEMS` Python packages into the active virtual environment, or with `--user` when none is active. If
`import openEMS` then cannot find its libraries, add `<prefix>/lib` to `LD_LIBRARY_PATH`.

Install kicad2ems **into the same Python environment** as openEMS:

```bash
source ~/venvs/openems/bin/activate
pip install .                       # re-run after pulling changes (a plain, not editable, install)
```

## Checking the installation

```bash
kicad2ems --check               # or: python -m kicad2ems --check
```

Run it with the Python you use for the simulations. It prints the Python in use, the import result of
numpy, h5py, CSXCAD and openEMS with the real error, which Python the `kicad2ems` command on PATH
runs, hints, and then what runs without openEMS (mom25d with or without its compiled core, scikit-rf,
qucsator, the GUI). The exit code is openEMS's: 0 when it can run. The hints:

* **not installed for this Python**: the `kicad2ems` command was installed by a different Python
  than your openEMS venv. Install kicad2ems inside the venv (`python -m pip install .`) or run
  `python -m kicad2ems`.
* **openEMS from your distribution (dnf / apt) but a venv**: a plain venv does not see system packages.
  Recreate it with `python3 -m venv --system-site-packages <dir>`.
* **missing dependency** (often `h5py`): `python -m pip install h5py`.
* **shared library not found**: add the openEMS install's `lib` folder to `LD_LIBRARY_PATH`.

## A first run

The demo boards are generated, so no KiCad is needed:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2ems examples/demo_transition.toml --preview      # model, mesh, line sections: seconds
kicad2ems examples/demo_transition.toml                # the openEMS run
```

`--preview` prints the stack, the ports, the line sections, the mesh size and time step, and writes
`examples/out/demo_transition_mesh.png`, `demo_transition_tlines.png`, the JSON summary and
`demo_transition_report.html`. The full run adds the S-parameters (`demo_transition_openems.s2p`),
their plot, the CSX geometry (open it in AppCSXCAD) and the openEMS run folders. While openEMS runs, a
progress line shows the energy decay and an ETA.

Without openEMS, the same board through the other two solvers:

```bash
kicad2ems examples/demo_transition.toml --solvers mom,lines
```

mom25d meshes the inner planes of this board too (13k unknowns: several minutes; a single-layer microstrip
like `examples/filter.toml` takes about a minute). [MoM](mom.md#runtime) says which boards suit which solver.

## Your own board

1. **Stackup.** Fill in *Board Setup → Physical Stackup* (thicknesses, εr, tan δ). The model is built
   from it.
2. **Ports.** Put a footprint (no copper needed) at the end of each feed line with a Fab-layer text
   `P1`, `P2`, … Its position is the port location; its rotation the direction *into* the circuit
   (0° = +x; along x or y). Differential ports: `P1+` / `P1-`. Optional fields `port.layer`, `port.z0`,
   `port.type`: see [KiCad import](kicad-import.md#ports).
3. **A config file** next to the board: `kicad2ems --init myboard.kicad_pcb --fmin 1 --fmax 6` writes a
   commented `myboard.toml` with the ports it found (or why it found none), the pick-and-place file next to
   the board, `nets = "auto"` and the options typed with it. By hand, it is as short as:

   ```toml
   board = "myboard.kicad_pcb"
   nets  = ["RF", "GND"]          # leave out to simulate all copper
   fmin  = 1.0                    # GHz
   fmax  = 6.0
   out   = "sim"
   ```

4. `kicad2ems myboard.toml --preview`, look at the mesh picture, the line sections and the report,
   then run without `--preview` (and with `--solvers` for MoM or the line model). A typo in the config is
   reported with the option it probably means (`fmx (did you mean fmax?)`).

The [Usage page](usage.md) describes every option; [Examples](examples.md) has filters and antennas
to copy from.

---
[← Index](index.md) · [Next: Usage and configuration →](usage.md)
