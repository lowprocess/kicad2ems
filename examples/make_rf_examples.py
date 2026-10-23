"""
Writes KiCad 8 test boards of classic RF structures for kicad2ems (no KiCad needed), sized with the
package's own line formulas for 0.8 mm FR-4 (er 4.4, tan d 0.02), B.Cu ground:

  branchline   3 dB 90-degree branch-line hybrid, 2.4 GHz: 35.4 ohm series and 50 ohm shunt quarter-wave arms,
               P1 in, P2 through (-90 deg), P3 coupled (-180 deg), P4 isolated
  wilkinson    equal-split Wilkinson divider, 2.4 GHz: 70.7 ohm quarter-wave arms, 100 ohm 0805 resistor
               between the outputs; P1 in, P2 / P3 out
  steplpf      5-pole Butterworth stepped-impedance low-pass, 2 GHz: 100 ohm / 20 ohm sections (L-C-L-C-L)
  stubnotch    band-stop of two open quarter-wave stubs a quarter wave apart, 2.4 GHz
  gcpw         grounded coplanar waveguide through line (50 ohm), F.Cu ground pours stitched to B.Cu by via
               fences along both gaps
  coupler      10 dB edge-coupled quarter-wave directional coupler, 2.4 GHz: P1 in, P2 through, P3 coupled
               (the near end), P4 isolated
  ratrace      rat-race (180-degree) hybrid ring, 2.4 GHz: 70.7 ohm ring 1.5 wavelengths round
  radialstub   band-stop of one 60-degree radial open stub on a 50 ohm line
  stripline    4-layer: 50 ohm stripline between ground planes, fenced by vias on both sides
  diffvia      4-layer: a 100 ohm differential pair from microstrip through two vias to stripline
  shielded     a half-wave resonator side-coupled to two lines on RO4003C, for a shielding can
  siw          substrate-integrated waveguide on RO4003C (TE10 cut-off 6 GHz), microstrip tapers at both ends
  msvia        4-layer: microstrip on F.Cu through one via to microstrip on B.Cu, four return vias
  ms2cpw       microstrip to grounded CPW and back (ground pours closing in, via fences)
  slotline     two microstrip-to-slotline transitions back to back (radial stubs, round slot cavities)
  marchand     4-layer broadside-coupled Marchand balun, 2.4 GHz: P1 in, P2+ / P2- the balanced output
  bends        square, mitred and curved right-angle bends (P1 / P2, P3 / P4, P5 / P6)
  serpentine   a serpentine delay line
  crosstalk    two parallel lines, P1 driven (P3 near end, P4 far end); crosstalk_guard: a grounded guard
               trace between them
  pcoupled     3-pole parallel-coupled-line band-pass, 2.4 GHz, 5 %
  combline     3-pole combline band-pass, 2.4 GHz: rods shorted by vias, loaded by 0603 capacitors
  openloop     two open-loop square resonators, electric coupling, tapped feeds
  dgs          a dumbbell defected ground structure under a 50 ohm line
  ebg          4-layer: a microstrip over a mushroom EBG (patches via'd to the ground)
  idc          an interdigital capacitor in series with a 50 ohm line
  meander      a meander inductor in series with a 50 ohm line
  spiral       4-layer: a square spiral inductor with an underpass

    python examples/make_rf_examples.py all             # every board, examples/rf_<name>.kicad_pcb
    python examples/make_rf_examples.py coupler /tmp/c  # one board at /tmp/c.kicad_pcb
    kicad2ems examples/rf_coupler.toml --preview

Ports are kicad2ems:RF_Port footprints (rotation = direction into the circuit), 0.5 mm inside the board
edge where the feed lines end.
"""
import math, os, sys, uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from kicad2ems.lines.tlines import microstrip, cpwg, stripline as strip_z   # noqa: E402
from kicad2ems.lines.coupled import coupled_microstrip, coupled_stripline   # noqa: E402

H_SUB, ER, TAND, F0 = 0.8, 4.4, 0.02, 2.4e9
H4 = 0.2                                # mm of FR-4 between the layers of the 4-layer boards
C0 = 299792458.0


def u():
    return str(uuid.uuid4())


def solve(fn, target, lo, hi, n=80):
    """x in [lo, hi] with fn(x) = target (bisection; fn monotonic)."""
    flo = fn(lo) - target
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        fm = fn(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def width(z0):
    """Microstrip width (mm) for z0 ohm on the substrate."""
    return round(solve(lambda w: microstrip(w, H_SUB, ER)[0], z0, 0.02, 20.0), 3)


def quarter(w, f=F0):
    """A quarter wavelength (mm) of microstrip of width w at f."""
    return round(C0 / (f * math.sqrt(microstrip(w, H_SUB, ER)[1])) / 4 * 1e3, 3)


W50 = width(50.0)


def _signed_area(pts):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))


