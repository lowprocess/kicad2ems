# Whole-board SI studies: the reproduction kit

Scripts behind [docs/segment-studies.md](../../docs/segment-studies.md): the boards, the stackups the KiCad 4
boards lack, the net selections, and the checks each result rests on.

| Script | What it does |
|---|---|
| `fetch_boards.sh [DIR]` | the Thor carrier, LimeRFE 1v31, HackRF One, Opera Cake, LibreVNA and ThunderScope Rev 5.3 at the commits used; adds the KiCad 4 boards' stackups |
| `add_stackup.py PRESET IN OUT` | a documented stackup into a KiCad 4 board (`limerfe`, `operacake`) |
| `select_nets.py pairs\|rf-wide BOARD`, `select_nets.py netclass BOARD CLASS,...` | the net lists the runs used (`--nets "$(...)"`); netclass: the project's own impedance classes (LibreVNA's `RF`, ThunderScope's `LVDS_ADC`, `PCIe`, `FE_50Z`, ...) |
| `compare_results.py A/segment.json B/segment.json` | two segment results channel by channel (windows against whole groups) |
| `coupling_vs_gap.py BOARD PIECE_DIR LAYER` | a piece's coupling against the gap between its nets (a floor is a model's mode) |
| `gcpw_pour_study.py OUT` | the coplanar-ground pour kept whole, in bands, with the signal zone |
| `afs_delay_replay.py RESULT.npz FMIN FMAX [PORTS]` | the sweep replayed with each entry's delay taken out (set aside) |
| `current_maps.py GHZ [--channels ...] -- SEGMENT ARGS` | a finished run's surface-current maps, 2-D a layer, at one frequency, with no sweep solved again: the network rebuilt from the run's folder, each channel driven at its first port, each layer piece solved once at that frequency and weighted by its ports' voltages |
