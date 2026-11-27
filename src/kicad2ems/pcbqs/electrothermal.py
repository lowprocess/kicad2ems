r"""
python -m kicad2ems.pcbqs.electrothermal: a rail's DC drop with its copper at
the temperature it runs at, and the board's temperature from the copper's own
loss and the parts'.

    python -m kicad2ems.pcbqs.electrothermal board.kicad_pcb --rail +5V \
        --volts 5 --load J2=6 --heat U3=1.2 --out et
    python -m kicad2ems.pcbqs.electrothermal board.kicad_pcb --rail +5V \
        --volts 5 --load J2=6 --transient 600 --current-profile pulse:30,90
        # also its temperature over 10 minutes of 30 s bursts
    python -m kicad2ems.pcbqs.electrothermal --describe --json

The DC drop (pcbqs.pdn's: the rail tree and its return, each layer's copper a
sheet, the vias' barrels) puts its loss, each triangle's and each barrel's, into
the thermal model (pcbqs.thermal's grid: the copper and the laminate of every
layer, all nets, the films and the edges) as heat at the triangles' edge
midpoints and at the vias, beside the parts' losses (--heat; --loads-heat: each
load's V x I on its pads). The temperature there sets each triangle's and each
barrel's resistivity,

    rho(T) = rho_20 (1 + alpha (T - 20 C)),  alpha = 0.00393 / K (copper),

and the drop is solved again, until no triangle's temperature moves by more than
--tolerance (K) between two passes (--passes at most; electrothermal.json says
whether it settled). The thermal system is factored once; each pass is a DC
solve and a thermal back-substitution. With --alpha 0 it is the two tools
apart: the DC drop at 20 C and the temperature of its loss.

With --transient SECONDS, the temperature over that long, from cold (or from the
steady state of the profiles' values before t = 0, --start steady): the loads'
current by --current-profile (a share of their DC current; the copper's loss
goes as its square) and each part's loss by --profile REF=SHAPE (pcbqs.thermal.
transient: step, off, pulse, a file). The copper's loss follows its temperature
there too, each point's I^2 R (1 + alpha (T - 20)) / (1 + alpha (T_hot - 20))
about the steady state's current pattern: exact while the current keeps that
pattern (a load current scaled up and down does), the pattern's own shift as the
copper warms left out (the steady passes have it: electrothermal.json gives the
difference at the end).

It writes <out>/electrothermal.json (each load's drop hot and cold, the loss
hot and cold, the copper's hottest point, each part's temperature, the passes),
the figures thermal.png (each layer's temperature) and dc_power_<layer>.png,
dc_ground_<layer>.png (the drop at temperature), electrothermal-transient.json
and .png with --transient, and the temperature and the drop where kicad2ems's
viewer reads results (thermal/viewer.py, pdn/viewer.py).

What it leaves out: the parts' own resistances' temperature (a series part's R
is its value), the current's shift between parallel paths as the copper warms
in the transient (above), and what thermal.py leaves out (air one part warms
reaching another, the film's change with temperature).
"""
import argparse
import json
import os
import sys
import time

import numpy as np

from ._cli import (add_options, describe as describe_options, load_board,
                   print_describe)
from kicad2ems.core.options import table

ALPHA = 0.00393         # 1/K: copper's resistance, at 20 C
T_SIGMA = 20.0          # C: the temperature pcbqs's SIGMA_CU holds at
PASSES = 20
TOLERANCE = 0.05        # K

# the rail's options are pcbqs.pdn's own (pdn.cli.OPTIONS), the air's and the
# parts' thermal.py's
RAIL_KEYS = ('rail', 'ground', 'volts', 'source', 'ground_source', 'load',
             'no_part_loads', 'spread_load', 'max_ohms', 'through', 'dcr',
             'stop', 'plating', 'dmax')
THERMAL_KEYS = ('theta', 'tmax', 'air', 'hold', 'ambient', 'film',
                'film_bottom', 'film_edge', 'air_speed', 'air_length', 'size')
G_HEAT, G_ET, G_TR = 'Heat', 'Electrothermal', 'Transient'


