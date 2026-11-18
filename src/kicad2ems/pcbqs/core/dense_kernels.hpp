// Dense systems filled in one pass: what the frequency loops did in numpy, a
// temporary of the whole matrix per operation. Each row is one thread's;
// nothing is summed across threads, so results do not depend on their number.
// pcbqs/kernels.py has the numpy twins, which the tests hold these to.
#include <complex>
#include <limits>

using cplx = std::complex<double>;

// A CSR matrix's three arrays, checked against its shape; indices of either
// integer width.
struct SparseRows {
    arr<long long> indptr, indices;
    arr<cplx> data;
    const long long *ptr, *idx;
    const cplx *val;
    bool any;
    SparseRows(const arr<long long> &p, const arr<long long> &i,
               const arr<cplx> &d, long long rows, long long cols,
               const char *name)
        : indptr(p), indices(i), data(d), ptr(p.data()), idx(i.data()),
          val(d.data()), any(d.size() > 0) {
        if (!any) return;
        if (p.ndim() != 1 || p.shape(0) != rows + 1 || i.ndim() != 1 ||
            d.ndim() != 1 || i.shape(0) != d.shape(0) || ptr[0] != 0 ||
            ptr[rows] != d.shape(0))
            throw std::invalid_argument(
                std::string(name) + ": sparse term does not fit the matrix");
        for (long long r = 0; r < rows; ++r)
            if (ptr[r + 1] < ptr[r])
                throw std::invalid_argument(std::string(name) +
                                            ": sparse rows out of order");
        for (long long k = 0; k < d.shape(0); ++k)
            if (idx[k] < 0 || idx[k] >= cols)
                throw std::invalid_argument(std::string(name) +
                                            ": sparse column out of range");
    }
};

// out[a, b] = R[rows[a], cols[b]] + jw L[rows[a], cols[b]] + S[a, b],
// S sparse (CSR over out's shape, or empty). The loop impedance of the copper
// with what changes with frequency (the parts' own impedances, the
// conductors' resistance) on the unknowns kept: rows and cols pick them out of
// the whole matrices.
py::array_t<cplx> loop_system(arr<double> R, arr<double> L, cplx jw,
                              arr<long long> rows, arr<long long> cols,
                              arr<long long> indptr, arr<long long> indices,
                              arr<cplx> data, int workers) {
    if (workers < 1) throw std::invalid_argument("workers must be positive");
    if (R.ndim() != 2 || L.ndim() != 2 || R.shape(0) != L.shape(0) ||
        R.shape(1) != L.shape(1) || rows.ndim() != 1 || cols.ndim() != 1)
        throw std::invalid_argument("loop_system: R and L must be matrices of "
                                    "one shape, rows and cols lists");
    const long long N0 = R.shape(0), N1 = R.shape(1), nr = rows.shape(0),
                    nc = cols.shape(0);
    const long long *ra = rows.data(), *ca = cols.data();
    for (long long a = 0; a < nr; ++a)
        if (ra[a] < 0 || ra[a] >= N0)
            throw std::invalid_argument("loop_system: row out of range");
    for (long long b = 0; b < nc; ++b)
        if (ca[b] < 0 || ca[b] >= N1)
            throw std::invalid_argument("loop_system: column out of range");
    const SparseRows S(indptr, indices, data, nr, nc, "loop_system");
    py::array_t<cplx> out({(py::ssize_t)nr, (py::ssize_t)nc});
    cplx *o = out.mutable_data();
    const double *r = R.data(), *l = L.data();
    const double wr = jw.real(), wi = jw.imag();
    {
        py::gil_scoped_release release;
#pragma omp parallel for num_threads(workers) \
    schedule(static) if (workers > 1 && nr * nc > 65536)
        for (long long a = 0; a < nr; ++a) {
            const double *rr = r + (size_t)ra[a] * N1,
                         *lr = l + (size_t)ra[a] * N1;
            cplx *row = o + (size_t)a * nc;
            for (long long b = 0; b < nc; ++b) {
                const double x = lr[ca[b]];
                row[b] = cplx(rr[ca[b]] + wr * x, wi * x);
            }
            if (S.any)
                for (long long k = S.ptr[a]; k < S.ptr[a + 1]; ++k)
                    row[S.idx[k]] += S.val[k];
        }
    }
    return out;
}

