"""
The microwave half of the analyzer, in the frequency domain.

A signal is a list of tones (frequency, power).  Every stage maps a list of
tones to a list of tones: a linear part scales each by its |S21|, a
non-linear part adds the harmonics and intermodulation products its
intercepts give, and a mixer adds every product |n LO +/- m f| at the level
its conversion loss, isolations and spur table give.  Nothing is sampled, so
a 0-16 GHz spectrum costs what its tones cost.

Noise goes the same way, as a power density against frequency: each stage
turns the density at its input into the density at its output (a passive
part adds its own thermal noise, an amplifier its noise figure, a mixer folds
both sidebands and the LO's odd harmonics, and adds the LO's phase noise as a
skirt around each strong product it makes).  The filters after a mixer then
shape those skirts as they shape everything else.
"""

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from .parts import GHZ, INF, Part, table

KT0 = 10 ** (-174.0 / 10)        # mW/Hz at 290 K (-173.98 dBm/Hz)
FLOOR_DBM = -185.0               # a product under this is dropped


def mw(dbm):
    return 10 ** (np.asarray(dbm, float) / 10)


def dbm(x):
    return 10 * np.log10(np.maximum(np.asarray(x, float), 1e-30))


# -------------------------------------------------------------------- tones

class Tones:
    def __init__(self, f=(), p=(), tag=()):
        self.f = np.asarray(f, float).reshape(-1)
        self.p = np.asarray(p, float).reshape(-1)
        self.tag = np.asarray(tag, object).reshape(-1)
        if self.tag.size != self.f.size:
            self.tag = np.full(self.f.size, "", object)

    def __len__(self):
        return self.f.size

    def copy(self):
        return Tones(self.f.copy(), self.p.copy(), self.tag.copy())

    def total_dbm(self):
        return float(dbm(mw(self.p).sum())) if len(self) else -300.0

    def strongest(self, n, within=90.0):
        """Indices of the n strongest tones, those within `within` dB of
        the strongest."""
        if not len(self):
            return np.zeros(0, int)
        order = np.argsort(-self.p)[:n]
        return order[self.p[order] > self.p[order[0]] - within]

    @staticmethod
    def join(*ts):
        ts = [t for t in ts if len(t)]
        if not ts:
            return Tones()
        return Tones(np.concatenate([t.f for t in ts]),
                     np.concatenate([t.p for t in ts]),
                     np.concatenate([t.tag for t in ts]))

    def pruned(self, floor=FLOOR_DBM, fmin=0.0, fmax=INF):
        """Without the tones under the floor or outside the band, and with
        those on one frequency (to 1 Hz) added in power."""
        keep = (self.p > floor) & (self.f >= fmin) & (self.f <= fmax)
        f, p, tag = self.f[keep], self.p[keep], self.tag[keep]
        if not f.size:
            return Tones()
        key = np.round(f).astype(np.int64)
        uniq, inv = np.unique(key, return_inverse=True)
        if uniq.size == f.size:
            return Tones(f, p, tag)
        lin = np.bincount(inv, mw(p), uniq.size)
        # the tag of the strongest contributor
        order = np.lexsort((p, inv))
        last = np.flatnonzero(np.diff(np.append(inv[order], -1)))
        return Tones(uniq.astype(float), dbm(lin), tag[order][last])


def compression_db(p_in_dbm, ip1db):
    """The gain a stage has lost with this total power at its input: 1 dB
    at ip1db, and the output levels off 1.2 dB above that point's."""
    if ip1db >= INF:
        return 0.0
    r = 10 ** ((p_in_dbm - (ip1db + 1.16)) / 10)
    return float(5 * np.log10(1 + r * r))


