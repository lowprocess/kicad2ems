---
name: kicad2ems-signal-integrity
description: Crosstalk and board-scale coupling studies with kicad2ems — NEXT/FEXT between named traces (kicad2ems.core.si) or a whole bus by net-name patterns one layer at a time (kicad2ems.core.coupling). Use when asked about crosstalk, coupling between nets, a DDR/bus layout review, victims and aggressors, or whether a spacing meets a limit.
---

# Signal integrity: crosstalk and board coupling

Two questions, both in `src/kicad2ems/core/`, both runnable from the CLI, `kicad2ems study`, and the window
(Study dock, *Signal integrity*). Docs: `docs/si.md`.

| Use | Module | Scale | Default engine |
|---|---|---|---|
| A few named traces, with the layout's explanation (Kc, Kl, wider-gap estimates) | `kicad2ems.core.si` | 2–10 nets | line model (+ a full-wave check) |
| A bus by name patterns, each layer alone, each victim's worst aggressors | `kicad2ems.core.coupling` | tens of nets | mom25d (line model for a first look) |

## Crosstalk between named traces

```sh
.venv/bin/python -m kicad2ems.core.si board.kicad_pcb --nets D0,D1,D2 --rise 100 --limit 3 --out out/xt
.venv/bin/python -m kicad2ems.core.si board.kicad_pcb --find        # pairs that run side by side, JSON
.venv/bin/python -m kicad2ems.core.si board.kicad_pcb --nets D0,D1 --solvers lines,gfdtd   # full-wave beside
```

Result `crosstalk.json`: `solved[...]` per solver (pairs with waveforms, victims' sums), `worst`, `sections`
(the stretches and their coupling estimates). The line model only couples traces within one layer's coupled
sections; pairs it cannot see are marked `modelled: false` and the limit is not judged.

## A bus, one layer at a time

```sh
.venv/bin/python -m kicad2ems.core.coupling board.kicad_pcb --nets 'DQ*,DQS*' --rise 100 --limit 15 --out out/cpl
.venv/bin/python -m kicad2ems.core.coupling board.kicad_pcb --nets 'DQ*' --solvers lines --out out/quick
```

- Nets: fnmatch patterns; poured nets (planes) are never traces.
- Each layer's stretch per net = its tracks on that layer (`si.trace_ends(kb, net, layer=...)`), ended where it
  leaves the layer. `--min-length` (5 mm) drops breakouts; they are listed in `left_out`.
- Each layer is solved on its own board file: its stretches + every layer's ground copper; the selected nets'
  vias and other-layer copper are removed. So inter-layer coupling is NOT included (stated in `not_modelled`).
- A differential pair's other leg (`X_P`/`X_N`, `X+`/`X-`) is not counted as an aggressor (`pair_leg`).
- Result `coupling.json`: per layer `matrix` (victim × aggressor NEXT/FEXT % and peak dB), `victims` (top
  aggressors, sums), `pairs`, `ends`, `tracks`; overall `worst` with the limit verdict. Figures
  `coupling_<layer>.png`; each layer's run under `<out>/<layer>/`.

Example: `examples/si_ddr.toml` (board from `python examples/make_rf_examples.py ddr examples/rf_ddr`):
mom25d ~9 min for both layers on a GPU, the line model ~18 s, NEXT within 3–15 % of each other.

## Reading the numbers

- Percentages are the largest share of the aggressor's swing at the victim's end for an edge of `--rise` ps
  (10–90 %), every end terminated in `--z0` (50 Ω).
- Stripline (one dielectric) has FEXT near zero; microstrip FEXT can exceed NEXT. If stripline shows large
  FEXT, suspect the model (a missing plane, a port on the wrong layer).
- A victim's sum adds every aggressor's peak (worst case), not a power sum.
- mom25d sees fan-outs and the whole layer's field; the line model only parallel coupled sections. Expect
  mom25d's FEXT to be higher where traces fan out.

## In a study

```toml
[study]
run = ["coupling"]          # or "crosstalk"
[study.coupling]
engine = "coupling_lines"   # the line model; default mom25d
[analysis.coupling]
nets = ["DQ*"]
rise = 100
limit = 15
```

`kicad2ems study cfg.toml [--only <question or engine>] [--out DIR]`; `--only` can only pick engines the config
asks for.
