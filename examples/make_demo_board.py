"""
Writes small KiCad 8 test boards (no KiCad needed) and their pick-and-place files:

    python examples/make_demo_board.py OUT [VARIANT]       # OUT.kicad_pcb, OUT-top.pos

  transition (default)  4-layer stackup with solder mask; F.Cu microstrip -> blind via -> In2.Cu
                        stripline; In1.Cu GND plane with an antipad, GND stitching vias to B.Cu
  diff                  2-layer edge-coupled microstrip pair with a 45 degree jog, ports P1+/- P2+/-
                        (run with --ground B.Cu: the board has no ground copper of its own)
  rlc                   lumped-part detection cases (C1 used; DNP, unknown values and odd footprints skipped)
  lowpass / highpass / bandpass
                        1 GHz 3rd-order Butterworth ladders of 0402 parts with ESR / ESL fields, plus
                        OUT_ideal.s2p, the ideal lumped circuit (demo_*.toml overlay it with compare)
  launch                4-layer edge launch: grounded-CPW feed, a taper drawn in the connector
                        footprint, 45 degree opening of the F.Cu pour, inner planes cut away under a
                        connector pad drawn as two pads numbered 1; P2 on the pad at the board edge
  thru                  2-layer lossless 50 ohm microstrip, 30 mm, over a B.Cu plane: a board with a known
                        answer (|S21| ~ 1, delay = length x sqrt(eps_eff) / c; tests/test_conformance.py)

Port footprints 'kicad2ems:RF_Port' carry the Fab text P1 / P2.
"""
import math, sys, uuid
import numpy as np

def u(): return str(uuid.uuid4())

def circle_pts(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]

