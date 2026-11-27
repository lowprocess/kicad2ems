r"""
pcbqs.pdn --choose-decaps: capacitors chosen for a rail's decap sites so the
impedance each load sees stays under its target over the band, with the fewest
parts (or the least cost).

    python -m kicad2ems.pcbqs.pdn board.kicad_pcb --rail +1V15 --volts 1.15 \
        --load U21=1 --choose-decaps --out pdn
    python -m kicad2ems.pcbqs.pdn.decoupling --library [--json] [--volts 3.3]
        # the parts it chooses from, and their capacitance at that bias

The sites are the rail's decap footprints (two-pad C* parts from its tree to
ground: rail.Rail.sites), fitted and unfitted (DNP), or those --decap-sites
names. A site's footprint fixes its package, and the PDN model its mount (its
pads' copper to the planes and the via pair: stack.RailPDN). Kept as fitted,
never changed: the parts --decap-keep names, a part with a vendor's file
(--cap-file), one whose package the library has no MLCC of (an electrolytic, a
polymer can).

The bare PDN (the planes, their ties, the series parts) between the loads and
the sites comes from the impedance sweep's own factorisation a frequency
(RailPDN.sweep(between=), or one of its own), the regulator and the kept parts
attached by Woodbury's identity as RailPDN.sweep does: Z (nf, k, k) over the
loads and the sites, at their plane nodes. A part of impedance z at site c (its
capacitance at the rail's DC bias, its ESR and ESL, the site's mount) changes
it by a rank-one term,

    Z' = Z - Z[:, c] Z[c, :] / (z + Z[c, c])
    (taking it off again: Z + Z[:, c] Z[c, :] / (z - Z[c, c]))

so every set of parts is closed in this form, with no new solve; a load sees
its diagonal entry plus its own pads' connection to the planes. A trial reads
only the loads' rows and the diagonal (Z is symmetric), so trying a part costs
O(nf n_loads) and taking one off for a trial O(nf n_loads k). The search is
deterministic:
  1. greedy: from no part and from the fitted ones (the better of the two
     kept; --decap-start empty or built: one), the part and site that lower the
     excess most (per cost, --decap-mode cost), until every load is under its
     target or no part lowers the excess by EARN of what is left;
  2. then, until nothing changes: a part dropped where the rest still meet the
     target; two parts replaced by one; a part moved to another site or value
     where that raises the margin (so that a later drop may be possible), or
     (cost) costs less.
The excess is the sum, over the loads and the band's frequencies, of
log(|Z| / target) where |Z| is over it, by log-frequency step. The target is
out of reach where a load's own connection to the planes (its pads and vias,
which no capacitor changes) is over it, or the floor is: an ideal part at
every site (its mount and its package's ESL, no capacitance and no ESR: what
any capacitor there tends to above its resonance; one at its resonance can dip
under it, but not over a band). Those frequencies are left out of the excess
and named in the result.

The library (library()): MLCC values 1 nF - 100 uF (E3) in each package's range
(PACKAGES); ESR from a typical curve, ESR_1U (C / 1 uF)^-0.4 (0.03 ohm at 100
nF, 5 mOhm at 10 uF); ESL by package (stack.ESL_PKG); X7R below 1 uF, X5R from
it; a relative cost by package and by how near the package's largest value it
is (cost()). --decap-library FILE replaces it: a CSV with a header of name,
package, C (and any of ESR, ESL, dielectric, volts, cost; values as '100n',
'4.7u' or numbers).

DC bias (share()): an MLCC keeps 1 / (1 + (V / Vh)^2) of its value at V volts,
Vh = V0 (Cmax / C)^(1/4) (Cmax: its package's largest value, the thinnest
layers: V0 = 2.4 V, so a part at its package's largest value keeps 35 % at 3.3
V, one a decade below 63 %, two decades 84 %), or a quarter of its rated
voltage where that is higher; C0G / NP0 keep all of it. These are typical
figures, not a vendor's: --derate-table FILE (a CSV of package, C, volts,
share; C empty for every value of the package) replaces them, interpolated in
volts. pcbqs.pdn --derate-bias takes the same curve for the fitted decaps.
"""
import csv
import math
import re
import sys
import time
from dataclasses import dataclass

import numpy as np
from scipy.sparse.linalg import splu

from kicad2ems.core import units
from .stack import ESL_PKG, package as package_of

E3 = (1.0, 2.2, 4.7)
# the MLCC values each package comes in (smallest, largest), F
PACKAGES = {
    '0201': (1e-9, 1e-6),
    '0402': (1e-9, 10e-6),
    '0603': (1e-9, 22e-6),
    '0805': (1e-9, 47e-6),
    '1206': (10e-9, 100e-6),
    '1210': (100e-9, 100e-6),
}
ESR_1U = 0.012          # ohm, a 1 uF MLCC's typical ESR
V0 = 2.4                # V: Vh of a part at its package's largest value
# relative cost by package (cost())
BASE_COST = {'0201': 1.1, '0402': 0.8, '0603': 0.9, '0805': 1.2, '1206': 2.0,
             '1210': 3.0}
EARN = 0.01             # a part must take this share of the excess left
CHECKS = 16             # frequencies the closed form is checked at
TOL = 1e-9              # an excess this small is none (round-off)
NO_LOSS = ('C0G', 'NP0', 'COG')
DIELECTRIC = re.compile(r'(?<![A-Z0-9])(X[5-8][RSTP]|Y5V|Z5U|C0G|NP0|COG)'
                        r'(?![A-Z0-9])', re.I)
RATED = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)\s*V(\d*)(?![a-z])', re.I)


@dataclass(frozen=True)
class Cap:
    """A capacitor the search can place: its nominal value, ESR, ESL."""
    name: str               # '100n 0402'
    package: str
    C: float                # F, nominal
    ESR: float              # ohm
    ESL: float              # H, the part's own (its mount is the site's)
    dielectric: str = 'X7R'
    volts: float = 0.0      # rated, V (0: not known)
    cost: float = 1.0
    fitted: bool = False    # the part fitted at a site, as it is


