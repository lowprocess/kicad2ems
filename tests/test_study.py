"""The study (kicad2ems.study): the questions, the engines that answer them (solvers and analyses alike), the tree
a front end lists, the runs a set of questions takes, and a run folder's mark."""
import os
import pytest
from kicad2ems import analyses, study
from kicad2ems.api.analysis import Analysis, Question


class Drop(Analysis):
    api, name, label, tool, engine = (2, 6), 'pdn', 'Power integrity', 'fake.pdn', 'fake'
    about, result, order = "a rail's drop and impedance", 'pdn.json', 20
    provides = ('dc', 'pdn')

    def for_questions(self, values, asked):
        return dict(values, no_pdn='pdn' not in asked)


class Warm(Analysis):
    api, name, label, tool, engine = (2, 6), 'thermal', 'Thermal', 'fake.thermal', 'fake'
    about, result, order, provides, fields = 'the temperature', 'thermal.json', 50, ('thermal',), 'maps'


class Elmer(Analysis):
    api, name, label, tool, engine = (2, 6), 'elmer_thermal', 'Temperature (Elmer)', 'fem.thermal', 'Elmer'
    about, result, order, provides = 'the temperature, in 3D', 'thermal.json', 60, ('thermal',)

    def owns(self, folder, preview=False):                      # the same file name as the other: its own key
        f = os.path.join(folder, 'thermal.json')
        return not preview and os.path.isfile(f) and 'elmer' in open(f).read()


class Old(Analysis):                                            # a 2.5 plugin: declares no questions
    api, name, label, tool, about, result = (2, 5), 'mqs', 'Near field', 'fake.mqs', 'a near field', 'mqs.json'


class Noise(Analysis):                                          # brings a question of its own
    api, name, label, tool, about, result = (2, 6), 'emission', 'Emissions', 'x.emi', 'radiated emission', 'emi.json'
    provides = ('emissions',)
    questions = (Question('emissions', 'Radiated emission', 'RF', 'the field at 3 m', order=90),)


class Wrong(Analysis):
    api, name, label, tool, result, provides = (2, 6), 'wrong', 'Wrong', 'x.w', 'w.json', ('nothing',)


class _EP:
    def __init__(self, cls):
        self.name, self.value, self.cls = cls.name, f"fake:{cls.__name__}", cls

    def load(self):
        return self.cls


@pytest.fixture
def plugins(monkeypatch):
    def install(*classes):
        monkeypatch.setattr(analyses, '_entry_points', lambda: [_EP(c) for c in classes])
        monkeypatch.setattr(analyses, '_registry', None)
        monkeypatch.setattr(analyses, 'broken', [])
        return analyses.registry()
    yield install
    monkeypatch.setattr(analyses, '_registry', None)


def test_engines_are_solvers_and_analyses_by_what_they_answer(plugins):
    plugins(Drop, Warm, Elmer)
    es = study.engines()
    assert es['pdn'].provides == ('dc', 'pdn') and es['pdn'].label == 'fake' and es['pdn'].kind == 'analysis'
    assert es['thermal'].fields == 'maps' and es['elmer_thermal'].label == 'Elmer'
    solvers = [e for e in es.values() if e.kind == 'solver']
    assert solvers and all('sparams' in e.provides for e in solvers)
    assert {e.name for e in solvers if 'lines' in e.provides} == {'qucs', 'scikit'}         # the line model's
    full = [e for e in solvers if 'lines' not in e.provides]
    assert full and any('antenna' in e.provides for e in full) and any(e.fields == 'dump' for e in full)
    assert [e.name for e in study.engines_of('thermal')] == ['thermal', 'elmer_thermal']
    assert es['pdn'].check() == (True, '')


