"""The board's temperature over time (thermal/transient.py: TR-BDF2 on the
grid's system, each loss by a profile) against closed forms: a lumped RC, a
plate heated all over (one mode: its exact response to any profile), the long-
time limit against the steady solve, the time constants against C / G."""
import json

import numpy as np
import pytest
from scipy.sparse import csc_matrix
from shapely.geometry import box

from kicad2ems.pcbqs.thermal import Problem, solve
from kicad2ems.pcbqs.thermal import transient as TR
from kicad2ems.pcbqs.thermal.grid import build
from kicad2ems.pcbqs.thermal.problem import RHO_C_CU, RHO_C_FR4, Held
from test_thermal_surroundings import EXAMPLES, plate

quiet = lambda *a: None


def test_a_lumped_rc_against_its_closed_form():
    G, C, P = 0.5, 20.0, 3.0         # W/K, J/K, W: tau 40 s
    tau = C / G
    out = TR.run(csc_matrix([[G]]), [C], [('p', np.array([P]), TR.constant(),
                                           False)],
                 10 * tau, lambda x, t: dict(x=float(x[0])), log=quiet)
    t, x = np.array(out['times_s']), np.array(out['values']['x'])
    exact = P / G * (1 - np.exp(-t / tau))
    # second order, steps a fifth of the time since the start
    assert np.abs(x - exact).max() < 5e-4 * P / G
    assert out['steps'] < 120 and out['factors'] < 12
    # a step off and on again, from the steady state
    pr = TR.profile('pulse:30,50,1,0.25@20')
    x0 = np.array([P / G])
    out = TR.run(csc_matrix([[G]]), [C], [('p', np.array([P]), pr, False)],
                 200.0, lambda x, t: dict(x=float(x[0])), start=x0, log=quiet)
    t, x = np.array(out['times_s']), np.array(out['values']['x'])
    want, ex = x0[0], [x0[0]]
    for a, b in zip(t, t[1:]):
        lev = float(pr(0.5 * (a + b)))
        want = lev * P / G + (want - lev * P / G) * np.exp(-(b - a) / tau)
        ex.append(want)
    assert np.abs(x - ex).max() < 2e-3 * P / G


def test_a_self_heating_heat_runs_to_its_closed_form():
    # C x' = q0 (1 + a x) - G x: x -> q0 / (G - a q0), rate (G - a q0) / C
    G, C, q0, a = 0.5, 20.0, 3.0, 0.05
    out = TR.run(csc_matrix([[G]]), [C], [('cu', np.array([q0]),
                                            TR.constant(), True)],
                 400.0, lambda x, t: dict(x=float(x[0])),
                 slope=np.array([a * q0]), slope_heat='cu', log=quiet)
    t, x = np.array(out['times_s']), np.array(out['values']['x'])
    g = G - a * q0
    assert np.abs(x - q0 / g * (1 - np.exp(-t * g / C))).max() < 1e-3 * q0 / g


def test_profiles():
    s = TR.profile('step@10')
    assert (s.before == 0 and float(s(9.9)) == 0 and float(s(10)) == 1
            and list(s.breaks(100)) == [10.0])
    o = TR.profile('off@5')
    assert o.before == 1 and float(o(4)) == 1 and float(o(5)) == 0
    b = TR.profile('step:0.5,2@3')
    assert b.before == 0.5 and float(b(3)) == 2
    p = TR.profile('pulse:2,8@1')
    assert ([float(p(t)) for t in (0.5, 1, 2.9, 3, 10.9, 11)]
            == [0, 1, 1, 0, 0, 1])
    assert list(p.breaks(22)) == [1, 3, 11, 13, 21]
    with pytest.raises(ValueError, match='pulse'):
        TR.profile('pulse:2')
    with pytest.raises(ValueError, match='file of t, value'):
        TR.profile('ramp@3')
    with pytest.raises(ValueError, match='negative'):
        TR.profile('step@-1')


def test_a_profile_from_a_file(tmp_path):
    f = tmp_path / 'p.csv'
    f.write_text('t_s, W\n0, 1\n10, 3\n# done\n20, 0\n')
    p = TR.profile(str(f), watts=True)
    assert (p.kind == 'file' and p.watts and p.before == 1
            and float(p(5)) == pytest.approx(2) and float(p(30)) == 0
            and list(p.breaks(15)) == [10.0])
    f.write_text('0 1\n0 2\n')
    with pytest.raises(ValueError, match='increase'):
        TR.profile(str(f))


