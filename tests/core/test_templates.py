"""The Draw page's templates (kicad2ems.core.templates): parametric groups of
shapes, sized to the board's stack, that are then ordinary shapes."""
import json, math, os
import numpy as np
import pytest
from kicad2ems.core import templates as T
from kicad2ems.core.shapes import Shape, Drawing, stack_of, apply

EXAMPLES = os.path.join(os.path.dirname(__file__), '..', '..', 'examples')
# rf_circulator's board: 0.508 mm RO4003C (er 3.55) between B.Cu and F.Cu
RO4003 = dict(cu_z={'B.Cu': 0.0, 'F.Cu': 0.508},
              cu_t={'B.Cu': 0.035, 'F.Cu': 0.035},
              layers=[dict(name='d1', z0=0.0, z1=0.508, er=3.55, tand=0.0027,
                           kind='diel')])
# sma_launch's: F.Cu over In1.Cu 0.21 mm down, four layers
FOUR = dict(cu_z={'B.Cu': 0.0, 'In2.Cu': 0.2104, 'In1.Cu': 1.2754,
                  'F.Cu': 1.4858},
            cu_t={k: 0.035 for k in ('B.Cu', 'In2.Cu', 'In1.Cu', 'F.Cu')},
            layers=[dict(name='d3', z0=0.0, z1=0.2104, er=4.4, tand=0.02),
                    dict(name='d2', z0=0.2104, z1=1.2754, er=4.6, tand=0.02),
                    dict(name='d1', z0=1.2754, z1=1.4858, er=4.4,
                         tand=0.02)])


def test_what_a_template_stands_on():
    s = T.substrate(RO4003, 'F.Cu')
    assert (s['z'], s['surface'], s['up']) == (0.508, 0.543, 1)
    assert s['h'] == pytest.approx(0.508) and s['er'] == pytest.approx(3.55)
    assert (s['zref'], s['zb'], s['zt']) == (0.0, 0.0, 0.508)
    # over the inner plane next down, not the board's bottom
    s = T.substrate(FOUR, 'F.Cu')
    assert s['zref'] == 1.2754 and s['h'] == pytest.approx(0.2104)
    assert s['er'] == pytest.approx(4.4)
    # the bottom layer faces down: its shapes hang below
    s = T.substrate(stack_of(None), 'B.Cu')
    assert s['up'] == -1 and s['surface'] == pytest.approx(-0.035)
    assert s['zref'] == 1.6 and s['h'] == pytest.approx(1.6)


def test_line_widths_on_the_stack():
    from kicad2ems.core.lines.formulas import microstrip
    sub = T.substrate(RO4003, 'F.Cu')
    # the circulator example's arms and transformers (make_rf_examples)
    assert T.line_width(50, sub) == pytest.approx(1.137, abs=1e-3)
    assert T.line_width(30, sub) == pytest.approx(2.384, abs=1e-3)
    w = T.line_width(50, T.substrate(FOUR, 'F.Cu'))
    assert microstrip(w, 0.2104, 4.4)[0] == pytest.approx(50, abs=0.05)