def fmt_c(C):
    """'100n', '2.2u', '47p': a capacitance as a BOM writes it."""
    for unit, scale in (('u', 1e-6), ('n', 1e-9), ('p', 1e-12)):
        if C >= scale * 0.999:
            v = C / scale
            return f"{v:.3g}{unit}"
    return f"{C:.3g}"


def esr(C):
    """A typical MLCC's ESR at its self-resonance, ohm (an assumption)."""
    return ESR_1U * (C / 1e-6)**-0.4


def cost(pkg, C):
    """A part's relative cost: its package's, more the nearer its value is to
    the package's largest (a 0402 100 nF ~1.1, a 0402 10 uF 4)."""
    cmax = PACKAGES.get(pkg, (C, C))[1]
    return BASE_COST.get(pkg, 1.0) * (1 + 4 * math.sqrt(min(C / cmax, 1.0)))


def _values(lo, hi):
    out = []
    for e in range(-12, -2):
        for m in E3:
            c = float(f'{m}e{e}')
            if lo * 0.999 <= c <= hi * 1.001:
                out.append(c)
    return out


def library(packages=None, values=None):
    """[Cap]: the built-in MLCCs, in `packages` (default all of PACKAGES), of
    `values` (F; default every E3 value in each package's range)."""
    out = []
    for pkg, (lo, hi) in PACKAGES.items():
        if packages and pkg not in packages:
            continue
        for C in _values(lo, hi):
            if values and not any(abs(C - v) <= 1e-3 * v for v in values):
                continue
            out.append(
                Cap(f"{fmt_c(C)} {pkg}", pkg, C, esr(C), ESL_PKG[pkg],
                    'X5R' if C >= 1e-6 else 'X7R', 0.0, cost(pkg, C)))
    return out


def _num(text, what, row):
    x = units.part_value(str(text)) if str(text).strip() else None
    if x is None:
        try:
            x = float(text)
        except (TypeError, ValueError):
            raise ValueError(f"{what} {text!r} on line {row}: not a value")
    return x


def parse_values(text):
    """[F] of a comma-separated list of values ('100n,1u,10u')."""
    out = []
    for x in str(text or '').split(','):
        if x.strip():
            v = units.part_value(x.strip())
            if v is None or not 0 < v <= 1:
                raise ValueError(f"--decap-values: {x.strip()!r} is no "
                                 "capacitance")
            out.append(v)
    return out


def read_library(path):
    """[Cap] of a CSV with a header: name, package, C and any of ESR, ESL,
    dielectric, volts, cost (missing: the built-in rules for its package)."""
    out = []
    with open(path, newline='', encoding='utf-8') as fh:
        rows = csv.DictReader(fh)
        for k, row in enumerate(rows, 2):
            row = {(a or '').strip().lower(): (b or '').strip()
                   for a, b in row.items()}
            pkg = row.get('package', '')
            if not pkg or not row.get('c'):
                raise ValueError(f"{path}: line {k}: a package and C needed")
            C = _num(row['c'], 'C', k)
            if not 0 < C <= 1:
                raise ValueError(f"{path}: line {k}: C {C} is no capacitance")
            out.append(
                Cap(row.get('name') or f"{fmt_c(C)} {pkg}", pkg, C,
                    _num(row['esr'], 'ESR', k) if row.get('esr') else esr(C),
                    (_num(row['esl'], 'ESL', k) if row.get('esl') else
                     ESL_PKG.get(pkg, 0.5e-9)),
                    row.get('dielectric') or ('X5R' if C >= 1e-6 else 'X7R'),
                    float(row['volts']) if row.get('volts') else 0.0,
                    float(row['cost']) if row.get('cost') else cost(pkg, C)))
    if not out:
        raise ValueError(f"{path}: no parts")
    return out


def read_derating(path):
    """{(package, C or None): (volts array, share array)} of a CSV with a
    header package, C, volts, share (C empty: every value of the package)."""
    rows = {}
    with open(path, newline='', encoding='utf-8') as fh:
        for k, row in enumerate(csv.DictReader(fh), 2):
            row = {(a or '').strip().lower(): (b or '').strip()
                   for a, b in row.items()}
            try:
                pkg, v, s = row['package'], float(row['volts']), float(
                    row['share'])
            except (KeyError, ValueError):
                raise ValueError(
                    f"{path}: line {k}: package, volts and share needed")
            if not 0 < s <= 1 or v < 0:
                raise ValueError(f"{path}: line {k}: share {s} at {v} V")
            C = _num(row['c'], 'C', k) if row.get('c') else None
            rows.setdefault((pkg, C), []).append((v, s))
    if not rows:
        raise ValueError(f"{path}: no rows")
    return {
        key: (np.array([v for v, _ in sorted(r)]),
              np.array([s for _, s in sorted(r)]))
        for key, r in rows.items()
    }


def share(pkg, C, volts, dielectric='', rated=0.0, table=None):
    """The share of its value an MLCC keeps at a DC bias of `volts`: the table's
    (read_derating; its package and value, else its package) where it has one,
    else the module's curve."""
    if table:
        hit = next((table[k] for k in table
                    if k[0] == pkg and k[1] and abs(k[1] - C) <= 1e-3 * C),
                   table.get((pkg, None)))
        if hit is not None:
            return float(np.interp(volts, *hit))
    if str(dielectric).upper() in NO_LOSS or volts <= 0:
        return 1.0
    cmax = max(PACKAGES.get(pkg, (C, C))[1], C)
    vh = V0 * (cmax / C)**0.25
    if rated:
        vh = max(vh, rated / 4)
    return 1.0 / (1.0 + (volts / vh)**2)


