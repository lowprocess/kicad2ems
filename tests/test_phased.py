"""phased.py: a phased drive from each port's far field (the drive's parsing, the waves, the beam's direction)."""
import numpy as np
import pytest

from kicad2ems import phased


def two_sources(d=0.07, f=2.45e9, z0=50.0, coupling=0.0, element=lambda th: np.ones_like(th)):
    """Two sources d apart along y (isotropic, or element(theta)); Y: matched ports (1 / z0) plus coupling."""
    th, ph = np.arange(0, 91, 1.0), np.arange(0, 361, 5.0)
    k0 = 2 * np.pi * f / 299792458.0
    TH, PH = np.meshgrid(np.radians(th), np.radians(ph))
    E = np.array([element(TH) * np.exp(1j * k0 * y * np.sin(TH) * np.sin(PH)) for y in (-d / 2, d / 2)])
    Y = np.array([[1 / z0, coupling], [coupling, 1 / z0]], complex)
    return dict(theta=th, phi=ph, theta_sphere=np.arange(0, 181, 1.0), freq=np.array([f]), ports=['P1', 'P2'],
                zc=np.array([z0, z0]), Y=Y[None], Eth=E[None], Eph=np.zeros_like(E)[None],
                drive=np.array([1, 1], complex), f_pattern=f), k0


def test_parse_and_format_drive():
    a = phased.parse_drive(['P1', 'P2=0.5@90'], ['P1', 'P2', 'P3'])
    assert np.allclose(a, [1, 0.5j, 0])
    assert phased.format_drive(a, ['P1', 'P2', 'P3']) == ['P1@0', 'P2=0.5@90']
    with pytest.raises(ValueError, match='no port'):
        phased.parse_drive(['P9@0'], ['P1'])
    with pytest.raises(ValueError, match='expected'):
        phased.parse_drive(['P1@x'], ['P1'])


def test_steer_adds_a_step_per_driven_port():
    a = phased.steer([1, 0, 1, 1j], 30)                              # P2 not driven: P3 is the second
    assert np.allclose(np.degrees(np.angle(a[[0, 2, 3]])), [0, 30, 150]) and a[1] == 0


def test_waves_give_s_of_y():
    """b = S a with S = (1 - Zc Y)(1 + Zc Y)^-1 (equal references), the accepted power |a|^2 - |b|^2."""
    rng = np.random.default_rng(1)
    Y = (rng.normal(size=(2, 2)) + 1j * rng.normal(size=(2, 2))) / 100
    Y = Y + Y.T
    z = 50.0
    a = np.array([1.0, 0.3 - 0.4j])
    V, b = phased.waves(Y, [z, z], a)
    S = (np.eye(2) - z * Y) @ np.linalg.inv(np.eye(2) + z * Y)
    assert np.allclose(b, S @ a)
    Ip = Y @ V
    assert np.isclose(0.5 * np.real(V @ np.conj(Ip)), 0.5 * np.sum(np.abs(a) ** 2 - np.abs(b) ** 2))


def test_beam_follows_the_phase_step():
    data, k0 = two_sources()
    c = phased.combine(data, [1, 1])
    assert c['peak']['theta'] == 0                                   # in phase: broadside
    assert np.allclose(c['active'][0], 0) and abs(c['rows'][0]['eff'] - c['rows'][0]['Prad'] / c['rows'][0]['Pacc']) < 1e-12
    for step in (60, -45):
        c = phased.combine(data, phased.steer([1, 1], step))
        pk = c['peak']
        u = np.sin(np.radians(pk['theta'])) * np.sin(np.radians(pk['phi']))      # a cone: sin(theta) sin(phi)
        assert abs(u + np.radians(step) / (k0 * 0.07)) < 0.03         # P2 (at +y) ahead: the beam to -y
    assert c['sphere']['e_norm'].shape == (1, len(data['phi']), 181)
    assert np.all(c['sphere']['e_norm'][0][:, 91:] == 0)             # nothing below the ground


def test_coupling_shows_in_the_active_reflection():
    data, _ = two_sources(coupling=0.002)
    c0 = phased.combine(data, [1, 1])
    c1 = phased.combine(data, [1, -1])
    assert np.all(np.abs(c0["active"][0]) > 0.04)
    c = phased.combine(two_sources(element=np.cos)[0], phased.steer([1, 1], 60))
    assert c['peak']['theta'] < 16                                   # a patch-like element pulls it to broadside
    assert not np.allclose(c0['active'][0], c1['active'][0])          # the scan changes what each port sees


