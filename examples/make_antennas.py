"""
Writes small KiCad 8 antenna boards for kicad2ems --mode antenna (no KiCad needed), all for
2.45 GHz on FR4 (er 4.4, tan d 0.02):

  patch   inset-fed rectangular patch (37.2 x 28.8 mm) on 1.6 mm FR4, 50 ohm microstrip feed from the
          board edge, B.Cu ground
  ifa     PCB inverted-F antenna above the top edge of a B.Cu ground plane, shorting leg via to
          ground, fed at the ground edge (port from the feed leg to the plane below)
  dipole  printed half-wave dipole on 0.8 mm FR4 without ground, fed in the centre gap (gap port,
          port.z0 = 73 ohm)
  slot    52 mm slot in the B.Cu ground plane, fed off-centre by a microstrip crossing it with an
          open quarter-wave stub
  array2x2  four of the patch, 100 mm apart, from one 50 ohm feed through a corporate network (T junctions
          and quarter-wave transformers), the bottom row's branch half a guided wavelength longer
  array2x2_phased  four of the patch, 70 mm apart, a feed per row (P1 the top row, P2 the bottom one): driven
          with a phase difference (drive = ["P1@0", "P2@60"]) the beam tilts in the yz plane

    python examples/make_antennas.py patch examples/antenna_patch
    python examples/make_antennas.py ifa /tmp/ifa arm=20 s=2.5      # dimensions (mm) as parameters
    kicad2ems examples/antenna_patch.toml

Dimensions are keyword parameters (mm) of each function, also on the command line (param=value).
The defaults were tuned with kicad2ems to resonate at 2.43-2.45 GHz with S11 below -19 dB.
"""
import sys, uuid


def u():
    return str(uuid.uuid4())


class Board:
    def __init__(self, thickness, ground_layer=True):
        self.t, self.items, self.nets = thickness, [], ["", "GND", "RF"]

    def rect(self, x0, y0, x1, y1, layer='F.Cu', net=2):
        self.items.append(f'  (gr_rect (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (stroke (width 0) (type solid)) '
                          f'(fill yes) (layer "{layer}") (net {net}) (uuid "{u()}"))')

    def poly(self, pts, layer='F.Cu', net=2):
        p = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
        self.items.append(f'  (gr_poly (pts {p}) (stroke (width 0) (type solid)) (fill yes) (layer "{layer}") '
                          f'(net {net}) (uuid "{u()}"))')

    def track(self, x0, y0, x1, y1, w, layer='F.Cu', net=2):
        self.items.append(f'  (segment (start {x0:.4f} {y0:.4f}) (end {x1:.4f} {y1:.4f}) (width {w}) (layer "{layer}") '
                          f'(net {net}) (uuid "{u()}"))')

    def via(self, x, y, net=1, size=0.6, drill=0.3):
        self.items.append(f'  (via (at {x:.4f} {y:.4f}) (size {size}) (drill {drill}) (layers "F.Cu" "B.Cu") (net {net}) '
                          f'(uuid "{u()}"))')

    def port(self, ref, text, x, y, rot, z0=None):
        L = [f'  (footprint "kicad2ems:RF_Port" (layer "F.Cu") (uuid "{u()}") (at {x:.4f} {y:.4f} {rot})',
             f'    (property "Reference" "{ref}" (at 0 -1.5 {rot}) (layer "F.SilkS") (uuid "{u()}"))',
             f'    (property "Value" "RF_Port" (at 0 1.5 {rot}) (layer "F.Fab") (uuid "{u()}"))']
        if z0:
            L.append(f'    (property "port.z0" "{z0:g}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u()}"))')
        L.append(f'    (fp_text user "{text}" (at 0 0.8 {rot}) (layer "F.Fab") (uuid "{u()}"))\n  )')
        self.items += L

    def write(self, path, W, H):
        L = ['(kicad_pcb (version 20240108) (generator "kicad2ems") (generator_version "8.0")',
             f'  (general (thickness {self.t}))\n  (paper "A4")',
             '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) (44 "Edge.Cuts" user) (49 "F.Fab" user))',
             '  (setup (stackup',
             '    (layer "F.Cu" (type "copper") (thickness 0.035))',
             f'    (layer "dielectric 1" (type "core") (thickness {self.t}) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))',
             '    (layer "B.Cu" (type "copper") (thickness 0.035))',
             '    (copper_finish "None") (dielectric_constraints no))\n    (pad_to_mask_clearance 0))',
             '  ' + ' '.join(f'(net {i} "{n}")' for i, n in enumerate(self.nets))]
        L += self.items
        L.append(f'  (gr_rect (start 0 0) (end {W} {H}) (stroke (width 0.1) (type default)) (fill none) '
                 f'(layer "Edge.Cuts") (uuid "{u()}"))\n)')
        open(path, 'w').write("\n".join(L) + "\n")


