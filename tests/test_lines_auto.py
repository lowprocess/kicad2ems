"""Which of the line model's sections go to the field solver by default (lines.reference.FIELD = 'auto'): those
whose cut holds what their formula leaves out (_beyond_formula), and the setting that chooses (--lines-field)."""
import math, os
from types import SimpleNamespace as NS
import numpy as np
import pytest
from kicad2ems.lines import reference as R

INF = math.inf
PLANE = (0.0, True, ((-INF, INF),))                     # a plane across the whole cut, 0.2 mm under the strip
ONE, TWO = {'below': ('B.Cu', 0.2, 'full')}, {'below': ('In2.Cu', 0.2, 'full'), 'above': ('In1.Cu', 0.13, 'full')}


def section(side=(), layers=(PLANE,), xs='microstrip', w=0.3, s=None):
    return NS(cut=(w, 0.2, 0.035, True, tuple(side), tuple(layers), ()), xs=xs, s=s)


def test_the_setting_s_three_values():
    assert [R.field_mode(v) for v in ('auto', 'ALL', ' off ', True, False, 'true', 'false', 'formulas')] == \
        ['auto', 'all', 'off', 'all', 'off', 'all', 'off', 'off']
    assert R.FIELD == 'auto' and R.field_mode() == 'auto'
    with pytest.raises(ValueError, match='auto, all or off'):
        R.field_mode('some')


def test_a_microstrip_over_its_plane_keeps_its_formula():
    assert R._beyond_formula(section(), ONE) is None
    assert R._beyond_formula(section(side=[(-INF, -0.5), (0.5, 2.0)]), ONE) is None        # copper 0.35 mm beside: 1.75 h
    assert R._beyond_formula(section(layers=[(0.0, False, ((-0.6, 5.0),))]), ONE) is None  # the plane 0.45 mm past: 2.25 h
    behind = [PLANE, (-0.4, False, ((-0.1, 0.1),))]                                       # copper behind the plane
    assert R._beyond_formula(section(layers=behind), ONE) is None


def test_what_the_formulas_leave_out():
    why = lambda *a, found=ONE, **k: R._beyond_formula(section(*a, **k), found)
    assert why(layers=[(0.0, False, ((-INF, 0.25),))]) == 'the plane 0.2 mm from it ends 0.10 mm beyond its edge'
    assert why(layers=[(0.0, False, ((-INF, 0.1),))]) == 'the plane 0.2 mm from it ends under it'
    assert why(layers=[(0.0, False, ((0.5, INF),))]) == 'the plane 0.2 mm from it ends under it'      # not under it at all
    assert why(layers=[]) == 'a plane it was classified on is not across its cut'
    between = [(0.1, False, ((0.2, 0.6),)), PLANE]                                        # a trace on a layer between
    assert why(layers=between) == 'copper 0.1 mm below it, 0.05 mm to the side'
    assert why(layers=[(0.1, False, ((-0.1, 0.1),)), PLANE]) == 'copper 0.1 mm below it'
    assert why(layers=[(0.1, False, ((0.5, 0.9),)), PLANE]) is None                       # 0.35 mm to the side: 3.5 dz
    assert why(side=[(0.25, 1.0)]) == 'copper 0.10 mm beside it'                          # nearer than one height
    planes = [PLANE, (0.33, True, ((-INF, INF),))]                                        # 0.13 mm over the strip
    assert why(layers=planes, found=TWO, xs='offset stripline') is None
    assert why(side=[(-INF, -0.35)], layers=planes, found=TWO, xs='offset stripline') == \
        'copper 0.20 mm beside it (between two planes)'                                   # nearer than 2 x 0.13 mm
    assert why(side=[(-INF, -0.45)], layers=planes, found=TWO, xs='offset stripline') is None