class Board:
    def __init__(self, nets=("", "GND", "RF")):
        self.items, self.nets = [], list(nets)

    def track(self, pts, w, layer='F.Cu', net=2):
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            self.items.append(f'  (segment (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (width {w}) '
                              f'(layer "{layer}") (net {net}) (uuid "{u()}"))')

    def rect(self, x0, y0, x1, y1, layer='F.Cu', net=2):
        self.items.append(f'  (gr_rect (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (stroke (width 0) (type solid)) '
                          f'(fill yes) (layer "{layer}") (net {net}) (uuid "{u()}"))')

    def via(self, x, y, net=1, size=0.6, drill=0.3, layers=('F.Cu', 'B.Cu')):
        kind = '' if layers == ('F.Cu', 'B.Cu') else ' blind'
        self.items.append(f'  (via{kind} (at {x:.4f} {y:.4f}) (size {size}) (drill {drill}) (layers "{layers[0]}" "{layers[1]}") '
                          f'(net {net}) (uuid "{u()}"))')

    def poly(self, pts, layer='F.Cu', net=2):
        xy = ' '.join(f'(xy {x:.4f} {y:.4f})' for x, y in pts)
        self.items.append(f'  (gr_poly (pts {xy}) (stroke (width 0) (type solid)) (fill yes) (layer "{layer}") (net {net}) '
                          f'(uuid "{u()}"))')

    def plane(self, layer, W, H, net=1, holes=(), outer=None):
        """A filled ground zone over the board (or the polygon `outer`) with holes: round antipads (x, y, r) or
        polygons [(x, y), ...] (cut in through slits, as KiCad fills them)."""
        outer = outer or [(0, 0), (W, 0), (W, H), (0, H)]
        pts = list(outer) + [outer[0]]
        for h in holes:
            if len(h) == 3 and not isinstance(h[0], (tuple, list)):
                x, y, r = h
                ring = [(x + r * math.cos(-2 * math.pi * i / 32), y + r * math.sin(-2 * math.pi * i / 32))
                        for i in range(32)]
            else:
                ring = list(h)
            if _signed_area(ring) * _signed_area(outer) > 0:   # a hole runs the other way round, or it fills in
                ring = ring[::-1]
            pts += [ring[0]] + ring + [ring[0], outer[0]]
        xy = lambda ps: ' '.join(f'(xy {a:.4f} {b:.4f})' for a, b in ps)
        self.items.append(f'  (zone (net {net}) (net_name "{self.nets[net]}") (layer "{layer}") (uuid "{u()}") (hatch edge 0.5)'
                          f' (connect_pads (clearance 0)) (min_thickness 0.1) (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))'
                          f' (polygon (pts {xy(outer)})) (filled_polygon (layer "{layer}") (pts {xy(pts)})))')

    def port(self, n, x, y, rot, layer=None):
        """A port footprint: n is 1, 2, ... or a name such as '1+' (a differential leg); layer: port.layer."""
        ref = str(n).replace('+', 'P').replace('-', 'N')
        L = [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
             f'    (property "Reference" "TP{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
             f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))']
        if layer:
            L.append(f'    (property "port.layer" "{layer}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u()}"))')
        self.items += L + [f'    (fp_text user "P{n}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )']

    def resistor(self, ref, value, x, y, rot, net=2, pitch=2.0, pad=(1.0, 1.25)):
        """A two-pad SMD resistor (0805-like) centred at x, y, pads `pitch` mm apart along its rotation."""
        self.part(ref, value, x, y, rot, net, pitch, pad, 'Resistor_SMD:R_0805_2012Metric')

    def part(self, ref, value, x, y, rot, net=2, pitch=2.0, pad=(1.0, 1.25), fp='Resistor_SMD:R_0805_2012Metric',
             nets=None):
        """A two-pad SMD part (R / L / C by its reference) centred at x, y, pads `pitch` mm apart along its
        rotation; nets: the two pads' nets (default both `net`)."""
        nets = nets or (net, net)
        L = [f'  (footprint "{fp}" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
             f'    (property "Reference" "{ref}" (at 0 -1.6 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
             f'    (property "Value" "{value}" (at 0 1.6 {rot}) (layer "F.Fab") (uuid "{u()}"))',
             '    (attr smd)']
        for num, px, n in (("1", -pitch / 2, nets[0]), ("2", pitch / 2, nets[1])):
            L.append(f'    (pad "{num}" smd roundrect (at {px} 0 {rot}) (size {pad[0]} {pad[1]}) (layers "F.Cu" "F.Paste" "F.Mask") '
                     f'(roundrect_rratio 0.25) (net {n} "{self.nets[n]}") (uuid "{u()}"))')
        self.items += L + ['  )']

    def write(self, path, W, H, ground=True, four=False, sub=None, stack=None):
        """four: a 4-layer board (F.Cu, In1.Cu, In2.Cu, B.Cu, H4 mm of FR-4 between them, or the three thicknesses
        `stack`) instead of 0.8 mm 2-layer. sub: (h, er, tand, material) of the substrate instead of the FR-4."""
        if ground:
            self.rect(0, 0, W, H, layer='B.Cu', net=1)
        four = four or stack is not None
        cu = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'] if four else ['F.Cu', 'B.Cu']
        h = H4 if four else H_SUB
        er, tand, mat = ER, TAND, 'FR4'
        if sub:
            h, er, tand, mat = sub
        hs = list(stack) if stack is not None else [h] * (len(cu) - 1)
        ids = {'F.Cu': 0, 'In1.Cu': 1, 'In2.Cu': 2, 'B.Cu': 31}
        stack = []
        for i, c in enumerate(cu):
            stack.append(f'    (layer "{c}" (type "copper") (thickness 0.035))')
            if i < len(cu) - 1:
                stack.append(f'    (layer "dielectric {i + 1}" (type "core") (thickness {hs[i]}) (material "{mat}") '
                             f'(epsilon_r {er}) (loss_tangent {tand}))')
        L = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
             f'  (general (thickness {sum(hs):g}))\n  (paper "A4")',
             '  (layers ' + ' '.join(f'({ids[c]} "{c}" signal)' for c in cu) +
             ' (37 "F.SilkS" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
             '  (setup (stackup'] + stack + [
             '    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))',
             '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(self.nets))]
        L += self.items
        L.append(f'  (gr_rect (start 0 0) (end {W:.4f} {H:.4f}) (stroke (width 0.1) (type default)) (fill none) '
                 f'(layer "Edge.Cuts") (uuid "{u()}"))\n)')
        with open(path, 'w') as fh:
            fh.write("\n".join(L) + "\n")


def branchline(path, feed=6.0):
    w35 = width(50 / math.sqrt(2))
    a, b = quarter(w35), quarter(W50)                         # series arms (along x), shunt arms (along y)
    m = 3.0
    W, H = a + 2 * feed, b + 2 * m + W50
    x0, x1, y0, y1 = feed, feed + a, m + W50 / 2, m + W50 / 2 + b
    bd = Board()
    bd.track([(x0, y0), (x1, y0)], w35)
    bd.track([(x0, y1), (x1, y1)], w35)
    bd.track([(x0, y0), (x0, y1)], W50)
    bd.track([(x1, y0), (x1, y1)], W50)
    for x, y, xe in ((x0, y0, 0), (x1, y0, W), (x1, y1, W), (x0, y1, 0)):
        bd.track([(x, y), (xe, y)], W50)
    for n, (x, y, rot) in enumerate(((0.5, y0, 0), (W - 0.5, y0, 180), (W - 0.5, y1, 180), (0.5, y1, 0)), 1):
        bd.port(n, x, y, rot)
    bd.write(path, W, H)


def wilkinson(path, feed=6.0, d=4.0, node=1.0, out_dy=5.0):
    w70 = width(50 * math.sqrt(2))
    q = quarter(w70)
    xj, yc = feed, 10.0
    xr = xj + q - d - (d - node)                              # arm: up d, along, down (d - node)
    xo = xr + 2.5                                             # where the outputs turn away from each other
    W, H = xo + feed, 2 * yc
    bd = Board()
    bd.track([(0, yc), (xj, yc)], W50)
    for s in (-1, 1):
        bd.track([(xj, yc), (xj, yc + s * d), (xr, yc + s * d), (xr, yc + s * node)], w70)
        bd.track([(xr, yc + s * node), (xo, yc + s * node), (xo, yc + s * out_dy), (W, yc + s * out_dy)], W50)
    bd.resistor('R1', '100', xr, yc, 90, pitch=2 * node)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc - out_dy, 180)
    bd.port(3, W - 0.5, yc + out_dy, 180)
    bd.write(path, W, H)


