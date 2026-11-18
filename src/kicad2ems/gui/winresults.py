"""
The main window's results (app.py): this board's runs (runfolders.index, on the
Runs page: runspanel.py), the one shown in the pages and those laid over it,
the toolbar's Showing; runs deleted.
"""
import os
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from .runner import find_results
from .study import RF
from . import runfolders as rf


class WindowResults:
    """MainWindow's results: this board's runs and the one shown."""
    shown = None  # the folder the pages show


    # ---- results into the pages ----
    def open_results(self, out_dir, quiet=False, front=True):
        """The results already in a folder, whatever analysis wrote them: the
        viewer (board, meshes, line model, dumps, far field) and the results
        page (S-parameters and antenna, or an analysis's report and curves);
        front: the page with something new in front."""
        from kicad2ems.core.analyses import analysis_of
        self.shown = os.path.abspath(out_dir)
        files = find_results(out_dir)
        if not files:  # a --vary run's folder: its first run shown,
            try:       # the others under Compare with (R-E5)
                from kicad2ems.core.vary import load
                v = load(out_dir)
            # (an older kicad2ems)
            except Exception:  # noqa: BLE001
                v = None
            first = next((r['folder']
                          for r in (v or {}).get('runs', [])
                          if r.get('code') == 0), None)
            if first:
                out_dir, files = first, find_results(first)
        # first: it decides the page's plots
        ant = self.results.load_antenna(out_dir)
        # an analysis's result: the page is its
        shown = self.results.load_analysis(out_dir)
        if files:
            self.results.show_files(files)
        seen = self.viewer.load(out_dir)
        made = analysis_of(out_dir)
        if made is not None and made[0] in self.pages:
            self._show_tool_result(made[0], out_dir, preview=made[1])
        if self.system.load_estimates(out_dir):
            self.new_estimates(self.system.est)
        self.acts['Report'].setEnabled(bool(self.results.report))
        self.runs_panel.select([self.shown])
        self.update_showing()
        # something to show: the pages that stepped aside for the Start page
        # are back
        if files or ant or shown or seen:
            self._start_leaves(hide=False)
        # its plots, or what an analysis found
        if front and (files or ant or shown):
            self.show_result()
        if front and seen and not (files or ant):  # and the board with its maps
            self.show_panel('Viewer')
        if not quiet and not (files or seen or ant or shown):
            QMessageBox.information(self, 'Results', f"No results in {out_dir}")

    def _show_tool_result(self, kind, out_dir, preview=False):
        """An analysis's folder is the one shown: its panel (or page) takes it
        as its result, and the viewer starts where its report says (the dump of
        a field with the largest values: the layer that drops most, the hottest;
        a mesh in the plan)."""
        page = self.pages[kind]
        if kind in self.editors:
            if (not preview and os.path.abspath(str(page.out or ''))
                    != os.path.abspath(out_dir)):
                # not its own run just finished: shown there too
                page.open_run(out_dir)
        else:
            if not preview:
                page.out = out_dir
            page.show_result(out_dir)
        rep = self.results.analysis_report or {}
        show, mesh = rep.get('show') or {}, rep.get('mesh')
        if show.get('field'):
            srcs = [
                s for s in self.viewer.cuts.srcs
                if s.field == show['field'] and show.get('run') in (None, s.run)
            ]

            def top(src):
                try:
                    return float(np.nanmax(np.abs(src.load(0)[1])))
                except Exception:  # a dump half written
                    return -1.0
            if srcs:
                self.viewer.show_dump(max(srcs, key=top).label)
        if mesh and self.viewer.plan.mesh.findData(mesh) >= 0:
            self.viewer.plan.mesh.setCurrentIndex(
                self.viewer.plan.mesh.findData(mesh))

    def latest_results(self):
        """{analysis: the folder of its latest result on this board}: the one
        its panel shows (its last run here, or the one picked in Runs), else
        this config's newest, else the board's. An analysis that starts from
        another's result (the temperature, from a circuit's losses) is given
        these."""
        out, runs = {}, self.run_index()
        for r in rf.in_scope(runs, 'config', self.config_path()) + runs:
            if r.ok and r.kind in self.pages:
                out.setdefault(r.kind, r.folder)
        for key, page in self.pages.items():
            d = getattr(page, 'out', None)
            if d and rf.tool_kind(str(d)) == key:
                out[key] = str(d)
        return out

    # ---- this board's runs: one index (runfolders.py) ----
    def config_path(self):
        """The config open (None: the form not saved as one)."""
        return getattr(self.setup, 'path', None)

    def running_folders(self):
        """The folders of the runs going now: kicad2ems's own, the
        analyses'."""
        out = ([self.current.out]
               if self.proc is not None and self.current is not None else [])
        return out + [
            str(p.out) for p in self.pages.values()
            if getattr(p, 'proc', None) is not None and getattr(p, 'out', None)
        ]

    def run_index(self, scope='board'):
        """runfolders.index of this board's runs ('all': of every board under
        the output folder)."""
        try:
            vals = self.setup.values()
        except ValueError:  # a field half typed
            return []
        board = vals.get('board')
        if scope != 'all' and not board:
            return []
        return rf.index(self.out_dir(vals), None if scope == 'all' else board,
                        self.running_folders())

    def settings_now(self, info):
        """The settings a run like info's would be started with now (its ≠
        settings are against these); None: not known (an analysis's run of a
        row no longer in its table)."""
        if info.kind in ('rf', 'preview'):
            try:
                return self.run_values(info.preview)
            except ValueError:
                return None
        page = self.pages.get(info.kind)
        page = getattr(page, 'panel', page)  # (an editor's settings)
        rows = page.runs() if hasattr(page, 'runs') else []
        if len(rows) == 1:
            return rows[0]
        tag = lambda v: (''.join(c if c.isalnum() else '_' for c in str(
            page.a.tag(v) or '')).strip('_') or 'run')
        return next((v for v in rows if tag(v) == info.what), None)

    def config_runs(self):
        """This config's runs (not the preview), as the Runs page last read
        them, the newest first."""
        return [r for r in rf.in_scope(self.runs_panel.board_runs(), 'config',
                                       self.config_path()) if not r.preview]

    def shown_info(self):
        return self.runs_panel.info(self.shown) if self.shown else None

    def update_showing(self):
        """The toolbar's Showing, the steps bar and the study's list: the run
        shown, and when each question was last answered."""
        from .runspanel import showing
        mine = self.config_runs()
        info = self.shown_info()
        self.showing_btn.setText(showing(info, self.shown, bool(mine)))
        self.showing_btn.setToolTip(
            (f"the pages show {info.folder if info else self.shown}"
             if self.shown else "nothing of this board's shown yet")
            + "\n(click: the Runs page, to pick another)")
        answered = {}
        for r in mine:
            if r.ok:
                for q in r.questions:
                    answered.setdefault(q, (r.label(), r.folder))
        self.study.set_answered(answered)
        self.steps.refresh()

    def board_loaded(self):
        """A config was opened: its newest run that finished into the pages,
        else nothing (the preview, while it is current), and a preview to show
        the board as it is now (the Viewer then shows it, when nothing else
        is)."""
        self.shown = None
        rs = self.refresh_runs()
        done = [r for r in rs if r.ok and not r.preview]
        if done:
            self.open_results(done[0].folder, quiet=True)
        else:  # nothing of the last board left in the pages
            self.results.load_antenna(None)
            self.results.show_files([])
            pv = next((r for r in rs if r.preview), None)
            now = self.settings_now(pv) if pv else None
            if pv and now is not None and not rf.differ(pv.settings, now):
                self.viewer.load(pv.folder)
                self.shown = pv.folder
            else:
                self.viewer.load(os.path.join(self.out_dir(), '-'))
            self.runs_panel.select([self.shown] if self.shown else [])
            self.update_showing()
        self.want_layout()  # the board as drawn, where nothing of it is shown
        # the last board's preview, still going: ended (its finish leaves the
        # pages to this board, and starts this one's)
        if self.proc is not None and self.current and self.current.preview:
            self._stopping = True
            self.proc.kill()
        QTimer.singleShot(0, self._auto_preview)

    def _auto_preview(self):
        """The preview a newly opened config gets: only in a window on the
        screen (not a test's), when idle, and only of a study that runs
        S-parameters (the preview is that run's: a circuit's board has no ports
        for it)."""
        board = self.setup.values().get('board')
        if (self.isVisible() and board and os.path.exists(board)
                and not self.busy() and RF in self.study.ticked()):
            self._auto = True  # (its end leaves a run shown as it is)
            if self.start(True) is not None:  # kicad2ems's own: the board
                self._auto = False

    def refresh_runs(self, select=None):
        """The Runs page read again; select: the folder to select there.
        Returns its runs (the preview last)."""
        rs = self.runs_panel.refresh(select)
        self.update_showing()
        return rs

    def show_run(self, folder):
        """A run picked: one of kicad2ems's own brings its page forward, as it
        did; another analysis's results go into the pages as they stand (the
        one in front is usually the one being compared). One that left no
        results: its log."""
        from .runfolders import tool_kind, ending, run_log
        if folder and os.path.isdir(folder):
            # stopped, failed: its log
            if (tool_kind(folder) is None and not find_results(folder)
                    and not os.path.isdir(os.path.join(folder, 'preview'))
                    and not any(
                        f.endswith('_run.json') for f in os.listdir(folder))):
                self.runtab.started('rf')
                self.runtab.log.setPlainText(run_log(folder) or '(no log)')
                self.runtab.stage.setText(f"this run: {ending(folder)}")
                self.show_panel('Run')
                return
            self.open_results(folder, quiet=True, front=tool_kind(folder)
                              is None)

    def compare_runs(self, folders, viewer=False):
        """These runs laid over the one shown (and no others): their curves in
        Plots, the last in the second Viewer when it has fields or maps (or is
        open, or viewer). False: one could not be (another kind of result, or
        none)."""
        from kicad2ems.core.fields import frames
        r = self.results
        want = [os.path.abspath(f) for f in folders]
        for d in r.compared():
            if d not in want and os.path.isdir(d):
                r.remove_folder(d)
        bad = []
        for d in want:
            info = self.runs_panel.info(d)
            if not r.add_folder(d, info.short() if info else None):
                bad.append(os.path.basename(d))
        if bad:
            self.statusBar().showMessage(
                f"not laid over the run shown: {', '.join(bad)} (another kind "
                "of result, or none)", 8000)
        v2 = getattr(self, '_viewer2', None)
        if want and (viewer or (v2 is not None and v2.isVisible())
                     or frames.find_dumps(want[-1])):
            self.second_viewer(want[-1])
        return not bad

    def delete_runs(self, folders):
        """Runs listed on the Runs page, their folders deleted once asked (all
        of them at once, their sizes said); not one going. Returns how many
        were."""
        import shutil
        going = {os.path.abspath(d) for d in self.running_folders()}
        listed = {r.folder: r for r in self.runs_panel.runs()}
        pick = [listed[os.path.abspath(d)] for d in folders
                if os.path.abspath(d) in listed]
        busy = [r for r in pick if r.folder in going]
        pick = [r for r in pick if r.folder not in going]
        if busy and not pick:
            QMessageBox.information(self, 'Delete runs',
                                    'That run is going: stop it first')
            return 0
        if not pick:
            return 0
        lines = '\n'.join(f"{os.path.basename(r.folder)}  "
                          f"({rf.size_text(r.size)})" for r in pick[:12])
        more = len(pick) - 12
        total = rf.size_text(sum(r.size for r in pick))
        ask = QMessageBox.question(
            self, 'Delete runs',
            f"Delete {len(pick)} run{'s' if len(pick) > 1 else ''} "
            f"({total}) and everything in "
            f"{'them' if len(pick) > 1 else 'it'}?\n\n{lines}"
            + (f"\n… and {more} more" if more > 0 else '')
            + ('\n\n(the one going is kept)' if busy else ''),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ask != QMessageBox.Yes:
            return 0
        for r in pick:
            shutil.rmtree(r.folder, ignore_errors=True)
            if os.path.abspath(str(getattr(self.viewer, 'out', '')
                                   or '')) == r.folder:
                self.viewer.out = None
            if self.shown == r.folder:
                self.shown = None
        self.refresh_runs()
        self.statusBar().showMessage(
            f"deleted {len(pick)} run{'s' if len(pick) > 1 else ''} ({total})",
            6000)
        return len(pick)
