"""The solver plugins: the registry (built in and through entry points), the settings made from their option
tables ([solver.<name>] config tables, command-line options), the checks before a run (assess) and a run
that does not need openEMS's mesh."""
import json, os
import numpy as np
import pytest
from kicad2ems import solvers
from kicad2ems.config import build_settings, settings_class
from kicad2ems.cli import parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTER = os.path.join(ROOT, 'examples', 'rf_interdig.toml')


def test_registry_and_aliases():
    """The line solvers built in, the others from their packages (kicad2ems-openems, mom25d, gfdtd), by order."""
    reg = solvers.registry()
    assert list(reg) == ['openems', 'mom25d', 'mixed', 'gfdtd', 'qucs', 'scikit']
    assert [solvers.get(a).name for a in ('fdtd', 'MOM', 'qucsator', 'skrf')] == ['openems', 'mom25d', 'qucs', 'scikit']
    with pytest.raises(ValueError, match="no solver 'hfss'"):
        solvers.get('hfss')
    for sv in reg.values():                                         # every feature declared by every solver
        assert set(sv.capabilities) == set(solvers.FEATURES), sv.name
    assert solvers.ordered({'scikit', 'lines', 'openems'}) == ['openems', 'lines', 'scikit']


class _Fake(solvers.Solver):
    name, aliases, label, rank = 'fake', ('fk',), 'fake solver', 0
    capabilities = {k: 'yes' for k in solvers.FEATURES}
    options = [solvers.Option('fake_level', 3, "level", int, name='level', gui='Fake level', group='fake')]


class _EP:
    def __init__(self, name, obj):
        self.name, self.value, self._obj = name, f"tests:{name}", obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


def test_entry_point_plugins(monkeypatch):
    class Clash(_Fake):
        name, aliases = 'clash', ()
        options = [solvers.Option('fmin', 1.0)]                     # a core setting: refused
    monkeypatch.setattr(solvers, '_registry', None)
    monkeypatch.setattr(solvers, 'broken', [])
    installed = solvers._entry_points()                             # the solver packages: openEMS, mom25d, gfdtd
    monkeypatch.setattr(solvers, '_entry_points', lambda: installed + [_EP('fake', _Fake), _EP('bad', ImportError('no numpy')),
                                                                       _EP('clash', Clash)])
    reg = solvers.registry()
    assert list(reg) == ['openems', 'mom25d', 'mixed', 'gfdtd', 'fake', 'qucs', 'scikit'] and solvers.get('fk') is reg['fake']
    assert [b[0].split(' =')[0] for b in solvers.broken] == ['bad', 'clash']
    assert 'ImportError: no numpy' in solvers.broken[0][1] and 'fmin' in solvers.broken[1][1]
    S = settings_class(reg)                                         # its options become settings
    assert S(board='b').fake_level == 3
    monkeypatch.setattr(solvers, '_registry', None)                 # back to the built-in ones for the others


def test_shared_fdtd_settings(monkeypatch):
    """FDTD solvers share the mesh and run settings (the same Option objects): one setting each, in either solver's
    table; another plugin may list them too, but not define its own of the same name."""
    from kicad2ems.config import all_options
    from kicad2ems.fdtd.plugin import FDTD, MESH, RUN

    class Engine(FDTD):
        name, aliases, label = 'fdtd2', (), 'another FDTD engine'
        options = MESH + RUN

    class Clash(_Fake):
        name, aliases = 'clash', ()
        options = [solvers.Option('res', 10.0)]                     # the shared setting, redefined: refused
    monkeypatch.setattr(solvers, '_registry', None)
    monkeypatch.setattr(solvers, 'broken', [])
    installed = solvers._entry_points()
    monkeypatch.setattr(solvers, '_entry_points', lambda: installed + [_EP('fdtd2', Engine), _EP('clash', Clash)])
    reg = solvers.registry()
    assert 'fdtd2' in reg and [b[0].split(' =')[0] for b in solvers.broken] == ['clash']
    owner = [(o.key, n) for n, o in all_options(reg)]
    assert len(owner) == len(dict(owner)) and dict(owner)['res'] == 'openems'      # once, under the first
    assert reg['gfdtd'].options[0] is reg['openems'].options[0]
    monkeypatch.setattr(solvers, '_registry', None)


