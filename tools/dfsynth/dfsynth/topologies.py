"""
Filter topologies: each builder turns a Spec into a Design (dimensions, layout geometry, the ideal TEM
network of the synthesis and the microstrip network of the layout).

    lowpass   stepped   stepped-impedance (alternating high / low impedance sections)
              stub      high-impedance lines with shunt open stubs
    highpass  stub      shunt short-circuited quarter-wave stubs (a wide stub band-pass, see below)
    bandpass  coupled   edge-coupled half-wave resonators (parallel-coupled lines)
              hairpin   the same, folded into U-shaped resonators
              endcoupled  gap-coupled half-wave resonators in line
              stub      shunt short-circuited quarter-wave stubs, quarter-wave connecting lines (wide band)
    bandstop  stub      shunt open quarter-wave stubs, quarter-wave connecting lines

A builder takes two correction factors on top of the spec: kf scales the design frequency (all
lengths go as 1 / kf) and kb the design bandwidth. calibrate.py sets them so the microstrip model
lands on the spec, and the EM tuning in validate.py updates them from a field solver.
"""
import math

import numpy as np
from dataclasses import dataclass, field

from .microstrip import C0
from .prototype import gvalues
from .circuit import Ideal, MLine, MStub, MCoupled, MGap, MRadial, radial_admittance, RADIAL_RI_LINE
from . import mtl

KINDS = {
    'lowpass': ('stepped', 'stub', 'radial'),
    'highpass': ('stub',),
    'bandpass': ('coupled', 'hairpin', 'interdigital', 'endcoupled', 'stub'),
    'bandstop': ('stub', 'radial'),
}

VIA_DRILL, VIA_SIZE = 0.3, 0.6


@dataclass
class Spec:
    kind: str                   # lowpass | highpass | bandpass | bandstop
    topology: str
    f0: float                   # Hz: centre (bandpass / bandstop) or cut-off (lowpass / highpass)
    bw: float = 0.0             # Hz: bandwidth (bandpass / bandstop); highpass: pass band width above f0
    response: str = 'chebyshev'
    order: int = 5
    ripple: float = 0.1         # dB (Chebyshev)
    z0: float = 50.0
    zhigh: float = 0.0          # lowpass: the high / low section impedances (0: chosen)
    zlow: float = 0.0
    zstub: float = 0.0          # lowpass stub: the stubs' impedance; interdigital: the rods' (0: chosen)
    feed: float = 0.0           # mm of 50 ohm feed line at each port (0: chosen)
    trim: tuple = ()            # lowpass: per-element electrical-length factors; bandstop: impedance factors
                                # (set by calibrate.refine / refine_bandstop)
    stub_angle: float = 60.0    # degrees: radial stubs' apex angle (lowpass radial)

    @property
    def fbw(self):
        return self.bw / self.f0 if self.f0 else 0.0

    def check(self):
        if self.kind not in KINDS:
            raise ValueError(f"filter type must be one of {', '.join(KINDS)}")
        if self.topology not in KINDS[self.kind]:
            raise ValueError(f"{self.kind} topologies: {', '.join(KINDS[self.kind])}")
        if self.kind in ('bandpass', 'bandstop') and not 0 < self.fbw < 1.5:
            raise ValueError("bandpass / bandstop need a bandwidth between 0 and 1.5 x the centre frequency")


@dataclass
class Geometry:
    polys: list = field(default_factory=list)     # [[(x, y), ...]] copper on F.Cu, net RF (mm, y down)
    vias: list = field(default_factory=list)      # [(x, y)] to the ground plane
    ports: list = field(default_factory=list)     # [(n, x, y, rot)]
    W: float = 0.0
    H: float = 0.0

    def rect(self, x0, y0, x1, y1):
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        self.polys.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    def hline(self, x0, x1, y, w):
        self.rect(x0, y - w / 2, x1, y + w / 2)

    def vline(self, x, y0, y1, w):
        self.rect(x - w / 2, y0, x + w / 2, y1)

    def finish(self, x_end, margin, port_in_y, port_out_y):
        """Board from x = 0 to x_end, margin mm above and below the copper; shift y so the board starts at
        0; ports 0.5 mm inside the left / right edges."""
        ys = [p[1] for poly in self.polys for p in poly] + [v[1] for v in self.vias]
        y0, y1 = min(ys) - margin, max(ys) + margin
        self.polys = [[(x, y - y0) for x, y in poly] for poly in self.polys]
        self.vias = [(x, y - y0) for x, y in self.vias]
        self.W, self.H = x_end, y1 - y0
        self.ports = [(1, 0.5, port_in_y - y0, 0), (2, x_end - 0.5, port_out_y - y0, 180)]


@dataclass
class Design:
    spec: Spec
    sub: object
    kf: float
    kb: float
    geometry: Geometry
    ideal: list                 # ideal TEM network (the synthesis) or None
    model_fn: object            # sub -> microstrip network of the layout (between the port markers)
    rows: list                  # [(element, Z ohm or '', width, length, gap)] for the report
    g: list
    notes: list = field(default_factory=list)

    def model(self, sub=None):
        return self.model_fn(sub or self.sub)


# ------------------------------------------------------------------------------- helpers
def _beta(sub, w, f):
    """rad / mm on a strip of width w at f."""
    return 2 * math.pi * f * math.sqrt(sub.eeff(w, f)) / C0 * 1e-3


def _coupled_beta(sub, w, s, f):
    ze, zo, ee, eo = sub.coupled(w, s)
    ee, eo = sub.disperse(ee, w, f), sub.disperse(eo, w, f)
    return 2 * math.pi * f * 0.5 * (math.sqrt(ee) + math.sqrt(eo)) / C0 * 1e-3


def _feed(spec, sub):
    return spec.feed or max(5.0, 4 * sub.h)


def _margin(sub):
    return max(3.0, 5 * sub.h)


class FabViolation(str):
    """A note that a dimension is below the substrate's fabrication limits: the CLI refuses such a design."""


def violations(design):
    return [n for n in design.notes if isinstance(n, FabViolation)]


def _check_w(sub, notes, what, w):
    if w < sub.min_width - 1e-9:
        notes.append(FabViolation(f"{what}: width {w:.3f} mm is below the {sub.min_width} mm minimum"))


def _check_s(sub, notes, what, s):
    if s < sub.min_gap - 1e-9:
        notes.append(FabViolation(f"{what}: gap {s:.3f} mm is below the {sub.min_gap} mm minimum"))


def _odd_order(spec, notes):
    """Ladder topologies with equal terminations need an odd-order Chebyshev prototype."""
    if spec.response == 'chebyshev' and spec.order % 2 == 0:
        notes.append(f"order {spec.order} Chebyshev needs unequal terminations here: using order {spec.order + 1}")
        spec.order += 1


def _trimmed(spec, th):
    if spec.trim and len(spec.trim) == len(th):
        return [t * f for t, f in zip(th, spec.trim)]
    return list(th)


