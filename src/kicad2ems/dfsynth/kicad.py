"""
Writes a Design's layout as a 2-layer KiCad board (KiCad 8 format, opens in
KiCad 8 and later), a kicad2ems run configuration next to it, and the filter as
a footprint (.kicad_mod) to place in one's own board.

The filter copper is filled polygons on F.Cu (net RF), the ground a filled zone
on B.Cu (net GND), the ports kicad2ems:RF_Port footprints with a Fab text P1 /
P2 (rotation = direction into the circuit) and the stubs' ground vias GND vias.
KiCad's DRC reports those vias as shorts to RF: they are meant to be.
"""
import math
import os
import uuid

from .calibrate import band, sweep
from .topologies import VIA_DRILL, VIA_SIZE


def _u():
    return str(uuid.uuid4())


def _xy(pts):
    return ' '.join(f'(xy {x:.4f} {y:.4f})' for x, y in pts)


def board_text(design, title=''):
    geo, sub = design.geometry, design.sub
    W, H = geo.W, geo.H
    nets = ['', 'GND', 'RF']
    L = [
        '(kicad_pcb (version 20240108) (generator "dfsynth") '
        '(generator_version "8.0")',
        f'  (general (thickness {sub.h + 2 * sub.t:.4f}) (legacy_teardrops '
        'no))',
        '  (paper "A4")',
        f'  (title_block (title "{title}") (comment 1 "dfsynth: '
        f'{design.spec.kind} / {design.spec.topology}"))',
        '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) '
        '(39 "F.Mask" user) (41 "Cmts.User" user) (44 "Edge.Cuts" user) (49 '
        '"F.Fab" user))',
        '  (setup (stackup',
        f'    (layer "F.Cu" (type "copper") (thickness {sub.t}))',
        f'    (layer "dielectric 1" (type "core") (thickness {sub.h}) '
        f'(material "{sub.material}") (epsilon_r {sub.er}) (loss_tangent '
        f'{sub.tand}))',
        f'    (layer "B.Cu" (type "copper") (thickness {sub.t}))',
        '    (copper_finish "None") (dielectric_constraints no))',
        '    (pad_to_mask_clearance 0))',
        '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(nets))
    ]
    for poly in geo.polys:
        L.append(
            f'  (gr_poly (pts {_xy(poly)}) (stroke (width 0) (type solid)) '
            f'(fill solid) (layer "F.Cu") (net 2) (uuid "{_u()}"))')
    for x, y in geo.vias:
        L.append(
            f'  (via (at {x:.4f} {y:.4f}) (size {VIA_SIZE}) (drill '
            f'{VIA_DRILL}) (layers "F.Cu" "B.Cu") (net 1) (uuid "{_u()}"))')
    outer = [(0, 0), (W, 0), (W, H), (0, H)]
    L.append(
        f'  (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{_u()}") '
        '(hatch edge 0.5) (connect_pads yes (clearance 0)) (min_thickness '
        '0.1) (filled_areas_thickness no) (fill yes (thermal_gap 0.5) '
        f'(thermal_bridge_width 0.5)) (polygon (pts {_xy(outer)})) '
        f'(filled_polygon (layer "B.Cu") (pts {_xy(outer)})))'
    )
    for n, x, y, rot in geo.ports:
        L += [
            f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{_u()}") '
            f'(at {x:.4f} {y:.4f} {rot})',
            f'    (property "Reference" "TP{n}" (at 0 -1.5 {rot}) (layer '
            f'"F.SilkS") (uuid "{_u()}"))',
            f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") '
            f'(uuid "{_u()}"))',
            f'    (fp_text user "P{n}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid '
            f'"{_u()}"))',
            '  )'
        ]
    f0 = design.spec.f0
    freq = f'{f0 / 1e9:.4g} GHz' if f0 >= 1e9 else f'{f0 / 1e6:.4g} MHz'
    note = f'{design.spec.kind} {design.spec.topology} {freq}'
    L.append(
        f'  (gr_text "{note}" (at 1 {H + 1.5:.3f} 0) (layer "Cmts.User") '
        f'(uuid "{_u()}") '
        '(effects (font (size 1 1) (thickness 0.15)) (justify left)))')
    L.append(
        f'  (gr_rect (start 0 0) (end {W:.4f} {H:.4f}) (stroke (width 0.1) '
        f'(type default)) (fill none) (layer "Edge.Cuts") (uuid "{_u()}"))')
    L.append(')')
    return '\n'.join(L) + '\n'


def _feed_width(polys, x, y, rot):
    """The width of the copper at a port (x, y; rot: the direction into the
    circuit, degrees): the polygon there, across that direction."""
    from shapely.geometry import Point, Polygon
    a = math.radians(rot)
    # (KiCad's y runs down: a rotation of 90 points to -y)
    inside = Point(x + 0.05 * math.cos(a), y - 0.05 * math.sin(a))
    for poly in polys:
        g = Polygon(poly)
        if g.buffer(1e-6).contains(inside):
            x0, y0, x1, y1 = g.bounds
            return (y1 - y0) if abs(math.cos(a)) > 0.5 else (x1 - x0)
    return 0.3


