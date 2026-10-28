#!/usr/bin/env python3
"""
The solver comparison of docs/examples.md, regenerated: each RF example with an openEMS reference
(examples/reference/) run on every solver, the table's figures measured from their Touchstone files, and each
solver's openEMS figure checked against the reference's. Run it before a release; it takes hours with openEMS.

    python tools/compare_solvers.py                                   # every board, openEMS, gfdtd, mom25d, the line model
    python tools/compare_solvers.py --only steplpf stubnotch --solvers mom25d lines
    python tools/compare_solvers.py --reference-only                  # the references' figures alone (no runs)
    python tools/compare_solvers.py --table-only                      # the table again from build/compare

The table is measured from the Touchstone files under build/compare/<board>/<solver>/, this run's and earlier
ones' (a solver run again replaces its own), and written with results.json. mom25d runs with lumped ports
(--mom-port lumped), the FDTD solvers' kind, so its figures compare with theirs. openEMS needs its libraries: run
it from an activated environment (setup.sh's activate sets LD_LIBRARY_PATH).

Writes build/compare/results.json and results.md (the table, in examples.md's columns). A board's boards are made
first (examples/make_rf_examples.py). Exit status 1 when an openEMS figure moved from its reference's by more
than --tol (dB) or --tol-ghz: the mesh or the model changed what the references were made with.
"""
import argparse, glob, json, os, subprocess, sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'build', 'compare')
SOLVERS = ('openems', 'gfdtd', 'mom25d', 'lines')
LABELS = dict(openems='openEMS', gfdtd='gfdtd', mom25d='mom25d', lines='line model')
SUFFIX = dict(openems=('openems',), gfdtd=('gfdtd',), mom25d=('mom25d',), lines=('qucs', 'scikit'))   # <name>_<suffix>.sNp

# per board: what the table shows, and the figures. A figure is (kind, S, argument):
#   at     |S| in dB at a frequency (GHz)            min   the frequency (GHz) of |S|'s minimum (a notch)
#   worst  |S|'s largest value in dB over the band   peak  the frequency (GHz) of |S|'s maximum (a pass band)
#   cut    the first frequency (GHz) where |S| falls below a level (dB)
# S is 'S21' (by port number) or 'P2d<P1d' (by port name: mixed-mode ports).
BOARDS = {
    'branchline': ('3 dB hybrid, 2.4 GHz', 'S21 / S31', [('at', 'S21', 2.4), ('at', 'S31', 2.4)], ' / '),
    'wilkinson': ('power divider with a 100 Ω resistor', 'S21, S22, S32',
                  [('at', 'S21', 2.4), ('at', 'S22', 2.4), ('at', 'S32', 2.4)], ', '),
    'steplpf': ('stepped-impedance low-pass, 2 GHz', '−3 dB frequency', [('cut', 'S21', -3.0)], ''),
    'stubnotch': ('two open λ/4 stubs, 2.4 GHz', 'notch', [('min', 'S21', None)], ''),
    'gcpw': ('grounded CPW with via fences', 'S11, S21 at 5 GHz', [('at', 'S11', 5.0), ('at', 'S21', 5.0)], ', '),
    'coupler': ('10 dB coupler, 0.14 mm gap', 'S31, S21', [('at', 'S31', 2.4), ('at', 'S21', 2.4)], ', '),
    'ratrace': ('180° hybrid ring, 2.4 GHz', 'S21, S41, S31; S31 null',
                [('at', 'S21', 2.4), ('at', 'S41', 2.4), ('at', 'S31', 2.4), ('min', 'S31', None)], ', '),
    'radialstub': ('one 60° radial stub', 'notch', [('min', 'S21', None)], ''),
    'stripline': ('4 layers, via-fenced stripline', 'worst S11, S21 at 10 GHz',
                  [('worst', 'S11', None), ('at', 'S21', 10.0)], ', '),
    'diffvia': ('100 Ω pair through two vias', 'Sdd21 at 5 / 10 GHz, worst Sdd11',
                [('at', 'P2d<P1d', 5.0), ('at', 'P2d<P1d', 10.0), ('worst', 'P1d<P1d', None)], [' / ', ', ']),
    'shielded': ('resonator filter in a metal can', 'peak', [('peak', 'S21', None)], ''),
}


