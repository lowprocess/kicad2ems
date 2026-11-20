#!/usr/bin/env python3
"""
The window's flows run end to end, as a user would, each recorded as an animated
GIF, with every error caught on the way: an exception in a slot, a Qt warning, a
run that failed, an error line in a run's log.

    python tools/gui/flows.py                      # every flow
    python tools/gui/flows.py --only sparams xt    # some (start, sparams,
                                                   # crosstalk, coupling,
                                                   # circuit, settings,
                                                   # filter, runs)
    python tools/gui/flows.py --out build/gui-flows

Writes <out>/<flow>.gif and <out>/flows.md (each flow's steps, its errors and
its time). The window runs offscreen unless --screen (the 3D view needs a
display), with settings and examples of its own under <out>/home: the user's
are not touched. Needs PySide6, pyqtgraph, Pillow; the runs take mom25d, the
line model and ngspice (the circuit flow).
"""
import argparse, os, shutil, sys, time, traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))
WIDTH = 1100            # the GIFs' width, px
SIZE = (1600, 950)      # the window's


class Recorder:
    """A flow's frames (the window grabbed), its steps and its errors."""

    def __init__(self, app, name):
        self.app, self.name = app, name
        self.frames, self.steps, self.errors, self.t0 = [], [], [], time.time()
        self.w = None

    def pump(self, n=10):
        for _ in range(n):
            self.app.processEvents()
            time.sleep(0.02)

    def frame(self, hold=1):
        """The window as it is now, held `hold` frames."""
        if self.w is None:
            return
        self.pump(3)
        from PIL import Image
        img = self.w.grab().toImage()
        ptr = img.constBits()
        im = Image.frombuffer('RGBA', (img.width(), img.height()),
                              bytes(ptr), 'raw', 'BGRA', 0, 1).convert('RGB')
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)),
                       Image.LANCZOS)
        self.frames += [im] * hold

    def step(self, text, hold=3):
        self.steps.append(f"{time.time() - self.t0:5.1f} s  {text}")
        print(f"  {self.name}: {text}", flush=True)
        if self.w is not None:
            self.w.statusBar().showMessage(text)
        self.frame(hold)

    def wait(self, cond, seconds, every=1.0, what=''):
        """Run the event loop until cond() holds (a frame every `every` s);
        False on timeout, said as an error."""
        t, last = time.time(), 0.0
        while not cond():
            if time.time() - t > seconds:
                self.errors.append(f"timeout ({seconds} s) waiting for {what}")
                return False
            self.pump(5)
            if time.time() - last > every:
                self.frame()
                last = time.time()
        return True

    def save(self, out):
        if not self.frames:
            return None
        path = os.path.join(out, f"{self.name}.gif")
        self.frames[0].save(path, save_all=True, append_images=self.frames[1:],
                            duration=400, loop=0, optimize=True)
        return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[1].strip())
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--out', default=os.path.join(REPO, 'build', 'gui-flows'))
    ap.add_argument('--screen', action='store_true',
                    help="on the display (default: offscreen)")
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    home = os.path.join(out, 'home')
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    # a window of its own: not the user's settings, layout or examples
    os.environ['XDG_CONFIG_HOME'] = os.path.join(home, 'config')
    os.environ['XDG_DATA_HOME'] = os.path.join(home, 'data')
    if not a.screen:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import qInstallMessageHandler
    from kicad2ems.gui import application
    app = application()
    rec = [None]
    qt_noise = ('propagateSizeHints', 'QOpenGLWidget is not supported',
                'This plugin does not support')

    def qt_message(mode, ctx, msg):
        if rec[0] is not None and not any(n in msg for n in qt_noise):
            rec[0].errors.append(f"Qt: {msg}")
    qInstallMessageHandler(qt_message)

    def hook(etype, value, tb):
        text = ''.join(traceback.format_exception(etype, value, tb))
        sys.__stderr__.write(text)
        if rec[0] is not None:
            rec[0].errors.append(f"exception: {etype.__name__}: {value}\n"
                                 + text[-1500:])
    sys.excepthook = hook

    flows = dict(start=flow_start, sparams=flow_sparams,
                 crosstalk=flow_crosstalk, coupling=flow_coupling,
                 circuit=flow_circuit, settings=flow_settings,
                 filter=flow_filter, runs=flow_runs)
    results = []
    for name, fn in flows.items():
        if a.only and name not in a.only:
            continue
        print(f"flow {name}", flush=True)
        r = rec[0] = Recorder(app, name)
        try:
            fn(r, home)
        except Exception as e:  # a flow's own fault: said, the others go on
            r.errors.append(f"flow stopped: {type(e).__name__}: {e}\n"
                            + traceback.format_exc()[-1500:])
        finally:
            if r.w is not None:
                if r.w.busy():
                    r.w.stop()
                    r.wait(lambda: not r.w.busy(), 30, what='the stop')
                r.w.setup.dirty = False
                r.w.close()
                r.pump(10)
        gif = r.save(out)
        results.append((name, r, gif, time.time() - r.t0))
        print(f"  -> {gif} ({len(r.frames)} frames), {len(r.errors)} "
              "error(s)", flush=True)
        rec[0] = None
    report(out, results)
    return 1 if any(r.errors for _, r, _, _ in results) else 0


