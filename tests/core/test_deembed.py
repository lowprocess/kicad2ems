"""De-embedding (R-E1): a port's plane moved along its trace and a fixture
removed, against exact cascades."""
import os
import numpy as np
import pytest

from kicad2ems.core import deembed as D
from kicad2ems.core.lines.elements import Element, LineModel

F = np.linspace(1e8, 10e9, 41)


def line_s(z0, eeff, mm, z=50.0, tand=0.0):
    return D.abcd_to_s(D.line_abcd(z0, eeff, mm * 1e-3, F, tand), z)


def model(port, sections):
    """A line model whose path from `port` is these (z0, eeff, mm) traces."""
    lm = LineModel()
    secs, at = [], 0.0
    for k, (z0, ee, mm) in enumerate(sections):
        e = Element('trace', f'n{k}', f'n{k + 1}', mm, w=0.3, label=f's{k}')
        e.z0, e.eeff, e.xs, e.t = z0, ee, 'microstrip', 0.035
        secs.append((e, at, False))
        at += mm
    lm.paths = [(port, None, secs, [])]
    return lm


def test_a_matched_lines_plane_moves_by_its_phase():
    thru = line_s(50.0, 3.0, 20.0)  # a 50 ohm line of 20 mm as the device
    lm = model('P1', [(50.0, 3.0, 5.0), (50.0, 3.0, 10.0)])
    S, done = D.apply(thru, ['P1', 'P2'], [50.0, 50.0], F, lines={'P1': 12.0},
                      lm=lm)
    # 8 mm left, through two sections
    assert np.allclose(S, line_s(50.0, 3.0, 8.0), atol=1e-12)
    assert done == ['P1: its reference +12 mm along its trace']
    back, _ = D.apply(S, ['P1', 'P2'], [50.0, 50.0], F, lines={'P1': -12.0},
                      lm=model('P1', [(50.0, 3.0, 5.0)]))
    # out again, the first section continued
    assert np.allclose(back, thru, atol=1e-12)


def test_an_unmatched_fixture_comes_off_both_ends_exactly(tmp_path):
    from kicad2ems.core.touchstone import write_touchstone
    dut = D.abcd_to_s(
        D.line_abcd(30.0, 4.0, 3e-3, F, 0.02) @ D.line_abcd(90.0, 2.0, 2e-3, F),
        50.0)
    # 70 ohm, lossy: port side first
    fix = D.line_abcd(70.0, 3.3, 4e-3, F, 0.01)
    total = D.connect(D.connect(dut, 0, D.abcd_to_s(fix, 50.0)), 1,
                      D.abcd_to_s(fix, 50.0))
    lm = LineModel()
    lm.paths = (model('P1', [(70.0, 3.3, 4.0)]).paths
                + model('P2', [(70.0, 3.3, 4.0)]).paths)
    lm.paths[0][2][0][0].tand_eff = lm.paths[1][2][0][0].tand_eff = 0.01
    S, _ = D.apply(total, ['P1', 'P2'], [50.0, 50.0], F,
                   lines={'P1': 4.0, 'P2': 4.0}, lm=lm)
    assert np.allclose(S, dut, atol=1e-10)
    path = tmp_path / 'fix.s2p'
    write_touchstone(str(path), F, D.abcd_to_s(fix, 50.0), [50.0, 50.0],
                     names=['a', 'b'])
    S2, done = D.apply(total, ['P1', 'P2'], [50.0, 50.0], F,
                       files={'P1': str(path), 'P2': str(path)})
    # (the file's digits)
    assert np.allclose(S2, dut, atol=1e-6) and 'fixture' in done[0]


def test_what_cannot_be_removed_is_said():
    lm = model('P1', [(50.0, 3.0, 5.0)])
    with pytest.raises(ValueError, match='not 9'):
        D.apply(line_s(50, 3, 20), ['P1', 'P2'], [50.0, 50.0], F,
                lines={'P1': 9.0}, lm=lm)
    with pytest.raises(ValueError, match='not a port'):
        D.apply(line_s(50, 3, 20), ['P1', 'P2'], [50.0, 50.0], F,
                lines={'P3': 1.0}, lm=lm)
    with pytest.raises(ValueError, match='no line model'):
        D.apply(line_s(50, 3, 20), ['P1', 'P2'], [50.0, 50.0], F,
                lines={'P2': 1.0}, lm=lm)


def test_the_far_end_of_a_path_walks_it_backwards():
    lm = model('P1', [(50.0, 3.0, 5.0), (70.0, 2.5, 4.0)])
    lm.paths = [(p, 'P2', secs, b) for p, _, secs, b in lm.paths]
    T = D.path_abcd(lm, 'P2', 6.0, F)
    want = D.line_abcd(70.0, 2.5, 4e-3, F) @ D.line_abcd(50.0, 3.0, 2e-3, F)
    assert np.allclose(T, want, atol=1e-12)
