"""The study (kicad2ems.core.study): the questions, the engines that answer them
(solvers and analyses alike), the tree a front end lists, the runs a set of
questions takes, and a run folder's mark."""
import os
import pytest
from kicad2ems.core import analyses, study
from kicad2ems.core.api.analysis import Analysis, Question


class Drop(Analysis):
    name, label, tool, engine = 'pdn', 'Power integrity', 'fake.pdn', 'fake'
    about, result, order = "a rail's drop and impedance", 'pdn.json', 20
    provides = ('dc', 'pdn')

    def for_questions(self, values, asked):
        return dict(values, no_pdn='pdn' not in asked)


class Warm(Analysis):
    name, label, tool, engine = 'thermal', 'Thermal', 'fake.thermal', 'fake'
    about, result, order, provides, fields = ('the temperature', 'thermal.json',
                                              50, ('thermal', ), 'maps')


class Elmer(Analysis):
    name, label, tool, engine = ('elmer_thermal', 'Temperature (Elmer)',
                                 'fem.thermal', 'Elmer')
    about, result, order, provides = ('the temperature, in 3D', 'thermal.json',
                                      60, ('thermal', ))

    # the same file name as the other: its own key
    def owns(self, folder, preview=False):
        f = os.path.join(folder, 'thermal.json')
        return not preview and os.path.isfile(f) and 'elmer' in open(f).read()


class Old(Analysis):  # declares no questions
    name, label, tool, about, result = ('mqs', 'Near field', 'fake.mqs',
                                        'a near field', 'mqs.json')


class Noise(Analysis):  # brings a question of its own
    name, label, tool, about, result = ('emission', 'Emissions', 'x.emi',
                                        'radiated emission', 'emi.json')
    provides = ('emissions',)
    questions = (Question('emissions', 'Radiated emission', 'RF',
                          'the field at 3 m', order=90), )


@pytest.fixture
def fakes(monkeypatch):
    """The registry: these analyses (classes), by their order."""
    def install(*classes):
        found = sorted((c() for c in classes), key=lambda a: (a.order, a.name))
        monkeypatch.setattr(analyses, '_registry', {a.name: a for a in found})
        return analyses.registry()
    return install


def test_engines_are_solvers_and_analyses_by_what_they_answer(fakes):
    fakes(Drop, Warm, Elmer)
    es = study.engines()
    assert (es['pdn'].provides == ('dc', 'pdn') and es['pdn'].label == 'fake'
            and es['pdn'].kind == 'analysis')
    assert (es['thermal'].fields == 'maps'
            and es['elmer_thermal'].label == 'Elmer')
    solvers = [e for e in es.values() if e.kind == 'solver']
    assert solvers and all('sparams' in e.provides for e in solvers)
    # the line model's
    assert ({e.name
             for e in solvers
             if 'lines' in e.provides} == {'qucs', 'scikit'})
    full = [e for e in solvers if 'lines' not in e.provides]
    assert (full and any('antenna' in e.provides for e in full)
            and any(e.fields == 'dump' for e in full))
    assert ([e.name for e in study.engines_of('thermal')
             ] == ['thermal', 'elmer_thermal'])
    assert es['pdn'].check() == (True, '')


def test_the_tree_lists_every_question_with_its_engines(fakes):
    fakes(Drop, Warm, Elmer)
    tree = study.study_tree()
    assert ([g['group']
             for g in tree] == ['RF', 'Signal integrity', 'Power', 'Thermal'])
    rows = {r['question'].name: r for g in tree for r in g['questions']}
    assert (list(rows) == [
        'sparams', 'antenna', 'lines', 'crosstalk', 'dc', 'pdn', 'nearfield',
        'circuit', 'coils', 'capacitance', 'thermal'
    ])
    # kicad2ems's own (analyses.BUILTIN): not these fakes'
    assert rows['crosstalk']['engines'] == []
    assert ([e.name for e in rows['dc']['engines']] == ['pdn'] ==
            [e.name for e in rows['pdn']['engines']])
    assert (rows['pdn']['question'].shares == 'dc'
            and rows['sparams']['question'].answer == 'touchstone')
    assert ([e.name for e in rows['thermal']['engines']]
            == ['thermal', 'elmer_thermal'] and not rows['thermal']['missing'])
    assert (rows['circuit']['engines'] == []
            and rows['circuit']['missing'] == 'no engine for it is installed')
    assert (rows['capacitance']['missing'] == 'no engine for it is installed'
            and not rows['lines']['missing'])


