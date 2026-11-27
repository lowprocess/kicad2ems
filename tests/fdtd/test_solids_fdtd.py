"""Drawn solids in the FDTD model (fdtd.mesh, fdtd.model): mesh lines at their
faces and the domain round them, their CSX geometry and priorities, and the
operator gfdtd lowers it to (no openEMS or GPU needed)."""
import numpy as np
import pytest
from shapely.geometry import box, mapping

gfdtd = pytest.importorskip('kicad2ems.gfdtd')  # the [gfdtd] extra


def _solid(g, material, z0, z1, name, er=1.0, tand=0.0):
    return dict(shape=name, material=material, z0=z0, z1=z1, er=er, tand=tand,
                polygon=mapping(g))


def _demo(demo_files, solids):
    """The demo board (24 x 10 mm, 0.6 mm of 4 layers, B.Cu its ground) with
    these solids."""
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu', log=lambda *a: None)
    m.solids = list(solids)
    return m


def _mesh(m):
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.mesh import auto_mesh
    return auto_mesh(m, 1e9, 4e9, res=8, fine=0.4, air=1.0, nz_min=2,
                     bc=resolve_bc('MUR', m), log=lambda *a: None)


def _on(lines, v):
    return bool(np.min(np.abs(np.asarray(lines) - v)) < 1e-9)


def test_mesh_lines_at_a_solids_faces_and_the_domain_round_it(demo_files):
    """A metal lid gets lines at its faces; a dielectric puck off the board
    its faces, its own cells (lambda / sqrt(er) / res, not the air's) and the
    air margin past it."""
    lid = _solid(box(5, -8, 19, -2), 'metal', 1.6, 2.0, 'lid')
    puck = _solid(box(30, -6, 34, -4), 'dielectric', 0.0, 1.0, 'puck', er=10)
    plain = _mesh(_demo(demo_files, []))
    g = _mesh(_demo(demo_files, [lid, puck]))
    for v in (5, 19, 30, 34):
        assert _on(g['x'], v)
    for v in (-8, -2):
        assert _on(g['y'], v)
    for v in (1.6, 2.0):
        assert _on(g['z'], v)
    # the domain: the board's and the puck's, 1 mm of air past them
    assert plain['x'][-1] == pytest.approx(24 + 1)
    assert g['x'][-1] == pytest.approx(34 + 1)
    assert g['z'][-1] == pytest.approx(2.0 + 1)
    x = np.asarray(g['x'])
    inside = np.diff(x)[(x[:-1] >= 30 - 1e-9) & (x[1:] <= 34 + 1e-9)]
    lam = 299792458.0 / 4e9 * 1e3
    assert inside.max() <= lam / np.sqrt(10) / 8 * 1.0001 < lam / 8
    assert g['info']['solids'] == ('2 drawn solids as 2 pieces: metal 1, '
                                   'dielectric 1 (er 10)')


def _model(m, mesh):
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    return build_fdtd(m, mesh, 1e9, 4e9, 0, bc=resolve_bc('MUR', m),
                      api=engine_api('gfdtd:numpy'))


def _er(op, a, i, j, k):
    """The permittivity an edge's update holds (lossless: vi = dt / C)."""
    from kicad2ems.gfdtd.operator import EPS0
    g = op.grid
    b, c = [x for x in range(3) if x != a]
    idx = (i, j, k)
    A = g.dual[b][idx[b]] * g.dual[c][idx[c]]
    return op.dt / op.vi[a, i, j, k] * g.d[a][idx[a]] / (EPS0 * A)


