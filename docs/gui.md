# GUI

[← Usage](usage.md) · [Index](index.md) · [Next: Examples →](examples.md)

```bash
pip install '.[gui]'          # PySide6, pyqtgraph, h5py, PyOpenGL
kicad2ems-gui [config.toml]   # or: python -m kicad2ems.gui
```

The GUI edits a run's settings and starts the command-line tool as a separate process. Closing the
window never loses a run, and the GUI itself doesn't need openEMS.

## The window

- **Toolbar**: New, Open, Save, Save as, Preview, Run, Stop, open a results folder, open the report.
- **Settings** (left): one collapsible section per group. Each header shows how many settings in it are
  set, for example `Band (3 set)`. Empty fields use the default, shown as a hint.
- **Results** (right): panels you can tab, split, float or close. *View* reopens them.

Panels open by themselves when a preview or run produces something for them:

| Panel | Shows |
|---|---|
| S-parameters | the S-parameters of every solver, a Smith chart, a cursor readout and markers ([below](#plots)) |
| Model and mesh | the model and openEMS's mesh |
| Line model | the transmission-line sections |
| Run | progress per driven port; openEMS's energy decay and mom25d's adaptive sweep (its model change and prediction error per solve, in dB, against its tolerance); the log, and *Stop* |
| Fields | the board in 3D (the default, and what a preview shows) with a solver's mesh, plane and volume dumps in place and the far field; or, toggled to 2D, a cut through a dump with its values ([3D](#fields3d), [2D](#fields)) |
| Antenna | antenna results ([below](#antenna)) |
| System | the solvers available here, the GPU, and the memory in use; after a preview, each solver's estimated time and memory for the board |

**Solvers** has one tick box per solver, plus *auto*. Plugin solvers appear too, with their own settings.

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

Each run writes `<out>/gui_run.toml`, so you can repeat it from a terminal with
`kicad2ems <out>/gui_run.toml`.

<a id="plots"></a>
## S-parameter plots

The traces are listed on the left, grouped by solver; tick the ones to show. Traces that weren't
computed (ports that weren't driven) are greyed out. *Show* switches between |S| in dB or linear, phase,
group delay and VSWR.

- **Smith chart**: the reflections (S11, S22, …) with an impedance readout.
- **Cursor**: hover over a plot to read every trace at that frequency.
- **Markers**: click to add, drag to move, Delete to remove. The table lists each trace at each marker.
- **Zoom**: mouse wheel and drag; *Fit* shows everything.
- **Add results folder…**: overlay another run, for comparison.
- **Export CSV…** and **Save image…**.

<a id="fields"></a>
## Field dumps: 2D cuts

The Fields panel's *2D* view. Add dumps under *Field dumps* in the settings; *Add a dump* builds the spec for you
([syntax](postprocessing.md#field-dumps)). A dump without frequencies is recorded over time, and the
Fields panel can follow it live while openEMS runs. A dump with frequencies is written at the end.

| Control | |
|---|---|
| Component | \|F\| or the x, y or z component |
| Cut normal to | for a volume, the plane to show |
| log scale, auto range | the colour scale |
| Frame, Play | step through time frames or frequencies |
| instantaneous, phase | for a frequency dump: the field at a phase of the cycle; *Play* turns it |
| Live | follow a running simulation |
| Save PNG / GIF / MP4 | the view or an animation (MP4 needs ffmpeg) |

Copper is outlined on the plots.

<a id="fields3d"></a>
## The 3D view

The Fields panel's *3D* view, the one it opens with. It shows the board, with the copper as shaded slabs,
after a preview or a run, and over it:

- **Mesh:** a solver's discretisation: openEMS's grid lines, mom25d's triangles or EMerge's element faces on
  the copper, ports and parts. A preview writes all three in seconds.
- **a dump:** a volume dump, for example `--dump E@volume+5@2.4` (the board plus 5 mm of air above and
  below), as a translucent cloud; a plane dump (an openEMS cut, mom25d's surface currents) as a coloured sheet
  in place, just above the copper when it lies on a copper layer.
- **Far field:** in antenna mode, the gain pattern at a recorded frequency over the board's centre: its
  distance from the centre is the gain, over 30 dB. *Far-field size* scales it against the board, and
  *Far-field height* lifts it off the board's surface.

Drag to orbit, scroll to zoom, Ctrl+drag to pan. This needs PyOpenGL, which is in the `gui` extra.

| Control | |
|---|---|
| Show, Colours | \|F\| or one component; the colour map |
| envelope | the peak over a cycle instead of an animation |
| log scale, Decades, Range top | the colour range; *Range top* ignores the strongest few voxels, such as a port gap |
| Opacity, Fade | how solid the strongest field looks, and how fast the weak field fades out |
| Height scale | stretches z, since boards are thin |
| Resolution | voxels along the longest side |
| copper, Copper shown | show the copper, and how thick it is drawn (0 is flat) |
| Mesh | none, or a solver's mesh |
| Far field, Far-field size, height | the pattern at a frequency, its size against the board, and how far above the board's surface it is drawn |
| Save GIF / MP4 | the animation from the current camera |

![patch 3D](img/readme/patch_3d.gif)

<a id="antenna"></a>
## Antenna results

In antenna mode, the Antenna panel shows:

- the input match, with the resonance, impedance and −10 dB band;
- directivity, gain, realized gain and efficiency over frequency;
- polar cuts and a 3D pattern at any recorded frequency.

When both openEMS and mom25d ran, a selector switches between them.

---
[← Usage](usage.md) · [Index](index.md) · [Next: Examples →](examples.md)