def steplpf(path, fc=2.0e9, zh=100.0, zl=20.0, feed=5.0):
    g = [0.618, 1.618, 2.0, 1.618, 0.618]                     # Butterworth, N = 5, L first
    wh, wl = width(zh), width(zl)
    secs = []
    for k, gk in enumerate(g):
        w = wh if k % 2 == 0 else wl
        bl = gk * 50 / zh if k % 2 == 0 else gk * zl / 50     # electrical length (rad) at fc
        beta = 2 * math.pi * fc * math.sqrt(microstrip(w, H_SUB, ER)[1]) / C0 * 1e-3   # rad / mm
        secs.append((w, round(bl / beta, 3)))
    yc = wl / 2 + 3.0
    W, H = 2 * feed + sum(l for _, l in secs), 2 * yc
    bd = Board()
    x = feed
    bd.track([(0, yc), (x, yc)], W50)
    for w, l in secs:
        bd.rect(x, yc - w / 2, x + l, yc + w / 2)
        x += l
    bd.track([(x, yc), (W, yc)], W50)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)
    return secs


def stubnotch(path, feed=6.0, rects=False):
    """rects: the stubs drawn as filled rectangles overlapping the line instead of tracks (the line model's
    regression case: a shape crossing a track is a tee and a stub, not a wide section of the line)."""
    q = quarter(W50)
    yc = q + W50 / 2 + 3.0
    xs = (feed + W50, feed + W50 + q)                         # stub centres, a quarter wave apart
    W, H = xs[1] + W50 + feed, yc + W50 / 2 + 3.0
    bd = Board()
    bd.track([(0, yc), (W, yc)], W50)
    for x in xs:                                              # open stubs up from the line (y down in KiCad)
        if rects:                                             # from the line's far edge to q beyond its near edge
            bd.rect(x - W50 / 2, yc - W50 / 2 - q, x + W50 / 2, yc + W50 / 2)
        else:                                                 # tracks: the round end cap reaches q beyond the edge
            bd.track([(x, yc), (x, yc - q)], W50)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)


def gcpw(path, w=1.0, length=24.0, pitch=1.2):
    s = round(solve(lambda s: -cpwg(w, s, H_SUB, ER)[0], -50.0, 0.05, 3.0), 3)   # Z0 grows with the gap
    H, yc = 12.0, 6.0
    W = length
    bd = Board()
    bd.track([(0, yc), (W, yc)], w)
    ga, gb = yc - w / 2 - s, yc + w / 2 + s                   # the ground pours' inner edges
    bd.rect(0, 0, W, ga, net=1)
    bd.rect(0, gb, W, H, net=1)
    n = int((W - 2.0) / pitch)
    for k in range(n + 1):
        x = 1.0 + k * (W - 2.0) / n
        bd.via(x, ga - 0.6)
        bd.via(x, gb + 0.6)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)
    return s


def coupler(path, c_db=10.0, feed=5.0, spread=4.0):
    k = 10 ** (-c_db / 20)
    ze_t, zo_t = 50 * math.sqrt((1 + k) / (1 - k)), 50 * math.sqrt((1 - k) / (1 + k))
    best = None                                               # (w, s) with Ze, Zo on target (grid + refine)
    for w in [0.6 + 0.02 * i for i in range(80)]:
        s = solve(lambda s: coupled_microstrip(w, s, H_SUB, ER)[0], ze_t, 0.02, 3.0)   # Ze falls as s grows
        zo = coupled_microstrip(w, s, H_SUB, ER)[1]
        if best is None or abs(zo - zo_t) < best[0]:
            best = (abs(zo - zo_t), w, s)
    _, w, s = best
    w, s = round(w, 3), round(s, 3)
    ze, zo, ee, eo = coupled_microstrip(w, s, H_SUB, ER)
    lc = round(C0 / (F0 * math.sqrt((ee + eo) / 2)) / 4 * 1e3, 3)
    yc = spread + W50 + 2.0
    W, H = 2 * feed + 2 * spread + lc, 2 * yc
    xa, xb = feed + spread, feed + spread + lc
    bd = Board()
    for sgn in (-1, 1):
        yl = yc + sgn * (s + w) / 2
        yo = yc + sgn * ((s + w) / 2 + spread)
        bd.track([(0, yo), (feed, yo)], W50)
        bd.track([(xb + spread, yo), (W, yo)], W50)
        # 45-degree bends into the coupled section at the coupled width: a wider track's round end cap would
        # reach across the gap and short the lines
        bd.track([(feed, yo), (xa, yl), (xb, yl), (xb + spread, yo)], w)
    ytop, ybot = yc - ((s + w) / 2 + spread), yc + ((s + w) / 2 + spread)
    bd.port(1, 0.5, ytop, 0)
    bd.port(2, W - 0.5, ytop, 180)
    bd.port(3, 0.5, ybot, 0)
    bd.port(4, W - 0.5, ybot, 180)
    bd.write(path, W, H)
    return w, s, ze, zo, lc


def ratrace(path, feed=6.0, n=96):
    """Rat-race (180-degree hybrid) ring, 2.4 GHz: a 70.7 ohm ring 1.5 guided wavelengths round, P1..P4 a quarter
    wave apart (60 degrees of the ring), 3/4 wave from P4 back to P1. From P1: P2 and P4 -3 dB, P3 isolated."""
    w70 = width(50 * math.sqrt(2))
    r = 1.5 * 4 * quarter(w70) / (2 * math.pi)
    cx, cy = feed + r, feed + r * math.sin(math.radians(60))
    W, H = 2 * (feed + r), cy + r + 3.0
    bd = Board()
    ring = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]
    bd.track(ring, w70)
    at = {a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in (0, -60, -120, 180)}
    bd.track([at[0], (W, cy)], W50)                           # P1 right, P2 / P3 up (y down in KiCad), P4 left
    bd.track([at[-60], (at[-60][0], 0)], W50)
    bd.track([at[-120], (at[-120][0], 0)], W50)
    bd.track([at[180], (0, cy)], W50)
    bd.port(1, W - 0.5, cy, 180)
    bd.port(2, at[-60][0], 0.5, 270)
    bd.port(3, at[-120][0], 0.5, 270)
    bd.port(4, 0.5, cy, 0)
    bd.write(path, W, H)
    return r


def radialstub(path, feed=8.0, angle=60.0, frac=0.75, n=24):
    """Band-stop of one radial (fan-shaped) open stub on a 50 ohm line: angle degrees wide, radius frac x a
    straight quarter wave at 2.4 GHz (a radial stub resonates shorter than a straight one). The line model
    sees the fan only as copper widening the line: a case for the field solvers."""
    r = round(frac * quarter(W50), 3)
    yc = r + 3.0
    W, H = 2 * feed + 2 * r * math.sin(math.radians(angle / 2)) + W50, yc + W50 / 2 + 3.0
    xs = W / 2
    bd = Board()
    bd.track([(0, yc), (W, yc)], W50)
    a0, a1 = math.radians(-90 - angle / 2), math.radians(-90 + angle / 2)   # opening up from the line (y down)
    fan = [(xs, yc)] + [(xs + r * math.cos(a0 + (a1 - a0) * k / n), yc + r * math.sin(a0 + (a1 - a0) * k / n))
                        for k in range(n + 1)]
    bd.poly(fan)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)
    return r


