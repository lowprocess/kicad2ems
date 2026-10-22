"""Choosing the solvers of a run (openEMS, mom25d, the line model): settings, the checks before a run,
runs without openEMS, exit codes."""
import json, os
import pytest
from kicad2ems.solvers.openems import simulation
from kicad2ems.cli import settings_from_argv
from kicad2ems.config import build_settings, ConfigError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTER = os.path.join(ROOT, 'examples', 'filter.toml')


def plan(**kw):
    return build_settings(dict(board='b.kicad_pcb', **kw)).solver_plan()


def test_solver_plan():
    assert plan() == (['auto'], ['auto'])                          # the default: auto, as if named
    assert build_settings(dict(board='b'), default_solvers=['openems']).solver_plan()[0] == ['openems']   # kicad2openems
    assert plan(mom=True, fast='skrf') == (['auto', 'mom25d', 'scikit'], ['auto'])   # --mom / --fast add theirs
    assert plan(solvers='mom') == (['mom25d'], ['mom25d'])
    assert plan(solvers='lines,MoM25d', fast='qucsator') == (['mom25d', 'qucs'], ['mom25d', 'qucs'])
    assert plan(solvers=['fdtd', 'line']) == (['openems', 'lines'], ['openems', 'lines'])   # lines: resolved later
    assert plan(solvers='all')[0] == ['openems', 'mom25d', 'lines']
    assert plan(solvers='skrf,qucsator')[0] == ['qucs', 'scikit']                         # both line solvers
    assert plan(solvers='openems', mom=True) == (['openems', 'mom25d'], ['openems'])
    assert plan(preview=True, mom=True)[0] == ['auto']             # --preview: no full-wave solve (auto: the line model)
    assert plan(solvers='mom25d,lines', preview=True)[0] == ['lines']
    assert plan(solvers='openems', preview=True)[0] == []          # no openEMS run in a preview


@pytest.mark.parametrize('values,match', [
    (dict(solvers='openems,hfss'), "no solver 'hfss'"),
    (dict(solvers='scikit', mode='antenna'), 'not available in antenna mode'),
    (dict(solvers='lines', mode='antenna'), 'antenna needs a solver with a far field'),
    (dict(fast='spice'), 'choose one of')])
def test_bad_solver_settings(values, match):
    with pytest.raises(ConfigError, match=match):
        build_settings(dict(board='b.kicad_pcb', **values))


def test_solvers_from_toml_and_command_line(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nsolvers = ["openems", "mom"]\n')
    s, _ = settings_from_argv([str(p)])
    assert s.solver_plan()[0] == ['openems', 'mom25d']
    s, _ = settings_from_argv([str(p), '--solvers', 'lines'])       # the command line wins
    assert s.solver_plan() == (['lines'], ['lines'])



def test_resolve_backend(monkeypatch):
    pytest.importorskip('skrf')
    from kicad2ems.solvers.lines import fastsolve
    from kicad2ems.solvers import resolve_lines
    monkeypatch.setattr(fastsolve.shutil, 'which', lambda name: None)
    assert fastsolve.resolve_backend('auto')[0] == 'skrf' and resolve_lines() == 'scikit'   # no qucsator: scikit-rf
    with pytest.raises(ValueError, match='--fast skrf'):
        fastsolve.resolve_backend('qucsator')
    monkeypatch.setattr(fastsolve.shutil, 'which', lambda name: '/usr/bin/' + name)
    assert fastsolve.resolve_backend('auto') == ('qucsator', '/usr/bin/qucsator_rf') and resolve_lines() == 'qucs'


def test_missing_openems_stops_before_any_work(run_cli, tmp_path, monkeypatch):
    monkeypatch.setattr(simulation, 'check_openems', lambda: (False, ['  FAILED  openEMS  ModuleNotFoundError']))
    out = tmp_path / 'out'
    ev = tmp_path / 'ev.jsonl'
    code, so, _ = run_cli(FILTER, '--solvers', 'openems', '--out', out, '--events', ev)
    assert code == 2 and '--solvers mom,lines' in so
    events = [json.loads(l) for l in ev.read_text().splitlines()]
    assert not [e for e in events if e['kind'] == 'stage'] and (events[-1]['kind'], events[-1]['code']) == ('done', 2)
    assert not (out / 'filter_mesh.png').exists()                  # nothing built: it stopped at once


def test_line_model_only(run_cli, tmp_path, monkeypatch):
    pytest.importorskip('skrf')
    monkeypatch.setattr(simulation, 'check_openems', lambda: pytest.fail('openEMS must not be needed'))
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--solvers', 'lines', '--fast', 'skrf', '--points', '51', '--out', out)
    assert code == 0, so + err
    sm = json.loads((out / 'filter_run.json').read_text())
    assert sm['solvers'] == ['scikit'] and sm['mode'] == 'S-parameters' and sm['results']['reference'] == 'scikit'
    assert 'pulse_steps' not in sm and not any('max-steps' in w for w in sm['warnings'])   # no openEMS talk
    assert 'mesh' not in sm and (out / 'filter_model.png').exists() and not (out / 'filter_mesh.png').exists()
    assert (out / 'filter_scikit.s2p').exists() and (out / 'filter_scikit.png').exists()
    page = (out / 'filter_report.html').read_text()
    assert 'line model' in page and "<div class='l'>mesh</div>" not in page    # no FDTD mesh tile without openEMS


def test_named_solver_that_fails_gives_exit_code_1(run_cli, tmp_path, monkeypatch):
    pytest.importorskip('skrf')
    from kicad2ems.solvers import mom
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (True, '0.0 (test)'))

    def broken(*a, **kw):
        raise RuntimeError('singular matrix')
    monkeypatch.setattr(mom, 'solve', broken)
    out = tmp_path / 'out'
    code, so, _ = run_cli(FILTER, '--solvers', 'mom', '--out', out)
    assert code == 1 and 'WARNING: mom25d failed: RuntimeError: singular matrix' in so and 'failed: mom25d' in so
    assert (out / 'filter_report.html').exists()                    # the report still says what happened
    code, so, _ = run_cli(FILTER, '--solvers', 'scikit', '--mom', '--out', out)   # a reference: a warning
    assert code == 0 and 'WARNING: mom25d failed' in so


def test_unavailable_reference_is_left_out(run_cli, tmp_path, monkeypatch):
    from kicad2ems.solvers import mom
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (False, 'ImportError: no mom25d'))
    out = tmp_path / 'out'
    code, so, _ = run_cli(FILTER, '--preview', '--mom', '--out', out)
    assert code == 0 and 'WARNING: mom25d cannot run' in so
    code, so, _ = run_cli(FILTER, '--solvers', 'mom', '--out', out)       # named: stops before it starts
    assert code == 2 and 'mom25d cannot run' in so


def test_kicad2openems_keeps_openems_as_its_default(tmp_path, monkeypatch, capsys):
    from kicad2ems.cli import main_openems
    monkeypatch.setattr(simulation, 'check_openems', lambda: (False, ['  FAILED  openEMS  ModuleNotFoundError']))
    assert main_openems([FILTER, '--out', str(tmp_path / 'out')]) == 2         # openEMS named by default: stops
    assert 'openEMS cannot be imported' in capsys.readouterr().out
