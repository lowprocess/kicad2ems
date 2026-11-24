"""Saving results: a mode table (CSV), a summary (JSON) and the meshes and
mode fields (NPZ, reloadable with load_fields)."""
import csv
import json
import os
from dataclasses import asdict

import numpy as np


def mode_rows(results):
    """One dict per mode of every pair: the table the GUI shows and the CSV."""
    rows = []
    for r in results:
        m = r.modes
        for k in range(len(m.f)):
            top = int(np.argmax(m.zpeak[:, k])) if m.zpeak.size else -1
            top = top if top >= 0 and m.zpeak[top, k] >= 0.01 else -1    # (0.01 ohm: none)
            rows.append(dict(
                pair=r.cavity.name, mode=k + 1, f_GHz=round(m.f[k] / 1e9, 6),
                Q=round(float(m.Q[k]), 1), participation=round(float(m.pr[k]), 5),
                peak_over_rms=round(float(m.peak[k]), 3),
                strongest_via=r.cavity.ports[top][3] if top >= 0 else '',
                via_z_ohm=round(float(m.zpeak[top, k]), 3) if top >= 0 else ''))
    return rows


def save_results(results, folder, board=None, cfg=None):
    """Write modes.csv, summary.json and fields.npz into `folder`."""
    os.makedirs(folder, exist_ok=True)
    rows = mode_rows(results)
    with open(os.path.join(folder, 'modes.csv'), 'w', newline='') as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    summary = dict(
        board=getattr(board, 'source', None),
        config=asdict(cfg) if cfg is not None else None,
        pairs=[dict(pair=r.cavity.name, d_mm=r.cavity.d, er=r.cavity.er, tand=r.cavity.tand,
                    area_mm2=round(r.cavity.region.area, 2), ground_vias=len(r.cavity.shorts),
                    other_vias=len(r.cavity.ports), nodes=len(r.mesh.nodes),
                    modes=len(r.modes.f), capped=r.modes.capped, seconds=r.seconds)
               for r in results])
    with open(os.path.join(folder, 'summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=1, default=str)
    arrays = {}
    for i, r in enumerate(results):
        p = f'p{i}_'
        arrays.update({p + 'nodes': r.mesh.nodes, p + 'tris': r.mesh.tris,
                       p + 'fixed': r.mesh.fixed, p + 'f': r.modes.f, p + 'Q': r.modes.Q,
                       p + 'V': r.modes.V.astype(np.float32)})
        arrays[p + 'name'] = np.array(r.cavity.name)
    np.savez_compressed(os.path.join(folder, 'fields.npz'), **arrays)
    return [os.path.join(folder, n) for n in ('modes.csv', 'summary.json', 'fields.npz')]


def load_fields(path):
    """fields.npz -> [dict(name, nodes, tris, fixed, f, Q, V)] per pair."""
    z = np.load(path)
    n = len({k.split('_')[0] for k in z.files})
    return [{k: (str(z[f'p{i}_{k}']) if k == 'name' else z[f'p{i}_{k}'])
             for k in ('name', 'nodes', 'tris', 'fixed', 'f', 'Q', 'V')} for i in range(n)]
