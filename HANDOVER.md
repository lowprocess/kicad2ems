# Handover: the line model's 2D field solver

Branch `ccr-5d4131b4-etdswn`, 8 commits on `main` (from `0271b41` to the handover commit). No PR yet. Everything
below is committed. Delete this file before merging.

## Why

It started from a feasibility question: a native KiCad feature for live impedance analysis with Altium parity
(single-ended, differential, coplanar), catching the impedance changes at pads and plane changes, coloured like the
line model's map. KiCad 10's tuning profiles already cover the width-from-impedance half. What neither KiCad nor
Altium does is check the routed copper, and the closed-form formulas are weakest exactly there (coplanar under
mask, planes with holes, copper thickness on pairs). Hence a 2D field solver, first in kicad2ems as the reference
implementation.

## What is in

| Where | What |
|---|---|
| `src/kicad2ems/lines/xsection.py` | The solver. Finite-volume Laplace on a tensor grid graded to every edge, extrapolated from the grid and the grid halved (the edge singularity makes the error first order). `CrossSection`, `Dielectric`, `Conductor`; `Result` (C, Ca, G_w, L, `single()`, `pair()`); builders `microstrip`, `stripline`, `coupled_*` (coplanar ground `s`, mask, thickness, N lines); `potential()` for drawing; `snap()`. Settings: `GROW`, `FINE`, `REACH`, `FAR`, `REFINE`, `SIMILAR`. |
| `lines/reference.py` | `classify` hooks: `FIELD` (`--lines-field`: every section from a cut of the board's copper across it, `cut()`, `_field()`, `_cut_section()`), `FIELD_CPW` (default: grounded CPW from an ideal cross-section, `_field_cpwg()`), `FIELD_DIFFERS` (2 %: when a field value replaces scikit-rf's / qucsator's own line model, `Element.field`), the cache bins `_distance_bins` / `_snap_distance`, mirror images share a solve. |
| `lines/topology.py` | `FIELD_GROUPS` (default): coupled sections' full C matrix (`_group_c`, cached, mirrored) and each pair's modes with copper thickness (`ze`, `zo`; `ze_sheet`, `zo_sheet`). |
| `solvers/lines/fastsolve.py` | `group_s` takes the group's field C; field-solved sections (`e.field`) become lines of their own Z0. |
| `config.py`, `pipeline.py` | `--lines-field` / `lines_field = true`. |
| `tools/xsection_figures.py` | Six figures: a pair's grid and fields, what the formulas miss, convergence, cache bins, five-line coupling, a real board cut (`--board`). |
| `tests/test_xsection.py` | 19 tests (exact and closed-form references, the board cut, the cache, the interdigital filter against openEMS). |
| `docs/transmission-lines.md` | `#field-solver`, `#lines-field`, `#similar`, `#cpw-thickness`, and `#coupled` rewritten. CHANGELOG, `docs/kicad-plugin.md` updated. |
| `tools/prototypes/` | Unfinished work and measurement scripts (below). |

## Results

**Accuracy** (Z0 against references): Cohn's exact stripline +0.30 %, coupled stripline +0.16 / +0.34 %,
Hammerstad & Jensen microstrip +0.1 %, Wheeler stripline with thickness +0.24 %, grounded CPW +0.38 %, Kirschning &
Jansen pairs within 0.65 %. The docs table predates the last grid change (`FAR`); refresh it with
`tools/prototypes/mesh_vs_grid.py`, whose grid column has these numbers.

**Against openEMS** (`tools/prototypes/lines_vs_openems.py`, scikit-rf, 15 boards with references): `rf_interdig`
|S21| 51.2 → 8.5 dB rms. Its stop bands are set by coupling between non-neighbouring fingers, which the formulas'
`group_lc` leaves out. Every other board is within 0.2 dB, both by default and with `--lines-field`. The examples
are drawn as the formulas' ideal cross-sections, so they can't show the board cut's value.

**A real board**, KiCad's CM5 Minima demo (`tools/prototypes/cm5_line_model.py`; the board isn't in the repository,
see the script):
- HDMI pairs, 100 Ω class: the zero-thickness formula gives 117.8 Ω differential, 35 µm copper 104.9 Ω, plus the
  board's 10 µm mask 101.3 Ω. The README's "+18 %" plugin screenshot is the formula's missing thickness. The
  README caption is unchanged.
- Grounded-CPW control lines: copper thickness takes 10–12 % off. Gupta's closed-form correction recovers only a
  third to a half of it, hence `FIELD_CPW`.
- Planes riddled with via clearances: `classify` falls through to the next plane down and is 30–75 % high. Only
  the board cut (`--lines-field`) fixes it. The default ideal-CPW solve still uses classify's far plane: SDA 90.7 →
  78.8 Ω against the cut's 63.2 Ω.