def test_the_circulator_is_the_examples():
    """On rf_circulator's stack, at its centre, the template makes the
    example's drawing (its arms end at 12 mm, the example's run past the
    board's edge), and a port at each arm's end facing the junction."""
    ex = Drawing.load(os.path.join(EXAMPLES, 'rf_circulator_shapes.json'))
    got = T.make('circulator', stack=RO4003, at=(12.0, -10.3925))
    shapes = [s for s in got if s.kind != 'port']
    assert [s.name for s in shapes] == [s.name for s in ex.shapes]
    for a, b in zip(ex.shapes, shapes):
        assert (a.kind, a.material, a.layer) == (b.kind, b.material, b.layer)
        assert (a.z0, a.z1, a.radius, a.br) == pytest.approx(
            (b.z0, b.z1, b.radius, b.br))
        if a.kind == 'path':
            assert b.width == pytest.approx(a.width, abs=1e-3)
            # the same line, from the same start
            assert b.points[0] == pytest.approx(a.points[0], abs=1e-3)
            u = np.subtract(a.points[1], a.points[0])
            v = np.subtract(b.points[1], b.points[0])
            assert abs(u[0] * v[1] - u[1] * v[0]) < 1e-3 * np.hypot(*u) * (
                np.hypot(*v))
            if a.name.startswith('transformer'):
                assert b.points[1] == pytest.approx(a.points[1], abs=1e-3)
    fer = got[0]
    assert (fer.ms, fer.dh, fer.er, fer.tand) == (1200, 30, 15, 2e-4)
    ports = [s for s in got if s.kind == 'port']
    assert [p.name for p in ports] == ['P1', 'P2', 'P3']
    assert [p.rot for p in ports] == [0.0, 240.0, 120.0]
    # each on its arm, 0.6 mm in from the end
    for p, ang in zip(ports, (180, 60, 300)):
        r = np.subtract(p.points[0], (12.0, -10.3925))
        assert np.hypot(*r) == pytest.approx(12 - 0.6)
        assert math.degrees(math.atan2(r[1], r[0])) % 360 == pytest.approx(
            ang)
    # no transformers, no magnet, no ports
    bare = T.make('circulator', dict(zt=0, magnet=False, ports=False),
                  RO4003)
    assert [s.name for s in bare] == ['puck', 'junction', 'arm1', 'arm2',
                                      'arm3']
    with pytest.raises(ValueError):
        T.make('circulator', dict(arm=5.0), RO4003)  # shorter than lambda/4
    with pytest.raises(ValueError):
        T.make('circulator', dict(r_disc=4.0), RO4003)  # disc past the puck


def test_a_shield_can_with_its_post_fence():
    v = dict(w=10.0, l=6.0, h=3.0, t=0.2, fence=True, post=0.3, pitch=1.0)
    ss = T.make('can', v, RO4003, at=(5.0, -3.0))
    can, air, posts = ss[0], ss[1], ss[2:]
    assert (can.material, air.material) == ('metal', 'air')
    assert can.bounds() == pytest.approx((0, -6, 10, 0))
    assert air.bounds() == pytest.approx((0.2, -5.8, 9.8, -0.2))
    # on the copper, up to its lid; the air under the lid (drawn after the
    # metal: it hollows it, open at the bottom)
    assert (can.z0, can.z1) == pytest.approx((0.543, 3.543))
    assert (air.z0, air.z1) == pytest.approx((0.543, 3.343))
    # posts through the board under the walls' middle, at most 1 mm apart,
    # one at every corner
    xy = np.array([p.points[0] for p in posts])
    assert all((p.material, p.z0, p.z1, p.radius) == ('metal', 0.0, 0.508,
                                                      0.15) for p in posts)
    on_wall = (np.isclose(xy[:, 0], 0.1) | np.isclose(xy[:, 0], 9.9)
               | np.isclose(xy[:, 1], -0.1) | np.isclose(xy[:, 1], -5.9))
    assert on_wall.all()
    assert len(posts) == 2 * (math.ceil(9.8) + math.ceil(5.8))
    for c in ((0.1, -0.1), (9.9, -0.1), (9.9, -5.9), (0.1, -5.9)):
        assert np.hypot(*(xy - c).T).min() < 1e-9
    assert len(T.make('can', dict(v, fence=False), RO4003)) == 2
    with pytest.raises(ValueError):
        T.make('can', dict(v, w=0.3), RO4003)
    # on the bottom layer it hangs below the board
    low = T.make('can', v, stack_of(None), 'B.Cu')[0]
    assert (low.z0, low.z1) == pytest.approx((-3.035, -0.035))