def report(out, results):
    L = ["# The window's flows", ""]
    for name, r, gif, sec in results:
        L += [f"## {name}: {'OK' if not r.errors else f'{len(r.errors)} error(s)'}"
              f" ({sec:.0f} s)", "", f"![{name}]({os.path.basename(gif or '')})",
              "", "```", *r.steps, "```", ""]
        for e in r.errors:
            L += ["- **error**: " + e.replace('\n', '\n  '), ""]
    path = os.path.join(out, 'flows.md')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L))
    print(f"-> {path}")


# ---- the flows -----------------------------------------------------------
def _window(r, config=None):
    from kicad2ems.gui.app import MainWindow
    w = r.w = MainWindow(config)
    w.show()
    r.pump(5)
    w.showNormal()
    w.resize(*SIZE)
    r.pump(20)
    return w


def _example(name, home):
    from kicad2ems import examples
    return examples.prepare_example(name, os.path.join(home, 'examples'))


def _run(r, w, what, seconds=600):
    """The toolbar's Run, followed to its end: its failures are errors."""
    w.run()
    r.step(f"Run: {what}")
    ok = r.wait(lambda: not w.busy(), seconds, every=1.5,
                what=f"the run of {what}")
    r.pump(20)
    log = w.runtab.logs.currentWidget()
    bar = log.verticalScrollBar()
    if bar.maximum() > 0 and bar.value() < bar.maximum():
        r.errors.append(f"the Run page's log of {what} is not at its end "
                        f"({bar.value()} of {bar.maximum()})")
    for k in list(getattr(w.study, 'states', {}) or {}):
        st = w.study.state(k)
        if st and ('fail' in st or 'not started' in st or 'not run' in st):
            r.errors.append(f"run {k}: {st}")
    return ok


def _log_errors(r, folder):
    """Error lines in a run folder's logs."""
    import glob
    for f in glob.glob(os.path.join(folder or '', '**', '*.log'),
                       recursive=True):
        with open(f, errors='replace') as fh:
            for line in fh:
                if ('Traceback' in line or line.startswith('ERROR')
                        or ' Error:' in line):
                    r.errors.append(f"log {os.path.basename(f)}: "
                                    f"{line.strip()[:200]}")


def flow_start(r, home):
    """No board: the Start page and its tiles; an example opened: its board,
    its preview and the plan."""
    w = _window(r)
    th = w.start_page._thread  # (the tiles' pictures: drawn in a thread)
    r.wait(lambda: th is None or not th.isRunning(), 300, what="the tiles")
    r.step("the Start page: the examples as tiles", 5)
    cfg = _example('rf_coupler', home)
    w.setup.load(cfg)
    r.step("rf_coupler opened: its preview runs")
    r.wait(lambda: not w.busy(), 300, what="the preview")
    w.show_panel('Viewer')
    w.viewer.set_view('Plan')
    r.step("the board from above, the line model over it", 5)


def flow_sparams(r, home):
    """demo_lowpass: S-parameters by mom25d and the line model, followed on the
    Run page, then the plots and the report."""
    w = _window(r, _example('demo_lowpass', home))
    r.wait(lambda: not w.busy(), 300, what="the preview")
    r.step("demo_lowpass opened (its preview done)")
    for name, box in w.setup.solver_boxes.items():
        box.setChecked(name in ('mom25d', 'lines', 'qucs'))
    w.study.set_ticked(['rf'])
    r.step("solvers ticked: mom25d and the line model")
    w.show_panel('Run')
    _run(r, w, "S-parameters", 600)
    w.show_panel('Results')
    r.step("the S-parameters, both solvers", 5)
    w.show_panel('Report')
    r.step("the run's report", 4)
    _log_errors(r, getattr(w.current, 'out', None))


def flow_crosstalk(r, home):
    """si_bus: the crosstalk question (line model), its report and curves."""
    w = _window(r, _example('si_bus', home))
    r.wait(lambda: not w.busy(), 300, what="the preview")
    w.study.set_ticked(['crosstalk'])
    r.step("si_bus: Crosstalk asked")
    w.show_panel('Run')
    _run(r, w, "crosstalk", 600)
    w.show_result()
    r.step("its report", 5)
    w.show_panel('Results')
    r.step("its curves", 4)