def test_an_analysis_without_questions_and_one_with_its_own(fakes):
    reg = fakes(Old, Noise)
    assert list(reg) == ['emission', 'mqs']
    qs = study.questions()
    # a question of its own name
    assert qs['mqs'].group == 'Other' and qs['mqs'].label == 'Near field'
    assert (qs['emissions'].group == 'RF'
            and study.engines()['mqs'].provides == ('mqs', ))
    tree = {
        g['group']: [r['question'].name
                     for r in g['questions']]
        for g in study.study_tree()
    }
    assert tree['RF'][-1] == 'emissions' and tree['Other'] == ['mqs']


def test_the_runs_a_set_of_questions_takes(fakes):
    reg = fakes(Drop, Warm, Elmer)
    # one run answers both
    assert study.runs_for({'dc': [], 'pdn': []}) == {'pdn': ('dc', 'pdn')}
    # two engines: compared
    assert (study.runs_for(
        {'dc': ['pdn'], 'thermal': ['thermal', 'elmer_thermal']}) == {
            'pdn': ('dc', ), 'thermal': ('thermal', ),
            'elmer_thermal': ('thermal', )
        })
    assert (reg['pdn'].for_questions(dict(rail='P'),
                                     ('dc', )) == dict(rail='P', no_pdn=True))
    assert reg['thermal'].for_questions(dict(a=1), ('thermal',)) == dict(a=1)
    with pytest.raises(ValueError,
                       match="'thermal' does not answer 'dc': one of pdn"):
        study.runs_for({'dc': ['thermal']})
    with pytest.raises(ValueError,
                       match="no engine installed answers 'circuit'"):
        study.runs_for({'circuit': []})


def test_a_run_folder_says_whose_it_is(fakes, tmp_path):
    fakes(Warm, Elmer)
    a, b, c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
    for d, text in ((a, '{"method": "grid"}'), (b, '{"tool": "elmer"}'),
                    (c, '{"tool": "elmer"}')):
        d.mkdir()
        (d / 'thermal.json').write_text(text)
    assert analyses.analysis_of(str(a)) == ('thermal', False)
    # the first that owns it: not Elmer's, wrongly
    assert analyses.analysis_of(str(b)) == ('thermal', False)
    path = study.mark(str(c), 'elmer_thermal', ('thermal',))
    assert (os.path.basename(path) == study.MARK
            and study.marked(str(c)) == dict(
                engine='elmer_thermal', questions=('thermal', ), preview=False))
    # the mark says
    assert analyses.analysis_of(str(c)) == ('elmer_thermal', False)
    # a mark the folder does not bear out: ignored
    study.mark(str(a), 'elmer_thermal')
    assert analyses.analysis_of(str(a)) == ('thermal', False)
    assert (study.marked(str(tmp_path)) is None
            and analyses.analysis_of(str(tmp_path)) is None)
    (tmp_path / study.MARK).write_text('not json')
    assert study.marked(str(tmp_path)) is None


def test_check_lists_the_questions(fakes):
    from kicad2ems.core.cli import check_report
    fakes(Drop, Warm)
    _, lines = check_report()
    i = lines.index('questions, and the engines that answer them:')
    text = '\n'.join(lines[i:i + 13])
    assert ('Rail drop             pdn' in text
            and 'Temperature           thermal' in text)
    assert ("Circuit               none (no engine for it is installed)"
            in text)
    assert 'S-parameters' in text and 'qucs' in text


# ---- a config's study, its plan, and the command that runs it ---------------
class Circuit(Analysis):
    """Writes its losses, as a tool would: python -c."""
    name, label, tool, engine = 'circuit', 'Circuit', 'fake.circuit', 'fake'
    about, result, order, provides, fields = ('a circuit', 'circuit.json', 40,
                                              ('circuit', ), 'fields')

    def tag(self, values):
        return 'buck'

    def command(self, values, board, out, preview=False, results=None):
        import sys
        if not values.get('model'):
            raise ValueError('name the circuit model')
        code = (
            "import json, os, sys; out = sys.argv[1]; os.makedirs(out, "
            "exist_ok=True); print('circuit: solved'); "
            "json.dump(dict(loss=float(sys.argv[2]), fields=sys.argv[3]), "
            "open(os.path.join(out, 'circuit.json'), 'w'))"
        )
        return [
            sys.executable, '-c', code, out,
            str(values.get('loss', 1.0)),
            str(bool(values.get('fields')))
        ]


