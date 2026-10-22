"""
Multiconductor microstrip lines and a nodal network around them, for filters whose resonators couple to
more than one neighbour over their whole length (interdigital rods, hairpin arms): the ABCD cascade of
circuit.py cannot represent a line shared by two coupled sections.

    arr = Array(sub, widths=[w, w, w], gaps=[s1, s2])         # N parallel strips, nearest-neighbour coupling
    net = Nodal()
    a, b = net.nodes(3), net.nodes(3)                        # the strips' ends at y = 0 and y = L
    net.mtl(arr, a, b, L)
    net.via(a[0], sub, drill); net.open(b[0], sub, w) ...
    ab = net.two_port(p1, p2)                                # an element for circuit.cascade / sparams

The per-unit-length capacitance matrix of two or more strips is the full Maxwell matrix of the cross
section (strip_caps: a quasi-static moment method on zero-thickness strips, every strip coupled to every
other, not only to its neighbours; next-but-one rods of an interdigital array couple a tenth as strongly
as neighbours). The copper's thickness and the dispersion come from the closed forms on top of it: the
nearest-neighbour matrix built from each adjacent pair's even / odd mode capacitances (Kirschning-Jansen,
the pair's mean width; a single strip's from Hammerstad-Jensen), at f and thickness t, less the same at
zero frequency and thickness. The inductance matrix is that of the strips in air (L = C_air^-1 / c^2), so
the modes of an inhomogeneous array get their own velocities. Loss is a series resistance 2 Rs / w per
strip and a shunt conductance from tan d on the dielectric's share of C.
"""
import math
import numpy as np

from .microstrip import C0, MU0

EPS0 = 1.0 / (MU0 * C0 ** 2)


def _iln(u, a):
    """An antiderivative of ln(u^2 + a^2) in u (a >= 0)."""
    with np.errstate(divide='ignore', invalid='ignore'):
        r = u * np.log(u * u + a * a) - 2 * u + 2 * a * np.arctan2(u, a)
    return np.where(u == 0, 0.0, r)


def strip_caps(widths, gaps, h, er, per=16):
    """Maxwell capacitance matrix (F/m) of side-by-side zero-thickness strips (widths, edge gaps, mm) on a
    slab of height h (mm) and permittivity er over a ground plane. Pulse charges on `per` edge-refined
    segments of each strip, matched at their centres to the strip's potential; the Green's function of a
    line charge on the slab's surface is the image series
        G(x) = sum_n K^n ln((x^2 + (2 (n + 1) h)^2) / (x^2 + (2 n h)^2)) / (2 pi eps0 (1 + er)),
    K = (1 - er) / (1 + er). Within 1 % of Hammerstad-Jensen and Kirschning-Jansen (single and coupled
    strips)."""
    lo, hi, x = [], [], 0.0
    t = 0.5 * (1 - np.cos(np.linspace(0, np.pi, per + 1)))
    for i, w in enumerate(widths):
        e = x + w * t
        lo.append(e[:-1])
        hi.append(e[1:])
        x += w + (gaps[i] if i < len(gaps) else 0.0)
    lo, hi = np.concatenate(lo) * 1e-3, np.concatenate(hi) * 1e-3
    xc = 0.5 * (lo + hi)
    owner = np.repeat(np.arange(len(widths)), per)
    K = (1 - er) / (1 + er)
    nt = 1 if K == 0 else int(math.ceil(math.log(1e-7) / math.log(abs(K)))) + 1
    d1, d0 = xc[:, None] - lo[None, :], xc[:, None] - hi[None, :]

    def seg(a):                                              # the integral of ln(x^2 + a^2) over each segment
        return _iln(d1, a) - _iln(d0, a)
    P = np.zeros((len(xc), len(xc)))
    prev = seg(0.0)
    for n in range(nt):
        cur = seg(2 * (n + 1) * h * 1e-3)
        P += K ** n * (cur - prev)
        prev = cur
    P /= 2 * math.pi * EPS0 * (1 + er)
    N = len(widths)
    q = np.linalg.solve(P, (owner[:, None] == np.arange(N)[None, :]).astype(float)) * (hi - lo)[:, None]
    return np.array([[q[owner == i, j].sum() for j in range(N)] for i in range(N)])


