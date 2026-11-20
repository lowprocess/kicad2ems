"""
openEMS's automesh (python/openEMS/automesh.py, GPL-3.0, as upstream at openEMS
1b46454) on gfdtd's primitives: the mesh hints a box's edges give
(openEMS.AddEdges2Grid), and the CFL time step of a mesh.
"""
import sys

import numpy as np

from .constants import C0

AXES = 'xyz'


def multi_dirs(dirs):
    """[axis indices] of 'x', 'xy', 'xyz', 'all' (CSXCAD's GetMultiDirs)."""
    if dirs == 'all':
        return [0, 1, 2]
    return [AXES.index(c) for c in str(dirs).lower() if c in AXES]


def mesh_combine(mesh1, mesh2, sort=True):
    mesh = [None, None, None]
    for ny in range(3):
        if mesh1[ny] is None and mesh2[ny] is None:
            continue
        elif mesh1[ny] is None:
            mesh[ny] = mesh2[ny]
        elif mesh2[ny] is None:
            mesh[ny] = mesh1[ny]
        else:
            mesh[ny] = list(sorted(mesh1[ny] + mesh2[ny]))
    return mesh


def mesh_hint_from_primitive(primitive, dirs, **kw):
    """A box's hint (mesh_hint_from_box); None for any other primitive."""
    if getattr(primitive, 'kind', '') == 'Box':
        return mesh_hint_from_box(primitive, dirs, **kw)
    return None


def mesh_hint_from_box(box, dirs, **kw):
    """mesh_hint_from_box(box, dirs, metal_edge_res=None, up_dir=True,
    down_dir=True, mesh=None): [3 lists or None] of lines at the box's edges
    in dirs, with metal_edge_res: a third inside and two thirds outside each
    edge (openEMS's 2D metal-edge rule)."""
    metal_edge_res = kw.get('metal_edge_res', None)
    up_dir = kw.get('up_dir', True)
    down_dir = kw.get('down_dir', True)
    if metal_edge_res is None:
        mer = 0
    else:
        mer = np.array([-1.0, 2.0]) / 3 * metal_edge_res
    if getattr(box, 'transform', None):
        sys.stderr.write('FDTD::automesh: Warning, cannot add edges to grid '
                         'with transformations enabled\n')
        return None
    hint = [None, None, None]
    start = np.fmin(box.GetStart(), box.GetStop())
    stop = np.fmax(box.GetStart(), box.GetStop())
    for ny in multi_dirs(dirs):
        hint[ny] = []
        if metal_edge_res is not None and stop[ny] - start[ny] > metal_edge_res:
            if down_dir:
                hint[ny].append(start[ny] - mer[0])
                hint[ny].append(start[ny] - mer[1])
            if up_dir:
                hint[ny].append(stop[ny] + mer[0])
                hint[ny].append(stop[ny] + mer[1])
        elif stop[ny] - start[ny]:
            if down_dir:
                hint[ny].append(start[ny])
            if up_dir:
                hint[ny].append(stop[ny])
        else:
            hint[ny].append(start[ny])
    if 'mesh' in kw:
        return mesh_combine(hint, kw['mesh'])
    return hint


def mesh_estimate_cfl_timestep(mesh):
    """The largest stable time step of a grid in vacuum, s."""
    inv = [np.min(np.diff(mesh.GetLines(ny))) ** -2 for ny in range(3)]
    return mesh.GetDeltaUnit() / (C0 * np.sqrt(np.sum(inv)))
