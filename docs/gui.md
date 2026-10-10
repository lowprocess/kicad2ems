# 13. GUI (first version)

[← Maintenance notes](maintenance.md) · [Index](index.md)

```bash
pip install '.[gui]'                 # PySide6 + pyqtgraph
kicad2openems-gui [config.toml]      # or: python -m kicad2openems.gui
```

The GUI edits a run's settings, starts the command-line tool as a separate process with `--events`, and
follows its event stream (`gui/runner.py`). Closing the window never loses a run's results, and the GUI
itself needs no openEMS.

| Tab | What it does |
|---|---|
| Setup | the main settings by group (board, band, mesh, solver, output); load / save a TOML config; *Preview* runs `--preview` and shows the mesh preview, the line-model plot and the fast S-parameters; *Run* starts the full run |
| Run | stage, one progress bar per driven port (step, dB, ETA), the decay curves, the log; *Stop* writes `ABORT` into every run folder (openEMS stops and finishes normally, so the results up to that point are written) |
| Results | the S-parameters (openEMS solid, line model dotted); *Open report*, *Open folder* |

Each run writes `<out>/gui_run.toml` (the settings, with absolute paths) and `<out>/gui_events.jsonl`;
the same run can be repeated from a terminal with `kicad2openems <out>/gui_run.toml`.

Not yet: the interactive mesh view, the live field plane, phase / Smith charts and overlays, run history.

---
[← Maintenance notes](maintenance.md) · [Index](index.md)
