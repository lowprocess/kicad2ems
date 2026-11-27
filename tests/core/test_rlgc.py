"""The per-unit-length RLGC (kicad2ems.core.lines.mqs, kicad2ems.core.rlgc):
the 2D eddy-current solve against closed forms (a coax's inner conductor and
tube, Bessel functions; a strip's DC resistance), its high-frequency limit (the
internal reactance equal to R, L tending to mu0 eps0 C_air^-1), Wheeler's
incremental inductance on the field solver, a coupled pair's R matrix
(symmetric, positive definite, growing as sqrt(f)), the losses adding up to R,
and the exports (W-element files, S-parameters) and the board path end to end
in a process of its own."""
import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

from kicad2ems.core.constants import C0, MU0
from kicad2ems.core.lines import mqs as Q
from kicad2ems.core.lines import xsection as X

SIG = 5.8e7
HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, '..', '..', 'examples')


def _f_for(a_over_delta, a_mm):
    """The frequency at which the skin depth is a / a_over_delta."""
    d = a_mm * 1e-3 / a_over_delta
    return 1 / (math.pi * MU0 * SIG * d * d)


def test_the_closed_forms_have_their_limits():
    """The Bessel forms' DC and skin limits: the wire 1 / (pi a^2 sigma) and
    Rs / (2 pi a) (1 + j); the tube 1 / (pi (c^2 - b^2) sigma) and Rs / (2 pi
    b) (1 + j)."""
    a, b, c = 1e-3, 2e-3, 2.5e-3
    lo, hi = 1.0, 1e9
    rs = math.sqrt(math.pi * hi * MU0 / SIG)
    w = Q.wire_internal_z([0, lo, hi], a, SIG)
    t = Q.tube_internal_z([0, lo, hi], b, c, SIG)
    assert w[0].real == pytest.approx(1 / (math.pi * a * a * SIG), rel=1e-12)
    assert w[1].real == pytest.approx(w[0].real, rel=1e-6)
    # the DC internal inductance mu0 / 8 pi
    assert w[1].imag / (2 * math.pi) == pytest.approx(MU0 / (8 * math.pi),
                                                      rel=1e-6)
    assert w[2] == pytest.approx(rs / (2 * math.pi * a) * (1 + 1j), rel=1e-3)
    assert t[0].real == pytest.approx(
        1 / (math.pi * (c * c - b * b) * SIG), rel=1e-12)
    assert t[1].real == pytest.approx(t[0].real, rel=1e-6)
    assert t[2] == pytest.approx(rs / (2 * math.pi * b) * (1 + 1j), rel=1e-3)


def test_coax_against_bessel():
    """A coax (a 1 mm, b 2 mm, c 2.5 mm) as staircases of slabs a / 16
    high: Z = Z_wire + Z_tube + j w mu0 / 2 pi ln(b / a) from DC to a / delta
    = 5, R within 0.6 % and L within 0.25 % (the staircase's own error, which
    halves as its slabs do)."""
    a, b, c = 1.0, 2.0, 2.5
    f = np.array([0.0] + [_f_for(x, a) for x in (0.5, 2.0, 5.0)])
    Z, nodes = Q.impedance(Q.coax_copper(a, b, c, n=16), f, SIG)
    w = 2 * np.pi * f
    ref = (Q.wire_internal_z(f, a * 1e-3, SIG)
           + Q.tube_internal_z(f, b * 1e-3, c * 1e-3, SIG)
           + 1j * w * MU0 / (2 * math.pi) * math.log(b / a))
    # DC: the areas are the circles' (the staircase keeps them)
    assert Z[0, 0, 0].real == pytest.approx(ref[0].real, rel=1e-9)
    assert Z[1:, 0, 0].real == pytest.approx(ref[1:].real, rel=6e-3)
    assert Z[1:, 0, 0].imag == pytest.approx(ref[1:].imag, rel=2.5e-3)


