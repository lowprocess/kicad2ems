"""
mom25d as a second full-wave reference: the openEMS geometry of a run (<name>.xml, CSXCAD's format, the
file AppCSXCAD shows) is solved by the 2.5D Method-of-Moments solver mom25d, so FDTD, MoM and the line
model all start from one converted board.

    write_xml(m, mesh, fmin, fmax, path, bc)      # CSX geometry + <mom25d> hints (needs CSXCAD / openEMS)
    S, names, zref, info = solve(path, m, freq, dmax=0.3)

mom25d is its own repository, a git submodule in mom25d/ (pip install ./mom25d, or used from the
checkout after git submodule update --init). The hints tell it what the CSX file cannot: the ground layer, the exact loss tangents, the port
names, layers and feed directions. MoM assumes laterally infinite dielectrics over an infinite ground
plane; the board outline, edge walls (approximated by via rows) and the air box do not exist for it.
"""
import os, sys
import xml.etree.ElementTree as ET
import numpy as np

VENDORED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mom25d', 'src')


def import_mom25d():
    """The mom25d package: installed, or from the mom25d/ submodule of this checkout."""
    try:
        import mom25d.csx                                           # noqa: F401
    except ImportError:
        for k in [k for k in sys.modules if k == 'mom25d' or k.startswith('mom25d.')]:
            del sys.modules[k]                                      # a namespace stub of the folder itself
        if not os.path.isfile(os.path.join(VENDORED, 'mom25d', '__init__.py')):
            raise ImportError("mom25d is not installed: git submodule update --init (in the kicad2openems "
                              "checkout), then pip install ./mom25d or run from the checkout")
        sys.path.insert(0, VENDORED)
        import mom25d.csx                                           # noqa: F401
    import mom25d
    return mom25d


def add_hints(path, m, fmin, fmax):
    """Add (or replace) the <mom25d> hints element of a CSX file (see mom25d.csx)."""
    tree = ET.parse(path)
    root = tree.getroot()
    for old in root.findall('mom25d'):
        root.remove(old)
    h = ET.SubElement(root, 'mom25d', version='1', f_loss=f"{0.5 * (fmin + fmax):.6g}")
    if getattr(m, 'ground', None):
        h.set('ground', m.ground)
    for lay in m.cu_order:
        ET.SubElement(h, 'Layer', name=lay, z=f"{m.cu_z[lay]:.9g}")
    for l in m.layers:
        ET.SubElement(h, 'Dielectric', name=str(l['name']), z0=f"{l['z0']:.9g}", z1=f"{l['z1']:.9g}",
                      er=f"{l['er']:.9g}", tand=f"{l['tand']:.9g}")
    for i, p in enumerate(m.ports):
        if p.kind != 'z':
            continue                                                # gap ports (antennas): no planar equivalent
        d = np.zeros(2)
        d[p.axis] = p.sgn
        ET.SubElement(h, 'Port', number=str(i + 1), name=p.name, layer=p.layer, ref_layer=p.ref_layer or '',
                      x=f"{p.c[0]:.9g}", y=f"{p.c[1]:.9g}", dx=f"{d[0]:g}", dy=f"{d[1]:g}", width=f"{p.w:.9g}",
                      z0=f"{p.z0:g}")
    ET.indent(tree)
    tree.write(path, xml_declaration=True, encoding='utf-8')


def write_xml(m, mesh, fmin, fmax, path, bc=('MUR',) * 6, **kw):
    """The CSX geometry openEMS would simulate (build_fdtd, no run) as <path>, with the mom25d hints."""
    from .simulation import build_fdtd
    FDTD, CSX, _ = build_fdtd(m, mesh, fmin, fmax, 0, bc=bc, **kw)
    CSX.Write2XML(os.path.abspath(path))                            # FDTD owns CSX: keep it alive until here
    del FDTD, CSX
    add_hints(path, m, fmin, fmax)
    return path


def solve(path, m, freq, dmax=0.3, port='auto', mesher='triangle', max_solves=60, log=print):
    """S-parameters of the CSX file `path` on `freq` (Hz) from mom25d, as kicad2openems reports them:
    (S (nf, n, n) in m.user_ports order with differential pairs as (d, c) modes, names, reference
    impedances, info dict). dmax in mm (planes 3x, refined near the copper of other layers)."""
    import_mom25d()
    from mom25d.csx import CSXModel
    from mom25d.run import solve_band
    from .sparams import mixed_mode
    fmin, fmax = float(freq[0]), float(freq[-1])
    kind = 'via' if port == 'lumped' else port
    pairs = any(len(legs) == 2 for _, legs in m.user_ports) and kind != 'via'
    model = CSXModel(path, port=kind, pairs=pairs, f_loss=0.5 * (fmin + fmax), log=log)
    for line in model.describe().splitlines()[1:]:
        log(f"  mom25d: {line}")
    dm = dmax * 1e-3
    area = m.outline.area * 1e-6
    planes = {k: 3 * dm for k, gs in model.shapes.items() if sum(g.area for g in gs) > 0.5 * area}
    sim = model.simulation(dmax=dm, dmax_layer=planes, fmin=fmin, fmax=fmax, solver='auto', verbose=False,
                           wave_down_to=fmin,
                           mesher='gmsh' if mesher == 'gmsh' else 'auto', title=os.path.basename(path))
    for note in [n for n in model.notes if 'lumped port' in n] + list(sim.log.notes):
        log(f"  mom25d: {note}")
    unknowns = int(sim.Ne + sim.Nv)
    log(f"mom25d: {unknowns:,} unknowns, solver {sim.solver}, ports " +
        ", ".join(f"{p.name} ({'wave' if hasattr(p, 's0') else 'lumped'})" for p in sim.ports))
    f, S, info = solve_band(sim, fmin, fmax, points=len(freq), zref=model.port_z0, max_solves=max_solves, afs_tol=5e-3,
                            log=lambda t: log(f"  mom25d:{t}") if 'AFS' in t else None)
    if len(f) != len(freq) or not np.allclose(f, freq):
        S = np.stack([np.stack([np.interp(freq, f, S[:, i, j].real) + 1j * np.interp(freq, f, S[:, i, j].imag)
                                for j in range(S.shape[2])], -1) for i in range(S.shape[1])], 1)
    if pairs:                                                       # mom25d's modal ports -> kicad2openems order
        _, names, zref = mixed_mode(np.zeros((1, len(m.ports), len(m.ports))), m)
        idx = [info['names'].index(n) for n in names]
        S = S[:, idx][:, :, idx]
    else:
        order = [info['names'].index(p.name) for p in m.ports]
        S, names, zref = mixed_mode(S[:, order][:, :, order], m)
    info.update(unknowns=unknowns, solver=sim.solver, mesher=mesher, dmax_mm=dmax,
                ports=[dict(name=p.name, kind='wave' if hasattr(p, 's0') else 'lumped',
                            zc=float(getattr(p, 'zc', 0) or 0)) for p in sim.ports],
                notes=list(model.notes) + list(sim.log.notes), xml=path,
                triangles=int(len(sim.rwg.tris)))
    return S, names, zref, info
