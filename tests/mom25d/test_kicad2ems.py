"""mom25d as a solver: it gets the board itself (no CSX file, no openEMS), --mom
/ --solvers mom end to end, and --sigma as conductor loss."""
import json, os
import numpy as np
import pytest

from k2e_fixtures import ROOT  # kicad2ems's checkout (conftest.py)

FILTER = os.path.join(ROOT, 'examples', 'rf_interdig.toml')


def test_import():
    from kicad2ems import mom25d
    from kicad2ems.mom25d.board import BoardModel  # noqa: F401
    assert hasattr(mom25d, '__version__')


def test_preview_solves_no_full_wave_solver(tmp_path, run_cli):
    """--preview builds the model and the meshes and solves only the line model:
    mom25d named (or added with --mom) is not run."""
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--preview', '--solvers', 'mom25d', '--mom',
                           '--points', '41', '--out', str(out))
    assert code == 0, so + se
    # the line model's
    assert (not (out / 'rf_interdig_mom25d.s2p').exists()
            and list(out.glob('rf_interdig_*.s2p')))


def test_mom_run(tmp_path, run_cli, no_csxcad):
    """--solvers mom25d end to end with no openEMS (MoM reads the board), and
    the line model the config's fast = "auto" picks compared against it."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--solvers', 'mom25d', '--mom-dmax', '1.6',
                           '--mom-solves', '8', '--points', '41', '--out',
                           str(out))
    assert code == 0, so + se
    sm = json.load(open(out / 'rf_interdig_run.json', encoding='utf-8'))
    # qucs or scikit, whichever is installed
    line = next(n for n in sm['solved'] if n != 'mom25d')
    for f in ('rf_interdig_mom25d.s2p', 'rf_interdig_mom25d.png',
              f'rf_interdig_{line}.s2p', 'rf_interdig_report.html',
              'rf_interdig_board.json'):
        assert (out / f).exists(), f
    mo = sm['solved']['mom25d']
    assert (mo['solves'] <= 8 and mo['unknowns'] > 100
            and {p['kind']
                 for p in mo['ports']} == {'lumped'})
    assert (mo['table'][0]['param'] == 'S(P1,P1)'
            and sm['results']['reference'] == 'mom25d')
    assert (sm['solved'][line]['reference'] == 'mom25d'
            and sm['solved'][line]['compare'])
    samples = mo['samples_ghz']  # the adaptive sweep's frequencies
    assert len(samples) == mo['solves'] and samples == sorted(samples)
    assert 'mom25d' in so


def test_sigma_reaches_mom(demo_files):
    """--sigma is conductor loss in openEMS's conducting sheets; MoM gets the
    same, with the copper thickness of the ports' layer."""
    from kicad2ems.mom25d.solvers.mom import _metal
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.board import Board
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'],
              pos_file=base + '-top.pos', ground='B.Cu', log=lambda *a: None)
    assert _metal(m, None) == (None, None)
    metal, t = _metal(m, 5.8e7)
    assert (metal.sigma == 5.8e7
            and t == pytest.approx(m.cu_t[m.ports[0].layer])
            and metal.t == pytest.approx(t * 1e-3))
    zs = metal.zs(np.array([1e9]))
    # a lossy surface impedance
    assert zs.real[0] > 0
    # --rough 1 um: Hammerstad on the resistance
    rough, _ = _metal(m, 5.8e7, 1.0)
    f = np.array([1e6, 5e9, 1e12])
    k = rough.zs(f).real / metal.zs(f).real
    delta = 1 / np.sqrt(np.pi * f * 4e-7 * np.pi * 5.8e7)
    np.testing.assert_allclose(
        k, 1 + 2 / np.pi * np.arctan(1.4 * (1e-6 / delta)**2))
    assert (k[0] == pytest.approx(1, abs=0.01)
            and k[2] == pytest.approx(2, abs=0.01))
    np.testing.assert_allclose(rough.zs(f).imag, metal.zs(f).imag)


