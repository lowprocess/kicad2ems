"""
Each solver's discretisation of the board, for the GUI's 3D view: <name>_mesh_<solver>.npz in the output folder,
model mm (y up, z = 0 the bottom copper), written when the solver prepares (a preview too) or solves.

    kind 'grid': x, y, z     openEMS's mesh lines
    kind 'tris': nodes (n, 3), tris (m, 3)    a surface mesh: mom25d's triangles on each layer

    save(run, 'openems', 'grid', x=..., y=..., z=...)
    find(out_dir) -> {solver: path}; segments(path, box) -> (2 k, 3) line segment end points, for drawing
"""
import glob, os
import numpy as np

LABELS = {'openems': 'openEMS grid', 'gfdtd': 'gfdtd grid (openEMS settings)', 'mom25d': 'mom25d mesh (MoM)'}


def save(run, solver, kind, **arrays):
    path = run.path(f'_mesh_{solver}.npz')
    np.savez_compressed(path, kind=kind, **{k: np.asarray(v) for k, v in arrays.items()})
    return path


def find(out_dir):
    """{solver: path} of the mesh files of a results folder (the newest run's name first)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(out_dir, '*_mesh_*.npz')), key=os.path.getmtime, reverse=True):
        solver = os.path.basename(p)[:-4].rsplit('_mesh_', 1)[1]
        out.setdefault(solver, p)
    return out


def segments(path, box=None, planes=None):
    """A mesh file as line segments (2 k, 3) to draw, model mm. A grid: its x and y lines across the board (box:
    xmin, ymin, xmax, ymax, z0, z1) on each of `planes` (z, mm: the copper layers; default the box's bottom
    and top), and its lines on the box's four sides; a surface mesh: the edges of its triangles, each once."""
    d = np.load(path)
    if str(d['kind']) == 'grid':
        x, y, z = (np.asarray(d[k], float) for k in 'xyz')
        x0, y0, x1, y1, z0, z1 = box if box is not None else (x.min(), y.min(), x.max(), y.max(), z.min(), z.max())
        xi, yi, zi = x[(x >= x0 - 1e-9) & (x <= x1 + 1e-9)], y[(y >= y0 - 1e-9) & (y <= y1 + 1e-9)], \
            z[(z >= z0 - 1e-9) & (z <= z1 + 1e-9)]
        seg = []
        for zz in planes if planes is not None else (z0, z1):      # the copper layers
            seg += [((a, y0, zz), (a, y1, zz)) for a in xi] + [((x0, b, zz), (x1, b, zz)) for b in yi]
        for b in (y0, y1):                                          # the sides: verticals at each x / y, rings at each z
            seg += [((a, b, z0), (a, b, z1)) for a in xi]
        for a in (x0, x1):
            seg += [((a, b, z0), (a, b, z1)) for b in yi]
        for zz in zi:
            seg += [((x0, y0, zz), (x1, y0, zz)), ((x1, y0, zz), (x1, y1, zz)),
                    ((x1, y1, zz), (x0, y1, zz)), ((x0, y1, zz), (x0, y0, zz))]
        return np.asarray(seg, float).reshape(-1, 3)
    nodes, tris = np.asarray(d['nodes'], float), np.asarray(d['tris'], int)
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    e = np.unique(e, axis=0)
    return nodes[e].reshape(-1, 3)


def counts(path):
    """A one-line size of a mesh file: grid lines, or triangles."""
    d = np.load(path)
    if str(d['kind']) == 'grid':
        return f"{len(d['x'])} x {len(d['y'])} x {len(d['z'])} lines"
    return f"{len(d['tris']):,} triangles" + (f", {int(d['tets']):,} tetrahedra" if 'tets' in d else '')
