"""
The questions' list in the Study dock: the view of a study.StudyModel.

    StudyTree(model=None)  a row per question, by group: a tick (it is asked),
                           who answers it (a menu: one engine, or several to
                           compare), a tick for its field dumps where an engine
                           can write them, and its state (else when the
                           config's runs last answered it: a click shows that
                           run)

The model holds the study (what is asked, by whom, the Fields ticks, what cannot
run here, each run's state) and answers every question about it; the list draws
it, passes on the user's hand, and says which question is selected. What the
model has is reached through the list too (`list.asked()`, `list.jobs()`,
`list.choice`), so the window talks to one object.
"""
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QHeaderView,
                               QAbstractItemView, QMenu)

from .study import RF, AUTO, StudyModel, run_key

TONES = {'': None, 'gray': 'gray', 'bad': '#c62828'}
C_Q, C_BY, C_FIELDS, C_STATE = range(4)             # the columns


class StudyTree(QTreeWidget):
    """The questions, by group: a tick (Run and Preview include it), who answers
    it (click the cell: a menu of its engines, several ticked are compared), a
    tick for its field dumps where an engine can write them, its state. The
    selected question's settings are the ones the dock shows."""
    selected = Signal(str)              # the question whose settings to show
    # a tick, an engine or a Fields tick changed
    ticks_changed = Signal()
    # double-clicked: the run whose page to show ('rf' or an analysis)
    opened = Signal(str)
    # the context menu: run this question alone
    run_one = Signal(str)
    # the user ticked or unticked a row's Fields
    fields_ticked = Signal(str, bool)
    # a State cell clicked: the run that last answered it, to show
    show_run = Signal(str)

    def __init__(self, model=None):
        super().__init__(
            rootIsDecorated=False,
            indentation=12,
            uniformRowHeights=True,
            textElideMode=Qt.ElideRight,
            selectionMode=QAbstractItemView.SingleSelection,
            contextMenuPolicy=Qt.CustomContextMenu,
            # every row shown, a long State cut short
            horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff,
            verticalScrollBarPolicy=Qt.ScrollBarAlwaysOff)
        self.m = model or StudyModel()
        self.setHeaderLabels(['Question', 'Answered by', 'Fields', 'State'])
        self.headerItem().setToolTip(
            C_BY,
            'click a cell: the engine that answers, or several to compare')
        self.headerItem().setToolTip(
            C_FIELDS,
            "its field dumps too, where the engine can write them (ticked with "
            "none set: a default, which its settings let you change)")
        self.customContextMenuRequested.connect(self._context)
        self.rows = {}
        # {question: (the run's label, its folder)}: the config's newest run
        # that answered it (set_answered)
        self.answered = {}
        self.boxes = {}  # the solvers' tick boxes (set_solver_boxes)
        self._setting = False
        bold = QFont(self.font())
        bold.setBold(True)
        n = 0
        for group, names in self.m.groups:
            head = QTreeWidgetItem([group])
            head.setFont(C_Q, bold)
            head.setFlags(Qt.ItemIsEnabled)
            self.addTopLevelItem(head)
            head.setFirstColumnSpanned(True)
            for q in names:
                it = QTreeWidgetItem([self.m.labels[q], '', '', ''])
                it.setData(C_Q, Qt.UserRole, q)
                it.setToolTip(C_Q, self.m.about[q])
                head.addChild(it)
                self.rows[q] = it
                it.setCheckState(C_Q, Qt.Unchecked)
            head.setExpanded(True)
            n += 1 + len(names)
        self.refresh()
        hd = self.header()
        for c in (C_Q, C_BY, C_FIELDS):
            hd.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hd.setStretchLastSection(True)
        first = next(iter(self.rows.values()), None)
        row = (self.sizeHintForRow(0) if first is None else
               self.rowHeight(self.indexFromItem(first)) or 20)
        self.setFixedHeight(row * max(n, 1) + hd.sizeHint().height()
                            + 2 * self.frameWidth() + 6)
        self.itemChanged.connect(self._changed)
        self.itemClicked.connect(self._clicked)
        self.currentItemChanged.connect(self._current)
        self.itemDoubleClicked.connect(lambda it, _col: self._is_row(
            it) and self.opened.emit(self.run_of(self._q(it))))
        if 'sparams' in self.rows:
            self.setCurrentItem(self.rows['sparams'])
        elif first is not None:
            self.setCurrentItem(first)
        self.m.listeners.append(self._model_changed)

    def __getattr__(self, name):
        """What the model has (asked, jobs, run_of, engines_for, choice, fields,
        why, states, engines, ...)."""
        if name.startswith('__') or name == 'm':
            raise AttributeError(name)
        return getattr(self.m, name)

    def _model_changed(self, what, key):
        """The model changed (by this list or by anyone else): the rows as it
        now is."""
        if what == 'state':
            for q in self.rows:
                self._state(q)
        else:
            self.refresh()

    # ---- the rows ----
    @staticmethod
    def _q(it):
        return it.data(C_Q, Qt.UserRole) if it is not None else None

    def _is_row(self, it):
        return self._q(it) in self.rows

    def _row(self, q):
        """A row's cells from what the model has for it."""
        it, m = self.rows[q], self.m
        self._setting = True
        try:
            ok = m.can_ask(q)
            gray = None if ok else QBrush(QColor('gray'))
            for col in range(4):
                it.setForeground(col, gray if gray is not None else QBrush())
            it.setFlags(it.flags()
                        | Qt.ItemIsUserCheckable if ok else it.flags()
                        & ~Qt.ItemIsUserCheckable)
            it.setCheckState(C_Q,
                             (Qt.Checked if ok and q in m.on else Qt.Unchecked))
            text, tip, more = m.by(q)
            it.setText(C_BY, text + ('  ▾' if more and ok else ''))
            it.setToolTip(C_BY, tip)
            if ok and m.can_dump(q):
                it.setCheckState(
                    C_FIELDS, (Qt.Checked if m.fields.get(q) else Qt.Unchecked))
            else:
                it.setData(C_FIELDS, Qt.CheckStateRole, None)
            self._state(q)
        finally:
            self._setting = False

    def refresh(self):
        """Every row anew (the solvers ticked changed, an engine became
        available)."""
        for q in self.rows:
            self._row(q)

    def set_solver_boxes(self, boxes):
        """The solvers' tick boxes ({name: QCheckBox}, 'auto' among them): the
        RF rows show and set them."""
        self.boxes = dict(boxes)
        sync = lambda _on=None: self.m.set_solvers(
            {n: w.isChecked()
             for n, w in self.boxes.items()})
        for w in self.boxes.values():
            w.toggled.connect(sync)
        sync()
        self.refresh()

    # ---- what is asked: the model's, with the list's signals ----
    def set_study(self, study):
        self.m.set_study(study)
        self.ticks_changed.emit()

    def set_ticked(self, keys):
        self.set_study(self.m.study_of_runs(keys))

    def set_fields(self, q, on):
        if self.m.set_fields(q, on):
            self.ticks_changed.emit()

    def ask(self, key):
        """The questions a run answers asked too (the model's ask), the list's
        signal with it."""
        before = set(self.m.on)
        self.m.ask(key)
        if self.m.on != before:
            self.ticks_changed.emit()

    def set_available(self, key, ok, why=''):
        self.m.set_available(key, ok, why)
        q = self.current_question()
        if (q is not None and any(key in (e.name, run_key(e))
                                  for e in self.m.engines.get(q, []))):
            self.selected.emit(q)  # its settings: the engines that can run now

    def state(self, key):  # (QAbstractItemView has a state() of its own)
        """A run's state as it was set."""
        return self.m.state(key)

    def set_answered(self, answered):
        """When each question was last answered: {question: (label, folder)}
        of the config's newest run that did (the window's Runs page)."""
        if answered != self.answered:
            self.answered = dict(answered)
            for q in self.rows:
                self._state(q)

    def _state(self, q):
        it = self.rows[q]
        text, tip, tone = self.m.state_of(q)
        last = self.answered.get(q)
        if last is not None:
            if not text:  # nothing run of it here: when it last was
                text, tone = f"last {last[0].split(' · ')[0]}", 'gray'
            tip = ((tip + '\n') if tip else '') + (
                f"last answered by the run of {last[0]} (click: show it)")
        it.setToolTip(C_Q, self.m.about[q] + (
            f"\nlast answered: {last[0]}" if last is not None else ''))
        it.setText(C_STATE, text)
        it.setToolTip(C_STATE, tip)
        c = TONES.get(tone)
        it.setForeground(C_STATE, QBrush(QColor(c)) if c else QBrush())
        f = it.font(C_STATE)
        f.setBold('running' in text)
        it.setFont(C_STATE, f)

    # ---- the selection ----
    def current_question(self):
        q = self._q(self.currentItem())
        return q if q in self.rows else next(iter(self.rows), None)

    def current(self):
        """The run of the selected question: 'rf' or an analysis."""
        q = self.current_question()
        return self.m.run_of(q) if q else RF

    def select(self, key):
        """A question, or the first question (an asked one first) a run
        answers."""
        m = self.m
        if key in self.rows:
            q = key
        else:
            qs = [
                q for q in self.rows if (m.is_solver(q) if key == RF else any(
                    run_key(e) == key for e in m.engines[q]))
            ]
            if key != RF and self.current_question() in qs:
                return
            if key == RF and m.is_solver(self.current_question()):
                return
            asked = [
                q for q in qs
                if q in m.asked() and (key == RF or key in m.runs_for(q))
            ]
            q = (asked or qs or [None])[0]
        if q is not None and self.current_question() != q:
            self.setCurrentItem(self.rows[q])

    def _current(self, cur, old):
        """The selected question: its settings shown. A group's heading is not
        one: the keys step over it (to the row beyond, the way they went)."""
        if cur is None:
            return
        if self._is_row(cur):
            self.selected.emit(self._q(cur))
            return
        down = (old is None or self.visualItemRect(cur).y() >
                self.visualItemRect(old).y())
        it = cur
        while it is not None and not self._is_row(it):
            it = self.itemBelow(it) if down else self.itemAbove(it)
        if it is None:
            it = old if self._is_row(old) else self.rows.get('sparams')
        if it is not None:  # (once the key's own selection is done)
            QTimer.singleShot(0, lambda: self.setCurrentItem(it))

    def mousePressEvent(self, ev):
        # a group's heading: no question to select (it had kept the last one's
        # settings in the dock, with none highlighted)
        it = self.itemAt(ev.position().toPoint())
        if it is not None and not self._is_row(it):
            ev.accept()
            return
        super().mousePressEvent(ev)

    # ---- the user's hand ----
    def _changed(self, it, col):
        if self._setting or not self._is_row(it):
            return
        q = self._q(it)
        if col == C_Q:
            self.m.tick(q, it.checkState(C_Q) == Qt.Checked)
            self._row(q)
            self.ticks_changed.emit()
        elif col == C_FIELDS:
            on = it.checkState(C_FIELDS) == Qt.Checked
            self.m.fields[q] = on
            self.fields_ticked.emit(q, on)
            self.ticks_changed.emit()

    def _clicked(self, it, col):
        if (col == C_STATE and self._is_row(it)
                and self._q(it) in self.answered):
            self.show_run.emit(self.answered[self._q(it)][1])
        if col == C_BY and self._is_row(it) and self.m.can_ask(self._q(it)):
            q = self._q(it)
            if self.m.is_solver(q) or len(self.m.engines[q]) > 1:
                from PySide6.QtCore import QPoint
                at = QPoint(self.header().sectionViewportPosition(C_BY),
                            self.visualItemRect(it).bottom() + 1)
                # under the cell
                self.engine_menu(q).exec(self.viewport().mapToGlobal(at))

    def choose(self, q, name, on):
        """Tick or untick an engine of a question ('auto': its default alone). A
        solver question: the solver's tick box."""
        if self.m.is_solver(q):
            if name in self.boxes:
                self.boxes[name].setChecked(bool(on))
            return
        self.m.choose(q, name, on)
        self.ticks_changed.emit()
        if self.current_question() == q:
            self.selected.emit(q)  # its settings: the engines' now

    def engine_menu(self, q, menu=None):
        """The menu of a question's engines: 'auto', then a tick each."""
        m = menu or QMenu(self)
        m.setToolTipsVisible(True)
        es = self.m.engines[q]
        if self.m.is_solver(q):
            for name in [AUTO] + [e.name for e in es]:
                box = self.boxes.get(name)
                if box is None:
                    continue
                a = m.addAction(box.text() or name)
                a.setCheckable(True)
                a.setChecked(box.isChecked())
                a.setToolTip(box.toolTip())
                a.toggled.connect(lambda on, n=name: self.choose(q, n, on))
            return m
        first = (self.m.usable(q) or [None])[0]
        label = next((e.label for e in es if e.name == first), '')
        a = m.addAction(f"auto ({label})" if label else 'auto')
        a.setCheckable(True)
        a.setChecked(not [n for n in self.m.choice[q] if n in self.m.usable(q)])
        a.triggered.connect(lambda _=False: self.choose(q, AUTO, True))
        m.addSeparator()
        for e in es:
            a = m.addAction(f"{e.label}  ({e.impl.tool})")
            a.setCheckable(True)
            a.setChecked(e.name in self.m.choice[q])
            if self.m.why_not(q, e.name):
                a.setEnabled(False)
                a.setToolTip(self.m.why_not(q, e.name))
            a.toggled.connect(lambda on, n=e.name: self.choose(q, n, on))
        return m

    def _context(self, pos):
        it = self.itemAt(pos)
        if not self._is_row(it):
            return
        q = self._q(it)
        m = QMenu(self)
        a = m.addAction(f"Run {self.m.labels[q]} alone")
        a.setEnabled(self.m.can_ask(q))
        a.triggered.connect(lambda: self.run_one.emit(q))
        b = m.addAction('Show its page')
        b.triggered.connect(lambda: self.opened.emit(self.m.run_of(q)))
        if (self.m.can_ask(q)
                and (self.m.is_solver(q) or len(self.m.engines[q]) > 1)):
            self.engine_menu(q, m.addMenu('Answered by'))
        m.exec(self.viewport().mapToGlobal(pos))


StudyList = StudyTree  # its name before the questions
