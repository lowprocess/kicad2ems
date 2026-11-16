"""Parts as internal ports, closed after the solve (embed.py, R-E5): the closing against the same network in Z, a
part's impedance, a vendor file's, the sweeps, and the Board's part ports."""
import numpy as np
import pytest

from kicad2ems.core import embed


def _s_of_z(Z, z0):
    n = Z.shape[-1]
    zr = np.diag(np.full(n, z0))
    return np.array([(z - zr) @ np.linalg.inv(z + zr) for z in Z])


def test_closing_a_port_is_the_networks_own_reduction():
    """A reciprocal 3-port by its Z matrix; its third port closed by Z_L: in Z, Z_ee - Z_ep (Z_pp + Z_L)^-1 Z_pe;
    close() in S gives the same."""
    rng = np.random.default_rng(3)
    nf, z0 = 7, 50.0
    A = rng.normal(size=(nf, 3, 3)) + 1j * rng.normal(size=(nf, 3, 3))
    Z = 20 * (A + np.swapaxes(A, 1, 2)) + np.eye(3) * 60
    ZL = 10 + 1j * np.linspace(-30, 30, nf)
    S = _s_of_z(Z, z0)
    got, names, zref = embed.close(S, ['P1', 'P2', 'C1'], [z0] * 3, {'C1': ZL})
    Ze = np.array([Z[k][:2, :2] - np.outer(Z[k][:2, 2], Z[k][2, :2]) / (Z[k][2, 2] + ZL[k]) for k in range(nf)])
    assert names == ['P1', 'P2'] and zref == [z0, z0]
    assert np.allclose(got, _s_of_z(Ze, z0), atol=1e-12)


def test_an_unexcited_column_stays_unknown():
    S = np.zeros((2, 3, 3), complex)
    S[:, :, 0] = 0.1
    S[:, :, 2] = 0.2
    S[:, :, 1] = np.nan                                              # P2 not excited
    got, _, _ = embed.close(S, ['P1', 'P2', 'C1'], [50.0] * 3, {'C1': np.full(2, 10.0 + 0j)})
    assert np.isfinite(got[:, :, 0]).all() and np.isnan(got[:, :, 1]).all()


def test_a_parts_impedance_and_a_vendor_files(tmp_path):
    f = np.array([1e8, 1e9])
    z = embed.impedance(R=0.1, L=1e-9, C=1e-12, f=f)
    assert z == pytest.approx(0.1 + 1j * (2 * np.pi * f * 1e-9 - 1 / (2 * np.pi * f * 1e-12)))
    from kicad2ems.core.touchstone import write_touchstone
    Zs = 2.0 + 5j                                                    # a series 2 + j5 ohm, measured in 50 ohm
    s21 = 2 * 50 / (2 * 50 + Zs)
    S = np.zeros((2, 2, 2), complex)
    S[:, 0, 0] = S[:, 1, 1] = Zs / (2 * 50 + Zs)
    S[:, 1, 0] = S[:, 0, 1] = s21
    write_touchstone(str(tmp_path / 'c.s2p'), f, S, [50.0, 50.0])
    assert embed.file_impedance(str(tmp_path / 'c.s2p'), f) == pytest.approx(np.full(2, Zs))
    with pytest.raises(ValueError, match='covers'):
        embed.file_impedance(str(tmp_path / 'c.s2p'), np.array([1e7, 1e9]))


def test_a_sweep_is_every_combination_of_a_parts_values():
    from types import SimpleNamespace
    parts = dict(C1=SimpleNamespace(R=None, L=None, C=1e-12), L2=SimpleNamespace(R=None, L=2e-9, C=None))
    combos = embed.sweep(dict(C1='1p,2.2p', L2='1n,3n'), parts)
    assert len(combos) == 4 and embed.tag(combos[1]) == 'C1=1p_L2=3n'
    loads = embed.loads_of(parts, np.array([1e9]), combo=combos[1])
    assert loads['C1'] == pytest.approx(embed.impedance(C=1e-12, f=[1e9]))
    assert loads['L2'] == pytest.approx(embed.impedance(L=3e-9, f=[1e9]))
    with pytest.raises(ValueError, match='not a part port'):
        embed.sweep(dict(R9='1k'), parts)
    with pytest.raises(ValueError, match='not a value'):
        embed.sweep(dict(C1='big'), parts)
    with pytest.raises(ValueError, match='at most'):
        embed.sweep({k: ','.join(f"{i}p" for i in range(1, 10)) for k in ('C1', 'L2')}, parts)     # 81 > 64


def test_a_part_becomes_a_gap_port_across_its_gap(boards):
    from kicad2ems.core.board import Board
    from kicad2ems.core.importers.kicad import KiCadBoard
    rlc = boards['rlc']
    plain = Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos', log=lambda *a: None)
    m = Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos', log=lambda *a: None,
              part_ports=['all'])
    c1 = next(p for p in plain.parts if p.ref == 'C1')
    port = next(p for p in m.ports if p.name == 'C1')
    assert not m.parts and list(m.part_of) == ['C1'] and port.internal and port.kind == 'gap'
    assert port.gap == pytest.approx((c1.s[c1.axis], c1.e[c1.axis])) and port.layer == c1.layer
    with pytest.raises(ValueError, match='not a two-pad'):
        Board(KiCadBoard(rlc + '.kicad_pcb'), nets=['RF1', 'RF2', 'GND'], pos_file=rlc + '-top.pos',
              log=lambda *a: None, part_ports=['C7'])


@pytest.mark.parametrize('backend', ['skrf', 'qucsator'])
def test_the_line_model_s_part_ports_close_to_its_parts(boards, backend):
    """R-E5: the line model's part ports, a floating port in each part's place (a series tee on scikit-rf, a Pac
    across the gap on qucsator), closed by the parts' values: the board with its parts, to rounding."""
    from kicad2ems.core.board import Board
    from kicad2ems.core.importers.kicad import KiCadBoard
    from kicad2ems.core.lines.tlines import build
    from kicad2ems.core.solvers.lines.fastsolve import solve, find_qucsator
    if backend == 'skrf':
        pytest.importorskip('skrf')
    else:
        try:
            find_qucsator()
        except ValueError:
            pytest.skip('qucsator not installed')
    base = boards['lowpass']
    plain = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None)
    m = Board(KiCadBoard(base + '.kicad_pcb'), pos_file=base + '-top.pos', log=lambda *a: None, part_ports=['all'])
    f = np.linspace(0.1e9, 4e9, 40)
    S0, _ = solve(build(plain), plain.ports, f, backend)
    lm = build(m)
    assert set(m.part_of) and all(isinstance(lm.ports[r], tuple) for r in m.part_of)
    S, _ = solve(lm, m.ports, f, backend)
    names = [p.name for p in m.ports]
    loads = embed.loads_of(m.part_of, f)
    Sc, kept, _ = embed.close(S, names, [p.z0 for p in m.ports], loads)
    assert list(kept) == [p.name for p in plain.ports] and np.nanmax(np.abs(Sc - S0)) < 1e-9