def test_a_strips_dc_resistance():
    """At DC each conductor's current is uniform: the strip's 1 / (sigma w t)
    and the plane's across the box's width, exactly."""
    xs = X.microstrip(0.3, 0.2, 4.3, t=0.035)
    cu = Q.geometry(xs, plane_t=0.018)
    Z, _ = Q.impedance(cu, [0.0], SIG)
    width = cu.box[1] - cu.box[0]
    ref = 1 / (SIG * 0.3e-3 * 0.035e-3) + 1 / (SIG * width * 1e-3 * 0.018e-3)
    assert Z[0, 0, 0].real == pytest.approx(ref, rel=1e-9)


def test_high_frequency_limit():
    """On a 50 ohm microstrip in the skin regime the internal reactance is
    the resistance ((L - L_inf) w / R -> 1) and L tends to mu0 eps0 C_air^-1
    (L_inf: the field solver's), from above; at DC the current fills the
    copper and spreads over the plane."""
    xs = X.microstrip(0.383, 0.2, 4.4, t=0.035)
    f = np.array([0.0, 1e6, 1e9, 1e10])
    r = Q.rlgc(xs, f, SIG)
    w = 2 * np.pi * f
    es = X.solve(xs)
    assert r.L_inf == pytest.approx(es.L)
    # one line: mu0 eps0 / C_air
    assert r.L_inf[0, 0] == pytest.approx(1 / (C0 ** 2 * es.Ca[0, 0]),
                                          rel=1e-12)
    ratio = (r.L[2:, 0, 0] - r.L_inf[0, 0]) * w[2:] / r.R[2:, 0, 0]
    assert ratio == pytest.approx(1.0, abs=0.03)
    assert 0 < r.L[-1, 0, 0] / r.L_inf[0, 0] - 1 < 0.005
    assert np.all(np.diff(r.L[:, 0, 0]) < 0)        # L falls with frequency
    assert np.all(np.diff(r.R[:, 0, 0]) > 0)        # R rises


def test_against_wheelers_incremental_inductance():
    """The skin-effect R of the microstrip by Wheeler's rule on the
    electrostatic solver (each copper surface receded by d: R = Rs / mu0
    dL/dn), which shares nothing with the eddy-current solve but the
    geometry: within 2 %, the strip's share of it within 0.01."""
    w, h, t = 0.383, 0.2, 0.035
    rs = math.sqrt(math.pi * 1e10 * MU0 / SIG)

    def L(ds, dp):
        xs = X.CrossSection(planes=[-dp])
        xs.conductor(-w / 2 + ds, w / 2 - ds, h + ds, h + t - ds)
        return X.solve(xs).L[0, 0]
    d = 0.002
    L0 = L(0, 0)
    rstrip = (L(d, 0) - L0) / (d * 1e-3) * rs / MU0
    rplane = (L(0, d) - L0) / (d * 1e-3) * rs / MU0
    cu = Q.geometry(X.microstrip(w, h, 4.4, t=t))
    (Z, P), _ = Q.impedance(cu, [1e10], SIG, losses=True)
    assert Z[0, 0, 0].real == pytest.approx(rstrip + rplane, rel=0.02)
    assert P[0, 0, 1] / P[0, 0].sum() == pytest.approx(
        rstrip / (rstrip + rplane), abs=0.01)


def test_the_losses_add_up_to_r():
    """Each conductor's loss (W/m for 1 A, peak) summed over the ground and
    the strips is R: at DC (uniform currents), in the proximity regime and in
    the skin regime, for each line of a pair."""
    xs = X.coupled_microstrip(0.15, 0.15, 0.2, 4.3, t=0.035)
    (Z, P), _ = Q.impedance(Q.geometry(xs), [0.0, 1e6, 1e9], SIG,
                            losses=True)
    for j in range(2):
        assert P[:, j].sum(1) == pytest.approx(Z[:, j, j].real, rel=1e-9)


