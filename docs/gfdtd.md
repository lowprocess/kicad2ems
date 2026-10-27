# gfdtd (GPU FDTD)

[← Index](index.md)

gfdtd is a 3D FDTD solver written for kicad2ems, in the manner of openEMS, that runs on a GPU, or on the CPU
through a compiled core. It is its own package, in its own repository ([gfdtd](https://github.com/lowprocess/gfdtd)), and runs openEMS
models without kicad2ems; this page is about using it from kicad2ems. Name it
(`--solvers gfdtd`) or tick it under Solvers in the GUI, where its section (Runs on: auto, gpu, cpu) follows
openEMS's: it uses openEMS's mesh and run settings. `--solvers auto` picks it over openEMS whenever it can run:
not with `--sigma`, `--converge` or `--drive`, which it would ignore (see [Which solver](usage.md#which)). In
the GUI those settings are in the *openEMS + gfdtd (FDTD)* section. A preview with gfdtd and without openEMS
draws the mesh (`<name>_mesh.png`, and the grid in the Viewer's 3D view).

It gets **openEMS's model**: kicad2ems builds the CSXCAD geometry with the same code (`build_fdtd`), through
gfdtd's copy of CSXCAD's and openEMS's Python interface. The mesh, boundaries, ports, parts, excitation and
end criteria are openEMS's and take openEMS's settings (`--res`, `--fine`, `--nz`, `--bc`, `--end-db`,
`--end-on`, `--max-steps`, `--excite`, `--diel`, `--dump`, `--dump-port`, antenna mode's `--ff-freq`,
`--ff-step`). Only the engine differs. On the examples below the two agree to within 0.004–0.05 in |S|. On one
mid-range GPU gfdtd runs 8–20 times faster than openEMS on a 24-thread CPU; its CPU core, on that same CPU, about
twice as fast, and five times with PML and wideband dielectrics (see its [Speed](https://github.com/lowprocess/gfdtd#speed)).

```bash
kicad2ems examples/rf_coupler.toml --solvers gfdtd              # the GPU when CuPy finds one, else the CPU
kicad2ems examples/rf_coupler.toml --solvers openems,gfdtd      # both, compared in the report
kicad2ems board.toml --solvers gfdtd --gfdtd-backend cpu        # the CPU core (numpy without it: slow)
kicad2ems board.toml --solvers gfdtd --bc PML_8 --diel wideband --dump "E@F.Cu-0.05@2,4"
kicad2ems examples/antenna_dipole.toml --solvers gfdtd          # antenna mode: gain, efficiency, patterns
```

| Setting | Meaning |
|---|---|
| `--gfdtd-backend auto\|gpu\|cpu` (`[solver.gfdtd] backend`) | `auto` (default): the GPU when CuPy finds one, else the CPU. `gpu`: CuPy (CUDA or ROCm), or the run stops. `cpu`: the compiled core, else numpy |

Install it with the `gfdtd` extra, or from its repository:

```bash
python -m pip install '.[gfdtd]'                                # here: gfdtd from https://github.com/lowprocess/gfdtd
python -m pip install -e ../gfdtd                               # or a clone of it, editable
```

Without it, `kicad2ems --check` lists gfdtd as not installed and `--solvers auto` leaves it out.
The GPU needs CuPy for your stack: one of the `gpu-*` extras (`pip install '.[gpu-rocm7]'`), as for mom25d
([GPU](mom.md#gpu)). The CPU core is built once, as mom25d's (g++ with OpenMP, and pybind11: the `core`
extra):

```bash
python -m gfdtd.core.build          # gfdtd/_core*.so, in the installed package; GFDTD_ARCH=-march=x86-64-v3 for a portable build
```

Without it the CPU runs on numpy, some 15 times slower. `kicad2ems --check` shows which one gfdtd will use.
`GFDTD_BACKEND=cupy|core|numpy` overrides the choice for every run.

Each run writes `<name>_gfdtd.xml`, the model as an openEMS XML file that both `gfdtd` and `openEMS` can run.
gfdtd writes openEMS's probe files, field dumps and status lines, so kicad2ems's progress lines, port watch
(`--end-on ports`), Stop and S-parameter code work on it unchanged.

gfdtd's own README covers the rest: [CSXCAD and openEMS compatibility](https://github.com/lowprocess/gfdtd#csxcad-and-openems-compatibility),
[what it models](https://github.com/lowprocess/gfdtd#what-it-models) (and what not yet: conductor loss, phased drives, `--converge`),
[its ports](https://github.com/lowprocess/gfdtd#ports), [how it works](https://github.com/lowprocess/gfdtd#how-it-works) and [accuracy and speed against
openEMS](https://github.com/lowprocess/gfdtd#accuracy-and-speed) on these examples. The plugin is `gfdtd/kicad2ems.py` in gfdtd's repository: openEMS's
plugin, run on gfdtd; its tests are `tests/test_gfdtd.py`.
