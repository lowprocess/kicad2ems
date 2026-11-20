"""
The main window's study (app.py): the analyses in the list, what each run still
needs (the list under the settings, the Run button's wording, the steps bar),
a proposal of one analysis's result for another, and the board as drawn, with a
setting picked on it.
"""
import json, os
from .study import StudyList, RF, title
from .winparts import _Layout


class WindowStudy:
    """MainWindow's study, its needs and picking."""

    # ---- the study: the analyses, which a Run includes, their settings in the
    # dock ----
    def _study(self):
        """The analyses' list into the settings dock, with each one's settings
        under it; the ticks of the last session (the first time: S-parameters,
        as Run always did)."""
        self._study_ready = False
        from .runqueue import RunQueue
        # a Run's runs in progress, with no window: its states said here
        self.runq = RunQueue(
            on_state=lambda k, text, bad, sec: self._job_state(k, text, bad,
                                                               sec),
            title=title)
        self.study = StudyList()
        panels = dict(self.panels)
        panels.update({k: p.settings_panel() for k, p in self.editors.items()})
        self.setup.set_study(self.study, panels)
        for key, page in self.pages.items():
            ok, why = self.registry[key].check()
            if not ok:
                self.study.set_available(key, False, why)
            # its model could not be made: why
            elif key in self.editors and not page.run_btn.isEnabled():
                self.study.set_available(key, False, page.status.text())
            self.runtab.add_log(key, title(key), page.log)
        # an analysis's variants: the solver each takes (openEMS missing)
        for es in self.study.engines.values():
            for e in es:
                if (getattr(e, 'sets', None) and e.name != e.run
                        and e.run in self.pages):
                    ok, why = e.check()
                    if not ok:
                        self.study.set_available(e.name, False, why)
        self.study.set_study(self._saved_study())
        self.setup.follow_fields()
        self.study.opened.connect(self.show_analysis)
        self.study.run_one.connect(lambda q: self.run_study(only=q))
        self.study.show_run.connect(self.show_run)

    def _saved_study(self):
        """The study of the last session: its questions, their engines and
        Fields ticks (the first time: S-parameters, as Run always did; a session
        of before the questions: the runs it had ticked)."""
        from kicad2ems.core.study import Study
        raw = self.qsettings.value('study/asked')
        if isinstance(raw, str) and raw:
            try:
                d = json.loads(raw)
                return Study(
                    {
                        str(q): [str(n)
                                 for n in names]
                        for q, names in d.get('asked', {}).items()
                    },
                    {str(q): bool(v)
                     for q, v in d.get('fields', {}).items()})
            except (ValueError, AttributeError, TypeError):
                pass
        old = self.qsettings.value('study/ticked')
        if old is None:
            return Study({'sparams': []})
        keys = [
            k for k in ([old] if isinstance(old, str) else list(old))
            if k == RF or k in self.registry
        ]
        asked = {}
        for k in keys:
            for q in self.study.rows:
                if ((k == RF and q == 'sparams')
                        or (k != RF and any(e.name == k
                                            for e in self.study.engines[q]))):
                    asked.setdefault(q, [])
        return Study(asked)

    # ---- what a run still needs: the list under the selected question's
    # settings, the Run button's wording ----
    def needs(self, runs):
        """{run: [dict(text, key, run)]} of the runs `runs` ('rf', or an
        analysis): what each still needs before it can start (kicad2ems's
        rf_needs and the solvers chosen; an analysis's own check)."""
        out, before = {}, []  # before: the analyses of this Run that come first
        for run in runs:
            if run == 'rf':
                from kicad2ems.core.study import rf_needs
                vals = self.setup.values()
                got = [
                    dict(text=f"{k.replace('_', ' ')}: not a number ({v!r})",
                         key=k)
                    for k, v in getattr(self.setup, 'bad', {}).items()
                ]
                try:
                    got += rf_needs(vals)
                # (what rf_needs cannot read)
                except Exception:  # noqa: BLE001
                    pass
                if (not any(w.isChecked()
                            for w in self.setup.solver_boxes.values())):
                    got.append(
                        dict(
                            text=
                            'no solver chosen: pick one on the S-parameters '
                            'row',
                            key=None))
            elif run in self.pages:
                page = self.pages[run]
                got = getattr(page, 'panel',
                              page).needs(self.study.asked_of(run, None),
                                          before)
                before.append(run)
            else:
                got = []
            out[run] = [dict(m, run=run) for m in got]
        return out

    def needs_soon(self):
        """The list and the Run button brought up to date shortly (a change,
        typed: not at every key)."""
        if getattr(self, '_study_ready', False):
            self._needs_timer.start(250)

    def update_needed(self):
        if (not getattr(self, '_study_ready', False)
                or not hasattr(self, 'acts')):
            return
        self.want_layout()  # (a board chosen in the settings: once a file)
        self._start_follows()
        q = self.study.current_question()
        mine = self.study.panels_of(q) if q in self.study.rows else ['rf']
        asked = self.study.jobs(False)
        # in the Run's order: each knows what comes before it
        needs = self.needs(asked)
        needs.update(self.needs([r for r in mine if r not in needs]))
        self.setup.needed.show_items(
            [m for r in mine for m in needs.get(r, [])], q
            in self.study.asked())
        first = next((m for r in asked for m in needs.get(r, [])), None)
        self._need = first
        # (the steps bar's Set up)
        self._needs_all = [m for r in asked for m in needs.get(r, [])]
        # the button reads Run, always: what a Run uses is counted beside it
        # (the estimate), what it still needs in the steps bar and here
        run = self.acts['Run']
        run.setText('Run')
        if first is not None:
            run.setToolTip(
                f"{title(first['run'])} still needs: "
                f"{first['text']}\n(click: go to where it is set; Run > Run "
                "the selected question alone runs one whatever the others "
                "need)"
            )
        else:
            titles = [title(k) for k in asked]
            run.setToolTip(('Run ' + ', then '.join(titles) if titles else
                            'Tick a question in the list first') + ' (Ctrl+R)')
        self.update_estimate()
        self.runs_panel.marks()  # ≠ settings: against the form as it is now
        self.steps.refresh()

    def go_to_step(self, key):
        """A step of the bar was clicked: where it is done. Board: the study's
        Board section; Questions: the list; Set up: what is still needed first,
        else the settings; Run: the runs' log; Results: the result's pages
        (none shown: Runs)."""
        if key in ('board', 'questions'):
            if self.study_dock.isHidden():
                self.study_dock.show()
            if key == 'board':
                self.setup.focus('board')
            else:
                self.study.setFocus()
        elif key == 'setup':
            if self.settings_dock.isHidden():
                self.settings_dock.show()
            if getattr(self, '_need', None):
                self.go_to_need(self._need)
        elif key == 'run':
            self.show_panel('Run')
        elif self.shown:
            self.show_result()
        else:  # nothing shown: the runs to pick one from
            self.show_panel('Runs')

    def go_to_need(self, item):
        """Where a thing a run still needs is set: its question selected, its
        field in view."""
        run, key = item.get('run'), item.get('key')
        if (run and self.study.current() != run and run
                not in self.study.panels_of(self.study.current_question())):
            self.study.select(run)
        if run == 'rf' and key:
            self.setup.focus(key)
        elif run in self.pages and key:
            page = self.pages[run]
            getattr(page, 'panel', page).focus(key)
        self.statusBar().showMessage(
            f"{title(run) if run else 'The run'} still needs: "
            f"{item['text']}",
            8000)

    def ticks_changed(self):
        self.needs_soon()
        if getattr(self, '_study_ready', False):
            st = self.study.study()
            self.qsettings.setValue(
                'study/asked', json.dumps(dict(asked=st.asked,
                                               fields=st.fields)))
            self._page_visibility()
            if self.setup.path:     # an open config says what its study asks
                self.setup.touch()  # (without one, the next window remembers)

    def show_analysis(self, key):
        """A run's page in front (S-parameters: Results), the settings of a
        question it answers in the dock. key: 'rf' or an analysis; a question:
        its run."""
        self.study.select(key)
        if key in self.study.rows:
            key = self.study.run_of(key)
        if key == RF or key in self.registry:
            if key in self.editors:
                if not self.show_panel(title(key)):
                    self.statusBar().showMessage(
                        f"Tick {title(key)} in the list: its page is "
                        "there while it is asked", 6000)
            else:
                self.show_result()

    def apply_suggestion(self, source, s):
        """What one analysis's result proposes for another (Analysis.suggest):
        those settings into the other's panel, on the same board, and its
        questions asked; the options it had no value for must be set before a
        run. For the analysis with an editor of its own (the circuit), a model
        the proposal names is opened there, as the study's own (a config then
        writes it beside itself, not into the result it came from)."""
        target = self.pages.get(s.get('to'))
        if target is None:
            return
        values = dict(s.get('values') or {})
        # a board of its own: the same one there
        own = source.board.text().strip()
        if hasattr(target, 'set_values'):
            panel = target
            panel.reset()
            if own:
                panel.set_own(own, None)
            panel.set_values(values)
        # (no model named: its settings for the model on the page, which
        # stays, with its file)
        elif hasattr(target, 'load_settings'):
            named = bool(values.get('model'))
            target.load_settings(
                dict(values, **({'board': own} if own else {})),
                keep_model=not named)
            if named:
                target.model_path = None
            panel = target.settings_panel()
        else:
            return
        panel.required = {
            k
            for k in s.get('need') or []
            if not str(values.get(k) or '').strip()
        }
        panel.status.setText(s.get('note', ''))
        panel.changed.emit()
        # proposed for it: asked, so Run includes it
        self.study.ask(s['to'])
        # its settings, and its page (the schematic)
        self.show_analysis(s['to'])

    # ---- the board as drawn, before anything is run; a setting picked on it
    # ----
    def want_layout(self):
        """The chosen board's layout (kicad2ems.core.layout) into the Viewer's
        plan wherever no run's board is shown there: read in a thread (a second
        or a few), once a board file. True: there, or being read. Another board
        (New, or one chosen in the settings): Runs lists its runs, and a
        run of the last board leaves the pages (they had kept it under the new
        study)."""
        board = self.setup.values().get('board')
        now = os.path.abspath(board) if board else None
        if now != getattr(self, '_runs_board', None):
            shown = getattr(self.viewer, 'out', None)
            self.refresh_runs()
            if shown and os.path.abspath(shown) not in [
                    r.folder for r in self.runs_panel.board_runs()]:
                self.viewer.clear()
                self.results.show_files([])
                self.shown = None
                self.update_showing()
        self._runs_board = now
        if (not board or not str(board).lower().endswith('.kicad_pcb')
                or not os.path.isfile(board)):
            # the board taken away: its drawing too
            if self._layout_stamp is not None:
                self._layout_stamp, self.drawn = None, None
                self.viewer.show_layout(None)
                self.draw.set_board(None)
            return False
        stamp = (os.path.abspath(board), os.path.getmtime(board))
        if stamp == self._layout_stamp:
            return not self._layout_bad
        self._layout_stamp, self.drawn, self._layout_bad = stamp, None, False
        t = _Layout(stamp[0])
        t.done.connect(self._layout_ready)
        t.start()
        return True

    def _layout_ready(self, board, layout):
        # (another board chosen since)
        if board != (self._layout_stamp or (None,))[0]:
            return
        # the board as drawn; None: not one to read
        self.drawn, self._layout_bad = layout, layout is None
        self.viewer.show_layout(layout)
        self.draw.set_board(layout)
        self.steps.refresh()
        if self._picking:  # a pick was waiting for it
            self._begin_pick()

    def start_pick(self, panel, key):
        """Setting `key` of an analysis's panel is picked on the board: the
        Viewer's plan in front, showing the board as drawn (in place of a run's
        board for as long), its clicks into the setting until end_pick."""
        # one at a time: the other's button let go
        if self._picking and self._picking != (panel, key):
            other, k = self._picking
            self._picking = None
            other.pick_btns[k].setChecked(False)
        # (the table's selected cell: not a net's or a part's)
        if not panel.pick_kind(key):
            self._no_pick(
                panel, key,
                'select a cell that names a net or a part, then pick')
            return
        self._picking = (panel, key)
        self._begin_pick()

    def _no_pick(self, panel, key, why):
        self.statusBar().showMessage(why, 6000)
        self._picking = None
        self.viewer.plan.end_pick()
        panel.pick_btns[key].setChecked(False)

    def _begin_pick(self):
        panel, key = self._picking
        plan = self.viewer.plan
        reading = self.want_layout()
        if self.drawn is None and reading:
            self.statusBar().showMessage('reading the board to pick on it …',
                                         4000)
            return  # _layout_ready comes back here
        if self.drawn is None and not plan.board:
            self._no_pick(panel, key,
                          'no board to pick on: choose a .kicad_pcb in Files')
            return
        self.show_panel('Viewer')
        self.viewer.buttons['Plan'].setEnabled(True)
        self.viewer.set_view('Plan')
        plan.start_pick(panel.pick_kind(key), lambda name: self._picked(name),
                        panel.picked_names(key), layout=self.drawn)

    def _picked(self, name):
        if self._picking:
            panel, key = self._picking
            self.viewer.plan.set_picked(panel.take_pick(key, name))

    def end_pick(self, panel=None, key=None):
        if self._picking and (panel is None or self._picking == (panel, key)):
            self._picking = None
            self.viewer.plan.end_pick()
