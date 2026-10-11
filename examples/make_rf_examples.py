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

    python examples/make_rf_examples.py all             # every board, examples/rf_<name>.kicad_pcb
    python examples/make_rf_examples.py coupler /tmp/c  # one board at /tmp/c.kicad_pcb
    kicad2ems examples/rf_coupler.toml --preview

Ports are kicad2ems:RF_Port footprints (rotation = direction into the circuit), 0.5 mm inside the board
edge where the feed lines end.
"""
import math, os, sys, uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from kicad2ems.solvers.lines.tlines import microstrip, cpwg               # noqa: E402
from kicad2ems.solvers.lines.coupled import coupled_microstrip           # noqa: E402

H_SUB, ER, TAND, F0 = 0.8, 4.4, 0.02, 2.4e9
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

    def via(self, x, y, net=1, size=0.6, drill=0.3):
        self.items.append(f'  (via (at {x:.4f} {y:.4f}) (size {size}) (drill {drill}) (layers "F.Cu" "B.Cu") (net {net}) '
                          f'(uuid "{u()}"))')

    def port(self, n, x, y, rot):
        self.items += [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
                       f'    (property "Reference" "TP{n}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
                       f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))',
                       f'    (fp_text user "P{n}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )']

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

    def write(self, path, W, H, ground=True):
        if ground:
            self.rect(0, 0, W, H, layer='B.Cu', net=1)
        L = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
             f'  (general (thickness {H_SUB}))\n  (paper "A4")',
             '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) (39 "F.Mask" user) (44 "Edge.Cuts" user)'
             ' (49 "F.Fab" user))',
             '  (setup (stackup',
             '    (layer "F.Cu" (type "copper") (thickness 0.035))',
             f'    (layer "dielectric 1" (type "core") (thickness {H_SUB}) (material "FR4") (epsilon_r {ER}) (loss_tangent {TAND}))',
             '    (layer "B.Cu" (type "copper") (thickness 0.035))',
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


def stubnotch(path, feed=6.0):
    q = quarter(W50)
    yc = q + W50 / 2 + 3.0
    xs = (feed + W50, feed + W50 + q)                         # stub centres, a quarter wave apart
    W, H = xs[1] + W50 + feed, yc + W50 / 2 + 3.0
    bd = Board()
    bd.track([(0, yc), (W, yc)], W50)
    for x in xs:                                              # open stubs up from the line (y down in KiCad): tracks
        bd.track([(x, yc), (x, yc - q)], W50)                 # the round end cap reaches q beyond the line's edge
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


KINDS = dict(branchline=branchline, wilkinson=wilkinson, steplpf=steplpf, stubnotch=stubnotch, gcpw=gcpw,
             coupler=coupler)

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in list(KINDS) + ['all']:
        sys.exit(f"usage: make_rf_examples.py {{{','.join(KINDS)},all}} [out_base]")
    here = os.path.dirname(os.path.abspath(__file__))
    names = list(KINDS) if sys.argv[1] == 'all' else [sys.argv[1]]
    for name in names:
        out = sys.argv[2] if len(sys.argv) > 2 and len(names) == 1 else os.path.join(here, f"rf_{name}")
        info = KINDS[name](out + '.kicad_pcb')
        print('wrote', out + '.kicad_pcb', '' if info is None else info)