def intermod(t, iip3, iip2, top=6, name=""):
    """The second and third-order products of the strongest tones, at the
    input: harmonics, two-tone and three-tone products."""
    idx = t.strongest(top)
    if not idx.size or (iip3 >= INF and iip2 >= INF):
        return Tones()
    f, p, tag = t.f[idx], t.p[idx], t.tag[idx]
    of, op, ot = [], [], []

    def add(freq, lev, what):
        if freq > 0 and lev > FLOOR_DBM:
            of.append(freq)
            op.append(lev)
            ot.append(what)

    for i in range(f.size):
        if iip2 < INF:
            add(2 * f[i], 2 * p[i] - iip2 - 6.0,
                "%s HD2 of [%s]" % (name, tag[i]))
        if iip3 < INF:
            add(3 * f[i], 3 * p[i] - 2 * iip3 - 9.54,
                "%s HD3 of [%s]" % (name, tag[i]))
    for i, j in combinations(range(f.size), 2):
        pair = "[%s],[%s]" % (tag[i], tag[j])
        if iip2 < INF:
            lev = p[i] + p[j] - iip2
            add(f[i] + f[j], lev, "%s IM2 %s" % (name, pair))
            add(abs(f[i] - f[j]), lev, "%s IM2 %s" % (name, pair))
        if iip3 < INF:
            for a, b in ((i, j), (j, i)):
                lev = 2 * p[a] + p[b] - 2 * iip3
                add(abs(2 * f[a] - f[b]), lev, "%s IM3 %s" % (name, pair))
                add(2 * f[a] + f[b], lev, "%s IM3 %s" % (name, pair))
    if iip3 < INF:
        for i, j, k in combinations(range(f.size), 3):
            lev = p[i] + p[j] + p[k] - 2 * iip3 + 6.0
            what = "%s triple beat" % name
            for s in ((1, 1, -1), (1, -1, 1), (-1, 1, 1)):
                add(abs(s[0] * f[i] + s[1] * f[j] + s[2] * f[k]), lev, what)
    return Tones(of, op, ot)


# ------------------------------------------------------------------- stages

@dataclass
class State:
    """One tuning: the LO frequencies, and what the tone pass leaves for
    the noise pass (the strong products each mixer made)."""
    lo: dict
    synth: dict
    skirts: dict = field(default_factory=dict)
    levels: list = field(default_factory=list)   # (stage, total dBm in)


class Lin:
    """A part: its gain, and its distortion when it has intercepts."""

    def __init__(self, part: Part):
        self.part = part
        self.name = part.name

    def tones(self, t, st):
        if not len(t):
            return t
        p = self.part
        fref = t.f[np.argmax(t.p)]
        tot = t.total_dbm()
        st.levels.append((self.name, tot))
        im = intermod(t, p.in_ip3(fref), p.in_ip2(fref), name=p.name)
        comp = compression_db(tot, p.in_p1db(fref))
        out = Tones.join(t, im)
        out.p = out.p + p.g(out.f) - comp
        return out.pruned()

    def noise(self, nin, st):
        p = self.part

        def nout(f):
            g = mw(p.g(f))
            if p.nf is None:
                return g * nin(f) + np.maximum(1 - g, 0.0) * KT0
            return g * (nin(f) + (mw(p.noise_figure(f)) - 1) * KT0)
        return nout

    def wanted(self, f, st):
        return f, self.part.g(f)


