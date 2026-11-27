r"""python -m kicad2ems.pcbqs.thermal: the steady temperature of a board from
what its parts dissipate.

    python -m kicad2ems.pcbqs.thermal board.kicad_pcb --loss Q1=0.35 \
        --loss D1=0.5
    python -m kicad2ems.pcbqs.thermal --circuit circuit-results
        # the board and the losses of a circuit run
    python -m kicad2ems.pcbqs.thermal --describe --json
        # the options, for kicad2ems-gui's form

It writes <out>/thermal.json (each part's temperature, each layer's hottest and
mean, the heat in and out), thermal.npz (the maps), thermal.png and, where
kicad2ems's viewer reads results, the board and each layer's temperature as
field dumps (thermal/viewer.py).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

from . import Problem, solve, losses_of, FILM, AMBIENT
from kicad2ems.core.options import table


# (key, kind, default, help, group): _cli's table, which --describe gives the
# window
OPTIONS = table([
    ('loss', 'list', [],
     "A footprint's loss, REF=W (repeatable); with a circuit run, these "
     "replace its figures", 'Heat'),
    ('circuit', 'str', '',
     "A circuit run's results folder (python -m kicad2ems.pcbqs.circuit): "
     "its board and its parts' losses", 'Heat'),
    ('case', 'str', '',
     "The circuit run's case, by name; empty: the one with the most loss; all: "
     "a map for each (in <out>/<case>) and a table of each part at each "
     "(thermal-cases.json)", 'Heat'),
    ('theta', 'list', [],
     "From a part's pads to the part itself, REF=K/W (repeatable); a "
     "footprint's field thermal.theta gives it too", 'Parts'),
    ('tmax', 'list', [],
     "The most a part may reach, REF=C (repeatable: its junction's limit); a "
     "footprint's field thermal.tmax gives it too", 'Parts'),
    ('air', 'list', [],
     "From a part itself to the air, REF=K/W (repeatable): its body's own "
     "path, or a heatsink's on it; its heat then splits between that and its "
     "pads (through its theta). A footprint's field thermal.air gives it "
     "too", 'Parts'),
    ('ambient', 'float', AMBIENT, 'The air, C', 'Air'),
    ('film', 'float', FILM,
     'Convection and radiation from the faces, W/m2/K (the top face, with '
     'film-bottom)', 'Air'),
    ('film_bottom', 'float', None, 'The bottom face, W/m2/K, when it differs',
     'Air'),
    ('film_edge', 'float', None,
     "The board's edges and cut-outs, W/m2/K; empty: the faces' mean, 0: "
     "insulated", 'Air'),
    ('air_speed', 'float', 0.0,
     "Forced air along both faces, m/s: their film from it (a flat plate's "
     "convection and radiation, the film's still air joined), in place of film "
     "and film-bottom", 'Air'),
    ('air_length', 'float', None,
     "mm of board along the moving air (default: the outline's longer side)",
     'Air'),
    ('hold', 'list', [],
     "A footprint's pads held at a temperature, REF=C (repeatable): bolted to "
     "a cold plate or a chassis; the heat that leaves through it is "
     "reported", 'Parts'),
    ('transient', 'float', 0.0,
     "s: also the temperature over this long, from cold unless --start "
     "steady (the grid's; the board's heat capacity, not the parts' "
     "bodies'): thermal-transient.json and .png", 'Transient'),
    ('profile', 'list', [],
     "a part's loss over time, REF=SHAPE (repeat; all=SHAPE: every part "
     "without its own): step[:A,B][@T] (a share of its loss, A before T and "
     "B after), off@T, pulse:ON,OFF[,LOW,HIGH][@T] (seconds), or a file of "
     "t_s, W (its loss in watts over time; its --loss then need not be "
     "given)", 'Transient'),
    ('start', 'str', 'cold',
     "the transient from cold (the ambient), or from the steady temperature "
     "of the profiles' values before t = 0 (a load step from a base load, a "
     "cooling)", 'Transient', ('cold', 'steady')),
    ('size', 'float', None, 'mm: the grid pitch (0.5)', 'Solver'),
])


def describe():
    from .._cli import describe as describe_options
    return describe_options(OPTIONS)


# mm: the copper's loss map gathered into squares this size, a heat source each
COPPER_BIN = 1.0


def add_copper_loss(p, case, board):
    """The board copper's own loss into the problem, where the circuit's copper
    currents put it (the case's copper-loss.npz, which the circuit's --fields
    writes), gathered into COPPER_BIN squares; without that map, said (the
    copper's loss had reached no thermal map: 3.6 % of the buck's)."""
    total = float(((case.get('copper') or {}).get('loss_W')) or 0.0)
    if total <= 0:
        return
    path = Path(case.get('folder', '')) / 'copper-loss.npz'
    if not path.is_file():
        p.notes.append(
            f"the board copper's own loss ({1e3 * total:.3g} mW) is left out: "
            "run the circuit with --fields for where it is")
        return
    from shapely.geometry import box
    with np.load(path, allow_pickle=False) as m:
        layer, x, y, W = m['layer'], m['x_mm'], m['y_mm'], m['W']
    names = {l.name for l in p.layers}
    bins = {}
    for lay, xi, yi, w in zip(layer, x, y, W):
        if lay in names and w > 0:
            k = (str(lay), int(np.floor(xi / COPPER_BIN)),
                 int(np.floor(yi / COPPER_BIN)))
            bins[k] = bins.get(k, 0.0) + float(w)
    for (lay, i, j), w in bins.items():
        p.heat('board copper',
               [(lay,
                 box(i * COPPER_BIN, j * COPPER_BIN, (i + 1) * COPPER_BIN,
                     (j + 1) * COPPER_BIN))], w)


def part_fields(board):
    """({ref: K/W}, {ref: C}, {ref: K/W}) from the footprints' fields
    thermal.theta, thermal.tmax and thermal.air (as pdn.load gives a part's
    current): what the design holds of its parts' thermal limits and paths."""
    from ..pdn.rail import field_value
    out = ({}, {}, {})
    for fp in (board.get('prims') or {}).get('footprints', []):
        for got, key in zip(out,
                            ('thermal.theta', 'thermal.tmax', 'thermal.air')):
            v = field_value(fp, key)
            if v is not None:
                got[fp['ref']] = v
    return out


