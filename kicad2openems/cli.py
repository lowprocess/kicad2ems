"""
kicad2openems - KiCad board -> openEMS (3D FDTD) with automatic meshing

    kicad2openems board.kicad_pcb --pos board-top.pos --nets SIG,GND --fmin 1 --fmax 6 --preview
    kicad2openems board.toml                        # config with board = "board.kicad_pcb"
    kicad2openems board.kicad_pcb --config board.toml --excite P1
    kicad2openems board.kicad_pcb ... --compare other_tool.s2p          # overlay another result

Ports are footprints with a Fab-layer text P1, P2, ... (or Reference / Value P<n> in the
pick-and-place file); P<n>+ / P<n>- give a differential port. --preview writes the model + mesh
plot and the cell / time-step report without running openEMS.

Outputs in --out: <name>_openems.sNp, <name>_openems.png, <name>_mesh.png, <name>_openems.json,
the CSX geometry <name>.xml (AppCSXCAD) and the raw runs under openems/.
"""
import argparse, sys

from .config import apply_toml
from .pipeline import Job


def parser():
    ap = argparse.ArgumentParser(prog='kicad2openems', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board', nargs='?', help='.kicad_pcb, or a .toml config (that names the board)')
    ap.add_argument('--check', action='store_true', help='check that this python can run openEMS, then exit')
    ap.add_argument('--config', help='TOML file with any of these options (openEMS ones also under [openems]); '
                                     'its paths are relative to the file')
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
    g.add_argument('--air', type=float, help='mm of air between the board and the boundary (default 5; '
                                              'antenna mode: a quarter wavelength at the band centre)')
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
    g.add_argument('--max-steps', type=int, default=1_000_000,
                   help='timestep limit per run (default 1000000); low frequencies and high-Q circuits need more')
    g.add_argument('--sigma', type=float, help='copper conductivity S/m (conducting sheets; default PEC)')
    g.add_argument('--diel', default='fixed', choices=['fixed', 'wideband'],
                   help='dielectric loss: fixed = conductivity fitted at the band centre (default); wideband = '
                        'Djordjevic-Sarkar model (er / tand constant-ish over the band) as Debye poles')
    g.add_argument('--diel-fref', type=float, default=1.0, help='GHz at which the stackup er / tand hold (wideband)')
    g.add_argument('--excite', help='comma-separated ports to excite (default all), e.g. P1')
    g.add_argument('--threads', type=int, default=0,
                   help='openEMS threads per run (0 = all cores; with --jobs: half the cores shared by the runs)')
    g.add_argument('--jobs', type=int, default=1, help='excitations run in parallel (separate processes; default 1)')
    g = ap.add_argument_group('antenna mode')
    g.add_argument('--mode', default='sparams', choices=['sparams', 'antenna'],
                   help='sparams (default): S-parameters of all ports; antenna: one driven port, PML on all '
                        'sides, far field (gain, efficiency, patterns)')
    g.add_argument('--ff-freq', metavar='GHz,...', help='antenna: frequency for the patterns (default: the '
                                                        'resonance, minimum |S11|); also recorded')
    g.add_argument('--ff-step', type=float, default=2.0, help='antenna: angle step of the pattern cuts (deg)')
    g = ap.add_argument_group('field dumps')
    g.add_argument('--dump', action='append', default=[], metavar='FIELD@REGION[@GHz,...]',
                   help='field dump, repeatable: E|H|J @ layer[+-mm] | x=/y=/z= | volume | box=x0,y0,z0,x1,y1,z1 '
                        '[@ frequencies in GHz -> frequency domain + PNG maps; none -> time domain VTK]. '
                        'e.g. E@F.Cu+0.05@2,6   J@In2.Cu@4   E@y=7')
    g.add_argument('--dump-port', help='ports whose excitation runs get the dumps (default: the first excited port)')
    g = ap.add_argument_group('output')
    g.add_argument('--compare', help='Touchstone file to overlay on the result plot')
    g.add_argument('--preview', action='store_true', help='write the model/mesh preview and exit')
    g.add_argument('--openems-output', action='store_true',
                   help="show openEMS's own output instead of the progress / ETA line (it is always kept in "
                        "openems.log of each run)")
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
    if a.board and a.board.lower().endswith('.toml'):
        if a.config:
            ap.error("two configs: give the .toml either as the argument or with --config")
        a.config, a.board = a.board, None
    if a.config:
        ignored = apply_toml(a, ap, a.config, argv)
        if ignored:
            print(f"config: ignoring keys not used here: {', '.join(ignored)}")
        for note in getattr(a, 'config_notes', []):
            print(f"config: {note}")
    if not a.board:
        ap.error("board file required: as the argument, or 'board = ...' in the config (or --check)")
    return Job(a).run()


if __name__ == '__main__':
    sys.exit(main())
