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
