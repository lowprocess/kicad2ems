import math
import os

import numpy as np
import pytest

from dfsynth.microstrip import Substrate
from dfsynth.prototype import gvalues, estimate_order
from dfsynth.topologies import Spec, build, KINDS
from dfsynth.calibrate import calibrate, targets, edges, errors, sweep
from dfsynth.circuit import sparams, db
from dfsynth import kicad
from dfsynth.validate import read_touchstone


def test_gvalues_tables():
    # Matthaei / Pozar tables
    assert gvalues('chebyshev', 3, 0.5)[1:4] == pytest.approx([1.5963, 1.0967, 1.5963], abs=1e-4)
    assert gvalues('chebyshev', 5, 0.1)[1:6] == pytest.approx([1.1468, 1.3712, 1.9750, 1.3712, 1.1468], abs=1e-4)
    assert gvalues('butterworth', 4)[1:5] == pytest.approx([0.7654, 1.8478, 1.8478, 0.7654], abs=1e-4)
    assert gvalues('chebyshev', 4, 0.5)[5] == pytest.approx(1.9841, abs=1e-4)


def test_order():
    assert estimate_order('butterworth', 40, 2.0) == 7
    assert estimate_order('chebyshev', 40, 2.0, 0.1) == 6      # N = 5 gives 34.8 dB


def test_microstrip_50ohm():
    sub = Substrate.preset('RO4003C-0.508', t=0)
    w = sub.width(50)
    assert 1.05 < w < 1.2                      # ~1.1 mm, the usual figure for 20 mil RO4003C
    w, s = sub.coupled_synth(70, 38)
    ze, zo, _, _ = sub.coupled(w, s)
    assert (ze, zo) == pytest.approx((70, 38), rel=1e-6)


@pytest.mark.parametrize('kind,topo,f0,bw', [
    ('lowpass', 'stepped', 2e9, 0), ('lowpass', 'stub', 2e9, 0), ('bandpass', 'coupled', 5e9, 0.5e9),
    ('bandpass', 'endcoupled', 10e9, 0.15e9), ('bandpass', 'stub', 3e9, 1.5e9)])
def test_ideal_synthesis(kind, topo, f0, bw):
    """The TEM network of each synthesis lands on the spec (before any calibration)."""
    sub = Substrate.preset('Alumina-0.635' if topo == 'endcoupled' else 'RO4003C-0.508')
    spec = Spec(kind, topo, f0, bw, 'chebyshev', 5, 0.1)
    d = build(spec, sub)
    f = sweep(spec, 4001)
    ef, eb = errors(spec, edges(spec, f, db(sparams(d.ideal, f)[1])))
    assert abs(ef - 1) < 0.05
    if eb is not None:
        assert abs(eb - 1) < 0.08


@pytest.mark.parametrize('kind,topo,f0,bw,sub', [
    ('lowpass', 'stepped', 2e9, 0, 'RO4003C-0.508'), ('lowpass', 'stub', 2e9, 0, 'RO4003C-0.508'),
    ('highpass', 'stub', 2e9, 0, 'RO4003C-0.508'), ('bandpass', 'coupled', 5e9, 0.5e9, 'RO4003C-0.813'),
    ('bandpass', 'hairpin', 2.4e9, 0.3e9, 'FR4-1.6'), ('bandpass', 'endcoupled', 10e9, 0.3e9, 'Alumina-0.635'),
    ('bandpass', 'stub', 3e9, 1.5e9, 'RO4003C-0.508'), ('bandstop', 'stub', 2.4e9, 1e9, 'RO4003C-0.508')])
def test_calibrated_and_written(kind, topo, f0, bw, sub, tmp_path):
    sub = Substrate.preset(sub)
    spec = Spec(kind, topo, f0, bw, 'chebyshev', 5, 0.1)
    d = calibrate(spec, sub)
    f = sweep(spec, 4001)
    from dfsynth.calibrate import model_response
    _, s21 = model_response(d, f, lossless=True)
    ef, eb = errors(spec, edges(spec, f, db(s21)))
    assert abs(ef - 1) < 2e-3
    if eb is not None:
        assert abs(eb - 1) < 2e-2
    pcb, cfg = kicad.write(d, str(tmp_path / 'f'))
    text = open(pcb).read()
    assert text.count('(') == text.count(')')
    assert text.count('"kicad2ems:RF_Port"') == 2
    assert 'board   = "f.kicad_pcb"' in open(cfg).read()


def test_touchstone_roundtrip(tmp_path):
    from dfsynth.cli import write_model_s2p
    sub = Substrate.preset('RO4003C-0.508')
    spec = Spec('lowpass', 'stepped', 2e9, order=5)
    d = calibrate(spec, sub)
    f = np.linspace(0.1e9, 5e9, 50)
    write_model_s2p(d, str(tmp_path / 'm.s2p'), f)
    f2, s = read_touchstone(str(tmp_path / 'm.s2p'))
    from dfsynth.calibrate import model_response
    s11, s21 = model_response(d, f)
    assert np.allclose(f2, f, rtol=1e-6)
    assert np.allclose(s[:, 1, 0], s21, atol=1e-5) and np.allclose(s[:, 0, 0], s11, atol=1e-5)


@pytest.mark.parametrize('topo,sub,f0,bw', [('coupled', 'RO4003C-0.813', 5e9, 0.5e9),
                                            ('coupled', 'RO4003C-0.508', 5e9, 0.75e9),
                                            ('hairpin', 'FR4-1.6', 2.4e9, 0.3e9),
                                            ('hairpin', 'RO4003C-0.813', 2.4e9, 0.36e9),
                                            ('endcoupled', 'Alumina-0.635', 10e9, 0.3e9)])
def test_resonators_are_separate_copper(topo, sub, f0, bw):
    """Feeds and resonators must not touch across a coupling gap: order N gives N + 2 copper pieces."""
    shapely = pytest.importorskip('shapely')
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    spec = Spec('bandpass', topo, f0, bw, 'chebyshev', 5, 0.1)
    d = calibrate(spec, Substrate.preset(sub))
    u = unary_union([Polygon(p) for p in d.geometry.polys])
    parts = list(u.geoms) if hasattr(u, 'geoms') else [u]
    assert len(parts) == spec.order + 2
    assert all(len(p.interiors) == 0 for p in parts)
