# Getting started

[Index](index.md) · [Next: Usage →](usage.md)

## Install

The simplest way is the installer in the repository, `install.py` (Python's standard library only; it runs before
anything is installed):

```bash
./setup.sh               # Linux, macOS: asks about each optional part (setup.sh runs install.py)
./setup.sh --yes         # takes the defaults
source .venv/bin/activate
```

```bat
py install.py            :: Windows (setup.cmd does the same)
py install.py --yes
.venv\Scripts\activate
```

It first asks where kicad2ems goes (`--target`). On Linux and macOS:

| Target | The environment | The commands | The GUI's launcher entry |
|---|---|---|---|
| `checkout` (default) | a venv in `.venv/` | `.venv/bin/` (activate it) | `~/.local/share/applications` |
| `user` | a venv in `~/.local/share/kicad2ems/` | linked into `~/.local/bin` | `~/.local/share/applications` |
| `system` | a venv in `/opt/kicad2ems/` (runs sudo) | linked into `/usr/local/bin` | `/usr/share/applications` |
| `python` | the Python itself (`pip install --user`) | `~/.local/bin` | `~/.local/share/applications` |

On Windows:

| Target | The environment | The commands | The GUI's Start-menu shortcut |
|---|---|---|---|
| `checkout` (default) | a venv in `.venv\` | `.venv\Scripts\` (activate it) | your Start menu |
| `user` | a venv in `%LOCALAPPDATA%\kicad2ems\` | `%LOCALAPPDATA%\kicad2ems\bin`, put on your PATH | your Start menu |
| `system` | a venv in `Program Files\kicad2ems\` (an Administrator prompt) | its `bin`, put on every user's PATH | every user's Start menu |
| `python` | the Python itself (`pip install --user`) | the user scripts folder | your Start menu |

The checkout and user installs follow this checkout (editable: a `git pull` updates them); the system install
is a copy. After a `git pull`, `./setup.sh --update` (`py install.py --update`) brings an existing install up to date
without asking anything: it reinstalls kicad2ems, and the stack's other packages as they were installed (a checkout
stays that checkout, a URL is fetched again), rebuilds a compiled core whose source changed, and remakes the example
boards. It doesn't touch openEMS, qucsator, the GPU wheel or other dependencies, and it doesn't pull; `pip check`
reports a requirement that has become missing, which a plain run of the installer then installs.

It then installs kicad2ems and offers:

- the GUI, field-dump support (h5py) and the scikit-rf line model;
- the compiled cores of mom25d (about five times faster) and gfdtd (about fifteen times, on the CPU), which need a C++
  compiler: g++ on Linux and macOS; on Windows Visual Studio 2022 or its Build Tools (*Desktop development with C++*),
  or MinGW-w64's g++ (MSYS2's UCRT64 toolchain) where Visual Studio isn't installed (`EMSDEV_COMPILER=msvc` or `gcc`
  chooses);
- the CuPy package that matches your GPU (detected: CUDA 12, CUDA 13, or ROCm 7 on Linux; CuPy has no ROCm wheel for
  Windows, so an AMD card there runs nothing on the GPU);
- openEMS: one the Python already has; one installed in another venv, linked in (its CSXCAD and openEMS
  packages; it must be built for the same Python version); on Linux and macOS a copy built with
  `tools/build_openems.sh`; on Windows the unpacked openEMS release (its Python wheels go into kicad2ems's environment
  and its folder is recorded as `openems_dir`, which puts its DLLs on the DLL search); or none.
  `--openems /opt/openems/venv/bin/python` names the other venv's Python, `--openems C:\opt\openEMS` Windows' folder;
- qucsator: found on the PATH (on Windows also in Qucs-S's install folder), a path you give (`--qucsator PATH`),
  installed from your distribution, or none. Its path goes into `kicad2ems-site.toml` in the environment, which
  kicad2ems reads;
- a launcher entry for the GUI (a Start-menu shortcut on Windows, which runs it without a console window), and an
  icon on the desktop. Both run the environment's own `kicad2ems-gui`, so nothing needs activating.

`./setup.sh --uninstall --target user` (`py install.py --uninstall --target user`) removes what that install added:
the commands, the PATH entry it made on Windows, the launcher entry and desktop icon, the link to openEMS, and the
venv if you say so.

### By hand

kicad2ems needs Python 3.10 or newer.

```bash
pip install .                   # kicad2ems: the line model, the FDTD model and mesh
pip install '.[openems,mom,gfdtd]'   # the full-wave solvers and their plugins, from their own repositories
                                     # (openems: the plugin; openEMS itself as below)
