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

    python examples/make_rf_examples.py all             # every board, examples/rf_<name>.kicad_pcb
    python examples/make_rf_examples.py coupler /tmp/c  # one board at /tmp/c.kicad_pcb
    kicad2ems examples/rf_coupler.toml --preview

Ports are kicad2ems:RF_Port footprints (rotation = direction into the circuit), 0.5 mm inside the board
edge where the feed lines end.
"""
import math, os, sys, uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from kicad2ems.solvers.lines.tlines import microstrip, cpwg, stripline as strip_z   # noqa: E402
from kicad2ems.solvers.lines.coupled import coupled_microstrip           # noqa: E402

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

    def plane(self, layer, W, H, net=1, holes=()):
        """A filled ground zone over the board with round antipads [(x, y, r)] (cut in through slits, as KiCad
        fills them)."""
        outer = [(0, 0), (W, 0), (W, H), (0, H)]
        pts = list(outer) + [outer[0]]
        for x, y, r in holes:
            ring = [(x + r * math.cos(-2 * math.pi * i / 32), y + r * math.sin(-2 * math.pi * i / 32)) for i in range(32)]
            pts += [(x + r, y)] + ring + [(x + r, y), outer[0]]
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
        L = [f'  (footprint "Resistor_SMD:R_0805_2012Metric" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
             f'    (property "Reference" "{ref}" (at 0 -1.6 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
             f'    (property "Value" "{value}" (at 0 1.6 {rot}) (layer "F.Fab") (uuid "{u()}"))',
             '    (attr smd)']
        for num, px in (("1", -pitch / 2), ("2", pitch / 2)):
            L.append(f'    (pad "{num}" smd roundrect (at {px} 0 {rot}) (size {pad[0]} {pad[1]}) (layers "F.Cu" "F.Paste" "F.Mask") '
                     f'(roundrect_rratio 0.25) (net {net} "{self.nets[net]}") (uuid "{u()}"))')
        self.items += L + ['  )']

    def write(self, path, W, H, ground=True, four=False):
        """four: a 4-layer board (F.Cu, In1.Cu, In2.Cu, B.Cu, H4 mm of FR-4 between them) instead of 0.8 mm
        2-layer."""
        if ground:
            self.rect(0, 0, W, H, layer='B.Cu', net=1)
        cu = ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'] if four else ['F.Cu', 'B.Cu']
        h = H4 if four else H_SUB
        ids = {'F.Cu': 0, 'In1.Cu': 1, 'In2.Cu': 2, 'B.Cu': 31}
        stack = []
        for i, c in enumerate(cu):
            stack.append(f'    (layer "{c}" (type "copper") (thickness 0.035))')
            if i < len(cu) - 1:
                stack.append(f'    (layer "dielectric {i + 1}" (type "core") (thickness {h}) (material "FR4") '
                             f'(epsilon_r {ER}) (loss_tangent {TAND}))')
        L = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
             f'  (general (thickness {h * (len(cu) - 1)}))\n  (paper "A4")',
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
    from mom25d.sim import coupled_stripline_cohn
    wm = round(solve(lambda w: -coupled_microstrip(w, s, H4, ER)[1], -50.0, 0.05, 2.0), 3)
    ws = round(solve(lambda w: -coupled_stripline_cohn(w * 1e-3, s * 1e-3, 2 * H4 * 1e-3, ER)[1], -50.0, 0.03, 2.0), 3)
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


KINDS = dict(branchline=branchline, wilkinson=wilkinson, steplpf=steplpf, stubnotch=stubnotch, gcpw=gcpw,
             coupler=coupler, ratrace=ratrace, radialstub=radialstub, stripline=stripline, diffvia=diffvia)

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in list(KINDS) + ['all']:
        sys.exit(f"usage: make_rf_examples.py {{{','.join(KINDS)},all}} [out_base]")
    here = os.path.dirname(os.path.abspath(__file__))
    names = list(KINDS) if sys.argv[1] == 'all' else [sys.argv[1]]
    for name in names:
        out = sys.argv[2] if len(sys.argv) > 2 and len(names) == 1 else os.path.join(here, f"rf_{name}")
        info = KINDS[name](out + '.kicad_pcb')
        print('wrote', out + '.kicad_pcb', '' if info is None else info)
