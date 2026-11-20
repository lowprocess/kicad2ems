---
name: kicad2ems-testing
description: Run and extend kicad2ems's test suites, reproduce CI failures, keep CI and the docs build green, and produce the pre-release GPU report. Use before committing, when CI is red, when adding tests or reference data, or when a test fails only on CI or only sometimes.
---

# Testing kicad2ems

## Suites

Each part has its own folder under `tests/` and conftest; run them as separate pytest invocations:

```sh
tools/test_all.sh                    # every part, each in its own pytest
tools/test_all.sh core mom25d        # some parts
.venv/bin/python -m pytest -q -n 4 tests/core
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -n 4 tests/gui
MOM25D_GPU=off .venv/bin/python -m pytest -q -n 4 tests/mom25d    # as CI (no GPU)
GFDTD_BACKEND=cpu .venv/bin/python -m pytest -q -n 4 tests/fdtd
```

Typical times: core ~30 s, gui ~25 s (with -n 4), mom25d ~80 s, fdtd ~30 s, pcbqs ~4 min.

## Things to know

- `tests/core` and `tests/gui` install **stand-in solvers and analyses** (`tests/core/standins.py`,
  `tests/gui/fake_analyses.py`). To exercise a real solver from them, run it in a subprocess
  (`python -m kicad2ems ...`) as `tests/core/test_conformance.py` and `tests/core/test_coupling.py` do.
- Adding an analysis, a question, an example config or a solver changes lists that tests pin:
  `tests/core/test_registry_lists.py`, `tests/core/test_study.py` (the study tree),
  `tests/gui/test_study.py::test_study_dock`, and `tests/core/data/config_compat.json`.
- `config_compat.json` records what every config means. When adding a config, **add only its entry**
  (do not regenerate the whole file — that would hide changed meanings):

  ```python
  import json, os, sys; sys.path.insert(0, 'tests/core'); import config_compat as cc
  cc._qucs_installed(); os.chdir(cc.ROOT)
  p = 'tests/core/data/config_compat.json'; d = json.load(open(p))
  d['examples/new.toml'] = cc.meaning(os.path.join(cc.ROOT, 'examples/new.toml'))
  json.dump(dict(sorted(d.items())), open(p, 'w'), indent=1, sort_keys=True); open(p, 'a').write('\n')
  ```
- Reference S-parameters for regression live beside the tests that use them
  (`tests/fdtd/data/demo_lowpass_openems.s2p`); record how they were made in the test's docstring.

## CI (`.github/workflows/`)

- `stack.yml`: every part's suite on Python 3.13 and 3.14, cores built with `-march=x86-64-v2`, no GPU
  (`MOM25D_GPU=off`, `GFDTD_BACKEND=cpu`), mom25d's Vulkan LU on lavapipe, Elmer from cache. ~35 min.
- `docs.yml`: `sphinx-build -W` (a warning fails it) with every extra installed and the cores built.
- `install.yml`: the installer.

```sh
gh run list --branch main --limit 6
gh run view <id> --log-failed | grep -E "FAILED|Error|assert" | head
```

## Reproducing CI-only or intermittent failures

1. Find whether the failing test passed on the previous run (`gh run view <old id> --log | grep <test>`):
   if only unrelated files changed, suspect nondeterminism or the environment.
2. Match CI: rebuild the core with `MOM25D_ARCH=-march=x86-64-v2`, set `OMP_NUM_THREADS=2
   OPENBLAS_NUM_THREADS=2`, unset GPU, and loop the test 10–20 times counting failures.
3. Machine-dependent tools (qucsator present here, absent on CI) change behaviour: e.g. the line model
   resolves to qucs or scikit-rf.
4. Rebuild the native core afterwards.

## Docs build locally

```sh
rm -rf docs/reference/_generated     # stale autosummary stubs from old layouts break it
QT_QPA_PLATFORM=offscreen .venv/bin/python -m sphinx -W --keep-going -q -b html docs /tmp/docs_html
```
A new `.md` must be in a toctree or the build warns. (If `.venv/bin/sphinx-build` has a stale shebang, use
`python -m sphinx`.)

## Before a release: the GPU report

```sh
.venv/bin/python tools/gpu_report.py           # GPU tests + two boards on each backend vs CPU -> gpu-report/<date>.md
.venv/bin/python tools/gpu_report.py --check   # is gpu-report/latest.json for the GPU parts' current commits?
```
Commit the report.