def flow_coupling(r, home):
    """si_ddr: board coupling on the line model (its engine chosen in the list),
    its report with the per-layer figures."""
    w = _window(r, _example('si_ddr', home))
    r.wait(lambda: not w.busy(), 300, what="the preview")
    w.study.set_ticked(['coupling'])
    # (after the tick: ticking the question takes its default engine)
    w.study.choose('coupling', 'coupling_lines', True)
    by = w.study.rows['coupling'].text(1)
    if 'line' not in by:
        r.errors.append(f"Board coupling's engine: {by!r}, not the line model")
    r.step("si_ddr: Board coupling, by the line model")
    w.show_panel('Run')
    _run(r, w, "board coupling", 600)
    w.show_result()
    r.step("its report: each layer's victims", 6)


def flow_circuit(r, home):
    """A buck converter's circuit on ngspice, from its example."""
    w = _window(r, _example('circuit_buck', home))
    r.wait(lambda: not w.busy(), 300, what="the preview")
    r.step("circuit_buck opened: its schematic")
    w.show_panel('Run')
    _run(r, w, "the circuit", 900)
    w.show_result()
    r.step("its report and waveforms", 6)


def flow_settings(r, home):
    """A setting typed, undone, redone, the config saved and opened again."""
    cfg = _example('rf_coupler', home)
    w = _window(r, cfg)
    r.wait(lambda: not w.busy(), 300, what="the preview")
    w.setup.focus('fmax')
    r.step("the settings: Frequency")
    edit = w.setup.edits['fmax'][0]
    before = edit.text()
    edit.setText('7.5')
    r.pump(60)  # (typing rests: a step for undo)
    r.step(f"fmax {before} -> 7.5: unsaved ({w.windowTitle()})")
    if not w.setup.undo() or edit.text() != before:
        r.errors.append(f"undo: fmax {edit.text()!r}, not {before!r}")
    r.step("Edit > Undo")
    if not w.setup.redo() or edit.text() != '7.5':
        r.errors.append(f"redo: fmax {edit.text()!r}, not '7.5'")
    r.step("Edit > Redo")
    saved = os.path.join(home, 'saved.toml')
    if not w.setup._write(saved):
        r.errors.append("save failed")
    w.setup.load(saved)
    r.pump(20)
    if w.setup.edits['fmax'][0].text() not in ('7.5', '7.5000'):
        r.errors.append(f"after saving and opening: fmax "
                        f"{w.setup.edits['fmax'][0].text()!r}")
    r.step("saved, and opened again: fmax 7.5", 4)


def flow_filter(r, home):
    """Filter Synthesis: a hairpin band-pass designed, its layout and model
    response shown."""
    from kicad2ems.gui import filtertab as ft
    from kicad2ems.dfsynth.cli import main as dfsynth
    base = os.path.join(home, 'filter', 'hairpin_2g4')
    os.makedirs(os.path.dirname(base), exist_ok=True)
    args = dict(type='bandpass', topology='hairpin', f0='2.4G', bw='240M',
                substrate='RO4003C-0.813')
    w = _window(r)
    w.show_panel('Filter')
    w.filter.set_values(dict(args, out=base))
    r.step("Filter Synthesis: a 2.4 GHz hairpin band-pass")
    code = dfsynth([x for k, v in args.items() for x in (f'--{k}', v)]
                   + ['-o', base])
    if code:
        r.errors.append(f"dfsynth exited {code}")
    w.filter.show_result(ft.load_design(base))
    r.step("its layout and its line model's response", 6)


def flow_runs(r, home):
    """The Runs page after the flows before it: this board's runs, one shown,
    another laid over it."""
    cfg = _example('demo_lowpass', home)
    w = _window(r, cfg)
    r.wait(lambda: not w.busy(), 300, what="the preview")
    w.show_panel('Runs')
    w.refresh_runs()
    r.step("the Runs page: demo_lowpass's runs", 5)
    rp = w.runs_panel
    items = list(rp._items())
    if not items:
        r.errors.append("the Runs page lists no run of demo_lowpass")
        return
    rp.tree.setCurrentItem(items[0])
    items[0].setSelected(True)
    r.pump(30)
    r.step("a run clicked: shown in the pages", 4)
    if len(items) > 1:
        items[1].setSelected(True)
        r.pump(10)
        if rp.cmp_btn.isEnabled():
            rp.cmp_btn.click()
            r.pump(30)
            w.show_panel('Results')
            r.step("two runs selected, compared on the plots", 5)
        else:
            r.errors.append("two runs selected: Compare not enabled")


if __name__ == '__main__':
    sys.exit(main())
