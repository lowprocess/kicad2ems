# Decoupling chosen to a target impedance (`pcbqs.pdn --choose-decaps`)

[← Power checks during layout](power-workflow.md) · [Index](../index.md)

The rail-impedance tool (`kicad2ems.pcbqs.pdn`) says where the impedance a load sees is over its target. With
`--choose-decaps` it also chooses the capacitors for the rail's decap sites: the fewest parts (or the least cost)
that keep every load under its target over the band. In a study this is the *Decoupling* question (Power). Its
engine is the power-integrity analysis, so the rail is set once for the drop, the impedance and the decaps.

```sh
python -m kicad2ems.pcbqs.pdn board.kicad_pcb --rail +1V15 --volts 1.15 --source L5 \
    --ground-source U69 --load U21=1 --vrm-r 0.002 --vrm-l 20e-9 --plane-min 50 --dmax 3 \
    --choose-decaps --out pdn_1v15
python -m kicad2ems.pcbqs.pdn.decoupling --library --volts 3.3   # the parts it chooses from
```

It writes the choice into `pdn.json` as `pdn.decoupling`, and draws `decoupling.png` (|Z| at each load as fitted
and as chosen, with the targets). The report gets three sections: the loads (the margin as fitted and as chosen,
where each is still over, where the target is out of reach), a BOM (each part, how many, at which sites) and every
site (as fitted, as chosen, the capacitance at bias, the mount inductance).

## What it does

- **The sites** are the rail's decap footprints: two-pad `C*` parts from the rail's tree to ground, fitted or
  unfitted (DNP), or the ones `--decap-sites` names. A site's footprint fixes its package. The PDN model fixes its
  mount: its pads' copper to the planes and the via pair (`stack.RailPDN`).
- **Kept as fitted**, never changed:
  - the parts `--decap-keep` names;
  - a part with a vendor file (`--cap-file`);
  - a part whose package the library has no MLCC of (an electrolytic, a polymer can);
  - the regulator.
- **One solve.** The bare PDN (the planes, their ties, the series parts) between the loads and the sites comes
  from the impedance sweep's own factorisation at each frequency. The fixed parts are attached by Woodbury's
  identity. A part of impedance *z* at site *c* then changes the network by a rank-one term,
  Z' = Z − Z[:, c] Z[c, :] / (z + Z[c, c]), so every candidate set is closed in that form with no new solve.
- **The search** is deterministic. It runs once from no part and once from the fitted parts, and keeps the better
  result (`--decap-start empty` or `built` runs one of them).
  - It adds, one at a time, the part and site that lower the excess most (per unit cost with
    `--decap-mode cost`). It stops when every load is under its target, or when no part takes 1 % of the excess
    that is left.
  - Then, until nothing changes, it:
    - drops a part the others can do without;
    - replaces two parts by one;
    - moves a part to another site or value, where that raises the margin (so a later drop may become possible) or
      lowers the cost.
- **The excess** is the sum, over the loads and the band's frequencies, of log(|Z| / target) wherever |Z| is over
  the target, weighted by log-frequency step.
- **Out of reach.** Some frequencies are left out of the excess and named in the result. These are where a load's
  own pads and vias to the planes are already over its target (no capacitor changes them), or where the floor is.
  The floor puts an ideal part at every site: its mount and its package's ESL, with no capacitance and no ESR.
  That is what any capacitor there tends to above its resonance. A part at its resonance can dip under the floor,
  but not over a band.
- **The check.** The closed form of the chosen set is compared with a full solve that has the parts attached
  (`RailPDN.z`, a factorisation at each frequency) at 16 frequencies and at each load's worst. The result records
  the largest relative difference: 1e-10 to 3e-8 on the boards below, which is round-off.

## The library and the DC bias

The built-in library (`decoupling.library()`):

- **Values:** MLCCs from 1 nF to 100 µF (E3), in each package's range (0201 to 1 µF, 0402 to 10 µF, 0603 to 22 µF,
  0805 to 47 µF, 1206 and 1210 to 100 µF).
- **ESR:** from a typical curve, 12 mΩ × (C / 1 µF)^−0.4. That gives 30 mΩ at 100 nF and 5 mΩ at 10 µF.
- **ESL:** by package (0.25 nH for 0201 to 0.9 nH for 1210, as the PDN tool's defaults).
- **Dielectric:** X7R below 1 µF, X5R from 1 µF up.
- **Cost:** relative, by package and by how near the package's largest value the part is (a 0402 100 nF is about
  1.1, a 0402 10 µF 4).

