"""
kicad2openems - KiCad board -> openEMS (3D FDTD) with automatic meshing

    kicad2openems board.kicad_pcb --pos board-top.pos --nets SIG,GND --fmin 1 --fmax 6 --preview
    kicad2openems board.kicad_pcb --config board.toml --excite P1
    kicad2openems board.kicad_pcb ... --compare other_tool.s2p          # overlay another result

Ports are footprints with a Fab-layer text P1, P2, ... (or Reference / Value P<n> in the
pick-and-place file); P<n>+ / P<n>- give a differential port. --preview writes the model + mesh
plot and the cell / time-step report without running openEMS.

Outputs in --out: <name>_openems.sNp, <name>_openems.png, <name>_mesh.png, <name>_openems.json,
the CSX geometry <name>.xml (AppCSXCAD) and the raw runs under openems/.
"""
import argparse, json, os, sys, time
import numpy as np

from .kicad.board import KiCadBoard
from .model import Model
from .boundaries import resolve_bc, cavity_check
from .mesh import auto_mesh
from .plots import preview, plot_results, plot_convergence
from .sparams import mixed_mode, fill_reciprocal
from .touchstone import write_touchstone, read_touchstone
from .config import apply_toml
from .dumps import parse_dump, estimate_size_mb, plot_fd_dumps