def _options():
    from .pdn.cli import OPTIONS as PDN
    from .thermal.__main__ import OPTIONS as TH
    by = {o.key: o for o in PDN}
    th = {o.key: o for o in TH}
    return ([by[k] for k in RAIL_KEYS] + table([
        ('heat', 'list', [],
         "a part's loss on the board, REF=W (repeat): a regulator's, the "
         "loads' own (their V x I is --loads-heat)", G_HEAT),
        ('loads_heat', 'bool', False,
         "each load dissipates what it draws, its V x I, on its pads (a "
         "resistive load's whole power; --heat REF=W overrides one)", G_HEAT),
    ]) + [th[k] for k in THERMAL_KEYS] + table([
        ('alpha', 'float', ALPHA,
         "copper's resistance temperature coefficient, 1/K (0: the drop at "
         "20 C, the two tools apart)", G_ET),
        ('passes', 'int', PASSES,
         "DC and thermal solves at most, each the other's at the last's "
         "temperature", G_ET),
        ('tolerance', 'float', TOLERANCE,
         "K: settled when no triangle's temperature moves by more between two "
         "passes", G_ET),
        ('transient', 'float', 0.0,
         "s: also the temperature over this long (electrothermal-transient.json "
         "and .png)", G_TR),
        ('current_profile', 'str', '',
         "the loads' current over time, a share of their DC current: step[:A,"
         "B][@T], off@T, pulse:ON,OFF[,LOW,HIGH][@T] or a file of t_s, share "
         "(empty: constant)", G_TR),
        ('profile', 'list', [],
         "a part's loss over time, REF=SHAPE (as the current's; a file's "
         "values are W) (repeat; all=SHAPE: every part without its own)",
         G_TR),
        ('start', 'str', 'cold',
         "the transient from cold (the ambient), or from the steady state of "
         "the profiles' values before t = 0", G_TR, ('cold', 'steady')),
    ]))


OPTIONS = _options()


def describe():
    return describe_options(OPTIONS, features=['viewer'])


def say(*a):
    print('electrothermal:', *a, flush=True)


# ---- the physics ------------------------------------------------------------
def copper_heat(result):
    """{layer: (x, y, W, tri)} of a DCResult's loss: each triangle's at its
    three edge midpoints, a third at each (tri: its triangle's index, -1 for a
    barrel's), and each via barrel's at its via, half on each of its two
    layers."""
    out = {}
    for lay, sh in result.sheets.items():
        if getattr(sh, 'P', None) is None or not len(sh.tris):
            continue
        P = np.nan_to_num(sh.P) / 3.0
        nd = sh.nodes[sh.tris]                                  # (M, 3, 2)
        mid = 0.5 * (nd + np.roll(nd, -1, axis=1))              # edge midpoints
        tri = np.repeat(np.arange(len(sh.tris)), 3)
        out[lay] = [mid[..., 0].ravel(), mid[..., 1].ravel(), np.repeat(P, 3),
                    tri]
    for v in result.vias:
        for la, lb, w in v.get('W', ()):
            for lay in (la, lb):
                xs, ys, ws, ts = out.setdefault(
                    lay, [np.zeros(0), np.zeros(0), np.zeros(0),
                          np.zeros(0, int)])
                out[lay] = [np.r_[xs, v['x']], np.r_[ys, v['y']],
                            np.r_[ws, 0.5 * w], np.r_[ts, -1]]
    return {k: tuple(v) for k, v in out.items()}


def _centroids(sh):
    return sh.nodes[sh.tris].mean(axis=1)


def resistivity(res, nets, alpha, t_sigma=T_SIGMA):
    """Each network's conductivity factor, 1 / (1 + alpha (T - t_sigma)), at a
    thermal Result's temperature: [(sheets {layer: (M,)}, vias [one each])],
    one for each Network of `nets`; and the triangles' temperatures {id(net):
    {layer: (M,) C}} (to compare passes)."""
    out, temps = [], {}
    layers = {l.name for l in res.problem.layers}
    for nw in nets:
        sheets, T = {}, {}
        for lay, sh in nw.sheets.items():
            if lay not in layers or not len(sh.tris):
                continue
            c = _centroids(sh)
            t = res.sample(lay, c[:, 0], c[:, 1])
            t = np.where(np.isfinite(t), t, res.problem.ambient)
            T[lay] = t
            sheets[lay] = 1.0 / (1.0 + alpha * (t - t_sigma))
        vias = []
        for v, segs in zip(nw.vias, nw.via_segs):
            ts = [res.sample(l, [v['x']], [v['y']])[0]
                  for la, lb, *_ in segs for l in (la, lb) if l in layers]
            ts = [t for t in ts if np.isfinite(t)]
            t = float(np.mean(ts)) if ts else res.problem.ambient
            vias.append(1.0 / (1.0 + alpha * (t - t_sigma)))
        out.append((sheets, vias))
        temps[id(nw)] = T
    return out, temps


