# 13. GUI

[← Maintenance notes](maintenance.md) · [Index](index.md)

```bash
pip install '.[gui]'                 # PySide6 + pyqtgraph + h5py
kicad2ems-gui [config.toml]      # or: python -m kicad2ems.gui
```

The GUI edits a run's settings, starts the command-line tool as a separate process with `--events`, and
follows its event stream (`gui/runner.py`). Closing the window never loses a run's results, and the GUI
itself needs no openEMS.

The window has a **toolbar** (New, Open, Save, Save as · Preview, Run, Stop · Results: open a results folder,
Report: its HTML report), the **Settings** dock on the left and the **results workspace** on the right.

**Settings** are one collapsible section per group (board, band, solvers, openEMS mesh, openEMS incl. *Mode*
and pattern frequencies, field dumps, output), all collapsed when the window opens; each header counts the
settings set in it (`Band (3 set)`). Empty fields show their default.

**The results workspace** is one page of panels, tabbed together; drag a panel's title to split the page,
float it, or close it (*View* reopens any, *Show all panels* every one). A preview opens the panels it fills
(*Model and mesh*, *Line model*, and *S-parameters* when a solver ran), a run opens *Run* while it goes and
then its results: *S-parameters*, *Fields* when it wrote dumps, *Antenna* in antenna mode (in front),
*Convergence* with `--converge`. A preview also brings *System* to the front with its estimates (and logs
them: `estimate: mom25d: 13,670 unknowns, ~16 EM solves, ~2 min, ~5.4 GB + 1.9 GB on the GPU`). Opening a config shows the results already in its output folder the same way.

