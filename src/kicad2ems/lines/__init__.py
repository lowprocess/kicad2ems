"""The transmission-line model: the board's lines as sections with their impedances and delays (tlines),
copper shapes as strips (strips), coupled lines (coupled). The pipeline builds it for every run; the KiCad
plugin's impedance map, the report and the GUI's overlay read it, and the line solvers (solvers/lines) solve it."""
