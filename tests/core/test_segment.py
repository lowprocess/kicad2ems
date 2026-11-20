"""A board solved in pieces (kicad2ems.core.segment): the network join against
cascades it must reproduce, the cuts round a pair's vias, and a via transition
in pieces against the whole board on mom25d (in a process of its own: this
suite's solvers are stand-ins)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from kicad2ems.core import segment as SG

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))


def test_join_is_the_cascade_and_the_two_port_connection():
    """Two lines joined end to end are their ABCD cascade; a two-port on one
    port of a three-port is deembed.connect's."""
    from kicad2ems.core.deembed import abcd_to_s, connect, line_abcd
    f = np.linspace(1e9, 10e9, 5)
    A = line_abcd(40.0, 3.0, 0.01, f, tand=0.02)
    B = line_abcd(65.0, 3.5, 0.017, f)
    want = abcd_to_s(np.einsum('fij,fjk->fik', A, B), 50.0)
    S, names = SG.join([(abcd_to_s(A, 50.0), ['a1', 'a2']),
                        (abcd_to_s(B, 50.0), ['b1', 'b2'])], [('a2', 'b1')])
    assert names == ['a1', 'b2'] and np.abs(S - want).max() < 1e-12
    rng = np.random.default_rng(1)
    M = rng.normal(size=(5, 3, 3)) + 1j * rng.normal(size=(5, 3, 3))
    M = 0.3 * (M + M.transpose(0, 2, 1))
    G = abcd_to_s(B, 50.0)
    got, nm = SG.join([(M, ['m0', 'm1', 'm2']), (G, ['g1', 'g2'])],
                      [('m1', 'g2')])
    o = [nm.index(x) for x in ('m0', 'g1', 'm2')]
    assert np.abs(got[:, o][:, :, o] - connect(M, 1, G)).max() < 1e-12
    with pytest.raises(ValueError, match='two links'):
        SG.join([(M, ['m0', 'm1', 'm2'])], [('m0', 'm1'), ('m1', 'm2')])


def _example(tmp, name):
    subprocess.check_call([sys.executable,
                           os.path.join(ROOT, 'examples', 'make_rf_examples.py'),
                           name, str(tmp / f"rf_{name}")],
                          stdout=subprocess.DEVNULL)
    cfg = tmp / f"rf_{name}.toml"
    cfg.write_text(open(os.path.join(ROOT, 'examples',
                                     f"rf_{name}.toml")).read())
    return cfg


def test_a_pairs_vias_are_one_model_cut_beyond_their_antipads(tmp_path):
    """rf_diffvia: the pair's two vias (1 mm apart) one via model, its circle
    beyond the 0.55 mm antipads; each leg cut on F.Cu and on In2.Cu, the cut
    pointing away from its via."""
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.pipeline import board_from_settings
    cfg = _example(tmp_path, 'diffvia')
    s = build_settings(dict(board=str(tmp_path / 'rf_diffvia.kicad_pcb'),
                            ground='B.Cu'))
    m = board_from_settings(s, log=lambda *x: None)
    (g,) = SG.via_groups(m, {'D_P', 'D_N'})
    assert sorted(v['net'] for v in g['vias']) == ['D_N', 'D_P']
    for v in g['vias']:
        assert v['antipad'] == pytest.approx(0.55, abs=0.02)
        assert v['r'] == pytest.approx(0.55 + SG.GAP, abs=0.02)
    cs = SG.cuts(KiCadBoard(s.board), g, {'D_P', 'D_N'})
    assert sorted((c['layer'], c['net']) for c in cs) == [
        ('F.Cu', 'D_N'), ('F.Cu', 'D_P'), ('In2.Cu', 'D_N'), ('In2.Cu', 'D_P')]
    for c in cs:  # away from the via: F.Cu's toward -x, In2.Cu's toward +x
        assert c['out'][0] < 0 if c['layer'] == 'F.Cu' else c['out'][0] > 0