def test_current_dumps(boards, run_cli, tmp_path, monkeypatch):
    """--mom-dump: mom25d's surface currents at the frequencies asked, a |J| map
    each, with --dump-animate one period of Re(J e^j phase), and the dumps where
    the GUI's Fields tab finds them. The sweep solves at the dump frequencies
    among its samples and the dumps reuse those currents: no solve of their
    own."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    from kicad2ems.core.fields import frames as F
    from kicad2ems.mom25d.sim import Simulation
    save = F.save_fd_outputs
    # 4 frames, not 24
    monkeypatch.setattr(F, 'save_fd_outputs',
                        lambda *a, **kw: save(*a, n_phase=4, **kw))
    calls, currents = [], Simulation.currents
    monkeypatch.setattr(
        Simulation, 'currents', lambda self, f, E:
        (calls.append(f), currents(self, f, E))[1])
    base, out = boards['lowpass'], tmp_path / 'out'
    code, so, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos',
                            '--solvers', 'mom', '--mom-dmax', '1.6',
                            '--mom-solves', '6', '--points', '21', '--mom-dump',
                            '2,5', '--dump-animate', 'gif', '--mom-near', '1',
                            '--out', out)
    assert code == 0, so + err
    for f in (2, 5):
        for ext in ('png', 'gif'):
            assert (
                out
                / f"lowpass_mom25d_P1_dump_J_F_Cu_fd_{f}GHz.{ext}").exists()
    # sorted by name: E@top+1mm, H@top+1mm, J@F.Cu
    E, Hn, src = F.find_dumps(str(out))
    assert (src.solver == 'mom25d' and src.freqs == [2e9, 5e9]
            and 'A/m' in src.unit)
    assert E.field == 'E' and 'V/m' in E.unit and np.abs(E.load(0)[1]).max() > 0
    # --mom-near: H as well
    assert (Hn.field == 'H' and 'A/m' in Hn.unit
            and np.abs(Hn.load(0)[1]).max() > 0)
    assert (out / 'lowpass_mom25d_P1_dump_E_topp1mm_fd_5GHz.gif').exists()
    mesh, J = src.load(0)
    # in-plane currents only
    assert np.abs(J).max() > 0 and np.all(J[..., 2] == 0)
    sm = json.loads((out / 'lowpass_run.json').read_text(encoding='utf-8'))
    assert any(d['label'].startswith('mom25d dump_J_F_Cu_fd')
               for d in sm['dumps'])
    mom = sm['solved']['mom25d']
    assert ({2.0, 5.0} <= set(mom['samples_ghz'])
            and len(calls) == mom['solves']), (calls, mom['solves'])


def test_antenna_mode(run_cli, tmp_path):
    """mom25d in antenna mode on the patch example: the resonance, efficiency
    below 1, and openEMS's antenna outputs (JSON, plots, the sphere the GUI
    reads)."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    import subprocess, sys
    ex = os.path.join(os.path.dirname(FILTER), 'make_antennas.py')
    subprocess.check_call(
        [sys.executable, ex, 'patch',
         str(tmp_path / 'patch')], stdout=subprocess.DEVNULL)
    out = tmp_path / 'out'
    code, so, err = run_cli(tmp_path / 'patch.kicad_pcb', '--mode', 'antenna',
                            '--fmin', '2.3', '--fmax', '2.6', '--points', '61',
                            '--solvers', 'mom', '--mom-dmax', '2',
                            '--mom-solves', '12', '--out', out)
    assert code == 0, so + err
    res = json.loads(
        (out / 'patch_mom25d_antenna.json').read_text(encoding='utf-8'))
    assert 2.38e9 < res['match']['f_res'] < 2.52e9 and res['solver'] == 'mom25d'
    row = min(res['rows'], key=lambda r: abs(r['f'] - res['f_pattern']))
    assert 0.1 < row['eff'] < 1 and 5 < row['D_dbi'] < 9
    assert (set(
        res['cuts']) == {'xz (phi = 0)', 'yz (phi = 90)', 'xy (theta = 90)'})
    for f in ('patch_mom25d_antenna.png', 'patch_mom25d_pattern.png',
              'patch_mom25d_sphere.npz'):
        assert (out / f).exists(), f