class Heat(Analysis):
    """Starts from the circuit's result; `fail` makes its tool exit 3."""
    name, label, tool, engine = 'thermal', 'Thermal', 'fake.thermal', 'fake'
    about, result, order, provides = ('the temperature', 'thermal.json', 50,
                                      ('thermal', ))
    key = 'grid'

    def owns(self, folder, preview=False):
        f = os.path.join(folder, 'thermal.json')
        return not preview and os.path.isfile(f) and self.key in open(f).read()

    def command(self, values, board, out, preview=False, results=None):
        import sys
        src = (results or {}).get('circuit')
        if not src:
            raise ValueError('run the circuit first')
        code = (
            "import json, os, sys; out, src, key, fail = sys.argv[1:5]; "
            "os.makedirs(out, exist_ok=True); loss = "
            "json.load(open(os.path.join(src, 'circuit.json')))['loss']; "
            "sys.exit(3) if fail == 'True' else json.dump(dict(tool=key, T=25 "
            "+ 10 * loss, board=sys.argv[5]), open(os.path.join(out, "
            "'thermal.json'), 'w'))"
        )
        return [
            sys.executable, '-c', code, out, src, self.key,
            str(bool(values.get('fail'))),
            os.path.basename(board)
        ]


class Heat3D(Heat):
    name, label, engine, order, key = ('elmer_thermal', 'Temperature (Elmer)',
                                       'Elmer', 60, 'elmer')


def _config(tmp_path, text, board='b.kicad_pcb', name='c.toml'):
    p = tmp_path / name
    # (a literal string: a Windows path's \\)
    p.write_text(f"board = '{board}'\n" + text, encoding='utf-8')
    return str(p)


def test_a_config_says_what_its_study_asks(fakes, tmp_path):
    fakes(Drop, Circuit, Heat, Heat3D)
    cfg = _config(tmp_path, '''
[study]
run = ["sparams", "antenna", "lines", "dc", "thermal"]
[study.sparams]
engine = ["mom", "OPENEMS"]         # an alias, a name in capitals
fields = false
[study.lines]
engine = "auto"
[study.thermal]
engine = ["fake", "Elmer"]          # as a list shows them
fields = true
[analysis.circuit]
model = "m.json"
''')
    st = study.read_config(cfg)
    assert (st.said == 'study' and list(st.asked)
            == ['sparams', 'antenna', 'lines', 'dc', 'thermal'])
    assert (st.asked == dict(sparams=['mom25d',
                                      'openems'], antenna=[], lines=[], dc=[],
                             thermal=['thermal', 'elmer_thermal']))
    assert st.fields == dict(sparams=False, thermal=True)
    # [analysis] run, as before the questions: rf is S-parameters, an analysis
    # the questions it answers
    old = _config(tmp_path, '[analysis]\nrun = ["rf", "pdn", "thermal"]\n')
    st = study.read_config(old)
    assert (st.said == 'analysis' and st.asked == dict(
        sparams=[], dc=['pdn'], pdn=['pdn'], thermal=['thermal']))
    assert (study.read_config(_config(tmp_path, '')).asked == dict(sparams=[])
            and study.read_config(old).fields == {})
    # no run: the questions that have a table
    assert (list(study.read_config(_config(
        tmp_path, '[study.dc]\n[study.thermal]\nengine = "elmer_thermal"\n'
    )).asked) == ['dc', 'thermal'])
    for text, why in (
        ('[study]\nrun = ["warmth"]\n', "no question 'warmth': one of sparams"),
        ('[study]\nrun = ["thermal"]\n[study.thermal]\nengine = "pdn"\n',
         "no engine 'pdn' for 'thermal': one of thermal, elmer_thermal"),
        ('[study]\nrun = ["dc"]\n[study.dc]\nengine = "elmer_dc"\n',
         "no engine 'elmer_dc' for 'dc': one of pdn"),
        ('[study]\nrun = ["sparams"]\n[study.sparams]\nengine = "palace"\n',
         "no engine 'palace' for 'sparams'"),
        ('[analysis]\nrun = ["mqs"]\n',
         r"\[analysis\] run: no analysis 'mqs': one of rf")):
        with pytest.raises(ValueError, match=why):
            study.read_config(_config(tmp_path, text))
    lax = study.read_config(
        _config(
            tmp_path,
            '[study]\nrun = ["warmth", "dc"]\n[study.dc]\nengine = "elmer_dc"\n'
        ), strict=False)
    # kept as written: a front end greys them
    assert lax.asked == dict(warmth=[], dc=['elmer_dc'])
    assert (study.engine_named('lines', 'lines') == 'lines'
            and study.engine_named('sparams', 'Auto') is None)
    assert study.engine_named('thermal', 'ELMER_THERMAL') == 'elmer_thermal'


