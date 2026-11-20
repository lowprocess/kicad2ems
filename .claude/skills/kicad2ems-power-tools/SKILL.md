---
name: kicad2ems-power-tools
description: Power-integrity and low-frequency tools in kicad2ems — pcbqs (rail DC drop and PDN impedance, converter near field, ngspice switching circuits on the board's copper, thermal, low-frequency S-parameters) and the fem engines (Elmer, Palace). Use when asked about IR drop, rail impedance, decoupling, a buck/boost loop, ngspice circuits with copper parasitics, board temperature, or capacitance/inductance extraction.
---

# Power tools: pcbqs and the fem engines

pcbqs lives in `src/kicad2ems/pcbqs/`, docs in `docs/pcbqs/` (start with `README.md`, `power-workflow.md`).
The fem engines (Elmer, Palace) are in `src/kicad2ems/fem/`, docs `docs/fem/`. All are kicad2ems
*analyses*: they appear in the study's questions (Power, Thermal) and run as commands of their own.

## The tools

| Question | Command | Notes |
|---|---|---|
| Rail DC drop / PDN impedance | `python -m kicad2ems.pcbqs.pdn board.kicad_pcb --rail +3V3 --load U1=2 ...` | `--describe --json` lists options |
| Converter input loop / near field | `python -m kicad2ems.pcbqs.mqs ...` | magneto-quasistatic, field maps |
| Switching circuit on the copper | `python -m kicad2ems.pcbqs.circuit model.json --out result` | needs ngspice on PATH |
| Circuit template | `python -m kicad2ems.pcbqs.circuit --template boost > boost.json` | |
| Low-frequency S-parameters | `python -m kicad2ems.pcbqs.sparams board.kicad_pcb --fmin 1e3 --fmax 1e8 --out lf` | also `--solvers pcbqs` in a run |
| Temperature | `python -m kicad2ems.pcbqs.thermal ...` | takes losses from a circuit run |
| Elmer DC / thermal / magnetics | `kicad2ems-fem-dc`, `kicad2ems-fem-thermal`, `kicad2ems-fem-mag` | Elmer from `tools/build_elmer.sh` or `K2E_ELMER` |
| Palace capacitance | `kicad2ems-fem-cap` | Palace from `tools/build_palace.sh` |

Always run `python -m <tool> --describe` first to see the real options: they change, and the window
builds its forms from the same tables.

## Through a study

A config holds several analyses and their settings; `kicad2ems study cfg.toml` runs them in plan order
(an analysis that `needs` another waits for it; thermal can take the latest circuit's losses):

```toml
[study]
run = ["dc", "pdn", "thermal"]
[[analysis.pdn]]          # several tables = several runs (a board's rails)
rail = "+5V"
volts = 5
[[analysis.pdn]]
rail = "+3V3"
volts = 3.3
```

Circuit examples with boards: `src/kicad2ems/pcbqs/circuit/examples/*.toml` (RC filter, rectifiers, buck,
boost, ...) and the wpthb full bridge `src/kicad2ems/pcbqs/data/wpthb_bridge.json`.

## Practicalities

- The pcbqs compiled core holds the mesher: `python -m kicad2ems.pcbqs.core.build`. Kernels fall back to
  NumPy with `PCBQS_NO_CORE=1`; the mesher has no fallback.
- Threads: `PCBQS_OMP_THREADS`, `PCBQS_FILL_THREADS`.
- Component models are explicit assumptions (ESR/ESL from fields or package defaults; vendor files via
  `--cap-file`, `--vrm-file`). Say which were assumed when reporting numbers.
- Validation status and known gaps: `docs/pcbqs/quasistatic-validation.md`, `docs/pcbqs/power-tools-review.md`
  (C/I/U findings), and the *Power tools* section of `ROADMAP.md`.
- Tests: `tools/test_all.sh pcbqs` (or `.venv/bin/python -m pytest -q -n auto tests/pcbqs`); real-board
  regressions need data outside the repo and skip without it.