def stripline(path, length=24.0, gap=1.0, pitch=1.2):
    """A 50 ohm stripline on In1.Cu between ground planes on F.Cu and In2.Cu (B.Cu ground too), fenced by vias
    through every plane gap mm from each edge, pitch mm apart: the fence keeps the parallel-plate mode off."""
    b = 2 * H4
    w = round(solve(lambda w: -strip_z(w, b, ER)[0], -50.0, 0.03, 2.0), 3)
    H, yc = 8.0, 4.0
    bd = Board()
    bd.track([(0, yc), (length, yc)], w, layer='In1.Cu')
    for layer in ('F.Cu', 'In2.Cu'):
        bd.plane(layer, length, H)
    n = int((length - 2.0) / pitch)
    for k in range(n + 1):
        x = 1.0 + k * (length - 2.0) / n
        for s in (-1, 1):
            bd.via(x, yc + s * (w / 2 + gap), size=0.5, drill=0.25)
    bd.port(1, 0.5, yc, 0, layer='In1.Cu')
    bd.port(2, length - 0.5, yc, 180, layer='In1.Cu')
    bd.write(path, length, H, four=True)
    return w


def diffvia(path, length=24.0, s=0.2, pitch=1.0, antipad=0.55):
    """A 100 ohm differential pair from F.Cu microstrip (over the In1.Cu plane) through two blind vias to In2.Cu
    stripline (between In1.Cu and B.Cu), the pair fanned out to the via pitch, antipads round the vias in In1.Cu
    and four return vias beside them. Ports P1+ / P1- on F.Cu, P2+ / P2- on In2.Cu."""
    wm = round(solve(lambda w: -coupled_microstrip(w, s, H4, ER)[1], -50.0, 0.05, 2.0), 3)
    ws = round(solve(lambda w: -coupled_stripline(w, s, 2 * H4, ER)[1], -50.0, 0.03, 2.0), 3)
    H, yc, xv = 8.0, 4.0, length / 2
    bd = Board(nets=("", "GND", "D_P", "D_N"))
    for sgn, net in ((-1, 2), (1, 3)):
        ym, ys, yv = yc + sgn * (wm + s) / 2, yc + sgn * (ws + s) / 2, yc + sgn * pitch / 2
        bd.track([(0, ym), (xv - 1.5, ym), (xv - 1.0, yv), (xv, yv)], wm, net=net)
        bd.track([(xv, yv), (xv + 1.0, yv), (xv + 1.5, ys), (length, ys)], ws, layer='In2.Cu', net=net)
        bd.via(xv, yv, net=net, size=0.5, drill=0.25, layers=('F.Cu', 'In2.Cu'))
        for dx in (-0.9, 0.9):                                # return vias beside the pair, clear of both layers' tracks
            bd.via(xv + dx, yc + sgn * (pitch / 2 + 0.9), size=0.5, drill=0.25)
    bd.plane('In1.Cu', length, H, holes=[(xv, yc + sg * pitch / 2, antipad) for sg in (-1, 1)])
    for leg, sgn in (('+', -1), ('-', 1)):
        bd.port(f'1{leg}', 0.5, yc + sgn * (wm + s) / 2, 0)
        bd.port(f'2{leg}', length - 0.5, yc + sgn * (ws + s) / 2, 180, layer='In2.Cu')
    bd.write(path, length, H, four=True)
    return wm, ws


RO4003 = (0.508, 3.55, 0.0027, 'RO4003C')


def shielded(path, f0=5.0e9, gap=1.0, feed=5.0, margin=4.0):
    """A half-wave resonator side-coupled to two 50 ohm lines on 0.508 mm RO4003C, made to sit in a shielding
    can: the input line couples to the resonator's first half, the output line to its second half, through
    `gap`. A narrow band-pass (loaded Q about 130) whose smallest feature is the 1 mm gap."""
    h, er = RO4003[:2]
    w = round(solve(lambda w: microstrip(w, h, er)[0], 50.0, 0.05, 5.0), 3)
    lr = round(C0 / (f0 * math.sqrt(microstrip(w, h, er)[1])) / 2 * 1e3, 2)      # half a guided wavelength, mm
    q = lr / 2
    pitch = w + gap
    W, H = 2 * feed + lr + q, 2 * margin + 3 * w + 2 * gap
    yc = H / 2
    x0 = feed                                                 # the resonator from x0 to x0 + lr
    bd = Board()
    bd.track([(0, yc - pitch), (x0 + q, yc - pitch)], w)      # input: along the resonator's first half
    bd.track([(x0, yc), (x0 + lr, yc)], w)                    # the resonator: RF too, so the port nets select it
    bd.track([(x0 + q, yc + pitch), (W, yc + pitch)], w)      # output: along its second half
    bd.port(1, 0.5, yc - pitch, 0)
    bd.port(2, W - 0.5, yc + pitch, 180)
    bd.write(path, W, H, sub=RO4003)
    return w, lr


def w_on(z0, h, er=ER):
    """Microstrip width (mm) for z0 ohm on h mm of er."""
    return round(solve(lambda w: microstrip(w, h, er)[0], z0, 0.02, 20.0), 3)


def _holes_union(shapes):
    """The outline (points) of the union of shapely shapes: one hole in a ground plane."""
    from shapely.ops import unary_union
    u = unary_union(shapes).buffer(0)
    return list(u.exterior.coords)[:-1]


def siw(path, fc=6.0e9, d=0.6, p=1.0, ls=30.0, lt=10.0, wt=6.0, feed=5.0):
    """Substrate-integrated waveguide on 0.508 mm RO4003C: two rows of d mm vias p mm apart (p / d 1.7, below the
    leakage limit of 2) between the F.Cu top wall and the B.Cu ground, a wide enough for a TE10 cut-off at fc (the
    equivalent width a_eff = c / (2 fc sqrt(er)), the via rows a_eff + d^2 / (0.95 p) apart), fed from 50 ohm
    microstrip through lt mm linear tapers to wt mm. Pass band above fc, up to the TE20 cut-off at 2 fc."""
    h, er = RO4003[:2]
    w50 = w_on(50.0, h, er)
    aeff = C0 / (2 * fc * math.sqrt(er)) * 1e3
    a = round(aeff + d * d / (0.95 * p), 3)
    W, H = 2 * feed + 2 * lt + ls, a + 12.0
    yc, x0 = H / 2, feed + lt
    x1 = x0 + ls
    bd = Board()
    bd.rect(x0, yc - a / 2 - d / 2, x1, yc + a / 2 + d / 2)             # the top wall over the via rows
    for xa, xb, sgn in ((feed, x0, 1), (x1 + lt, x1, -1)):
        bd.poly([(xa, yc - w50 / 2), (xb, yc - wt / 2), (xb, yc + wt / 2), (xa, yc + w50 / 2)])
    bd.track([(0, yc), (feed, yc)], w50)
    bd.track([(x1 + lt, yc), (W, yc)], w50)
    n = int(round(ls / p))
    for k in range(n):
        x = x0 + p / 2 + k * ls / n
        for y in (yc - a / 2, yc + a / 2):
            bd.via(x, y, net=2, size=d + 0.3, drill=d)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H, sub=RO4003)
    return a, w50