def port_names(path):
    with open(path) as fh:
        for line in fh:
            if not line.startswith('!'):
                break
            if line.startswith('! ports:'):
                return [p.split('=', 1)[1].strip() for p in line[8:].split(',') if '=' in p]
    return None


def column(path, spec):
    """(f in Hz, |S| in dB) of S spec ('S21', or 'P2d<P1d' by port name) in a Touchstone file."""
    sys.path.insert(0, os.path.join(REPO, 'src'))
    from kicad2ems.touchstone import read_touchstone
    f, S = read_touchstone(path)
    if '<' in spec:
        names = port_names(path) or []
        out, into = spec.split('<')
        if out not in names or into not in names:
            raise ValueError(f"{os.path.basename(path)}: no ports {out}, {into} (it has {', '.join(names)})")
        i, j = names.index(out), names.index(into)
    else:
        i, j = int(spec[1]) - 1, int(spec[2]) - 1
    return f, 20 * np.log10(np.abs(S[:, i, j]) + 1e-15)


def figure(path, kind, spec, arg):
    """One figure of a Touchstone file (BOARDS' kinds); None where it has none (a cut never reached)."""
    f, db = column(path, spec)
    ok = np.isfinite(db)
    f, db = f[ok], db[ok]
    if not len(f):
        return None
    if kind == 'at':
        return float(np.interp(arg * 1e9, f, db)) if f[0] <= arg * 1e9 <= f[-1] else None
    if kind == 'worst':
        return float(db.max())
    if kind in ('min', 'peak'):
        k = int(np.argmin(db) if kind == 'min' else np.argmax(db))
        if 0 < k < len(f) - 1:                                       # a parabola through the three samples round it
            y0, y1, y2 = db[k - 1:k + 2]
            den = y0 - 2 * y1 + y2
            if den:
                return float((f[k] + 0.5 * (y0 - y2) / den * (f[k + 1] - f[k - 1]) / 2) / 1e9)
        return float(f[k] / 1e9)
    if kind == 'cut':
        below = np.nonzero(db < arg)[0]
        if not len(below) or below[0] == 0:
            return None
        k = below[0]
        return float(np.interp(arg, [db[k], db[k - 1]], [f[k], f[k - 1]]) / 1e9)
    raise ValueError(kind)


def figures(path, board):
    return [figure(path, *fig) for fig in BOARDS[board][2]]


def fmt(board, vals):
    """A cell of the table: the figures in examples.md's form (dB to one decimal, frequencies in GHz)."""
    kinds = [k for k, _, _ in BOARDS[board][2]]
    parts = []
    for k, v in zip(kinds, vals):
        if v is None:
            parts.append('–')
        elif k in ('min', 'peak', 'cut'):
            parts.append(f"{v:.3f} GHz")
        else:
            parts.append(f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}")
    unit = ' dB' if all(k in ('at', 'worst') for k in kinds) else ''
    sep = BOARDS[board][3]
    seps = sep if isinstance(sep, list) else [sep] * len(parts)        # a separator per gap, or one for all
    out = parts[0] if parts else ''
    for k, p in enumerate(parts[1:]):
        out += seps[min(k, len(seps) - 1)] + p
    return out.replace('-', '−') + unit if parts else ''


def touchstone(folder, name, solver):
    for suf in SUFFIX[solver]:
        hits = sorted(glob.glob(os.path.join(folder, f"{name}_{suf}.s*p")))
        if hits:
            return hits[0]
    return None


def run(board, solver, python, timeout):
    """Run one solver on one board into build/compare/<board>/<solver>; the Touchstone file, or None."""
    name = f"rf_{board}"
    cfg = os.path.join(REPO, 'examples', f"{name}.toml")
    pcb = os.path.join(REPO, 'examples', f"{name}.kicad_pcb")
    if not os.path.exists(pcb):
        subprocess.run([sys.executable, os.path.join(REPO, 'examples', 'make_rf_examples.py'), board,
                        os.path.join(REPO, 'examples', name)], check=True, stdout=subprocess.DEVNULL)
    out = os.path.join(OUT, board, solver)
    os.makedirs(out, exist_ok=True)
    cmd = [python, '-m', 'kicad2ems.cli', cfg, '--solvers', solver, '--out', out, '--no-report']
    if solver == 'mom25d':
        cmd += ['--mom-port', 'lumped']     # the FDTD solvers' lumped ports, not wave ports de-embedded onto the line
    with open(os.path.join(out, 'log.txt'), 'w') as log:
        try:
            p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, cwd=REPO)
        except subprocess.TimeoutExpired:
            print(f"  {board} {solver}: timed out after {timeout} s", flush=True)
            return None
    ts = touchstone(out, name, solver)
    if p.returncode or not ts:
        print(f"  {board} {solver}: failed (exit {p.returncode}; {os.path.join(out, 'log.txt')})", flush=True)
    return ts