def one_sheet(film=10.0):
    p = Problem.of(plate(40, layers=(('F.Cu', 0.0),)), film=film,
                   edge_film=0.0)
    p.heat('all', [('F.Cu', box(0, 0, 40, 40))], 1.0)
    C = 0.04 ** 2 * (1.5e-3 * RHO_C_FR4 + 0.035e-3 * RHO_C_CU)
    G = 0.04 ** 2 * 2 * film
    return p, C, G


def test_the_board_s_time_constant_is_c_over_g():
    p, C, G = one_sheet()
    r = solve(p, 1.0, log=quiet, transient=3000.0)
    tr = r.transient
    assert tr['tau_s'][0] == pytest.approx(C / G, rel=1e-6)
    assert tr['to_63_s']['all'] == pytest.approx(C / G, rel=1e-3)
    assert tr['peak']['all']['final_C'] == pytest.approx(25 + 1 / G, rel=1e-3)


@pytest.mark.parametrize('text,start', [('pulse:20,40@10', 'cold'),
                                        ('off@100', 'steady'),
                                        ('step:0.5,1@50', 'steady')])
def test_a_plate_follows_its_profile_exactly(text, start):
    """A plate heated all over has one mode: its exact response to a profile
    is the lumped RC's, interval by interval."""
    p, C, G = one_sheet()
    pr = TR.profile(text)
    r = solve(p, 1.0, log=quiet, transient=600.0, profiles={'all': pr},
              start=start)
    tr = r.transient
    t = np.array(tr['times_s'])
    rise = np.array(tr['parts']['all']) - 25
    x = pr.before / G if start == 'steady' else 0.0
    ex = [x]
    for a, b in zip(t, t[1:]):
        lev = float(pr(0.5 * (a + b)))
        x = lev / G + (x - lev / G) * np.exp(-(b - a) * G / C)
        ex.append(x)
    assert np.abs(rise - ex).max() < 0.01 * (1 / G)
    assert tr['profiles']['all']['text'] == text and tr['to_63_s'] == {}


def test_the_long_time_limit_is_the_steady_temperature():
    """Two sheets, a part with its own path to the air and a theta, a held
    footprint: after many time constants every part is at its steady
    temperature, and a run from the steady state stays there."""
    p = Problem.of(plate(40), film=10.0, edge_film=5.0)
    p.heat('Q1', [('F.Cu', box(5, 5, 10, 10))], 1.5)
    p.heat('D1', [('F.Cu', box(25, 25, 30, 32))], 0.7)
    p.theta, p.air = {'Q1': 5.0, 'D1': 3.0}, {'Q1': 30.0}
    p.held.append(Held('H1', 0, box(35, 0, 40, 5), 40.0))
    steady = solve(p, 1.0, log=quiet)
    r = solve(p, 1.0, log=quiet, transient=20000.0)
    for name in ('Q1', 'D1'):
        assert (r.transient['peak'][name]['final_C'] ==
                pytest.approx(steady.parts[name]['part_C'], abs=2e-3))
    g = build(p, 1.0)
    tr = g.transient(100.0, start='steady', log=quiet)
    for name in ('Q1', 'D1'):
        c = np.array(tr['parts'][name])
        assert np.abs(c - steady.parts[name]['part_C']).max() < 1e-6


def test_a_profile_names_a_heat_of_the_problem():
    p, _, _ = one_sheet()
    with pytest.raises(ValueError, match='not a heat'):
        solve(p, 1.0, log=quiet, transient=10.0,
              profiles={'U9': TR.profile('step')})


def test_the_command_line_takes_profiles(tmp_path, capsys):
    from kicad2ems.pcbqs.thermal.__main__ import main
    board = str(EXAMPLES / '04_buck.kicad_pcb')
    f = tmp_path / 'd1.csv'
    f.write_text('0 0.2\n100 0.8\n200 0.2\n')
    assert main([board, '--loss', 'L1=0.15', '--profile', f'D1={f}',
                 '--profile', 'L1=pulse:30,30@0', '--transient', '300',
                 '--out', str(tmp_path / 'o')]) == 0
    r = json.loads((tmp_path / 'o' / 'thermal.json').read_text())
    tr = json.loads((tmp_path / 'o' / 'thermal-transient.json').read_text())
    # the file's watts: D1's loss its peak, its profile that over it
    assert r['parts']['D1']['power_W'] == pytest.approx(0.8)
    t = np.array(tr['times_s'])
    assert (r['transient']['profiles']['L1']['kind'] == 'pulse'
            and 100 < tr["peak"]["D1"]["at_s"] < 200
            and t[-1] == pytest.approx(300))
    assert 'peaks at' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([board, '--loss', 'L1=0.15', '--profile', 'L1=step@3', '--out',
              str(tmp_path / 'p')])
