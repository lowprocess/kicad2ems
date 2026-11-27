"""
The temperatures where kicad2ems's viewer reads results: the board and, per
layer, its map on a grid, to turn, cut and probe in its 2D and 3D
views (as the DC drop's maps: pdn/viewer.py).

    write_viewer(out, board, problem, result)  the files' names, for
                                               thermal.json

It writes
    <out>/thermal_board.json  the board: all its copper (it all conducts), the
                              parts that dissipate marked
    <out>/thermal/exc_board/  dump_T_<layer>_fd.npz per layer: the
                              temperature, C (NaN off the board)
with a dump_info.json, the dumps at 0 Hz as plane dumps (a temperature is one
number: the vector's x). Each says its unit and that its colours span its own
values ("range": "data"), so a map runs from the board's coolest point to its
hottest and not from zero.
"""
import json
import os
from types import SimpleNamespace

import numpy as np

GROUP, RUN = 'thermal', 'exc_board'


def _maps(r):
    """A result's temperatures per layer on a grid: (x, y lines mm, [(ny, nx) C,
    NaN off the board])."""
    ny, nx = r.T[0].shape
    return (r.x0 + (np.arange(nx) + 0.5) * r.size, r.y0 +
            (np.arange(ny) + 0.5) * r.size, list(r.T))


def write_viewer(out, board, problem, result):
    """Write the files above for a Result; returns dict(board=,
    dumps=[{method, layer, file}])."""
    from kicad2ems.core.fields.dumps import Dump, write_info, write_npz
    from ..mqs_layout import crop_layout, geometry
    p = problem
    nets = {r['net'] for records in board['copper'].values() for r in records}
    layout = crop_layout(board, p.outline, nets, set(p.powers))
    with open(os.path.join(out, 'thermal_board.json'), 'w',
              encoding='utf-8') as fh:
        fh.write(json.dumps(layout))  # (dumps: the C encoder, 5-10x dump's)
    exc = os.path.join(out, GROUP, RUN)
    os.makedirs(exc, exist_ok=True)
    dumps, meta, files = [], {}, []

    def add(method, layer, z, x, y, t):
        dm = Dump(f"T@{layer}", 'T', (x[0], y[0], z), (x[-1], y[-1], z), [0.0],
                  2, layer)
        data = np.zeros((len(x), len(y), 3))
        data[:, :, 0] = t.T  # (ny, nx) -> (nx, ny)
        write_npz(os.path.join(exc, dm.name + '.npz'),
                  dict(x=x, y=y, z=np.array([z])), [0.0], data[None, :, :,
                                                               None, :])
        dumps.append(dm)
        meta[dm.name] = dict(unit='°C', range='data')
        files.append(
            dict(method=method, layer=layer, unit='°C',
                 file=os.path.join(GROUP, RUN, dm.name + '.npz')))
    # the top layer first
    order = sorted(range(len(p.layers)), key=lambda l: -p.layers[l].z)
    x, y, maps = _maps(result)
    for l in order:
        add(result.method, p.layers[l].name, p.layers[l].z, x, y, maps[l])
    for stale in os.listdir(exc):  # a layer of an earlier solve in this folder
        if (stale.startswith('dump_') and stale.endswith('.npz')
                and stale[:-4] not in meta):
            os.remove(os.path.join(exc, stale))
    cu_z = {l.name: l.z for l in p.layers}
    m = SimpleNamespace(
        outline=p.outline, cu_z=cu_z, copper={}, ports=[], vias=[],
        thickness=board['stack'].get('thickness', max(cu_z.values())))
    write_info(exc, dumps, m)
    path = os.path.join(exc, 'dump_info.json')
    with open(path, encoding='utf-8') as fh:
        info = json.load(fh)
    for d in info['dumps']:
        d.update(meta[d['name']])
    info.update(geometry(layout))  # the copper, the vias and the parts
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(info))
    return dict(board='thermal_board.json', dumps=files)
