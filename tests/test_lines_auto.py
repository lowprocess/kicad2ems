"""Which of the line model's sections go to the field solver by default (lines.reference.FIELD = 'auto'): those
whose cut holds what their formula leaves out (_beyond_formula), and the setting that chooses (--lines-field)."""
import math, os
from types import SimpleNamespace as NS
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
