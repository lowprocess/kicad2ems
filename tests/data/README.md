# Reference results shared by the parts' tests

- `demo_lowpass_openems.s2p`: `kicad2ems examples/demo_lowpass.toml --solvers openems` (2026-11-19, 69 s;
  the board from `examples/make_demo_board.py ... lowpass`). The end-to-end tests of gfdtd
  (`tests/fdtd/test_board_end_to_end.py`), mom25d (`tests/mom25d/test_reference.py`) and the line model
  (`tests/core/test_reference.py`) solve the same board and compare with it.
