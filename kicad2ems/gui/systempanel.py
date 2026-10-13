"""
The Solvers window (Help > Solvers and hardware, or ? beside the Solvers settings): the solvers this machine can
run, the GPU mom25d would use, the memory of the system and of the GPU (kicad2ems.sysinfo, run as its own process
with the interpreter the runs use), and after a preview what each full-wave solver would need on that board
(summary['estimates'] of <name>_run.json) against it.
"""
import glob, json, os, sys
from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QProgressBar, QGroupBox, QFormLayout, QHeaderView)

OK, NO = '✔', '✖'


def _bar(used, total):
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setValue(int(1000 * used / total) if total else 0)
    b.setFormat(f"{used:.1f} of {total:.1f} GB in use" if total else 'unknown')
    b.setMaximumHeight(16)
    return b


def fit_note(need, have):
    """'fits' / 'tight' / 'does not fit' of `need` GB against `have` GB available (the solvers keep 20 % spare)."""
    if not need or have is None:
        return ''
    return 'fits' if need <= 0.8 * have else ('tight' if need <= have else 'does not fit')


class SystemPanel(QWidget):
    ready = Signal(dict)                                            # the machine's info, when its check is done

    def __init__(self):
        super().__init__()
        self.info, self.est, self.proc = None, None, None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.state = QLabel('checking the solvers and the GPU…')
        self.state.setStyleSheet('color: gray')
        top.addWidget(self.state, 1)
        self.refresh_btn = QPushButton('Refresh')
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)
        box = QGroupBox('Solvers')
        bl = QVBoxLayout(box)
        self.solvers = QTreeWidget()
        self.solvers.setHeaderLabels(['', 'solver', 'details'])
        self.solvers.setRootIsDecorated(False)
        self.solvers.header().setSectionResizeMode(2, QHeaderView.Stretch)
        bl.addWidget(self.solvers)
        lay.addWidget(box)
        box = QGroupBox('Hardware')
        self.hw = QFormLayout(box)
        lay.addWidget(box)
        box = QGroupBox('Estimate (the last preview)')
        el = QVBoxLayout(box)
        self.est_note = QLabel('Preview a board: what each full-wave solver needs on its mesh appears here')
        self.est_note.setWordWrap(True)
        self.est_note.setStyleSheet('color: gray')
        el.addWidget(self.est_note)
        self.est_tree = QTreeWidget()
        self.est_tree.setHeaderLabels(['solver', 'time', 'memory', 'GPU memory', 'how', 'model'])
        self.est_tree.setRootIsDecorated(False)
        self.est_tree.header().setSectionResizeMode(5, QHeaderView.Stretch)
        el.addWidget(self.est_tree)
        lay.addWidget(box, 1)
        self.refresh()

    # ---- the machine ----
    def refresh(self):
        if self.proc is not None:
            return
        self.state.setText('checking the solvers and the GPU…')
        self.refresh_btn.setEnabled(False)
        self.proc = QProcess(self)
        self.proc.finished.connect(self._done)
        self.proc.start(sys.executable, ['-m', 'kicad2ems.sysinfo'])

    def stop(self):
        """End a check still going (the window closes)."""
        if self.proc is not None:
            self.proc.finished.disconnect()
            self.proc.kill()
            self.proc.waitForFinished(2000)
            self.proc = None

    def _done(self, code, _status):
        out = bytes(self.proc.readAllStandardOutput()).decode('utf-8', 'replace')
        err = bytes(self.proc.readAllStandardError()).decode('utf-8', 'replace')
        self.proc = None
        self.refresh_btn.setEnabled(True)
        try:
            self.set_info(json.loads(out))
        except ValueError:
            self.state.setText(f"system check failed (code {code}): {err.strip().splitlines()[-1] if err.strip() else out[:200]}")

    def set_info(self, info):
        self.info = info
        self.state.setText(f"{sum(s['ok'] for s in info['solvers'])} of {len(info['solvers'])} solvers available")
        self.state.setStyleSheet('')
        self.solvers.clear()
        for s in info['solvers']:
            it = QTreeWidgetItem([OK if s['ok'] else NO, s['label'], s['what']])
            it.setForeground(0, Qt.darkGreen if s['ok'] else Qt.red)
            it.setToolTip(2, s['what'])
            self.solvers.addTopLevelItem(it)
        for i in range(2):
            self.solvers.resizeColumnToContents(i)
        self.ready.emit(info)
        rows = max(1, self.solvers.topLevelItemCount())
        self.solvers.setFixedHeight(self.solvers.header().height() + rows * self.solvers.sizeHintForRow(0) + 6)
        while self.hw.rowCount():
            self.hw.removeRow(0)
        ram, g = info['ram'], info.get('gpu')
        self.hw.addRow('CPU', QLabel(f"{info.get('cpus') or '?'} cores"))
        if ram.get('total'):
            avail = ram.get('available')
            self.hw.addRow('Memory', _bar(ram['total'] - (avail if avail is not None else 0), ram['total']))
            self.hw.addRow('', QLabel(f"{avail:.1f} GB available" if avail is not None else ''))
        if g:
            self.hw.addRow('GPU', QLabel(f"{OK} {g['device']} ({g['backend']}, {g['api']})"))
            if g.get('total'):
                self.hw.addRow('GPU memory', _bar(g['total'] - g['free'], g['total']))
            self.hw.addRow('', QLabel(f"{g['free']:.1f} GB free: mom25d's complex64 LU runs there"))
        else:
            lab = QLabel(f"{NO} none: mom25d solves on the CPU ({info.get('gpu_why') or 'no GPU backend'}). For a GPU, "
                         f"install CuPy into this Python ({sys.executable}): pip install cupy-rocm-7-0 (AMD, ROCm 7), "
                         f"cupy-cuda12x or cupy-cuda13x (NVIDIA)")
            lab.setWordWrap(True)
            self.hw.addRow('GPU', lab)
        self.show_estimates(self.est)

    # ---- a preview's estimates ----
    def load_estimates(self, out_dir):
        """The estimates of the newest <name>_run.json in out_dir (a preview's); False when there are none."""
        runs = sorted(glob.glob(os.path.join(out_dir, '*_run.json')), key=os.path.getmtime)
        if not runs:
            return False
        try:
            with open(runs[-1]) as fh:
                est = json.load(fh).get('estimates')
        except (OSError, ValueError):
            return False
        if not est:
            return False
        self.est = est
        self.show_estimates(est)
        return True

    def show_estimates(self, est):
        self.est_tree.clear()
        if not est or not est.get('solvers'):
            return
        ram = (self.info or {}).get('ram', {}).get('available')
        g = (self.info or {}).get('gpu')
        gfree = g['free'] if g else None
        usable = {s['name']: s['ok'] for s in (self.info or {}).get('solvers', [])}
        from ..solvers import shown
        for n, e in est['solvers'].items():
            if n not in shown():                                    # an experimental solver, hidden
                continue
            sec = e.get('seconds', 0)
            t = f"~{sec:.0f} s" if sec < 120 else (f"~{sec / 60:.0f} min" if sec < 7200 else f"~{sec / 3600:.1f} h")
            mem = f"{e['gb']:.1f} GB" + (f" ({fit_note(e['gb'], ram)})" if ram else '')
            gm = e.get('gpu_gb')
            gtxt = (f"{gm:.1f} GB" + (f" ({fit_note(gm, gfree)})" if gfree else '')) if gm else ''
            how = e.get('how', '') if usable.get(n, True) else 'not available here (the preview only meshes it)'
            it = QTreeWidgetItem([n, t, mem, gtxt, how, e.get('what', '')])
            if ram and e['gb'] > ram:
                it.setForeground(2, Qt.red)
            if not usable.get(n, True):
                for c in range(6):
                    it.setForeground(c, Qt.gray)
            it.setToolTip(5, e.get('what', ''))
            self.est_tree.addTopLevelItem(it)
        for i in range(5):
            self.est_tree.resizeColumnToContents(i)
        self.est_note.setText("On the board's mesh at the current settings; the memory against what is "
                              "available now (20 % kept spare).")
