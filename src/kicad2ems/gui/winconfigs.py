"""
The main window's configs (app.py): the examples and the recent files, a
config's study and analyses' settings read and written, a file dropped on the
window.
"""
import os
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox
from .analysispanel import config_values
from .study import BY_KEY, RF
from .winparts import _Preparing

MAX_RECENT = 8


def _whole(tab, base):
    """A config table's own board and folder made whole: they are beside the
    config (in base)."""
    for k in ('board', 'out'):
        if tab.get(k) and not os.path.isabs(str(tab[k])):
            tab[k] = os.path.normpath(os.path.join(base, str(tab[k])))
    return tab


class WindowConfigs:
    """MainWindow's configs and examples."""

    def _fill_examples(self):
        """The examples, kicad2ems's own and those the installed analyses ship
        (pcbqs's circuits), as a menu of their groups; one opens from a folder
        of the user's, where it is copied, its board made if it is generated."""
        from kicad2ems.core.api.frontend import example_index
        self.examples_menu.clear()
        try:
            entries = example_index()
        except Exception:  # an analysis's fault: no examples, not no window
            entries = []
        # {group path: its menu}, {top group: whose}
        menus, self.example_sources = {}, {}
        for e in entries:
            m, path = self.examples_menu, ''
            for part in [
                    p.strip()
                    for p in (e.get('group') or '').split('/')
                    if p.strip()
            ]:
                path += '/' + part
                # kept here: QAction.menu() must not be asked for them
                # (PySide deletes the menu with the wrapper it hands back)
                if path not in menus:
                    menus[path] = m.addMenu(part)
                    menus[path].setToolTipsVisible(True)
                m = menus[path]
            top = '/' + (e.get('group') or '').split('/')[0].strip()
            self.example_sources.setdefault(top, set()).add(e.get('source'))
            m.addAction(
                QAction(
                    e['name'], self, toolTip=e.get('title') or '',
                    statusTip=e.get('title') or '', triggered=lambda _=False, n=
                    e['name']: self.open_example(n)))
        self.example_menus = menus
        self.examples_menu.setEnabled(bool(entries))

    def open_example(self, name):
        """An example: made (its board generated, its files copied) in a thread,
        then loaded; the window had waited on the generator (R-D22)."""
        if not self.setup.maybe_save():
            return
        if getattr(self, '_preparing', None) is not None:  # one at a time
            return
        self.statusBar().showMessage(f"preparing the example {name}...")
        self._preparing = job = _Preparing(name, self)
        job.done.connect(lambda path, n=name: self._example_ready(n, path))
        job.start()

    def _example_ready(self, name, path):
        job, self._preparing = self._preparing, None
        if job is not None:
            job.wait()
        # a generator that failed: say so, keep the form
        if job is not None and job.error:
            QMessageBox.warning(self, 'Examples', f"{name}: {job.error}")
            return
        self.statusBar().clearMessage()
        self.setup.load(path)

    def recent(self):
        v = self.qsettings.value('recent', [])
        v = [v] if isinstance(v, str) else list(v or [])
        return [p for p in v if os.path.exists(p)]

    def remember(self, path):
        r = [path] + [p for p in self.recent() if p != path]
        self.qsettings.setValue('recent', r[:MAX_RECENT])
        self._fill_recent()
        self.start_page.refresh_recent()

    def _fill_recent(self):
        self.recent_menu.clear()
        r = self.recent()
        for p in r:
            self.recent_menu.addAction(
                QAction(p, self, triggered=lambda _=False, p=p: self._open(p)))
        self.recent_menu.setEnabled(bool(r))

    def _open(self, path):
        if self.setup.maybe_save():
            self.setup.load(path)

    def load_analyses(self, path=None):
        """A config's study into the list
        (kicad2ems.core.api.frontend.read_study: its [study] tables, else the
        analyses [analysis] run names; neither: S-parameters) and each
        analysis's settings from its [analysis. <name>] table, the others'
        cleared. path None: a new study, nothing set. A table of an analysis not
        installed here is kept, and saved back, as is a question or an engine of
        the study this window lacks."""
        from kicad2ems.core.api.frontend import (
            read_analyses, read_analysis_sets, read_study, Study)
        tables, study, more = {}, Study({'sparams': []}), {}
        if not self.busy():
            # another study: the last one's states are not its
            self.study.clear_states()
        self._fresh_log()
        base = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
        if path:
            try:
                tables = read_analyses(path)[1]
                study = read_study(path, strict=False)
                # the analyses a config gives several tables
                # ([[analysis.<name>]]: a run each): those after the first
                more = {
                    name: [_whole(dict(t), base)
                           for t in sets[1:]]
                    for name, sets in read_analysis_sets(path).items()
                    if len(sets) > 1
                }
            except Exception as e:
                self.statusBar().showMessage(
                    f"{path}: its study could not be read ({e})", 8000)
        self.more_sets = more
        loading, self.setup._loading = self.setup._loading, True
        try:
            for key, page in self.pages.items():
                tab = _whole(dict(tables.get(key) or {}), base)
                if key in self.editors:
                    page.load_settings(tab)
                    continue
                page.reset()
                page.set_own(tab.pop('board', None), tab.pop('out', None))
                # several runs (a board's rails): its table
                if getattr(page, 'table', None) and more.get(key):
                    page.set_runs([tab, *more.pop(key)])
                else:
                    page.set_values(tab)
            self.extra_analyses = {
                k: ([v, *more[k]] if more.get(k) else v)
                for k, v in tables.items()
                if k not in self.pages
            }
            # an analysis given several tables (a board's rails): the first is
            # shown and run here
            for key, rest in more.items():
                page = self.pages.get(key)
                if page is not None and hasattr(page, 'status'):
                    tags = ', '.join(self.registry[key].tag(dict(v))
                                     for v in rest)
                    page.status.setText(
                        f"This config has {len(rest)} more of these ({tags}): "
                        "the first is shown and run here, and all of them by "
                        "`kicad2ems study`; saving keeps them."
                    )
            self.extra_study = Study(
                {
                    q: n
                    for q, n in study.asked.items()
                    if q not in self.study.rows or not self.study.engines[q]
                }, {
                    q: v
                    for q, v in study.fields.items()
                    if q not in self.study.rows
                })
            fields = dict(study.fields)
            # a config of before the Fields ticks: what its settings ask for
            for q in study.asked:
                if q not in fields and q in self.study.rows:
                    fields[q] = self._fields_set(q, study)
            self.study.set_study(Study(study.asked, fields, study.said))
            self.setup._antenna_from_mode()  # mode = "antenna" in the settings
        finally:
            self.setup._loading = loading
        asked = list(self.study.asked())
        # the study's first question: its settings shown, its editor (a
        # circuit's) in front
        first = asked[0] if asked else 'sparams'
        self.study.select(first)
        run = self.study.run_of(first) if first in self.study.rows else RF
        if path and run in self.editors:
            self.show_panel(BY_KEY[run].page)

    def _fresh_log(self):
        """Another study: the Log page blank, unless a run of the last one is
        going (not its preview, which ends: board_loaded), whose log it is."""
        going = (self.job not in (None, RF) or self.queue
                 or any(p.proc is not None for p in self.pages.values())
                 or self.proc is not None and not self.current.preview)
        if going:
            return
        self.tail = None  # (the preview's last events: not this study's)
        self.runtab.clear()

    def _fields_set(self, question, study):
        """Whether a question's settings ask for field dumps, for a study that
        does not say (a config written before the list had the tick): a solver's
        dump setting filled in, an analysis's fields switch on."""
        st = self.study
        if st.is_solver(question):
            if question == 'lines':
                return False
            vals = self.setup.values()
            return any(vals.get(k) for k in self.setup.dump_keys())
        for name in (study.asked.get(question) or st.engines_for(question)):
            e = next(
                (x for x in st.engines.get(question, []) if x.name == name),
                None)
            box = (self.setup.fields_box(name, e.fields)
                   if e is not None and e.fields else None)
            if box is not None and box.isChecked():
                return True
        return False

    def save_analyses(self, path):
        """The study into a config: what it asks and who answers as its [study]
        tables (kicad2ems.core.api.frontend. write_study), each analysis's
        settings that are set as its [analysis.<name>] table. A study of
        S-parameters alone with nothing else set writes neither."""
        from kicad2ems.core.api.frontend import write_study, Study
        from .runner import write_analyses
        base = os.path.dirname(os.path.abspath(path))
        tables = dict(getattr(self, 'extra_analyses', {}))
        for key, page in self.pages.items():
            tab = (page.save_settings(path)
                   if key in self.editors else config_values(page, base))
            if (key not in self.editors and getattr(page, 'table', None)
                    and len(page.runs()) > 1):
                # a table a row
                tab = [config_values(page, base, r) for r in page.runs()]
            if tab:
                # the config's other tables of it: kept, their paths relative
                # to where it is saved
                rest = getattr(self, 'more_sets', {}).get(key)
                if rest:
                    paths = (*getattr(self.registry.get(key), 'paths',
                                      ()), 'board', 'out')
                    rel = lambda v: (
                        os.path.relpath(v, base).replace(os.sep, '/')
                        if isinstance(v, str) and os.path.isabs(v) else v)
                    tab = [
                        tab, *[{
                            k: (rel(v) if k in paths else v)
                            for k, v in t.items()
                        }
                               for t in rest]
                    ]
                tables[key] = tab
        # [study] says which run: no [analysis] run
        write_analyses(path, None, tables)
        st = self.study.study()
        extra = getattr(self, 'extra_study', None)
        # what this window could not show: kept
        if extra is not None and extra.asked:
            st = Study({**st.asked, **extra.asked},
                       {**st.fields, **extra.fields}, 'study')
        plain = (list(st.asked) == ['sparams'] and not st.fields.get('sparams')
                 and not tables)
        write_study(path, None if plain else st)

    # ---- a file or folder dropped on the window (R-E25) ---------------------
    DROPPED = ('.kicad_pcb', '.toml', '_board.json', '.gbrjob')

    def _dropped(self, e):
        urls = ([u.toLocalFile() for u in e.mimeData().urls()]
                if e.mimeData().hasUrls() else [])
        return [
            p for p in urls
            if p and (os.path.isdir(p) or p.lower().endswith(self.DROPPED))
        ]

    def dragEnterEvent(self, e):
        if self._dropped(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        """A config opens; a board (.kicad_pcb, a run's board.json, a Gerber
        job) becomes the board; a folder's results open."""
        paths = self._dropped(e)
        if not paths:
            return
        p = paths[0]
        e.acceptProposedAction()
        if os.path.isdir(p):
            self.open_results(p)
        elif p.lower().endswith('.toml'):
            self._open(p)
        else:
            self.setup.edits['board'][0].setText(p)
