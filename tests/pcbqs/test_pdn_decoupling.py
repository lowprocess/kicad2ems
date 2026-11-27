"""Decaps chosen to a target impedance (pcbqs.pdn.decoupling): the library and
its DC-bias derating against their closed forms, the closed form of a set of
parts against a full solve, the search against an exhaustive one, and the
CLI's --choose-decaps end to end on the rail tests' synthetic board."""
import copy
import json

import numpy as np
import pytest
from shapely.geometry import box, mapping

from kicad2ems.pcbqs.pdn import decoupling as D
from kicad2ems.pcbqs.pdn.rail import Rail
from kicad2ems.pcbqs.pdn.stack import RailPDN

from test_pdn_rail import board, pad, fp

# three values, so an exhaustive search stays small
VALUES = (1e-7, 1e-6, 1e-5)


def with_sites(b):
    """The rail board with three unfitted decap footprints on P (two 0402
    beside U1, a 0603 far from it), each pad on a via to the planes."""
    b = copy.deepcopy(b)
    F = b['copper']['F.Cu']
    for ref, x, y, lib in (('C4', 28.0, 20.0, 'C_0402_1005Metric'),
                           ('C5', 30.0, 20.0, 'C_0402_1005Metric'),
                           ('C6', 10.0, 25.0, 'C_0603_1608Metric')):
        F.append(dict(net='P', polygon=mapping(box(x - .3, y - .3, x + .3,
                                                   y + .3))))
        F.append(dict(net='GND', polygon=mapping(box(x - .3, y + .7, x + .3,
                                                     y + 1.3))))
        b['vias'] += [dict(x=x, y=y, r=0.15, z0=0, z1=0.6, net='P'),
                      dict(x=x, y=y + 1.0, r=0.15, z0=0, z1=0.6, net='GND')]
        f = fp(ref, '', [pad('1', 'P', x, y, .6, .6),
                         pad('2', 'GND', x, y + 1, .6, .6)], lib=lib)
        f.update(dnp=True, props={})
        b['prims']['footprints'].append(f)
    return b


@pytest.fixture(scope='module')
def rail_pdn():
    r = Rail(with_sites(board()), 'P', loads={'U1': 2.0})
    pdn = RailPDN(r, dmax=1.0, log=lambda *a: None, sites=r.sites())
    return r, pdn, np.logspace(4, 9, 80)


def small_library():
    return [c for c in D.library()
            if any(abs(c.C - v) < 1e-3 * v for v in VALUES)]


def test_the_derating_curve_at_its_closed_form_points():
    # a part at its package's largest value keeps 1 / (1 + (V / V0)^2)
    assert D.share('0402', 10e-6, 3.3) == pytest.approx(
        1 / (1 + (3.3 / D.V0)**2))
    assert D.share('0402', 10e-6, 3.3) == pytest.approx(0.346, abs=1e-3)
    # a decade below: Vh = V0 10^(1/4); two: V0 10^(1/2)
    assert D.share('0402', 1e-6, 3.3) == pytest.approx(
        1 / (1 + (3.3 / (D.V0 * 10**0.25))**2))
    assert D.share('0603', 220e-9, 3.3) == pytest.approx(
        1 / (1 + (3.3 / (D.V0 * 10**0.5))**2))
    # a rated voltage's quarter where it is higher; C0G and no bias keep all
    assert D.share('0402', 10e-6, 3.3, rated=50) == pytest.approx(
        1 / (1 + (3.3 / 12.5)**2))
    assert D.share('0402', 1e-9, 3.3, 'C0G') == 1.0 == D.share(
        '0402', 10e-6, 0.0)
    assert D.dielectric_of('10u X5R 6V3') == ('X5R', 6.3)
    assert D.dielectric_of('100nF/50V X7R') == ('X7R', 50.0)
    assert D.dielectric_of('1n NP0') == ('NP0', 0.0)


def test_a_derating_table_and_a_library_from_files(tmp_path):
    (tmp_path / 'd.csv').write_text(
        'package,C,volts,share\n0402,10u,0,1\n0402,10u,4,0.5\n'
        '0402,,0,1\n0402,,5,0.9\n')
    t = D.read_derating(tmp_path / 'd.csv')
    # its value's rows, interpolated in volts; else its package's
    assert D.share('0402', 10e-6, 2.0, table=t) == pytest.approx(0.75)
    assert D.share('0402', 1e-6, 2.5, table=t) == pytest.approx(0.95)
    # a package it has no row of: the curve
    assert D.share('0603', 1e-6, 2.0, table=t) == D.share('0603', 1e-6, 2.0)
    (tmp_path / 'lib.csv').write_text(
        'name,package,C,ESR,cost\nA,0402,100n,0.02,0.5\nB,0603,4.7u,,\n')
    a, b = D.read_library(tmp_path / 'lib.csv')
    assert (a.C == pytest.approx(1e-7) and a.ESR == 0.02 and a.cost == 0.5
            and a.ESL == D.ESL_PKG['0402'])
    assert b.ESR == pytest.approx(D.esr(4.7e-6)) and b.dielectric == 'X5R'
    (tmp_path / 'bad.csv').write_text('name,package,C\nX,0402,10k\n')
    with pytest.raises(ValueError):
        D.read_library(tmp_path / 'bad.csv')
    # the built-in: E3 values in each package's range, ESL by package
    lib = D.library()
    assert {c.package for c in lib} == set(D.PACKAGES)
    assert max(c.C for c in lib if c.package == '0201') == pytest.approx(1e-6)
    assert D.esr(1e-6) == D.ESR_1U and D.esr(1e-7) == pytest.approx(
        D.ESR_1U * 10**0.4)
    assert D.parse_values('100n, 1u,10u') == pytest.approx([1e-7, 1e-6, 1e-5])


