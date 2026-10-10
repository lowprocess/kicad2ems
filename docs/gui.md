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
| Setup | the main settings by group (board, band, mesh, solver incl. *Mode* and pattern frequencies, field dumps, output); load / save a TOML config; *Preview* runs `--preview` and shows the mesh preview, the line-model plot and the fast S-parameters; *Run* starts the full run |
| Run | stage, one progress bar per driven port (step, dB, ETA), the decay curves, the log; *Stop* writes `ABORT` into every run folder (openEMS stops and finishes normally, so the results up to that point are written) |
| Results | the S-parameters (openEMS solid, line model dotted); *Open results folder…* (an earlier run), *Open report*, *Open folder* |
| Fields | the field dumps: live view, x / y / z cuts, playback over time or phase, PNG / GIF / MP4 ([below](#fields)) |
| Antenna | antenna mode: S11, gain / directivity / realized gain / efficiency over frequency, the far-field pattern at any recorded frequency ([below](#antenna)) |

A loaded config keeps the settings that have no field in the form (`air`, `bc`, `no_mask`, `diel`, …) and
passes them on to its runs. When the config's output folder already holds results, they are shown.
Each run writes `<out>/gui_run.toml` (the settings, with absolute paths) and `<out>/gui_events.jsonl`;
the same run can be repeated from a terminal with `kicad2openems <out>/gui_run.toml`.

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
