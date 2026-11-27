"""
An analysis's settings and its run, for any analysis kicad2ems's registry holds
(kicad2ems.core.analyses.registry:
pcbqs's power integrity, near field and thermal): the form made from its
options, in sections by their groups; its commands (a run each: a row of its
table of runs) for the one launcher to start (runprocess.RunProcess: the
window's, or the panel's own when it is alone); their output followed (the Run
page shows the log, the status line its progress).

The panel is the analysis's part of the Study dock. It has no page: what a run
leaves is shown where every result is, its maps in the Viewer (it writes them as
field dumps), its report and curves on the Results page. Nothing here knows one
analysis from another; what an analysis does differently it says through its
Analysis (command, status, discover and choices, suggest).

    panel = AnalysisPanel(analysis, win)
        # win: the window (its board, its output folder), or None alone
    panel.values(), panel.set_values({...}), panel.reset()
    panel.commands(preview, asked, engine, sets)
        # its runs as they stand, for the launcher: each made when its turn
        # comes (its folder, its mark), or why it cannot start
    panel.began(cmd), panel.output(text), panel.done(cmd, code, outcome)
        # what the launcher says of them: the log, the status line
    panel.start(preview=False), panel.stop(), panel.proc, panel.out
        # alone: its Run and Stop (in the window: the toolbar's)
    config_values(panel)
        # what a config's [analysis.<name>] table holds of it
    panel.set_runs([{...}, ...]), panel.runs()
        # an analysis a study may hold several of (Analysis.several: a
        # board's rails): a table of what differs, a run each, in order
"""
import os
import re
from contextlib import contextmanager

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout,
                               QLineEdit, QPushButton, QPlainTextEdit,
                               QFileDialog, QProgressBar,
                               QLabel, QInputDialog, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView)

# a pick's key for the runs table's selected cell (pick_kind, take_pick)
CELL = '(the selected cell)'

from .optionrow import field, value, set_value
from .panels import Section, FlowLayout
from .runprocess import Command, RunProcess
from .toolprocess import run_tool

# a config table's keys that are the panel's own: its board, its folder
OWN = ('board', 'out')


def typed(option, value):
    """A form's value as a config holds it: a number where the option is one, a
    list for a repeatable one, None when it is not set (empty, or the option's
    default)."""
    kind = option.kind
    if kind == 'bool':
        return True if value else None
    if kind == 'list':
        return option.convert(value) or None
    text = '' if value is None else str(value).strip()
    if not text or text == str(option.default):
        return None
    if kind in ('float', 'int'):
        try:
            v = float(text)
            return int(v) if kind == 'int' and v == int(v) else v
        except ValueError:
            return text
    return text


def _cell(v):
    """A value as a runs table's cell: a list comma-separated, a float as %g,
    None empty."""
    if isinstance(v, (list, tuple)):
        return ', '.join(map(str, v))
    return '' if v is None else f"{v:g}" if isinstance(v, float) else str(v)


def config_values(panel, base=None, vals=None):
    """What a config's [analysis.<name>] table holds of a panel: the options set
    (of vals, a run of its table, when given), and its own board and folder;
    paths (the analysis's, and those two) relative to `base`."""
    out = {}
    from .runner import relative
    rel = lambda p: relative(os.path.abspath(p), base) if base else p
    for key, edit in (('board', panel.board), ('out', panel.outdir)):
        if edit.text().strip():
            out[key] = rel(edit.text().strip())
    vals = panel.values() if vals is None else vals
    for o in panel.options:
        v = typed(o, vals.get(o.key))
        if v is not None:
            out[o.key] = (rel(v) if o.key in panel.a.paths
                          and isinstance(v, str) else v)
    return out