def footprint_text(design, name):
    """The filter as a KiCad footprint (.kicad_mod, KiCad 8): its copper as
    polygons on F.Cu, a pad at each port (1, 2: the feed's width, 0.5 mm into
    it, where a board's track joins), the stubs' ground vias as plated pads 3
    (GND); the origin at the filter's centre. It needs a ground plane on the
    layer below at the substrate's height it was designed for (its
    description says which)."""
    geo, sub = design.geometry, design.sub
    cx, cy = geo.W / 2, geo.H / 2
    sh = lambda pts: [(x - cx, y - cy) for x, y in pts]
    s = design.spec
    f0 = f'{s.f0 / 1e9:.4g} GHz' if s.f0 >= 1e9 else f'{s.f0 / 1e6:.4g} MHz'
    descr = (f"dfsynth {s.kind} / {s.topology} {f0}, order {s.order}, on "
             f"{sub.h:g} mm {sub.material} (er {sub.er:g}) over a ground "
             "plane on the next layer down; pads 1, 2: the ports, 3: ground")
    L = [
        f'(footprint "{name}" (version 20240108) (generator "dfsynth") '
        '(generator_version "8.0") (layer "F.Cu")',
        f'  (descr "{descr}")',
        '  (attr smd)',
        f'  (property "Reference" "FL**" (at 0 {-cy - 1.2:.3f} 0) (layer '
        f'"F.SilkS") (uuid "{_u()}") (effects (font (size 1 1) (thickness '
        '0.15))))',
        f'  (property "Value" "{name}" (at 0 {cy + 1.2:.3f} 0) (layer '
        f'"F.Fab") (uuid "{_u()}") (effects (font (size 1 1) (thickness '
        '0.15))))',
    ]
    for poly in geo.polys:
        L.append(f'  (fp_poly (pts {_xy(sh(poly))}) (stroke (width 0) (type '
                 f'solid)) (fill solid) (layer "F.Cu") (uuid "{_u()}"))')
    for n, x, y, rot in geo.ports:
        w = _feed_width(geo.polys, x, y, rot)
        a = math.radians(rot)
        px, py = x + 0.25 * math.cos(a) - cx, y - 0.25 * math.sin(a) - cy
        L.append(f'  (pad "{n}" smd rect (at {px:.4f} {py:.4f} {rot}) (size '
                 f'0.5 {w:.4f}) (layers "F.Cu") (uuid "{_u()}"))')
    for x, y in geo.vias:
        L.append(f'  (pad "3" thru_hole circle (at {x - cx:.4f} {y - cy:.4f}) '
                 f'(size {VIA_SIZE} {VIA_SIZE}) (drill {VIA_DRILL}) (layers '
                 f'"*.Cu") (uuid "{_u()}"))')
    L.append(f'  (fp_rect (start {-cx:.4f} {-cy:.4f}) (end {cx:.4f} {cy:.4f}) '
             f'(stroke (width 0.05) (type default)) (fill none) (layer '
             f'"F.CrtYd") (uuid "{_u()}"))')
    L.append(')')
    return '\n'.join(L) + '\n'


def toml_text(design, board_name, points=401):
    s = design.spec
    # the EM run's band (GHz): the filter's and its skirts
    kind, f0, fbw = band(s)
    if kind == 'lowpass':
        fmin, fmax = 0.05 * f0 / 1e9, 3.0 * f0 / 1e9
    elif kind == 'bandstop' and s.topology == 'radial':
        f = sweep(s, 2)
        fmin, fmax = f[0] / 1e9, f[-1] / 1e9
    elif s.topology == 'stub':
        fmin, fmax = 0.1 * f0 / 1e9, 1.9 * f0 / 1e9
    else:
        span = max(2.5 * fbw, 0.3)
        fmin, fmax = max(0.1, 1 - span) * f0 / 1e9, (1 + span) * f0 / 1e9
    return '\n'.join([
        f'# kicad2ems run configuration written by dfsynth: {s.kind} / '
        f'{s.topology}, {s.response} order {s.order}',
        f'#   kicad2ems {os.path.splitext(board_name)[0]}.toml --solvers '
        'mom,lines',
        f'board   = "{board_name}"   # paths are relative to this file',
        'ground  = "B.Cu"',
        'nets    = ["RF", "GND"]',
        'mask    = false',
        *([
            f'sigma   = {design.sub.sigma:.4g}   # S/m: conductor loss '
            '(mom25d, the line model)'
        ] if design.sub.sigma and math.isfinite(design.sub.sigma) else []),
        f'fmin    = {fmin:.4f}       # GHz',
        f'fmax    = {fmax:.4f}',
        f'points  = {points}',
        'out     = "out"',
        # (a free-form pattern is no set of lines: the line model has nothing
        # to say of it)
        *([] if s.topology == 'freeform' else ['fast    = "auto"']),
        'bc      = "PML_8"   # FDTD boundaries: the default Mur, 5 mm from a '
        'resonant filter, absorbs its near field',
        '',
        '[solver.openems]',
        'excite  = ["P1"]',
        '',
    ])


def write(design, base, title=''):
    """Writes <base>.kicad_pcb, <base>.toml and <base>.kicad_mod (the filter
    as a footprint, to place in one's own board); returns the first two's
    paths."""
    d = os.path.dirname(os.path.abspath(base))
    os.makedirs(d, exist_ok=True)
    pcb, cfg = base + '.kicad_pcb', base + '.toml'
    with open(pcb, 'w') as fh:
        fh.write(board_text(design, title))
    with open(cfg, 'w') as fh:
        fh.write(toml_text(design, os.path.basename(pcb)))
    with open(base + '.kicad_mod', 'w') as fh:
        fh.write(footprint_text(design, os.path.basename(base)))
    return pcb, cfg
