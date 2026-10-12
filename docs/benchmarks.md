# Benchmarks

[Index](index.md)

Run time and peak memory of each solver on each example, measured with `tools/bench_solvers.py`. Each
solver ran alone, in a process of its own, on one machine: 24 CPU threads, 31 GB RAM, an AMD Radeon RX 7800
XT (16 GB) through CuPy and ROCm 7. Times are wall time for the whole run: model, mesh, port
characterisation and the solve. RAM is the process's peak. GPU memory is what mom25d planned for its matrix.
The mom25d rows of the via transition, the filter, the differential via and the radial stub were measured
again after its wave ports took the characterised propagation constant everywhere: the filter's sweep went
from 60 solves to 21, the transition's from 42 to 21, the radial stub's from 43 to 18.

| Example | openEMS cells | openEMS | RAM | mom25d unknowns | mom25d GPU | RAM | GPU mem | mom25d CPU | RAM | GPU speed-up | Line model |
|---|---|---|---|---|---|---|---|---|---|---|---|
| antenna_array2x2 | 2.39 M | 253 s | 0.5 GB | 7,328 | 49 s | 1.9 GB | 0.8 GB | 96 s | 1.3 GB | 2.0× | n/a |
| antenna_dipole | 0.26 M | 16 s | 0.2 GB |  | n/a |  |  | n/a |  |  | n/a |
| antenna_ifa | 0.46 M | 26 s | 0.2 GB | 1,990 | 24 s | 1.1 GB | – | 24 s | 1.1 GB | 1.0× | n/a |
| antenna_patch | 0.42 M | 30 s | 0.2 GB | 1,391 | 20 s | 0.7 GB | – | 20 s | 0.7 GB | 1.0× | n/a |
| antenna_slot | 0.34 M | 17 s | 0.2 GB | 8,096 | 30 s | 2.2 GB | 0.9 GB | 69 s | 1.4 GB | 2.3× | n/a |
| demo_bandpass | 0.23 M | 81 s | 0.2 GB | 2,333 | 38 s | 1.5 GB | – | 38 s | 1.5 GB | 1.0× | 1 s |
| demo_highpass | 0.19 M | 69 s | 0.2 GB | 1,875 | 19 s | 1.2 GB | – | 19 s | 1.2 GB | 1.0× | 1 s |
| demo_lowpass | 0.19 M | 69 s | 0.2 GB | 1,875 | 19 s | 1.2 GB | – | 19 s | 1.2 GB | 1.0× | 1 s |
| demo_transition | 0.53 M | 55 s | 0.2 GB | 13,670 | 188 s | 5.7 GB | 1.9 GB | 436 s | 4.1 GB | 2.3× | 1 s |
| filter | 0.40 M | 119 s | 0.2 GB | 4,641 | 18 s | 1.1 GB | 0.6 GB | 22 s | 0.6 GB | 1.2× | 1 s |
| hairpin | 0.31 M | 50 s | 0.2 GB | 4,763 | 9 s | 1.0 GB | 0.6 GB | 10 s | 0.6 GB | 1.2× | 1 s |
| rf_branchline | 0.12 M | 6 s | 0.2 GB | 9,011 | 34 s | 2.5 GB | 1.0 GB | 46 s | 1.6 GB | 1.4× | 1 s |
| rf_coupler | 1.21 M | 92 s | 0.3 GB | 5,864 | 16 s | 1.3 GB | 0.7 GB | 20 s | 0.8 GB | 1.2× | 1 s |
| rf_diffvia | 0.36 M | 126 s | 0.2 GB | 7,688 | 109 s | 2.3 GB | 0.9 GB | 168 s | 1.7 GB | 1.5× | 1 s |
| rf_gcpw | 0.23 M | 54 s | 0.2 GB | 3,526 | 30 s | 2.6 GB | 0.5 GB | 32 s | 2.6 GB | 1.1× | 1 s |
| rf_radialstub | 0.14 M | 35 s | 0.2 GB | 6,888 | 18 s | 1.7 GB | 0.8 GB | 31 s | 1.0 GB | 1.7× | 1 s |
| rf_ratrace | 0.24 M | 11 s | 0.2 GB | 8,707 | 34 s | 2.4 GB | 1.0 GB | 55 s | 1.5 GB | 1.6× | 2 s |
| rf_steplpf | 0.17 M | 35 s | 0.2 GB | 6,129 | 16 s | 1.4 GB | 0.7 GB | 25 s | 0.9 GB | 1.6× | 1 s |
| rf_stripline | 0.28 M | 18 s | 0.2 GB | 15,563 | 185 s | 6.9 GB | 2.3 GB | 418 s | 5.1 GB | 2.3× | 1 s |
| rf_stubnotch | 0.06 M | 35 s | 0.2 GB | 5,090 | 11 s | 1.1 GB | 0.6 GB | 17 s | 0.7 GB | 1.5× | 1 s |
| rf_wilkinson | 0.25 M | 26 s | 0.2 GB | 4,657 | 8 s | 1.0 GB | 0.6 GB | 9 s | 0.6 GB | 1.1× | 1 s |
| sma_launch | 1.09 M | 33 s | 0.3 GB | 16,137 | 171 s | 7.6 GB | 2.5 GB | 417 s | 5.8 GB | 2.4× | 1 s |

*n/a*: the solver can't model that board. mom25d can't model the dipole's gap feed, and the line model has no
antenna mode. mom25d's result for the inverted-F is computed over an infinite ground, which that antenna
doesn't have, so its time is shown but its answer shouldn't be trusted.

## Reading the table

- **openEMS** is fast and needs little memory: 0.2–0.5 GB even for 2.4 M cells. Its time follows cells ×
  timesteps, so high-Q circuits (the lumped filters, about 200k timesteps) and fine meshes (the coupler,
  1.2 M cells) are its slow cases.
- **mom25d** wins on single-layer microstrip (the filters, couplers, the Wilkinson, the patch): 2 to 6 times
  faster than openEMS. It loses once ground planes have to be meshed (the via transition, the stripline,
  the SMA launch: 14k unknowns or more), and on structures where openEMS has very few cells (the
  branch-line, the rat-race).
- **The GPU** helps mom25d in proportion to its matrix size: no gain below about 3,500 unknowns (a small
  dense solve), 1.1–1.6× around 5,000–9,000, and 2.3–2.4× at 15,000–16,000. It costs RAM: the host keeps the
  double-precision matrix for the refinement next to the single-precision copy the GPU factors. Up to about
  2.5 GB of GPU memory was used here.
- **The line model** takes about a second on every board, in under 0.1 GB.

To run it again: `python tools/bench_solvers.py --openems-python <python with openEMS>`. Run nothing else
meanwhile, since the times are wall time.
