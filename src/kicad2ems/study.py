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

A config holds a study as its [study] tables (Study, read_config, write_config), plan() gives the runs it takes in
order, and run() makes them, which is the command `kicad2ems study board.toml`:

    study = read_config('board.toml')           # Study(asked={'dc': ['pdn', 'elmer_dc']}, fields={}, said='study')
    [st.name for st in plan(study)]             # ['pdn', 'elmer_dc']: 'rf' is the solvers' run
    run('board.toml')                           # 0: every run finished
"""
import json
import os
from dataclasses import dataclass, field

GROUPS = ('RF', 'Signal integrity', 'Power', 'Thermal', 'Other')
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
    figures: str = ''       # the figures its engines name alike (Analysis.figures), so their answers can be compared


QUESTIONS = {q.name: q for q in (
    Question('sparams', 'S-parameters', 'RF', "the ports' S-parameters over the band", answer='touchstone', order=10,
             extra='mom'),
    Question('antenna', 'Antenna pattern', 'RF', "a driven port's far field: pattern, gain, efficiency",
             answer='antenna', order=20, extra='mom'),
    Question('lines', 'Trace impedances', 'RF', "every trace's impedance and delay, from the line model", order=30),
    Question('crosstalk', 'Crosstalk', 'Signal integrity', "near- and far-end crosstalk between traces for an edge, "
             "and what in the layout makes it", order=10,
             figures="NEXT <victim>, FEXT <victim>: all its aggressors together, % of the swing"),
    Question('dc', 'Rail drop', 'Power', "a rail's DC drop, its current density and its loss", order=10, extra='qs',
             figures="drop <load>: the rail's own copper from the source to the load's pads, mV (no ground return, "
                     "no series part); loss: in that copper, mW"),
    Question('pdn', 'Rail impedance', 'Power', "the impedance a rail's loads see over frequency", shares='dc',
             order=20, extra='qs'),
    Question('nearfield', 'Converter near field', 'Power', "a converter's input loop and the magnetic field over it",
             order=30, extra='qs'),
    Question('circuit', 'Circuit', 'Power', "a switching circuit on the board's copper: waveforms, losses, stress",
             order=40, extra='qs'),
    Question('coils', 'Coils and ferrite', 'Power', "wound coils beside ferrite plates: their inductances and "
             "coupling (a wireless-power link's coil model)", order=45, extra='fem',
             figures="L <coil>: its inductance, uH; k <coil>,<coil>: two coils' coupling"),
    Question('capacitance', 'Capacitance', 'Power', "the capacitance between nets", order=50, extra='fem',
             figures="C <net>,<net>: between two nets, pF; C <net>: a net to all the others, pF"),
    Question('thermal', 'Temperature', 'Thermal', "the board's steady temperature from what its parts dissipate",
             order=10, extra='qs', figures="hottest: the copper's highest temperature, C; <part>: the board at "
                                           "the part's pads, C"),
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
    out = {}
    for name, sv in solvers().items():
        what = ('sparams',) + (('lines',) if sv.consumes == 'network' else ())
        if str(sv.capabilities.get('antenna', 'no')).split(':')[0] in ('yes', 'approximated'):
            what += ('antenna',)
        dumps = getattr(sv, 'dumps', None)
        if dumps is None:
            dumps = bool(dump_options(sv))
        out[name] = Engine(name, sv.label, 'solver', what, sv, fields='dump' if dumps else '',
                           previewed=bool(sv.previewed), order=sv.order)
    for name, a in registry().items():
        out[name] = Engine(name, getattr(a, 'engine', '') or a.label, 'analysis', provided(a), a,
                           fields=getattr(a, 'fields', '') or '', previewed=bool(a.previewed), order=a.order)
    return out


def dump_options(sv):
    """The options that ask a solver for field dumps: its `dump` (the FDTD solvers': FIELD@REGION@GHz specs) or
    one named <its own>_dump (mom25d's mom_dump: the frequencies of its current maps)."""
    from .fields.dumps import G_DUMP
    return [o for o in sv.options if o.type is list and
            ((o.key == 'dump' and getattr(o, 'group', None) == G_DUMP) or o.key.endswith('_dump'))]


def default_fields(engine, fmin, fmax, layer='F.Cu', sweep='linear'):
    """What an engine is asked for when its field dumps are wanted and none is set: {option key: value}. A
    solver's dump: the E field on `layer` (the first port's, a front end knows) at the band's centre; a list of
    frequencies (mom25d's current maps): that centre; an analysis: its `fields` switch. fmin, fmax in GHz."""
    e = engines().get(engine)
    if e is None or not e.fields:
        return {}
    if e.kind == 'analysis':
        return {e.fields: True}
    fc = (fmin * fmax) ** 0.5 if sweep == 'log' and fmin > 0 else 0.5 * (fmin + fmax)
    fc = float(f"{fc:.3g}")
    return {o.key: [f"E@{layer}@{fc:g}"] if o.key == 'dump' else [fc] for o in dump_options(e.plugin)}


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


def compare(results):
    """Engines' answers side by side: dict(engines=[names], labels=[as a list shows them], rows=[dict(name, unit,
    values=[a number or None per engine], spread)]) from {engine: its result's folder}. A figure is a row when at
    least one engine gives it, in the first engine's order and then the others' own; spread: the largest
    difference between engines that give it (None with fewer than two)."""
    es = engines()
    names = [n for n in results if n in es and es[n].kind == 'analysis']
    got, order, units = {}, [], {}
    for n in names:
        try:
            figs = es[n].plugin.figures(results[n]) or []
        except Exception:                                           # noqa: BLE001 (a plugin's own reader)
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
        rows.append(dict(name=name, unit=units[name], values=vals,
                         spread=(max(have) - min(have)) if len(have) > 1 else None))
    return dict(engines=names, labels=[es[n].label for n in names], rows=rows)


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
            who = ', '.join(e.name for e in row['engines']) or (f"none ({row['missing']})" if row['missing'] else 'none')
            lines.append(f"  {g['group']:<18s}{q.label:<22s}{who}")
    return lines


# ---- a config's study: what it asks, the runs that takes, and running them ---------------------------------
RF = 'rf'                                   # the S-parameter run's name in a plan and in a run folder's mark
SOLVER_QUESTIONS = ('sparams', 'antenna', 'lines')      # what solver engines answer: together, one kicad2ems run


@dataclass
class Study:
    """What a study asks, as a config holds it:

        [study]
        run = ["sparams", "dc", "thermal"]      # the questions, by name (kicad2ems --check lists them)
        [study.sparams]
        engine = ["mom25d", "pcbqs"]            # who answers: one, or several to compare; none: the default
        [study.thermal]
        engine = "Elmer"                        # an engine's name ('elmer_thermal') or how a list shows it
        fields = true                           # its field dumps too, where it can write them

    An engine's own settings stay where they were: a solver's under [solver.<name>] (and the top level), an
    analysis's under [analysis.<name>]."""
    asked: dict                                 # {question: [engine names]}, in the order written; []: its default
    fields: dict = field(default_factory=dict)  # {question: True | False}: its run's field dumps asked for, or not
    said: str = ''                              # the table that said so: 'study', 'analysis' ([analysis] run) or ''


@dataclass(frozen=True)
class Step:
    """One run of a study: the solvers together (a kicad2ems run), or one analysis."""
    kind: str               # 'solvers' or 'analysis'
    engines: tuple          # solvers: those to run, () for the settings' own choice (auto); analysis: (its name,)
    questions: tuple        # what it is asked
    mode: str = ''          # solvers: 'antenna' or 'sparams' where the study says; '': as the settings say
    fields: object = None   # True / False: field dumps asked for, or not; None: as the settings say

    @property
    def name(self):
        return RF if self.kind == 'solvers' else self.engines[0]


def engine_named(question, text, es=None):
    """The registry name of the engine `text` means among those that answer a question: its name ('elmer_thermal',
    'mom25d'), a solver's alias ('mom', 'lines'), or the name a list shows it by ('pcbqs', 'Elmer'), in any case;
    None for 'auto' (the default engine). ValueError when nothing matches, or more than one does."""
    from .solvers import LINES, aliases
    want = str(text).strip().lower()
    if want in ('', 'auto'):
        return None
    have = [e for e in (es or engines()).values() if question in e.provides]
    if want in ('lines', 'line', 'fast') and any(e.kind == 'solver' and e.plugin.consumes == 'network' for e in have):
        return LINES
    solver = aliases().get(want)
    hits = [e.name for e in have if want in (e.name.lower(), e.label.lower(), e.label.split(' (')[0].lower())
            or (e.kind == 'solver' and solver == e.name)]
    if len(hits) == 1:
        return hits[0]
    from .analyses import missing as missing_analysis
    from .solvers import missing as missing_solver
    why = missing_solver(want) or missing_analysis(want)
    if not hits and why:
        raise ValueError(why)
    names = ', '.join(e.name for e in have) or 'none installed'
    raise ValueError(f"{'no' if not hits else 'more than one'} engine {text!r} for {question!r}: one of {names}")


def _names(v):
    return [str(x).strip().lower() for x in (v if isinstance(v, (list, tuple)) else str(v).split(',')) if str(x).strip()]


def read_config(path, strict=True):
    """The study a TOML config asks (Study). Its [study] tables say; without them, [analysis] run as before the
    questions ("rf": S-parameters; an analysis: the questions it answers, by it); with neither, S-parameters.
    strict False (a front end, which shows what it cannot run greyed): a question nobody knows and an engine not
    installed are kept as written instead of a ValueError."""
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:                                                           # pragma: no cover
        import tomli as tomllib
    with open(path, 'rb') as fh:
        cfg = tomllib.load(fh)
    table = cfg.get('study')
    if isinstance(table, dict):
        known, es = questions(), engines()
        tabs = {str(k).lower(): v for k, v in table.items() if isinstance(v, dict)}
        run = _names(table['run']) if table.get('run') is not None else list(tabs)
        asked, fields = {}, {}
        for q in run:
            if q not in known and strict:
                raise ValueError(f"[study] run: no question {q!r}: one of {', '.join(known)}")
            tab = tabs.get(q, {})
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
        return Study(asked, fields, 'study')
    from .analyses import read_config as read_analyses, registry, missing
    run, _ = read_analyses(path)
    if run is None:
        return Study({'sparams': []})
    asked, reg = {}, registry()
    for name in run:
        if name == RF:
            asked.setdefault('sparams', [])
        elif name in reg:
            for q in provided(reg[name]):
                if name not in asked.setdefault(q, []):
                    asked[q].append(name)
        elif strict:
            raise ValueError(missing(name) or f"[analysis] run: no analysis {name!r}: one of "
                                              f"{', '.join([RF, *reg])}")
        else:
            asked.setdefault(name, [name])
    return Study(asked, {}, 'analysis')


def write_config(path, study):
    """The [study] tables of the config at `path` written anew from a Study (None: taken out); the rest of the
    file is kept. An engine is written by its registry name."""
    import re
    from .runs import toml_value
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    out, skip = [], False
    for line in lines:
        head = re.match(r'^\s*\[\s*([^\]]+?)\s*\]', line)
        if head:
            name = head.group(1).strip()
            skip = name == 'study' or name.startswith('study.')
        if not skip:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    if study is not None:
        out += ['', '[study]', f"run = {toml_value(list(study.asked))}"]
        for q, names in study.asked.items():
            tab = ([f"engine = {toml_value(names[0] if len(names) == 1 else list(names))}"] if names else []) + \
                  ([f"fields = {toml_value(bool(study.fields[q]))}"] if q in study.fields else [])
            if tab:
                out += ['', f"[study.{q}]"] + tab
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(out) + '\n')


def plan(study):
    """The runs a Study takes, in the order to make them: [Step]. First the solvers, as one kicad2ems run (two
    when an antenna is asked of some and only S-parameters of others: the antenna's run answers S-parameters for
    its own solvers); then each analysis engine, in the registry's order (the temperature after the circuit whose
    losses it takes). ValueError: a question nobody answers, or an engine that does not answer what it is asked."""
    from .solvers import LINES
    es = engines()
    asked = study.asked
    steps = []
    flag = lambda qs: (True if any(study.fields.get(q) for q in qs) else
                       False if qs and all(study.fields.get(q) is False for q in qs) else None)
    solver_q = [q for q in asked if q in SOLVER_QUESTIONS]
    for q in solver_q:
        for name in asked[q]:
            if name != LINES and (name not in es or q not in es[name].provides):
                have = ', '.join(e.name for e in es.values() if q in e.provides) or 'none installed'
                raise ValueError(f"{name!r} does not answer {q!r}: one of {have}")
    said = study.said == 'study'
    if 'antenna' in solver_q:
        ant = tuple(asked['antenna'])
        both = 'sparams' in asked and (not asked['sparams'] or not ant or set(asked['sparams']) <= set(ant))
        steps.append(Step('solvers', ant, ('antenna',) + (('sparams',) if both else ()), 'antenna',
                          flag(['antenna'] + (['sparams'] if both else []))))
        rest = [q for q in solver_q if q != 'antenna' and not (q == 'sparams' and both)]
    else:
        rest = solver_q
    if rest:
        auto = 'sparams' in rest and not asked['sparams']           # the settings' own choice: the line model's too
        names = [] if auto else [n for q in rest for n in asked[q]]
        if not auto and 'lines' in rest and not asked['lines']:
            names.append(LINES)
        steps.append(Step('solvers', tuple(dict.fromkeys(names)), tuple(rest), 'sparams' if said else '', flag(rest)))
    other = {q: names for q, names in asked.items() if q not in SOLVER_QUESTIONS}
    runs = runs_for(other)
    for name in [n for n in es if n in runs]:                       # the registry's order
        steps.append(Step('analysis', (name,), runs[name], '', flag(list(runs[name]))))
    return steps


def describe(steps, sets=None):
    """A plan as lines to print: each run, what it is asked, by whom. sets: the analyses' tables of the config
    (analyses.read_sets): an analysis given several is as many runs, named by their tags."""
    qs, es = questions(), engines()
    lines = []
    for st in steps:
        what = ', '.join(qs[q].label if q in qs else q for q in st.questions)
        if st.kind == 'solvers':
            who = ', '.join(es[n].label if n in es else n for n in st.engines) or "the settings' solvers"
            extra = (' (antenna mode)' if st.mode == 'antenna' else '')
        else:
            a = es[st.engines[0]]
            who, extra = f"{a.label} ({a.plugin.tool})", ''
            many = (sets or {}).get(a.name) or []
            if len(many) > 1:
                extra = f", {len(many)} runs: {', '.join(a.plugin.tag(dict(v)) for v in many)}"
        lines.append(f"  {what}: {who}{extra}" + (', with field dumps' if st.fields else
                                                 ', without field dumps' if st.fields is False else ''))
    return lines


def _dump_keys():
    """The settings that ask a solver for field dumps, with nothing asked: {key: its default}."""
    from .solvers import registry
    return {o.key: o.default for sv in registry().values() for o in dump_options(sv)}


def _fresh(folder_at):
    """A run folder nobody has yet: its name is its start to the second, so a second run within one waits for
    the next."""
    import time
    for _ in range(30):
        d = folder_at()
        if not os.path.exists(d):
            break
        time.sleep(0.1)
    return d


def latest_results(base, board):
    """{analysis: the folder of its newest finished result on this board under base}: what an analysis that starts
    from another's result is given (Analysis.command's results)."""
    import glob
    from .analyses import analysis_of
    from .runs import board_name
    out, when = {}, {}
    for d in glob.glob(os.path.join(glob.escape(base), f"{glob.escape(board_name(board))}_*")):
        made = analysis_of(d) if os.path.isdir(d) else None
        if made and not made[1] and os.path.getmtime(d) > when.get(made[0], 0):
            out[made[0]], when[made[0]] = d, os.path.getmtime(d)
    return out


def run(config, only=None, preview=False, out=None, echo=print, extra=None):
    """Run the study a config asks, one run after the other (plan), each into a folder of its own under the output
    folder: the solvers' as kicad2ems's own run, an analysis as the command its plugin gives, with its settings
    from [analysis.<name>] (given several tables, [[analysis.<name>]]: a run for each, a board's rails). only:
    the questions (or engines) to run of those it asks; preview: the runs that have one, without solving; extra:
    settings typed on the command line, for the solvers' run. A run that cannot start or fails says why and the
    rest go on, except an analysis that needs its result. Returns 0 when every run finished, 1 when one did not,
    2 when the study cannot be read."""
    import subprocess
    import sys
    import time
    from .analyses import read_sets, registry
    from .config import ConfigError, build_settings, default_out
    from .runs import preview_dir, run_dir, tool_dir, tool_preview_dir
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
        steps = [st for st in steps if want & (set(st.questions) | set(st.engines) | {st.name})]
        if not steps:
            echo(f"study: error: --only {', '.join(sorted(want))}: nothing of that in this study "
                 f"({', '.join(study.asked) or 'which asks nothing'})")
            return 2
    if preview:
        reg = registry()
        steps = [st for st in steps if st.kind == 'solvers' or reg[st.engines[0]].previewed]
    board = base_s.board
    base = os.path.abspath(out or base_s.out or default_out(board))
    count = sum(1 if st.kind == 'solvers' else len(tables.get(st.engines[0]) or [1]) for st in steps)
    echo(f"study of {os.path.basename(board or config)}: {count} run(s)")
    for line in describe(steps, tables):
        echo(line)
    results, state, failed = latest_results(base, board), [], set()
    for st in steps:
        t0 = time.time()
        if st.kind == 'solvers':
            folder = preview_dir(base, board) if preview else _fresh(lambda: run_dir(base, board))
            cli = dict(extra or {}, out=folder, preview=bool(preview))
            if st.engines:
                cli['solvers'] = list(st.engines)
            if st.mode:
                cli['mode'] = st.mode
            if st.fields is False:
                cli.update(_dump_keys())
            try:
                s = build_settings(cli, config)
                mark(folder, RF, st.questions, preview)
                from .pipeline import Job
                code = Job(s, command=f"kicad2ems study {config}").run()
            except (ConfigError, ValueError, OSError) as e:
                echo(f"study: {st.name}: not started: {e}")
                code = 2
        else:
            a = registry()[st.engines[0]]
            need = [n for n in a.needs if n in failed]
            if need:
                echo(f"study: {a.label}: not run: {', '.join(need)} did not finish")
                state.append((st, 'not run', '', 0.0, ''))
                failed.add(a.name)
                continue
            many = tables.get(a.name) or [{}]                       # its tables: a run each (a board's rails)
            for table in many:
                t0 = time.time()
                values = dict(table)
                own, named = values.pop('board', None), values.pop('out', None)
                tag = a.tag(values) if len(many) > 1 else ''
                who = a.label + (f": {tag}" if tag else '')
                values = a.for_questions(values, st.questions)
                if st.fields is not None and a.fields:
                    values[a.fields] = bool(st.fields)
                try:
                    folder = os.path.abspath(named) if named else (
                        tool_preview_dir(base, own or board, a.name) if preview else
                        _fresh(lambda: tool_dir(base, own or board, a.name, a.tag(values))))
                    argv = a.command(values, own or board, folder, preview=preview, results=results)
                except ValueError as e:
                    echo(f"study: {who}: not started: {e}")
                    state.append((st, f"not started: {e}", '', 0.0, tag))
                    failed.add(a.name)
                    continue
                mark(folder, a.name, st.questions, preview)
                echo(f"study: {who}: {' '.join(['python', *argv[1:]] if argv[0] == sys.executable else argv)}")
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        env=dict(os.environ, **(a.env or {})))
                for line in proc.stdout:
                    echo('  ' + line.rstrip('\n'))
                code = proc.wait()
                if code == 0 and not preview:
                    results[a.name] = folder
                if code != 0:
                    failed.add(a.name)
                state.append((st, 'done' if code == 0 else f"failed (exit {code})", folder, time.time() - t0, tag))
            continue
        took = time.time() - t0
        if code != 0:
            failed.add(st.name)
        state.append((st, 'done' if code == 0 else f"failed (exit {code})", folder, took, ''))
    echo('study:')
    qs, es = questions(), engines()
    for st, text, folder, took, tag in state:
        what = ', '.join(qs[q].label if q in qs else q for q in st.questions)
        who = 'the solvers' if st.kind == 'solvers' else es[st.engines[0]].label + (f": {tag}" if tag else '')
        echo(f"  {what + ' (' + who + ')':<40s} {text}" + (f", {took:.0f} s: {folder}" if folder else ''))
    return 0 if all(text == 'done' for _, text, *_ in state) else 1


