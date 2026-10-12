"""
The GUI's antenna view (mode = antenna): input match, gain / directivity / efficiency over frequency, and the
far-field pattern (NF2FF) at any recorded frequency as polar cuts and a 3D pattern. Reads the run's
<name>_antenna.json and <out>/openems/exc_<port>/nf2ff_sphere.h5.
"""
import glob, json, os
import numpy as np
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel, QSplitter, QFileDialog
from PySide6.QtCore import Qt
import pyqtgraph as pg
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from ..solvers.openems.antenna import read_sphere, sphere_gain, sphere_cuts

QUANTITIES = ('Gain (dBi)', 'Realized gain (dBi)', 'Directivity (dBi)')
RANGE_DB = 30.0                                                     # dynamic range of the 3D pattern


def find_antenna(out_dir):
    """(results dict, sphere path or None, S1P path or None) of the newest antenna run in out_dir, or None."""
    js = sorted(glob.glob(os.path.join(out_dir, '*_antenna.json')), key=os.path.getmtime)
    if not js:
        return None
    with open(js[-1]) as fh:
        res = json.load(fh)
    name = os.path.basename(js[-1])[:-len('_antenna.json')]
    solver = res.get('solver', 'openems')
    if solver != 'openems':                                         # <name>_<solver>_antenna.json, its own sphere
        name = name[:-len(f"_{solver}")] if name.endswith(f"_{solver}") else name
        sph = os.path.join(out_dir, res.get('sphere_file', ''))
    else:
        tag = res.get('port', 'P1').replace('+', 'p').replace('-', 'n')
        sph = os.path.join(out_dir, 'openems', f"exc_{tag}", 'nf2ff_sphere.h5')
    s1p = os.path.join(out_dir, f"{name}_{solver}.s1p")
    return res, (sph if os.path.isfile(sph) else None), (s1p if os.path.exists(s1p) else None)


def load_sphere(path):
    """An antenna sphere: openEMS's NF2FF HDF5 file, or the .npz of mom25d's antenna mode (the same keys)."""
    if path.endswith('.npz'):
        with np.load(path) as z:
            return {k: np.asarray(z[k]) for k in z.files}
    return read_sphere(path)


class AntennaTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.res, self.sph, self.out = win, None, None, None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel('no antenna results (Setup: Mode = antenna)')
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        top.addWidget(QLabel('pattern at'))
        self.freq = QComboBox()
        self.freq.currentIndexChanged.connect(self.draw_pattern)
        top.addWidget(self.freq)
        self.qty = QComboBox()
        self.qty.addItems(QUANTITIES)
        self.qty.currentIndexChanged.connect(self.draw_pattern)
        top.addWidget(self.qty)
        b = QPushButton('Save pattern PNG…')
        b.clicked.connect(self.save)
        top.addWidget(b)
        lay.addLayout(top)

        split = QSplitter()
        left = QSplitter(Qt.Vertical)
        self.s11 = pg.PlotWidget(title='input match')
        self.s11.setLabel('bottom', 'frequency (GHz)')
        self.s11.setLabel('left', '|S11| (dB)')
        self.s11.showGrid(x=True, y=True, alpha=0.3)
        left.addWidget(self.s11)
        self.gain = pg.PlotWidget(title='gain over frequency')
        self.gain.setLabel('bottom', 'frequency (GHz)')
        self.gain.setLabel('left', 'dBi')
        self.gain.addLegend()
        self.gain.showGrid(x=True, y=True, alpha=0.3)
        self.eff_vb = pg.ViewBox()                                  # efficiency on its own right axis
        pi = self.gain.getPlotItem()
        pi.showAxis('right')
        pi.scene().addItem(self.eff_vb)
        pi.getAxis('right').linkToView(self.eff_vb)
        pi.getAxis('right').setLabel('efficiency (%)')
        self.eff_vb.setXLink(pi)
        pi.vb.sigResized.connect(lambda: self.eff_vb.setGeometry(pi.vb.sceneBoundingRect()))
        left.addWidget(self.gain)
        self.marker = pg.InfiniteLine(angle=90, pen=pg.mkPen('#888', style=Qt.DashLine))
        split.addWidget(left)
        self.fig = Figure(figsize=(9, 5), layout='constrained')
        self.canvas = FigureCanvasQTAgg(self.fig)
        split.addWidget(self.canvas)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

    def load(self, out_dir):
        """Show the antenna results of a results folder; False if it holds none."""
        found = find_antenna(out_dir) if out_dir else None
        self.out = out_dir
        if not found:
            return False
        self.res, sph_path, s1p = found
        m = self.res['match']
        bw = (f", −10 dB band {m['bw10_lo'] / 1e9:.3f}–{m['bw10_hi'] / 1e9:.3f} GHz" if m.get('bw10') else
              ', S11 never below −10 dB')
        self.info.setText(f"{self.res.get('port', '')}: resonance {m['f_res'] / 1e9:.4f} GHz, S11 {m['s11_db']:.1f} dB, "
                          f"Zin {m['zin'][0]:.1f} {'+' if m['zin'][1] >= 0 else '−'} j{abs(m['zin'][1]):.1f} Ω{bw}")
        self.s11.clear()
        if s1p:
            from ..touchstone import read_touchstone
            f, S = read_touchstone(s1p)
            self.s11.plot(f / 1e9, 20 * np.log10(np.abs(S[:, 0, 0]) + 1e-15), pen=pg.mkPen('#1f77b4', width=2))
            self.s11.addLine(y=-10, pen=pg.mkPen('#aaa', style=Qt.DashLine))
        rows = self.res['rows']
        fr = np.array([r['f'] for r in rows]) / 1e9
        self.gain.clear()
        self.eff_vb.clear()
        for key, name, color in (('D_dbi', 'directivity', '#2ca02c'), ('G_dbi', 'gain', '#1f77b4'),
                                 ('Grealized_dbi', 'realized gain', '#d62728')):
            y = np.array([np.nan if r[key] is None else r[key] for r in rows], float)
            self.gain.plot(fr, y, pen=pg.mkPen(color, width=2), symbol='o', symbolSize=4, symbolBrush=color, name=name)
        eff = pg.PlotDataItem(fr, [100 * r['eff'] for r in rows], pen=pg.mkPen('#9467bd', width=2, style=Qt.DashLine))
        self.eff_vb.addItem(eff)
        self.gain.getPlotItem().legend.addItem(eff, 'efficiency (right)')
        self.eff_vb.enableAutoRange()
        self.gain.addItem(self.marker)
        self.sph = load_sphere(sph_path) if sph_path else None
        self.freq.blockSignals(True)
        self.freq.clear()
        if self.sph is not None:
            self.freq.addItems([f"{f / 1e9:.4g} GHz" for f in self.sph['freq']])
            self.freq.setCurrentIndex(int(np.argmin(np.abs(self.sph['freq'] - self.res['f_pattern']))))
        self.freq.blockSignals(False)
        self.draw_pattern()
        return True

    def _row(self, f):
        return min(self.res['rows'], key=lambda r: abs(r['f'] - f))

    def draw_pattern(self, *_):
        self.fig.clear()
        if self.res is None:
            self.canvas.draw_idle()
            return
        if self.sph is None:
            self.fig.text(0.5, 0.5, 'no nf2ff_sphere.h5 in the run folder: pattern cuts at the pattern frequency only',
                          ha='center')
            self._cuts_from_json()
            return
        i = self.freq.currentIndex()
        f = float(self.sph['freq'][i])
        r = self._row(f)
        q = self.qty.currentIndex()
        scale = (r['eff'], r['Prad'] / r['Pinc'] if r['Pinc'] > 0 else np.nan, 1.0)[q]
        g = sphere_gain(self.sph, i, scale)
        gdb = 10 * np.log10(np.maximum(g, 1e-12))
        self.marker.setValue(f / 1e9)
        label = QUANTITIES[q]
        peak = float(gdb.max())
        lo = np.ceil((peak - RANGE_DB) / 5) * 5
        ax = self.fig.add_subplot(1, 2, 1, projection='polar')
        for (name, (ang, gg)), color in zip(sphere_cuts(self.sph, g).items(), ('#1f77b4', '#d62728', '#2ca02c')):
            ax.plot(np.radians(ang), np.maximum(10 * np.log10(np.maximum(gg, 1e-12)), lo), color=color, lw=1.6, label=name)
        ax.set_rlim(lo, np.ceil(peak / 5) * 5 + 1)
        ax.set_theta_zero_location('N')
        ax.set_theta_direction(-1)
        ax.set_title(f"cuts, {label}", fontsize=10)
        ax.legend(loc='lower center', bbox_to_anchor=(0.5, -0.32), fontsize=8, ncol=1)
        # 3D pattern: radius = dB above (peak - RANGE_DB), coloured by dB
        th, ph = np.meshgrid(np.radians(self.sph['theta']), np.radians(self.sph['phi']))
        rr = np.clip(gdb - (peak - RANGE_DB), 0, None) / RANGE_DB
        x, y, z = rr * np.sin(th) * np.cos(ph), rr * np.sin(th) * np.sin(ph), rr * np.cos(th)
        ax3 = self.fig.add_subplot(1, 2, 2, projection='3d')
        import matplotlib
        from matplotlib.colors import Normalize
        nrm = Normalize(peak - RANGE_DB, peak)
        ax3.plot_surface(x, y, z, facecolors=matplotlib.colormaps['jet'](nrm(gdb)), rstride=1, cstride=1,
                         linewidth=0, antialiased=False, shade=False)
        lim = max(np.abs([x, y, z]).max(), 1e-3)
        for a in (ax3.set_xlim, ax3.set_ylim, ax3.set_zlim):
            a(-lim, lim)
        ax3.set_box_aspect((1, 1, 1))
        ax3.set_xlabel('x')
        ax3.set_ylabel('y (up in KiCad view)')
        ax3.set_zlabel('z (top side)')
        ax3.set_xticks([])
        ax3.set_yticks([])
        ax3.set_zticks([])
        sm = matplotlib.cm.ScalarMappable(norm=nrm, cmap='jet')
        self.fig.colorbar(sm, ax=ax3, shrink=0.6, pad=0.12, label=label)
        ax3.set_title(f"3D, peak {peak:.2f} dBi", fontsize=10)
        self.fig.suptitle(f"{self.res.get('port', '')} far field at {f / 1e9:.4g} GHz: D {r['D_dbi'] or 0:.2f} dBi, "
                          f"efficiency {100 * r['eff']:.1f} %, G {r['G_dbi'] or 0:.2f} dBi", fontsize=11)
        self.canvas.draw_idle()

    def _cuts_from_json(self):
        ax = self.fig.add_subplot(1, 1, 1, projection='polar')
        for name, c in self.res.get('cuts', {}).items():
            ax.plot(np.radians(c['angle']), np.maximum(c['gain_dbi'], -30), label=name)
        ax.set_theta_zero_location('N')
        ax.set_theta_direction(-1)
        ax.legend(fontsize=8)
        self.canvas.draw_idle()

    def save(self):
        if self.res is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save the pattern', os.path.join(self.out or os.getcwd(), 'pattern.png'),
                                              'PNG (*.png)')
        if path:
            self.fig.savefig(path, dpi=130)
