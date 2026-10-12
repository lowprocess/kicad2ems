"""
The EMerge run of a kicad2ems board: a script of its own, run with the Python EMerge is installed in (EMerge
2.5+ needs Python <= 3.13). It reads only the job and the board's JSON (<name>_board.json), so that Python
needs EMerge and nothing of kicad2ems.

    python worker.py job.json

job.json: board (path), freqs (Hz, the frequencies to solve), out (path of the result JSON), res (EMerge's
resolution, a fraction of the wavelength), cell (mm, the mesh size along copper edges), margin (mm of air
on every side), bc ('abc' absorbing or 'pec' a closed metal box), workers, dense (Hz: the run's frequencies,
where EMerge's rational fit gives S), mesh (a path: the element faces on the copper, ports and parts, for the
3D view, kicad2ems.meshes), mesh_only (stop there). Progress lines start with 'K2E '.

The model (the board's own rules, docs/model.md): each dielectric a box over the outline's bounding box,
copper as zero-thickness PEC sheets (holes cut out), vias as PEC cylinders, each port a lumped port from the
reference copper to the trace across its width, each part a lumped element filling its copper-free gap.
"""
import json, sys, time
import numpy as np

MM = 1e-3


def say(text):
    print(f"K2E {text}", flush=True)


def rings(geom):
    """GeoJSON Polygon / MultiPolygon -> [(exterior, [holes])], vertex lists without the closing point."""
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    return [([tuple(p) for p in poly[0][:-1]], [[tuple(p) for p in h[:-1]] for h in poly[1:]]) for poly in polys]


def impedance(part):
    """Z(f) of a part: R, L and C in series (any of them missing)."""
    R, L, C = part.get('R'), part.get('L'), part.get('C')

    def z(f):
        w = 2 * np.pi * f
        out = (R or 0.0) + 1j * w * (L or 0.0)
        return out + (1 / (1j * w * C) if C else 0.0)
    return z


