"""Coupled lines with a jog (R-E17): a polyline trace whose straight run lies beside another line is cut at its
corners and that run paired; a polyline alone stays whole."""
import numpy as np

from kicad2ems.lines import topology
from kicad2ems.lines.elements import Element, LineModel


def trace(a, b, pts, w=0.3):
    e = Element('trace', a, b, float(sum(np.hypot(*np.diff(np.asarray(pts, float), axis=0).T))), layer='F.Cu', w=w,
                pts=np.asarray(pts, float), net=a, label=a)
    e.xs, e.h, e.er, e.z0, e.eeff, e.t = 'microstrip', 0.2, 4.3, 50.0, 3.2, 0.035
    return e


def test_a_jogged_trace_couples_along_its_straight_run():
    lm = LineModel()
    jog = trace('A', 'A2', [(0, 0), (5, 0), (6, 1), (16, 1)])       # straight, a jog, then 10 mm along y = 1
    other = trace('B', 'B2', [(6, 1.5), (16, 1.5)])                 # beside that run, 0.2 mm edge to edge
    lm.elements = [jog, other]
    topology._find_groups(lm, None, None)
    assert len(lm.groups) == 1
    (e1, _, _), (e2, _, _) = lm.groups[0]['members']
    assert {e1.net, e2.net} == {'A', 'B'} and abs(lm.groups[0]['length'] - 10.0) < 1e-6
    pieces = [e for e in lm.elements if e.net == 'A']
    assert len(pieces) == 3 and pieces[0].a == 'A' and pieces[-1].b == 'A2'                 # cut at its two corners
    assert all(pieces[k].b == pieces[k + 1].a for k in range(2))


def test_a_jog_with_nothing_beside_it_stays_whole():
    lm = LineModel()
    jog = trace('A', 'A2', [(0, 0), (5, 0), (6, 1), (16, 1)])
    far = trace('B', 'B2', [(6, 9.0), (16, 9.0)])                    # 40 h away
    lm.elements = [jog, far]
    topology._find_groups(lm, None, None)
    assert not lm.groups and lm.elements[0] is jog