def msvia(path, length=24.0, antipad=0.55, ret=1.0):
    """4-layer: a 50 ohm microstrip on F.Cu (over the In1.Cu plane) through one via to a 50 ohm microstrip on B.Cu
    (under the In2.Cu plane), antipads round the via in both planes and four ground vias 1 mm round it carrying
    the return current from one plane to the other. P1 on F.Cu, P2 on B.Cu."""
    w = w_on(50.0, H4)
    H, yc, xv = 8.0, 4.0, length / 2
    bd = Board()
    bd.track([(0, yc), (xv, yc)], w)
    bd.track([(xv, yc), (length, yc)], w, layer='B.Cu')
    bd.via(xv, yc, net=2, size=0.5, drill=0.25)
    for k in range(4):
        a = math.radians(45 + 90 * k)
        bd.via(xv + ret * math.cos(a), yc + ret * math.sin(a), size=0.5, drill=0.25)
    for layer in ('In1.Cu', 'In2.Cu'):
        bd.plane(layer, length, H, holes=[(xv, yc, antipad)])
    bd.port(1, 0.5, yc, 0)
    bd.port(2, length - 0.5, yc, 180, layer='B.Cu')
    bd.write(path, length, H, ground=False, four=True)
    return w


def ms2cpw(path, feed=8.0, lt=4.0, lc=12.0, s0=3.0, pitch=1.2):
    """Microstrip to grounded CPW and back: a 50 ohm strip throughout, F.Cu ground pours closing in from s0 mm to
    the CPW gap (50 ohm with the bottom ground, for the same strip) over lt mm tapers, stitched to B.Cu by via
    fences along the CPW section and the tapers."""
    w = W50
    s = round(solve(lambda g: -cpwg(w, g, H_SUB, ER)[0], -50.0, 0.05, 3.0), 3)
    L, H = 2 * feed + 2 * lt + lc, 16.0
    yc = H / 2
    xa, xb, xc, xd = feed, feed + lt, feed + lt + lc, L - feed
    bd = Board()
    bd.track([(0, yc), (L, yc)], w)
    for sg in (-1, 1):
        e0, e1 = yc + sg * (w / 2 + s0), yc + sg * (w / 2 + s)    # the pour's inner edge: wide, then the CPW gap
        edge = yc + sg * H / 2
        bd.poly([(xa, edge), (xd, edge), (xd, e0), (xc, e1), (xb, e1), (xa, e0)], net=1)
        n = int((xc - xb) / pitch)
        for k in range(n + 1):
            bd.via(xb + k * (xc - xb) / n, e1 + sg * 0.6)
        for xs, xe in ((xa, xb), (xd, xc)):                      # along the tapers, 0.6 mm inside the edge
            for k in range(1, 3):
                t = k / 3
                bd.via(xs + t * (xe - xs), e0 + t * (e1 - e0) + sg * 0.6)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, L - 0.5, yc, 180)
    bd.write(path, L, H)
    return s


def slotline(path, ws=0.3, ls=20.0, rc=3.0, rs=5.0, fan=90.0, feed=10.0):
    """Two microstrip-to-slotline transitions back to back: a ws mm slotline in the B.Cu ground, ls mm between the
    crossings; at each, a 50 ohm F.Cu strip comes down from the top edge, crosses the slot and ends in a radial
    stub (rs, fan degrees), and the slot ends past it in a round cavity (rc). The stub and the cavity make each
    crossing a wideband balun; the slotline in between is balanced."""
    from shapely.geometry import Point, box
    w = W50
    xa, W = 6.0 + 2 * rc + 1.0, None
    xb = xa + ls
    W = xb + 2 * rc + 7.0
    yc = feed + 2.0
    H = yc + rs + 6.0
    bd = Board()
    cav = [Point(xa - rc - 0.5, yc).buffer(rc, 32), Point(xb + rc + 0.5, yc).buffer(rc, 32)]
    hole = _holes_union(cav + [box(xa - rc - 0.5, yc - ws / 2, xb + rc + 0.5, yc + ws / 2)])
    bd.plane('B.Cu', W, H, holes=[hole])
    t = math.radians(fan / 2)
    for x, sgn in ((xa, 1), (xb, -1)):
        bd.rect(x - w / 2, 0, x + w / 2, yc + ws / 2 + 0.6)
        ya = yc + ws / 2 + 0.6                                     # the stub's apex, just past the slot
        bd.poly([(x - w / 2, ya), (x + w / 2, ya)] +
                [(x + rs * math.sin(a), ya + rs * math.cos(a)) for a in (t - 2 * t * k / 24 for k in range(25))])
    bd.port(1, xa, 0.5, 270)
    bd.port(2, xb, 0.5, 270)
    bd.write(path, W, H, ground=False)
    return W, H


def marchand(path, L=16.0, wt=0.3, wb=1.0, gap=1.0, feed=6.0, pair_s=0.3, drop=6.0):
    """Broadside-coupled Marchand balun, 2.4 GHz, 4 layers (F.Cu, In1.Cu 0.2 mm below it, the In2.Cu ground 0.4 mm
    below In1, B.Cu ground stitched to it): the input line on F.Cu runs over two quarter-wave In1 strips in turn
    and ends open; each In1 strip is grounded at its outer end (vias to In2 / B.Cu) and its inner end is an
    output. The outputs drop as a coupled pair on In1 to the bottom edge: P2+ / P2- (100 ohm differential), P1
    the 50 ohm input on F.Cu."""
    W = 2 * feed + 2 * L + gap
    H = 24.0
    yc = 8.0
    xa = feed                                                    # section A: xa .. xa + L, B: xb .. xb + L
    xb = xa + L + gap
    bd = Board(nets=("", "GND", "RF", "OUT_P", "OUT_N"))
    w50 = 1.0                                                    # 50 ohm-ish on F.Cu, 0.6 mm over In2
    bd.track([(0, yc), (xa, yc)], w50)
    bd.rect(xa, yc - wt / 2, xb + L, yc + wt / 2)                # the top strip over both sections, open at its end
    xo = xa + L + gap / 2                                        # the outputs' centre line
    for x0, x1, net, sg in ((xa, xa + L, 3, -1), (xb, xb + L, 4, 1)):
        bd.rect(x0, yc - wb / 2, x1, yc + wb / 2, layer='In1.Cu', net=net)
        xg = x0 if sg < 0 else x1                                # the grounded (outer) end
        for dy in (-wb / 3, wb / 3):
            bd.via(xg + (0.35 if sg < 0 else -0.35), yc + dy, net=net, size=0.5, drill=0.25,
                   layers=('In1.Cu', 'B.Cu'))
        xi = x1 if sg < 0 else x0                                # the inner end: the output, down to the edge
        xp = xo + sg * (pair_s / 2 + 0.25)
        bd.track([(xi + sg * 0.4, yc), (xp, yc + 2.0), (xp, H)], 0.5, layer='In1.Cu', net=net)   # from inside the strip
    bd.plane('In2.Cu', W, H)
    for x in (2.0, W - 2.0):
        for y in (2.0, H - 2.0):
            bd.via(x, y, size=0.6, drill=0.3, layers=('In2.Cu', 'B.Cu'))
    bd.port(1, 0.5, yc, 0)
    for leg, sg in (('+', -1), ('-', 1)):
        bd.port(f'2{leg}', xo + sg * (pair_s / 2 + 0.25), H - 0.5, 90, layer='In1.Cu')
    bd.write(path, W, H, stack=(0.2, 0.4, 0.2))
    return W, H