| Panel | What it shows |
|---|---|
| S-parameters | interactive S-parameter plots: openEMS solid, mom25d dash-dot, line model dotted, the `compare` file dashed, each solver a group of tick boxes; a Smith chart of the return loss beside them, a cursor readout and markers ([below](#plots)); *Add results folder…* overlays another run's S-parameters, labelled by its folder (*Remove added*); *Open folder* |
| Model and mesh | the model plot and openEMS's mesh preview |
| Line model | the line sections of the transmission-line model |
| Run | stage, one progress bar per driven port (step, dB, ETA), the decay curves, the log; *Stop* writes `ABORT` into every run folder (openEMS stops and finishes normally, so the results up to that point are written) |
| Fields | the field dumps, openEMS's and mom25d's (`--mom-dump`): live view, x / y / z cuts, playback over time or phase, PNG / GIF / MP4 ([below](#fields)) |
| Antenna | antenna mode: S11, gain / directivity / realized gain / efficiency over frequency, the far-field pattern at any recorded frequency ([below](#antenna)); a folder with openEMS's and mom25d's antenna results has a selector for them |
| 3D fields | volume dumps as a translucent voxel cloud over the copper, navigable, animated over the phase ([below](#fields3d)) |
| Convergence | openEMS's mesh-convergence plot (`--converge`) |
| System | beside the others, open from the start: each solver and whether it can run here (its check: version, or what is missing), the CPU cores, the system memory in use and available, the GPU mom25d would use (backend, free and total memory, or why there is none), *Refresh*; after a preview, each full-wave solver's estimate on that board's mesh: time, memory and (mom25d) GPU memory against what is free (*fits* / *tight* / *does not fit*, 20 % kept spare), how mom25d would solve it, and the model size (cells or unknowns). A solver that cannot run here is greyed |

**Solvers.** One tick box per solver plugin: *openEMS (FDTD)*, *mom25d (MoM)*, *line model (qucsator)*,
*line model (scikit-rf)*, and any solver installed as a plugin ([solvers](usage.md#solvers)), plus *auto*
(the full-wave solver estimated cheapest for the board, and the line model: [auto](usage.md#auto)). The config
gets `solvers = [...]` with the ticked ones; with *auto* alone ticked, `solvers` is left out (the
default). Opening a config ticks the solvers it runs (its `solvers`, `mom = true` and `fast = ...`;
`lines` ticks qucsator when it is installed, else scikit-rf). The form's fields are made from the solvers'
option tables, so a plugin's settings that have a GUI label appear in it too.

<a id="configs"></a>
## Configs

*File* → *New* (Ctrl+N), *Open…* (Ctrl+O), *Open recent*, *Examples* (every `examples/*.toml`), *Save*
(Ctrl+S), *Save as…* (Ctrl+Shift+S); the same in the toolbar. The window title shows the
config and `*` while it has unsaved changes; opening another config or closing the window asks first.
*Run* → *Preview* (Ctrl+P), *Run* (Ctrl+R), *Stop* (Ctrl+.).

Saving over a config that was opened **keeps the file**: its comments, key order, `[solver.<name>]` tables and
keys the GUI does not know stay; each setting gets its new value in place (a trailing comment stays in
its column), a cleared field removes its line, and settings that were not in the file are added above
the first table. *Save as…* does the same into the new file. Paths are written relative to the config's
folder (absolute when the target is more than one folder up), so a config can move with its board. A
new config gets only the settings that differ from the defaults. A config whose board is missing (the
generated antenna examples) says so in the status bar, with the generator command in the file's header.

A loaded config keeps the settings that have no field in the form (`air`, `bc`, `no_mask`, `diel`, …) and
passes them on to its runs. When the config's output folder already holds results, they are shown. An
empty *Output folder* means `out` next to the board (the command line's default too); saving leaves it
empty rather than writing the folder the GUI happened to start in.
Each run writes `<out>/gui_run.toml` (the settings, with absolute paths) and `<out>/gui_events.jsonl`;
the same run can be repeated from a terminal with `kicad2ems <out>/gui_run.toml`.

<a id="plots"></a>
## S-parameter plots

The Results tab lists every trace of the run on the left, grouped by source (openEMS, mom25d, line model,
compare file); tick the ones to show. At first: the first port's column (S11, S21, S31, …, what a run driving P1 computes); traces that were
not computed (ports not driven) are greyed out. *Show* picks the rectangular plot: |S| in dB or linear,
phase, group delay (−dφ/dω) or VSWR.

- **Smith chart (return loss)**, beside it: the reflections S11, S22, … of the ticked traces, with
  resistance circles and reactance arcs; the readout gives Z = Z0 (1 + Γ) / (1 − Γ) for the run's
  reference impedance. Untick it to give the rectangular plot the width.
- **Cursor**: the mouse over either plot shows every shown trace at that frequency below the plots (over
  the Smith chart: the nearest point), with dots on both plots.
- **Markers**: click a plot (the Smith chart: the nearest point) to drop one, drag its line to move it;
  the table lists every trace at every marker (and Z for the reflections); select rows and press Delete,
  or *Clear markers*. Markers keep their frequency when the plot kind changes.
- **Zoom / pan**: mouse wheel and drag (right-drag: zoom one axis), *Fit* to see everything, right-click
  for pyqtgraph's menu (axis ranges, log scale, its own export).
- **Export CSV…**: the shown traces as the plot shows them, on the first trace's frequency grid (others
  interpolated), plus Re / Im of the Smith chart's reflections. **Save image…**: both plots as PNG.

<a id="fields"></a>
## Field dumps

Add dumps in Setup → *Field dumps*: the line takes specs separated by `;`
([spec syntax](postprocessing.md#field-dumps)), and *Add a dump* builds one: field (E, H, J), where (a
copper layer with an optional offset such as `F.Cu-0.1`, an `x =` / `y =` / `z =` plane in mm, or the
volume) and frequencies. **Without frequencies** the dump is time-domain: openEMS writes a frame every
few timesteps and the Fields tab can follow it **live**. **With frequencies** it is frequency-domain,
written at the end of the run and scaled to a 1 V incident wave at the driven port.

The Fields tab lists every dump of the output folder (*Refresh*, or *Open results folder…* for another),
openEMS's and mom25d's current dumps (`--mom-dump`, frequency domain, labelled `mom25d P1: J F.Cu`):

| Control | |
|---|---|
| Component | \|F\| or the x / y / z component (KiCad axes: y down) |
| Cut normal to, slider | a plane dump has its one plane; a volume can be cut at any x, y or z mesh line |
| log scale, auto range per frame | colour scale; without auto range one range is taken over the series (12 frames across a time-domain dump, half a period at a frequency) so playback does not flicker; dragging the colour bar fixes it |
| frame, Play | time-domain frames (step and time), or the dump frequencies; *every n-th frame* thins playback and animations |
| instantaneous, phase | frequency domain: Re(F·e^jφ) at the phase (Play turns it); off: the peak \|F\| |
| Live | while a run goes, show each new time-domain frame as it is written (the range follows the run) |
| Save PNG / GIF / MP4 | the current view, or an animation (time domain: the frames; frequency domain: one period in 24 steps) with one colour range, drawn with matplotlib; MP4 needs `ffmpeg` on PATH (or `pip install imageio-ffmpeg`) |

Copper is outlined on z planes within 0.3 mm of a copper layer; on x / y cuts the copper crossing the cut
is drawn at its layer height. The cursor readout gives the position and value under the mouse. The same
reading, cutting and animation code is usable without the GUI (`kicad2ems.fields`, see
[post-processing](postprocessing.md#field-dumps)).

<a id="fields3d"></a>
## 3D fields

A volume dump (`--dump E@volume+5@2.4`: the board with 5 mm of air above and below; `volume` alone has 0.5 mm)
opens the *3D fields* panel: the field as a translucent voxel cloud over the board (copper translucent and
outlined, vias as lines, the board's box), in an OpenGL view you orbit (drag), zoom (wheel) and pan (Ctrl + drag).
It needs PyOpenGL (`pip install PyOpenGL`, in the `gui` extra).

| Control | |
|---|---|
| Dump, Frame | the volume dumps of the folder, their frequencies (time domain: frames) |
| Show, Colours | \|F\| or a component (signed at an instant: blue - white - red); the colour map |
| envelope | frequency domain: the peak \|F\| over a period, no animation; off: Re(F·e^jφ), *Play* turns the phase |
| log scale, Decades, Range top | colour range: the top at a percentile of the values (99.5 %: a port's gap would otherwise take it all), log over the decades below |
| Opacity, Fade | how opaque the strongest field is, and how fast the weak field fades out (alpha ∝ value^fade) |
| Height scale | z stretched (a board is thin) |
| Resolution | the dump resampled onto a uniform grid of this many voxels along its longest side (OpenGL volume textures are uniform; openEMS's mesh is graded) |
| copper, board box and vias | the board drawn under the field; untick copper to see between stacked planes |
| Save GIF / MP4 | the animation from the current camera, at the view's size |

![patch 3D](img/readme/patch_3d.gif)

<a id="antenna"></a>
## Antenna far field

With *Mode* = antenna, the Antenna tab opens after the run (or with *Open results folder…*). It reads
`<name>_antenna.json` and the full-sphere NF2FF result `openems/exc_<port>/nf2ff_sphere.h5` (5° grid,
every recorded frequency: 21 over the band plus the *Pattern frequencies*):

- the input match and the resonance, impedance and −10 dB band;
- directivity, gain, realized gain (dBi) and radiation efficiency (right axis) over frequency, with the
  pattern frequency marked;
- at the chosen frequency and quantity (gain, realized gain or directivity): the xz (φ = 0), yz (φ = 90°)
  and xy (θ = 90°) polar cuts and a 3D pattern (30 dB range), in model axes (x right, y up in the KiCad
  view, z out of the top side); *Save pattern PNG…*.

Not yet: the interactive mesh view, phase / Smith charts and overlays, run history.

---
[← Maintenance notes](maintenance.md) · [Index](index.md)