@dataclass
class Mixer:
    """Mini-Circuits SIM-83+, a level 7 double-balanced diode mixer: RF and
    LO 2.3-8 GHz, IF DC-3 GHz.

    From its datasheet (figures read from search results: the datasheet could
    not be fetched): conversion loss 6.0 dB typical (7.8 maximum), LO-RF
    isolation 35 dB typical (27 minimum) at 2.3-3.2 GHz, LO-IF isolation 23 dB
    typical (15 minimum), IP3 +12 dBm.

    Assumed, and marked so in the report:
      * the 1 dB compression point, +1 dBm at the input (a level 7 mixer's)
      * the single-tone spur table: the textbook table for a double-balanced
        mixer (after Henderson, Watkins-Johnson Tech-notes 17-2), taken at
        -10 dBm, with each product's (m - 1) dB per dB slope
      * the roll-off of each port outside its band
      * the LO's harmonics leaking to the output
      * 25 dB rejection of the LO's amplitude noise at the IF
    Bench numbers for the SIM-83+ should replace these."""
    name: str = "SIM-83+"
    source: str = ("datasheet typ. (CL, isolations, IP3, via search results); "
                   "spur table, port roll-off and P1dB assumed")
    cl_db: float = 6.0
    iip3: float = 12.0
    ip1db: float = 1.0
    iso_lr_db: float = 35.0
    iso_li_db: float = 23.0
    pref_dbm: float = -10.0
    # spur[m][n]: dB under the (1, 1) product, signal harmonic m, LO
    # harmonic n = 0..7
    spur: tuple = ((), (24, 0, 35, 13, 40, 24, 45, 28),
                   (73, 73, 74, 70, 71, 64, 69, 64),
                   (67, 64, 69, 50, 77, 47, 74, 44))
    lo_harm_dbc: tuple = (0.0, 0.0, -25.0, -15.0, -35.0, -25.0)
    am_reject_db: float = 25.0
    r_port: object = field(default_factory=lambda: table(
        [(0.3 * GHZ, -25), (1.0 * GHZ, -10), (1.7 * GHZ, -3), (2.0 * GHZ, -1),
         (2.3 * GHZ, 0), (8.0 * GHZ, 0), (9.5 * GHZ, -3), (12 * GHZ, -10),
         (16 * GHZ, -20), (24 * GHZ, -30)]))
    i_port: object = field(default_factory=lambda: table(
        [(0, 0), (3.0 * GHZ, 0), (3.5 * GHZ, -1.5), (4.5 * GHZ, -4),
         (6.0 * GHZ, -10), (9.0 * GHZ, -20), (16 * GHZ, -30)]))


class Mix:
    """A mixer in the chain.  mode 'up': in at the IF port, out at the RF
    port (the first mixer, used backwards); 'down': in at RF, out at IF."""

    def __init__(self, mixer, lo, mode, out=(0.0, 14 * GHZ), spur_top=12):
        self.mx, self.lo, self.mode, self.out = mixer, lo, mode, out
        self.name = "%s (%s, %s)" % (mixer.name, lo.upper(), mode)
        self.spur_top = spur_top

    def _ports(self):
        mx = self.mx
        if self.mode == "up":
            return mx.i_port, mx.r_port, mx.iso_lr_db
        return mx.r_port, mx.i_port, mx.iso_li_db

    def tones(self, t, st):
        mx = self.mx
        flo, syn = st.lo[self.lo], st.synth[self.lo]
        pin, pout, iso = self._ports()
        lo_dbm = syn.out_dbm
        of, op, ot = [], [], []
        skirts = []
        if len(t):
            tot = t.total_dbm()
            st.levels.append((self.name, tot))
            comp = compression_db(tot, mx.ip1db)
            t = Tones.join(t, intermod(t, mx.iip3, INF, name=mx.name))
            at_port = t.p + pin(t.f)
            big = set(t.strongest(self.spur_top, 70.0).tolist())
            for m in (1, 2, 3):
                # the (m - 1) dB/dB slope, held past +10 dB over the
                # reference (the table's own level)
                slope = (m - 1) * np.minimum(at_port - mx.pref_dbm, 10.0)
                for n in range(8):
                    for sign in ((1,) if n == 0 else (1, -1)):
                        fo = np.abs(n * flo + sign * m * t.f)
                        lev = (at_port - mx.cl_db - mx.spur[m][n] + slope
                               + pout(fo) - comp)
                        ok = ((lev > FLOOR_DBM) & (fo >= self.out[0])
                              & (fo <= self.out[1]))
                        if n == 0:
                            what = "%dx[%%s] through %s" % (m, mx.name)
                        else:
                            what = "%d%s%+d[%%s]" % (n, self.lo.upper(),
                                                     sign * m)
                        for i in np.flatnonzero(ok):
                            of.append(fo[i])
                            op.append(lev[i])
                            ot.append(what % t.tag[i])
                        if m == 1 and n in (1, 3):
                            # the LO's noise rides on these, wherever they
                            # land (in or out of the band kept)
                            for i in big:
                                if lev[i] > -110.0:
                                    skirts.append((fo[i], mw(lev[i]), n))
            # the LO's own spurs (phase-detector sidebands) as conversions
            for off, dbc in syn.spurs:
                for i in big:
                    for s1 in (1, -1):
                        for s2 in (1, -1):
                            fo = abs(flo + s1 * off + s2 * t.f[i])
                            lev = (at_port[i] - mx.cl_db + dbc + pout(fo)
                                   - comp)
                            if (lev > FLOOR_DBM
                                    and self.out[0] <= fo <= self.out[1]):
                                of.append(fo)
                                op.append(float(lev))
                                ot.append("%s spur %+.0f MHz on [%s]" % (
                                    self.lo.upper(), s1 * off / 1e6,
                                    t.tag[i]))
        # the LO and its harmonics leaking to the output port
        for n in range(1, len(mx.lo_harm_dbc)):
            fo = n * flo
            lev = float(lo_dbm + mx.lo_harm_dbc[n] - iso + pout(fo))
            if self.mode == "up":
                # the LO's noise at the first IF comes out of the RF port
                # with the LO (a 'down' mixer's is the AM term in noise())
                skirts.append((fo, mw(lev), n))
            if self.out[0] <= fo <= self.out[1]:
                of.append(fo)
                op.append(lev)
                ot.append("%s x%d leak" % (self.lo.upper(), n))
        st.skirts[id(self)] = skirts
        return Tones(of, op, ot).pruned()

    def noise(self, nin, st):
        mx = self.mx
        flo, syn = st.lo[self.lo], st.synth[self.lo]
        pin, pout, _ = self._ports()
        skirts = st.skirts.get(id(self), [])

        def nout(fo):
            fo = np.asarray(fo, float)
            tot = np.zeros(fo.shape)
            gsum = np.zeros(fo.shape)
            for n in (1, 3, 5):
                for sign in (1, -1):
                    fi = np.abs(n * flo + sign * fo)
                    g = mw(-mx.cl_db - mx.spur[1][n] + pin(fi) + pout(fo))
                    tot += g * nin(fi)
                    gsum += g
            tot += np.maximum(1 - gsum, 0.0) * KT0
            for fs, ps, n in skirts:
                ln = syn.phase_noise(flo, fo - fs) + 20 * np.log10(n)
                tot += ps * mw(ln)
            if self.mode == "down":
                # the LO's amplitude noise, detected: half its far-out
                # floor (the close-in noise is phase noise, which a
                # switching mixer does not detect), both sidebands, less
                # the mixer's balance
                am = syn.phase_noise(flo, 1e9) - 3.0
                tot += 2 * mw(syn.out_dbm + am - mx.cl_db
                              - mx.am_reject_db + pout(fo))
            return tot
        return nout

    def wanted(self, f, st):
        pin, pout, _ = self._ports()
        fo = np.abs(st.lo[self.lo] - np.asarray(f, float))
        return fo, -self.mx.cl_db + pin(f) + pout(fo)