def bends(path, sep=10.0, H=48.0):
    """Three 50 ohm lines, each one right-angle bend from the left edge to the bottom edge: square (P1 / P2),
    mitred (P3 / P4, the optimum mitre of Douville and James: m = 52 + 65 exp(-1.35 w / h) %) and curved
    (P5 / P6, a 3 w centre-line radius)."""
    w = W50
    m = 0.52 + 0.65 * math.exp(-1.35 * w / H_SUB)
    W = H
    bd = Board()
    corners = [(W - 8.0, 6.0), (W - 8.0 - sep, 6.0 + sep), (W - 8.0 - 2 * sep, 6.0 + 2 * sep)]
    h = w / 2
    for k, (xk, yk) in enumerate(corners):
        if k == 0:
            bd.poly([(0, yk - h), (xk + h, yk - h), (xk + h, H), (xk - h, H), (xk - h, yk + h), (0, yk + h)])
        elif k == 1:
            c = 2 * m * w                                        # the cut's legs along the outer edges
            bd.poly([(0, yk - h), (xk + h - c, yk - h), (xk + h, yk - h + c), (xk + h, H), (xk - h, H),
                     (xk - h, yk + h), (0, yk + h)])
        else:
            R = 3 * w
            cx, cy = xk - R, yk + R                              # the arc's centre
            outer = [(cx + (R + h) * math.sin(a), cy - (R + h) * math.cos(a)) for a in
                     (math.pi / 2 * i / 24 for i in range(25))]
            inner = [(cx + (R - h) * math.sin(a), cy - (R - h) * math.cos(a)) for a in
                     (math.pi / 2 * i / 24 for i in range(24, -1, -1))]
            bd.poly([(0, yk - h)] + outer + [(xk + h, H), (xk - h, H)] + inner + [(0, yk + h)])
        bd.port(2 * k + 1, 0.5, yk, 0)
        bd.port(2 * k + 2, xk, H - 0.5, 90)
    bd.write(path, W, H)
    return m


def serpentine(path, n=6, amp=8.0, pitch=4.5, feed=5.0):
    """A 50 ohm delay line meandering n times across amp mm, pitch mm between runs (3 w apart: little coupling
    between them), ports at both ends; its delay against its length shows what the coupled turns take off."""
    w = W50
    W = 2 * feed + (n - 1) * pitch
    yt, yb = 4.0, 4.0 + amp
    H = yb + 4.0
    pts = [(0, yt), (feed, yt)]
    for k in range(n):
        x = feed + k * pitch
        y0, y1 = (yt, yb) if k % 2 == 0 else (yb, yt)
        pts += [(x, y0), (x, y1)]
        if k < n - 1:
            pts.append((x + pitch, y1))
    yend = pts[-1][1]
    pts.append((W, yend))
    bd = Board()
    bd.track(pts, w)
    bd.port(1, 0.5, yt, 0)
    bd.port(2, W - 0.5, yend, 180)
    bd.write(path, W, H)
    return sum(math.dist(a, b) for a, b in zip(pts[:-1], pts[1:]))


def crosstalk(path, length=40.0, gap=3.0, guard=False, gw=0.8, vpitch=4.0):
    """Two parallel 50 ohm lines, length mm, gap mm apart (edge to edge): near-end (P3) and far-end (P4)
    crosstalk from P1 (P2 the through). guard: a grounded gw mm guard trace between them, stitched to B.Cu every
    vpitch mm and at its ends."""
    w = W50
    H = 2 * w + gap + 12.0
    yc = H / 2
    ya, yb = yc - (gap + w) / 2, yc + (gap + w) / 2
    bd = Board()
    bd.track([(0, ya), (length, ya)], w)
    bd.track([(0, yb), (length, yb)], w)
    if guard:
        x0, x1 = 2.0, length - 2.0
        bd.track([(x0, yc), (x1, yc)], gw, net=1)
        n = int(round((x1 - x0) / vpitch))
        for k in range(n + 1):
            bd.via(x0 + k * (x1 - x0) / n, yc, size=min(0.6, gw), drill=0.3)
    bd.port(1, 0.5, ya, 0)
    bd.port(2, length - 0.5, ya, 180)
    bd.port(3, 0.5, yb, 0)
    bd.port(4, length - 0.5, yb, 180)
    bd.write(path, length, H)


def crosstalk_guard(path):
    return crosstalk(path, guard=True)


def pcoupled(path, f0=2.4e9, fbw=0.05, feed=6.0):
    """Parallel-coupled-line band-pass, 3 poles, 0.1 dB Chebyshev, f0, fbw: four quarter-wave coupled sections
    (even / odd impedances from the admittance inverters, gaps and widths from the coupled-line formulas), each
    resonator half in one section and half in the next, staggered."""
    g = [1.0, 1.0316, 1.1474, 1.0316, 1.0]
    n = 3
    J = [math.sqrt(math.pi * fbw / (2 * g[0] * g[1]))]
    J += [math.pi * fbw / (2 * math.sqrt(g[i] * g[i + 1])) for i in range(1, n)]
    J.append(math.sqrt(math.pi * fbw / (2 * g[n] * g[n + 1])))
    secs = []
    for j in J:
        ze, zo = 50 * (1 + j + j * j), 50 * (1 - j + j * j)
        best = None
        for w in [0.6 + 0.02 * i for i in range(80)]:
            s_ = solve(lambda s: coupled_microstrip(w, s, H_SUB, ER)[0], ze, 0.02, 4.0)
            if not 0.03 < s_ < 3.9:                              # Ze out of reach at this width
                continue
            e = abs(coupled_microstrip(w, s_, H_SUB, ER)[1] - zo)
            if best is None or e < best[0]:
                best = (e, w, s_)
        _, w, s_ = best
        w, s_ = round(w, 3), round(s_, 3)
        ze_, zo_, ee, eo = coupled_microstrip(w, s_, H_SUB, ER)
        l = round(C0 / (f0 * math.sqrt((ee + eo) / 2)) / 4 * 1e3 - 0.3, 3)   # less ~ the open end's extension
        secs.append((w, s_, l))
    W = 2 * feed + sum(l for _, _, l in secs)
    # resonator j's half in section j-1 sits on its lower edge, its half in section j has its upper edge at that
    # plus the wider of the two widths (as dfsynth's coupled filter): every gap exactly s; y from the input strip
    rects, x, e, wprev = [], feed, 0.0, secs[0][0]
    for w, s_, l in secs:
        top = e + max(wprev, w)
        rects.append((x, top - w, x + l, top))
        e = top + s_
        rects.append((x, e, x + l, e + w))
        x, wprev = x + l, w
    y_in, y_out = secs[0][0] - W50 / 2, e + W50 / 2
    m = 5.0
    dy = m + W50 - y_in                                          # 5 mm below the input strip's feed
    H = y_out - y_in + W50 + 2 * m
    bd = Board()
    for x0, y0, x1, y1 in rects:
        bd.rect(x0, y0 + dy, x1, y1 + dy)
    bd.track([(0, y_in + dy), (feed, y_in + dy)], W50)
    bd.track([(x, y_out + dy), (W, y_out + dy)], W50)
    bd.port(1, 0.5, y_in + dy, 0)
    bd.port(2, W - 0.5, y_out + dy, 180)
    bd.write(path, W, H)
    return secs


