"""
The study's config with no window: what a config holds, as data, read and
written back.

    StudyDocument.read(path)    a config: the RF settings as the form has them
                                (runner.settings_values), the settings the form
                                has no field for (extra), each analysis's
                                [analysis.<name>] tables (the first, and the
                                rest of an analysis given several: a run
                                each), the tables of analyses not installed
                                here, and the study ([study]: what is asked,
                                who answers)
    doc.write(path, ...)        the settings, the analyses' tables and the
                                study written back (write_study: the last
                                two alone): the loaded config's comments and
                                order kept (runner.save_config),
                                what this machine cannot show (an analysis not
                                installed, a question no engine here answers,
                                an analysis's other tables) kept as it was

The window's form and panels are views of a document (setuptab.py,
winconfigs.py): they are filled from it when a config opens and give it their
values when it is saved. A program that edits a study without the window (the
KiCad plugin, a test) reads, changes and writes the same object. Nothing in
this module needs Qt.
"""
import os

from kicad2ems.core.study import Study


def whole(tab, base):
    """A config table's own board and folder made whole: they are beside the
    config (in base)."""
    for k in ('board', 'out'):
        if tab.get(k) and not os.path.isabs(str(tab[k])):
            tab[k] = os.path.normpath(os.path.join(base, str(tab[k])))
    return tab


class StudyDocument:
    """A config as the window holds it:

        path      the file (None: not saved yet)
        values    the RF settings, as the form has them (the solver ticks
                  included: runner.solver_ticks)
        extra     the config's settings the form has no field for, passed on
                  to the runs as they are
        tables    {analysis: its first [analysis.<name>] table}, the board and
                  folder made whole
        more      {analysis: [its other tables]} of an analysis given several
                  (a board's rails: a run each)
        others    {analysis: table, or [tables]} of analyses not installed
                  here: written back as they were
        study     what is asked and who answers (core.study.Study)
        foreign   the part of the study this machine cannot show (a question
                  no engine here answers): written back as it was
        notes     what reading it found to say (a setting not understood, the
                  board not found)
        dirty     changed since it was read or written

    `installed` is the analyses this machine has (their names): a table of
    another is kept in `others`; None takes kicad2ems's registry."""

    def __init__(self, installed=None):
        self.path, self.values, self.extra = None, {}, {}
        self.tables, self.more, self.others = {}, {}, {}
        self.study, self.foreign = Study({'sparams': []}), Study({})
        self.notes, self.dirty = [], False
        self._installed = installed

    # ---- reading ----
    @property
    def installed(self):
        if self._installed is None:
            from kicad2ems.core.analyses import registry
            self._installed = set(registry())
        return self._installed

    @classmethod
    def read(cls, path, installed=None, questions=None):
        """A config read. Its RF settings must be readable (an error raises);
        its study and analyses' tables are read leniently (a fault is a note,
        and leaves them empty). `questions`: the questions this machine has an
        engine for ({question: engines}); the rest of the study goes to
        `foreign`. None: every question of the study is shown."""
        from kicad2ems.core.config import build_settings
        from .runner import settings_values, extra_values
        doc = cls(installed)
        doc.values, doc.extra = settings_values(path), extra_values(path)
        doc.notes = list(build_settings({}, path).notes)
        board = doc.values.get('board')
        if board and not os.path.exists(board):
            doc.notes.append(
                f"board not found: {board} (generated examples: see the "
                "commands at the top of the file)")
        doc.path = os.path.abspath(path)
        doc._read_study(path, questions)
        return doc

    def _read_study(self, path, questions=None):
        from kicad2ems.core import analyses
        from kicad2ems.core.study import read_config
        base = os.path.dirname(os.path.abspath(path))
        try:
            tables = analyses.read_config(path)
            study = read_config(path, strict=False)
            sets = analyses.read_sets(path)
        except Exception as e:                                 # noqa: BLE001
            self.notes.append(f"{path}: its study could not be read ({e})")
            return
        more = {name: [whole(dict(t), base) for t in ts[1:]]
                for name, ts in sets.items() if len(ts) > 1}
        self.tables = {k: whole(dict(v or {}), base)
                       for k, v in tables.items() if k in self.installed}
        self.more = {k: v for k, v in more.items() if k in self.installed}
        self.others = {k: ([v, *more[k]] if more.get(k) else v)
                       for k, v in tables.items() if k not in self.installed}
        if questions is None:
            self.study, self.foreign = study, Study({})
        else:
            shown = lambda q: q in questions and bool(questions[q])
            self.study = Study(
                {q: n for q, n in study.asked.items() if shown(q)},
                {q: v for q, v in study.fields.items() if q in questions},
                study.said)
            self.foreign = Study(
                {q: n for q, n in study.asked.items() if not shown(q)},
                {q: v for q, v in study.fields.items() if q not in questions})

    def table(self, key):
        """An analysis's first table (a copy; {} when the config has none)."""
        return dict(self.tables.get(key) or {})

    def runs(self, key):
        """An analysis's tables, the first and the rest: a run each."""
        return [self.table(key), *[dict(t) for t in self.more.get(key, [])]]

    # ---- writing ----
    def write(self, path, values=None, tables=None, study=None):
        """Write the config to `path` (the file read is the template: its
        comments and order stay): the RF settings (values; None: the
        document's), then the study and the analyses' tables (write_study).
        The document then is that file, saved."""
        from .runner import save_config
        values = self.values if values is None else values
        save_config(path, values, template=self.path)
        self.values = values
        return self.write_study(path, tables, study)

    def write_study(self, path, tables=None, study=None):
        """The study and the analyses' tables into the config at `path`, its
        other settings left as they are. tables: {analysis: table, or [tables]
        for one with several runs} as the window's panels give them now, paths
        relative to `path` (None: the document's); study: the study now
        (None: the document's)."""
        from kicad2ems.core import study as core_study
        from kicad2ems.core.runs import update_toml
        study = self.study if study is None else study
        base = os.path.dirname(os.path.abspath(path))
        if tables is None:
            tables = {k: self._relative(self.runs(k) if self.more.get(k)
                                        else self.table(k), k, base)
                      for k in self.tables}
        out = dict(self.others)
        for key, tab in tables.items():
            if not tab:
                continue
            # the config's other tables of it, not shown by a window that
            # shows one: kept
            rest = self.more.get(key)
            if rest and not isinstance(tab, list):
                tab = [tab, *[self._relative(t, key, base) for t in rest]]
            out[key] = tab
        update_toml(path, tables={'analysis': out})
        if self.foreign.asked:
            study = Study({**study.asked, **self.foreign.asked},
                          {**study.fields, **self.foreign.fields}, 'study')
        plain = (list(study.asked) == ['sparams']
                 and not study.fields.get('sparams') and not out)
        core_study.write_config(path, None if plain else study)
        self.path, self.study, self.dirty = os.path.abspath(path), study, False
        return self.path

    def _relative(self, tab, key, base):
        """A table's paths (the analysis's, its board and folder) relative to
        where the config is written."""
        if isinstance(tab, list):
            return [self._relative(t, key, base) for t in tab]
        from kicad2ems.core.analyses import registry
        paths = (*getattr(registry().get(key), 'paths', ()), 'board', 'out')
        from .runner import relative
        rel = lambda v: (relative(v, base)
                         if isinstance(v, str) and os.path.isabs(v) else v)
        return {k: (rel(v) if k in paths else v) for k, v in tab.items()}
