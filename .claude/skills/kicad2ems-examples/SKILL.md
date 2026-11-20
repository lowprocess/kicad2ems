---
name: kicad2ems-examples
description: Generate kicad2ems's example boards, write a new parametric test board (microstrip, stripline, vias, planes, buses), and add it as an example config the window and CLI list. Use when a task needs a board to test on, when an example's board is missing, or when adding a new example.
---

# Example boards

`examples/` is the package `kicad2ems.examples`: configs (`*.toml`) and the generators of boards that are
not in git (`.gitignore`: `examples/rf_*.kicad_pcb`, `antenna_*`, `demo_*`). Only `hairpin`,
`rf_interdig` and `sma_launch` boards are committed.

## Making boards

```sh
.venv/bin/python examples/make_rf_examples.py ddr examples/rf_ddr     # one: <kind> <out base>
.venv/bin/python examples/make_rf_examples.py all                     # every rf_* board
.venv/bin/python examples/make_antennas.py ...                        # antenna_*
.venv/bin/python examples/make_demo_board.py examples/demo_lowpass lowpass
```
`kicad2ems.examples.make_boards()` makes every missing board; the window does it when an example opens
(`prepare_example`). The generator is found from the board's prefix (`GENERATORS` in
`examples/__init__.py`): `rf_<kind>` → `make_rf_examples.py <kind>`.

## Writing a generator (make_rf_examples.py)

Use the `Board` helper class there:

- `bd = Board(nets=("", "GND", "SIG1", ...))` — net 1 is ground; refer to nets by index.
- `bd.track(pts, width, layer='F.Cu', net=2)`; `bd.via(x, y, net=..., size=, drill=, layers=('F.Cu','In2.Cu'))`.
- `bd.plane(layer, W, H, net=1, holes=[(x, y, r), ...])` — a filled zone with antipads.
- `bd.port(n, x, y, rot, layer=...)` — a port footprint (omit ports when the question places them, as the
  SI questions do).
- `bd.write(path, W, H, four=True)` — 4-layer (F.Cu, In1.Cu, In2.Cu, B.Cu, `H4` mm dielectric each);
  `ground=True` (default) fills B.Cu with GND.
- Widths: `w_on(50.0, H4)` microstrip, `solve(lambda w: -strip_z(w, 2*H4, ER)[0], -50.0, 0.03, 2.0)` stripline.
- Register it in `KINDS` and return a dict of what was built (widths, nets) — it is printed.

## Adding an example

1. The generator (above) or a committed board.
2. `examples/<name>.toml`: a comment header saying how to make the board and what results to expect (with
   the date and solver), `board = "<file>"`, `out = "out"`, and its `[study]` / `[analysis.*]` tables if it
   is an analysis example. Look at `examples/si_bus.toml`, `examples/si_ddr.toml`.
3. Add it to a menu group in `GROUPS` (`examples/__init__.py`).
4. Add its `config_compat.json` entry (see the `kicad2ems-testing` skill) and run `tests/core`.
5. Mention it in the relevant doc (`docs/examples.md` or the feature's page).