def test_save_load(tmp_path):
    data, _ = two_sources()
    p = tmp_path / 'a.npz'
    phased.save(p, **data)
    d = phased.load(p)
    assert d['ports'] == ['P1', 'P2'] and d['f_pattern'] == data['f_pattern']
    assert np.allclose(d['Eth'], data['Eth'], atol=1e-6)


def grid_sources(cols=4, rows=4, d=0.06, f=2.45e9, z0=50.0):
    """cols x rows isotropic sources d apart, P1..P(cols*rows) row by row: a column along +x, a row along +y."""
    th, ph = np.arange(0, 91, 1.0), np.arange(0, 361, 2.0)
    k0 = 2 * np.pi * f / 299792458.0
    TH, PH = np.meshgrid(np.radians(th), np.radians(ph))
    u, v = np.sin(TH) * np.cos(PH), np.sin(TH) * np.sin(PH)
    E = np.array([np.exp(1j * k0 * d * (c * u + r * v)) for r in range(rows) for c in range(cols)])
    n = cols * rows
    names = [f'P{i + 1}' for i in range(n)]
    data = dict(theta=th, phi=ph, theta_sphere=np.arange(0, 181, 1.0), freq=np.array([f]), ports=names,
                zc=np.full(n, z0), Y=(np.eye(n) / z0)[None].astype(complex), Eth=E[None],
                Eph=np.zeros_like(E)[None], drive=np.ones(n, complex), f_pattern=f)
    return data, k0, d


def test_parse_and_format_grid():
    names = [f'P{i + 1}' for i in range(16)]
    g = phased.parse_grid('4x4', names)
    assert g['P1'] == (0, 0) and g['P4'] == (3, 0) and g['P5'] == (0, 1) and g['P16'] == (3, 3)
    assert phased.format_grid(g, names) == '4x4'
    assert phased.parse_grid('2 x 3', names)['P6'] == (1, 2) and len(phased.parse_grid('2x3', names)) == 6
    assert phased.parse_grid('', names) is None and phased.parse_grid(None, names) is None
    e = phased.parse_grid('P1:0,0; P2:1,0  P3:0.5,1', names)                # an offset (triangular) lattice
    assert e == {'P1': (0, 0), 'P2': (1, 0), 'P3': (0.5, 1)}
    assert phased.parse_grid(phased.format_grid(e, names), names) == e
    assert phased.format_grid(phased.parse_grid('P2:0,0 P1:1,0', names), names) == 'P2:0,0 P1:1,0'
    with pytest.raises(ValueError, match='no port'):
        phased.parse_grid('P99:0,0', names)
    with pytest.raises(ValueError, match='expected'):
        phased.parse_grid('P1:0', names)


def test_steer2d_steps_per_column_and_row():
    names = ['P1', 'P2', 'P3', 'P4', 'P5']
    g = phased.parse_grid('2x2', names)                              # P5 off the grid: kept as it is
    a = phased.steer2d([1, 1, 1, 0, 1j], names, g, 30, -45)         # P4 not driven
    assert np.allclose(np.degrees(np.angle(a[:3])), [0, 30, -45]) and a[3] == 0 and np.isclose(a[4], 1j)
    assert np.allclose(phased.steer2d([1, 1, 1], names[:3], None, 20, 99), phased.steer([1, 1, 1], 20))
    a, g = phased.settings_drive(['P1', 'P2', 'P3', 'P4'], names, '2x2', [10, 20])
    assert np.allclose(np.degrees(np.angle(a[:4])), [0, 10, 20, 30]) and g['P4'] == (1, 1)
    with pytest.raises(ValueError, match='needs --grid'):
        phased.settings_drive(['P1', 'P2'], names, None, [10, 20])


