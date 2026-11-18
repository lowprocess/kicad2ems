"""
mom25d.blocks - the via and sides blocks (B, C) between the frequencies a
sweep has solved

An entry of B or C is a / (jw) + b + jw c: the layered kernels' integrals of
the charge and of the current, which change with f as the kernels do (the
phase over the board, a wideband dielectric's permittivity). So s B and s C
are smooth in f, s = jw eps (eps: a metal interface's permittivity at f, the
charge's 1 / eps taken out as its 1 / (jw) is), where the blocks themselves
cost a solve's largest share on a board with vias and thick copper (the SMA
launch: 2.4 s of 6.4 s a frequency). The blocks a sweep fills are kept
(anchors: the first in double, the others their complex64 difference from
it), and a frequency between them takes the polynomial in f through the P
nearest, where that and the one through P - 1 agree to TOL on a sample of
rows; else its blocks are filled and kept too. What is not smooth is left
out of them and added at each frequency: the elements in vias and the
barrels' loss (sim._via_loss).

The strips' own block is filled at every frequency, from the Green tables,
and a loop of current (a side's vertical with its folds and the sheet's
edges about its node) sees no charge only where its columns of B, C and of
the strips' block are one kernel's: blocks 1e-8 off the tables' (5 anchors
from 1 to 8 GHz) moved rf_interdig's S by 2e-4 at a resonance, the plates'
loops being 1e-4 of their charge terms. So such a frequency's tables are
the anchors' too (tables: by the same weights, the current's with w^2 and
the charge's without, as s B has them), and its whole matrix is the
polynomial through the anchors'.

The differences are single precision's: of the charge's part what is left
after eps is the layers' permittivities drifting apart (a wideband board's:
0.5 % a decade) and the phase. So a frequency where the current's part is
less than LOOP_TREE_BELOW of the charge's is filled (fast.FastOperator).
"""
import numpy as np

from kicad2ems.emsdev.memory import available as available_memory
from . import _core

# anchors a frequency's polynomial takes, and a sweep fills before any is
# interpolated: the adaptive sweep's five across the band and the first two
# it picks, at the response's sharpest features. (rf_interdig, 1-8 GHz:
# through its first 5, blocks within 1e-8 of the filled ones, S 3.2e-4 off
# on a resonance's flank (3 ppm in frequency); through 7, 8e-6. rf_bends,
# 1-12 GHz, 29 solves: through 5 at most, 23 of them filled; through 9, no
# fewer than through 7; in sqrt(f), which takes a wideband dielectric's log f
# better, 14 filled against 16 but S 2.9e-4 off against 2.9e-5.)
P = 7
# anchors kept at most
KEEP = 16
# the blocks' relative change (Frobenius norm over the sampled rows, each
# unknown scaled as the solver's equilibration does) between the polynomials
# through P and P - 1 anchors, below which the first is taken: the
# refinement's own tolerance (fast.FastOperator.mixed_tol). 3e-7 left
# rf_bends 6 frequencies of 29 to take (the fills' own noise, 1e-8 to 1e-7
# from one frequency to the next, is in the estimate), 3e-6 saved one fill
# of the 16 for twice the error
TOL = 1e-6
ROWS = 128      # rows sampled of each block


def _weights(xa, x):
    """Lagrange's weights of the nodes xa at x."""
    d = xa[:, None] - xa[None, :]
    np.fill_diagonal(d, 1.0)
    num = np.where(np.eye(len(xa), dtype=bool), 1.0, x - xa[None, :])
    return (num / d).prod(1)


