"""
The main window's runs as they go (app.py): the study's queue (the Steps of
kicad2ems's plan, StudyModel.steps), each step's commands run by the one
launcher (runprocess.RunProcess: kicad2ems's own run with its events, an
analysis's tool a command per row of its table), Stop, and the estimate of what
a Run takes.
"""
import json, os
from PySide6.QtWidgets import QMessageBox
from kicad2ems.core import runs
from kicad2ems.core.runs import Run
from kicad2ems.core.study import engines, unmet
from .runprocess import Command, RunProcess
from .study import RF, title


class WindowRun:
    """MainWindow's runs: started, followed, stopped."""

    def _runner(self):
        """The one launcher, its signals to the window and the pages."""
        self.runner = r = RunProcess(self)
        r.began.connect(self._began)
        r.output.connect(self._output)
        r.ended.connect(self._ended)
        r.skipped.connect(lambda text: self._panel(self.job) is not None
                          and self._panel(self.job).skipped(text))
        r.finished.connect(lambda code: self._job_done(
            r.last.key if r.last else None, code))

    def _panel(self, key):
        page = self.pages.get(key)
        return getattr(page, 'panel', page)

    def busy(self):
        """A run of the window's or a tool of a page's is going."""
        return (self.runner.busy() or self.job is not None or bool(self.queue)
                or any(p.proc is not None for p in self.pages.values()))

    def run_study(self, preview=False, only=None):
        """Run (preview) the questions asked: the runs they take, one after the
        other (kicad2ems's plan: the solvers' first, then each analysis);
        only: that question (or run) alone. One that cannot start (a setting
        missing) says why in the list, and the rest go on, except those that
        need it."""
        if self.busy():
            QMessageBox.information(self, 'Busy',
                                    'A run is going: stop it first')
            return
        steps = self.study.steps(preview, only=only)
        if not steps:
            QMessageBox.warning(self, 'Study', (
                'None of the questions asked has a preview: tick S-parameters '
                'or Converter near field in the list'
                if preview else 'Tick a question in the list first'))
            return
        from .study import key_of
        self._steps = {key_of(st): st for st in steps}
        self.queue, self._queue_preview, self._queue_stopped = (
            list(self._steps), preview, False)
        self._failed = set()
        self.runtab.set_jobs([(k, title(k)) for k in self.queue])
        for k in self.queue:
            self.study.set_state(k, 'queued', 'gray')
        self._next_job()

    def _next_job(self):
        import time
        if self.job is not None:  # one at a time
            return
        while self.queue:
            key = self.queue.pop(0)
            st = getattr(self, '_steps', {}).get(key)
            need = (unmet(st.engines[0], self._failed)
                    if st is not None and st.kind == 'analysis' else [])
            if need:
                self._failed.add(key)
                self._job_state(key, "not run: " + ', '.join(map(title, need))
                                + ' did not finish', bad=True)
                continue
            self.job, self._job_t0 = key, time.monotonic()
            try:
                why = self._start_job(key, self._queue_preview)
            # a page's or an analysis's bug: said on the row, and the queue goes
            # on (the window must not stay busy with nothing running)
            except Exception as e:
                import traceback
                panel = self._panel(key)
                (panel.log if panel is not None else
                 self.runtab.log).appendPlainText(traceback.format_exc())
                why = f"{type(e).__name__}: {e}"
            if why is None:
                self._job_state(key, 'running')
                self.acts['Stop'].setEnabled(True)
                self.runtab.stop_btn.setEnabled(True)
                self.show_panel('Run', front=not self._queue_preview)
                return
            self._failed.add(key)
            self._job_state(key, f"not started: {why}", bad=True)
        self.job = None
        self.acts['Stop'].setEnabled(self.runner.busy())
        self.runtab.stop_btn.setEnabled(self.runner.busy())

    def _start_job(self, key, preview):
        """Start one step: kicad2ems's own run, or an analysis's commands (a
        row of its table each); None, or why it did not start."""
        st = self._steps[key]
        if key == RF:
            return self.start(preview, st.questions)
        panel = self._panel(key)
        panel.log.clear()  # one log for the rows' runs together
        e = engines()[st.engines[0]]
        return self.runner.start(panel.commands(
            preview, st.questions, e.name, e.sets)) or None

    def _began(self, cmd):
        if cmd.key != RF and self._panel(cmd.key) is not None:
            self._panel(cmd.key).began(cmd)
            self.refresh_runs()  # listed, running
        p = self.runner.proc
        p.started.connect(lambda: self.meters.set_run(p.processId() or None))

    def _output(self, cmd, text):
        if cmd.key == RF:
            self._stdout(text)
        elif self._panel(cmd.key) is not None:
            self._panel(cmd.key).output(text)

    def _ended(self, cmd, code, how):
        self.meters.set_run(None)
        if cmd.key == RF:
            self._finished(code, how)
        elif self._panel(cmd.key) is not None:
            self._panel(cmd.key).done(cmd, code, how)

    def _job_state(self, key, text, bad=False, seconds=None):
        self.runtab.job_state(key, text, seconds)
        self.study.set_state(key, text, 'bad' if bad else '')
        self.steps.refresh()
        if key != 'rf':  # kicad2ems's own run says its stages itself
            self.runtab.stage.setText(f"{title(key)}: {text}")
            if text == 'running':
                self.runtab.own_plots(False)

    def _job_done(self, key, code):
        """A step of the queue ended: its state and time, then the next (the
        last one's page in front)."""
        import time
        if self.job != key or key is None:
            return
        took = time.monotonic() - self._job_t0
        if code == 0:
            self._job_state(key, f"done {time.strftime('%H:%M')}", seconds=took)
            out = getattr(self.pages.get(key), 'out', None)
            # its maps, its report and its curves
            if key != RF and out and os.path.isdir(str(out)):
                self.refresh_runs(select=str(out))
                self.open_results(str(out), quiet=True, front=False)
        else:
            self._failed.add(key)
            self._job_state(
                key,
                ('stopped' if self._queue_stopped else f"failed (exit {code})"),
                bad=not self._queue_stopped, seconds=took)
        self.job = None
        self.steps.refresh()
        self._page_visibility()  # (a page of a run alone, not ticked: gone)
        if self.queue:  # after this one's own end is dealt with
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._next_job)
            return
        self._next_job()               # none left: the toolbar's Stop
        if key != 'rf' and code == 0:  # S-parameters: _finished chose its page
            if self._queue_preview:    # a mesh preview: the mesh, in the plan
                self.viewer.set_view('Plan')
                self.show_panel('Viewer')
            else:                                                   # its report
                # (an editor's page is its model: not its result)
                self.show_result()

    def _basis(self):
        """The settings an estimate depends on: all but the solver choice (which
        the estimate follows) and where the results go."""
        v = self.setup.values()
        for k in ('solvers', 'out', 'compare', 'no_report', 'events'):
            v.pop(k, None)
        return json.dumps(v, sort_keys=True, default=str)

    def new_estimates(self, est):
        """A preview's estimates: beside the solvers in the S-parameters row's
        menu and in the toolbar, for the settings as they are now."""
        self.setup.set_estimates(est)
        self._est_basis = self._basis() if est else None
        self.update_estimate()

    def update_estimate(self):
        """The toolbar's estimate for the solvers ticked now (greyed when other
        settings changed since), and how many solvers a Run uses."""
        if not hasattr(self, 'est_btn'):
            return
        from .meters import estimate
        est = self.setup.est
        info = self.system.info or {}
        labels = {
            x['name']: x['label'].split(' (')[0]
            for x in info.get('solvers', [])
        }
        e = None
        if est:
            usable = ({x['name'] for x in info.get('solvers', []) if x['ok']}
                      if info.get('solvers') else None)
            e = estimate(est, self.setup.values(), usable, self.meters.free_gb)
        jobs = (self.study.jobs(False)
                if getattr(self, '_study_ready', False) else [])
        runs = ((self._rf_solvers(e, labels) if RF in jobs else []) +
                [title(k) for k in jobs if k != RF])
        self.est_btn.show_estimate(
            e, has_preview=bool(est), labels=labels, runs=runs,
            rf=RF in jobs, stale=(e is not None and self._est_basis is not None
                                  and self._basis() != self._est_basis))

    def _rf_solvers(self, e, labels):
        """The solvers the S-parameters run uses, a name each: the full-wave
        ones the estimate is of (auto's choice), else those ticked ('auto' while
        none is), then the others ticked (the line model's)."""
        from kicad2ems.core.solvers import registry, AUTO
        try:
            plan = self.setup.values().get('solvers')
        except ValueError:  # (a field half typed)
            plan = None
        plan = [AUTO] if plan is None else list(plan)
        full = {n for n, sv in registry().items() if sv.consumes == 'board'}
        name = lambda n: labels.get(n, n)
        if e is not None:
            out = [name(n) + (' (auto)' if auto else '')
                   for n, *_, auto in e['rows']] + list(e.get('missing') or [])
        else:
            out = ([name(n) for n in plan if n in full]
                   or (['auto'] if AUTO in plan else []))
        return out + [name(n) for n in plan
                      if n not in full and n != AUTO and n in registry()]

    def tool_out(self, kind, board, name, preview=False):
        """Where a page's tool writes a run started here: a folder of its own
        under the output folder (the board's 'out', or the Output setting), so
        earlier runs stay to compare with; a preview: one per board."""
        from kicad2ems.core.config import default_out
        from kicad2ems.core.runs import tool_dir, tool_preview_dir
        try:
            base = self.setup.values().get('out')
        except ValueError:
            base = None
        base = (
            base or
            (default_out(board) if board else os.path.join(os.getcwd(), 'out')))
        return (tool_preview_dir(base, board, kind) if preview else tool_dir(
            base, board, kind, name))

    def out_dir(self, vals=None):
        """The output folder: the form's, else 'out' next to the board (where a
        run writes), else in the working directory."""
        from kicad2ems.core.config import default_out
        vals = self.setup.values() if vals is None else vals
        if vals.get('out'):
            return vals['out']
        board = vals.get('board')
        return default_out(board) if board else os.path.join(os.getcwd(), 'out')

    def run_values(self, preview):
        """The settings kicad2ems's own run (preview) would start with now: the
        form's, without the field dumps when the RF row's Fields is not
        ticked (the settings keep them)."""
        vals = self.setup.values()
        if not preview and not self.study.fields_on(RF):
            for k in self.setup.dump_keys():
                vals.pop(k, None)
        return vals

    def start(self, preview, asked=None):
        """Start kicad2ems's own run (the S-parameters analysis) or its preview,
        asked those questions (None: the RF ones asked); None, or why it did
        not start (said in a dialog too when the user started it alone)."""
        def no(title, why):
            # alone: a dialog; in a queue: the list says it
            if self.job != 'rf' or not self.queue:
                (QMessageBox.information
                 if title == 'Busy' else QMessageBox.warning)(self, title, why)
            return why
        if self.runner.busy():
            return no('Busy', 'A run is going: stop it first')
        vals = self.setup.values()
        if getattr(self.setup, 'bad', None):
            k, v = next(iter(self.setup.bad.items()))
            return no('Setup', f"{k.replace('_', ' ')}: {v!r} is not a number")
        if not vals.get('board'):
            return no('Setup', 'Choose a board first')
        if not preview and vals.get('solvers') == []:
            return no(
                'Setup',
                "Choose a solver on the list's S-parameters row (or Preview)")
        vals = self.run_values(preview)
        base = self.out_dir(vals)  # each run in a folder of its own
        self.current = Run(vals, (
            runs.preview_dir(base, vals['board']) if preview else
            runs.run_dir(base, vals['board'])), preview)
        runs.mark(self.current.out, RF,
                  asked or self.study.asked_of(RF) or ('sparams', ), preview,
                  config=self.config_path())
        self._auto = getattr(self, '_auto', False) and preview
        self.files, self._traceback = [], False
        self.runtab.reset()
        self.runner.start([Command(RF, self.current.argv(), self.current.out,
                                   events=self.current.events,
                                   preview=preview)])
        self.runtab.stop_btn.setEnabled(True)
        self.acts['Stop'].setEnabled(True)
        self.runtab.stage.setText('starting…')
        self.runtab.started('rf')
        self.show_panel('Run', front=not preview)
        self.timer.start(300)
        self.refresh_runs()  # listed, running
        return None

    def preview(self):
        self.run_study(preview=True)

    def run(self):
        """Run what is asked; something it still needs: there instead (the
        button says which)."""
        self.update_needed()
        if getattr(self, '_need', None) is not None:
            self.go_to_need(self._need)
            return
        self.run_study()

    def stop(self):
        """What is queued: not run. The run going: asked to stop at the first
        press (kicad2ems's own: stop.py, mom25d after the frequency it is
        solving, openEMS at once with what it has, later solvers skipped; a
        tool: SIGTERM, it stops what it started), its process ended at the
        second (RunProcess.stop)."""
        for k in self.queue:
            self._job_state(k, 'not run: stopped')
        self.queue = []
        if self.job is None and not self.runner.busy():
            return
        self._queue_stopped = True
        said = self.runner.stop()
        if said:
            self.runtab.log.appendPlainText(said)

    def _stdout(self, data):
        """kicad2ems's own output: the log already comes as events, so only
        errors - a traceback whole - and the launcher's word that it could not
        start."""
        for line in data.splitlines():
            self._traceback = self._traceback or line.startswith('Traceback')
            if (self._traceback or 'error' in line.lower()
                    or line.startswith('the run could not start')):
                self.runtab.log.appendPlainText(line)

    def poll(self):
        tail = self.runner.tail
        if not tail:
            return
        for ev in tail.read():
            k = ev.get('kind')
            if k == 'log':
                self.runtab.log.appendPlainText(ev.get('text', ''))
            elif k == 'stage':
                self.runtab.stage.setText(f"stage: {ev.get('name')}")
            elif k == 'status':
                self.runtab.status(ev)
            elif k == 'file':
                self.files.append((ev.get('label', ''), ev.get('path', '')))
            elif k == 'done':
                self.runtab.stage.setText('done' if ev.get('code') in (
                    0, None) else f"ended with code {ev.get('code')}")
        self.ticks += 1
        # field frames, ~1 s
        if self.current and not self.current.preview and self.ticks % 3 == 0:
            try:
                self.viewer.follow(self.current.out)
            except Exception as e:  # never stop following the run over a frame
                self.runtab.log.appendPlainText(f"field view: {e}")

    def _finished(self, code, how):
        """kicad2ems's own run is over (how: its outcome, which its mark
        keeps, so a run with no results stays listed, with its log): its
        results into the pages. A run: Results in front (S-parameters,
        antenna); a preview: the Viewer (the board, its meshes, the line
        model), and the estimates on the solver tick boxes. The queue's next:
        the launcher's finished (_job_done)."""
        from PySide6.QtCore import QTimer
        self.poll()
        self.timer.stop()
        crashed = how == 'crashed'
        if crashed:
            self.runtab.log.appendPlainText(
                "the run's process ended abnormally (crashed): its whole "
                f"output is in {os.path.join(self.current.out, 'stdout.log')}")
        self.runtab.stop_btn.setEnabled(False)
        self.acts['Stop'].setEnabled(False)
        out = self.current.out
        board = self.setup.values().get('board')
        if os.path.normcase(os.path.abspath(str(
                self.current.values.get('board') or ''))) != os.path.normcase(
                    os.path.abspath(str(board or ''))):
            # a run of a board no longer open (another config opened while it
            # went): listed with its board's runs, not shown over this one's
            # (a preview of rf_coupler's had drawn over demo_lowpass)
            self.refresh_runs()
            self.steps.refresh()
            QTimer.singleShot(0, self._auto_preview)
            return
        auto, self._auto = getattr(self, '_auto', False), False
        if (auto and self.shown and self.shown != os.path.abspath(out)
                and self.runs_panel.info(self.shown) is not None):
            # the preview a config gets: a run of it is shown, and stays
            self.refresh_runs()
            if self.system.load_estimates(out):
                self.new_estimates(self.system.est)
            return
        has_s = any(l.startswith('S-parameters') for l, _ in self.files)
        ant = self.results.load_antenna(None if self.current.preview else out)
        if has_s or not self.current.preview:
            # a preview: the solvers it runs (the line model)
            self.results.show_files(self.files)
        seen = self.viewer.load(out)
        self.shown = os.path.abspath(out)
        self.refresh_runs(select=out)
        self.acts['Report'].setEnabled(bool(self.results.report))
        if self.current.preview and self.system.load_estimates(out):
            self.new_estimates(self.system.est)
            self.statusBar().showMessage(
                "preview done: estimates beside the solvers in the "
                "S-parameters row's menu (? under Solvers for details)", 8000)
        if code or crashed:
            self.runtab.stage.setText('crashed - see the log' if crashed else
                                      f"ended with code {code} - see the log")
            self.show_panel('Run')
        elif not self.current.preview and (has_s or ant):
            self.show_result()
        else:
            self.show_panel('Viewer' if seen else 'Run')
        self.steps.refresh()  # (it had kept 'running' after a preview)
