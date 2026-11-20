"""The Plots page: S-parameters and antenna results as Cartesian, Smith and
polar plots you add and remove, and an analysis's curves (ResultsTab). Moved out
of app.py (R-F2)."""
import os
import re
from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QComboBox, QPushButton,
                               QLabel, QFileDialog, QMessageBox, QToolButton,
                               QStackedWidget, QMenu)
from kicad2ems.core.runs import find_results
from .splot import SPlot, default_layout
from .curveplot import CurvePlot
from .curves import load_sets
from .reportview import ReportView
from .panels import FlowLayout
from .runtab import run_notes

TOUCHSTONE = re.compile(r'\.s\d+p$', re.I)

class ResultsTab(QWidget):
    """The Results page: the S-parameters of every solver and, for an antenna,
    its gain, efficiency and pattern cuts, as subplots you add and remove
    (splot.SPlot). The plots of a page are remembered: one layout for
    S-parameters, one for antennas. An analysis's result (a rail's drop and
    impedance, a near field, a circuit, a board's temperature: whatever
    kicad2ems's registry holds) takes the page instead with its curves
    (curveplot.CurvePlot; a result with several sets: one at a time), and has
    two more pages, which this one fills: Report, what it found, to read
    (`reportview`), and Tables, the report's tables to sort, copy and export
    (`tablesview`; reportview.ReportView both). Compare with lays a run's own
    variations, or another folder, over the plots (this board's other runs:
    the Runs page)."""
    MODES = ('Plots', 'Report', 'Tables')

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.info = QLabel('no results yet', wordWrap=True,
                           textInteractionFlags=Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.notes_btn = QToolButton(
            checkable=True,
            toolButtonStyle=Qt.ToolButtonTextBesideIcon,
            autoRaise=True,
            # the run's notes and warnings, folded
            toggled=self._fold_notes)
        self.notes = QLabel(wordWrap=True,
                            textInteractionFlags=Qt.TextSelectableByMouse,
                            textFormat=Qt.RichText)
        lay.addWidget(self.notes_btn)
        lay.addWidget(self.notes)
        self.set_notes([], [])
        top = FlowLayout()
        self.which = QComboBox(
            visible=False,
            toolTip="The antenna results to show (a folder can hold openEMS's "
            "and mom25d's)")
        self.which.currentIndexChanged.connect(lambda i: self.load_antenna(
            self.out, self.which.itemData(i)) if i >= 0 and self.out else None)
        top.addWidget(self.which)
        self.cmp_btn = QToolButton(
            text='Compare with', popupMode=QToolButton.InstantPopup, toolTip=
            ("Lay this run's variations or another folder over the one shown "
             "(this board's other runs: Ctrl and a click on the Runs page)"))
        self.cmp_menu = QMenu(self.cmp_btn, aboutToShow=self._fill_compare)
        self.cmp_btn.setMenu(self.cmp_menu)
        top.addWidget(self.cmp_btn)
        self.export_btn = QToolButton(
            text='Export Touchstone', popupMode=QToolButton.InstantPopup,
            toolTip=
            ("Save one of this run's S-parameter files (each solver's, the "
             "de-embedded and part-port ones) as a Touchstone file where you "
             "choose"))
        self.export_menu = QMenu(self.export_btn, aboutToShow=self._fill_export)
        self.export_btn.setMenu(self.export_menu)
        top.addWidget(self.export_btn)
        self.folder_btn = QPushButton(
            'Open folder', clicked=lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(self.out if self.analysis and self.out
                                   else self.win.out_dir())))
        top.addWidget(self.folder_btn)
        # several sets: which
        self.set_box = QComboBox(
            visible=False, minimumWidth=220,
            currentIndexChanged=self._pick_set,
            toolTip=("The result's curves to plot (a circuit: each point's "
                     "voltages and currents)"))
        top.addWidget(self.set_box)
        lay.addLayout(top)
        from .steering import SteeringBar
        self.steer = SteeringBar()
        lay.addWidget(self.steer)
        self.steers = []
        self.link_steering(self.steer)
        self.splot = SPlot()
        self.splot.layout_changed.connect(self._save_layout)
        self.curveplot = CurvePlot()
        # the Report and Tables pages
        self.reportview, self.tablesview = ReportView(), ReportView()
        self.reportview.set_mode('Report')
        self.tablesview.set_mode('Tables')
        # the S-parameter plots, or an analysis's
        self.views = QStackedWidget()
        self.views.addWidget(self.splot)                            # curves
        self.views.addWidget(self.curveplot)
        lay.addWidget(self.views, 1)
        self.report, self.added, self._files, self.out, self._mode = (None, [],
                                                                      [], None,
                                                                      None)
        # the analysis whose result is shown, its report
        self.analysis, self.analysis_report = None, None
        # its curve sets, the one plotted, other runs'
        self.curve_sets, self.curves, self.added_sets = [], None, []
        self.mode = None  # the view of an analysis's result
        self._restore_layout('sparams')

    # ---- the plots a page has: remembered per kind of result ----
    def _key(self, mode):
        return f"results/layout/{mode}"

    def _restore_layout(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        spec = (self.win.qsettings.value(self._key(mode)) if hasattr(
            self.win, 'qsettings') else None)
        spec = [spec] if isinstance(spec, str) else list(spec or [])
        self.splot.set_layout(spec or default_layout(mode == 'antenna'))

    def _save_layout(self):
        if hasattr(self.win, 'qsettings') and self._mode:
            self.win.qsettings.setValue(self._key(self._mode),
                                        self.splot.layout_spec())

    # ---- Touchstone export ----
    def touchstones(self):
        """[(label, path)] of the shown run's Touchstone files, as the run lists
        them."""
        return [(l, p)
                for l, p in self._files
                if TOUCHSTONE.search(p) and os.path.exists(p)]

    def _fill_export(self):
        self.export_menu.clear()
        files = self.touchstones()
        if not files:
            a = self.export_menu.addAction('no S-parameter files in this run')
            a.setEnabled(False)
            return
        for label, path in files:
            a = self.export_menu.addAction(f"{label}: {os.path.basename(path)}")
            a.triggered.connect(
                lambda _=False, p=path: self.export_touchstone(p))

    def export_touchstone(self, src, dst=None):
        """A copy of the Touchstone file src at dst (asked for without one; its
        extension kept: .s2p ...). Returns the path written, or None."""
        import shutil
        ext = TOUCHSTONE.search(src).group(0)
        if not dst:
            start = os.path.join(os.path.expanduser('~'), os.path.basename(src))
            dst, _ = QFileDialog.getSaveFileName(
                self, 'Export the S-parameters', start,
                f"Touchstone (*{ext});;All files (*)")
            if not dst:
                return None
        if not TOUCHSTONE.search(dst):
            dst += ext
        if os.path.abspath(dst) != os.path.abspath(src):
            shutil.copyfile(src, dst)
        return dst

    # ---- data ----
    def open_report(self):
        if self.report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report))

    def _add(self):
        d = QFileDialog.getExistingDirectory(
            self, 'Another results folder to overlay', self.win.out_dir())
        if d and not self.add_folder(d):
            what = ('S-parameters' if self.curves is None
                    else self.curves.title.lower())
            QMessageBox.information(self, 'Results', f"No {what} in {d}")

    def kind(self):
        """What the page shows: 'rf' (S-parameters, antenna) or the analysis
        whose result it is ('pdn', 'thermal')."""
        return self.analysis or 'rf'

    def compared(self):
        """The folders laid over the run shown (of its kind)."""
        if self.analysis is not None:
            return [
                sets[0].folder
                for sets in self.added_sets
                if sets and sets[0].kind == self.analysis
            ]
        return [d for _label, _files, d in self.added]

    def add_folder(self, d, label=None):
        """Another run over the one shown: its S-parameters (under label, else
        its folder's name), or its curves when the page shows an analysis's
        (the same kind only). False: nothing of that kind in the folder."""
        d = os.path.abspath(d)
        if d in self.compared():
            return True
        if self.analysis is not None:
            sets = load_sets(d)
            if not sets or sets[0].kind != self.analysis:
                return False
            self.added_sets.append(sets)
            self._show_analysis()
            return True
        files = find_results(d)
        if not files:
            return False
        self.added.append((label or os.path.basename(os.path.normpath(d))
                           or d, files, d))
        self.show_files(self._files)
        return True

    def sweeps(self):
        """The shown run's variations to lay over it: [(label, 'folder' |
        'file', path)]: each run of its --vary (<name>_vary.json: a folder each)
        and each part value combination of its solvers (--part-value: a
        file)."""
        out, here = [], getattr(self, '_run_folder', None)
        if not here:
            return out
        try:
            from kicad2ems.core.vary import load
            # one of a variation's runs: <out>/vary/<tag>
            up = os.path.dirname(os.path.dirname(here))
            v = (load(here) or (load(up) if os.path.basename(
                os.path.dirname(here)) == 'vary' else None))
        # (an older kicad2ems, a damaged list)
        except Exception:  # noqa: BLE001
            v = None
        for r in (v or {}).get('runs', []):
            if r.get('code') == 0 and os.path.isdir(r['folder']) and \
                    os.path.abspath(r['folder']) != os.path.abspath(here):
                out.append(
                    (', '.join(f"{k} = {x}" for k, x in r['values'].items()),
                     'folder', r['folder']))
        for solver, e in ((getattr(self, '_summary', None) or {}).get('solved')
                          or {}).items():
            for c in ((((e or {}).get('part_ports') or {}).get('sweep') or [])
                      if isinstance(e, dict) else []):
                p = os.path.join(here, c['touchstone'])
                if os.path.exists(p):
                    out.append((f"{solver}: "
                                + ', '.join(f"{k} = {x}"
                                            for k, x in c['values'].items()),
                                'file', p))
        return out

    def _add_sweep(self, kind, path, label):
        if kind == 'folder':
            return self.add_folder(path)
        # one Touchstone file (a part value's combination)
        path = os.path.abspath(path)
        if path in self.compared() or not os.path.exists(path):
            return False
        self.added.append((label, [(label, path)], path))
        self.show_files(self._files)
        return True

    def remove_folder(self, d=None):
        """One compared run off the page (None: all of the kind shown)."""
        if self.analysis is not None:
            self.added_sets = [
                sets for sets in self.added_sets
                if sets[0].kind != self.analysis or (d and sets[0].folder != d)
            ]
            self._show_analysis()
        else:
            self.added = [a for a in self.added if d and a[2] != d]
            self.show_files(self._files)

    def _fill_compare(self):
        """The Compare with menu: the runs laid over (this board's are picked
        on the Runs page: Ctrl and a click), this run's own variations,
        another folder, none."""
        m = self.cmp_menu
        m.clear()
        on = self.compared()
        sw = self.sweeps() if self.analysis is None else []
        varied = {os.path.abspath(p) for _l, _k, p in sw}

        def ticked(label, checked, toggled):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(toggled)
        for d in (d for d in on if d not in varied):
            ticked(os.path.basename(d), True,
                   lambda tick, d=d: (None if tick
                                      else self.remove_folder(d)))
        a = m.addAction("This board's other runs: Ctrl and a click on the "
                        "Runs page")
        a.triggered.connect(lambda: hasattr(self.win, 'show_panel')
                            and self.win.show_panel('Runs'))
        if sw:  # this run's own variations (R-E5)
            m.addSection('This run varied')
            for label, kind, path in sw:
                ticked(
                    label,
                    os.path.abspath(path) in on,
                    lambda tick, k=kind, p=path, l=label: (
                        self._add_sweep(k, p, l) if tick
                        else self.remove_folder(os.path.abspath(p))))
            a = m.addAction('All of them')
            a.triggered.connect(
                lambda: [self._add_sweep(k, p, l) for l, k, p in self.sweeps()])
        m.addSeparator()
        m.addAction('Another folder…').triggered.connect(self._add)
        a = m.addAction('None')
        a.setEnabled(bool(on))
        a.triggered.connect(lambda: self.remove_folder(None))

    def load_analysis(self, out_dir):
        """A folder's result of an analysis (whichever kicad2ems's registry says
        it is of) onto the page in place of the S-parameter plots: its report
        and its curves. False: it holds none, and the page shows
        S-parameters."""
        from kicad2ems.core import analyses
        from kicad2ems.core.analyses import analysis_of
        made = analysis_of(out_dir) if out_dir else None
        rep, sets = None, []
        if made is not None and made[0] in analyses.registry():
            folder = os.path.abspath(out_dir)
            try:
                rep = analyses.registry()[made[0]].report(folder)
            except Exception:  # an analysis's fault, a damaged file: no report
                rep = None
            if rep:
                rep = self._with_engines(made[0], folder, rep)
            sets = load_sets(folder)
        self.analysis = made[0] if made is not None and (rep or sets) else None
        self.analysis_report, self.curve_sets = ((rep,
                                                  sets) if self.analysis else
                                                 (None, []))
        if self.analysis:
            self.out = os.path.abspath(out_dir)
        keep = self.set_box.currentText()
        with QSignalBlocker(self.set_box):
            self.set_box.clear()
            self.set_box.addItems([cs.title for cs in self.curve_sets])
            self.set_box.setCurrentIndex(
                max(self.set_box.findText(keep), 0) if self.curve_sets else -1)
        self._show_analysis()
        # (an analysis's tables export as CSV)
        self.export_btn.setVisible(self.analysis is None)
        return self.analysis is not None

    def _with_engines(self, name, folder, report):
        """A result's report with the other engines' answers to the same
        question beside it: a section 'Engines compared' whose table has the
        figures they name (kicad2ems.core.study.compare: this
        result and each other engine's newest on this board) against the
        engines, and the difference where two give one. Nothing to compare: the
        report as it is."""
        from kicad2ems.core import study
        from kicad2ems.core.study import engines, questions
        try:
            es, qs = engines(), questions()
            latest = (dict(self.win.latest_results()) if hasattr(
                self.win, 'latest_results') else {})
            latest[name] = folder
            added = []
            for q in getattr(es.get(name), 'provides', ()):
                names = [
                    e.name for e in es.values() if e.kind == 'analysis'
                    and q in e.provides and e.name in latest
                ]
                if len(names) < 2:
                    continue
                c = study.compare({n: latest[n] for n in names})
                if not any(r['spread'] is not None for r in c['rows']):
                    continue  # no figure two of them give
                rows = [[
                    r['name'], r['unit'],
                    *['' if v is None else v for v in r['values']],
                    '' if r['spread'] is None else r['spread']
                ] for r in c['rows']]
                where = '; '.join(f"{lab}: {os.path.basename(latest[k])}"
                                  for lab, k in zip(c['labels'], c['engines']))
                added.append(dict(
                    title=f"Engines compared: {qs[q].label if q in qs else q}",
                    text=(f"Each engine's newest result on this board "
                          f"({where}). A figure both name is the same "
                          "quantity; one only an engine has is its own."),
                    table=dict(
                        columns=['figure', 'unit', *c['labels'], 'difference'],
                        rows=rows,
                        formats=[None, None, *['.4g'] * len(c['labels']),
                                 '.3g'])))
            if not added:
                return report
            sections = list(report.get('sections') or [])
            # after its opening words
            at = 1 if sections and not sections[0].get('title') else 0
            return dict(report, sections=sections[:at] + added + sections[at:])
        # (an analysis's figures, a damaged file)
        except Exception:  # noqa: BLE001
            return report

    def _pick_set(self, *_):
        if self.analysis is not None:
            self._show_analysis()

    def modes(self):
        """The views the result shown has: Plots (it has curves), Report, Tables
        (its report has tables)."""
        rep = self.analysis_report
        has = dict(
            Plots=bool(self.curve_sets), Report=bool(rep),
            Tables=(bool(rep) and any(
                sec.get('table') for sec in rep.get('sections', []))))
        return [m for m in self.MODES if has[m]] if self.analysis else []

    def set_mode(self, mode):
        """The page of an analysis's result in front: Plots, Report or Tables
        (one the result has; else its first). They are pages of their own, so
        this only brings one forward."""
        has = self.modes()
        if not has:
            return
        self.mode = mode if mode in has else has[0]
        if self.win is not None and hasattr(self.win, 'docks'):
            self.win.show_panel('Results' if self.mode ==
                                'Plots' else self.mode)

    def _show_analysis(self):
        """The result of an analysis on its pages (the curves here, the report
        and its tables on theirs); none: the S-parameter plots here, and nothing
        on those."""
        has = self.modes()
        if not has:
            self.curves = None
            self.set_box.hide()
            self.cmp_btn.setEnabled(True)
            self.views.setCurrentWidget(self.splot)
            for view in (self.reportview, self.tablesview):
                view.set_report(None)
            return
        i = self.set_box.currentIndex()
        self.curves = (self.curve_sets[i]
                       if 0 <= i < len(self.curve_sets) else None)
        if self.curves is not None:  # other runs: their set of the same title
            others = [
                next((o
                      for o in sets
                      if o.title == self.curves.title), None)
                for sets in self.added_sets
                if sets and sets[0].kind == self.analysis
                and sets[0].folder != self.curves.folder
            ]
            self.curveplot.set_sets(self.curves,
                                    [o for o in others if o is not None])
        else:
            others = []
            self.curveplot.set_sets(None)
        for view in (self.reportview, self.tablesview):
            view.set_report(self.analysis_report)
        self.views.setCurrentWidget(self.curveplot)
        self.set_box.setVisible(len(self.curve_sets) > 1)
        self.cmp_btn.setEnabled(bool(self.curve_sets))
        self.which.hide()
        self.steer.hide()
        self.report = None
        rep = self.analysis_report or {}
        self.set_notes([], [])  # the report has its own
        head = (rep.get('summary')
                or (self.curves.info if self.curves is not None else ''))
        if not self.curve_sets and rep:
            head += '  ·  no curves in this result: its Report page has it'
        self.info.setText(head + (
            f"  ·  with {', '.join(o.label for o in others if o is not None)}"
            if others else ''))
        self.mode = self.mode if self.mode in has else has[0]

    def _load(self):
        d = QFileDialog.getExistingDirectory(
            self, 'A results folder (the run\'s output folder)',
            self.win.out_dir())
        if d:
            self.win.open_results(d)

    def load_antenna(self, out_dir, path=None):
        """The antenna results of a folder (path: which; default the newest)
        into the plots; False: none."""
        from .antennatab import (find_antenna, antenna_files, load_sphere,
                                 load_array)
        self.out = out_dir
        files = antenna_files(out_dir) if out_dir else []
        with QSignalBlocker(self.which):
            self.which.clear()
            for f in files:
                try:
                    from kicad2ems.core.fields import antenna
                    solver = antenna.read(f).get('solver', 'openems')
                except (OSError, ValueError):
                    solver = os.path.basename(f)
                self.which.addItem(solver, f)
            if files:
                self.which.setCurrentIndex(
                    files.index(path) if path in files else len(files) - 1)
            self.which.setVisible(len(files) > 1)
        try:
            found = find_antenna(out_dir, path) if files else None
            sph = load_sphere(found[1]) if found and found[1] else None
        except Exception as e:  # a damaged file must not take the GUI down
            found, sph = None, None
            self.info.setText(f"could not read the antenna results: {e}")
        res = found[0] if found else None
        try:
            arr = load_array(out_dir, res) if res else None
        except Exception:  # no steering then
            arr = None
        # first: the plots' antenna options exist then
        self.splot.set_antenna(res, sph)
        self._restore_layout('antenna' if res else 'sparams')
        for b in self.steers:
            b.set_data(res, arr)
        if res:
            m = res['match']
            bw = (
                ", −10 dB band "
                f"{m['bw10_lo'] / 1e9:.3f}–{m['bw10_hi'] / 1e9:.3f} GHz"
                if m.get('bw10') else ', S11 never below −10 dB')
            self.info.setText(
                f"{res.get('solver', 'openems')} antenna, "
                f"{res.get('port', '')}: resonance {m['f_res'] / 1e9:.4f} GHz, "
                f"S11 {m['s11_db']:.1f} dB, Zin {m['zin'][0]:.1f} "
                f"{'+' if m['zin'][1] >= 0 else '−'} j{abs(m['zin'][1]):.1f} "
                f"Ω{bw}"
            )
        return bool(res)

    def link_steering(self, bar):
        """A Beam steering bar (this page's, the 3D view's): every bar shows the
        same drive, and steering any redraws the antenna plots and the 3D
        pattern."""
        self.steers.append(bar)
        bar.steered.connect(
            lambda res, sphere, src=bar: self._steered(res, sphere, src))
        bar.anim.toggled.connect(
            lambda on, src=bar:
            [b.follow_animation(on) for b in self.steers if b is not src])

    def _steered(self, res, sphere, src=None):
        """A steered drive (a Beam steering bar): the antenna plots, the 3D
        view's pattern, the other bars."""
        self.splot.update_antenna(res, sphere)
        vol = getattr(self.win, 'volume', None)
        if vol is not None and hasattr(vol, 'set_far'):
            vol.set_far(res, sphere)
        for b in self.steers:
            if b is not src:
                b.mirror(src)

    def set_notes(self, warns, notes):
        """The run's warnings (red) and notes under a fold, open when there is a
        warning; hidden when none."""
        import html
        self.notes_btn.setVisible(bool(warns or notes))
        parts = [
            f"{len(x)} {w}{'s' if len(x) > 1 else ''}" for x, w in ((warns,
                                                                     'warning'),
                                                                    (notes,
                                                                     'note'))
            if x
        ]
        self.notes_btn.setText(
            f"Notes and warnings ({', '.join(parts)})" if parts else '')
        items = (
            [f"<li style='color:#c62828'>{html.escape(w)}</li>" for w in warns]
            + [f"<li>{html.escape(n)}</li>" for n in notes])
        self.notes.setText(
            f"<ul style='margin:0'>{''.join(items)}</ul>" if items else '')
        with QSignalBlocker(self.notes_btn):
            self.notes_btn.setChecked(bool(warns))
        self._fold_notes(bool(warns))

    def _fold_notes(self, on):
        self.notes_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.notes.setVisible(bool(on) and self.notes_btn.isVisibleTo(self))

    def show_files(self, files, compare=None):
        """The S-parameters of a run's files (openEMS, mom25d, line model) and a
        compare file."""
        from .traces import load_traces
        self._files = list(files)
        if self.analysis is not None:  # an analysis's result has the page
            return
        self.report = next((p for l, p in files if p.endswith('_report.html')),
                           None)
        for view in (self.reportview, self.tablesview):
            view.set_report(None, self.report)
        z0 = 50.0
        summary = next(
            (p for l, p in files if p.endswith(('_run.json', '_openems.json'))),
            None)
        self.set_notes([], [])
        self._summary, self._run_folder = (
            None, os.path.dirname(summary) if summary else None)
        if summary and os.path.exists(summary):
            try:
                from kicad2ems.core.runs import read_summary
                sm = self._summary = read_summary(summary)
                self.set_notes(*run_notes(sm))
                compare = compare or (sm.get('settings') or {}).get('compare')
                zr = sm.get('zref')
                z0 = float(zr[0] if isinstance(zr, list) else zr or 50.0)
            except (ValueError, TypeError, OSError):
                pass
        if compare is None:
            compare = self.win.setup.values().get('compare')
        try:
            traces = load_traces(files, compare)
            # other runs: their solvers under the folder's name
            for label, fs, _d in self.added:
                for tr in load_traces(fs):
                    tr.source = f"{label}: {tr.source}"
                    traces.append(tr)
        except Exception as e:  # a damaged file must not take the GUI down
            self.info.setText(f"could not read the S-parameters: {e}")
            return
        self.splot.set_traces(traces, z0)
        shown = sorted({os.path.basename(t.path) for t in traces})
        if self.splot.antenna is None:
            self.info.setText(
                ', '.join(shown) if shown else 'no S-parameters in this run')
