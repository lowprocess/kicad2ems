"""
The crosstalk question (kicad2ems.core.si): the traces' ends and ports, NEXT and
FEXT from S-parameters over frequency and for an edge, the layout's coupled
stretches, and the question as the study and its report have it.
"""
import json
import os
import types

import numpy as np
import pytest

from kicad2ems.core import si
from k2e_fixtures import ROOT

# three traces D0, D1, D2 (the session's rf_examples)
BUS = os.path.join(ROOT, 'examples', 'rf_bus.kicad_pcb')


def test_a_trace_s_ends_are_its_tracks_free_ends_and_ports_go_there():
    from kicad2ems.core.importers.kicad import KiCadBoard
    kb = KiCadBoard(BUS)
    ends = {n: si.trace_ends(kb, n) for n in ('D0', 'D1', 'D2')}
    for n, (a, b) in ends.items():
        assert ({round(a['x']), round(b['x'])} == {1, 39}
                and a['layer'] == 'F.Cu' and a['pad'] is None)
        # the port: just in from it
        assert (37.5 < a['length'] < 39
                and abs(a['at'][0] - a['x']) <= si.INSET + 1e-9)
    with pytest.raises(ValueError, match='no tracks'):
        si.trace_ends(kb, 'NOPE')
    order = si.order_ends(ends)
    # one side: near
    assert all(
        round(near['x']) == round(order['D0'][0]['x'])
        for near, _ in order.values())
    specs, names = si.port_specs(order)
    assert (list(names.values()) == [(n, e)
                                     for n in ('D0', 'D1', 'D2')
                                     for e in ('near', 'far')])
    assert (specs[0].startswith('P1@') and specs[0].endswith(',F.Cu')
            and len(specs) == 6)
    # a pad at an end is the port's place, and a part named near decides which
    # end is the near one
    a = dict(x=0, y=0, layer='F.Cu', pad=('U1', '3'), at=(0.3, 0), length=10.0)
    b = dict(x=10, y=0, layer='F.Cu', pad=('J2', '1'), at=(9.7, 0), length=10.0)
    o = si.order_ends(dict(A=(a, b)), near=['j2'])
    assert o['A'][0] is b and si.port_specs(o)[0] == ['P1=J2.1', 'P2=U1.3']


def test_an_edge_through_an_ideal_backward_coupler():
    """H = k (1 - exp(-2 j w Td)): a pulse of height k lasting 2 Td; an edge
    slower than 2 Td reaches only its share of it."""
    f = np.linspace(25e6, 10e9, 400)
    k, td = 0.05, 200e-12
    H = k * (1 - np.exp(-2j * 2 * np.pi * f * td))
    t, y = si.edge_response(f, H, 100e-12)  # ramp 125 ps < 400 ps: saturated
    assert abs(y).max() == pytest.approx(k, rel=0.05)
    wide = t[np.abs(y) > 0.5 * k]
    assert wide[-1] - wide[0] == pytest.approx(2 * td, rel=0.1)
    t, y = si.edge_response(f, H, 800e-12)  # ramp 1 ns: 2 Td / ramp = 0.4 of it
    assert abs(y).max() == pytest.approx(0.4 * k, rel=0.08)


def test_renormalised_is_the_impedance_matrix_s_at_the_new_reference():
    rng = np.random.default_rng(0)
    n = 4
    Z = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    Z = Z + Z.T + 60 * np.eye(n)
    s_of = lambda z0: (Z / z0 - np.eye(n)) @ np.linalg.inv(Z / z0 + np.eye(n))
    assert (
        np.abs(si.renormalised(s_of(50.0)[None], 50.0, 75.0)[0]
               - s_of(75.0)).max() < 1e-12)
    assert si.renormalised(s_of(50.0)[None], [50.0] * n, 50.0)[0] is not None