def test_a_study_is_written_into_its_config(fakes, tmp_path):
    fakes(Drop, Circuit, Heat, Heat3D)
    cfg = _config(
        tmp_path,
        'fmin = 1.0\n\n[study]\nrun = ["dc"]\n[study.dc]\nengine = '
        '"pdn"\n\n[analysis.pdn]\nrail = "5V"\n'
    )
    st = study.Study(
        dict(sparams=[], thermal=['thermal', 'elmer_thermal'],
             circuit=['circuit']), dict(circuit=True, sparams=False))
    study.write_config(cfg, st)
    text = open(cfg).read()
    assert (text.count('[study]') == 1 and '[study.dc]' not in text
            and 'fmin = 1.0' in text and 'rail = "5V"' in text)
    assert ('run = ["sparams", "thermal", "circuit"]' in text
            and 'engine = ["thermal", "elmer_thermal"]' in text)
    assert ('[study.circuit]\nengine = "circuit"\nfields = true' in text
            and '[study.sparams]\nfields = false' in text)
    back = study.read_config(cfg)
    assert (back.asked == st.asked and back.fields == st.fields
            and back.said == 'study')
    study.write_config(cfg, None)
    assert ('[study' not in open(cfg).read()
            and 'rail = "5V"' in open(cfg).read())
    assert study.read_config(cfg).asked == dict(sparams=[])


def test_the_runs_a_study_takes_in_order(fakes):
    fakes(Drop, Circuit, Heat, Heat3D)
    S, plan = study.Study, study.plan
    step = lambda st: (st.name, st.engines, st.questions, st.mode, st.fields)
    # as the settings say
    assert ([step(s) for s in plan(S(dict(sparams=[])))
             ] == [('rf', (), ('sparams', ), '', None)])
    assert ([step(s) for s in plan(S(dict(sparams=[]), said='study'))
             ] == [('rf', (), ('sparams', ), 'sparams', None)])
    # the analyses in the registry's order, whatever the order asked: the
    # temperature after the circuit
    steps = plan(
        S(
            dict(thermal=['elmer_thermal', 'thermal'], circuit=[], dc=[],
                 sparams=['mom25d'], lines=[]),
            dict(circuit=True, sparams=False, lines=False), 'study'))
    assert ([step(s) for s in steps] == [
        ('rf', ('mom25d', 'lines'), ('sparams', 'lines'), 'sparams', False),
        ('pdn', ('pdn', ), ('dc', ), '', None),
        ('circuit', ('circuit', ), ('circuit', ), '', True),
        ('thermal', ('thermal', ), ('thermal', ), '', None),
        ('elmer_thermal', ('elmer_thermal', ), ('thermal', ), '', None)
    ])
    # an antenna: its run answers S-parameters for the same solvers; other
    # solvers' S-parameters are a second run
    assert ([
        step(s)
        for s in plan(S(dict(antenna=['openems'], sparams=[]), said='study'))
    ] == [('rf', ('openems', ), ('antenna', 'sparams'), 'antenna', None)])
    assert ([
        step(s) for s in plan(
            S(dict(antenna=['openems'], sparams=['openems', 'mom25d', 'qucs']),
              said='study'))
    ] == [('rf', ('openems', ), ('antenna', ), 'antenna', None),
          ('rf', ('openems', 'mom25d', 'qucs'), ('sparams', ), 'sparams', None)
          ])
    assert ([step(s) for s in plan(S(dict(lines=[])))
             ] == [('rf', ('lines', ), ('lines', ), '', None)])
    for asked, why in ((dict(antenna=['qucs']),
                        "'qucs' does not answer 'antenna'"),
                       (dict(sparams=['pdn']),
                        "'pdn' does not answer 'sparams'"),
                       (dict(nearfield=[]),
                        "no engine installed answers 'nearfield'")):
        with pytest.raises(ValueError, match=why):
            plan(S(asked))
    lines = study.describe(steps)
    assert (
        lines[0] ==
        '  S-parameters, Trace impedances: mom25d (MoM), lines, without field '
        'dumps'
    )
    assert (lines[2] == '  Circuit: fake (fake.circuit), with field dumps'
            and lines[4] == '  Temperature: Elmer (fake.thermal)')


