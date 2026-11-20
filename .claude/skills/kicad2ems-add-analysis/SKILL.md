---
name: kicad2ems-add-analysis
description: Add a new analysis (question) to kicad2ems so it runs from the CLI, from `kicad2ems study`, and in the window — the Analysis class, the study Question, engines/variants, report/figures, and every list and test that must change. Use when implementing a new kind of study (an SI, power, thermal or extraction question) or a new engine for an existing question.
---

# Adding an analysis

An analysis is a tool that works on the board but is not an S-parameter solver. The window knows none by
name: it builds forms from `options()`, runs `command()` as a separate process, and shows `report()`,
`curves()`, `figures()` from the result folder. Read `src/kicad2ems/core/analyses.py`'s module docstring
(the full contract) and a worked example: `kicad2ems.core.coupling` (newest, compact) or `kicad2ems.core.si`.

## 1. The tool: a module with a CLI

- `python -m kicad2ems.<part>.<tool> board ... --out DIR` writes one JSON result (`format`, `version`) plus
  figures into DIR, prints progress lines, exits 2 with a message on a `ValueError`.
- Reuse the pipeline rather than reimplementing: `config.build_settings(dict(...))`,
  `pipeline.board_from_settings(s)` (a `Board`; `.to_dict()` is a board file you can edit and solve via
  `Job(build_settings(dict(board='x_board.json', ...))).run()`), `touchstone.read_touchstone`.
- Figures: matplotlib `Figure` + `FigureCanvasAgg` (never pyplot); never let a figure fail the run.

## 2. The Analysis class (same module, bottom)

```python
from .analyses import Analysis
class MyTool(Analysis):
    name, label, tool = 'mytool', 'My tool', 'kicad2ems.core.mytool'
    about = "one line"
    order = 20                    # place in lists and in a study's plan
    result = 'mytool.json'
    provides = ('myquestion',)    # the study question(s) it answers
    engine = 'mom25d'             # what a list shows beside the question
    variants = (('', 'mom25d', dict(solvers='mom25d')),       # each an engine: mytool, mytool_lines
                ('lines', 'line model', dict(solvers='lines')))
    def check(self): ...          # (usable, one line)
    def check_with(self, sets): ...   # a variant's check
    def options(self): ...        # [Option]; level='basic' shows first; pick='nets'/'parts' picks on the board
    def tag(self, values): ...    # short run-folder tag
    def command(self, values, board, out, preview=False, results=None): ...  # argv; ValueError: what's missing
    def status(self, line): ...   # progress line -> text or None
    def report(self, folder): ... # dict(title, summary, sections=[dict(title, text, table=dict(columns, rows, formats))], notes, images, footer)
    def figures(self, folder): ...# [dict(name, value, unit)] named as the Question says
```

Variant settings (`dict(solvers=...)`) arrive in `values` without being options.

## 3. Register it

- `src/kicad2ems/core/analyses.py`: add `'kicad2ems.<part>.<module>:<Class>'` to `ANALYSES`.
- `src/kicad2ems/core/study.py`: add a `Question(name, label, group, about, order=, figures=)` to
  `QUESTIONS` if no existing question fits (or set `questions` on the class for one of its own).

## 4. Tests and lists to update

- `tests/core/test_registry_lists.py` (analysis names in order), `tests/core/test_study.py` (the study
  tree's questions), `tests/gui/test_study.py::test_study_dock` (dock rows per group).
- New tests for the tool: pure functions directly; the end-to-end run in a **subprocess** (core/gui suites
  use stand-in solvers). Keep them under ~20 s: use the line model or a small board.
- An example config → `config_compat.json` entry (kicad2ems-testing skill) and `GROUPS` in
  `examples/__init__.py` (kicad2ems-examples skill).

## 5. Docs and records

- The feature's page in `docs/` (options table, what it does, what it leaves out, an example with numbers),
  `docs/index.md` row, `docs/api.md` if it is listed there. Build docs with `-W` locally.
- `CHANGELOG.md` (*Added*), and update/remove the `ROADMAP.md` item, leaving what is still to do.

## Verify

```sh
.venv/bin/python -c "from kicad2ems.core.analyses import registry; print(registry()['mytool'].check())"
.venv/bin/kicad2ems study examples/my_example.toml --list
.venv/bin/kicad2ems study examples/my_example.toml --out /tmp/s
.venv/bin/python -m pytest -q -n 4 tests/core && QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q -n 4 tests/gui
```