def test_a_coupled_pairs_matrices():
    """A microstrip pair's R and L: symmetric, positive definite, equal on
    its two lines, the mutual R positive (the shared return, the
    neighbour's proximity); R growing as sqrt(f) once the skin is thin (to 3
    %, the corners' crowding adds a little), L falling to L_inf."""
    xs = X.coupled_microstrip(0.15, 0.15, 0.2, 4.3, t=0.035)
    f = np.array([0.0, 1e7, 2.5e9, 1e10])
    r = Q.rlgc(xs, f, SIG)
    for M in (r.R, r.L):
        assert np.abs(M - np.swapaxes(M, 1, 2)).max() <= 1e-12 * np.abs(M).max()
        assert np.all(np.linalg.eigvalsh(M) > 0)
        assert M[:, 0, 0] == pytest.approx(M[:, 1, 1], rel=1e-6)
    assert np.all(r.R[1:, 0, 1] > 0)
    assert r.R[-1, 0, 0] / r.R[-2, 0, 0] == pytest.approx(2.0, rel=0.03)
    assert np.all(r.L[-1] - r.L_inf >= -1e-12)
    # G and C: the field solver's, G = w G_w
    es = X.solve(xs)
    assert r.C[1] == pytest.approx(es.C)
    assert r.G[-1] == pytest.approx(2 * np.pi * 1e10 * es.G_w)


def test_a_stripline_and_coplanar_ground():
    """Every cross-section kind: a stripline pair between two planes (each
    its own copper) and a grounded CPW whose ground runs to the box: R
    positive definite, rising, L above L_inf."""
    for xs in (X.coupled_stripline(0.1, 0.12, 0.15, 0.15, 3.7, t=0.018),
               X.microstrip(0.3, 0.2, 4.3, t=0.035, s=0.15)):
        r = Q.rlgc(xs, [0.0, 1e8, 1e10], SIG)
        assert np.all(np.linalg.eigvalsh(r.R) > 0)
        assert np.all(np.diff(r.R[:, 0, 0]) > 0)
        assert np.all(r.L[:, 0, 0] > r.L_inf[0, 0])


def test_roughness_raises_the_loss():
    """1 um RMS on copper whose skin is 0.66 um (10 GHz): R up by about
    Hammerstad's factor; at DC nothing changes."""
    from kicad2ems.core.roughness import hammerstad
    xs = X.microstrip(0.383, 0.2, 4.4, t=0.035)
    smooth = Q.rlgc(xs, [0.0, 1e10], SIG)
    rough = Q.rlgc(xs, [0.0, 1e10], SIG, rough=1.0)
    assert rough.R[0] == pytest.approx(smooth.R[0])
    k = float(hammerstad(1e10, 1.0, SIG))
    assert rough.R[1, 0, 0] / smooth.R[1, 0, 0] == pytest.approx(k, rel=0.03)


def test_a_touching_conductor_is_refused():
    xs = X.CrossSection(planes=[0.0])
    xs.conductor(-0.1, 0.1, 0.2, 0.235)
    xs.conductor(0.1, 0.4, 0.2, 0.235, ground=True)
    with pytest.raises(ValueError, match='touches'):
        Q.geometry(xs)


def test_interpolation_between_solves():
    """RLGC.at: between solves R as log-log (exact for R ~ sqrt f), the
    internal inductance too; at a solved frequency the solve's own values."""
    xs = X.microstrip(0.383, 0.2, 4.4, t=0.035)
    f = np.array([0.0, 1e8, 1e9, 1e10])
    r = Q.rlgc(xs, f, SIG)
    same = r.at(f)
    assert same.R == pytest.approx(r.R) and same.L == pytest.approx(r.L)
    mid = r.at([np.sqrt(1e9 * 1e10)]).R[0, 0, 0]
    direct = Q.rlgc(xs, [np.sqrt(1e9 * 1e10)], SIG, mesh_f=1e10).R[0, 0, 0]
    assert mid == pytest.approx(direct, rel=0.01)


def test_sparams_of_the_rlgc():
    """zy_s on one lossless line of the RLGC's L_inf and C: matched to its
    own Z0, S21 has the line's delay; with the loss |S21| below 1 by the
    attenuation."""
    from kicad2ems.core.lines.coupled import zy_s
    xs = X.microstrip(0.383, 0.2, 4.4, t=0.035)
    r = Q.rlgc(xs, [1e9], SIG)
    z0 = math.sqrt(r.L_inf[0, 0] / r.C[0, 0, 0])
    w = 2 * math.pi * 1e9
    S = zy_s(1j * w * r.L_inf[None], 1j * w * r.C, 20.0, z0)
    assert abs(S[0, 0, 0]) < 1e-9
    assert np.angle(S[0, 1, 0]) == pytest.approx(
        np.angle(np.exp(-1j * w * math.sqrt(r.L_inf[0, 0] * r.C[0, 0, 0])
                        * 0.02)), abs=1e-9)
    S = zy_s(r.z(), r.y(), 20.0, z0)
    alpha = np.sqrt(r.z()[0, 0, 0] * r.y()[0, 0, 0]).real
    assert abs(S[0, 1, 0]) == pytest.approx(math.exp(-alpha * 0.02), rel=1e-3)


