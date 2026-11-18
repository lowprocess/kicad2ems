"""
The Solvers window (Help > Solvers and hardware, or ? beside the Solvers
settings): the solvers this machine can run, the GPU mom25d would use, the
memory of the system and of the GPU (kicad2ems.core.sysinfo, run as its own
process with the interpreter the runs use), and after a preview what each
full-wave solver would need on that board (summary['estimates'] of
<name>_run.json) against it.
"""
import glob, json, os, sys
from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem,
                               QProgressBar, QGroupBox, QFormLayout,
                               QHeaderView, QLineEdit)
from . import meters

OK, NO = '✔', '✖'


def _bar(used, total):
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setValue(int(1000 * used / total) if total else 0)
    b.setFormat(f"{used:.1f} of {total:.1f} GB in use" if total else 'unknown')
    b.setMaximumHeight(16)
    return b


def fit_note(need, have):
    """'fits' / 'tight' / 'does not fit' of `need` GB against `have` GB
    available (the solvers keep 20 % spare)."""
    if not need or have is None:
        return ''
    return ('fits' if need <= 0.8 * have else
            ('tight' if need <= have else 'does not fit'))


def _user_value(key):
    """The user's own site.toml setting of key, or '' (the field shows it, not
    the installer's)."""
    from kicad2ems.core import sitecfg
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    try:
        with open(sitecfg.user_file(), 'rb') as fh:
            return str(tomllib.load(fh).get(key) or '')
    except (OSError, ValueError):
        return ''


