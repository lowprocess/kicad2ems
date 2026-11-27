"""
Exact re-analysis of a pixel pattern under changes of a few pixels.

At each frequency the grid's matrix Z is kept as its principal pivot transform
on the pattern's unknowns A (the rest: I),

    P = [[ Z_AA^-1,        -Z_AA^-1 Z_AI             ],
         [ Z_IA Z_AA^-1,    Z_II - Z_IA Z_AA^-1 Z_AI ]]

whose ports' block is the pattern's port admittance. Giving the pattern the
unknowns B, or taking them away, is a pivot of P on B (N^2 |B| operations, no
new factorisation), and the admittance after it is

    Y' = Y - P_pB P_BB^-1 P_Bp

which needs P's entries at the ports and B alone: every single-pixel change of
a pattern is evaluated, exactly, for a few small solves each. A search then
pays N^2 per change it makes, and next to nothing per change it considers.
(Pivoted: the changes' low-rank factors kept beside P and applied together.)
"""
import numpy as np
from scipy.linalg import blas, inv


def transform(Z, act):
    """The principal pivot transform of Z on the unknowns `act` (bool), in
    Fortran order."""
    a, i = np.flatnonzero(act), np.flatnonzero(~act)
    P = np.empty(Z.shape, complex, order='F')
    W = inv(Z[np.ix_(a, a)])
    T = W @ Z[np.ix_(a, i)]
    P[np.ix_(a, a)] = W
    P[np.ix_(a, i)] = -T
    P[np.ix_(i, a)] = Z[np.ix_(i, a)] @ W
    P[np.ix_(i, i)] = Z[np.ix_(i, i)] - Z[np.ix_(i, a)] @ T
    return P


def pivot(P, B):
    """P pivoted on the unknowns B (indices), in place."""
    B = np.asarray(B)
    X = inv(P[np.ix_(B, B)])
    U = np.asfortranarray(P[:, B])
    V = np.asfortranarray(X @ P[B, :])
    out = blas.zgemm(-1.0, U, V, beta=1.0, c=P, overwrite_c=1)
    assert out is P or np.shares_memory(out, P)
    P[:, B] = U @ X
    P[B, :] = -V
    P[np.ix_(B, B)] = X


class Pivoted:
    """A pivot transform kept as M - U V: a pivot on B is the rank-|B|
    change

        M  ->  M - (M[:, B] + e_B) M_BB^-1 (M[B, :] - e_B^T)

    whose two factors join U and V, and M itself is written only every
    `hold` columns of them, all at once (a pass over M for each pivot was
    most of a search's time: its few columns at a time use the memory's
    speed, not the processor's)."""

    def __init__(self, M, hold=48):
        self.M, self.hold = M, hold
        n = M.shape[0]
        self.U = np.empty((n, hold + 64), complex, order='F')
        self.V = np.empty((hold + 64, n), complex, order='F')
        self.r = 0

    def take(self, rows, cols):
        """The entries [rows, cols] (index arrays, broadcast together)."""
        out = self.M[rows, cols]
        if self.r:
            out = out - np.einsum('...r,...r->...', self.U[rows, :self.r],
                                  self.V.T[cols, :self.r])
        return out

    def pivot(self, B):
        B = np.asarray(B)
        b, r = len(B), self.r
        if r + b > self.U.shape[1]:
            self.flush()
            r = 0
        cols = self.M[:, B]
        rows = self.M[B, :]
        if r:
            cols = cols - self.U[:, :r] @ self.V[:r, B]
            rows = rows - self.U[B, :r] @ self.V[:r, :]
        X = inv(cols[B, :])
        cols[B, np.arange(b)] += 1.0
        rows[np.arange(b), B] -= 1.0
        self.U[:, r:r + b] = cols
        self.V[r:r + b, :] = X @ rows
        self.r = r + b
        if self.r >= self.hold:
            self.flush()

    def flush(self):
        if self.r:
            out = blas.zgemm(-1.0, self.U[:, :self.r], self.V[:self.r, :],
                             beta=1.0, c=self.M, overwrite_c=1)
            assert out is self.M or np.shares_memory(out, self.M)
            self.r = 0

    def array(self):
        self.flush()
        return self.M


