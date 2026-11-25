"""gfdtd's connection (kicad2ems.fdtd.gfdtd): registered, its engine, and the
FDTD model built through gfdtd's API. Needs a kicad2ems checkout
(conftest.py)."""
import os

import pytest

gfdtd = pytest.importorskip('kicad2ems.gfdtd')  # the [gfdtd] extra
from kicad2ems.gfdtd.operator import build  # noqa: E402


def test_registered():
    from kicad2ems.core.solvers import registry, get
    sv = registry()['gfdtd']
    assert get('gpu-fdtd') is sv and sv.in_auto and not sv.in_all
    assert sv.check()[0]  # numpy at worst
    assert sv.capabilities['antenna'].startswith('yes')
    # its own, last
    assert ([o.key for o in sv.options[-3:]
             ] == ['gfdtd_backend', 'gfdtd_gpus', 'gfdtd_timestep'])
    shared = {o.key: o for o in registry()['openems'].options}
    assert all(shared[o.key] is o for o in sv.options[:-3])


def test_engine_api_and_run_dir():
    from kicad2ems.fdtd.engines import engine_api, engine_name
    from kicad2ems.fdtd.run import run_dir
    assert (engine_name(None) == ('openems', 'openEMS')
            and engine_name('gfdtd:gpu') == ('gfdtd', 'gfdtd'))
    with pytest.raises(ValueError, match='unknown FDTD engine'):
        engine_api('hfss')
    cs, fd = engine_api('gfdtd:numpy')
    assert cs is gfdtd.ContinuousStructure and fd().backend == 'numpy'

    class P:
        name = 'P1+'

    class M:
        ports = [P()]
    assert run_dir(M, 0, '/o').endswith(os.path.join('openems', 'exc_P1p'))
    assert run_dir(M, 0, '/o',
                   'gfdtd:gpu').endswith(os.path.join('gfdtd', 'exc_P1p'))


def test_build_fdtd_builds_the_board_on_gfdtd(demo_files):
    """kicad2ems's openEMS model, built through gfdtd's API, lowers to an
    operator: ports, copper, vias."""
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    from kicad2ems.fdtd.mesh import auto_mesh
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, log=lambda *a: None)
    FDTD, CSX, ports = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc,
                                  api=engine_api('gfdtd:numpy'))
    op = build(CSX, FDTD.settings())
    assert len(ports) == len(m.ports) == 2 and len(op.exc[0]) > 0
    assert op.n_pec[2] > 0  # the vias' z edges
    assert len(op.probes) == 4 and op.bc == bc


def test_sigma_is_lossy_sheets_on_gfdtd(demo_files):
    """--sigma: conducting sheets on gfdtd (lossy there), and auto keeps gfdtd
    for such runs."""
    from types import SimpleNamespace
    from kicad2ems.core.solvers import AUTO
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    from kicad2ems.fdtd import gfdtd as conn
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    from kicad2ems.fdtd.mesh import auto_mesh
    run = SimpleNamespace(plan=[AUTO],
                          s=SimpleNamespace(sigma=5.8e7, converge=None))
    sv = conn.GFDTD()
    assert (sv.conductor_loss and sv.assess(run) == []
            and sv.capabilities['conductor_loss'].startswith('yes'))
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, log=lambda *a: None)
    FDTD, CSX, _ = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, sigma=5.8e7,
                              api=engine_api('gfdtd:numpy'))
    op = build(CSX, FDTD.settings())
    assert op.n_sheet > 0 and any('conducting sheet cu_' in n for n in op.notes)


