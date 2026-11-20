"""
A study: what can be asked of a board, and the programs that answer.

A *question* is something to find out with a known kind of answer: the ports'
S-parameters, an antenna's pattern, every trace's impedance, a rail's DC drop,
its impedance, a converter's near field, a circuit on the copper, the
capacitance between nets, the board's temperature. An *engine* is a program that
answers one or more of them: the S-parameter solvers (kicad2ems.core.solvers:
openEMS, gfdtd, mom25d, the line model ...) and the analyses
(kicad2ems.core.analyses: pcbqs's tools, kicad2ems.fem's Elmer and Palace
tools). Several engines may answer one question (the temperature by pcbqs or by
Elmer), and one engine several questions from one run (a rail's drop and its
impedance).

    from kicad2ems.core.study import study_tree, engines, questions
    for g in study_tree():      # what a front end lists: by group, each
                                # question with its engines
        for row in g['questions']:
            (row['question'].label, [e.label for e in row['engines']],
             row['missing'])
    engines()['pdn'].provides   # ('dc', 'pdn')

A front end shows the tree, lets each question asked name its engine (or
several, to compare them), and starts them: an analysis engine by the command
its Analysis gives (Analysis.command, its settings first passed through
Analysis.for_questions with the questions asked of it), the solver engines
together as one kicad2ems run. Whoever starts a run leaves a mark in its folder
(kicad2ems.core.runs.mark) saying which engine made it, what was asked and how
it ended, so a folder is known as that engine's result even when two engines
leave a file of the same name (runs.index, analyses.analysis_of).

A config holds a study as its [study] tables (Study, read_config, write_config),
plan() gives the runs it takes in order, and run() makes them, which is the
command `kicad2ems study board.toml`:

    study = read_config('board.toml')  # Study(asked={'dc': ['pdn',
                                       # 'elmer_dc']}, fields={}, said='study')
    [st.name for st in plan(study)]    # ['pdn', 'elmer_dc']: 'rf' is the
                                       # solvers' run
    run('board.toml')                  # 0: every run finished
"""
import json
import os
import sys
import time
from dataclasses import dataclass, field

from .runs import RF, load_toml, mark, update_toml

__all__ = ['Study', 'RF', 'study_tree', 'questions', 'engines', 'provided',
           'dump_options', 'default_fields', 'runs_for', 'check_lines',
           'load_config', 'read_config', 'write_config', 'plan', 'unmet',
           'engine_named', 'compare', 'rf_needs', 'main']

GROUPS = ('RF', 'Signal integrity', 'Power', 'Thermal', 'Other')


@dataclass(frozen=True)
class Question:
    """Something to find out about a board."""
    # 'sparams': how configs, engines and run folders name it
    name: str
    label: str              # 'S-parameters': as a list shows it
    group: str              # one of GROUPS
    about: str              # one line: what the answer is
    # the question whose run also answers this one, when one engine does both
    # ('pdn': 'dc')
    shares: str = ''
    # the form of the answer: 'touchstone' (S-parameters, compared as networks),
    # 'antenna' (a far field), 'report' (text, tables, curves and maps:
    # Analysis.report / .curves)
    answer: str = 'report'
    order: int = 50         # its place in its group
    # the figures its engines name alike (Analysis.figures), so their answers
    # can be compared
    figures: str = ''


