"""The 3D field view: volume dumps (volume+MM), their resampling onto a uniform grid, the translucent colouring,
the board's copper as triangles and outlines, and the GUI panel on a results folder."""
import os
import numpy as np
import pytest
from kicad2ems import volume3d as v3
from kicad2ems.solvers.openems import fields as F
from kicad2ems.solvers.openems.dumps import write_info, parse_dump


@pytest.fixture(scope='module')
def board(demo_files):
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    base, _ = demo_files
    return Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')


@pytest.fixture(scope='module')
def volume_run(board, tmp_path_factory):
    """A frequency-domain volume dump on a graded grid (fine in z at the board, coarse above), as a grid file:
    a wave running along x that decays with height."""
    m = board
    out = tmp_path_factory.mktemp('vol')
    run = out / 'openems' / 'exc_P1'
    d = parse_dump('E@volume+2@3', m)
    write_info(str(run), [d], m)
    x = np.linspace(d.start[0], d.stop[0], 40)
    y = np.linspace(d.start[1], d.stop[1], 20)
    z = np.concatenate([np.linspace(d.start[2], m.thickness, 8), m.thickness + np.array([0.3, 0.8, 1.5, 2.0])])
    X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
    data = np.zeros((1, len(x), len(y), len(z), 3), complex)
    data[0, ..., 2] = np.exp(-1j * X / 3) * np.exp(-np.maximum(Z - m.thickness, 0))
    F.write_npz(str(run / f"{d.name}.npz"), dict(x=x, y=y, z=z), [3e9], data)
    return str(out), m, d


def test_volume_dump_region(board):
    m = board
    d = parse_dump('E@volume+2@3', m)
    assert d.normal is None and d.label == 'volume+2' and d.name == 'dump_E_volumep2_fd'
    assert d.start[2] == pytest.approx(-2.0) and d.stop[2] == pytest.approx(m.thickness + 2.0)  # beyond planes' +1 mm
    plain = parse_dump('E@volume@3', m)
    assert plain.stop[2] == pytest.approx(m.thickness + 0.5) and plain.label == 'volume'


def test_volume_resampled_and_coloured(volume_run):
    out, m, d = volume_run
    src = F.find_dumps(out)[0]
    assert v3.is_volume(src)
    vol = v3.Volume(src, 0, n=64)
    steps = vol.step
    assert np.allclose(np.diff(vol.axes[0]), steps[0]) and np.allclose(np.diff(vol.axes[2]), steps[2])
    assert vol.data.shape[0] == 65 and vol.complex
    env = vol.scalar('mag')                                     # the envelope: |E| = e^-(z - top) above the board
    top = np.searchsorted(vol.axes[2], m.thickness)
    assert env[:, :, :top].max() == pytest.approx(1.0, rel=1e-6) and env[..., -1].max() < 0.2
    a, b = vol.scalar('z', 0.0), vol.scalar('z', np.pi)          # half a period apart: opposite signs
    assert np.allclose(a, -b) and vol.signed('z', 0.0) and not vol.signed('mag', 0.0) and not vol.signed('z')
    lo, hi = v3.levels([env], False, True, decades=0.5, clip=100)
    assert hi == pytest.approx(env.max()) and lo == pytest.approx(hi / 10 ** 0.5)
    rgba = v3.colour(env, lo, hi, log=True, opacity=0.5)
    assert rgba.shape == env.shape + (4,) and rgba.dtype == np.uint8
    alpha = rgba[..., 3]
    assert alpha.max() <= 128 and alpha[env <= lo].max() == 0 < alpha[env > 0.9 * hi].min()   # weak: transparent
    s = v3.colour(a, -1, 1, signed=True)
    weak, strong = np.argmin(np.abs(a)), np.argmax(np.abs(a))
    assert s[..., 3].flat[weak] <= 2 < s[..., 3].flat[strong]      # a signed component: transparent at zero


def test_board_geometry(volume_run):
    out, m, _ = volume_run
    g = F.find_dumps(out)[0].geometry
    tris = v3.copper_triangles(g)
    assert {lay for lay, *_ in tris} == set(m.copper)
    for lay, z, verts, faces in tris:
        assert np.allclose(verts[:, 2], m.cu_z[lay]) and faces.max() < len(verts)
        tri = verts[faces]
        area = 0.5 * np.abs(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[:, 2]).sum()
        assert area == pytest.approx(sum(p.area for p in m.copper[lay]), rel=2e-2)   # rings simplified to 0.01 mm
    ol = v3.copper_outlines(g)
    assert len(ol) % 2 == 0 and set(np.round(ol[:, 2], 6)) <= {round(z, 6) for z in m.cu_z.values()}
    assert len(v3.via_lines(g)) == 2 * len(m.vias)
    x0, y0, x1, y1, z0, z1 = v3.board_box(g)
    assert (x0, y0, x1, y1) == pytest.approx(m.outline.bounds, abs=1e-3) and z1 == pytest.approx(max(m.cu_z.values()))