def test_rough_copper_by_engine(demo_files):
    """--rough: gfdtd gets it in its sheets (Hammerstad over the band); openEMS
    the conductivity at the band centre (band_sigma)."""
    from types import SimpleNamespace
    from kicad2ems.core.roughness import band_sigma
    from kicad2ems.core.solvers import registry
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    from kicad2ems.fdtd import gfdtd as conn
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    from kicad2ems.fdtd.mesh import auto_mesh
    s = SimpleNamespace(sigma=5.8e7, rough=1.0)
    run = SimpleNamespace(s=s, fmin=1e9, fmax=9e9)
    centre = band_sigma(s, 1e9, 9e9)
    assert centre < 5.8e7 / 1.5  # K(5 GHz, 1 um) ~ 1.65
    assert registry()['openems'].copper(run) == (centre, None)
    sv = conn.GFDTD()
    assert (sv.copper(run) == (5.8e7, 1.0)
            and sv.capabilities['roughness'].startswith('yes'))
    assert (sv.copper(
        SimpleNamespace(s=SimpleNamespace(sigma=None, rough=1.0), fmin=1e9,
                        fmax=9e9)) == (None, None))
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu')
    bc = resolve_bc('auto', m)
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, log=lambda *a: None)
    _, CSX, _ = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, sigma=5.8e7, rough=1.0,
                           api=engine_api('gfdtd:numpy'))
    sheets = [
        p for p in CSX.GetAllProperties()
        if type(p).__name__ == 'ConductingSheet'
    ]
    assert sheets and all(p.roughness == pytest.approx(1e-6) for p in sheets)


def test_finished_copper_by_engine(demo_files):
    """--finish: gfdtd gets it in its sheets (thick copper:
    the outer face's own sheet, all finished; sheets: weighted by the strip's
    share) and marks thick copper's sheets as faces; openEMS the conductivity of
    the same loss at the band centre."""
    from types import SimpleNamespace
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    from kicad2ems.core.solvers import registry
    from kicad2ems.fdtd import gfdtd as conn
    from kicad2ems.fdtd.boundaries import resolve_bc
    from kicad2ems.fdtd.model import build_fdtd
    from kicad2ems.fdtd.engines import engine_api
    from kicad2ems.fdtd.mesh import auto_mesh
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu')
    s = SimpleNamespace(sigma=5.8e7, rough=None, finish='enig', mask=False)
    run = SimpleNamespace(s=s, m=m, fmin=1e9, fmax=8e9)
    ckw = conn.GFDTD().copper_kw(run)
    top = m.cu_order[-1]
    assert (ckw['faces'] and ckw['finish']['wideband']
            and ckw['finish']['share'][top] > 0.3)
    bc = resolve_bc('auto', m)
    sheets = lambda CSX: {
        p.name: p
        for p in CSX.GetAllProperties()
        if type(p).__name__ == 'ConductingSheet'
    }
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, thickness=True,
                     log=lambda *a: None)
    _, CSX, _ = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, sigma=5.8e7,
                           copper_kw=ckw, api=engine_api('gfdtd:numpy'))
    sh = sheets(CSX)
    assert (sh[f'cu_{top}_finish'].finish
            and sh[f'cu_{top}_finish'].finish_share == 1.0
            and sh[f'cu_{top}'].face)
    assert not sh[f'cu_{top}'].finish
    mesh = auto_mesh(m, 1e9, 8e9, bc=bc, res=10, log=lambda *a: None)
    _, CSX, _ = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, sigma=5.8e7,
                           copper_kw=ckw, api=engine_api('gfdtd:numpy'))
    sh = sheets(CSX)
    assert (sh[f'cu_{top}'].finish_share == pytest.approx(
        ckw['finish']['share'][top]))
    oc = registry()['openems'].copper_kw(run)
    assert not oc['faces'] and not oc['finish']['wideband']
    _, CSX, _ = build_fdtd(m, mesh, 1e9, 8e9, 0, bc=bc, sigma=5.8e7,
                           copper_kw=oc, api=engine_api('gfdtd:numpy'))
    assert (sheets(CSX)[f'cu_{top}'].conductivity < 5.8e7 / 1.5
            and not sheets(CSX)[f'cu_{top}'].finish)


def test_gfdtd_runs_the_mesh_convergence_check():
    """--converge is the FDTD layer's: gfdtd has its options and levels, and
    auto no longer hands such runs to openEMS (its code was the openEMS
    subclass's, so gfdtd said 'not supported yet - ignored')."""
    from types import SimpleNamespace
    from kicad2ems.core.solvers import AUTO
    from kicad2ems.fdtd import gfdtd as conn
    from kicad2ems.fdtd.openems.solver import OpenEMS
    sv = conn.GFDTD()
    assert ({'converge', 'converge_tol'} <= {o.key
                                             for o in sv.options}
            and {o.key
                 for o in OpenEMS().options} >= {'converge'})
    s = SimpleNamespace(converge=[1.0, 1.5], antenna=False, sigma=None)
    assert (sv.levels(s) == 2 and sv.validate(s) is None
            and sv.assess(SimpleNamespace(plan=[AUTO], s=s)) == [])
    assert ('two positive'
            in sv.validate(SimpleNamespace(converge=[1.0], antenna=False)))