def spots(problem, dcs, alpha=0.0, t_sigma=T_SIGMA, at=None, name='copper'):
    """The DC results' (dcs: [DCResult]) copper loss as the problem's spots
    (Problem.heat_points, in place of any of that name). With alpha and a
    thermal Result `at` (the temperature the loss was solved at), each point's
    loss is referred to the ambient with its self-heating slope: W' (1 + alpha
    (T_a - t_sigma)) and alpha W', W' = W / (1 + alpha (T - t_sigma)), so that
    it follows the copper's resistance about this current pattern (the
    transient's). Returns the total W (at the ambient)."""
    problem.spots = [s for s in problem.spots if s.name != name]
    total = 0.0
    amb = problem.ambient
    layers = {l.name for l in problem.layers}
    for r in dcs:
        for lay, (x, y, W, _) in copper_heat(r).items():
            slope = None
            if alpha and at is not None and lay in layers:
                T = at.sample(lay, x, y)
                T = np.where(np.isfinite(T), T, amb)
                base = W / (1.0 + alpha * (T - t_sigma))
                W = base * (1.0 + alpha * (amb - t_sigma))
                slope = alpha * base
            problem.heat_points(name, lay, x, y, W, slope)
            total += float(W.sum())
    return total


def iterate(nets, solve_dc, problem, alpha=ALPHA, t_sigma=T_SIGMA, pitch=0.5,
            passes=PASSES, tolerance=TOLERANCE, log=say, prepare=None):
    """The DC networks (nets: [pdn.dc.Network]) and the problem's temperature
    (thermal.Problem: the board's, with the parts' losses) solved together:
    solve_dc() -> (answer, [DCResult]) solves the networks at their present
    conductivity, answer a dict with 'loads' ({ref: dict(drop)}). Returns
    dict(cold, hot: the answers at t_sigma and at the temperature, cold_dc,
    hot_dc: their DCResults, thermal: the grid's Result with the copper's
    heat, grid, passes: [dict(largest_change_K, hottest_C, loss_W,
    worst_drop_V)], settled, previous: the Result hot was solved at (None:
    at t_sigma), seconds). prepare(cold answer): called before the thermal
    system is built (the series parts' losses into the problem)."""
    from .thermal.grid import build
    t0 = time.perf_counter()
    cold, cold_dc = solve_dc()
    t_dc = time.perf_counter() - t0
    # the copper's own loss, its points placed once the grid is built
    if prepare is not None:
        prepare(cold)
    spots(problem, cold_dc)
    g = build(problem, pitch)
    t_build = g.seconds['build']
    hist, last, ans, dcs, previous = [], None, cold, cold_dc, None
    settled, change = False, float('inf')
    seconds = dict(dc=t_dc, thermal_build=t_build, thermal_solve=0.0,
                   resistivity=0.0)
    for k in range(max(1, passes)):
        t = time.perf_counter()
        x = g.steady()
        res = g.result(x, dict(build=t_build, solve=0.0))
        seconds['thermal_solve'] += time.perf_counter() - t
        t = time.perf_counter()
        scale, temps = resistivity(res, nets, alpha, t_sigma)
        seconds['resistivity'] += time.perf_counter() - t
        now = {(id(nw), lay): v for nw in nets
               for lay, v in temps[id(nw)].items()}
        if last is not None:
            change = max((float(np.max(np.abs(v - last[key])))
                          for key, v in now.items() if key in last),
                         default=0.0)
        hist.append(dict(
            hottest_C=res.hottest, loss_W=float(sum(r.loss for r in dcs)),
            worst_drop_V=max((l['drop'] for l in ans['loads'].values()),
                             default=0.0),
            largest_change_K=(None if last is None else change)))
        log(f"pass {k + 1}: hottest copper {res.hottest:.2f} C, copper loss "
            f"{hist[-1]['loss_W'] * 1e3:.3g} mW"
            + ('' if last is None else f", moved by up to {change:.3g} K"))
        if not alpha or (last is not None and change <= tolerance):
            settled = True
            break
        if k == passes - 1:
            break
        last, previous = now, res
        t = time.perf_counter()
        for nw, (sheets, vias) in zip(nets, scale):
            nw.conductivity(sheets, vias)
        ans, dcs = solve_dc()
        seconds['dc'] += time.perf_counter() - t
        spots(problem, dcs)
        g.place(problem.spots)
    if alpha and not settled:
        log(f"not settled in {passes} passes (still moving by {change:.3g} K)")
    res.seconds = dict(build=t_build, solve=seconds['thermal_solve'])
    seconds['total'] = time.perf_counter() - t0
    return dict(cold=cold, hot=ans, cold_dc=cold_dc, hot_dc=dcs, thermal=res,
                grid=g, passes=hist, settled=settled, previous=previous,
                seconds=seconds)


