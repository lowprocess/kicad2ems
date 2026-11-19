"""
The study's runs in progress with no window: the queue of a Run (the Steps of
kicad2ems's plan, StudyModel.steps), the one going, what failed, and each
run's state as the list and the Run page say it.

    RunQueue          one Run's queue: start(steps) fills it, next() gives
                      the run to start (one whose `needs` did not finish is
                      passed over, said), began / not_started / ended say what
                      became of it, stop() empties it

Starting a run is the caller's (the window's launcher, RunProcess, or a
program's own): this says which, and keeps the states. Every state change
calls `on_state(key, text, bad, seconds)`. Nothing here needs Qt.
"""
import time

from kicad2ems.core.study import unmet


class RunQueue:
    """One Run's runs, one after the other:

        queue     the runs still to start, in order ('rf', or an analysis's)
        job       the run going (None: none)
        steps     {run: its Step of the plan}
        preview   a preview's queue
        stopped   Stop was pressed: what ends now ended stopped, not failed
        failed    the runs that did not finish (a later run that needs one is
                  not started)"""

    def __init__(self, on_state=None, title=str):
        self.on_state, self.title = on_state, title
        self.queue, self.job, self.steps = [], None, {}
        self.preview, self.stopped, self.failed = False, False, set()
        self.t0 = 0.0

    def _say(self, key, text, bad=False, seconds=None):
        if self.on_state is not None:
            self.on_state(key, text, bad, seconds)

    def start(self, steps, preview=False, key_of=None):
        """A new Run's queue: steps of the plan (core.study.plan, as
        StudyModel.steps gives them), each a run by key_of(step) (study.key_of
        by default). Returns the runs, in order, each said 'queued'."""
        if key_of is None:
            from .study import key_of
        self.steps = {key_of(st): st for st in steps}
        self.queue, self.preview, self.stopped = list(self.steps), preview, False
        self.failed, self.job = set(), None
        return list(self.queue)

    def next(self):
        """The next run to start, now `job` (began: it started; not_started:
        it could not), or None: none left, or one is going. A run whose
        analysis needs one that did not finish is not run, and said so."""
        if self.job is not None:
            return None
        while self.queue:
            key = self.queue.pop(0)
            st = self.steps.get(key)
            need = (unmet(st.engines[0], self.failed)
                    if st is not None and st.kind == 'analysis' else [])
            if need:
                self.failed.add(key)
                self._say(key, "not run: " + ', '.join(map(self.title, need))
                          + ' did not finish', bad=True)
                continue
            self.job, self.t0 = key, time.monotonic()
            return key
        return None

    def began(self, key):
        """The run `key` (as next() gave it) started."""
        self._say(key, 'running')

    def not_started(self, key, why):
        """The run `key` could not start: why (a setting missing, a fault)."""
        self.failed.add(key)
        self.job = None
        self._say(key, f"not started: {why}", bad=True)

    def ended(self, key, code):
        """The run going ended with `code` (0: it finished). Returns its
        seconds, or None when `key` is not the run going."""
        if self.job != key or key is None:
            return None
        took = time.monotonic() - self.t0
        if code == 0:
            self._say(key, f"done {time.strftime('%H:%M')}", seconds=took)
        else:
            self.failed.add(key)
            self._say(key, 'stopped' if self.stopped
                      else f"failed (exit {code})", bad=not self.stopped,
                      seconds=took)
        self.job = None
        return took

    def stop(self):
        """Stop pressed: what is queued is not run; the run going, if any,
        ends stopped. Returns whether one is going."""
        for k in self.queue:
            self._say(k, 'not run: stopped')
        self.queue = []
        if self.job is not None:
            self.stopped = True
        return self.job is not None
