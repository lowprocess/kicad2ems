"""The stack-up impedance calculator (lines/calc.py): its cross-sections against the field solver's own and the
closed forms, the fit of a width or a gap to a target, a board's stack-up as the stack, the command."""
import json
import pytest
from kicad2ems.lines import calc as K, sections, xsection as X



def test_a_microstrip_is_the_field_solver_s_and_near_its_formula():
    r = K.calculate(K.Line(0.35, 0.035, below=[(0.2, 4.3, 0.02)]))
    z, ee = X.microstrip(0.35, 0.2, 4.3, t=0.035, tand=0.02).solve().single()
    assert r['z0'] == pytest.approx(z, rel=1e-9) and r['eeff'] == pytest.approx(ee, rel=1e-9)
    name, zf, eef = r['formula']
    assert 'Hammerstad' in name and r['z0'] == pytest.approx(zf, rel=0.01) and r['eeff'] == pytest.approx(eef, rel=0.01)
    assert r['kind'] == 'microstrip' and r['delay_ps_mm'] == pytest.approx(ee ** 0.5 / 0.299792458, rel=1e-6)
    assert 0.01 < r['tand_eff'] < 0.02 and 'zdiff' not in r and 'loss_db_m' not in r
    assert K.calculate(K.Line(0.35, below=[(0.2, 4.3, 0.02)]), f=5e9)['loss_db_m'] == pytest.approx(
        8.685889638 * 3.14159265 * 5e9 * ee ** 0.5 / 299792458 * r['tand_eff'], rel=1e-6)


def test_a_stripline_s_thicknesses_are_between_the_copper_faces():
    line = K.Line(0.2, 0.035, below=[(0.2, 4.3)], above=[(0.15, 4.3)], top_plane=True)
    r = K.calculate(line)
    assert r['z0'] == pytest.approx(X.stripline(0.2, 0.2, 0.15, 4.3, t=0.035).solve().single()[0], rel=1e-9)
    assert r['eeff'] == pytest.approx(4.3) and r['kind'] == 'stripline'
    assert 'offset stripline' in r['formula'][0] and r['z0'] == pytest.approx(r['formula'][1], rel=0.01)
    xs = K.cross_section(line)
    assert xs.planes == [0.0, pytest.approx(0.385)] and (xs.signals[0].z0, xs.signals[0].z1) == (0.2, pytest.approx(0.235))


def test_a_pair_gives_its_modes_and_a_mask_lowers_them():
    line = K.Line(0.15, 0.035, below=[(0.1, 4.1)], gap=0.15)
    r = K.calculate(line)
    ze, zo, ee, eo = X.coupled_microstrip(0.15, 0.15, 0.1, 4.1, t=0.035).solve().pair()
    assert r['zdiff'] == pytest.approx(2 * zo, rel=1e-9) and r['zcommon'] == pytest.approx(ze / 2, rel=1e-9)
    assert r['zodd'] < r['z0'] < r['zeven'] and r['eeff_odd'] < r['eeff_even'] and 0 < r['coupling'] < 0.3
    assert 'formula' not in r and r['kind'] == 'microstrip pair'
    masked = K.calculate(K.Line(0.15, 0.035, below=[(0.1, 4.1)], gap=0.15, mask=0.02))
    assert masked['zdiff'] < r['zdiff'] and masked['eeff_odd'] > r['eeff_odd']
    grounded = K.calculate(K.Line(0.3, 0.0, below=[(0.2, 4.3)], ground_gap=0.2))
    assert grounded['kind'] == 'coplanar microstrip' and grounded['z0'] == pytest.approx(grounded['formula'][1], rel=0.02)


def test_the_width_and_the_gap_of_a_target():
    line = K.fit(K.Line(0.2, 0.035, below=[(0.2, 4.3)]), 50.0)
    assert K.calculate(line)['z0'] == pytest.approx(50.0, rel=1e-3) and 0.33 < line.w < 0.39
    far = K.fit(K.Line(5.0, 0.035, below=[(0.2, 4.3)]), 50.0)                     # from the other side: the same
    assert far.w == pytest.approx(line.w, rel=2e-3)
    pair = K.Line(0.15, 0.035, below=[(0.1, 4.1)], gap=0.15)
    by_gap = K.fit(pair, 100.0, vary='gap')
    assert K.calculate(by_gap)['zdiff'] == pytest.approx(100.0, rel=1e-3) and by_gap.gap > 0.15 and by_gap.w == 0.15
    by_w = K.fit(pair, 100.0)
    assert K.calculate(by_w)['zdiff'] == pytest.approx(100.0, rel=1e-3) and by_w.w < 0.15 and by_w.gap == 0.15
    assert K.calculate(K.fit(pair, 30.0, what='zcommon'))['zcommon'] == pytest.approx(30.0, rel=1e-3)
    with pytest.raises(ValueError, match=r"no gap from .* gives Zdiff 130 Ω: it runs from \d+\.\d to \d+\.\d Ω"):
        K.fit(pair, 130.0, vary='gap')                                          # the gap alone cannot reach it
    with pytest.raises(ValueError, match="a pair's"):
        K.fit(K.Line(0.2, below=[(0.2, 4.3)]), 100.0, what='zdiff')
    with pytest.raises(ValueError, match='has no gap'):
        K.fit(K.Line(0.2, below=[(0.2, 4.3)]), 50.0, vary='gap')


