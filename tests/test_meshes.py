"""Each solver's mesh for the GUI's 3D view (kicad2ems.meshes): a preview writes openEMS's grid and mom25d's
triangles (EMerge's when it is installed), and the board's geometry comes from <name>_board.json."""
import os
import numpy as np
import pytest
from kicad2ems import meshes, volume3d as v3

FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'filter.toml')


def test_preview_writes_the_meshes(run_cli, tmp_path):
    pytest.importorskip('triangle')
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--preview', '--solvers', 'openems,mom25d', '--points', '21', '--no-report',
                           '--out', out)
    assert code == 0, so + se
    found = meshes.find(str(out))
    assert {'openems', 'mom25d'} <= set(found)
    g = v3.geometry_from_board(str(out / 'filter_board.json'))
    box = v3.board_box(g)
    planes = sorted(set(g['cu_z'].values()))
    grid = meshes.segments(found['openems'], box, planes)
    assert len(grid) and len(grid) % 2 == 0
    assert np.all(grid[:, 0] >= box[0] - 1e-6) and np.all(grid[:, 0] <= box[2] + 1e-6)      # inside the board
    d = np.load(found['mom25d'])
    assert len(d['tris']) > 100 and set(np.unique(d['nodes'][:, 2])) <= set(planes)     # on copper layers
    edges = meshes.segments(found['mom25d'])
    assert len(edges) // 2 < 3 * len(d['tris'])                                          # each edge once
    assert 'lines' in meshes.counts(found['openems']) and 'triangles' in meshes.counts(found['mom25d'])
    assert v3.copper_triangles(g) and g['thickness'] > 0
