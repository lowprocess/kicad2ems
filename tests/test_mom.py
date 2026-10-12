"""mom25d as a solver: it gets the board itself (no CSX file, no openEMS), --mom / --solvers mom end to end,
and --sigma as conductor loss."""
import json, os
import numpy as np
import pytest

FILTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples', 'filter.toml')


def test_import():
    import mom25d
    from mom25d.board import BoardModel                             # noqa: F401
    assert hasattr(mom25d, '__version__')


def test_preview_with_mom(tmp_path, run_cli):
    pytest.importorskip('triangle')
    out = tmp_path / 'out'
    code, so, se = run_cli(FILTER, '--preview', '--mom', '--mom-dmax', '0.8', '--mom-solves', '8', '--points', '41',
                           '--out', str(out))
    assert code == 0, so + se
    assert (out / 'filter_mom25d.s2p').exists() and (out / 'filter_mom25d.png').exists()
    assert not (out / 'filter.xml').exists()                        # no CSX file: MoM reads the board
    sm = json.load(open(out / 'filter_run.json'))
    mo = sm['solved']['mom25d']
    assert mo['solves'] <= 8 and mo['unknowns'] > 100 and {p['kind'] for p in mo['ports']} == {'lumped'}
    assert mo['table'][0]['param'] == 'S(P1,P1)' and sm['results']['reference'] == 'mom25d'
    line = next(n for n in sm['solved'] if n != 'mom25d')          # the config's fast = "auto": against MoM
    assert sm['solved'][line]['reference'] == 'mom25d' and sm['solved'][line]['compare']
    assert 'mom25d' in so


def test_mom_without_openems(run_cli, tmp_path, no_csxcad):
    """MoM needs no openEMS: it reads the board, and the run is the same."""
    pytest.importorskip('triangle')
    pytest.importorskip('skrf')
    out = tmp_path / 'out'
    code, so, err = run_cli(FILTER, '--preview', '--mom', '--fast', 'skrf', '--mom-dmax', '0.8', '--mom-solves', '8',
                            '--points', '41', '--out', out)
    assert code == 0, so + err
    sm = json.loads((out / 'filter_run.json').read_text())
    assert sm['solved']['mom25d']['solves'] <= 8 and sm['solved']['scikit']['compare']
    samples = sm['solved']['mom25d']['samples_ghz']                 # the adaptive sweep's frequencies
    assert len(samples) == sm['solved']['mom25d']['solves'] and samples == sorted(samples)
    for f in ('filter_mom25d.s2p', 'filter_mom25d.png', 'filter_scikit.s2p', 'filter_report.html', 'filter_board.json'):
        assert (out / f).exists(), f


def test_sigma_reaches_mom(demo_files):
    """--sigma is conductor loss in openEMS's conducting sheets; MoM gets the same, with the copper thickness
    of the ports' layer."""
    from kicad2ems.solvers.mom import _metal
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    assert _metal(m, None) == (None, None)
    metal, t = _metal(m, 5.8e7)
    assert metal.sigma == 5.8e7 and t == pytest.approx(m.cu_t[m.ports[0].layer]) and metal.t == pytest.approx(t * 1e-3)
    zs = metal.zs(np.array([1e9]))
    assert zs.real[0] > 0                                           # a lossy surface impedance


def test_current_dumps(boards, run_cli, tmp_path):
    """--mom-dump: mom25d's surface currents at the frequencies asked, a |J| map each, with --dump-animate one
    period of Re(J e^j phase), and the dumps where the GUI's Fields tab finds them."""
    pytest.importorskip('triangle')
    from kicad2ems.solvers.openems import fields as F
    base, out = boards['lowpass'], tmp_path / 'out'
    code, so, err = run_cli(base + '.kicad_pcb', '--pos', base + '-top.pos', '--solvers', 'mom', '--mom-dmax', '0.8',
                            '--mom-solves', '6', '--points', '21', '--mom-dump', '2,5', '--dump-animate', 'gif',
                            '--mom-near', '1', '--out', out)
    assert code == 0, so + err
    for f in (2, 5):
        for ext in ('png', 'gif'):
            assert (out / f"lowpass_mom25d_P1_dump_J_F_Cu_fd_{f}GHz.{ext}").exists()
    E, src = F.find_dumps(str(out))                                  # sorted by name: E@top+1mm, J@F.Cu
    assert src.solver == 'mom25d' and src.freqs == [2e9, 5e9] and 'A/m' in src.unit
    assert E.field == 'E' and 'V/m' in E.unit and np.abs(E.load(0)[1]).max() > 0
    assert (out / 'lowpass_mom25d_P1_dump_E_topp1mm_fd_5GHz.gif').exists()
    mesh, J = src.load(0)
    assert np.abs(J).max() > 0 and np.all(J[..., 2] == 0)                # in-plane currents only
    sm = json.loads((out / 'lowpass_run.json').read_text())
    assert any(d['label'].startswith('mom25d dump_J_F_Cu_fd') for d in sm['dumps'])


def test_antenna_mode(run_cli, tmp_path):
    """mom25d in antenna mode on the patch example: the resonance, efficiency below 1, and openEMS's antenna
    outputs (JSON, plots, the sphere the GUI reads)."""
    pytest.importorskip('triangle')
    import subprocess, sys
    ex = os.path.join(os.path.dirname(FILTER), 'make_antennas.py')
    subprocess.check_call([sys.executable, ex, 'patch', str(tmp_path / 'patch')], stdout=subprocess.DEVNULL)
    out = tmp_path / 'out'
    code, so, err = run_cli(tmp_path / 'patch.kicad_pcb', '--mode', 'antenna', '--fmin', '2.3', '--fmax', '2.6',
                            '--points', '61', '--solvers', 'mom', '--mom-dmax', '2', '--mom-solves', '12', '--out', out)
    assert code == 0, so + err
    res = json.loads((out / 'patch_mom25d_antenna.json').read_text())
    assert 2.38e9 < res['match']['f_res'] < 2.52e9 and res['solver'] == 'mom25d'
    row = min(res['rows'], key=lambda r: abs(r['f'] - res['f_pattern']))
    assert 0.1 < row['eff'] < 1 and 5 < row['D_dbi'] < 9
    assert set(res['cuts']) == {'xz (phi = 0)', 'yz (phi = 90)', 'xy (theta = 90)'}
    for f in ('patch_mom25d_antenna.png', 'patch_mom25d_pattern.png', 'patch_mom25d_sphere.npz'):
        assert (out / f).exists(), f


def test_stop_ends_mom25d_after_its_current_solve(run_cli, tmp_path, monkeypatch):
    """The GUI's Stop (a STOP file, kicad2ems.stop): mom25d ends after the frequency it is solving, with no
    result; the line model (the --fast reference) still gives its. ABORT alone had left mom25d running."""
    pytest.importorskip('triangle')
    from kicad2ems import pipeline
    from kicad2ems.gui.runner import stop_runs
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
    code, so, se = run_cli(FILTER, '--solvers', 'mom25d', '--fast', 'auto', '--mom-dmax', '0.8', '--points', '41',
                           '--no-report', '--out', out)
    assert code == 1 and seen == [1, 2], so + se
    assert 'stopped: mom25d (MoM) ended early, no result' in so + se
    assert not (out / 'filter_mom25d.s2p').exists() and list(out.glob('filter_*.s2p'))