def test_side_by_side_traces_are_found_and_a_pair_s_own_legs_left_out():
    from kicad2ems.core.importers.kicad import KiCadBoard
    rows = si.find_pairs(KiCadBoard(BUS))
    near = {tuple(r['nets']): r for r in rows}
    assert ('D0', 'D1') in near and ('D1', 'D2') in near
    r = near[('D0', 'D1')]
    # (and some of the fan-out)
    assert (29.0 < r['length_mm'] < 39.0
            and r['gap_mm'] == pytest.approx(0.2, abs=0.01))
    assert (si._legs('/A/TX_P', '/A/TX_N') and si._legs('D+', 'D-')
            and not si._legs('D0', 'D1'))
    assert not si._legs('CLK_P', 'DAT_N')


@pytest.fixture(scope='module')
def result(tmp_path_factory):
    out = tmp_path_factory.mktemp('xt')
    a = si.parser().parse_args([
        BUS, '--nets', 'D0,D1,D2', '--ground', 'In1.Cu', '--limit', '3',
        '--out',
        str(out)
    ])
    return si.run(a, echo=lambda *x: None), str(out)


def test_crosstalk_of_three_traces_side_by_side(result):
    d, out = result
    assert (os.path.isfile(os.path.join(out, si.RESULT))
            and d['nets'] == ['D0', 'D1', 'D2'])
    pairs = {(r['aggressor'], r['victim']): r
             for r in d['solved'][d['answer']]['pairs']}
    assert len(pairs) == 6
    near, far = pairs[('D0', 'D1')], pairs[('D0', 'D2')]
    # microstrip
    assert (4.5 < near['next_pct'] < 7.5 and 5.0 < near['fext_pct'] < 8.5
            and near['fext_sign'] == -1)
    # the next trace but one: far less
    assert far['next_pct'] < 0.4 * near['next_pct']
    # over frequency NEXT peaks at twice the edge's flat top (a quarter wave:
    # both ends' reflections in phase)
    assert (near['next_peak_db'] == pytest.approx(
        20 * np.log10(2 * near['next_pct'] / 100), abs=1.5))
    # the waveform: the edge's stretch, not the period
    assert near['next_t_ns'][-1] < 3.0
    mid = next(v for v in d['solved'][d['answer']]['victims']
               if v['victim'] == 'D1')
    assert (mid['next_pct'] == pytest.approx(2 * near['next_pct'], rel=0.02)
            and len(mid['aggressors']) == 2)
    w = d['worst']
    assert (w['ok'] is False and w['value_pct'] == pytest.approx(
        max(w['next_pct'], w['fext_pct'])))


def test_the_layout_s_coupled_stretches_explain_it(result):
    d, _ = result
    secs = {tuple(s['nets']): s for s in d['sections']}
    assert set(secs) == {('D0', 'D1'), ('D1', 'D2'), ('D0', 'D2')}
    s = secs[('D0', 'D1')]
    assert (s['length_mm'] == pytest.approx(30.0)
            and s['gap_mm'] == pytest.approx(0.2, abs=0.01) and s['saturated'])
    # microstrip: more inductive than capacitive
    assert s['kl'] > s['kc'] > 0 and s['fext_pct'] < 0
    pairs = {(r['aggressor'], r['victim']): r
             for r in d['solved'][d['answer']]['pairs']}
    # the estimate: near it
    assert (s['next_pct'] == pytest.approx(pairs[('D0', 'D1')]['next_pct'],
                                           rel=0.3))
    assert (abs(s['fext_pct']) == pytest.approx(pairs[('D0', 'D1')]['fext_pct'],
                                                rel=0.3))
    gaps = [x['gap_mm'] for x in s['wider']]
    assert gaps == pytest.approx([f * s['gap_mm'] for f in si.GAPS])
    assert (s['next_pct'] > s['wider'][0]['next_pct'] >
            s['wider'][1]['next_pct'] > s['wider'][2]['next_pct'] > 0)
    assert secs[('D0', 'D2')]['gap_mm'] > 3 * s['gap_mm']