def test_phased_array(run_cli, tmp_path):
    """The 2 x 2 array with a feed per row placed as a 1 x 2 grid (--grid),
    driven with P2 20 deg behind P1 (--drive) and steered by a Y step of 40 deg
    on top (--steer 0,40) with a taper (--taper: two rows, so uniform):
    P2 60 deg behind P1, the beam tilted in the yz plane, the run's base drive,
    grid, taper and steps recorded for the GUI's Reset (the grid in its short
    form, 1x2), the per-port fields kept, and recombining them (phased.combine)
    gives the run's result back and broadside for the drive in phase."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    import subprocess, sys
    from kicad2ems.core import phased
    ex = os.path.join(os.path.dirname(FILTER), 'make_antennas.py')
    subprocess.check_call(
        [sys.executable, ex, 'array2x2_phased',
         str(tmp_path / 'arr')], stdout=subprocess.DEVNULL)
    out = tmp_path / 'out'
    code, so, err = run_cli(tmp_path / 'arr.kicad_pcb', '--mode', 'antenna',
                            '--drive', 'P1@0,P2@20', '--grid', 'P1:0,0 P2:0,1',
                            '--steer', '0,40', '--taper', 'taylor:30', '--fmin',
                            '2.3', '--fmax', '2.6', '--points', '61',
                            '--solvers', 'mom', '--mom-dmax', '3',
                            '--mom-solves', '12', '--out', out)
    assert code == 0, so + err
    res = json.loads(
        (out / 'arr_mom25d_antenna.json').read_text(encoding='utf-8'))
    assert (res['drive'] == ['P1@0', 'P2@60']
            and res['drive_base'] == ['P1@0', 'P2@20'])
    assert (res['grid'] == '1x2' and res['taper'] == 'taylor:30'
            and res['steer'] == [0.0, 40.0])
    assert 5 <= res['peak']['theta'] <= 25 and res['peak']['phi'] == 90
    assert (res['port'] == 'P1@0, P2@60'
            and set(res['active_db']) == {'P1', 'P2'})
    data = phased.load(out / res['array_file'])
    assert (data['ports'] == ['P1', 'P2']
            and data['Eth'].shape[:2] == (len(data['freq']), 2))
    again = phased.combine(data, data['drive'])
    assert np.allclose([r['G_dbi'] for r in again['rows']],
                       [r['G_dbi'] for r in res['rows']], atol=0.01)
    assert phased.combine(data, [1, 1])['peak']['theta'] <= 5
    code, so, err = run_cli(tmp_path / 'arr.kicad_pcb', '--mode', 'antenna',
                            '--drive', 'P1,P2', '--steer', '0,60', '--solvers',
                            'mom', '--preview', '--out', tmp_path / 'bad')
    assert code != 0 and 'needs grid' in so + err


def test_stop_ends_mom25d_after_its_current_solve(run_cli, tmp_path,
                                                  monkeypatch):
    """The GUI's Stop (a STOP file, kicad2ems.core.stop): mom25d ends after the
    frequency it is solving, with no result; the line model (the --fast
    reference) still gives its. ABORT alone had left mom25d running."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    from kicad2ems.core import pipeline
    from kicad2ems.core.runs import stop_runs
    out = tmp_path / 'out'
    seen = []
    status = pipeline.Job.status

    def stop_at_second(self, run, st):
        status(self, run, st)
        if run == 'mom25d':
            seen.append(st['solves'])
            if st['solves'] == 2:
                stop_runs(str(out))
    monkeypatch.setattr(pipeline.Job, 'status', stop_at_second)
    code, so, se = run_cli(FILTER, '--solvers', 'mom25d', '--fast', 'auto',
                           '--mom-dmax', '2.4', '--points', '41', '--no-report',
                           '--out', out)
    assert code == 1 and seen == [1, 2], so + se
    # openEMS importable: still no CSX file
    assert not (out / 'rf_interdig.xml').exists()
    assert 'stopped: mom25d (MoM) ended early, no result' in so + se
    assert (not (out / 'rf_interdig_mom25d.s2p').exists()
            and list(out.glob('rf_interdig_*.s2p')))


def test_short_differential_feed_falls_back_to_single_ended_legs(monkeypatch):
    """--mom-port auto: a pair whose coupled feed is too short for a
    differential wave port (ports on pads) is solved with its legs as
    single-ended ports and the modes taken from them, instead of failing."""
    import types
    import kicad2ems.mom25d.api
    from kicad2ems.core.board import Board
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.mom25d.solvers.mom import solve
    ex = os.path.join(ROOT, 'examples')
    m = Board(KiCadBoard(os.path.join(ex, 'rf_diffvia.kicad_pcb')),
              ground='B.Cu', log=lambda *a: None)
    names = [p.name for p in m.ports]
    calls = []

    def fake(board, freq, pairs=False, **kw):
        calls.append(pairs)
        if pairs:
            raise ValueError(
                "P1: straight coupled feed too short for a differential wave "
                "port (0.15 mm, need 2.80 mm)"
            )
        n = len(names)
        model = types.SimpleNamespace(describe=lambda: 'model')
        return types.SimpleNamespace(
            S=np.zeros((len(freq), n, n), complex), names=names, model=model,
            sim=types.SimpleNamespace(log=types.SimpleNamespace(notes=[])),
            unknowns=1, solver='fake',
            ports=[dict(name=x, kind='via')
                   for x in names], seconds=0.0, solves=1, triangles=1,
            notes=[], solved=list(freq), afs=False, afs_tol=None)

    monkeypatch.setattr(kicad2ems.mom25d.api, 'solve_board', fake)
    log = []
    S, out_names, zref, info = solve(m, np.array([1e9, 2e9]), port='auto',
                                     log=log.append)
    assert (calls == [True, False]
            and out_names == ['P1d', 'P1c', 'P2d', 'P2c']
            and S.shape == (2, 4, 4))
    assert any('single-ended' in line for line in log)