def test_the_study_command_runs_them(fakes, tmp_path, capsys, demo_files):
    """kicad2ems study: each run into a folder of its own with its mark, an
    analysis's settings from its table, one that starts from another's result
    given it, one that fails or cannot start not stopping the rest."""
    import json
    from kicad2ems.core.cli import main
    fakes(Drop, Circuit, Heat, Heat3D)
    base, _ = demo_files
    out = tmp_path / 'runs'
    cfg = _config(tmp_path, f'''out = '{out}'
solvers = ["mom25d"]
fmin = 1.0
fmax = 2.0
points = 5
no_report = true

[study]
run = ["sparams", "circuit", "thermal"]
[study.thermal]
engine = ["fake", "Elmer"]
[study.circuit]
fields = true

[analysis.circuit]
model = "m.json"
loss = 2.5
[analysis.elmer_thermal]
fail = true
''', board=base + '.kicad_pcb')
    assert main(['study', cfg, '--list']) == 0
    assert (capsys.readouterr().out.splitlines() == [
        "  S-parameters: the settings' solvers",
        '  Circuit: fake (fake.circuit), with field dumps',
        '  Temperature: fake (fake.thermal)',
        '  Temperature: Elmer (fake.thermal)'
    ])
    assert main(['study', cfg]) == 1  # Elmer's tool exits 3
    text = capsys.readouterr().out
    name = os.path.basename(base)
    made = {d.name: d for d in out.iterdir()}
    import re
    kinds = sorted(
        re.sub(r'_?\d{8}_\d{6}$', '', n[len(name) + 1:]) for n in made)
    assert (kinds == ['', 'circuit_buck', 'elmer_thermal_run',
                      'thermal_run']), list(made)
    by = {study.marked(str(d))['engine']: d for d in made.values()}
    # and the config it came from
    assert (study.marked(str(by['rf'])) == dict(
        engine='rf', questions=('sparams', ), preview=False,
        config=os.path.abspath(cfg)))
    assert ((by['rf'] / f"{name}_mom25d.s2p").exists()
            and (by['rf'] / f"{name}_run.json").exists())
    # its table, the fields asked
    assert (json.load(open(by['circuit'] / 'circuit.json')) == dict(
        loss=2.5, fields='True'))
    assert (json.load(open(by['thermal'] / 'thermal.json')) == dict(
        tool='grid', T=50.0, board=name + '.kicad_pcb'))
    assert not (by['elmer_thermal'] / 'thermal.json').exists()
    assert analyses.analysis_of(str(by['thermal'])) == ('thermal', False)
    assert 'study of ' + name in text and '  circuit: solved' in text
    tail = text[text.rindex('study:'):].splitlines()
    assert ([re.sub(r', \d+ s: .*$', '', t) for t in tail[1:]] == [
        '  S-parameters (the solvers)               done',
        '  Circuit (fake)                           done',
        '  Temperature (fake)                       done',
        '  Temperature (Elmer)                      failed (exit 3)'
    ])
    # only one question; its engine starts from the circuit's result of before;
    # a setting missing: not started
    assert (main(['study', cfg, '--only', 'thermal']) == 1
            and len(list(out.iterdir())) == 6)
    assert 'Circuit' not in capsys.readouterr().out.split('study:')[-1]
    bare = _config(
        tmp_path, f"out = '{tmp_path / 'bare'}'\n" +
        '[study]\nrun = ["circuit", "thermal"]\n[study.thermal]\nengine = '
        '"fake"\n',
        board=base + '.kicad_pcb', name='bare.toml')
    assert main(['study', bare]) == 1
    text = capsys.readouterr().out
    assert ('study: Circuit: not started: name the circuit model' in text
            and 'study: Thermal: not started: run the circuit first' in text)
    assert (main(['study', cfg, '--only', 'pdn']) == 2
            and 'nothing of that in this study' in capsys.readouterr().out)
    assert (main(['study', str(tmp_path / 'none.toml')]) == 2
            and 'No such file' in capsys.readouterr().out)
    # the solvers' preview: no analysis has one
    assert main(['study', cfg, '--preview', '--only', 'sparams']) == 0
    assert any(d.name == name + '_preview' and study.marked(str(d))['preview']
               for d in out.iterdir())


class Rails(Drop):
    """A rail's tool, as a study runs it for each of a board's rails: writes
    what it was given; a rail named 'bad' exits 4, one with no name does not
    start."""
    several = 'rail'

    def tag(self, values):
        return str(values.get('rail') or 'rail')

    def command(self, values, board, out, preview=False, results=None):
        import sys
        if not values.get('rail'):
            raise ValueError('name the rail')
        code = (
            "import json, os, sys; out, rail, volts, no_pdn = sys.argv[1:5]; "
            "os.makedirs(out, exist_ok=True); print('pdn: rail', rail); "
            "sys.exit(4) if rail == 'bad' else json.dump(dict(rail=rail, "
            "volts=float(volts), no_pdn=no_pdn), open(os.path.join(out, "
            "'pdn.json'), 'w'))"
        )
        return [
            sys.executable, '-c', code, out, values['rail'],
            str(values.get('volts', 0)),
            str(values.get('no_pdn'))
        ]


