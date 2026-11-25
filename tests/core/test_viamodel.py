"""The physics-based via model (kicad2ems.core.viamodel): a cavity left to the
join (each of its segments a port) and joined again is the model."""
import math
import os
import subprocess
import sys

import numpy as np
import pytest

from kicad2ems.core import segment as SG
from kicad2ems.core import viamodel as VM

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))


def _via_pieces(tmp, name, nets):
    """The example's via pieces' (board file, vias)."""
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.pipeline import board_from_settings
    subprocess.check_call([sys.executable,
                           os.path.join(ROOT, 'examples', 'make_rf_examples.py'),
                           name, str(tmp / f"rf_{name}")],
                          stdout=subprocess.DEVNULL)
    s = build_settings(dict(board=str(tmp / f"rf_{name}.kicad_pcb"),
                            nets=nets))
    m = board_from_settings(s, log=lambda *x: None)
    kb = KiCadBoard(s.board)
    parts, _ = SG.pieces(m, kb, kb, set(m.sel or [
        n for ns in m.copper_nets.values() for n in ns]))
    return [(SG._to_dict(p['board'], p), p['vias']) for p in parts
            if p['kind'] == 'via']


def _infinite(model, pair, f, z0=50.0):
    """The cavity `pair` (upper plane, lower) between the model's cavity
    ports as the model has it: the infinite planes' w mu0 d / 4 H0^(2)(k rho)
    between every segment and the box's ground vias, each segment weighted by
    its share of the height, the ground vias shorted. S (nf, n, n) at z0."""
    from scipy.special import hankel2
    pz, sig = model['pz'], model['sig']
    hi, lo = pz[pair[0]], pz[pair[1]]
    cp = [c for c in model['cavity_ports'] if c['cavity'] == pair]
    gs = [g for g in model['gv'] if set(pair) <= set(g['joined'])]
    mem = ([(c['x'], c['y'], sig[c['via']]['a'], c['w'], c['via'])
            for c in cp] + [(g['x'], g['y'], g['a'], 1.0, None) for g in gs])
    e = VM.eps_between(model['diel'], lo, hi)
    n = len(cp)
    out = np.empty((len(f), n, n), complex)
    for i, fi in enumerate(f):
        w = 2 * math.pi * fi
        k = w * math.sqrt(VM.MU0 * VM.EPS0) * np.sqrt(e)
        Z = np.empty((len(mem), len(mem)), complex)
        for a, (xa, ya, ra, wa, va) in enumerate(mem):
            for b, (xb, yb, rb, wb, vb) in enumerate(mem):
                same = a == b or (va is not None and va == vb)
                rho = (ra if same else math.hypot(xa - xb, ya - yb)) * 1e-3
                Z[a, b] = (wa * wb * w * VM.MU0 * (hi - lo) * 1e-3 / 4
                           * hankel2(0, k * rho))
        Zs = Z[:n, :n] - Z[:n, n:] @ np.linalg.solve(Z[n:, n:], Z[n:, :n])
        out[i] = np.eye(n) - 2 * z0 * np.linalg.inv(Zs + z0 * np.eye(n))
    return out


@pytest.mark.parametrize('name, nets', [('msvia', ['RF', 'GND']),
                                        ('diffvia', None)])
def test_a_cavity_left_to_the_join_and_joined_again_is_the_model(
        tmp_path, name, nets):
    """Each cavity the via model crosses left to the join (its segments
    ports, the box's ground vias in it left out) and the infinite planes'
    coupling joined there again: the model's S to rounding (the ports'
    signs, terminals and weights the join's)."""
    f = np.linspace(0.1e9, 10e9, 23)
    for d, vias in _via_pieces(tmp_path, name, nets):
        S0, n0 = VM.solve(VM.build(d, vias), f)
        planes = VM.planes_of(d)
        for pair in zip(planes[:-1], planes[1:]):
            model = VM.build(d, vias, cavities=[pair])
            S, names = VM.solve(model, f)
            cp = [c['name'] for c in model['cavity_ports']]
            if not cp:
                continue
            assert names == n0 + cp
            B = _infinite(model, pair, f)
            J, ext = SG.join([(S, [f"v:{x}" for x in names]),
                              (B, [f"c:{x}" for x in cp])],
                             [(f"v:{x}", f"c:{x}") for x in cp])
            assert ext == [f"v:{x}" for x in n0]
            assert np.abs(J - S0).max() < 1e-9