def test_the_w_element_files(tmp_path):
    """The RLGC file's six lower triangles (Lo the external inductance, Ro
    the DC resistance, Rs fitted to R - Ro ~ sqrt f) and the tabular model's
    four sp models, every frequency."""
    from kicad2ems.core import rlgc as K
    xs = X.coupled_microstrip(0.15, 0.15, 0.2, 4.3, t=0.035, tand=0.02)
    f = K.frequencies(0.01, 10, 3)
    r = Q.rlgc(xs, f, SIG)
    p = K.write_rlgc(tmp_path / 'p.rlgc', r, 'pair')
    nums = [list(map(float, ln.split())) for ln in open(p)
            if not ln.startswith('*')]
    assert nums[0] == [2]
    blocks = [nums[1 + 2 * k:3 + 2 * k] for k in range(6)]
    Lo, Co, Ro, Go, Rs, Gd = blocks
    assert Lo == [[pytest.approx(r.L_inf[0, 0], rel=1e-6)],
                  [pytest.approx(r.L_inf[1, 0], rel=1e-6),
                   pytest.approx(r.L_inf[1, 1], rel=1e-6)]]
    assert Ro[0][0] == pytest.approx(r.R[0, 0, 0], rel=1e-6)
    # the fit holds the top of the band within a few %
    assert Ro[0][0] + Rs[0][0] * math.sqrt(f[-1]) == pytest.approx(
        r.R[-1, 0, 0], rel=0.05)
    assert Gd[0][0] * f[-1] == pytest.approx(r.G[-1, 0, 0], rel=1e-6)
    t = open(K.write_table(tmp_path / 'p_w.inc', r, 'pair')).read()
    assert t.count('sp N=2') == 4 and 'MODELTYPE=TABLE' in t
    assert t.count(f"DATA=({len(f)}") == 4


