"""The analysis plugins: the registry (entry points, the API version, names and option kinds), whose result a
folder holds, a config's [analysis] tables, the examples an analysis ships (index, prepared in a folder) and
--check's lines."""
import os

import pytest
from kicad2ems import analyses
from kicad2ems.api import API_VERSION, analysis as analysis_api, frontend


class _EP:
    def __init__(self, name, obj):
        self.name, self.value, self._obj = name, f"tests:{name}", obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


class Heat(analyses.Analysis):
    api = (2, 5)
    name, label, tool, order = 'heat', 'Heat', 'fake.heat', 30
    result, preview_result, paths = 'heat.json', 'heat-preview.json', ('losses',)

    def options(self):
        return [dict(key='losses', flag='--losses', kind='str', default='', help='a file', group='Heat'),
                dict(key='film', flag='--film', kind='float', default=10.0, help='W/m2/K', group='Air')]

    def tag(self, values):
        return 'all'

    def command(self, values, board, out, preview=False, results=None):
        if not board:
            raise ValueError('choose a board')
        return ['heat', board, '--out', out]


class Drop(Heat):
    name, label, tool, order, result, preview_result, paths = 'drop', 'Drop', 'fake.drop', 20, 'drop.json', '', ()

    def options(self):
        return []


@pytest.fixture
def plugins(monkeypatch):
    def install(*eps):
        monkeypatch.setattr(analyses, '_registry', None)
        monkeypatch.setattr(analyses, 'broken', [])
        monkeypatch.setattr(analyses, '_entry_points', lambda: list(eps))
        return analyses.registry()
    yield install
    analyses._registry = None


def test_every_name_resolves():
    for name in analysis_api.NAMES:
        assert getattr(analysis_api, name) is not None, name
    assert analysis_api.Analysis is analyses.Analysis and analysis_api.API_VERSION == API_VERSION
    assert frontend.analyses is analyses.registry and frontend.analysis_of is analyses.analysis_of


def test_registry_by_order_and_what_it_refuses(plugins):
    class Newer(Heat):
        name, api = 'newer', (API_VERSION[0] + 1, 0)

    class Nameless(Heat):
        name = ''

    class Rf(Heat):
        name = analyses.RF                                          # the S-parameter run's key

    class Odd(Heat):
        name = 'odd'

        def options(self):
            return [dict(key='x', flag='--x', kind='colour', default='', help='', group='')]
    reg = plugins(_EP('heat', Heat), _EP('drop', Drop), _EP('again', Heat), _EP('newer', Newer),
                  _EP('nameless', Nameless), _EP('rf', Rf), _EP('odd', Odd), _EP('bad', ImportError('no numpy')))
    assert list(reg) == ['drop', 'heat']                            # by order
    why = dict((b[0].split(' =')[0], b[1]) for b in analyses.broken)
    assert set(why) == {'again', 'newer', 'nameless', 'rf', 'odd', 'bad'}
    assert 'kicad2ems API' in why['newer'] and 'already taken' in why['again'] and 'no name' in why['nameless']
    assert 'already taken' in why['rf'] and 'unknown kind' in why['odd'] and 'no numpy' in why['bad']
    assert analyses.get('HEAT') is reg['heat']
    with pytest.raises(ValueError, match="no analysis 'cfd'"):
        analyses.get('cfd')
    with pytest.raises(ValueError, match=r"kicad2ems\[qs\]"):       # one kicad2ems has an extra for
        analyses.get('pdn')
    lines = analyses.check_lines()
    assert any(l.split()[:2] == ['ok', 'heat'] for l in lines) and any('BROKEN' in l and 'odd' in l for l in lines)
    assert any('kicad2ems[qs]' in l for l in lines)


def test_whose_result_a_folder_holds(plugins, tmp_path):
    plugins(_EP('heat', Heat), _EP('drop', Drop))
    assert analyses.analysis_of(str(tmp_path)) is None and analyses.analysis_of(None) is None
    (tmp_path / 'heat-preview.json').write_text('{}')
    assert analyses.analysis_of(str(tmp_path)) == ('heat', True)
    (tmp_path / 'heat.json').write_text('{}')
    assert analyses.analysis_of(str(tmp_path)) == ('heat', False)