QUESTIONS = {
    q.name: q
    for q in (
        Question('sparams', 'S-parameters', 'RF',
                 "the ports' S-parameters over the band", answer='touchstone',
                 order=10),
        Question('antenna', 'Antenna pattern', 'RF',
                 "a driven port's far field: pattern, gain, efficiency",
                 answer='antenna', order=20),
        Question('lines', 'Trace impedances', 'RF',
                 "every trace's impedance and delay, from the line model",
                 order=30),
        Question(
            'crosstalk', 'Crosstalk', 'Signal integrity',
            "near- and far-end crosstalk between traces for an edge, "
            "and what in the layout makes it", order=10, figures=
            "NEXT <victim>, FEXT <victim>: all its aggressors together, % of "
            "the swing"
        ),
        Question(
            'coupling', 'Board coupling', 'Signal integrity',
            "many nets' crosstalk a layer at a time (a bus by name patterns): "
            "each victim's worst aggressors", order=15, figures=
            "NEXT <victim>, FEXT <victim> [(<layer>)]: all its aggressors on "
            "its layer together, % of the swing"),
        Question(
            'dc', 'Rail drop', 'Power',
            "a rail's DC drop, its current density and its loss", order=10,
            figures="drop <load>: the rail's own copper from the source to the "
            "load's pads, mV (no ground return, no series part); loss: in that "
            "copper, mW"),
        Question('pdn', 'Rail impedance', 'Power',
                 "the impedance a rail's loads see over frequency", shares='dc',
                 order=20),
        Question('nearfield', 'Converter near field', 'Power',
                 "a converter's input loop and the magnetic field over it",
                 order=30),
        Question(
            'circuit', 'Circuit', 'Power',
            "a switching circuit on the board's copper: waveforms, losses, "
            "stress",
            order=40),
        Question(
            'coils', 'Coils and ferrite', 'Power',
            "wound coils beside ferrite plates: their inductances and "
            "coupling (a wireless-power link's coil model)", order=45,
            figures="L <coil>: its inductance, uH; k <coil>,<coil>: two coils' "
            "coupling"),
        Question(
            'capacitance', 'Capacitance', 'Power',
            "the capacitance between nets", order=50,
            figures="C <net>,<net>: between two nets, pF; C <net>: a net to "
            "all the others, pF"),
        Question(
            'thermal', 'Temperature', 'Thermal',
            "the board's steady temperature from what its parts dissipate",
            order=10,
            figures="hottest: the copper's highest temperature, C; <part>: the "
            "board at the part's pads, C"),
    )
}


@dataclass(frozen=True)
class Engine:
    """A program that answers questions, as a front end lists it: a solver or
    an analysis."""
    # its registry name: 'mom25d', 'pdn', 'elmer_thermal' (its config table too)
    name: str
    # as a list shows it beside a question: 'mom25d (MoM)', 'pcbqs', 'Elmer'
    label: str
    # 'solver' (kicad2ems.core.solvers: run together, as one kicad2ems run) or
    # 'analysis'
    kind: str
    provides: tuple         # the questions it answers
    # the Solver or the Analysis
    impl: object = field(compare=False, repr=False)
    # the setting that makes it write field dumps too ('' it cannot, or always
    # does)
    fields: str = ''
    previewed: bool = False
    order: int = 50
    # an analysis engine: the analysis it runs (its own name, or that of the
    # analysis it is a variant of: Analysis.variants, run with the settings
    # `sets` fixes); a solver: ''
    run: str = ''
    sets: dict = field(default_factory=dict, compare=False)

    def check(self):
        """(usable, one line): the Solver's or the Analysis's own check (a
        variant's: Analysis.check_with its settings)."""
        return (self.impl.check_with(self.sets) if self.sets else
                self.impl.check())


def provided(a):
    """The questions an analysis answers: what it declares, else the one of its
    own name."""
    return tuple(a.provides) or (a.name,)


def questions():
    """{name: Question}: kicad2ems's, then those the analyses bring for what it
    has none for (an analysis's `questions`; one that declares nothing is a
    question of its own name, in the group Other)."""
    from .analyses import registry
    out = dict(QUESTIONS)
    for a in registry().values():
        for q in getattr(a, 'questions', ()) or ():
            out.setdefault(q.name, q)
        for name in provided(a):
            out.setdefault(
                name, Question(name, a.label, 'Other', a.about, order=a.order))
    return out


def engines():
    """{name: Engine}: the solvers (each answers 'sparams'; 'antenna' where it
    models one; the line model's also 'lines') and the analyses, in the order
    lists show them."""
    from .analyses import registry
    from .solvers import registry as solvers
    out = {}
    for name, sv in solvers().items():
        what = ('sparams',) + (('lines',) if sv.consumes == 'network' else ())
        if (str(sv.capabilities.get('antenna', 'no')).split(':')[0]
                in ('yes', 'approximated')):
            what += ('antenna',)
        dumps = getattr(sv, 'dumps', None)
        if dumps is None:
            dumps = bool(dump_options(sv))
        out[name] = Engine(name, sv.label, 'solver', what, sv,
                           fields='dump' if dumps else '',
                           previewed=bool(sv.previewed), order=sv.order)
    for name, a in registry().items():
        kw = dict(fields=getattr(a, 'fields', '') or '',
                  previewed=bool(a.previewed), order=a.order, run=name)
        for suffix, label, sets in (getattr(a, 'variants', ()) or
                                    (('', '', {}),)):
            e = name + ('_' + suffix if suffix else '')
            out[e] = Engine(e, label or getattr(a, 'engine', '') or a.label,
                            'analysis', provided(a), a, sets=dict(sets), **kw)
    return out


