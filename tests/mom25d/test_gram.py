"""The Gram matrix (a surface impedance's term, --sigma) is sparse from the
start and the dense formula's to rounding."""
import numpy as np
from scipy import sparse

from kicad2ems.emsdev.numerics.base import RWG, combine, quad_pts, _BAR3, _W3
from kicad2ems.mom25d.base import gram_matrix


def _grid(n):
    x, y = np.meshgrid(np.linspace(0, 1e-3, n), np.linspace(0, 2e-3, n))
    nodes, tris = np.c_[x.ravel(), y.ravel()], []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            tris += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    return RWG(nodes, np.array(tris))


def test_the_gram_matrix_is_sparse_and_the_dense_formula_s():
    """combine's sum of S^T M S with its diagonal triangle moments: the same
    numbers, without the dense Nt x Nt diagonals and Ne x Ne products (836 MB
    at 4,485 unknowns)."""
    r = _grid(14)
    Ar, P = r.area, quad_pts(r, _BAR3)
    m = lambda v: np.diag(Ar * (_W3[None, :] * v).sum(1))
    D = combine(r, m((P**2).sum(-1)), m(P[..., 0]), m(P[..., 1]), np.diag(Ar))
    G = gram_matrix(r)
    assert sparse.issparse(G) and G.nnz < 6 * r.Ne
    assert np.abs(G.toarray() - D).max() <= 1e-12 * np.abs(D).max()
    assert np.abs(G - G.T).max() <= 1e-12 * np.abs(D).max()
