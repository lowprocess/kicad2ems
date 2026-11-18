# gfdtd (GPU FDTD)

[← Index](index.md)

gfdtd is a 3D FDTD solver in the manner of openEMS that runs on a GPU, or on the
CPU through a compiled core. It is its own package ([gfdtd](gfdtd/README.md))
and runs openEMS models without kicad2ems; this page is about using it from
kicad2ems, through [kicad2ems-fdtd](fdtd/README.md)'s `gfdtd` solver
(`kicad2ems/fdtd/gfdtd.py`). `--sigma` is conductor loss, as openEMS's
conducting sheets, and `--rough` its own wideband fit (Hammerstad).

It gets **openEMS's model**: kicad2ems-fdtd builds the same CSXCAD geometry
(`build_fdtd`) through gfdtd's copy of CSXCAD's and openEMS's Python interface,
so the mesh, boundaries, ports, parts, excitation and end criteria are openEMS's
and take openEMS's settings (`--res`, `--fine`, `--nz`, `--bc`, `--end-db`,
`--end-on`, `--max-steps`, `--excite`, `--diel`, `--dump`, `--ff-freq`, ...: the
GUI's *FDTD (openEMS, gfdtd)* section). Only the engine differs: on the examples
the two agree to within 0.004–0.05 in |S|, and on one mid-range GPU gfdtd runs
8–20 times faster than openEMS on a 24-thread CPU (its CPU core about twice as
fast). `--solvers auto` picks it over openEMS whenever it can run
(`--converge`'s mesh levels run on it as on openEMS) ([Which
solver](usage.md#which)).

```bash
kicad2ems examples/rf_coupler.toml --solvers gfdtd              # the GPU when Vulkan finds one, else the CPU
kicad2ems examples/rf_coupler.toml --solvers openems,gfdtd      # both, compared in the report
kicad2ems board.toml --solvers gfdtd --gfdtd-backend cpu        # the CPU core (numpy without it: slow)
kicad2ems board.toml --solvers gfdtd --bc PML_8 --diel wideband --dump "E@F.Cu-0.05@2,4"
kicad2ems examples/antenna_dipole.toml --solvers gfdtd          # antenna mode: gain, efficiency, patterns
```

| Setting | Meaning |
|---|---|
| `--gfdtd-backend auto\|gpu\|cpu` (`[solver.gfdtd] backend`) | `auto` (default): the GPU when Vulkan finds one, else the CPU. `gpu`: Vulkan (slangpy, any GPU), or the run stops. `cpu`: the compiled core, else numpy. `GFDTD_BACKEND=vulkan\|core\|numpy` overrides it for every run |

It comes with kicad2ems (`kicad2ems.gfdtd`). The GPU needs slangpy (the
`gpu-vulkan` extra; [GPU](mom.md#gpu)); the CPU core is built once, `python -m
kicad2ems.gfdtd.core.build` (g++ with OpenMP and pybind11;
`GFDTD_ARCH=-march=x86-64-v3` for a portable build), without which the CPU runs
on numpy, some 15 times slower. `kicad2ems --check` shows which backend gfdtd
will use.

Each run writes `<name>_gfdtd.xml`, the model as an openEMS XML file that both `gfdtd` and `openEMS` can run. gfdtd
writes openEMS's probe files, field dumps and status lines, so kicad2ems's progress lines, port watch, Stop and
S-parameter code work on it unchanged. A preview with gfdtd and without openEMS draws the mesh (`<name>_mesh.png`).

gfdtd's README covers the rest: [CSXCAD and openEMS
compatibility](gfdtd/README.md#csxcad-and-openems-compatibility), [what it
models](gfdtd/README.md#what-it-models), [its ports](gfdtd/README.md#ports),
[how it works](gfdtd/README.md#how-it-works) and [accuracy and
speed](gfdtd/README.md#accuracy-and-speed) against openEMS. The solver's tests
are `tests/fdtd/test_gfdtd_solver.py`; the engine's, `tests/gfdtd/`.