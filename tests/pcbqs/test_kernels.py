"""The kernels with a compiled twin (pcbqs/kernels.py): the compiled core's
against numpy's, the LU of a row-major matrix in its own memory, and a mesh's
own interpolation where the thermal maps use it."""
import numpy as np
import pytest
import scipy.sparse as sp

from kicad2ems.pcbqs import kernels as K

core = pytest.mark.skipif(
    K.core('loop_system') is None,
    reason='no compiled core with the dense kernels')


def _matrices(n, seed=0):
    rng = np.random.default_rng(seed)
    R = rng.normal(size=(n, n))
    L = rng.normal(size=(n, n))
    S = (sp.random(n, n, 0.02, random_state=seed + 1) * (1 + 2j)).tocsr()
    return rng, R + R.T + 4 * n * np.eye(n), L + L.T, S


@core
@pytest.mark.parametrize('n', [1, 7, 400])
def test_loop_system_is_numpy_s(n):
    rng, R, L, S = _matrices(n)
    rows = np.delete(np.arange(n), [0] if n > 1 else [])
    cols = rng.permutation(n)[:max(1, n // 2)]
    jw = 2j * np.pi * 3e5
    for term in (None, S[rows][:, cols],
                 sp.csr_matrix((len(rows), len(cols)), dtype=complex)):
        a = K.loop_system(R, L, jw, rows, cols, term)
        b = K.loop_system(R, L, jw, rows, cols, term, numpy=True)
        assert (a.shape == b.shape == (len(rows), len(cols))
                and a.flags.c_contiguous)
        assert np.allclose(a, b, rtol=1e-14, atol=0)
    with pytest.raises(ValueError):
        K.loop_system(R, L, jw, [n], cols)  # a row that is not the matrix's
    with pytest.raises(ValueError):
        # another shape
        K.loop_system(R, L, jw, rows, cols,
                      sp.csr_matrix(np.ones((len(rows) + 1, len(cols)))))


@core
@pytest.mark.parametrize('lossy', [False, True])
def test_peec_system_is_numpy_s(lossy):
    n, nl = 300, 110
    rng, R, L, S = _matrices(n, 3)
    electro = rng.normal(size=(n - nl, n - nl)) * ((1 + 0.2j) if lossy else 1)
    for jw in (2j * np.pi * 1e3, 2j * np.pi * 1e9):
        for term in (S, None):
            a, za = K.peec_system(R, L, jw, 7.5, nl, electro, term)
            b, zb = K.peec_system(R, L, jw, 7.5, nl, electro, term, numpy=True)
            assert (za == pytest.approx(zb, rel=1e-15)
                    and np.allclose(a, b, rtol=1e-13, atol=1e-300))
    # no tree at all, and no loops at all
    a, _ = K.peec_system(R, L, 1j, 1.0, n, np.zeros((0, 0)))
    assert a.shape == (n, n)
    a, _ = K.peec_system(R, L, 1j, 1.0, 0, rng.normal(size=(n, n)))
    assert a.shape == (n, n)
    with pytest.raises(ValueError):
        K.peec_system(R, L, 1j, 1.0, nl, electro[:-1, :-1])


def test_complex_matrices_keep_numpy_s_path():
    """R or L complex (no solver makes them so): the compiled kernel would drop
    the imaginary part."""
    _, R, L, S = _matrices(20)
    a = K.loop_system(R * (1 + 1j), L, 2j, np.arange(20), np.arange(20), S)
    assert np.allclose(a, R * (1 + 1j) + 2j * L + S.toarray())


def test_lu_of_a_row_major_matrix_in_its_own_memory():
    rng = np.random.default_rng(5)
    a = rng.normal(size=(60, 60)) + 1j * rng.normal(size=(60, 60))
    b = rng.normal(size=(60, 3)) + 0j
    keep = a.copy()
    lu = K.lu_factor_rows(a)
    assert np.shares_memory(lu[0], a)  # no second matrix
    assert np.allclose(keep @ K.lu_solve_rows(lu, b), b)
    assert np.allclose(keep @ K.lu_solve_rows(lu, b[:, 0]), b[:, 0])


def test_symmetric_solve_by_cholesky_where_it_can():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(40, 40))
    P = a @ a.T + 40 * np.eye(40)  # definite: Cholesky
    B = rng.normal(size=(40, 3))
    assert np.allclose(P @ K.solve_symmetric(P, B), B)
    # complex with nothing imaginary: the same
    x = K.solve_symmetric(P.astype(complex), B)
    assert not np.iscomplexobj(x) and np.allclose(P @ x, B)
    Q = P.copy()
    Q[0, 0] = -3.0  # symmetric, not definite
    assert np.allclose(Q @ K.solve_symmetric(Q, B), B)
    Z = P * (1 + 0.3j)  # complex symmetric (a lossy stack)
    assert np.allclose(Z @ K.solve_symmetric(Z, B), B)


def test_the_single_lu_is_refined_to_double_and_falls_back_when_it_cannot():
    """lu_factor_rows's mixed factor (R-D13) solves to a double solve's
    accuracy, several right-hand sides at once, and a system too ill-conditioned
    for single precision is factored in double, with the same answer."""
    import numpy as np
    from kicad2ems.pcbqs.kernels import (lu_factor_rows, lu_solve_rows, MixedLU,
                                         factor_bytes)
    rng = np.random.default_rng(3)
    n = 300
    a = (rng.standard_normal((n, n)) + 1j * rng.standard_normal(
        (n, n)) + 20 * np.eye(n))
    b = rng.standard_normal((n, 3)) + 1j * rng.standard_normal((n, 3))
    want = np.linalg.solve(a, b)
    lu = lu_factor_rows(a.copy(), mixed=True)
    assert (isinstance(lu, MixedLU) and lu.double is None
            and factor_bytes(lu) == lu.nbytes)
    np.testing.assert_allclose(lu_solve_rows(lu, b), want, rtol=1e-11,
                               atol=1e-13)
    np.testing.assert_allclose(lu_solve_rows(lu, b[:, 0]), want[:, 0],
                               rtol=1e-11, atol=1e-13)
    u, _ = np.linalg.qr(
        rng.standard_normal((n, n)) + 1j * rng.standard_normal((n, n)))
    bad = (u * np.logspace(0, -11, n)) @ u.conj().T  # condition number 1e11
    want = np.linalg.solve(bad, b)
    lu = lu_factor_rows(bad.copy(), mixed=True)
    got = lu_solve_rows(lu, b)
    # in double now, in the matrix's memory
    assert lu.double is not None and lu.a is None
    # as good as a double solve
    assert np.linalg.norm(bad @ got - b) <= 10 * np.linalg.norm(bad @ want - b)
    # below MIXED_MIN: double, as before
    assert not isinstance(lu_factor_rows(a.copy()), MixedLU)


def test_one_eigendecomposition_solves_the_pencil_at_each_f_with_its_parts():
    """R-D13.2: (R + s L + P^T diag(z) P)^-1 b from one Pencil, against a dense
    solve, for s from DC up, with parts of no impedance (a short) and of an
    impedance, and several right-hand sides."""
    from scipy.sparse import csr_matrix
    from kicad2ems.pcbqs.kernels import Pencil
    rng = np.random.default_rng(3)
    n, k = 120, 4
    A = rng.standard_normal((n, n)); R = A @ A.T / n + 0.1 * np.eye(n)
    B = rng.standard_normal((n, n)); L = (B @ B.T / n + np.eye(n)) * 1e-9
    P = np.zeros((k, n))
    P[np.arange(k), rng.choice(n, k, replace=False)] = 1.0
    P[1, 7] = (-1.0)
    pencil = Pencil(R, L, csr_matrix(P))
    b = rng.standard_normal((n, 3)) + 1j * rng.standard_normal((n, 3))
    for f in (0.0, 1e3, 1e6, 1e9):
        s = 2j * np.pi * f
        for z in (np.zeros(k),
                  np.array(
                      [0.0, 1e-3, 5.0 + s * 1e-9, 1 /
                       (s * 1e-6) if f else 1e6])):
            want = np.linalg.solve(R + s * L + P.T @ np.diag(z) @ P, b)
            got = pencil.solve(s, z, b)
            assert np.max(np.abs(got - want)) < 1e-10 * np.max(np.abs(want))
    assert pencil.solve(0.0, np.zeros(k), b[:, 0]).shape == (n,)
    with pytest.raises(np.linalg.LinAlgError):
        Pencil(-R, L)


def test_the_pencil_is_taken_for_a_long_sweep_only(monkeypatch):
    from kicad2ems.pcbqs.kernels import pencil_pays
    monkeypatch.delenv('PCBQS_PENCIL', raising=False)
    assert (not pencil_pays(4000, 6) and pencil_pays(4000, 40)
            and not pencil_pays(100, 400))
    monkeypatch.setenv('PCBQS_PENCIL', 'never')
    assert not pencil_pays(4000, 400)
    monkeypatch.setenv('PCBQS_PENCIL', 'always')
    assert pencil_pays(100, 2)