def test_rank_one_updates_are_the_woodbury_closure(rail_pdn):
    """A part added at a site and taken off again by the rank-one terms is the
    network with and without it as Woodbury's identity closes it."""
    r, pdn, f = rail_pdn
    sites = [n for n, i in pdn.parts.items() if i['role'] in ('decap', 'site')]
    cl = D.Closure(pdn, f, ['U1'], sites, ['L1'], log=lambda *a: None)
    lib = small_library()
    options = [[(c, 1.0, cl.site_z(n, c, 1.0))
                for c in lib if c.package == D.package_of(
                    pdn.parts[n]['part'].lib)] for n in sites]
    se = D.Search(cl, options, [0.2], (f[0], f[-1]))
    at = {0: 1, 3: 2, 5: 0}
    Z = se.Z0
    for c, j in at.items():
        Z = se.added(Z, se.nl + c, se.zp[c, j])
    ref = se.state(at).Z
    assert np.abs(Z - ref).max() <= 1e-9 * np.abs(ref).max()
    back = se.removed(Z, se.nl + 3, se.zp[3, 2])
    assert np.allclose(back, se.state({0: 1, 5: 0}).Z, rtol=1e-9,
                       atol=1e-12 * np.abs(ref).max())


def test_the_closed_form_is_a_full_solve_and_the_fitted_set_the_sweep(
        rail_pdn):
    r, pdn, f = rail_pdn
    Z, _ = pdn.sweep(f, observe=['U1'])
    dec = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, lib=small_library(),
                   fmax=2e7, log=lambda *a: None)
    # the chosen parts: the closed form against RailPDN.z with them attached
    assert dec['check']['max_rel'] < 1e-7
    # the fitted parts through the closure: the rail's own sweep
    assert np.allclose(dec['loads']['U1']['mag_before'], np.abs(Z['U1']),
                       rtol=1e-7)
    # the model's parts are as they were
    assert {n: i['el'] is None for n, i in pdn.parts.items()} == {
        'C1': False, 'C2': False, 'C3': False, 'C4': True, 'C5': True,
        'C6': True, 'L1': False, 'U1': True}


def test_the_fewest_parts_against_an_exhaustive_search(rail_pdn):
    """0.15 ohm to 20 MHz at U1: the three fitted parts miss it (2.6 x); the
    search meets it with two, and no one part of any site and value does."""
    r, pdn, f = rail_pdn
    lib = small_library()
    kw = dict(lib=lib, fmax=2e7, log=lambda *a: None)
    dec = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, **kw)
    lo = dec['loads']['U1']
    assert (dec['met'] and dec['parts'] == 2 and dec['parts_before'] == 3
            and lo['worst_before'] > 2 and lo['worst_after'] <= 1)
    assert lo['over_after'] == [] and lo['margin_after'] >= 0
    assert sum(b['count'] for b in dec['bom']) == 2
    # deterministic
    assert D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, **kw)['sites'] == (
        dec['sites'])
    # every one-part set, exhaustively
    sites = [x['site'] for x in dec['sites']]
    cl = D.Closure(pdn, f, ['U1'], sites, ['L1'], log=lambda *a: None)
    best = np.inf
    for s, n in enumerate(sites):
        pkg = D.package_of(pdn.parts[n]['part'].lib)
        for c in lib + ([None] if pdn.parts[n]['el'] else []):
            if c is None:  # the fitted part
                z = pdn._part_z(pdn.parts[n], 2 * np.pi * f)
            elif c.package == pkg:
                z = cl.site_z(n, c, D.share(pkg, c.C, 3.3, c.dielectric))
            else:
                continue
            Za = cl.attached({s: z})
            m = f <= 2e7
            best = min(best, (np.abs(Za[m, 0, 0] + cl.own[m, 0]) / 0.15).max())
    assert best > 1
    # cost: no dearer than the fewest's set, and its own cost the lowest
    # it found
    cheap = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, mode='cost', **kw)
    priced = {(c.name, c.package): c.cost for c in lib}
    fewest_cost = sum(priced[(x['part'], x['package'])] for x in dec['sites']
                      if x['part'])
    assert cheap['met'] and cheap['cost'] <= fewest_cost + 1e-9
    # from the fitted parts: no more than the fitted, the target met
    built = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, start='built', **kw)
    assert built['met'] and built['parts'] <= 3