def patch(path, pw=37.2, pl=28.8, inset=8.8, gap=1.0, wf=3.0):
    """Inset-fed patch: W 37.2, L 28.8 mm, inset 8.8 mm deep with 1 mm gaps, 3.0 mm feed (50 ohm on 1.6 mm)."""
    W, H = 80.0, 80.0
    b = Board(1.6)
    x0, y0 = (W - pw) / 2, (H - pl) / 2                          # patch top-left (KiCad, y down)
    xc, y1 = W / 2, y0 + pl                                      # feed enters the bottom edge
    b.poly([(x0, y0), (x0 + pw, y0), (x0 + pw, y1), (xc + wf / 2 + gap, y1), (xc + wf / 2 + gap, y1 - inset),
            (xc - wf / 2 - gap, y1 - inset), (xc - wf / 2 - gap, y1), (x0, y1)])
    b.rect(xc - wf / 2, y1 - inset, xc + wf / 2, H)              # feed line from the inset to the board edge
    b.rect(0, 0, W, H, layer='B.Cu', net=1)
    b.port('TP1', 'P1', xc, H - 0.5, 90)
    b.write(path, W, H)


def ifa(path, h=5.0, w=0.8, xs=8.0, s=3.0, arm=17.5):
    """Inverted-F above the top edge of a B.Cu ground plane: 0.8 mm arm h mm above the ground edge, shorting
    leg at x = xs down to a via 1 mm inside the ground area, feed leg s mm further along, ending 1 mm over
    the ground where the port sits (the usual feed point at the ground edge); arm `arm` mm from the short."""
    W, H, ye = 40.0, 60.0, 12.0                                  # ground from y = ye down to the bottom edge
    b = Board(1.6)
    ya = ye - h
    b.track(xs, ya, xs + arm, ya, w)                             # arm
    b.track(xs, ya, xs, ye + 1.0, w)                             # shorting leg, down to a via in the ground area
    b.via(xs, ye + 1.0, net=1)
    b.track(xs + s, ya, xs + s, ye + 1.0, w)                     # feed leg, ends 1 mm over the ground
    b.rect(0, ye, W, H, layer='B.Cu', net=1)                     # ground (the shorting via lands on it)
    b.port('TP1', 'P1', xs + s, ye + 0.9, 90)
    b.write(path, W, H)


def dipole(path, arm=23.3, w=2.0, g=1.0):
    """Half-wave printed dipole: two arm x w mm arms (23.3 x 2), g mm feed gap, 0.8 mm FR4, no ground; gap
    port (73 ohm reference)."""
    W, H = 70.0, 20.0
    b = Board(0.8)
    yc = H / 2
    b.rect(W / 2 - g / 2 - arm, yc - w / 2, W / 2 - g / 2, yc + w / 2)
    b.rect(W / 2 + g / 2, yc - w / 2, W / 2 + g / 2 + arm, yc + w / 2)
    b.port('TP1', 'P1', W / 2, yc, 0, z0=73)
    b.write(path, W, H)


def slot(path, sl=52.0, sw=2.0, wf=3.0, stub=16.8, off=16.0):
    """Slot sl x sw mm in the B.Cu ground (60 x 60 mm board, slot centred), 3.0 mm microstrip from the
    bottom edge crossing the slot `off` mm right of its centre (lower impedance than at the centre) and
    ending `stub` mm (about lambda_g / 4) beyond it."""
    W, H = 60.0, 60.0
    b = Board(1.6)
    xc, yc = W / 2, H / 2
    sx0, sx1, sy0, sy1 = xc - sl / 2, xc + sl / 2, yc - sw / 2, yc + sw / 2
    if sx0 < 1 or abs(off) > sl / 2 - wf:
        raise ValueError("slot: keep the slot inside the board and the feed inside the slot")
    for r in ((0, 0, W, sy0), (0, sy1, W, H), (0, sy0, sx0, sy1), (sx1, sy0, W, sy1)):   # ground minus the slot
        b.rect(*r, layer='B.Cu', net=1)
    xf = xc + off
    b.rect(xf - wf / 2, yc - stub, xf + wf / 2, H)
    b.port('TP1', 'P1', xf, H - 0.5, 90)
    b.write(path, W, H)


