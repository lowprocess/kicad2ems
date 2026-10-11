"""Parity: each solver's native model read back against the board, minus what the solver declared. Every
difference must be declared; a deliberately undeclared change (a via moved 0.1 mm) fails."""
import os
import pytest
from kicad2ems.cli import settings_from_argv
from kicad2ems.pipeline import Job
from kicad2ems.solvers import registry
from kicad2ems.parity import check


def _prepared(tmp_path, base, *args):
    """A job with its board, line model and every solver prepared (openEMS: the mesh of a preview)."""
    s, _ = settings_from_argv([base + '.kicad_pcb', '--pos', base + '-top.pos', '--preview', '--mom',
                               '--out', str(tmp_path), *args])
    j = Job(s, echo=lambda *a: None)
    os.makedirs(s.out, exist_ok=True)
    j.model()
    j.lines()
    return j, {n: registry()[n].prepare(j) for n in ('openems', 'mom25d')}


CASES = dict(transition=('demo', ['--nets', 'RF,GND', '--ground', 'B.Cu']),           # snapped and blind vias
             lowpass=('lowpass', []),                                                # parts with ESR / ESL
             launch=('launch', ['--edge-walls', 'all']))                             # edge walls, inner planes


@pytest.mark.parametrize('case', list(CASES))
def test_every_difference_is_declared(case, boards, tmp_path):
    pytest.importorskip('triangle')
    name, args = CASES[case]
    j, prep = _prepared(tmp_path, boards[name], *args)
    for n in ('openems', 'mom25d'):
        sv = registry()[n]
        devs = sv.deviations(j, prep[n])
        assert check(j.m, sv.readback(j, prep[n]), devs) == [], n
    seen = {d.feature for d in registry()['openems'].deviations(j, prep['openems'])}
    if case == 'transition':
        assert 'vias' in seen                                       # openEMS snaps the stitching vias
    if case == 'lowpass':
        assert 'parts' in seen                                      # and splits the parts with ESR


def test_undeclared_changes_fail(boards, tmp_path, monkeypatch):
    pytest.importorskip('triangle')
    name, args = CASES['transition']
    j, prep = _prepared(tmp_path, boards[name], *args)
    sv = registry()['openems']
    devs = sv.deviations(j, prep['openems'])
    grid = prep['openems'].grid
    x, y, r, z0, z1 = grid['vias'][0]
    grid['vias'][0] = (x + 0.1, y, r, z0, z1)                       # moved after it was declared: not declared
    problems = check(j.m, sv.readback(j, prep['openems']), devs)
    assert len(problems) == 2 and all(p.startswith('via:') for p in problems)

    from mom25d.board import BoardModel
    real = BoardModel._via
    monkeypatch.setattr(BoardModel, '_via', lambda self, x, *a, **k: real(self, x + 0.1 if x > 0 else x, *a, **k))
    sv = registry()['mom25d']
    problems = check(j.m, sv.readback(j, prep['mom25d']), sv.deviations(j, prep['mom25d']))
    assert problems and all(p.startswith('via:') for p in problems)


def test_a_dropped_declaration_fails(boards, tmp_path):
    pytest.importorskip('triangle')
    j, prep = _prepared(tmp_path, boards['lowpass'])
    for n in ('openems', 'mom25d'):
        sv = registry()[n]
        devs = [d for d in sv.deviations(j, prep[n]) if not d.item.startswith('part:')]   # parts left unsaid
        problems = check(j.m, sv.readback(j, prep[n]), devs)
        assert any(p.startswith(('part', 'copper on F.Cu')) for p in problems), (n, problems)   # mom25d: the strips