def dump_options(sv):
    """The options that ask a solver for field dumps: its `dump` (the FDTD
    solvers': FIELD@REGION@GHz specs) or one named <its own>_dump (mom25d's
    mom_dump: the frequencies of its current maps)."""
    from .fields.dumps import G_DUMP
    return [
        o for o in sv.options if o.type is list and (
            (o.key == 'dump' and getattr(o, 'group', None) == G_DUMP)
            or o.key.endswith('_dump'))
    ]


def default_fields(engine, fmin, fmax, layer='F.Cu', sweep='linear'):
    """What an engine is asked for when its field dumps are wanted and none is
    set: {option key: value}. A solver's dump: the E field on `layer` (the first
    port's, a front end knows) at the band's centre; a list of frequencies
    (mom25d's current maps): that centre; an analysis: its `fields` switch.
    fmin, fmax in GHz."""
    e = engines().get(engine)
    if e is None or not e.fields:
        return {}
    if e.kind == 'analysis':
        return {e.fields: True}
    fc = ((fmin * fmax)**0.5 if sweep == 'log' and fmin > 0 else 0.5 *
          (fmin + fmax))
    fc = float(f"{fc:.3g}")
    return {
        o.key: [f"E@{layer}@{fc:g}"] if o.key == 'dump' else [fc]
        for o in dump_options(e.impl)
    }


def engines_of(question):
    """The engines that answer a question, in their order."""
    return [e for e in engines().values() if question in e.provides]


def study_tree():
    """What a front end lists: [dict(group, questions=[dict(question, engines,
    missing)])], every question kicad2ems or an analysis knows, by group and
    order. engines: those that answer it (their own check says whether each can
    run here); missing: why there is none ('' otherwise)."""
    qs, es = questions(), list(engines().values())
    tree = []
    for group in GROUPS:
        rows = []
        for q in sorted((q for q in qs.values() if q.group == group),
                        key=lambda q: (q.order, q.name)):
            have = [e for e in es if q.name in e.provides]
            how = '' if have else 'no engine for it is installed'
            rows.append(dict(question=q, engines=have, missing=how))
        if rows:
            tree.append(dict(group=group, questions=rows))
    return tree


def runs_for(asked):
    """The runs a set of questions takes: {engine name: the questions asked of
    it}, from {question: [engine names]} (a question with no engine named: its
    first). Solver engines are separate entries here; a front end runs them as
    one kicad2ems run. ValueError names a question nobody answers, or an engine
    that does not answer it."""
    es = engines()
    out = {}
    for q, names in asked.items():
        have = [e.name for e in es.values() if q in e.provides]
        if not have:
            raise ValueError(f"no engine installed answers {q!r}")
        for name in (names or have[:1]):
            if name not in have:
                raise ValueError(
                    f"{name!r} does not answer {q!r}: one of {', '.join(have)}")
            out.setdefault(name, [])
            if q not in out[name]:
                out[name].append(q)
    return {k: tuple(v) for k, v in out.items()}


def compare(results):
    """Engines' answers side by side: dict(engines=[names], labels=[as a list
    shows them], rows=[dict(name, unit, values=[a number or None per engine],
    spread)]) from {engine: its result's folder}. A figure is a row when at
    least one engine gives it, in the first engine's order and then the others'
    own; spread: the largest difference between engines that give it (None with
    fewer than two)."""
    es = engines()
    names = [n for n in results if n in es and es[n].kind == 'analysis']
    got, order, units = {}, [], {}
    for n in names:
        try:
            figs = es[n].impl.figures(results[n]) or []
        # (an analysis's own reader)
        except Exception:  # noqa: BLE001
            figs = []
        got[n] = {}
        for f in figs:
            name = str(f.get('name', ''))
            if not name or not isinstance(f.get('value'), (int, float)):
                continue
            got[n][name] = float(f['value'])
            units.setdefault(name, str(f.get('unit', '')))
            if name not in order:
                order.append(name)
    rows = []
    for name in order:
        vals = [got[n].get(name) for n in names]
        have = [v for v in vals if v is not None]
        rows.append(
            dict(name=name, unit=units[name], values=vals,
                 spread=((max(have) - min(have)) if len(have) > 1 else None)))
    return dict(engines=names, labels=[es[n].label for n in names], rows=rows)


