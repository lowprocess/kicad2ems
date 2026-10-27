"""Each solver's mesh for the GUI's 3D view (kicad2ems.meshes): a preview writes the FDTD grid (mom25d's triangles:
in mom25d's tests), and the board's geometry comes from <name>_board.json."""
import os
import numpy as np
import pytest
from kicad2ems import meshes, volume3d as v3

FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'rf_interdig.toml')


def test_preview_writes_the_meshes(run_cli, tmp_path):
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--preview', '--solvers', 'openems', '--points', '21', '--no-report', '--out', out)
    assert code == 0, so + se
    found = meshes.find(str(out))
    assert 'openems' in found
    g = v3.geometry_from_board(str(out / 'rf_interdig_board.json'))
    box = v3.board_box(g)
    planes = sorted(set(g['cu_z'].values()))
    grid = meshes.segments(found['openems'], box, planes)
    assert len(grid) and len(grid) % 2 == 0
    assert np.all(grid[:, 0] >= box[0] - 1e-6) and np.all(grid[:, 0] <= box[2] + 1e-6)      # inside the board
    assert 'lines' in meshes.counts(found['openems'])
    assert v3.copper_triangles(g) and g['thickness'] > 0


def test_far_field_pattern_mesh():
    """The far field as a surface: radius at the peak, 0 at 30 dB below, colours by gain."""
    th, ph = np.arange(0, 91, 5.0), np.arange(0, 360, 10.0)
    T, P = np.meshgrid(np.radians(th), np.radians(ph))
    e = np.cos(T) + 1e-3                                            # a pattern straight up
    sph = dict(theta=th, phi=ph, e_norm=e[None], dmax=np.array([4.0]), freq=np.array([2.45e9]))
    v, faces, col, peak = v3.pattern_mesh(sph, 0, 1.0, (10, 20, 1), 5.0)
    assert v.shape == (len(ph) * len(th), 3) and faces.max() < len(v) and col.shape == (len(v), 4)
    assert abs(peak - 10 * np.log10(4.0)) < 1e-9
    top = v[np.argmax(v[:, 2])]
    assert np.allclose(top, (10, 20, 6), atol=1e-6)                 # 5 mm straight up at theta 0