def array2x2(path, pw=37.2, pl=28.8, inset=8.8, gap=1.0, wf=3.0, d=100.0, wq=5.2, lq=16.2, lg=67.4, a=4.0):
    """2 x 2 array of the inset-fed patch, d mm apart (0.82 lambda0: room between the rows for the two transformers
    and the detour), one corporate feed from the left edge.
    The top row is fed at its bottom edges and the bottom row at its top edges, so both rows' feeds meet in the
    gap between them; the bottom row's branch is lg / 2 (half a 50 ohm guided wavelength) longer, which undoes
    the 180 deg of the flipped feed. Each T joins two 50 ohm lines (25 ohm), and a quarter-wave transformer
    (wq wide = 35.4 ohm, lq long) brings it back to 50 ohm: one per row, one after the centre T."""
    W, H = 2 * d + 20.0, 2 * d - 10.0
    b = Board(1.6)
    xc, yc = W / 2 + 4.0, H / 2                                   # array centre (KiCad, y down)
    h = wf / 2

    def patch_at(px, py, feed_down):
        x0, y0 = px - pw / 2, py - pl / 2
        y1 = y0 + pl
        if feed_down:                                              # inset in the bottom edge
            b.poly([(x0, y0), (x0 + pw, y0), (x0 + pw, y1), (px + h + gap, y1), (px + h + gap, y1 - inset),
                    (px - h - gap, y1 - inset), (px - h - gap, y1), (x0, y1)])
            return y1 - inset
        b.poly([(x0, y1), (x0 + pw, y1), (x0 + pw, y0), (px + h + gap, y0), (px + h + gap, y0 + inset),
                (px - h - gap, y0 + inset), (px - h - gap, y0), (x0, y0)])
        return y0 + inset

    def row(py, down):
        """Two patches at py, their feeds turning to a T at x = xc, a below (top row) or above the patches."""
        yt = py + (pl / 2 + a) * (1 if down else -1)               # the row's horizontal feed line
        for px in (xc - d / 2, xc + d / 2):
            ys = patch_at(px, py, down)
            b.rect(px - h, min(ys, yt) - (h if not down else 0), px + h, max(ys, yt) + (h if down else 0))
        b.rect(xc - d / 2 - h, yt - h, xc + d / 2 + h, yt + h)
        return yt
    yu, yl = row(yc - d / 2, True), row(yc + d / 2, False)
    # top row: transformer down from its T, then 50 ohm to the centre T
    b.rect(xc - wq / 2, yu + h, xc + wq / 2, yu + h + lq)
    b.rect(xc - h, yu + h + lq, xc + h, yc + h)
    # bottom row: transformer up from its T, then 50 ohm with a U detour adding lg / 2
    yq = yl - h - lq
    b.rect(xc - wq / 2, yq, xc + wq / 2, yl - h)
    det = lg / 4                                                    # two legs of lg / 4: lg / 2 longer
    ya, yb = yq - 2.0, yq - 2.0 - 6.0                             # the detour's two horizontal runs
    if yb - h < yc + wq / 2 + 1.0:
        raise ValueError("array2x2: no room for the detour between the rows: a larger d")
    b.rect(xc - h, ya - h, xc + h, yq)                             # up from the transformer
    b.rect(xc - h, ya - h, xc + det + h, ya + h)                   # right
    b.rect(xc + det - h, yb - h, xc + det + h, ya + h)             # up
    b.rect(xc - h, yb - h, xc + det + h, yb + h)                   # back left
    b.rect(xc - h, yc - h, xc + h, yb + h)                         # up to the centre T
    # centre T -> transformer -> 50 ohm to the port at the left edge
    b.rect(xc - h - lq, yc - wq / 2, xc - h, yc + wq / 2)
    b.rect(0, yc - h, xc - h - lq, yc + h)
    b.rect(0, 0, W, H, layer='B.Cu', net=1)
    b.port('TP1', 'P1', 0.5, yc, 0)
    b.write(path, W, H)


