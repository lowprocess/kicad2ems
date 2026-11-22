"""
A complex-symmetric LDL^T factor without pivoting (blocked, NumPy): the
reference for a GPU version and the trial of whether one preconditions
mom25d's matrices (tools/mom25d_ldl_trial.py).

MoM's Z is complex symmetric (Z = Z^T, not Hermitian), so Z = L D L^T with L
unit lower triangular and D diagonal holds half of an LU's numbers and takes
half its work. Without pivoting it relies on the equilibrated matrix's
diagonal (the fill's self terms dominate each row); the mixed-precision
refinement (GMRES on the exact operator) corrects what a complex64 factor
loses, as it does for the LU.

    F = factor(A)          # A: (n, n) complex, symmetric; overwritten
    x = solve(F, b)        # (n,) or (n, k)

factor raises ZeroDivisionError on a zero pivot (no pivoting to go round it).
"""
import numpy as np
from scipy.linalg import solve_triangular

__all__ = ['factor', 'solve']


def factor(A, nb=256):
    """L D L^T of the complex-symmetric A in place (its lower triangle: L
    below the diagonal, D on it), right-looking by blocks of nb. Returns A."""
    n = len(A)
    for k0 in range(0, n, nb):
        k1 = min(k0 + nb, n)
        # the diagonal block, unblocked
        for j in range(k0, k1):
            d = A[j, j]
            if d == 0:
                raise ZeroDivisionError(f"LDL^T: zero pivot at {j}")
            ld = A[j + 1:k1, j].copy()               # l d
            A[j + 1:k1, j] = ld / d                  # l
            A[j + 1:k1, j + 1:k1] -= np.outer(A[j + 1:k1, j], ld)
        if k1 == n:
            break
        L11 = A[k0:k1, k0:k1]
        D1 = np.diagonal(L11).copy()
        # W = A21 L11^-T, L21 = W D1^-1 (A21's rows: k1..n)
        W = solve_triangular(L11, A[k1:, k0:k1].T, lower=True,
                             unit_diagonal=True, check_finite=False).T
        A[k1:, k0:k1] = W / D1[None, :]
        # the trailing update, its lower triangle being what is kept (here
        # the whole block: the reference, not the fast version)
        A[k1:, k1:] -= W @ A[k1:, k0:k1].T
    return A


def solve(F, b):
    """Z^-1 b with factor()'s L D L^T: L y = b, z = y / D, L^T x = z."""
    y = solve_triangular(F, b, lower=True, unit_diagonal=True,
                         check_finite=False)
    d = np.diagonal(F)
    y = y / (d if np.ndim(b) == 1 else d[:, None])
    return solve_triangular(F, y, lower=True, trans='T', unit_diagonal=True,
                            check_finite=False)
