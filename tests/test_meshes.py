"""Each solver's mesh for the 3D view (kicad2ems.meshes): a preview writes the FDTD grid (mom25d's triangles: in
mom25d's tests; drawing them on the board: kicad2ems-gui's)."""
import os
import numpy as np
import pytest
from kicad2ems import meshes

FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'rf_interdig.toml')


def test_preview_writes_the_meshes(run_cli, tmp_path):
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--preview', '--solvers', 'openems', '--points', '21', '--no-report', '--out', out)
    assert code == 0, so + se
    found = meshes.find(str(out))
    assert 'openems' in found
    grid = meshes.segments(found['openems'])
    assert len(grid) and len(grid) % 2 == 0 and grid.shape[1] == 3
    assert 'lines' in meshes.counts(found['openems'])