def _via_row(geo, x, y, w):
    """Vias across a stub end of width w at (x, y); returns how many."""
    n = max(1, min(4, int(w / (2 * VIA_SIZE))))
    for k in range(n):
        geo.vias.append((x + (k - (n - 1) / 2) * min(w / n, 2 * VIA_SIZE), y))
    return n


def _z_limits(sub, wmax=None):
    """(zmin, zmax): impedances of the widest sensible strip and of the narrowest manufacturable one."""
    wmax = wmax or max(12 * sub.h, 3.0)
    return sub.line(wmax)[0], sub.line(sub.min_width)[0]


# ------------------------------------------------------------------------------- low-pass
def lowpass_stepped(spec, sub, kf=1.0, kb=1.0):
    notes = []
    _odd_order(spec, notes)
    g = gvalues(spec.response, spec.order, spec.ripple)
    zmin, zmax = _z_limits(sub)
    zh = spec.zhigh or min(120.0, 0.98 * zmax)
    zl = spec.zlow or max(15.0, zmin)
    z0, fd = spec.z0, spec.f0 * kf
    n = spec.order
    # series L: zh sin(theta) = g z0; shunt C: 2 yl tan(theta / 2) + neighbours' shunt parasitics = g / z0
    th = [0.0] * n
    for k in range(n):
        if k % 2 == 0:
            x = g[k + 1] * z0 / zh
            if x >= 1:
                raise ValueError(f"high impedance {zh:.0f} ohm too low for g{k + 1} = {g[k + 1]:.3f}: raise --zhigh")
            th[k] = math.asin(x)
    for _ in range(3):
        for k in range(1, n, 2):
            par = sum(math.tan(th[j] / 2) / zh for j in (k - 1, k + 1) if 0 <= j < n)
            b = g[k + 1] / z0 - par
            th[k] = 2 * math.atan(max(b, 1e-6) * zl / 2)
    th = _trimmed(spec, th)
    zs = [zh if k % 2 == 0 else zl for k in range(n)]
    ws = [sub.width(z) for z in zs]
    w50, feed = sub.width(z0), _feed(spec, sub)
    # the low-impedance sections are shortened by the step extension at both ends
    ext = [0.0 if k % 2 == 0 else sub.step(ws[k - 1], ws[k]) + sub.step(ws[k + 1], ws[k]) for k in range(n)]
    ls = [t / _beta(sub, w, fd) - e for t, w, e in zip(th, ws, ext)]
    fext = sub.step(w50, ws[0])                               # the feed's own extension into the first section
    rows = [(f"{'L' if k % 2 == 0 else 'C'}{k + 1}", zs[k], ws[k], ls[k], None) for k in range(n)]
    for k, w in enumerate(ws):
        _check_w(sub, notes, rows[k][0], w)

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    for w, l in zip(ws, ls):
        geo.hline(x, x + l, 0, w)
        x += l
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub) + max(ws) / 2, 0, 0)

    ideal = [Ideal.line(z, t, fd) for z, t in zip(zs, th)]

    def model(s):
        net = [MLine(s, w50, feed - 0.5 + fext)]
        net += [MLine(s, w, l + e) for w, l, e in zip(ws, ls, ext)]
        return net + [MLine(s, w50, feed - 0.5 + fext)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


def lowpass_stub(spec, sub, kf=1.0, kb=1.0):
    """Series high-impedance lines (the inductors, L first) with shunt open stubs (the capacitors) at
    the junctions, the stubs alternating sides."""
    notes = []
    _odd_order(spec, notes)
    g = gvalues(spec.response, spec.order, spec.ripple)
    zmin, zmax = _z_limits(sub)
    zh = spec.zhigh or min(120.0, 0.98 * zmax)
    zst = spec.zstub or max(30.0, zmin)
    z0, fd, n = spec.z0, spec.f0 * kf, spec.order
    th = {}
    for k in range(0, n, 2):
        x = g[k + 1] * z0 / zh
        if x >= 1:
            raise ValueError(f"high impedance {zh:.0f} ohm too low for g{k + 1} = {g[k + 1]:.3f}: raise --zhigh")
        th[k] = math.asin(x)
    for k in range(1, n, 2):
        par = sum(math.tan(th[j] / 2) / zh for j in (k - 1, k + 1) if j in th)
        th[k] = math.atan(max(g[k + 1] / z0 - par, 1e-6) * zst)        # ys tan(theta) = B
    th = dict(enumerate(_trimmed(spec, [th[k] for k in range(n)])))
    wh, wst = sub.width(zh), sub.width(zst)
    w50, feed = sub.width(z0), _feed(spec, sub)
    # the main line under a stub is part of the stub: each series line is lengthened by the half widths of
    # the stubs at its ends (centre spacing), and the model counts only the uncovered part as line
    cover = {k: wst / 2 * sum(1 for j in (k - 1, k + 1) if 0 <= j < n) for k in range(0, n, 2)}
    lser = {k: th[k] / _beta(sub, wh, fd) + cover[k] for k in range(0, n, 2)}
    lstub = {k: th[k] / _beta(sub, wst, fd) - sub.open_end(wst) - wh / 2 for k in range(1, n, 2)}
    rows = []
    for k in range(n):
        if k % 2 == 0:
            rows.append((f"L{k + 1} line", zh, wh, lser[k], None))
        else:
            rows.append((f"C{k + 1} open stub", zst, wst, lstub[k], None))
            if lstub[k] <= 0:
                raise ValueError(f"stub C{k + 1} comes out shorter than the line width: raise --zstub")
    _check_w(sub, notes, 'high impedance line', wh)
    _check_w(sub, notes, 'stubs', wst)

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    side = -1
    for k in range(n):
        if k % 2 == 0:
            geo.hline(x, x + lser[k], 0, wh)
            x += lser[k]
        else:
            geo.vline(x, 0, side * (wh / 2 + lstub[k]), wst)
            side = -side
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    ideal = []
    for k in range(n):
        ideal.append(Ideal.line(zh, th[k], fd) if k % 2 == 0 else Ideal.stub(zst, th[k], fd, 'open'))

    def model(s):
        net = [MLine(s, w50, feed - 0.5)]
        for k in range(n):
            net.append(MLine(s, wh, lser[k] - cover[k]) if k % 2 == 0 else MStub(s, wst, lstub[k] + wh / 2, 'open'))
        return net + [MLine(s, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


# ------------------------------------------------------------------------------- radial stubs
def _fan(geo, x, y, r, angle, side, n=24):
    """A radial stub: a sector of radius r and apex angle (degrees) from the line centre (x, y), opening
    towards side (-1: up, +1: down; y is down)."""
    a0 = math.radians(90 * side - angle / 2)
    pts = [(x, y)] + [(x + r * math.cos(a0 + math.radians(angle) * k / n),
                       y + r * math.sin(a0 + math.radians(angle) * k / n)) for k in range(n + 1)]
    geo.polys.append(pts)


def _radial_z(sub, wl, r2, angle, f):
    return 1 / radial_admittance(sub, wl / 2 + RADIAL_RI_LINE * wl, r2, angle, np.atleast_1d(f))[0]


def _radial_resonant(sub, wl, angle, fd):
    """Radius (mm) of a fan on a line of width wl resonating (Z_in = 0) at fd."""
    from scipy.optimize import brentq
    r1 = wl / 2 + RADIAL_RI_LINE * wl
    lam = C0 / (fd * math.sqrt(sub.er)) * 1e3
    rs = np.linspace(1.05 * r1, r1 + lam, 400)
    x = np.array([_radial_z(sub, wl, r, angle, fd).imag for r in rs])
    for k in range(len(rs) - 1):
        if x[k] < 0 <= x[k + 1]:
            return brentq(lambda r: _radial_z(sub, wl, r, angle, fd).imag, rs[k], rs[k + 1], xtol=1e-5)
    raise ValueError(f"no radial stub of {angle:.0f} degrees resonates at {fd / 1e9:.3f} GHz on this line")


def _radial_slope(sub, wl, r2, angle, fd):
    """Reactance slope (w0 / 2) dX/dw (ohm) of a fan at fd."""
    h = 1e-4 * fd
    return fd / 2 * (_radial_z(sub, wl, r2, angle, fd + h).imag - _radial_z(sub, wl, r2, angle, fd - h).imag) / (2 * h)


def _radial_table(sub, wl, fd, lo=15.0, hi=160.0):
    """(angles, resonant radii, reactance slopes) of fans on a line of width wl at fd, cached."""
    key = _sub_key(sub, wl, fd, lo, hi)
    if key not in _TABLES:
        ang = np.geomspace(lo, hi, 32)
        rr = np.array([_radial_resonant(sub, wl, a, fd) for a in ang])
        xs = np.array([_radial_slope(sub, wl, r, a, fd) for a, r in zip(ang, rr)])
        _TABLES[key] = (ang, rr, xs)
    return _TABLES[key]


def _radial_for_slope(sub, wl, x, fd, lo=15.0, hi=160.0):
    """(angle, radius) of the fan resonating at fd with reactance slope x (a wider fan: a lower one),
    interpolated in _radial_table; clipped to lo..hi degrees."""
    ang, rr, xs = _radial_table(sub, wl, fd, lo, hi)
    la = np.interp(math.log(x), np.log(xs)[::-1], np.log(ang)[::-1])
    a = float(np.exp(la))
    return a, float(np.interp(la, np.log(ang), rr))


def _radial_for_b(sub, wl, angle, b, fd, count):
    """Radius (mm) of `count` fans in shunt with susceptance b (S) at fd, on their capacitive branch."""
    from scipy.optimize import brentq
    rres = _radial_resonant(sub, wl, angle, fd)
    r1 = wl / 2 + RADIAL_RI_LINE * wl

    def err(r):
        return count * (1 / _radial_z(sub, wl, r, angle, fd)).imag - b
    lo = 1.001 * r1
    if err(lo) > 0:
        raise ValueError(f"a {angle:.0f} degree fan is already too big where it leaves the line: lower --stub-angle")
    return brentq(err, lo, 0.999 * rres, xtol=1e-5)


def lowpass_radial(spec, sub, kf=1.0, kb=1.0):
    """lowpass_stub with each capacitor a butterfly (two radial stubs of spec.stub_angle, one each side): the
    pair has the stub's susceptance at the cut-off, and each fan's resonance above it adds a transmission
    zero to the stop band."""
    notes = []
    _odd_order(spec, notes)
    g = gvalues(spec.response, spec.order, spec.ripple)
    zmin, zmax = _z_limits(sub)
    zh = spec.zhigh or min(120.0, 0.98 * zmax)
    zst = spec.zstub or max(30.0, zmin)
    z0, fd, n, ang = spec.z0, spec.f0 * kf, spec.order, spec.stub_angle
    th = {}
    for k in range(0, n, 2):
        x = g[k + 1] * z0 / zh
        if x >= 1:
            raise ValueError(f"high impedance {zh:.0f} ohm too low for g{k + 1} = {g[k + 1]:.3f}: raise --zhigh")
        th[k] = math.asin(x)
    for k in range(1, n, 2):
        par = sum(math.tan(th[j] / 2) / zh for j in (k - 1, k + 1) if j in th)
        th[k] = math.atan(max(g[k + 1] / z0 - par, 1e-6) * zst)
    th = dict(enumerate(_trimmed(spec, [th[k] for k in range(n)])))
    wh = sub.width(zh)
    w50, feed = sub.width(z0), _feed(spec, sub)
    om = 2 * math.pi * fd
    rad = {k: _radial_for_b(sub, wh, ang, math.tan(th[k]) / zst, fd, 2) for k in range(1, n, 2)}
    fres = {}
    lser = {k: th[k] / _beta(sub, wh, fd) for k in range(0, n, 2)}
    rows = []
    for k in range(n):
        if k % 2 == 0:
            rows.append((f"L{k + 1} line", zh, wh, lser[k], None))
        else:
            rows.append((f"C{k + 1} butterfly, {ang:.0f} deg", '', None, rad[k], None))
    _check_w(sub, notes, 'high impedance line', wh)
    half = {k: rad[k] * math.sin(math.radians(min(ang, 180) / 2)) for k in rad}
    for k in range(2, n - 1, 2):                              # fans either side of line L_k+1
        clear = lser[k] - half[k - 1] - half[k + 1]
        if ang < 180:
            _check_s(sub, notes, f"fans C{k} and C{k + 2}", clear)

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    for k in range(n):
        if k % 2 == 0:
            geo.hline(x, x + lser[k], 0, wh)
            x += lser[k]
        else:
            _fan(geo, x, 0, rad[k], ang, -1)
            _fan(geo, x, 0, rad[k], ang, 1)
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    def model(s):
        net = [MLine(s, w50, feed - 0.5)]
        for k in range(n):
            net.append(MLine(s, wh, lser[k]) if k % 2 == 0 else MRadial(s, wh, rad[k], ang, count=2))
        return net + [MLine(s, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, None, model, rows, g, notes)


def radial_stub_range(sub, w, fd, angles=(160.0, 15.0)):
    """(low, high) ohm: the straight quarter-wave stubs whose reactance slope fans of 160 and 15 degrees on a
    line of width w have at fd (a fan cannot be a higher or lower impedance stub than these)."""
    key = _sub_key(sub, w, fd)
    if key not in _TABLES:
        ang, _, xs = _radial_table(sub, w, fd)
        _TABLES[key] = (4 / math.pi * float(xs[-1]), 4 / math.pi * float(xs[0]))
    return _TABLES[key]


def bandstop_radial(spec, sub, kf=1.0, kb=1.0):
    """bandstop_stub with radial stubs: each fan resonates at f0 with the reactance slope of the quarter-
    wave stub it replaces, (pi / 4) Z_k (its angle sets the slope, its radius the resonance). Radial stubs
    are low-impedance stubs, so this suits wide stop bands. spec.trim (calibrate.refine_bandstop) scales the
    n stub and n - 1 line impedances."""
    notes = []
    _odd_order(spec, notes)
    g = gvalues(spec.response, spec.order, spec.ripple)
    n, fd, z0 = spec.order, spec.f0 * kf, spec.z0
    w50, feed = sub.width(z0), _feed(spec, sub)
    zs = [4 * z0 / (math.pi * g[k + 1] * spec.fbw * kb) for k in range(n)]
    zl = [z0] * (n - 1)
    if spec.trim and len(spec.trim) == 2 * n - 1:
        zs = [z * t for z, t in zip(zs, spec.trim[:n])]
        zl = [z * t for z, t in zip(zl, spec.trim[n:])]
    wl = [sub.width(z) for z in zl]
    zr = radial_stub_range(sub, w50, fd)
    if not (zr[0] * 0.999 <= min(zs) and max(zs) <= zr[1] * 1.001):
        # refused like a fabrication violation (the CLI then looks for the nearest bandwidth that works)
        notes.append(FabViolation(f"radial stubs (15 to 160 degrees) behave as {zr[0]:.0f} to {zr[1]:.0f} ohm "
                                  f"stubs here, the band needs {min(zs):.0f} to {max(zs):.0f}: use --topology "
                                  f"stub for narrower stop bands"))
    fans = [_radial_for_slope(sub, w50, math.pi / 4 * z, fd) for z in zs]     # fans designed on the 50 ohm line
    lc = [math.pi / 2 / _beta(sub, w, fd) for w in wl]
    half = [r * math.sin(math.radians(min(a, 180) / 2)) for a, r in fans]
    for k in range(n - 2):                                    # same-side fans are two sections apart
        _check_s(sub, notes, f"fans {k + 1} and {k + 3}", lc[k] + lc[k + 1] - half[k] - half[k + 2])
    for k in range(n - 1):
        _check_w(sub, notes, f"line {k + 1}-{k + 2}", wl[k])
    feed = max(feed, half[0] + _margin(sub), half[-1] + _margin(sub))   # the outer fans clear of the board edge
    rows = []
    for k, (a, r) in enumerate(fans):
        rows.append((f"stub {k + 1} (radial, {a:.1f} deg)", zs[k], None, r, None))
        if k < n - 1:
            rows.append((f"line {k + 1}-{k + 2}", zl[k], wl[k], lc[k], None))

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    side = -1
    for k, (a, r) in enumerate(fans):
        _fan(geo, x, 0, r, a, side)
        side = -side
        if k < n - 1:
            geo.hline(x, x + lc[k], 0, wl[k])
            x += lc[k]
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    ideal = []
    for k in range(n):
        ideal.append(Ideal.stub(zs[k], math.pi / 2, fd, 'open'))
        if k < n - 1:
            ideal.append(Ideal.line(zl[k], math.pi / 2, fd))

    def model(s_):
        net = [MLine(s_, w50, feed - 0.5)]
        for k, (a, r) in enumerate(fans):
            net.append(MRadial(s_, w50, r, a))
            if k < n - 1:
                net.append(MLine(s_, wl[k], lc[k]))
        return net + [MLine(s_, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


# ------------------------------------------------------------------------------- stub band-pass / high-pass
def stub_bpf_values(g, fbw, h, z0):
    """Matthaei's short-circuited-stub band-pass: (stub impedances [n], connecting-line impedances [n-1]),
    stubs and lines a quarter wave at the centre frequency."""
    n, g0, y0 = len(g) - 2, g[0], 1 / z0
    th = math.pi / 2 * (1 - fbw / 2)
    J = {1: g0 * math.sqrt(h * g[1] / g[2]), n - 1: g0 * math.sqrt(h * g[1] * g[n + 1] / (g0 * g[n - 1]))}
    for i in range(2, n - 1):
        J[i] = h * g0 * g[1] / math.sqrt(g[i] * g[i + 1])
    N = {i: math.sqrt(J[i] ** 2 + (h * g0 * g[1] * math.tan(th) / 2) ** 2) for i in J}
    Y = {1: g0 * y0 * (1 - h / 2) * g[1] * math.tan(th) + y0 * (N[1] - J[1]),
         n: y0 * (g[n] * g[n + 1] - g0 * g[1] * h / 2) * math.tan(th) + y0 * (N[n - 1] - J[n - 1])}
    for i in range(2, n):
        Y[i] = y0 * (N[i - 1] + N[i] - J[i - 1] - J[i])
    return [1 / Y[i] for i in range(1, n + 1)], [1 / (y0 * J[i]) for i in range(1, n)]


def _stub_band(spec, sub, kf, kb, f0, fbw, label):
    notes = []
    if spec.order < 3:
        notes.append("stub band-pass needs at least 3 stubs: using order 3")
        spec.order = 3
    g = gvalues(spec.response, spec.order, spec.ripple)
    zmin, zmax = _z_limits(sub)
    fbw_d, fd, z0, n = fbw * kb, f0 * kf, spec.z0, spec.order
    best = None
    for h in (2.0, 1.5, 1.0, 0.75, 0.5, 2.5, 3.0):           # the scale of the stub admittances (Matthaei's h)
        try:
            zs, zc = stub_bpf_values(g, fbw_d, h, z0)
        except (ValueError, ZeroDivisionError):
            continue
        zall = zs + zc
        if min(zall) <= 0:
            continue
        bad = sum(max(0, zmin / z - 1) + max(0, z / zmax - 1) for z in zall)
        if best is None or bad < best[0] - 1e-9:
            best = (bad, h, zs, zc)
        if bad == 0:
            break
    if best is None:
        raise ValueError("no realisable stub band-pass for this bandwidth")
    _, h, zs, zc = best
    if best[0] > 0:
        notes.append(f"some impedances fall outside {zmin:.0f}..{zmax:.0f} ohm (h = {h})")
    wst = [sub.width(z) for z in zs]
    wc = [sub.width(z) for z in zc]
    w50, feed = sub.width(z0), _feed(spec, sub)
    om = 2 * math.pi * fd
    cover = [(wst[i] + wst[i + 1]) / 2 for i in range(n - 1)]     # see lowpass_stub
    lc = [math.pi / 2 / _beta(sub, w, fd) + c for w, c in zip(wc, cover)]
    wmain = [max([w50] * (i in (0, n - 1)) + [wc[j] for j in (i - 1, i) if 0 <= j < n - 1]) for i in range(n)]
    lst, nvias = [], []
    for i in range(n):
        nv = max(1, min(4, int(wst[i] / (2 * VIA_SIZE))))
        lv = sub.via_inductance(VIA_DRILL) / nv
        b = _beta(sub, wst[i], fd)
        dvia = math.atan(om * lv / zs[i]) / b
        lst.append(math.pi / 2 / b - wmain[i] / 2 - dvia)
        nvias.append(nv)
    rows = []
    for i in range(n):
        rows.append((f"stub {i + 1} (short)", zs[i], wst[i], lst[i], None))
        _check_w(sub, notes, f"stub {i + 1}", wst[i])
        if i < n - 1:
            rows.append((f"line {i + 1}-{i + 2}", zc[i], wc[i], lc[i], None))
            _check_w(sub, notes, f"line {i + 1}-{i + 2}", wc[i])

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    side = -1
    for i in range(n):
        y_via = side * (wmain[i] / 2 + lst[i])
        pad = max(wst[i], 2 * VIA_SIZE)
        geo.vline(x, 0, y_via + side * VIA_SIZE, wst[i])
        geo.rect(x - pad / 2, y_via - VIA_SIZE, x + pad / 2, y_via + VIA_SIZE)
        _via_row(geo, x, y_via, wst[i])
        side = -side
        if i < n - 1:
            geo.hline(x, x + lc[i], 0, wc[i])
            x += lc[i]
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    ideal = []
    for i in range(n):
        ideal.append(Ideal.stub(zs[i], math.pi / 2, fd, 'short'))
        if i < n - 1:
            ideal.append(Ideal.line(zc[i], math.pi / 2, fd))

    def model(s):
        net = [MLine(s, w50, feed - 0.5)]
        for i in range(n):
            net.append(MStub(s, wst[i], lst[i] + wmain[i] / 2, 'short', vias=nvias[i], drill=VIA_DRILL))
            if i < n - 1:
                net.append(MLine(s, wc[i], lc[i] - cover[i]))
        return net + [MLine(s, w50, feed - 0.5)]
    notes.append(f"{label}: Matthaei h = {h}; {sum(nvias)} ground vias ({VIA_DRILL} mm drill)")
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


def bandpass_stub(spec, sub, kf=1.0, kb=1.0):
    return _stub_band(spec, sub, kf, kb, spec.f0, spec.fbw, 'stub band-pass')


def highpass_band(spec):
    """(f0, fbw) of the stub band-pass realising a high-pass from spec.f0 with spec.bw of pass band above it
    (default: up to 3 x the cut-off)."""
    bw = spec.bw or 2.0 * spec.f0
    f2 = spec.f0 + bw
    return (spec.f0 + f2) / 2, bw / ((spec.f0 + f2) / 2)


def highpass_stub(spec, sub, kf=1.0, kb=1.0):
    f0, fbw = highpass_band(spec)
    if fbw >= 1.6:
        raise ValueError("high-pass band too wide for quarter-wave stubs: keep the pass band below ~4x the cut-off")
    return _stub_band(spec, sub, kf, kb, f0, fbw, 'stub high-pass')


# ------------------------------------------------------------------------------- coupled-line band-pass
def _inverters(g, fbw):
    """J / Y0 of the n + 1 admittance inverters of a band-pass of half-wave resonators."""
    n = len(g) - 2
    j = [math.sqrt(math.pi * fbw / (2 * g[0] * g[1]))]
    j += [math.pi * fbw / (2 * math.sqrt(g[i] * g[i + 1])) for i in range(1, n)]
    j.append(math.sqrt(math.pi * fbw / (2 * g[n] * g[n + 1])))
    return j


def _coupled_sections(spec, sub, kb, notes):
    g = gvalues(spec.response, spec.order, spec.ripple)
    z0 = spec.z0
    secs = []
    for i, jj in enumerate(_inverters(g, spec.fbw * kb)):
        ze, zo = z0 * (1 + jj + jj * jj), z0 * (1 - jj + jj * jj)
        w, s = sub.coupled_synth(ze, zo)
        secs.append((ze, zo, w, s))
        _check_w(sub, notes, f"section {i}", w)
        _check_s(sub, notes, f"section {i}", s)
    return g, secs


def bandpass_coupled(spec, sub, kf=1.0, kb=1.0):
    notes = []
    g, secs = _coupled_sections(spec, sub, kb, notes)
    fd, z0 = spec.f0 * kf, spec.z0
    w50, feed = sub.width(z0), _feed(spec, sub)
    ls = [math.pi / 2 / _coupled_beta(sub, w, s, fd) - sub.open_end(w) for _, _, w, s in secs]
    rows = [(f"section {i}", f"{ze:.1f}/{zo:.1f}", w, l, s) for i, ((ze, zo, w, s), l) in enumerate(zip(secs, ls))]

    geo = Geometry()
    # resonator j's half in section j-1 sits on its lower edge e_j (facing resonator j-1); its half in section j
    # has its upper edge (facing resonator j+1) at e_j + max(w_j-1, w_j). A width change then never crosses a
    # gap at the section corners, and every gap is exactly s_i.
    x, e, wprev = feed, 0.0, secs[0][2]
    y_in = secs[0][2] - w50 / 2                               # the feeds' inner edges on their strips' inner edges
    geo.hline(0, x, y_in, w50)
    for (ze, zo, w, s), l in zip(secs, ls):
        top = e + max(wprev, w)
        geo.rect(x, top - w, x + l, top)                      # the previous resonator (or the input strip)
        e = top + s
        geo.rect(x, e, x + l, e + w)                          # the next resonator (or the output strip)
        x, wprev = x + l, w
    y_out = e + w50 / 2
    geo.hline(x, x + feed, y_out, w50)
    geo.finish(x + feed, _margin(sub), y_in, y_out)

    ideal = [Ideal.coupled(ze, zo, math.pi / 2, fd) for ze, zo, _, _ in secs]

    def model(s_):
        net = [MLine(s_, w50, feed - 0.5)]
        net += [MCoupled(s_, w, s, l) for (_, _, w, s), l in zip(secs, ls)]
        return net + [MLine(s_, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


def bandpass_hairpin(spec, sub, kf=1.0, kb=1.0):
    """Folded edge-coupled filter: resonator j's arms are the strips of sections j-1 and j, joined by a
    bar; resonators alternate between opening up and down, coupled-line feeds at both ends."""
    notes = []
    g, secs = _coupled_sections(spec, sub, kb, notes)
    n, fd, z0 = spec.order, spec.f0 * kf, spec.z0
    w50, feed = sub.width(z0), _feed(spec, sub)
    wbar = []
    ws = [w for _, _, w, _ in secs]
    wavg = sum(ws) / len(ws)
    inner = max(2.0 * wavg, 3 * sub.h, 4 * max(s for _, _, _, s in secs))   # gap between a hairpin's own arms
    d = [ws[j] / 2 + inner + ws[j + 1] / 2 for j in range(n)]                # arm centre spacing of resonator j+1
    beta = sum(_coupled_beta(sub, w, s, fd) for _, _, w, s in secs) / len(secs)
    A = [(math.pi / beta - d[j]) / 2 - sub.open_end((ws[j] + ws[j + 1]) / 2) for j in range(n)]
    arm = sum(A) / n                                          # one arm length for all: the arms overlap fully
    if arm <= 0:
        raise ValueError("hairpin arms come out negative: the band is too high for this substrate")
    rows = [(f"section {i}", f"{ze:.1f}/{zo:.1f}", w, arm, s) for i, (ze, zo, w, s) in enumerate(secs)]
    rows += [(f"resonator {j + 1} arm spacing", '', None, d[j], None) for j in range(n)]

    geo = Geometry()
    xs = []                                                   # arm centre x: feed, (left, right) per resonator, feed
    x = feed + w50
    xs.append(x)
    for j in range(n):
        x += (ws[j] + ws[j]) / 2 + secs[j][3]                 # gap of section j to resonator j+1's left arm
        xl = x
        x += d[j]
        xs.append((xl, x))
    x += ws[n] + secs[n][3]
    xs.append(x)
    # input feed: connected at the top (resonator 1 opens at the top)
    xf = xs[0]
    geo.vline(xf, -w50, arm, ws[0])
    geo.hline(0, xf + ws[0] / 2, -w50 / 2, w50)
    for j in range(n):
        xl, xr = xs[j + 1]
        wl, wr = ws[j], ws[j + 1]
        wb = (wl + wr) / 2
        wbar.append(wb)
        if j % 2 == 0:                                        # U at the bottom, open at y = 0
            yb = arm
            geo.vline(xl, 0, yb + wb / 2, wl)
            geo.vline(xr, 0, yb + wb / 2, wr)
        else:                                                 # U at the top, open at y = arm
            yb = 0.0
            geo.vline(xl, yb - wb / 2, arm, wl)
            geo.vline(xr, yb - wb / 2, arm, wr)
        geo.rect(xl - wl / 2, yb - wb / 2, xr + wr / 2, yb + wb / 2)
    xo = xs[-1]
    out_top = (n - 1) % 2 == 0                                # the last resonator opens at the top
    x_end = xo + ws[n] / 2 + feed
    if out_top:
        geo.vline(xo, -w50, arm, ws[n])
        geo.hline(xo - ws[n] / 2, x_end, -w50 / 2, w50)
        yout = -w50 / 2
    else:
        geo.vline(xo, 0, arm + w50, ws[n])
        geo.hline(xo - ws[n] / 2, x_end, arm + w50 / 2, w50)
        yout = arm + w50 / 2
    geo.finish(x_end, _margin(sub), -w50 / 2, yout)

    ideal = None

    def model(s_):
        net = [MLine(s_, w50, xf - 0.5)]
        for j, (_, _, w, s) in enumerate(secs):
            net.append(MCoupled(s_, w, s, arm))
            if j < n:
                net.append(MLine(s_, wbar[j], d[j]))
        return net + [MLine(s_, w50, x_end - xo - 0.5)]
    notes.append("hairpin: arm spacing and bends are not in the synthesis; the calibration corrects the "
                 "centre frequency and bandwidth on the line model")
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


def bandpass_endcoupled(spec, sub, kf=1.0, kb=1.0):
    notes = []
    g = gvalues(spec.response, spec.order, spec.ripple)
    n, fd, z0 = spec.order, spec.f0 * kf, spec.z0
    w50, feed = sub.width(z0), _feed(spec, sub)
    jj = _inverters(g, spec.fbw * kb)
    if max(jj) >= 0.9:
        raise ValueError("bandwidth too wide for gap coupling: use the coupled or stub topology")
    B = [j / (1 - j * j) for j in jj]
    th = [math.pi - 0.5 * (math.atan(2 * B[i]) + math.atan(2 * B[i + 1])) for i in range(n)]
    gaps = []
    for i, b in enumerate(B):
        cg = b / (2 * math.pi * fd * z0)
        try:
            s = sub.gap_for(w50, cg)
        except ValueError as e:
            raise ValueError(f"end-coupled gap {i}: {e}. Narrow the band or use the coupled / hairpin topology") from None
        gaps.append(s)
        _check_s(sub, notes, f"gap {i}", s)
        if s / w50 < 0.1:
            notes.append(f"gap {i}: s/w = {s / w50:.3f} is below the gap model's fitted range")
    # each gap's shunt capacitance loads the resonators on both sides: shorten each end by its equivalent
    b50 = _beta(sub, w50, fd)
    dl = [math.atan(2 * math.pi * fd * sub.gap(w50, s)[0] * z0) / b50 for s in gaps]
    ls = [t / b50 - dl[i] - dl[i + 1] for i, t in enumerate(th)]
    rows = []
    for i in range(n + 1):
        rows.append((f"gap {i}", '', w50, None, gaps[i]))
        if i < n:
            rows.append((f"resonator {i + 1}", z0, w50, ls[i], None))
    if not 0.5 <= w50 / sub.h <= 2.2:
        notes.append(f"w/h = {w50 / sub.h:.2f} is outside the gap model's fitted range (0.5..2)")

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    for i in range(n):
        x += gaps[i]
        geo.hline(x, x + ls[i], 0, w50)
        x += ls[i]
    x += gaps[n]
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    ideal = [Ideal.series_c(b / (2 * math.pi * fd * z0)) for b in B]
    ideal = [e for i in range(n) for e in (ideal[i], Ideal.line(z0, th[i], fd))] + [ideal[n]]

    def model(s_):
        net = [MLine(s_, w50, feed - 0.5)]
        for i in range(n):
            net += [MGap(s_, w50, gaps[i]), MLine(s_, w50, ls[i])]
        return net + [MGap(s_, w50, gaps[n]), MLine(s_, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


# ------------------------------------------------------------------------------- interdigital
_TABLES = {}


def _sub_key(sub, *extra):
    return (sub.h, sub.er, sub.t, sub.tand, sub.sigma) + tuple(round(x, 6) for x in extra)


VIA_PAD = VIA_SIZE + 0.2                                    # mm: the pad at a rod's shorted end


def _rod_vias(w):
    """Vias across a rod of width w that holds them: as many pads as fit (up to 4), else 0 (a stem)."""
    return min(4, int(w / VIA_PAD)) if w >= VIA_PAD else 0


def _via_stem(sub, w):
    """mm of rod-width strip from a rod's shorted end to its via's centre: none where the vias fit inside
    the rod's end (the short is at the rod's end); else a min_gap stem (its pad starts clear of the
    neighbours' open ends) and half the pad."""
    return 0.0 if _rod_vias(w) else sub.min_gap + VIA_PAD / 2


def _rod_length(sub, w, fd):
    """Length (mm, open end to the shorted end) of a rod shorted through its stem and via(s) that is a
    quarter wave at fd."""
    z0, _ = sub.line(w)
    b = _beta(sub, w, fd)
    lv = sub.via_inductance(VIA_DRILL) / max(1, _rod_vias(w))
    return math.pi / 2 / b - sub.open_end(w) - _via_stem(sub, w) - math.atan(2 * math.pi * fd * lv / z0) / b


def _rod(net, arr_sub, w, top, bottom, shorted_top, ext=0.0, stem=None):
    """Terminations of one rod: the via(s), on a stem if they do not fit in the rod, at the shorted end; the
    open end (ext mm longer) at the other."""
    short, opn = (top, bottom) if shorted_top else (bottom, top)
    stem = _via_stem(arr_sub, w) if stem is None else stem
    if stem > 0:
        vn = net.nodes(1)[0]
        net.line(short, vn, arr_sub, w, stem)
    else:
        vn = short
    net.via(vn, arr_sub, VIA_DRILL, max(1, _rod_vias(w)))
    net.open(opn, arr_sub, w, ext)


def _tap_neck(sub, w50):
    """(width, length) mm of the narrow line from a feed to its rod. A 50 ohm feed joined straight to the
    rod is as wide as the tap's distance from the short (at strong taps): the junction then spreads the tap
    over the feed's width, and openEMS and gfdtd both see a tap some 1.5 x lower in Qe than the line
    model's. A neck a quarter of the feed's width keeps the junction small."""
    return min(w50, max(sub.min_width, 0.25 * w50)), max(1.0, sub.h)


def _idg_tables(sub, w, f0, neck):
    """Interdigital design curves on the lossless line model at f0, cached: the coupling M of two rods
    shorted at opposite ends against their gap (from their split resonances, M = (f2^2 - f1^2) / (f2^2 +
    f1^2)), and the external Q of a rod tapped at a fraction u of its length from the short, through its
    tap neck (width, length)."""
    key = _sub_key(sub, w, f0, *neck)
    if key in _TABLES:
        return _TABLES[key]
    ls = sub.lossless()
    L = _rod_length(ls, w, f0)
    gaps = np.geomspace(0.03, max(8 * ls.h, 3 * w), 28)
    ms = []
    for s in gaps:
        net = mtl.Nodal()
        a, b = net.nodes(2), net.nodes(2)
        net.mtl(mtl.Array(ls, [w, w], [s]), a, b, L)
        _rod(net, ls, w, a[0], b[0], False)
        _rod(net, ls, w, a[1], b[1], True)
        r = net.resonances(0.4 * f0, 1.6 * f0, 300)
        ms.append((r[-1] ** 2 - r[0] ** 2) / (r[-1] ** 2 + r[0] ** 2) if len(r) >= 2 else 0.0)
    us = np.geomspace(0.005, 0.95, 36)
    qs = []
    arr = mtl.Array(ls, [w], [])
    for u in us:
        net = mtl.Nodal()
        a, m, b, p = net.nodes(1), net.nodes(1), net.nodes(1), net.nodes(1)
        net.mtl(arr, a, m, (1 - u) * L)                     # a: the open end at the top, b: the short
        net.mtl(arr, m, b, u * L)
        net.line(m[0], p[0], ls, neck[0], neck[1])
        _rod(net, ls, w, a[0], b[0], False)
        qs.append(net.external_q(p[0], 0.5 * f0, 1.5 * f0)[1] or np.inf)
    _TABLES[key] = (np.array(gaps), np.array(ms), us, np.array(qs))
    return _TABLES[key]


def _interp_desc(x, xs, ys):
    """xs at which the decreasing table ys(xs) equals x (log-log), clipped to the table."""
    ys, xs = np.asarray(ys), np.asarray(xs)
    ok = np.isfinite(ys) & (ys > 0)
    ly, lx = np.log(ys[ok])[::-1], np.log(xs[ok])[::-1]
    return float(np.exp(np.interp(math.log(x), ly, lx)))


def bandpass_interdigital(spec, sub, kf=1.0, kb=1.0):
    """Quarter-wave rods shorted at alternate ends, tapped feeds on the first and last rods. The gaps and
    taps come from design curves extracted on the multiconductor line model (_idg_tables): M_i,i+1 =
    FBW / sqrt(g_i g_i+1), Qe = g0 g1 / FBW. spec.trim (calibrate.refine_coupled) scales the n - 1 gaps,
    the two tap positions and the n rods' lengths."""
    notes = []
    g = gvalues(spec.response, spec.order, spec.ripple)
    n, fd, z0 = spec.order, spec.f0 * kf, spec.z0
    fbw = spec.fbw * kb
    w50, feed = sub.width(z0), _feed(spec, sub)
    wr = sub.width(spec.zstub) if spec.zstub else w50
    wt, lt = neck = _tap_neck(sub, w50)
    gaps_t, ms, us, qs = _idg_tables(sub, wr, spec.f0, neck)
    M = [fbw / math.sqrt(g[i + 1] * g[i + 2]) for i in range(n - 1)]
    if max(M) > ms.max():
        raise ValueError(f"interdigital: coupling {max(M):.3f} needs rods closer than {gaps_t[0]:.2f} mm: "
                         "narrow the band or raise --zstub")
    gaps = [_interp_desc(m, gaps_t, ms) for m in M]
    qe = [g[0] * g[1] / fbw, g[n] * g[n + 1] / fbw]
    taps = [_interp_desc(q, us, qs) for q in qe]
    tr = list(spec.trim) if spec.trim and len(spec.trim) == 2 * n + 1 else [1.0] * (2 * n + 1)
    gaps = [s * t for s, t in zip(gaps, tr[:n - 1])]
    taps = [min(0.95, u * t) for u, t in zip(taps, tr[n - 1:n + 1])]
    L = _rod_length(sub, wr, fd)
    ext = [(t - 1) * L for t in tr[n + 1:]]                 # each rod's open end past (or short of) y = 0 / L
    t_in, t_out = taps[0] * L, taps[1] * L
    for i, s in enumerate(gaps):
        _check_s(sub, notes, f"gap {i + 1}-{i + 2}", s)
    _check_w(sub, notes, "rods", wr)
    _check_w(sub, notes, "tap necks", wt)

    # layout: rod k centred at xs[k], spanning y = 0 (top) .. L (bottom); even rods shorted at the bottom
    nv = _rod_vias(wr)
    if nv:                                                   # vias inside the rods' ends
        stem, pad, dv = 0.0, wr, 0.0
    else:                                                    # a stem; the pad clear of a lengthened open end
        stem = sub.min_gap + max(0.0, *ext)
        pad = VIA_PAD
        dv = stem + VIA_PAD / 2
    if min(t_in, t_out) < wt / 2:
        # the outer rods have no neighbour on the feed's side: the neck may overlap their stem and pad
        notes.append(f"interdigital: the tap necks overlap the outer rods' via stems (taps "
                     f"{min(t_in, t_out):.2f} mm from the shorted ends)")
    xs = [feed + wr / 2]
    for s in gaps:
        xs.append(xs[-1] + wr + s)
    for k in range(n - 2):
        clear = xs[k + 2] - xs[k] - pad
        if pad > wr:
            _check_s(sub, notes, f"via pads of rods {k + 1} and {k + 3}", clear)
    short_top = [k % 2 == 1 for k in range(n)]
    y_in = L - t_in                                          # rod 1 is shorted at the bottom
    y_out = t_out if short_top[-1] else L - t_out
    geo = Geometry()
    for k in range(n):
        geo.vline(xs[k], L + ext[k] if short_top[k] else -ext[k], 0 if short_top[k] else L, wr)
        sgn = -1 if short_top[k] else 1
        y_end = 0 if short_top[k] else L
        if nv:
            yv = y_end - sgn * VIA_PAD / 2
            for j in range(nv):
                geo.vias.append((xs[k] + (j - (nv - 1) / 2) * VIA_PAD, yv))
        else:
            geo.vline(xs[k], y_end, y_end + sgn * stem, wr)
            geo.rect(xs[k] - pad / 2, y_end + sgn * stem, xs[k] + pad / 2, y_end + sgn * (stem + VIA_PAD))
            geo.vias.append((xs[k], y_end + sgn * dv))
    geo.hline(0, xs[0] - wr / 2 - lt, y_in, w50)
    geo.hline(xs[0] - wr / 2 - lt, xs[0] - wr / 2, y_in, wt)
    x_end = xs[-1] + wr / 2 + feed
    geo.hline(xs[-1] + wr / 2, xs[-1] + wr / 2 + lt, y_out, wt)
    geo.hline(xs[-1] + wr / 2 + lt, x_end, y_out, w50)
    geo.finish(x_end, _margin(sub), y_in, y_out)

    rows = [(f"rod {k + 1} ({'top' if short_top[k] else 'bottom'} shorted)", sub.line(wr)[0], wr, L + ext[k], None)
            for k in range(n)]
    rows += [(f"gap {i + 1}-{i + 2} (M {m:.4f})", '', None, None, s) for i, (m, s) in enumerate(zip(M, gaps))]
    rows += [(f"tap {name} (Qe {q:.2f}), from the short", '', wt, t, None)
             for name, q, t in (('in', qe[0], t_in), ('out', qe[1], t_out))]
    rows.append(("tap necks", sub.line(wt)[0], wt, lt, None))

    def model(s_):
        arr = mtl.Array(s_, [wr] * n, gaps)
        net = mtl.Nodal()
        hs = sorted({0.0, y_in, y_out, L})
        rows_ = [net.nodes(n) for _ in hs]                   # rows_[j][k]: rod k at height hs[j]
        for j in range(len(hs) - 1):
            if hs[j + 1] - hs[j] > 1e-6:
                net.mtl(arr, rows_[j], rows_[j + 1], hs[j + 1] - hs[j])
            else:
                for k in range(n):
                    net.series(rows_[j][k], rows_[j + 1][k], lambda f: np.full(np.shape(f), 1e9, complex))
        for k in range(n):
            _rod(net, s_, wr, rows_[0][k], rows_[-1][k], short_top[k], ext[k], dv)
        pin, pout = rows_[hs.index(y_in)][0], rows_[hs.index(y_out)][n - 1]
        fl = MLine(s_, w50, feed - lt - 0.5)
        return [fl, MLine(s_, wt, lt), net.two_port(pin, pout), MLine(s_, wt, lt), fl]
    notes.append(f"interdigital: {n * max(1, nv)} ground vias ({VIA_DRILL} mm drill); gaps and taps from the "
                 "multiconductor line model")
    return Design(spec, sub, kf, kb, geo, None, model, rows, g, notes)


# ------------------------------------------------------------------------------- band-stop
def bandstop_stub(spec, sub, kf=1.0, kb=1.0):
    """Open quarter-wave stubs Z_k = 4 Z0 / (pi g_k FBW) a quarter wave apart on a Z0 line; spec.trim
    (set by calibrate.refine_bandstop) scales the n stub and n - 1 line impedances."""
    notes = []
    _odd_order(spec, notes)
    g = gvalues(spec.response, spec.order, spec.ripple)
    n, fd, z0 = spec.order, spec.f0 * kf, spec.z0
    zmin, zmax = _z_limits(sub)
    zs = [4 * z0 / (math.pi * g[k + 1] * spec.fbw * kb) for k in range(n)]
    zl = [z0] * (n - 1)
    if spec.trim and len(spec.trim) == 2 * n - 1:
        zs = [z * t for z, t in zip(zs, spec.trim[:n])]
        zl = [z * t for z, t in zip(zl, spec.trim[n:])]
    if max(zs) > zmax:
        notes.append(f"stub impedances up to {max(zs):.0f} ohm exceed the {zmax:.0f} ohm of a minimum-width strip: "
                     "those stubs are clamped to the minimum width, so the stop band comes out wider than asked")
        zs = [min(z, zmax) for z in zs]
    wst = [sub.width(z) for z in zs]
    wl = [sub.width(z) for z in zl]
    w50, feed = sub.width(z0), _feed(spec, sub)
    cover = [(wst[k] + wst[k + 1]) / 2 for k in range(n - 1)]     # see lowpass_stub
    lc = [math.pi / 2 / _beta(sub, wl[k], fd) + cover[k] for k in range(n - 1)]
    # a stub's length runs from the main line's edge: the wider of the lines either side of it
    wj = [max(wl[k - 1] if k else w50, wl[k] if k < n - 1 else w50) for k in range(n)]
    lst = [math.pi / 2 / _beta(sub, w, fd) - sub.open_end(w) - wj[k] / 2 for k, w in enumerate(wst)]
    rows = []
    for k in range(n):
        rows.append((f"stub {k + 1} (open)", zs[k], wst[k], lst[k], None))
        _check_w(sub, notes, f"stub {k + 1}", wst[k])
        if k < n - 1:
            rows.append((f"line {k + 1}-{k + 2}", zl[k], wl[k], lc[k], None))
            _check_w(sub, notes, f"line {k + 1}-{k + 2}", wl[k])

    geo = Geometry()
    x = feed
    geo.hline(0, x, 0, w50)
    side = -1
    for k in range(n):
        geo.vline(x, 0, side * (wj[k] / 2 + lst[k]), wst[k])
        side = -side
        if k < n - 1:
            geo.hline(x, x + lc[k], 0, wl[k])
            x += lc[k]
    geo.hline(x, x + feed, 0, w50)
    geo.finish(x + feed, _margin(sub), 0, 0)

    ideal = []
    for k in range(n):
        ideal.append(Ideal.stub(zs[k], math.pi / 2, fd, 'open'))
        if k < n - 1:
            ideal.append(Ideal.line(zl[k], math.pi / 2, fd))

    def model(s_):
        net = [MLine(s_, w50, feed - 0.5)]
        for k in range(n):
            net.append(MStub(s_, wst[k], lst[k] + wj[k] / 2, 'open'))
            if k < n - 1:
                net.append(MLine(s_, wl[k], lc[k] - cover[k]))
        return net + [MLine(s_, w50, feed - 0.5)]
    return Design(spec, sub, kf, kb, geo, ideal, model, rows, g, notes)


BUILDERS = {
    ('lowpass', 'stepped'): lowpass_stepped,
    ('lowpass', 'stub'): lowpass_stub,
    ('lowpass', 'radial'): lowpass_radial,
    ('highpass', 'stub'): highpass_stub,
    ('bandpass', 'coupled'): bandpass_coupled,
    ('bandpass', 'hairpin'): bandpass_hairpin,
    ('bandpass', 'interdigital'): bandpass_interdigital,
    ('bandpass', 'endcoupled'): bandpass_endcoupled,
    ('bandpass', 'stub'): bandpass_stub,
    ('bandstop', 'stub'): bandstop_stub,
    ('bandstop', 'radial'): bandstop_radial,
}


def prepare(spec):
    """Adjust the spec to what its topology can realise (in place); returns notes on what changed."""
    spec.check()
    notes = []
    if spec.kind in ('lowpass', 'bandstop'):
        _odd_order(spec, notes)
    if spec.topology == 'stub' and spec.kind in ('bandpass', 'highpass') and spec.order < 3:
        notes.append("stub band-pass needs at least 3 stubs: using order 3")
        spec.order = 3
    return notes


def build(spec, sub, kf=1.0, kb=1.0):
    spec.check()
    return BUILDERS[(spec.kind, spec.topology)](spec, sub, kf, kb)