def main(argv=None, prog='kicad2ems study'):
    """`kicad2ems study board.toml`: run the study a config asks (run); --list shows its runs without making them."""
    import argparse
    ap = argparse.ArgumentParser(prog=prog, description="Run the study a config asks: every question of its "
                                 "[study] table, each by the engine named for it, one run after the other.")
    ap.add_argument('config', help="the config (.toml): its [study] run and tables (docs/usage.md#study)")
    ap.add_argument('--only', help="of the questions it asks (or their engines), run only these, comma-separated")
    ap.add_argument('--preview', action='store_true', help="the runs that have a preview, without solving")
    ap.add_argument('--list', action='store_true', help="show the runs the study takes, and stop")
    ap.add_argument('--out', help="the output folder (default: the config's, else out next to the board)")
    a = ap.parse_args(argv)
    if a.list:
        try:
            from .analyses import read_sets
            steps, sets = plan(read_config(a.config)), read_sets(a.config)
        except (ValueError, OSError) as e:
            import sys
            print(f"{prog}: error: {e}", file=sys.stderr)
            return 2
        print('\n'.join(describe(steps, sets)))
        return 0
    return run(a.config, only=a.only, preview=a.preview, out=a.out)


def rf_needs(values):
    """What an S-parameter run of these settings still needs before it can start: [dict(text, key)], key the
    setting it is set by. Cheap (the board file's footprints are read, no model is made): a front end asks on
    every change, lists the answers and does not start the run."""
    import os
    out = []
    board = str(values.get('board') or '').strip()
    if not board:
        return [dict(text='choose the board', key='board')]
    if not os.path.exists(board):
        return [dict(text=f"the board is not there: {os.path.basename(board)}", key='board')]
    if board.lower().endswith('.kicad_pcb') and not any(values.get(k) for k in ('ports', 'region', 'pos')):
        try:
            from .importers.kicad import KiCadBoard
            if not _ports_of(board, os.path.getmtime(board)):
                out.append(dict(text="the board has no port: a footprint with P1, P2 ... on its Fab layer at a "
                                     "trace's end, or Extra ports", key='ports'))
        except Exception:                                    # noqa: BLE001 (the run will say what is wrong with it)
            pass
    try:
        lo, hi = float(values.get('fmin') or 0), float(values.get('fmax') or 0)
        if lo and hi and hi <= lo:
            out.append(dict(text='f max is not above f min', key='fmax'))
    except (TypeError, ValueError):
        out.append(dict(text='the frequencies are not numbers', key='fmin'))
    return out


_PORTS = {}


def _ports_of(board, stamp):
    """How many port footprints a KiCad board file has (kept until the file changes)."""
    if _PORTS.get(board, (None,))[0] != stamp:
        from .importers.kicad import KiCadBoard
        _PORTS[board] = (stamp, len(KiCadBoard(board).port_footprints()))
    return _PORTS[board][1]
