"""Field viewing: VTR frames as openEMS writes them, dump discovery in a run folder, plane cuts in KiCad
axes, copper outlines, animations; the NF2FF sphere cuts of the antenna view."""
import base64, json, os, zlib
import numpy as np
import pytest
from kicad2ems import fields as F
from kicad2ems.dumps import write_info, write_norm, parse_dump


def _b64_block(raw, compress):
    if not compress:
        return base64.b64encode(np.uint32(len(raw)).tobytes() + raw).decode()
    c = zlib.compress(raw)
    head = np.array([1, 32768, len(raw), len(c)], '<u4').tobytes()
    return base64.b64encode(head).decode() + base64.b64encode(c).decode()   # encoded separately, as VTK does


def write_vtr(path, x, y, z, field, compress=True):
    """A VTK rectilinear grid like openEMS's dumps: coordinates in m, point data x fastest."""
    hdr = ' compressor="vtkZLibDataCompressor"' if compress else ''
    data = np.transpose(field.astype('<f4'), (2, 1, 0, 3)).tobytes()
    coords = ''.join(f'<DataArray type="Float64" format="binary">{_b64_block((np.asarray(c, "<f8") * 1e-3).tobytes(), compress)}</DataArray>'
                     for c in (x, y, z))
    ext = f"0 {len(x) - 1} 0 {len(y) - 1} 0 {len(z) - 1}"
    with open(path, 'w') as fh:
        fh.write(f'<?xml version="1.0"?><VTKFile type="RectilinearGrid" version="0.1" byte_order="LittleEndian" '
                 f'header_type="UInt32"{hdr}><RectilinearGrid WholeExtent="{ext}"><Piece Extent="{ext}"><PointData>'
                 f'<DataArray type="Float32" Name="E-Field" NumberOfComponents="3" format="binary">'
                 f'{_b64_block(data, compress)}</DataArray></PointData><CellData></CellData><Coordinates>{coords}'
                 f'</Coordinates></Piece></RectilinearGrid></VTKFile>')


@pytest.mark.parametrize('compress', [True, False])
def test_read_vtr(tmp_path, compress):
    x, y, z = np.linspace(0, 10, 6), np.array([-3.0, -1.0]), np.array([0.0, 0.2, 0.5])
    f = np.random.default_rng(1).normal(size=(6, 2, 3, 3)).astype(np.float32)
    write_vtr(tmp_path / 'a.vtr', x, y, z, f, compress)
    mesh, v = F.read_vtr(str(tmp_path / 'a.vtr'))
    assert np.allclose(mesh['x'], x) and np.allclose(mesh['y'], y) and np.allclose(mesh['z'], z)
    assert v.shape == (6, 2, 3, 3) and np.allclose(v, f)


def test_plane_axes_and_resample():
    mesh = dict(x=np.array([0.0, 1.0, 3.0]), y=np.array([-2.0, -1.0]), z=np.array([0.0, 0.5]))
    d = np.zeros((3, 2, 2, 3))
    d[2, 0, 1] = [0, 5, 0]                                          # at x = 3, model y = -2 (KiCad y = 2), z = 0.5
    pl = F.plane(mesh, d, 2, 1, 'y')
    assert pl.names == ('x', 'y') and list(pl.v) == [1.0, 2.0] and pl.signed
    assert pl.img[2, 1] == -5                                       # KiCad y component: sign flipped, row reversed
    assert F.plane(mesh, d, 2, 1, 'mag').img[2, 1] == 5
    cut = F.plane(mesh, d, 1, 0, 'mag')                             # y cut: x across, z up
    assert cut.names == ('x', 'z') and cut.img[2, 1] == 5 and cut.pos == -2.0
    img, (u0, v0, du, dv) = pl.resample()
    assert u0 == 0 and du == pytest.approx(1.0) and img.shape == (4, 2) and img[3, 1] == -5
    fd = d.astype(complex) * np.exp(1j * np.pi / 2)                 # complex: peak magnitude, or Re(F e^jphi)
    assert F.plane(mesh, fd, 2, 1, 'y').img[2, 1] == pytest.approx(5) and not F.plane(mesh, fd, 2, 1, 'y').signed
    assert F.plane(mesh, fd, 2, 1, 'y', phase=-np.pi / 2).img[2, 1] == pytest.approx(-5)
    assert F.default_axis((5, 1, 4)) == 1 and F.default_axis((5, 4, 3)) == 2
    assert F.levels([np.array([-2.0, 1.0])], True, False) == (-2.0, 2.0)
    assert F.levels([np.array([0.0, 10.0])], False, True, decades=2) == (0.1, 10.0)


@pytest.fixture(scope='module')
def run_folder(demo_files, tmp_path_factory):
    """A run folder with a time-domain plane dump (VTR frames), a frequency-domain one (HDF5) and their info."""
    h5py = pytest.importorskip('h5py')
    from kicad2ems.kicad import KiCadBoard
    from kicad2ems.model import Model
    base, _ = demo_files
    m = Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    out = tmp_path_factory.mktemp('out')
    run = out / 'openems' / 'exc_P1'
    td, fd = parse_dump('E@F.Cu-0.1', m), parse_dump('J@F.Cu@3', m)
    write_info(str(run), [td, fd], m, dt_ps=0.5)
    ob = m.outline.bounds
    x, y = np.linspace(ob[0], ob[2], 30), np.linspace(ob[1], ob[3], 12)
    for k in range(5):
        f = np.zeros((30, 12, 1, 3), np.float32)
        f[:, :, 0, 2] = (k + 1) * np.sin(x / 3)[:, None]
        write_vtr(run / f"{td.name}_{k * 60:010d}.vtr", x, y, [td.start[2]], f)
    with h5py.File(run / f"{fd.name}.h5", 'w') as h:
        for ax, v in zip('xyz', (x, y, [fd.start[2]])):
            h[f'Mesh/{ax}'] = np.asarray(v) * 1e-3
        g = h.create_group('FieldData/FD')
        ds = g.create_dataset('f0', data=np.ones((3, 30, 12, 1)) * (1 + 1j))
        ds.attrs['d_order'], ds.attrs['frequency'] = 'NXYZ', 3e9
    write_norm(str(run), {3e9: 0.5j})
    return str(out), m


