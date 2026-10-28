"""Field viewing: VTR frames as openEMS writes them, dump discovery in a run folder, plane cuts in KiCad
axes, copper outlines, animations; the NF2FF sphere cuts of the antenna view."""
import os
import numpy as np
import pytest
from kicad2ems.fields import frames as F
from kicad2ems.fields.dumps import write_info, parse_dump
from k2e_fixtures import write_vtr                                     # and the run_folder fixture




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
    from kicad2ems.fields.antenna import read_sphere, sphere_gain, sphere_cuts
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


def test_grid_dumps_next_to_openems(run_folder, tmp_path):
    """A frequency-domain dump as a grid (.npz, mom25d's currents) is found next to openEMS's, labelled with its
    solver and unit, and gets a |F| map and one period of Re(F e^j phase) per frequency."""
    import shutil
    out, m = run_folder
    o = tmp_path / 'out'
    shutil.copytree(out, o)
    run = o / 'mom25d' / 'exc_P1'
    d = parse_dump('J@F.Cu@2,4', m)
    write_info(str(run), [d], m)
    x, y = np.linspace(0, 10, 20), np.linspace(-5, 0, 8)
    wave = np.exp(-1j * x / 2)[:, None] * np.ones((1, 8))            # a wave running along x
    data = np.zeros((2, 20, 8, 1, 3), complex)
    data[0, :, :, 0, 0], data[1, :, :, 0, 0] = wave, 2 * wave
    F.write_npz(str(run / f"{d.name}.npz"), dict(x=x, y=y, z=np.array([0.6])), [2e9, 4e9], data)
    srcs = F.find_dumps(str(o))
    assert [s.solver for s in srcs] == ['openems', 'openems', 'mom25d']           # openEMS's first
    g = srcs[-1]
    assert g.label.startswith('mom25d P1: J') and g.freqs == [2e9, 4e9] and g.shape() == (20, 8, 1)
    assert np.allclose(g.load(1)[1][..., 0], 2 * wave[..., None]) and g.scale(0) is None
    files = F.save_fd_outputs(g, str(tmp_path / 'j'), animate='gif', n_phase=6)
    from PIL import Image
    assert [os.path.basename(p) for _, p in files] == ['j_2GHz.png', 'j_2GHz.gif', 'j_4GHz.png', 'j_4GHz.gif']
    assert Image.open(files[1][1]).n_frames == 6


def test_dumps_of_both_solvers_are_listed(tmp_path):
    """A folder with openEMS's and mom25d's dumps: both are found, openEMS's first, each label naming its
    solver (the Viewer's 3D and Cut lists offer both)."""
    import json
    from kicad2ems.fields.frames import find_dumps
    for solver, name in (('openems', 'dump_E_F_Cu_fd'), ('mom25d', 'dump_J_F_Cu_fd')):
        d = tmp_path / solver / 'exc_P1'
        d.mkdir(parents=True)
        json.dump(dict(dumps=[dict(name=name, freqs=[2.4e9])]), open(d / 'dump_info.json', 'w'))
    labels = [s.label for s in find_dumps(str(tmp_path))]
    assert len(labels) == 2 and labels[0].startswith('openEMS P1: ') and labels[1].startswith('mom25d P1: ')