class Coupled(Analysis):
    """Its variants: its own check, or a solver's beside it (as the
    crosstalk's); writes the check it was given."""
    name, label, tool, engine = 'crosstalk', 'Crosstalk', 'fake.xt', 'lines'
    about, result, order, provides = ('crosstalk', 'xt.json', 15,
                                      ('crosstalk', ))
    variants = (('', 'lines', dict(check='')),
                ('mom25d', 'lines + mom25d', dict(check='mom25d')),
                ('gone', 'lines + gone', dict(check='gone')))

    def check_with(self, sets):
        return sets['check'] != 'gone', 'gone: not installed'

    def command(self, values, board, out, preview=False, results=None):
        import sys
        code = ("import json, os, sys; os.makedirs(sys.argv[1], exist_ok=True);"
                " json.dump(dict(check=sys.argv[2], nets=sys.argv[3]), "
                "open(os.path.join(sys.argv[1], 'xt.json'), 'w'))")
        return [sys.executable, '-c', code, out, values.get('check', ''),
                values.get('nets', '')]


def test_an_analysis_s_variants_are_engines_of_its_question(
        fakes, tmp_path, capsys, demo_files):
    """Each variant an engine (crosstalk_mom25d: the analysis run with check =
    mom25d), named by its label's last word too; one whose solver is missing
    says so; the engine chosen sets the setting, over a table's own; a table's
    check of before the variants is that engine; the run's mark names it."""
    import json
    from kicad2ems.core.cli import main
    fakes(Coupled)
    es = study.engines_of('crosstalk')
    assert ([(e.name, e.label, e.run, e.sets) for e in es] == [
        ('crosstalk', 'lines', 'crosstalk', dict(check='')),
        ('crosstalk_mom25d', 'lines + mom25d', 'crosstalk',
         dict(check='mom25d')),
        ('crosstalk_gone', 'lines + gone', 'crosstalk', dict(check='gone'))
    ])
    assert es[1].check()[0] and es[2].check() == (False, 'gone: not installed')
    assert ([study.engine_named('crosstalk', t)
             for t in ('mom25d', 'lines + MoM25D', 'lines', 'crosstalk_gone')]
            == ['crosstalk_mom25d', 'crosstalk_mom25d', 'crosstalk',
                'crosstalk_gone'])
    base, _ = demo_files
    out = tmp_path / 'runs'
    head = (f"out = '{out}'\n[study]\nrun = ['crosstalk']\n")
    tab = "[analysis.crosstalk]\nnets = 'A,B'\n"
    cfg = _config(tmp_path, head + "[study.crosstalk]\nengine = 'mom25d'\n"
                  + tab + "check = 'gone'\n", board=base + '.kicad_pcb')
    assert study.read_config(cfg).asked == {'crosstalk': ['crosstalk_mom25d']}
    assert (study.describe(study.plan(study.read_config(cfg)))
            == ['  Crosstalk: lines + mom25d (fake.xt)'])
    assert main(['study', cfg]) == 0
    (d, ) = list(out.iterdir())
    assert (json.load(open(d / 'xt.json')) == dict(check='mom25d', nets='A,B')
            and study.marked(str(d))['engine'] == 'crosstalk_mom25d'
            and analyses.analysis_of(str(d)) == ('crosstalk', False))
    assert '(lines + mom25d)' in capsys.readouterr().out.split('study:')[-1]
    # a table's check, no engine named: that variant; the line model named:
    # no check, whatever the table says
    old = _config(tmp_path, head + tab + "check = 'mom25d'\n", name='o.toml')
    assert study.read_config(old).asked == {'crosstalk': ['crosstalk_mom25d']}
    old = _config(tmp_path, "[analysis]\nrun = ['crosstalk']\n" + tab
                  + "check = 'mom25d'\n", name='o.toml')
    assert study.read_config(old).asked == {'crosstalk': ['crosstalk_mom25d']}
    lines = _config(tmp_path, head + "[study.crosstalk]\nengine = 'lines'\n"
                    + tab + "check = 'mom25d'\n", board=base + '.kicad_pcb',
                    name='l.toml')
    assert main(['study', lines]) == 0
    new = max(out.iterdir(), key=os.path.getmtime)
    assert json.load(open(new / 'xt.json'))['check'] == ''
    study.write_config(cfg, study.Study({'crosstalk': ['crosstalk_gone']}))
    assert study.read_config(cfg).asked == {'crosstalk': ['crosstalk_gone']}