def check_lines():
    """--check's lines about the questions: each one with the engines that
    answer it."""
    lines = []
    for g in study_tree():
        for row in g['questions']:
            q = row['question']
            who = (', '.join(e.name for e in row['engines']) or
                   (f"none ({row['missing']})" if row['missing'] else 'none'))
            lines.append(f"  {g['group']:<18s}{q.label:<22s}{who}")
    return lines


# ---- a config's study: what it asks, the runs that takes, and running them --
# what solver engines answer: together, one kicad2ems run
SOLVER_QUESTIONS = ('sparams', 'antenna', 'lines')


@dataclass
class Study:
    """What a study asks, as a config holds it:

        [study]
        run = ["sparams", "dc", "thermal"]  # the questions, by name (kicad2ems
                                            # --check lists them)
        [study.sparams]
        engine = ["mom25d", "pcbqs"]        # who answers: one, or several to
                                            # compare; none: the default
        [study.thermal]
        engine = "Elmer"                    # an engine's name ('elmer_thermal')
                                            # or how a list shows it
        fields = true                       # its field dumps too, where it can
                                            # write them

    An engine's own settings stay where they were: a solver's under
    [solver.<name>] (and the top level), an analysis's under
    [analysis.<name>]."""
    # {question: [engine names]}, in the order written; []: its default
    asked: dict
    # {question: True | False}: its run's field dumps asked for, or not
    fields: dict = field(default_factory=dict)
    # the table that said so: 'study', 'analysis' ([analysis] run) or ''
    said: str = ''


@dataclass(frozen=True)
class Step:
    """One run of a study: the solvers together (a kicad2ems run), or one
    analysis."""
    kind: str               # 'solvers' or 'analysis'
    # solvers: those to run, () for the settings' own choice (auto); analysis:
    # (its name,)
    engines: tuple
    questions: tuple        # what it is asked
    # solvers: 'antenna' or 'sparams' where the study says; '': as the settings
    # say
    mode: str = ''
    # True / False: field dumps asked for, or not; None: as the settings say
    fields: object = None

    @property
    def name(self):
        return RF if self.kind == 'solvers' else self.engines[0]


def engine_named(question, text, es=None):
    """The registry name of the engine `text` means among those that answer a
    question: its name ('elmer_thermal', 'mom25d', 'crosstalk_gfdtd'), a
    solver's alias ('mom', 'lines'), the name a list shows it by ('pcbqs',
    'Elmer', 'line model + gfdtd') or a variant's last word ('gfdtd'), in any
    case; None for 'auto' (the default engine). ValueError when nothing
    matches, or more than one does."""
    from .solvers import LINES, aliases
    want = str(text).strip().lower()
    if want in ('', 'auto'):
        return None
    have = [e for e in (es or engines()).values() if question in e.provides]
    if (want in ('lines', 'line', 'fast')
            and any(e.kind == 'solver' and e.impl.consumes == 'network'
                    for e in have)):
        return LINES
    solver = aliases().get(want)
    hits = [
        e.name
        for e in have
        if want in (e.name.lower(), e.label.lower(),
                    e.label.split(' (')[0].lower(),
                    e.label.split(' + ')[-1].lower()) or (
                        e.kind == 'solver' and solver == e.name)
    ]
    if len(hits) == 1:
        return hits[0]
    names = ', '.join(e.name for e in have) or 'none installed'
    raise ValueError(
        f"{'no' if not hits else 'more than one'} engine {text!r} for "
        f"{question!r}: one of {names}"
    )


def _names(v):
    return [
        str(x).strip().lower()
        for x in (v if isinstance(v, (list, tuple)) else str(v).split(','))
        if str(x).strip()
    ]


