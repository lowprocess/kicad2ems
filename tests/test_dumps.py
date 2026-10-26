"""Field dumps: spec parsing, CSX setup with plain numbers, HDF5 reading (old and new openEMS layouts),
plots, TOML lists."""
import numpy as np
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.fields.dumps import parse_dump, add_dumps, read_fd, plot_fd_dumps


@pytest.fixture(scope='module')
def model(demo_files):
    base, _ = demo_files
    return Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')


def test_parse_regions(model):
    d = parse_dump('E@F.Cu-0.1@2,6', model)
    assert d.field == 'E' and d.normal == 2 and d.start[2] == pytest.approx(model.cu_z['F.Cu'] - 0.1)
    assert d.freqs == [2e9, 6e9] and d.name.endswith('_fd')
    assert parse_dump('E@F.Cu+0.1', model).name != d.name                       # +/- give different files
    y = parse_dump('H@y=5', model)
    assert y.normal == 1 and y.start[1] == -5 and y.freqs is None               # KiCad y -> model y
    v = parse_dump('J@volume@4', model)
    assert v.normal is None and v.start[2] < 0 < model.thickness < v.stop[2]
    b = parse_dump('E@box=1,2,0,10,8,0.6@3', model)
    assert tuple(b.start) == (1, -8, 0) and tuple(b.stop) == (10, -2, 0.6)
    for bad in ('Q@F.Cu', 'E@Foo.Cu', 'E@F.Cu@x', 'E', 'E@box=1,2,3'):
        with pytest.raises(ValueError):
            parse_dump(bad, model)


def test_add_dumps_plain_numbers(model):
    calls = []
    class Box:
        def AddBox(self, a, b):
            assert all(type(v) is float for v in list(a) + list(b)); calls.append('box')
    class CSX:
        def AddDump(self, name, **kw):
            for k, v in kw.items():
                vals = v if isinstance(v, list) else [v]
                assert all(type(x) in (int, float) for x in vals), (k, v)
            calls.append(kw['dump_type']); return Box()
    add_dumps(CSX(), [parse_dump('E@F.Cu@2', model), parse_dump('J@In2.Cu', model)])
    assert calls == [10, 'box', 3, 'box']                                        # E FD, J (rot H) TD


def _write_h5(path, new_layout):
    h5py = pytest.importorskip('h5py')
    nx, ny, nz = 5, 4, 1
    rng = np.random.default_rng(0)
    F = rng.standard_normal((nx, ny, nz, 3)) + 1j * rng.standard_normal((nx, ny, nz, 3))
    with h5py.File(path, 'w') as h:
        g = h.create_group('Mesh')
        for ax, n in zip('xyz', (nx, ny, nz)):
            g[ax] = np.linspace(0, 1e-3 * (n - 1), n)                            # metres
        fd = h.create_group('FieldData').create_group('FD')
        if new_layout:
            g.attrs['mesh_scaling'] = 1e-3
            ds = fd.create_dataset('f0', data=np.transpose(F, (3, 0, 1, 2)).astype(np.complex64))
            ds.attrs['d_order'] = 'NXYZ'; ds.attrs['frequency'] = 2e9
        else:
            a = np.transpose(F, (3, 2, 1, 0))
            r = fd.create_dataset('f0_real', data=a.real.astype(np.float32)); r.attrs['frequency'] = [2e9]
            fd.create_dataset('f0_imag', data=a.imag.astype(np.float32))
    return F


@pytest.mark.parametrize('new_layout', [True, False])
def test_read_fd_both_layouts(tmp_path, new_layout):
    p = str(tmp_path / 'd.h5')
    F = _write_h5(p, new_layout)
    mesh, frames = read_fd(p)
    assert mesh['x'][-1] == pytest.approx(4.0)                                   # mm
    (f, data), = frames
    assert f == 2e9 and data.shape == F.shape and np.allclose(data, F, atol=1e-6)


def test_plot_normalised(tmp_path, model):
    p = str(tmp_path / 'd.h5')
    _write_h5(p, True)
    d = parse_dump('E@F.Cu@2', model)
    files = plot_fd_dumps(d, p, model, str(tmp_path / 'x'), norm={2e9: 0.5 + 0j}, port='P1')
    assert len(files) == 1 and (tmp_path / 'x_dump_E_F_Cu_fd_2GHz.png').exists()


def test_toml_dump_list(tmp_path):
    from kicad2ems.cli import settings_from_argv
    c = tmp_path / 'c.toml'
    c.write_text('[openems]\ndump = ["E@F.Cu-0.1@2,6", "J@In2.Cu@4"]\ndump_port = ["P1"]\n')
    s, _ = settings_from_argv(['b.kicad_pcb', '--config', str(c)])
    assert s.dump == ['E@F.Cu-0.1@2,6', 'J@In2.Cu@4'] and s.dump_port == ['P1']