def make(path, pos_path):
    W, H = 24.0, 10.0                     # board (mm)
    yc = 5.0
    via = (12.0, yc)
    # return-path vias around the signal via, and a GND fence along the stripline (In2.Cu)
    stitch = [(via[0] + 1.3 * math.cos(a), yc + 1.3 * math.sin(a)) for a in (math.radians(d) for d in (45, 135, 225, 315))]
    stitch += [(x, yc + s) for x in [13.5 + 1.2 * i for i in range(9)] for s in (-1.0, 1.0)]
    L = []
    L.append('(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")')
    L.append('  (general (thickness 0.7))')
    L.append('  (paper "A4")')
    L.append('  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)'
             ' (37 "F.SilkS" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))')
    L.append('  (setup (stackup')
    L.append('    (layer "F.SilkS" (type "Top Silk Screen"))')
    L.append('    (layer "F.Mask" (type "Top Solder Mask") (thickness 0.015) (epsilon_r 3.3) (loss_tangent 0.025))')
    L.append('    (layer "F.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    L.append('    (layer "In1.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 2" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    L.append('    (layer "In2.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 3" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    L.append('    (layer "B.Cu" (type "copper") (thickness 0.035))')
    L.append('    (copper_finish "None") (dielectric_constraints no))')
    L.append('    (pad_to_mask_clearance 0))')
    L.append('  (net 0 "") (net 1 "GND") (net 2 "RF")')
    # microstrip on F.Cu, stripline on In2.Cu
    L.append(f'  (segment (start 2.0 {yc}) (end {via[0]} {yc}) (width 0.35) (layer "F.Cu") (net 2) (uuid "{u()}"))')
    L.append(f'  (segment (start {via[0]} {yc}) (end 22.0 {yc}) (width 0.15) (layer "In2.Cu") (net 2) (uuid "{u()}"))')
    L.append(f'  (via blind (at {via[0]} {via[1]}) (size 0.5) (drill 0.25) (layers "F.Cu" "In2.Cu") (net 2) (uuid "{u()}"))')
    for x, y in stitch:
        L.append(f'  (via (at {x:.4f} {y:.4f}) (size 0.5) (drill 0.25) (layers "F.Cu" "B.Cu") (net 1) (uuid "{u()}"))')
    # In1.Cu GND plane with antipad (filled polygon: outer outline + hole as a slit polygon, like KiCad)
    outer = [(0, 0), (W, 0), (W, H), (0, H)]
    hole = circle_pts(via[0], via[1], 0.6)[::-1]
    pts = outer + [outer[0], (via[0] + 0.6, via[1])] + hole + [(via[0] + 0.6, via[1]), outer[0]]
    L.append(f'  (zone (net 1) (net_name "GND") (layer "In1.Cu") (uuid "{u()}") (hatch edge 0.5)')
    L.append('    (connect_pads (clearance 0.2)) (min_thickness 0.2) (filled_areas_thickness no)')
    L.append('    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))')
    L.append(f'    (polygon (pts {" ".join(f"(xy {x} {y})" for x, y in outer)}))')
    L.append(f'    (filled_polygon (layer "In1.Cu") (pts {" ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)})))')
    # port footprints (no copper): Fab text P1/P2, rotation = direction into the circuit
    for ref, num, x, rot, lay in (("TP1", 1, 2.0, 0, None), ("TP2", 2, 22.0, 180, "In2.Cu")):
        L.append(f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x} {yc} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        if lay:
            L.append(f'    (property "port.layer" "{lay}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u()}"))')
        L.append(f'    (fp_text user "P{num}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (fp_line (start -0.5 0) (end 0.5 0) (stroke (width 0.1) (type solid)) (layer "F.Fab") (uuid "{u()}"))')
        L.append('  )')
    L.append(f'  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u()}"))')
    L.append(')')
    open(path, 'w').write("\n".join(L) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions - created by kicad2ems demo ###\n## Unit = mm, Angle = deg.\n## Side : top\n")
        fh.write("# Ref     Val       Package                PosX       PosY       Rot  Side\n")
        fh.write(f"TP1       P1        RF_Port              2.0000    -{yc:.4f}     0.0000  top\n")
        fh.write(f"TP2       P2        RF_Port             22.0000    -{yc:.4f}   180.0000  top\n")

VARIANTS = ('transition', 'diff', 'rlc', 'lowpass', 'highpass', 'bandpass', 'launch', 'thru')
if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] not in VARIANTS:
    sys.exit(f"make_demo_board.py: unknown variant {sys.argv[2]!r} - choose one of {', '.join(VARIANTS)}")
if __name__ == '__main__' and not (len(sys.argv) > 2 and sys.argv[2] in VARIANTS[1:]):
    out = sys.argv[1] if len(sys.argv) > 1 else 'demo_transition'
    make(out + '.kicad_pcb', out + '-top.pos')
    print('wrote', out + '.kicad_pcb', out + '-top.pos')


def make_diff(path, pos_path):
    """2-layer board: edge-coupled microstrip pair (w 0.25, gap 0.2 mm) on F.Cu over B.Cu with a 45 deg
    jog (unequal inner/outer path lengths -> some differential-to-common conversion), and four port
    footprints with Fab text P1+ / P1- / P2+ / P2-."""
    w, s, yc = 0.25, 0.2, 6.0
    off = (w + s) / 2
    L = []
    L.append('(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")')
    L.append('  (general (thickness 0.2))\n  (paper "A4")')
    L.append('  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (39 "F.Mask" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))')
    L.append('  (setup (stackup')
    L.append('    (layer "F.Mask" (type "Top Solder Mask") (thickness 0.015) (epsilon_r 3.3) (loss_tangent 0.025))')
    L.append('    (layer "F.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 1" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    L.append('    (layer "B.Cu" (type "copper") (thickness 0.035))')
    L.append('    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))')
    L.append('  (net 0 "") (net 1 "GND") (net 2 "D_P") (net 3 "D_N")')
    # centre line: (2, yc) -> (9, yc) -> (11, yc - 2) -> (22, yc - 2); each line offset by +-off
    # (a mitred 45-degree jog: the offsets are applied perpendicular to each segment)
    pts = [(2.0, yc), (9.0, yc), (11.0, yc - 2.0), (22.0, yc - 2.0)]
    import math
    def offset_path(sign):
        out = []
        n = len(pts)
        for i in range(n):
            if i == 0 or i == n - 1:
                (x0, y0), (x1, y1) = (pts[0], pts[1]) if i == 0 else (pts[-2], pts[-1])
                dx, dy = x1 - x0, y1 - y0; l = math.hypot(dx, dy)
                nx, ny = -dy / l, dx / l
                x, y = pts[i]
                out.append((x + sign * off * nx, y + sign * off * ny))
            else:
                (xa, ya), (xb, yb), (xc, yc_) = pts[i - 1], pts[i], pts[i + 1]
                d1 = ((xb - xa), (yb - ya)); l1 = math.hypot(*d1); d1 = (d1[0] / l1, d1[1] / l1)
                d2 = ((xc - xb), (yc_ - yb)); l2 = math.hypot(*d2); d2 = (d2[0] / l2, d2[1] / l2)
                n1 = (-d1[1], d1[0]); n2 = (-d2[1], d2[0])
                bis = (n1[0] + n2[0], n1[1] + n2[1]); bl = math.hypot(*bis)
                cosh = (bis[0] * n1[0] + bis[1] * n1[1]) / bl
                k = off / cosh
                out.append((xb + sign * k * bis[0] / bl, yb + sign * k * bis[1] / bl))
        return out
    for net, sign in ((2, -1), (3, +1)):             # KiCad y down: P line at smaller y (top)
        p = offset_path(sign)
        for (x0, y0), (x1, y1) in zip(p[:-1], p[1:]):
            L.append(f'  (segment (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (width {w}) (layer "F.Cu") (net {net}) (uuid "{u()}"))')
    pP, pN = offset_path(-1), offset_path(+1)
    ports = [("TP1", "P1+", pP[0], 0), ("TP2", "P1-", pN[0], 0), ("TP3", "P2+", pP[-1], 180), ("TP4", "P2-", pN[-1], 180)]
    for ref, txt, (x, y), rot in ports:
        L.append(f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (fp_text user "{txt}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )')
    L.append(f'  (gr_rect (start 0 0) (end 24 10) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u()}"))\n)')
    open(path, 'w').write("\n".join(L) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions ###\n## Unit = mm, Angle = deg.\n# Ref Val Package PosX PosY Rot Side\n")
        for ref, txt, (x, y), rot in ports:
            fh.write(f"{ref} {txt} RF_Port {x:.4f} {-y:.4f} {rot:.4f} top\n")


def make_rlc(path, pos_path):
    """2-layer board: 0.35 mm microstrip on F.Cu over a B.Cu GND plane, broken by a 0.5 mm gap that an
    0402 capacitor C1 (1.5 pF, pads on nets RF1 / RF2) bridges; plus R9 (DNP) and L3 (value 'tbd'),
    which are skipped. Ports P1 (x = 2) and P2 (x = 22)."""
    yc, W, H = 5.0, 24.0, 10.0
    L = []
    L.append('(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")')
    L.append('  (general (thickness 0.2))\n  (paper "A4")')
    L.append('  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user) (49 "F.Fab" user))')
    L.append('  (setup (stackup')
    L.append('    (layer "F.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 1" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))')
    L.append('    (layer "B.Cu" (type "copper") (thickness 0.035))')
    L.append('    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))')
    L.append('  (net 0 "") (net 1 "GND") (net 2 "RF1") (net 3 "RF2")')
    L.append(f'  (segment (start 2.0 {yc}) (end 11.52 {yc}) (width 0.35) (layer "F.Cu") (net 2) (uuid "{u()}"))')
    L.append(f'  (segment (start 12.48 {yc}) (end 22.0 {yc}) (width 0.35) (layer "F.Cu") (net 3) (uuid "{u()}"))')
    outer = [(0, 0), (W, 0), (W, H), (0, H)]
    L.append(f'  (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{u()}") (hatch edge 0.5)')
    L.append('    (connect_pads (clearance 0.2)) (min_thickness 0.2) (filled_areas_thickness no)')
    L.append('    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))')
    L.append(f'    (polygon (pts {" ".join(f"(xy {x} {y})" for x, y in outer)}))')
    L.append(f'    (filled_polygon (layer "B.Cu") (pts {" ".join(f"(xy {x} {y})" for x, y in outer)})))')
    for ref, val, x, y, nets, extra in (("C1", "1.5p", 12.0, yc, (2, 3), ""),
                                        ("R9", "100", 12.0, 8.0, (0, 0), " dnp"),
                                        ("L3", "tbd", 6.0, 8.0, (0, 0), "")):
        L.append(f'  (footprint "Capacitor_SMD:C_0402_1005Metric" (layer "F.Cu") (uuid "{u()}") (at {x} {y} 0)')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.2 0) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "{val}" (at 0 1.2 0) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (attr smd{extra})')
        for num, px, net in (("1", -0.48, nets[0]), ("2", 0.48, nets[1])):
            nn = ["", "GND", "RF1", "RF2"][net]
            L.append(f'    (pad "{num}" smd roundrect (at {px} 0) (size 0.56 0.62) (layers "F.Cu" "F.Paste" "F.Mask") '
                     f'(roundrect_rratio 0.25) (net {net} "{nn}") (uuid "{u()}"))')
        L.append('  )')
    ports = [("TP1", "P1", 2.0, 0), ("TP2", "P2", 22.0, 180)]
    for ref, txt, x, rot in ports:
        L.append(f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x} {yc} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (fp_text user "{txt}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )')
    L.append(f'  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u()}"))\n)')
    open(path, 'w').write("\n".join(L) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions ###\n## Unit = mm, Angle = deg.\n# Ref Val Package PosX PosY Rot Side\n")
        for ref, txt, x, rot in ports:
            fh.write(f"{ref} {txt} RF_Port {x:.4f} {-yc:.4f} {rot:.4f} top\n")


# Lumped-element ladder filters (50 ohm, 0402 parts on 0.35 mm microstrip over a B.Cu GND plane), E12
# values. A stage is ('series', part) or ('shunt', [parts]) (shunt parts in parallel, each to a GND pad
# with two vias); a part is (ref, kind 'L' | 'C', value, esr, esl). Inductors carry 0.4 ohm ESR,
# capacitors 0.1 ohm ESR and 0.5 nH ESL (sim.esr / sim.esl fields).
LADDERS = {
    # 3rd-order Butterworth low-pass, fc = 1 GHz: L - C to GND - L (C2's ESL: notch near 2.7 GHz)
    'lowpass': [('series', ('L1', 'L', 8.2e-9, 0.4, None)), ('shunt', [('C2', 'C', 6.8e-12, 0.1, 0.5e-9)]),
                ('series', ('L3', 'L', 8.2e-9, 0.4, None))],
    # 3rd-order Butterworth high-pass, fc = 1 GHz: C - L to GND - C
    'highpass': [('series', ('C1', 'C', 3.3e-12, 0.1, 0.5e-9)), ('shunt', [('L2', 'L', 3.9e-9, 0.4, None)]),
                 ('series', ('C3', 'C', 3.3e-12, 0.1, 0.5e-9))],
    # 2nd-order Butterworth band-pass, f0 = 1 GHz, ~40 % bandwidth: series L-C resonator, then a shunt
    # L || C resonator
    'bandpass': [('series', ('L1', 'L', 27e-9, 0.4, None)), ('series', ('C1', 'C', 1.0e-12, 0.1, 0.5e-9)),
                 ('shunt', [('L2', 'L', 2.2e-9, 0.4, None), ('C2', 'C', 12e-12, 0.1, 0.5e-9)])],
}


def _part_z(kind, val, esr, esl, w):
    z = (esr or 0) + 1j * w * (esl or 0)
    return z + (1j * w * val if kind == 'L' else 1 / (1j * w * val))


def ladder_ideal(ladder, f, z0=50.0):
    """S-parameters (nf, 2, 2) of the ideal lumped circuit (no lines, no pads) for --compare."""
    w = 2 * math.pi * np.asarray(f, float)
    one, zero = np.ones_like(w, complex), np.zeros_like(w, complex)
    A, B, C, D = one, zero, zero, one
    for kind, p in ladder:
        if kind == 'series':
            z = _part_z(*p[1:], w)
            A, B, C, D = A, A * z + B, C, C * z + D
        else:
            y = sum(1 / _part_z(*q[1:], w) for q in p)
            A, B, C, D = A + B * y, B, C + D * y, D
    den = A + B / z0 + C * z0 + D
    S = np.empty((len(w), 2, 2), complex)
    S[:, 0, 0] = (A + B / z0 - C * z0 - D) / den
    S[:, 0, 1] = 2 * (A * D - B * C) / den
    S[:, 1, 0] = 2 / den
    S[:, 1, 1] = (-A + B / z0 - C * z0 + D) / den
    return S


def make_ladder(name, path, pos_path, ideal_path):
    """Board for LADDERS[name]: P1 -> parts -> P2 along y = 5 mm, plus the ideal circuit's S-parameters
    (ideal_path, Touchstone, 0.1-4 GHz)."""
    ladder = LADDERS[name]
    yc, H = 5.0, 10.0
    L, nets = [], ["", "GND", "RF_IN"]
    seg = lambda x0, y0, x1, y1, net: L.append(
        f'  (segment (start {x0:.3f} {y0}) (end {x1:.3f} {y1}) (width 0.35) (layer "F.Cu") (net {net}) (uuid "{u()}"))')
    parts, x, prev, net = [], 6.0, 2.0, 2               # prev: where the current node's trace starts
    for kind, p in ladder:
        if kind == 'series':
            cx = x + 0.5
            seg(prev, yc, cx - 0.48, yc, net)
            nets.append(f"N{len(nets) - 2}")
            parts.append((p, cx, yc, 0, (net, len(nets) - 1)))
            net, prev, x = len(nets) - 1, cx + 0.48, cx + 2.5
        else:
            for q in p:
                cx = x + 0.5
                seg(cx, yc, cx, yc + 0.52, net)
                parts.append((q, cx, yc + 1.0, 270, (net, 1)))
                L.append(f'  (gr_rect (start {cx - 0.6:.3f} {yc + 1.25}) (end {cx + 0.6:.3f} {yc + 2.2}) '
                         f'(stroke (width 0) (type solid)) (fill yes) (layer "F.Cu") (net 1) (uuid "{u()}"))')
                for vx in (cx - 0.3, cx + 0.3):
                    L.append(f'  (via (at {vx:.3f} {yc + 1.85}) (size 0.45) (drill 0.25) (layers "F.Cu" "B.Cu") '
                             f'(net 1) (uuid "{u()}"))')
                x = cx + 1.1
            x += 1.4
    nets[-1] = "RF_OUT"
    W = x + 6.0
    x_out = W - 2.0
    seg(prev, yc, x_out, yc, net)
    head = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
            '  (general (thickness 0.2))\n  (paper "A4")',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
            '  (setup (stackup',
            '    (layer "F.Cu" (type "copper") (thickness 0.035))',
            '    (layer "dielectric 1" (type "core") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))',
            '    (layer "B.Cu" (type "copper") (thickness 0.035))',
            '    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))',
            '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(nets))]
    L[:0] = head
    outer = [(0, 0), (W, 0), (W, H), (0, H)]
    L.append(f'  (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{u()}") (hatch edge 0.5)')
    L.append('    (connect_pads (clearance 0.2)) (min_thickness 0.2) (filled_areas_thickness no)')
    L.append('    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))')
    L.append(f'    (polygon (pts {" ".join(f"(xy {a:.3f} {b})" for a, b in outer)}))')
    L.append(f'    (filled_polygon (layer "B.Cu") (pts {" ".join(f"(xy {a:.3f} {b})" for a, b in outer)})))')
    fmt = lambda v, k: f"{v * 1e9:g}nH" if k == 'L' else f"{v * 1e12:g}p"
    for (ref, kind, val, esr, esl), cx, cy, rot, pn in parts:
        lib = "Inductor_SMD:L_0402_1005Metric" if kind == 'L' else "Capacitor_SMD:C_0402_1005Metric"
        L.append(f'  (footprint "{lib}" (layer "F.Cu") (uuid "{u()}") (at {cx:.3f} {cy} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.1 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "{fmt(val, kind)}" (at 0 1.1 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        for k, v in (('sim.esr', f"{esr:g}" if esr else None), ('sim.esl', f"{esl * 1e9:g}nH" if esl else None)):
            if v:
                L.append(f'    (property "{k}" "{v}" (at 0 0 {rot}) (layer "F.Fab") (hide yes) (uuid "{u()}"))')
        L.append('    (attr smd)')
        for num, px, n in (("1", -0.48, pn[0]), ("2", 0.48, pn[1])):
            L.append(f'    (pad "{num}" smd roundrect (at {px} 0 {rot}) (size 0.56 0.62) (layers "F.Cu" "F.Paste" "F.Mask") '
                     f'(roundrect_rratio 0.25) (net {n} "{nets[n]}") (uuid "{u()}"))')
        L.append('  )')
    ports = [("TP1", "P1", 2.0, 0), ("TP2", "P2", x_out, 180)]
    for ref, txt, px, rot in ports:
        L.append(f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {px:.3f} {yc} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (fp_text user "{txt}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )')
    L.append(f'  (gr_rect (start 0 0) (end {W:.3f} {H}) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u()}"))\n)')
    open(path, 'w').write("\n".join(L) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions ###\n## Unit = mm, Angle = deg.\n# Ref Val Package PosX PosY Rot Side\n")
        for ref, txt, px, rot in ports:
            fh.write(f"{ref} {txt} RF_Port {px:.4f} {-yc:.4f} {rot:.4f} top\n")
    f = np.linspace(0.1e9, 4e9, 391)
    S = ladder_ideal(ladder, f)
    with open(ideal_path, 'w') as fh:
        fh.write(f"! ideal lumped circuit of the demo {name} filter (with ESR / ESL)\n# Hz S RI R 50\n")
        for fi, s_ in zip(f, S):
            fh.write(f"{fi:.6e} " + " ".join(f"{v.real:.9e} {v.imag:.9e}" for v in (s_[0, 0], s_[1, 0], s_[0, 1], s_[1, 1])) + "\n")


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] in LADDERS:
    make_ladder(sys.argv[2], sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos', sys.argv[1] + '_ideal.s2p')
    print('wrote', sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos', sys.argv[1] + '_ideal.s2p')


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] == 'rlc':
    make_rlc(sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')
    print('wrote', sys.argv[1] + '.kicad_pcb')


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] == 'diff':
    make_diff(sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')
    print('wrote', sys.argv[1] + '.kicad_pcb')


def make_launch(path, pos_path):
    """4-layer edge launch: a 0.32 mm grounded-CPW feed on F.Cu (F.Cu GND pour, In1.Cu reference
    0.21 mm below) into an edge connector's signal pad, drawn as two pads numbered 1 (an oval and a
    rectangle), with a taper drawn as copper in the connector footprint. The F.Cu pour opens out at
    45 degrees and In1.Cu / In2.Cu are cut away under the launch, so the pad references B.Cu 1.49 mm
    below. P2 sits on the pad near the board edge, where the connector pin lands."""
    W, H, yc = 17.0, 10.0, 5.0
    gap, wt = 0.25, 0.32
    hw = wt / 2 + gap                                     # half-width of the F.Cu clearance along the feed
    L = []
    L.append('(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")')
    L.append('  (general (thickness 1.6))')
    L.append('  (paper "A4")')
    L.append('  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)'
             ' (37 "F.SilkS" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))')
    L.append('  (setup (stackup')
    L.append('    (layer "F.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))')
    L.append('    (layer "In1.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))')
    L.append('    (layer "In2.Cu" (type "copper") (thickness 0.035))')
    L.append('    (layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))')
    L.append('    (layer "B.Cu" (type "copper") (thickness 0.035))')
    L.append('    (copper_finish "None") (dielectric_constraints no))')
    L.append('    (pad_to_mask_clearance 0))')
    L.append('  (net 0 "") (net 1 "GND") (net 2 "RF")')
    L.append(f'  (segment (start 2.0 {yc}) (end 16.0 {yc}) (width {wt}) (layer "F.Cu") (net 2) (uuid "{u()}"))')
    # F.Cu GND pour: CPW gap along the feed, opening at 45 degrees from x = 10 to a 2.41 mm half-width
    fcu = [(0, 0), (W, 0), (W, yc - 2.41), (12.0, yc - 2.41), (10.0, yc - hw), (1.5, yc - hw), (1.5, yc + hw),
           (10.0, yc + hw), (12.0, yc + 2.41), (W, yc + 2.41), (W, H), (0, H)]
    # In1.Cu / In2.Cu GND planes cut away under the launch, open to the board edge; B.Cu solid
    inner = [(0, 0), (W, 0), (W, yc - 3.0), (9.9, yc - 3.0), (9.9, yc + 3.0), (W, yc + 3.0), (W, H), (0, H)]
    full = [(0, 0), (W, 0), (W, H), (0, H)]
    for lay, pts in (('F.Cu', fcu), ('In1.Cu', inner), ('In2.Cu', inner), ('B.Cu', full)):
        L.append(f'  (zone (net 1) (net_name "GND") (layer "{lay}") (uuid "{u()}") (hatch edge 0.5)')
        L.append('    (connect_pads (clearance 0.2)) (min_thickness 0.2) (filled_areas_thickness no)')
        L.append('    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))')
        L.append(f'    (polygon (pts {" ".join(f"(xy {x} {y})" for x, y in pts)}))')
        L.append(f'    (filled_polygon (layer "{lay}") (pts {" ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)})))')
    for x in [3.0 + 1.5 * i for i in range(5)]:           # GND stitching beside the feed
        for s in (-1.0, 1.0):
            L.append(f'  (via (at {x} {yc + s}) (size 0.45) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "{u()}"))')
    # the edge connector: signal pad 1 = oval + rectangle (one pad), GND pads above and below
    L.append(f'  (footprint "kicad2ems:Edge_SMA" (layer "F.Cu") (uuid "{u()}") (at 15.5 {yc} 0)')
    L.append(f'    (property "Reference" "J1" (at 0 -4 0) (layer "F.SilkS") (uuid "{u()}"))')
    L.append(f'    (property "Value" "Edge_SMA" (at 0 4 0) (layer "F.Fab") (uuid "{u()}"))')
    L.append(f'    (pad "1" smd rect (at 0.25 0) (size 2.5 1.6) (layers "F.Cu") (net 2 "RF") (uuid "{u()}"))')
    L.append(f'    (pad "1" smd oval (at -1.25 0) (size 1.6 1.6) (layers "F.Cu") (net 2 "RF") (uuid "{u()}"))')
    for y in (-3.5, 3.5):
        L.append(f'    (pad "2" smd rect (at 0.25 {y}) (size 2.5 1.5) (layers "F.Cu") (net 1 "GND") (uuid "{u()}"))')
    # a taper drawn in the footprint (copper, no pad): 0.32 mm at x = 11.5 widening to 1.2 mm into the oval
    taper = [(-4.0, -wt / 2), (-1.9, -0.6), (-1.9, 0.6), (-4.0, wt / 2)]
    L.append(f'    (fp_poly (pts {" ".join(f"(xy {x} {y})" for x, y in taper)}) (stroke (width 0) (type solid)) (fill solid)'
             f' (layer "F.Cu") (uuid "{u()}"))')
    L.append('  )')
    for ref, num, x, rot in (("TP1", 1, 2.0, 0), ("TP2", 2, 16.5, 180)):
        L.append(f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x} {yc} {rot})')
        L.append(f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))')
        L.append(f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append(f'    (fp_text user "P{num}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))')
        L.append('  )')
    L.append(f'  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{u()}"))')
    L.append(')')
    open(path, 'w').write("\n".join(L) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions - created by kicad2ems demo ###\n## Unit = mm, Angle = deg.\n## Side : top\n")
        fh.write("# Ref     Val       Package                PosX       PosY       Rot  Side\n")
        fh.write(f"J1        Edge_SMA  Edge_SMA            15.5000    -{yc:.4f}     0.0000  top\n")
        fh.write(f"TP1       P1        RF_Port              2.0000    -{yc:.4f}     0.0000  top\n")
        fh.write(f"TP2       P2        RF_Port             16.5000    -{yc:.4f}   180.0000  top\n")


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] == 'launch':
    make_launch(sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')
    print('wrote', sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')


THRU = dict(h=0.2, er=4.3, w=0.38, length=30.0)          # mm; w: ~50 ohm on h = 0.2 mm, er 4.3 (zero thickness)


def make_thru(path, pos_path):
    """2-layer board, lossless dielectric (tan d = 0): one straight microstrip on F.Cu over a B.Cu zone covering the
    board, ports P1 / P2 at its ends."""
    h, er, w, L = THRU['h'], THRU['er'], THRU['w'], THRU['length']
    W, H, x0, yc = L + 4.0, 10.0, 2.0, 5.0
    s = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
         f'  (general (thickness {h}))\n  (paper "A4")',
         '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
         '  (setup (stackup',
         '    (layer "F.Cu" (type "copper") (thickness 0.035))',
         f'    (layer "dielectric 1" (type "core") (thickness {h}) (material "ideal") (epsilon_r {er}) (loss_tangent 0))',
         '    (layer "B.Cu" (type "copper") (thickness 0.035))',
         '    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))',
         '  (net 0 "") (net 1 "GND") (net 2 "RF")',
         f'  (segment (start {x0} {yc}) (end {x0 + L} {yc}) (width {w}) (layer "F.Cu") (net 2) (uuid "{u()}"))',
         f'  (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "{u()}") (hatch edge 0.5) (connect_pads (clearance 0))'
         f' (min_thickness 0.1) (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))'
         f' (polygon (pts (xy 0 0) (xy {W} 0) (xy {W} {H}) (xy 0 {H})))'
         f' (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy {W} 0) (xy {W} {H}) (xy 0 {H}))))']
    ports = [("TP1", "P1", x0, 0), ("TP2", "P2", x0 + L, 180)]
    for ref, txt, x, rot in ports:
        s += [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x} {yc} {rot})',
              f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))',
              f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))',
              f'    (fp_text user "{txt}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )']
    s.append(f'  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts") '
             f'(uuid "{u()}"))\n)')
    open(path, 'w').write("\n".join(s) + "\n")
    with open(pos_path, 'w') as fh:
        fh.write("### Footprint positions ###\n## Unit = mm, Angle = deg.\n# Ref Val Package PosX PosY Rot Side\n")
        for ref, txt, x, rot in ports:
            fh.write(f"{ref} {txt} RF_Port {x:.4f} {-yc:.4f} {rot:.4f} top\n")


if __name__ == '__main__' and len(sys.argv) > 2 and sys.argv[2] == 'thru':
    make_thru(sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')
    print('wrote', sys.argv[1] + '.kicad_pcb', sys.argv[1] + '-top.pos')
