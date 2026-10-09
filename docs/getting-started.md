# 1. Getting started

[← Index](index.md) · [Next: Usage and configuration →](usage.md)

## Install

kicad2openems needs Python 3.10+, numpy, shapely and matplotlib (installed with the package), and
openEMS with its Python bindings (`CSXCAD`, `openEMS`) for actual simulations. Everything except the
FDTD run — model building, meshing, `--preview`, the report — works without openEMS.

```bash
pip install .                       # from the repository
```

openEMS is not on PyPI. Use your distribution's package if it has one, or build it with
`tools/build_openems.sh [prefix]`, which lists the build dependencies and pip-installs the Python
bindings. Install kicad2openems **into the same Python environment** as openEMS:

```bash
source /opt/openems/venv/bin/activate
pip install .                       # re-run after pulling changes (it is a plain, not editable, install)
```

Check that this Python can run openEMS:

```bash
kicad2openems --check
```

It prints the Python in use, the import result of numpy, h5py, CSXCAD and openEMS, and hints for the
usual problems (installed for another Python, venv without system packages, missing `h5py`,
`LD_LIBRARY_PATH`).

## A first run

The demo boards are generated, so no KiCad is needed:

```bash
python examples/make_demo_board.py examples/demo_transition
kicad2openems examples/demo_transition.toml --preview      # model + mesh only, seconds
kicad2openems examples/demo_transition.toml                # the openEMS run, about a minute
```

`--preview` writes `examples/out/demo_transition_mesh.png` and `demo_transition_report.html` and prints
the stack, the ports, the mesh size and time step. The full run adds the S-parameters
(`demo_transition_openems.s2p`), their plot, a JSON summary, the CSX geometry (open it in AppCSXCAD)
and the openEMS run folders. While openEMS runs, a progress line shows the energy decay and an ETA.

## Your own board

1. **Stackup.** Fill in *Board Setup → Physical Stackup* (thicknesses, εr, tan δ). The model is built
   from it.
2. **Ports.** Put a footprint (no copper needed) at the end of each feed line with a Fab-layer text
   `P1`, `P2`, … Its position is the port location; its rotation the direction *into* the circuit
   (0° = +x). Differential ports: `P1+` / `P1-`. See [KiCad import](kicad-import.md#ports).
3. **A config file** next to the board:

   ```toml
   board = "myboard.kicad_pcb"
   nets  = ["RF", "GND"]          # leave out to simulate all copper
   fmin  = 1.0                    # GHz
   fmax  = 6.0
   out   = "sim"
   ```

4. `kicad2openems myboard.toml --preview`, look at the mesh picture and the report, then run without
   `--preview`.

The [Usage page](usage.md) describes every option; [Examples](examples.md) has filters and antennas
to copy from.

---
[← Index](index.md) · [Next: Usage and configuration →](usage.md)
