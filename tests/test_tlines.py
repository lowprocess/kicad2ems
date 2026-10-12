"""Transmission-line sections: the closed-form impedances against reference values, the section
graph of the example boards (traces, pads, vias, parts, branches, T-junctions, reference changes)."""
import os
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.solvers.lines.tlines import (build, microstrip, stripline, offset_stripline, cpwg, coax, ellipk,
                                  embedded_microstrip)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# reference values: scikit-rf 2.1 (MLine model='hammerstadjensen', disp='none'; CPW has_metal_backside)
@pytest.mark.parametrize('w,h,t,er,z0,ee', [
    (0.35, 0.2, 0.0, 4.3, 53.267, 3.2388), (0.40, 0.2104, 0.035, 4.4, 48.009, 3.2123),
    (0.40, 0.2104, 0.0, 4.4, 50.200, 3.3294), (3.0, 1.6, 0.035, 4.4, 50.166, 3.3008), (0.1, 0.1, 0.018, 3.5, 72.801, 2.4787)])
def test_microstrip(w, h, t, er, z0, ee):
    z, e = microstrip(w, h, er, t)
    assert z == pytest.approx(z0, rel=2e-3) and e == pytest.approx(ee, rel=2e-3)


@pytest.mark.parametrize('w,s,h,er,z0,ee', [(0.3, 0.15, 0.2, 4.3, 54.159, 3.0126), (1.0, 0.2, 0.5, 4.4, 42.127, 3.0579),
                                            (0.5, 0.5, 1.6, 4.4, 85.910, 2.7540)])
def test_grounded_cpw(w, s, h, er, z0, ee):
    z, e = cpwg(w, s, h, er)
    assert z == pytest.approx(z0, rel=3e-3) and e == pytest.approx(ee, rel=3e-3)


def test_stripline_and_friends():
    import math
    # Cohn's exact zero-thickness result for w 0.15, b 0.4, er 4.3: 30 pi / sqrt(er) K(k) / K(k')
    k = 1 / math.cosh(math.pi * 0.15 / 0.8)
    cohn = 30 * math.pi / math.sqrt(4.3) * ellipk(k) / ellipk(math.sqrt(1 - k * k))
    assert stripline(0.15, 0.4, 4.3)[0] == pytest.approx(cohn, rel=2e-3) == pytest.approx(56.22, abs=0.05)
    assert stripline(0.15, 0.435, 4.3, 0.035)[0] < cohn                       # thickness lowers Z0
    assert offset_stripline(0.15, 0.2, 0.2, 4.3)[0] == pytest.approx(stripline(0.15, 0.4, 4.3)[0])
    assert offset_stripline(0.15, 0.1, 0.3, 4.3)[0] < stripline(0.15, 0.4, 4.3)[0]
    assert ellipk(0.0) == pytest.approx(math.pi / 2)
    assert coax(1.0, 2.3, 1.0) == pytest.approx(49.97, abs=0.05)
    z, ee = embedded_microstrip(0.35, 0.2, 4.3, 0.0, 1.0, 4.3)               # deeply buried: eeff -> er
    assert ee == pytest.approx(4.3, rel=0.02) and z < microstrip(0.35, 0.2, 4.3)[0]


def test_transition_sections(demo_files):
    base, _ = demo_files
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', ground='B.Cu',
              log=lambda *a: None)
    lm = build(m)
    (p, q, secs, branches), = lm.paths
    assert (p, q) == ('P1', 'P2')
    xs = [e.xs for e, _, _ in secs]
    assert xs[0] == 'microstrip' and xs[-1] == 'stripline' and 'coax' in xs           # the via in the antipad
    ms, sl = secs[0][0], secs[-1][0]
    assert ms.z0_sheet == pytest.approx(53.27, abs=0.05) and ms.z0 < ms.z0_sheet          # 35 um copper: lower
    assert sl.z0_sheet == pytest.approx(56.2, abs=0.1) and sl.layer == 'In2.Cu'
    # the In1 antipad under the trace next to the via: sections of their own, higher Z0
    near = [e for e, _, _ in secs if e.kind == 'trace' and e.length < 1]
    assert near and all(e.z0 > ms.z0 for e in near)
    total = sum(e.length for e, _, _ in secs)
    assert total == pytest.approx(19.875 + 0.4, abs=0.02)                              # port to port + barrel
    assert all(s0 == pytest.approx(sum(e.length for e, _, _ in secs[:i])) for i, (_, s0, _) in enumerate(secs))


