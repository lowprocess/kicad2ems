"""mom25d.half: a symmetric matrix by its lower half (block columns), its
products, and the LDL^T factor without pivoting (the GPU's layout and its host
reference)."""
import numpy as np
import pytest

from kicad2ems.mom25d.half import Half, ldl_factor, ldl_solve, ldl_small


def sym(n, rng, dtype=complex, diag=3.0):
    A = rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n))
    return (A + A.T + diag * np.sqrt(n) * np.eye(n)).astype(dtype)


@pytest.mark.parametrize('n,nb', [(1, 4), (5, 2), (300, 64), (513, 256),
                                  (256, 256)])
def test_pack_holds_the_lower_half_by_block_columns(n, nb):
    """Block row J is Z[J0:J1, J0:] (the block column J0.. transposed), one
    after another; to_full gives the matrix back, matvec its products; the
    compiled pack (complex64) and NumPy's agree."""
    rng = np.random.default_rng(n)
    A = sym(n, rng)
    H = Half.pack(A, dtype=complex, nb=nb)
    assert H.size == sum(min(nb, n - j) * (n - j) for j in range(0, n, nb))
    assert np.array_equal(H.to_full(), A)
    for J in range(H.nblk):
        a = int(H.j0[J])
        assert np.array_equal(H.block(J), A[a:a + int(H.w[J]), a:])
    X = rng.standard_normal((n, 3)) + 0j
    assert np.allclose(H.matvec(X), A @ X, rtol=1e-12, atol=1e-12)
    assert np.allclose(H.matvec(X[:, 0]), A @ X[:, 0], rtol=1e-12,
                       atol=1e-12)
    d = rng.random(n) + 0.5
    c = Half.pack(A, d, nb=nb)            # the core's
    ref = Half.pack(A * d[:, None] * d[None, :], dtype=complex, nb=nb)
    assert c.dtype == np.complex64
    assert np.abs(c.data - ref.data).max() < 1e-6 * np.abs(ref.data).max()
    rp, c0 = H.rows()
    r = n // 2
    assert H.data[rp[r]] == A[r, c0[r]] and c0[r] == (r // nb) * nb


@pytest.mark.parametrize('dtype,tol', [(complex, 1e-12), (np.complex64, 1e-5)])
@pytest.mark.parametrize('n,nb', [(7, 3), (700, 256), (450, 64)])
def test_ldl_factor_solves(dtype, tol, n, nb):
    """The blocked LDL^T in place on the half, in double and single
    precision: Z^-1 B for one and several right-hand sides."""
    rng = np.random.default_rng(1)
    A = sym(n, rng)
    H = ldl_factor(Half.pack(A, dtype=dtype, nb=nb))
    B = rng.standard_normal((n, 4)) + 1j * rng.standard_normal((n, 4))
    X = ldl_solve(H, B)
    assert X.dtype == complex
    assert np.abs(A @ X - B).max() < tol * np.abs(B).max()
    x = ldl_solve(H, B[:, 1])
    assert np.abs(A @ x - B[:, 1]).max() < tol * np.abs(B).max()


def test_ldl_small_and_its_zero_pivot():
    rng = np.random.default_rng(2)
    A = sym(100, rng)
    L, d = ldl_small(A)
    assert np.allclose(L @ np.diag(d) @ L.T, A, atol=1e-10)
    assert np.array_equal(np.triu(L, 1), np.zeros_like(L))
    A[0, 0] = 0
    with pytest.raises(ZeroDivisionError):
        ldl_small(A)


def test_the_core_diagonal_block_is_ldl_small():
    """_core.ldl_diag (the GPU factor's host part): the packed L \\ D, L^-T,
    1 / D and L^-1, complex64, of ldl_small's double factor; a zero pivot
    raises ValueError."""
    from kicad2ems.mom25d import _core
    rng = np.random.default_rng(3)
    w = 70
    A = sym(w, rng).astype(np.complex64)
    out = [np.empty(w * w, np.complex64) for _ in range(2)]
    dinv, linv = np.empty(w, np.complex64), np.empty(w * w, np.complex64)
    _core.ldl_diag(A.T, out[0], out[1], dinv, linv)   # (any strides)
    L, d = ldl_small(A.astype(complex))
    Li = np.linalg.inv(L)
    pk = out[0].reshape(w, w).T                       # column major
    assert np.allclose(np.tril(pk, -1), np.tril(L, -1), atol=1e-5)
    assert np.allclose(np.diagonal(pk), d, rtol=1e-5)
    assert np.allclose(out[1].reshape(w, w).T, Li.T, atol=1e-5)
    assert np.allclose(linv.reshape(w, w).T, Li, atol=1e-5)
    assert np.allclose(dinv, 1 / d, rtol=1e-5)
    A[0, 0] = 0
    with pytest.raises(ValueError, match='zero pivot'):
        _core.ldl_diag(A, out[0], out[1], dinv, linv)


@pytest.mark.parametrize('n,nb', [(300, 64), (513, 256), (5, 2)])
def test_compact_is_pack_in_place(n, nb):
    """Half.compact: a complex64 Z into the half in its own memory, as
    pack's."""
    rng = np.random.default_rng(n)
    A = sym(n, rng, np.complex64)
    ref = Half.pack(A, nb=nb)
    B = A.copy()
    H = Half.compact(B, nb=nb)
    assert np.shares_memory(H.data, B) and np.array_equal(H.data, ref.data)


def test_ldl_mul_is_the_factored_matrixs_product():
    from kicad2ems.mom25d.half import ldl_mul
    rng = np.random.default_rng(7)
    A = sym(300, rng)
    H = ldl_factor(Half.pack(A, dtype=complex, nb=64))
    X = rng.standard_normal((300, 3)) + 0j
    assert np.abs(ldl_mul(H, X) - A @ X).max() < 1e-12 * np.abs(A @ X).max()
    assert np.abs(ldl_mul(H, X[:, 0]) - A @ X[:, 0]).max() < 1e-10


def test_compact_of_a_mapped_matrix_gives_its_rest_back():
    """zeros_mapped + Half.compact (complex128): pack's half, in the
    mapping's own memory, the pages past it released (zeros if touched)."""
    from kicad2ems.mom25d.half import zeros_mapped
    rng = np.random.default_rng(9)
    n, nb = 700, 64
    A = sym(n, rng)
    Z = zeros_mapped(n)
    assert not Z.any()
    Z[...] = A
    H = Half.compact(Z, nb=nb)
    assert H.dtype == complex and np.shares_memory(H.data, Z)
    assert np.array_equal(H.data, Half.pack(A, dtype=complex, nb=nb).data)
    tail = Z.reshape(-1)[-1000:]
    assert not tail.any()          # (given back: zero pages)


@pytest.mark.parametrize('n,nb', [(300, 64), (513, 256)])
def test_rows_and_a_border_transform_on_the_half(n, nb):
    """rows_of / set_rows: any rows whole, read and written where the half
    keeps them (a diagonal block's mirror too); loop_transform: T^T Z T for T
    = I + W, W in some columns V on rows outside them (the sides' loops, the
    fan paths), as on the whole matrix."""
    from scipy import sparse
    rng = np.random.default_rng(n)
    A = sym(n, rng)
    H = Half.pack(A, dtype=complex, nb=nb)
    idx = np.array([3, nb - 1, nb, n - 2, 5])
    assert np.array_equal(H.rows_of(idx), A[idx])
    R = rng.standard_normal((len(idx), n)) + 0j
    R[:, idx] = (R[:, idx] + R[:, idx].T) / 2
    B = A.copy()
    B[idx], B[:, idx] = R, R.T
    assert np.array_equal(H.set_rows(idx, R).to_full(), B)
    V = np.r_[n - 40:n - 10, 7]
    rows = np.setdiff1d(np.arange(n), V)
    W = sparse.random(n, n, density=0.02, random_state=1, format='lil')
    W[:, np.setdiff1d(np.arange(n), V)] = 0
    W[V] = 0
    W = W.tocsc()
    assert np.flatnonzero(np.diff(W.indptr)).size and rows.size
    T = np.eye(n) + W.toarray()
    H = Half.pack(A, dtype=complex, nb=nb).loop_transform(W, chunk=17)
    ref = T.T @ A @ T
    assert np.abs(H.to_full() - ref).max() < 1e-12 * np.abs(ref).max()