def dielectric_of(text):
    """(dielectric, rated volts) a part's value or fields say ('10u X5R 6V3':
    ('X5R', 6.3)), '' and 0 where they do not."""
    text = str(text or '')
    m = DIELECTRIC.search(text)
    r = RATED.search(text)
    volts = (float(f"{r.group(1)}.{r.group(2)}") if r and r.group(2) else
             float(r.group(1)) if r else 0.0)
    return (m.group(1).upper() if m else ''), volts


def part_share(p, C, volts, table=None):
    """share() of a fitted part p (a rail.Part) of value C: its package from its
    footprint, its dielectric and rating from its value and fields."""
    text = ' '.join([str(p.value)] + [str(v) for v in p.props.values()])
    d, rated = dielectric_of(text)
    return share(package_of(p.lib), C, volts, d, rated, table)


# ---- the PDN between the loads and the sites ---------------------------------
class Closure:
    """The bare PDN of a RailPDN between its loads and the sites, with the fixed
    parts (the regulator, the kept parts) attached: Z (nf, k, k) over
    [loads..., sites...] at their plane nodes; `own` (nf, n_loads), each load's
    own pads' connection, which reading Z at a load adds."""

    def __init__(self, pdn, f, loads, sites, fixed, bare=None, log=print):
        """bare: RailPDN.sweep's B between (at least) the loads, the sites
        and the fixed parts at these frequencies: no factorisation here; else
        one a frequency."""
        t0 = time.time()
        self.pdn, self.f = pdn, np.asarray(f, float)
        self.loads, self.sites = list(loads), list(sites)
        names = self.loads + self.sites
        dofs = [pdn.dof(n) for n in names]
        fixed = [n for n in fixed if pdn.dof(n) is not None
                 and pdn.parts[n]['el'] is not None]
        nf, k = len(self.f), len(names)
        if bare is not None:
            # by name: a node two parts share is a row each (Woodbury holds)
            pos = {n: j for j, n in enumerate(bare['names'])}
            missing = [n for n in names + fixed if n not in pos]
            if missing or len(bare['Z']) != nf:
                raise ValueError(f"the sweep's bare network lacks "
                                 f"{', '.join(missing) or 'these frequencies'}")
            every = [pos[n] for n in names + fixed]
            idx, F = list(range(k)), list(range(k, k + len(fixed)))
        else:
            cols = list(dict.fromkeys(dofs + [pdn.dof(n) for n in fixed]))
            at = {d: j for j, d in enumerate(cols)}
            idx = [at[d] for d in dofs]
            F = [at[pdn.dof(n)] for n in fixed]
            E = np.zeros((pdn.n, len(cols)), complex)
            E[cols, np.arange(len(cols))] = 1.0
        self.Z = np.empty((nf, k, k), complex)
        for i, fi in enumerate(self.f):
            w = 2 * np.pi * fi
            if bare is not None:
                X = bare['Z'][i][np.ix_(every, every)]
            else:
                X = splu(pdn.matrix(fi, bare=True)).solve(E)[cols]
            if F:
                zF = np.array([pdn._part_z(pdn.parts[n], w) for n in fixed])
                X = X - X[:, F] @ np.linalg.solve(
                    np.diag(zF) + X[np.ix_(F, F)], X[F, :])
            self.Z[i] = X[np.ix_(idx, idx)]
        w = 2 * np.pi * self.f
        self.own = np.zeros((nf, len(self.loads)), complex)
        for j, n in enumerate(self.loads):
            info = pdn.parts[n]
            if info['rail'].vias:
                self.own[:, j] = 1j * w * info['mount'] + info.get(
                    'mount_R', 0.0)
        self.seconds = time.time() - t0
        log(f"decoupling: the PDN between {len(self.loads)} load(s) and "
            f"{len(self.sites)} site(s), {nf} frequencies, in "
            f"{self.seconds:.1f} s"
            + (" (the sweep's factorisations)" if bare is not None else ''))

    def site_z(self, site, cap, share_):
        """(nf,) a part's impedance at a site: its element (C at its bias, ESR,
        ESL) and the site's mount, as RailPDN._part_z has a decap's."""
        info = self.pdn.parts[site]
        el = dict(C=cap.C * share_, ESR=cap.ESR, ESL=cap.ESL)
        return self.pdn._part_z(dict(info, el=el), 2 * np.pi * self.f)

    def ideal_z(self, site, esl):
        """(nf,) an ideal part at a site: inductance `esl` (its package's) and
        the site's mount, no capacitance and no ESR."""
        info = self.pdn.parts[site]
        return self.pdn._part_z(dict(info, el=dict(R=0.0, L=esl)),
                                2 * np.pi * self.f)

    def attached(self, chosen):
        """Z with the parts {site index: (nf,) impedance} attached (Woodbury:
        one small solve a frequency)."""
        if not chosen:
            return self.Z.copy()
        S = [len(self.loads) + c for c in chosen]
        zS = np.stack([chosen[c] for c in chosen], axis=1)      # (nf, s)
        Z = self.Z
        A = Z[:, S][:, :, S] + zS[:, :, None] * np.eye(len(S))[None]
        return Z - Z[:, :, S] @ np.linalg.solve(A, Z[:, S, :])


@dataclass
class Choice:
    """A search state: the parts at the sites, the PDN with them attached."""
    at: dict                # {site index: option index}
    Z: np.ndarray           # (nf_band, k, k)