def load_config(path):
    """A config's tables in the one form the study and the analyses read, its
    older forms turned into it:

        [analysis.<name>]       a list of one ([[analysis.<name>]]: several);
                                names in lower case, keys with underscores
        [analysis] run = [...]  (before the questions, and no [study]): the
                                [study] it means: run = the questions (rf:
                                S-parameters; an analysis: those it answers,
                                by it), said = "analysis"
        an analysis's check     (the crosstalk's, before its variants were
                                engines), for a question [study] names no
                                engine for: that variant (crosstalk_gfdtd)

    [study] then holds run (the questions, in lower case) and said ("study";
    "analysis": the older form), [study.<q>] as written; none: None."""
    from .options import strlist
    cfg = load_toml(path)
    old = cfg.get('analysis')
    old = old if isinstance(old, dict) else {}
    sets = cfg['analysis'] = {}
    for name, tabs in old.items():
        tabs = [tabs] if isinstance(tabs, dict) else tabs
        if (isinstance(tabs, list) and tabs
                and all(isinstance(t, dict) for t in tabs)):
            sets[name.lower()] = [{k.replace('-', '_'): v
                                   for k, v in t.items()} for t in tabs]
    st = cfg.get('study')
    if isinstance(st, dict):
        tabs = {str(k).lower(): v for k, v in st.items() if isinstance(v, dict)}
        st = dict(tabs, said='study', run=_names(st['run'])
                  if st.get('run') is not None else list(tabs))
    elif old.get('run') is not None:
        from .analyses import registry
        reg, st = registry(), {'run': [], 'said': 'analysis'}
        for name in [x.lower() for x in strlist(old['run']) or []]:
            qs = (['sparams'] if name == RF else
                  provided(reg[name]) if name in reg else [name])
            for q in qs:
                e = ([] if name == RF else [name] if name not in reg else
                     (_variant(q, sets, run=name) or [name])[:1])
                have = st.setdefault(q, {'engine': []})['engine']
                st['run'] += [q] if q not in st['run'] else []
                have += [x for x in e if x not in have]
    else:
        st = None
    for q in st['run'] if st else ():
        tab = st.get(q)
        if not isinstance(tab, dict) or not any(
                x not in ('', 'auto') for x in _names(tab.get('engine', []))):
            v = _variant(q, sets)
            if v:
                st[q] = dict(tab or {}, engine=v)
    cfg['study'] = st
    return cfg


def read_config(path, strict=True):
    """The study a TOML config asks (Study): its [study] tables (load_config:
    or what an older form means); with none, S-parameters. strict False (a
    front end, which shows what it cannot run greyed): a question nobody knows
    and an engine nobody lists are kept as written instead of a
    ValueError."""
    table = load_config(path).get('study')
    if table is None:
        return Study({'sparams': []})
    known, es = questions(), engines()
    asked, fields = {}, {}
    for q in table['run']:
        if q not in known and strict:
            if table['said'] == 'analysis':
                from .analyses import registry
                raise ValueError(f"[analysis] run: no analysis {q!r}: one of "
                                 f"{', '.join([RF, *registry()])}")
            raise ValueError(
                f"[study] run: no question {q!r}: one of {', '.join(known)}")
        tab = table.get(q, {})
        # ('engines' for 'engine' had run the default
        odd = sorted(set(tab) - {'engine', 'fields'})
        # engine without a word)
        if odd and strict:
            raise ValueError(
                f"[study.{q}]: {', '.join(odd)}: not a key of a question "
                "(engine, fields)"
            )
        names = []
        for text in _names(tab.get('engine', [])):
            try:
                name = engine_named(q, text, es)
            except ValueError:
                if strict:
                    raise
                name = text
            if name and name not in names:
                names.append(name)
        asked[q] = names
        if 'fields' in tab:
            fields[q] = bool(tab['fields'])
    return Study(asked, fields, table['said'])


def _variant(q, sets, es=None, run=None):
    """[the variant engine] that a config's analysis tables (load_config's)
    ask for by the settings it fixes (a crosstalk table's check = "gfdtd",
    written before the variants were engines: crosstalk_gfdtd), else []. run:
    of that analysis only."""
    for e in (es or engines()).values():
        tab = (sets.get(e.run, [None])[0] if e.kind == 'analysis'
               and run in (None, e.run) else None)
        if (tab is not None and q in e.provides and any(e.sets.values())
                and all(str(tab.get(k) or '') == str(v)
                        for k, v in e.sets.items())):
            return [e.name]
    return []


def write_config(path, study):
    """The [study] tables of the config at `path` written anew from a Study
    (None: taken out); the rest of the file is kept. An engine is written by its
    registry name."""
    tab = {}
    if study is not None:
        tab = {'run': list(study.asked)}
        for q, names in study.asked.items():
            tab[q] = ({'engine': names[0] if len(names) == 1 else list(names)}
                      if names else {})
            if q in study.fields:
                tab[q]['fields'] = bool(study.fields[q])
    update_toml(path, tables={'study': tab})