def test_lowpass_pads_parts_and_branch(boards):
    base = boards['lowpass']
    m = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None)
    lm = build(m)
    (p, q, secs, branches), = lm.paths
    kinds = [e.kind for e, _, _ in secs]
    assert kinds.count('part') == 2 and kinds.count('pad') == 4                         # L1, L3 with their pads
    pads = [e for e, _, _ in secs if e.kind == 'pad']
    trace = secs[0][0]
    assert all(e.w > trace.w and e.z0 < trace.z0 for e in pads)                        # pads: wide, low Z0
    assert [e.label for e, _, _ in secs if e.kind == 'part'] == ['L1', 'L3']
    (at, L, lab, z), = branches                                                         # C2 hangs off a T-junction
    assert 'C2' in lab and 'to ground' in lab and 5 < at < 10
    # the trace through the T-junction is split there: no trace passes the junction
    assert sum(1 for e, _, _ in secs if e.kind == 'trace') == 4


def test_filter_feeds_and_gap_ports(boards):
    m = Board(KiCadBoard(os.path.join(ROOT, 'examples', 'filter.kicad_pcb')), nets=['RF', 'GND'], log=lambda *a: None)
    lm = build(m)
    assert set(lm.ports) == {'P1', 'P2'}
    assert all(secs for _, _, secs, _ in lm.paths)
    dip = Board(KiCadBoard(boards['dipole'] + '.kicad_pcb'), log=lambda *a: None)
    lm = build(dip)
    assert not lm.paths and any('gap port' in n for n in lm.notes)