class SystemPanel(QWidget):
    ready = Signal(dict)  # the machine's info, when its check is done

    def __init__(self):
        super().__init__()
        self.info, self.est, self.proc = None, None, None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.state = QLabel('checking the solvers and the GPU…',
                            styleSheet='color: gray')
        self.refresh_btn = QPushButton('Refresh', clicked=self.refresh)
        top.addWidget(self.state, 1)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)
        box = QGroupBox('Solvers')
        self.solvers = self._tree(QVBoxLayout(box), ['', 'solver', 'details'],
                                  2)
        lay.addWidget(box)
        box = QGroupBox('Programs')  # the machine's, not a board's: site.toml
        pf = QFormLayout(box)
        from kicad2ems.core import sitecfg
        where = sitecfg.user_file()
        # (site.toml key, label, tooltip, placeholder, the folder dialog's
        # title: None for a file's)
        programs = [
            ('qucsator', 'qucsator program',
             "the line model's circuit solver (Qucs-S's qucsator_rf, or "
             "qucsator): empty finds it on the PATH (or where the installer "
             "recorded it)",
             '', None),
            ('openems_venv', 'openEMS environment',
             "a Python environment (venv) with openEMS (CSXCAD and openEMS), "
             "when it is not in kicad2ems's own: its folder, or its python "
             "(bin/python, Scripts\\python.exe). It must be the same Python "
             "version as kicad2ems's",
             "empty: openEMS in kicad2ems's own Python",
             'A Python environment (venv) with openEMS')
        ]
        if sys.platform == 'win32':  # Windows: where openEMS's DLLs are
            programs.append(
                ('openems_dir', 'openEMS folder',
                 "the unpacked openEMS for Windows, the folder with "
                 "openEMS.dll and CSXCAD.dll: its DLLs go on the DLL search",
                 'empty: OPENEMS_INSTALL_PATH, or the DLLs already on the PATH',
                 'The openEMS folder (openEMS.dll, CSXCAD.dll)'))
        self.openems_dir, self._programs = None, {}
        for key, label, tip, empty, folder in programs:
            field = QLineEdit(toolTip=f"{tip}; saved in {where}",
                              placeholderText=empty, text=_user_value(key),
                              editingFinished=self._save_programs)
            b = QPushButton(
                '…', clicked=lambda _=False, f=field, t=label, d=folder:
                self._browse(f, t, d))
            b.setFixedWidth(28)
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(b)
            pf.addRow(label, row)
            setattr(self, key, field)
            self._programs[key] = field
        lay.addWidget(box)
        box = QGroupBox('Hardware')
        self.hw = QFormLayout(box)
        lay.addWidget(box)
        box = QGroupBox('Estimate (the last preview)')
        el = QVBoxLayout(box)
        self.est_note = QLabel(
            'Preview a board: what each full-wave solver needs on its mesh '
            'appears here',
            wordWrap=True, styleSheet='color: gray')
        el.addWidget(self.est_note)
        self.est_tree = self._tree(
            el, ['solver', 'time', 'memory', 'GPU memory', 'how', 'model'], 5)
        lay.addWidget(box, 1)
        self.refresh()

    @staticmethod
    def _tree(lay, headers, stretch):
        """A list of rows with these columns, column `stretch` taking the width
        left, added to lay."""
        tree = QTreeWidget(rootIsDecorated=False)
        tree.setHeaderLabels(headers)
        tree.header().setSectionResizeMode(stretch, QHeaderView.Stretch)
        lay.addWidget(tree)
        return tree

    # ---- the programs ----
    def _browse(self, field, title, folder):
        """A program chosen for a field (folder: the title of the folder dialog;
        None: a file's, titled `title`)."""
        from PySide6.QtWidgets import QFileDialog
        if folder is None:
            path, _ = QFileDialog.getOpenFileName(
                self, title, (field.text() or
                              ('' if sys.platform == 'win32' else '/usr/bin')))
        else:
            path = QFileDialog.getExistingDirectory(
                self, folder, (field.text() or os.path.expanduser('~')))
        if path:
            field.setText(path)
            self._save_programs()

    def _save_programs(self):
        """The programs into the user's site.toml (sitecfg), then the solvers
        checked again."""
        from kicad2ems.core import sitecfg
        changed = False
        for key, field in self._programs.items():
            path = field.text().strip()
            if path != _user_value(key):
                sitecfg.set_user(key, path)
                changed = True
        if changed:
            self.refresh()

    # ---- the machine ----
    def refresh(self):
        if self.proc is not None:
            return
        self.state.setText('checking the solvers and the GPU…')
        self.refresh_btn.setEnabled(False)
        self.proc = QProcess(self)
        self.proc.finished.connect(self._done)
        self.proc.start(sys.executable, ['-m', 'kicad2ems.core.sysinfo'])

    def stop(self):
        """End a check still going (the window closes)."""
        if self.proc is not None:
            self.proc.finished.disconnect()
            self.proc.kill()
            self.proc.waitForFinished(2000)
            self.proc = None

    def _done(self, code, _status):
        out = bytes(self.proc.readAllStandardOutput()).decode(
            'utf-8', 'replace')
        err = bytes(self.proc.readAllStandardError()).decode('utf-8', 'replace')
        self.proc = None
        self.refresh_btn.setEnabled(True)
        try:
            from kicad2ems.core import formats
            d = json.loads(out)
            why = formats.check(d, 'kicad2ems-sysinfo')
            if why:
                self.state.setText(f"system check: {why}")
                return
            self.set_info(d)
        except ValueError:
            last = err.strip().splitlines()[-1] if err.strip() else out[:200]
            self.state.setText(f"system check failed (code {code}): {last}")

    def set_info(self, info):
        self.info = info
        self.state.setText(
            f"{sum(s['ok'] for s in info['solvers'])} of "
            f"{len(info['solvers'])} solvers available"
        )
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
        self.solvers.setFixedHeight(self.solvers.header().height()
                                    + rows * self.solvers.sizeHintForRow(0) + 6)
        while self.hw.rowCount():
            self.hw.removeRow(0)
        ram, g = info['ram'], info.get('gpu')
        self.hw.addRow('CPU', QLabel(f"{info.get('cpus') or '?'} cores"))
        if ram.get('total'):
            avail = ram.get('available')
            self.hw.addRow(
                'Memory',
                _bar(ram['total'] - (avail if avail is not None else 0),
                     ram['total']))
            self.hw.addRow(
                '',
                QLabel(
                    f"{avail:.1f} GB available" if avail is not None else ''))
        if g:
            self.hw.addRow(
                'GPU',
                QLabel(f"{OK} {g['device']} ({g['backend']}, {g['api']})"))
            if g.get('total'):
                self.hw.addRow('GPU memory',
                               _bar(g['total'] - g['free'], g['total']))
            self.hw.addRow(
                '',
                QLabel(
                    f"{g['free']:.1f} GB free: mom25d's complex64 LU runs there"
                ))
        else:
            why = info.get('gpu_why') or 'no GPU backend'
            self.hw.addRow(
                'GPU',
                QLabel(
                    f"{NO} none: mom25d solves on the CPU ({why}). For a GPU, "
                    f"install CuPy into this Python ({sys.executable}): pip "
                    "install cupy-rocm-7-0 (AMD, ROCm 7), cupy-cuda12x or "
                    "cupy-cuda13x (NVIDIA)", wordWrap=True))
        self.show_estimates(self.est)

    # ---- a preview's estimates ----
    def load_estimates(self, out_dir):
        """The estimates of the newest <name>_run.json in out_dir (a preview's);
        False when there are none."""
        runs = sorted(glob.glob(os.path.join(out_dir, '*_run.json')),
                      key=os.path.getmtime)
        if not runs:
            return False
        try:
            from kicad2ems.core.runs import read_summary
            est = read_summary(runs[-1]).get('estimates')
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
        usable = {
            s['name']: s['ok']
            for s in (self.info or {}).get('solvers', [])
        }
        for n, e in est['solvers'].items():
            mem = (f"{e['gb']:.1f} GB" +
                   (f" ({fit_note(e['gb'], ram)})" if ram else ''))
            gm = e.get('gpu_gb')
            gtxt = ((f"{gm:.1f} GB" +
                     (f" ({fit_note(gm, gfree)})" if gfree else ''))
                    if gm else '')
            how = (e.get('how', '') if usable.get(n, True) else
                   'not available here (the preview only meshes it)')
            it = QTreeWidgetItem([
                n,
                meters.duration(e.get('seconds', 0)), mem, gtxt, how,
                e.get('what', '')
            ])
            if ram and e['gb'] > ram:
                it.setForeground(2, Qt.red)
            if not usable.get(n, True):
                for c in range(6):
                    it.setForeground(c, Qt.gray)
            it.setToolTip(5, e.get('what', ''))
            self.est_tree.addTopLevelItem(it)
        for i in range(5):
            self.est_tree.resizeColumnToContents(i)
        self.est_note.setText(
            "On the board's mesh at the current settings; the memory against "
            "what is available now (20 % kept spare).")
