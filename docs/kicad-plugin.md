# KiCad plugin

[← Index](index.md)

kicad2ems's plugin for KiCad's PCB editor lives in its own repository,
[kicad2ems-ipc](https://github.com/lowprocess/kicad2ems-ipc). It talks to KiCad through KiCad's IPC API (KiCad 9
and later) and reads the board as it is open in KiCad, unsaved edits included. A toolbar button opens a window
with the impedance map of the board (every trace coloured by how far its [line model](transmission-lines.md) Z0
is from its net class's target), the nets, region and ports to simulate, and a kicad2ems run on them, or the same
set-up opened in [kicad2ems's own window](gui.md).

It installs from KiCad's Plugin and Content Manager, or from its repository. KiCad then builds the plugin its own
Python environment with kicad2ems in it; its README says how, and how to use a kicad2ems you have installed
already.

## What the plugin uses from kicad2ems

The plugin uses kicad2ems as a library. A change to these is a change to the plugin's interface: keep them
stable, or change kicad2ems-ipc with them.

| Module | What |
|---|---|
| `importers.kicad.KiCadBoard`, `board.Board` | the board from the text KiCad's API gives |
| `lines.tlines` (`build`, `RefCopper`) | the impedance map |
| `region.extra_port_fps`, `parts._kind_value` | the region's ports and parts |
| `overlay` | the line model's drawing |
| `touchstone.read_touchstone` | the results |
| `gui.planview.PlanView`, `gui.runner` (`Run`, `EventTail`, `stop_runs`, `toml_value`) | the plan view, runs and their settings |