def test_an_analysis_given_several_tables_is_a_run_for_each(
        fakes, tmp_path, capsys):
    """[[analysis.pdn]]: a board's rails, each a run of its own in its own
    folder, named by its tag; one that fails or cannot start does not stop
    the others, and the study says which."""
    import json
    import re
    from kicad2ems.core.cli import main
    from kicad2ems.core.api import frontend
    fakes(Rails, Circuit)
    assert (analyses.registry()['pdn'].several == 'rail'
            and analyses.registry()['circuit'].several == '')
    out = tmp_path / 'runs'
    cfg = _config(tmp_path, f'''out = '{out}'
[study]
run = ["dc"]

[[analysis.pdn]]
rail = "+5V"
volts = 5
[[analysis.pdn]]
rail = "+3V3"
volts = 3.3
''')
    (tmp_path / 'b.kicad_pcb').write_text('')
    sets = analyses.read_sets(cfg)
    assert (sets == dict(
        pdn=[dict(rail='+5V', volts=5),
             dict(rail='+3V3', volts=3.3)]))
    # the first, as before
    assert (analyses.read_config(cfg) == (None,
                                          dict(pdn=dict(rail='+5V', volts=5))))
    assert frontend.read_analysis_sets is analyses.read_sets
    assert main(['study', cfg, '--list']) == 0
    assert (capsys.readouterr().out.splitlines() == [
        '  Rail drop: fake (fake.pdn), 2 runs: +5V, +3V3'
    ])
    assert main(['study', cfg]) == 0
    text = capsys.readouterr().out
    assert ('study of b.kicad_pcb: 2 run(s)' in text
            and '  pdn: rail +5V' in text and '  pdn: rail +3V3' in text)
    made = sorted(d.name for d in out.iterdir())
    assert ([re.sub(r'_\d{8}_\d{6}$', '', n)
             for n in made] == ['b_pdn_3V3', 'b_pdn_5V'])
    got = {
        json.load(open(out / n / 'pdn.json'))['rail']: json.load(
            open(out / n / 'pdn.json'))
        for n in made
    }
    assert (got == {
        '+5V': dict(rail='+5V', volts=5.0, no_pdn='True'),
        '+3V3': dict(rail='+3V3', volts=3.3, no_pdn='True')
    })
    assert all(
        study.marked(str(out / n)) == dict(engine='pdn', questions=(
            'dc', ), preview=False, config=os.path.abspath(cfg)) for n in made)
    tail = text[text.rindex('study:'):].splitlines()
    assert ([re.sub(r', \d+ s: .*$', '', t) for t in tail[1:]] == [
        '  Rail drop (fake: +5V)                    done',
        '  Rail drop (fake: +3V3)                   done'
    ])
    # one that fails, one that cannot start: said by name, the third still run
    bad = _config(tmp_path, f'''out = '{tmp_path / "more"}'
[study]
run = ["dc", "pdn"]
[[analysis.pdn]]
rail = "bad"
[[analysis.pdn]]
volts = 1.8
[[analysis.pdn]]
rail = "+1V8"
volts = 1.8
''', name='bad.toml')
    assert main(['study', bad]) == 1
    text = capsys.readouterr().out
    assert 'study: Power integrity: rail: not started: name the rail' in text
    tail = text[text.rindex('study:'):].splitlines()
    assert [re.sub(r', \d+ s: .*$', '', t) for t in tail[1:]] == [
        '  Rail drop, Rail impedance (fake: bad)    failed (exit 4)',
        '  Rail drop, Rail impedance (fake: rail)   not started: name the rail',
        '  Rail drop, Rail impedance (fake: +1V8)   done']
    assert (json.load(
        open(next((tmp_path / 'more').glob('b_pdn_1V8_*'))
             / 'pdn.json'))['no_pdn'] == 'False')


