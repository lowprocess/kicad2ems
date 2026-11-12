"""
A study: what can be asked of a board, and the programs that answer.

A *question* is something to find out with a known kind of answer: the ports' S-parameters, an antenna's pattern,
every trace's impedance, a rail's DC drop, its impedance, a converter's near field, a circuit on the copper, the
capacitance between nets, the board's temperature. An *engine* is a program that answers one or more of them: the
S-parameter solvers (kicad2ems.solvers: openEMS, gfdtd, mom25d, the line model ...) and the analysis plugins
(kicad2ems.analyses: pcbqs's tools, kicad2ems-fem's Elmer and Palace tools). Several engines may answer one
question (the temperature by pcbqs or by Elmer), and one engine several questions from one run (a rail's drop and
its impedance).

    from kicad2ems.study import study_tree, engines, questions
    for g in study_tree():                      # what a front end lists: by group, each question with its engines
        for row in g['questions']:
            row['question'].label, [e.label for e in row['engines']], row['missing']
    engines()['pdn'].provides                   # ('dc', 'pdn')

A front end shows the tree, lets each question asked name its engine (or several, to compare them), and starts
them: an analysis engine by the command its plugin gives (Analysis.command, its settings first passed through
Analysis.for_questions with the questions asked of it), the solver engines together as one kicad2ems run. Whoever
starts a run leaves a mark in its folder (mark) saying which engine made it and what was asked, so a folder is
known as that engine's result even when two engines leave a file of the same name (analyses.analysis_of).
"""
import json
import os
from dataclasses import dataclass, field

GROUPS = ('RF', 'Power', 'Thermal', 'Other')
MARK = 'kicad2ems-study.json'               # in a run's folder: the engine that made it and what was asked of it


@dataclass(frozen=True)
class Question:
    """Something to find out about a board."""
    name: str               # 'sparams': how configs, engines and run folders name it
    label: str              # 'S-parameters': as a list shows it
    group: str              # one of GROUPS
    about: str              # one line: what the answer is
    shares: str = ''        # the question whose run also answers this one, when one engine does both ('pdn': 'dc')
    answer: str = 'report'  # the form of the answer: 'touchstone' (S-parameters, compared as networks), 'antenna'
                            # (a far field), 'report' (text, tables, curves and maps: Analysis.report / .curves)
    order: int = 50         # its place in its group
    extra: str = ''         # the kicad2ems extra that installs an engine for it ('' built in, or none)


QUESTIONS = {q.name: q for q in (
    Question('sparams', 'S-parameters', 'RF', "the ports' S-parameters over the band", answer='touchstone', order=10,
             extra='mom'),
    Question('antenna', 'Antenna pattern', 'RF', "a driven port's far field: pattern, gain, efficiency",
             answer='antenna', order=20, extra='mom'),
    Question('lines', 'Trace impedances', 'RF', "every trace's impedance and delay, from the line model", order=30),
    Question('dc', 'Rail drop', 'Power', "a rail's DC drop, its current density and its loss", order=10, extra='qs'),
    Question('pdn', 'Rail impedance', 'Power', "the impedance a rail's loads see over frequency", shares='dc',
             order=20, extra='qs'),
    Question('nearfield', 'Converter near field', 'Power', "a converter's input loop and the magnetic field over it",
             order=30, extra='qs'),
    Question('circuit', 'Circuit', 'Power', "a switching circuit on the board's copper: waveforms, losses, stress",
             order=40, extra='qs'),
    Question('capacitance', 'Capacitance', 'Power', "the capacitance between nets", order=50, extra='fem'),
    Question('thermal', 'Temperature', 'Thermal', "the board's steady temperature from what its parts dissipate",
             order=10, extra='qs'),
)}


@dataclass(frozen=True)
class Engine:
    """A program that answers questions, as a front end lists it: a solver plugin or an analysis plugin."""
    name: str               # its registry name: 'mom25d', 'pdn', 'elmer_thermal' (its config table too)
    label: str              # as a list shows it beside a question: 'mom25d (MoM)', 'pcbqs', 'Elmer'
    kind: str               # 'solver' (kicad2ems.solvers: run together, as one kicad2ems run) or 'analysis'
    provides: tuple         # the questions it answers
    plugin: object = field(compare=False, repr=False)       # the Solver or the Analysis
    fields: str = ''        # the setting that makes it write field dumps too ('' it cannot, or always does)
    previewed: bool = False
    order: int = 50

    def check(self):
        """(usable, one line): the plugin's own check."""
        return self.plugin.check()


