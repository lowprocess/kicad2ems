"""
The main window's small parts (app.py): the Solvers window, the second Viewer,
a page's dock, and the threads that make an example and read a board's layout
off the event loop.
"""
import os
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel, QDockWidget, QDialog)
from .viewer import Viewer


class SolversDialog(QDialog):
    """Help > Solvers and hardware: the solvers this machine can run (✔ / ✖ and
    why), the GPU, the memory, and the last preview's estimates."""
    def __init__(self, win, panel):
        super().__init__(win)
        self.setWindowTitle('Solvers and hardware - kicad2ems')
        self.setModal(False)
        lay = QVBoxLayout(self)
        lay.addWidget(panel)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(QPushButton('Close', clicked=self.close))
        lay.addLayout(row)
        self.resize(780, 660)


class _Preparing(QThread):
    """prepare_example in a thread: done(path), with .error set when it
    failed."""
    done = Signal(str)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name, self.error = name, ''

    def run(self):
        from kicad2ems.core.api.frontend import prepare_example
        try:
            path = prepare_example(self.name)
        # (said by the window)
        except Exception as e:  # noqa: BLE001
            path, self.error = '', f"{type(e).__name__}: {e}"
        self.done.emit(path)


class ViewerWindow(QWidget):
    """A second Viewer, its own window: another run of the board beside the one
    the main Viewer shows (a drop map against the last one's, an S-parameter
    run's fields against another's). Its run is the second one picked on the
    Runs page (Ctrl and a click); opened alone, the run before the one
    shown."""
    def __init__(self, win):
        super().__init__(None, Qt.Window)
        self.win, self.folder = win, None
        self.setWindowTitle('Viewer - kicad2ems')
        lay = QVBoxLayout(self)
        self.which = QLabel(
            wordWrap=True, toolTip='pick another on the Runs page: Ctrl and a '
            'click on its row')
        lay.addWidget(self.which)
        self.viewer = Viewer(win)
        lay.addWidget(self.viewer, 1)
        self.resize(1100, 760)

    def refresh(self, folder=None):
        """folder shown (None: the one shown here, else the run listed after
        the one the main Viewer shows)."""
        folder = folder or self.folder
        if not folder or not os.path.isdir(folder):
            runs = [r.folder for r in self.win.runs_panel.runs()
                    if not r.preview]
            here = self.win.shown
            folder = next((runs[k + 1] for k in range(len(runs) - 1)
                           if runs[k] == here), None) or next(
                               (d for d in runs if d != here), None)
        if folder:
            self.show_run(folder)

    def show_run(self, folder):
        if folder and os.path.isdir(folder):
            self.folder = os.path.abspath(folder)
            self.viewer.load(folder)
            if (self.win.viewer.view() in self.viewer.buttons and
                    self.viewer.buttons[self.win.viewer.view()].isEnabled()):
                # the same view as the main one
                self.viewer.set_view(self.win.viewer.view())
            here = self.win.viewer.cuts.src
            if here is not None:  # and the same dump, when it has it
                self.viewer.show_dump(here.label)
            info = self.win.runs_panel.info(folder)
            text = (info.label() if info is not None else
                    os.path.basename(os.path.normpath(folder)))
            self.which.setText(f"Second run: <b>{text}</b> &nbsp; "
                               "<span style='color:gray'>(another: Ctrl and a "
                               "click on the Runs page)</span>")
            self.setWindowTitle(f"{text} - Viewer - kicad2ems")


class PageDock(QDockWidget):
    """A dock of the window: a page (the board's Viewer, an editor, Results,
    Run, Filter Synthesis) or one of the study's two panels. It moves, tabs with
    others, floats and closes; View brings it back."""
    def __init__(self, title, name, widget, parent):
        super().__init__(title, parent)
        self.setObjectName(name)  # its name in saved layouts
        self.setWidget(widget)
        # closable: not closable, its View action is disabled, and a layout
        # saved with it hidden (or a floated dock the window manager closed)
        # left no way to open it again
        self.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)

    def in_front(self):
        """In view: shown, and not behind another tab of its group."""
        return not self.visibleRegion().isEmpty()


# layout threads at work, kept until they are done: a QThread destroyed while it
# runs aborts the program
_READING = set()


class _Layout(QThread):
    """A board's layout read off the event loop
    (kicad2ems.core.layout.board_layout: a few seconds on a large board)."""
    done = Signal(str, object)  # the board file, its layout (None: not read)

    def __init__(self, board):
        # no parent: a window closed meanwhile does not take it
        super().__init__()
        self.board = board
        _READING.add(self)
        self.finished.connect(self._over)

    def _over(self):
        _READING.discard(self)

    def run(self):
        try:
            from kicad2ems.core.api.frontend import board_layout
            layout = board_layout(self.board)
        # (a board that cannot be read: no layout)
        except Exception:  # noqa: BLE001
            layout = None
        self.done.emit(self.board, layout)
