# gfdtd (GPU FDTD)

[← Index](index.md)

gfdtd is a 3D FDTD solver in the manner of openEMS that runs on a GPU, or on the CPU through a compiled core. It
is its own package ([gfdtd](https://github.com/lowprocess/gfdtd)) and runs openEMS models without kicad2ems; this
page is about using it from kicad2ems, through [kicad2ems-fdtd](https://github.com/lowprocess/kicad2ems-fdtd)'s
`gfdtd` plugin (`kicad2ems_fdtd/gfdtd.py`). `--sigma` is conductor loss on gfdtd 261104 or later, as openEMS's
conducting sheets, and `--rough` its own wideband fit (Hammerstad); an older gfdtd's copper is lossless.

It gets **openEMS's model**: kicad2ems-fdtd builds the same CSXCAD geometry (`build_fdtd`) through gfdtd's copy of
CSXCAD's and openEMS's Python interface, so the mesh, boundaries, ports, parts, excitation and end criteria are
openEMS's and take openEMS's settings (`--res`, `--fine`, `--nz`, `--bc`, `--end-db`, `--end-on`, `--max-steps`,
`--excite`, `--diel`, `--dump`, `--ff-freq`, ...: the GUI's *FDTD (openEMS, gfdtd)* section). Only the engine
differs: on the examples the two agree to within 0.004–0.05 in |S|, and on one mid-range GPU gfdtd runs 8–20 times
faster than openEMS on a 24-thread CPU (its CPU core about twice as fast). `--solvers auto` picks it over openEMS
whenever it can run: not with `--converge`, which it would ignore, nor `--sigma` on a gfdtd older than 261104
([Which solver](usage.md#which)).

```bash
kicad2ems examples/rf_coupler.toml --solvers gfdtd              # the GPU when Vulkan or CuPy finds one, else the CPU
kicad2ems examples/rf_coupler.toml --solvers openems,gfdtd      # both, compared in the report
kicad2ems board.toml --solvers gfdtd --gfdtd-backend cpu        # the CPU core (numpy without it: slow)
kicad2ems board.toml --solvers gfdtd --bc PML_8 --diel wideband --dump "E@F.Cu-0.05@2,4"
kicad2ems examples/antenna_dipole.toml --solvers gfdtd          # antenna mode: gain, efficiency, patterns
```

| Setting | Meaning |
|---|---|
| `--gfdtd-backend auto\|gpu\|cpu` (`[solver.gfdtd] backend`) | `auto` (default): the GPU when Vulkan or CuPy finds one, else the CPU. `gpu`: Vulkan (slangpy, any GPU), else CuPy (used first on NVIDIA CUDA), or the run stops. `cpu`: the compiled core, else numpy. `GFDTD_BACKEND=vulkan\|cupy\|core\|numpy` overrides it for every run |

Install it with the `gfdtd` extra (`python -m pip install '.[gfdtd]'`, or `pip install -e ../gfdtd` for a clone).
The GPU needs slangpy (the `gpu-vulkan` extra) or, optionally, CuPy for your stack, as for mom25d ([GPU](mom.md#gpu));
the CPU core is built once, `python -m gfdtd.core.build` (g++ with OpenMP and pybind11; `GFDTD_ARCH=-march=x86-64-v3`
for a portable build), without which the CPU runs on numpy, some 15 times slower. `kicad2ems --check` shows which
backend gfdtd will use.

Each run writes `<name>_gfdtd.xml`, the model as an openEMS XML file that both `gfdtd` and `openEMS` can run. gfdtd
writes openEMS's probe files, field dumps and status lines, so kicad2ems's progress lines, port watch, Stop and
S-parameter code work on it unchanged. A preview with gfdtd and without openEMS draws the mesh (`<name>_mesh.png`).

gfdtd's README covers the rest: [CSXCAD and openEMS compatibility](https://github.com/lowprocess/gfdtd#csxcad-and-openems-compatibility),
[what it models](https://github.com/lowprocess/gfdtd#what-it-models) (and what not yet: `--converge`),
[its ports](https://github.com/lowprocess/gfdtd#ports), [how it works](https://github.com/lowprocess/gfdtd#how-it-works)
and [accuracy and speed](https://github.com/lowprocess/gfdtd#accuracy-and-speed) against openEMS. The plugin's tests
are kicad2ems-fdtd's `tests/test_gfdtd_plugin.py`; the engine's, gfdtd's.