def test_the_sections_solve_each_cut_once_and_widen_the_worst(
        result, monkeypatch):
    """A bus's pairs share their cut's one solve, and only the worst stretches
    are solved at the wider gaps (each pair had been solved four times); a cut
    the solver cannot take is said, not dropped silently."""
    import glob
    from kicad2ems.core.lines import xsection as X
    d, out = result
    lines = json.load(
        open(glob.glob(os.path.join(out, '*_lines.json'))[0], encoding='utf-8'))
    calls, solve = [], X.CrossSection.solve
    monkeypatch.setattr(X.CrossSection, 'solve',
                        lambda self: calls.append(1) or solve(self))
    secs = si.sections(lines, d['nets'], d['rise_ps'] * 1e-12, widen=1)
    # the three pairs' one cut, the worst widened
    assert len(secs) == 3 and len(calls) == 1 + len(si.GAPS)
    assert (len(secs[0]['wider']) == len(si.GAPS) and not secs[1]['wider']
            and not secs[2]['wider'])
    assert secs[0]['next_pct'] == pytest.approx(d['sections'][0]['next_pct'])
    monkeypatch.setattr(X.CrossSection, 'solve', lambda self: 1 / 0)
    notes = []
    assert (si.sections(lines, d['nets'], d['rise_ps'] * 1e-12,
                        notes=notes) == [])
    assert (len(notes) == 3
            and all('left out' in n and 'could not be solved' in n
                    for n in notes))


def test_the_question_as_the_study_has_it(result):
    from kicad2ems.core.analyses import registry
    from kicad2ems.core.study import study_tree, QUESTIONS
    d, out = result
    a = registry()['crosstalk']
    assert a.owns(out) and QUESTIONS['crosstalk'].group == 'Signal integrity'
    group = next(g for g in study_tree() if any(
        r['question'].name == 'crosstalk' for r in g['questions']))
    row = next(r for r in group['questions']
               if r['question'].name == 'crosstalk')
    assert [e.name for e in row['engines']] == ['crosstalk']
    opts = {o['key']: o for o in a.options()}
    assert (opts['nets']['kind'] == 'list'
            and opts['mode']['choices'] == ['both', 'next', 'fext'])
    with pytest.raises(ValueError, match='two nets'):
        a.command(dict(nets=['D0']), BUS, out)
    with pytest.raises(ValueError, match='board'):
        a.command(dict(nets=['D0', 'D1']), None, out)
    cmd = a.command(
        dict(nets='D0\nD1, D2', rise=50.0, limit=3, check='mom25d',
             mode='next'), BUS, out)
    assert (cmd[1:4] == ['-m', 'kicad2ems.core.si', BUS]
            and cmd[cmd.index('--nets') + 1] == 'D0,D1,D2')
    assert (cmd[cmd.index('--solvers') + 1] == 'lines,mom25d'
            and cmd[cmd.index('--mode') + 1] == 'next')
    assert a.tag(dict(nets=['/CM5/A.B', 'D1'])) == 'CM5-A-B_D1'
    assert (a.choices(
        json.dumps(
            [dict(nets=['A', 'B'], layer='F.Cu', length_mm=12.0,
                  gap_mm=0.2)]))[0][1] == dict(nets=['A', 'B']))
    rep = a.report(out)
    titles = [s['title'] for s in rep['sections']]
    assert ('NOT met' in rep['summary']
            and 'Where the layout couples them' in titles)
    assert ('What a wider gap would give' in titles
            and 'Each victim, all its aggressors together' in titles)
    wide = next(s for s in rep['sections']
                if s['title'] == 'What a wider gap would give')
    assert (wide['notes'] and ('would meet' in wide['notes'][0]
                               or 'does not meet' in wide['notes'][0]))
    for s in rep['sections']:  # every table: its rows as wide as its columns
        t = s.get('table')
        assert (t is None or all(
            len(r) == len(t['columns']) == len(t['formats'])
            for r in t['rows']))
    sets = a.curves(out)
    assert ([s['title'].split(' (')[0] for s in sets][:3] == [
        "The edge at the victims' ends", 'NEXT over frequency',
        'FEXT over frequency'
    ])
    assert (any(c['role'] == 'limit' for c in sets[0]['curves']) and all(
        len(c['x']) == len(c['y']) for s in sets for c in s['curves']))
    figs = {f['name']: f for f in a.figures(out)}
    assert (figs['NEXT D1']['unit'] == '%'
            and figs['NEXT D1']['value'] > figs['NEXT D0']['value'])
    assert (a.report(os.path.dirname(out)) is None
            and a.curves(os.path.dirname(out)) == [])


