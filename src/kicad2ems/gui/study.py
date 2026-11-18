"""
The study: one board, the questions asked of it, who answers each, and the runs
that takes.

    TREE                        what the list shows: kicad2ems's study_tree (the
                                questions by group, each with the engines
                                that answer it)
    title(key), key_of(step)    a run's name ('rf': S-parameters); the run a
                                Step of kicad2ems's plan is
    StudyModel                  the study itself, with no window: what can be
                                asked, what is, who answers each question, the
                                Fields ticks, what cannot run here and why, each
                                run's state, and the Steps a Run takes
                                (kicad2ems.core.study.plan)
    StudyTree                   the list in the Study dock (studytree.py: the
                                model's view): a row per question, with a tick
                                (it is asked), who answers it (a menu: one
                                engine, or several to compare), a tick for its
                                field dumps where an engine can write them, and
                                its state

A question is something to find out (kicad2ems.core.study: the ports'
S-parameters, a rail's drop, the temperature); an engine is a solver or an
analysis that answers it. The three RF questions are answered by the
solvers, together, as kicad2ems's one run; every other engine is a run of its
own, in the registry's order, which is also a run's: Thermal, which takes its
losses from the latest circuit run unless they are typed, runs after Circuit.
The plan, and an analysis's `needs` (one not made when what it needs did not
finish: core.study.unmet), are kicad2ems's own, as `kicad2ems study` has them.

Nothing in this module needs Qt (or a display): a program that drives a study
without the window (kicad2ems-ipc, a test) imports it alone.
"""
import re

RF = 'rf'                       # kicad2ems's own run: not an analysis's


def tree():
    """What the list shows: kicad2ems's study_tree, [dict(group,
    questions=[dict(question, engines, missing)])]."""
    from kicad2ems.core.study import study_tree
    return study_tree()


TREE = tree()


def title(key):
    """A run's name as the window shows it: 'S-parameters', an analysis's
    label."""
    from kicad2ems.core.analyses import registry
    a = registry().get(key)
    return 'S-parameters' if key == RF else a.label if a else key


def key_of(step):
    """The run a Step of the plan (kicad2ems.core.study.plan) is in the
    window: 'rf' for the solvers', else its analysis's name (a variant's: the
    analysis it is one of)."""
    from kicad2ems.core.study import engines
    if step.kind == 'solvers':
        return RF
    e = engines().get(step.engines[0])
    return run_key(e) or step.engines[0]


# what the solvers answer: together, kicad2ems's one run
SOLVER_QUESTIONS = ('sparams', 'antenna', 'lines')
# the solvers' tick box of that name
AUTO = 'auto'
short = lambda label: label.split(' (')[0]          # 'mom25d (MoM)' -> 'mom25d'


def run_key(e):
    """The run an analysis engine makes: its analysis's name (a variant's:
    the analysis it is one of); None for none."""
    return (getattr(e, 'run', '') or e.name) if e is not None else None


