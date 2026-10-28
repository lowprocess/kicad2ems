# Results

[← Examples](examples.md) · [Index](index.md) · [Next: openEMS meshing →](meshing.md)

[Usage](usage.md#outputs) lists the output files. This page explains what's in them.

## S-parameters

Each solver writes a Touchstone file, `<name>_<solver>.sNp`.

- **Ports that weren't driven.** openEMS only drives the ports in `--excite`. The missing columns are filled
  in by reciprocity (S_ik = S_ki). Terms that are still unknown are written as 0 and listed in the file
  header.
- **Differential pairs** become a differential mode (`P1d`, 2·Z0) and a common mode (`P1c`, Z0/2).
- **Reference impedances.** The file uses Touchstone v1 when all ports share one impedance, and v2
  otherwise.
- **The plot** `<name>_<solver>.png` shows |S| in dB and a Smith chart of the reflections for the first
  solver, with the others and any `--compare` file drawn over it.

## Mesh convergence

With `--converge`, openEMS also runs coarser meshes and compares them with the finest
([details](meshing.md#convergence-levels)). `<name>_converge.png` shows each level, and the log and the
report give a verdict. A `WARNING` means the result hasn't converged.

<a id="antenna-mode"></a>
## Antenna mode

With `mode = "antenna"`, kicad2ems computes the far field after the run:

| Result | How it is computed |
|---|---|
| resonance, S11, input impedance | from the driven port; the resonance is the lowest \|S11\| |
| directivity | from the far field over the whole sphere |
| efficiency | radiated power / accepted power (dielectric and copper loss) |
| gain | directivity × efficiency |
| realized gain | gain including the mismatch loss |
| polarisation | at the gain maximum: the axial ratio, the hand (RHCP / LHCP, IEEE: seen from behind the wave, clockwise is right-handed; *linear* above 20 dB) and the gain in each hand (`G_rhcp_dbi`, `G_lhcp_dbi`) |
| patterns | the xz, yz and xy cuts, and a 3D pattern |

θ is measured from +z (the board's top normal) and φ from +x.

| File | Contents |
|---|---|
| `<name>_antenna.png` | match, impedance, directivity, gain and efficiency over frequency |
| `<name>_pattern.png` | the pattern cuts and a 3D pattern |
| `<name>_antenna.json` | the numbers behind both |

mom25d writes the same files with `_mom25d` in the name ([details](mom.md#antenna-mode)).

<a id="field-dumps"></a>
## Field dumps

`--dump FIELD@REGION[@GHz,...]` saves a field during an openEMS run. It can be repeated.

| Part | Values |
|---|---|
| FIELD | `E`, `H`, or `J` (current density, which shows the copper currents) |
| REGION | `F.Cu` (a plane at a copper layer), `F.Cu-0.1` (offset in mm, for example inside the dielectric), `z=0.3`, `x=12`, `y=7` (mm), `volume` (the board plus 0.5 mm), `volume+5` (plus 5 mm of air above and below), or `box=x0,y0,z0,x1,y1,z1` |
| GHz | frequencies: a frequency-domain dump, saved as HDF5 with a PNG map per frequency. Leave them out for a time-domain dump (a series of frames) |

```bash
kicad2ems board.toml --dump E@F.Cu-0.1@2,6 --dump J@In2.Cu@4 --dump E@volume+5@2.4
```

- Dumps are recorded in the run of the first driven port; `--dump-port` picks another.
- Frequency-domain dumps are scaled to a 1 V incident wave at the driven port.
- `--preview` checks the specs and reports the size of each dump.
- The files go to `<out>/openems/exc_<port>/`. The GUI's Viewer (its Cut and 3D views) reads them, and so
  does ParaView.

<a id="animations"></a>
### Animations

A frequency-domain dump holds a complex value at each point. Playing Re(F·e^jφ) for φ from 0 to 2π shows
what the field does over one cycle: standing waves pulse in place, travelling waves move.
`--dump-animate gif` (or `mp4`) writes this for every frequency-domain plane dump, from openEMS and from
mom25d's `--mom-dump`.

The same is available from Python:

```python
from kicad2ems import fields
src = fields.find_dumps('out')[0]
fields.save_animation(src, 'ez.gif', axis=2, index=0, component='z')
```

<a id="json-summary"></a>
## The run summary

`<name>_run.json` records the whole run, including previews:

| Key | Contents |
|---|---|
| `settings` | every option |
| `model` | outline, nets, stack, copper, ports, parts |
| `mesh`, `levels` | the openEMS mesh and convergence levels |
| `lines` | the [line sections](transmission-lines.md#outputs) |
| `solved` | per solver: its file, run time, ports, S-parameter table, and comparison with the first solver |
| `results` | the first solver's results |
| `runs` | openEMS: time and timesteps per driven port |
| `deviations`, `saw` | what each solver changed from the board, and the *What each solver saw* table |
| `estimates` | after a preview: each solver's estimated time and memory |
| `antenna`, `convergence`, `dumps` | when they apply |
| `warnings`, `log` | every message |

<a id="the-report"></a>
## The report

`<name>_report.html` is one self-contained page built from the run summary. It covers the model, the line
sections, the mesh, the S-parameters with a table at key frequencies, a section per solver, what each
solver saw of the board, antenna and convergence results, field maps, warnings and the full log.

The S-parameters are an interactive plot, drawn in the page itself (no library, nothing loaded from the
network), from each solver's Touchstone file next to the report: hover for every trace's value at a
frequency, tick the solvers and S-parameters to show, switch between |S| in dB and the phase, drag across
the plot to zoom and double-click for the whole band. The static image is folded below it.

The report can be rebuilt from the summary, for example after changing `report.py`:

```python
import json
from kicad2ems.report import write_report
write_report(json.load(open('out/board_run.json')), 'out/board_report.html')
```

---
[← Examples](examples.md) · [Index](index.md) · [Next: openEMS meshing →](meshing.md)
