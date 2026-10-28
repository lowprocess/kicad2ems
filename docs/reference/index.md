# Code reference

Every module, class and function of the kicad2ems stack, from the installed packages: their docstrings, shown
as written, and their source. What one package may use of another is on [APIs](../api.md); the rest is private
to its package. The compiled cores (C++) are on [their own page](cores.md).

| Package | Repository | What |
|---|---|---|
| `kicad2ems` | kicad2ems | the board, the line model, settings, the run pipeline, results; `kicad2ems.api` |
| `kicad2ems_fdtd` | kicad2ems-fdtd | the FDTD layer, and the `openems` and `gfdtd` solvers on it |
| `mom25d` | mom25d | the 2.5D method of moments, and its `mom25d` and `mixed` solvers (`mom25d.kicad2ems`) |
| `gfdtd` | gfdtd | the FDTD engine (GPU, compiled CPU core, numpy) behind openEMS's interface |
| `kicad2ems_gui` | kicad2ems-gui | the window; `kicad2ems_gui.api` |
| `kicad2ems_ipc` | kicad2ems-ipc | the KiCad plugin |
| `dfsynth` | dfsynth | filter synthesis to a KiCad layout |
| `emsdev` | emsdev | the machine: GPUs, CPU threads, memory, compiled-core builds |

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :recursive:

   kicad2ems
   kicad2ems_fdtd
   mom25d
   gfdtd
   kicad2ems_gui
   kicad2ems_ipc
   dfsynth
   emsdev
```

```{toctree}
:hidden:

api-names
cores
```
