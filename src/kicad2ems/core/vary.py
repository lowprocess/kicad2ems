"""
Settings varied (R-E5): the run solved again for each combination of other
settings' values, where a part's value needs no new solve (--part-value) but a
setting does (a mesh, a dielectric, a layer's height through a config).

    --vary fine=0.1,0.05 --vary diel=fixed,wideband
        # every combination, at most VARY_MAX
    [vary]
        # the same in a config
    fine = [0.1, 0.05]

Each combination runs the whole job (its solvers, reports, an earlier result
reused where nothing changed) in <out>/vary/<tag>/, the tag its values
(fine=0.1_diel=fixed); <out>/<name>_vary.json lists them (their values, folders,
exit codes, each solver's Touchstone and its figures), the window's view of the
variation; <out>/<name>_vary.csv and the log set the figures side by side, a
line a combination and solver (figures: the worst match and its dip at each
port, each transmission at the band's centre and its lowest).
"""
import itertools
import json
import math
import os
import re

VARY_MAX = 32
NOT_VARIED = {'vary', 'out', 'board', 'config', 'preview', 'check'}


def parse(specs, options):
    """[(key, [raw values])] of --vary specs ('KEY=V1,V2'; a list from a [vary]
    table: KEY=V1,V2 too), each key an option (its values converted by it when a
    combination is made); raises ValueError for anything else."""
    out = []
    for spec in specs or []:
        m = re.fullmatch(r'\s*([A-Za-z_][\w-]*)\s*=\s*(.+)', str(spec))
        if not m:
            raise ValueError(
                f"--vary {spec!r}: write KEY=V1,V2,... (e.g. fine=0.1,0.05)")
        key = m.group(1).replace('-', '_')
        if key not in options:
            raise ValueError(f"--vary {key}: not a setting")
        if key in NOT_VARIED:
            raise ValueError(f"--vary {key}: not a setting a run varies")
        vals = [v.strip() for v in m.group(2).split(',') if v.strip()]
        if len(vals) < 2:
            raise ValueError(f"--vary {key}: give two values or more")
        for v in vals:
            options[key].convert(v)  # (a value its option refuses: said now)
        if any(k == key for k, _ in out):
            raise ValueError(f"--vary {key}: given twice")
        out.append((key, vals))
    n = math.prod(len(vals) for _, vals in out)
    if n > VARY_MAX:
        raise ValueError(f"--vary: {n} combinations, at most {VARY_MAX}")
    return out


def combinations(spec):
    """[[(key, raw value), ...]] of every combination, in order."""
    keys = [k for k, _ in spec]
    return [
        list(zip(keys, vals))
        for vals in itertools.product(*[v for _, v in spec])
    ]


def tag(combo):
    """The folder of a combination: fine=0.1_diel=fixed (file-name safe)."""
    return '_'.join(
        f"{k}={re.sub(r'[^A-Za-z0-9.+-]', '-', str(v))}" for k, v in combo)


def run(s, command='', echo=print):
    """Each combination of s.vary as a job of its own (pipeline.Job) in
    <out>/vary/<tag>; <out>/<name>_vary.json. Returns the worst exit code."""
    import copy
    from .config import OPTIONS
    from .pipeline import Job
    from .runs import board_name
    spec = parse(s.vary, OPTIONS)
    combos = combinations(spec)
    name = board_name(s.board)
    os.makedirs(s.out, exist_ok=True)
    echo(
        f"vary: {len(combos)} combinations of {', '.join(k for k, _ in spec)}, "
        f"each solved in {os.path.join(s.out, 'vary')}")
    runs, worst = [], 0
    for i, combo in enumerate(combos):
        sc = copy.deepcopy(s)
        for k, v in combo:
            setattr(sc, k, OPTIONS[k].convert(v))
        sc.vary = []
        sc.out = os.path.join(s.out, 'vary', tag(combo))
        echo(
            f"--- vary {i + 1}/{len(combos)}: "
            f"{', '.join(f'{k} = {v}' for k, v in combo)} ---"
        )
        code = Job(sc, echo=echo,
                   command=f"{command} [vary {tag(combo)}]").run()
        worst = max(worst, int(code or 0))
        runs.append(
            dict(values={k: v
                         for k, v in combo},
                 folder=os.path.relpath(sc.out, s.out), code=int(code or 0),
                 solved=_solved(sc.out, name)))
    for r in runs:
        r['figures'] = {k: figures(os.path.join(s.out, r['folder'], v))
                        for k, v in r['solved'].items()}
    out = os.path.join(s.out, f"{name}_vary.json")
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(
            dict(format='kicad2ems-vary', version=1, name=name,
                 keys=[k for k, _ in spec], runs=runs), fh, indent=1)
    keys = [k for k, _ in spec]
    head, rows = table(keys, runs)
    csv = os.path.join(s.out, f"{name}_vary.csv")
    with open(csv, 'w', encoding='utf-8') as fh:
        fh.write(','.join(head) + '\n')
        fh.writelines(','.join(str(x) for x in row) + '\n' for row in rows)
    if rows:
        echo("vary: the figures, a line a combination and solver (" + csv + "):")
        w = [max(len(str(h)), *(len(_cell(r[i])) for r in rows))
             for i, h in enumerate(head)]
        echo('  ' + '  '.join(str(h).ljust(w[i]) for i, h in enumerate(head)))
        for row in rows:
            echo('  ' + '  '.join(_cell(x).ljust(w[i])
                                  for i, x in enumerate(row)))
    echo(
        f"vary: {sum(r['code'] == 0 for r in runs)} of {len(runs)} solved; the "
        f"list: {out}"
    )
    return worst


