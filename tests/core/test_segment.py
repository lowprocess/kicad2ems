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


def test_a_node_joins_the_parts_at_one_place():
    """node_block: an ideal junction (S11 -1/3, S21 2/3 for three ports,
    lossless); nodes: a port two links share becomes a junction with their
    other sides, every port then in one link, and the network joins: a 1-port
    on a line shared with another line is their tee."""
    from kicad2ems.core.deembed import abcd_to_s, line_abcd
    J = SG.node_block(3, 2)
    assert np.allclose(J[0], np.full((3, 3), 2 / 3) - np.eye(3))
    assert np.allclose(np.einsum('fij,fik->fjk', J.conj(), J), np.eye(3))
    f = np.linspace(1e9, 3e9, 2)
    A = abcd_to_s(line_abcd(50.0, 3.0, 0.01, f), 50.0)
    B = abcd_to_s(line_abcd(50.0, 3.0, 0.02, f), 50.0)
    load = np.zeros((2, 1, 1), complex)         # a matched end
    blocks = [(A, ['a1', 'a2']), (B, ['b1', 'b2']), (load, ['x'])]
    pairs = SG.nodes(blocks, [('a2', 'x'), ('b1', 'x')], 2,
                     echo=lambda *a: None)
    used = [p for pr in pairs for p in pr]
    assert len(used) == len(set(used)) and len(blocks) == 4
    S, names = SG.join(blocks, pairs)
    assert sorted(names) == ['a1', 'b2']
    # matched lines into a matched tee: |S21| = 2/3, |S11| = 1/3
    i, j = names.index('a1'), names.index('b2')
    assert np.allclose(np.abs(S[:, j, i]), 2 / 3)
    assert np.allclose(np.abs(S[:, i, i]), 1 / 3)


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


def test_a_track_drawn_twice_is_one_cut(tmp_path):
    """A track drawn twice (a KiCad 4 file's), and again 3 um off, crosses a
    via's circle at one place: one cut (two ports there were two coincident
    via chains: the matrix near singular). A second conductor a width away
    is a cut of its own."""
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.pipeline import board_from_settings
    _example(tmp_path, 'msvia')
    s = build_settings(dict(board=str(tmp_path / 'rf_msvia.kicad_pcb'),
                            nets=['RF', 'GND']))
    m = board_from_settings(s, log=lambda *x: None)
    kb = KiCadBoard(s.board)
    (g,) = SG.via_groups(m, {'RF'})
    n0 = len(SG.cuts(kb, g, {'RF'}))
    assert n0 >= 2
    rf = [dict(t) for t in kb.tracks if t['net'] == 'RF']
    kb.tracks += [dict(t) for t in rf]
    assert len(SG.cuts(kb, g, {'RF'})) == n0
    shift = lambda dy: [dict(t, pts=[(x, y + dy) for x, y in t['pts']])
                        for t in rf]
    kb.tracks += shift(3e-3)
    assert len(SG.cuts(kb, g, {'RF'})) == n0
    w = min(t['width'] for t in rf)
    kb.tracks += shift(1.5 * w)
    assert len(SG.cuts(kb, g, {'RF'})) > n0


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


