"""Elmer DC IR drop: a strip against its closed form; pads and terminals from
board.json."""
import pytest
from shapely.geometry import box, mapping

from kicad2ems.fem.elmer import run as elmer
from kicad2ems.fem.geometry import pads_of


def strip_board(t=0.035):
    """A 50 x 5 mm strip on F.Cu, 5 x 5 mm pads of parts S and L at its ends."""
    pad = lambda ref, x: dict(
        ref=ref, pads=[
            dict(name='1', net='N', layers=['F.Cu'], geom=mapping(
                box(x, 0, x + 5, 5)))
        ])
    return dict(
        stack=dict(
            copper=[
                dict(name='B.Cu', z=0.0, t=t),
                dict(name='F.Cu', z=1.6, t=t)
            ], layers=[dict(name='core', z0=0.0, z1=1.6, er=4.5, tand=0.02)]),
        copper={'F.Cu': [dict(net='N', polygon=mapping(box(0, 0, 50, 5)))]},
        vias=[], prims=dict(footprints=[pad('S', 0), pad('L', 45)]))


def test_pads_of_takes_a_parts_pads_on_the_net():
    b = strip_board()
    (lay, poly), = pads_of(b, 'L', {'N'})
    assert lay == 'F.Cu' and poly.bounds == (45, 0, 50, 5)
    with pytest.raises(ValueError):
        pads_of(b, 'L', {'other'})
    with pytest.raises(ValueError):
        pads_of(b, 'X', {'N'})


@pytest.mark.skipif(not elmer.found()[0], reason='Elmer not installed')
def test_strip_drop_matches_closed_form(tmp_path):
    """R = 40 mm / (sigma t w) between the pads' inner edges, on the layered
    mesh (R-D23)."""
    from kicad2ems.fem.elmer.dc import solve
    R0 = 0.040 / (5.8e7 * 35e-6 * 0.005)
    r = solve(strip_board(), {'N'}, 'S', {'L': 1.0}, out=tmp_path, h=0.25,
              log=lambda *a: None)
    assert -r['V']['L'] == pytest.approx(R0, rel=1e-3) and r['mesh'] == 'wedge'
    assert r['loss_W'] == pytest.approx(-r['V']['L'] * 1.0, rel=1e-6)
    assert (tmp_path / 'dc.json').is_file() and 'wedges' in r
    # which Elmer it was (R-B14)
    assert r['engine']['program'] == 'Elmer' and r['engine']['version']


def via_board(drills=(0.2, 0.6), t=0.035):
    """Net N: a 10 x 4 mm square on F.Cu and on B.Cu joined by two vias
    (drills mm, 5 mm apart) through 1.6 mm; part S's pad the top square, L's
    the bottom one."""
    sq = box(0, 0, 10, 4)
    fp = lambda ref, lay: dict(ref=ref, pads=[dict(
        name='1', net='N', layers=[lay], geom=mapping(sq))])
    return dict(
        stack=dict(
            copper=[dict(name='B.Cu', z=0.0, t=t),
                    dict(name='F.Cu', z=1.6, t=t)],
            layers=[dict(name='core', z0=0.0, z1=1.6, er=4.5, tand=0.02)]),
        copper={lay: [dict(net='N', polygon=mapping(sq))]
                for lay in ('F.Cu', 'B.Cu')},
        vias=[dict(x=2.5 + 5 * k, y=2.0, r=d / 2, z0=0.0, z1=1.6, net='N')
              for k, d in enumerate(drills)],
        outline=mapping(box(-1, -1, 11, 5)),
        prims=dict(footprints=[fp('S', 'F.Cu'), fp('L', 'B.Cu')]))


@pytest.mark.skipif(not elmer.found()[0], reason='Elmer not installed')
def test_each_via_barrel_is_its_own_plating(tmp_path):
    """A 0.2 and a 0.6 mm drill with 25 um plating side by side on one net:
    the two barrels' resistance in parallel, h / (sigma pi (r^2 - (r - p)^2))
    each, within 10 % (measured 1.04). One copper fraction for every barrel,
    from their mean, had made the pair 35 % low (0.65)."""
    import math
    from kicad2ems.fem.elmer.dc import solve, PLATING
    g = sum(5.8e7 * math.pi * ((d / 2) ** 2 - (d / 2 - PLATING) ** 2) * 1e-6
            / 1.6e-3 for d in (0.2, 0.6))
    got = solve(via_board(), {'N'}, 'S', {'L': 1.0}, out=tmp_path, h=0.25,
                log=lambda *x: None)
    assert -got['V']['L'] * g == pytest.approx(1.0, rel=0.10)