def test_a_resonator_an_sma_and_an_absorber():
    dr = T.make('dr', dict(gap=1.0, r=3.0, height=2.5, side='+y'), RO4003,
                at=(0.0, 0.0))
    line, puck, p1, p2 = dr
    w = line.width
    assert w == pytest.approx(1.137, abs=1e-3)
    # the puck's rim `gap` from the line's edge, on the substrate
    assert puck.points[0][1] - 3.0 - w / 2 == pytest.approx(1.0)
    assert (puck.z0, puck.z1, puck.er) == pytest.approx((0.508, 3.008, 36))
    assert (p1.rot, p2.rot) == (0.0, 180.0)
    below = T.make('dr', dict(side='-y'), RO4003)[1]
    assert below.points[0][1] < 0
    # an edge launch at x = 0: the body off the board, the pin onto the line
    sma = T.make('sma', dict(facing='left'), RO4003, at=(0.0, 0.0))
    body, ptfe, pin, sline, port = sma
    assert body.bounds()[2] == pytest.approx(0.0) and body.bounds()[0] < 0
    assert pin.bounds()[2] == pytest.approx(1.5)
    a = pin.bounds()[3] - pin.bounds()[1]
    assert a == pytest.approx(T.coax_pin(50, 4.1, 2.1))
    # the pin lies on the copper; the channel round it
    assert pin.z0 == pytest.approx(0.543)
    assert (ptfe.z0 + ptfe.z1) / 2 == pytest.approx((pin.z0 + pin.z1) / 2)
    # a square pin in a square channel: 60/sqrt(er) ln(1.0787 d / 1.18 a)
    assert 60 / math.sqrt(2.1) * math.log(1.0787 * 4.1 / (1.1803 * a)) == (
        pytest.approx(50))
    right = T.make('sma', dict(facing='right'), RO4003, at=(10.0, 0.0))[0]
    assert right.bounds()[0] == pytest.approx(10.0)
    up = T.make('sma', dict(facing='up'), RO4003, at=(0.0, 5.0))
    assert up[0].bounds()[1] == pytest.approx(5.0)
    assert up[-1].rot == pytest.approx(90.0)  # (its port faces the edge)
    ab = T.make('absorber', dict(w=4, l=2, t=0.5, z=2.0, er=10, tand=0.5),
                RO4003, at=(1.0, 1.0))[0]
    assert ab.bounds() == pytest.approx((-1, 0, 3, 2))
    assert (ab.z0, ab.z1, ab.er, ab.tand) == pytest.approx((2.543, 3.043, 10,
                                                            0.5))


def test_a_fence_along_traces():
    tr = Shape('path', [[0, 0], [10, 0], [10, 10]], width=1.0, name='rf')
    d = T.defaults('fence', RO4003, along=[tr])
    assert d['offset'] == pytest.approx(0.5 + 0.5 + 0.15)
    posts = T.make('fence', dict(offset=1.5, pitch=1.0, post=0.3),
                   RO4003, along=[tr])
    xy = np.array([p.points[0] for p in posts])
    from shapely.geometry import LineString
    line = LineString(tr.points)
    assert all(line.distance(__import__('shapely').geometry.Point(p))
               == pytest.approx(1.5, rel=2e-3) for p in xy)
    assert (xy[:, 1] > 0).any() and (xy[:, 1] < 0).any()  # both sides
    # spaced at most a pitch along each side
    one = T.fence_posts(tr.points, 1.5, 1.0, both=False)
    gaps = np.hypot(*np.diff(np.array(one), axis=0).T)
    assert gaps.max() <= 1.0 + 1e-9
    assert posts[0].name == 'rf_post_1' and posts[0].z1 == 0.508
    with pytest.raises(ValueError):  # touching the trace
        T.make('fence', dict(offset=0.6, post=0.3), RO4003, along=[tr])
    with pytest.raises(ValueError):  # no trace
        T.make('fence', None, RO4003, along=[Shape('rect', [[0, 0], [1, 1]])])


def test_every_template_applies_to_a_board():
    """Each template's shapes are valid, and a run takes them: its copper on
    the layer, its solids as board.solids."""
    from shapely.geometry import box
    from kicad2ems.gui.drawmagnets import _Board
    tr = Shape('path', [[2, -10], [20, -10]], width=1.0)
    for key, t in T.TEMPLATES.items():
        ss = T.make(key, None, RO4003, at=(12.0, -10.0), along=[tr])
        assert ss and all(isinstance(s, Shape) for s in ss)
        b = _Board(box(0, -21, 24, 0), RO4003)
        b.cu_z, b.copper, b.copper_nets = RO4003['cu_z'], {'F.Cu': []}, {
            'F.Cu': []}
        b.cu_order = ['F.Cu', 'B.Cu']
        apply(b, Drawing(ss))
        assert len(b.solids) == sum(s.solid for s in ss)
        assert (len(b.copper['F.Cu']) > 0) == any(
            s.material == 'copper' and s.kind != 'port' for s in ss)
        # the dialog's numbers: every parameter has a default of its kind
        for p in t.params:
            assert (isinstance(p.default, bool) if p.kind == 'bool' else
                    p.default in p.choices if p.kind == 'choice' else
                    isinstance(p.default, (int, float)))