def couple(rail, volts, problem, alpha=ALPHA, t_sigma=T_SIGMA, dmax=1.0,
           plating=0.025, pitch=0.5, passes=PASSES, tolerance=TOLERANCE,
           log=say, board=None):
    """The rail's DC drop (pdn.dc.solve_rail's, a rail.Rail at `volts`) and
    the problem's temperature solved together (iterate; the module's
    docstring); cold and hot are solve_rail's answers. The series parts'
    losses (a sense resistor's, a bead's: the cold solve's I^2 R at their
    value) heat the board at their pads (board: the thermal problem's board,
    default the rail's), unless the problem has a loss for them already."""
    from .pdn.dc import rail_networks, solve_networks
    pw, gn, no_gnd = rail_networks(rail, dmax=dmax, plating=plating, log=log)
    first = [True]

    def solve_dc():
        r = solve_networks(rail, volts, pw, gn, no_gnd,
                           log=log if first[0] else (lambda *a: None))
        first[0] = False
        return r, [r['power'], r['ground']]

    added = {}

    def prepare(cold):
        own = {ref: v['loss_W'] for ref, v in cold['series'].items()
               if ref not in problem.powers and v['loss_W'] > 0}
        added.update(own)
        if own:
            problem.dissipate(board or rail.board, own)
            log("the series parts' loss on their pads: " + ', '.join(
                f"{r} {w * 1e3:.3g} mW" for r, w in sorted(own.items())))
    out = iterate((pw, gn), solve_dc, problem, alpha, t_sigma, pitch, passes,
                  tolerance, log, prepare=prepare)
    out['series_heat'] = added
    return out


# ---- the command line -------------------------------------------------------
def parser():
    ap = argparse.ArgumentParser(
        prog='python -m kicad2ems.pcbqs.electrothermal',
        description=__doc__.split('\n\n')[0].strip())
    ap.add_argument('board', nargs='?',
                    help='.kicad_pcb, or a kicad2ems <name>_board.json')
    ap.add_argument('--out', default='electrothermal', help='output folder')
    ap.add_argument('--describe', action='store_true',
                    help='print the options and exit')
    ap.add_argument('--json', action='store_true',
                    help='with --describe: as JSON')
    add_options(ap, OPTIONS)
    return ap


def _pairs(items, flag):
    out = {}
    for it in items:
        ref, _, val = str(it).partition('=')
        try:
            out[ref.strip()] = float(val)
        except ValueError:
            raise ValueError(f"{flag} {it!r}: REF=NUMBER") from None
    return out


def thermal_problem(a, board, losses):
    """thermal.Problem of the board (all its copper) with the parts' losses and
    the air's and the parts' options, as the thermal tool builds it."""
    from .thermal import Problem
    from .thermal.__main__ import part_fields
    from .thermal.problem import forced_film
    from shapely.geometry import shape
    theta, tmax, air = part_fields(board)
    theta.update(_pairs(a.theta, '--theta'))
    tmax.update(_pairs(a.tmax, '--tmax'))
    air.update(_pairs(a.air, '--air'))
    film = (a.film if a.film_bottom is None else a.film_bottom, a.film)
    if a.air_speed:
        length = a.air_length
        if not length:
            x0, y0, x1, y1 = (shape(board['outline']).bounds
                              if board.get('outline') else (0, 0, 100, 100))
            length = max(x1 - x0, y1 - y0)
        film = tuple(forced_film(a.air_speed, length, still=f) for f in film)
    p = Problem.of(board, losses, film=film, ambient=a.ambient, theta=theta,
                   edge_film=a.film_edge)
    p.tmax = tmax
    p.air = {k: v for k, v in air.items() if v > 0}
    held = _pairs(a.hold, '--hold')
    if held:
        p.hold(board, held)
    return p


