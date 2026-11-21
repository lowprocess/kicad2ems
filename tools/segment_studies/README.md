# Whole-board SI studies: the reproduction kit

Scripts behind [docs/segment-studies.md](../../docs/segment-studies.md): the boards, the stackups the KiCad 4
boards lack, the net selections, and the checks each result rests on.

| Script | What it does |
|---|---|
| `fetch_boards.sh [DIR]` | the Thor carrier, LimeRFE 1v31, HackRF One and Opera Cake at the commits used; adds the stackups |
| `add_stackup.py PRESET IN OUT` | a documented stackup into a KiCad 4 board (`limerfe`, `operacake`) |
| `select_nets.py pairs\|rf-wide BOARD` | the net lists the runs used (`--nets "$(...)"`) |
| `compare_results.py A/segment.json B/segment.json` | two segment results channel by channel (windows against whole groups) |
| `coupling_vs_gap.py BOARD PIECE_DIR LAYER` | a piece's coupling against the gap between its nets (a floor is a model's mode) |
| `gcpw_pour_study.py OUT` | the coplanar-ground pour kept whole, in bands, with the signal zone |
| `afs_delay_replay.py RESULT.npz FMIN FMAX [PORTS]` | the sweep replayed with each entry's delay taken out (set aside) |