def combline(path, f0=2.4e9, theta=45.0, wr=1.0, gaps=(0.7, 0.7), tap=1.8, cap='0.4p', cap_end='0.6p', feed=6.0):
    """Combline band-pass, 3 poles: rods wr mm wide, theta degrees long at f0, shorted to ground through two vias
    at their bottom ends, loaded at their top ends by a cap (0603) to a grounded pad; the rods `gaps` apart; 50 ohm
    taps tap mm up the outer rods. The loading capacitors make the rods resonate well short of a quarter wave; the
    outer rods (one neighbour, a tap) take cap_end (default cap) to resonate with the middle one."""
    L = round(theta / 360 * C0 / (f0 * math.sqrt(microstrip(wr, H_SUB, ER)[1])) * 1e3, 3)
    xs = [feed + wr / 2]
    for g_ in gaps:
        xs.append(xs[-1] + wr + g_)
    W = xs[-1] + wr / 2 + feed
    ybot = 4.0                                                   # the rods' shorted ends
    ytop = ybot + L
    H = ytop + 7.0
    bd = Board(nets=("", "GND", "RF", "R1", "R2", "R3"))
    for k, x in enumerate(xs):
        net = 3 + k
        bd.rect(x - wr / 2, ybot - 0.8, x + wr / 2, ytop + 0.6, net=net)
        for dx in (-wr / 4, wr / 4):
            bd.via(x + dx, ybot - 0.4, net=net, size=0.45, drill=0.25)
        yc = ytop + 1.6                                          # the 0603: pads 1.6 mm apart, the top one to ground
        bd.rect(x - 0.5, ytop + 2.0, x + 0.5, ytop + 3.4, net=1)
        bd.via(x, ytop + 3.0, size=0.5, drill=0.3)
        c = cap if k == 1 or not cap_end else cap_end
        bd.part(f'C{k + 1}', c, x, yc, 90, pitch=1.6, pad=(0.8, 0.9), fp='Capacitor_SMD:C_0603_1608Metric',
                nets=(1, net))
    yt = ybot + tap
    bd.track([(0, yt), (xs[0] - wr / 2, yt)], W50, net=3)
    bd.track([(xs[-1] + wr / 2, yt), (W, yt)], W50, net=5)
    bd.port(1, 0.5, yt, 0)
    bd.port(2, W - 0.5, yt, 180)
    bd.write(path, W, H)
    return L


def openloop(path, a=9.3, w=1.0, g=1.0, s=0.6, tap=3.0, feed=6.0):
    """Two square open-loop resonators (side a, strip w, a g mm gap in the side facing the other loop, perimeter
    about half a guided wavelength at 2.4 GHz), s mm apart: their open ends face each other (electric coupling).
    50 ohm feeds tapped onto the outer sides, tap mm from the loops' centre line."""
    W = 2 * feed + 2 * a + w + s
    H = a + 16.0
    y0 = 8.0                                                    # the loops' top edges
    bd = Board()
    for k, x0 in enumerate((feed, feed + a + w + s)):           # s mm between the loops' edges
        x1 = x0 + a
        gx = x1 if k == 0 else x0                               # the side with the gap
        ox = x0 if k == 0 else x1                               # the outer side
        ym = y0 + a / 2
        h = w / 2
        bd.rect(x0 - h, y0 - h, x1 + h, y0 + h)                 # top
        bd.rect(x0 - h, y0 + a - h, x1 + h, y0 + a + h)         # bottom
        bd.rect(ox - h, y0 - h, ox + h, y0 + a + h)             # the outer side
        bd.rect(gx - h, y0 - h, gx + h, ym - g / 2)             # the gap side, open g mm in its middle
        bd.rect(gx - h, ym + g / 2, gx + h, y0 + a + h)
        yt = ym + tap
        xe = ox - w / 2 if k == 0 else ox + w / 2
        bd.track([(0 if k == 0 else W, yt), (xe, yt)], W50)
        bd.port(k + 1, 0.5 if k == 0 else W - 0.5, yt, 0 if k == 0 else 180)
    bd.write(path, W, H)


def dgs(path, a=5.0, g=0.4, dl=3.0, length=30.0):
    """A dumbbell defected ground structure under a 50 ohm line: two a x a mm squares cut from the B.Cu ground,
    one each side of the line, joined by a g mm wide slot across under it (dl mm long between the squares). The
    squares add inductance, the slot capacitance: a low-pass with a stop band, with no copper on the top side."""
    W, H = length, 2 * a + dl + 10.0
    yc, xc = H / 2, length / 2
    from shapely.geometry import box
    hole = _holes_union([box(xc - a / 2, yc - dl / 2 - a, xc + a / 2, yc - dl / 2),
                         box(xc - a / 2, yc + dl / 2, xc + a / 2, yc + dl / 2 + a),
                         box(xc - g / 2, yc - dl / 2 - 0.01, xc + g / 2, yc + dl / 2 + 0.01)])
    bd = Board()
    bd.track([(0, yc), (W, yc)], W50)
    bd.plane('B.Cu', W, H, holes=[hole])
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H, ground=False)


