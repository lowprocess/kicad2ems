"""Antenna mode: defaults, match / gain post-processing, gap ports (centre-fed dipole) and an end-to-end
--mode antenna --preview of the example boards (no openEMS needed)."""
import os
import numpy as np
import pytest
from kicad2ems.importers.kicad import KiCadBoard
from kicad2ems.board import Board
from kicad2ems.fdtd.mesh import auto_mesh
from kicad2ems.fdtd.antenna import antenna_air, record_freqs
from kicad2ems.fields.antenna import match_summary, gain_table, pattern_gain



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
    m = Board(KiCadBoard(boards['dipole'] + '.kicad_pcb'), log=lambda *a: None)
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
    from kicad2ems.fdtd import model
    m = Board(KiCadBoard(boards['dipole'] + '.kicad_pcb'), log=lambda *a: None)
    mesh = auto_mesh(m, 1.5e9, 3.5e9, bc=('PML_8',) * 6, air=30, log=lambda *a: None)
    seen = []
    import openEMS
    orig = openEMS.openEMS.AddLumpedPort
    monkeypatch.setattr(openEMS.openEMS, 'AddLumpedPort',
                        lambda self, nr, R, s, e, d, **kw: seen.append(d) or orig(self, nr, R, s, e, d, **kw))
    model.build_fdtd(m, mesh, 1.5e9, 3.5e9, 0, bc=('PML_8',) * 6)
    assert seen == ['x']


@pytest.mark.parametrize('kind', ['patch', 'ifa', 'dipole', 'slot', 'vivaldi', 'vivaldi_antipodal'])
def test_antenna_preview(boards, kind, tmp_path, run_cli):
    code, out, err = run_cli(boards[kind] + '.kicad_pcb', '--mode', 'antenna', '--fmin', '2', '--fmax', '3',
                             '--out', tmp_path, '--preview', '--no-report')
    assert code == 0, err
    assert 'antenna mode: boundaries PML_8' in out and 'zmin=PML_8' in out
    assert ('gap port' in out) == (kind == 'dipole')


def test_vivaldi_antipodal_options(tmp_path, run_cli):
    """Edge slots and a director past the aperture: the board grows by dext, the outline and the model build."""
    import subprocess, sys
    from conftest import ANT
    base = tmp_path / 'v'
    subprocess.check_call([sys.executable, ANT, 'vivaldi_antipodal', str(base), 'cd=5', 'dext=15', 'dl=24', 'fr=5'],
                          stdout=subprocess.DEVNULL)
    code, out, err = run_cli(str(base) + '.kicad_pcb', '--mode', 'antenna', '--fmin', '3', '--fmax', '10', '--out',
                             tmp_path, '--preview', '--no-report')
    assert code == 0 and 'outline 50.00 x 85.00 mm' in out, err
    bad = subprocess.run([sys.executable, ANT, 'vivaldi_antipodal', str(base), 'dext=5', 'dl=24'],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and 'runs off the board' in bad.stderr


@pytest.mark.parametrize('kind, args, outline, vias', [
    ('vivaldi_antipodal', ['sym=1', 'fr=15', 'vias=1', 'ap=34', 'nd=7'], '50.00 x 70.00', 22),
    ('vivaldi_antipodal_array', ['N=2', 'P=40', 'ap=34'], '80.00 x ', 0),
    ('vivaldi_antipodal_array', ['N=2', 'P=40', 'ap=34', 'er=3.55', 'tand=0.0027', 'h=0.813', 'ns=3'], '80.00 x ',
     0)])

def test_vivaldi_antipodal_variants(tmp_path, run_cli, kind, args, outline, vias):
    """The symmetric curved form with corner via walls and striped directors; two elements from one port."""
    import subprocess, sys
    from conftest import ANT
    base = tmp_path / 'v'
    subprocess.check_call([sys.executable, ANT, kind, str(base)] + args, stdout=subprocess.DEVNULL)
    code, out, err = run_cli(str(base) + '.kicad_pcb', '--mode', 'antenna', '--fmin', '3', '--fmax', '10', '--out',
                             tmp_path, '--preview', '--no-report')
    assert code == 0 and f'outline {outline}' in out and f'vias {vias} ' in out, out + err


def test_chebyshev_transformer():
    """The array feed's 50 -> 100 ohm transformers: the sections' impedances and their ripple over 3-10 GHz."""
    import sys
    from conftest import ANT
    sys.path.insert(0, os.path.dirname(ANT))
    import make_antennas as ma
    assert ma._chebyshev(50, 100, 1, 3, 10) == [70.7]
    assert [round(z, 1) for z in ma._chebyshev(50, 100, 3, 3, 10)] == [58.1, 70.7, 86.1]
    f = np.linspace(3, 10, 141)
    for n, worst in ((2, 0.14), (3, 0.066)):
        zs, G = ma._chebyshev(50, 100, n, 3, 10), []
        for fi in f:
            t, z_in = np.tan(np.pi / 2 * fi / 6.5), 100.0
            for z in reversed(zs):
                z_in = z * (z_in + 1j * z * t) / (z + 1j * z_in * t)
            G.append(abs((z_in - 50) / (z_in + 50)))
        assert max(G) < worst