`--decap-library FILE` replaces the library with a CSV whose header names `name, package, C` and any of `ESR, ESL,
dielectric, volts, cost`. `--decap-values 100n,1u,10u` limits the choice to those values, for a short BOM.

Every part counts at its capacitance at the rail's DC bias. An MLCC keeps 1 / (1 + (V / Vh)²) of its value, with
Vh = 2.4 V × (Cmax / C)^¼, where Cmax is its package's largest value (the thinnest layers). Where its rated
voltage is known, Vh is at least a quarter of it. C0G and NP0 parts keep all of their value. At 3.3 V, a part at
its package's largest value keeps 35 %, one a decade below 63 %, two decades below 84 %. These are typical
figures, not a vendor's: `--derate-table FILE` replaces them. It is a CSV of `package, C, volts, share`, with C
left empty for every value of a package, interpolated in volts.

The same curve applies to the fitted MLCCs whenever the decaps are chosen, so the comparison between as fitted and
as chosen is at the same bias. Alone it is `--derate-bias`, which reads the dielectric and the rating from the
part's value and fields (`10u X5R 6V3`).

## Options

| Option | Default | |
|---|---|---|
| `--choose-decaps` | off | choose the decaps (needs a target: `--target`, `--load-target`) |
| `--decap-mode` | `fewest` | `fewest` (parts) or `cost` (the library's relative cost) |
| `--decap-start` | `both` | `both`, `empty` (from no part) or `built` (from the fitted parts) |
| `--decap-sites` | every decap footprint | the sites to choose for, comma-separated |
| `--decap-keep` | | parts kept as fitted, comma-separated |
| `--decap-values` | the library's | the values to choose from, comma-separated |
| `--decap-library` | built in | a CSV of parts |
| `--decap-fmax` | `--fmax` | the top of the band the target is held over, Hz |
| `--derate-bias` | off | the fitted MLCCs at their DC bias, without choosing |
| `--derate-table` | the curve | a CSV of the DC-bias derating |

## On a real board

The Jetson AGX Thor carrier's +1V15 rail to U21, with the
[power-workflow](power-workflow.md) settings (1 A, the regulator as 2 mΩ + 20 nH, target 1.15 V × 5 % / 0.5 A =
115 mΩ), has 13 fitted decaps: 100 nF 0402s and 22 µF 0603s. With `--choose-decaps`:

- **Out of reach:** from 43 MHz, U21's own pads, and the sites' mount and package inductance, are over 115 mΩ, so
  that part of the band is out of reach (51 of the 160 frequencies).
- **Below 43 MHz:** five parts hold the target, against the thirteen fitted.

| | parts | worst \|Z\| / target below 43 MHz |
|---|---|---|
| as fitted | 13 | 0.967 |
| chosen | 5: 22 µF 0603 (C311), 10 µF 0402 (C88), 220 nF 0402 (C100), 22 nF 0402 (C86, C95) | 0.976 |

- **Time:** the choice took 0.1 s of the run's 75 s; the DC solve took 49 s of it.

The +3V3 rail is larger: 49 sites, loads J13, U21 and J12 at 1 A each, so a target of 110 mΩ. Ten parts meet
the target where 49 are fitted: 22 µF 0603 ×2, 10 µF, 100 nF, 22 nF ×3 and 10 nF in 0402, and 22 nF and 10 nF in
0603. The worst load is J13, at 0.978 of its target (0.970 as fitted). The target is out of reach from 40 MHz
at U21, 46 MHz at J12 and 108 MHz at J13. There the choice took 7.5 s (the search 5.4 s, the check 1.9 s), against the sweep's 22 s and
the DC solve's 79 s. That was measured with other runs on the machine (load average about 8).

## What it leaves out

- **Sites are not added.** A site's package is its footprint's. New footprints, and moves to a package the
  footprint does not take, are not proposed.
- **Plane pairs only.** A rail with no plane pair has no PDN model (`--plane-min`), and so no choice.
- **The library is typical.** The ESR, the derating curve and the cost are typical figures, not a vendor's: give
  your parts' (`--decap-library`, `--derate-table`).
- **The target in frequency only.** The droop after a load step, with the chosen parts, is in `pdn.json`'s `droop`
  for the fitted parts alone.
- **Greedy plus local moves.** The search is not proven optimal. On the test board, an exhaustive search finds no
  one part that meets the target where the search's two parts do (`tests/pcbqs/test_pdn_decoupling.py`).