def plan(study):
    """The runs a Study takes, in the order to make them: [Step]. First the
    solvers, as one kicad2ems run (two when an antenna is asked of some and only
    S-parameters of others: the antenna's run answers S-parameters for its own
    solvers); then each analysis engine, in the registry's order (the
    temperature after the circuit whose losses it takes). ValueError: a question
    nobody answers, or an engine that does not answer what it is asked."""
    from .solvers import LINES
    es = engines()
    asked = study.asked
    steps = []
    flag = lambda qs: (True if any(study.fields.get(q) for q in qs) else False
                       if qs and all(study.fields.get(q) is False
                                     for q in qs) else None)
    solver_q = [q for q in asked if q in SOLVER_QUESTIONS]
    for q in solver_q:
        for name in asked[q]:
            if name != LINES and (name not in es or q not in es[name].provides):
                have = (', '.join(
                    e.name for e in es.values() if q in e.provides)
                        or 'none installed')
                raise ValueError(
                    f"{name!r} does not answer {q!r}: one of {have}")
    said = study.said == 'study'
    if 'antenna' in solver_q:
        ant = tuple(asked['antenna'])
        both = ('sparams' in asked and (not asked['sparams'] or not ant
                                        or set(asked['sparams']) <= set(ant)))
        steps.append(
            Step('solvers', ant, ('antenna', ) + (('sparams', ) if both else
                                                  ()), 'antenna',
                 flag(['antenna'] + (['sparams'] if both else []))))
        rest = [
            q for q in solver_q
            if q != 'antenna' and not (q == 'sparams' and both)
        ]
    else:
        rest = solver_q
    if rest:
        # the settings' own choice: the line model's too
        auto = 'sparams' in rest and not asked['sparams']
        names = [] if auto else [n for q in rest for n in asked[q]]
        if not auto and 'lines' in rest and not asked['lines']:
            names.append(LINES)
        steps.append(
            Step('solvers', tuple(dict.fromkeys(names)), tuple(rest),
                 'sparams' if said else '', flag(rest)))
    other = {
        q: names
        for q, names in asked.items()
        if q not in SOLVER_QUESTIONS
    }
    runs = runs_for(other)
    for name in [n for n in es if n in runs]:  # the registry's order
        steps.append(
            Step('analysis', (name, ), runs[name], '', flag(list(runs[name]))))
    return steps


def describe(steps, sets=None):
    """A plan as lines to print: each run, what it is asked, by whom. sets: the
    analyses' tables of the config (analyses.read_sets): an analysis given
    several is as many runs, named by their tags."""
    qs, es = questions(), engines()
    lines = []
    for st in steps:
        what = ', '.join(qs[q].label if q in qs else q for q in st.questions)
        if st.kind == 'solvers':
            who = (', '.join(es[n].label if n in es else n for n in st.engines)
                   or "the settings' solvers")
            extra = (' (antenna mode)' if st.mode == 'antenna' else '')
        else:
            a = es[st.engines[0]]
            who, extra = f"{a.label} ({a.impl.tool})", ''
            many = (sets or {}).get(a.run) or []
            if len(many) > 1:
                extra = (f", {len(many)} runs: "
                         f"{', '.join(a.impl.tag(dict(v)) for v in many)}")
        lines.append(f"  {what}: {who}{extra}"
                     + (', with field dumps' if st.fields else
                        ', without field dumps' if st.fields is False else ''))
    return lines


def _fresh(folder_at):
    """A run folder nobody has yet: its name is its start to the second, so a
    second run within one waits for the next."""
    for _ in range(30):
        d = folder_at()
        if not os.path.exists(d):
            break
        time.sleep(0.1)
    return d


def unmet(name, failed):
    """The analyses an engine's run needs (Analysis.needs) of `failed`, the
    runs that did not finish: it is not made then."""
    e = engines().get(name)
    return ([n for n in e.impl.needs if n in failed]
            if e is not None and e.kind == 'analysis' else [])


