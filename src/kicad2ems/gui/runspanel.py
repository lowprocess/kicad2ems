"""
The Runs page: this board's runs as a table (runfolders.index), the one shown
and the ones compared picked there; and the toolbar's Showing, which says which
run the pages show and brings the page forward.

    RunsPanel(win)       a row per run: when, what it answered, the engines,
                         how it ended, how long it took, its size, whether its
                         settings are the form's now (≠ settings: which
                         differ); the board's preview last, current or stale.
                         Its scope: This config, This board, All.
    showing(info, ...)   the Showing button's text

A click shows a run in the pages (Viewer, Plots, Report, Tables); Ctrl or
Shift and a click more lays those over it (Plots; the second Viewer when they
have fields or maps). Delete and Open folder act on the rows selected.
"""
import os
from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
                               QPushButton, QLabel, QTreeWidget,
                               QTreeWidgetItem, QAbstractItemView,
                               QHeaderView, QMenu)
from . import runfolders as rf

COLS = ('When', 'Answered', 'Engines', 'Outcome', 'Took', 'Size', 'Settings',
        'Board')
C_WHEN, C_ABOUT, C_ENG, C_OUT, C_TOOK, C_SIZE, C_SET, C_BOARD = range(8)
SCOPE_TEXT = dict(config='This config', board='This board', all='All')
BAD = '#c62828'


def showing(info, folder=None, ran=False):
    """The Showing button's text: the run shown (info), a folder that is no run
    of this board's, or none ('Nothing run yet': nothing of this config's)."""
    if info is not None:
        return f"Showing: {info.label()} ▾"
    if folder:
        return f"Showing: {os.path.basename(os.path.normpath(folder))} ▾"
    return 'Showing: none ▾' if ran else 'Nothing run yet'


def changes_text(diff):
    """A ≠ settings tooltip: a line a setting, 'key: old -> now'."""
    show = lambda v: '(not set)' if v is None else str(v)
    return '\n'.join(f"{k}: {show(a)} -> {show(b)}" for k, a, b in diff)


