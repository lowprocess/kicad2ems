"""mom25d's conductor loss on thick copper's sides (thickness 'sides'): the
plates' functions carry the copper's surface impedance as the sheet's do
(Sides.gram, fast.extra_blocks). They had carried none, so a trace's edge
current, which the plates take, was lossless."""
import numpy as np
import pytest
from shapely.geometry import box

from kicad2ems.emsdev.numerics.base import _BAR3, _W3, quad_pts
from kicad2ems.mom25d.multilayer import Layer, LayerStack
from kicad2ems.mom25d.sim import Metal, Simulation, Via, ViaPort

SIDES = dict(t={1: 35e-6}, up={1: True})
# Hammerstad and Jensen's conductor loss of this microstrip (w 0.383 mm on 0.2
# mm, er 4.4, t 35 um, 5.8e7 S/m) at 10 GHz: 0.00935 dB/mm (scikit-rf's MLine,
# as qucsator), the strip's share by Wheeler's incremental inductance 0.74
# (the ground's, which the ideal plane does not have, 0.26)
STRIP_DB_MM = 0.00935 * 0.74


def _strip(sides, metal, L=10e-3, w=0.383e-3):
    """A 50 ohm microstrip over the ideal ground, a via port 0.15 mm in from
    each end (9.7 mm apart)."""
    st = LayerStack([Layer(0.2e-3, 4.4, 0.0)])
    ports = [ViaPort(Via(-L / 2 + 0.15e-3, 0, 0, 1, 0.1e-3)),
             ViaPort(Via(L / 2 - 0.15e-3, 0, 0, 1, 0.1e-3))]
    sim = Simulation(st, {1: [box(-L / 2, -w / 2, L / 2, w / 2)]}, ports,
                     dmax=0.5e-3, w_ref={1: w}, solver='direct',
                     precision='double', verbose=False, metal=metal,
                     sides=sides)
    sim.gpu = 'off'
    return sim


def test_the_sides_gram_is_the_functions_overlap():
    """Sides.gram against the functions' halves integrated directly: a fold's
    and a vertical's own entry, a fold with an edge on its sheet triangle;
    symmetric, no edge-edge block (gram_matrix's)."""
    pytest.importorskip('kicad2ems.mom25d._core')
    sim = _strip(SIDES, None)
    sd, r, Ne = sim.sides, sim.rwg, sim.Ne
    G = sd.gram(sim).toarray()
    c0 = Ne + sim.Nv - sd.N
    assert np.abs(G - G.T).max() <= 1e-12 * np.abs(G).max()
    assert not G[:Ne, :Ne].any()
    Nt = len(r.tris)
    pts = np.concatenate([np.concatenate(
        [quad_pts(r, _BAR3), np.zeros((Nt, 3, 1))], 2), sd.op])
    area = np.r_[np.abs(r.area), 0.5 * np.repeat(sd.L * sd.t, 2)]

    def field(n):
        """{triangle: f at its 3 points} of side function n."""
        out = {}
        for j in np.flatnonzero(sd.h_fn == n):
            T = int(sd.h_obs[j])
            out[T] = out.get(T, 0) + sd.h_alpha[j] * (pts[T] - sd.h_v[j])
        return out

    def edge_field(e, T):
        a = r.Aal[T, e]
        return a * pts[T] - np.r_[r.Bx[T, e], r.By[T, e], 0.0]

    def inner(fa, fb):
        return sum(area[T] * (_W3 * (fa[T] * fb[T]).sum(-1)).sum()
                   for T in set(fa) & set(fb))
    fold, vert = 0, sd.N - 1
    for n in (fold, vert):
        f = field(n)
        assert G[c0 + n, c0 + n] == pytest.approx(inner(f, f), rel=1e-10)
    f = field(fold)
    T = int(sd.T[0])                   # the fold's sheet triangle
    e = int(np.flatnonzero(r.Aal[T].toarray().ravel())[0])
    assert G[e, c0 + fold] == pytest.approx(
        inner({T: edge_field(e, T)}, f), rel=1e-10, abs=1e-30)


def test_a_microstrip_s_loss_on_its_sides():
    """The strip's copper loss at 10 GHz with thick copper's sides, over 9.7
    mm: within 20 % of Hammerstad and Jensen's strip share (measured 0.90 of
    it; the plates lossless as before: 0.46), passive."""
    pytest.importorskip('kicad2ems.mom25d._core')
    db = lambda x: 20 * np.log10(np.abs(x))
    S0 = _strip(SIDES, None).sparams(10e9)
    S1 = _strip(SIDES, Metal(5.8e7, 35e-6)).sparams(10e9)
    loss = (db(S0[1, 0]) - db(S1[1, 0])) / 9.7
    assert 0.8 < loss / STRIP_DB_MM < 1.2, loss / STRIP_DB_MM
    assert np.linalg.svd(S1, compute_uv=False).max() <= 1 + 1e-9
