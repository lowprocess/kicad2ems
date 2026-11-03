"""kicad2ems.meshes: a surface mesh's `hidden` edges (the diagonals of quad cells) are not drawn."""
import numpy as np
from types import SimpleNamespace
from kicad2ems import meshes


def test_hidden_edges_are_left_out(tmp_path):
    run = SimpleNamespace(path=lambda suffix: str(tmp_path / f'b{suffix}'))
    nodes = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    tris = np.array([[0, 1, 2], [0, 2, 3]])                     # a square as two triangles: diagonal 0-2
    plain = meshes.save(run, 'mom25d', 'tris', nodes=nodes, tris=tris)
    quad = meshes.save(run, 'mom25d-run', 'tris', nodes=nodes, tris=tris, hidden=np.array([[2, 0]]))
    assert len(meshes.segments(plain)) == 2 * 5 and len(meshes.segments(quad)) == 2 * 4
    assert meshes.counts(plain) == '2 triangles' and meshes.counts(quad) == '2 triangles (1 quad cell)'
    assert set(meshes.find(str(tmp_path))) == {'mom25d', 'mom25d-run'}