def test_barrels_at_one_place_are_one(tmp_path):
    """A via drawn twice (its record and its entry), a ground via drawn twice
    and a ground via in a signal via's barrel: the model as without them (two
    barrels at one place had made H0^(2)(0) infinite and S NaN), each said."""
    f = np.linspace(0.1e9, 10e9, 5)
    d, vias = _via_pieces(tmp_path, 'msvia', ['RF', 'GND'])[0]
    S0, n0 = VM.solve(VM.build(d, vias), f)
    ground = set(d['ground_nets'])
    g = next(u for u in d['vias'] if u['net'] in ground)
    s = next(u for u in d['vias'] if u['net'] == vias[0]['net'])
    twice = dict(d, vias=d['vias'] + [dict(g), dict(s)])
    model = VM.build(twice, vias + [dict(vias[0])])
    S, names = VM.solve(model, f)
    assert names == n0 and np.isfinite(S).all()
    assert np.abs(S - S0).max() < 1e-12
    assert len(model['notes']) == 1 and 'one barrel' in model['notes'][0]
    short = dict(d, vias=d['vias'] + [dict(s, net=g['net'])])
    model = VM.build(short, vias)
    S, _ = VM.solve(model, f)
    assert np.abs(S - S0).max() < 1e-12
    assert any("signal via's barrel" in n for n in model['notes'])


def test_the_planes_copper_loss(tmp_path):
    """solve's sigma: each cavity's k^2 times (1 + (1 - j) delta / d) (the
    planes' surface impedance), rough's factor on delta: more loss at the top
    of the band (at 1 GHz within 1e-7: the internal inductance's change of
    the match), still passive; without sigma the model as before (lossless
    copper)."""
    f = np.linspace(1e9, 10e9, 4)
    d, vias = _via_pieces(tmp_path, 'msvia', ['RF', 'GND'])[0]
    model = VM.build(d, vias)
    S0, _ = VM.solve(model, f)
    assert np.abs(VM.solve(model, f, sigma=None)[0] - S0).max() == 0
    lost = lambda S: 1 - (np.abs(S[:, :, 0]) ** 2).sum(1)
    S1, _ = VM.solve(model, f, sigma=5.8e7)
    S2, _ = VM.solve(model, f, sigma=5.8e7, rough=1.0)
    assert (lost(S1) - lost(S0) > -1e-7).all() and (lost(S2) - lost(S1)
                                                     > -1e-7).all()
    assert lost(S0)[-1] < lost(S1)[-1] < lost(S2)[-1]
    assert np.linalg.svd(S2, compute_uv=False).max() <= 1 + 1e-12


def test_a_cavity_left_to_the_join_as_the_board_has_its_planes(tmp_path):
    """The board's cavity (core.cavities: 40 % of the outline) and the box's
    planes (planes_of: half the box) can differ: rf_msvia's In2.Cu cut to a
    ring round its antipad (a third of the box) is no plane in the box. The
    cavity named for the join had matched none of the model's, the via kept
    its infinite planes there with no word; now its planes are the board's
    cavity's, its segment there a port, and the note says so."""
    from shapely.geometry import Point, mapping, shape
    f = np.linspace(1e9, 10e9, 3)
    d, vias = _via_pieces(tmp_path, 'msvia', ['RF', 'GND'])[0]
    v = vias[0]
    ring = Point(v['x'], v['y']).buffer(1.2)
    cut = dict(d, copper=dict(d['copper']))
    cut['copper']['In2.Cu'] = [
        dict(c, polygon=mapping(shape(c['polygon']).intersection(ring)))
        for c in d['copper']['In2.Cu']]
    assert VM.planes_of(cut) == ['In1.Cu']
    model = VM.build(cut, vias, cavities=[('In1.Cu', 'In2.Cu')])
    S, names = VM.solve(model, f)
    assert model['planes'] == ['In1.Cu', 'In2.Cu']
    assert [c['cavity'] for c in model['cavity_ports']] == [('In1.Cu',
                                                            'In2.Cu')]
    assert any('under half the box' in n for n in model['notes'])
    assert np.isfinite(S).all() and len(names) == 3