def test_two_engines_answers_side_by_side(fakes, tmp_path):
    """compare: the figures engines name alike in one row each, an engine's own
    where only it has them; the spread between those that give one.
    default_fields: what an engine is asked when its dumps are wanted."""
    class A(Heat):
        def figures(self, folder):
            return [
                dict(name='hottest', value=77.4, unit='C'),
                dict(name='D1', value=76.2, unit='C'),
                dict(name='film', value=10, unit='W/m2K'),
                dict(name='', value=1.0),
                dict(name='bad', value='x')
            ]

    class B(Heat3D):
        def figures(self, folder):
            if folder.endswith('broken'):
                raise OSError('no file')
            return [
                dict(name='D1', value=76.8, unit='C'),
                dict(name='hottest', value=79.1, unit='C'),
                dict(name='coolest', value=59.6, unit='C')
            ]
    fakes(Circuit, A, B)
    c = study.compare(dict(thermal='x', elmer_thermal='y', rf='z'))
    assert (c['engines'] == ['thermal', 'elmer_thermal']
            and c['labels'] == ['fake', 'Elmer'])
    assert ([(r['name'], r['unit'], r['values'])
             for r in c['rows']] == [('hottest', 'C', [77.4, 79.1]),
                                     ('D1', 'C', [76.2, 76.8]),
                                     ('film', 'W/m2K', [10.0, None]),
                                     ('coolest', 'C', [None, 59.6])])
    assert ([
        None if r['spread'] is None else round(r['spread'], 2)
        for r in c['rows']
    ] == [1.7, 0.6, None, None])
    assert (study.compare(
        dict(thermal='x',
             elmer_thermal='broken'))['rows'][0]['values'] == [77.4, None])
    assert ('hottest' in study.questions()['thermal'].figures
            and study.questions()['sparams'].figures == '')
    assert Analysis().figures('anywhere') == []
    assert (study.default_fields('circuit', 1.0, 3.0) == dict(fields=True)
            and study.default_fields('thermal', 1, 3) == {})
    assert (study.default_fields('openems', 1.0, 6.0,
                                 'In1.Cu') == dict(dump=['E@In1.Cu@3.5']))
    assert (study.default_fields('openems', 1e-5, 1e-2,
                                 sweep='log') == dict(dump=['E@F.Cu@0.000316']))
    # its current maps: the frequency
    assert study.default_fields('mom25d', 1.0, 6.0) == dict(mom_dump=[3.5])
    assert (study.default_fields('qucs', 1, 2) == {}
            and study.default_fields('nobody', 1, 2) == {})


def test_what_a_run_still_needs_and_what_is_shown_first(fakes, tmp_path,
                                                        demo_files):
    """rf_needs: what an S-parameter run of a form's values lacks, with
    the setting it is set by (cheap: the board's footprints, no model). An
    analysis's missing(): what its command raises, unless what it uses is to
    come in the same run. An option's level: kicad2ems's few, and those a solver
    names in `basic`."""
    from kicad2ems.core.study import rf_needs
    from kicad2ems.core.config import all_options, CORE
    from kicad2ems.core.analyses import Analysis
    assert rf_needs({}) == [dict(text='choose the board', key='board')]
    assert (rf_needs(dict(board=str(tmp_path / 'no.kicad_pcb'))) == [
        dict(text='the board is not there: no.kicad_pcb', key='board')
    ])
    board = demo_files[0] + '.kicad_pcb'
    # (its ports: from its pos file)
    assert rf_needs(dict(board=board, fmin='1', fmax='8')) == []
    assert rf_needs(dict(board=board, pos=demo_files[0] + '-top.pos')) == []
    assert ([m['key'] for m in rf_needs(dict(board=board, fmin='8', fmax='2'))
             ] == ['fmax'])
    assert [m['key'] for m in rf_needs(dict(board=board, fmin='x'))] == ['fmin']
    bare = tmp_path / 'bare.kicad_pcb'
    bare.write_text(
        '(kicad_pcb (version 20240108) (generator "pcbnew") (layers (0 "F.Cu" '
        'signal) (31 "B.Cu" signal)))'
    )
    got = rf_needs(dict(board=str(bare)))
    assert ([m['key'] for m in got] == ['ports']
            and got[0]['text'].startswith('the board has no port'))
    assert (rf_needs(dict(board=str(bare), ports=['P1@1,2'])) == []
            and rf_needs(dict(board=str(bare), pos='b.pos')) == [])

    class Rail(Analysis):
        name = 'rail'

        def command(self, values, board, out, preview=False, results=None):
            if not values.get('rail'):
                raise ValueError('name the rail')
            return ['x']

    class Heat(Analysis):
        name, uses = 'heat', ('circuit',)

        def command(self, values, board, out, preview=False, results=None):
            if not values.get('loss') and not (results or {}).get('circuit'):
                raise ValueError('give the losses, or run the circuit first')
            return ['x']

    assert (Rail().missing({}, None) == [dict(text='name the rail', key=None)]
            and Rail().missing(dict(rail='3V3'), None) == [])
    assert Heat().missing({}, None)[0]['text'].startswith('give the losses')
    # asked with it: to come
    assert Heat().missing({}, None, dict(circuit=None)) == []
    assert (Heat().missing({}, None, dict(circuit='out/c')) == []
            and Rail().missing({}, None, dict(circuit=None)) != [])
    from kicad2ems.core.solvers import registry
    basic = {o.key for _, o in all_options() if o.level == 'basic'}
    assert ({o.key
             for o in CORE
             if o.level == 'basic'
             } == {'board', 'ports', 'fmin', 'fmax', 'points'})
    for sv in registry().values():  # a solver's own: those it names
        assert set(sv.basic) & {o.key for o in sv.options} <= basic
    assert 'sweep' not in basic and 'out' not in basic
