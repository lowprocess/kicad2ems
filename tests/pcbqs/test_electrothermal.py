"""The DC drop with the copper at its temperature (pcbqs.electrothermal)
against closed forms: a sheet heated evenly by its own current (the coupled
fixed point exactly), a trace across a wide board (the two-region fin, and
IPC-2221's external-trace fit as a sanity bound), alpha = 0 as the two tools
apart, the passes' convergence, the transient's frozen current pattern against
the passes, the command line, the report and the study's figures."""
import json
import math

import numpy as np
import pytest
from shapely.geometry import box, mapping

from kicad2ems.pcbqs import electrothermal as ET
from kicad2ems.pcbqs.pdn import dc
from kicad2ems.pcbqs.thermal import Problem, solve
from test_pdn_rail import board as rail_board

SIG = 5.8e7
quiet = lambda *a: None


def sheet_board(w, h, cu, t=0.035, thick=1.6):
    return dict(
        stack=dict(layers=[dict(name='d', z0=0, z1=thick, er=4.3, tand=0,
                                kind='diel')],
                   copper=[dict(name='F.Cu', z=thick, t=t)], thickness=thick),
        copper={'F.Cu': [dict(net='P', polygon=mapping(g)) for g in cu]},
        vias=[], outline=mapping(box(0, 0, w, h)))


def one_net(b, terms, I, dmax):
    """A one-net network from 'src' (1 V) to 'L' (drawing I) and its solve
    for iterate."""
    nw = dc.Network(b, 'P', {k: [('F.Cu', g)] for k, g in terms.items()},
                    dmax=dmax, log=quiet, fine=list(terms))

    def solve_dc():
        r = nw.solve({'src': 1.0}, {'L': -I})
        return dict(loads={'L': dict(drop=1.0 - r.terminals['L'])}), [r]
    return nw, solve_dc


def test_a_sheet_heated_by_its_own_current_is_the_coupled_closed_form():
    """60 x 20 mm of 35 um copper over 1.6 mm of laminate, 25 A from one end
    to the other, the edges insulated: the heat is even, the temperature too,
    T - Ta = P0 (1 + a (Ta - 20)) / (H A - a P0) and the drop I R0 (1 + a (T -
    20))."""
    W, L, I, a, Ta = 20.0, 60.0, 25.0, ET.ALPHA, 25.0
    b = sheet_board(L, W, [box(0, 0, L, W)])
    nw, solve_dc = one_net(b, dict(src=box(0, 0, 0.5, W),
                                   L=box(L - 0.5, 0, L, W)), I, 1.0)
    p = Problem.of(b, film=10.0, edge_film=0.0, ambient=Ta)
    r = ET.iterate([nw], solve_dc, p, alpha=a, pitch=0.5, log=quiet)
    H, A = 20.0, L * W * 1e-6
    R0 = (L - 1.0) * 1e-3 / (SIG * 35e-6 * W * 1e-3)   # between the pads
    P0 = I * I * R0
    T = Ta + P0 * (1 + a * (Ta - 20)) / (H * A - a * P0)
    res = r['thermal']
    assert np.nanmean(res.T[0]) == pytest.approx(T, abs=0.02)
    assert res.hottest - np.nanmin(res.T[0]) < 0.6       # (the pads unheated)
    assert r['hot']['loads']['L']['drop'] == pytest.approx(
        I * R0 * (1 + a * (T - 20)), rel=2e-4)
    assert r['cold']['loads']['L']['drop'] == pytest.approx(I * R0, rel=1e-4)
    # the passes: each move a fixed share of the last (the loop's gain, a P0 /
    # (H A) / (1 + a (T - 20)) ~ 0.15), settled within the tolerance
    moves = [q['largest_change_K'] for q in r['passes'][1:]]
    gain = a * (T - Ta) / (1 + a * (T - 20))
    assert all(m2 < 1.2 * gain * m1 for m1, m2 in zip(moves, moves[1:]))
    assert r['settled'] and moves[-1] <= ET.TOLERANCE
    # one pass: not settled, and said
    p = Problem.of(b, film=10.0, edge_film=0.0, ambient=Ta)
    nw, solve_dc = one_net(b, dict(src=box(0, 0, 0.5, W),
                                   L=box(L - 0.5, 0, L, W)), I, 1.0)
    assert not ET.iterate([nw], solve_dc, p, alpha=a, passes=1,
                          log=quiet)['settled']