def figures(path, most=4):
    """A Touchstone file's figures, to set combinations side by side: the worst
    match (S11's highest, dB) and its dip (S11's lowest, dB, and where, GHz) at
    each port, and each transmission term's value at the band's centre and its
    lowest (dB); ports past `most` left out. {} for a file that cannot be
    read."""
    import numpy as np
    from .touchstone import read_touchstone
    try:
        f, S = read_touchstone(path)
    except (OSError, ValueError):
        return {}
    db = lambda h: 20 * np.log10(np.maximum(np.abs(h), 1e-15))
    n, out, mid = min(S.shape[1], most), {}, len(f) // 2
    for i in range(n):
        d = db(S[:, i, i])
        if np.isnan(d).all():
            continue
        k = int(np.nanargmin(d))
        out[f"S{i + 1}{i + 1} max dB"] = round(float(np.nanmax(d)), 3)
        out[f"S{i + 1}{i + 1} dip dB"] = round(float(d[k]), 3)
        out[f"S{i + 1}{i + 1} dip GHz"] = round(float(f[k] / 1e9), 4)
    for i in range(n):
        for j in range(i):
            d = db(S[:, i, j])
            if np.isnan(d).all():
                continue
            out[f"S{i + 1}{j + 1} mid dB"] = round(float(d[mid]), 3)
            out[f"S{i + 1}{j + 1} min dB"] = round(float(np.nanmin(d)), 3)
    return out


def table(keys, runs):
    """(header, rows) of the runs' figures: a row a combination and solver,
    the varied values first."""
    cols = list(dict.fromkeys(c for r in runs
                              for fig in (r.get('figures') or {}).values()
                              for c in fig))
    rows = [[r['values'][k] for k in keys] + [solver]
            + [fig.get(c, '') for c in cols]
            for r in runs for solver, fig in (r.get('figures') or {}).items()]
    return keys + ['solver'] + cols, rows


def _cell(x):
    return f"{x:g}" if isinstance(x, float) else str(x)


def _solved(folder, name):
    """{solver: its Touchstone, relative to the folder} from a run's summary
    (<name>_run.json)."""
    fn = os.path.join(folder, f"{name}_run.json")
    try:
        with open(fn, encoding='utf-8') as fh:
            sm = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {
        k: v.get('touchstone')
        for k, v in (sm.get('solved') or {}).items()
        if isinstance(v, dict) and v.get('touchstone')
    }


def load(folder):
    """The variation listed in a results folder (<name>_vary.json), or None:
    dict(name, keys, runs) with each run's folder and Touchstones made
    absolute."""
    hits = ([f for f in os.listdir(folder) if f.endswith('_vary.json')]
            if os.path.isdir(folder) else [])
    if not hits:
        return None
    with open(os.path.join(folder, sorted(hits)[0]), encoding='utf-8') as fh:
        d = json.load(fh)
    for r in d.get('runs', []):
        r['folder'] = os.path.join(folder, r['folder'])
        r['solved'] = {
            k: os.path.join(r['folder'], v)
            for k, v in (r.get('solved') or {}).items()
        }
    return d
