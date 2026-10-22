"""The gfdtd solver plugin: registered, its engine API, and kicad2ems's openEMS model built through gfdtd's API.
gfdtd's own tests (the engine, openEMS compatibility, the physics) are in its repository."""
import os

import pytest

gfdtd = pytest.importorskip('gfdtd')
from gfdtd.operator import build                                       # noqa: E402


def test_plugin_registered():
    from kicad2ems.solvers import registry, get
    sv = registry()['gfdtd']
    assert get('gpu-fdtd') is sv and sv.in_auto and not sv.in_all
    assert sv.check()[0]                                              # numpy at worst
    assert sv.capabilities['antenna'].startswith('yes')
    assert [o.key for o in sv.options] == ['gfdtd_backend']


def test_engine_api_and_run_dir():
    from kicad2ems.solvers.openems.simulation import engine_api, run_dir
    assert engine_api(None) is None and engine_api('openems') is None
    cs, fd = engine_api('gfdtd:numpy')
    assert cs is gfdtd.ContinuousStructure and fd().backend == 'numpy'

    class P:
        name = 'P1+'

    class M:
        ports = [P()]
    assert run_dir(M, 0, '/o').endswith(os.path.join('openems', 'exc_P1p'))
    assert run_dir(M, 0, '/o', 'gfdtd:cupy').endswith(os.path.join('gfdtd', 'exc_P1p'))


def test_build_fdtd_builds_the_board_on_gfdtd(demo_files):
    """kicad2ems's openEMS model, built through gfdtd's API, lowers to an operator: ports, copper, vias."""
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    from kicad2ems.solvers.openems.boundaries import resolve_bc
    from kicad2ems.solvers.openems.simulation import build_fdtd, engine_api
    from kicad2ems.solvers.openems.mesh import auto_mesh
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, log=lambda *a: None)
    FDTD, CSX, ports = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, api=engine_api('gfdtd:numpy'))
    op = build(CSX, FDTD.settings())
    assert len(ports) == len(m.ports) == 2 and len(op.exc[0]) > 0
    assert op.n_pec[2] > 0                                            # the vias' z edges
    assert len(op.probes) == 4 and op.bc == bc
