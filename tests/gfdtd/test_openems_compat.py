"""openEMS's Python interface over gfdtd, the rest: the grid's helpers as
CSXCAD has them (SmoothMeshLines: CSXCAD's own function, so the same lines;
GetLine, Snap2LineNumber, GetSimArea, IsValid, Clear), openEMS.AddEdges2Grid
(automesh's box hints), and the CSXCAD / openEMS packages of
kicad2ems.gfdtd.compat that let an openEMS script run unchanged."""
import os
import subprocess
import sys

import numpy as np
import pytest

from kicad2ems.gfdtd import ContinuousStructure, openEMS
from kicad2ems.gfdtd import compat


def test_the_grids_helpers():
    g = ContinuousStructure().GetGrid()
    g.SetLines('x', [10, 0, 4])
    assert g.GetLine('x', 0) == 0 and g.GetLine('x', -1) == 10
    with pytest.raises(IndexError):
        g.GetLine('x', 3)
    assert g.Snap2LineNumber('x', 3.9) == (1, True)
    assert g.Snap2LineNumber('x', 12) == (2, False)
    g.SmoothMeshLines('x', 1.0)
    x = g.GetLines('x')
    assert x[0] == 0 and x[-1] == 10 and 4 in x and np.diff(x).max() <= 1.0 + 1e-9
    assert not g.IsValid()
    g.SetLines('y', [0, 1]); g.SetLines('z', [-1, 2])
    assert g.IsValid() and g.GetSimArea().tolist() == [[0, 0, -1], [10, 1, 2]]
    g.Clear()
    assert g.GetQtyLines('x') == 0 and g.GetDeltaUnit() == 1.0


def test_smoothing_is_csxcads():
    """The same lines as CSXCAD's own SmoothMeshLines (where it is installed)."""
    sml = pytest.importorskip('CSXCAD.SmoothMeshLines')
    from kicad2ems.gfdtd.smooth import SmoothMeshLines
    for lines, res, ratio in (([0, 0.3, 0.5, 10, 25], 1.0, 1.5),
                              ([-5, -0.1, 0.1, 5], 0.4, 1.3)):
        assert np.allclose(SmoothMeshLines(lines, res, ratio),
                           sml.SmoothMeshLines(lines, res, ratio))


def test_add_edges_to_grid():
    csx = ContinuousStructure()
    patch = csx.AddMetal('patch')
    patch.AddBox([-10, -5, 1.0], [10, 5, 1.0])
    fdtd = openEMS()
    fdtd.SetCSX(csx)
    fdtd.AddEdges2Grid(dirs='xy', properties=patch, metal_edge_res=0.6)
    x = csx.GetGrid().GetLines('x')
    # a third inside, two thirds outside each edge
    assert np.allclose(x, [-10.4, -9.8, 9.8, 10.4])
    fdtd.AddEdges2Grid(dirs='z', properties=patch)
    assert csx.GetGrid().GetLines('z').tolist() == [1.0]


def test_an_openems_script_runs_unchanged_through_compat(tmp_path):
    script = tmp_path / 's.py'
    script.write_text(
        "from CSXCAD import ContinuousStructure\n"
        "from CSXCAD import CSXCAD\n"
        "from CSXCAD.CSRectGrid import CoordinateSystem\n"
        "from openEMS import openEMS\n"
        "from openEMS.physical_constants import *\n"
        "from openEMS.automesh import mesh_hint_from_box\n"
        "csx = CSXCAD.ContinuousStructure(); g = csx.GetGrid()\n"
        "g.SetLines('x', [0, 10]); g.SmoothMeshLines('all', 2.5)\n"
        "assert CoordinateSystem.CARTESIAN == 0 and abs(MUE0 - 4e-7 * 3.141592653589793) < 1e-20\n"
        "print(len(g.GetLines('x')), type(openEMS()).__module__)\n")
    p = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=dict(os.environ, PYTHONPATH=compat.PATH))
    assert p.returncode == 0, p.stderr
    assert p.stdout.split() == ['5', 'kicad2ems.gfdtd.fdtd']


def test_spheres(tmp_path):
    """AddSphere / AddSphericalShell: filled where the grid's points are within
    the radius (the shell: within its width), and through CSXCAD's XML."""
    from kicad2ems.gfdtd.operator import inside
    csx = ContinuousStructure()
    g = csx.GetGrid()
    for a in 'xyz':
        g.SetLines(a, np.linspace(-5, 5, 21))
    m = csx.AddMetal('ball')
    ball = m.AddSphere([0, 0, 0], 2.0)
    shell = csx.AddMaterial('skin', epsilon=2).AddSphericalShell([0, 0, 0], 4.0,
                                                                 0.6)
    pts = [g.GetLines(a) for a in range(3)]

    class Grid:  # (inside's view of the grid)
        lines, tol = pts, [1e-6] * 3
    mb = inside(ball, pts, Grid)
    X, Y, Z = np.meshgrid(*pts, indexing='ij')
    r = np.sqrt(X ** 2 + Y ** 2 + Z ** 2)
    assert (mb == (r <= 2.0 + 1e-5)).all() and mb.sum() > 20
    ms = inside(shell, pts, Grid)
    assert ms[r < 3.5].sum() == 0 and ms[np.abs(r - 4) < 0.25].all()
    csx.Write2XML(str(tmp_path / 'm.xml'))
    back = ContinuousStructure()
    back.ReadFromXML(str(tmp_path / 'm.xml'))
    kinds = sorted(q.kind for p in back.GetAllProperties()
                   for q in p.GetAllPrimitives())
    assert kinds == ['Sphere', 'SphericalShell'] and not back.skipped