def test_ground_nets_region_and_partner(boards, demo_files):
    """A shunt part's ground pad with a track stays a shunt; a region crop clips the traces; a
    differential leg's partner is not its coplanar ground; a trace never references its own net."""
    base = boards['lowpass']
    m = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None)
    assert 'GND' in m.ground_nets
    c2 = [e for e in build(m).elements if e.kind == 'part' and e.label == 'C2']
    assert c2 and c2[0].b == 'gnd'
    demo, diff = demo_files
    m = Board(KiCadBoard(demo + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=demo + '-top.pos', ground='B.Cu',
              region=(0, -20, 14, 20), log=lambda *a: None)
    (p, q, secs, _), = build(m).paths
    assert q is None and sum(e.length for e, _, _ in secs) == pytest.approx(14 - 2.0875 + 0.4, abs=0.05)
    m = Board(KiCadBoard(diff + '.kicad_pcb'), pos_file=diff + '-top.pos', ground='B.Cu', log=lambda *a: None)
    lm = build(m)
    assert any('differential pair' in n for n in lm.notes)
    assert all(not any('beside it' in note for note in e.notes) for _, _, secs, _ in lm.paths for e, _, _ in secs)
    assert all(e.xs == 'microstrip' for _, _, secs, _ in lm.paths for e, _, _ in secs if e.kind == 'trace')


def test_edge_launch_pad_port_and_taper(boards):
    """The connector's signal pad drawn as two pads numbered 1 is one pad section, running from where the
    feed meets it to P2 on it; the taper drawn as footprint copper widens the trace in steps; the
    45 degree opening of the F.Cu pour widens the coplanar gap in steps."""
    base = boards['launch']
    m = Board(KiCadBoard(base + '.kicad_pcb'), nets=['RF', 'GND'], pos_file=base + '-top.pos', log=lambda *a: None)
    lm = build(m)
    (p, q, secs, branches), = lm.paths
    assert (p, q) == ('P1', 'P2') and not branches
    p1, p2 = (next(pp for pp in m.ports if pp.name == n) for n in ('P1', 'P2'))
    assert sum(e.length for e, _, _ in secs) == pytest.approx(p2.c[0] - p1.c[0], abs=0.01)   # port to port
    pad = secs[-1][0]
    assert pad.kind == 'pad' and 'J1.1' in pad.label and pad.w == pytest.approx(1.6, abs=0.01)
    assert pad.length == pytest.approx(p2.c[0] - 13.45, abs=0.05)            # from the oval's edge to the port
    assert pad.xs == 'cpwg' and pad.h == pytest.approx(1.4858, abs=1e-3)       # over the cut-away inner planes
    assert len({e.label for e in lm.elements if e.kind == 'pad'}) == 1         # one pad, not two
    feed = secs[0][0]
    assert feed.xs == 'cpwg' and feed.s == pytest.approx(0.25, abs=1e-3) and feed.z0 == pytest.approx(55.9, abs=0.5)
    rest = [(e, s0 + p1.c[0]) for e, s0, _ in secs[1:-1]]
    assert all(e.kind == 'trace' for e, _ in rest)
    opening = [e for e, x in rest if x < 11.4]                 # the pour opens out, the trace is still 0.32 mm
    assert len(opening) >= 3 and all(e.w == pytest.approx(0.32) for e in opening)
    z = [e.z0 for e in opening]
    assert z == sorted(z) and z[0] > feed.z0                   # rising Z0 as the gap opens
    taper = [e for e, x in rest if x > 11.6]
    w = [e.w for e in taper]
    assert len(taper) >= 4 and w == sorted(w) and w[0] > 0.33 and w[-1] > 1.0     # the drawn taper, stepped
    assert all('widened' in e.label for e in taper)


def test_sma_launch_example_line_model():
    """examples/sma_launch: the drawn taper in the connector footprint widens the line, and the line
    model stays close to the openEMS reference over 1-6 GHz."""
    import numpy as np
    rf = pytest.importorskip('skrf')
    from kicad2ems.importers.kicad import KiCadBoard
    from kicad2ems.board import Board
    from kicad2ems.solvers.lines.tlines import build
    from kicad2ems.solvers.lines.fastsolve import solve
    ex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples')
    m = Board(KiCadBoard(os.path.join(ex, 'sma_launch.kicad_pcb')), nets=['REF_SMA_N', 'GND'],
              pos_file=os.path.join(ex, 'sma_launch-all.pos'), log=lambda *a: None)
    lm = build(m)
    widths = [e.w for e in lm.elements if e.kind == 'trace' and e.w]
    assert max(widths) > 1.5 * min(widths)                                   # the taper is seen
    ref = rf.Network(os.path.join(ex, 'reference', 'sma_launch_openems.s2p'))
    s, _ = solve(lm, m.ports, ref.f, backend='skrf')
    s21 = 20 * np.log10(np.abs(s[:, 1, 0]))
    assert np.max(np.abs(s21 - ref.s_db[:, 1, 0])) < 0.6


def test_stubs_drawn_as_rectangles_are_tees(tmp_path):
    """Stubs drawn as filled rectangles over the line (not tracks) are tees with open stubs: the line keeps its
    50 ohm, and the notch stays near 2.4 GHz (the shapes had widened the line into an 8 ohm piece, the notch at
    3.5 GHz)."""
    import sys
    import numpy as np
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'examples'))
    import make_rf_examples as rf
    path = str(tmp_path / 'stubrect.kicad_pcb')
    rf.stubnotch(path, rects=True)
    m = Board(KiCadBoard(path), ground='B.Cu', log=lambda *a: None)
    lm = build(m)
    (path_,) = lm.summary()['paths']
    assert min(s['z0'] for s in path_['sections']) > 45
    long_ = [b for b in path_['branches'] if b['length'] > 10]
    assert len(long_) == 2 and all(abs(b['length'] - long_[0]['length']) < 1e-6 for b in long_)
    pytest.importorskip('skrf')
    from kicad2ems.solvers.lines.fastsolve import solve
    f = np.linspace(1e9, 4e9, 301)
    S, _ = solve(lm, m.ports, f, backend='skrf')
    notch = f[np.argmin(np.abs(S[:, 1, 0]))]
    assert 2.2e9 < notch < 2.6e9
