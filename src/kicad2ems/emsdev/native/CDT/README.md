# CDT (vendored)

The constrained Delaunay triangulation of [artem-ogre/CDT](https://github.com/artem-ogre/CDT), release 2.0.1
(commit 53eff28, GitHub's tarball sha256 dbdf0ea5713d7690e6ba3dc4b183ba20c4b42df1377147f6f185eb6347b1b61b): the
headers of its `CDT/include`, unmodified, used header-only by `../cdt_mesh.hpp` (`cdt_triangulate`, in mom25d's and
pcbqs's cores: `mom25d/core/mom25d_core.cpp`, `pcbqs/core/pcbqs_core.cpp`).

Licences: CDT is MPL-2.0 (`LICENSE`); `predicates.h` is BSD-3-Clause (William C. Lenthe, its header);
`portable_nth_element.hpp` is Apache-2.0 with LLVM exceptions (its header). All are compatible with kicad2ems's GPL-3.0.