- Time for all 94 nets: formulas 10 s, default 60 s (146 CPW and 206 coupled solves), `--lines-field` 456 s
  (1,763 cuts).

## Environment notes (cloud container)

- `emsdev` can't be downloaded (GitHub returns 403). A stub with `cpu.core_threads` and `memory.total` /
  `memory.available` on `PYTHONPATH` lets the suite run. With the stub, one test fails,
  `test_api.py::test_every_api_name_is_documented`, only because the stub has no docstrings. Without it, 13 tests
  fail identically before and after these changes.
- qucsator isn't installable (no `qucs-s` in apt), so every comparison used scikit-rf. The qucsator path is
  untested: MCOUPLED pairs keep its own model, and groups of 3 or more lines use `group_s`.
- No full-wave solver here (openEMS, gfdtd, mom25d), so the CM5 numbers aren't checked against full-wave.
- `pip install scipy shapely matplotlib scikit-rf triangle`, then `pip install -e . --no-deps`.

## Open work, in order

1. **Speed is the blocker for live KiCad use.** A distinct cross-section takes 0.02–0.35 s on the tensor grid. The
   distance grading (`FAR`) already took the CM5's worst cuts from 8.5 s to 0.36 s (171k → 17.7k nodes, within
   0.07 %). What remains is the tensor grid itself: each edge's lines cross the whole domain.
2. **The quad mesh, mom25d's approach** (`tools/prototypes/xsection_quadmesh.py`). Rectangles start on the key
   lines and are halved in x or z only where they exceed a size field that grows from copper corners, so long thin
   cells fill the layers. Bilinear elements, with hanging nodes tied by linear interpolation.
   - Status: single lines are better than the grid (+0.02–0.2 % with 2–3x fewer nodes), but several conductors or
     thick copper go wrong (−5 % to −78 %).
   - Diagnosis so far: the patch test fails. With eps = 1, a linear potential leaves residuals of 0.01–0.03 at
     free nodes:
     - on the base mesh with thick copper (`stripline(..., t=0.035)`);
     - on the split mesh for a coupled pair.

     Fixed nodes are already excluded from the ties, and that didn't change anything.
   - Likely cause: halving x and z independently makes neighbouring sides overlap without nesting (side [0, 1]
     beside side [0.5, 1.5]). A node is then interior to a side whose own end is hanging on another, and averaging
     the duplicate constraints (the `W.multiply(1 / s)` step) is wrong there.
   - Fix to try: enforce nesting with 2:1 balance in each direction (split a neighbour whenever a side would cover
     more than two of the other's), or tie each hanging node only to the longest side containing it. The patch test
     in the last conversation step is the check.
   - The size field mirrors mom25d's meshing: aligned quads, a coarsening grade of 1.3, edges kept fine. mom25d also
     merges triangles to quads with a ~60° angle limit and keeps the trace edges triangular.
   - The constraint loop is Python and makes the prototype slower than the grid in wall time even with fewer nodes.
     Vectorise it once it's right.
3. **The isotropic triangle prototype** (`xsection_trimesh.py`, the `triangle` library) is set aside. Between thin
   parallel layers it needs small triangles along their whole width (41k nodes with a gap cap). Without the cap it
   starts too coarse (−8 % before extrapolation) and the extrapolation overshoots (+1–2 %).
4. **Boundary elements with a layered Green's function** (mom25d's `multilayer.py`, if it can give the 2D static
   kernel) would be the fastest: about 300–2,000 unknowns for a real cut, no box. That repository wasn't
   available in this session.
5. Smaller items:
   - refresh the docs' accuracy table;
   - wire `--lines-field` / `FIELD_CPW` choices into the KiCad plugin (its interface: `docs/kicad-plugin.md`; pair
     `ze` / `zo` now include thickness);
   - a full-wave check of a CM5 cut (openEMS or mom25d on a cropped region);
   - decide whether the README caption should change.

## Commands

```bash
pytest tests/test_xsection.py tests/test_tlines.py tests/test_coupled.py      # the solver and the line model
python tools/prototypes/lines_vs_openems.py base base                      # line model vs openEMS, 15 boards
python tools/prototypes/lines_vs_openems.py field field                    # the same with --lines-field
python tools/prototypes/mesh_vs_grid.py                                    # quad-mesh prototype vs the grid
python tools/prototypes/cm5_line_model.py CM5_MINIMA_3.kicad_pcb --nets HDMI
python tools/xsection_figures.py build/xsection_figures --board CM5_MINIMA_3.kicad_pcb
```
