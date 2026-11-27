# Drop at temperature (`kicad2ems.pcbqs.electrothermal`)

A rail's DC drop with its copper at the temperature it runs at, and the board's temperature from that copper's own
loss and the parts': the [DC drop](power-workflow.md) and the [thermal grid](thermal.md) solved together. Copper's
resistance rises 0.39 % a kelvin, so a rail whose copper runs 50 K above 20 °C drops 20 % more than the DC tool's
answer at 20 °C, and loses 20 % more in its copper. Over time, for a load that steps or bursts, it gives the
temperature of each part and of the hottest copper.

    python -m kicad2ems.pcbqs.electrothermal board.kicad_pcb --rail +5V --volts 5 --load J2=6 \
        --heat Q1=0.25 --heat L1=0.3 --out et
    python -m kicad2ems.pcbqs.electrothermal board.kicad_pcb --rail +5V --volts 5 --load J2=6 \
        --loads-heat --transient 1200 --current-profile pulse:60,240@10 --out et
    python -m kicad2ems.pcbqs.electrothermal --describe --json

In a study it is the **Drop at temperature** question (group Thermal), and an engine of **Rail drop** as well,
*pcbqs (at temperature)*, beside the drop at 20 °C: the study's *Engines compared* table then sets the two drops
side by side. Its figures are the DC drop's, `drop <load>` (the rail's own copper, mV) and `drop with return <load>`,
at temperature, then `drop cold <load>` (at 20 °C), `hottest` (°C), `loss with return` (mW) and, with a transient,
`hottest in time`.

## What it does

1. **The DC drop** (`pcbqs.pdn.dc`): the rail tree's copper and its return's, each layer a resistive sheet on
   linear triangles, the vias' plated barrels, the source held at the rail's volts and each load drawing its current.
   The rail options are the DC tool's own (`--rail`, `--ground`, `--volts`, `--load REF=AMPS[@RETURN]`, `--source`,
   `--dcr`, the parts' `pdn.load` fields, ...).
2. **Its loss as heat**: each triangle's g |∇V|² A, put in thirds at its three edge midpoints, and each barrel's
   I²/g, half at each of its two layers, into the cell of the thermal grid each point lies in. The series parts'
   own loss (a sense resistor's, a bead's: I² R at their value) heats their pads, as a part's loss does, unless
   `--heat` gives it.
3. **The temperature** (`pcbqs.thermal`'s grid: every layer's copper of every net, the laminate, the films, the
   edges, the parts' `--theta`, `--air`, `--hold`) of that heat and the parts' (`--heat REF=W`; `--loads-heat`: each
   load dissipates its V × I on its pads).
4. **The resistivity at that temperature**, each triangle's at its centroid and each barrel's at its via,

       ρ(T) = ρ20 (1 + α (T − 20 °C)),   α = 0.00393 / K (`--alpha`),

   and the drop solved again, until no triangle's temperature moves by more than `--tolerance` (0.05 K) between two
   passes (`--passes`, 20, at most; `electrothermal.json` says whether it settled). Each pass moves by the loop's
   gain times the last, α ΔT / (1 + α (T − 20)): 0.15 at a 45 K rise, so three to five passes. The thermal system
   is factored once; a pass is a DC solve and a back-substitution.

With `--alpha 0` it is the two tools apart: the drop to the last bit the DC tool's, the temperature the thermal
tool's of that drop's loss and the parts' (tested).

## Over time

`--transient SECONDS` runs the thermal grid's transient (`pcbqs.thermal.transient`, [thermal.md](thermal.md#over-time))
from the steady passes' current pattern:

- `--current-profile SHAPE`: the loads' current over time, a share of their DC current: `step[:A,B][@T]`,
  `off@T`, `pulse:ON,OFF[,LOW,HIGH][@T]` or a file of `t_s, share`. The copper's loss and the series parts' go as
  its square; with `--loads-heat` the loads' own as the share.
- `--profile REF=SHAPE`: a part's loss (a file's values in watts); `all=SHAPE` every part without its own.
- `--start cold` (the ambient) or `steady` (the steady temperature of the profiles' values before t = 0: a load step
  from a base load, a cooling).

