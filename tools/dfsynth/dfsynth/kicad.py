"""
Writes a Design's layout as a 2-layer KiCad board (KiCad 8 format, opens in KiCad 8 and later) and a
kicad2ems run configuration next to it.

The filter copper is filled polygons on F.Cu (net RF), the ground a filled zone on B.Cu (net GND), the
ports kicad2ems:RF_Port footprints with a Fab text P1 / P2 (rotation = direction into the circuit) and
the stubs' ground vias GND vias. KiCad's DRC reports those vias as shorts to RF: they are meant to be.
"""
import math
import os
import uuid

from .topologies import VIA_DRILL, VIA_SIZE


def _u():
    return str(uuid.uuid4())


def _xy(pts):
    return ' '.join(f'(xy {x:.4f} {y:.4f})' for x, y in pts)


def board_text(design, title=''):
    geo, sub = design.geometry, design.sub
    W, H = geo.W, geo.H
    nets = ['', 'GND', 'RF']
    L = ['(kicad_pcb (version 20240108) (generator "dfsynth") (generator_version "8.0")',
         f'  (general (thickness {sub.h + 2 * sub.t:.4f}) (legacy_teardrops no))',
         '  (paper "A4")',
         f'  (title_block (title "{title}") (comment 1 "dfsynth: {design.spec.kind} / {design.spec.topology}"))',
         '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) (39 "F.Mask" user) '
         '(41 "Cmts.User" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
         '  (setup (stackup',
         f'    (layer "F.Cu" (type "copper") (thickness {sub.t}))',
         f'    (layer "dielectric 1" (type "core") (thickness {sub.h}) (material "{sub.material}") '
         f'(epsilon_r {sub.er}) (loss_tangent {sub.tand}))',
         f'    (layer "B.Cu" (type "copper") (thickness {sub.t}))',
         '    (copper_finish "None") (dielectric_constraints no))',
         '    (pad_to_mask_clearance 0))',
         '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(nets))]
    for poly in geo.polys:
        L.append(f'  (gr_poly (pts {_xy(poly)}) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu") '
                 f'(net 2) (uuid "{_u()}"))')
    for x, y in geo.vias:
        L.append(f'  (via (at {x:.4f} {y:.4f}) (size {VIA_SIZE}) (drill {VIA_DRILL}) (layers "F.Cu" "B.Cu") '
                 f'(net 1) (uuid "{_u()}"))')
    outer = [(0, 0), (W, 0), (W, H), (0, H)]
    L.append(f'  (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{_u()}") (hatch edge 0.5)'
             f' (connect_pads yes (clearance 0)) (min_thickness 0.1) (filled_areas_thickness no)'
             f' (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))'
             f' (polygon (pts {_xy(outer)})) (filled_polygon (layer "B.Cu") (pts {_xy(outer)})))')
    for n, x, y, rot in geo.ports:
        L += [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{_u()}") (at {x:.4f} {y:.4f} {rot})',
              f'    (property "Reference" "TP{n}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{_u()}"))',
              f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{_u()}"))',
              f'    (fp_text user "P{n}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{_u()}"))',
              '  )']
    note = f'{design.spec.kind} {design.spec.topology} {_fmt_f(design.spec.f0)}'
    L.append(f'  (gr_text "{note}" (at 1 {H + 1.5:.3f} 0) (layer "Cmts.User") (uuid "{_u()}") '
             '(effects (font (size 1 1) (thickness 0.15)) (justify left)))')
    L.append(f'  (gr_rect (start 0 0) (end {W:.4f} {H:.4f}) (stroke (width 0.1) (type default)) (fill none) '
             f'(layer "Edge.Cuts") (uuid "{_u()}"))')
    L.append(')')
    return '\n'.join(L) + '\n'


def _fmt_f(f):
    return f'{f / 1e9:.4g} GHz' if f >= 1e9 else f'{f / 1e6:.4g} MHz'


def sweep_range(design):
    """(fmin, fmax) GHz for the EM run: the band and its skirts."""
    from .calibrate import band
    kind, f0, fbw = band(design.spec)
    if kind == 'lowpass':
        return 0.05 * f0 / 1e9, 3.0 * f0 / 1e9
    if kind == 'bandstop' and design.spec.topology == 'radial':
        from .calibrate import sweep
        f = sweep(design.spec, 2)
        return f[0] / 1e9, f[-1] / 1e9
    if design.spec.topology == 'stub':
        return 0.1 * f0 / 1e9, 1.9 * f0 / 1e9
    span = max(2.5 * fbw, 0.3)
    return max(0.1, 1 - span) * f0 / 1e9, (1 + span) * f0 / 1e9


def toml_text(design, board_name, points=401):
    fmin, fmax = sweep_range(design)
    s = design.spec
    return '\n'.join([
        f'# kicad2ems run configuration written by dfsynth: {s.kind} / {s.topology}, {s.response} order {s.order}',
        f'#   kicad2ems {os.path.splitext(board_name)[0]}.toml --solvers mom,lines',
        f'board   = "{board_name}"   # paths are relative to this file',
        'ground  = "B.Cu"',
        'nets    = ["RF", "GND"]',
        'no_mask = true',
        *([f'sigma   = {design.sub.sigma:.4g}   # S/m: conductor loss (mom25d, the line model)']
          if design.sub.sigma and math.isfinite(design.sub.sigma) else []),
        f'fmin    = {fmin:.4f}       # GHz',
        f'fmax    = {fmax:.4f}',
        f'points  = {points}',
        'out     = "out"',
        'fast    = "auto"',
        'bc      = "PML_8"   # FDTD boundaries: the default Mur, 5 mm from a resonant filter, absorbs its near field',
        '',
        '[solver.openems]',
        'excite  = ["P1"]',
        '',
    ])


def write(design, base, title=''):
    """Writes <base>.kicad_pcb and <base>.toml; returns their paths."""
    d = os.path.dirname(os.path.abspath(base))
    os.makedirs(d, exist_ok=True)
    pcb, cfg = base + '.kicad_pcb', base + '.toml'
    with open(pcb, 'w') as fh:
        fh.write(board_text(design, title))
    with open(cfg, 'w') as fh:
        fh.write(toml_text(design, os.path.basename(pcb)))
    return pcb, cfg
