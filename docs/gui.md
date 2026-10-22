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
  the report, *Filter* (the wand: the [Filter Synthesis](#filter) page), and *Results of*: whose results the pages show, this board's preview or one of its runs
  ([below](#run-folders)). The estimate (after a preview) is the time and memory of the full-wave solvers ticked, or the one
  *auto* would pick, for example `~4 min · 1.3 GB` over `mom25d (auto)`. It is grey once other settings change
  (preview again), red when it won't fit in the memory available, and opens the Solvers window.
- **Status bar**: the system's memory in use, the GPU's (through `nvidia-smi` or `rocm-smi`; without them, as
  the Solvers window found it), and while a run goes the run's own memory (kicad2ems, its workers, openEMS).
  A bar turns amber above 80 % and red above 95 %.
- **Settings** (left): collapsible sections: *Board*, *Frequency*, *Solvers* (the tick boxes, the mode and
  the driven ports), then one per solver, *openEMS + gfdtd (FDTD)* (with *Mesh*, *Run* and *Field dumps*
  inside: gfdtd runs openEMS's model, so these settings are both solvers'; *Mesh* has the air margin, one value
  or six, one per side), *gfdtd* (where it runs) and *mom25d*, and *Output* (the folder, the dump settings both solvers use, the antenna's pattern
  frequencies). A solver's section is greyed while that solver isn't ticked (*auto* may run either), and
  the antenna settings outside antenna mode. Each header shows how many settings in it are set, for
  example `Band (3 set)`. Empty fields use the default, shown as a hint. In a narrow dock a field moves
  under its label.
- **Pages** (right): **Viewer**, **Run**, **Results** and **Filter Synthesis** ([below](#filter)), tabbed. Drag a tab out to see two side by side
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
estimates against what is available. Its *Programs* box sets where qucsator is, and the **openEMS
environment**: a venv with openEMS, when it isn't in kicad2ems's own Python (its folder or its
`bin/python`; it must be the same Python version, since openEMS's compiled modules load only in their own).
These are settings of the machine rather than of a board: they go into `~/.config/kicad2ems/site.toml`.
Empty finds qucsator on the PATH or where `setup.sh` recorded it, and openEMS in kicad2ems's own Python.

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
- **Mesh:** a solver's discretisation: the FDTD grid lines (openEMS's, or gfdtd's when openEMS isn't ticked:
  the same grid) or mom25d's triangles. A preview writes them in seconds.
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
| one colour range for every frame | the range over the whole dump (every time step, or every frequency), so a pulse is seen passing and dying away; off, each time step is scaled on its own |
| log scale, Decades, Range top | the colour range; *Range top* ignores the strongest few voxels, such as a port gap |
| Opacity, Fade | how solid the strongest field looks, and how fast the weak field fades out |
| Height scale | stretches z, since boards are thin |
| Resolution | voxels along the longest side |
| copper, Copper shown | show the copper, and how thick it is drawn (0 is flat) |
| line model, Z0 relief | the sections, and how tall the highest Z0 stands (0 is flat) |
| parts at true size | the lumped parts as chips |
| Mesh | none, or a solver's mesh |
| Far field: Pattern, Size, Height | the pattern at a frequency, its size against the board, and how far above the board's surface it is drawn |
| fps, Every n-th frame | the speed of Play and of saved animations, and for a time-domain dump which time steps they use (a long dump starts at about 120 frames), as in the Cut view |
| Save GIF / MP4 | the animation from the current camera: a period of a frequency-domain dump, or the time steps of a time-domain one |

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
computed (ports that weren't driven) are greyed out. Each trace has its own colour and each source
(a solver, the compare file, an added folder) its own line style, shown beside the trace's name.
Right-click a trace to change its colour, line style or width, or a source to set the style of all its
traces; *Automatic colour and style* undoes it. Your choices are remembered from one session to the next.

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

<a id="beam-steering"></a>
**Beam steering.** For an antenna with several ports, mom25d keeps each port's far field
([Phased arrays](mom.md#phased-drive)), and a *Beam steering* bar appears above the plots. Any drive is
then a sum of those fields, recombined at once without solving again:

- **Drive**: `PORT[=amplitude][@phase]` per driven port, the run's to start with. Ports not named are
  terminated.
- **Grid**: where each port sits in a 2D array, the run's `grid` to start with. `4x4` places the ports
  in order row by row (P1–P4 the top row); `P1:0,0 P2:1,0 P3:0,1 ...` places each one (fractional places
  for an offset lattice). Empty: the ports form a line.
- **Phase step** (no grid): added from each driven port to the next, on top of the drive's phases. The beam
  tilts across the ports.
- **X step**, **Y step** (with a grid): X added per column, Y per row, so an element at column *c*, row *r*
  gets *c*·X + *r*·Y more phase. The beam tilts in both planes. A **phase map** shows the elements as the
  grid lays them out, each coloured by its phase, with its port, phase and active reflection.
- **Taper**: an amplitude taper multiplied into the drive, for lower sidelobes at the cost of a wider beam
  and some gain. **Taylor** keeps the sidelobes next to the main beam near the level set; **Chebyshev**
  holds all of them at it; **cosine** has no level to set. With a grid it is a column weight times a row
  weight; without one, it runs across the driven ports. With a taper, the phase map adds each element's
  amplitude (×, relative to the largest; paler for weaker ones).
- **Animate** sweeps the phase step back and forth over ±180°; with a grid it sweeps **X**, **Y**, or
  both round a **Circle** (a conical scan at the steps' current size, 90° when they are zero). **Reset**
  goes back to the run's drive, grid, taper and steps.

The line below gives the beam's direction, its gain and realized gain, and each driven port's active
reflection (for more than four ports, their range and the worst). The gain and efficiency plots, the far-field cuts and the Viewer's 3D pattern follow the
steering. The Viewer's 3D view has the same controls in its *Far field* section; the two stay in step, so
the beam can be steered or animated from either.

<a id="filter"></a>
## The Filter Synthesis page

Designs a microstrip filter with [dfsynth](../tools/dfsynth/README.md) and writes its board and kicad2ems
config (*Filter* in the toolbar, the wand, or *File > New filter*, brings the page to the front). dfsynth is
installed with kicad2ems (it lives in `tools/dfsynth`); without it the page says so and stays disabled.

- **Settings** (left): *Filter* (type, topology, f0, bandwidth, response, order or an attenuation spec such
  as `40@6G`, ripple, Z0), *Topology details* (only those the topology uses are enabled), *Substrate and
  fabrication* (a preset, its height, εr and tan δ overridden when given, the copper, the minimum trace and
  space: 0.1 mm each by default; a design below them is refused unless *allow below the limits* is
  ticked), *Output* (the base path of the files) and *Validation* (the kicad2ems solvers, the tuning rounds,
  extra kicad2ems arguments such as `--fine 0.03`).
- **Design** synthesises the filter, calibrates it on dfsynth's line model and writes the board.
  **Design + validate** then solves the board with the solvers chosen and tunes it for the rounds asked
  (centre and bandwidth; an interdigital filter by space mapping). gfdtd suits interdigital filters, mom25d
  the other planar ones. dfsynth runs as a separate process, its output in the *Log*; **Stop** ends it.
  While it runs a busy bar shows under the buttons, and where it is up to in the status bar; a validation
  follows the kicad2ems run into the *Log* as it goes (its messages, and every 10 s the solver's progress:
  an FDTD run's timestep and the field energy left, a frequency sweep's frequencies done).
- **Log**: dfsynth's output. Its warnings (⚠: the design's notes, a tuning stopped early) and errors (✖: a
  design refused for the fabrication limits or the spec, a solver failing) are in bold red, and the tab
  counts them (*Log (1 error, 3 warnings)*, in red); an error brings the log to the front. The design's
  notes are in red on the *Summary* too.
- **Layout**: the copper, the ground vias and the ports as drawn on the board. **Response**: S21 (solid) and
  S11 (dashed) of the line model and of each solver of the last validation round, with the target band
  edges dotted. **Dimensions**: every element's impedance, width, length and gap. **Summary**: the spec,
  the line model's figures and each validation round's (centre and bandwidth error, loss, return loss).
- **Open the board in kicad2ems** loads the config dfsynth wrote into the settings on the left, which
  previews it: from there any solver, mesh or field dump runs as for any other board.

---
[← Usage](usage.md) · [Index](index.md) · [Next: Examples →](examples.md)