def test_the_cli_on_a_cross_section(tmp_path):
    """python -m kicad2ems.core.rlgc --xs microstrip, its files and JSON."""
    out = tmp_path / 'o'
    p = subprocess.run(
        [sys.executable, '-m', 'kicad2ems.core.rlgc', '--xs', 'microstrip',
         '--w', '0.15', '--lines', '2', '--gap', '0.15', '--h', '0.2',
         '--er', '4.3', '--t', '0.035', '--fmax', '10', '--per-decade', '2',
         '--out', str(out)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    d = json.load(open(out / 'rlgc.json'))
    assert d['format'] == 'kicad2ems-rlgc' and len(d['sections']) == 1
    s = d['sections'][0]
    assert s['kind'] == 'coupled' and 85 < s['at_fmax']['zdiff'] < 115
    for k in ('rlgc', 'table', 'touchstone', 'figure'):
        assert (out / s['files'][k]).is_file()


def test_the_board_path(tmp_path):
    """rf_diffvia's pair: its coupled stretches on F.Cu and In2.Cu and their
    single breakouts, each on the board's cut, the planes their own layers'
    copper; the analysis's report and figures of the result."""
    from kicad2ems.core.analyses import registry
    board = os.path.join(EXAMPLES, 'rf_diffvia.kicad_pcb')
    out = tmp_path / 'b'
    a = registry()['rlgc']
    cmd = a.command(dict(nets=['D_*'], fmax=10, per_decade=2), board,
                    str(out))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    d = json.load(open(out / 'rlgc.json'))
    kinds = {(s['layer'], s['kind']) for s in d['sections']}
    assert {('F.Cu', 'coupled'), ('In2.Cu', 'coupled')} <= kinds
    pair = next(s for s in d['sections']
                if s['layer'] == 'In2.Cu' and s['kind'] == 'coupled')
    assert pair['source'] == 'cut' and pair['length_mm'] > 9
    assert 70 < pair['at_fmax']['zdiff'] < 110
    rep = a.report(str(out))
    assert rep['sections'][0]['table']['rows']
    names = {x['name'] for x in a.figures(str(out))}
    assert any(n.startswith('R D_N') for n in names)
    assert a.curves(str(out))[0]['curves']


@pytest.mark.parametrize('w', [0.15, 0.3, 0.6])
def test_stripline_loss_against_wheelers_closed_form(w):
    """A centred stripline's conductor attenuation at 10 GHz against the
    closed form from Wheeler's rule (Pozar, Microwave Engineering, 3.198: b
    0.435 mm, t 35 um, er 4.3): within 2.5 %."""
    b, t, er, f = 0.435, 0.035, 4.3, 1e10
    rs = math.sqrt(math.pi * f * MU0 / SIG)
    r = Q.rlgc(X.stripline(w, 0.2, 0.2, er, t=t), [f], SIG)
    z0 = math.sqrt(r.L_inf[0, 0] / r.C[0, 0, 0])
    A = (1 + 2 * w / (b - t) + (1 / math.pi) * (b + t) / (b - t)
         * math.log((2 * b - t) / t))
    pozar = 2.7e-3 * rs * er * z0 / (30 * math.pi * (b - t) * 1e-3) * A
    assert r.R[0, 0, 0] / (2 * z0) == pytest.approx(pozar, rel=0.025)


def test_a_mirrored_section_is_one_solve():
    """A cross-section and its mirror image share a key, and their RLGC is
    the same (to the factorisation's round-off)."""
    xs = X.CrossSection(dielectrics=[X.Dielectric(0, 0.2, 4.3, 0.02)],
                        planes=[0.0])
    xs.conductor(-0.3, -0.15, 0.2, 0.235)
    xs.conductor(0.05, 0.2, 0.2, 0.235)
    xs.conductor(0.4, math.inf, 0.2, 0.235, ground=True)
    m = Q.mirrored(xs)
    assert Q.section_key(xs, 1) == Q.section_key(m, 1)
    a, b = Q.rlgc(xs, [0, 1e10], SIG), Q.rlgc(m, [0, 1e10], SIG)
    assert np.abs(a.R - b.R).max() <= 1e-8 * np.abs(a.R).max()
    assert np.abs(a.L - b.L).max() <= 1e-10 * np.abs(a.L).max()


def test_the_line_model_takes_each_cross_sections_loss():
    """--sigma on rf_stripline's line model: each section's conductor loss
    from its cross-section (about a third of 2 Rs / w's on a stripline), each
    distinct one solved once; above FIELD_LOSS_MAX the formulas', said."""
    pytest.importorskip('skrf')
    from kicad2ems.core.config import build_settings
    from kicad2ems.core.lines.builder import build
    from kicad2ems.core.pipeline import board_from_settings
    from kicad2ems.core.solvers.lines import fastsolve as F
    s = build_settings({}, config=os.path.join(EXAMPLES, 'rf_stripline.toml'))
    m = board_from_settings(s, log=lambda *a: None)
    lm = build(m)
    f = np.array([1e9, 1e10])
    db = lambda S: 20 * np.log10(np.abs(S[:, 1, 0]))
    lossless = db(F.solve(lm, m.ports, f, 'skrf')[0])
    info = {}
    field = db(F.solve(lm, m.ports, f, 'skrf', sigma=SIG, info=info)[0])
    assert 'eddy-current' in info['loss']
    keep = F.FIELD_LOSS_MAX
    try:
        F.FIELD_LOSS_MAX = 0
        info = {}
        formula = db(F.solve(lm, m.ports, f, 'skrf', sigma=SIG,
                             info=info)[0])
        assert 'FIELD_LOSS_MAX' in info['loss']
    finally:
        F.FIELD_LOSS_MAX = keep
    ratio = (lossless - field)[-1] / (lossless - formula)[-1]
    assert 0.2 < ratio < 0.5
