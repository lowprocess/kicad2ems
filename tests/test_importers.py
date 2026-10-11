"""Importer parity: a board through the KiCad reader and through its own fabrication data (Gerber X2 with
nets, Excellon, job file, pick-and-place) resolves to the same Board - the same stack, copper per layer and
net, vias, ports - so every solver sees the same thing whichever file it came from."""
import pytest
from kicad2ems.board import Board
from kicad2ems.importers import open_board
from kicad2ems.importers.gerber import parse_gerber
from gerber_export import export, write_gerber

quiet = lambda *a: None


@pytest.fixture(scope='module')
def demo_pair(boards, tmp_path_factory):
    """The transition demo (four layers, blind via, inner-layer port) as KiCad and as Gerber."""
    base = boards['demo']
    job = export(open_board(base + '.kicad_pcb'), str(tmp_path_factory.mktemp('fab')), 'demo')
    return base, job


def _close_summary(a, b, area_tol=0.05):
    assert a['stack'] == b['stack'] and a['copper_z'] == b['copper_z']
    assert a['outline'] == b['outline']
    for lay in set(a['copper']) | set(b['copper']):
        ca, cb = a['copper'].get(lay, {}), b['copper'].get(lay, {})
        assert set(ca) == set(cb), lay
        for net in ca:                                              # arcs and round apertures: a few um^2
            assert abs(ca[net] - cb[net]) < area_tol, (lay, net, ca[net], cb[net])
    assert a['vias'] == b['vias']
    for p, q in zip(a['ports'], b['ports']):
        assert {k: v for k, v in p.items() if k not in ('at', 'w')} == {k: v for k, v in q.items() if k not in ('at', 'w')}
        assert max(abs(u - v) for u, v in zip(p['at'], q['at'])) < 1e-3 and abs(p['w'] - q['w']) < 1e-3
    assert len(a['ports']) == len(b['ports'])


def test_kicad_and_gerber_give_the_same_board(demo_pair):
    base, job = demo_pair
    kw = dict(nets=['RF', 'GND'], ground='B.Cu', log=quiet)
    k = Board(open_board(base + '.kicad_pcb'), pos_file=base + '-top.pos', **kw)
    # a Gerber has no footprint fields: the inner-layer port P2 is a setting (position and rotation from the
    # pick-and-place file, layer as on the KiCad footprint)
    p2 = next(p for p in k.ports if p.name == 'P2')
    fp = next(f for f in open_board(base + '.kicad_pcb').footprints if 'P2' in f['fab'])
    g = Board(open_board(job, pos=base + '-top.pos'), pos_file=base + '-top.pos',
              ports=[f"P2@{fp['x']},{fp['y']},{p2.layer},{fp['rot']}"], **kw)
    _close_summary(k.summary(), g.summary())
    assert g.source['importer'] == 'gerber' and k.source['importer'] == 'kicad'


def test_gerber_nets_tracks_and_outline(demo_pair):
    _, job = demo_pair
    raw = open_board(job)
    assert {n for _, n, _ in raw.items} == {'RF', 'GND'}
    assert {t['layer'] for t in raw.tracks} == {'F.Cu', 'In2.Cu'} and all(t['net'] == 'RF' for t in raw.tracks)
    assert raw.edges and all(v['net'] for v in raw.vias)


def test_macros_and_clear_polarity(tmp_path):
    """Aperture macros (KiCad's RoundRect) and clear polarity (LPC) take copper away from every net."""
    p = tmp_path / 'a.gbr'
    rr = ('%AMRoundRect*0 Rectangle with rounded corners*4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*'
          '1,1,$1+$1,$2,$3*1,1,$1+$1,$4,$5*1,1,$1+$1,$6,$7*1,1,$1+$1,$8,$9*'
          '20,1,$1+$1,$2,$3,$4,$5,0*20,1,$1+$1,$4,$5,$6,$7,0*20,1,$1+$1,$6,$7,$8,$9,0*20,1,$1+$1,$8,$9,$2,$3,0*%')
    text = "\n".join(['%FSLAX46Y46*%', '%MOMM*%', rr, '%ADD10RoundRect,0.1X-0.4X-0.4X0.4X-0.4X0.4X0.4X-0.4X0.4*%',
                      '%TO.N,A*%', 'D10*', 'X0Y0D03*', '%TO.N,B*%', 'G36*', 'X2000000Y0D02*', 'G01X4000000Y0D01*',
                      'X4000000Y2000000D01*', 'X2000000Y2000000D01*', 'X2000000Y0D01*', 'G37*', '%LPC*%', 'G36*',
                      'X2500000Y500000D02*', 'G01X3500000Y500000D01*', 'X3500000Y1500000D01*', 'X2500000Y1500000D01*',
                      'X2500000Y500000D01*', 'G37*', 'M02*'])
    p.write_text(text)
    copper, draws, _ = parse_gerber(str(p))
    assert set(copper) == {'A', 'B'} and not draws
    assert copper['A'].area == pytest.approx(1.0 - (4 - 3.14159) * 0.1 ** 2, rel=2e-2)      # 1 mm square, r 0.1
    assert copper['B'].area == pytest.approx(4.0 - 1.0, rel=1e-6)


def test_writer_round_trip(tmp_path):
    from shapely.geometry import box
    p = tmp_path / 'w.gbr'
    write_gerber(str(p), 'Copper,L1,Top', tracks=[('N1', 0.2, [(0, 0), (1, 0)])], flashes=[('N2', 0.5, 2, 2)],
                 regions=[('N3', box(3, 3, 5, 5).difference(box(3.5, 3.5, 4, 4)))])
    copper, draws, attrs = parse_gerber(str(p))
    assert attrs['FileFunction'][0] == 'Copper' and draws == [([(0.0, 0.0), (1.0, 0.0)], 0.2, 'N1')]
    assert copper['N3'].area == pytest.approx(4 - 0.25)
    assert copper['N2'].area == pytest.approx(3.14159 * 0.25 ** 2, rel=2e-2)