class RunsPanel(QWidget):
    """The Runs page (see the module)."""
    def __init__(self, win):
        super().__init__()
        self.win, self._filling = win, False
        self._all, self._shown_rows = [], []  # the board's runs, those listed
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.scope = QComboBox(toolTip=(
            "This config: the runs started from the config open (its file, "
            "which each run's folder names); This board: every run of the "
            "board; All: everything under the output folder"))
        for key in rf.SCOPES:
            self.scope.addItem(SCOPE_TEXT[key], key)
        # (after the items: the first one is a change)
        self.scope.currentIndexChanged.connect(lambda _i: self.refresh())
        row.addWidget(self.scope)
        self.hint = QLabel(styleSheet='color: gray;')
        row.addWidget(self.hint, 1)
        self.cmp_btn = QPushButton(
            'Compare', enabled=False, clicked=self._compare, toolTip=(
                "the rows selected over the one shown: their curves in Plots, "
                "the second in a second Viewer (Ctrl or Shift and a click "
                "selects more than one)"))
        self.open_btn = QPushButton('Open folder', enabled=False,
                                    clicked=self.open_folders)
        self.del_btn = QPushButton(
            'Delete…', enabled=False, clicked=self._delete,
            toolTip="the rows selected, their folders and all in them (asked "
            "first; not a run going)")
        for b in (self.cmp_btn, self.open_btn, self.del_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.tree = QTreeWidget(
            rootIsDecorated=False, uniformRowHeights=True,
            alternatingRowColors=True,
            selectionMode=QAbstractItemView.ExtendedSelection,
            contextMenuPolicy=Qt.CustomContextMenu)
        self.tree.setHeaderLabels(list(COLS))
        hd = self.tree.header()
        for c in range(len(COLS)):
            hd.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hd.setSectionResizeMode(C_ABOUT, QHeaderView.Stretch)
        hd.setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)

    # ---- what is listed ----
    def scope_key(self):
        return self.scope.currentData()

    def set_scope(self, key):
        self.scope.setCurrentIndex(max(self.scope.findData(key), 0))

    def runs(self):
        """The runs listed, as the table has them (the preview last)."""
        return list(self._shown_rows)

    def board_runs(self):
        """This board's runs, whatever the scope, as last read."""
        return list(self._all)

    def info(self, folder):
        """The RunInfo of a folder listed (any scope of this board), or
        None."""
        d = os.path.abspath(folder) if folder else None
        return next((r for r in self._all + self._shown_rows
                     if r.folder == d), None)

    def refresh(self, select=None):
        """The table read again (runfolders.index); select: the folder to
        select (else the selection as it was)."""
        win = self.win
        self._all = win.run_index('board')
        scope = self.scope_key()
        rows = (win.run_index('all') if scope == 'all' else self._all)
        self._shown_rows = rf.in_scope(rows, scope, win.config_path())
        keep = ([os.path.abspath(select)] if select else
                self.selected() or ([win.shown] if win.shown else []))
        self._fill(keep)
        return self._shown_rows

    def _fill(self, keep):
        self._filling = True
        try:
            with QSignalBlocker(self.tree):
                self.tree.clear()
                for r in self._shown_rows:
                    self.tree.addTopLevelItem(self._item(r))
                self.tree.setColumnHidden(C_BOARD, self.scope_key() != 'all')
                self._select(keep)
            self.marks()
        finally:
            self._filling = False
        self._buttons()

    def _item(self, r):
        it = QTreeWidgetItem([
            'preview' if r.preview else rf.when_text(r.when, seconds=True),
            r.about(), ', '.join(map(rf.engine_label, r.engines)),
            r.outcome, rf.took_text(r.took), rf.size_text(r.size), '',
            r.board])
        it.setData(0, Qt.UserRole, r.folder)
        it.setToolTip(C_WHEN, f"{r.when:%Y-%m-%d %H:%M:%S}\n{r.folder}"
                      + (f"\nconfig: {r.config}" if r.config else ''))
        it.setToolTip(C_ABOUT, r.about())
        if r.outcome != 'done':
            bad = r.outcome.startswith(('failed', 'crashed'))
            it.setForeground(C_OUT, QBrush(QColor(BAD if bad else 'gray')))
        if r.outcome == 'running':
            f = it.font(C_OUT)
            f.setBold(True)
            it.setFont(C_OUT, f)
        if r.preview:
            f = it.font(C_WHEN)
            f.setItalic(True)
            for c in range(len(COLS)):
                it.setFont(c, f)
        return it

    def _items(self):
        return [self.tree.topLevelItem(i)
                for i in range(self.tree.topLevelItemCount())]

    def marks(self):
        """Each row's ≠ settings (its settings against the form's now, the
        differing ones in its tooltip), the preview's current or stale, and
        the run shown in bold."""
        shown, nows = self.win.shown, {}
        for it in self._items():
            r = self.info(it.data(0, Qt.UserRole))
            if r is None:
                continue
            key = (r.kind, r.what)  # (a form read once a pass)
            if key not in nows:
                nows[key] = self.win.settings_now(r)
            now = nows[key]
            diff = (rf.differ(r.settings, now)
                    if now is not None and r.settings is not None else None)
            text = '' if not diff else '≠ settings'
            if r.preview and r.outcome in ('done', 'no result'):
                it.setText(C_OUT, 'stale' if diff or diff is None else
                           'current')
            it.setText(C_SET, text)
            it.setToolTip(C_SET, (
                'run with other settings than the form has now:\n'
                + changes_text(diff)) if diff else
                ('the settings the form has now' if diff == [] else
                 'its settings are not known (a run from elsewhere)'))
            bold = r.folder == shown
            for c in range(len(COLS)):
                f = it.font(c)
                f.setBold(bold or (c == C_OUT and r.outcome == 'running'))
                it.setFont(c, f)
        self._hint()

    def _hint(self):
        here = [r for r in self._shown_rows if not r.preview]
        more = (len([r for r in self._all if not r.preview]) - len(here)
                if self.scope_key() == 'config' else 0)
        text = ('nothing run yet for this config' if not here else
                f"{len(here)} run{'s' if len(here) != 1 else ''}")
        if more > 0:
            text += (f"; {more} more of this board (other configs, older "
                     "runs): This board lists them")
        self.hint.setText(text)

    # ---- the selection ----
    def selected(self):
        """The folders of the rows selected, top first."""
        return [it.data(0, Qt.UserRole) for it in self._items()
                if it.isSelected()]

    def _select(self, folders):
        want = {os.path.abspath(f) for f in folders if f}
        first = None
        for it in self._items():
            on = it.data(0, Qt.UserRole) in want
            it.setSelected(on)
            if on and first is None:
                first = it
        if first is not None:
            self.tree.scrollToItem(first)

    def select(self, folders):
        """Rows selected by the window (the run shown, those compared): the
        pages are not asked again."""
        self._filling = True
        try:
            with QSignalBlocker(self.tree):
                self._select(list(folders))
            self.marks()
        finally:
            self._filling = False
        self._buttons()

    def _buttons(self):
        sel = self.selected()
        self.cmp_btn.setEnabled(len(sel) > 1)
        self.open_btn.setEnabled(bool(sel))
        self.del_btn.setEnabled(bool(sel))

    def _selected(self):
        """One row: that run shown; more: the others laid over the one shown
        (it stays, when it is among them; else the top one)."""
        self._buttons()
        if self._filling:
            return
        sel = self.selected()
        if not sel:
            return
        win = self.win
        first = win.shown if win.shown in sel else sel[0]
        if first != win.shown:
            win.show_run(first)
        win.compare_runs([f for f in sel if f != first])
        self.select(sel)  # (showing one selects it alone)

    def _compare(self):
        """Compare: the plots in front, and the second run in the second
        Viewer whatever it holds."""
        sel = self.selected()
        first = self.win.shown if self.win.shown in sel else sel[0]
        others = [f for f in sel if f != first]
        if others:
            self.win.compare_runs(others, viewer=True)
            self.win.show_result()

    # ---- the rows' actions ----
    def open_folders(self):
        for d in self.selected()[:4]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _delete(self):
        self.win.delete_runs(self.selected())

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            self.tree.setCurrentItem(it)
        sel = self.selected()
        m = QMenu(self)
        if len(sel) > 1:
            m.addAction('Compare').triggered.connect(self._compare)
        m.addAction('Open folder').triggered.connect(self.open_folders)
        m.addAction(f"Delete {len(sel)} run{'s' if len(sel) > 1 else ''}…"
                    ).triggered.connect(self._delete)
        m.exec(self.tree.viewport().mapToGlobal(pos))
