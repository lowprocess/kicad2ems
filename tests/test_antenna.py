"""Antenna mode: defaults, match / gain post-processing, gap ports (centre-fed dipole) and an end-to-end
--mode antenna --preview of the example boards (no openEMS needed)."""
import numpy as np
import pytest
from kicad2openems.kicad import KiCadBoard
from kicad2openems.model import Model
from kicad2openems.mesh import auto_mesh
from kicad2openems.antenna import antenna_air, record_freqs, match_summary, gain_table, pattern_gain



def test_defaults_and_record_freqs():
    assert antenna_air(2e9, 3e9) == pytest.approx(299.792458 / 2.5 / 4)
    assert antenna_air(2e9, 3e9, air=12.0) == 12.0
    assert antenna_air(20e9, 30e9) == 5.0                            # at least 5 mm
    f = record_freqs(2e9, 3e9, [2.43e9])
    assert len(f) == 22 and 2.43e9 in f and f[0] == 2e9 and f[-1] == 3e9


def test_match_summary():
    f = np.linspace(2e9, 3e9, 101)
    s11 = 0.9 - 0.85 * np.exp(-((f - 2.4e9) / 0.05e9) ** 2) + 0j          # dip to 0.05 (-26 dB) at 2.4 GHz
    mt = match_summary(f, s11, 50.0)
    assert mt['f_res'] == pytest.approx(2.4e9) and mt['s11_db'] == pytest.approx(20 * np.log10(0.05))
    assert mt['bw10_lo'] < 2.4e9 < mt['bw10_hi'] and mt['zin'][0] == pytest.approx(50 * 1.05 / 0.95)
    assert match_summary(f, np.full(101, 0.5 + 0j), 50.0)['bw10'] is None


def test_gain_table_and_pattern():
    rows = gain_table([2.4e9], [2.0], [0.8e-3], [1.0e-3], [1.25e-3])
    r = rows[0]
    assert r['eff'] == pytest.approx(0.8) and r['D_dbi'] == pytest.approx(10 * np.log10(2))
    assert r['G_dbi'] == pytest.approx(10 * np.log10(1.6)) and r['Grealized_dbi'] == pytest.approx(10 * np.log10(1.28))
    g = pattern_gain([[1.0, 0.5], [0.0, 2.0]], 4.0, 0.5)
    assert g.max() == pytest.approx(2.0) and g[0, 0] == pytest.approx(0.5)


def test_dipole_gap_port(boards):
    m = Model(KiCadBoard(boards['dipole'] + '.kicad_pcb'), log=lambda *a: None)
    p = m.ports[0]
    assert p.kind == 'gap' and p.direction == 'x' and p.z0 == 73
    assert p.gap == pytest.approx((34.5, 35.5)) and p.w == pytest.approx(2.0) and p.c[1] == pytest.approx(-10.0)
    s, e = p.box()
    assert s[2] == e[2] == m.cu_z['F.Cu'] and (s[0], e[0]) == pytest.approx((34.5, 35.5))
    mesh = auto_mesh(m, 1.5e9, 3.5e9, bc=('PML_8',) * 6, air=30, log=lambda *a: None)
    for v in p.gap:                                                    # both gap edges are mesh lines
        assert np.min(np.abs(mesh['x'] - v)) < 1e-9
    assert mesh['info']['lines_across_ports']['P1'] >= 2


def test_gap_port_direction_in_fdtd(boards, fake_openems, monkeypatch):
    from kicad2openems import simulation
    m = Model(KiCadBoard(boards['dipole'] + '.kicad_pcb'), log=lambda *a: None)
    mesh = auto_mesh(m, 1.5e9, 3.5e9, bc=('PML_8',) * 6, air=30, log=lambda *a: None)
    seen = []
    import openEMS
    orig = openEMS.openEMS.AddLumpedPort
    monkeypatch.setattr(openEMS.openEMS, 'AddLumpedPort',
                        lambda self, nr, R, s, e, d, **kw: seen.append(d) or orig(self, nr, R, s, e, d, **kw))
    simulation.build_fdtd(m, mesh, 1.5e9, 3.5e9, 0, bc=('PML_8',) * 6)
    assert seen == ['x']


@pytest.mark.parametrize('kind', ['patch', 'ifa', 'dipole', 'slot'])
def test_antenna_preview(boards, kind, tmp_path, run_cli):
    code, out, err = run_cli(boards[kind] + '.kicad_pcb', '--mode', 'antenna', '--fmin', '2', '--fmax', '3',
                             '--out', tmp_path, '--preview', '--no-report')
    assert code == 0, err
    assert 'antenna mode: boundaries PML_8' in out and 'zmin=PML_8' in out
    assert ('gap port' in out) == (kind == 'dipole')