def test_beam_follows_both_steps_of_a_4x4_array():
    data, k0, d = grid_sources()
    g = phased.parse_grid('4x4', data['ports'])
    assert phased.combine(data, data['drive'])['peak']['theta'] == 0
    for sx, sy in ((60, 0), (0, -50), (40, 40), (-70, 30)):
        pk = phased.combine(data, phased.steer2d(data['drive'], data['ports'], g, sx, sy))['peak']
        th, ph = np.radians(pk['theta']), np.radians(pk['phi'])
        u, v = np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph)
        assert abs(u + np.radians(sx) / (k0 * d)) < 0.04, (sx, sy, pk)   # later columns ahead: the beam to -x
        assert abs(v + np.radians(sy) / (k0 * d)) < 0.04, (sx, sy, pk)


def line_sidelobe_db(w, d=0.5):
    """The highest sidelobe (dB below the peak) of a line of isotropic elements d wavelengths apart, weights w."""
    u = np.linspace(-1, 1, 20001)
    af = np.abs(np.exp(2j * np.pi * d * np.outer(u, np.arange(len(w)))) @ np.asarray(w))
    af = 20 * np.log10(af / af.max() + 1e-12)
    i0 = int(np.argmax(af))
    j = i0
    while j + 1 < len(af) and af[j + 1] < af[j]:                     # down the main lobe to its first null
        j += 1
    return float(af[j:].max())


def test_parse_taper_and_weights():
    assert phased.parse_taper('') == ('uniform', phased.TAPER_SLL)
    assert phased.parse_taper('Taylor:-30') == ('taylor', 30.0) and phased.parse_taper('cosine')[0] == 'cosine'
    for bad, msg in (('hann', 'one of'), ('taylor:x', 'number'), ('chebyshev:10', '13.3')):
        with pytest.raises(ValueError, match=msg):
            phased.parse_taper(bad)
    assert np.allclose(phased.taper_1d('uniform', 4), 1) and np.allclose(phased.taper_1d('taylor', 2), 1)
    for kind, n in (('taylor', 4), ('chebyshev', 6), ('cosine', 5)):
        w = phased.taper_1d(kind, n, 30)
        assert w.max() == pytest.approx(1) and np.allclose(w, w[::-1]) and w[0] < w[n // 2]
    assert line_sidelobe_db(np.ones(4)) == pytest.approx(-11.3, abs=0.2)
    assert line_sidelobe_db(phased.taper_1d('chebyshev', 6, 25)) == pytest.approx(-25, abs=0.3)
    assert line_sidelobe_db(phased.taper_1d('taylor', 8, 30)) < -27


def test_taper_on_a_grid_is_separable():
    names = [f'P{i + 1}' for i in range(17)]
    g = phased.parse_grid('4x4', names)                              # P17 off the grid
    a = phased.taper(np.ones(17, complex), names, g, 'taylor:25')
    w = phased.taper_1d('taylor', 4, 25)
    assert np.allclose(np.abs(a[:16]).reshape(4, 4), np.outer(w, w)) and a[16] == 1
    b = phased.taper([1, 0, 1j, 1], names[:4], None, 'cosine')       # no grid: across the driven ports
    assert b[1] == 0 and np.allclose(np.abs(b[[0, 2, 3]]), phased.taper_1d('cosine', 3))
    assert np.isclose(np.angle(b[2]), np.pi / 2)                     # phases kept
    a, _ = phased.settings_drive([f'P{i + 1}' for i in range(16)], names, '4x4', [30, 0], 'taylor:25')
    assert np.allclose(np.abs(a[:16]).reshape(4, 4), np.outer(w, w)) and np.isclose(np.angle(a[1] / a[0]),
                                                                                     np.radians(30))


def test_taper_lowers_the_4x4_sidelobes():
    data, k0, d = grid_sources()
    names = data['ports']
    g = phased.parse_grid('4x4', names)

    def sidelobe(a):                                                  # the phi = 0 cut, past the first null
        e = phased.combine(data, a)['sphere']['e_norm'][0]
        cut = 20 * np.log10(e[0, :91] / e.max() + 1e-12)
        j = 0
        while j + 1 < len(cut) and cut[j + 1] < cut[j]:              # down the main lobe to its first null
            j += 1
        return float(cut[j:].max())

    uni = sidelobe(data['drive'])
    tay = sidelobe(phased.taper(data['drive'], names, g, 'taylor:25'))
    assert uni == pytest.approx(-11.3, abs=1.0) and tay < -21