class StudyModel:
    """The study with no window: the questions by group with the engines that
    answer each (TREE as it is when this is made), and what the
    user has made of them:

        on       the questions ticked
        choice   {question: [engine names]}: who answers ([]: its default, the
                 first that can run here)
        fields   {question: its Fields tick}
        solvers  {name: ticked} of the solvers and 'auto', as the RF settings
                 have them; None: not known
        why      {run: why it cannot be made here}
        states   {run: (text, tone)}

    A run is an engine's, so availability, state and the queue go by run ('rf',
    or an analysis's name), and a question shows those of its engines. Every
    change calls each of `listeners` with what changed ('ticks', 'engines',
    'fields', 'available', 'state', 'study') and the question or run it was
    (None: several)."""

    def __init__(self, tree=None):
        from kicad2ems.core.analyses import registry
        reg = registry()
        self.groups, self.labels, self.about, self.engines, self.missing = ([],
                                                                            {},
                                                                            {},
                                                                            {},
                                                                            {})
        for g in (TREE if tree is None else tree):
            self.groups.append(
                (g['group'], [row['question'].name for row in g['questions']]))
            for row in g['questions']:
                q = row['question']
                self.labels[q.name], self.about[q.name] = q.label, q.about
                self.engines[q.name] = [
                    e for e in row['engines']
                    if q.name in SOLVER_QUESTIONS or run_key(e) in reg
                ]
                self.missing[q.name] = (row['missing']
                                        or 'no engine for it is installed')
        self.questions = list(self.labels)
        self.on = set()
        self.choice, self.fields = ({q: []
                                     for q in self.questions},
                                    {q: False
                                     for q in self.questions})
        self.why, self.states, self.solvers = {}, {}, None
        self.listeners = []

    def changed(self, what, key=None):
        for f in list(self.listeners):
            f(what, key)

    # ---- the questions and their engines ----
    def is_solver(self, q):
        return q in SOLVER_QUESTIONS

    def usable(self, q):
        """The engines of a question that can run here, in their order."""
        return [
            e.name for e in self.engines.get(q, [])
            if e.name not in self.why and run_key(e) not in self.why
        ]

    def why_not(self, q, name):
        """Why an engine of a question cannot run here ('' it can): its own
        reason (a variant's solver missing) or its run's."""
        e = self._engine(q, name)
        return (self.why.get(name)
                or (self.why.get(run_key(e), '') if e is not None else ''))

    def _engine(self, q, name):
        return next((e for e in self.engines.get(q, []) if e.name == name),
                    None)

    def runs_for(self, q):
        """The runs (analyses) of the engines that answer a question as it
        stands: engines_for's, a variant as the analysis it runs."""
        return list(
            dict.fromkeys(
                run_key(self._engine(q, n)) or n for n in self.engines_for(q)))

    def _chosen(self, key):
        """The engine chosen for a run among the questions asked (one of an
        analysis's variants, or the analysis), or None."""
        for q in self.asked():
            for n in self.engines_for(q):
                e = self._engine(q, n)
                if e is not None and run_key(e) == key:
                    return e
        return None

    def engine_of(self, key):
        """The name of the engine chosen for a run (a variant's:
        crosstalk_gfdtd; the run's own name when none is)."""
        e = self._chosen(key)
        return e.name if e is not None else key

    def sets_of(self, key):
        """The settings the engine chosen for a run fixes (a variant's: the
        crosstalk's full-wave check), {} for none."""
        return dict(getattr(self._chosen(key), 'sets', None) or {})

    def can_ask(self, q):
        return (bool(self.engines.get(q)) if self.is_solver(q) else bool(
            self.usable(q)))

    def engines_for(self, q):
        """The analyses that answer a question as it stands: those chosen that
        can run here, else its first that can. A solver question: none (the
        solvers ticked answer it, in kicad2ems's own run)."""
        if self.is_solver(q):
            return []
        ok = self.usable(q)
        chosen = [n for n in self.choice.get(q, []) if n in ok]
        return chosen or ok[:1]

    def run_of(self, q):
        """The run a question's settings and page belong to: 'rf', or its first
        engine's."""
        if self.is_solver(q) or not self.engines.get(q):
            return RF
        return (self.runs_for(q) or [run_key(self.engines[q][0])])[0]

    def panels_of(self, q):
        """The runs whose settings a question shows: 'rf' for the solvers', else
        its engines' (those chosen, or its default; all of them where none can
        run, so that their settings can still be read)."""
        if self.is_solver(q):
            return [RF]
        return self.runs_for(q) or list(
            dict.fromkeys(run_key(e) for e in self.engines.get(q, [])))

    def solver_names(self, q):
        """The solvers ticked that answer a solver question, and whether auto
        is."""
        ticked = self.solvers or {}
        return ([e.name for e in self.engines.get(q, []) if ticked.get(e.name)],
                bool(ticked.get(AUTO)))

    def set_solvers(self, ticked):
        """The solvers' ticks ({name: bool}, 'auto' among them), as the RF
        settings have them."""
        if self.solvers != dict(ticked):
            self.solvers = dict(ticked)
            self.changed('engines')

    def can_dump(self, q):
        """Whether a question's engines, as chosen, can write field dumps."""
        es = {e.name: e for e in self.engines.get(q, [])}
        if q == 'lines':
            return False
        if self.is_solver(q):
            on, auto = self.solver_names(q)
            return (any(es[n].fields for n in on)
                    or (auto or self.solvers is None)
                    and any(e.fields for e in es.values()))
        return any(es[n].fields for n in self.engines_for(q))

    def by(self, q):
        """A question's 'Answered by' as a list shows it: (text, tooltip,
        whether there is a choice to make)."""
        es = self.engines[q]
        by = {e.name: e for e in es}
        if not es:
            return '', self.missing[q], False
        if self.is_solver(q):
            on, auto = self.solver_names(q)
            if q == 'lines':
                text = (
                    ' + '.join(short(by[n].label) for n in on) or
                    ('auto' if auto or self.solvers is None else 'line model'))
            else:
                text = (' + '.join((['auto'] if auto else [])
                                   + [short(by[n].label) for n in on])
                        or ('auto' if self.solvers is None else 'no solver'))
            return (
                text,
                'the solvers that answer it, in one run with the other RF '
                'questions: click to choose',
                True)
        names = self.engines_for(q)
        text = (' + '.join(by[n].label for n in names)
                or ' / '.join(e.label for e in es))
        tip = (('click to choose: ' + ', '.join(e.label for e in es))
               if len(es) > 1 else by[es[0].name].impl.tool)
        return text, tip, len(es) > 1

    # ---- what is asked ----
    def tick(self, q, on=True):
        """Ask a question, or not (one that cannot be asked here stays
        unasked)."""
        on = bool(on) and self.can_ask(q)
        if (q in self.on) != on:
            (self.on.add if on else self.on.discard)(q)
            self.changed('ticks', q)

    def asked(self):
        """{question: [engine names]} of the questions ticked, in the list's
        order: the engines chosen, [] for its default (a solver question: the
        solvers ticked, which the settings hold)."""
        return {
            q: ([] if self.is_solver(q) else list(self.choice[q]))
            for q in self.questions
            if q in self.on and self.can_ask(q)
        }

    def asked_fields(self):
        """{question: its Fields tick} of the questions asked that have one."""
        return {
            q: bool(self.fields.get(q))
            for q in self.asked()
            if self.can_dump(q)
        }

    def study(self):
        """The study as kicad2ems holds one (a config's [study] tables)."""
        from kicad2ems.core.study import Study
        return Study(self.asked(), self.asked_fields(), 'study')

    def set_study(self, study):
        """The ticks, the engines and the Fields ticks of a Study (a config's):
        a question it does not ask unticked. An engine the registry does not
        list is left out; a solver question's solvers are the settings'."""
        self.on = {
            q
            for q in self.questions
            if q in study.asked and self.can_ask(q)
        }
        for q in self.questions:
            names = {e.name for e in self.engines[q]}
            self.choice[q] = ([] if self.is_solver(q) else
                              [n for n in study.asked.get(q, []) if n in names])
            self.fields[q] = bool(study.fields.get(q, False))
        self.changed('study')

    def steps(self, preview=False, only=None):
        """The runs the questions asked take, in order (kicad2ems.core.study's
        plan: [Step]): the solvers' (one run) when an RF question is asked,
        then each analysis engine's in the registry's order; those that cannot
        run here left out. preview: those that have one. only: a question (or
        a run) alone, ticked or not."""
        import dataclasses
        from kicad2ems.core.analyses import registry
        from kicad2ems.core.study import Study, plan
        if only is not None:
            qs = ([only] if only in self.labels else [
                q for q in self.questions
                if only == self.run_of(q) or (only == RF and self.is_solver(q))
            ][:1])
            qs = [q for q in qs if self.can_ask(q)]
        else:
            qs = list(self.asked())
        reg, out = registry(), []
        for st in plan(Study({q: self.engines_for(q) for q in qs}, {})):
            key = key_of(st)
            if key in self.why or preview and key != RF and not (
                    key in reg and reg[key].previewed):
                continue
            if st.kind == 'solvers' and out and out[0].kind == 'solvers':
                # (the window's settings choose its solvers: one run)
                out[0] = dataclasses.replace(
                    out[0], questions=out[0].questions + st.questions)
                continue
            out.append(st)
        return out

    def jobs(self, preview=False, only=None):
        """The runs (their keys) the questions asked take, in order:
        steps'."""
        return [key_of(st) for st in self.steps(preview, only)]

    def asked_of(self, key, only=None):
        """The questions asked of a run ('rf' or an analysis), in the list's
        order."""
        qs = [only] if only in self.labels else list(self.asked())
        return tuple(
            q for q in qs
            if (self.is_solver(q) if key == RF else key in self.runs_for(q)))

    def set_fields(self, q, on):
        """A question's Fields tick, as its settings say (no default filled in:
        they hold what is asked). True: it changed."""
        if q in self.labels and bool(self.fields.get(q)) != bool(on):
            self.fields[q] = bool(on)
            self.changed('fields', q)
            return True
        return False

    def fields_on(self, key):
        """Whether a run's field dumps are asked for: a Fields tick on one of
        its questions."""
        return any(
            self.fields.get(q) and self.can_dump(q) for q in self.asked_of(key))

    def choose(self, q, name, on):
        """Tick or untick an engine of a question that analyses answer ('auto':
        its default alone). The variants of one analysis are one run: ticking
        one unticks the others."""
        names = [e.name for e in self.engines[q]]
        if name == AUTO:
            self.choice[q] = []
        elif name in names:
            cur = (set(self.choice[q] or self.engines_for(q))
                   if on else set(self.choice[q]))
            if on:
                run = run_key(self._engine(q, name))
                cur = {n for n in cur if run_key(self._engine(q, n)) != run}
            cur = (cur | {name}) if on else (cur - {name})
            self.choice[q] = [n for n in names if n in cur]
        self.changed('engines', q)

    # ---- as the runs see it ----
    def ticked(self):
        """The runs of the questions asked, in order."""
        return self.jobs()

    def study_of_runs(self, keys):
        """The Study in which the questions these runs answer are asked, each by
        that engine: 'rf' is S-parameters."""
        asked = {}
        for k in keys:
            for q in self.questions:
                if ((k == RF and q == 'sparams')
                        or (k != RF and any(e.name == k
                                            for e in self.engines[q]))):
                    first = (self.usable(q) or [None])[0]
                    asked.setdefault(q, [])
                    if k != RF and k != first and k not in asked[q]:
                        asked[q].append(k)
        from kicad2ems.core.study import Study
        return Study(asked, {q: self.fields.get(q, False) for q in asked})

    def set_ticked(self, keys):
        self.set_study(self.study_of_runs(keys))

    def ask(self, key):
        """The questions a run answers asked too (those that can be, by that run
        where it is one of several engines: the others stay as they are): what a
        result proposed for it is to be run."""
        for q in self.questions:
            if key != RF and key in self.runs_for(q):
                self.tick(q, True)

    def set_available(self, key, ok, why=''):
        """A run that cannot be made here: its engine is not offered, and a
        question nobody else answers cannot be asked (it is unticked), its state
        saying why."""
        if ok:
            self.why.pop(key, None)
        else:
            self.why[key] = why or 'not installed'
            self.states.pop(key, None)
        self.on = {q for q in self.on if self.can_ask(q)}
        self.changed('available', key)

    def set_state(self, key, text, tone=''):
        """A run's state ('running', 'done 21:42', 'failed (exit 2)', 'not
        started: no rail'); tone: '', 'gray' or 'bad'."""
        if key in self.why:
            return
        self.states[key] = (text, tone)
        self.changed('state', key)

    def state(self, key):
        """A run's state as it was set."""
        return self.states.get(key, ('', ''))[0]

    def clear_states(self):
        """Another board: the last one's runs say nothing of it."""
        self.states.clear()
        self.changed('state')

    def state_of(self, q):
        """A question's state as a list shows it, from its engines' runs: (text,
        tooltip, tone)."""
        if not self.engines[q]:
            return 'not installed', self.missing[q], 'gray'
        if not self.can_ask(q):
            why = '; '.join(
                f"{e.label}: {self.why_not(q, e.name)}" for e in self.engines[q]
                if self.why_not(q, e.name))
            return 'not available', why, 'gray'
        keys = [RF] if self.is_solver(q) else self.runs_for(q)
        by = {run_key(e): e.label for e in self.engines[q]
              if e.name in self.engines_for(q)}
        got = [(k, *self.states[k])
               for k in keys
               if self.states.get(k, ('', ''))[0]]
        cut = lambda t: t.split(' (')[0].split(': ')[0]
        if len(keys) > 1:
            # alike but for the time each ended ('done 21:42', 'done 21:43'):
            # the last's
            word = lambda t: re.sub(r' \d\d:\d\d$', '', cut(t))
            same = (len(got) == len(keys) and len({word(t)
                                                   for _, t, _ in got}) == 1)
            text = (max(cut(t) for _, t, _ in got) if same else ' · '.join(
                f"{by.get(k, k)} {cut(t)}" for k, t, _ in got))
            tip = '\n'.join(f"{by.get(k, k)}: {t}" for k, t, _ in got)
        else:
            text, tip = (cut(got[0][1]), got[0][1]) if got else ('', '')
        tone = ('bad' if any(tn == 'bad' for _, _, tn in got) else
                'gray' if got and all(tn == 'gray' for _, _, tn in got) else '')
        return text, tip, tone


def __getattr__(name):  # the list itself is the window's (studytree.py: Qt)
    if name in ('StudyTree', 'StudyList'):
        from . import studytree
        return getattr(studytree, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