def run(config, only=None, preview=False, out=None, echo=print, extra=None):
    """Run the study a config asks, one run after the other (plan), each into a
    folder of its own under the output folder and each a command of its own:
    the solvers' kicad2ems's run of the config's settings, an analysis the
    command its Analysis gives, with its settings from [analysis.<name>] (given
    several tables, [[analysis.<name>]]: a run for each, a board's rails).
    only: the questions (or engines) to run of those it asks; preview: the runs
    that have one, without solving; extra: settings typed on the command line,
    for the solvers' run. A run that cannot start or fails says why and the
    rest go on, except an analysis that needs its result. Returns 0 when every
    run finished, 1 when one did not, 2 when the study cannot be read."""
    import subprocess
    from .analyses import read_sets, registry
    from .config import ConfigError, build_settings, default_out, read_toml
    from .runs import (Run, ended, latest_results, outcome, preview_dir,
                       run_dir, tool_dir, tool_preview_dir)
    from .solvers import registry as solvers
    try:
        study = read_config(config)
        steps = plan(study)
        base_s = build_settings(dict(extra or {}), config)
        tables = read_sets(config)
    except (ConfigError, ValueError, OSError) as e:
        echo(f"study: error: {e}")
        return 2
    if only:
        want = set(_names(only))
        steps = [
            st for st in steps
            if want & (set(st.questions) | set(st.engines) | {st.name})
        ]
        if not steps:
            echo(
                f"study: error: --only {', '.join(sorted(want))}: nothing of "
                "that in this study "
                f"({', '.join(study.asked) or 'which asks nothing'})")
            return 2
    es = engines()
    if preview:
        steps = [
            st for st in steps
            if st.kind == 'solvers' or es[st.engines[0]].previewed
        ]
    board = base_s.board
    base = os.path.abspath(out or base_s.out or default_out(board))
    count = sum(1 if st.kind == 'solvers' else len(
        tables.get(es[st.engines[0]].run) or [1]) for st in steps)
    echo(f"study of {os.path.basename(board or config)}: {count} run(s)")
    for line in describe(steps, tables):
        echo(line)
    results = latest_results(base, board, config)
    state, failed = [], set()

    def spawn(argv, folder, env=None):
        """A run's command, its output echoed, its end into its mark."""
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=dict(os.environ, **(env or {})))
        for line in proc.stdout:
            echo('  ' + line.rstrip('\n'))
        code = proc.wait()
        ended(folder, outcome(code))
        return code

    for st in steps:
        t0 = time.time()
        if st.kind == 'solvers':
            folder = (preview_dir(base, board)
                      if preview else _fresh(lambda: run_dir(base, board)))
            cli = dict(extra or {}, out=folder)
            if st.engines:
                cli['solvers'] = list(st.engines)
            if st.mode:
                cli['mode'] = st.mode
            if st.fields is False:  # the solvers' field dumps: none asked
                cli.update({
                    o.key: o.default
                    for sv in solvers().values()
                    for o in dump_options(sv)
                })
            try:
                build_settings(cli, config)  # (what is wrong: said here)
                values = dict(read_toml(config)[0], **cli)
                values.pop('vary', None)
                mark(folder, RF, st.questions, preview, config=config)
                code = spawn(Run(values, folder, preview).argv(), folder)
            except (ConfigError, ValueError, OSError) as e:
                echo(f"study: {st.name}: not started: {e}")
                code = 2
        else:
            eng = es[st.engines[0]]
            a = registry()[eng.run]
            need = unmet(eng.name, failed)
            if need:
                echo(
                    f"study: {a.label}: not run: {', '.join(need)} did not "
                    "finish"
                )
                state.append((st, 'not run', '', 0.0, ''))
                failed.add(a.name)
                continue
            # its tables: a run each (a board's rails)
            many = tables.get(a.name) or [{}]
            for table in many:
                t0 = time.time()
                values = dict(table)
                own, named = values.pop('board', None), values.pop('out', None)
                tag = a.tag(values) if len(many) > 1 else ''
                who = ((eng.label if eng.name != a.name else a.label)
                       + (f": {tag}" if tag else ''))
                values = dict(a.for_questions(values, st.questions),
                              **eng.sets)
                if st.fields is not None and a.fields:
                    values[a.fields] = bool(st.fields)
                try:
                    folder = (os.path.abspath(named) if named else
                              (tool_preview_dir(base, own or board, a.name)
                               if preview else _fresh(lambda: tool_dir(
                                   base, own or board, a.name, a.tag(values)))))
                    argv = a.command(values, own or board, folder,
                                     preview=preview, results=results)
                except ValueError as e:
                    echo(f"study: {who}: not started: {e}")
                    state.append((st, f"not started: {e}", '', 0.0, tag))
                    failed.add(a.name)
                    continue
                mark(folder, eng.name, st.questions, preview, config=config)
                shown = (['python', *argv[1:]] if argv[0] == sys.executable
                         else argv)
                echo(f"study: {who}: {' '.join(shown)}")
                code = spawn(argv, folder, a.env)
                if code == 0 and not preview:
                    results[a.name] = folder
                if code != 0:
                    failed.add(a.name)
                state.append(
                    (st, 'done' if code == 0 else f"failed (exit {code})",
                     folder, time.time() - t0, tag))
            continue
        if code != 0:
            failed.add(st.name)
        state.append(
            (st, 'done' if code == 0 else f"failed (exit {code})", folder,
             time.time() - t0, ''))
    echo('study:')
    qs, es = questions(), engines()
    for st, text, folder, took, tag in state:
        what = ', '.join(qs[q].label if q in qs else q for q in st.questions)
        who = ('the solvers' if st.kind == 'solvers' else
               es[st.engines[0]].label + (f": {tag}" if tag else ''))
        echo(f"  {what + ' (' + who + ')':<40s} {text}"
             + (f", {took:.0f} s: {folder}" if folder else ''))
    return 0 if all(text == 'done' for _, text, *_ in state) else 1


