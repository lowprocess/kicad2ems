"""
The numerics mom25d and pcbqs share (one copy: pcbqs's had been a copy of
mom25d's), on numpy, scipy and shapely:

    base     RWG basis functions on triangles, the constants, the quadrature
             rules, the moments' assembly (combine)
    geo      mesh_polygons: constrained-Delaunay meshes of copper (edge rings,
             port and cut lines, vias, coarsening)
    cdtmesh  the points of geo's triangulation (mesh2d: a compiled core's
             cdt_triangulate, emsdev/native/cdt_mesh.hpp)
    runs     straight runs of trace (find_runs): geo's structured grid
    sing     analytic 1/R integrals between triangles with an out-of-plane
             offset (offplane_pairs)
    biot     the magnetic field of surface currents and filaments (Biot-Savart),
             the currents on a grid
"""