def test_whole_stack_ports_below_the_ground(run_cli, tmp_path):
    """--mom-stack whole on rf_msvia (F.Cu microstrip through a via to B.Cu, two
    inner planes): the B.Cu port that no ground plane allows, lumped ports to
    the nearest plane, and S21 as gfdtd gives it (-0.38 dB at 5 GHz); with the
    board's own ground (the default) that port cannot be."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    import subprocess, sys
    from kicad2ems.mom25d.board import BoardModel
    ex = os.path.join(os.path.dirname(FILTER), 'make_rf_examples.py')
    subprocess.check_call(
        [sys.executable, ex, 'msvia',
         str(tmp_path / 'rf_msvia')], stdout=subprocess.DEVNULL)
    cfg = os.path.join(os.path.dirname(FILTER), 'rf_msvia.toml')
    toml = (tmp_path / 'msvia.toml')
    toml.write_text(
        open(cfg, encoding='utf-8').read().replace(
            'rf_msvia.kicad_pcb', (tmp_path / 'rf_msvia.kicad_pcb').as_posix()))
    out = tmp_path / 'out'
    code, so, err = run_cli(toml, '--solvers', 'mom25d', '--mom-stack', 'whole',
                            '--mom-dmax', '0.8', '--out', out, '--no-report')
    assert code == 0, so + err
    assert 'whole stack: no infinite ground' in so and 'P2 (lumped)' in so
    from kicad2ems.core.touchstone import read_touchstone
    f, S = read_touchstone(str(out / 'rf_msvia_mom25d.s2p'))
    i = int(np.argmin(np.abs(f - 5e9)))
    assert abs(20 * np.log10(abs(S[i, 1, 0])) + 0.38) < 0.15
    board = json.loads(
        (out / 'rf_msvia_board.json').read_text(encoding='utf-8'))
    with pytest.raises(
            ValueError,
            match='B.Cu is not a metal interface above the ground In2.Cu'):
        BoardModel(board, port='via', log=lambda *a: None)
    m = BoardModel(board, ground='none', port='via', log=lambda *a: None)
    assert (m.whole and m.stack.bottom == 'open'
            and [(s['k'], s['kref']) for s in m.port_specs] == [(3, 2), (0, 1)])


def test_whole_stack_antenna_radiates_behind_its_ground(run_cli, tmp_path):
    """The patch over its own 80 mm ground meshed (--mom-stack whole): a back
    lobe (gfdtd, the finite board: -13 dBi), which the infinite ground cannot
    have, and gain near gfdtd's 3.1 dBi (the infinite ground's 1.9). No ground
    named is the board's own ground, not the whole stack (ground=None once read
    as 'none')."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    import subprocess, sys
    ex = os.path.join(os.path.dirname(FILTER), 'make_antennas.py')
    subprocess.check_call(
        [sys.executable, ex, 'patch',
         str(tmp_path / 'patch')], stdout=subprocess.DEVNULL)
    out = tmp_path / 'out'
    code, so, err = run_cli(tmp_path / 'patch.kicad_pcb', '--mode', 'antenna',
                            '--fmin', '2.3', '--fmax', '2.6', '--points', '61',
                            '--solvers', 'mom', '--mom-dmax', '2',
                            '--mom-solves', '12', '--mom-stack', 'whole',
                            '--out', out, '--no-report')
    assert code == 0, so + err
    res = json.loads(
        (out / 'patch_mom25d_antenna.json').read_text(encoding='utf-8'))
    c = res['cuts']['xz (phi = 0)']
    a, g = np.array(c['angle']), np.array(c['gain_dbi'])
    # behind the board
    assert -25 < g[np.abs(np.abs(a) - 180) < 1.5].max() < -10
    row = min(res['rows'], key=lambda r: abs(r['f'] - res['f_pattern']))
    assert 2.4 < row['G_dbi'] < 3.6 and 'whole stack' in res['model']
    from kicad2ems.mom25d.board import BoardModel
    board = json.loads((out / 'patch_board.json').read_text(encoding='utf-8'))
    assert (not BoardModel(board, log=lambda *a: None).whole
            and BoardModel(board, ground='none', log=lambda *a: None).whole)


