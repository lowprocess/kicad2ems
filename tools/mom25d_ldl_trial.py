"""Does a complex-symmetric factor precondition mom25d's matrices? On a board
(a <name>_board.json, e.g. a segment piece's) at fixed frequencies, the
equilibrated MoM matrix the direct solver factors (captured where it would
factor it) is factored in complex64 three ways and each used as GMRES's
preconditioner on the double matrix, as the mixed-precision solve does:

  lu     scipy's LU with partial pivoting (getrf): what mom25d does today
  bk     LAPACK's symmetric Bunch-Kaufman LDL^T (csytrf): half the numbers,
         pivoted (2x2 blocks), any symmetric matrix
  ldl    mom25d.ldl: LDL^T without pivoting: what maps onto the GPU's hybrid
         factor (the CPU's panels, the GPU's trailing update on the lower
         half alone)

and the double-precision solve as the reference. Per frequency: each factor's
time and bytes, GMRES's iterations per port to 1e-6 (mixed_tol), and its
solution's largest error relative to the reference. usage:
  mom25d_ldl_trial.py BOARD.json GHZ[,GHZ...] [--thickness grow] [--edges coarse]
                      [--zone] [--dmax 0.5]
(the matrix is held in double: 16 N^2 bytes, and the reference solve's copy:
N up to ~15k on 31 GB)."""
import argparse
import time

import numpy as np
import scipy.linalg as sla
from scipy.linalg import lapack

from kicad2ems.mom25d import ldl
from kicad2ems.mom25d.api import build_board
from kicad2ems.mom25d.dense import gmres_batched
from kicad2ems.mom25d import fast as F


def capture(sim, f):
    """The equilibrated double Z and right-hand sides the direct solve
    factors at f (its _ladder's), the solve then let finish."""
    got = {}
    orig = F.FastOperator._ladder

    def grab(self, f_, plan, box, A, B, dsc, t0):
        if 'Z' not in got:
            Z = box[0]
            got.update(Z=(dsc[:, None] * Z * dsc[None, :]).astype(complex),
                       B=np.array(B, complex), basis=plan.basis)
        return orig(self, f_, plan, box, A, B, dsc, t0)
    F.FastOperator._ladder = grab
    try:
        sim.sparams(f)
    finally:
        F.FastOperator._ladder = orig
    if 'Z' not in got:
        raise RuntimeError("the solve took no dense path (too large for the "
                           "CPU's dense LU here: a smaller board)")
    return got['Z'], got['B'], got['basis']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('board')
    ap.add_argument('ghz')
    ap.add_argument('--thickness', default='grow')
    ap.add_argument('--edges', default='fine')
    ap.add_argument('--zone', action='store_true')
    ap.add_argument('--dmax', type=float, default=0.5)
    ap.add_argument('--lt-below', type=float, default=None,
                    help="the loop/tree basis's threshold in (k h)^2 "
                    "(policy.LOOP_TREE_BELOW, 1e-5 by default)")
    ap.add_argument('--basis', default='auto', choices=('auto', 'rwg'),
                    help="auto: the loop/tree basis where mom25d would take it "
                    "(low frequency, room for it), as a run does; rwg: never")
    a = ap.parse_args()
    if a.lt_below is not None:
        from kicad2ems.mom25d import policy
        policy.LOOP_TREE_BELOW = F.LOOP_TREE_BELOW = a.lt_below
    kw = dict(dmax=a.dmax * 1e-3, port='via', thickness=a.thickness,
              edges=a.edges, log=lambda *x, **k: None)
    if a.zone:
        kw['zone'] = 'auto'
    _, sim = build_board(a.board, 0.1e9, 10e9, **kw)
    # (mixed precision and the RWG basis: what a large piece's solve factors)
    sim.gpu, sim.solver, sim.precision = 'off', 'direct', 'mixed'
    sim.loop_tree = 'off' if a.basis == 'rwg' else 'auto'
    sim.reuse = 'off'  # (each frequency's own factor captured)
    N = sim.Ne + sim.Nv
    print(f"N {N}, {len(sim.ports)} ports", flush=True)
    for f in (float(x) * 1e9 for x in a.ghz.split(',')):
        Z, B, basis = capture(sim, f)
        asym = np.abs(Z - Z.T).max() / np.abs(Z).max()
        t = time.time()
        X0 = np.linalg.solve(Z, B)
        print(f"--- {f / 1e9:g} GHz ({basis} basis): |Z - Z^T| {asym:.1e} of max|Z|; double "
              f"solve {time.time() - t:.1f} s", flush=True)
        Z64 = Z.astype(np.complex64)
        facs = {}
        t = time.time()
        lu = sla.lu_factor(Z64, check_finite=False)
        facs['lu'] = (time.time() - t, lu[0].nbytes,
                      lambda R, lu=lu: sla.lu_solve(lu, R.astype(np.complex64),
                                                    check_finite=False))
        t = time.time()
        lf, ipiv, info = lapack.csytrf(Z64, lower=1)
        tb = time.time() - t
        if info == 0:
            facs['bk'] = (tb, lf.nbytes // 2, lambda R, lf=lf, ipiv=ipiv:
                          lapack.csytrs(lf, ipiv, R.astype(np.complex64),
                                        lower=1)[0])
        t = time.time()
        try:
            L = ldl.factor(Z64.copy())
            facs['ldl'] = (time.time() - t, L.nbytes // 2,
                           lambda R, L=L: ldl.solve(L, R.astype(np.complex64)))
        except ZeroDivisionError as e:
            print(f"  ldl: {e}")
        b1 = B[:, :1]
        for name, (tf, nbytes, M) in facs.items():
            # the factor's own solve of its complex64 matrix: a misused routine
            # shows here, a weak preconditioner in GMRES's iterations
            r = (np.linalg.norm(Z64 @ M(b1).astype(np.complex64) - b1)
                 / np.linalg.norm(b1))
            t = time.time()
            X, iters = gmres_batched(lambda V: Z @ V, B,
                                     M=lambda R: M(R).astype(complex),
                                     rtol=1e-6, restart=60, maxiter=5)
            err = (np.abs(X - X0).max(axis=0) / np.abs(X0).max(axis=0)).max()
            print(f"  {name:4s} factor {tf:6.1f} s, {nbytes / 1e9:5.2f} GB; "
                  f"GMRES {time.time() - t:5.1f} s, iterations per port "
                  f"median {int(np.median(iters))} max {max(iters)}; "
                  f"error vs double {err:.1e}; its own solve's residual "
                  f"{r:.1e}", flush=True)


if __name__ == '__main__':
    main()