def test_a_grounded_cpw_is_solved_as_classified_when_that_is_its_cut():
    gaps = lambda a, b: [(-INF, -0.15 - a), (0.15 + b, INF)]
    assert R._beyond_formula(section(side=gaps(0.15, 0.15), xs='cpwg', s=0.15), ONE) is None
    assert R._beyond_formula(section(side=gaps(0.13, 0.18), xs='cpwg', s=0.155), ONE) is None
    assert R._beyond_formula(section(side=gaps(0.15, 0.6), xs='cpwg', s=0.375), ONE) == \
        'its ground is 0.15 and 0.60 mm away, not 0.38 mm either side'
    assert 'not 0.00 mm' in R._beyond_formula(section(side=gaps(1.5, 2.0), xs='cpwg', s=0.0), ONE)


@pytest.fixture(scope='module')
def transition(boards):
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder
    m = Board(KiCadBoard(boards['demo'] + '.kicad_pcb'), log=lambda *a: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))
    return {mode: builder.build(m, nets=nets, field=mode) for mode in (None, 'off', 'all', True)}


def test_the_default_solves_the_sections_at_the_via_s_antipads(transition):
    auto, off, every = (transition[k].elements for k in (None, 'off', 'all'))
    cuts = [e for e in auto if e.source == 'cut']
    assert len(cuts) == 2 and len([e for e in auto if e.cut is not None]) == 6
    for e in cuts:                                              # the plane under them ends at the antipad
        assert any(n.startswith("solved on the board's cut: the plane 0.2 mm from it ends") for n in e.notes)
        assert any(n.startswith('field solver: Z0') for n in e.notes) and e.z0 != e.z0_formula
    assert not [e for e in off if e.source == 'cut'] and R.FIELD == 'auto'
    assert all(e.source == 'cut' for e in every if e.cut is not None)
    assert [e.source for e in transition[True].elements] == [e.source for e in every]       # the old flag: all
    for a, b, c in zip(auto, off, every):                       # a section is its cut's, or as without the cuts
        if a.cut is not None:
            assert a.z0 == pytest.approx(c.z0 if a.source == 'cut' else b.z0, rel=1e-12)


def test_the_option_and_its_old_switch(tmp_path):
    from kicad2ems.cli import parser
    from kicad2ems.config import build_settings, ConfigError
    args = lambda *a: vars(parser().parse_args(['board.kicad_pcb', *a])).get('lines_field')
    assert args() is None and args('--lines-field') == 'all' and args('--lines-field', 'off') == 'off'
    assert build_settings(dict(board='b.kicad_pcb')).lines_field == 'auto'
    for text, mode in (('true', 'all'), ('false', 'off'), ('"auto"', 'auto'), ('"all"', 'all')):
        cfg = tmp_path / 'b.toml'
        cfg.write_text(f'board = "b.kicad_pcb"\nlines_field = {text}\n')
        assert build_settings({}, str(cfg)).lines_field == mode
    cfg.write_text('board = "b.kicad_pcb"\nlines_field = "some"\n')
    with pytest.raises(ConfigError, match='auto, all or off'):
        build_settings({}, str(cfg))


def test_many_cuts_are_solved_in_worker_processes(monkeypatch):
    """A build's cross-sections go to worker processes when there are many, with the answers of solving them one
    after another; where a process cannot start, or the program is no python, they are solved as before. The
    workers are commands (python -m kicad2ems.lines.cutworker), so a program's main module is not run again."""
    import subprocess, sys
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder
    ex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples')
    m = Board(KiCadBoard(os.path.join(ex, 'rf_radialstub.kicad_pcb')), log=lambda *a: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))
    started, real = [], subprocess.Popen

    def popen(argv, **kw):
        started.append(argv[1:])
        return real(argv, **kw)

    def build(parallel, start=popen, exe=sys.executable):
        monkeypatch.setattr(subprocess, 'Popen', start)
        monkeypatch.setattr(sys, 'executable', exe)
        monkeypatch.setattr(R, 'PARALLEL', parallel)
        monkeypatch.setattr('emsdev.cpu.core_threads', lambda: 2)
        R._solve_cut.cache_clear()
        del started[:]
        lm = builder.build(m, nets=nets, field='all')
        return [(e.z0, e.eeff, e.z0_sheet, e.source) for e in lm.elements], R._solve_cut.cache_info().misses, len(started)

    one, n, procs = build(10 ** 6)
    assert procs == 0 and n >= 16 and all(s == 'cut' for *_, s in one if s)
    two, n2, procs = build(8)
    assert procs == 2 and started[0] == ['-m', 'kicad2ems.lines.cutworker'] and n2 == n and two == one
    assert R.DEFER is None

    def broken(*a, **k):
        raise OSError('no processes here')
    three, n3, _ = build(8, broken)
    assert three == one and n3 == n
    four, n4, procs = build(8, exe='/usr/bin/kicad')            # an embedded interpreter: its host is not asked
    assert four == one and n4 == n and procs == 0