def array2x2_phased(path, pw=37.2, pl=28.8, inset=8.8, gap=1.0, wf=3.0, d=70.0, wq=5.2, lq=16.2, a=4.0, s=4.0):
    """2 x 2 array of the inset-fed patch, d mm apart (0.57 lambda0: no grating lobe steering to about 45 deg), a
    feed per row from the left edge: P1 the top row, P2 the bottom one. Both rows are alike (fed at their bottom
    edges, the same lines), so driving P1 and P2 in phase points the beam up; a phase difference tilts it in the
    yz plane. Each row's T joins its two 50 ohm patch lines (25 ohm), a quarter-wave transformer (wq wide =
    35.4 ohm, lq long) brings it back to 50 ohm, then s mm down and 50 ohm to the board edge."""
    W = H = 150.0
    b = Board(1.6)
    xc, yc = W / 2, 65.0                                          # array centre (KiCad, y down)
    h = wf / 2
    for ref, py in (('P1', yc - d / 2), ('P2', yc + d / 2)):
        yt = py + pl / 2 + a                                      # the row's feed line, below its patches
        for px in (xc - d / 2, xc + d / 2):
            x0, y0 = px - pw / 2, py - pl / 2
            y1 = y0 + pl
            b.poly([(x0, y0), (x0 + pw, y0), (x0 + pw, y1), (px + h + gap, y1), (px + h + gap, y1 - inset),
                    (px - h - gap, y1 - inset), (px - h - gap, y1), (x0, y1)])
            b.rect(px - h, y1 - inset, px + h, yt + h)
        b.rect(xc - d / 2 - h, yt - h, xc + d / 2 + h, yt + h)
        b.rect(xc - wq / 2, yt + h, xc + wq / 2, yt + h + lq)       # transformer down from the T
        yp = yt + h + lq + s
        b.rect(xc - h, yt + h + lq, xc + h, yp + h)
        b.rect(0, yp - h, xc + h, yp + h)                          # 50 ohm to the left edge
        if ref == 'P1' and yp + h + 3 > yc + d / 2 - pl / 2:
            raise ValueError("array2x2_phased: the top row's feed runs into the bottom row: a larger d")
        b.port('TP' + ref[1], ref, 0.5, yp, 0)
    b.rect(0, 0, W, H, layer='B.Cu', net=1)
    b.write(path, W, H)


def vivaldi(path, ap=44.0, lt=40.0, ws=0.4, ls=6.0, rc=3.5, wf=1.5, xf=10.0, rs=6.0, fan=90.0, n=48):
    """Tapered-slot (Vivaldi) antenna, 50 x 70 mm on 0.8 mm FR4, endfire towards -y (the board's top edge).
    The slot is cut in the B.Cu ground: an exponential taper lt mm long opening from ws to an ap mm wide
    aperture at the top edge, then ls mm of ws slotline down to the feed crossing and a circular cavity of
    radius rc (the slot's open end) below it. A 50 ohm (wf) microstrip on F.Cu comes up from the bottom edge
    at x = xf, turns and crosses the slot, and ends past it in a radial stub of radius rs, fan degrees wide:
    the stub and the cavity make the crossing a wideband microstrip-to-slotline balun."""
    import math
    W, H = 50.0, 70.0
    b = Board(0.8)
    xc, hs = W / 2, ws / 2
    ys, yx = lt, lt + ls                                         # taper start, feed crossing (KiCad, y down)
    ycav = yx + rc + 0.5                                         # cavity centre: just below the crossing
    if ap / 2 > xc - 1 or ycav + rc > H - 10:
        raise ValueError("vivaldi: keep the aperture and the cavity inside the board")
    R = math.log((ap / 2) / hs) / lt                             # half-width hs * exp(R * (ys - y))
    left = [(xc - hs * math.exp(R * (ys - ys * i / n)), ys * i / n) for i in range(n + 1)]   # y 0 -> ys
    a0 = math.asin(hs / rc)                                       # where the slotline meets the cavity
    arc = [(xc - rc * math.sin(a), ycav - rc * math.cos(a))      # around the cavity, left side first
           for a in (a0 + (2 * math.pi - 2 * a0) * k / n for k in range(n + 1))]
    right = [(2 * xc - x, y) for x, y in reversed(left)]
    b.poly([(0, 0)] + left + arc + right + [(W, 0), (W, H), (0, H)], layer='B.Cu', net=1)
    h = wf / 2
    b.rect(xf - h, yx - h, xf + h, H)                            # up from the bottom edge
    b.rect(xf - h, yx - h, xc + hs + 0.6, yx + h)                # across the slot
    xa = xc + hs + 0.6                                           # radial stub apex, just past the slot
    t = math.radians(fan / 2)
    b.poly([(xa, yx - h), (xa, yx + h)] +
           [(xa + rs * math.cos(a), yx + rs * math.sin(a)) for a in (t - 2 * t * k / 24 for k in range(25))])
    b.port('TP1', 'P1', xf, H - 0.5, 90)
    b.write(path, W, H)


KINDS = dict(patch=patch, ifa=ifa, dipole=dipole, slot=slot, array2x2=array2x2, array2x2_phased=array2x2_phased,
             vivaldi=vivaldi)

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in KINDS:
        sys.exit(f"usage: make_antennas.py {{{','.join(KINDS)}}} [out_base] [param=mm ...]")
    out = sys.argv[2] if len(sys.argv) > 2 else f"antenna_{sys.argv[1]}"
    kw = dict(a.split('=') for a in sys.argv[3:])                 # e.g. arm=20 (mm), see each function
    KINDS[sys.argv[1]](out + '.kicad_pcb', **{k: float(v) for k, v in kw.items()})
    print('wrote', out + '.kicad_pcb')