def _nn_caps(sub, widths, gaps, f):
    """The closed-form (C (F, N, N), C_air) of strips coupled to their neighbours only, at f (Hz, array;
    None: quasi-static, F = 1)."""
    n = len(widths)
    disp = (lambda ee0, w: np.array([ee0])) if f is None else (lambda ee0, w: sub.disperse(ee0, w, f))
    iso = [sub.line(w) for w in widths]
    C = np.zeros((1 if f is None else len(f), n, n))
    Ca = np.zeros((n, n))
    for i, (z0, ee0) in enumerate(iso):
        ca = 1.0 / (C0 * z0 * math.sqrt(ee0))
        Ca[i, i] += ca
        C[:, i, i] += ca * disp(ee0, widths[i])
    for i, s in enumerate(gaps):
        wp = 0.5 * (widths[i] + widths[i + 1])
        ze, zo, ee0, eo0 = sub.coupled(wp, s)
        cea, coa = 1.0 / (C0 * ze * math.sqrt(ee0)), 1.0 / (C0 * zo * math.sqrt(eo0))
        ce, co = cea * disp(ee0, wp), coa * disp(eo0, wp)
        for k in (i, i + 1):                                  # the even mode: ground capacitance shielded
            z0, ee0k = iso[k]
            cik = 1.0 / (C0 * z0 * math.sqrt(ee0k))
            Ca[k, k] -= cik - cea
            C[:, k, k] -= cik * disp(ee0k, widths[k]) - ce
        cm, cma = 0.5 * (co - ce), 0.5 * (coa - cea)
        C[:, i, i + 1] = C[:, i + 1, i] = -cm
        Ca[i, i + 1] = Ca[i + 1, i] = -cma
        C[:, i, i] += cm
        C[:, i + 1, i + 1] += cm
        Ca[i, i] += cma
        Ca[i + 1, i + 1] += cma
    return C, Ca

class Array:
    """N parallel strips of widths (mm), edge gaps (mm, N - 1 of them) on a Substrate."""

    def __init__(self, sub, widths, gaps):
        self.sub, self.w, self.s = sub, list(widths), list(gaps)
        n = self.n = len(self.w)
        assert len(self.s) == n - 1
        self.dC, self.dCa = 0.0, 0.0
        if n > 1:                                             # the field solution less its closed-form estimate
            s0 = sub.with_(t=0.0)
            C, Ca = _nn_caps(s0, self.w, self.s, None)
            self.dC = strip_caps(self.w, self.s, sub.h, sub.er) - C[0]
            self.dCa = strip_caps(self.w, self.s, sub.h, 1.0) - Ca

    def _caps(self, f):
        """(C (F, N, N), C_air (N, N)) per metre over f (Hz, array)."""
        C, Ca = _nn_caps(self.sub, self.w, self.s, f)
        return C + self.dC, Ca + self.dCa


    def y(self, f, length):
        """Admittance matrices (F, 2N, 2N) of a length (mm) of the array: ports 0..N-1 at one end,
        N..2N-1 at the other, currents into the lines."""
        sub = self.sub
        f = np.atleast_1d(np.asarray(f, float))
        C, Ca = self._caps(f)
        L = np.linalg.inv(Ca) / C0 ** 2
        om = (2 * np.pi * f)[:, None, None]
        Z = 1j * om * L
        if sub.sigma and math.isfinite(sub.sigma):
            rs = np.sqrt(np.pi * f * MU0 / sub.sigma)
            Z = Z + rs[:, None, None] * np.diag([2 / (w * 1e-3) for w in self.w])
        er = sub.er
        Y = 1j * om * C
        if er > 1 and sub.tand:
            Y = Y + om * sub.tand * (C - Ca) * er / (er - 1)
        lam, T = np.linalg.eig(Z @ Y)
        g = np.sqrt(lam)
        g = np.where(g.real < 0, -g, g)                        # decaying forward waves
        Ti = np.linalg.inv(T)
        Yc = np.linalg.solve(Z, T * g[:, None, :]) @ Ti
        gl = g * length * 1e-3
        y11 = Yc @ (T * (1 / np.tanh(gl))[:, None, :]) @ Ti
        y12 = -Yc @ (T * (1 / np.sinh(gl))[:, None, :]) @ Ti
        return np.concatenate([np.concatenate([y11, y12], 2), np.concatenate([y12, y11], 2)], 1)