def test_a_drawn_can_in_the_model_and_the_operator(demo_files):
    """A metal box with an air box inside it (a shell round the board), and a
    puck of er 10 inside on the board: CSX properties of their own, metal over
    the others where they meet; in the operator the walls' edges PEC, the
    air between them and the board air, the puck's er 10, the board's
    dielectric as it was."""
    from kicad2ems.gfdtd.operator import build
    from kicad2ems.gfdtd import csx as C
    from kicad2ems.fdtd.model import SOLID_PRIORITY
    can = _solid(box(-2, -12, 26, 2), 'metal', -0.3, 2.0, 'can')
    air = _solid(box(-1.5, -11.5, 25.5, 1.5), 'air', 0.0, 1.7, 'inside')
    puck = _solid(box(10, -6, 14, -4), 'dielectric', 0.6, 1.2, 'puck', er=10)
    m = _demo(demo_files, [can, air, puck])
    mesh = _mesh(m)
    FDTD, CSX, _ = _model(m, mesh)
    props = {p.name: p for p in CSX.GetAllProperties()}
    assert isinstance(props['solid1_can'], C.Metal)
    assert props['solid2_inside'].epsilon == 1.0
    assert props['solid3_puck'].epsilon == 10.0
    pr = {n: {q.priority for q in props[n].primitives}
          for n in ('solid1_can', 'solid2_inside', 'solid3_puck')}
    assert pr == {'solid1_can': {SOLID_PRIORITY['metal']},
                  'solid2_inside': {SOLID_PRIORITY['air']},
                  'solid3_puck': {SOLID_PRIORITY['dielectric']}}
    # over the board's dielectric (1) and cut-outs (2), under its copper (10)
    assert 2 < SOLID_PRIORITY['air'] < SOLID_PRIORITY['metal'] < 9
    op = build(CSX, FDTD.settings())
    x, y, z = (np.asarray(mesh[a]) for a in 'xyz')
    at = lambda v, w: int(np.argmin(np.abs(v - w)))
    j = at(y, -5.0)
    # z edges in the gap between the board's edge and the wall (z 0.6..1.7)
    k = int(np.flatnonzero((z[:-1] >= 0.6 - 1e-9) & (z[1:] <= 1.7 + 1e-9))[0])
    for xw in (-2.0, -1.5, 26.0, 25.5):  # the walls' faces: PEC
        assert op.vi[2, at(x, xw), j, k] == 0
    i = int(np.flatnonzero((x > -1.5 + 1e-9) & (x < -1e-9))[0])
    assert _er(op, 2, i, j, k) == pytest.approx(1.0, rel=1e-6)
    # inside the puck (an x line within it, a z cell within its height)
    i = int(np.flatnonzero((x > 10 + 1e-9) & (x < 14 - 1e-9))[0])
    k = int(np.flatnonzero((z[:-1] >= 0.6 - 1e-9) & (z[1:] <= 1.2 + 1e-9))[0])
    assert _er(op, 2, i, at(y, -5.0), k) == pytest.approx(10.0, rel=1e-6)
    # the board's own dielectric (4.3) stays inside the can's air
    k = int(np.flatnonzero((z[:-1] >= 0.2 - 1e-9) & (z[1:] <= 0.4 + 1e-9))[0])
    ib = int(np.flatnonzero((x > 1 + 1e-9) & (x < 2 - 1e-9))[0])
    jb = int(np.flatnonzero((y > -1.5 + 1e-9) & (y < -0.5 - 1e-9))[0])
    assert _er(op, 0, ib, jb, k) == pytest.approx(4.3, rel=0.02)
    # without the air box the can is a solid block: the gap's edges PEC
    m2 = _demo(demo_files, [can])
    FDTD2, CSX2, _ = _model(m2, mesh)
    op2 = build(CSX2, FDTD2.settings())
    i = int(np.flatnonzero((x > -1.5 + 1e-9) & (x < -1e-9))[0])
    k = int(np.flatnonzero((z[:-1] >= 0.6 - 1e-9) & (z[1:] <= 1.7 + 1e-9))[0])
    assert op2.vi[2, i, j, k] == 0 and op.vi[2, i, j, k] > 0


def test_thin_metal_is_a_sheet_on_its_nearest_line(demo_files):
    """A foil within the merge distance (fine / 2) of another solid's face
    holds no z line of its own: it is put on its nearest line as a sheet, not
    left out between two. One far from any keeps its face's line."""
    from kicad2ems.gfdtd import csx as C
    block = _solid(box(20, -8, 23, -2), 'metal', 1.6, 1.8, 'block')
    foil = _solid(box(5, -8, 19, -2), 'metal', 1.65, 1.66, 'foil')
    m = _demo(demo_files, [block, foil])
    mesh = _mesh(m)
    assert not np.any((mesh['z'] > 1.6 + 1e-9) & (mesh['z'] < 1.8 - 1e-9))
    _, CSX, _ = _model(m, mesh)
    prims = lambda n: next(p for p in CSX.GetAllProperties()
                           if p.name == n).primitives
    q, = prims('solid2_foil')
    assert type(q) is C.Polygon and q.elevation == 1.6
    q, = prims('solid1_block')
    assert type(q) is C.LinPoly and (q.elevation, q.length) == (1.6,
                                                                 pytest.approx(0.2))
    m = _demo(demo_files, [foil])
    mesh = _mesh(m)
    q, = next(p for p in _model(m, mesh)[1].GetAllProperties()
              if p.name == 'solid1_foil').primitives
    assert type(q) is C.LinPoly and _on(mesh['z'], 1.65)