def test_a_via_transition_in_pieces_is_the_whole_board(tmp_path):
    """rf_msvia (F.Cu microstrip through one via to B.Cu, four return vias) in
    pieces: two layers over ideal planes and a via model, joined; S21 within
    0.3 dB of the whole board on mom25d to 10 GHz (measured: 0.18 dB at worst,
    0.04 dB rms; gfdtd whole is 0.10 / 0.02 from it)."""
    pytest.importorskip('kicad2ems.mom25d.core')
    from kicad2ems.core.touchstone import read_touchstone
    cfg = _example(tmp_path, 'msvia')
    env = dict(os.environ, MOM25D_GPU='off', QT_QPA_PLATFORM='offscreen')
    common = ['--fmin', '1', '--fmax', '10', '--points', '91']
    p = subprocess.run([sys.executable, '-m', 'kicad2ems.core.segment',
                        str(cfg), '--out', str(tmp_path / 'seg')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    res = json.load(open(tmp_path / 'seg' / SG.RESULT))
    assert res['ports'] == ['P1', 'P2']
    assert sorted(x['kind'] for x in res['pieces']) == ['layer', 'layer',
                                                        'via']
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', str(cfg),
                        '--solvers', 'mom25d', '--no-report', '--out',
                        str(tmp_path / 'whole')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    f0, S0 = read_touchstone(str(tmp_path / 'whole' / 'rf_msvia_mom25d.s2p'))
    f, S = read_touchstone(str(tmp_path / 'seg' / res['touchstone']))
    db = lambda x: 20 * np.log10(np.abs(x))
    assert np.allclose(f, f0)
    d = db(S[:, 1, 0]) - db(S0[:, 1, 0])
    assert np.abs(d).max() < 0.3 and np.sqrt(np.mean(d ** 2)) < 0.1
    assert db(S[:, 0, 0]).max() < -8.0  # a matched transition still
    # again: every piece solved before is taken as it is, the result the same
    p = subprocess.run([sys.executable, '-m', 'kicad2ems.core.segment',
                        str(cfg), '--out', str(tmp_path / 'seg')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0 and p.stdout.count('solved before') == 3
    f2, S2 = read_touchstone(str(tmp_path / 'seg' / res['touchstone']))
    assert np.array_equal(S2, S)
    # the layers on the line model, the via on mom25d (--layer-solver lines):
    # as near the whole board (measured 0.15 dB at worst; rf_diffvia's Sdd21
    # 0.11 dB rms, all on mom25d 0.16)
    p = subprocess.run([sys.executable, '-m', 'kicad2ems.core.segment',
                        str(cfg), '--layer-solver', 'lines', '--out',
                        str(tmp_path / 'hyb')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    res = json.load(open(tmp_path / 'hyb' / SG.RESULT))
    f3, S3 = read_touchstone(str(tmp_path / 'hyb' / res['touchstone']))
    d = db(S3[:, 1, 0]) - db(S0[:, 1, 0])
    assert np.abs(d).max() < 0.3 and np.sqrt(np.mean(d ** 2)) < 0.1


def test_a_layer_pieces_tracks_end_at_the_via_circles(tmp_path):
    """A layer piece's tracks (what the line model builds from) are its own
    nets' on its layer, cut outside the via circles as its copper is (uncut, a
    trace ran on past its cut port into the circle: an open stub)."""
    from shapely.geometry import LineString, Point
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.pipeline import board_from_settings
    _example(tmp_path, 'msvia')
    s = build_settings(dict(board=str(tmp_path / 'rf_msvia.kicad_pcb'),
                            nets=['RF', 'GND']))
    m = board_from_settings(s, log=lambda *x: None)
    kb = KiCadBoard(s.board)
    parts, cs = SG.pieces(m, kb, kb, set(m.sel))
    (g,) = SG.via_groups(m, {'RF'})
    via = Point(g['vias'][0]['x'], g['vias'][0]['y'])
    for p in parts:
        if p['kind'] != 'layer':
            continue
        d = SG._to_dict(p['board'], p)
        tr = [t for t in d['prims']['tracks'] if t['net'] == 'RF']
        assert tr and all(t['layer'] == p['layer'] for t in tr)
        for t in tr:
            assert LineString(t['pts']).distance(via) >= g['vias'][0]['r'] - 1e-6
