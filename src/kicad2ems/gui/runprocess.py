"""
The one launcher of the window's runs: kicad2ems's own (its events followed)
or an analysis's tool (its output is its log), commands one after the other,
for the study's queue and for an analysis's panel alone:

    p = RunProcess(parent)
    p.start([Command(...), make, 'why one cannot start', ...])
        # '' once one is going, else why none could; make: a function that
        # makes its Command (or says why not) when its turn comes
    signals: began(cmd), output(cmd, text), ended(cmd, code, outcome),
             skipped(why), finished(code: 0, or the first failure)
    p.stop()   # the rest not run, the one going asked to stop (kicad2ems:
               # STOP and ABORT; a tool: SIGTERM), ended at a second call
    p.proc, p.cmd, p.tail   # the QProcess and Command going, its events

A run leaves its whole output in its folder (stdout.log) and how it ended in
its mark (core.runs.ended), which whoever made the Command wrote.
"""
import os
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer
from PySide6.QtCore import Signal

from kicad2ems.core.runs import EventTail, ended, outcome, stop_runs


@dataclass
class Command:
    """One run's command line, and what it is of."""
    key: str             # the run: 'rf' (kicad2ems's own), an analysis's name
    argv: list
    folder: str          # its run folder (marked)
    events: str = ''     # its events file: kicad2ems's own run
    env: dict = field(default_factory=dict)
    preview: bool = False
    label: str = ''      # one of several (a table's rows): its own name
    values: dict = None  # the settings it runs with


class RunProcess(QObject):
    """Commands run one after the other, one process at a time."""
    began = Signal(object)
    output = Signal(object, str)
    ended = Signal(object, int, str)
    skipped = Signal(str)
    finished = Signal(int)
    # how long a tool has to stop what it started before it is killed
    STOP_GRACE_MS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = self.cmd = self.last = self.tail = self._log = None
        self._todo, self._code, self._why = [], 0, ''
        self._stopping = False

    def busy(self):
        return self.proc is not None

    def start(self, items):
        """Run these one after the other (a str: one that cannot start, said
        by `skipped` and counted a failure); '' once one is going, else why
        none could start (then no `finished`)."""
        if self.proc is not None:
            return 'A run is going: stop it first'
        self._todo, self._code, self._why = list(items), 0, ''
        self._stopping = False
        return '' if self._next(first=True) else (self._why or 'nothing to run')

    def _next(self, first=False):
        while self._todo and not self._stopping:
            item = self._todo.pop(0)
            item = item() if callable(item) else item
            if isinstance(item, Command):
                self._launch(item)
                return True
            self._code, self._why = self._code or 1, str(item)
            self.skipped.emit(str(item))
        self._todo = []
        if not first:
            self.finished.emit(self._code)
        return False

    def _launch(self, cmd):
        self.cmd = self.last = cmd
        os.makedirs(cmd.folder, exist_ok=True)
        try:
            self._log = open(os.path.join(cmd.folder, 'stdout.log'), 'a',
                             encoding='utf-8')
        except OSError:
            self._log = None
        self.tail = EventTail(cmd.events) if cmd.events else None
        p = self.proc = QProcess(self)
        p.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in {'PYTHONUNBUFFERED': '1', **(cmd.env or {})}.items():
            env.insert(k, str(v))
        p.setProcessEnvironment(env)
        if cmd.events:
            # in its folder: started where the GUI was, the run could import a
            # folder there named like one of its packages (a mom25d/ of the
            # user's) in place of the package
            p.setWorkingDirectory(cmd.folder)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._error)  # (FailedToStart: no finished)
        self.began.emit(cmd)
        p.start(cmd.argv[0], cmd.argv[1:])

    def _read(self):
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode(
            'utf-8', 'replace')
        if data and self._log is not None:
            self._log.write(data)
            self._log.flush()
        if data:
            self.output.emit(self.cmd, data)

    def _error(self, err):
        if err == QProcess.FailedToStart and self.proc is not None:
            text = f"the run could not start: {self.proc.errorString()}\n"
            if self._log is not None:
                self._log.write(text)
            self.output.emit(self.cmd, text)
            self._done(-1)

    def _done(self, code, status=None):
        if self.proc is None:
            return
        self._read()
        proc, cmd = self.proc, self.cmd
        how = outcome(code, self._stopping,
                      status == QProcess.CrashExit and not self._stopping)
        if self._log is not None:
            self._log.close()
        self._log = self.proc = self.cmd = None
        proc.deleteLater()
        ended(cmd.folder, how)
        self._code = self._code or code or (0 if how == 'done' else 1)
        self.ended.emit(cmd, code, how)
        self._next()

    def stop(self):
        """The rest not run; the one going asked to stop, ended at a second
        call. Returns a line for its log ('' none)."""
        self._todo = []
        if self.proc is None:
            return ''
        if self._stopping:
            self.proc.kill()
            return 'stop: ending the process'
        self._stopping = True
        if self.cmd.events:
            stop_runs(self.cmd.folder)
            return ("stop: requested - the run ends after its current step "
                    "(press Stop again to end the process at once)")
        proc = self.proc
        proc.terminate()

        def kill():
            try:
                if proc.state() != QProcess.NotRunning:
                    proc.kill()
            except RuntimeError:  # (ended and deleted meanwhile)
                pass
        QTimer.singleShot(self.STOP_GRACE_MS, kill)
        return ''

    def kill(self, wait_ms=0):
        """The rest not run, the one going ended now (its end awaited up to
        wait_ms: the window closes)."""
        self._todo = []
        if self.proc is not None:
            self._stopping = True
            proc = self.proc
            proc.kill()
            if wait_ms:
                proc.waitForFinished(wait_ms)
