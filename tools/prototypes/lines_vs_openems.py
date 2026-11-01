"""lines_vs_openems.py TAG base|field [boards...]: the line model (scikit-rf) on the RF examples, agreement with openEMS."""
import os, sys, subprocess, json, time
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import compare_solvers as cs
EXTRA = ('demo_transition', 'sma_launch', 'hairpin', 'rf_interdig')
tag = sys.argv[1]; field = len(sys.argv) > 2 and sys.argv[2] == 'field'
boards = [b for b in sys.argv[3 if field or len(sys.argv) > 2 and sys.argv[2] == 'base' else 2:]] or list(cs.BOARDS) + list(EXTRA)
out = os.path.join(REPO, 'build', 'lines_vs_openems', tag); res = {}
for b in boards:
    name = b if b in EXTRA else f"rf_{b}"; d = os.path.join(out, b); os.makedirs(d, exist_ok=True)
    pre = "import kicad2ems.lines.reference as r; r.FIELD = True; " if field else ""
    code = pre + "import sys; from kicad2ems.cli import main; sys.argv = ['kicad2ems'] + sys.argv[1:]; sys.exit(main())"
    t = time.time()
    p = subprocess.run([sys.executable, '-c', code, f'examples/{name}.toml', '--solvers', 'lines', '--fast', 'skrf', '--out', d, '--no-report'],
                       cwd=REPO, capture_output=True, text=True)
    dt = time.time() - t
    ts = cs.touchstone(d, name, 'lines')
    ref = os.path.join(REPO, 'examples', 'reference', f'{name}_openems.s{ {"branchline":4,"coupler":4,"ratrace":4,"diffvia":4,"wilkinson":3}.get(b,2)}p')
    if not ts:
        print(b, 'FAILED', p.stdout[-600:], p.stderr[-1500:]); continue
    if b in EXTRA:
        ref = os.path.join(REPO, 'examples', 'reference', f'{name}_openems.s2p')
    a = cs.agreement(ts, ref, b); fg = '' if b in EXTRA else cs.fmt(b, cs.figures(ts, b))
    res[b] = dict(agreement=a, fig=fg, t=dt)
    print(f"{b:11s} refl {a[0]:6.1f} dB  thru {a[1]:5.2f} dB   {fg}   {dt:.1f}s", flush=True)
json.dump(res, open(os.path.join(out, 'res.json'), 'w'), indent=1)
