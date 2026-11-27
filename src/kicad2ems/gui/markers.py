"""
What the Results page's two plots share (splot.SPlot, the S-parameters;
curveplot.CurvePlot, an analysis's curves): the buttons over them and the
table of their markers' values under them.

    plot_buttons(bar, fit, clear, export, save, tips)  # Fit, Clear markers,
                                                       # Export CSV, Save image
    table = MarkerTable()        # a row a marker; Delete: removed(rows)
    table.fill(head, rows)       # the markers' values, as tall as its rows
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QPushButton, QLabel, QTableWidget,
                               QTableWidgetItem, QHeaderView,
                               QAbstractItemView)

HINT = 'click: marker · drag a marker to move it · wheel / drag: zoom, pan'


def plot_buttons(bar, fit, clear, export, save, tips):
    """The four buttons (tips: theirs, in order) and the mouse's hint, added
    to the layout bar."""
    for text, slot, tip in zip(
            ('Fit', 'Clear markers', 'Export CSV…', 'Save image…'),
            (fit, clear, export, save), tips):
        bar.addWidget(QPushButton(text, toolTip=tip,
                                  clicked=lambda _=False, f=slot: f()))
    bar.addStretch(1)
    # (wrapped: one line had set the plot pages' least width, and with CI's
    # font the window's past its screen)
    bar.addWidget(QLabel(HINT, styleSheet='color: gray', wordWrap=True), 1)


class MarkerTable(QTableWidget):
    """The markers' values: a row a marker, read only; Delete (or Backspace)
    asks for the selected rows to go (removed: their indices, the last
    first). Hidden while there are none; as tall as its rows, up to five."""
    removed = Signal(list)

    def __init__(self, parent=None, **props):
        super().__init__(0, 0, parent,
                         editTriggers=QAbstractItemView.NoEditTriggers,
                         selectionBehavior=QAbstractItemView.SelectRows,
                         **props)
        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.removed.emit(sorted({i.row() for i in self.selectedIndexes()},
                                     reverse=True))
        else:
            super().keyPressEvent(ev)

    def fill(self, head, rows, names=None):
        """Columns head, a row of texts a marker (names: their row
        headers)."""
        self.setColumnCount(len(head))
        self.setHorizontalHeaderLabels(head)
        self.setRowCount(len(rows))
        if names:
            self.setVerticalHeaderLabels(names)
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.setItem(r, c, QTableWidgetItem(text))
        self.setVisible(bool(rows))
        if rows:
            h = (self.horizontalHeader().height() + min(len(rows), 5)
                 * self.verticalHeader().defaultSectionSize())
            sb = (self.horizontalScrollBar().sizeHint().height()
                  if self.horizontalScrollBar().isVisible() else 0)
            self.setFixedHeight(h + sb + 2 * self.frameWidth() + 2)

    def values(self):
        """The table as it reads: [[cell, ...]] a row."""
        return [[
            self.item(r, c).text() if self.item(r, c) else ''
            for c in range(self.columnCount())
        ] for r in range(self.rowCount())]