class Nodal:
    """A linear network of numbered nodes (ground is implicit), reduced to a 2-port."""

    def __init__(self):
        self.n = 0
        self.elems = []                                       # (node list, yfun(f) -> (F, k, k))

    def nodes(self, k):
        r = list(range(self.n, self.n + k))
        self.n += k
        return r

    def add(self, nodes, yfun):
        self.elems.append((list(nodes), yfun))

    def mtl(self, arr, a, b, length):
        """The array between node lists a (one end) and b (the other), length mm."""
        self.add(list(a) + list(b), lambda f: arr.y(f, length))

    def shunt(self, node, yfun):
        self.add([node], lambda f: np.asarray(yfun(f), complex).reshape(-1, 1, 1))

    def series(self, n1, n2, yfun):
        def y(f):
            v = np.asarray(yfun(f), complex)
            return np.stack([np.stack([v, -v], -1), np.stack([-v, v], -1)], -2)
        self.add([n1, n2], y)

    def via(self, node, sub, drill, count=1):
        """A short to ground through `count` vias of drill mm (their inductance)."""
        lv = sub.via_inductance(drill) / max(count, 1)
        self.shunt(node, lambda f: 1 / (2j * np.pi * np.asarray(f) * lv))

    def open(self, node, sub, w, extra=0.0):
        """A strip's open end: the end capacitance, as the admittance of its length extension, plus `extra`
        mm of strip beyond the node (negative: the strip ends that much short of it)."""
        z0, ee0 = sub.line(w)
        dl = (sub.open_end(w) + extra) * 1e-3

        def y(f):
            f = np.asarray(f, float)
            ee = sub.disperse(ee0, w, f)
            return 1j * np.tan(2 * np.pi * f * np.sqrt(ee) / C0 * dl) / z0
        self.shunt(node, y)

    def line(self, n1, n2, sub, w, length):
        """A single microstrip line between two nodes (its 2-port admittance)."""
        arr = Array(sub, [w], [])
        self.add([n1, n2], lambda f: arr.y(f, length))

    def two_port(self, p1, p2):
        """ABCD (F, 2, 2) function of the network seen between nodes p1 and p2 (to ground)."""
        def ab(f):
            f = np.atleast_1d(np.asarray(f, float))
            Y = np.zeros((len(f), self.n, self.n), complex)
            for nodes, yf in self.elems:
                idx = np.array(nodes)
                Y[:, idx[:, None], idx[None, :]] += yf(f)
            ports = [p1, p2]
            inner = [k for k in range(self.n) if k not in ports]
            Ypp = Y[:, ports][:, :, ports]
            if inner:
                Ypi = Y[:, ports][:, :, inner]
                Yip = Y[:, inner][:, :, ports]
                Yii = Y[:, inner][:, :, inner]
                Ypp = Ypp - Ypi @ np.linalg.solve(Yii, Yip)
            y11, y12, y21, y22 = Ypp[:, 0, 0], Ypp[:, 0, 1], Ypp[:, 1, 0], Ypp[:, 1, 1]
            det = y11 * y22 - y12 * y21
            return np.stack([np.stack([-y22 / y21, -1 / y21], -1), np.stack([-det / y21, -y11 / y21], -1)], -2)
        return ab

    # ---------------------------------------------------------------- extraction (lossless networks)
    def _ymat(self, f):
        f = np.atleast_1d(np.asarray(f, float))
        Y = np.zeros((len(f), self.n, self.n), complex)
        for nodes, yf in self.elems:
            idx = np.array(nodes)
            Y[:, idx[:, None], idx[None, :]] += yf(f)
        return Y

    def resonances(self, f_lo, f_hi, n=400):
        """Natural frequencies (Hz) of the network with every node unloaded, between f_lo and f_hi: the zeros
        of det Y. Build it on a lossless substrate: Y is then j B with B real, and det B changes sign at a
        zero (and at a pole, where |det B| is large on both sides instead)."""
        from scipy.optimize import brentq
        f = np.linspace(f_lo, f_hi, n)

        def d(x):
            B = self._ymat([x])[0].imag
            return np.linalg.det(B / np.abs(B).max())
        v = np.array([d(x) for x in f])
        out = []
        for k in range(n - 1):
            if v[k] == 0 or np.sign(v[k]) != np.sign(v[k + 1]):
                if min(abs(v[k]), abs(v[k + 1])) < 0.5 * max(abs(v[max(k - 1, 0)]), abs(v[min(k + 2, n - 1)])):
                    out.append(brentq(d, f[k], f[k + 1], xtol=1e-3 * (f[1] - f[0])))
        return out

    def external_q(self, node, f_lo, f_hi, z0=50.0):
        """(f0, Qe) of the resonance between f_lo and f_hi seen from `node` loaded by z0 ohm: the input
        susceptance B through zero, Qe = z0 (w0 / 2) dB/dw. Lossless network."""
        from scipy.optimize import brentq
        others = [k for k in range(self.n) if k != node]

        def b(x):
            Y = self._ymat([x])[0]
            if others:
                yo = Y[np.ix_([node], others)]
                y = Y[node, node] - (yo @ np.linalg.solve(Y[np.ix_(others, others)], Y[np.ix_(others, [node])]))[0, 0]
            else:
                y = Y[node, node]
            return y.imag
        f = np.linspace(f_lo, f_hi, 200)
        v = np.array([b(x) for x in f])
        for k in range(len(f) - 1):
            if v[k] < 0 <= v[k + 1]:                          # a parallel resonance: B rising through zero
                f0 = brentq(b, f[k], f[k + 1], xtol=1e-6 * f[k])
                h = 1e-4 * f0
                slope = (b(f0 + h) - b(f0 - h)) / (2 * h)     # dB/df
                return f0, z0 * f0 / 2 * slope
        return None, None
