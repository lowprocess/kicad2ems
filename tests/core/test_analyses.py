"""The analyses: the registry (here of fakes), whose result a folder holds, a
config's [analysis] tables, the examples an analysis ships (index, prepared in a
folder) and --check's lines."""
import os

import pytest
from kicad2ems.core import analyses
from kicad2ems.core.config import build_settings


class Heat(analyses.Analysis):
    name, label, tool, order = 'heat', 'Heat', 'fake.heat', 30
    result, preview_result, paths = ('heat.json', 'heat-preview.json',
                                     ('losses', ))

    def options(self):
        return [
            dict(key='losses', flag='--losses', kind='str', default='',
                 help='a file', group='Heat'),
            dict(key='film', flag='--film', kind='float', default=10.0,
                 help='W/m2/K', group='Air')
        ]

    def tag(self, values):
        return 'all'

    def command(self, values, board, out, preview=False, results=None):
        if not board:
            raise ValueError('choose a board')
        return ['heat', board, '--out', out]


class Drop(Heat):
    name, label, tool, order, result, preview_result, paths = ('drop', 'Drop',
                                                               'fake.drop', 20,
                                                               'drop.json', '',
                                                               ())

    def options(self):
        return []


@pytest.fixture
def fakes(monkeypatch):
    """The registry: these analyses (classes), by their order."""
    def install(*classes):
        found = sorted((c() for c in classes), key=lambda a: (a.order, a.name))
        monkeypatch.setattr(analyses, '_registry', {a.name: a for a in found})
        return analyses.registry()
    return install


def test_get_and_check_lines(fakes):
    reg = fakes(Heat, Drop)
    assert analyses.get('HEAT') is reg['heat']
    with pytest.raises(ValueError, match="no analysis 'cfd'"):
        analyses.get('cfd')
    lines = analyses.check_lines()
    assert any(l.split()[:2] == ['ok', 'heat'] for l in lines)


def test_whose_result_a_folder_holds(fakes, tmp_path):
    fakes(Heat, Drop)
    assert (analyses.analysis_of(str(tmp_path)) is None
            and analyses.analysis_of(None) is None)
    (tmp_path / 'heat-preview.json').write_text('{}')
    assert analyses.analysis_of(str(tmp_path)) == ('heat', True)
    (tmp_path / 'heat.json').write_text('{}')
    assert analyses.analysis_of(str(tmp_path)) == ('heat', False)


def test_a_config_s_analysis_tables(fakes, tmp_path):
    fakes(Heat)
    cfg = tmp_path / 'b.toml'
    cfg.write_text(
        'board = "b.kicad_pcb"\n[analysis]\nrun = ["Heat", '
        '"rf"]\n[analysis.heat]\nlosses = "l.json"\nfilm = '
        '12\n[analysis.other]\nmodel-file = "m.json"\n')
    run, tables = analyses.read_config(str(cfg))
    assert run == ['heat', 'rf']
    # a path: beside the config
    assert tables['heat'] == dict(losses=str(tmp_path / 'l.json'), film=12)
    # not installed: kept as written
    assert tables['other'] == dict(model_file='m.json')
    # kicad2ems itself: another tool's tables
    assert build_settings({}, str(cfg)).notes == []
    cfg.write_text('board = "b.kicad_pcb"\n')
    assert (analyses.read_config(str(cfg)) == (None, {})
            and analyses.read_sets(str(cfg)) == {})
    # several tables of one analysis: each with its paths; read_config
    # gives the first
    cfg.write_text(
        '[[analysis.heat]]\nlosses = "a.json"\n[[analysis.heat]]\nlosses = '
        '"b.json"\nfilm = 3\n[analysis.other]\nx = 1\n[analysis.odd]\n')
    assert (analyses.read_sets(str(cfg)) == dict(
        heat=[
            dict(losses=str(tmp_path / 'a.json')),
            dict(losses=str(tmp_path / 'b.json'), film=3)
        ], other=[dict(x=1)], odd=[{}]))
    assert (analyses.read_config(
        str(cfg))[1]['heat'] == dict(losses=str(tmp_path / 'a.json')))


def test_an_analysis_ships_examples(fakes, tmp_path):
    """Its examples in the index after kicad2ems's own, under the group it
    names; prepared: the config, its files (always) and its board (kept when
    there)."""
    import kicad2ems.examples as ex
    src = tmp_path / 'shipped'
    src.mkdir()
    (src / '01_warm.toml').write_text(
        '# fake example: a warm board\nboard = "01_warm.kicad_pcb"\n'
        '[analysis.heat]\nlosses = "01_warm.json"\n')
    (src / '01_warm.json').write_text('{"Q1": 1}')
    (src / '01_warm.kicad_pcb').write_text('(kicad_pcb)')
    (src / 'bare.toml').write_text('[analysis.heat]\nfilm = 5\n')

    class Shipping(Heat):
        def examples(self):
            return [
                dict(name='heat_warm', config=str(src / '01_warm.toml'),
                     group='Heat/Boards',
                     files=['01_warm.json', '01_warm.kicad_pcb']),
                dict(name='heat_bare', config=str(src / 'bare.toml'),
                     title='no board'),
                # kicad2ems's own name: left out
                dict(name='rf_gcpw', config=str(src / 'bare.toml')),
                dict(name='heat_gone', config=str(src / 'gone.toml'))
            ]
    fakes(Shipping)
    ix = ex.index()
    mine = [e for e in ix if e['source'] == ex.SOURCE]
    # each in a group
    assert (len(mine) == len(ex.own())
            and not [e for e in mine if e['group'] == ex.OTHER])
    assert all(e['title'] for e in mine)
    assert [e['name'] for e in ix[len(mine):]] == ['heat_warm', 'heat_bare']
    warm, bare = ix[-2:]
    assert (warm['group'] == 'Heat/Boards' and warm['title'] == 'a warm board'
            and warm['source'] == 'heat')
    assert bare['group'] == ex.OTHER and bare['title'] == 'no board'
    assert set(ex.examples()) == set(ex.own()) | {'heat_warm', 'heat_bare'}
    dest = tmp_path / 'mine'
    cfg = ex.prepare_example('heat_warm', str(dest))
    assert (cfg == str(dest / 'heat_warm.toml')
            and (dest / '01_warm.json').exists())
    (dest / '01_warm.kicad_pcb').write_text('edited')
    (dest / '01_warm.json').write_text('edited')
    ex.prepare_example('heat_warm', str(dest))
    assert ((dest / '01_warm.kicad_pcb').read_text() == 'edited'
            and (dest / '01_warm.json').read_text() == '{"Q1": 1}')
    run, tables = analyses.read_config(cfg)
    assert (run is None
            and tables['heat']['losses'] == str(dest / '01_warm.json'))
    assert (ex.prepare_example('heat_bare',
                               str(dest)) == str(dest / 'heat_bare.toml'))


def test_index_groups_are_kicad2ems_s_own():
    import kicad2ems.examples as ex
    listed = [n for _g, _s, names in ex.GROUPS for n in names]
    assert len(listed) == len(set(listed)) and set(listed) == set(ex.own())
    assert ex.title_of(
        ex.own()['rf_gcpw']).startswith('grounded coplanar waveguide')
    assert ex.title_of(
        ex.own()['hairpin']).startswith('hairpin band-pass filter')