def test_kept_parts_sites_and_an_unreachable_band(rail_pdn):
    r, pdn, f = rail_pdn
    kw = dict(lib=small_library(), log=lambda *a: None)
    dec = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, fmax=2e7,
                   keep=['C2'], **kw)
    assert 'C2' in dec['kept'] and 'C2' not in [x['site']
                                               for x in dec['sites']]
    dec = D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, fmax=2e7,
                   sites=['C4', 'C5', 'C6'], **kw)
    assert ([x['site'] for x in dec['sites']] == ['C4', 'C5', 'C6']
            and {'C1', 'C2', 'C3'} <= set(dec['fixed']))
    with pytest.raises(ValueError, match='not a decap footprint'):
        D.choose(pdn, f, ['U1'], {'U1': 0.15}, 3.3, sites=['U1'], **kw)
    with pytest.raises(ValueError, match='target'):
        D.choose(pdn, f, ['U1'], {'U1': None}, 3.3, **kw)
    # 20 mOhm to 1 GHz: U1's own pads are over it from some frequency on, said
    # and left out; not met, and every part it chose took its share
    dec = D.choose(pdn, f, ['U1'], {'U1': 0.02}, 3.3, **kw)
    assert (not dec['met'] and dec['loads']['U1']['out_of_reach_from']
            and any('own pads' in n for n in dec['notes']))
    # the report says where it is out of reach, not 'to' the band's top
    from kicad2ems.pcbqs import reports
    out = dict(summary='', sections=[], images=[])
    reports._decoupling_sections(dec, out)
    a = dec['loads']['U1']['out_of_reach'][0][0]
    assert (f"where in reach (out of reach from {reports._hz(a)} at U1"
            in out['summary'] and ' to 1 GHz' not in out['summary'])
    assert 'out of reach from' in out['sections'][0]['text']
    assert all(s['excess'] < p['excess'] * (1 - D.EARN)
               for p, s in zip(dec['steps'], dec['steps'][1:])
               if s['move'] == 'added')


def test_cli_chooses_the_decaps(tmp_path):
    from kicad2ems.pcbqs.pdn.cli import main
    from kicad2ems.pcbqs import reports
    from kicad2ems.pcbqs.analyses import PowerIntegrity
    path = tmp_path / 'b_board.json'
    path.write_text(json.dumps(with_sites(board())))
    common = [str(path), '--rail', 'P', '--volts', '3.3', '--load', 'U1=2',
              '--dmax', '1', '--points', '60', '--target', '0.15',
              '--decap-fmax', '2e7']
    assert main(common + ['--out', str(tmp_path / 'a')]) == 0
    assert main(common + ['--choose-decaps', '--decap-values', '100n,1u,10u',
                          '--out', str(tmp_path / 'b')]) == 0
    a, b = (json.loads((tmp_path / x / 'pdn.json').read_text()) for x in 'ab')
    dec = b['pdn']['decoupling']
    assert dec['met'] and dec['parts'] <= 3 and (tmp_path / 'b' /
                                                 'decoupling.png').is_file()
    assert {x['part'].split()[0] for x in dec['sites'] if x['part']} <= {
        '100n', '1u', '10u'}
    # the sites are no parts of the model's (Palace's PDN reads those)
    assert set(b['pdn']['parts']) == set(a['pdn']['parts'])
    # --choose-decaps takes the fitted MLCCs at their bias, as --derate-bias
    c2 = b['pdn']['parts']['C2']['element']
    assert c2['derate'] == pytest.approx(D.share('0805', 10e-6, 3.3))
    assert 'derate' not in a['pdn']['parts']['C2']['element']
    rep = reports.report('pdn', str(tmp_path / 'b'))
    titles = [s['title'] for s in rep['sections']]
    assert ('Decoupling: the parts (a BOM)' in titles
            and f"decaps chosen: {dec['parts']} part(s)" in rep['summary']
            and 'decoupling.png' in rep['images'])
    cs = reports.curves('pdn', str(tmp_path / 'b'))
    assert any(c['title'].startswith('Decoupling') for c in cs)
    figs = {x['name']: x['value']
            for x in PowerIntegrity().figures(str(tmp_path / 'b'))}
    assert figs['parts'] == dec['parts'] and figs['margin U1'] >= 0
    # the question asks for it
    v = PowerIntegrity().for_questions(dict(rail='P'), ('decoupling', ))
    assert v['choose_decaps'] and not v['no_pdn']
    # what a run of these settings answers (its mark, started with none asked)
    pi = PowerIntegrity()
    assert (pi.answers(v) == ('dc', 'pdn', 'decoupling')
            and pi.answers(dict(rail='P')) == ('dc', 'pdn')
            and pi.answers(dict(rail='P', no_pdn='true')) == ('dc', ))
    # what it is refused
    with pytest.raises(SystemExit, match='needs the PDN'):
        main(common + ['--choose-decaps', '--no-pdn', '--out',
                       str(tmp_path / 'c')])
    with pytest.raises(SystemExit, match='no capacitance'):
        main(common + ['--choose-decaps', '--decap-values', '10k', '--out',
                       str(tmp_path / 'd')])