def _pairs(items, what, ap):
    out = {}
    for it in items:
        ref, _, val = it.partition('=')
        try:
            out[ref.strip()] = float(val)
        except ValueError:
            ap.error(f"{what} {it!r}: REF=NUMBER")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='python -m kicad2ems.pcbqs.thermal', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    from .._cli import add_options, load_board, print_describe
    ap.add_argument('board', nargs='?',
                    help='a .kicad_pcb or a kicad2ems board.json')
    ap.add_argument('--out', default='thermal-results',
                    help='the results folder (default: thermal-results)')
    ap.add_argument('--describe', action='store_true',
                    help='the options (with --json: for kicad2ems-gui)')
    ap.add_argument('--json', action='store_true')
    add_options(ap, OPTIONS)
    a = ap.parse_args(argv)
    if a.describe:
        print_describe(describe(), a.json)
        return 0
    # (R-E11: a sweep had one map, its worst point)
    if a.circuit and a.case == 'all':
        return every_case(a, argv)
    losses, left = _pairs(a.loss, '--loss', ap), []
    board_file, case_name, case = a.board, None, None
    if a.circuit:
        folder = Path(a.circuit)
        try:
            model = json.loads((folder / 'model.json').read_text())
            cases = json.loads((folder / 'circuit.json').read_text())['cases']
        except (OSError, KeyError, ValueError) as e:
            ap.error(f'{folder}: not the results of a circuit run ({e})')
        if a.case:
            pick = [c for c in cases if c['name'] == a.case]
            if not pick:
                ap.error(
                    f"case {a.case!r}: one of "
                    f"{', '.join(c['name'] for c in cases)}"
                )
            case = pick[0]
        else:
            case = max(cases, key=lambda c: c.get('loss_W', 0.0))
        from_run, left = losses_of(case, model)
        losses = {**from_run, **losses}
        board_file = board_file or (model.get('board') or {}).get('file')
        case_name = case['name']
        print(f"losses of case {case_name}")
    if not board_file:
        ap.error('a board, or --circuit with a model that names one')
    from . import transient as TR
    profiles = {}
    for item in a.profile:
        ref, _, text = str(item).partition('=')
        try:
            if not ref.strip() or not text.strip():
                raise ValueError(f"--profile {item!r}: REF=SHAPE")
            profiles[ref.strip()] = TR.profile(text.strip(), watts=True)
        except ValueError as e:
            ap.error(str(e))
    for ref, pr in profiles.items():  # a file's W: a share of the part's loss
        if pr.kind == 'file' and ref != 'all':
            peak = float(abs(pr.values).max()) or 1.0
            losses.setdefault(ref, peak)
            pr.values = pr.values / losses[ref]
    if not losses:
        ap.error('no loss: --loss REF=W or --circuit FOLDER')
    try:
        board = load_board(board_file)
    except (OSError, RuntimeError, ValueError) as e:
        ap.error(str(e))
    # the footprints' own, the options over them
    theta, tmax, air = part_fields(board)
    theta.update(_pairs(a.theta, '--theta', ap))
    tmax.update(_pairs(a.tmax, '--tmax', ap))
    air.update(_pairs(a.air, '--air', ap))
    film = (a.film if a.film_bottom is None else a.film_bottom, a.film)
    if a.air_speed:  # moving air: both faces' from it
        from .problem import forced_film
        from shapely.geometry import shape
        length = a.air_length
        if not length:
            x0, y0, x1, y1 = (shape(board['outline']).bounds
                              if board.get('outline') else (0, 0, 100, 100))
            length = max(x1 - x0, y1 - y0)
        try:
            film = tuple(
                forced_film(a.air_speed, length, still=f) for f in film)
        except ValueError as e:
            ap.error(str(e))
    p = Problem.of(board, losses, film=film, ambient=a.ambient, theta=theta,
                   edge_film=a.film_edge)
    p.tmax = tmax
    p.air = {k: v for k, v in air.items() if v > 0}
    held = _pairs(a.hold, '--hold', ap)
    if held:
        p.hold(board, held)
    p.notes += left
    if case is not None:
        add_copper_loss(p, case, board)
    if not p.sources:
        for n in p.notes:
            print(n, file=sys.stderr)
        ap.error('none of the losses is on the board')
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from .picture import picture
    if a.transient < 0:
        ap.error('--transient: seconds, positive')
    if (profiles or a.start != 'cold') and not a.transient:
        ap.error('--profile and --start are the transient\'s: give '
                 '--transient SECONDS')
    try:
        first = solve(p, a.size, **(dict(
            transient=a.transient, profiles=profiles, start=a.start)
            if a.transient else {}))
    except ValueError as e:
        ap.error(str(e))
    summary = dict(first.summary(), board=str(Path(board_file).resolve()),
                   circuit=str(a.circuit or ''), case=case_name,
                   figure='thermal.png')
    arrays = dict(grid_T=first.T, grid_origin=np.array([first.x0, first.y0]),
                  grid_pitch=np.array(first.size))
    picture(first, out / 'thermal.png')
    extra = first.extra or {}
    print(
        f"{p.watts:.3f} W into the board, {first.out_W:.3f} W out through the "
        f"films ({first.edge_W:.3f} W of it through the edges)"
        + (f", {sum(extra['air_W'].values()):.3f} W from the parts to the air"
           if extra.get('air_W') else '')
        + (f", {sum(extra['held_W'].values()):.3f} W out through the held "
           "footprints" if extra.get('held_W') else '')
        + f"; ambient {p.ambient:g} C")
    print(f"{'part':8s} {'W':>7s} {'C':>9s}")
    for ref in sorted(first.parts, key=lambda k: -first.parts[k]['part_C']):
        print(f"{ref:8s} {first.parts[ref]['power_W']:7.3f} "
              f"{first.parts[ref]['part_C']:9.1f}")
    print(f"{'hottest copper':16s} {first.hottest:9.1f}")
    for name, d in first.parts.items():
        if 'to_air_W' in d:
            print(
                f"{name}: {d['to_air_W']:.3f} W of its {d['power_W']:.3f} W to "
                f"the air directly ({d['air_K_W']:g} K/W)"
            )
    for name, w in ((first.extra or {}).get('held_W') or {}).items():
        print(f"{name}: held, {w:.3f} W out through it")
    if first.transient:
        tr = first.transient
        (out / 'thermal-transient.json').write_text(json.dumps(tr, indent=1))
        from .picture import heating_picture
        heating_picture(tr, out / 'thermal-transient.png')
        slow = sorted(tr['to_63_s'].items(), key=lambda kv: -kv[1])[:3]
        if slow:
            print("heating from cold: "
                  + ', '.join(f"{k} 63 % in {v:.0f} s" for k, v in slow)
                  + " (thermal-transient.json)")
        hot = sorted(tr['peak'].items(), key=lambda kv: -kv[1]['C'])[:3]
        print(f"transient over {a.transient:g} s from {a.start}: "
              + ', '.join(f"{k} peaks at {v['C']:.1f} C at {v['at_s']:.3g} s"
                          for k, v in hot)
              + (f"; the board's time constants "
                 + ', '.join(f"{t:.3g}" for t in tr['tau_s'][:3]) + " s"
                 if tr['tau_s'] else ''))
        summary['transient'] = dict(
            file='thermal-transient.json', seconds=a.transient,
            to_63_s=tr['to_63_s'], start=a.start, peak=tr['peak'],
            tau_s=tr['tau_s'], hottest_C=float(max(tr['hottest_C'])),
            steps=tr['steps'], profiles=tr['profiles'])
    for n in p.notes:
        print('note:', n)
    np.savez_compressed(out / 'thermal.npz',
                        layers=np.array([l.name for l in p.layers]), **arrays)
    try:  # the same maps for the viewer; a failure costs only them
        from .viewer import write_viewer
        shown = write_viewer(str(out), board, p, first)
    except Exception as e:
        shown = None
        print(
            f"note: the viewer's maps were not written: {type(e).__name__}: {e}"
        )
    summary['viewer'] = shown
    (out / 'thermal.json').write_text(json.dumps(summary, indent=1))
    print(f"results in {out}")
    return 0


