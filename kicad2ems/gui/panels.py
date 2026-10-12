"""
GUI building blocks: a collapsible settings section, an image panel (model, mesh, line model, convergence plots).
"""
import os
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QLabel, QScrollArea, QSizePolicy, QFrame


class Section(QWidget):
    """A titled group that folds open and closed: a header (arrow, title, how many of its settings are set) and
    the content widget. Collapsed when made."""
    def __init__(self, title, content, expanded=False):
        super().__init__()
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QToolButton()
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setCheckable(True)
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.head.setStyleSheet('QToolButton { border: none; font-weight: bold; padding: 4px; text-align: left; }')
        self.head.toggled.connect(self.set_expanded)
        lay.addWidget(self.head)
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 4, 4, 6)
        fl.addWidget(content)
        self.body = frame
        lay.addWidget(frame)
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
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        lay.addWidget(scroll)
        self.show_images([])

    def show_images(self, paths):
        self.paths = [p for p in paths if p and os.path.exists(p)]
        while self.inner.count():
            it = self.inner.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not self.paths:
            note = QLabel(self.empty)
            note.setStyleSheet('color: gray')
            self.inner.addWidget(note)
        for p in self.paths:
            lab = QLabel()
            pm = QPixmap(p)
            lab.setPixmap(pm.scaledToWidth(min(pm.width(), 1400), Qt.SmoothTransformation))
            lab.setToolTip(p)
            self.inner.addWidget(lab)
        self.inner.addStretch(1)