class Anchors:
    """The blocks kept over a sweep. at(f): the weights of f's polynomial, or
    None; mix: the blocks from them; add: blocks filled, kept."""

    def __init__(self):
        # the anchors' frequencies, s and tables (the core's: fast.setup);
        # their blocks less the first's (base), complex64; the same of the
        # sampled rows
        self.f, self.s, self.tb, self.D, self.Ds = [], [], [], [], []
        self.base = self.bs = self.sc = self.rows = None

    def at(self, f):
        """[(anchor, weight)] of the polynomial through the anchors about f
        (its neighbours first, then outwards), or None: outside them, fewer
        than P, or the estimate above TOL. (Anchor 0 is the base: its
        weight what the others leave of 1.)"""
        s, x = np.array(self.f), float(f)
        if len(s) < P or not s.min() <= x <= s.max():
            return None
        o = np.argsort(s)
        hi = int(np.searchsorted(s[o], x))
        lo, k = hi - 1, []
        while len(k) < P:
            a = x - s[o[lo]] if lo >= 0 else np.inf
            b = s[o[hi]] - x if hi < len(s) else np.inf
            if a <= b:
                k.append(o[lo]); lo -= 1
            else:
                k.append(o[hi]); hi += 1
        w1 = _weights(s[k], x)
        dw = w1 - np.r_[_weights(s[k[:-1]], x), 0.0]
        for j, sc in enumerate(self.sc):
            T = [np.empty_like(self.bs[j]) for _ in (0, 1)]
            for O, F0, ws in ((T[0], self.bs[j], w1), (T[1], None, dw)):
                _core.blend(O, F0, [self.Ds[i][j] for i in k if i],
                            [w for i, w in zip(k, ws) if i], 1.0)
            if np.linalg.norm(T[1] * sc) > TOL * np.linalg.norm(T[0] * sc):
                return None
        return list(zip(k, w1))

    def mix(self, kw, s, out=None):
        """(B, C) from at(f)'s weights and f's s, into out's two arrays
        (blocks of Z) where given."""
        out = out or [np.empty_like(F) for F in self.base]
        for j in (0, 1):
            _core.blend(out[j], self.base[j],
                        [self.D[i][j] for i, _ in kw if i],
                        [w for i, w in kw if i], 1.0 / s)
        return out

    def tables(self, kw, f, s):
        """The Green tables at f as the blocks have them: {A, cA, cAi, Q,
        cq, cqi} of the anchors' by at(f)'s weights, the current's (s B's
        part in w^2) x s_k f_k / (s f), the charge's x s_k f / (s f_k)."""
        out = {}
        for keys, p in (('A', 'cA', 'cAi'), 1), (('Q', 'cq', 'cqi'), -1):
            c = [w * self.s[i] / s * (self.f[i] / f) ** p for i, w in kw]
            for key in keys:
                out[key] = sum(ci * self.tb[i][key]
                               for ci, (i, _) in zip(c, kw))
        return out

    def add(self, f, B, C, s, tb, room, spare):
        """Keep the blocks filled at f, s x them (what is smooth in f),
        and f's tables, where the anchors stay within `room` bytes and
        that leaves `spare` bytes of memory available."""
        from .fast import _row_blocks
        n, size = len(self.f), B.size + C.size
        held, need = (8.0 + 8.0 * n) * size if n else 0.0, (
            8.0 if n else 16.0) * size
        if (n >= KEEP or held + need > room
                or available_memory() - need < spare):
            return
        if self.base is None:
            self.base = (s * B, s * C)
            d = 1.0 / np.sqrt(np.abs(np.diagonal(self.base[1])) + 1e-300)
            self.rows = [np.unique(np.linspace(0, len(M) - 1, ROWS).astype(
                int)) for M in (B, C)]
            self.sc = (d[None, :], d[self.rows[1], None] * d[None, :])
            self.bs = [F[r] for F, r in zip(self.base, self.rows)]
            D = None
        else:
            D = (np.empty(B.shape, np.complex64),
                 np.empty(C.shape, np.complex64))
            for M, F0, Dj in zip((B, C), self.base, D):
                def fn(a, b):
                    Dj[a:b] = s * M[a:b] - F0[a:b]
                _row_blocks(Dj, fn)
        self.f.append(float(f))
        self.s.append(s)
        self.tb.append(tb)
        self.D.append(D)
        self.Ds.append(D and [Dj[r] for Dj, r in zip(D, self.rows)])