def test_find_dumps_and_frames(run_folder):
    out, m = run_folder
    srcs = F.find_dumps(out)
    assert [s.domain for s in srcs] == ['TD', 'FD']
    td, fd = srcs
    assert len(td) == 5 and td.run == 'P1' and td.field == 'E' and 't = 0.030 ns' in td.frame_label(1)
    assert td.shape() == (30, 12, 1) and td.newest_complete() == 4
    mesh, d = td.load(4)
    assert np.allclose(d[..., 2].max(), 5 * np.sin(mesh['x'] / 3).max(), rtol=1e-5)
    assert len(fd) == 1 and fd.scale(0) == pytest.approx(2.0)       # 1 V incident: 1 / |0.5j|
    assert np.allclose(fd.load(0)[1], 2 * (1 + 1j))
    lines = F.outlines(td.geometry, 2, m.cu_z['F.Cu'] - 0.1)
    assert len(lines) > 1 and all(len(u) == len(v) for u, v in lines)
    cut = F.outlines(td.geometry, 0, float(np.mean(m.outline.bounds[0::2])))   # an x cut crosses the copper
    assert cut and all(len(set(v)) == 1 for _, v in cut)


def test_save_animations(run_folder, tmp_path):
    out, _ = run_folder
    td, fd = F.find_dumps(out)
    calls = []
    p = F.save_animation(td, str(tmp_path / 'a.gif'), 2, 0, 'z', progress=lambda k, n: calls.append((k, n)))
    from PIL import Image
    im = Image.open(p)
    assert im.n_frames == 5 and calls[-1] == (5, 5)
    p = F.save_animation(fd, str(tmp_path / 'b.gif'), 2, 0, 'mag', n_phase=6, lim=(0.1, 10))
    assert Image.open(p).n_frames == 6
    with pytest.raises(ValueError):
        F.save_animation(td, str(tmp_path / 'c.avi'), 2, 0)
    if F.ffmpeg_exe():
        p = F.save_animation(td, str(tmp_path / 'a.mp4'), 2, 0, 'mag')
        assert os.path.getsize(p) > 1000


def test_sphere_cuts(tmp_path):
    h5py = pytest.importorskip('h5py')
    from kicad2ems.antenna import read_sphere, sphere_gain, sphere_cuts
    th, ph = np.radians(np.arange(0, 181, 5.0)), np.radians(np.arange(0, 361, 5.0))
    T, P = np.meshgrid(th, ph)
    e = np.abs(np.sin(T) * np.cos(P)) + 1e-9                        # null along z and along y (phi = 90)
    with h5py.File(tmp_path / 's.h5', 'w') as h:
        h['Mesh/theta'], h['Mesh/phi'], h['Mesh/r'] = th, ph, [1.0]
        g = h.create_group('nf2ff')
        g.attrs['Frequency'], g.attrs['Dmax'], g.attrs['Prad'] = [2e9], [3.0], [1e-3]
        g['E_theta/FD/f0_real'], g['E_theta/FD/f0_imag'] = e, 0 * e
        g['E_phi/FD/f0_real'], g['E_phi/FD/f0_imag'] = 0 * e, 0 * e
    sph = read_sphere(str(tmp_path / 's.h5'))
    assert sph['theta'][-1] == pytest.approx(180) and sph['e_norm'].shape == (1, 73, 37)
    gain = sphere_gain(sph, 0, 0.5)
    assert gain.max() == pytest.approx(1.5)                         # Dmax x efficiency
    cuts = sphere_cuts(sph, gain)
    a, gxz = cuts['xz (phi = 0)']
    assert a[0] == pytest.approx(-180) and a[-1] == pytest.approx(180) and len(a) == 73
    assert gxz[np.argmin(np.abs(a - 90))] == pytest.approx(1.5) and gxz[np.argmin(np.abs(a))] < 1e-6
    assert gxz[np.argmin(np.abs(a + 90))] == pytest.approx(1.5)    # far side (phi = 180)
    assert cuts['yz (phi = 90)'][1].max() < 1e-6
    ang, gxy = cuts['xy (theta = 90)']
    assert gxy[0] == pytest.approx(1.5) and gxy[np.argmin(np.abs(ang - 90))] < 1e-6


def test_field_view_tab(run_folder, tmp_path):
    pytest.importorskip('PySide6')
    pytest.importorskip('pyqtgraph')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from kicad2ems.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    out, _ = run_folder
    w = MainWindow()
    f = w.fields
    f.load(out)
    assert len(f.srcs) == 2 and f.src.domain == 'TD' and f.frame.maximum() == 4 and f.axis.currentText() == 'z'
    f.frame.setValue(3)
    assert '4/5' in f.frame_lab.text() and f.pl.img.shape == (30, 12)
    f.comp.setCurrentIndex(3)                                       # Ez: signed, symmetric range
    assert f.pl.signed and f.lim[0] == -f.lim[1]
    f.follow(out)                                                   # live: jumps to the newest frame
    assert f.frame.value() == 4
    f.dump.setCurrentIndex(1)
    f.phase_on.setChecked(True)
    f.phase.setValue(90)
    assert 'phase 90°' in f.frame_lab.text()
    w.close()