def _puck_magnet(demo_files, magnet=True, br=1.2):
    """A garnet puck set into the demo board under a NdFeB magnet."""
    from shapely.geometry import Point
    puck = dict(_solid(Point(12, -5).buffer(2.5, quad_segs=32), 'ferrite',
                       0.0, 0.6, 'puck', er=15.0, tand=2e-4),
                ms=1200.0, dh=30.0)
    mag = dict(_solid(Point(12, -5).buffer(3.5, quad_segs=32), 'magnet',
                      2.0, 5.0, 'magnet'), br=br)
    return _demo(demo_files, [puck, mag] if magnet else [puck])


def test_a_magnetised_ferrite_in_the_model_and_the_operator(demo_files):
    """The puck as gfdtd's FerriteMaterial (er, its loss, Ms, the damping of
    its linewidth, a bias table at the mesh's cell centres from
    core.magnets.magnetisation: the internal field and M / Ms, the band
    centre for a partly magnetised cell), the magnet as metal; in the
    operator the puck's cells magnetised along their own directions.
    Reversed, the bias is too."""
    from kicad2ems.gfdtd.operator import build
    from kicad2ems.gfdtd import csx as C
    from kicad2ems.core import magnets as mg
    m = _puck_magnet(demo_files)
    mesh = _mesh(m)
    FDTD, CSX, _ = _model(m, mesh)
    props = {p.name: p for p in CSX.GetAllProperties()}
    f = props['solid1_puck']
    assert isinstance(f, C.FerriteMaterial)
    assert isinstance(props['solid2_magnet'], C.Metal)
    assert f.epsilon == 15.0 and f.kappa > 0
    assert f.ms == pytest.approx(1200 * 1e-4 / mg.MU0)
    assert f.alpha == pytest.approx(2.8e6 * 30 / (2 * 9.4e9), rel=2e-3)
    t = f.bias
    x = np.asarray(mesh['x'])
    mids = 0.5 * (x[1:] + x[:-1])
    assert set(np.round(t['x'], 9)) <= set(np.round(mids, 9))
    # the table is core.magnets.magnetisation's at those points
    k = len(t['x']) // 2, len(t['y']) // 2, len(t['z']) // 2
    p = np.array([[t['x'][k[0]], t['y'][k[1]], t['z'][k[2]]]])
    from kicad2ems.core.solids import pieces
    h, mm = mg.magnetisation(m, pieces(m)).at(p)
    assert t['hz'][k] == pytest.approx(h[0, 2], rel=1e-9) and h[0, 2] > 0
    assert t['mz'][k] == pytest.approx(mm[0, 2], rel=1e-9)
    assert f.freq > 0      # the band centre: Green and Sandy's mu there
    op = build(CSX, FDTD.settings())
    fe = op.ferrite
    assert fe is not None and fe.nc > 0 and fe.na == 3
    assert (fe.u[:, 2] > 0).all() and (fe.mr > 0).all()
    m2 = _puck_magnet(demo_files, br=-1.2)
    FDTD2, CSX2, _ = _model(m2, mesh)
    t2 = [p for p in CSX2.GetAllProperties()
          if isinstance(p, C.FerriteMaterial)][0].bias
    for c in ('hx', 'hy', 'hz', 'mx', 'my', 'mz'):
        np.testing.assert_allclose(t2[c], -t[c], rtol=1e-6,
                                   atol=1e-6 * np.abs(t[c]).max())


def test_an_unbiased_ferrite_is_a_dielectric(demo_files):
    """No magnet drawn: the ferrite is its er and loss (every solver can
    model it), no FerriteMaterial."""
    from kicad2ems.gfdtd import csx as C
    m = _puck_magnet(demo_files, magnet=False)
    FDTD, CSX, _ = _model(m, _mesh(m))
    p = {q.name: q for q in CSX.GetAllProperties()}['solid1_puck']
    assert type(p) is C.Material and p.epsilon == 15.0