def test_the_example_config_is_a_study_of_the_question():
    from kicad2ems.core.study import read_config, plan
    st = read_config(os.path.join(ROOT, 'examples', 'si_bus.toml'))
    assert [s.name for s in plan(st)] == ['crosstalk']


def test_a_net_with_a_zone_is_not_proposed_as_a_trace():
    """A ground or power net with a pour is not a trace to pair, even where a
    track of it runs beside a signal (the zones were looked for as dicts in the
    board's shapes, which are tuples: none was ever left out)."""
    trk = lambda net, y: dict(net=net, layer='F.Cu', width=0.2, pts=[(0.0, y),
                                                                     (20.0, y)])
    kb = types.SimpleNamespace(
        tracks=[trk('D0', 0.0), trk('GND', 0.4),
                trk('D1', 0.8)], zone_nets=set())
    assert ['D0', 'GND'] in [r['nets'] for r in si.find_pairs(kb)]
    kb.zone_nets = {'GND'}
    assert all('GND' not in r['nets'] for r in si.find_pairs(kb))
    from kicad2ems.core.importers.kicad import KiCadBoard
    assert KiCadBoard(BUS).zone_nets == {'GND'}


def test_a_pair_the_line_model_did_not_couple_is_not_judged(
        tmp_path, monkeypatch):
    """Traces the line model puts in no coupled section have no path between
    them: their S terms are not computed (the Touchstone file says so and holds
    0). Their crosstalk is not 0 % and "met": it is not judged."""
    from kicad2ems.core.lines import topology
    # (as traces on two layers, or far apart)
    monkeypatch.setattr(topology, 'COUPLE_H', 0.01)
    a = si.parser().parse_args([
        BUS, '--nets', 'D0,D1', '--ground', 'In1.Cu', '--limit', '3', '--out',
        str(tmp_path)
    ])
    d = si.run(a, echo=lambda *x: None)
    w = d['worst']
    assert w['ok'] is None and w['not_modelled'] == ['D0 to D1', 'D1 to D0']
    assert (si.verdict(w).startswith('not judged')
            and 'not judged' in si.Crosstalk().report(str(tmp_path))['summary'])


def test_the_time_grid_holds_a_long_coupler():
    """A 1 m TEM coupler (C = 0.05, matched: its near-end crosstalk settles at C
    / 2) under a 25 ps edge: on the grid time_grid gives, the edge response
    reaches its 2.5 % plateau; on the fixed 401 points it read 1.92 % (R-E16). A
    short trace keeps 401 points."""
    import numpy as np
    from kicad2ems.core.si import edge_response, time_grid
    C0 = 299792458.0

    def next_peak(fmin, points, L=1.0, er=4.0, C=0.05):
        f = np.linspace(fmin, fmin * (points - 1), points)
        th = 2 * np.pi * f * L * np.sqrt(er) / C0
        S31 = (1j * C * np.sin(th) /
               (np.sqrt(1 - C * C) * np.cos(th) + 1j * np.sin(th)))
        return 100 * np.max(np.abs(edge_response(f, S31, 25e-12)[1]))
    fmin, points = time_grid(40e9, 1000.0, 4.0)
    assert (points > 8000
            and next_peak(fmin, points) == pytest.approx(2.5, rel=0.02))
    assert next_peak(40e9 / 400, 401) < 2.0  # the old grid, short
    assert (time_grid(40e9, 30.0, 4.0)[1] == 401
            and time_grid(40e9, 1e5, 4.0)[1] == 16001)