def test_factorisations_and_reused_solves_from_the_stage_records():
    from types import SimpleNamespace
    from kicad2ems.mom25d.solvers.mom import factorisations, reused
    log = SimpleNamespace(
        records=[('LU factor', 1.0, 'N=100, complex64, kept'),
                 ('LU factor (GPU)', 1.0, ''),
                 ('refinement (GMRES, reused factorisation)', 0.5, ''),
                 ('refinement (GMRES, reused factorisation)', 0.5, ''),
                 ('LU factor', 1.0, 'N=100')],
        notes=[
            'the LU of 1 GHz did not precondition 3 GHz in 20 iterations '
            '(residual 1e-2) - a new one'
        ])
    assert factorisations(log) == (1, 2)
    assert reused(log) == 1  # one of the two attempts converged
    assert reused(SimpleNamespace()) == 0


def test_mesh_convergence_of_two_levels():
    """--mom-converge's comparison (R-E3): the largest change in dB and degrees
    over the points above -40 dB, the term it is on, and the verdict against 0.1
    dB / 2 deg, as the FDTD layer's check."""
    import numpy as np
    from kicad2ems.mom25d.solvers.mom import mesh_convergence
    S = np.zeros((3, 2, 2), complex)
    S[:, 0, 1] = S[:, 1, 0] = 0.9
    S[:, 0, 0] = 0.2
    T = S.copy()
    T[:, 0, 1] *= 10 ** (0.05 / 20)  # 0.05 dB on S21
    c = mesh_convergence(T, S, ['P1', 'P2'])
    assert (c['converged'] and c['db'] == pytest.approx(0.05)
            and c['worst'] == 'S(P1,P2)')
    T[:, 0, 0] *= np.exp(1j * np.radians(5))  # 5 degrees on S11
    assert (not mesh_convergence(T, S)['converged']
            and mesh_convergence(T, S)['deg'] == pytest.approx(5))
    T[:, 1, 1] = 1e-4  # below -40 dB: not compared
    assert mesh_convergence(T, S)['db'] == pytest.approx(0.05)


def test_part_ports_are_delta_gaps_closing_to_the_parts(tmp_path, run_cli,
                                                        no_csxcad):
    """kicad2ems R-E5: a part port is a delta gap across the part's strip (the
    strip and its pads kept: a shunt part's ground pad with its vias is not a
    lone stitching pad), and closed by the parts' values it is the board with
    its parts (as sheets: with the sides, a part's port and the part itself
    differ by 0.012)."""
    pytest.importorskip('kicad2ems.mom25d._core')   # (the mesher)
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.pipeline import board_from_settings
    from kicad2ems.core.touchstone import read_touchstone
    from kicad2ems.mom25d.board import BoardModel
    from kicad2ems.examples import prepare_example
    # (a generated board: made there)
    toml = prepare_example('demo_lowpass', str(tmp_path / 'board'))
    models = {}
    for pp in ((), ('all',)):
        s = build_settings({'part_ports': list(pp)} if pp else {}, toml)
        models[pp] = BoardModel(
            board_from_settings(s, log=lambda *a: None).to_dict(),
            log=lambda *a: None)
    plain, ports = models[()], models[('all',)]
    area = lambda bm: sum(g.area for v in bm.shapes.values() for g in v)
    assert (area(ports) == pytest.approx(area(plain))
            and len(ports.vias) == len(plain.vias) > 0)
    assert ([sp['kind'] for sp in ports.port_specs][2:] == ['gap'] * 3
            and not ports.lumped)
    S = {}
    for pp in ('none', 'all'):
        extra = ['--part-ports', 'all'] if pp == 'all' else []
        code, so, err = run_cli(toml, '--solvers', 'mom25d', '--mom-port',
                                'lumped', '--excite', 'P1,P2', '--points', '41',
                                '--mom-thickness', 'off',
                                '--no-report', '--no-reuse', '--out',
                                tmp_path / pp, *extra)
        assert code == 0, so + err
        S[pp] = np.asarray(
            read_touchstone(str(tmp_path / pp / 'demo_lowpass_mom25d.s2p'))[1])
    assert np.abs(S['all'] - S['none']).max() < 2e-3