def test_openems_and_palace_refuse_a_magnetised_ferrite(demo_files):
    from types import SimpleNamespace
    from kicad2ems.fdtd.openems.solver import OpenEMS
    from kicad2ems.fdtd.gfdtd import GFDTD
    from kicad2ems.fem.palace.solver import Palace
    run = SimpleNamespace(m=_puck_magnet(demo_files), s=SimpleNamespace(),
                          antenna=False)
    for cls in (OpenEMS, Palace):
        why = cls().assess(run)
        assert any('gyrotropic' in w and 'gfdtd' in w for w in why), cls
    assert not any('gyrotropic' in w for w in GFDTD().assess(run))
    run.m = _puck_magnet(demo_files, magnet=False)
    assert not any('gyrotropic' in w for w in OpenEMS().assess(run))


def _can(demo_files, *more):
    """The demo board in a drawn can (a metal box, an air box inside it)."""
    can = _solid(box(-2, -12, 26, 2), 'metal', -0.3, 2.0, 'can')
    air = _solid(box(-1.5, -11.5, 25.5, 1.5), 'air', 0.0, 1.7, 'inside')
    return _demo(demo_files, [can, air, *more])


def test_lossy_metal_faces_in_the_model_and_the_operator(demo_files):
    """--sigma: the can's inner faces are conducting sheets (one side of a
    conductor FACE_DEPTHS skin depths thick: sigma / 4, twice that), its outer
    ones (the mesh's ends) none; its sides PEC, not Mur (whose next cell keeps
    sheets PEC). In the operator the walls' inner faces are sheet edges, PEC
    when lossless."""
    from kicad2ems.gfdtd.operator import build
    from kicad2ems.gfdtd import csx as C
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    from kicad2ems.fdtd import faces
    m = _can(demo_files)
    mesh = _mesh(m)
    assert mesh['enclosure'] == [-2.0, -12.0, -0.3, 26.0, 2.0, 2.0]
    built = {}
    for sg in (None, 5.8e7):
        FDTD, CSX, _ = build_fdtd(m, mesh, 1e9, 4e9, 0, sigma=sg,
                                  bc=resolve_bc('MUR', m),
                                  api=engine_api('gfdtd:numpy'))
        built[sg] = (FDTD, CSX)
    props = {p.name: p for p in built[5.8e7][1].GetAllProperties()}
    f = props['solid_metal_faces']
    assert isinstance(f, C.ConductingSheet)
    f_fit = 2.5e9 + 0.5 * 3e9 * 1.05
    t = faces.FACE_DEPTHS * faces.skin_depth(5.8e7, f_fit)
    assert f.conductivity == 5.8e7 / 4 and f.thickness == pytest.approx(2 * t)
    zs = sorted(q.elevation for q in f.primitives
                if isinstance(q, C.Polygon))
    assert zs == [0.0, 1.7]  # the floor's top, the lid's underside
    planes = sorted((a, q.start[a]) for q in f.primitives
                    if isinstance(q, C.Box) for a in range(2)
                    if q.start[a] == q.stop[a])
    assert planes == [(0, -1.5), (0, 25.5), (1, -11.5), (1, 1.5)]
    assert built[5.8e7][0].settings()['bc'] == ['PEC'] * 6
    assert built[None][0].settings()['bc'] == ['MUR'] * 6
    assert not any('solid_metal' in p.name
                   for p in built[None][1].GetAllProperties())
    x, y, z = (np.asarray(mesh[a]) for a in 'xyz')
    at = lambda v, w: int(np.argmin(np.abs(v - w)))
    k = int(np.flatnonzero((z[:-1] >= 0.6 - 1e-9) & (z[1:] <= 1.7 + 1e-9))[0])
    edge = (2, at(x, -1.5), at(y, -5.0), k)  # a z edge on the wall's face
    ops = {sg: build(CSX, FDTD.settings())
           for sg, (FDTD, CSX) in built.items()}
    assert ops[None].vi[edge] == 0 and ops[5.8e7].vi[edge] != 0
    assert ops[5.8e7].n_sheet > ops[None].n_sheet == 0


