"""The EMerge plugin without EMerge: its check names how to install it, and a part's element as EMerge gets it
spans pad to pad (the copper reaching into the gap cut away). EMerge itself runs in test_conformance.py when
it is installed (KICAD2EMS_EMERGE_PYTHON)."""
import os, sys
import pytest
from shapely.geometry import LineString
from shapely.ops import unary_union
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.solvers import registry
from kicad2ems.solvers.emerge.plugin import emerge_status, pad_span

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'examples')


def test_registered_and_check_without_emerge(tmp_path):
    assert 'emerge' in registry() and registry()['emerge'].label == 'EMerge (FEM, experimental)'
    py = tmp_path / 'python'                                        # a Python without EMerge
    py.write_text(f"#!/bin/sh\nexec {sys.executable} -I -S \"$@\"\n")
    py.chmod(0o755)
    ok, what = emerge_status(str(py))
    assert not ok and '--emerge-python' in what


def test_part_spans_pad_to_pad():
    """The Wilkinson's resistor: kicad2ems's element starts where the side-fed tracks' copper ends, so copper
    meets it along half its width; EMerge's element reaches the pads, where copper covers the whole width."""
    m = Board(KiCadBoard(os.path.join(EX, 'rf_wilkinson.kicad_pcb')), ground='B.Cu', log=lambda *a: None)
    (part,) = m.parts
    d = pad_span(m, part)
    a, o = part.axis, 1 - part.axis
    cu = unary_union(m.copper[part.layer])
    edge = lambda t: LineString([(t, d['s'][o]), (t, d['e'][o])] if a == 0 else [(d['s'][o], t), (d['e'][o], t)])
    w = abs(d['e'][o] - d['s'][o])
    assert cu.intersection(edge(part.s[a] - 1e-4)).length < 0.6 * w           # kicad2ems's element: half the width
    for t, sgn in ((d['s'][a], -1), (d['e'][a], 1)):
        assert cu.intersection(edge(t + sgn * 1e-4)).length >= 0.99 * w      # EMerge's: the whole width
    assert d['cut'] and d['s'][a] < part.s[a] and d['e'][a] > part.e[a] and d['R'] == 100.0