def test_copper_solids_face_outward():
    """A square with a hole as a slab: top faces up, bottom faces down, every wall facing away from the copper
    (out of the square, into the hole), and the caps' area that of the copper."""
    sq = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [4, 6], [6, 6], [6, 4], [4, 4]]
    g = dict(cu_z={'F.Cu': 1.0}, copper={'F.Cu': [[sq, hole]]})
    (lay, z, v, f), = v3.copper_solids(g, 0.2)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    c = tri.mean(1)
    top, bot = np.isclose(c[:, 2], 1.1), np.isclose(c[:, 2], 0.9)
    assert (n[top, 2] > 0).all() and (n[bot, 2] < 0).all()
    assert 0.5 * n[top, 2].sum() == pytest.approx(100 - 4)
    wall = ~(top | bot)
    out = c[wall, :2] - 5.0                                     # from the square's centre
    inner = np.abs(out).max(1) < 1.5                             # the hole's walls
    radial = (n[wall, :2] * out).sum(1)
    assert (radial[~inner] > 0).all() and (radial[inner] < 0).all()


def test_volume_panel(volume_run, tmp_path):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    pytest.importorskip('OpenGL')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from kicad2ems.gui.volumetab import VolumeTab, N_PHASE
    app = QApplication.instance() or QApplication([])
    out, m, _ = volume_run
    w = VolumeTab()
    assert not w.load(str(tmp_path))                            # no dumps: nothing
    assert w.load(out) and w.dump.count() == 1 and w.frame.currentText() == '3 GHz'
    assert len(w._frames) == N_PHASE                             # one period of Re(E e^jphi)
    assert {'vol', 'outline', 'box', 'vias'} <= set(w.items) and any(k.startswith('cu') for k in w.items)
    w.envelope.setChecked(True)
    assert len(w._frames) == 1
    w.comp.setCurrentIndex(3)
    w.envelope.setChecked(False)                                 # Ez at an instant: signed
    assert 'Ez' in w.status.text() and '-' in w.status.text().split(':')[1]
    assert w.cu_t.value() > 0                                     # shaded slabs, as thick as 0.6 % of the board
    w.cu_t.setValue(0)                                           # flat
    assert any(k.startswith('cu') for k in w.items)
    w.copper.setChecked(False)
    assert not any(k.startswith('cu') or k == 'outline' for k in w.items)


def test_saved_animations(volume_run, board, tmp_path, monkeypatch):
    """Save GIF / MP4 of the 3D view: a frequency-domain dump's period, a time-domain dump's time steps (it had
    given a single still frame), and one frame only (the envelope) refused."""
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    pytest.importorskip('OpenGL')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from kicad2ems.gui.volumetab import VolumeTab, N_PHASE
    from test_fields import write_vtr
    app = QApplication.instance() or QApplication([])
    out, m, _ = volume_run
    shown = []                                                       # no GL offscreen: what each grab would show
    monkeypatch.setattr(VolumeTab, 'grab_rgb', lambda self: (shown.append((self.frame.currentIndex(), self.k)),
                                                             np.full((8, 8, 3), 7 * len(shown) % 256, np.uint8))[1])
    w = VolumeTab()
    w.resize(400, 300)
    assert w.load(out) and w.animated() == 'phase' and len(w.render_frames()) == N_PHASE
    w.envelope.setChecked(True)
    assert w.animated() is None
    with pytest.raises(ValueError, match='untick envelope'):
        w.render_frames()
    td_out = tmp_path / 'td'
    run = td_out / 'openems' / 'exc_P1'
    d = parse_dump('E@volume+2', m)
    write_info(str(run), [d], m, dt_ps=0.5)
    x, y = np.linspace(d.start[0], d.stop[0], 12), np.linspace(d.start[1], d.stop[1], 8)
    z = np.linspace(d.start[2], d.stop[2], 6)
    for k in range(4):
        f = np.zeros((12, 8, 6, 3), np.float32)
        f[..., 2] = np.sin(x / 3 + k)[:, None, None]
        write_vtr(run / f"{d.name}_{k * 60:010d}.vtr", x, y, z, f)
    w2 = VolumeTab()
    w2.resize(400, 300)
    assert w2.load(str(td_out)) and w2.src.domain == 'TD' and w2.animated() == 'time'
    t0 = w2.frame.currentIndex()
    imgs = w2.render_frames()
    assert len(imgs) == 4 and w2.frame.currentIndex() == t0      # each time step, then back where it was
    assert [t for t, _ in shown[-4:]] == [0, 1, 2, 3]
    w2.log.setChecked(False)
    own = float(w2.status.text().split(' to ')[1].split()[0])        # this frame's own range
    w2.whole.setChecked(True)                                        # one range over the four time steps
    top = float(w2.status.text().split(' to ')[1].split()[0])
    assert 'over every frame' in w2.status.text() and top >= own
    tops = [v3.levels([v3.Volume(w2.src, k, n=w2.res.value()).scalar('mag')], False, False, clip=w2.clip.value())[1]
            for k in range(4)]
    assert top == pytest.approx(max(tops), rel=1e-3)
    from kicad2ems.solvers.openems.fields import write_frames
    from PIL import Image
    p = write_frames(imgs, str(tmp_path / 'a.gif'))
    assert Image.open(p).n_frames == 4