class Search:
    """The deterministic search (the module docstring) over a Closure's band."""

    def __init__(self, cl, options, targets, band, mode='fewest', ideal=None,
                 log=print):
        """options: [[(Cap, share, zp (nf,))] per site]; targets: [ohms or None]
        per load; band: (fmin, fmax) Hz; ideal: [(nf,)] per site, the floor's
        part there (default: cl.ideal_z with the options' lowest ESL)."""
        if ideal is None:
            ideal = [cl.ideal_z(n, min((c.ESL for c, _, _ in o), default=0.0))
                     for n, o in zip(cl.sites, options)]
        self.cl, self.mode, self.log = cl, mode, log
        f = cl.f
        self.inb = (f >= band[0] * (1 - 1e-9)) & (f <= band[1] * (1 + 1e-9))
        self.fb = f[self.inb]
        if not len(self.fb):
            raise ValueError(f"no frequency of the sweep in the band "
                             f"{band[0]:g}-{band[1]:g} Hz")
        self.nl = len(cl.loads)
        self.t = np.array([t if t else np.inf for t in targets])
        own = np.abs(cl.own[self.inb])                      # (nfb, nl)
        self.Z0 = cl.Z[self.inb]
        # the floor: every site holding an ideal part (no capacitance, no ESR:
        # the lowest inductance of its options and its mount alone, what any
        # capacitor there tends to above its resonance), with the load's own
        # connection
        nl, S = self.nl, list(range(self.nl, self.Z0.shape[1]))
        Z = self.Z0
        zi = np.stack([ideal[s][self.inb] for s in range(len(S))], axis=1)
        A = Z[:, S][:, :, S] + zi[:, :, None] * np.eye(len(S))[None]
        fl = (Z[:, :nl, :nl] - Z[:, :nl, S] @ np.linalg.solve(A, Z[:, S, :nl]))
        self.floor = np.abs(fl[:, np.arange(nl), np.arange(nl)]
                            + cl.own[self.inb])
        # the frequencies a load's target can be met at: its own connection
        # under it, and the floor
        self.own_over = own >= self.t[None, :]
        self.reach = ~self.own_over & (self.floor < self.t[None, :])
        # log-frequency weights (the trapezoid's)
        lf = np.log(self.fb) if len(self.fb) > 1 else np.zeros(len(self.fb))
        wgt = np.zeros(len(self.fb))
        if len(lf) > 1:
            d = np.diff(lf)
            wgt[:-1] += d / 2
            wgt[1:] += d / 2
        else:
            wgt[:] = 1.0
        self.w = wgt[:, None] * self.reach * np.isfinite(self.t)[None, :]
        self.own = cl.own[self.inb]
        self.options = options
        # the parts' impedances on the band: (n_sites, n_opt_max, nfb), inf
        # where a site has fewer options
        m = max((len(o) for o in options), default=0)
        self.zp = np.full((len(options), max(m, 1), len(self.fb)), np.inf,
                          complex)
        self.cost = np.full((len(options), max(m, 1)), np.inf)
        for s, opts in enumerate(options):
            for j, (cap, _, z) in enumerate(opts):
                self.zp[s, j] = z[self.inb]
                self.cost[s, j] = 1.0 if mode == 'fewest' else cap.cost
        self.evaluations = 0

    # ---- what a state gives -------------------------------------------------
    def ratio(self, zl):
        """|Z| / target at the loads, (nfb, nl) (or batched: (..., nfb, nl))."""
        return np.abs(zl + self.own) / self.t

    def excess(self, r):
        """Sum over the band and the loads of log(r) over 1, log-f weighted:
        (...,) of r (..., nfb, nl)."""
        return np.sum(np.log(np.maximum(r, 1.0)) * self.w, axis=(-2, -1))

    def worst(self, r):
        """The largest ratio where the target can be met: (...,)."""
        m = np.where(self.w > 0, r, 0.0)
        return m.max(axis=(-2, -1)) if m.size else np.zeros(r.shape[:-2])

    def loads_z(self, Z):
        n = self.nl
        return Z[:, np.arange(n), np.arange(n)]

    def state(self, at):
        chosen = {c: self.zp[c, j] for c, j in at.items()}
        if not chosen:
            return Choice(dict(at), self.Z0.copy())
        S = [self.nl + c for c in chosen]
        zS = np.stack([chosen[c] for c in chosen], axis=1)
        Z = self.Z0
        A = Z[:, S][:, :, S] + zS[:, :, None] * np.eye(len(S))[None]
        return Choice(dict(at), Z - Z[:, :, S] @ np.linalg.solve(A, Z[:, S, :]))

    def measure(self, ch):
        r = self.ratio(self.loads_z(ch.Z))
        return self.excess(r), self.worst(r)

    def total_cost(self, at):
        return float(sum(self.cost[c, j] for c, j in at.items()))

    # ---- moves ----------------------------------------------------------------
    @staticmethod
    def removed(Z, c, zp):
        """Z with the part of impedance zp (nfb,) at column c taken off."""
        zc = Z[:, :, c]
        return Z + zc[:, :, None] * Z[:, c, None, :] / (zp - Z[:, c, c])[:,
                                                                       None,
                                                                       None]

    @staticmethod
    def added(Z, c, zp):
        zc = Z[:, :, c]
        return Z - zc[:, :, None] * Z[:, c, None, :] / (zp + Z[:, c, c])[:,
                                                                       None,
                                                                       None]

    def light(self, Z):
        """(ZL, dg) of a full Z: the loads' rows (nfb, nl, k) and the diagonal
        (nfb, k), all a trial reads (Z is symmetric: a site's column at the
        loads is their rows')."""
        return Z[:, :self.nl, :], np.diagonal(Z, axis1=1, axis2=2)

    @staticmethod
    def off(ZL, dg, rows, c, zp):
        """(ZL, dg, rows) with the part zp (nfb,) at column c taken off; rows:
        {column: its row of Z (nfb, k)}, c's and those of the parts to be
        taken off next (each updated, c's dropped). O(nfb nl k), where
        removed() is O(nfb k^2)."""
        zc = rows[c]
        d = (zp - zc[:, c])[:, None]
        q = zc / d
        ZL = ZL + ZL[:, :, c, None] * q[:, None, :]
        dg = dg + zc * q
        rows = {k: r + r[:, c, None] * q for k, r in rows.items() if k != c}
        return ZL, dg, rows

    def trials(self, ZL, dg, sites, excess=True):
        """For each site in `sites` and each of its options: (excess, worst) of
        the state (ZL, dg: light()) with that part added, arrays (len(sites),
        n_opt); excess False: (None, worst), all a feasibility test needs
        (excess 0 is worst <= 1). On |Z|^2 in the batch's own layout."""
        nl, no = self.nl, self.zp.shape[1]
        if not len(sites):
            return np.zeros((0, no)), np.zeros((0, no))
        zl = ZL[:, np.arange(nl), np.arange(nl)] + self.own     # (nfb, nl)
        cols = nl + np.asarray(sites)
        ex = np.empty((len(sites), no)) if excess else None
        wo = np.empty((len(sites), no))
        it2 = (1.0 / self.t**2)[None, :, None, None]
        m4 = (self.w > 0)[:, :, None, None]
        w4 = 0.5 * self.w[:, :, None, None]
        # chunks of sites: the batch (nfb, nl, sites, options) under ~32 MB
        per = max(1, int(2e6 // max(1, len(self.fb) * nl * no)))
        for a in range(0, len(sites), per):
            cs = cols[a:a + per]
            zlc = ZL[:, :, cs]                                  # (nfb, nl, s)
            zcc = dg[:, cs]                                     # (nfb, s)
            zp = np.moveaxis(self.zp[sites[a:a + per]], 2, 0)   # (nfb, s, o)
            with np.errstate(invalid='ignore', divide='ignore'):
                new = (zl[:, :, None, None] - (zlc * zlc)[:, :, :, None]
                       / (zp + zcc[:, :, None])[:, None])       # (nfb,nl,s,o)
            r2 = new.real**2 + new.imag**2
            r2 *= it2
            wo[a:a + per] = np.sqrt(np.max(np.where(m4, r2, 0.0),
                                           axis=(0, 1)))
            if excess:
                ex[a:a + per] = np.sum(np.log(np.maximum(r2, 1.0)) * w4,
                                       axis=(0, 1))
            self.evaluations += zp.shape[1] * zp.shape[2]
        bad = ~np.isfinite(self.zp[sites][:, :, 0])
        wo[bad] = np.inf
        if excess:
            ex[bad] = np.inf
        return ex, wo

    # ---- the search -------------------------------------------------------------
    def greedy(self, start):
        ch = self.state(start)
        e, wst = self.measure(ch)
        steps = []
        while e > TOL:
            empty = [s for s in range(len(self.options)) if s not in ch.at]
            if not empty:
                break
            ex, _ = self.trials(*self.light(ch.Z), empty)
            gain = e - ex
            with np.errstate(invalid='ignore', divide='ignore'):
                score = (gain / self.cost[empty] if self.mode == 'cost' else
                         gain)
            score = np.where(np.isfinite(score), score, -np.inf)
            k = int(np.argmax(score))
            s, j = divmod(k, score.shape[1])
            if not gain[s, j] > EARN * e:
                break
            c = empty[s]
            ch = Choice({**ch.at, c: j}, self.added(ch.Z, self.nl + c,
                                                   self.zp[c, j]))
            e, wst = self.measure(ch)
            steps.append(('add', c, j, e, wst))
        return self.state(ch.at), steps

    def _drop(self, ch, e, wst, feasible):
        """The part whose removal keeps the target met (feasible) or costs under
        EARN of the excess (not feasible), the one leaving the best margin
        (cost: the dearest first); None. Every part's removal at once: the
        loads' diagonal alone, zl + Z_lc^2 / (zp - Z_cc)."""
        parts = sorted(ch.at.items())
        if not parts:
            return None
        nl = self.nl
        cols = [nl + c for c, _ in parts]
        zp = np.stack([self.zp[c, j] for c, j in parts], axis=1)  # (nfb, n)
        zlc = ch.Z[:, :nl, cols]                                  # (nfb,nl,n)
        d = zp - ch.Z[:, cols, cols]
        zl = self.loads_z(ch.Z)[:, :, None] + zlc * zlc / d[:, None, :]
        r = self.ratio(np.moveaxis(zl, 2, 0))                     # (n,nfb,nl)
        e2, w2 = self.excess(r), self.worst(r)
        best = None
        for k, (c, j) in enumerate(parts):
            ok = (e2[k] <= TOL if feasible else
                  e2[k] <= e * (1 + EARN) + 1e-15)
            if not ok:
                continue
            key = ((-self.cost[c, j], w2[k]) if self.mode == 'cost' else
                   (w2[k], ))
            if best is None or key < best[0]:
                best = (key, c)
        return best and best[1]

    def improve(self, ch, rounds=None):
        steps = []
        for _ in range(rounds or 3 * len(self.options) + 10):
            e, wst = self.measure(ch)
            feasible = e <= TOL
            c = self._drop(ch, e, wst, feasible)
            if c is not None:
                at = {k: v for k, v in ch.at.items() if k != c}
                ch = self.state(at)
                steps.append(('drop', c, None, *self.measure(ch)))
                continue
            if not feasible:
                break
            moved = self._merge(ch) or self._polish(ch, wst)
            if moved is None:
                break
            kind, at = moved
            ch = self.state(at)
            steps.append((kind, None, None, *self.measure(ch)))
        return ch, steps

    def _merge(self, ch):
        """Two parts for one, the target still met: ('merge', at) or None."""
        parts = sorted(ch.at.items())
        best = None
        ZL0, dg0 = self.light(ch.Z)
        nl = self.nl
        for a in range(len(parts)):
            for b in range(a + 1, len(parts)):
                (p, jp), (q, jq) = parts[a], parts[b]
                P, Q = nl + p, nl + q
                ZL, dg, rows = self.off(ZL0, dg0, {P: ch.Z[:, P, :],
                                                   Q: ch.Z[:, Q, :]}, P,
                                        self.zp[p, jp])
                ZL, dg, _ = self.off(ZL, dg, rows, Q, self.zp[q, jq])
                free = [s for s in range(len(self.options))
                        if s not in ch.at or s in (p, q)]
                _, wo = self.trials(ZL, dg, free, excess=False)
                ok = wo <= 1.0 + TOL
                if not ok.any():
                    continue
                if self.mode == 'cost':
                    cst = np.where(ok, self.cost[free], np.inf)
                    s, j = np.unravel_index(np.argmin(cst), cst.shape)
                    gain = self.cost[p, jp] + self.cost[q, jq] - cst[s, j]
                    if not gain > 1e-9:
                        continue
                    key = (-gain, wo[s, j])
                else:
                    w = np.where(ok, wo, np.inf)
                    s, j = np.unravel_index(np.argmin(w), w.shape)
                    key = (w[s, j], )
                if best is None or key < best[0]:
                    at = {k: v for k, v in ch.at.items() if k not in (p, q)}
                    at[free[s]] = int(j)
                    best = (key, at)
        return best and ('merge', best[1])

    def _polish(self, ch, wst):
        """One part moved to another site or value: where it lowers the worst
        ratio by over 1e-3 (cost: where it costs less, the target met), the best
        such; ('move', at) or None."""
        best = None
        ZL0, dg0 = self.light(ch.Z)
        for p, jp in sorted(ch.at.items()):
            P = self.nl + p
            ZL, dg, _ = self.off(ZL0, dg0, {P: ch.Z[:, P, :]}, P,
                                 self.zp[p, jp])
            free = [s for s in range(len(self.options))
                    if s not in ch.at or s == p]
            _, wo = self.trials(ZL, dg, free, excess=False)
            ok = wo <= 1.0 + TOL
            if self.mode == 'cost':
                cst = np.where(ok, self.cost[free], np.inf)
                s, j = np.unravel_index(np.argmin(cst), cst.shape)
                if not cst[s, j] < self.cost[p, jp] - 1e-9:
                    continue
                key = (cst[s, j] - self.cost[p, jp], wo[s, j])
            else:
                w = np.where(ok, wo, np.inf)
                s, j = np.unravel_index(np.argmin(w), w.shape)
                if not w[s, j] < wst - 1e-3:
                    continue
                key = (w[s, j], )
            if (free[s], j) == (p, jp):
                continue
            if best is None or key < best[0]:
                at = {k: v for k, v in ch.at.items() if k != p}
                at[free[s]] = int(j)
                best = (key, at)
        return best and ('move', best[1])


def bands_over(f, r):
    """[[f0, f1]] where r > 1."""
    over = np.asarray(r) > 1
    edges = np.flatnonzero(np.diff(np.r_[0, over.astype(int), 0]))
    return [[float(f[a]), float(f[min(b, len(f)) - 1])]
            for a, b in zip(edges[::2], edges[1::2])]


def choose(pdn, f, loads, targets, volts, sites=None, keep=(), lib=None,
           table=None, mode='fewest', start='both', fmax=None, bare=None,
           log=print):
    """Capacitors for the sites of a RailPDN (built with sites=rail.sites()):
    the result as pdn.json's 'decoupling' (the module docstring). loads: the
    observed loads; targets {load: ohms or None}; sites: references (default:
    every decap and site the model has); keep: references kept as fitted;
    bare: RailPDN.sweep(f, between=every part)'s B, so that nothing is
    factorised again but the check's CHECKS frequencies."""
    t0 = time.time()
    if mode not in ('fewest', 'cost'):
        raise ValueError(f"--decap-mode {mode!r}: fewest or cost")
    if start not in ('both', 'empty', 'built'):
        raise ValueError(f"--decap-start {start!r}: both, empty or built")
    lib = list(lib or library())
    notes = []
    loads = [r for r in loads if targets.get(r)]
    if not loads:
        raise ValueError("choosing decaps needs a target impedance (--target, "
                         "--load-target; not --no-target)")
    cand = [n for n, i in pdn.parts.items() if i['role'] in ('decap', 'site')]
    if sites:
        unknown = sorted(set(sites) - set(cand))
        if unknown:
            raise ValueError(
                f"--decap-sites {', '.join(unknown)}: not a decap footprint of "
                "the rail (a two-pad C* part from its tree to ground)")
        cand = [n for n in cand if n in set(sites)]
    keep = set(keep or ())
    unknown = sorted(keep - set(pdn.parts))
    if unknown:
        notes.append(f"--decap-keep {', '.join(unknown)}: not a part of the "
                     "rail's PDN (ignored)")
    fixed, free = [], []
    for n in cand:
        info = pdn.parts[n]
        el, pkg = info['el'], package_of(info['part'].lib)
        if pdn.dof(n) is None:
            notes.append(f"{n}: not connected to the planes: not a site")
            continue
        if n in keep:
            if el is not None:
                fixed.append(n)
            continue
        if el is not None and ('Zf' in el or pkg not in PACKAGES):
            fixed.append(n)
            notes.append(
                f"{n}: kept as fitted ("
                + ("its vendor file" if 'Zf' in el else
                   f"package {pkg or 'not known'}: no MLCC of it in the library")
                + ")")
            continue
        if pkg is None:
            notes.append(f"{n}: its footprint ({info['part'].lib}) names no "
                         "package: not a site")
            continue
        if pkg not in {c.package for c in lib}:
            notes.append(f"{n}: no part of its package {pkg} in the library: "
                         "not a site")
            if el is not None:
                fixed.append(n)
            continue
        free.append(n)
    # the parts not at a site: fixed as they are (the regulator, a decap not
    # among --decap-sites)
    fixed += [n for n, i in pdn.parts.items()
              if i['el'] is not None and n not in free and n not in fixed]
    if not free:
        raise ValueError("no decap site to choose for (every one kept, or none "
                         "on the rail)")
    log(f"decoupling: {len(free)} site(s), {len(fixed)} part(s) fixed "
        f"(the regulator, kept), {len(lib)} parts in the library")
    cl = Closure(pdn, f, loads, free, fixed, bare=bare, log=log)
    # each site's options: the library's of its package, and the fitted part
    options, fitted_at = [], {}
    for s, n in enumerate(free):
        info = pdn.parts[n]
        pkg = package_of(info['part'].lib)
        opts = []
        for cap in lib:
            if cap.package != pkg:
                continue
            sh = share(pkg, cap.C, volts, cap.dielectric, cap.volts, table)
            opts.append((cap, sh, cl.site_z(n, cap, sh)))
        el = info['el']
        if el is not None:
            C0 = el['C'] / el.get('derate', 1.0)
            cap = Cap(f"{fmt_c(C0)} {pkg} (fitted)", pkg, C0, el['ESR'],
                      el['ESL'], cost=cost(pkg, C0), fitted=True)
            z = pdn._part_z(info, 2 * np.pi * cl.f)
            fitted_at[s] = len(opts)
            opts.append((cap, el.get('derate', 1.0), z))
        options.append(opts)
    f = cl.f
    band = (float(f[0]), float(fmax or f[-1]))
    se = Search(cl, options, [targets[r] for r in loads], band, mode=mode,
                log=log)
    for j, r in enumerate(loads):
        out = ~se.reach[:, j]
        if out.any():
            why = ("its own pads' connection to the planes is over its "
                   "target" if se.own_over[:, j].any() else '')
            if (out & ~se.own_over[:, j]).any():
                why += ((", and elsewhere " if why else '')
                        + "the floor is (an ideal part at every site, its "
                        "mount and its package's inductance alone)")
            notes.append(
                f"{r}: at {int(out.sum())} of the band's {len(se.fb)} "
                f"frequencies, from {se.fb[np.argmax(out)] / 1e6:.4g} MHz, "
                f"{why}: no capacitor reaches that (left out of the search)")
    before = se.state(fitted_at)
    t1 = time.time()
    found = []
    for begin in ((('empty', 'built') if fitted_at else ('empty', ))
                  if start == 'both' else (start, )):
        ch, steps = se.greedy(dict(fitted_at) if begin == 'built' else {})
        ch, more = se.improve(ch)
        ex, wo = se.measure(ch)
        # the better: met, then the fewest parts (cost: the least), then the
        # margin; not met: the least excess
        key = ((0, se.total_cost(ch.at), wo) if ex <= TOL else (1, ex, wo))
        found.append((key, begin, ch, steps + more))
    _, begun, ch, steps = min(found, key=lambda x: x[0])
    t_search = time.time() - t1
    e, wst = se.measure(ch)
    e0, w0 = se.measure(before)
    # the whole band (not only the search's): the parts attached by Woodbury
    full = {c: options[c][j][2] for c, j in ch.at.items()}
    Zall = cl.attached(full)
    Zb = cl.attached({c: options[c][j][2] for c, j in fitted_at.items()})
    n = len(loads)
    za = Zall[:, np.arange(n), np.arange(n)] + cl.own
    zb = Zb[:, np.arange(n), np.arange(n)] + cl.own
    # the check: a full solve (RailPDN.z, a factorisation a frequency) with
    # the chosen parts attached, at CHECKS frequencies over the sweep and each
    # load's worst
    t2 = time.time()
    pick = sorted(set(np.linspace(0, len(f) - 1, min(CHECKS, len(f))).round()
                      .astype(int)) | {int(np.argmax(np.abs(za[:, j]) /
                                                     targets[r]))
                                       for j, r in enumerate(loads)})
    saved = {m: i['el'] for m, i in pdn.parts.items()}
    try:
        for s, m in enumerate(free):
            if s in ch.at:
                cap, sh, _ = options[s][ch.at[s]]
                pdn.parts[m]['el'] = (saved[m] if cap.fitted else dict(
                    C=cap.C * sh, ESR=cap.ESR, ESL=cap.ESL))
            else:
                pdn.parts[m]['el'] = None
        direct = pdn.z(f[pick], observe=loads)
    finally:
        for m, el in saved.items():
            pdn.parts[m]['el'] = el
    t_check = time.time() - t2
    dev = max(float(np.max(np.abs(direct[r] - za[pick, j])
                           / np.abs(direct[r])))
              for j, r in enumerate(loads))
    log(f"decoupling: the closed form against a full solve with the parts "
        f"attached: {dev:.2g} at most (relative, |Z|; {len(pick)} "
        f"frequencies, {t_check:.1f} s)")
    rows = []
    for s, m in enumerate(free):
        info = pdn.parts[m]
        cap = sh = None
        if s in ch.at:
            cap, sh, _ = options[s][ch.at[s]]
        was = options[s][fitted_at[s]][0] if s in fitted_at else None
        rows.append(dict(
            site=m, package=package_of(info['part'].lib),
            fitted=(was.name.replace(' (fitted)', '') if was else None),
            part=(cap.name.replace(' (fitted)', '') if cap else None),
            C=(cap.C if cap else None), C_bias=(cap.C * sh if cap else None),
            ESR=(cap.ESR if cap else None), ESL=(cap.ESL if cap else None),
            mount_nH=info['mount'] * 1e9,
            changed=((cap.name.replace(' (fitted)', '') if cap else None)
                     != (was.name.replace(' (fitted)', '') if was else None)),
            dnp=bool(info['part'].raw and info['part'].raw.get('dnp'))))
    bom = {}
    for row in rows:
        if row['part']:
            b = bom.setdefault(row['part'], dict(part=row['part'],
                                                 package=row['package'],
                                                 C=row['C'], refs=[]))
            b['refs'].append(row['site'])
    bom = sorted(bom.values(), key=lambda b: (b['C'], b['package']))
    for b in bom:
        b['count'] = len(b['refs'])
    lo = {}
    rb, ra = np.abs(zb), np.abs(za)
    for j, r in enumerate(loads):
        t = targets[r]
        m = se.inb.copy()
        m[se.inb] = se.reach[:, j]
        wb = float(rb[m, j].max() / t) if m.any() else 0.0
        wa = float(ra[m, j].max() / t) if m.any() else 0.0
        lo[r] = dict(
            target=t, worst_before=wb, worst_after=wa,
            margin_before=100 * (1 - wb), margin_after=100 * (1 - wa),
            over_before=bands_over(f[m], rb[m, j] / t),
            over_after=bands_over(f[m], ra[m, j] / t),
            out_of_reach_from=(float(se.fb[np.argmax(~se.reach[:, j])])
                               if (~se.reach[:, j]).any() else None),
            out_of_reach=bands_over(se.fb, (~se.reach[:, j]) * 2.0),
            floor=np.r_[np.full(int(np.argmax(se.inb)), np.nan), se.floor[:, j],
                        np.full(len(f) - int(np.argmax(se.inb)) - len(se.fb),
                                np.nan)].tolist(),
            mag_before=rb[:, j].tolist(), mag_after=ra[:, j].tolist())
    if e > TOL:
        notes.append(
            "the target is not met at every load over the band with these "
            "sites and parts (each further part would take under "
            f"{EARN:.0%} of what is left): the worst is "
            f"{max(v['worst_after'] for v in lo.values()):.3g} x its target")
    kind = {'add': 'added', 'drop': 'dropped', 'merge': 'two for one',
            'move': 'moved'}
    out = dict(
        mode=mode, start=start, began=begun, band=list(band), volts=volts,
        derating=('table' if table else 'curve'), met=bool(e <= TOL),
        parts=len(ch.at), parts_before=len(fitted_at),
        cost=se.total_cost(ch.at) if mode == 'cost' else None,
        cost_before=se.total_cost(fitted_at) if mode == 'cost' else None,
        sites=rows, bom=bom, kept=sorted(set(fixed) & set(cand)),
        fixed=sorted(fixed), loads=lo, f=f.tolist(),
        excess_before=float(e0), excess_after=float(e),
        check=dict(max_rel=dev, frequencies=len(pick),
                   seconds=round(t_check, 2)),
        steps=[dict(move=kind[k], site=(free[c] if c is not None else None),
                    part=(options[c][j][0].name if j is not None else None),
                    parts=None, excess=float(x), worst=float(wv))
               for k, c, j, x, wv in steps],
        evaluations=se.evaluations,
        seconds=dict(closure=round(cl.seconds, 2), search=round(t_search, 2),
                     total=round(time.time() - t0, 2)),
        notes=notes)
    worst_r = max(lo, key=lambda r: lo[r]['worst_after'])
    log(f"decoupling: {len(ch.at)} part(s) (fitted {len(fitted_at)}), "
        f"{'target met' if e <= TOL else 'target not met'}; worst {worst_r} "
        f"{lo[worst_r]['worst_after']:.3g} x its target (fitted "
        f"{lo[worst_r]['worst_before']:.3g}); search {t_search:.1f} s, "
        f"{se.evaluations:,} sets tried")
    return out


def figure(dec, out):
    """decoupling.png: |Z| at each load as fitted (dashed) and with the parts
    chosen (solid), the targets, the band's top."""
    import os
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    f = np.asarray(dec['f'])
    fig, ax = plt.subplots(figsize=(9, 4.6))
    levels = {}
    for k, (r, d) in enumerate(dec['loads'].items()):
        c = f'C{k % 10}'
        ax.loglog(f, d['mag_before'], '--', c=c, lw=1.0,
                  label=f"{r} as fitted")
        ax.loglog(f, d['mag_after'], c=c, lw=1.8, label=f"{r} chosen")
        levels.setdefault(d['target'], []).append(r)
    for t, who in levels.items():
        ax.axhline(t, c='r', lw=1,
                   label=f"target {t * 1e3:.3g} mOhm"
                   + (f" ({', '.join(who)})" if len(levels) > 1 else ''))
    if dec['band'][1] < f[-1]:
        ax.axvline(dec['band'][1], c='0.5', lw=0.8, ls=':')
    ax.set_xlabel('Hz')
    ax.set_ylabel('|Z| (Ohm)')
    ax.grid(True, which='both', alpha=0.3)
    ax.set_title(f"decoupling: {dec['parts']} part(s) chosen "
                 f"({dec['parts_before']} fitted); target "
                 f"{'met' if dec['met'] else 'not met'}"
                 + (" where in reach" if any(
                     l.get('out_of_reach') for l in dec['loads'].values())
                    else ''), fontsize=10)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'decoupling.png'), dpi=110)
    plt.close(fig)
    return dict(file='decoupling.png', side='pdn', layer='')


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(
        prog='python -m kicad2ems.pcbqs.pdn.decoupling',
        description="the parts pcbqs.pdn --choose-decaps chooses from")
    ap.add_argument('--library', action='store_true',
                    help='list the parts (the built-in library or --file)')
    ap.add_argument('--file', default='', help='a library CSV')
    ap.add_argument('--volts', type=float, default=0.0,
                    help="each part's capacitance at this DC bias too")
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    try:
        lib = read_library(a.file) if a.file else library()
    except (OSError, ValueError) as e:
        print(f"decoupling: error: {e}")
        return 2
    rows = [dict(name=c.name, package=c.package, C=c.C, ESR=c.ESR, ESL=c.ESL,
                 dielectric=c.dielectric, cost=round(c.cost, 3),
                 share=share(c.package, c.C, a.volts, c.dielectric, c.volts))
            for c in lib]
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    print(f"{'part':14s} {'ESR mOhm':>9s} {'ESL nH':>7s} {'diel':>5s} "
          f"{'cost':>5s} {'at %g V' % a.volts:>8s}")
    for r in rows:
        print(f"{r['name']:14s} {r['ESR'] * 1e3:9.3g} {r['ESL'] * 1e9:7.3g} "
              f"{r['dielectric']:>5s} {r['cost']:5.2f} {r['share']:8.0%}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
