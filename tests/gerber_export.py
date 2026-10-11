"""Fabrication data of an imported board for the importer-parity tests: one Gerber X2 file per copper layer
(the nets as TO.N attributes; tracks as draws with round apertures, vias as flashes, everything else as
regions), a profile (the outline), Excellon drill files (plated, with their X2 layer span) and a KiCad job
file with the stackup - what KiCad's Fabrication Outputs write, in the parts the Gerber importer reads.

    job = export(KiCadBoard('demo.kicad_pcb'), 'fab/')      # fab/demo-job.gbrjob and the files it lists
"""
import json, os
from shapely import affinity
from shapely.ops import unary_union
from kicad2ems.geometry import polygons, split_holes
from kicad2ems.importers.kicad.outline import board_outline

HEAD = ['%TF.GenerationSoftware,kicad2ems tests*%', '%TF.FileFunction,{}*%', '%FSLAX46Y46*%', '%MOMM*%', '%LPD*%']


def _c(v):
    return f"{int(round(v * 1e6))}"


def write_gerber(path, function, tracks=(), flashes=(), regions=()):
    """tracks [(net, width, [(x, y), ...])], flashes [(net, diameter, x, y)], regions [(net, polygon)] in
    Gerber coordinates (mm, y up); polygons with holes are cut into pieces without holes."""
    L = [h.format(function) if '{}' in h else h for h in HEAD]
    code, net = 10, None

    def set_net(n):
        nonlocal net
        if n != net:
            L.append(f'%TO.N,{n}*%' if n else '%TD*%')
            net = n
    for n, w, pts in tracks:
        L += [f'%ADD{code}C,{w:.6f}*%', f'D{code}*']
        set_net(n)
        L.append(f'X{_c(pts[0][0])}Y{_c(pts[0][1])}D02*')
        L += [f'X{_c(x)}Y{_c(y)}D01*' for x, y in pts[1:]]
        code += 1
    for n, d, x, y in flashes:
        L += [f'%ADD{code}C,{d:.6f}*%', f'D{code}*']
        set_net(n)
        L.append(f'X{_c(x)}Y{_c(y)}D03*')
        code += 1
    for n, poly in regions:
        set_net(n)
        for piece in split_holes(poly):
            pts = list(piece.exterior.coords)
            L += ['G36*', f'X{_c(pts[0][0])}Y{_c(pts[0][1])}D02*'] + [f'G01X{_c(x)}Y{_c(y)}D01*' for x, y in pts[1:]] + ['G37*']
    with open(path, 'w') as fh:
        fh.write("\n".join(L + ['M02*']) + "\n")


def export(raw, outdir, base=None):
    """Write the fabrication data of a KiCadBoard to outdir; returns the job file's path."""
    os.makedirs(outdir, exist_ok=True)
    base = base or os.path.splitext(os.path.basename(raw.path))[0]
    up = lambda pts: [(float(x), -float(y)) for x, y in pts]
    n_cu = len(raw.copper_order)
    files = []
    for i, lay in enumerate(raw.copper_order):
        tracks = [(t['net'], t['width'], up(t['pts'])) for t in raw.tracks if t['layer'] == lay]
        flashes = [(v['net'], v['size'], v['x'], -v['y']) for v in raw.vias if lay in v['layers'] and v['size'] > 0]
        # the rest of the layer as regions merged per net: zones and drawn copper, pads
        by = {}
        for n, g in [(n, g) for l, n, g in raw.shapes if l == lay] + \
                [(q['net'], q['geom']) for fp in raw.footprints for q in fp['pads'] if lay in q['layers']]:
            by.setdefault(n, []).append(g)
        regions = [(n, affinity.scale(p, 1, -1, origin=(0, 0))) for n, gs in by.items() for p in polygons(unary_union(gs))]
        func = 'Copper,L%d,%s' % (i + 1, 'Top' if i == 0 else ('Bot' if i == n_cu - 1 else 'Inr'))
        fn = f"{base}-{lay.replace('.', '_')}.gbr"
        write_gerber(os.path.join(outdir, fn), func, tracks, flashes, regions)
        files.append(dict(Path=fn, FileFunction=func, FilePolarity='Positive'))
    ol = board_outline(raw)
    if ol is not None:
        fn = f"{base}-Edge_Cuts.gbr"
        write_gerber(os.path.join(outdir, fn), 'Profile,NP', [('', 0.05, up(ol.exterior.coords))])
        files.append(dict(Path=fn, FileFunction='Profile,NP', FilePolarity='Positive'))
    stack = []
    for it in raw.stack_items:
        if it['kind'] == 'copper':
            stack.append(dict(Type='Copper', Thickness=it['t'] * 1e3, Name=it['name']))
        else:
            stack.append(dict(Type='SolderMask' if it['kind'] == 'mask' else 'Dielectric', Thickness=it['t'] * 1e3,
                              Name=it['name'], DielectricConstant=it['er'], LossTangent=it['tand']))
    groups = {}
    for v in raw.vias:
        i0, i1 = raw.copper_order.index(v['layers'][0]) + 1, raw.copper_order.index(v['layers'][-1]) + 1
        groups.setdefault((min(i0, i1), max(i0, i1)), []).append(v)
    for (i0, i1), vs in groups.items():
        kind = 'PTH' if (i0, i1) == (1, n_cu) else 'Blind'
        L = ['M48', f'; #@! TF.FileFunction,Plated,{i0},{i1},{kind}', 'METRIC']
        tools = sorted({v['drill'] for v in vs})
        L += [f'T{k + 1}C{d:.3f}' for k, d in enumerate(tools)] + ['%', 'G90', 'G05']
        for k, d in enumerate(tools):
            L.append(f'T{k + 1}')
            L += [f"X{v['x']:.4f}Y{-v['y']:.4f}" for v in vs if v['drill'] == d]
        with open(os.path.join(outdir, f"{base}-{i0}-{i1}.drl"), 'w') as fh:
            fh.write("\n".join(L + ['M30']) + "\n")
    job = os.path.join(outdir, base + '-job.gbrjob')
    with open(job, 'w') as fh:
        json.dump(dict(Header=dict(GenerationSoftware=dict(Application='kicad2ems tests')), FilesAttributes=files,
                       MaterialStackup=stack), fh, indent=1)
    return job

