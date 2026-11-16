# Code reference

Every module, class and function of kicad2ems, from the installed package: their docstrings, shown as written, and
their source. What one part may use of another is on [APIs](../api.md); the rest is private to its part. The
compiled cores (C++) are on [their own page](cores.md).

| Part | What |
|---|---|
| `kicad2ems.core` | the board, the line model, settings, the run pipeline, results; `kicad2ems.core.api` |
| `kicad2ems.fdtd` | the FDTD layer, and the `openems` and `gfdtd` solvers on it |
| `kicad2ems.mom25d` | the 2.5D method of moments, and its `mom25d` and `mixed` solvers (`kicad2ems.mom25d.kicad2ems`) |
| `kicad2ems.pcbqs` | DC/PDN, quasistatic fields, ngspice circuits and temperature, and its analyses |
| `kicad2ems.gfdtd` | the FDTD engine (GPU, compiled CPU core, numpy) behind openEMS's interface |
| `kicad2ems.fem` | Palace and Elmer behind kicad2ems, and their analyses |
| `kicad2ems.gui` | the window; `kicad2ems.gui.api` |
| `kicad2ems.ipc` | the KiCad plugin |
| `kicad2ems.dfsynth` | filter synthesis to a KiCad layout |
| `kicad2ems.emsdev` | the machine: GPUs, CPU threads, memory, compiled-core builds |

```{eval-rst}
.. autosummary::
   :toctree: _generated
   :recursive:

   kicad2ems.core
   kicad2ems.fdtd
   kicad2ems.mom25d
   kicad2ems.pcbqs
   kicad2ems.gfdtd
   kicad2ems.fem
   kicad2ems.gui
   kicad2ems.ipc
   kicad2ems.dfsynth
   kicad2ems.emsdev
```

```{toctree}
:hidden:

api-names
cores
```