def test_the_faces_the_field_reaches():
    """faces.faces: a block's six faces; a face against more metal none; a
    wall one line thick one two-sided sheet of its thickness; a magnet's
    apart; a round post's sides none (its staircase stays PEC), its top and
    bottom faces."""
    from shapely.geometry import Point
    from kicad2ems.core.solids import Piece
    from kicad2ems.fdtd import faces

    def pc(g, z0, z1, magnet=None, i=0):
        return Piece('metal', 1.0, 0.0, g, z0, z1, i, f'p{i}', magnet=magnet)
    ln = [np.arange(-5, 15.01, 0.5), np.arange(-5, 15.01, 0.5),
          np.arange(-2, 6.01, 0.5)]
    fs = faces.faces([pc(box(0, 0, 2, 3), 0, 1)], ln, fine=0.2)
    got = sorted((f.axis, f.c, round(f.geom.area, 6)) for f in fs)
    assert got == [(0, 0.0, 3.0), (0, 2.0, 3.0), (1, 0.0, 2.0),
                   (1, 3.0, 2.0), (2, 0.0, 6.0), (2, 1.0, 6.0)]
    assert not any(f.two or f.magnet for f in fs)
    # a block on another: their shared face none, the lower's top round it
    fs = faces.faces([pc(box(0, 0, 4, 4), 0, 1), pc(box(1, 1, 3, 3), 1, 2,
                                                    i=1)], ln, fine=0.2)
    top = [f for f in fs if f.axis == 2 and f.c == 1.0]
    assert len(top) == 1 and top[0].geom.area == pytest.approx(16 - 4)
    # a wall 0.1 mm thick between lines 0.5 apart: on the line inside it, one
    # sheet with field on both sides
    fs = faces.faces([pc(box(0.95, 0, 1.05, 3), 0, 1)], ln, fine=0.05)
    two = [f for f in fs if f.two]
    assert ([(f.axis, f.c, round(f.t, 6)) for f in two]
            == [(0, 1.0, 0.1)])
    # a magnet: its faces apart, the one against the other metal none
    fs = faces.faces([pc(box(0, 0, 2, 2), 0, 1),
                      pc(box(2, 0, 4, 2), 0, 1, magnet=1.2, i=1)], ln,
                     fine=0.2)
    assert {f.magnet for f in fs} == {True, False}
    assert not [f for f in fs if f.axis == 0 and f.c == 2.0]
    # a round post: top and bottom only
    fs = faces.faces([pc(Point(5, 5).buffer(1.0, quad_segs=16), 0, 1)], ln,
                     fine=0.2)
    assert sorted(f.axis for f in fs) == [2, 2]
    # the box's planes: none there
    fs = faces.faces([pc(box(0, 0, 2, 3), 0, 1)], ln, fine=0.2,
                     box=(0, 0, 0, 2, 3, 1))
    assert fs == []


def test_a_foil_is_a_conducting_sheet_of_its_thickness(demo_files):
    """A metal foil with no z line across it is a sheet on its nearest line:
    with --sigma openEMS's sheet of its own thickness (field on both sides);
    one of no height stays PEC."""
    from kicad2ems.gfdtd import csx as C
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    block = _solid(box(20, -8, 23, -2), 'metal', 1.6, 1.8, 'block')
    foil = _solid(box(5, -8, 19, -2), 'metal', 1.65, 1.66, 'foil')
    leaf = _solid(box(5, -1.5, 19, -0.5), 'metal', 1.7, 1.7, 'leaf')
    m = _demo(demo_files, [block, foil, leaf])
    mesh = _mesh(m)
    _, CSX, _ = build_fdtd(m, mesh, 1e9, 4e9, 0, bc=resolve_bc('MUR', m),
                           sigma=5.8e7, api=engine_api('gfdtd:numpy'))
    props = {p.name: p for p in CSX.GetAllProperties()}
    s = props['solid2_foil_foil']
    assert isinstance(s, C.ConductingSheet)
    assert (s.conductivity, s.thickness) == (5.8e7, pytest.approx(1e-5))
    q, = s.primitives
    assert type(q) is C.Polygon and q.elevation == 1.6
    q, = props['solid3_leaf'].primitives
    assert isinstance(props['solid3_leaf'], C.Metal) and type(q) is C.Polygon


def _fence(n=40, pitch=0.147, r=0.06, y=-5.0):
    """A row of n small metal posts through the demo board's stack."""
    from shapely.geometry import Point
    return [_solid(Point(4 + k * pitch, y).buffer(r, quad_segs=8), 'metal',
                   0.0, 0.6, f'post{k + 1}') for k in range(n)]


