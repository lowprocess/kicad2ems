"""
GUI building blocks: a collapsible settings section, an image panel (model, mesh, line model, convergence plots), a
row of controls that wraps when narrow.
"""
import os
from PySide6.QtCore import Qt, QRect, QPoint, QSignalBlocker, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QLabel, QScrollArea, QSizePolicy,
                               QFrame, QLayout)


def refill(combo, items):
    """A combo box's items anew, [(label, data)], without its signals; the item that was current stays so (else the
    first)."""
    keep = combo.currentData()
    with QSignalBlocker(combo):
        combo.clear()
        for label, data in items:
            combo.addItem(label, data)
        combo.setCurrentIndex(max(combo.findData(keep), 0))


class Section(QWidget):
    """A titled group that folds open and closed: a header (arrow, title, how many of its settings are set) and
    the content widget. Collapsed when made."""
    def __init__(self, title, content, expanded=False, extra=None):
        """extra: a widget at the right of the header (a help button)."""
        super().__init__()
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QToolButton(toolButtonStyle=Qt.ToolButtonTextBesideIcon, checkable=True, toggled=self.set_expanded,
                                styleSheet='QToolButton { border: none; font-weight: bold; padding: 4px; '
                                           'text-align: left; }')
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if extra is None:
            lay.addWidget(self.head)
        else:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 4, 0)
            row.addWidget(self.head, 1)
            row.addWidget(extra)
            lay.addLayout(row)
        self.body = QFrame(frameShape=QFrame.StyledPanel)
        fl = QVBoxLayout(self.body)
        fl.setContentsMargins(8, 4, 4, 6)
        fl.addWidget(content)
        lay.addWidget(self.body)
        self.set_count(0)
        self.head.setChecked(expanded)
        self.set_expanded(expanded)

    def set_expanded(self, on):
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)
        if self.head.isChecked() != on:
            self.head.setChecked(on)

    @property
    def expanded(self):
        return self.head.isChecked()

    def set_count(self, n):
        """n: how many settings of the section differ from their defaults (shown in the header)."""
        self.head.setText(f"{self.title}" + (f"  ({n} set)" if n else ''))


class NeededList(QWidget):
    """What a run of the selected question still needs: a line each, which leads to where it is set (picked: the
    item, dict(text, key, run)). Nothing needed: it says so; a question not asked and complete: hidden."""
    picked = Signal(object)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(3)
        self.title = QLabel('Still needed', styleSheet='font-weight: bold;')
        lay.addWidget(self.title)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        lay.addLayout(self.rows)
        self.items = []

    def show_items(self, items, asked=True):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.hide()                                    # (gone now: deleteLater alone left it drawn under the new)
                w.setParent(None)
                w.deleteLater()
        self.items = list(items)
        self.setVisible(bool(items) or asked)
        if not items:
            self.rows.addWidget(QLabel('nothing: ready to run', enabled=False))
        for i, it in enumerate(self.items):
            self.rows.addWidget(QLabel(f'<a href="{i}" style="text-decoration: none; color: #9a5b00;">&#9888;&nbsp; '
                                       f'{it["text"]}</a>', wordWrap=True, toolTip='go to where it is set',
                                       textInteractionFlags=Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard,
                                       linkActivated=lambda n: self.picked.emit(self.items[int(n)])))


class ImagePanel(QWidget):
    """Plots written by a run (PNG), stacked and scaled to the panel's width; a note when there are none."""
    def __init__(self, empty='nothing yet'):
        super().__init__()
        self.paths, self.empty = [], empty
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.inner = QVBoxLayout()
        w = QWidget()
        w.setLayout(self.inner)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(w)
        lay.addWidget(scroll)
        self.show_images([])

    def show_images(self, paths):
        self.paths = [p for p in paths if p and os.path.exists(p)]
        while self.inner.count():
            it = self.inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self.paths:
            self.inner.addWidget(QLabel(self.empty, styleSheet='color: gray'))
        for p in self.paths:
            lab = QLabel()
            pm = QPixmap(p)
            lab.setPixmap(pm.scaledToWidth(min(pm.width(), 1400), Qt.SmoothTransformation))
            lab.setToolTip(p)
            self.inner.addWidget(lab)
        self.inner.addStretch(1)


class FlowLayout(QLayout):
    """A row of controls that wraps onto more lines when the panel is narrow (Qt's flow layout example). A drop-in
    for a QHBoxLayout of controls: addWidget takes (and ignores) a stretch, addStretch / addSpacing do nothing."""
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items, self._sp = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, w, stretch=0, alignment=None):
        super().addWidget(w)

    def addStretch(self, *_):
        pass

    def addSpacing(self, *_):
        pass

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, False)

    def sizeHint(self):
        w = sum(it.sizeHint().width() + self._sp for it in self._visible())
        h = max([it.sizeHint().height() for it in self._visible()] or [0])
        return QSize(w, h)

    def minimumSize(self):
        s = QSize()
        for it in self._visible():
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _visible(self):
        return [it for it in self._items if not (it.widget() and it.widget().isHidden())]

    def _place(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = r.x(), r.y(), 0
        for it in self._visible():
            hint = it.sizeHint()
            nxt = x + hint.width() + self._sp
            if nxt - self._sp > r.right() and line > 0:
                x, y, line = r.x(), y + line + self._sp, 0
                nxt = x + hint.width() + self._sp
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, line = nxt, max(line, hint.height())
        return y + line - rect.y() + m.bottom()