def test_a_config_s_analysis_tables(plugins, tmp_path):
    plugins(_EP('heat', Heat))
    cfg = tmp_path / 'b.toml'
    cfg.write_text('board = "b.kicad_pcb"\n[analysis]\nrun = ["Heat", "rf"]\n[analysis.heat]\nlosses = "l.json"\n'
                   'film = 12\n[analysis.other]\nmodel-file = "m.json"\n')
    run, tables = analyses.read_config(str(cfg))
    assert run == ['heat', 'rf']
    assert tables['heat'] == dict(losses=str(tmp_path / 'l.json'), film=12)          # a path: beside the config
    assert tables['other'] == dict(model_file='m.json')            # not installed: kept as written
    assert frontend.build_settings({}, str(cfg)).notes == []       # kicad2ems itself: another tool's tables
    cfg.write_text('board = "b.kicad_pcb"\n')
    assert analyses.read_config(str(cfg)) == (None, {}) and analyses.read_sets(str(cfg)) == {}
    assert frontend.read_analyses is analyses.read_config
    # several tables of one analysis (2.10): each with its paths; read_config gives the first
    cfg.write_text('[[analysis.heat]]\nlosses = "a.json"\n[[analysis.heat]]\nlosses = "b.json"\nfilm = 3\n'
                   '[analysis.other]\nx = 1\n[analysis.odd]\n')
    assert analyses.read_sets(str(cfg)) == dict(
        heat=[dict(losses=str(tmp_path / 'a.json')), dict(losses=str(tmp_path / 'b.json'), film=3)], other=[dict(x=1)],
        odd=[{}])
    assert analyses.read_config(str(cfg))[1]['heat'] == dict(losses=str(tmp_path / 'a.json'))


def test_an_analysis_ships_examples(plugins, tmp_path):
    """Its examples in the index after kicad2ems's own, under the group it names; prepared: the config, its files
    (always) and its board (kept when there)."""
    import kicad2ems.examples as ex
    src = tmp_path / 'shipped'
    src.mkdir()
    (src / '01_warm.toml').write_text('# fake example: a warm board\nboard = "01_warm.kicad_pcb"\n'
                                      '[analysis.heat]\nlosses = "01_warm.json"\n')
    (src / '01_warm.json').write_text('{"Q1": 1}')
    (src / '01_warm.kicad_pcb').write_text('(kicad_pcb)')
    (src / 'bare.toml').write_text('[analysis.heat]\nfilm = 5\n')

    class Shipping(Heat):
        def examples(self):
            return [dict(name='heat_warm', config=str(src / '01_warm.toml'), group='Heat/Boards',
                         files=['01_warm.json', '01_warm.kicad_pcb']),
                    dict(name='heat_bare', config=str(src / 'bare.toml'), title='no board'),
                    dict(name='rf_gcpw', config=str(src / 'bare.toml')),             # kicad2ems's own name: left out
                    dict(name='heat_gone', config=str(src / 'gone.toml'))]
    plugins(_EP('heat', Shipping))
    ix = ex.index()
    mine = [e for e in ix if e['source'] == ex.SOURCE]
    assert len(mine) == len(ex.own()) and not [e for e in mine if e['group'] == ex.OTHER]    # each in a group
    assert all(e['title'] for e in mine)
    assert [e['name'] for e in ix[len(mine):]] == ['heat_warm', 'heat_bare']
    warm, bare = ix[-2:]
    assert warm['group'] == 'Heat/Boards' and warm['title'] == 'a warm board' and warm['source'] == 'heat'
    assert bare['group'] == ex.OTHER and bare['title'] == 'no board'
    assert set(ex.examples()) == set(ex.own()) | {'heat_warm', 'heat_bare'}
    dest = tmp_path / 'mine'
    cfg = frontend.prepare_example('heat_warm', str(dest))
    assert cfg == str(dest / 'heat_warm.toml') and (dest / '01_warm.json').exists()
    (dest / '01_warm.kicad_pcb').write_text('edited')
    (dest / '01_warm.json').write_text('edited')
    frontend.prepare_example('heat_warm', str(dest))
    assert (dest / '01_warm.kicad_pcb').read_text() == 'edited' and (dest / '01_warm.json').read_text() == '{"Q1": 1}'
    run, tables = frontend.read_analyses(cfg)
    assert run is None and tables['heat']['losses'] == str(dest / '01_warm.json')
    assert frontend.prepare_example('heat_bare', str(dest)) == str(dest / 'heat_bare.toml')
    assert frontend.example_index is ex.index


def test_index_groups_are_kicad2ems_s_own():
    import kicad2ems.examples as ex
    listed = [n for _g, _s, names in ex.GROUPS for n in names]
    assert len(listed) == len(set(listed)) and set(listed) == set(ex.own())
    assert ex.title_of(ex.own()['rf_gcpw']).startswith('grounded coplanar waveguide')
    assert ex.title_of(ex.own()['hairpin']).startswith('hairpin band-pass filter')
