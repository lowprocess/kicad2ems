"""
A small ABCD circuit simulator for the filters: ideal (TEM) elements for checking a synthesis, and
microstrip elements on a Substrate (dispersion, loss, open ends, gaps, vias) for the expected response
of a layout.

    net = [MLine(sub, w, 12.0), MStub(sub, w2, 8.4, 'open'), MLine(sub, w, 12.0)]
    s11, s21 = sparams(net, f)          # complex arrays over f (Hz), 50 ohm ports
"""
import math
import numpy as np

from .microstrip import C0


def _line_abcd(z, gl):
    """ABCD of a line of impedance z (array or scalar) and complex electrical length gl = gamma * l."""
    ch, sh = np.cosh(gl), np.sinh(gl)
    return np.stack([np.stack([ch, z * sh], -1), np.stack([sh / z, ch], -1)], -2)


def _shunt(y):
    one, zero = np.ones_like(y), np.zeros_like(y)
    return np.stack([np.stack([one, zero], -1), np.stack([y, one], -1)], -2)


def _series(z):
    one, zero = np.ones_like(z), np.zeros_like(z)
    return np.stack([np.stack([one, z], -1), np.stack([zero, one], -1)], -2)


def _z2abcd(z11, z12, z21, z22):
    det = z11 * z22 - z12 * z21
    return np.stack([np.stack([z11 / z21, det / z21], -1), np.stack([1 / z21, z22 / z21], -1)], -2)


def _coupled_abcd(ze, zo, gle, glo):
    """2-port of a coupled-line section driven at diagonally opposite ends, the other two ends open."""
    zse, zso = ze / np.tanh(gle), zo / np.tanh(glo)            # self (Z coth gl)
    zte, zto = ze / np.sinh(gle), zo / np.sinh(glo)            # transfer (Z csch gl)
    z11 = 0.5 * (zse + zso)
    z12 = 0.5 * (zte - zto)                                    # line a near end -> line b far end
    return _z2abcd(z11, z12, z12, z11)


# ------------------------------------------------------------------------------- ideal elements
class Ideal:
    """TEM elements: z ohm, electrical length theta0 (rad) at f0."""

    @staticmethod
    def line(z, theta0, f0):
        return lambda f: _line_abcd(np.full(f.shape, z, complex), 1j * theta0 * f / f0)

    @staticmethod
    def stub(z, theta0, f0, kind='open'):
        def ab(f):
            t = np.tan(theta0 * f / f0).astype(complex)
            y = 1j * t / z if kind == 'open' else 1 / (1j * z * t)
            return _shunt(y)
        return ab

    @staticmethod
    def coupled(ze, zo, theta0, f0):
        return lambda f: _coupled_abcd(ze, zo, 1j * theta0 * f / f0, 1j * theta0 * f / f0)

    @staticmethod
    def series_c(c):
        return lambda f: _series(1 / (2j * np.pi * f * c))

    @staticmethod
    def shunt_c(c):
        return lambda f: _shunt(2j * np.pi * f * c)


# ------------------------------------------------------------------------------- microstrip elements
def _gamma(sub, w, z0, ee0, f):
    ee = np.array([sub.disperse(ee0, w, fi) for fi in f])
    a = np.array([sub.alpha(w, z0, e, fi) for e, fi in zip(ee, f)])
    return a + 1j * 2 * np.pi * f * np.sqrt(ee) / C0           # per metre


def MLine(sub, w, length):
    """A microstrip line of width w, length (mm)."""
    z0, ee0 = sub.line(w)

    def ab(f):
        return _line_abcd(np.full(f.shape, z0, complex), _gamma(sub, w, z0, ee0, f) * length * 1e-3)
    return ab


def MStub(sub, w, length, kind='open', vias=1, drill=0.3):
    """A shunt stub of width w, physical length (mm). open: the open-end extension is added; short: ends in
    `vias` vias of `drill` mm in parallel."""
    z0, ee0 = sub.line(w)
    lv = sub.via_inductance(drill) / max(vias, 1)

    def ab(f):
        if kind == 'open':
            gl = _gamma(sub, w, z0, ee0, f) * (length + sub.open_end(w)) * 1e-3
            y = np.tanh(gl) / z0
        else:
            gl = _gamma(sub, w, z0, ee0, f) * length * 1e-3
            zl = 2j * np.pi * f * lv
            t = np.tanh(gl)
            y = (z0 + zl * t) / (z0 * (zl + z0 * t))
        return _shunt(y)
    return ab


def MCoupled(sub, w, s, length, open_ext=True):
    """An edge-coupled section (diagonal ports, the other ends open): open_ext adds each open end's
    extension (as the layout shortens the strips by it)."""
    ze, zo, ee0, eo0 = sub.coupled(w, s)
    L = (length + (sub.open_end(w) if open_ext else 0.0)) * 1e-3

    def ab(f):
        ge = _gamma(sub, w, ze, ee0, f)
        go = _gamma(sub, w, zo, eo0, f)
        return _coupled_abcd(ze, zo, ge * L, go * L)
    return ab


def MGap(sub, w, s):
    """A series gap in a strip: pi of Cp, Cg, Cp."""
    cp, cg = sub.gap(w, s)

    def ab(f):
        om = 2 * np.pi * f
        return _shunt(1j * om * cp) @ _series(1 / (1j * om * cg)) @ _shunt(1j * om * cp)
    return ab


# ------------------------------------------------------------------------------- network
def cascade(net, f):
    f = np.asarray(f, float)
    m = np.broadcast_to(np.eye(2, dtype=complex), (len(f), 2, 2)).copy()
    for el in net:
        m = m @ el(f)
    return m


def sparams(net, f, z0=50.0):
    """(S11, S21) of the cascade between z0 ports."""
    m = cascade(net, f)
    a, b, c, d = m[:, 0, 0], m[:, 0, 1], m[:, 1, 0], m[:, 1, 1]
    den = a + b / z0 + c * z0 + d
    return (a + b / z0 - c * z0 - d) / den, 2 / den


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-15))
