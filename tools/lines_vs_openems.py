#!/usr/bin/env python3
"""
The line model against openEMS on the examples with references: |S21| rms difference (dB), by scikit-rf and qucsator
(the circuit solver), with the formulas throughout, by default, and with --lines-field.

    python tools/lines_vs_openems.py [BOARD ...]         # default: every board with an openEMS reference

`install.py` makes the generated examples' boards (examples/make_boards.py), or demo_transition is skipped.
"""
import os, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import compare_solvers as cs                                             # noqa: E402

EXTRA = ('demo_transition', 'sma_launch', 'hairpin', 'rf_interdig')
PORTS = {'branchline': 4, 'coupler': 4, 'ratrace': 4, 'diffvia': 4, 'wilkinson': 3}
MODES = {'formulas': "import kicad2ems.lines.reference as r, kicad2ems.lines.topology as t; "
                     "r.FIELD_CPW = t.FIELD_GROUPS = False; ",
         'default': '', 'field': "import kicad2ems.lines.reference as r; r.FIELD = True; "}
CLI = "import sys; from kicad2ems.cli import main; sys.argv = ['kicad2ems'] + sys.argv[1:]; sys.exit(main())"

boards = sys.argv[1:] or list(cs.BOARDS) + list(EXTRA)
out = os.path.join(REPO, 'build', 'lines_vs_openems')
print(f"{'board':16s}" + ''.join(f"{b + ' ' + m:>18s}" for b in ('skrf', 'qucsator') for m in MODES))
for b in boards:
    name = b if b in EXTRA else f"rf_{b}"
    ref = os.path.join(REPO, 'examples', 'reference', f"{name}_openems.s{PORTS.get(b, 2)}p")
    if not os.path.exists(os.path.join(REPO, 'examples', f'{name}.kicad_pcb')):
        print(f"{b:16s} no board (examples/make_boards.py)")
        continue
    row = f"{b:16s}"
    for fast in ('skrf', 'qucsator'):
        for mode, pre in MODES.items():
            d = os.path.join(out, fast, mode, b)
            os.makedirs(d, exist_ok=True)
            subprocess.run([sys.executable, '-c', pre + CLI, f'examples/{name}.toml', '--solvers', 'lines', '--fast', fast,
                            '--out', d, '--no-report'], cwd=REPO, capture_output=True, text=True)
            ts = cs.touchstone(d, name, 'lines')
            row += f"{cs.agreement(ts, ref, b)[1]:18.2f}" if ts else f"{'FAILED':>18s}"
    print(row, flush=True)
