"""Running slow work (loading a board, a study) off the GUI thread."""
import traceback

from PySide6.QtCore import QObject, QThread, Signal

from ..study import Cancelled


class Task(QObject):
    """Runs fn(progress, cancelled) in a thread; progress(fraction, text)
    and the result come back as signals."""
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn, self._stop = fn, False

    def cancel(self):
        self._stop = True

    def run(self):
        try:
            out = self.fn(lambda f, t: self.progress.emit(float(f), str(t)), lambda: self._stop)
            self.done.emit(out)
        except Cancelled:
            self.failed.emit('cancelled')
        except Exception as exc:                       # shown to the user, not raised
            self.failed.emit(f'{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}')


def start(task, parent):
    """Start `task` in a new QThread owned by `parent`; returns the thread."""
    th = QThread(parent)
    task.moveToThread(th)
    th.started.connect(task.run)
    task.done.connect(th.quit)
    task.failed.connect(th.quit)
    th.finished.connect(th.deleteLater)
    th.start()
    return th