def table(results, solvers):
    head = ['Board', 'Result'] + [LABELS[s] for s in solvers]
    rows = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    for board, r in results.items():
        desc, what, _, _ = BOARDS[board]
        cells = [fmt(board, r['solvers'][s]) if r['solvers'].get(s) else '–' for s in solvers]
        rows.append(f"| `rf_{board}`: {desc} | {what} | " + ' | '.join(cells) + ' |')
    return '\n'.join(rows)


def moved(board, ref, now, tol_db, tol_ghz):
    """The openEMS figures that moved from the reference's beyond the tolerances: [(index, ref, now)]."""
    out = []
    for k, ((kind, _, _), a, b) in enumerate(zip(BOARDS[board][2], ref, now)):
        if a is None or b is None:
            if (a is None) != (b is None):
                out.append((k, a, b))
            continue
        if abs(a - b) > (tol_ghz if kind in ('min', 'peak', 'cut') else tol_db):
            out.append((k, a, b))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--only', nargs='+', choices=list(BOARDS), help='these boards (default: all)')
    ap.add_argument('--solvers', nargs='+', choices=SOLVERS, default=list(SOLVERS))
    ap.add_argument('--python', default=sys.executable, help='the Python that runs kicad2ems (its solvers installed)')
    ap.add_argument('--timeout', type=float, default=4 * 3600, help='seconds per solver run')
    ap.add_argument('--tol', type=float, default=0.3, help='dB an openEMS figure may move from its reference')
    ap.add_argument('--tol-ghz', type=float, default=0.01, help='GHz a frequency may move from its reference')
    ap.add_argument('--reference-only', action='store_true', help="the references' figures alone (no runs)")
    ap.add_argument('--table-only', action='store_true', help='the table from the runs already in build/compare')
    a = ap.parse_args()
    boards = a.only or list(BOARDS)
    os.makedirs(OUT, exist_ok=True)
    results = {}
    for b in boards:
        ref = os.path.join(REPO, 'examples', 'reference', f"rf_{b}_openems")
        ref = next(iter(sorted(glob.glob(ref + '.s*p'))), None)
        r = results.setdefault(b, dict(reference=None, solvers={}))
        if ref:
            r['reference'] = figures(ref, b)
        if a.reference_only:
            print(f"{b:12s} reference: {fmt(b, r['reference']) if r['reference'] else 'none'}", flush=True)
            continue
        for s in ([] if a.table_only else a.solvers):
            print(f"{b}: {s} ...", flush=True)
            ts = run(b, s, a.python, a.timeout)
            if ts:
                print(f"  {fmt(b, figures(ts, b))}", flush=True)
        for s in SOLVERS:                                           # what is on disk: this run's and earlier ones
            ts = touchstone(os.path.join(OUT, b, s), f"rf_{b}", s)
            if ts:
                r['solvers'][s] = figures(ts, b)
    if a.reference_only:
        return 0
    json.dump(results, open(os.path.join(OUT, 'results.json'), 'w'), indent=1)
    shown = [s for s in SOLVERS if s in a.solvers or any(r['solvers'].get(s) for r in results.values())]
    md = table({b: results[b] for b in boards if b in results}, shown)
    bad = []
    for b in boards:
        r = results.get(b) or {}
        if r.get('reference') and (r.get('solvers') or {}).get('openems'):
            bad += [(b, k, x, y) for k, x, y in moved(b, r['reference'], r['solvers']['openems'], a.tol, a.tol_ghz)]
    if bad:
        md += '\n\nopenEMS figures that moved from examples/reference/:\n\n' + '\n'.join(
            f"- `rf_{b}` figure {k + 1}: {x} -> {y}" for b, k, x, y in bad)
    open(os.path.join(OUT, 'results.md'), 'w').write(md + '\n')
    print(md)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