def ebg(path, patch=5.0, gap=0.1, cols=6, rows=3, feed=5.0):
    """Mushroom EBG under a microstrip: 4 layers, the 50 ohm line on F.Cu, 0.2 mm above rows x cols patches on
    In1.Cu (patch mm, gap mm apart) in a window of the In1.Cu ground (under the feeds, fenced to B.Cu round the
    window), each patch tied to the B.Cu ground 1.6 mm below by a via through In2.Cu (empty);
    the patches' LC (gap capacitance, via loop inductance) blocks a band where the line sees them resonate."""
    pitch = patch + gap
    W = 2 * feed + cols * pitch
    H = rows * pitch + 10.0
    yc = H / 2
    w = w_on(50.0, 0.2)
    bd = Board()
    y0 = yc - rows * pitch / 2 + gap / 2
    x0, x1, ya, yb = feed - gap / 2, W - feed + gap / 2, y0 - gap, y0 + rows * pitch    # the patches' window
    bd.plane('In1.Cu', W, H, holes=[[(x0, ya), (x1, ya), (x1, yb), (x0, yb)]])   # ground under the feeds
    o = 0.6                                                      # a via fence round the window, In1.Cu to B.Cu
    for xa, ya_, xb, yb_ in ((x0 - o, ya - o, x1 + o, ya - o), (x0 - o, yb + o, x1 + o, yb + o),
                             (x0 - o, ya - o, x0 - o, yb + o), (x1 + o, ya - o, x1 + o, yb + o)):
        n = max(1, int(round(math.hypot(xb - xa, yb_ - ya_) / 1.0)))
        for k in range(n + (1 if xa == xb else 0)):
            if xa == xb and k in (0, n):
                continue                                         # the corners: on the horizontal rows
            t = k / n
            x, y = xa + t * (xb - xa), ya_ + t * (yb_ - ya_)
            if abs(y - yc) < 1.0:
                continue                                         # not under the line
            bd.via(x, y, size=0.5, drill=0.3, layers=('In1.Cu', 'B.Cu'))
    for r in range(rows):
        for c in range(cols):
            x = feed + c * pitch + gap / 2
            y = y0 + r * pitch
            bd.rect(x, y, x + patch, y + patch, layer='In1.Cu', net=1)
            bd.via(x + patch / 2, y + patch / 2, size=0.5, drill=0.3, layers=('In1.Cu', 'B.Cu'))
    bd.track([(0, yc), (W, yc)], w)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H, stack=(0.2, 0.8, 0.8))
    return w


def idc(path, n=8, wf=0.25, gf=0.2, lf=3.0, ge=0.2, feed=8.0):
    """Interdigital capacitor in series with a 50 ohm line: n fingers wf mm wide, gf mm apart, lf mm long,
    alternately from either bus bar, ge mm from the opposite bar. A series C (and the fingers' inductance)."""
    w = W50
    span = n * wf + (n - 1) * gf
    W = 2 * feed + 2 * w + lf + ge
    H = span + 10.0
    yc = H / 2
    xa, xb = feed + w, feed + w + lf + ge                       # the bus bars' inner edges
    bd = Board()
    bd.track([(0, yc), (feed, yc)], w)
    bd.track([(xb + w, yc), (W, yc)], w)
    bd.rect(feed, yc - span / 2, xa, yc + span / 2)
    bd.rect(xb, yc - span / 2, xb + w, yc + span / 2)
    for k in range(n):
        y = yc - span / 2 + k * (wf + gf)
        if k % 2 == 0:
            bd.rect(xa, y, xa + lf, y + wf)
        else:
            bd.rect(xb - lf, y, xb, y + wf)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)


def meander(path, n=5, wl=0.3, pitch=0.9, amp=4.0, feed=8.0):
    """Meander inductor in series with a 50 ohm line: a wl mm strip meandering n times across amp mm, pitch mm
    between runs. The opposite currents of neighbouring runs cancel part of each other's inductance."""
    w = W50
    W = 2 * feed + (n - 1) * pitch + 2.0
    H = amp + 12.0
    yc = H / 2
    yt, yb = yc - amp / 2, yc + amp / 2
    x0 = feed + 1.0
    pts = [(feed, yc), (x0, yc), (x0, yt)]
    for k in range(n):
        x = x0 + k * pitch
        y0, y1 = (yt, yb) if k % 2 == 0 else (yb, yt)
        pts += [(x, y0), (x, y1)]
        if k < n - 1:
            pts.append((x + pitch, y1))
    xe = pts[-1][0]
    pts += [(xe, yc), (xe + 1.0, yc)]
    bd = Board()
    bd.track([(0, yc), (feed, yc)], w)
    bd.track(pts, wl)
    bd.track([(xe + 1.0, yc), (W, yc)], w)
    bd.port(1, 0.5, yc, 0)
    bd.port(2, W - 0.5, yc, 180)
    bd.write(path, W, H)


def spiral(path, turns=3, ws=0.25, ss=0.25, dout=5.0, feed=8.0):
    """Square spiral inductor, 4 layers (0.2 mm each): turns of a ws mm strip ss mm apart on F.Cu, dout mm across,
    its inner end brought out under the turns on In1.Cu through two vias (an underpass); the ground on B.Cu only,
    0.6 mm below. In series between 50 ohm lines (over that ground)."""
    w = w_on(50.0, 3 * H4)
    p = ws + ss
    W = 2 * feed + dout + 4.0
    H = dout + 12.0
    xc, yc = W / 2, H / 2
    r = dout / 2 - ws / 2                                        # the outer turn's centre line
    pts = [(feed, yc + r), (xc - r, yc + r)]                     # in from the left along the bottom, then clockwise
    for k in range(turns):
        q = r - k * p
        pts += [(xc - q, yc - q), (xc + q, yc - q), (xc + q, yc + q), (xc - q + p, yc + q)]
    pts.append((xc - r + turns * p, yc - r + turns * p))         # up into the centre
    xi, yi = pts[-1]
    xo = W - feed
    bd = Board()
    bd.track([(0, yc + r), (feed, yc + r)], w)
    bd.track(pts, ws)
    for x in (xi, xo):                                           # the underpass on In1.Cu, out under the turns
        bd.via(x, yi, net=2, size=0.45, drill=0.2, layers=('F.Cu', 'In1.Cu'))
    bd.track([(xi, yi), (xo, yi)], ws, layer='In1.Cu')
    bd.track([(xo, yi), (W, yi)], w)
    bd.port(1, 0.5, yc + r, 0)
    bd.port(2, W - 0.5, yi, 180)
    bd.write(path, W, H, four=True)
    return w


KINDS = dict(branchline=branchline, wilkinson=wilkinson, steplpf=steplpf, stubnotch=stubnotch, gcpw=gcpw,
             coupler=coupler, ratrace=ratrace, radialstub=radialstub, stripline=stripline, diffvia=diffvia,
             shielded=shielded, siw=siw, msvia=msvia, ms2cpw=ms2cpw, slotline=slotline, marchand=marchand, bends=bends,
             serpentine=serpentine, crosstalk=crosstalk, crosstalk_guard=crosstalk_guard, pcoupled=pcoupled,
             combline=combline, openloop=openloop, dgs=dgs, ebg=ebg, idc=idc, meander=meander, spiral=spiral)

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in list(KINDS) + ['all']:
        sys.exit(f"usage: make_rf_examples.py {{{','.join(KINDS)},all}} [out_base]")
    here = os.path.dirname(os.path.abspath(__file__))
    names = list(KINDS) if sys.argv[1] == 'all' else [sys.argv[1]]
    for name in names:
        out = sys.argv[2] if len(sys.argv) > 2 and len(names) == 1 else os.path.join(here, f"rf_{name}")
        info = KINDS[name](out + '.kicad_pcb')
        print('wrote', out + '.kicad_pcb', '' if info is None else info)