def every_case(a, argv):
    """--case all: each case of the circuit run its own map (<out>/<case>), the
    one with the most loss also at the top (what the report and the Viewer read,
    as before), and thermal-cases.json: each part's temperature, margin and loss
    at each case, and each case's hottest copper."""
    import shutil
    folder = Path(a.circuit)
    cases = json.loads((folder / 'circuit.json').read_text())['cases']
    argv = list(sys.argv[1:] if argv is None else argv)
    keep, given = [], iter(argv)  # the arguments but --case and --out
    for x in given:
        if x in ('--case', '--out'):
            next(given, None)
        elif not x.startswith(('--case=', '--out=')):
            keep.append(x)
    out = Path(a.out)
    table = dict(format=1, circuit=str(folder.resolve()), cases=[], parts={})
    for c in cases:
        sub = out / c['name']
        main(keep + ['--case', c['name'], '--out', str(sub)])
        r = json.loads((sub / 'thermal.json').read_text())
        r = (r if 'parts' in r else next(
            iter(v for v in r.values()
                 if isinstance(v, dict) and 'parts' in v)))
        table['cases'].append(
            dict(name=c['name'], loss_W=c.get('loss_W'),
                 hottest_C=r.get('hottest_C')))
        for ref, q in r['parts'].items():
            table['parts'].setdefault(ref, {})[c['name']] = {
                k: q.get(k)
                for k in ('part_C', 'power_W', 'margin_K')
            }
    worst = max(cases, key=lambda c: c.get('loss_W', 0.0))['name']
    for f in (out / worst).iterdir():  # the worst case at the top, as before
        dest = out / f.name
        (shutil.copytree(f, dest, dirs_exist_ok=True)
         if f.is_dir() else shutil.copy2(f, dest))
    table['top'] = worst
    (out / 'thermal-cases.json').write_text(json.dumps(table, indent=1))
    refs = sorted(table['parts'],
                  key=lambda k: -max(v['part_C'] or 0
                                     for v in table['parts'][k].values()))
    print(f"{'part':8s} "
          + ' '.join(f"{c['name'][:14]:>14s}" for c in table['cases']))
    for ref in refs:
        row = table['parts'][ref]
        print(f"{ref:8s} " + ' '.join(
            f"{(row.get(c['name']) or {}).get('part_C') or float('nan'):14.1f}"
            for c in table['cases']))
    print(
        f"{len(cases)} cases; the top-level map is {worst}'s (the most loss); "
        "thermal-cases.json has the table"
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