def test_solver_tables(tmp_path):
    p = tmp_path / 'c.toml'
    p.write_text('board = "b.kicad_pcb"\nsolvers = ["fdtd", "mom"]\n[solver.mom25d]\ndmax = 0.5\nport = "wave"\n'
                 'dmx = 1\nfmin = 2\n[solver.openems]\nend-db = -30\nexcite = "P1"\n[solver.hfss]\nx = 1\n'
                 '[othertool]\nfoo = 1\n')
    s = build_settings({}, str(p))
    assert s.mom_dmax == 0.5 and s.mom_port == 'wave' and s.end_db == -30 and s.excite == ['P1'] and s.fmin == 0.5
    note = next(n for n in s.notes if n.startswith('ignoring'))
    assert 'solver.mom25d.dmx (did you mean dmax?)' in note and 'solver.mom25d.fmin (a top-level setting)' in note
    assert '[solver.hfss]' in note and 'foo' not in note            # another tool's table: not ours to judge
    p.write_text('board = "b.kicad_pcb"\n[openems]\nair = 3\nfast = "auto"\n')     # the older form, with a note
    s = build_settings({}, str(p))
    assert s.air == 3 and s.fast == 'auto' and any('[solver.openems]' in n for n in s.notes)


def test_command_line_from_the_option_tables():
    flags = {f for a in parser()._actions for f in a.option_strings}
    assert {'--mom-dmax', '--mom-solves', '--qucsator', '--end-db', '--port', '--dump', '--solvers'} <= flags
    s = build_settings(vars(parser().parse_args(['b.kicad_pcb', '--mom-dmax', '0.6', '--port', 'P3=J1.1',
                                                  '--port', 'P4@1,2'])))
    assert s.mom_dmax == 0.6 and s.ports == ['P3=J1.1', 'P4@1,2']


def _canned_mom(m, freq, **kw):
    n = len(m.ports)
    S = np.zeros((len(freq), n, n), complex)
    S[:, 1, 0] = S[:, 0, 1] = 0.9 * np.exp(-2j * np.pi * freq * 100e-12)
    S[:, 0, 0] = S[:, 1, 1] = 0.1
    info = dict(seconds=1.5, solves=7, unknowns=1234, triangles=800, solver='dense', dmax_mm=0.3,
                ports=[dict(name=p.name, kind='lumped', zc=0.0) for p in m.ports], notes=['canned'])
    return S, [p.name for p in m.ports], [50.0] * n, info


@pytest.mark.mom25d
def test_mom_alone_builds_no_openems_mesh(run_cli, tmp_path, monkeypatch):
    from mom25d.kicad2ems import mom
    from kicad2ems.fdtd import mesh as fdtd_mesh
    monkeypatch.setattr(mom, 'solve', _canned_mom)
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (True, '0.0 (test)'))
    monkeypatch.setattr(fdtd_mesh, 'auto_mesh', lambda *a, **k: pytest.fail('no openEMS mesh for MoM alone'))
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--solvers', 'mom', '--points', '21', '--out', out)
    assert code == 0, so + err
    sm = json.loads((out / 'rf_interdig_run.json').read_text())
    assert sm['solvers'][0] == 'mom25d' and 'mesh' not in sm and sm['solved']['mom25d']['unknowns'] == 1234
    assert sm['results']['reference'] == 'mom25d' and sm['zref'] == [50.0, 50.0]
    for f in ('rf_interdig_mom25d.s2p', 'rf_interdig_mom25d.png', 'rf_interdig_model.png', 'rf_interdig_report.html'):
        assert (out / f).exists(), f
    page = (out / 'rf_interdig_report.html').read_text()
    assert "id='mom25d'" in page and "id='mesh'" not in page and 'canned' in page
    assert "id='saw'" in page and 'What each solver saw' in page                    # mom25d and the line model
    assert sm['saw']['solvers'][0] == 'mom25d (MoM)' and any(d['item'] == 'outline' for d in sm['deviations']['mom25d'])


def test_assess_named_and_reference(boards, run_cli, tmp_path, monkeypatch):
    """A gap-fed dipole: mom25d cannot model it. Named, the run stops before any solver; added with --mom, it is
    left out with a warning."""
    from mom25d.kicad2ems import mom
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (True, '0.0 (test)'))
    monkeypatch.setattr(mom, 'solve', lambda *a, **k: pytest.fail('mom25d must not run'))
    out = tmp_path / 'out'
    code, so, _ = run_cli(boards['dipole'] + '.kicad_pcb', '--solvers', 'mom', '--fmin', '1', '--fmax', '3', '--out', out)
    assert code == 2 and 'mom25d cannot model this board' in so and 'gap port' in so
    code, so, _ = run_cli(boards['dipole'] + '.kicad_pcb', '--preview', '--mom', '--fmin', '1', '--fmax', '3', '--out', out)
    assert code == 0 and 'WARNING: mom25d cannot model this board, left out' in so