class AnalysisPanel(QWidget):
    """One analysis's settings (the sections of its options, its own board and
    folder), the buttons its Analysis gives it (Find, what its result proposes
    for another analysis) and its status line."""
    # a setting was edited by hand (not set_values: a config loaded, a test)
    changed = Signal()
    ended = Signal(int)  # a run ended, all of its rows: 0, or the first failure

    def __init__(self, analysis, win=None):
        super().__init__()
        self.a, self.win = analysis, win
        self.out, self.result, self._found, self._setting = None, None, None, 0
        self._count, self._k, self._vals = 1, 1, {}
        # its runs' launcher: the window's, else its own (Run alone)
        self.runner = getattr(win, 'runner', None)
        if self.runner is None:
            self.runner = RunProcess(self)
            r = self.runner
            for sig, slot in ((r.began, self.began),
                              (r.output, lambda _c, t: self.output(t)),
                              (r.ended, self.done), (r.skipped, self.skipped),
                              (r.finished, self.ended.emit)):
                sig.connect(slot)
        self._finder = _Finder(self)
        # options a run needs set (proposed without a value)
        self.required = set()
        self.options = list(analysis.options())
        # an option's field; a group's section, and its form
        self.w, self.sections, self.forms = {}, {}, {}
        v = self._layout = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        files = QWidget()
        f = QFormLayout(files)
        f.setContentsMargins(0, 0, 0, 0)
        self.board = QLineEdit(
            placeholderText=("the study's board" if win is not None
                             else 'a .kicad_pcb, or a board.json'),
            toolTip=".kicad_pcb (or a run's <name>_board.json); empty: the "
                    "board under Board, above")
        b = QPushButton('…', clicked=self._browse)
        b.setFixedWidth(28)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.board, 1)
        rl.addWidget(b)
        f.addRow('Board', row)
        self.outdir = QLineEdit(
            placeholderText='a folder per run, in the output folder')
        f.addRow('Output folder', self.outdir)
        for edit in (self.board, self.outdir):
            edit.textChanged.connect(self._edited)
        self.sections['Files'] = Section('Files', files)
        v.addWidget(self.sections['Files'])
        self.pick_btns = {}  # {option key: its pick button}
        groups = {}
        # the few settings a run is set up with first and open (an option's
        # level 'basic', as the solvers' form has them), the rest in their
        # groups, closed; with none marked, the groups as they come
        basic = [o for o in self.options if getattr(o, 'level', '') == 'basic']
        if basic:
            groups['Settings'] = basic
        for o in self.options:
            if o in basic:
                continue
            g = o.group or 'Settings'
            groups.setdefault('More settings' if basic and g == 'Settings'
                              else g, []).append(o)
        for i, (g, opts) in enumerate(groups.items()):
            inner = QWidget()
            fl = QFormLayout(inner)
            fl.setContentsMargins(0, 0, 0, 0)
            fl.setRowWrapPolicy(QFormLayout.WrapLongRows)
            for o in opts:
                # named as the other options are, what it does in its
                # tooltip: a long line beside the box made the panel wider
                # than its column
                # a drop-down's first item the default
                wid = field(o, self._edited, o.key.replace('_', ' '), True,
                            True, '' if o.default in ('', None) else
                            str(o.default))
                wid.setToolTip(f"{o.flag}: {o.help}")
                self.w[o.key] = wid
                label = (o.key.replace('_', ' ').capitalize()
                         if o.kind != 'bool' else '')
                if (o.pick and self.win is not None
                        and hasattr(self.win, 'start_pick')):
                    # the setting, picked on the board (planview)
                    fl.addRow(self._pick_label(label, o), wid)
                else:
                    fl.addRow(label, wid)
            self.forms[g] = fl
            # the first group open: what a run is of
            self.sections[g] = Section(g, inner, i == 0)
            v.addWidget(self.sections[g])
        # where a page's own section goes (add_section)
        self._after_sections = v.count()
        # what a study may hold several of (Analysis.several, its several_keys):
        # those settings as a table, a row a run (a board's rails), the rest of
        # the form shared by them all
        self.run_keys = ([
            k for k in getattr(analysis, 'several_keys', ()) or ()
            if k in self.w
        ] if getattr(analysis, 'several', '') else [])
        self.table = None
        if self.run_keys:
            self._run_table(v)
        self.buttons = FlowLayout()
        self.find_btn = None
        if getattr(analysis, 'finds', ''):  # the board proposes settings
            self.find_btn = QPushButton(
                f"Find {analysis.finds}", clicked=self.find, toolTip=
                (f"List the board's {analysis.finds}, and pick one: its "
                 "settings into the form"
                 ))
            self.buttons.addWidget(self.find_btn)
        self.suggest_btns = []
        self.run_btn = QPushButton('Run', clicked=lambda: self.start())
        self.stop_btn = QPushButton('Stop', enabled=False, clicked=self.stop)
        for b in (self.run_btn, self.stop_btn):
            self.buttons.addWidget(b)
            # in the window: its toolbar's Run and Stop
            b.setVisible(win is None)
        holder = QWidget()
        holder.setLayout(self.buttons)
        v.addWidget(holder)
        self.busy = QProgressBar(minimum=0, maximum=0, visible=False)
        v.addWidget(self.busy)
        self.status = QLabel('', wordWrap=True,
                             textInteractionFlags=Qt.TextSelectableByMouse)
        self.status.setContentsMargins(6, 2, 6, 4)
        v.addWidget(self.status)
        # the Run page shows it (Log of)
        self.log = QPlainTextEdit(
            readOnly=True, maximumBlockCount=5000,
            font=QFontDatabase.systemFont(QFontDatabase.FixedFont))
        ok, why = analysis.check()
        if not ok:
            self.setEnabled(False)
            self.status.setText(why)

    # ---- several runs: a table of what differs between them ----
    def _run_table(self, v):
        what = self.a.several
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        t = QTableWidget(0, len(self.run_keys))
        t.setHorizontalHeaderLabels(
            [k.replace('_', ' ').capitalize() for k in self.run_keys])
        # each heading whole; the dock scrolls sideways if it must
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.verticalHeader().hide()
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for c, k in enumerate(self.run_keys):
            o = self.option(k)
            t.horizontalHeaderItem(c).setToolTip(f"{o.flag}: {o.help}" + (
                ' (several: comma-separated)' if o.kind == 'list' else ''))
        t.itemChanged.connect(lambda *_: self._edited())
        lay.addWidget(t)
        row = QHBoxLayout()
        for text, slot in ((f"Add a {what}", self.add_run),
                           (f"Remove the {what}", self.remove_run)):
            row.addWidget(QPushButton(text, clicked=slot))
        if (self.win is not None and hasattr(self.win, 'start_pick')
                and any(self.pick_kind(k) for k in self.run_keys)):
            # the selected cell, picked on the board
            b = QPushButton(
                'Pick', checkable=True,
                toolTip=("Pick the selected cell on the board (a net, or a "
                         "part: by its column): click in the Viewer's plan; "
                         "this button again ends it"),
                toggled=lambda on: (self.win.start_pick(self, CELL) if on
                                    else self.win.end_pick(self, CELL)))
            row.addWidget(b)
            self.pick_btns[CELL] = b
            # its column's
            t.currentCellChanged.connect(
                lambda *_: b.isChecked() and self.win.start_pick(self, CELL))
        row.addStretch(1)
        lay.addLayout(row)
        self.table = t
        title = what.capitalize() + 's'
        self.sections[title] = Section(title, holder, True)
        # after Files: what the runs are of
        v.insertWidget(1, self.sections[title])
        self._after_sections += 1
        for k in self.run_keys:  # in the table, not the form
            for fl in self.forms.values():
                if fl.indexOf(self.w[k]) >= 0:
                    fl.setRowVisible(self.w[k], False)
        self.add_run()

    # ---- a setting picked on the board ----
    def _pick_label(self, label, o):
        """A form row's label with a button that picks the setting on the board:
        clicks in the Viewer's plan name a net or a part into it (the option's
        `pick`), until the button is let go."""
        from PySide6.QtWidgets import QToolButton
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(QLabel(label))
        many, what = (o.pick.endswith('s'),
                      'traces' if o.pick.startswith('net') else 'parts')
        b = QToolButton(
            text='Pick', checkable=True,
            toolTip=f"Pick {'them' if many else 'it'} on the board: click "
            f"{what} in the Viewer's plan "
            f"({'again takes one out; ' if many else ''}this button again "
            "ends it)")
        b.toggled.connect(lambda on, key=o.key: self.win.start_pick(
            self, key) if on else self.win.end_pick(self, key))
        lay.addWidget(b)
        self.pick_btns[o.key] = b
        return box

    def _cell(self):
        """(row, column key) of the table's selected cell, or None."""
        t = self.table
        if t is None or t.currentRow() < 0 or t.currentColumn() < 0:
            return None
        return t.currentRow(), self.run_keys[t.currentColumn()]

    def pick_kind(self, key):
        if key == CELL:  # the table's selected cell: its column's
            cell = self._cell()
            return self.pick_kind(cell[1]) if cell else None
        o = self.option(key)
        return o.pick if o else None

    def option(self, key):
        """Its Option of a key, or None."""
        return next((o for o in self.options if o.key == key), None)

    def picked_names(self, key):
        """The names a picked setting holds now."""
        if key == CELL:
            cell = self._cell()
            it = (self.table.item(cell[0], self.run_keys.index(cell[1]))
                  if cell else None)
            text = it.text() if it is not None else ''
        else:
            w = self.w[key]
            text = (w.toPlainText()
                    if isinstance(w, QPlainTextEdit) else w.text())
        return [x.strip() for x in re.split(r'[\n,]', text) if x.strip()]

    def take_pick(self, key, name):
        """A name picked on the board into setting `key`: one that holds several
        takes it in, or out when it is there already; one that holds one is set.
        Returns the names it now holds."""
        kind = self.pick_kind(key) or ''
        cell = self._cell() if key == CELL else None
        if key == CELL and (cell is None or not kind):
            return []
        names = self.picked_names(key)
        if not kind.endswith('s'):
            names = [name]
        elif name in names:
            names = [n for n in names if n != name]
        else:
            names = names + [name]
        if cell is not None:
            self.table.item(cell[0], self.run_keys.index(cell[1])).setText(
                ', '.join(names))
        elif isinstance(self.w[key], QPlainTextEdit):
            self.w[key].setPlainText('\n'.join(names))
        else:
            self.w[key].setText(', '.join(names))
        return names

    def add_run(self, values=None):
        """A row of the table (a run): from values, else empty (its fields'
        defaults)."""
        t = self.table
        with self._quiet():
            r = t.rowCount()
            t.insertRow(r)
            for c, k in enumerate(self.run_keys):
                t.setItem(r, c, QTableWidgetItem(_cell((values or {}).get(k))))
        self._fit_table()
        if values is None:
            self._edited()

    def _fit_table(self):
        """The table as tall as its rows (two at least), not a box of empty
        space."""
        t = self.table
        h = (t.horizontalHeader().height()
             + max(t.rowCount(), 2) * t.verticalHeader().defaultSectionSize())
        t.setFixedHeight(h + 2 * t.frameWidth()
                         + t.horizontalScrollBar().sizeHint().height())

    def remove_run(self):
        """The selected row out (the last one stays: a run is of one at
        least)."""
        t = self.table
        r = t.currentRow() if t.currentRow() >= 0 else t.rowCount() - 1
        if t.rowCount() > 1:
            t.removeRow(r)
            self._fit_table()
            self._edited()

    def _row(self, r):
        out = {}
        for c, k in enumerate(self.run_keys):
            it = self.table.item(r, c)
            out[k] = it.text().strip() if it is not None else ''
            o = self.option(k)
            if o.kind == 'list':  # its items, comma- or line-separated
                out[k] = '\n'.join(o.convert(out[k]) or [])
        return out

    def runs(self):
        """The runs the table asks, [values]: the shared settings with each
        row's (one row: values()). A row with nothing in it is left out, unless
        it is the only one."""
        if not self.table:
            return [self.values()]
        shared = {
            k: v
            for k, v in self._form_values().items()
            if k not in self.run_keys
        }
        rows = [self._row(r) for r in range(self.table.rowCount())]
        filled = [r for r in rows if any(r.values())] or rows[:1]
        return [dict(shared, **r) for r in filled]

    def set_runs(self, runs):
        """The table from several runs' settings ([{key: value}]): each its row,
        the shared settings the first's."""
        runs = [dict(r) for r in runs] or [{}]
        self.set_values(runs[0])
        if not self.table:
            return
        with self._quiet():
            self.table.setRowCount(0)
        for r in runs:
            self.add_run(r)

    # ---- the settings ----
    @contextmanager
    def _quiet(self):
        """Fields set by the panel itself, not edits: no `changed` meanwhile."""
        self._setting += 1
        try:
            yield
        finally:
            self._setting -= 1

    def _edited(self, *_):
        if not self._setting:
            self.changed.emit()

    def needs(self, asked=(), to_come=()):
        """What a run of these settings still needs, [dict(text, key)]: the
        options a proposal left without a value, and what the analysis says
        (Analysis.missing). to_come: the analyses asked with it that run before
        it and have no result yet: what they will give is not missing."""
        out = []
        runs = self.runs()
        # each run's (a table's rows: said by which)
        for i, vals in enumerate(runs):
            said = (f"{self._label(vals) or f'{self.a.several} {i + 1}'}: "
                    if len(runs) > 1 else '')
            if asked:
                vals = self.a.for_questions(vals, tuple(asked))
            out += [
                dict(text=said + f"set {k.replace('_', ' ')}", key=k)
                for k in sorted(self.required)
                if not str(vals.get(k, '')).strip()
            ]
            if hasattr(self.a, 'missing'):
                latest = (self.win.latest_results() if self.win is not None
                          and hasattr(self.win, 'latest_results') else {})
                latest = {**{k: None for k in to_come}, **latest}
                try:
                    out += [
                        dict(text=said + str(m['text']),
                             key=m.get('key')) for m in self.a.missing(
                                 vals,
                                 self.board_path() or None, latest)
                    ]
                # (an analysis's check: never the window's end)
                except Exception:  # noqa: BLE001
                    pass
        return out

    def focus(self, key):
        """The field of an option in view, its section open; no such option: the
        first section."""
        wid = self.w.get(key)
        for g, fl in self.forms.items():
            if wid is not None and fl.indexOf(wid) >= 0 and g in self.sections:
                self.sections[g].set_expanded(True)
        if wid is not None:
            wid.setFocus()

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(
            self, 'Board', '',
            'KiCad board (*.kicad_pcb);;kicad2ems board (*.json)')
        if p:
            self.board.setText(p)

    def add_section(self, title, widget, form=None, expanded=False):
        """A section of the page's own under the options' (the circuit's model
        values): its widget, and the form its rows are in."""
        self.sections[title] = Section(title, widget, expanded)
        if form is not None:
            self.forms[title] = form
        self._layout.insertWidget(self._after_sections, self.sections[title])
        self._after_sections += 1
        return self.sections[title]

    def values(self):
        """The settings: the form's, and with a table of runs the first run's
        (runs(): all of them)."""
        out = self._form_values()
        if self.table:
            out.update(self._row(0) if self.table.rowCount() else {})
        return out

    def _form_values(self):
        return {k: value(wid) for k, wid in self.w.items()}

    def set_values(self, vals):
        """Some of the form's fields (the others stay): a list as lines, None as
        empty. With a table of runs, its keys into the first row."""
        with self._quiet():
            if self.table and any(k in vals for k in self.run_keys):
                if not self.table.rowCount():
                    self.add_run({})
                for c, k in enumerate(self.run_keys):
                    if k in vals:
                        self.table.setItem(0, c,
                                           QTableWidgetItem(_cell(vals[k])))
            for k, v in vals.items():
                if k in self.w:
                    set_value(self.w[k], f"{v:g}" if isinstance(v, float)
                              else v)
        self.required -= {
            k
            for k, v in vals.items()
            if str(v if v is not None else '').strip()
        }

    def set_own(self, board, out):
        """The panel's own board and output folder (None or '': the study's)."""
        with self._quiet():
            self.board.setText(str(board or ''))
            self.outdir.setText(str(out or ''))

    def reset(self):
        """The form as it is made: every field empty (its default), no board or
        folder of its own."""
        self.set_values({
            o.key: (bool(o.default) if o.kind == 'bool' else '')
            for o in self.options
        })
        with self._quiet():
            self.board.clear()
            self.outdir.clear()
        self.required = set()
        self.result = None
        self._suggestions([])
        if self.table:
            with self._quiet():
                self.table.setRowCount(0)
            self.add_run({})
        if self.isEnabled():
            self.status.setText('')

    def board_path(self):
        """The board named here, else the study's (the window's settings)."""
        p = self.board.text().strip()
        if not p and self.win is not None:
            try:
                p = self.win.setup.values().get('board') or ''
            except Exception:  # a field half typed
                p = ''
        return p

    def default_out(self, board, tag, preview=False):
        """Where a run goes when no folder is named: in the window a folder per
        run under its output folder (MainWindow.tool_out); alone, <name>_<tag>
        beside the board."""
        if self.win is not None and hasattr(self.win, 'tool_out'):
            return self.win.tool_out(self.a.name, board, tag, preview)
        base = os.path.join(
            (os.path.dirname(os.path.abspath(board)) if board else os.getcwd()),
            f"{self.a.name}_" + ''.join(c if c.isalnum() else '_' for c in tag))
        return os.path.join(base, 'preview') if preview else base

    def say(self, text):
        self.log.appendPlainText(text)

    def _running(self, on):
        self.run_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        if self.find_btn is not None:
            self.find_btn.setEnabled(not on)
        self.busy.setVisible(on)

    # ---- a run ----
    @property
    def proc(self):
        """The process of its run (or its Find) going, or None."""
        cmd = self.runner.cmd
        return (self.runner.proc if cmd is not None and cmd.key == self.a.name
                else self._finder.proc)

    def start(self, preview=False):
        """Alone: start the analysis (its preview), a table's rows one after
        the other (a preview: the first's alone). The status line says why
        when it cannot start; ended(code) when it all ends."""
        if self.proc is not None or (preview and not self.a.previewed):
            return
        self.log.clear()  # one log for the rows' runs together
        why = self.runner.start(self.commands(
            preview, getattr(self, 'asked', None) or (),
            getattr(self, 'engine', None), getattr(self, 'sets', None)))
        if why:
            self.status.setText(why)

    def commands(self, preview=False, asked=(), engine=None, sets=None):
        """Its runs as they stand, for the launcher: a function each (a row of
        the table: a preview, the first's alone) that makes its Command when
        its turn comes (its folder, its mark) or says why it cannot start.
        asked: the questions it is asked; engine: the engine chosen (one of
        its variants: the mark names it), sets: what that engine fixes."""
        if preview and not self.a.previewed:
            return []
        runs = self.runs()[:1] if preview else self.runs()
        self._count, self._k = len(runs), 0
        return [lambda v=v: self._command(v, preview, tuple(asked or ()),
                                          engine, sets)
                for v in runs]

    def _command(self, row, preview, asked, engine, sets):
        vals = self.a.for_questions(row, asked) if asked else dict(row)
        vals = dict(vals, **(sets or {}))
        many = self._count > 1
        said = f"{self._label(vals) or self.a.several}: " if many else ''
        unset = sorted(k for k in self.required
                       if not str(vals.get(k, '')).strip())
        if unset:
            return (said + "Set " + ', '.join(k.replace('_', ' ')
                                              for k in unset)
                    + " before Run: " + ('it was' if len(unset) == 1 else
                                         'they were')
                    + " not known where these settings came from")
        board = self.board_path() or None
        try:
            named = self.outdir.text().strip()
            tag = self.a.tag(vals)
            # several runs: a folder each under the named one
            if named and many:
                named = os.path.join(
                    named, ''.join(c if c.isalnum() else '_' for c in tag))
            out = os.path.abspath(
                (os.path.join(named, 'preview') if preview else named)
                if named else self.default_out(board, tag, preview))
            latest = (self.win.latest_results() if self.win is not None
                      and hasattr(self.win, 'latest_results') else {})
            argv = self.a.command(vals, board, out, preview=preview,
                                  results=latest)
        except ValueError as e:
            return said + str(e)
        from kicad2ems.core.runs import mark
        mark(out, engine or self.a.name, asked or self.a.answers(vals), preview,
             config=getattr(getattr(self.win, 'setup', None), 'path', None),
             settings=row)
        return Command(self.a.name, argv, out, env=dict(self.a.env or {}),
                       preview=preview, label=self._label(vals), values=vals)

    def began(self, cmd):
        """One of its runs started (the launcher's)."""
        self.out, self._vals = cmd.folder, cmd.values or {}
        self._k += 1
        python = os.path.basename(cmd.argv[0]).startswith('python')
        self.say('$ ' + ' '.join(['python', *cmd.argv[1:]] if python
                                 else cmd.argv))
        self._running(True)
        self.status.setText(self._progress('running…'))

    def skipped(self, why):
        """One of its runs could not start: why."""
        self.status.setText(why)

    def _label(self, vals):
        """A run of the table by its first column (the rail's net), or ''."""
        return str(vals.get(self.run_keys[0]) or '') if self.run_keys else ''

    def _progress(self, text):
        """A status line that says which run of how many, when there are
        several."""
        if self._count <= 1:
            return text
        return (f"{self.a.several} {self._k} of {self._count} "
                f"({self._label(self._vals)}): {text}")

    def stop(self):
        """Its run asked to stop (a table's next rows not run), its Find
        ended: RunProcess.stop."""
        if self.runner.cmd is not None and self.runner.cmd.key == self.a.name:
            self.runner.stop()
        if self._finder.proc is not None:
            self._finder.proc.kill()

    def output(self, text):
        """Its run's output: into its log, its progress on the status line."""
        for line in text.splitlines():
            # a library's warning: not the tool's word
            if re.search(r'\.py:\d+: \w*Warning', line):
                continue
            self.say(line)
            st = self.a.status(line)
            if st:
                self.status.setText(st)

    def done(self, cmd, code, how):
        """One of its runs ended (how: its outcome): the status line says how,
        its result the one shown."""
        self._running(False)
        if code == 0 and self.a.owns(cmd.folder, cmd.preview):
            self.status.setToolTip('')
            rep = self.a.report(cmd.folder) or {}
            self.status.setText(
                self._progress('preview ready' if cmd.preview else (
                    'done: '
                    + rep['summary'] if rep.get('summary') else 'done')))
            self.show_result(cmd.folder)
            return
        lines = [l for l in self.log.toPlainText().splitlines() if l.strip()]
        errs = [
            l for l in lines
            if 'error' in l.lower() or l.startswith(('RuntimeError',
                                                     'ValueError'))
        ]
        last = (errs or lines or [''])[-1].strip()
        if how == 'stopped':
            self.status.setText(self._progress('stopped'))
        else:
            self.status.setText(
                self._progress(f"the run failed (exit {code})"
                               + (f": {last[:200]}" if last else '')
                               + ' (the whole log: the Run page)'))
        self.status.setToolTip('\n'.join((errs or lines)[-8:]))

    # ---- what a result proposes, what the board proposes ----
    def show_result(self, folder):
        """A result of this analysis is the one shown: what it proposes for
        other analyses, as buttons."""
        self.result = folder
        try:
            self._suggestions(
                self.a.suggest(folder) if self.win is not None else [])
        except Exception:  # an analysis's fault never stops the window
            self._suggestions([])

    def _suggestions(self, items):
        for b in self.suggest_btns:
            self.buttons.removeWidget(b)
            b.deleteLater()
        self.suggest_btns = []
        for s in items:
            if s.get('to') not in getattr(self.win, 'pages', {}):
                continue
            b = QPushButton(s.get('label') or f"Copy to {s['to']}")
            b.setToolTip(s.get('note', ''))
            b.clicked.connect(
                lambda _=False, s=s: self.win.apply_suggestion(self, s))
            self.buttons.addWidget(b)
            self.suggest_btns.append(b)

    def find(self):
        """The analysis's discover command on the board; what it finds to pick
        from, the pick into the form."""
        if self.proc is not None:
            return
        f = self._finder
        try:
            argv = self.a.discover(self.values(), self.board_path() or None)
        except ValueError as e:
            self.status.setText(str(e))
            return
        if not argv:
            return
        self._found = []
        self.say('$ ' + ' '.join(['python', *argv[1:]]))
        self.status.setText(f"finding {self.a.finds}…")
        self._running(True)

        def output():
            self._found.append(bytes(f.proc.readAllStandardOutput()).decode(
                errors='replace') if f.proc is not None else '')

        def finished(code, _status=None):
            if f.proc is not None:
                output()
                f.proc.deleteLater()
                f.proc = None
            self._running(False)
            text = ''.join(self._found)
            picks = self.a.choices(text) if code == 0 else None
            if picks is None:
                self.say(text)
                self.status.setText(
                    f"finding {self.a.finds} failed (exit {code}): the Run "
                    "page has the log"
                )
                return
            self.pick(picks)
        run_tool(f, argv, output, finished)

    def pick(self, choices):
        """One of [(label, values)] into the form: the only one, else the one
        chosen in a dialog."""
        if not choices:
            self.status.setText(f"no {self.a.finds} found on this board")
            return
        labels = [label for label, _ in choices]
        if len(choices) == 1:
            label = labels[0]
        else:
            label, ok = QInputDialog.getItem(self, f"Find {self.a.finds}",
                                             'Use:', labels, 0, False)
            if not ok:
                return
        self.set_values(dict(choices)[label])
        self.changed.emit()
        self.status.setText(f"{label.split(' (')[0]}: set; Run"
                            + (' or Preview' if self.a.previewed else ''))


class _Finder(QObject):
    """A panel's Find: its discover command's process (toolprocess.run_tool),
    apart from its runs'."""
    def __init__(self, panel):
        super().__init__(panel)
        self.proc, self.say = None, panel.say