The copper's loss follows its temperature there too: each point's I²R (1 + α (T − 20)) / (1 + α (T_hot − 20)) about
the hot pattern, which is linear in T, so it is the matrix A − α diag(W′) and still one factor a step length. That
is exact while the current keeps its pattern (a load scaled up and down does); the pattern's own shift as the
copper warms (current leaving hot copper for cool) is in the passes and not in the transient:
`electrothermal.json`'s `transient.frozen_hottest_C`, the transient's own steady state, against `copper.hottest_C`,
the passes', says how much (2 mK on the test rail).

It writes `electrothermal-transient.json` (each part's temperature and the hottest copper against time, the copper's
loss, each part's peak and when, the board's longest time constants) and `electrothermal-transient.png`.

## What it writes

- `electrothermal.json`: each load's drop at temperature and at 20 °C (`drop`, `drop_cold`, the supply side and the
  ground's rise each way), the copper's loss hot and cold, the series parts', the hottest copper (its layer and
  place) and its rise, each part's temperature (`thermal`: the thermal tool's summary), the passes (each one's
  hottest copper, loss, worst drop and largest move), `settled`, and the transient's summary.
- `thermal.png` (each layer's temperature), `dc_power_<layer>.png` and `dc_ground_<layer>.png` (the drop and the
  current at temperature).
- The temperature (`thermal_board.json`, `thermal/`) and the drop (`pdn_board.json`, `pdn/`) where kicad2ems's
  viewer reads results.

## Checks (tests/pcbqs/test_electrothermal.py)

| Case | Closed form | Here |
|---|---|---|
| 60 × 20 mm of 35 µm copper over 1.6 mm FR-4, 25 A end to end, edges insulated: the heat even, so the temperature | T − Ta = P0 (1 + α (Ta − 20)) / (H A − α P0): 70.329 °C | 70.326 °C mean, the drop within 5e-5, five passes (moves 6.4, 0.95, 0.14, 0.021 K) |
| A 1 mm trace along a 60 mm wide plane-less board, 2.4 A | the two-region fin across it (the strip's copper and the laminate), coupled: 8.81 K | 8.80 K (−0.2 %); IPC-2221's external-trace fit 10.1 K (a sanity bound) |
| `--alpha 0` on a rail board | the DC tool's drop, the thermal tool's map | identical (bit for bit; 1e-9 K) |
| The transient's frozen pattern, run long | the passes' temperature | within 0.03 K |

## An example

The synchronous buck's board (`pcbqs/circuit/examples/05_buck_synchronous.kicad_pcb`, 36.8 × 20.4 mm, two layers),
its output `out` from L1 to the load connector J2 at 5 A, with Q1, Q2 and L1 dissipating 0.25, 0.2 and 0.3 W:

    python -m kicad2ems.pcbqs.electrothermal 05_buck_synchronous.kicad_pcb --rail out --volts 5 --load J2=5 \
        --heat Q1=0.25 --heat Q2=0.2 --heat L1=0.3 --dmax 0.5 --transient 1200 --current-profile pulse:60,240@10

The copper runs to 80.0 °C, so the drop at J2 is 6.65 mV against 5.52 mV at 20 °C (+20.5 %), and the copper's own
loss 33.3 mW against 27.6. Settled in three passes (0.40, then 0.003 K), 0.3 s; the whole run 2.7 s. With the load
in 60 s bursts every 300 s the hottest copper reaches 78.8 °C (the parts' losses held constant); the board's longest
time constants are 143, 19 and 8 s.

## What it leaves out

- **The parts' own resistance against temperature**: a series part's R is its value (`--dcr`), a load's current
  is what is given.
- **The current's shift as the copper warms, in the transient** (above): the passes have it.
- **What the thermal grid leaves out** ([thermal.md](thermal.md#what-it-does-not-do-yet)): the film's change with
  temperature, air one part warms reaching another, the heat the parts' bodies hold.
- **Only DC**: a converter's ripple current's AC loss in the copper (skin, proximity) is the circuit tool's
  (`pcbqs.circuit --fields` writes it; the thermal tool takes it with `--circuit`).
