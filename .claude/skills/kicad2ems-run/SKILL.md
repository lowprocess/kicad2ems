---
name: kicad2ems-run
description: Run a KiCad board (or a kicad2ems config) through kicad2ems's solvers — openEMS, gfdtd, mom25d, the line model, Palace, pcbqs — and read the results. Use when asked to simulate a board, get S-parameters, compare solvers, preview a mesh, estimate a run's time/memory, or debug why a run picked a solver.
---

# Running boards through kicad2ems

kicad2ems is one package (`src/kicad2ems/{core,gui,fdtd,gfdtd,mom25d,pcbqs,fem,ipc,dfsynth,emsdev}`).
A run imports the board, builds one board model (`<name>_board.json`), and hands it to each solver.

## Environment first

- Use the checkout's venv: `.venv/bin/python`, `.venv/bin/kicad2ems`. A `kicad2ems` on PATH may be an
  old install from another checkout (check `head -1 $(which kicad2ems)`).
- If `import kicad2ems` fails in `.venv`, reinstall editable: `.venv/bin/python -m pip install -e '.[test,fast,gui,fem,core,plugin]'`.
- The compiled cores are built in place, not shipped. If mom25d/gfdtd/pcbqs complain, build them:
  `for p in mom25d gfdtd pcbqs; do .venv/bin/python -m kicad2ems.$p.core.build; done`
  (CI uses `MOM25D_ARCH=-march=x86-64-v2` etc.; a native build behaves slightly differently numerically).
- `.venv/bin/kicad2ems --check` lists the solvers usable here and why others are not.
- `.venv/bin/python -m kicad2ems.emsdev` lists GPUs, cores, memory.

## Running

```sh
cd examples
../.venv/bin/kicad2ems rf_coupler.toml                         # the config's solvers (auto: the cheapest that can)
../.venv/bin/kicad2ems rf_coupler.toml --solvers mom25d,gfdtd --out /tmp/x --no-report
../.venv/bin/kicad2ems rf_coupler.toml --preview               # model, mesh and estimates only, no solve
../.venv/bin/kicad2ems board.kicad_pcb --port P1=J1.1 --port P2=J2.1 --fmin 0.1 --fmax 6
```

- Paths in a config are relative to the config. Generated example boards (`rf_*`, `antenna_*`, `demo_*`)
  are not in git: make them first (see the `kicad2ems-examples` skill).
- `--no-reuse` forces a fresh solve (by default a matching earlier result is reused).
- `--mom-solves N` caps mom25d's adaptive sweep. A very low cap gives an unstable fit (|S| > 1,
  passivity warnings): fine for timing, not for comparing results.
- Every option: `kicad2ems --help`; the GUI builds its form from the same option tables.

## Choosing where a solver runs

| Variable | Effect |
|---|---|
| `MOM25D_GPU=off\|vulkan\|cupy` | mom25d's LU on the CPU or a GPU backend |
| `GFDTD_BACKEND=cpu\|vulkan\|numpy` | gfdtd's engine |
| `EMSDEV_VULKAN_DEVICE=N` | which Vulkan GPU (0 = first) |
| `MOM25D_BLR_MIN_N`, `MOM25D_BLR_CPU_MIN_N` | when mom25d switches to its block low-rank solver (GPU / CPU-only) |

On the maintainer's machine device 0 is an RX 7800 XT; device 1, a GTX 980 Ti, is bus-limited
(read-back ~0.1 GB/s), so its timings are not representative.

## Results

In the output folder: `<name>_<solver>.sNp` (Touchstone), `<name>_run.json` (the summary: ports, zref,
`solved` per solver with its touchstone, `estimates`, warnings), `<name>_board.json`, plots and
`<name>_report.html` unless `--no-report`.

```python
from kicad2ems.core.touchstone import read_touchstone
f, S = read_touchstone('out/rf_coupler_mom25d.s4p')     # f in Hz, S (nf, n, n)
```

Compare two solvers with `np.abs(Sa - Sb).max()` on the same frequency grid (check `np.allclose(fa, fb)`).
`tools/compare_solvers.py` and `tools/bench_solvers.py` do this over many boards.

## Estimates

`--preview` prints `estimate: <solver>: N unknowns, ~solves, ~seconds, ~GB (path)` for each solver; `auto`
picks the fastest field solver by these. mom25d's estimate meshes as the run does and its memory plan
(`kicad2ems.mom25d.policy.memory_plan`) counts the LU, the GPU's double Z, the kept via blocks and the
near field. If an estimate disagrees with a run, compare the estimate's unknowns with the run's
`mesh:` log line first.