def test_a_coarse_edge_mesh_couples_as_the_fine_one(tmp_path):
    """mom25d's --mom-edges coarse (no edge rings, no gap rings): rf_coupler's
    10 dB coupling in under half the unknowns, S31 within 0.6 dB of the fine
    mesh's (measured 0.55 dB at 2.4 GHz; each 0.5-0.6 from gfdtd's)."""
    pytest.importorskip('kicad2ems.mom25d.core')
    from kicad2ems.core.touchstone import read_touchstone
    cfg = _example(tmp_path, 'coupler')
    env = dict(os.environ, MOM25D_GPU='off', QT_QPA_PLATFORM='offscreen')
    got = {}
    for edges in ('fine', 'coarse'):
        p = subprocess.run([sys.executable, '-m', 'kicad2ems', str(cfg),
                            '--solvers', 'mom25d', '--mom-edges', edges,
                            '--points', '31', '--no-report', '--out',
                            str(tmp_path / edges)],
                           capture_output=True, text=True, env=env)
        assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
        n = int(next(l for l in p.stdout.splitlines()
                     if l.startswith('mom25d:') and 'unknowns' in l)
                .split()[1].replace(',', ''))
        f, S = read_touchstone(str(tmp_path / edges / 'rf_coupler_mom25d.s4p'))
        got[edges] = (n, S)
    (nf, Sf), (nc, Sc) = got['fine'], got['coarse']
    assert nc < 0.55 * nf
    db = lambda x: 20 * np.log10(np.abs(x))
    assert np.abs(db(Sc[:, 2, 0]) - db(Sf[:, 2, 0])).max() < 0.8
    assert np.abs(db(Sc[:, 1, 0]) - db(Sf[:, 1, 0])).max() < 0.3