def test_a_line_says_what_is_wrong_with_it():
    for kw, msg in ((dict(w=0.2), 'needs a dielectric below'), (dict(w=-1, below=[(0.2, 4.3)]), 'w has to be positive'),
                    (dict(w=0.2, below=[(0.2, 0.5)]), 'er >= 1'), (dict(w=0.2, below=[(0.2, 4.3)], top_plane=True),
                                                                   'needs a dielectric between'),
                    (dict(w=0.2, below=[(0.2, 4.3)], above=[(0.1, 4)], mask=0.02), 'outer layer')):
        with pytest.raises(ValueError, match=msg):
            K.Line(**kw)
    line = K.Line(0.2, below=[[0.2, 4.3]], above=[(0.1, 3.6, 0.01)])
    assert line.below == [(0.2, 4.3, 0.0)] and K.Line.from_dict(json.loads(json.dumps(line.to_dict()))) == line
    assert line.kind == 'embedded microstrip' and 'Bahl' in K.calculate(line)['formula'][0]


def test_a_board_s_stack_up_around_a_layer(boards):
    board = boards['demo'] + '.kicad_pcb'                                       # 4 layers, a mask on top only
    top = K.Line.on_board(board, 'F.Cu', 0.3)
    assert top.kind == 'microstrip' and top.mask == pytest.approx(0.015) and top.er_mask == pytest.approx(3.3)
    assert top.below == [(pytest.approx(0.2), 4.3, pytest.approx(0.02))] and not top.above and top.t == pytest.approx(0.035)
    inner = K.Line.on_board(board, 'In1.Cu', 0.3, gap=0.2)
    assert inner.kind == 'stripline pair' and inner.top_plane and len(inner.below) == 1 and len(inner.above) == 1
    bottom = K.Line.on_board(board, 'B.Cu', 0.3, t=0.018)                       # from underneath: In2.Cu its plane
    assert bottom.kind == 'microstrip' and not bottom.mask and bottom.t == 0.018
    assert K.calculate(bottom)['z0'] > K.calculate(top)['z0'] > K.calculate(K.Line.on_board(board, 'In1.Cu', 0.3))['z0']
    with pytest.raises(ValueError, match="no copper layer 'In7.Cu': F.Cu, In1.Cu, In2.Cu, B.Cu"):
        K.Line.on_board(board, 'In7.Cu', 0.3)


def test_the_field_view_of_a_line():
    single = K.element(K.Line(0.35, 0.035, below=[(0.2, 4.3)]))
    assert [k for k, _ in sections.modes(single)] == ['cut']
    v = sections.solve(single)
    assert v.values['Z0'] == pytest.approx(50.8, abs=0.3) and v.volts == [1.0]
    pair = K.Line(0.15, 0.035, below=[(0.1, 4.1)], gap=0.15)
    e = K.element(pair, K.calculate(pair))
    assert [k for k, _ in sections.modes(e)] == ['odd', 'even', 'line'] and e['z0'] and e['source'] == 'field'
    assert sections.solve(e, 'odd').values['Zdiff'] == pytest.approx(K.calculate(pair)['zdiff'], rel=1e-6)
    assert sections.solve(e, 'even').volts == [1.0, 1.0]


def test_the_command(tmp_path, capsys, boards):
    assert K.main(['--w', '0.35', '--h', '0.2', '--er', '4.3']) == 0
    out = capsys.readouterr().out
    assert out.startswith('microstrip: w 0.35, t 0.035 mm on 0.2 mm εr 4.3') and 'Z0 ' in out and 'Hammerstad' in out
    png = tmp_path / 'z0.png'
    assert K.main(['--h', '0.2', '--target', '50', '--plot', str(png), '--f', '2.4']) == 0
    out = capsys.readouterr().out
    assert out.startswith('w = 0.3') and 'for Z0 50 Ω' in out and 'dB/m' in out and png.stat().st_size > 20000
    assert K.main(['--w', '0.15', '--gap', '0.15', '--below', '0.05:4.1:0.02,0.05:3.6', '--target', '100', '--vary',
                   'gap', '--json']) == 0
    d = json.loads(capsys.readouterr().out)
    assert d['result']['zdiff'] == pytest.approx(100, rel=1e-3) and d['line']['below'] == [[0.05, 4.1, 0.02], [0.05, 3.6, 0.0]]
    board = boards['demo'] + '.kicad_pcb'
    assert K.main(['--board', board, '--layer', 'In1.Cu', '--w', '0.2', '--gap', '0.2']) == 0
    assert capsys.readouterr().out.startswith('stripline pair')
    for argv, msg in ((['--w', '0.3'], 'give the stack'), (['--h', '0.2'], 'give the width'),
                      (['--w', '0.3', '--h', '0.2', '--target', '900'], 'no w from'),
                      (['--board', board, '--w', '0.3'], 'needs --layer'),
                      (['--w', '0.3', '--below', '0.2;4.3'], "thickness:er")):
        assert K.main(argv) == 2
        assert msg in capsys.readouterr().err
