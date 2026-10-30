"""The 2D field solver (xsection.py) against exact and closed-form results: Cohn's stripline, single and
coupled; Hammerstad & Jensen's microstrip; Wheeler's thick stripline; grounded CPW; and what the formulas
leave out (solder mask, coplanar ground beside a stripline) in the direction it should go."""
import math
import numpy as np
import pytest
from kicad2ems.lines import xsection as X
from kicad2ems.lines.formulas import microstrip, stripline, cpwg, ellipk
from kicad2ems.lines.coupled import coupled_stripline, coupled_microstrip, mtl_s


def _cohn(w, b, er):
    k = 1 / math.cosh(math.pi * w / (2 * b))
    return 30 * math.pi / math.sqrt(er) * ellipk(k) / ellipk(math.sqrt(1 - k * k))


def test_stripline_against_cohn():
    z, ee = X.stripline(0.15, 0.2, 0.2, 4.3).solve().single()
    assert z == pytest.approx(_cohn(0.15, 0.4, 4.3), rel=3e-3)
    assert ee == pytest.approx(4.3)                                           # one dielectric: eeff = er


def test_coupled_stripline_against_cohn():
    got = X.coupled_stripline(0.1, 0.1, 0.2, 0.2, 4.3).solve().pair()
    ref = coupled_stripline(0.1, 0.1, 0.4, 4.3)
    assert got[0] == pytest.approx(ref[0], rel=4e-3) and got[1] == pytest.approx(ref[1], rel=4e-3)


@pytest.mark.parametrize('w,h,er,t', [(0.35, 0.2, 4.3, 0.0), (3.0, 1.6, 4.4, 0.0), (0.4, 0.2104, 4.4, 0.035)])
def test_microstrip_against_hammerstad_jensen(w, h, er, t):
    z, ee = X.microstrip(w, h, er, t).solve().single()
    z0, ee0 = microstrip(w, h, er, t)
    assert z == pytest.approx(z0, rel=4e-3) and ee == pytest.approx(ee0, rel=3e-3)


def test_thick_stripline_against_wheeler():
    z, _ = X.stripline(0.15, 0.2, 0.2, 4.3, t=0.035).solve().single()
    assert z == pytest.approx(stripline(0.15, 0.435, 4.3, 0.035)[0], rel=4e-3)


def test_grounded_cpw_against_conformal_mapping():
    z, ee = X.microstrip(0.5, 1.6, 4.4, s=0.5).solve().single()
    z0, ee0 = cpwg(0.5, 0.5, 1.6, 4.4)
    assert z == pytest.approx(z0, rel=5e-3) and ee == pytest.approx(ee0, rel=3e-3)


def test_coupled_microstrip_and_the_matrices():
    r = X.coupled_microstrip(0.15, 0.15, 0.2, 4.4).solve()
    ze, zo, ee, eo = r.pair()
    ref = coupled_microstrip(0.15, 0.15, 0.2, 4.4)
    assert ze == pytest.approx(ref[0], rel=1e-2) and zo == pytest.approx(ref[1], rel=1e-2)
    assert eo < ee
    assert np.allclose(r.C, r.C.T) and r.C[0, 1] < 0 < r.C[0, 0]             # Maxwell form
    assert r.C[0, 0] == pytest.approx(r.C[1, 1], rel=1e-6)                   # the pair is symmetric
    S = mtl_s(r.C, r.Ca, 4.4, 0.0, 10.0, [1e9])                              # what the line model's MTL takes
    assert S.shape == (1, 4, 4) and np.allclose(S[0] @ S[0].conj().T, np.eye(4), atol=1e-9)   # lossless


def test_what_the_formulas_leave_out():
    bare, _ = X.microstrip(0.35, 0.2, 4.3, 0.035).solve().single()
    masked, _ = X.microstrip(0.35, 0.2, 4.3, 0.035, mask=0.02, er_mask=3.8).solve().single()
    assert 0.94 * bare < masked < 0.99 * bare                                # mask lowers Z0 a few percent
    plain, _ = X.stripline(0.15, 0.2, 0.2, 4.3).solve().single()
    side, _ = X.stripline(0.15, 0.2, 0.2, 4.3, s=0.15).solve().single()
    assert side < plain                                                      # coplanar ground the formulas drop


def test_dielectric_loss_by_filling_factor():
    r = X.microstrip(0.35, 0.2, 4.3, tand=0.02).solve()
    _, ee = r.single()
    q = 4.3 * (ee - 1) / (ee * (4.3 - 1))                                    # the filling-factor result
    assert r.tand_eff() == pytest.approx(0.02 * q, rel=2e-2)


def test_conductors_must_not_touch():
    xs = X.CrossSection(dielectrics=[X.Dielectric(0, 0.2, 4.3)], planes=[0.0])
    xs.conductor(0, 0.2, 0.2).conductor(0.2, 0.4, 0.2)
    with pytest.raises(ValueError):
        xs.solve()