def test_a_trace_is_the_two_region_fin_and_near_ipc_2221():
    """A 1 mm trace along a 60 mm wide plane-less board: across it the heat
    spreads as a fin, the strip's own copper nearly isothermal; T - Ta =
    q0 F (1 + a (Ta - 20)) / (1 - a q0 F), F the strip's mean rise per W/m.
    IPC-2221's external-trace fit (I = 0.048 dT^0.44 A^0.725, A in mil^2) is
    a sanity bound only: a factor of two."""
    w, L, B, I, a, Ta = 1.0, 60.0, 60.0, 2.4, ET.ALPHA, 25.0
    b = sheet_board(L, B, [box(0, B / 2 - w / 2, L, B / 2 + w / 2)])
    nw, solve_dc = one_net(
        b, dict(src=box(0, B / 2 - w / 2, 0.5, B / 2 + w / 2),
                L=box(L - 0.5, B / 2 - w / 2, L, B / 2 + w / 2)), I, 0.25)
    p = Problem.of(b, film=10.0, edge_film=0.0, ambient=Ta)
    r = ET.iterate([nw], solve_dc, p, alpha=a, pitch=0.25, log=quiet)
    H = 20.0
    Gs = p.layers[0].laminate                   # W/K a square, the laminate
    Gc = Gs + 390.0 * 35e-6                     # with the trace's copper
    lc = math.sqrt(Gc / H)
    u, rr = w * 1e-3 / (2 * lc), math.sqrt(Gs / Gc)
    flat = 1.0 / (w * 1e-3 * H)                 # K per W/m, no spreading
    edge = flat / (1 + rr / math.tanh(u))       # at the strip's edges
    F = flat - edge * rr / math.sinh(u) * math.sinh(u) / u
    q0 = I * I / (SIG * 35e-6 * w * 1e-3)       # W/m at 20 C
    rise = q0 * F * (1 + a * (Ta - 20)) / (1 - a * q0 * F)
    ys = np.linspace(B / 2 - w / 2 + 0.05, B / 2 + w / 2 - 0.05, 9)
    got = np.nanmean(r['thermal'].sample('F.Cu', np.full(9, L / 2), ys)) - Ta
    assert got == pytest.approx(rise, rel=0.01)
    mil2 = (w / 0.0254) * (0.035 / 0.0254)
    ipc = (I / (0.048 * mil2 ** 0.725)) ** (1 / 0.44)
    assert 0.5 < got / ipc < 2.0


def test_alpha_zero_is_the_two_tools_apart():
    """--alpha 0: the drop is pdn's at 20 C to the last bit, the temperature
    the thermal tool's of that drop's loss and the parts'."""
    from kicad2ems.pcbqs.pdn.dc import solve_rail
    from kicad2ems.pcbqs.pdn.rail import Rail
    b = rail_board()
    rail = Rail(b, 'P', ground=('GND',), loads={'U1': 6.0, 'U2': 1.0})
    p = Problem.of(b, {'U3': 1.0})
    r = ET.couple(rail, 3.3, p, alpha=0.0, dmax=1.0, log=quiet)
    alone = solve_rail(rail, 3.3, dmax=1.0, log=quiet)
    for ref in ('U1', 'U2'):
        assert r['hot']['loads'][ref]['drop'] == alone['loads'][ref]['drop']
        assert r['cold']['loads'][ref]['drop'] == alone['loads'][ref]['drop']
    assert len(r['passes']) == 1 and r['settled']
    # the thermal tool with the same heat: the parts', the series parts', the
    # copper's
    q = Problem.of(b, {'U3': 1.0, **{k: v['loss_W']
                                     for k, v in alone['series'].items()}})
    ET.spots(q, [alone['power'], alone['ground']])
    th = solve(q, 0.5, log=quiet)
    assert np.allclose(r['thermal'].T, th.T, equal_nan=True, atol=1e-9)
    assert r['thermal'].hottest == pytest.approx(th.hottest, abs=1e-9)