def test_the_excitations_go_out_to_the_gpus_named(monkeypatch):
    """R-D9: --gfdtd-gpus all or indices, a process each (threads ran each GPU
    at a fifth of its speed); fewer than two usable, or not asked: None (one
    GPU); at most a GPU an excitation."""
    import kicad2ems.gfdtd.engine as E
    from kicad2ems.fdtd import gfdtd as G
    monkeypatch.setattr(E, 'vulkan_gpus', lambda: [(0, 'RX'), (1, 'GTX'),
                                                   (2, 'Arc')])
    assert (G.gpu_slots('', 4) is None and G.gpu_slots('all', 4) == [0, 1, 2]
            and G.gpu_slots('all', 2) == [0, 1])
    assert (G.gpu_slots('1,0', 4) == [1, 0] and G.gpu_slots('1', 4) is None
            and G.gpu_slots('all', 1) is None)
    with pytest.raises(ValueError, match='no usable'):
        G.gpu_slots('0,5', 4)
    with pytest.raises(ValueError, match='indices'):
        G.gpu_slots('first', 4)
    import multiprocessing
    ctx = multiprocessing.get_context('spawn')
    counter, lock = ctx.Value('i', 0), ctx.Lock()
    monkeypatch.delenv('EMSDEV_VULKAN_DEVICE', raising=False)
    G.bind_process([1, 0], counter, lock)
    assert os.environ['EMSDEV_VULKAN_DEVICE'] == '1'
    G.bind_process([1, 0], counter, lock)
    assert os.environ['EMSDEV_VULKAN_DEVICE'] == '0' and counter.value == 2



@pytest.mark.parametrize('before', [None, '0'])
def test_the_gpu_a_solve_chose_is_given_back_at_its_end(monkeypatch, before):
    """jobs() binds the solve to the one GPU the speeds chose
    (EMSDEV_VULKAN_DEVICE): given back when the solve ends, a failed one too
    (it had stayed set, and the next solves took that GPU)."""
    from kicad2ems.fdtd import gfdtd as G
    from kicad2ems.fdtd.solver import FDTD
    if before is None:
        monkeypatch.delenv('EMSDEV_VULKAN_DEVICE', raising=False)
    else:
        monkeypatch.setenv('EMSDEV_VULKAN_DEVICE', before)

    def solve(self, run, p):
        os.environ['EMSDEV_VULKAN_DEVICE'] = '1'
        if p == 'fail':
            raise RuntimeError('the run failed')
        return 'S'
    monkeypatch.setattr(FDTD, 'solve', solve)
    sv = G.GFDTD.__new__(G.GFDTD)
    assert sv.solve(None, 'ok') == 'S'
    assert os.environ.get('EMSDEV_VULKAN_DEVICE') == before
    with pytest.raises(RuntimeError):
        sv.solve(None, 'fail')
    assert os.environ.get('EMSDEV_VULKAN_DEVICE') == before


def test_the_runs_shared_by_each_gpu_s_speed(tmp_path, monkeypatch):
    """R-D9: each run to the GPU that would finish it first, by the speeds
    measured before (kept by GPU name): a GPU 4.7 times slower gets none of two
    runs and one of six; GPUs never measured share alike."""
    from kicad2ems.fdtd import gfdtd as conn
    fast, slow = 9300.0, 1970.0
    assert conn.share([0, 1], 2, {0: fast, 1: slow}) == {0: [0, 1], 1: []}
    got = conn.share([0, 1], 6, {0: fast, 1: slow})
    assert len(got[0]) == 5 and len(got[1]) == 1
    assert conn.share([0, 1], 4, {0: None, 1: None}) == {0: [0, 2], 1: [1, 3]}
    # (the new one measured)
    assert conn.share([0, 1], 2, {0: fast, 1: None}) == {0: [0], 1: [1]}
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    monkeypatch.setattr('kicad2ems.gfdtd.engine.vulkan_gpus',
                        lambda: [(0, 'Fast GPU'), (1, 'Slow GPU')])
    assert conn.gpu_speeds([0, 1]) == {0: None, 1: None}
    conn.record_speed(1, 2000.0)
    conn.record_speed(1, 1940.0)
    assert conn.gpu_speeds([0, 1]) == {0: None, 1: 1970.0}


