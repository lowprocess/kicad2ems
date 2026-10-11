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
