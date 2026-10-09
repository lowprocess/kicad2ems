# 8. Post-processing and outputs

[← Simulation](simulation.md) · [Index](index.md) · [Next: Examples →](examples.md)

## S-parameters

`S[:, i, k] = uf_ref(port i) / uf_inc(port k)` from the run with port k driven (`simulation._run_port`).

- **Reciprocity** (`sparams.fill_reciprocal`): columns of ports not driven are filled from S_ik = S_ki.
  Their own reflection and the terms between two undriven ports stay unknown (NaN, written as 0, with a
  note).
- **Mixed mode** (`sparams.mixed_mode`): differential pairs become a differential (`P1d`, Z = 2·Z0) and a
  common (`P1c`, Z = Z0/2) mode. Unknown entries don't spread: the transform runs with zeros and only
  results that depend on an unknown entry are marked unknown.
- **Touchstone** (`touchstone.write_touchstone`): v1 when all reference impedances are equal, v2.0 with
  `[Reference]` otherwise. `read_touchstone` reads v1 / v2, RI / MA / DB (used by `--compare`).
- **Plot** (`plots.plot_results`): |S| in dB (reflection thick, transmission thin) and a Smith chart of
  the reflections; `--compare` overlays dashed curves labelled with the file name.

## Convergence

`converge.compare_levels` and `report` (see [Meshing](meshing.md#convergence-levels));
`plots.plot_convergence` shows |S| per level (line style per level) and the deviation from the finest.
The verdict and the table go to the JSON, the log (a `WARNING` when not converged) and the report.

<a id="antenna-mode"></a>
## Antenna mode

After the run (`antenna.run_antenna`):

| Result | How |
|---|---|
| S11, Zin | from the driven port; `match_summary`: resonance = minimum |S11|, −10 dB band around it |
| accepted / incident power | `port.CalcPort` at the recorded frequencies: `P_acc`, `P_inc` |
| directivity, Prad | `nf2ff.CalcNF2FF` on a 5° full sphere at every recorded frequency: `Dmax`, `Prad` |
| efficiency, gain | η = Prad / P_acc; G = D·η; realized gain = D·Prad / P_inc (includes mismatch) |
| patterns | cuts φ = 0 (xz), φ = 90° (yz), θ = 90° (xy) at the pattern frequency (`--ff-freq` or the recorded frequency nearest the resonance), normalised to the sphere's maximum; the 3D pattern from the sphere |

Angles: θ from +z (the board's top-side normal), φ from +x (KiCad x). The phase centre is the outline
centre at mid-stack. Outputs: `_antenna.png` (match, impedance, gains, efficiency), `_pattern.png` (cuts,
3D, summary), `_antenna.json`.

## Field dumps

`dumps.parse_dump` turns `FIELD@REGION[@GHz]` into a box (planes and boxes clipped to the board + 1 mm
and the stack + 1 mm), `add_dumps` adds it to CSX (`AddDump`, time-domain VTK or frequency-domain HDF5).
After the run, `plot_fd_dumps` reads frequency-domain plane dumps (`read_fd`, both HDF5 layouts) and
plots |field| with the copper outlined, scaled to a 1 V incident wave at the driven port. Volume and
time-domain dumps are left for ParaView (or `read_fd`).

## JSON summary

`<name>_openems.json`: ports, reference impedances, the mesh `info`, driven ports, parts, dielectric
model, per-run time and steps, plus `convergence` and `antenna` summaries when they apply, and the run
time.

<a id="the-report"></a>
## The report

`report.build(job)` assembles `<name>_report.html` from the finished `Job`: summary figures (mode, band,
ports, mesh, time step; resonance / S11 / bandwidth / directivity / efficiency / gain in antenna mode;
the convergence verdict), warnings, the model (stack, copper, ports, parts, notes), the mesh (statistics,
preview, convergence levels), S-parameters (plot and a table at the band edges and centre with minima and
maxima), antenna and convergence sections, field maps, the openEMS runs (time, steps, link to
`openems.log`), the output files, all settings and the full log. Images are embedded (base64), so the
page is one file; links to other outputs are relative. It is written after `--preview` too (model and
mesh only). Styling is plain CSS with a dark variant (`prefers-color-scheme`).

To add something to the report: keep the data on the `Job` (in the stage that computes it) and add a
`section(...)` call in `report.build`.

---
[← Simulation](simulation.md) · [Index](index.md) · [Next: Examples →](examples.md)