def main(path):
    import emerge as em
    job = json.load(open(path))
    b = json.load(open(job['board']))
    freqs = np.asarray(job['freqs'], float)
    T = b['stack']['thickness']
    zem = lambda z: z - T                                           # EMerge's PCB stack runs from -T to 0
    t0 = time.time()
    say(f"EMerge {em.__version__}")
    model = em.Simulation('kicad2ems', loglevel='WARNING')
    diel = sorted([l for l in b['stack']['layers'] if l['kind'] == 'diel'], key=lambda l: l['z0'])
    stack = [em.geo.PCBLayer(l['z1'] - l['z0'], em.Material(er=l['er'], tand=l['tand'] or 0.0, name=l['name']))
             for l in diel]
    pcb = em.geo.PCBNew(T, unit=MM, stack=stack, trace_material=em.lib.PEC)
    cu_z = {c['name']: c['z'] for c in b['stack']['copper']}
    for layer, items in b['copper'].items():
        for it in items:
            for ext, holes in rings(it['polygon']):
                xs, ys = zip(*ext)
                pcb.add_poly(list(xs), list(ys), z=zem(cu_z[layer]))
                for h in holes:
                    xs, ys = zip(*h)
                    pcb.add_hole(list(xs), list(ys), z=zem(cu_z[layer]))
    for v in b['vias']:
        pcb.add_vias((v['x'], v['y']), radius=v['r'], z1=zem(v['z0']), z2=zem(v['z1']))
    (x0, y0, x1, y1) = _bounds(b['outline'])
    pcb.set_bounds(x0, y0, x1, y1)
    traces = pcb.compile_paths(merge=True, fragment=False)        # its fragmenting made zero-length edges (filters)
    if b['vias']:
        pcb.generate_vias(merge=True)
    ports = []
    for p in b['ports']:
        if p['kind'] != 'z':
            raise SystemExit(f"EMerge: port {p['name']} is a {p['kind']} port; only ports on a trace over a plane")
        ax, o = p['axis'], 1 - p['axis']
        e1, e2 = np.array(p['c'], float), np.array(p['c'], float)
        e1[o] -= p['w'] / 2
        e2[o] += p['w'] / 2
        ports.append(pcb.lumped_port_pts(tuple(e1 * MM), tuple(e2 * MM), zem(p['z_sig']) * MM, z_ground=zem(p['z_ref']),
                                         name=p['name']))
    elements = []
    for part in job.get('parts', b['parts']):                       # the plugin's pad-to-pad spans (pad_span)
        s, e = np.array(part['s'], float), np.array(part['e'], float)
        ax = part['axis']
        length, width = abs(e[ax] - s[ax]), abs(e[1 - ax] - s[1 - ax])
        if part.get('R') == 0 and not part.get('L') and not part.get('C'):
            pcb.add_poly([s[0], e[0], e[0], s[0]], [s[1], s[1], e[1], e[1]], z=zem(part['z']))   # a 0 ohm link: copper
            continue
        if part.get('cut'):                                         # copper reaching into the span: cut away
            pcb.add_hole([s[0], e[0], e[0], s[0]], [s[1], s[1], e[1], e[1]], z=zem(part['z']))
        z = zem(part['z']) * MM
        plate = em.geo.Plate((s[0] * MM, s[1] * MM, z), ((e[0] - s[0]) * MM, 0, 0), (0, (e[1] - s[1]) * MM, 0))
        elements.append((plate, impedance(part), width * MM, length * MM, part['ref']))
    pcb.generate_pcb(True, merge=True)
    mg = job['margin']
    below = 0.0 if job['bc'] == 'pec' and job.get('bottom_plane') else mg   # a closed box: its floor the bottom plane
    air = em.geo.Box((x1 - x0 + 2 * mg) * MM, (y1 - y0 + 2 * mg) * MM, (T + mg + below) * MM,
                     position=((x0 - mg) * MM, (y0 - mg) * MM, (-T - below) * MM)).background()
    model.commit_geometry()
    model.mw.set_frequency(list(freqs))
    model.mw.set_resolution(job['res'])
    model.mesher.set_boundary_size(traces, job['cell'] * MM)
    for pp in ports:
        model.mesher.set_face_size(pp, job['cell'] * MM)
    for plate, func, w, h, ref in elements:                         # about 50 elements, as EMerge's own layouter
        model.mesher.set_face_size(plate, min(job['cell'] * MM / 2, (w * h / 50) ** 0.5))
    model.generate_mesh()
    say(f"mesh {model.mesh.n_tets} tetrahedra, {time.time() - t0:.1f} s")
    if job.get('mesh'):                                             # the 3D view's: element faces on copper, ports, parts
        surf = [traces] + ports + [plate for plate, *_ in elements]
        tags = sorted({t for g in surf for t in getattr(g, 'tags', [])})
        tri = np.unique(np.concatenate([np.asarray(model.mesh.get_triangles([t]), int) for t in tags
                                        if t in model.mesh.ftag_to_tri]))
        corners = np.asarray(model.mesh.tris)[:, tri].T                # (m, 3) node indices
        used, inv = np.unique(corners, return_inverse=True)
        nodes = np.asarray(model.mesh.nodes)[:, used].T / MM          # model mm, z from the bottom copper
        nodes[:, 2] += T
        np.savez_compressed(job['mesh'], kind='tris', nodes=nodes, tris=inv.reshape(-1, 3), tets=int(model.mesh.n_tets))
        if job.get('mesh_only'):
            say("mesh written")
            return
    for i, (pp, p) in enumerate(zip(ports, b['ports'])):
        model.mw.bc.LumpedPort(pp, i + 1, Z0=p['z0'])
    for plate, func, w, h, ref in elements:
        model.mw.bc.LumpedElement(plate, func, width=w, height=h)
    if job['bc'] == 'abc':
        model.mw.bc.AbsorbingBoundary(air.boundary())
    t1 = time.time()
    data = model.mw.run_sweep(parallel=job['workers'] > 1, n_workers=job['workers'], frequency_groups=job['workers'] * 2)
    g = data.scalar.grid
    n = len(b['ports'])
    f = np.asarray(g.freq, float)
    S = np.stack([np.stack([np.asarray(g.S(i + 1, j + 1)) for j in range(n)], -1) for i in range(n)], 1)
    say(f"solved {len(f)} frequencies in {time.time() - t1:.0f} s")
    out = dict(f=f.tolist(), re=S.real.tolist(), im=S.imag.tolist(), seconds=time.time() - t0,
               version=em.__version__, mesh_s=t1 - t0, tets=int(model.mesh.n_tets))
    if job.get('dense') and len(f) > 3:                             # EMerge's rational fit at the run's frequencies
        try:
            fd = np.asarray(job['dense'], float)
            D = np.stack([np.stack([np.asarray(g.model_S(i + 1, j + 1, fd)) for j in range(n)], -1)
                          for i in range(n)], 1)
            out.update(dense_re=D.real.tolist(), dense_im=D.imag.tolist())
        except Exception as e:                                      # the plugin interpolates instead
            say(f"no rational fit ({type(e).__name__}: {e}): interpolated")
    json.dump(out, open(job['out'], 'w'))


def _bounds(outline):
    pts = [p for ext, holes in rings(outline) for p in ext]
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


if __name__ == '__main__':
    main(sys.argv[1])