def parser():
    ap = argparse.ArgumentParser(prog='kicad2openems', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board', nargs='?', help='.kicad_pcb')
    ap.add_argument('--check', action='store_true', help='check that this python can run openEMS, then exit')
    ap.add_argument('--config', help='TOML file with any of these options (openEMS ones also under [openems])')
    g = ap.add_argument_group('board')
    g.add_argument('--pos', help='pick-and-place file listing the port components')
    g.add_argument('--nets', help='comma-separated nets to include (default: all copper)')
    g.add_argument('--region', help='crop x0,y0,x1,y1 (KiCad mm)')
    g.add_argument('--ground', help='copper layer to model as a solid plane over the whole outline')
    g.add_argument('--mask', action='store_true', help='include solder mask layers (thin: smaller time step)')
    g.add_argument('--parts', default='auto', choices=['auto', 'none'],
                   help='lumped R / L / C parts between two pads on the modelled copper (default auto)')
    g = ap.add_argument_group('frequency')
    g.add_argument('--fmin', type=float, default=0.5, help='GHz')
    g.add_argument('--fmax', type=float, default=10.0, help='GHz')
    g.add_argument('--points', type=int, default=401)
    g = ap.add_argument_group('mesh')
    g.add_argument('--res', type=float, default=20, help='cells per wavelength at fmax (in each medium)')
    g.add_argument('--fine', type=float, help='mm, cell size at copper edges (default: min port width / 4)')
    g.add_argument('--air', type=float, default=5.0, help='mm of air between the board and the boundary')
    g.add_argument('--ratio', type=float, default=1.4, help='max growth between neighbouring cells')
    g.add_argument('--nz', type=int, default=4, help='minimum cells through each dielectric layer')
    g.add_argument('--curves', default='extremes', choices=['extremes', 'vertices'],
                   help='mesh lines for rounded corners / arcs / circles: only the ends and extremes of each curve '
                        '(default) or every vertex')
    g.add_argument('--vias', default='auto', choices=['auto', 'exact', 'centre', 'snap'],
                   help='via mesh lines: auto = exact on signal nets, snap elsewhere; exact = centre and +-radius; '
                        'centre = one line per axis; snap = move onto existing lines (within --via-snap)')
    g.add_argument('--via-snap', type=float, default=0.2, help='mm a snapped via may move (default 0.2)')
    g.add_argument('--converge', metavar='F1,F2,...',
                   help='mesh convergence check: run on several mesh levels, level f with --res x f and --fine / f '
                        '(e.g. 1,1.5,2); the finest level gives the result')
    g.add_argument('--converge-tol', default='0.1,2', metavar='DB,DEG',
                   help='converged when the second-finest level is within this of the finest (default 0.1 dB, 2 deg)')
    g = ap.add_argument_group('solver')
    g.add_argument('--bc', default='auto', help='boundaries: auto (PEC on a solid bottom plane, MUR elsewhere), '
                                                'MUR, PML_8, PEC, or six values xmin,xmax,ymin,ymax,zmin,zmax')
    g.add_argument('--end-db', type=float, default=-40, help='stop when the field energy has decayed by this much')
    g.add_argument('--sigma', type=float, help='copper conductivity S/m (conducting sheets; default PEC)')
    g.add_argument('--diel', default='fixed', choices=['fixed', 'wideband'],
                   help='dielectric loss: fixed = conductivity fitted at the band centre (default); wideband = '
                        'Djordjevic-Sarkar model (er / tand constant-ish over the band) as Debye poles')
    g.add_argument('--diel-fref', type=float, default=1.0, help='GHz at which the stackup er / tand hold (wideband)')
    g.add_argument('--excite', help='comma-separated ports to excite (default all), e.g. P1')
    g.add_argument('--threads', type=int, default=0,
                   help='openEMS threads per run (0 = all cores; with --jobs: half the cores shared by the runs)')
    g.add_argument('--jobs', type=int, default=1, help='excitations run in parallel (separate processes; default 1)')
    g = ap.add_argument_group('field dumps')
    g.add_argument('--dump', action='append', default=[], metavar='FIELD@REGION[@GHz,...]',
                   help='field dump, repeatable: E|H|J @ layer[+-mm] | x=/y=/z= | volume | box=x0,y0,z0,x1,y1,z1 '
                        '[@ frequencies in GHz -> frequency domain + PNG maps; none -> time domain VTK]. '
                        'e.g. E@F.Cu+0.05@2,6   J@In2.Cu@4   E@y=7')
    g.add_argument('--dump-port', help='ports whose excitation runs get the dumps (default: the first excited port)')
    g = ap.add_argument_group('output')
    g.add_argument('--compare', help='Touchstone file to overlay on the result plot')
    g.add_argument('--preview', action='store_true', help='write the model/mesh preview and exit')
    g.add_argument('--out', default='openems_out')
    return ap


def _ram_gb():
    """Installed RAM in GB (None where sysconf cannot tell)."""
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1e9
    except (ValueError, OSError, AttributeError):
        return None


def main(argv=None):
    ap = parser()
    a = ap.parse_args(argv)
    if a.check:
        from .simulation import check_openems
        ok, report = check_openems()
        print("\n".join(report))
        print("openEMS: ready" if ok else "openEMS: NOT usable from this python")
        return 0 if ok else 2
    if not a.board:
        ap.error("board file required (or --check)")
    if a.config:
        ignored = apply_toml(a, ap, a.config, argv)
        if ignored:
            print(f"config: ignoring keys not used here: {', '.join(ignored)}")
    name = os.path.splitext(os.path.basename(a.board))[0]
    os.makedirs(a.out, exist_ok=True)
    fmin, fmax = a.fmin * 1e9, a.fmax * 1e9
    t0 = time.time()

    board = KiCadBoard(a.board)
    region = tuple(float(v) for v in a.region.split(',')) if a.region else None
    m = Model(board, nets=a.nets.split(',') if a.nets else None, pos_file=a.pos, include_mask=a.mask,
              region=region, ground=a.ground, parts=a.parts)
    print(m.describe())
    if a.diel == 'wideband':
        from .dielectric import describe as describe_diel
        print("\n".join(describe_diel(m.layers, a.diel_fref * 1e9, fmin, fmax)))
    bc = resolve_bc(a.bc, m)
    mesh_kw = dict(air=a.air, ratio=a.ratio, nz_min=a.nz, bc=bc, curves=a.curves, vias=a.vias, via_snap=a.via_snap)
    levels = None
    if a.converge:
        from .converge import parse_levels, mesh_levels, describe_levels
        factors = parse_levels(a.converge)
        levels = mesh_levels(m, fmin, fmax, factors, res=a.res, fine=a.fine, **mesh_kw)
        print("\n".join(describe_levels(levels)))
        mesh = levels[-1][1]                                        # the finest level gives the result
    else:
        mesh = auto_mesh(m, fmin, fmax, res=a.res, fine=a.fine, **mesh_kw)
    cavity_check(m, mesh, bc, fmax)
    for sp in (a.dump or []):                                      # validate dump specs before any run
        print("  " + parse_dump(sp, m, mesh).describe(mesh))
    png = os.path.join(a.out, f"{name}_mesh.png")
    preview(m, mesh, png, name)
    print(f"wrote {png}")
    if a.preview:
        return 0

    from .simulation import run_excitations, port_index, check_openems
    ok, report = check_openems()
    if not ok:
        print("openEMS cannot be imported - details:\n" + "\n".join(report))
        return 2

    excite = port_index(m, a.excite)
    dumps = [parse_dump(sp, m, mesh) for sp in (a.dump or [])]
    dump_runs = port_index(m, a.dump_port) if a.dump_port else None
    if dumps:
        n_runs = len(dump_runs) if dump_runs else 1
        print(f"field dumps: ~{estimate_size_mb(dumps, mesh) * n_runs:.0f} MB on disk (rough), in "
              f"{n_runs} run(s)")
    freq = np.linspace(fmin, fmax, a.points)
    jobs = max(1, min(a.jobs, len(excite)))
    ram = _ram_gb()
    if jobs > 1 and ram and jobs * mesh['info']['mem_gb'] > 0.8 * ram:
        print(f"WARNING: {jobs} parallel runs need ~{jobs * mesh['info']['mem_gb']:.1f} GB, {ram:.1f} GB RAM "
              f"installed - use fewer --jobs")
    run_kw = dict(end_db=a.end_db, sigma=a.sigma, threads=a.threads, jobs=jobs, diel=a.diel,
                  diel_fref=a.diel_fref * 1e9)
    coarse = []
    for f, lm in (levels or [])[:-1]:                               # convergence: coarser levels first, no dumps
        print(f"--- mesh level x{f:g} ({lm['info']['cells'] / 1e6:.2f} M cells) ---")
        Sl = run_excitations(m, lm, freq, excite, bc, os.path.join(a.out, 'converge', f"L{f:g}"), **run_kw)
        fill_reciprocal(Sl, excite)
        coarse.append((f, mixed_mode(Sl, m)[0]))
    if levels:
        print(f"--- mesh level x{levels[-1][0]:g} (finest: the result) ---")
    S = run_excitations(m, mesh, freq, excite, bc, a.out, xml_path=os.path.join(a.out, f"{name}.xml"), dumps=dumps,
                        dump_runs=dump_runs, **run_kw)
    for (pname, dname), path in getattr(run_excitations, 'dump_files', {}).items():
        d = next(x for x in dumps if x.name == dname)
        if d.freqs and os.path.exists(path):
            pngs = plot_fd_dumps(d, path, m, os.path.join(a.out, f"{name}_{pname.replace('+', 'p').replace('-', 'n')}"),
                                 norm=getattr(run_excitations, 'dump_norm', {}).get(pname), port=pname)
            print(f"  {dname} ({pname} driven): {path}" + (f" -> {len(pngs)} PNG map(s)" if pngs else ''))
        else:
            print(f"  {dname} ({pname} driven): {path}  (time domain: open the .vtr series in ParaView)")
    missing = fill_reciprocal(S, excite)
    if missing:
        print(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the terms "
              f"between unexcited ports are unknown (written as 0)")
    Smm, names, zref = mixed_mode(S, m)
    out = os.path.join(a.out, f"{name}_openems.s{len(names)}p")
    write_touchstone(out, freq, np.nan_to_num(Smm), zref, comment=f"openEMS FDTD ({name})", names=names)
    cmp = (*read_touchstone(a.compare), os.path.basename(a.compare)) if a.compare else None
    plot_results(os.path.join(a.out, f"{name}_openems.png"), freq, Smm, names, f"{name} - openEMS", cmp)
    info = dict(ports=names, zref=zref, mesh=mesh['info'], excited=[m.ports[k].name for k in excite],
                parts=[dict(ref=p.ref, value=p.value_str(), layer=p.layer) for p in m.parts], diel=a.diel)
    if levels:
        from .converge import compare_levels, report
        tol_db, tol_deg = (float(v) for v in str(a.converge_tol).split(','))
        dev = compare_levels([S_ for _, S_ in coarse] + [Smm], names) + [{}]      # finest: the reference
        lines, ok = report([f for f, _ in levels], dev[:-1], tol_db, tol_deg)
        print("\n".join(lines))
        cpng = os.path.join(a.out, f"{name}_converge.png")
        plot_convergence(cpng, freq, [(f"x{f:g}", S_) for f, S_ in coarse] + [(f"x{levels[-1][0]:g}", Smm)], names,
                         f"{name} - mesh convergence")
        info['convergence'] = dict(converged=ok, tol_db=tol_db, tol_deg=tol_deg,
                                   levels=[dict(factor=f, cells=lm['info']['cells'], **d)
                                           for (f, lm), d in zip(levels, dev)])
        print(f"wrote {cpng}")
    with open(os.path.join(a.out, f"{name}_openems.json"), 'w') as fh:
        json.dump(dict(info, runtime_s=time.time() - t0), fh, indent=1)
    print(f"wrote {out}, {os.path.join(a.out, name + '_openems.png')}  ({time.time() - t0:.0f} s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
