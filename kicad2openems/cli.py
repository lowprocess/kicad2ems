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
from .plots import preview, plot_results
from .sparams import mixed_mode, fill_reciprocal
from .touchstone import write_touchstone, read_touchstone
from .config import apply_toml


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
    g = ap.add_argument_group('solver')
    g.add_argument('--bc', default='auto', help='boundaries: auto (PEC on a solid bottom plane, MUR elsewhere), '
                                                'MUR, PML_8, PEC, or six values xmin,xmax,ymin,ymax,zmin,zmax')
    g.add_argument('--end-db', type=float, default=-40, help='stop when the field energy has decayed by this much')
    g.add_argument('--sigma', type=float, help='copper conductivity S/m (conducting sheets; default PEC)')
    g.add_argument('--excite', help='comma-separated ports to excite (default all), e.g. P1')
    g.add_argument('--threads', type=int, default=0, help='openEMS threads (0 = all cores)')
    g = ap.add_argument_group('output')
    g.add_argument('--compare', help='Touchstone file to overlay on the result plot')
    g.add_argument('--preview', action='store_true', help='write the model/mesh preview and exit')
    g.add_argument('--out', default='openems_out')
    return ap


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
              region=region, ground=a.ground)
    print(m.describe())
    bc = resolve_bc(a.bc, m)
    mesh = auto_mesh(m, fmin, fmax, res=a.res, fine=a.fine, air=a.air, ratio=a.ratio, nz_min=a.nz, bc=bc)
    cavity_check(m, mesh, bc, fmax)
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
    freq = np.linspace(fmin, fmax, a.points)
    S = run_excitations(m, mesh, freq, excite, bc, a.out, end_db=a.end_db, sigma=a.sigma, threads=a.threads,
                        xml_path=os.path.join(a.out, f"{name}.xml"))
    missing = fill_reciprocal(S, excite)
    if missing:
        print(f"note: {', '.join(m.ports[k].name for k in missing)} not excited - their reflection and the terms "
              f"between unexcited ports are unknown (written as 0)")
    Smm, names, zref = mixed_mode(S, m)
    out = os.path.join(a.out, f"{name}_openems.s{len(names)}p")
    write_touchstone(out, freq, np.nan_to_num(Smm), zref, comment=f"openEMS FDTD ({name})", names=names)
    cmp = (*read_touchstone(a.compare), os.path.basename(a.compare)) if a.compare else None
    plot_results(os.path.join(a.out, f"{name}_openems.png"), freq, Smm, names, f"{name} - openEMS", cmp)
    with open(os.path.join(a.out, f"{name}_openems.json"), 'w') as fh:
        json.dump(dict(ports=names, zref=zref, mesh=mesh['info'], excited=[m.ports[k].name for k in excite],
                       runtime_s=time.time() - t0), fh, indent=1)
    print(f"wrote {out}, {os.path.join(a.out, name + '_openems.png')}  ({time.time() - t0:.0f} s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
