# 13. GUI

[← Maintenance notes](maintenance.md) · [Index](index.md)

```bash
pip install '.[gui]'                 # PySide6 + pyqtgraph + h5py
kicad2openems-gui [config.toml]      # or: python -m kicad2openems.gui
```

The GUI edits a run's settings, starts the command-line tool as a separate process with `--events`, and
follows its event stream (`gui/runner.py`). Closing the window never loses a run's results, and the GUI
itself needs no openEMS.

| Tab | What it does |
|---|---|
| Setup | the main settings by group (board, band, mesh, solver incl. *Mode* and pattern frequencies, field dumps, output; empty fields show their default); open / save the TOML config ([below](#configs)); *Preview* runs `--preview` and shows the mesh preview, the line-model plot and the fast S-parameters; *Run* starts the full run |
| Run | stage, one progress bar per driven port (step, dB, ETA), the decay curves, the log; *Stop* writes `ABORT` into every run folder (openEMS stops and finishes normally, so the results up to that point are written) |
| Results | interactive S-parameter plots: openEMS solid, mom25d dash-dot, line model dotted, the `compare` file dashed; a Smith chart of the return loss beside them, a cursor readout and markers ([below](#plots)); *Open results folder…* (an earlier run), *Open report*, *Open folder* |
| Fields | the field dumps: live view, x / y / z cuts, playback over time or phase, PNG / GIF / MP4 ([below](#fields)) |
| Antenna | antenna mode: S11, gain / directivity / realized gain / efficiency over frequency, the far-field pattern at any recorded frequency ([below](#antenna)) |

<a id="configs"></a>
## Configs

*File* → *New* (Ctrl+N), *Open…* (Ctrl+O), *Open recent*, *Examples* (every `examples/*.toml`), *Save*
(Ctrl+S), *Save as…* (Ctrl+Shift+S); the same as buttons under the form. The window title shows the
config and `*` while it has unsaved changes; opening another config or closing the window asks first.
*Run* → *Preview* (Ctrl+P), *Run* (Ctrl+R), *Stop* (Ctrl+.).

Saving over a config that was opened **keeps the file**: its comments, key order, `[openems]` table and
keys the GUI does not know stay; each setting gets its new value in place (a trailing comment stays in
its column), a cleared field removes its line, and settings that were not in the file are added above
the first table. *Save as…* does the same into the new file. Paths are written relative to the config's
folder (absolute when the target is more than one folder up), so a config can move with its board. A
new config gets only the settings that differ from the defaults. A config whose board is missing (the
generated antenna examples) says so in the status bar, with the generator command in the file's header.

A loaded config keeps the settings that have no field in the form (`air`, `bc`, `no_mask`, `diel`, …) and
passes them on to its runs. When the config's output folder already holds results, they are shown.
Each run writes `<out>/gui_run.toml` (the settings, with absolute paths) and `<out>/gui_events.jsonl`;
the same run can be repeated from a terminal with `kicad2openems <out>/gui_run.toml`.

<a id="plots"></a>
## S-parameter plots

The Results tab lists every trace of the run on the left, grouped by source (openEMS, mom25d, line model,
compare file); tick the ones to show. At first: the lower triangle (S11, S21, S22, …); traces that were
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

The Fields tab lists every dump of the output folder (*Refresh*, or *Open results folder…* for another):

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
reading, cutting and animation code is usable without the GUI (`kicad2openems.fields`, see
[post-processing](postprocessing.md#field-dumps)).

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