def test_jobs_on_the_gpu_are_as_many_as_its_memory_holds(monkeypatch):
    """R-D7: --jobs N on Vulkan runs at most as many excitations at once as the
    GPU's free memory holds (each run's fields its own); unknown memory, or
    the CPU: N."""
    from types import SimpleNamespace
    import kicad2ems.gfdtd.engine as E
    from kicad2ems.fdtd import gfdtd as G
    assert E.runs_that_fit(50e6, 8, 10e9) == 6
    assert E.runs_that_fit(1e6, 8, 10e9) == 8 == E.runs_that_fit(50e6, 8, None)
    assert E.runs_that_fit(500e6, 8, 10e9) == 1
    said = []
    run = SimpleNamespace(s=SimpleNamespace(gfdtd_backend='gpu', gfdtd_gpus='',
                                            jobs=8), log=said.append)
    p = SimpleNamespace(grid={'info': {'cells': 50e6}}, excite=list(range(8)))
    monkeypatch.setattr(E, 'resolve_backend', lambda name: 'vulkan')
    monkeypatch.setattr(G, '_vram_free', lambda: 10e9)
    assert G.GFDTD().jobs(run, p) == 6 and 'holds 6 run(s)' in said[-1]
    monkeypatch.setattr(G, '_vram_free', lambda: None)
    assert G.GFDTD().jobs(run, p) == 8
    monkeypatch.setattr(E, 'resolve_backend', lambda name: 'core')
    monkeypatch.setattr(G, '_vram_free', lambda: 1.0)
    assert G.GFDTD().jobs(run, p) == 8


def test_an_antenna_s_estimate_has_its_own_air_and_pulse(monkeypatch):
    """estimate meshes an antenna with antenna_air (lambda / 4 at the band
    centre), as prepare does, and its pulse reaches exc_fmax (s.air and the
    band's pulse had put its cells and time low, and --solvers auto with
    them); a board's run keeps s.air."""
    from types import SimpleNamespace
    from kicad2ems.fdtd import mesh, model
    from kicad2ems.fdtd import solver as S
    from kicad2ems.fdtd.antenna import antenna_air, exc_fmax
    got = {}

    def fake_mesh(m, fmin, fmax, air=None, **kw):
        got['air'] = air
        return dict(info=dict(cells=1e6, dt_ps=1.0, mem_gb=1.0))

    def fake_pulse(fmin, fmax, dt_ps, exc=None):
        got['exc'] = exc
        return 1000
    monkeypatch.setattr(mesh, 'auto_mesh', fake_mesh)
    monkeypatch.setattr(model, 'pulse_steps', fake_pulse)
    monkeypatch.setattr(model, 'port_index', lambda m, e: [0])
    monkeypatch.setattr(S, '_peak_delay', lambda run: None)
    monkeypatch.setattr(S, 'mesh_zone', lambda s: None)
    import kicad2ems.fdtd.boundaries as B
    monkeypatch.setattr(B, 'resolve_bc', lambda bc, m: bc)
    sv = S.FDTD.__new__(S.FDTD)
    for antenna in (True, False):
        s = SimpleNamespace(antenna=antenna, bc='auto', air=None, res=None,
                            fine=None, ratio=1.4, nz=None, curves=None,
                            vias=None, via_snap=None, z_sheet=None,
                            curve_res=None, thickness='off', nz_mask=None,
                            end_db=-40, max_steps=10**6, excite=None)
        sv.estimate(SimpleNamespace(s=s, m=None, fmin=1e9, fmax=3e9))
        if antenna:
            assert got['air'] == antenna_air(1e9, 3e9)
            assert got['exc'] == exc_fmax(1e9, 3e9)
        else:
            assert got['air'] is None and got['exc'] is None
