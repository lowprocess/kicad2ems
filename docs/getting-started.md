# Getting started

[Index](index.md) · [Next: Usage →](usage.md)

## Install

The installer, `install.py`, needs only Python's standard library:

```bash
./setup.sh               # Linux, macOS: asks about each optional part (setup.sh runs install.py)
./setup.sh --yes         # takes the defaults
source .venv/bin/activate
```

```bat
py install.py            :: Windows (setup.cmd does the same); --yes takes the defaults
.venv\Scripts\activate
```

It first asks where kicad2ems goes (`--target`):

| Target | The environment | The commands | The GUI's launcher entry |
|---|---|---|---|
| `checkout` (default) | a venv in `.venv/` | `.venv/bin/` (`.venv\Scripts\`): activate it | yours |
| `user` | a venv in `~/.local/share/kicad2ems/` (`%LOCALAPPDATA%\kicad2ems\`) | on your PATH | yours |
| `system` | a venv in `/opt/kicad2ems/` (`Program Files\kicad2ems\`), via sudo (an Administrator prompt) | on every user's PATH | every user's |
| `python` | the Python itself (`pip install --user`) | the user scripts folder | yours |

The checkout and user installs are editable, so a `git pull` updates them; the system install is a copy. After a
pull, `./setup.sh --update` (`py install.py --update`) brings an install up to date without asking: kicad2ems and
the stack's other packages as they were installed, the compiled cores whose sources changed, and the example
boards. It leaves openEMS, qucsator, Elmer, Palace and the GPU wheel alone. `--uninstall --target user` removes what that install
added (the commands, the PATH entry, the launcher entry, the link to openEMS, the venv if you say so).

It then offers, each optional:

- the GUI, field-dump support (h5py) and the scikit-rf line model;
- the compiled cores of mom25d (about five times faster) and gfdtd (about fifteen times, on the CPU). They need a
  C++ compiler: g++ on Linux and macOS; on Windows Visual Studio 2022 or its Build Tools, or MinGW-w64's g++
  (`EMSDEV_COMPILER=msvc` or `gcc` chooses);
- the GPU: slangpy (Vulkan, any GPU), and optionally the CuPy wheel for a detected CUDA 12, CUDA 13 or ROCm 7 (Linux);
- openEMS: one the Python already has; one in another venv of the same Python version, linked in
  (`--openems /opt/openems/venv/bin/python`); on Linux and macOS a build by `tools/build_openems.sh`; on Windows
  the unpacked release (`--openems C:\opt\openEMS`); or none;
- qucsator: on the PATH (on Windows also in Qucs-S's folder), a path you give (`--qucsator PATH`), your
  distribution's, or none. Its path goes into the environment's `kicad2ems-site.toml`;
- kicad2ems-fem, the finite-element second opinions (experimental; asked for by default only when Elmer or Palace
  is found), with
  - Elmer: one found (`K2E_ELMER`, the PATH, `/usr/local`, `/opt/elmer`, `~/opt/elmer`, Windows' Program Files),
    a folder you give (`--elmer PATH`), on Linux and macOS a build from source by `tools/build_elmer.sh` (serial
    with OpenMP and UMFPACK, no GUI: a few minutes; `--elmer build`), or none;
  - Palace: one found (`K2E_PALACE`, the PATH, an unpacked Palace image run in podman), an executable or image
    you give (`--palace PATH`), on Linux and macOS a build with Spack by `tools/build_palace.sh` (`--palace spack`:
    never the default, several hours and about 10 GB), or none. The build takes Palace's newest release and
    builds it as Palace's own CI does: its own Spack packages, its patches to Spack's and Spack's packages at the
    commit it names (`PALACE_TAG=v0.18.1` another release, `PALACE_TAG=main` the newest code);

  where each is goes into `kicad2ems-site.toml` (`elmer`, `palace`, `palace_rootfs`), which kicad2ems-fem reads
  after the environment's `K2E_*`;
- a launcher entry (a Start-menu shortcut on Windows) and a desktop icon for the GUI, which run the environment's
  own `kicad2ems-gui`, so nothing needs activating.

### By hand

kicad2ems needs Python 3.10 or newer.

```bash
pip install .                   # kicad2ems: the board model and the line model
pip install '.[openems,mom,gfdtd]'   # the full-wave solvers' packages (openems: the plugin; openEMS itself as below)
pip install '.[gui,dumps,fast]' # the GUI, field dumps, the scikit-rf line model
pip install '.[all]'            # or all of the above
python -m mom25d.core.build     # optional: mom25d's compiled core
python -m gfdtd.core.build      # optional: gfdtd's compiled CPU core
pip install '.[gpu-vulkan]'     # optional: the GPU through Vulkan (slangpy); or a CuPy wheel such as cupy-cuda12x
```

mom25d's mesher, triangle, has no wheel on PyPI for every Python (none for 3.14). If the `mom` extra fails on it,
build it as [mom25d's README](https://github.com/lowprocess/mom25d#install) says (the installer does it for you).

openEMS is not on PyPI. On Linux, install it from your distribution, or build it with `tools/build_openems.sh
[prefix]` (default `~/opt/openEMS`; its header lists the build dependencies), which installs its Python packages
into the active venv. kicad2ems must run in the same Python environment as openEMS, or point at it: `install.py
--openems PATH`, the GUI's *Solvers and hardware* window, or `openems_venv = "/path/to/venv"` in the user's
`site.toml` (`~/.config/kicad2ems/site.toml`; `%APPDATA%\kicad2ems\site.toml` on Windows), of the same Python
version. If `import openEMS` can't find its libraries, add `<prefix>/lib` to `LD_LIBRARY_PATH`.

On Windows, unpack openEMS's release (from [docs.openems.de](https://docs.openems.de)), for example to
`C:\opt\openEMS`, install its CSXCAD and openEMS wheels for kicad2ems's Python (`pip install
C:\opt\openEMS\python\*cp313*.whl`), and set `openems_dir = "C:/opt/openEMS"` (the GUI's *openEMS folder*) so its
DLLs are found.

## Checking the install

```bash
kicad2ems --check
```

This shows which solvers the current Python can run, whether the compiled cores are built, and the GPU the solvers
will use. If openEMS is missing it explains why. The common causes:

- **kicad2ems and openEMS are in different Pythons.** Install kicad2ems in openEMS's environment, or
  run `python -m kicad2ems` with that Python.
- **openEMS came from your distribution but you're in a virtual environment.** Recreate it with
  `python3 -m venv --system-site-packages <dir>`.
- **A missing dependency**, often h5py: `pip install h5py`.
- **A shared library is not found**: add openEMS's `lib` folder to `LD_LIBRARY_PATH` (on Windows: set
  `openems_dir` to the openEMS folder).

## A first run

The example boards are made by scripts, so you don't need KiCad; the installer made them in `examples/`
(`python examples/make_boards.py` makes them again):

```bash
kicad2ems examples/demo_transition.toml --preview
kicad2ems examples/demo_transition.toml
```

`--preview` builds the model and the mesh without solving: in seconds it writes pictures of the model, the mesh
and the line sections, and a report, to `examples/out/`. The second command runs the solvers and adds the
S-parameters and their plot. Without openEMS, name the others: `--solvers gfdtd,mom,lines`.

## Your own board

1. **Stackup.** Fill in *Board Setup → Physical Stackup* in KiCad: thicknesses, εr and tan δ.
2. **Ports.** Place a footprint at the end of each feed line with a Fab-layer text `P1`, `P2`, … The
   footprint needs no copper. Its position is the port; its rotation points into the circuit (0° is +x).
   Use `P1+` / `P1-` for a differential pair. See [KiCad import](kicad-import.md#ports) for the optional
   fields.
3. **A config file.** `kicad2ems --init myboard.kicad_pcb --fmin 1 --fmax 6` writes a commented
   `myboard.toml` with the ports it found. A minimal one:

   ```toml
   board = "myboard.kicad_pcb"
   nets  = ["RF", "GND"]   # leave out to simulate all copper
   fmin  = 1.0             # GHz
   fmax  = 6.0
   ```

4. **Preview, then run.** `kicad2ems myboard.toml --preview`, check the pictures and the report, then run
   without `--preview`. A typo in the config is reported with the option it probably means.

[Usage](usage.md) describes every option. [Examples](examples.md) has boards to copy from.

---
[Index](index.md) · [Next: Usage →](usage.md)
