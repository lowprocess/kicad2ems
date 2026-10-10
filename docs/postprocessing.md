# 9. Post-processing and outputs

[← Simulation](simulation.md) · [Index](index.md) · [Next: Examples →](examples.md)

The list of output files is in [Usage](usage.md#outputs).

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
  the reflections. `--compare` overlays dashed curves labelled with the file name; `--fast` adds the
  line model dotted ([transmission lines](transmission-lines.md#fast)).

## Convergence

`converge.compare_levels` and `converge.verdict` (see [Meshing](meshing.md#convergence-levels));
`plots.plot_convergence` shows |S| per level (line style per level) and the deviation from the finest.
The verdict and the table go to the JSON, the log (a `WARNING` when not converged) and the report.

<a id="antenna-mode"></a>
## Antenna mode

After the run (the `antenna.analyse` hook):

| Result | How |
|---|---|
| S11, Zin | from the driven port; `match_summary`: resonance = minimum \|S11\|, −10 dB band around it |
| accepted / incident power | `port.CalcPort` at the recorded frequencies: `P_acc`, `P_inc` |
| directivity, Prad | `nf2ff.CalcNF2FF` on a 5° full sphere at every recorded frequency: `Dmax`, `Prad` |
| efficiency, gain | η = Prad / P_acc (dielectric and copper loss); G = D·η; realized gain = D·Prad / P_inc (includes the mismatch) |
| patterns | cuts φ = 0 (xz), φ = 90° (yz), θ = 90° (xy) at the pattern frequency (`--ff-freq` or the recorded frequency nearest the resonance), angle step `--ff-step`, normalised to the sphere's maximum; the 3D pattern from the sphere |

Angles: θ from +z (the normal of the board's top side), φ from +x (KiCad x); y points up in the KiCad
view. The phase centre is the outline centre at mid-stack. Outputs:

| File | Contents |
|---|---|
| `<name>_antenna.png` | \|S11\| with the −10 dB band, input impedance, directivity / gain / realized gain and radiation efficiency over frequency |
| `<name>_pattern.png` | gain (dBi) in the three cuts and a 3D pattern at the pattern frequency, with a summary |
| `<name>_antenna.json` | resonance, S11, Zin, bandwidth, the gain table and the cuts |

## Field dumps

`--dump FIELD@REGION[@GHz,...]` (repeatable; or `dump = [...]` in the TOML). `dumps.parse_dump` turns it
into a box:

| Part | Values |
|---|---|
| FIELD | `E`, `H`, `J` (total current density: shows the copper currents) |
| REGION | `F.Cu` (plane at a copper layer), `F.Cu-0.1` / `In1.Cu+0.05` (offset in mm, e.g. inside the dielectric under a trace), `z=0.3`, `x=12`, `y=7` (KiCad mm), `volume`, `box=x0,y0,z0,x1,y1,z1` |
| FREQS | GHz list → frequency-domain dump (HDF5) **plus PNG field maps** with copper outlines; omit → time-domain VTK series (animate in ParaView) |

```bash
kicad2openems board.toml --excite P1 --dump E@F.Cu-0.1@2,6 --dump J@In2.Cu@4 --dump E@y=7
```

Planes are clipped to the board outline + 1 mm and the stack + 1 mm of air, so a dump does not cover
the empty air margin; `volume` is the board + 0.5 mm; `box=` is used as given. `add_dumps` adds the
boxes to CSX (`AddDump`). Dumps are recorded in the run of the first excited port (`--dump-port P2` to
choose). `--preview` validates the specs and prints the number of points per dump; the run prints a
rough disk estimate. Files land in `<out>/openems/exc_<port>/dump_*.h5|.vtr`.

After the run, `plot_fd_dumps` reads frequency-domain plane dumps (`read_fd`, old and new openEMS HDF5
layouts) and plots |field| with the copper outlined, scaled to a **1 V incident wave** at the driven
port (checked: |Ez| under a 0.2 mm-high microstrip is 5.0 kV/m). Volume and time-domain dumps are left
for the GUI's Fields tab ([GUI](gui.md#fields)) or ParaView; `kicad2openems.dumps.read_fd(path)` reads an
HDF5 dump into numpy (needs h5py) and `kicad2openems.fields.read_vtr(path)` a time-domain frame.

Each run that records dumps also writes `dump_info.json` (before openEMS starts: the dumps, the timestep,
the board outline, copper per layer and ports, for outlines in a viewer) and, with frequency-domain
dumps, `dump_norm.json` (the incident port voltage at each dump frequency). `kicad2openems.fields` reads a
run folder with them:

```python
from kicad2openems import fields
src = fields.find_dumps('out')[0]                     # every dump under out/openems/exc_*/
pl = fields.plane(*src.load(-1), axis=2, index=0, component='z')   # a cut, in KiCad axes
fields.save_animation(src, 'ez.gif', axis=2, index=0, component='z')     # or .mp4 (needs ffmpeg)
```

<a id="json-summary"></a>
## JSON summary

`<name>_openems.json` is the full run summary (`Job.summary`), written by every run, also `--preview`
and `--no-report`:

| Key | Contents |
|---|---|
| `name`, `version`, `mode`, `command`, `created` | the run |
| `settings` | every option (`Settings.options()`) |
| `model` | outline, nets, stack, copper layers, ports, parts, notes |
| `mesh`, `levels`, `pulse_steps` | mesh statistics, convergence levels, excitation length |
| `lines` | [transmission-line sections](transmission-lines.md#outputs) per path, notes |
| `fast` | `--fast`: backend, time, Touchstone file, \|S\| table, comparison with openEMS |
| `results` | port names, reference impedances, excited ports, \|S\| table at the band edges and centre with extremes |
| `runs` | per driven port: seconds, timesteps, log file |
| `antenna`, `convergence`, `dumps` | when they apply |
| `images`, `files` | the plots and all output files |
| `warnings`, `log`, `runtime_s` | every message |

The keys older readers use (`ports`, `zref`, `excited`, `parts`, `diel`) are still written after a solver
run; `runtime_s` always.

<a id="the-report"></a>
## The report

`<name>_report.html` is rendered by `report.build(summary, base_dir)` from the summary alone: summary
figures (mode, band, ports, mesh, time step; resonance / S11 / bandwidth / directivity / efficiency /
gain in antenna mode; the convergence verdict; the line-model time), warnings, the model (stack,
copper, ports, parts, notes), the transmission-line sections, the mesh (statistics, preview,
convergence levels), S-parameters (plot and a table at the band edges and centre with minima and
maxima), the fast line model, antenna and convergence sections, field maps, the openEMS runs (time,
steps, link to `openems.log`), the output files, all settings and the full log. Images are embedded
(base64), so the page is one file; links to other outputs are relative. Styling is plain CSS with a dark
variant (`prefers-color-scheme`).

Paths in the summary (images, files, field maps, run logs) are stored relative to the output folder.
Because it needs only the JSON (and the plots at the paths stored in it), a report can be rebuilt, for
instance after changing `report.py`:

```python
import json
from kicad2openems.report import write_report
write_report(json.load(open('out/board_openems.json')), 'out/board_report.html')
```

To add something to the report: put the data into `Job.summary` in the stage that computes it, and add a
`section(...)` call in `report.build`.

---
[← Simulation](simulation.md) · [Index](index.md) · [Next: Examples →](examples.md)