class Chain:
    """One band's signal path: `main`, and a `leak` path that leaves `main`
    after stage `fork` and rejoins it before stage `join` (the unselected
    arm of the band switches)."""

    def __init__(self, name, main, leak=(), fork=0, join=0):
        self.name, self.main, self.leak = name, list(main), list(leak)
        self.fork, self.join = fork, join

    def run(self, tones, st, psd_in=None):
        """(tones at the output, noise density there as a function of
        frequency, mW/Hz).  psd_in: a density at the input, on top of the
        thermal noise there (a noise-like signal), as a function of
        frequency."""
        st.skirts.clear()
        st.levels.clear()
        t = tones.copy()
        side = None
        for i, s in enumerate(self.main):
            if self.leak and i == self.fork:
                side = t.copy()
                for ls in self.leak:
                    side = ls.tones(side, st)
            if self.leak and i == self.join and side is not None:
                side.tag = np.array(["leak: " + x for x in side.tag], object)
                t = Tones.join(t, side).pruned()
            t = s.tones(t, st)
        if psd_in is None:
            noise = lambda f: np.full(np.shape(f), KT0)   # noqa: E731
        else:
            noise = lambda f: KT0 + psd_in(f)             # noqa: E731
        for s in self.main:
            noise = s.noise(noise, st)
        return t, noise

    def wanted_gain(self, f, st):
        """(frequency at the output, gain in dB) of the wanted response to
        an input at f."""
        f = np.asarray(f, float)
        g = np.zeros(f.shape)
        for s in self.main:
            f, gs = s.wanted(f, st)
            g = g + gs
        return f, g