def test_a_worker_answers_until_its_input_closes():
    import pickle, subprocess, sys
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    p = subprocess.Popen([sys.executable, '-m', 'kicad2ems.lines.cutworker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    good = ('cpwg', (0.3, 0.15, 0.2, 4.3, 0.035))
    bad = ('cut', (0.3,))                                       # not a cross-section: None, not a crash
    for items in ([good], [bad, good]):
        pickle.dump(items, p.stdin)
        p.stdin.flush()
        got = pickle.load(p.stdout)
        assert [item for item, _ in got] == items and got[-1][1] == pytest.approx(R._solve_cpwg(*good[1]))
    assert got[0][1] is None
    p.stdin.close()
    assert p.wait(timeout=20) == 0


# ---- coupled groups on the board's cut (topology._group_on_cut) ---------------------------------------------
class _Copper:
    """Reference copper as RefCopper has it (geom per layer), without tiles."""
    def __init__(self, geom):
        self.geom = geom


STACK = NS(cu_z={'F.Cu': 0.2, 'B.Cu': 0.0}, cu_t={'F.Cu': 0.035}, layers=[dict(z0=0.0, z1=0.2, er=4.3, tand=0.02)])
PAIR = [(-0.2, 0.2), (0.2, 0.2)]                        # two 0.2 mm lines 0.2 mm apart: outer edges at +-0.3 mm


def _group(front=(), back=None, legs=PAIR):
    """group_cut's arguments for legs along y over B.Cu (`back`: its copper, the whole plane by default) with
    the F.Cu copper `front` beside them (boxes' x0, x1)."""
    from shapely.geometry import box
    from shapely.ops import unary_union
    geom = {'B.Cu': box(-50, -50, 50, 50) if back is None else unary_union([box(a, -50, b, 50) for a, b in back])}
    if front:
        geom['F.Cu'] = unary_union([box(a, -50, b, 50) for a, b in front])
    return R.group_cut(STACK, _Copper(geom), 'F.Cu', legs, np.zeros(2), np.array([1.0, 0.0]), (0.2,), True)


def test_a_group_s_cut_has_its_legs_and_the_copper_around_them():
    strips, zs, t, up, side, layers, diel = _group(front=[(0.45, 1.0)])
    assert np.allclose(strips, [(-0.3, -0.1), (0.1, 0.3)], atol=3e-3) and strips[0][0] == -strips[1][1]
    assert (zs, t, up) == (0.2, 0.035, True) and diel == ((0.0, 0.2, 4.3, 0.02),)
    assert layers == ((0.0, True, ((-INF, INF),)),)
    assert len(side) == 1 and side[0] == pytest.approx((0.45, 1.0), abs=0.02)   # 0.15 mm from its outer edge
    assert _group(front=[(0.45, 2.0)])[4][0][1] == INF                           # beyond the cut's reach, 1 mm
    three = _group(legs=[(-0.5, 0.2), (0.0, 0.3), (0.45, 0.2)])[0]               # gaps 0.25 and 0.2 mm
    assert np.allclose(three, [(-0.575, -0.375), (-0.125, 0.175), (0.375, 0.575)], atol=5e-3)
    a, b = _group(front=[(0.4501, 2.0)]), _group(front=[(0.4502, 2.0)])          # similar cuts: one solve
    assert R.group_keys(a)[0] == R.group_keys(b)[0]


def test_a_cut_and_its_mirror_image_are_one_solve():
    right, left = _group(front=[(0.45, 2.0)]), _group(front=[(-2.0, -0.45)])
    (k1, f1), (k2, f2) = R.group_keys(right), R.group_keys(left)
    assert k1 == k2 and f1 != f2 and [k[2] for k in k1] == [0.035, 0.0]          # with its thickness, and as sheets
    whole = R.group_keys(_group())
    assert whole[1] is False and whole[0][0][0] == _group()[0]
    assert (f1, f2) == (True, False) and k1[1][4][0][1] < 0                      # as solved: the ground on the left
    C, _ = R._solve_group(*k1[1])
    assert C[0, 0] > 1.02 * C[1, 1] and C[0, 1] == pytest.approx(C[1, 0], rel=1e-6)   # more to ground beside it


def test_which_groups_ideal_planes_are_not_enough_for():
    why = lambda xs='microstrip', hs=(0.2,), **k: R.group_beyond(_group(**k), xs, hs)
    assert why() is None and why(front=[(0.55, 2.0)]) is None                    # copper 0.25 mm beside: 1.25 h
    assert why(front=[(0.45, 2.0)]) == 'copper 0.15 mm beside it'
    assert why(back=[(-50, 0.5)]) == 'the plane 0.2 mm from it ends 0.20 mm beyond its edge'
    assert why(back=[(-50, 0.8)]) is None                                        # 0.5 mm beyond: 2.5 h
    assert why(back=[(-50, -0.05), (0.05, 50)]) == 'the plane 0.2 mm from it ends under it'


WIDE = [(-0.6, 0.2), (0.6, 0.2)]                        # two 0.2 mm lines 1 mm apart


def test_a_guard_trace_between_two_lines_is_in_their_cut():
    plain, guarded = _group(legs=WIDE), _group(front=[(-0.2, 0.2)], legs=WIDE)
    assert plain[4] == () and R.group_beyond(plain, 'microstrip', (0.2,)) is None
    (a, b), = guarded[4]
    assert (a, b) == pytest.approx((-0.2, 0.2), abs=5e-3) and guarded[0] == plain[0]
    assert R.group_beyond(guarded, 'microstrip', (0.2,)) == 'copper 0.40 mm wide between its lines'
    both = _group(front=[(-0.2, 0.2), (0.85, 1.5)], legs=WIDE)             # and ground beside it: the guard is said
    assert len(both[4]) == 2 and R.group_beyond(both, 'microstrip', (0.2,)).endswith('between its lines')
    touching = _group(front=[(-0.5, 0.5)], legs=WIDE)[4]                   # never onto a line: 2 um from each
    assert touching[0][0] > plain[0][0][1] and touching[0][1] < plain[0][1][0]
    k = lambda args: R._solve_group(*R.group_keys(args)[0][1])
    (C0, Ca0), (C, Ca) = k(plain), k(_group(front=[(-0.4, 0.4)], legs=WIDE))   # 0.8 mm of guard, 0.1 mm from each
    assert abs(C[0, 1]) < 0.55 * abs(C0[0, 1]) and abs(Ca[0, 1]) < 0.45 * abs(Ca0[0, 1])   # half the coupling, and less
    assert 1.04 * C0[0, 0] < C[0, 0] < 1.08 * C0[0, 0]


@pytest.fixture(scope='module')
def diffvia():
    """rf_diffvia's pair (a microstrip and a stripline coupled section either side of its vias), as drawn and with
    ground 0.15 mm beside the stripline pair and the plane under the microstrip pair ending 0.1 mm past it."""
    from shapely.geometry import box
    from kicad2ems.board import Board
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.lines import builder
    ex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples')
    m = Board(KiCadBoard(os.path.join(ex, 'rf_diffvia.kicad_pcb')), log=lambda *a: None, need_ports=False)
    nets = sorted({t['net'] for t in m.prims.tracks} - set(m.ground_nets))

    def changed():
        ref = R.RefCopper(m, nets)
        ref.geom['In2.Cu'] = ref.geom['In2.Cu'].union(box(15.0, -5.4065, 22.0, -4.4065))    # beside the stripline pair
        ref.geom['In1.Cu'] = ref.geom['In1.Cu'].difference(box(2.0, -3.5035, 9.0, -1.0))    # under the microstrip pair
        return ref

    def guarded():
        ref = R.RefCopper(m, nets)
        ref.geom['F.Cu'] = ref.geom['F.Cu'].union(box(2.0, -4.04, 9.0, -3.96))              # between the microstrip pair
        return ref
    out = {mode: builder.build(m, nets=nets, field=mode) for mode in ('off', 'auto', 'all')}
    out.update({mode + ' changed': builder.build(m, nets=nets, refcu=changed(), field=mode) for mode in ('off', 'auto', 'all')})
    out['guarded'] = builder.build(m, nets=nets, refcu=guarded())
    out['board'] = m
    return out


def test_a_group_over_whole_planes_stays_on_ideal_planes(diffvia):
    off, auto, every = (diffvia[k].groups for k in ('off', 'auto', 'all'))
    assert len(auto) == 4 and not any(g.get('source') for g in off + auto)
    assert all(g.get('source') == 'cut' for g in every)
    for a, b, c in zip(off, auto, every):                       # the cut's solve of the same section: the same pair
        for p, q, r in zip(a['pairs'], b['pairs'], c['pairs']):
            assert (p['ze'], p['zo']) == (q['ze'], q['zo'])
            assert (r['ze'], r['zo'], r['ze_sheet']) == pytest.approx((p['ze'], p['zo'], p['ze_sheet']), rel=2e-3)
        assert c['C'] == pytest.approx(a['C'], rel=2e-3) and 'cut_keys' not in c and 'cut_keys' not in b
    assert not any('coupled group' in n for g in auto for e, *_ in g['members'] for n in e.notes)


def test_the_default_solves_a_group_on_its_cut_where_the_planes_are_not_ideal(diffvia):
    plain, off, auto, every = (diffvia[k].groups for k in ('auto', 'off changed', 'auto changed', 'all changed'))
    assert [g.get('source') for g in auto] == [None, 'cut', 'cut', None] and not any(g.get('source') for g in off)
    notes = lambda g: sorted({n for e, *_ in g['members'] for n in e.notes if n.startswith('coupled group')})
    assert notes(auto[1]) == ["coupled group solved on the board's cut: copper 0.15 mm beside it (between two planes)"]
    assert notes(auto[2]) == ["coupled group solved on the board's cut: the plane 0.2 mm from it ends 0.10 mm beyond its edge"]
    assert not notes(auto[0]) and not notes(every[1])           # 'all' gives no reason
    strip, micro = auto[1], auto[2]
    C = strip['C']                                              # ground beside one leg: more to ground there
    assert max(C[0, 0], C[1, 1]) > 1.01 * min(C[0, 0], C[1, 1]) and C[0, 1] < 0
    p, q = strip['pairs'][0], plain[1]['pairs'][0]
    assert 0.97 * q['ze'] < p['ze'] < 0.985 * q['ze'] and 0.975 * q['zo'] < p['zo'] < 0.99 * q['zo']   # -2.0, -1.6 %
    p, q = micro['pairs'][0], plain[2]['pairs'][0]              # less plane under it: both modes higher
    assert 1.01 * q['ze'] < p['ze'] < 1.02 * q['ze'] and 1.003 * q['zo'] < p['zo'] < 1.01 * q['zo']    # +1.3, +0.6 %
    for a, b in zip(auto, every):                               # a group is its cut's, or as without the cuts
        ref = b if a.get('source') == 'cut' else off[auto.index(a)]
        assert [(x['ze'], x['zo']) for x in a['pairs']] == [(x['ze'], x['zo']) for x in ref['pairs']]
    legs = [e for e, *_ in strip['members']]                    # the section the GUI draws: the board's cut
    assert [e.coupled['index'] for e in legs] == [0, 1]
    drawn = legs[0].coupled['section'].to_dict()
    assert [c[4] for c in drawn['conductors']] == [False, False, True] and len(drawn['planes']) == 2
    assert drawn['conductors'][2][1] == pytest.approx(drawn['conductors'][0][0] - 0.15, abs=5e-3)
    from kicad2ems.lines import sections
    lm = diffvia['auto changed']
    leg = next(e for e in lm.drawing({})['elements'] if e['kind'] == 'trace' and e['group'] == 1)
    assert sections.solve(leg, 'odd').z0 == pytest.approx(strip['pairs'][0]['zo'], rel=1e-6)
    assert [g['source'] for g in lm.summary()['groups']] == ['planes', 'cut', 'cut', 'planes']
    assert {g['source'] for g in diffvia['all'].summary()['groups']} == {'cut'}


def test_the_line_model_s_answer_follows_the_group_s_cut(diffvia):
    pytest.importorskip('skrf')
    from kicad2ems.solvers.lines.fastsolve import group_s
    f = np.array([1e9, 5e9])
    plain, cut = (group_s(diffvia[k].groups[1], f) for k in ('auto', 'auto changed'))
    assert plain.shape == cut.shape == (2, 4, 4) and np.abs(plain - cut).max() > 0.01


def test_a_guarded_pair_is_a_block_of_its_cut_in_both_circuit_solvers(diffvia, tmp_path):
    """qucsator's own coupled microstrip (MCOUPLED) knows nothing of the copper between the lines: a pair solved on
    the board's cut is the multiconductor block there too."""
    from kicad2ems.solvers.lines.fastsolve import group_s, qucs_netlist
    plain, guarded = diffvia['auto'], diffvia['guarded']
    g, g0 = guarded.groups[2], plain.groups[2]
    assert g.get('source') == 'cut' and [x.get('source') for x in guarded.groups] == [None, None, 'cut', None]
    assert any(n.endswith('copper 0.08 mm wide between its lines') for n in g['members'][0][0].notes)
    assert abs(g['C'][0, 1]) < 0.5 * abs(g0['C'][0, 1])
    f = np.linspace(1e9, 5e9, 5)
    far = lambda S: np.abs(S[:, 3, 0]).max()                    # into the other line's far end
    assert far(group_s(g, f)) < 0.6 * far(group_s(g0, f))
    text = lambda lm: qucs_netlist(lm, diffvia['board'].ports, f, workdir=str(tmp_path))[0]
    assert 'MCOUPLED:G2 ' in text(plain) and 'SPfile:G2 ' not in text(plain)
    assert 'SPfile:G2 ' in text(guarded) and 'MCOUPLED:G2 ' not in text(guarded) and 'MCOUPLED:G0 ' in text(guarded)


def test_many_group_cuts_are_solved_in_worker_processes(monkeypatch):
    import subprocess
    keys = [k for d in np.linspace(0.35, 1.2, 9) for k in R.group_keys(_group(front=[(d, 2.0)]))[0]]
    keys = list(dict.fromkeys(keys))
    assert len(keys) >= 16
    started, real = [], subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda argv, **kw: started.append(argv[1:]) or real(argv, **kw))
    monkeypatch.setattr(R, 'PARALLEL', 8)
    R._solve_group.cache_clear()
    R.solve_groups(keys[:6], workers=4)                         # too few for a worker: left to the groups
    assert not started and not R._solve_group.got
    R.solve_groups(keys, workers=2)
    assert started == [['-m', 'kicad2ems.lines.cutworker']] * 2 and len(R._solve_group.got) == len(keys)
    there = {k: R._solve_group.got[k] for k in keys[:4]}
    R._solve_group.cache_clear()
    for k, (C, Ca) in there.items():
        here = R._solve_group(*k)
        assert np.array_equal(here[0], C) and np.array_equal(here[1], Ca)
    R.solve_groups(keys[:4], workers=2)                         # solved already: nothing to start
    assert len(started) == 2