def main(argv=None, prog='kicad2ems study'):
    """`kicad2ems study board.toml`: run the study a config asks (run); --list
    shows its runs without making them."""
    import argparse
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Run the study a config asks: every question of its "
                    "[study] table, each by the engine named for it, one run "
                    "after the other."
    )
    ap.add_argument(
        'config', help=
        "the config (.toml): its [study] run and tables (docs/usage.md#study)")
    ap.add_argument(
        '--only', help=
        "of the questions it asks (or their engines), run only these, "
        "comma-separated"
    )
    ap.add_argument('--preview', action='store_true',
                    help="the runs that have a preview, without solving")
    ap.add_argument('--list', action='store_true',
                    help="show the runs the study takes, and stop")
    ap.add_argument(
        '--out', help=
        "the output folder (default: the config's, else out next to the board)")
    a = ap.parse_args(argv)
    if a.list:
        try:
            from .analyses import read_sets
            steps, sets = plan(read_config(a.config)), read_sets(a.config)
        except (ValueError, OSError) as e:
            print(f"{prog}: error: {e}", file=sys.stderr)
            return 2
        print('\n'.join(describe(steps, sets)))
        return 0
    return run(a.config, only=a.only, preview=a.preview, out=a.out)


_PORTS = {}  # {board: (its file's time, how many port footprints it has)}


def rf_needs(values):
    """What an S-parameter run of these settings still needs before it can
    start: [dict(text, key)], key the setting it is set by. Cheap (the board
    file's footprints are read, no model is made): a front end asks on every
    change, lists the answers and does not start the run."""
    out = []
    board = str(values.get('board') or '').strip()
    if not board:
        return [dict(text='choose the board', key='board')]
    if not os.path.exists(board):
        return [
            dict(text=f"the board is not there: {os.path.basename(board)}",
                 key='board')
        ]
    if (board.lower().endswith('.kicad_pcb')
            and not any(values.get(k) for k in ('ports', 'region', 'pos'))):
        try:
            from .importers.kicad import KiCadBoard
            stamp = os.path.getmtime(board)
            # read again when the file changes
            if _PORTS.get(board, (None,))[0] != stamp:
                _PORTS[board] = (stamp,
                                 len(KiCadBoard(board).port_footprints()))
            if not _PORTS[board][1]:
                out.append(
                    dict(
                        text=
                        "the board has no port: a footprint with P1, P2 ... on "
                        "its Fab layer at a trace's end, or Extra "
                        "ports", key='ports'))
        # (the run will say what is wrong with it)
        except Exception:  # noqa: BLE001
            pass
    try:
        lo, hi = float(values.get('fmin') or 0), float(values.get('fmax') or 0)
        if lo and hi and hi <= lo:
            out.append(dict(text='f max is not above f min', key='fmax'))
    except (TypeError, ValueError):
        out.append(dict(text='the frequencies are not numbers', key='fmin'))
    return out