@pytest.mark.mom25d
def test_auto_picks_the_cheaper_full_wave_solver(run_cli, tmp_path, monkeypatch):
    from mom25d.kicad2ems import mom
    from gfdtd import kicad2ems as gfdtd
    from kicad2ems_openems import plugin as fdtd
    from kicad2ems_openems import engine
    monkeypatch.setattr(mom, 'solve', _canned_mom)
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (True, '0.0 (test)'))
    monkeypatch.setattr(engine, 'check', lambda: (True, ['  ok      openEMS  /x/openEMS/__init__.py']))
    monkeypatch.setattr(gfdtd.GFDTD, 'check', lambda self, s=None: (True, 'GPU: test'))
    cost = dict(openems=100.0, mom25d=10.0, gfdtd=50.0)
    monkeypatch.setattr(fdtd.OpenEMS, 'estimate', lambda self, run: dict(seconds=cost['openems'], gb=0.1, what='0.4 M cells'))
    monkeypatch.setattr(gfdtd.GFDTD, 'estimate', lambda self, run: dict(seconds=cost['gfdtd'], gb=0.1, what='on the GPU'))
    monkeypatch.setattr(mom.MoM25D, 'estimate', lambda self, run: dict(seconds=cost['mom25d'], gb=0.1, what='4,400 unknowns'))
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--solvers', 'auto', '--points', '21', '--out', out)
    assert code == 0, so + err
    assert 'solvers auto: mom25d + ' in so and 'openems not chosen (0.4 M cells, ~100 s)' in so
    assert 'gfdtd not chosen (on the GPU, ~50 s)' in so
    sm = json.loads((out / 'rf_interdig_run.json').read_text())
    assert sm['solvers'][0] == 'mom25d' and sm['auto']['chosen'][0] == 'mom25d' and 'openems' in sm['auto']['estimates']
    assert '--solvers auto: mom25d' in (out / 'rf_interdig_report.html').read_text()
    cost.update(gfdtd=5.0)                                          # now gfdtd is cheaper: --preview prepares openEMS's mesh
    code, so, err = run_cli(FILTER, '--solvers', 'auto', '--preview', '--points', '21', '--out', out)
    assert code == 0 and 'solvers auto: gfdtd + ' in so and (out / 'rf_interdig_mesh.png').exists(), so + err
    code, so, err = run_cli(FILTER, '--solvers', 'auto', '--preview', '--sigma', '5.8e7', '--points', '21', '--out', out)
    assert code == 0 and 'solvers auto: mom25d + ' in so, so + err        # copper loss: not gfdtd's
    assert "gfdtd not used: --sigma (gfdtd's copper is PEC): openEMS's" in so
    cost.update(mom25d=50.0, openems=20.0)
    code, so, err = run_cli(FILTER, '--solvers', 'auto', '--preview', '--sigma', '5.8e7', '--points', '21', '--out', out)
    assert code == 0 and 'solvers auto: openems + ' in so, so + err
    assert settings_plan('auto') == (['auto'], ['auto'])


def settings_plan(v):
    return build_settings(dict(board='b.kicad_pcb', solvers=v)).solver_plan()


def test_auto_leaves_out_what_cannot_model_the_board(boards, run_cli, tmp_path, monkeypatch):
    from mom25d.kicad2ems import mom
    monkeypatch.setattr(mom, 'mom25d_status', lambda: (True, '0.0 (test)'))
    monkeypatch.setattr(mom.MoM25D, 'estimate', lambda self, run: pytest.fail('a dipole is not for mom25d'))
    out = tmp_path / 'out'
    code, so, err = run_cli(boards['dipole'] + '.kicad_pcb', '--solvers', 'auto', '--preview', '--fmin', '1', '--fmax', '3',
                            '--out', out)
    assert code == 0, so + err
    assert ('solvers auto: openems' in so or 'solvers auto: gfdtd' in so)            # FDTD, whichever is cheaper
    assert 'mom25d not used: gap port(s) P1: no planar equivalent' in so