def test_the_tree_lists_every_question_with_its_engines(plugins):
    plugins(Drop, Warm, Elmer)
    tree = study.study_tree()
    assert [g['group'] for g in tree] == ['RF', 'Power', 'Thermal']
    rows = {r['question'].name: r for g in tree for r in g['questions']}
    assert list(rows) == ['sparams', 'antenna', 'lines', 'dc', 'pdn', 'nearfield', 'circuit', 'capacitance', 'thermal']
    assert [e.name for e in rows['dc']['engines']] == ['pdn'] == [e.name for e in rows['pdn']['engines']]
    assert rows['pdn']['question'].shares == 'dc' and rows['sparams']['question'].answer == 'touchstone'
    assert [e.name for e in rows['thermal']['engines']] == ['thermal', 'elmer_thermal'] and not rows['thermal']['missing']
    assert rows['circuit']['engines'] == [] and rows['circuit']['missing'] == "python -m pip install 'kicad2ems[qs]'"
    assert rows['capacitance']['missing'] == "python -m pip install 'kicad2ems[fem]'" and not rows['lines']['missing']


def test_a_plugin_without_questions_and_one_with_its_own(plugins):
    reg = plugins(Old, Noise, Wrong)
    assert list(reg) == ['emission', 'mqs'] and 'provides questions nobody declares: nothing' in analyses.broken[0][1]
    qs = study.questions()
    assert qs['mqs'].group == 'Other' and qs['mqs'].label == 'Near field'       # a question of its own name
    assert qs['emissions'].group == 'RF' and study.engines()['mqs'].provides == ('mqs',)
    tree = {g['group']: [r['question'].name for r in g['questions']] for g in study.study_tree()}
    assert tree['RF'][-1] == 'emissions' and tree['Other'] == ['mqs']


def test_the_runs_a_set_of_questions_takes(plugins):
    reg = plugins(Drop, Warm, Elmer)
    assert study.runs_for({'dc': [], 'pdn': []}) == {'pdn': ('dc', 'pdn')}                  # one run answers both
    assert study.runs_for({'dc': ['pdn'], 'thermal': ['thermal', 'elmer_thermal']}) == \
        {'pdn': ('dc',), 'thermal': ('thermal',), 'elmer_thermal': ('thermal',)}            # two engines: compared
    assert reg['pdn'].for_questions(dict(rail='P'), ('dc',)) == dict(rail='P', no_pdn=True)
    assert reg['thermal'].for_questions(dict(a=1), ('thermal',)) == dict(a=1)
    with pytest.raises(ValueError, match="'thermal' does not answer 'dc': one of pdn"):
        study.runs_for({'dc': ['thermal']})
    with pytest.raises(ValueError, match="no engine installed answers 'circuit'"):
        study.runs_for({'circuit': []})


def test_a_run_folder_says_whose_it_is(plugins, tmp_path):
    plugins(Warm, Elmer)
    a, b, c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
    for d, text in ((a, '{"method": "grid"}'), (b, '{"tool": "elmer"}'), (c, '{"tool": "elmer"}')):
        d.mkdir()
        (d / 'thermal.json').write_text(text)
    assert analyses.analysis_of(str(a)) == ('thermal', False)
    assert analyses.analysis_of(str(b)) == ('thermal', False)        # the first that owns it: not Elmer's, wrongly
    path = study.mark(str(c), 'elmer_thermal', ('thermal',))
    assert os.path.basename(path) == study.MARK and study.marked(str(c)) == dict(engine='elmer_thermal',
                                                                                 questions=('thermal',), preview=False)
    assert analyses.analysis_of(str(c)) == ('elmer_thermal', False)   # the mark says
    study.mark(str(a), 'elmer_thermal')                              # a mark the folder does not bear out: ignored
    assert analyses.analysis_of(str(a)) == ('thermal', False)
    assert study.marked(str(tmp_path)) is None and analyses.analysis_of(str(tmp_path)) is None
    (tmp_path / study.MARK).write_text('not json')
    assert study.marked(str(tmp_path)) is None


def test_check_lists_the_questions(plugins):
    from kicad2ems.cli import check_report
    plugins(Drop, Warm)
    _, lines = check_report()
    i = lines.index('questions, and the engines that answer them:')
    text = '\n'.join(lines[i:i + 11])
    assert 'Rail drop             pdn' in text and 'Temperature           thermal' in text
    assert "Circuit               none (python -m pip install 'kicad2ems[qs]')" in text
    assert 'S-parameters' in text and 'qucs' in text