def provided(a):
    """The questions an analysis answers: what it declares, else the one of its own name."""
    return tuple(a.provides) or (a.name,)


def questions():
    """{name: Question}: kicad2ems's, then those the installed analyses bring for what it has none for (a plugin's
    `questions`; an analysis that declares nothing is a question of its own name, in the group Other)."""
    from .analyses import registry
    out = dict(QUESTIONS)
    for a in registry().values():
        for q in getattr(a, 'questions', ()) or ():
            out.setdefault(q.name, q)
        for name in provided(a):
            out.setdefault(name, Question(name, a.label, 'Other', a.about, order=a.order))
    return out


def engines():
    """{name: Engine}: the solvers (each answers 'sparams'; 'antenna' where it models one; the line model's also
    'lines') and the analyses, in the order lists show them."""
    from .analyses import registry
    from .solvers import registry as solvers
    from .fields.dumps import G_DUMP
    out = {}
    for name, sv in solvers().items():
        what = ('sparams',) + (('lines',) if sv.consumes == 'network' else ())
        if str(sv.capabilities.get('antenna', 'no')).split(':')[0] in ('yes', 'approximated'):
            what += ('antenna',)
        dumps = getattr(sv, 'dumps', None)
        if dumps is None:
            dumps = any(getattr(o, 'group', None) == G_DUMP for o in sv.options)
        out[name] = Engine(name, sv.label, 'solver', what, sv, fields='dump' if dumps else '',
                           previewed=bool(sv.previewed), order=sv.order)
    for name, a in registry().items():
        out[name] = Engine(name, getattr(a, 'engine', '') or a.label, 'analysis', provided(a), a,
                           fields=getattr(a, 'fields', '') or '', previewed=bool(a.previewed), order=a.order)
    return out


def engines_of(question):
    """The engines that answer a question, in their order."""
    return [e for e in engines().values() if question in e.provides]


def study_tree():
    """What a front end lists: [dict(group, questions=[dict(question, engines, missing)])], every question
    kicad2ems or a plugin knows, by group and order. engines: those installed that answer it (their own check
    says whether each can run here); missing: how to get one when there is none ('' otherwise)."""
    qs, es = questions(), list(engines().values())
    tree = []
    for group in GROUPS:
        rows = []
        for q in sorted((q for q in qs.values() if q.group == group), key=lambda q: (q.order, q.name)):
            have = [e for e in es if q.name in e.provides]
            how = '' if have else (f"python -m pip install 'kicad2ems[{q.extra}]'" if q.extra else
                                   'no engine for it is installed')
            rows.append(dict(question=q, engines=have, missing=how))
        if rows:
            tree.append(dict(group=group, questions=rows))
    return tree


def runs_for(asked):
    """The runs a set of questions takes: {engine name: the questions asked of it}, from {question: [engine names]}
    (a question with no engine named: its first). Solver engines are separate entries here; a front end runs them
    as one kicad2ems run. ValueError names a question nobody answers, or an engine that does not answer it."""
    es = engines()
    out = {}
    for q, names in asked.items():
        have = [e.name for e in es.values() if q in e.provides]
        if not have:
            raise ValueError(f"no engine installed answers {q!r}")
        for name in (names or have[:1]):
            if name not in have:
                raise ValueError(f"{name!r} does not answer {q!r}: one of {', '.join(have)}")
            out.setdefault(name, [])
            if q not in out[name]:
                out[name].append(q)
    return {k: tuple(v) for k, v in out.items()}


def mark(folder, engine, asked=(), preview=False):
    """Leave the mark of a run in its folder (made if it is not there): the engine that makes it, the questions
    asked of it, whether it is a preview. Returns the mark's path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, MARK)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(dict(format='kicad2ems-study-run', version=1, engine=engine, questions=list(asked),
                       preview=bool(preview)), fh, indent=1)
    return path


def marked(folder):
    """The mark of a run's folder: dict(engine, questions, preview), or None (no mark, or not readable)."""
    try:
        with open(os.path.join(folder or '', MARK), encoding='utf-8') as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get('format') != 'kicad2ems-study-run' or not d.get('engine'):
        return None
    return dict(engine=str(d['engine']), questions=tuple(d.get('questions') or ()), preview=bool(d.get('preview')))


def check_lines():
    """--check's lines about the questions: each one with the engines that answer it, or how to get one."""
    lines = []
    for g in study_tree():
        for row in g['questions']:
            q = row['question']
            who = ', '.join(e.name for e in row['engines']) or f"none ({row['missing']})"
            lines.append(f"  {g['group']:<8s}{q.label:<22s}{who}")
    return lines