class Moves:
    """A list of moves on a grid, each a set of pixels toggled together; for
    a pattern, the unknowns of one of the grid's systems each would add or
    remove (padded to one width). groups: [[pixel, ...], ...] (pixel = i * ny
    + j)."""

    def __init__(self, grid, groups, system=None):
        system = system or grid.systems()[0]
        self.groups = [np.asarray(g, int) for g in groups]
        npx = grid.nx * grid.ny
        touch = [[] for _ in range(npx)]
        for e in range(system.n):
            for q in {system.pa[e], system.pb[e]} - {-1}:
                touch[q].append(e)
        slots = [np.unique(np.concatenate([touch[q] for q in g])).astype(int)
                 for g in self.groups]
        self.width = max(max(len(s) for s in slots), 1)
        n = len(slots)
        self.edge = np.zeros((n, self.width), int)
        self.used = np.zeros((n, self.width), bool)
        for m, s in enumerate(slots):
            self.edge[m, :len(s)], self.used[m, :len(s)] = s, True
        # each slot's two pixels (npx: a feed's, always on) and whether the
        # move toggles them
        pa = np.where(system.pa < 0, npx, system.pa)[self.edge]
        pb = np.where(system.pb < 0, npx, system.pb)[self.edge]
        member = np.zeros((n, npx + 1), bool)
        for m, g in enumerate(self.groups):
            member[m, g] = True
        rows = np.arange(n)[:, None]
        self.pa, self.pb = pa, pb
        self.ta, self.tb = member[rows, pa], member[rows, pb]

    def changed(self, mask):
        """(n, width) bool: the slots whose unknown each move would add or
        remove on this pattern."""
        on = np.append(np.asarray(mask, bool).ravel(), True)
        a, b = on[self.pa], on[self.pb]
        return self.used & ((a & b) != ((a ^ self.ta) & (b ^ self.tb)))


class State:
    """A pattern on a grid at the frequencies `freqs`: its pivoted matrices
    (one for each of the grid's systems: grid.systems(symmetry), a symmetric
    pattern's two halves), its port admittance, and the admittance after any
    of `groups`' moves (pixels toggled together; they keep the symmetry).
    16 n^2 bytes a system and frequency."""

    def __init__(self, grid, freqs, mask, groups, symmetry=None,
                 refresh=600):
        from .grid import combine
        self.grid, self.freqs = grid, [float(f) for f in freqs]
        self.systems = grid.systems(symmetry)
        self.combine = combine
        self.groups = [np.asarray(g, int) for g in groups]
        self.moves = [Moves(grid, groups, s) for s in self.systems]
        self.refresh, self.drift = refresh, 0.0
        self.made = 0                   # moves made, in all
        self.set(mask)

    def _matrices(self, f, act):
        Z = self.grid.matrix(f)
        return [Pivoted(transform(s.reduce(Z), a))
                for s, a in zip(self.systems, act)]

    def set(self, mask):
        self.mask = np.array(mask, bool)
        act = [s.active(self.mask) for s in self.systems]
        self.P = [self._matrices(f, act) for f in self.freqs]
        self.count = 0

    def add_frequency(self, f):
        self.freqs.append(float(f))
        self.P.append(self._matrices(
            float(f), [s.active(self.mask) for s in self.systems]))

    def y(self):
        """(nf, 2, 2): the pattern's port admittance."""
        return self.combine([
            np.array([s.sign[:, None]
                      * P[k].take(s.ports[:, None], s.ports[None, :])
                      * s.sign[None, :] for P in self.P])
            for k, s in enumerate(self.systems)])

    def trial(self):
        """(nf, n, 2, 2): the port admittance after each move, each
        alone."""
        ys = []
        for k, (s, mv) in enumerate(zip(self.systems, self.moves)):
            ch, edge = mv.changed(self.mask), mv.edge
            p, sg = s.ports, s.sign
            n, w = edge.shape
            eye = np.eye(w)[None] * (~ch)[:, :, None]
            out = np.empty((len(self.P), n, len(p), len(p)), complex)
            for i, Ps in enumerate(self.P):
                P = Ps[k]
                # the unused slots: an identity row, no coupling to the ports
                BB = np.where(ch[:, :, None] & ch[:, None, :],
                              P.take(edge[:, :, None], edge[:, None, :]),
                              eye)
                pB = np.where(ch[:, None, :],
                              P.take(p[None, :, None], edge[:, None, :]), 0)
                Bp = np.where(ch[:, :, None],
                              P.take(edge[:, :, None], p[None, None, :]), 0)
                Y = (P.take(p[:, None], p[None, :])[None]
                     - pB @ np.linalg.solve(BB, Bp))
                out[i] = sg[None, :, None] * Y * sg[None, None, :]
            ys.append(out)
        return self.combine(ys)

    def apply(self, m):
        """Makes move m: the mask toggled, every matrix pivoted. From time to
        time the matrices are made afresh (a pivot's rounding adds up)."""
        Bs = [mv.edge[m][mv.changed(self.mask)[m]] for mv in self.moves]
        self.mask.ravel()[self.groups[m]] ^= True
        self.count += 1
        self.made += 1
        if self.count >= self.refresh:
            y0 = self.y()
            self.set(self.mask)
            self.drift = max(self.drift, float(np.abs(self.y() - y0).max()
                                               / np.abs(y0).max()))
            return
        for Ps in self.P:
            for P, B in zip(Ps, Bs):
                if len(B):
                    P.pivot(B)