def test_the_transient_s_frozen_pattern_ends_where_the_passes_do():
    """The transient's copper loss follows each point's temperature about the
    hot current pattern: its steady state is the passes' to within the
    pattern's own shift as the copper warms (a few mK here)."""
    b = rail_board()
    from kicad2ems.pcbqs.pdn.rail import Rail
    rail = Rail(b, 'P', ground=('GND',), loads={'U1': 8.0, 'U2': 1.0})
    p = Problem.of(b, {'U3': 1.0})
    r = ET.couple(rail, 3.3, p, dmax=1.0, log=quiet)
    ET.spots(p, [r['hot']['power'], r['hot']['ground']], ET.ALPHA,
             ET.T_SIGMA, r['previous'])
    g = r['grid']
    g.place(p.spots)
    frozen = g.steady()
    assert (p.ambient + frozen[:g.n].max()
            == pytest.approx(r['thermal'].hottest, abs=0.02))
    # and run long enough, the transient ends there
    tr = g.transient(3000.0, log=quiet)
    assert tr['hottest_C'][-1] == pytest.approx(r['thermal'].hottest, abs=0.03)
    assert tr['copper_W'][-1] == pytest.approx(
        r['hot']['power'].loss + r['hot']['ground'].loss, rel=2e-3)


def test_a_runaway_has_no_steady_temperature():
    from kicad2ems.pcbqs.thermal.grid import build
    p = Problem.of(sheet_board(20, 20, [box(0, 0, 20, 20)]), film=10.0,
                   edge_film=0.0)
    xs, ys = np.meshgrid(np.arange(0.5, 20, 1.0), np.arange(0.5, 20, 1.0))
    n = xs.size
    # dq/dT above the films' 2 x 10 W/m2K over the board
    p.heat_points('copper', 'F.Cu', xs, ys, np.full(n, 0.01),
                  np.full(n, 2.5 * 20 * 0.02 ** 2 / n))
    with pytest.raises(ValueError, match='runaway'):
        build(p, 1.0).steady()


def test_the_command_line_the_report_and_the_figures(tmp_path):
    from kicad2ems.core.analyses import registry
    from kicad2ems.pcbqs import reports as R
    path = tmp_path / 'b_board.json'
    path.write_text(json.dumps(rail_board()))
    out = tmp_path / 'et'
    assert ET.main([str(path), '--rail', 'P', '--volts', '3.3', '--load',
                    'U1=6', '--load', 'U2=1', '--heat', 'U3=1', '--dmax', '1',
                    '--transient', '300', '--current-profile',
                    'pulse:20,40@10', '--out', str(out)]) == 0
    r = json.loads((out / 'electrothermal.json').read_text())
    for ref in ('U1', 'U2'):
        l = r['loads'][ref]
        assert l['drop'] > l['drop_cold'] > 0
    assert (r['settled'] and r['loss_W'] > r['loss_cold_W']
            and r['copper']['hottest_C'] > r['ambient_C'])
    # the series parts' loss heats their pads
    assert {'R1', 'FB1'} <= set(r['thermal']['parts'])
    tr = json.loads((out / 'electrothermal-transient.json').read_text())
    assert (tr['profiles']['copper']['kind'] == 'pulse'
            and tr['profiles']['R1']['kind'] == 'pulse'
            and r['transient']['hottest_C'] <= r['copper']['hottest_C'] + 0.1)
    for f in ('thermal.png', 'electrothermal-transient.png',
              'thermal_board.json', 'pdn_board.json'):
        assert (out / f).is_file()
    rep = R.report('electrothermal', str(out))
    assert rep['summary'].startswith('Electrothermal, rail P: worst DC drop')
    titles = [s['title'] for s in rep['sections']]
    assert {'Loads at DC', 'Parts', 'Passes', 'Over time'} <= set(titles)
    (cs,) = R.curves('electrothermal', str(out))
    assert cs['curves'][0]['name'] == 'hottest copper'
    a = registry()['electrothermal']
    figs = {f['name']: f['value'] for f in a.figures(str(out))}
    assert (figs['drop U1'] == pytest.approx(
        r['loads']['U1']['power_drop'] * 1e3)
            and figs['drop cold U1'] < figs['drop U1']
            and figs['hottest'] == pytest.approx(r['copper']['hottest_C']))
    assert a.report(str(out)) == rep and a.provides == ('electrothermal', 'dc')
    cmd = a.command(dict(rail='P', load=['U1=6'], transient='60'), str(path),
                    str(tmp_path / 'x'))
    assert cmd[2] == 'kicad2ems.pcbqs.electrothermal' and '--transient' in cmd
    with pytest.raises(ValueError, match='rail'):
        a.command(dict(load=['U1=6']), str(path), 'o')
    assert ET.main([str(path), '--rail', 'P', '--load', 'U1=1', '--passes',
                    '0', '--out', str(tmp_path / 'bad')]) == 2