def run(a):
    from .pdn.cli import parse_loads, read_rail, dc_figures
    from .thermal import transient as TR
    t0 = time.time()
    if not a.rail:
        raise ValueError('--rail is required')
    if a.passes < 1 or a.tolerance <= 0 or a.alpha < 0:
        raise ValueError('--passes at least 1, --tolerance positive, --alpha '
                         'not negative')
    if a.transient < 0:
        raise ValueError('--transient: seconds, positive')
    os.makedirs(a.out, exist_ok=True)
    ground = tuple(x.strip() for x in a.ground.split(',') if x.strip())
    loads, returns, dcr = parse_loads(a)
    heat = _pairs(a.heat, '--heat')
    # the profiles, checked before the (slow) board import
    current = TR.profile(a.current_profile) if a.current_profile else None
    profiles = {}
    for item in a.profile:
        ref, _, shape_ = str(item).partition('=')
        if not ref.strip() or not shape_.strip():
            raise ValueError(f"--profile {item!r}: REF=SHAPE")
        profiles[ref.strip()] = TR.profile(shape_.strip(), watts=True)
    board, rail, found = read_rail(a, ground, loads, returns, dcr, log=say)
    for line in rail.describe():
        say(line.strip())
    # the thermal model's board: all the copper (it all spreads the heat)
    tboard = (board if a.board.endswith('.json') else load_board(a.board))
    losses = {}
    if a.loads_heat:
        losses.update({ref: a.volts * i for ref, i in rail.loads.items()})
    losses.update(heat)
    for ref, pr in profiles.items():  # a file's W: the share of the loss
        if pr.kind == 'file' and pr.watts and ref != 'all':
            peak = float(np.max(np.abs(pr.values))) or 1.0
            losses.setdefault(ref, peak)
            pr.values = pr.values / losses[ref]
    p = thermal_problem(a, tboard, losses)
    say('DC and thermal: meshing and solving')
    r = couple(rail, a.volts, p, alpha=a.alpha, dmax=a.dmax,
               plating=a.plating, pitch=a.size or 0.5, passes=a.passes,
               tolerance=a.tolerance, log=say, board=tboard)
    cold, hot, res = r['cold'], r['hot'], r['thermal']
    th = res.summary()
    hottest = None
    for i, lay in enumerate(p.layers):
        t = res.T[i]
        if np.isfinite(t).any() and (hottest is None
                                     or np.nanmax(t) > hottest['C']):
            j = int(np.nanargmax(t))
            iy, ix = divmod(j, t.shape[1])
            hottest = dict(C=float(np.nanmax(t)), layer=lay.name,
                           x=float(res.x0 + (ix + 0.5) * res.size),
                           y=float(res.y0 + (iy + 0.5) * res.size))
    out = dict(
        format=1, tool='kicad2ems.pcbqs.electrothermal',
        board=os.path.abspath(a.board), rail=a.rail, nets=sorted(rail.nets),
        ground=list(ground), volts=a.volts, alpha=a.alpha,
        sigma_at_C=T_SIGMA, ambient_C=p.ambient,
        settings={o.key: getattr(a, o.key) for o in OPTIONS},
        assumed_loads=found['assumed'], field_loads=sorted(found['fielded']),
        loads={
            ref: dict(I=l['I'], V=l['V'], drop=l['drop'],
                      power_drop=l['power_drop'], ground_rise=l['ground_rise'],
                      drop_cold=cold['loads'][ref]['drop'],
                      power_drop_cold=cold['loads'][ref]['power_drop'],
                      ground_rise_cold=cold['loads'][ref]['ground_rise'])
            for ref, l in hot['loads'].items()
        },
        loss_W=hot['power'].loss + hot['ground'].loss,
        loss_cold_W=cold['power'].loss + cold['ground'].loss,
        series_loss_W=sum(s['loss_W'] for s in hot['series'].values()),
        series=dict(hot['series']), copper=dict(
            hottest_C=res.hottest, rise_K=res.hottest - p.ambient,
            where=hottest, heat_W=float(sum(float(s.W.sum())
                                            for s in p.spots))),
        thermal=th, passes=r['passes'], settled=r['settled'],
        notes=list(dict.fromkeys(list(rail.notes) + found['field_notes'] +
                                 hot['notes'] + p.notes)),
        seconds={k: round(v, 2) for k, v in r['seconds'].items()})
    worst = max(out['loads'], key=lambda k: out['loads'][k]['drop'])
    w = out['loads'][worst]
    say(f"{worst}: drop {w['drop'] * 1e3:.3f} mV at temperature, "
        f"{w['drop_cold'] * 1e3:.3f} mV at 20 C "
        f"({(w['drop'] / w['drop_cold'] - 1) * 100:+.2f} %); hottest copper "
        f"{res.hottest:.1f} C; copper loss {out['loss_W'] * 1e3:.1f} mW "
        f"(cold {out['loss_cold_W'] * 1e3:.1f})")
    figs = []
    try:
        from .thermal.picture import picture
        picture(res, os.path.join(a.out, 'thermal.png'))
        figs.append(dict(file='thermal.png', side='thermal', layer=''))
        figs += dc_figures(board, hot, a.volts, a.out)
    except Exception as e:  # noqa: BLE001 (a figure never fails the run)
        out['notes'].append(f"figures not written: {type(e).__name__}: {e}")
    out['figures'] = figs
    if a.transient:
        t = time.time()
        # each point's loss about the hot pattern, with its self-heating
        spots(p, [hot['power'], hot['ground']], a.alpha, T_SIGMA,
              r['previous'])
        g = r['grid']
        g.place(p.spots)
        pr, currents = dict(profiles), ()
        if current is not None:
            # the copper's loss and the series parts' by the current's square,
            # the loads' own (--loads-heat: V x I) by the current
            pr['copper'] = current
            currents = tuple(ref for ref in r['series_heat']
                             if ref not in pr)
            pr.update({ref: current for ref in currents})
            if a.loads_heat:
                pr.update({ref: current for ref in rail.loads
                           if ref not in heat and ref not in pr})
        try:
            tr = g.transient(a.transient, pr, a.start, log=say,
                             currents=currents)
        except ValueError as e:
            raise ValueError(f"--transient: {e}") from None
        # the frozen pattern's steady state against the passes' (the shift of
        # the current as the copper warms)
        frozen = g.steady()
        tr['frozen_hottest_C'] = float(p.ambient + frozen[:g.n].max())
        tr['seconds'] = round(time.time() - t, 2)
        with open(os.path.join(a.out, 'electrothermal-transient.json'),
                  'w') as fh:
            json.dump(tr, fh, indent=1)
        try:
            from .thermal.picture import heating_picture
            heating_picture(tr, os.path.join(a.out,
                                             'electrothermal-transient.png'))
            figs.append(dict(file='electrothermal-transient.png',
                             side='transient', layer=''))
        except Exception as e:  # noqa: BLE001
            out['notes'].append(
                f"the transient's figure not written: {type(e).__name__}: {e}")
        top = max(tr['hottest_C'])
        out['transient'] = dict(
            file='electrothermal-transient.json', seconds=a.transient,
            start=a.start, hottest_C=top,
            at_s=float(tr['times_s'][int(np.argmax(tr['hottest_C']))]),
            tau_s=tr['tau_s'], steps=tr['steps'], peak=tr['peak'],
            frozen_hottest_C=tr['frozen_hottest_C'],
            profiles=tr['profiles'])
        say(f"transient: hottest copper {top:.1f} C at "
            f"{out['transient']['at_s']:.3g} s; time constants "
            + ', '.join(f"{v:.3g}" for v in tr['tau_s'][:3]) + " s")
    try:  # the maps for the viewer; a failure costs only them
        from .thermal.viewer import write_viewer as thermal_viewer
        from .pdn.viewer import write_viewer as dc_viewer
        refs = ({q.ref for q in rail.source_parts} | set(rail.loads)
                | {s.part.ref for s in rail.series})
        out['viewer'] = dict(
            thermal=thermal_viewer(a.out, tboard, p, res),
            dc=dc_viewer(a.out, board, hot, a.volts, rail.nets, ground, refs))
    except Exception as e:  # noqa: BLE001
        out['notes'].append(
            f"the viewer's maps were not written: {type(e).__name__}: {e}")
    out['seconds']['wall'] = round(time.time() - t0, 1)
    with open(os.path.join(a.out, 'electrothermal.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    say(f"wrote {os.path.join(a.out, 'electrothermal.json')}")
    return out


def main(argv=None):
    a = parser().parse_args(argv)
    if a.describe:
        print_describe(describe(), a.json)
        return 0
    if not a.board:
        parser().error('a board is required')
    try:
        run(a)
    except (ValueError, KeyError) as e:
        print(f'electrothermal: error: {e}', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
