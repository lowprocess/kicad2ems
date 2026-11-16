"""
Settings varied (R-E5): the run solved again for each combination of other settings' values, where a part's value
needs no new solve (--part-value) but a setting does (a mesh, a dielectric, a layer's height through a config).

    --vary fine=0.1,0.05 --vary diel=fixed,wideband        # every combination, at most VARY_MAX
    [vary]                                                 # the same in a config
    fine = [0.1, 0.05]

Each combination runs the whole job (its solvers, reports, an earlier result reused where nothing changed) in
<out>/vary/<tag>/, the tag its values (fine=0.1_diel=fixed); <out>/<name>_vary.json lists them (their values,
folders, exit codes and each solver's Touchstone), the window's view of the variation.
"""
import itertools
import json
import os
import re

VARY_MAX = 32
NOT_VARIED = {'vary', 'out', 'board', 'config', 'preview', 'check'}


def parse(specs, options):
    """[(key, [raw values])] of --vary specs ('KEY=V1,V2'; a list from a [vary] table: KEY=V1,V2 too), each key
    an option (its values converted by it when a combination is made); raises ValueError for anything else."""
    out = []
    for spec in specs or []:
        m = re.fullmatch(r'\s*([A-Za-z_][\w-]*)\s*=\s*(.+)', str(spec))
        if not m:
            raise ValueError(f"--vary {spec!r}: write KEY=V1,V2,... (e.g. fine=0.1,0.05)")
        key = m.group(1).replace('-', '_')
        if key not in options:
            raise ValueError(f"--vary {key}: not a setting")
        if key in NOT_VARIED:
            raise ValueError(f"--vary {key}: not a setting a run varies")
        vals = [v.strip() for v in m.group(2).split(',') if v.strip()]
        if len(vals) < 2:
            raise ValueError(f"--vary {key}: give two values or more")
        for v in vals:
            options[key].convert(v)                                 # (a value its option refuses: said now)
        if any(k == key for k, _ in out):
            raise ValueError(f"--vary {key}: given twice")
        out.append((key, vals))
    n = 1
    for _, vals in out:
        n *= len(vals)
    if n > VARY_MAX:
        raise ValueError(f"--vary: {n} combinations, at most {VARY_MAX}")
    return out


def combinations(spec):
    """[[(key, raw value), ...]] of every combination, in order."""
    keys = [k for k, _ in spec]
    return [list(zip(keys, vals)) for vals in itertools.product(*[v for _, v in spec])]


def tag(combo):
    """The folder of a combination: fine=0.1_diel=fixed (file-name safe)."""
    return '_'.join(f"{k}={re.sub(r'[^A-Za-z0-9.+-]', '-', str(v))}" for k, v in combo)


def run(s, command='', echo=print):
    """Each combination of s.vary as a job of its own (pipeline.Job) in <out>/vary/<tag>; <out>/<name>_vary.json.
    Returns the worst exit code."""
    import copy
    from .config import OPTIONS
    from .pipeline import Job
    spec = parse(s.vary, OPTIONS)
    combos = combinations(spec)
    name = os.path.splitext(os.path.basename(s.board))[0]
    if name.endswith('_board'):
        name = name[:-len('_board')]
    os.makedirs(s.out, exist_ok=True)
    echo(f"vary: {len(combos)} combinations of {', '.join(k for k, _ in spec)}, each solved in "
         f"{os.path.join(s.out, 'vary')}")
    runs, worst = [], 0
    for i, combo in enumerate(combos):
        sc = copy.deepcopy(s)
        for k, v in combo:
            setattr(sc, k, OPTIONS[k].convert(v))
        sc.vary = []
        sc.out = os.path.join(s.out, 'vary', tag(combo))
        echo(f"--- vary {i + 1}/{len(combos)}: {', '.join(f'{k} = {v}' for k, v in combo)} ---")
        code = Job(sc, echo=echo, command=f"{command} [vary {tag(combo)}]").run()
        worst = max(worst, int(code or 0))
        runs.append(dict(values={k: v for k, v in combo}, folder=os.path.relpath(sc.out, s.out), code=int(code or 0),
                         solved=_solved(sc.out, name)))
    out = os.path.join(s.out, f"{name}_vary.json")
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(dict(format='kicad2ems-vary', version=1, name=name, keys=[k for k, _ in spec], runs=runs), fh,
                  indent=1)
    echo(f"vary: {sum(r['code'] == 0 for r in runs)} of {len(runs)} solved; the list: {out}")
    return worst


def _solved(folder, name):
    """{solver: its Touchstone, relative to the folder} from a run's summary (<name>_run.json)."""
    fn = os.path.join(folder, f"{name}_run.json")
    try:
        with open(fn, encoding='utf-8') as fh:
            sm = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v.get('touchstone') for k, v in (sm.get('solved') or {}).items() if isinstance(v, dict)
            and v.get('touchstone')}


def load(folder):
    """The variation listed in a results folder (<name>_vary.json), or None: dict(name, keys, runs) with each run's
    folder and Touchstones made absolute."""
    hits = [f for f in os.listdir(folder) if f.endswith('_vary.json')] if os.path.isdir(folder) else []
    if not hits:
        return None
    with open(os.path.join(folder, sorted(hits)[0]), encoding='utf-8') as fh:
        d = json.load(fh)
    for r in d.get('runs', []):
        r['folder'] = os.path.join(folder, r['folder'])
        r['solved'] = {k: os.path.join(r['folder'], v) for k, v in (r.get('solved') or {}).items()}
    return d
