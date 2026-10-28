# Roadmap

What is worth doing next, in priority order. Each item comes from something the examples or benchmarks
showed. Finished items leave this file; what was done is in CHANGELOG.md, and the decisions not to do
something are under its *Decided* headings. Last updated 2026-10-28.

1. **Regenerate the examples.md comparison table** with `tools/compare_solvers.py` (every board on every solver:
   hours). The references are up to date (2026-10-28): the step low-pass from a converged mesh, the Wilkinson from
   its default mesh (its converged figures are in its config); the rat-race and grounded CPW configs now set the mesh
   their references were made with (not rerun: the table run checks them).
