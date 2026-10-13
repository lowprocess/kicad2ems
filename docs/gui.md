# GUI

[← Usage](usage.md) · [Index](index.md) · [Next: Examples →](examples.md)

```bash
pip install '.[gui]'          # PySide6, pyqtgraph, h5py, PyOpenGL
kicad2ems-gui [config.toml]   # or: python -m kicad2ems.gui
```

The GUI edits a run's settings and starts the command-line tool as a separate process. Closing the
window never loses a run, and the GUI itself doesn't need openEMS.

## The window

- **Toolbar**: New, Open, Save, Save as, Preview, Run, Stop, the run's estimate, open a results folder, open
  the report, and *Results of*: whose results the pages show, this board's preview or one of its runs
  ([below](#run-folders)). The estimate (after a preview) is the time and memory of the full-wave solvers ticked, or the one
  *auto* would pick, for example `~4 min · 1.3 GB` over `mom25d (auto)`. It is grey once other settings change
  (preview again), red when it won't fit in the memory available, and opens the Solvers window.
- **Status bar**: the system's memory in use, the GPU's (through `nvidia-smi` or `rocm-smi`; without them, as
  the Solvers window found it), and while a run goes the run's own memory (kicad2ems, its workers, openEMS).
  A bar turns amber above 80 % and red above 95 %.
- **Settings** (left): collapsible sections: *Board*, *Frequency*, *Solvers* (the tick boxes, the mode and
  the driven ports), then one per solver, *openEMS* (with *Mesh*, *Run* and *Field dumps* inside) and
  *mom25d*, and *Output* (the folder, the dump settings both solvers use, the antenna's pattern
  frequencies). A solver's section is greyed while that solver isn't ticked (*auto* may run either), and
  the antenna settings outside antenna mode. Each header shows how many settings in it are set, for
  example `Band (3 set)`. Empty fields use the default, shown as a hint. In a narrow dock a field moves
  under its label.
- **Pages** (right): **Viewer**, **Run** and **Results**, tabbed. Drag a tab out to see two side by side
  (the Run page next to the Viewer's live field, say), or float it. A page can be closed and shown again
  from the *View* menu (back in the window if it had floated). *View > Reset layout* puts them all back.

The pages come to the front when they have something new: the Run page when a run starts, the Viewer when a
preview ends, the Results page when a run ends.

<a id="run-folders"></a>
**Where results go.** Each run from the GUI gets a folder of its own under the output folder, named after the
board and when it started: `<out>/<board>_20261013_104514/`. A preview goes to `<out>/<board>_preview/`, which
each preview refreshes. A folder so only ever holds one board's results. Opening a config shows its newest
run on the Results page and starts a preview, so the Viewer shows the board as it is now; *Results of* picks
an earlier run instead.

The window remembers its size and position, where the docks and pages are, the splitters, and the plots of
the Results page, from one session to the next.

<a id="solvers"></a>
## Solvers

**Solvers** in the settings has one tick box per solver, plus *auto*. Plugin solvers appear too, with
their own settings. After a preview, each full-wave solver's box shows its estimated time and memory on
that board, for example `mom25d (MoM) · ~4 min, 1.3 GB`. A solver that can't run here is grey, and its
tooltip says why.

**?** beside *Solvers* (or *Help > Solvers and hardware*) opens the Solvers window: every solver with ✔
or ✖ and what it found, the CPU, the memory and the GPU mom25d would use, and the last preview's
estimates against what is available. Its *Programs* box sets where qucsator is, a setting of the machine
rather than of a board: it goes into `~/.config/kicad2ems/site.toml`, and empty finds it on the PATH or
where `setup.sh` recorded it.

Experimental solvers (EMerge) appear only when the GUI is started with `kicad2ems-gui --experimental`.

**Stop** asks the run to stop: openEMS ends at once and keeps what it has computed, mom25d ends after the
frequency it is solving (with no result), and solvers that haven't started are skipped. Press Stop again to
end the process at once.

<a id="configs"></a>
## Configs

The *File* menu has New, Open, recent files, the examples, Save and Save as. The title shows `*` while
there are unsaved changes.

Saving over an existing config **keeps the file as it was**: comments, key order, tables and keys the
GUI doesn't know all stay. Only the changed values are rewritten. Paths are saved relative to the
config. Settings that have no field in the form are kept and passed on to runs.

Each run writes `gui_run.toml` into its folder, so you can repeat it from a terminal with
`kicad2ems <folder>/gui_run.toml`. Runs start in their folder.

<a id="viewer"></a>
## The Viewer

Everything about the board and its fields, in four views. A view the folder has nothing for is greyed out.

| View | Shows |
|---|---|
| 3D | the board with its copper as shaded slabs, a solver's mesh, the line model, dumps in place, the far field |
| Plan | the board from above: one layer's copper (the others faint), a solver's mesh on it, the ports, the line model |
| Cut | a cut through a field dump, with its values ([below](#fields)) |
| Images | the plots a run wrote: openEMS's mesh with its side views, the model, the line model |

<a id="line-model"></a>
### The line model over the board

With *Line model* ticked, the 3D and Plan views draw the [line model](transmission-lines.md)'s sections on
their copper at their width, coloured by Z0: grey at the ports' impedance, blue below it, red above. The colour
bar beside the view gives the scale. In 3D each section also stands as tall as its Z0 is high (*Z0 relief*: the
highest Z0 that tall, the lowest a quarter of it), so every change of impedance, a step, a tee, a pad, shows as
a step in height.

The list beside the view has every section by path (`P1 → P2`, …) with its Z0, length, delay and εeff.
Pick a section in the list, or click it in the plan, and both views mark it. Hovering over a section in
the plan shows its values. A preview writes the sections as `<name>_lines.json`.

<a id="fields3d"></a>
### 3D

Drag to orbit, scroll to zoom, Ctrl+drag to pan. This needs PyOpenGL, which is in the `gui` extra. The
controls are in folding sections beside the view, and only the ones the folder has something for are
shown: *Field* with a dump, *Far field* with antenna results.

- **Parts:** the lumped R / C / L parts as their chips at true size, from the footprint's name (0201, 0402,
  0603, 0805, …; without a size in the name, the pads' extent): resistors green, capacitors cyan, inductors
  pink, a 0 Ω link grey, with metal end caps and a label with the reference and value. A part on the bottom
  layer hangs below the board. The plan view outlines them in the same colours.
- **Mesh:** a solver's discretisation: openEMS's grid lines, mom25d's triangles or EMerge's element faces on
  the copper, ports and parts. A preview writes all three in seconds.
- **a dump:** a volume dump, for example `--dump E@volume+5@2.4` (the board plus 5 mm of air above and
  below), as a translucent cloud; a plane dump (an openEMS cut, mom25d's surface currents) as a coloured sheet
  in place, just above the copper when it lies on a copper layer.
- **Far field:** in antenna mode, the gain pattern at a recorded frequency over the board's centre: its
  distance from the centre is the gain, over 30 dB. *Size* scales it against the board, and *Height* lifts
  it off the board's surface.

| Control | |
|---|---|
| Show, Colours | \|F\| or one component; the colour map |
| envelope | the peak over a cycle instead of an animation |
| log scale, Decades, Range top | the colour range; *Range top* ignores the strongest few voxels, such as a port gap |
| Opacity, Fade | how solid the strongest field looks, and how fast the weak field fades out |
| Height scale | stretches z, since boards are thin |
| Resolution | voxels along the longest side |
| copper, Copper shown | show the copper, and how thick it is drawn (0 is flat) |
| line model, Z0 relief | the sections, and how tall the highest Z0 stands (0 is flat) |
| parts at true size | the lumped parts as chips |
| Mesh | none, or a solver's mesh |
| Far field: Pattern, Size, Height | the pattern at a frequency, its size against the board, and how far above the board's surface it is drawn |
| Save GIF / MP4 | the animation from the current camera |

![patch 3D](img/readme/patch_3d.gif)

<a id="fields"></a>
### Cut: field dumps in 2D

Add dumps under *Field dumps* in the settings; *Add a dump* builds the spec for you
([syntax](postprocessing.md#field-dumps)). A dump without frequencies is recorded over time, and the
Cut view can follow it live while openEMS runs. A dump with frequencies is written at the end.

| Control | |
|---|---|
| Component | \|F\| or the x, y or z component |
| Cut normal to | for a volume, the plane to show |
| log scale, auto range | the colour scale |
| Frame (a slider), Play | step through time frames or frequencies |
| instantaneous, phase | for a frequency dump: the field at a phase of the cycle; *Play* turns it |
| Live | follow a running simulation |
| Save PNG / GIF / MP4 | the view or an animation (MP4 needs ffmpeg) |

Copper is outlined on the plots.

## The Run page

Progress per driven port, openEMS's energy decay, mom25d's adaptive sweep (its model change and prediction
error per solve, in dB, against its tolerance), the log, and *Stop*.

<a id="plots"></a>
## The Results page

S-parameters and antenna results on one page, as a grid of plots. *Add plot* adds one; ✕ on a plot removes
it. The plots fill as many columns as the page's width allows.

| Plot | Shows |
|---|---|
| Cartesian | over frequency: \|S\| in dB or linear, phase, group delay or VSWR; with antenna results also directivity, gain and realized gain, or the radiation efficiency |
| Smith | the reflections (S11, S22, …) with an impedance readout |
| Polar | S in polar form, or with antenna results the far-field cuts (xz, yz, xy) at a recorded frequency, as gain, realized gain or directivity |

The traces are listed on the left, grouped by solver; tick the ones to show. Traces that weren't
computed (ports that weren't driven) are greyed out.

- **Cursor**: hover over any plot to read every trace at that frequency.
- **Markers**: click to add, drag to move, Delete to remove. A marker is on every plot. The table lists each
  trace at each marker, and the gain and efficiency when a plot shows them.
- **Zoom**: mouse wheel and drag; *Fit* shows everything.
- **Add results folder…**: overlay another run, for comparison.
- **Export CSV…** and **Save image…**.

The page starts with |S| and a Smith chart, or for an antenna with |S|, the gain, the far-field cuts and a
Smith chart. Your plots are remembered, one set for S-parameters and one for antennas. A plot of the gain
stays one while a folder without antenna results is open, and shows the gain again when one with antenna
results is.

<a id="antenna"></a>
In antenna mode the line above the plots gives the resonance, the input impedance and the −10 dB band. When
both openEMS and mom25d ran, a selector beside it switches between them. The pattern in 3D is in the Viewer.

---
[← Usage](usage.md) · [Index](index.md) · [Next: Examples →](examples.md)
