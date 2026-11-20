"""A board's coupling one layer at a time (kicad2ems.core.coupling), on the
DDR-like example (make_rf_examples.py ddr: two byte lanes, lane 0 microstrip
on F.Cu, lane 1 stripline on In2.Cu through vias): the nets by pattern, each
layer's stretches and their order across, the breakouts left out, a layer's
board cut to its own copper and the ground's, the summary's victims (a
differential pair's other leg not an aggressor); and the question end to end on
the line model, in a process of its own (this suite's solvers are stand-ins)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from kicad2ems.core import coupling as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))


@pytest.fixture(scope='module')
def ddr(tmp_path_factory):
    d = tmp_path_factory.mktemp('ddr')
    subprocess.check_call([sys.executable,
                           os.path.join(ROOT, 'examples', 'make_rf_examples.py'),
                           'ddr', str(d / 'rf_ddr')], stdout=subprocess.DEVNULL)
    return str(d / 'rf_ddr.kicad_pcb')


@pytest.fixture(scope='module')
def kb(ddr):
    from kicad2ems.core.importers.kicad import KiCadBoard
    return KiCadBoard(ddr)


def test_nets_by_pattern(kb):
    assert C.select_nets(kb, ['DQ?']) == [f"DQ{k}" for k in range(8)]
    assert C.select_nets(kb, ['DQS*']) == ['DQS0_N', 'DQS0_P', 'DQS1_N',
                                           'DQS1_P']
    # the plane's net is never a trace
    assert C.select_nets(kb, ['*']) == C.select_nets(kb, ['DQ*'])


def test_each_layers_stretches_and_the_breakouts_left_out(kb):
    nets = C.select_nets(kb, ['DQ*'])
    by, left = C.stretches(kb, nets)
    assert set(by) == {'F.Cu', 'In2.Cu'}
    assert set(by['F.Cu']) == {'DQ0', 'DQ1', 'DQ2', 'DQ3', 'DQS0_P', 'DQS0_N'}
    assert set(by['In2.Cu']) == {'DQ4', 'DQ5', 'DQ6', 'DQ7', 'DQS1_P',
                                 'DQS1_N'}
    # lane 1's 2 mm F.Cu breakouts: left out, said
    assert sorted(r['net'] for r in left) == ['DQ4', 'DQ5', 'DQ6', 'DQ7',
                                              'DQS1_N', 'DQS1_P']
    assert all(r['layer'] == 'F.Cu' and r['length_mm'] < 5 for r in left)
    # the stripline stretch ends where it leaves the layer (at the vias)
    a, b = by['In2.Cu']['DQ4']
    assert {round(a['x'], 3), round(b['x'], 3)} == {3.0, 39.0}
    # across the run: neighbours next to each other (the matrices' order)
    assert C.across(by['F.Cu']) in (
        ['DQ0', 'DQ1', 'DQS0_P', 'DQS0_N', 'DQ2', 'DQ3'],
        ['DQ3', 'DQ2', 'DQS0_N', 'DQS0_P', 'DQ1', 'DQ0'])


def test_a_layers_board_is_its_copper_and_the_grounds():
    full = dict(
        ground_nets=['GND'], nets=['GND', 'A', 'B'],
        copper={'F.Cu': [dict(net='A', polygon=1), dict(net='GND', polygon=2)],
                'In1.Cu': [dict(net='GND', polygon=3)],
                'In2.Cu': [dict(net='B', polygon=4), dict(net='A', polygon=5)]},
        vias=[dict(net='GND'), dict(net='A')],
        ports=[dict(name='P1'), dict(name='P2'), dict(name='P3')],
        parts=[dict(name='R1')], notes=[],
        prims=dict(items=[dict(layer='F.Cu', net='A'),
                          dict(layer='In2.Cu', net='A')],
                   tracks=[dict(layer='In2.Cu', net='B')],
                   vias=[dict(net='A'), dict(net='GND')], footprints=[]))
    d = C.layer_board(full, 'In2.Cu', ['B'], {'P3'})
    assert d['copper'] == {'F.Cu': [dict(net='GND', polygon=2)],
                           'In1.Cu': [dict(net='GND', polygon=3)],
                           'In2.Cu': [dict(net='B', polygon=4)]}
    assert d['vias'] == [dict(net='GND')] and d['parts'] == []
    assert [p['name'] for p in d['ports']] == ['P3']
    assert d['nets'] == ['B', 'GND'] and d['prims']['items'] == []
    assert d['prims']['tracks'] == [dict(layer='In2.Cu', net='B')]
    # the full board untouched
    assert len(full['copper']['In2.Cu']) == 2


def test_the_summary_leaves_a_pairs_other_leg_out():
    nets = ['D0', 'CLK_P', 'CLK_N', 'D1']
    N = np.array([[0, 2, 1, 0.5], [2, 0, 9, 2], [1, 9, 0, 2], [0.5, 2, 2, 0]])
    m = dict(nets=nets, next_pct=N.tolist(), fext_pct=(N / 2).tolist())
    vs, ps = C.summary(m, top=2)
    clk = vs[1]
    assert clk['pair_leg'] == 'CLK_N'
    assert [w['aggressor'] for w in clk['worst']] == ['D0', 'D1']
    assert clk['next_sum_pct'] == 4.0           # not the other leg's 9
    assert ('CLK_N', 'CLK_P') not in {(p['aggressor'], p['victim'])
                                      for p in ps}
    w = C.worst([dict(layer='L', victims=vs, pairs=ps)], 'next', limit=3.5)
    assert (w['victim'], w['sum_pct'], w['ok']) == ('D1', 4.5, False)


def test_the_question_end_to_end_on_the_line_model(ddr, tmp_path):
    out = tmp_path / 'cpl'
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.coupling', ddr, '--nets',
         'DQ*', '--solvers', 'lines', '--rise', '100', '--limit', '30',
         '--out', str(out)], capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    res = json.load(open(out / C.RESULT))
    assert res['format'] == C.FORMAT
    layers = {L['layer']: L for L in res['layers']}
    assert set(layers) == {'F.Cu', 'In2.Cu'}
    for name, L in layers.items():
        N, F = (np.asarray(L['matrix'][k]) for k in ('next_pct', 'fext_pct'))
        # four DQ lines and the DQS pair as one channel, in its order across
        assert N.shape == (5, 5) and not np.diag(N).any()
        assert [L['kinds'][c] for c in L['nets']] == [
            'single', 'single', 'pair', 'single', 'single']
        # a neighbour couples more than the line two over
        assert N[0, 1] > 3 * N[0, 2]
        # the pair between two lines, mirror-symmetric: no mode conversion
        (conv,) = L['conversion'].values()
        assert conv['near_pct'] < 0.05 and conv['far_pct'] < 0.05
        assert (out / f"coupling_{C._safe(name)}.png").is_file()
    # stripline (one dielectric): next to no FEXT; microstrip: as much as NEXT
    s = layers['In2.Cu']['victims']
    assert max(v['fext_sum_pct'] for v in s) < 0.2 * max(
        v['next_sum_pct'] for v in s)
    m = layers['F.Cu']['victims']
    assert max(v['fext_sum_pct'] for v in m) > 0.5 * max(
        v['next_sum_pct'] for v in m)
    assert res['worst']['layer'] == 'F.Cu' and res['worst']['ok'] is True
    assert len(res['left_out']) == 6 and res['not_modelled']


def test_channels_and_their_modes():
    ch = C.channels(['DQ0', 'DQS0_N', 'DQS0_P', 'CLK+', 'CLK-'])
    assert [(c['name'], c['kind'], c['legs']) for c in ch] == [
        ('DQ0', 'single', ['DQ0']), ('DQS0', 'pair', ['DQS0_P', 'DQS0_N']),
        ('CLK', 'pair', ['CLK+', 'CLK-'])]
    assert [c['kind'] for c in C.channels(['A_P', 'A_N'], pairs=False)] == [
        'single', 'single']
    # a pair whose legs transmit 1 and 0.5: half the difference to common mode
    names = {'P1': ('A_P', 'near'), 'P2': ('A_P', 'far'),
             'P3': ('A_N', 'near'), 'P4': ('A_N', 'far')}
    S = np.zeros((1, 4, 4))
    S[0, 1, 0] = S[0, 0, 1] = 1.0
    S[0, 3, 2] = S[0, 2, 3] = 0.5
    Sm, rows, zref = C.modal(S, names, C.channels(['A_P', 'A_N']))
    at = {r: i for i, r in enumerate(rows)}
    assert np.isclose(Sm[0, at[('A', 'd', 'far')], at[('A', 'd', 'near')]], 0.75)
    assert np.isclose(Sm[0, at[('A', 'c', 'far')], at[('A', 'd', 'near')]], 0.25)
    assert zref[at[('A', 'd', 'near')]] == 100.0 and zref[at[('A', 'c', 'far')]] == 25.0