def test_a_physics_based_via_model(tmp_path):
    """--via-solver pbm (viamodel.py: the cavities between the planes, no
    mesh): rf_diffvia's pair through its blind vias, against the whole board on
    mom25d: Sdd21 within 0.25 dB rms over 1-10 GHz (measured 0.073; the meshed
    via model 0.193, gfdtd's whole board 0.177), the via model passive."""
    pytest.importorskip('kicad2ems.mom25d.core')
    import glob
    from kicad2ems.core.touchstone import read_touchstone
    cfg = _example(tmp_path, 'diffvia')
    env = dict(os.environ, MOM25D_GPU='off', QT_QPA_PLATFORM='offscreen')
    common = ['--fmin', '1', '--fmax', '10', '--points', '91']
    p = subprocess.run([sys.executable, '-m', 'kicad2ems.core.segment',
                        str(cfg), '--edges', 'coarse', '--via-solver', 'pbm',
                        '--out', str(tmp_path / 'seg')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    f, Sv = read_touchstone(glob.glob(str(tmp_path / 'seg' / 'via1' /
                                          '*_pbm.s4p'))[0])
    assert np.linalg.svd(Sv, compute_uv=False).max() <= 1.0 + 1e-9
    res = json.load(open(tmp_path / 'seg' / SG.RESULT))
    f, S = read_touchstone(str(tmp_path / 'seg' / res['touchstone']))
    p = subprocess.run([sys.executable, '-m', 'kicad2ems', str(cfg),
                        '--solvers', 'mom25d', '--no-report', '--out',
                        str(tmp_path / 'whole')] + common,
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    f0, S0 = read_touchstone(str(tmp_path / 'whole' / 'rf_diffvia_mom25d.s4p'))
    r = 1 / np.sqrt(2)
    M = np.array([[r, -r, 0, 0], [r, r, 0, 0], [0, 0, r, -r], [0, 0, r, r]])
    Sm = np.einsum('ij,fjk,lk->fil', M, S, M)  # P1d, P1c, P2d, P2c
    db = lambda x: 20 * np.log10(np.abs(x))
    d = db(Sm[:, 2, 0]) - db(S0[:, 2, 0])  # (the whole board's: mixed mode)
    assert np.sqrt(np.mean(d ** 2)) < 0.25


def test_the_overlay_figure(tmp_path):
    """segment.plot_overlay: the channels' tracks coloured by their crosstalk
    and their loss over the board's outline, the via models circled."""
    from types import SimpleNamespace
    from shapely.geometry import LineString
    ch = lambda name, x, nxt, il: dict(
        channel=name, kind='pair', nets=[name + '_P', name + '_N'],
        il_db=[-0.1, -1.0, il], rl_db=[-30, -20, -10], to_common_far_db=-30,
        next_sum_pct=nxt, fext_sum_pct=nxt / 2, worst=[],
        tracks=[dict(layer='F.Cu', net=name + '_P', w=0.1,
                     pts=[[x, 0.0], [x, 10.0]])])
    ov = dict(board='b.kicad_pcb', ghz=[1, 5, 10], rise_ps=100,
              channels=[ch('A', 1.0, 0.5, -2.0), ch('B', 2.0, 0.1, -4.0)],
              via_models=[dict(name='via1', ports=2, vias=[
                  dict(x=1.0, y=5.0, r=0.4, net='A_P')])], cuts=[])
    kb = SimpleNamespace(edges=[LineString([(0, 0), (5, 0), (5, -12)])])
    path = SG.plot_overlay(ov, kb, str(tmp_path / 'o.png'))
    assert os.path.getsize(path) > 10_000


def test_the_overlay_page(tmp_path):
    """segment.overlay_html: the page with the overlay in it (no placeholder
    left, a '</' in a name escaped); without a path, the page that fetches
    overlay.json."""
    ov = dict(board='b.kicad_pcb', ghz=[10], rise_ps=100, curve_ghz=[1, 10],
              channels=[dict(channel='/x/A</script>', kind='single',
                             nets=['A'], il_db=[-1], rl_db=[-20], tracks=[],
                             curve=dict(il=[-0.1, -1], rl=[-30, -20]))],
              via_models=[], cuts=[], outline=[])
    path = SG.overlay_html(ov, str(tmp_path / 'o.html'))
    text = open(path, encoding='utf-8').read()
    assert '/*OVERLAY_JSON*/' not in text and '"curve_ghz":[1,10]' in text
    assert 'A</script>' not in text
    assert "fetch('overlay.json')" in SG.overlay_html(ov)


def test_a_lane_through_its_ac_capacitor():
    """segment.chains: nets joined by a two-pad series part (an AC-coupling
    capacitor) are one channel, joined through the part's own two-port
    (series_block); a part of three pads or with a pad off the selected nets
    joins nothing."""
    from types import SimpleNamespace
    pad = lambda net: dict(net=net, name='1', xy=(0, 0), layers=['F.Cu'],
                           geom=None)
    kb = SimpleNamespace(footprints=[
        dict(ref='C1', pads=[pad('/a/TX0_C+'), pad('/b/TX0+')]),
        dict(ref='C2', pads=[pad('/a/TX0_C-'), pad('/b/TX0-')]),
        dict(ref='U1', pads=[pad('/b/TX0+'), pad('/c/X')]),
        dict(ref='C3', pads=[pad('/b/TX0+'), pad('GND')]),
        dict(ref='R1', pads=[pad('/c/X'), pad('/c/Y'), pad('/c/Z')])])
    nets = ['/a/TX0_C+', '/a/TX0_C-', '/b/TX0+', '/b/TX0-', '/c/X', '/c/Y']
    ch = SG.chains(kb, nets)
    assert ch['/a/TX0_C+'] == ['/a/TX0_C+', '/b/TX0+']
    assert ch['/c/X'] == ['/c/X'] and ch['/c/Y'] == ['/c/Y']
    # the part's two-port: 100 nF a through at GHz, a 33 ohm resistor not
    from types import SimpleNamespace as NS
    S = SG.series_block([1e9], NS(R=None, L=None, C=100e-9))
    assert abs(S[0, 0, 1]) > 0.9999 and abs(S[0, 0, 0]) < 1e-3
    S = SG.series_block([1e9], NS(R=33.0, L=None, C=None))
    assert abs(S[0, 0, 0] - 33 / 133) < 1e-12


def test_windows_of_a_bus_and_their_assembly():
    """segment._windows: a bus of 8 pairs in cores of 3 pairs, each window its
    core and the pairs within reach; assemble: each core port's row from its
    window, an entry both windows have their mean, one neither has 0."""
    from types import SimpleNamespace
    tracks = []
    for i in range(8):
        for leg, dy in (('+', 0.0), ('-', 0.2)):
            y = i * 1.0 + dy
            tracks.append(dict(layer='In1.Cu', net=f'/b/L{i}{leg}', width=0.1,
                               pts=[[0.0, y], [20.0, y]]))
    kb = SimpleNamespace(tracks=tracks)
    nets = sorted(t['net'] for t in tracks)
    wins = SG._windows(kb, 'In1.Cu', nets, reach=0.85)
    cores = [c for c, _ in wins]
    assert sorted(n for c in cores for n in c) == nets
    assert all(len(c) <= SG.CORE_NETS for c in cores)
    for core, win in wins:  # a window: its core and the next pair either side
        idx = sorted({int(n[4]) for n in core})
        assert {int(n[4]) for n in win} == set(range(
            max(idx[0] - 1, 0), min(idx[-1] + 2, 8)))
    # assembly on a known symmetric S
    rng = np.random.default_rng(0)
    n = 6
    S = rng.standard_normal((3, n, n)) + 1j * rng.standard_normal((3, n, n))
    S = S + S.transpose(0, 2, 1)
    names = [f'P{i}' for i in range(n)]
    w1 = ([0, 1, 2, 3], {'P0', 'P1'})
    w2 = ([2, 3, 4, 5], {'P2', 'P3', 'P4', 'P5'})
    got, order = SG.assemble([(S[:, w][:, :, w], [names[i] for i in w], c)
                              for w, c in (w1, w2)])
    at = [order.index(p) for p in names]
    G = got[:, at][:, :, at]
    assert np.allclose(G[:, 0, 3], S[:, 0, 3])      # w1 alone
    assert np.allclose(G[:, 2, 5], S[:, 2, 5])      # w2's
    assert np.allclose(G[:, 0, 5], 0)               # in no window
    assert np.allclose(G, G.transpose(0, 2, 1))


class _Proc:
    """A piece's run standing in for its process: ends when killed."""

    def __init__(self):
        import threading
        self.gone = threading.Event()

    def kill(self):
        self.gone.set()


def _parts(names, small):
    import types
    b = types.SimpleNamespace(ports=[], copper={}, copper_nets={},
                              ground_nets=set())
    return [dict(name=n, kind='layer' if n in small else 'via', board=b)
            for n in names]


def test_a_piece_without_room_runs_again_alone(monkeypatch, tmp_path):
    """_solve_all: a small piece runs in its share (EMSDEV_MEM_BUDGET, the GPU
    off, MOM25D_NO_ROOM_EXIT), a big one beside them in the rest; one that
    gives its share back (NoRoom) runs again at the end with no share and no
    exit; the solved ones' geometry is freed."""
    import types
    calls = []

    def fake(p, folder, band, solver, echo, env=None, started=None, **kw):
        calls.append((p['name'], dict(env or {})))
        if p['name'] == 's1' and env and env.get('MOM25D_NO_ROOM_EXIT'):
            raise SG.NoRoom('its direct solver needs more than its share')
        if p['name'] == 'b1' and [n for n, _ in calls].count('b1') == 1:
            raise SG.Killed('b1: its run ended by signal 9')
        return (np.zeros(1), np.zeros((1, 1, 1)), ['P1'], {})
    monkeypatch.setattr(SG, 'solve_piece', fake)
    monkeypatch.setattr(SG, 'JOBS', 2)
    monkeypatch.setattr(SG, '_small', lambda p: p['kind'] == 'layer')
    a = types.SimpleNamespace(thickness=None, dmax=None, layer_solver=None,
                              via_solver=None, edges=None, solver='mom25d')
    parts = _parts(['s0', 's1', 'b0', 'b1'], {'s0', 's1'})
    res = SG._solve_all(parts, str(tmp_path), {}, a, echo=lambda *x: None)
    assert sorted(res) == ['b0', 'b1', 's0', 's1']
    assert all(isinstance(v, tuple) for v in res.values())
    first = [e for n, e in calls if n == 's0'][0]
    assert first['MOM25D_GPU'] == 'off' and first['MOM25D_NO_ROOM_EXIT']
    assert float(first['EMSDEV_MEM_BUDGET']) == SG.SMALL_BUDGET
    again = [e for n, e in calls if n == 's1']
    assert len(again) == 2 and again[-1] == {}
    # (killed, b1: again at the end too)
    assert sorted(n for n, _ in calls[-2:]) == ['b1', 's1']
    assert all(not hasattr(p['board'], 'copper') for p in parts)


def test_the_newest_small_piece_is_stopped_when_memory_runs_low(
        monkeypatch, tmp_path):
    """The watchdog: below its low mark the newest small piece's run is
    killed and runs again at the end, alone."""
    import time
    import types
    from kicad2ems.emsdev import memory
    level = [64e9]
    monkeypatch.setattr(memory, 'available', lambda: level[0])
    calls = []

    def fake(p, folder, band, solver, echo, env=None, started=None, **kw):
        calls.append(p['name'])
        if p['name'] == 's0' and env:
            proc = _Proc()
            started(proc)
            level[0] = 0.1e9                  # it fills the machine
            assert proc.gone.wait(20)
            level[0] = 64e9
            started(None)
            raise ValueError('s0: its run ended with code -9')
        time.sleep(0.05)
        return (np.zeros(1), np.zeros((1, 1, 1)), ['P1'], {})
    monkeypatch.setattr(SG, 'solve_piece', fake)
    monkeypatch.setattr(SG, 'JOBS', 2)
    monkeypatch.setattr(SG, '_small', lambda p: p['kind'] == 'layer')
    a = types.SimpleNamespace(thickness=None, dmax=None, layer_solver=None,
                              via_solver=None, edges=None, solver='mom25d')
    res = SG._solve_all(_parts(['s0', 'b0'], {'s0'}), str(tmp_path), {}, a,
                        echo=lambda *x: None)
    assert all(isinstance(v, tuple) for v in res.values())
    assert calls.count('s0') == 2 and calls[-1] == 's0'


def test_a_piece_process_is_the_oom_killers_first_choice():
    """_victim (a piece's preexec): its oom_score_adj raised above the run's,
    so the kernel kills a piece, not the study."""
    if not os.path.exists('/proc/self/oom_score_adj'):
        pytest.skip('no oom_score_adj')
    out = subprocess.run(
        [sys.executable, '-c', 'print(open("/proc/self/oom_score_adj").read())'],
        preexec_fn=SG._victim, capture_output=True, text=True).stdout
    assert int(out) == 800


def test_ports_that_come_to_one_place_are_one():
    """_one_port_a_place: a piece's cut or joint ports on one net and layer
    within 1 um (two parts' pads apart, each port centred on the same wide
    copper) one port, the other's links its (run's nodes: a junction); a
    board end's port, another net's or layer's kept apart."""
    import types
    P = lambda name, x, net='A', layer='F.Cu': types.SimpleNamespace(
        name=name, c=np.array([x, 0.0]), net=net, layer=layer)
    b = types.SimpleNamespace(ports=[P('P1', 0.0), P('P20076', 5.0),
                                     P('P20085', 5.0 + 1e-4),
                                     P('P20090', 5.0, net='B'),
                                     P('P20091', 5.0, layer='B.Cu'),
                                     P('P2', 5.0)])
    pc = SG._one_port_a_place(dict(board=b, external=['P2'], links=[
        ('P20076', 76), ('P20085', 85), ('P20090', 90), ('P20091', 91)],
        part_net={'P20076': 'A', 'P20085': 'A'}, core_ports=['P20085']))
    assert [p.name for p in pc['board'].ports] == [
        'P1', 'P20076', 'P20090', 'P20091', 'P2']
    assert pc['links'] == [('P20076', 76), ('P20076', 85), ('P20090', 90),
                           ('P20091', 91)]
    assert pc['part_net'] == {'P20076': 'A'}
    assert pc['core_ports'] == ['P20076']
    assert pc['merged'] == {'P20085': 'P20076'}


def test_a_piece_whose_every_channel_is_open_is_not_solved():
    """open_pieces: a piece whose every net is a channel's that unrouted found
    open end to end (a layer piece, a via model on its chain's middle net) is
    left out; one with a live channel's net beside them, or a net of no
    channel, is kept."""
    import types
    P = lambda net: types.SimpleNamespace(name='P1', net=net)
    piece = lambda name, nets=(), vias=(), ports=(): dict(
        name=name, nets=list(nets), vias=[dict(net=n) for n in vias],
        board=types.SimpleNamespace(ports=[P(n) for n in ports]))
    ends = [dict(port='P1', net='A', nets=['a1', 'a2']),
            dict(port='P2', net='A', nets=['a1', 'a2']),
            dict(port='P3', net='B', nets=['b1']),
            dict(port='P4', net='B', nets=['b1'])]
    parts = [piece('alone', ['a1']), piece('beside', ['a1', 'b1']),
             piece('via', vias=['a2'], ports=['a2']), piece('other', ['x']),
             piece('live', ['b1']), piece('empty')]
    assert SG.open_pieces(parts, [('A', ['alone', None])], ends) == [
        'alone', 'via']
    assert SG.open_pieces(parts, [], ends) == []


def test_an_end_port_on_another_nets_copper_leaves_its_channel_out():
    """_off_its_net: an end port the model puts on another net's copper than
    its pad's (two nets' copper overlapping in the file: LimeRFE's U11 pad 4
    on /30MHz_TX's track, read as 37.7 % far-end coupling) names its channel,
    once; a port on its own pad's copper, or with no net, none."""
    import types
    P = lambda name, net: types.SimpleNamespace(name=name, net=net)
    ends = [dict(port='P1', net='C', pad_net='c1', ref='J5', pad='1'),
            dict(port='P2', net='C', pad_net='c2', ref='U11', pad='4')]
    m = types.SimpleNamespace(ports=[P('P1', 'c1'), P('P2', 'TX'),
                                     P('P1000', 'c1')])
    (leg, port, x), = SG._off_its_net(m, ends)
    assert (leg, port.name, x['ref']) == ('C', 'P2', 'U11')
    m.ports[0].net = 'GND'          # (both its ends off: the channel once)
    assert [leg for leg, _, _ in SG._off_its_net(m, ends)] == ['C']
    m.ports[0].net, m.ports[1].net = 'c1', 'c2'
    assert SG._off_its_net(m, ends) == []
    m.ports[1].net = None
    assert SG._off_its_net(m, ends) == []


def test_a_via_model_that_is_no_meshed_box():
    """_unmeshable: a port whose reference is a signal net's copper (a cut
    circle inside the antipad: the port over its own net's copper on the
    board's far side, shorted by the via, the model open), or a box with
    fewer than two ground vias (its clipped planes tied by none, or one: a
    shorted patch), is no meshed via model; two ground vias and ports over a
    plane, or copper of no net, are."""
    import types
    from shapely.geometry import box
    P = lambda name, ref: types.SimpleNamespace(
        name=name, kind='z', ref_layer=ref, c=np.array([0.0, 0.0]))
    under = {'In1.Cu': 'GND', 'B.Cu': 'SIG', 'In4.Cu': None}
    b = types.SimpleNamespace(ports=[P('P1', 'In1.Cu'), P('P2', 'In4.Cu')],
                              net_at=lambda lay, x, y: under[lay])
    m = types.SimpleNamespace(ground_nets={'GND'}, vias=[
        (1.0, 1.0, 0.15, 0, 1), (-1.0, 1.0, 0.15, 0, 1),
        (9.0, 9.0, 0.15, 0, 1), (0.0, 0.0, 0.15, 0, 1)],
        via_nets=['GND', 'GND', 'GND', 'SIG'])
    bx = box(-2, -2, 2, 2)
    assert SG._unmeshable(m, b, bx) == ''
    m.via_nets[1] = 'SIG2'
    assert SG._unmeshable(m, b, bx) == '1 ground via in its box'
    b.ports.append(P('P1001', 'B.Cu'))
    assert SG._unmeshable(m, b, bx) == (
        'port P1001 over signal copper, no plane under')
