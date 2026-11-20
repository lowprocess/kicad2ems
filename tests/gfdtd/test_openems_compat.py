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


def test_transforms(tmp_path):
    """AddTransform as CSXCAD's: post-multiplied (the last added applies
    last), angles in degrees; a transformed primitive fills the points that map
    back inside it, and the transform goes through the XML."""
    from kicad2ems.gfdtd.operator import inside
    csx = ContinuousStructure()
    g = csx.GetGrid()
    for a in 'xyz':
        g.SetLines(a, np.linspace(-6, 6, 49))
    box = csx.AddMetal('m').AddBox([-4, -0.5, -0.5], [4, 0.5, 0.5])
    box.AddTransform('RotateAxis', 'z', 90)        # along y now
    box.AddTransform('Translate', [1, 0, 0])       # then moved along x
    tr = box.GetTransform()
    assert np.allclose(tr.Transform([4, 0, 0]), [1, 4, 0])
    assert np.allclose(tr.Transform([1, 4, 0], invers=True), [4, 0, 0])
    pts = [g.GetLines(a) for a in range(3)]

    class Grid:
        lines, tol = pts, [1e-6] * 3
    m = inside(box, pts, Grid)
    X, Y, Z = np.meshgrid(*pts, indexing='ij')
    want = ((np.abs(X - 1) <= 0.5 + 1e-5) & (np.abs(Y) <= 4 + 1e-5)
            & (np.abs(Z) <= 0.5 + 1e-5))
    assert (m == want).all()
    sph = csx.AddMaterial('d', epsilon=3).AddSphere([0, 0, 0], 1.0)
    sph.AddTransform('Scale', 2.0)
    sph.AddTransform('Translate', [0, 0, 2])
    ms = inside(sph, pts, Grid)
    assert (ms == (np.sqrt(X ** 2 + Y ** 2 + (Z - 2) ** 2) <= 2 + 1e-5)).all()
    csx.Write2XML(str(tmp_path / 'm.xml'))
    back = ContinuousStructure()
    back.ReadFromXML(str(tmp_path / 'm.xml'))
    b, s = [q for p in back.GetAllProperties() for q in p.GetAllPrimitives()]
    assert np.allclose(b.GetTransform().GetMatrix(), tr.GetMatrix())
    assert np.allclose(s.GetTransform().GetMatrix(),
                       sph.GetTransform().GetMatrix()) and not back.skipped


def test_a_polygons_coordinates_are_csxcads():
    """A polygon's X1, X2 run along the axes after its normal, cyclic, as
    CSXCAD's: normal y, X1 is z and X2 is x (gfdtd had taken x, z)."""
    from kicad2ems.gfdtd.operator import inside
    csx = ContinuousStructure()
    g = csx.GetGrid()
    for a in 'xyz':
        g.SetLines(a, np.arange(0, 6.5, 0.5))
    q = csx.AddMetal('m').AddLinPoly([[0, 1, 1, 0], [0, 0, 5, 5]], 'y', 1.0,
                                     2.0)
    pts = [g.GetLines(a) for a in range(3)]

    class Grid:
        lines, tol = pts, [1e-6] * 3
    m = inside(q, pts, Grid)
    X, Y, Z = np.meshgrid(*pts, indexing='ij')
    want = (Z <= 1 + 1e-6) & (X <= 5 + 1e-6) & (Y >= 1 - 1e-6) & (Y <= 3 + 1e-6)
    assert (m == want).all()


def test_a_rotated_polygon(tmp_path):
    """AddRotPoly as CSXCAD's: a cone shell (the conical horn's profile: norm
    x, points[0] the distance from the z axis, points[1] z), turned about z."""
    from kicad2ems.gfdtd.operator import inside
    csx = ContinuousStructure()
    g = csx.GetGrid()
    for a in 'xy':
        g.SetLines(a, np.linspace(-6, 6, 61))
    g.SetLines('z', np.linspace(-1, 6, 36))
    # an annulus 2 <= r <= 3 from z = 0 to 4
    q = csx.AddMetal('m').AddRotPoly([[2, 3, 3, 2], [0, 0, 4, 4]], 'x', 0,
                                     'z', [0, 2 * np.pi])
    pts = [g.GetLines(a) for a in range(3)]

    class Grid:
        lines, tol = pts, [1e-6] * 3
    m = inside(q, pts, Grid)
    X, Y, Z = np.meshgrid(*pts, indexing='ij')
    r = np.hypot(X, Y)
    core = (r >= 2.05) & (r <= 2.95) & (Z >= 0.05) & (Z <= 3.95)
    away = (r < 1.9) | (r > 3.1) | (Z < -0.1) | (Z > 4.1)
    assert m[core].all() and not m[away].any()
    # half a turn, as CSXCAD turns it: from the polygon's own plane (+y, for
    # norm x) right-handed about z, through -x: the x < 0 half, the other empty
    h = csx.AddMetal('h').AddRotPoly([[2, 3, 3, 2], [0, 0, 4, 4]], 'x', 0,
                                     'z', [0, np.pi])
    mh = inside(h, pts, Grid)
    assert mh[core & (X < -0.1)].all() and not mh[core & (X > 0.1)].any()
    csx.Write2XML(str(tmp_path / 'm.xml'))
    back = ContinuousStructure()
    back.ReadFromXML(str(tmp_path / 'm.xml'))
    qs = [x for p in back.GetAllProperties() for x in p.GetAllPrimitives()]
    assert [x.kind for x in qs] == ['RotPoly', 'RotPoly'] and not back.skipped
    assert np.isclose(qs[1].angle[1], np.pi)
