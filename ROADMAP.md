# Roadmap

What is worth doing next, in priority order. Each item comes from something the examples or benchmarks
showed. Finished items leave this file; what was done is in CHANGELOG.md, and the decisions not to do
something are under its *Decided* headings. Last updated 2026-10-28.

1. **Give kicad2ems-gui a public surface.** kicad2ems-ipc imports `kicad2ems_gui.planview.PlanView`, a private
   module. A `kicad2ems_gui.api` exporting PlanView alone keeps the GUI's plan view and the plugin's map the same
   and gives the import a stable name.

2. **Make dfsynth process-only from the GUI.** The Filter page runs dfsynth as a process but imports
   `dfsynth.topologies` and `dfsynth.microstrip` for its presets and hard-codes dfsynth's 29 options. A
   `dfsynth --describe --json` listing the topologies, substrates and options (from its argparse) would let the
   page build itself from declarations, as the main settings form does, leaving the command line its only link.

3. **Ship the examples as package data.** The GUI finds `examples/` by walking up from `kicad2ems.__file__`,
   which fails for setup.sh's copy install and the KiCad plugin's. Install them as `kicad2ems.examples` and
   expose them through `api.frontend.examples()`, copying or generating a board into a user folder before it
   runs.