pip install '.[gui,dumps,fast]' # the GUI, field dumps, the scikit-rf line model
pip install '.[all]'            # or all of the above
python -m mom25d.core.build     # optional: mom25d's compiled core
python -m gfdtd.core.build      # optional: gfdtd's compiled CPU core
pip install cupy-rocm-7-0       # optional, AMD GPU (ROCm 7); NVIDIA: cupy-cuda12x or cupy-cuda13x
```

mom25d's mesher, triangle, has no wheel on PyPI for every Python (none for 3.14). If the `mom` extra fails on
`triangle>=20230923`, build it from its repository first (needs a C compiler; the installer does this for you):

```bash
pip install "triangle @ git+https://github.com/drufat/triangle.git@595b43eb6682992a0b1012b9671bf860c0e6ae56"
```

openEMS is not on PyPI. On Linux, install it from your distribution if it has it, or build it:

```bash
tools/build_openems.sh [prefix]   # default prefix ~/opt/openEMS
```

The script's header lists the build dependencies for Debian/Ubuntu and Fedora. It installs openEMS's
Python packages into the active virtual environment. kicad2ems must be installed in the **same**
Python environment as openEMS, or point it at openEMS's: `install.py --openems PATH`, the GUI's *Solvers and
hardware* window (*openEMS environment*), or `openems_venv = "/path/to/venv"` in the user's `site.toml`
(`~/.config/kicad2ems/site.toml`; `%APPDATA%\kicad2ems\site.toml` on Windows). The two must be the same Python
version. If `import openEMS` can't find its libraries, add `<prefix>/lib` to `LD_LIBRARY_PATH`.

On Windows, unpack openEMS's release for Windows (the MSVC build, from [docs.openems.de](https://docs.openems.de)),
for example to `C:\opt\openEMS`. Its `python` folder has CSXCAD and openEMS wheels for some Python versions:
install the pair for kicad2ems's Python into its environment (`pip install C:\opt\openEMS\python\*cp313*.whl`, or
let the installer do it) and set `openems_dir = "C:/opt/openEMS"` (the GUI's *openEMS folder*), which puts its DLLs on
the DLL search; `OPENEMS_INSTALL_PATH` works too.

## Checking the install

```bash
kicad2ems --check
```

This shows which solvers the current Python can run, whether mom25d's compiled core is built, and the
GPU mom25d will use. If openEMS is missing it explains why. The common causes:

- **kicad2ems and openEMS are in different Pythons.** Install kicad2ems in openEMS's environment, or
  run `python -m kicad2ems` with that Python.
- **openEMS came from your distribution but you're in a virtual environment.** Recreate it with
  `python3 -m venv --system-site-packages <dir>`.
- **A missing dependency**, often h5py: `pip install h5py`.
- **A shared library is not found**: add openEMS's `lib` folder to `LD_LIBRARY_PATH` (on Windows: set
  `openems_dir` to the openEMS folder, the one with its DLLs).

## A first run

The example boards are generated by scripts, so you don't need KiCad; the installer has made them in
`examples/` (`python examples/make_boards.py` makes them again):

```bash
kicad2ems examples/demo_transition.toml --preview
kicad2ems examples/demo_transition.toml
```

`--preview` builds the model and the mesh without solving. It takes seconds and writes pictures of the
model, the mesh and the line sections, plus a report, to `examples/out/`. The second command runs the
solvers and adds the S-parameters and their plot.

Without openEMS, use the other two solvers:

```bash
kicad2ems examples/demo_transition.toml --solvers mom,lines
```

## Your own board

1. **Stackup.** Fill in *Board Setup → Physical Stackup* in KiCad: thicknesses, εr and tan δ.
2. **Ports.** Place a footprint at the end of each feed line with a Fab-layer text `P1`, `P2`, … The
   footprint needs no copper. Its position is the port; its rotation points into the circuit (0° is +x).
   Use `P1+` / `P1-` for a differential pair. See [KiCad import](kicad-import.md#ports) for the optional
   fields.
3. **A config file.** `kicad2ems --init myboard.kicad_pcb --fmin 1 --fmax 6` writes a commented
   `myboard.toml` with the ports it found. A minimal one looks like this:

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