def test_small_posts_are_snapped_as_ground_vias(demo_files):
    """A fence of small metal posts (0.12 mm at 0.147 mm): no x / y lines of
    their own (--vias auto, snap), each moved onto the nearest line crossing
    (the model's piece too), a line every 2 --via-snap at most for those no
    line is near; --vias exact gives them their faces' lines (merged at fine
    / 2: one every 0.147 mm). The move is logged."""
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.mesh import auto_mesh
    from kicad2ems.core.solids import pieces
    from kicad2ems.gfdtd import csx as C

    def mesh(m, **kw):
        return auto_mesh(m, 1e9, 4e9, res=8, fine=0.1, air=1.0, nz_min=2,
                         bc=resolve_bc('MUR', m), **kw)
    plain = mesh(_demo(demo_files, []), log=lambda *a: None)
    m = _demo(demo_files, _fence())
    logs = []
    snap = mesh(m, log=logs.append)
    exact = mesh(m, vias='exact', log=lambda *a: None)
    assert len(snap['posts']) == 40 and exact['posts'] == []
    assert snap['info']['posts_snapped'] == 40
    # (each way within --via-snap, or a line's merge further)
    assert max(max(abs(dx), abs(dy)) for _, dx, dy in snap['posts']) <= 0.3
    assert snap['info']['post_max_move_mm'] == pytest.approx(max(
        np.hypot(dx, dy) for _, dx, dy in snap['posts']))
    assert any('drawn posts: 40 snapped' in s for s in logs)
    # along the fence (5.7 mm)
    near = lambda g: np.sum((np.asarray(g['x']) > 3.5)
                            & (np.asarray(g['x']) < 10.5))
    assert near(snap) <= near(plain) + np.ceil(5.73 / 0.4) + 1
    assert near(exact) >= 5.73 / 0.147 > near(snap)
    ps = pieces(m)
    x, y = np.asarray(snap['x']), np.asarray(snap['y'])
    for k, dx, dy in snap['posts']:
        b = ps[k].polygon.bounds
        cx, cy = 0.5 * (b[0] + b[2]) + dx, 0.5 * (b[1] + b[3]) + dy
        assert np.min(np.abs(x - cx)) < 1e-9 and np.min(np.abs(y - cy)) < 1e-9
    _, CSX, _ = _model(m, snap)
    q = next(p for p in CSX.GetAllProperties()
             if p.name == 'solid1_post1').primitives[0]
    k, dx, dy = snap['posts'][0]
    b = ps[k].polygon.bounds
    assert isinstance(q, C.LinPoly)
    assert np.mean(q.points[0]) == pytest.approx(0.5 * (b[0] + b[2]) + dx,
                                                 abs=1e-6)


def test_solids_with_copper_thickness(demo_files):
    """--thickness on: the solids' heights mapped as the stack's (moved up by
    the copper below them that grows up; a face on a copper level at that
    copper's lower face), the can still closing round the board, its lossy
    faces on the mapped lines; a pocket under F.Cu ends at the copper's lower
    face."""
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.mesh import auto_mesh
    from kicad2ems.fdtd import thick
    from kicad2ems.gfdtd import csx as C
    top = 0.6  # the demo's F.Cu
    pocket = _solid(box(10, -6, 14, -4), 'air', top - 0.1, top, 'pocket')
    m = _can(demo_files, pocket)
    g = auto_mesh(m, 1e9, 4e9, res=8, fine=0.4, air=1.0, nz_min=2,
                  bc=resolve_bc('MUR', m), thickness=True, log=lambda *a: None)
    th = g['thick']
    up = sum(t for lay, t in th['t'].items() if th['up'][lay])
    assert g['enclosure'] is not None
    for z in (-0.3, 1.7, 2.0, top - 0.1):
        assert _on(g['z'], thick.z_map(m, th, z))
    assert thick.z_map(m, th, 2.0) == pytest.approx(2.0 + up)
    f_lo = thick.faces(m, th, 'F.Cu')[0]
    assert f_lo == pytest.approx(thick.z_map(m, th, top))
    FDTD, CSX, _ = _model_sigma(m, g)
    props = {p.name: p for p in CSX.GetAllProperties()}
    zs = sorted(q.elevation for q in props['solid_metal_faces'].primitives
                if isinstance(q, C.Polygon))
    assert zs == [0.0, pytest.approx(1.7 + up)]
    q, = props['solid3_pocket'].primitives
    assert (q.elevation, q.elevation + q.length) == (
        pytest.approx(top - 0.1 + up - th['t']['F.Cu']), pytest.approx(f_lo))


def _model_sigma(m, mesh):
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    return build_fdtd(m, mesh, 1e9, 4e9, 0, bc=resolve_bc('MUR', m),
                      sigma=5.8e7, api=engine_api('gfdtd:numpy'))
