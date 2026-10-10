"""Edge walls (--edge-walls): PEC sheets where the ground planes end at the chosen board sides, kept
clear of signal copper; settings, solver setup and the run summary."""
import numpy as np
import pytest
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.edges import edge_walls, parse_sides, describe
from kicad2openems.config import build_settings, ConfigError


@pytest.fixture(scope='module')
def launch(boards):
    base = boards['launch']
    return Model(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', log=lambda *a: None)


def test_parse_sides():
    assert parse_sides('all') == ['xmin', 'xmax', 'ymin', 'ymax']
    assert parse_sides('ymax, XMIN') == ['xmin', 'ymax'] and parse_sides(['xmax']) == ['xmax']
    assert parse_sides(None) == [] and parse_sides('') == []
    with pytest.raises(ValueError, match='unknown side'):
        parse_sides('left')
    assert build_settings(dict(board='b.kicad_pcb', edge_walls='xmin,ymin')).edge_walls == ['xmin', 'ymin']
    with pytest.raises(ConfigError, match='edge-walls'):
        build_settings(dict(board='b.kicad_pcb', edge_walls='top'))


def test_walls_follow_the_planes_and_clear_the_launch(launch):
    m = launch
    walls = edge_walls(m, parse_sides('all'))
    x0, y0, x1, y1 = m.outline.bounds
    zs = sorted(m.cu_z.values())
    left = [w for w in walls if w[0] == 'x' and w[1] < (x0 + x1) / 2]
    right = [w for w in walls if w[0] == 'x' and w[1] > (x0 + x1) / 2]
    assert len(left) == 1 and left[0][2:4] == pytest.approx((y0, y1), abs=0.11)   # the whole left edge
    assert left[0][4:] == pytest.approx((zs[0], zs[-1]))                        # B.Cu to F.Cu
    assert len([w for w in walls if w[0] == 'y']) == 2
    # right edge: the launch pad (y -5 +- 0.8 in model coordinates) and its keepout stay open
    assert right and all(w[3] <= -5.0 - 0.8 - 0.5 + 1e-6 or w[2] >= -5.0 + 0.8 + 0.5 - 1e-6 for w in right)
    # beside the opening the F.Cu pour and B.Cu still meet the edge: walls there join B.Cu to F.Cu
    assert any(w[4:] == pytest.approx((zs[0], zs[-1])) for w in right)
    assert 'xmin 1 piece' in describe(walls, m)
    assert edge_walls(m, []) == []


def test_build_fdtd_adds_the_walls(launch, fake_openems):
    from kicad2openems.simulation import build_fdtd
    from kicad2openems.boundaries import resolve_bc
    from kicad2openems.mesh import auto_mesh
    m = launch
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 6e9, bc=bc, log=lambda *a: None)
    mesh['walls'] = edge_walls(m, ['xmin', 'ymax'])
    build_fdtd(m, mesh, 1e9, 6e9, 0, bc=bc)
    assert fake_openems.count('box') >= 2


def test_preview_logs_and_reports_walls(boards, run_cli, tmp_path):
    base = boards['launch']
    out = tmp_path / 'out'
    code, so, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--nets', 'RF,GND', '--fmin', '1',
                            '--fmax', '6', '--edge-walls', 'all', '--out', out, '--preview')
    assert code == 0, err
    assert 'edge walls (PEC): xmin 1 piece' in so
    assert 'edge walls (PEC)' in (out / 'launch_report.html').read_text()


def test_walls_and_vias_sit_on_mesh_lines(launch, boards):
    """openEMS drops a zero-thickness sheet or a thin cylinder that misses every mesh line (an 'unused
    primitive'): walls are snapped onto lines, and vias whose own lines were merged away are snapped
    even beyond --via-snap."""
    from kicad2openems.boundaries import resolve_bc
    from kicad2openems.mesh import auto_mesh
    from kicad2openems.edges import snap
    for m in (launch, Model(KiCadBoard(boards['demo'] + '.kicad_pcb'), nets=['RF', 'GND'],
                            pos_file=boards['demo'] + '-top.pos', ground='B.Cu', log=lambda *a: None)):
        for fine in (None, 0.05):
            g = auto_mesh(m, 1e9, 6e9, fine=fine, bc=resolve_bc('auto', m), log=lambda *a: None)
            X, Y = np.asarray(g['x']), np.asarray(g['y'])
            assert all(np.min(np.abs(X - v[0])) < 1e-9 and np.min(np.abs(Y - v[1])) < 1e-9 for v in g['vias'])
            for axis, c, *_ in snap(edge_walls(m, ['xmin', 'xmax', 'ymin', 'ymax']), g):
                assert np.min(np.abs((X if axis == 'x' else Y) - c)) < 1e-12