// The charge solve's system at one frequency, from Z = R + jw L + S (S sparse:
// the parts and the resistance's change): columns below nl (the loops) are Z /
// zscale, the others (the tree's) jw Z / pscale, with `electro` added to the
// tree's own block; zscale = max |Z|. Returns (system, zscale). electro is
// real, or complex in a lossy stack.
template <class E>
static py::tuple peec_system_of(arr<double> R, arr<double> L, cplx jw,
                                double pscale, long long nl, arr<E> electro,
                                arr<long long> indptr, arr<long long> indices,
                                arr<cplx> data, int workers) {
    if (workers < 1) throw std::invalid_argument("workers must be positive");
    if (R.ndim() != 2 || L.ndim() != 2 || R.shape(0) != R.shape(1) ||
        L.shape(0) != R.shape(0) || L.shape(1) != R.shape(0))
        throw std::invalid_argument(
            "peec_system: R and L must be square matrices of one size");
    const long long n = R.shape(0), nt = n - nl;
    if (nl < 0 || nl > n || electro.ndim() != 2 || electro.shape(0) != nt ||
        electro.shape(1) != nt)
        throw std::invalid_argument(
            "peec_system: electro must be the tree's block");
    if (!(pscale > 0))
        throw std::invalid_argument("peec_system: pscale must be positive");
    const SparseRows S(indptr, indices, data, n, n, "peec_system");
    py::array_t<cplx> out({(py::ssize_t)n, (py::ssize_t)n});
    cplx *o = out.mutable_data();
    const double *r = R.data(), *l = L.data();
    const E *el = electro.data();
    const double wr = jw.real(), wi = jw.imag();
    std::vector<double> rowmax((size_t)n, 0.);
    double zscale = std::numeric_limits<double>::min();
    {
        py::gil_scoped_release release;
#pragma omp parallel for num_threads(workers) \
    schedule(static) if (workers > 1 && n > 256)
        for (long long i = 0; i < n; ++i) {
            const double *rr = r + (size_t)i * n, *lr = l + (size_t)i * n;
            cplx *row = o + (size_t)i * n;
            for (long long j = 0; j < n; ++j)
                row[j] = cplx(rr[j] + wr * lr[j], wi * lr[j]);
            if (S.any)
                for (long long k = S.ptr[i]; k < S.ptr[i + 1]; ++k)
                    row[S.idx[k]] += S.val[k];
            double big = 0.;
            for (long long j = 0; j < n; ++j)
                big = std::max(big, std::norm(row[j]));
            rowmax[(size_t)i] = big;
        }
        for (long long i = 0; i < n; ++i)
            zscale = std::max(zscale, std::sqrt(rowmax[(size_t)i]));
#pragma omp parallel for num_threads(workers) \
    schedule(static) if (workers > 1 && n > 256)
        for (long long i = 0; i < n; ++i) {
            cplx *row = o + (size_t)i * n;
            for (long long j = 0; j < nl; ++j) row[j] /= zscale;
            for (long long j = nl; j < n; ++j) row[j] = (jw * row[j]) / pscale;
            if (i >= nl) {
                const E *er = el + (size_t)(i - nl) * nt;
                for (long long j = 0; j < nt; ++j) row[nl + j] += er[j];
            }
        }
    }
    return py::make_tuple(out, zscale);
}

py::tuple peec_system(arr<double> R, arr<double> L, cplx jw, double pscale,
                      long long nl, py::array electro, arr<long long> indptr,
                      arr<long long> indices, arr<cplx> data, int workers) {
    if (electro.dtype().kind() == 'c')
        return peec_system_of<cplx>(R, L, jw, pscale, nl,
                                    arr<cplx>::ensure(electro), indptr, indices,
                                    data, workers);
    return peec_system_of<double>(R, L, jw, pscale, nl,
                                  arr<double>::ensure(electro), indptr, indices,
                                  data, workers);
}
