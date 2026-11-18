"""
The main window's results (app.py): this board's runs, a folder each, in the
toolbar's Results of; the one picked into the pages, or deleted.
"""
import os
import numpy as np
from PySide6.QtCore import QTimer, QSignalBlocker
from PySide6.QtWidgets import QMessageBox
from .runner import find_results
from .study import RF


class WindowResults:
    """MainWindow's results: this board's runs and the one shown."""

    # ---- results into the pages ----
    def open_results(self, out_dir, quiet=False, front=True):
        """The results already in a folder, whatever analysis wrote them: the
        viewer (board, meshes, line model, dumps, far field) and the results
        page (S-parameters and antenna, or an analysis's report and curves);
        front: the page with something new in front."""
        from kicad2ems.core.api.frontend import analysis_of
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
        i = self.runs_box.findData(out_dir)
        if i >= 0:
            self.runs_box.setCurrentIndex(i)
        self.steps.refresh()
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
        its panel shows (its last run here, or the one picked under Results of),
        else the newest listed. An analysis that starts from another's result
        (the temperature, from a circuit's losses) is given these."""
        from .runfolders import tool_kind
        out = {}
        for src in self.sources():  # each kind's newest first
            if src['kind'] in self.pages:
                out.setdefault(src['kind'], src['folder'])
        for key, page in self.pages.items():
            d = getattr(page, 'out', None)
            if d and tool_kind(str(d)) == key:
                out[key] = str(d)
        return out

    # ---- this board's runs: a folder each (runfolders.py) ----
    def board_loaded(self):
        """A config was opened: its newest run into the pages, and a preview to
        show the board as it is now (the Viewer then shows the preview; the
        toolbar's Results of picks a run instead)."""
        from .runfolders import preview_dir
        rs = self.refresh_runs()
        board = self.setup.values().get('board')
        if rs:
            self.open_results(rs[0][0], quiet=True)
        else:  # nothing of the last board left in the pages
            self.results.load_antenna(None)
            self.results.show_files([])
            self.viewer.load(
                preview_dir(self.out_dir(), board) if board
                else os.path.join(os.getcwd(), 'out', '-'))
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
            self.start(True)  # kicad2ems's own: the board

    # s: how long the runs read from disk stand while their folders are
    # unchanged
    SOURCES_S = 1.0

    def sources(self):
        """This board's results, as the Results of box lists them: [{folder,
        kind, label}], kind 'preview', 'rf' (a run of kicad2ems's own), 'pdn' or
        'mqs'; each kind's newest first. Read again once the output folder or
        the board's own changes, SOURCES_S on, or after refresh_runs: a settings
        change had read every run folder once per analysis asked and per row of
        its table (20 ms each at 200 runs)."""
        import time
        try:
            vals = self.setup.values()
        except ValueError:  # a field half typed
            return []
        board = vals.get('board')
        if not board:
            return []
        base = self.out_dir(vals)

        def stamp(d):
            try:
                return os.stat(d).st_mtime_ns
            except OSError:
                return None

        key = (os.path.abspath(base), os.path.abspath(board), stamp(base),
               stamp(os.path.dirname(os.path.abspath(board))))
        memo = getattr(self, '_sources_memo', None)
        if (memo and memo[0] == key
                and time.monotonic() - memo[1] < self.SOURCES_S):
            return [dict(s) for s in memo[2]]
        from .runfolders import runs, preview_dir, tool_runs
        pv = preview_dir(base, board)
        out = ([dict(folder=pv, kind='preview', label='preview')]
               if os.path.isdir(pv) else [])
        out += [
            dict(folder=d, kind='rf', label=f"run {label}")
            for d, label in runs(base, board)
        ]
        out += [
            dict(folder=d, kind=kind, label=label)
            for d, kind, label in tool_runs(base, board)
        ]
        self._sources_memo = (key, time.monotonic(), out)
        return [dict(s) for s in out]

    def refresh_runs(self, select=None):
        """The Results of choice: this board's preview and runs, then its Power
        integrity and Near field results, each kind's newest first; select: the
        folder to show as chosen. Returns the runs [(folder, label)]."""
        self._sources_memo = None  # (a run just finished: read them again)
        srcs = self.sources()
        keep = select or self.runs_box.currentData()
        last = None
        with QSignalBlocker(self.runs_box):
            self.runs_box.clear()
            for src in srcs:
                group = ('rf' if src['kind'] in ('preview',
                                                 'rf') else src['kind'])
                if last is not None and group != last:
                    self.runs_box.insertSeparator(self.runs_box.count())
                last = group
                self.runs_box.addItem(src['label'], src['folder'])
            i = self.runs_box.findData(keep) if keep else -1
            self.runs_box.setCurrentIndex(i if i >= 0 else 0)
        return [(src['folder'], src['label'][4:])
                for src in srcs
                if src['kind'] == 'rf']

    def _pick_run(self, folder):
        """Results of: a run of kicad2ems's own brings its page forward, as it
        did; another analysis's results go into the pages as they stand (the one
        in front is usually the one being compared)."""
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

    def delete_run(self, folder=None):
        """The run in Results of, its folder deleted once asked (R-E24: run
        folders hold hundreds of MB, and none could be deleted here); not the
        one running, nor anything but a run folder of this board."""
        import shutil
        folder = folder or self.runs_box.currentData()
        if not folder or not os.path.isdir(folder):
            return False
        if (self.current is not None and self.proc is not None and
                os.path.abspath(folder) == os.path.abspath(self.current.out)):
            QMessageBox.information(self, 'Delete run',
                                    'That run is going: stop it first')
            return False
        listed = [
            self.runs_box.itemData(i) for i in range(self.runs_box.count())
        ]
        if folder not in listed:
            return False
        size = sum(
            os.path.getsize(os.path.join(r, f))
            for r, _, fs in os.walk(folder)
            for f in fs)
        ask = QMessageBox.question(
            self, 'Delete run',
            f"Delete {os.path.basename(folder)} ({size / 1e6:.0f} MB) "
            f"and everything in it?", QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No)
        if ask != QMessageBox.Yes:
            return False
        shutil.rmtree(folder, ignore_errors=True)
        if getattr(self.viewer, 'out', None) == folder:
            self.viewer.out = None
        self.refresh_runs()
        self.statusBar().showMessage(f"deleted {folder}", 6000)
        return True
